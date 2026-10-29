"""
Interpretación de la columna AGENDA.

Convierte textos libres como "Primer  Domingo del mes", "Lunes - Sábado",
"Días 7, 15, 21 y 30 del \\nmes" o "Domingos menos el primero del mes" en una
regla de calendario que Python puede evaluar día por día.

Si el texto no se puede interpretar con seguridad (ej. "Por demanda",
"A PARTIR TERCER VIERNES DEL MES") la regla queda como NO reconocida y la
asignación de fechas se delega a Copilot.
"""
from __future__ import annotations

import calendar
import random
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

DIAS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}
NOMBRE_DIA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
NOMBRE_DIA_PLURAL = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábados", "domingos"]

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
NOMBRE_MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
              "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# ordinal -> número (-1 = último)
ORDINALES = {
    "primer": 1, "primero": 1, "primera": 1, "1er": 1, "1ro": 1, "1ero": 1, "1o": 1,
    "segundo": 2, "segunda": 2, "2do": 2, "2o": 2,
    "tercer": 3, "tercero": 3, "tercera": 3, "3er": 3, "3ro": 3, "3o": 3,
    "cuarto": 4, "cuarta": 4, "4to": 4, "4o": 4,
    "quinto": 5, "quinta": 5, "5to": 5,
    "ultimo": -1, "ultima": -1,
}
NOMBRE_ORDINAL = {1: "primer", 2: "segundo", 3: "tercer", 4: "cuarto", 5: "quinto", -1: "último"}

_RE_DIA = r"(lunes|martes|miercoles|jueves|viernes|sabados?|domingos?)"


def normalizar(texto: str) -> str:
    """minúsculas, sin tildes, espacios colapsados."""
    if texto is None:
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("\xa0", " ").lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _dia_idx(palabra: str) -> int:
    palabra = palabra.rstrip("s") if palabra not in ("lunes", "martes", "miercoles", "jueves", "viernes") else palabra
    return DIAS[palabra]


