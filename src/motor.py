"""
Motor de ejecución (corre en un hilo aparte para no congelar la interfaz).

* Reparte lotes entre 1..4 pestañas de Copilot en paralelo (envía en una, mientras espera
  las otras). Es la forma de escalar sin "agentes".
* Cada lote: prompt -> esperar marcador FIN (o que el texto deje de cambiar) -> parsear ->
  validar -> guardar. Las fechas que no vinieron se reintentan solas en otro lote.
* Si la caja de Copilot no acepta el prompt completo, reduce el tamaño de lote automáticamente.
* Si Copilot dice "límite alcanzado", espera y reintenta sin gastar intentos.
* Guarda avance (.avance.json) y el Excel después de CADA lote: si algo se cae, se reanuda.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path

from .ajustes import Ajustes
from .consolidado import contar_estados, escribir_excel, evaluar_filas
from .copilot_web import CopilotWeb, ErrorCopilot, ErrorPromptLargo, Pestana
from .planificador import Plan, Unidad, armar_lote
from .prompt_copilot import (ancla_prompt, construir_prompt, extraer_respuesta, generar_codigo,
                             unidades_completas, validar_lineas)


@dataclass
class Tarea:
    uid: str
    fechas: list[str] | None = None      # None = todas las de la unidad


class Motor(threading.Thread):
    def __init__(self, plan: Plan, ajustes: Ajustes, ruta_excel: Path, cola: queue.Queue,
                 avance: dict | None = None, fabrica_web=None):
        super().__init__(daemon=True)
        self.fabrica_web = fabrica_web or CopilotWeb
        self.plan = plan
        self.a = ajustes
        self.ruta_excel = Path(ruta_excel)
        self.ruta_avance = self.ruta_excel.with_suffix(".avance.json")
        self.cola = cola
        self.parar = threading.Event()
        self.pausa = threading.Event()
        self.inicio = date.fromisoformat(plan.parametros["inicio"])
        self.fin = date.fromisoformat(plan.parametros["fin"])
        # estado
        self.resultados: dict[str, dict[str, dict]] = {}
        self.estado_unidades: dict[str, dict] = {uid: {"estado": "pendiente", "intentos": 0} for uid in plan.unidades}
        self.lotes: list[dict] = []
        if avance:
            self.resultados = avance.get("resultados", {})
            self.estado_unidades.update(avance.get("estado_unidades", {}))
            self.lotes = avance.get("lotes", [])
            for st in self.estado_unidades.values():          # al reanudar se reintenta lo que no respondió
                if st["estado"] == "sin_respuesta":
                    st["estado"], st["intentos"] = "pendiente", 0
        self.factor = 1.0
        self.t_inicio = 0.0
        self.duraciones: list[float] = []
        self.ruta_excel_real = self.ruta_excel

    # ------------------------------------------------------------------ eventos
    def _emit(self, tipo: str, **datos):
        self.cola.put((tipo, datos))

    def log(self, nivel: str, msg: str):
        self._emit("log", nivel=nivel, msg=msg)

    # ------------------------------------------------------------------ cola de trabajo
    def _cola_inicial(self) -> deque:
        q = deque()
        for uid, u in self.plan.unidades.items():
            if self.estado_unidades[uid]["estado"] != "pendiente":
                continue
            faltan = [f for f in u.fechas if f not in self.resultados.get(uid, {})] if not u.delegada else None
            if u.delegada and self.resultados.get(uid):
                self.estado_unidades[uid]["estado"] = "ok"
                continue
            if not u.delegada and not faltan:
                self.estado_unidades[uid]["estado"] = "ok"
                continue
            q.append(Tarea(uid, faltan if faltan and len(faltan) < len(u.fechas) else None))
        return q

    def _vista(self, t: Tarea) -> Unidad:
        u = self.plan.unidades[t.uid]
        return replace(u, fechas=list(t.fechas)) if t.fechas is not None else u

    def _tomar_lote(self, q: deque) -> list[Tarea]:
        max_u = max(1, int(self.a.servidores_por_lote * self.factor))
        max_f = max(1, int(self.a.fechas_por_lote * self.factor))
        candidatas = [q[i] for i in range(min(len(q), 60))]
        vistas = [self._vista(t) for t in candidatas]
        lote_v = armar_lote(vistas, max_u, max_f)
        tareas = candidatas[:len(lote_v)]
        # respetar el límite de caracteres del prompt
        while len(tareas) > 1 and len(self._prompt(tareas, "XXXX")[0]) > self.a.max_caracteres_prompt:
            tareas = tareas[:-1]
        for _ in tareas:
            q.popleft()
        # un solo servidor con demasiadas fechas: partir sus fechas en dos
        while (len(tareas) == 1 and len(self._prompt(tareas, "XXXX")[0]) > self.a.max_caracteres_prompt):
            t = tareas[0]
            fechas = t.fechas if t.fechas is not None else list(self.plan.unidades[t.uid].fechas)
            if len(fechas) <= 1:
                break
            mitad = len(fechas) // 2
            tareas = [Tarea(t.uid, fechas[:mitad])]
            q.appendleft(Tarea(t.uid, fechas[mitad:]))
        return tareas

    def _prompt(self, tareas: list[Tarea], codigo: str):
        return construir_prompt([self._vista(t) for t in tareas], self.a.nombre_carpeta, self.inicio, self.fin,
                                self.a.ventana_cercania, codigo, compacto=self.a.prompt_compacto)

    # ------------------------------------------------------------------ persistencia
    def _guardar(self, estado_txt: str):
        datos = {
            "version": 1, "guardado": datetime.now().isoformat(timespec="seconds"),
            "excel": str(self.ruta_excel), "plan": self.plan.a_dict(), "resultados": self.resultados,
            "estado_unidades": self.estado_unidades, "lotes": self.lotes,
        }
        try:
            tmp = self.ruta_avance.with_suffix(".tmp")
            tmp.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.ruta_avance)
        except Exception as e:
            self.log("aviso", f"No pude guardar el avance: {e}")
        try:
            info = {"carpeta": self.a.nombre_carpeta, "ventana": self.a.ventana_cercania, "estado": estado_txt}
            real = escribir_excel(self.ruta_excel, self.plan, self.resultados, self.estado_unidades, self.lotes, info)
            if real != self.ruta_excel and real != self.ruta_excel_real:
                self.log("aviso", f"El Excel estaba abierto; guardé una copia: {real.name}")
            self.ruta_excel_real = real
        except Exception as e:
            self.log("error", f"No pude escribir el Excel: {e}")

    def _progreso(self, q: deque, en_curso: int):
        total_u = len(self.plan.unidades)
        ok = sum(1 for s in self.estado_unidades.values() if s["estado"] == "ok")
        sin = sum(1 for s in self.estado_unidades.values() if s["estado"] == "sin_respuesta")
        filas = evaluar_filas(self.plan, self.resultados, self.estado_unidades)
        conteo = contar_estados(filas)
        peso_pend = sum(self._vista(t).peso for t in q)
        por_lote = max(1, int(self.a.fechas_por_lote * self.factor))
        lotes_rest = -(-peso_pend // por_lote) + en_curso
        prom = (sum(self.duraciones[-10:]) / len(self.duraciones[-10:])) if self.duraciones else None
        eta = (prom * lotes_rest / max(1, len(self.web.pestanas))) if prom and getattr(self, "web", None) else None
        self._emit("progreso", unidades_ok=ok, unidades_sin=sin, unidades_total=total_u,
                   lotes=len(self.lotes), lotes_restantes=lotes_rest, conteo=dict(conteo), eta=eta,
                   prom_lote=prom, transcurrido=time.time() - self.t_inicio)

    # ------------------------------------------------------------------ ciclo principal
    def run(self):
        self.t_inicio = time.time()
        self.web = self.fabrica_web(self.a, log=self.log, debe_parar=self.parar.is_set)
        estado_final = "Completada"
        try:
            q = self._cola_inicial()
            if not q:
                self.log("ok", "No hay consultas pendientes.")
                self._guardar("Completada")
                self._emit("fin", ruta=str(self.ruta_excel_real), estado="Completada")
                return
            self._emit("estado", texto="Abriendo Edge…")
            self.web.abrir_navegador()
            self.web.preparar_pestana("Pestaña 1")
            for i in range(2, max(1, min(4, int(self.a.pestanas))) + 1):
                try:
                    self.web.preparar_pestana(f"Pestaña {i}", nueva=True)
                except ErrorCopilot as e:
                    self.log("aviso", f"No pude preparar la pestaña {i}: {e}. Sigo con {i - 1}.")
                    break
            self._emit("estado", texto="Consultando a Copilot")
            self._guardar("En curso")
            self._progreso(q, 0)
            fallos: dict[str, int] = {}

            while not self.parar.is_set():
                ocupadas = [p for p in self.web.pestanas if p.lote is not None]
                if not q and not ocupadas:
                    break
                for p in list(self.web.pestanas):
                    if self.parar.is_set():
                        break
                    try:
                        if p.lote is None:
                            if q and not self.pausa.is_set():
                                self._enviar_lote(p, q)
                        else:
                            self._revisar(p, q)
                        fallos[p.handle] = 0
                    except ErrorPromptLargo as e:
                        self._prompt_largo(p, q, e)
                    except ErrorCopilot as e:
                        if self.parar.is_set():
                            break
                        self._fallo_pestana(p, q, str(e), fallos)
                    except Exception as e:
                        if self.parar.is_set():
                            break
                        self._fallo_pestana(p, q, f"{type(e).__name__}: {e}", fallos)
                if not self.web.pestanas:
                    raise ErrorCopilot("No quedan pestañas de Copilot funcionando")
                time.sleep(1.2)

            if self.parar.is_set():
                estado_final = "Detenida por el usuario"
                for p in self.web.pestanas:            # devolver a la cola lo que estaba en curso
                    p.lote = None
            self._guardar(estado_final)
            self._progreso(q, 0)
            self._emit("fin", ruta=str(self.ruta_excel_real), estado=estado_final)
        except ErrorCopilot as e:
            self.log("error", str(e))
            self._guardar(f"Interrumpida: {e}")
            self._emit("fin", ruta=str(self.ruta_excel_real), estado=f"Interrumpida: {e}")
        except Exception as e:
            self.log("error", f"Error inesperado: {e}")
            self.log("debug", traceback.format_exc())
            self._guardar(f"Interrumpida: {e}")
            self._emit("fin", ruta=str(self.ruta_excel_real), estado=f"Interrumpida: {e}")
        finally:
            self.web.cerrar()

    def _prompt_largo(self, p: Pestana, q: deque, e: ErrorPromptLargo):
        """La caja tiene límite: aprenderlo, pasar a prompt compacto y reintentar más pequeño."""
        proporcion = e.aceptados / max(1, e.objetivo)
        limite = int(e.largo_prompt * proporcion * 0.9)
        anterior = self.a.max_caracteres_prompt
        self.a.max_caracteres_prompt = max(400, min(anterior - 200, limite))
        msg = f"La caja de Copilot acepta ≈ {int(e.largo_prompt * proporcion)} caracteres; ajusto el máximo a {self.a.max_caracteres_prompt}"
        if not self.a.prompt_compacto:
            self.a.prompt_compacto = True
            msg += " y paso al prompt compacto"
        self.log("aviso", msg)
        if p.lote:
            for t in reversed(p.lote["tareas"]):
                q.appendleft(t)
            p.lote = None
        if self.a.max_caracteres_prompt <= 400 and anterior <= 400:
            raise ErrorCopilot("La caja de Copilot no acepta ni un prompt mínimo. Revisa el selector 'entrada' con 'Probar conexión'.")

    def _fallo_pestana(self, p: Pestana, q: deque, msg: str, fallos: dict):
        self.log("aviso", f"{p.nombre}: {msg}")
        if p.lote:
            tareas = p.lote["tareas"]
            for t in reversed(tareas):
                q.appendleft(t)
            p.lote = None
        fallos[p.handle] = fallos.get(p.handle, 0) + 1
        if fallos[p.handle] >= 3:
            self.log("error", f"{p.nombre}: falló 3 veces seguidas; la cierro")
            try:
                self.web.driver.switch_to.window(p.handle)
                if len(self.web.pestanas) > 1:
                    self.web.driver.close()
            except Exception:
                pass
            self.web.pestanas.remove(p)
            return
        try:
            self.web._cargar_copilot(p)
        except Exception as e:
            self.log("aviso", f"{p.nombre}: no pude recargar Copilot ({e})")

    # ------------------------------------------------------------------ un lote
    def _enviar_lote(self, p: Pestana, q: deque):
        tareas = self._tomar_lote(q)
        if not tareas:
            return
        vistas = [self._vista(t) for t in tareas]
        codigo = generar_codigo()
        texto, mapa = self._prompt(tareas, codigo)
        n = len(self.lotes) + sum(1 for x in self.web.pestanas if x.lote) + 1
        p.lote = {"n": n, "tareas": tareas, "vistas": vistas, "mapa": mapa, "prompt": texto,
                  "enviado": datetime.now().strftime("%H:%M:%S"), "lecturas_frames": 0}
        p.codigo = codigo
        try:
            self.web.enviar(p, texto)
        except Exception:
            raise
        fechas = sum(v.peso for v in vistas)
        self.log("info", f"Lote {n} → {p.nombre}: {len(vistas)} servidores, {fechas} fechas (código {codigo})")

    def _revisar(self, p: Pestana, q: deque):
        lote = p.lote
        ahora = time.time()
        transcurrido = ahora - p.t_envio
        todos = transcurrido > 60 and int(transcurrido) // 30 > lote["lecturas_frames"]
        if todos:
            lote["lecturas_frames"] = int(transcurrido) // 30
        texto = self.web.leer_texto(p, todos_los_frames=todos)
        r = extraer_respuesta(texto, p.codigo)
        firma = f"{len(texto)}|{hash(texto[-800:])}"
        if firma != p.firma_texto:
            p.firma_texto, p.t_ultimo_cambio = firma, ahora
        quieto = ahora - p.t_ultimo_cambio
        generando = self.web.generando(p)

        motivo = None
        if r.hay_fin and quieto >= 2.5:
            motivo = "completo"
        elif not generando and quieto >= self.a.estabilidad_seg and transcurrido > 25:
            cola_txt = texto[texto.rfind(ancla_prompt(p.codigo)):].lower() if ancla_prompt(p.codigo) in texto else ""
            if not r.lineas and any(fr.lower() in cola_txt for fr in self.a.frases_limite):
                self.log("aviso", f"{p.nombre}: Copilot indica un límite/errores; espero {self.a.pausa_limite_seg} s")
                for t in reversed(lote["tareas"]):
                    q.appendleft(t)
                p.lote = None
                self.web._esperar(self.a.pausa_limite_seg)
                self.web.nuevo_chat(p)
                return
            motivo = "parcial" if r.lineas else "sin formato"
        elif transcurrido > self.a.timeout_respuesta:
            motivo = "tiempo agotado"
        if motivo is None:
            return
        self._procesar(p, q, r, texto, motivo, transcurrido)

    def _procesar(self, p: Pestana, q: deque, r, texto: str, motivo: str, duracion: float):
        lote = p.lote
        res, avisos = validar_lineas(r.lineas, lote["mapa"], self.plan.unidades, self.inicio, self.fin,
                                     self.a.ventana_cercania, lote["n"])
        if r.aviso:
            avisos.insert(0, r.aviso)
        for x in res:
            self.resultados.setdefault(x.unidad, {})[x.fecha_pedida] = asdict(x)
        completas, incompletas = unidades_completas(res, lote["vistas"])
        for t in lote["tareas"]:
            st = self.estado_unidades[t.uid]
            u = self.plan.unidades[t.uid]
            if u.delegada:
                ok = bool(self.resultados.get(t.uid))
                faltan = None
            else:
                faltan = [f for f in u.fechas if f not in self.resultados.get(t.uid, {})]
                ok = not faltan
            if ok:
                st["estado"] = "ok"
                continue
            st["intentos"] += 1
            if st["intentos"] <= self.a.reintentos:
                q.append(Tarea(t.uid, faltan))
            else:
                st["estado"] = "sin_respuesta"
        if motivo != "completo":
            avisos.append(f"Respuesta {motivo}")
        bloque = r.bloque if r.bloque else texto[-6000:]
        reg = {
            "n": lote["n"], "pestana": p.nombre, "codigo": p.codigo, "enviado": lote["enviado"],
            "duracion": round(duracion, 1), "items": len(lote["vistas"]), "lineas": len(res),
            "resultado": motivo, "avisos": avisos, "prompt": lote["prompt"], "respuesta": bloque.strip(),
        }
        self.lotes.append(reg)
        self.duraciones.append(duracion)
        nivel = "ok" if motivo == "completo" and not incompletas else "aviso"
        self.log(nivel, f"Lote {lote['n']} ({p.nombre}): {len(res)} líneas válidas en {duracion:.0f} s"
                        + (f" · {len(incompletas)} servidor(es) a reintentar" if incompletas else "")
                        + (f" · {motivo}" if motivo != "completo" else ""))
        for a in avisos[:5]:
            self.log("debug", f"   {a}")
        p.lote = None
        p.lotes_hechos += 1
        self._guardar("En curso")
        self._progreso(q, sum(1 for x in self.web.pestanas if x.lote))
        if self.a.nuevo_chat_por_lote and (q or any(x.lote for x in self.web.pestanas)):
            self.web.nuevo_chat(p)


# --------------------------------------------------------------------------------------
class PruebaConexion(threading.Thread):
    """Abre Edge, llega al chat, genera diagnóstico y hace una pregunta mínima de ida y vuelta."""

    def __init__(self, ajustes: Ajustes, carpeta_diag: Path, cola: queue.Queue):
        super().__init__(daemon=True)
        self.a, self.carpeta, self.cola = ajustes, Path(carpeta_diag), cola
        self.parar = threading.Event()

    def log(self, nivel, msg):
        self.cola.put(("log", {"nivel": nivel, "msg": msg}))

    def run(self):
        web = CopilotWeb(self.a, log=self.log, debe_parar=self.parar.is_set)
        ok = False
        try:
            web.abrir_navegador()
            p = web.preparar_pestana("Prueba")
            ruta = web.diagnostico(self.carpeta)
            self.log("info", f"Diagnóstico guardado en: {ruta}")
            codigo = generar_codigo()
            texto = (f"Responde únicamente con una línea, sin nada más: @@INICIO-<CÓDIGO>@@ OK @@FIN-<CÓDIGO>@@ "
                     f"reemplazando <CÓDIGO> por el código de este lote.\n{ancla_prompt(codigo)}")
            web.enviar(p, texto)
            t0 = time.time()
            while time.time() - t0 < 150:
                if self.parar.is_set():
                    raise ErrorCopilot("Detenido por el usuario")
                r = extraer_respuesta(web.leer_texto(p, todos_los_frames=time.time() - t0 > 40), codigo)
                if r.hay_fin:
                    ok = True
                    self.log("ok", f"Copilot respondió con el formato correcto en {time.time() - t0:.0f} s. ¡Listo para auditar!")
                    break
                time.sleep(2)
            if not ok:
                self.log("aviso", "Copilot no devolvió los marcadores en 150 s. Revisa la ventana de Edge y el diagnóstico.")
                web.diagnostico(self.carpeta)
        except Exception as e:
            self.log("error", f"Prueba fallida: {e}")
            try:
                if web.driver:
                    web.diagnostico(self.carpeta)
            except Exception:
                pass
        finally:
            web.cerrar()
            self.cola.put(("prueba_fin", {"ok": ok}))
