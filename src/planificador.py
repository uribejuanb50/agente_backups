"""
Arma el plan de consultas a Copilot.

Fila del Excel  ->  (servidores, fechas)  ->  Unidades de consulta  ->  Lotes (1 prompt c/u)

* Modo "hibrido": Python calcula las fechas de cada fila (muestra aleatoria reproducible
  con semilla). Las filas cuya agenda no se entiende (ej. "Por demanda") se delegan a
  Copilot, que elige las N fechas.
* Modo "copilot": todas las filas se delegan.
* Un mismo servidor que aparece en varias filas se consulta UNA sola vez con la unión de
  sus fechas, y el resultado se reparte a cada fila. Esto reduce mucho el número de prompts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from .agenda import muestrear_fechas
from .control import FilaControl
from .servidores import Servidor


@dataclass
class Unidad:
    id: str
    servidor: dict                     # Servidor.a_dict()
    fechas: list[str] = field(default_factory=list)   # ISO; vacío si es delegada
    delegada: bool = False
    agenda: str = ""                   # texto de agenda (para delegadas)
    regla: str = ""
    n: int = 0
    filas: list[int] = field(default_factory=list)

    @property
    def srv(self) -> Servidor:
        return Servidor.desde_dict(self.servidor)

    @property
    def peso(self) -> int:
        return self.n if self.delegada else len(self.fechas)


@dataclass
class FilaPlan:
    fila_excel: int
    datos: dict
    servidor_texto: str
    agenda_texto: str
    regla: str
    regla_nota: str
    origen: str                         # "Python" | "Copilot"
    n: int
    fechas: list[str]                   # ISO (Python); vacío si delegada
    nota: str
    servidores: list[dict]
    unidades: list[str] = field(default_factory=list)


@dataclass
class Plan:
    filas: list[FilaPlan]
    unidades: dict[str, Unidad]
    parametros: dict

    def a_dict(self) -> dict:
        return {
            "filas": [asdict(f) for f in self.filas],
            "unidades": {k: asdict(u) for k, u in self.unidades.items()},
            "parametros": self.parametros,
        }

    @staticmethod
    def desde_dict(d: dict) -> "Plan":
        return Plan(
            filas=[FilaPlan(**f) for f in d["filas"]],
            unidades={k: Unidad(**u) for k, u in d["unidades"].items()},
            parametros=d["parametros"],
        )

    # ---- métricas para la UI ----
    def resumen(self) -> dict:
        n_fechas = sum(u.peso for u in self.unidades.values())
        return {
            "filas": len(self.filas),
            "filas_sin_servidor": sum(1 for f in self.filas if not f.servidores),
            "unidades": len(self.unidades),
            "servidores": len({u.srv.clave for u in self.unidades.values()}),
            "fechas": n_fechas,
            "delegadas": sum(1 for u in self.unidades.values() if u.delegada),
        }


def construir_plan(filas_control: list[FilaControl], repeticiones: dict, inicio: date, fin: date,
                   semilla: str, modo: str = "hibrido", forzar_copilot: list | None = None,
                   fechas_por_unidad: int = 30) -> Plan:
    forzar = set(forzar_copilot or [])
    filas_plan: list[FilaPlan] = []
    # agrupadores
    por_servidor: dict[str, dict] = {}            # clave srv -> {"srv": Servidor, "fechas": {iso: set(filas)}}
    delegadas: dict[tuple, dict] = {}             # (srv, regla, n) -> {...}

    for f in filas_control:
        regla = f.regla
        n = int(repeticiones.get(regla.clave, 0) or 0)
        if n <= 0:
            continue
        delegar = (modo == "copilot") or (not regla.reconocida) or (regla.clave in forzar)
        fechas_iso: list[str] = []
        nota = ""
        if not delegar:
            fechas, nota = muestrear_fechas(regla, inicio, fin, n, f"{semilla}|{f.fila_excel}")
            fechas_iso = [d.isoformat() for d in fechas]
        fp = FilaPlan(
            fila_excel=f.fila_excel, datos=f.datos, servidor_texto=f.servidor_texto,
            agenda_texto=f.agenda_texto, regla=regla.descripcion, regla_nota=regla.nota,
            origen="Copilot" if delegar else "Python", n=n, fechas=fechas_iso, nota=nota,
            servidores=[s.a_dict() for s in f.servidores],
        )
        filas_plan.append(fp)
        for s in f.servidores:
            if delegar:
                k = (s.clave, regla.clave, n)
                g = delegadas.setdefault(k, {"srv": s, "agenda": " ".join(f.agenda_texto.split()),
                                             "regla": regla.descripcion, "n": n, "filas": []})
                g["filas"].append(f.fila_excel)
            elif fechas_iso:
                g = por_servidor.setdefault(s.clave, {"srv": s, "fechas": {}})
                for a in s.alias:                           # unir alias vistos en otras filas
                    if a.upper() not in {x.upper() for x in g["srv"].alias}:
                        g["srv"].alias.append(a)
                for iso in fechas_iso:
                    g["fechas"].setdefault(iso, set()).add(f.fila_excel)

    unidades: dict[str, Unidad] = {}
    contador = 0

    def nuevo_id() -> str:
        nonlocal contador
        contador += 1
        return f"U{contador:04d}"

    for clave in sorted(por_servidor):
        g = por_servidor[clave]
        fechas = sorted(g["fechas"])
        for i in range(0, len(fechas), max(1, fechas_por_unidad)):
            trozo = fechas[i:i + fechas_por_unidad]
            filas = sorted({fl for iso in trozo for fl in g["fechas"][iso]})
            u = Unidad(id=nuevo_id(), servidor=g["srv"].a_dict(), fechas=trozo, filas=filas)
            unidades[u.id] = u
    for k in sorted(delegadas, key=lambda x: (x[0], x[1])):
        g = delegadas[k]
        u = Unidad(id=nuevo_id(), servidor=g["srv"].a_dict(), delegada=True, agenda=g["agenda"],
                   regla=g["regla"], n=g["n"], filas=sorted(set(g["filas"])))
        unidades[u.id] = u

    # vincular filas -> unidades
    idx_filas = {fp.fila_excel: fp for fp in filas_plan}
    for u in unidades.values():
        for fl in u.filas:
            idx_filas[fl].unidades.append(u.id)

    parametros = {
        "inicio": inicio.isoformat(), "fin": fin.isoformat(), "semilla": semilla, "modo": modo,
        "repeticiones": {k: v for k, v in repeticiones.items() if v}, "forzar_copilot": sorted(forzar),
    }
    return Plan(filas=filas_plan, unidades=unidades, parametros=parametros)


def armar_lote(pendientes: list[Unidad], max_unidades: int, max_fechas: int) -> list[Unidad]:
    """Toma unidades del inicio de la lista sin pasarse de los límites (siempre al menos una)."""
    lote: list[Unidad] = []
    peso = 0
    for u in pendientes:
        if lote and (len(lote) >= max_unidades or peso + u.peso > max_fechas):
            break
        lote.append(u)
        peso += u.peso
    return lote