def _unir(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " y " + items[-1]


def nesimo_dia_semana(anio: int, mes: int, dia_semana: int, n: int) -> Optional[date]:
    """n-ésimo (1..5, -1 = último) día_semana del mes; None si no existe."""
    dias_mes = calendar.monthrange(anio, mes)[1]
    candidatos = [date(anio, mes, d) for d in range(1, dias_mes + 1)
                  if date(anio, mes, d).weekday() == dia_semana]
    if n == -1:
        return candidatos[-1]
    if 1 <= n <= len(candidatos):
        return candidatos[n - 1]
    return None


@dataclass
class Regla:
    """Regla de calendario derivada de una AGENDA."""
    descripcion: str                      # texto canónico, también sirve de clave de agrupación
    reconocida: bool
    predicado: Optional[Callable[[date], bool]] = field(default=None, repr=False)
    nota: str = ""                        # aclaración (supuesto aplicado o por qué se delega)

    @property
    def clave(self) -> str:
        return self.descripcion

    def dias_en_rango(self, inicio: date, fin: date) -> list[date]:
        if not self.reconocida or self.predicado is None:
            return []
        out, d = [], inicio
        while d <= fin:
            if self.predicado(d):
                out.append(d)
            d += timedelta(days=1)
        return out


def _delegada(desc: str, nota: str) -> Regla:
    return Regla(descripcion=desc, reconocida=False, predicado=None, nota=nota)


def interpretar_agenda(texto: str) -> Regla:
    original = (texto or "").strip()
    t = normalizar(texto)
    if not t:
        return _delegada("(vacía)", "La agenda está vacía")

    # --- casos que no son un calendario fijo ---------------------------------
    if "demanda" in t or "manual" in t or "eventual" in t:
        return _delegada("Por demanda", "Agenda no periódica: Copilot reporta fechas con evidencia")
    if "a partir" in t or "desde" in t:
        return _delegada(f"Ambigua: {original}", "Texto ambiguo ('a partir de...'): se delega a Copilot")

    # --- diario ---------------------------------------------------------------
    if re.search(r"\bdiari", t) or "todos los dias" in t or "cada dia" in t:
        return Regla("Diario", True, lambda d: True)

    # --- mes específico ("del mes de marzo", "de marzo") ----------------------
    mes_fijo = None
    m_mes = re.search(r"\bde (" + "|".join(MESES) + r")\b", t)
    if m_mes:
        mes_fijo = MESES[m_mes.group(1)]
    anual = bool(re.search(r"\bdel (ano|anio)\b|cada (ano|anio)|\banual", t))

    dias_semana = [(m.start(), _dia_idx(m.group(1))) for m in re.finditer(r"\b" + _RE_DIA + r"\b", t)]

    # --- días del mes (sin nombre de día de semana) ---------------------------
    if not dias_semana:
        if re.search(r"\b(primer|primero|1er|1ro) dia\b", t):
            return Regla("Día 1 de cada mes", True, lambda d: d.day == 1)
        if "fin de mes" in t or re.search(r"\bultimo dia\b", t):
            return Regla("Último día de cada mes", True,
                         lambda d: d.day == calendar.monthrange(d.year, d.month)[1])
        nums = [int(n) for n in re.findall(r"\b(\d{1,2})(?:ro|ero|er|o|vo|to|do)?\b", t)]
        nums = sorted({n for n in nums if 1 <= n <= 31})
        if nums and ("mes" in t or re.search(r"\b(dia|dias|el|todos los|cada)\b", t)):
            desc = ("Día " if len(nums) == 1 else "Días ") + _unir([str(n) for n in nums]) + " de cada mes"
            nota = ""
            if any(n > 28 for n in nums):
                nota = "En meses sin ese día (ej. 30 en febrero) no se genera fecha"
            return Regla(desc, True, lambda d, s=frozenset(nums): d.day in s, nota)
        if re.search(r"\bsemanal", t):
            return _delegada("Semanal (sin día)", "No indica el día de la semana: se delega a Copilot")
        if re.search(r"\bmensual", t):
            return _delegada("Mensual (sin día)", "No indica el día del mes: se delega a Copilot")
        return _delegada(f"No reconocida: {original}", "No se pudo interpretar: se delega a Copilot")

    # --- ordinales ("primer y tercer lunes", "2,3,4 lunes", "último sábado") --
    primer_pos = dias_semana[0][0]
    antes = t[:primer_pos]
    ordinales: list[int] = []
    for tok in re.findall(r"[a-z0-9]+", antes):
        if tok in ORDINALES:
            ordinales.append(ORDINALES[tok])
        elif tok.isdigit() and 1 <= int(tok) <= 5:
            ordinales.append(int(tok))
    excluir_primero = bool(re.search(r"(menos|excepto|salvo) el primer", t))
    if excluir_primero:
        ordinales = []

    dias_unicos = sorted({d for _, d in dias_semana})

    if ordinales:
        if len(dias_unicos) != 1:
            return _delegada(f"No reconocida: {original}", "Ordinal con varios días de semana: se delega")
        ds = dias_unicos[0]
        ords = sorted(set(ordinales), key=lambda x: (x == -1, x))
        etiqueta = _unir([NOMBRE_ORDINAL[o] for o in ords]) + " " + NOMBRE_DIA[ds]
        if mes_fijo:
            desc = f"{etiqueta.capitalize()} de {NOMBRE_MES[mes_fijo]} (anual)"
            meses = {mes_fijo}
        elif anual:
            desc = f"{etiqueta.capitalize()} del año"
            meses = {1}
        else:
            desc = f"{etiqueta.capitalize()} del mes"
            meses = None

        def pred(d: date, ds=ds, ords=tuple(ords), meses=meses) -> bool:
            if d.weekday() != ds or (meses and d.month not in meses):
                return False
            return any(nesimo_dia_semana(d.year, d.month, ds, o) == d for o in ords)

        nota = "" if ("mes" in t or mes_fijo or anual) else "Se asumió 'del mes'"
        return Regla(desc, True, pred, nota)

    # --- rangos ("lunes a sábado", "lunes - sábado", "sábado a jueves") -------
    m_rango = re.search(r"\b" + _RE_DIA + r"\s*(?:a|al|-|hasta)\s*" + _RE_DIA + r"\b", t)
    if m_rango:
        a, b = _dia_idx(m_rango.group(1)), _dia_idx(m_rango.group(2))
        dias = set()
        i = a
        while True:
            dias.add(i)
            if i == b:
                break
            i = (i + 1) % 7
        desc = f"{NOMBRE_DIA[a].capitalize()} a {NOMBRE_DIA[b]}"
        return Regla(desc, True, lambda d, s=frozenset(dias): d.weekday() in s)

    # --- "domingos menos el primero del mes" ----------------------------------
    if excluir_primero and len(dias_unicos) == 1:
        ds = dias_unicos[0]
        desc = f"{NOMBRE_DIA_PLURAL[ds].capitalize()} excepto el primero del mes"
        return Regla(desc, True,
                     lambda d, ds=ds: d.weekday() == ds and nesimo_dia_semana(d.year, d.month, ds, 1) != d)

    # --- días de semana simples ("sábado", "domingos", "martes y jueves") -----
    if len(dias_unicos) == 1:
        desc = f"Cada {NOMBRE_DIA[dias_unicos[0]]}"
    else:
        desc = "Cada " + _unir([NOMBRE_DIA[d] for d in dias_unicos])
    return Regla(desc, True, lambda d, s=frozenset(dias_unicos): d.weekday() in s)


def muestrear_fechas(regla: Regla, inicio: date, fin: date, n: int, semilla: str) -> tuple[list[date], str]:
    """
    Muestra aleatoria reproducible de n fechas válidas para la regla.
    Retorna (fechas_ordenadas, nota).
    """
    if n <= 0 or not regla.reconocida:
        return [], ""
    poblacion = regla.dias_en_rango(inicio, fin)
    if not poblacion:
        return [], "No hay días que cumplan la agenda dentro del rango"
    nota = ""
    if n > len(poblacion):
        nota = f"Se pidieron {n} fechas pero el rango solo tiene {len(poblacion)} válidas"
        n = len(poblacion)
    rng = random.Random(semilla)
    return sorted(rng.sample(poblacion, n)), nota
