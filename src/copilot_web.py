"""
Automatización de Copilot en OneDrive/SharePoint con Selenium + Edge.

No dependemos de la estructura exacta de la página:
* Todos los selectores vienen de ajustes.json (listas de candidatos, el primero visible gana).
* Se buscan elementos en la página, dentro de iframes (hasta 4 niveles) y dentro de shadow DOM.
* Para saber si Copilot terminó NO se necesita conocer el contenedor de la respuesta: se lee el
  texto visible del panel y se buscan los marcadores únicos del lote (ver prompt_copilot.py).
* "Probar conexión" genera un diagnóstico (HTML + captura + lista de botones/cajas) para
  recalibrar selectores si Microsoft cambia la interfaz.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import portapapeles
from .ajustes import Ajustes

# ---------------------------------------------------------------------------------------
# JavaScript (se ejecuta con driver.execute_script; los parámetros llegan en `arguments`)
# ---------------------------------------------------------------------------------------
JS_BUSCAR = r"""
const sels = arguments[0];
function raices(root, out){ out.push(root); const todos = root.querySelectorAll('*');
  for (const el of todos){ if (el.shadowRoot) raices(el.shadowRoot, out); } return out; }
function visible(el){ const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false;
  const st = getComputedStyle(el); return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0'; }
const rs = raices(document, []);
for (let i = 0; i < sels.length; i++){ for (const root of rs){ let lista; try { lista = root.querySelectorAll(sels[i]); } catch(e){ break; }
  for (const el of lista){ if (visible(el)) return [el, i]; } } }
return null;
"""

JS_TEXTO = r"""
let partes = [document.body ? document.body.innerText : ''];
function recorrer(root){ for (const el of root.querySelectorAll('*')){ if (el.shadowRoot){
  for (const h of el.shadowRoot.children){ if (h.innerText) partes.push(h.innerText); }
  recorrer(el.shadowRoot); } } }
recorrer(document);
return partes.join('\n');
"""

JS_VALOR = r"""
const el = arguments[0];
return (el.value !== undefined && el.tagName !== 'DIV') ? el.value : (el.innerText || el.textContent || '');
"""

JS_LIMPIAR = r"""
const el = arguments[0]; el.focus();
if (el.value !== undefined && el.tagName !== 'DIV'){
  const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set;
  set.call(el, ''); el.dispatchEvent(new Event('input', {bubbles:true}));
} else {
  const sel = window.getSelection(); const rg = document.createRange(); rg.selectNodeContents(el);
  sel.removeAllRanges(); sel.addRange(rg); document.execCommand('delete', false);
}
"""

JS_INSERTAR = r"""
const el = arguments[0], texto = arguments[1]; el.focus();
if (el.value !== undefined && el.tagName !== 'DIV'){
  const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set;
  set.call(el, texto); el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
} else {
  const dt = new DataTransfer(); dt.setData('text/plain', texto);
  const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
  const consumido = !el.dispatchEvent(ev);
  if (!consumido || !(el.innerText || '').trim()) { document.execCommand('insertText', false, texto); }
}
return (el.value !== undefined && el.tagName !== 'DIV') ? el.value.length : (el.innerText || '').length;
"""

JS_HABILITADO = r"""
const b = arguments[0];
return !(b.disabled || b.getAttribute('aria-disabled') === 'true');
"""

JS_DIAGNOSTICO = r"""
function raices(root, out){ out.push(root); for (const el of root.querySelectorAll('*')){ if (el.shadowRoot) raices(el.shadowRoot, out);} return out; }
function desc(el){ const r = el.getBoundingClientRect(); return {tag: el.tagName.toLowerCase(), id: el.id || '',
  aria: el.getAttribute('aria-label') || '', automation: el.getAttribute('data-automation-id') || '',
  testid: el.getAttribute('data-testid') || '', role: el.getAttribute('role') || '',
  placeholder: el.getAttribute('placeholder') || '', clase: (el.className && el.className.toString ? el.className.toString() : '').slice(0,80),
  texto: (el.innerText || '').trim().slice(0, 60), visible: r.width > 2 && r.height > 2}; }
const out = {url: location.href, titulo: document.title, botones: [], entradas: [], iframes: []};
for (const root of raices(document, [])){
  for (const b of root.querySelectorAll('button,[role="button"]')) out.botones.push(desc(b));
  for (const e of root.querySelectorAll('textarea,input[type="text"],[contenteditable="true"],[role="textbox"]')) out.entradas.push(desc(e));
  for (const f of root.querySelectorAll('iframe,frame')) out.iframes.push({src: f.src || '', id: f.id || '', titulo: f.title || ''});
}
out.botones = out.botones.filter(b => b.aria || b.automation || b.testid || b.texto).slice(0, 400);
return JSON.stringify(out);
"""

JS_GRABADOR_INSTALAR = r"""
if (window.__grabInstalado) return 'ya';
window.__grabInstalado = true; window.__grab = null;
function q(v){ return String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"'); }
function esc(v){ return (window.CSS && CSS.escape) ? CSS.escape(v) : v; }
function selector(el){
  const t = el.tagName.toLowerCase(), at = n => el.getAttribute(n), op = [];
  if (at('data-automation-id')) op.push(`${t}[data-automation-id="${q(at('data-automation-id'))}"]`);
  if (at('data-testid')) op.push(`${t}[data-testid="${q(at('data-testid'))}"]`);
  if (at('data-lexical-editor') === 'true') op.push('[data-lexical-editor="true"][contenteditable="true"]');
  if (el.id && !/\d{3,}|:r|^r\d|[a-f0-9]{8,}/i.test(el.id)) op.push('#' + esc(el.id));
  if (at('aria-label')) op.push(`${t}[aria-label="${q(at('aria-label'))}"]`);
  const cl = Array.from(el.classList).filter(c => /__|^fai-/.test(c) && !/^___/.test(c));
  if (cl.length) op.push(t + '.' + cl.slice(0, 3).map(esc).join('.'));
  if (at('contenteditable') === 'true') op.push(`[contenteditable="true"]${at('role') ? `[role="${q(at('role'))}"]` : ''}`);
  if (at('placeholder')) op.push(`${t}[placeholder="${q(at('placeholder'))}"]`);
  if (at('title')) op.push(`${t}[title="${q(at('title'))}"]`);
  const validas = op.filter(s => { try { return document.querySelector(s) === el; } catch(e){ return false; } });
  return {sel: validas[0] || op[0] || t, opciones: validas.length ? validas : op};
}
document.addEventListener('pointerdown', ev => {
  const camino = ev.composedPath ? ev.composedPath() : [ev.target];
  let obj = null;
  for (const n of camino){
    if (!n || !n.getAttribute) continue;
    if (n.getAttribute('contenteditable') === 'true' || n.tagName === 'TEXTAREA' || n.tagName === 'INPUT'){ obj = n; break; }
    if (n.tagName === 'BUTTON' || n.getAttribute('role') === 'button' || n.tagName === 'A'){ obj = n; break; }
  }
  obj = obj || ev.target;
  const r = selector(obj);
  window.__grab = {sel: r.sel, opciones: r.opciones, tag: obj.tagName.toLowerCase(),
                   aria: obj.getAttribute('aria-label') || '', texto: (obj.innerText || '').trim().slice(0, 60)};
}, true);
return 'ok';
"""

JS_GRABADOR_LEER = r"""
const g = window.__grab; window.__grab = null;
return g ? JSON.stringify(g) : null;
"""


class ErrorCopilot(Exception):
    pass


class ErrorPromptLargo(ErrorCopilot):
    """La caja de Copilot no aceptó el texto completo (tiene un límite de caracteres)."""

    def __init__(self, aceptados: int, objetivo: int, largo_prompt: int):
        super().__init__(f"La caja de Copilot solo aceptó {aceptados} de {objetivo} caracteres")
        self.aceptados, self.objetivo, self.largo_prompt = aceptados, objetivo, largo_prompt


@dataclass
class Pestana:
    handle: str
    nombre: str
    ruta_entrada: list[int] = field(default_factory=list)
    lote: object = None                 # lote en curso (lo maneja el motor)
    codigo: str = ""
    t_envio: float = 0.0
    t_ultimo_cambio: float = 0.0
    firma_texto: str = ""
    ultimo_texto: str = ""
    lotes_hechos: int = 0


def mensaje_corto(e: BaseException) -> str:
    """Selenium mete el stacktrace en el mensaje; para la bitácora basta la primera línea."""
    m = getattr(e, "msg", None) or str(e) or type(e).__name__
    m = m.strip().split("\n")[0].replace("Message: ", "")
    return m[:300]


def _sin_espacios(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


class CopilotWeb:
    def __init__(self, ajustes: Ajustes, log: Callable[[str, str], None] | None = None,
                 debe_parar: Callable[[], bool] | None = None):
        self.a = ajustes
        self.log = log or (lambda nivel, msg: print(f"[{nivel}] {msg}"))
        self.debe_parar = debe_parar or (lambda: False)
        self.driver = None
        self.pestanas: list[Pestana] = []

    # ------------------------------------------------------------------ navegador
    FUERA_DE_PANTALLA = (-32000, -32000)

    def abrir_navegador(self, modo_ventana: str | None = None) -> None:
        """modo_ventana: "visible" | "oculta" (ventana real movida fuera de la pantalla) |
        "sin_ventana" (headless, experimental)."""
        self.modo_ventana = modo_ventana or getattr(self.a, "ventana_edge", "visible") or "visible"
        self.ventana_mostrada = self.modo_ventana == "visible"
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
        from selenium.webdriver.edge.service import Service

        import os
        os.environ.setdefault("SE_AVOID_STATS", "true")       # evita el aviso "Error sending stats to Plausible"
        perfil = Path(self.a.perfil_edge).expanduser()
        perfil.mkdir(parents=True, exist_ok=True)
        op = Options()
        op.add_argument(f"--user-data-dir={perfil}")
        op.add_argument("--profile-directory=Default")
        if self.modo_ventana == "oculta":
            x, y = self.FUERA_DE_PANTALLA
            op.add_argument(f"--window-position={x},{y}")
            op.add_argument("--window-size=1500,1000")
        elif self.modo_ventana == "sin_ventana":
            op.add_argument("--headless=new")
            op.add_argument("--window-size=1500,1000")
        else:
            op.add_argument("--start-maximized")
        # que las pestañas en segundo plano no se "duerman" mientras Copilot responde
        op.add_argument("--disable-background-timer-throttling")
        op.add_argument("--disable-renderer-backgrounding")
        op.add_argument("--disable-backgrounding-occluded-windows")
        op.add_argument("--disable-features=msEdgeSleepingTabs,CalculateNativeWinOcclusion")
        op.add_experimental_option("excludeSwitches", ["enable-automation"])
        op.add_experimental_option("useAutomationExtension", False)
        servicio = Service(executable_path=self.a.ruta_driver) if self.a.ruta_driver else Service()
        if os.name == "nt":
            import subprocess
            servicio.creation_flags = subprocess.CREATE_NO_WINDOW      # sin consola negra de msedgedriver
        try:
            self.driver = webdriver.Edge(options=op, service=servicio)
        except Exception as e:
            msg = str(e)
            if "user data directory is already in use" in msg.lower():
                raise ErrorCopilot("El perfil de Edge ya está abierto en otra ventana. Ciérrala y vuelve a intentar.") from e
            raise ErrorCopilot(f"No pude abrir Edge: {msg.splitlines()[0] if msg else e}. "
                               "Si la red bloquea la descarga del driver, indica la ruta de msedgedriver.exe en Ajustes.") from e
        self.driver.set_script_timeout(30)

    def mostrar_ventana(self) -> None:
        if not self.driver or self.modo_ventana == "sin_ventana":
            return
        try:
            self.driver.set_window_position(0, 0)
            self.driver.maximize_window()
            self.ventana_mostrada = True
        except Exception:
            pass

    def _reocultar_tras_login(self) -> None:
        if getattr(self, "mostrada_por_login", False):
            self.mostrada_por_login = False
            self.ocultar_ventana()
            self.log("info", "Sesión lista: vuelvo a ocultar Edge.")

    def ocultar_ventana(self) -> None:
        if not self.driver or self.modo_ventana == "sin_ventana":
            return
        try:
            self.driver.set_window_rect(*self.FUERA_DE_PANTALLA, 1500, 1000)
            self.ventana_mostrada = False
        except Exception:
            pass

    def cerrar(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        self.driver = None
        self.pestanas = []

    # ------------------------------------------------------------------ frames
    def _ir_a(self, ruta: list[int]) -> bool:
        from selenium.webdriver.common.by import By
        d = self.driver
        d.switch_to.default_content()
        for i in ruta:
            frames = d.find_elements(By.CSS_SELECTOR, "iframe,frame")
            if i >= len(frames):
                d.switch_to.default_content()
                return False
            d.switch_to.frame(frames[i])
        return True

    def _rutas_frames(self, max_prof: int = 4) -> list[list[int]]:
        from selenium.webdriver.common.by import By
        d = self.driver
        rutas: list[list[int]] = []

        def rec(ruta: list[int]):
            rutas.append(ruta)
            if len(ruta) >= max_prof:
                return
            try:
                n = len(d.find_elements(By.CSS_SELECTOR, "iframe,frame"))
            except Exception:
                return
            for i in range(n):
                try:
                    if not self._ir_a(ruta):
                        return
                    frames = d.find_elements(By.CSS_SELECTOR, "iframe,frame")
                    d.switch_to.frame(frames[i])
                except Exception:
                    continue
                rec(ruta + [i])
        d.switch_to.default_content()
        rec([])
        d.switch_to.default_content()
        return rutas

    def _buscar(self, clave: str, preferida: Optional[list[int]] = None, solo_preferida: bool = False,
                solo_especificos: bool = False):
        """Retorna (ruta_frame, elemento) del primer candidato visible, o (None, None).
        Deja el driver posicionado en ese frame. Nunca lanza excepción: si la página se está
        recargando (elementos "stale"), simplemente no encuentra nada y se reintenta después.
        solo_especificos: ignora los 2 últimos selectores (genéricos, ej. 'textarea')."""
        try:
            return self._buscar_interno(clave, preferida, solo_preferida, solo_especificos)
        except Exception:
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            return None, None

    def _buscar_interno(self, clave, preferida, solo_preferida, solo_especificos):
        sels = self.a.selectores.get(clave, [])
        if solo_especificos and len(sels) > 2:
            sels = sels[:-2]
        if not sels:
            return None, None
        candidatas = []
        if preferida is not None:
            candidatas.append(preferida)
        if not solo_preferida:
            candidatas += [r for r in self._rutas_frames() if r != preferida]
        # el frame preferido (donde se encontró antes) gana si tiene coincidencia;
        # si no, se recorre todo y gana el selector más específico (el primero de la lista)
        mejor = None
        for ruta in candidatas:
            try:
                if not self._ir_a(ruta):
                    continue
                r = self.driver.execute_script(JS_BUSCAR, sels)
            except Exception:
                continue
            if r:
                if ruta == preferida:
                    return ruta, r[0]
                if mejor is None or r[1] < mejor[1]:
                    mejor = (ruta, r[1])
                    if r[1] == 0:
                        break
        if mejor is not None and self._ir_a(mejor[0]):
            r = self.driver.execute_script(JS_BUSCAR, sels)
            if r:
                return mejor[0], r[0]
        self.driver.switch_to.default_content()
        return None, None

    def _click(self, el) -> None:
        try:
            el.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", el)

    def _esperar(self, segundos: float) -> None:
        fin = time.time() + segundos
        while time.time() < fin:
            if self.debe_parar():
                raise ErrorCopilot("Detenido por el usuario")
            time.sleep(0.25)

    # ------------------------------------------------------------------ preparación
    def preparar_pestana(self, nombre: str, nueva: bool = False) -> Pestana:
        d = self.driver
        if nueva:
            d.switch_to.new_window("tab")
        p = Pestana(handle=d.current_window_handle, nombre=nombre)
        self._cargar_copilot(p)
        self.pestanas.append(p)
        return p

    def _cargar_copilot(self, p: Pestana) -> None:
        d = self.driver
        d.switch_to.window(p.handle)
        d.get(self.a.url_carpeta)
        self.log("info", f"{p.nombre}: abriendo la carpeta en OneDrive…")

        # 1) esperar a que cargue (o a que el usuario inicie sesión)
        limite = time.time() + max(30, int(self.a.timeout_login))
        aviso = time.time()
        while True:
            if self.debe_parar():
                raise ErrorCopilot("Detenido por el usuario")
            ruta, el = self._buscar("entrada", solo_especificos=True)
            if el is not None:
                p.ruta_entrada = ruta
                self.log("ok", f"{p.nombre}: el chat de Copilot ya está abierto")
                self._reocultar_tras_login()
                return
            ruta_btn, btn = self._buscar("abrir_copilot")
            if btn is not None:
                break
            if time.time() > limite:
                raise ErrorCopilot("No apareció el botón de Copilot. ¿Iniciaste sesión? Usa 'Probar conexión' "
                                   "para generar un diagnóstico de la página.")
            if time.time() - aviso > 20:
                if getattr(self, "modo_ventana", "visible") == "oculta" and not self.ventana_mostrada:
                    self.mostrar_ventana()
                    self.mostrada_por_login = True
                    self.log("aviso", f"{p.nombre}: la página no carga Copilot; mostré Edge por si pide iniciar sesión.")
                elif getattr(self, "modo_ventana", "visible") == "sin_ventana":
                    raise ErrorCopilot("En modo 'sin ventana' no se puede iniciar sesión. Ejecuta una vez con la "
                                       "ventana visible para que Edge guarde la sesión.")
                else:
                    self.log("aviso", f"{p.nombre}: esperando la página… si Edge pide iniciar sesión, hazlo en esa ventana.")
                aviso = time.time()
            self._esperar(2)

        # 2) cerrar popups de bienvenida (nunca el del chat) y abrir el panel
        self._cerrar_popups()
        for intento in range(3):
            ruta_btn, btn = self._buscar("abrir_copilot")
            if btn is not None:
                try:
                    self._click(btn)
                except Exception:
                    pass
            limite = time.time() + 20
            while time.time() < limite:
                ruta, el = self._buscar("entrada")
                if el is not None:
                    p.ruta_entrada = ruta
                    self.log("ok", f"{p.nombre}: Copilot listo (frame {ruta or 'principal'})")
                    self._reocultar_tras_login()
                    return
                self._esperar(1)
            self.log("aviso", f"{p.nombre}: no veo la caja del chat todavía, reintento abrir Copilot ({intento + 1}/3)")
        raise ErrorCopilot("Se abrió Copilot pero no encontré la caja de texto del chat. "
                           "Usa 'Calibrar con clics' para enseñarle al programa dónde está.")

    def _cerrar_popups(self) -> None:
        from selenium.webdriver.common.by import By
        d = self.driver
        for xp in self.a.selectores.get("cerrar_popup_xpath", []):
            try:
                d.switch_to.default_content()
                for b in d.find_elements(By.XPATH, xp):
                    if b.is_displayed():
                        self._click(b)
                        time.sleep(0.5)
            except Exception:
                pass

    # ------------------------------------------------------------------ envío
    def enviar(self, p: Pestana, texto: str) -> None:
        from selenium.webdriver.common.keys import Keys
        d = self.driver
        d.switch_to.window(p.handle)
        ruta, el = self._buscar("entrada", p.ruta_entrada)
        if el is None:
            raise ErrorCopilot("No encuentro la caja de texto de Copilot")
        p.ruta_entrada = ruta
        self._click(el)
        objetivo = len(_sin_espacios(texto))

        def contenido() -> int:
            try:
                return len(_sin_espacios(d.execute_script(JS_VALOR, el)))
            except Exception:
                return 0

        def limpiar():
            try:
                d.execute_script(JS_LIMPIAR, el)
            except Exception:
                pass

        limpiar()
        metodo = ""
        # a) pegar desde el portapapeles (lo más fiel en editores enriquecidos)
        if portapapeles.disponible() and portapapeles.copiar(texto):
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(d).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
            time.sleep(0.8)
            if contenido() >= objetivo * 0.9:
                metodo = "pegar"
        # b) inyección por JavaScript
        if not metodo:
            limpiar()
            try:
                d.execute_script(JS_INSERTAR, el, texto)
            except Exception:
                pass
            time.sleep(0.6)
            if contenido() >= objetivo * 0.9:
                metodo = "js"
        # c) teclear (lento, último recurso): Shift+Enter para los saltos de línea
        if not metodo:
            limpiar()
            for i, linea in enumerate(texto.split("\n")):
                if i:
                    el.send_keys(Keys.SHIFT, Keys.ENTER)
                if linea:
                    el.send_keys(linea)
            time.sleep(0.5)
            if contenido() >= objetivo * 0.9:
                metodo = "teclear"
        if not metodo:
            obtenido = contenido()
            limpiar()
            raise ErrorPromptLargo(obtenido, objetivo, len(texto))

        # enviar: botón si está habilitado, si no Enter
        time.sleep(0.3)
        _, btn = self._buscar("enviar", ruta, solo_preferida=True)
        self._ir_a(ruta)                      # asegurar que seguimos en el frame del chat
        if btn is not None:
            try:
                if d.execute_script(JS_HABILITADO, btn):
                    self._click(btn)
                else:
                    el.send_keys(Keys.ENTER)
            except Exception:
                el.send_keys(Keys.ENTER)
        else:
            el.send_keys(Keys.ENTER)
        p.t_envio = p.t_ultimo_cambio = time.time()
        p.firma_texto = ""
        self.log("debug", f"{p.nombre}: prompt enviado ({len(texto)} caracteres, método: {metodo})")

    # ------------------------------------------------------------------ lectura
    def leer_texto(self, p: Pestana, todos_los_frames: bool = False) -> str:
        d = self.driver
        d.switch_to.window(p.handle)
        textos = []
        rutas = self._rutas_frames() if todos_los_frames else [p.ruta_entrada]
        for ruta in rutas:
            try:
                if self._ir_a(ruta):
                    textos.append(d.execute_script(JS_TEXTO) or "")
            except Exception:
                continue
        d.switch_to.default_content()
        return "\n".join(textos)

    def generando(self, p: Pestana) -> bool:
        try:
            _, el = self._buscar("detener", p.ruta_entrada, solo_preferida=True)
            return el is not None
        except Exception:
            return False

    def nuevo_chat(self, p: Pestana) -> None:
        d = self.driver
        d.switch_to.window(p.handle)
        _, btn = self._buscar("nuevo_chat", p.ruta_entrada)
        if btn is not None:
            self._click(btn)
            time.sleep(2.5)
            ruta, el = self._buscar("entrada", p.ruta_entrada)
            if el is not None:
                p.ruta_entrada = ruta
                return
        # sin botón: recargar la carpeta y reabrir Copilot
        self.log("debug", f"{p.nombre}: recargando la página para empezar un chat nuevo")
        self._cargar_copilot(p)

    # ------------------------------------------------------------------ diagnóstico
    def diagnostico(self, carpeta: Path) -> Path:
        """Guarda captura, HTML y lista de elementos de cada frame para calibrar selectores."""
        d = self.driver
        carpeta.mkdir(parents=True, exist_ok=True)
        marca = time.strftime("%Y%m%d_%H%M%S")
        base = carpeta / f"diagnostico_copilot_{marca}"
        base.mkdir(exist_ok=True)
        try:
            d.switch_to.default_content()
            d.save_screenshot(str(base / "captura.png"))
        except Exception:
            pass
        reporte = {"selectores_usados": self.a.selectores, "frames": []}
        for ruta in self._rutas_frames():
            try:
                self._ir_a(ruta)
                info = json.loads(d.execute_script(JS_DIAGNOSTICO))
                info["ruta"] = ruta
                for clave in ("abrir_copilot", "entrada", "enviar", "detener", "nuevo_chat"):
                    r = d.execute_script(JS_BUSCAR, self.a.selectores.get(clave, []))
                    info[f"encontrado_{clave}"] = (self.a.selectores.get(clave, [])[r[1]] if r else None)
                reporte["frames"].append(info)
                nombre = "principal" if not ruta else "frame_" + "_".join(map(str, ruta))
                (base / f"{nombre}.html").write_text(d.page_source, encoding="utf-8")
            except Exception as e:
                reporte["frames"].append({"ruta": ruta, "error": str(e)})
        d.switch_to.default_content()
        (base / "elementos.json").write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        return base

    # ------------------------------------------------------------------ calibración con clics
    PASOS_CALIBRACION = [
        ("abrir_copilot", "Haz clic en el botón de Copilot (el ícono que abre el chat en la carpeta).", True),
        ("entrada", "Haz clic dentro de la caja donde se escribe la pregunta.", True),
        ("enviar", "Escribe una letra en la caja y haz clic en el botón Enviar (o espera para omitir).", False),
    ]

    def calibrar(self, avisar: Callable[[int, int, str], None], timeout_paso: int = 180) -> dict[str, dict]:
        """Abre la carpeta y registra en qué elementos hace clic el usuario, paso a paso.
        Retorna {clave: {"sel":..., "opciones": [...], "aria":..., "texto":...}}."""
        d = self.driver
        d.get(self.a.url_carpeta)
        self.log("info", "Calibración: cargando la carpeta… (inicia sesión en Edge si te lo pide)")
        limite = time.time() + max(30, int(self.a.timeout_login))
        while time.time() < limite:            # esperar a que la página esté lista (o al login)
            try:
                if d.execute_script("return document.readyState") == "complete":
                    _, btn = self._buscar("abrir_copilot")
                    if btn is not None or time.time() > limite - max(0, int(self.a.timeout_login) - 25):
                        break
            except Exception:
                pass
            self._esperar(1.5)
        self._esperar(1.5)
        resultados: dict[str, dict] = {}
        total = len(self.PASOS_CALIBRACION)
        for i, (clave, texto, obligatorio) in enumerate(self.PASOS_CALIBRACION, start=1):
            avisar(i, total, texto)
            self._leer_grab()                  # descartar clics viejos
            espera = timeout_paso if obligatorio else 60
            fin = time.time() + espera
            g = None
            while time.time() < fin and g is None:
                self._esperar(0.6)
                g = self._leer_grab()
            if g is None:
                if obligatorio:
                    raise ErrorCopilot(f"No registré ningún clic en el paso {i} ({clave}).")
                self.log("aviso", f"Paso {i} omitido ({clave}).")
                continue
            resultados[clave] = g
            self.log("ok", f"Paso {i}: {clave} → {g['sel']}" + (f"  («{g['aria']}»)" if g.get("aria") else ""))
            time.sleep(1.2)                    # dejar que la interfaz reaccione al clic
        return resultados

    def _leer_grab(self) -> dict | None:
        """Instala el grabador (en la página y sus frames) y lee el último clic registrado."""
        d = self.driver
        hallado = None
        for ruta in self._rutas_frames(max_prof=2):
            try:
                if not self._ir_a(ruta):
                    continue
                d.execute_script(JS_GRABADOR_INSTALAR)
                crudo = d.execute_script(JS_GRABADOR_LEER)
                if crudo and hallado is None:
                    hallado = json.loads(crudo)
                    hallado["ruta"] = ruta
            except Exception:
                continue
        try:
            d.switch_to.default_content()
        except Exception:
            pass
        return hallado
