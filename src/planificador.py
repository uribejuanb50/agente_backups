"""
Arma el plan de consultas a Copilot.

Fila del Excel  ->  (servidores, fechas)  ->  Unidades de consulta  ->  Lotes (1 prompt c/u)

* Modo "hibrido": Python calcula las fechas de cada fila (muestra aleatoria reproducible
  con semilla). Las filas cuya agenda no se entiende (ej. "Por demanda") se delegan a
  Copilot, que elige las N fechas.
* Modo "copilot": todas las filas se delegan.
* Criterio "cualquiera" (por defecto, lo que pide auditoría): los servidores de una fila son
  alternativos; una fecha cumple si aparece AL MENOS UNO de ellos. La unidad de consulta es el
  GRUPO de servidores de la fila. Filas con el mismo grupo se consultan juntas (unión de fechas).
* Criterio "cada_uno": cada servidor se verifica por separado (la fila cumple solo si todos
  cumplen). Un servidor repetido en varias filas se consulta una sola vez.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from .agenda import muestrear_fechas
from .control import FilaControl
from .servidores import Servidor


@dataclass
class Unidad:
    """Una consulta: un grupo de servidores (basta con que aparezca cualquiera) + sus fechas."""
    id: str
    servidores: list[dict]             # [Servidor.a_dict(), ...]
    fechas: list[str] = field(default_factory=list)   # ISO; vacío si es delegada
    delegada: bool = False
    agenda: str = ""                   # texto de agenda (para delegadas)
    regla: str = ""
    n: int = 0
    filas: list[int] = field(default_factory=list)

    @property
    def srvs(self) -> list[Servidor]:
        return [Servidor.desde_dict(x) for x in self.servidores]

    @property
    def srv(self) -> Servidor:            # compatibilidad: primer servidor del grupo
        return self.srvs[0]

    @property
    def clave(self) -> str:
        return clave_grupo(self.srvs)

    @property
    def etiqueta(self) -> str:
        return " / ".join(s.nombre for s in self.srvs)

    @property
    def peso(self) -> int:
        return self.n if self.delegada else len(self.fechas)


def clave_grupo(servidores) -> str:
    return "|".join(sorted({s.clave for s in servidores}))


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
            unidades={k: Unidad(**_compat_unidad(u)) for k, u in d["unidades"].items()},
            parametros=d["parametros"],
        )

    # ---- métricas para la UI ----
    def unidades_de_fila(self, fp: "FilaPlan") -> list[Unidad]:
        return [self.unidades[u] for u in fp.unidades if u in self.unidades]

    def resumen(self) -> dict:
        n_fechas = sum(u.peso for u in self.unidades.values())
        return {
            "filas": len(self.filas),
            "filas_sin_servidor": sum(1 for f in self.filas if not f.servidores),
            "unidades": len(self.unidades),
            "servidores": len({s.clave for u in self.unidades.values() for s in u.srvs}),
            "grupos": len({u.clave for u in self.unidades.values()}),
            "fechas": n_fechas,
            "delegadas": sum(1 for u in self.unidades.values() if u.delegada),
        }


def _compat_unidad(u: dict) -> dict:
    u = dict(u)
    if "servidor" in u:                  # avances guardados con la versión anterior
        u["servidores"] = [u.pop("servidor")]
    return u


def construir_plan(filas_control: list[FilaControl], repeticiones: dict, inicio: date, fin: date,
                   semilla: str, modo: str = "hibrido", forzar_copilot: list | None = None,
                   fechas_por_unidad: int = 30, criterio: str = "cualquiera") -> Plan:
    forzar = set(forzar_copilot or [])
    filas_plan: list[FilaPlan] = []
    por_grupo: dict[str, dict] = {}               # clave grupo -> {"srvs": [...], "fechas": {iso: set(filas)}}
    delegadas: dict[tuple, dict] = {}             # (grupo, regla, n) -> {...}

    def grupos_de(fila: FilaControl) -> list[list[Servidor]]:
        if not fila.servidores:
            return []
        return [fila.servidores] if criterio == "cualquiera" else [[s] for s in fila.servidores]

    def unir_alias(destino: list[Servidor], nuevos: list[Servidor]):
        por_clave = {s.clave: s for s in destino}
        for s in nuevos:
            d = por_clave.get(s.clave)
            if d is None:
                continue
            for a in s.alias:
                if a.upper() not in {x.upper() for x in d.alias}:
                    d.alias.append(a)

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
        for grupo in grupos_de(f):
            cg = clave_grupo(grupo)
            if delegar:
                k = (cg, regla.clave, n)
                g = delegadas.setdefault(k, {"srvs": [Servidor(s.nombre, list(s.alias)) for s in grupo],
                                             "agenda": " ".join(f.agenda_texto.split()),
                                             "regla": regla.descripcion, "n": n, "filas": []})
                unir_alias(g["srvs"], grupo)
                g["filas"].append(f.fila_excel)
            elif fechas_iso:
                g = por_grupo.setdefault(cg, {"srvs": [Servidor(s.nombre, list(s.alias)) for s in grupo], "fechas": {}})
                unir_alias(g["srvs"], grupo)
                for iso in fechas_iso:
                    g["fechas"].setdefault(iso, set()).add(f.fila_excel)

    unidades: dict[str, Unidad] = {}
    contador = 0

    def nuevo_id() -> str:
        nonlocal contador
        contador += 1
        return f"U{contador:04d}"

    for clave in sorted(por_grupo):
        g = por_grupo[clave]
        fechas = sorted(g["fechas"])
        for i in range(0, len(fechas), max(1, fechas_por_unidad)):
            trozo = fechas[i:i + fechas_por_unidad]
            filas = sorted({fl for iso in trozo for fl in g["fechas"][iso]})
            u = Unidad(id=nuevo_id(), servidores=[s.a_dict() for s in g["srvs"]], fechas=trozo, filas=filas)
            unidades[u.id] = u
    for k in sorted(delegadas, key=lambda x: (x[0], x[1])):
        g = delegadas[k]
        u = Unidad(id=nuevo_id(), servidores=[s.a_dict() for s in g["srvs"]], delegada=True, agenda=g["agenda"],
                   regla=g["regla"], n=g["n"], filas=sorted(set(g["filas"])))
        unidades[u.id] = u

    # vincular filas -> unidades
    idx_filas = {fp.fila_excel: fp for fp in filas_plan}
    for u in unidades.values():
        for fl in u.filas:
            idx_filas[fl].unidades.append(u.id)

    parametros = {
        "inicio": inicio.isoformat(), "fin": fin.isoformat(), "semilla": semilla, "modo": modo,
        "criterio": criterio,
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
