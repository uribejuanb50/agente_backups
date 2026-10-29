"""
Prompt para Copilot + lectura de su respuesta.

CONTRATO DE SALIDA
------------------
Copilot debe responder con un bloque de texto así (una línea por servidor y fecha):

    @@INICIO-K7Q2@@
    S01 ; 05/07/2026 ; EXACTA ; 05/07/2026 ; Respaldo COMHP36 05-07.xlsx
    S01 ; 12/07/2026 ; CERCANA ; 13/07/2026 ; RE: Backup semanal.msg
    S02 ; 19/07/2026 ; NO_ENCONTRADA ; - ; -
    @@FIN-K7Q2@@

Decisiones de diseño:
* El código del lote (K7Q2) es aleatorio y NO aparece pegado a los marcadores dentro del
  prompt. Así, al leer la página, el texto del prompt (que también se ve en el chat) nunca
  se confunde con la respuesta.
* Los IDs son locales y cortos (S01, S02...) para que Copilot no se equivoque copiándolos.
* Copilot solo hace lo que hace bien (buscar). La decisión final (SI / PARCIAL / CERCANA /
  NO COINCIDEN FECHAS) la toma Python, que además corrige incoherencias (ej. dice EXACTA
  pero la fecha encontrada es otra).
* El parser tolera que Copilot ignore el bloque de código, use "|" en vez de ";", agregue
  negritas o viñetas, o junte varias líneas en un párrafo.
"""
from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass, field
from datetime import date, datetime

from .planificador import Unidad

ALFABETO = "".join(c for c in string.ascii_uppercase + string.digits if c not in "O0I1L")
ESTADOS = ("EXACTA", "CERCANA", "NO_ENCONTRADA")


def generar_codigo() -> str:
    return "".join(random.choice(ALFABETO) for _ in range(4))


def _fmt(iso: str | date) -> str:
    d = date.fromisoformat(iso) if isinstance(iso, str) else iso
    return d.strftime("%d/%m/%Y")


MODOS_PROMPT = ("denso", "compacto", "detallado")


def _fechas_por_mes(fechas_iso: list[str]) -> str:
    """['2026-08-02','2026-08-09','2026-09-04'] -> '2026-08[02,09] 2026-09[04]'"""
    grupos: dict[str, list[str]] = {}
    for iso in sorted(fechas_iso):
        grupos.setdefault(iso[:7], []).append(iso[8:10])
    return " ".join(f"{mes}[{','.join(dias)}]" for mes, dias in grupos.items())


def _item_denso(sid: str, u: Unidad) -> str:
    s = u.srv
    nombre = s.nombre + (f" ({', '.join(s.alias)})" if s.alias else "")
    if u.delegada:
        return f'{sid} {nombre}: ELEGIR {u.n} · agenda "{u.agenda}"'
    return f"{sid} {nombre}: {_fechas_por_mes(u.fechas)}"


def _prompt_denso(lote: list[Unidad], carpeta: str, inicio: date, fin: date, ventana: int,
                  codigo: str) -> tuple[str, dict[str, str]]:
    """Formato para lotes grandes: fechas agrupadas por mes y UNA línea de respuesta por servidor."""
    mapa = {f"S{i:02d}": u.id for i, u in enumerate(lote, start=1)}
    items = [_item_denso(sid, u) for sid, u in zip(mapa, lote)]
    deleg = ""
    if any(u.delegada for u in lote):
        deleg = (f'\nÍtems "ELEGIR n": elige tú n fechas entre {_fmt(inicio)} y {_fmt(fin)} que cumplan la agenda '
                 f'indicada (si es "por demanda", fechas en que sí haya evidencia) y repórtalas igual que las demás.')
    texto = (
        f'Actúa como auditor de backups. Revisa SOLO los correos y adjuntos de la carpeta "{carpeta}" '
        f"(incluidas subcarpetas). Sin evidencia = sin backup: no inventes.\n"
        f"Evidencia = correo (asunto o cuerpo) o adjunto (Excel, CSV, PDF, TXT, imagen…) que muestre el backup "
        f"del servidor ese día; el servidor puede aparecer con cualquiera de los nombres entre paréntesis.\n"
        f"Para cada fecha pedida: ¿hay evidencia ese mismo día? Si no, busca la fecha con evidencia más cercana "
        f"(máximo {ventana} días antes o después).\n"
        f"Las fechas van agrupadas por mes: 2026-08[02,09] significa 02/08/2026 y 09/08/2026.{deleg}\n\n"
        f"ÍTEMS ({len(lote)}):\n" + "\n".join(items) + "\n\n"
        f"RESPONDE SOLO con un bloque de código, sin texto antes ni después, así:\n"
        f"@@INICIO-<CÓDIGO>@@\n"
        f"Sxx ; SI=dd/mm,dd/mm ; CERCA=dd/mm>dd/mm ; NO=dd/mm ; EV=correo o adjunto\n"
        f"@@FIN-<CÓDIGO>@@\n"
        f"Reglas: UNA línea por ítem, con su ID (S01, S02…). SI = fechas pedidas con evidencia ese día. "
        f"CERCA = fecha pedida>fecha encontrada. NO = sin evidencia. Cada fecha pedida va en SI, CERCA o NO; "
        f"deja vacío lo que no aplique (ej. CERCA=). EV = un nombre corto de correo o adjunto, o -. "
        f"Incluye TODOS los ítems.\n"
        f"Código de este lote: {codigo}"
    )
    return texto, mapa


def construir_prompt(lote: list[Unidad], carpeta: str, inicio: date, fin: date, ventana: int,
                     codigo: str, compacto: bool = False, modo: str | None = None) -> tuple[str, dict[str, str]]:
    """Retorna (texto_prompt, mapa_id_local -> id_unidad).
    modo: "denso" (lotes grandes, 1 línea por servidor), "compacto" o "detallado" (1 línea por fecha)."""
    modo = modo or ("compacto" if compacto else "detallado")
    if modo == "denso":
        return _prompt_denso(lote, carpeta, inicio, fin, ventana, codigo)
    compacto = modo == "compacto"
    mapa: dict[str, str] = {}
    lineas_items: list[str] = []
    hay_delegadas = any(u.delegada for u in lote)
    for i, u in enumerate(lote, start=1):
        sid = f"S{i:02d}"
        mapa[sid] = u.id
        s = u.srv
        nombre = s.nombre + (f" (también como: {', '.join(s.alias)})" if s.alias else "")
        if u.delegada:
            lineas_items.append(f'{sid} | servidor: {nombre} | ELEGIR {u.n} fechas | agenda: "{u.agenda}"')
        else:
            lineas_items.append(f"{sid} | servidor: {nombre} | fechas: {', '.join(_fmt(x) for x in u.fechas)}")

    if compacto:
        deleg = (f'\nÍtems "ELEGIR n fechas": elige tú n fechas entre {_fmt(inicio)} y {_fmt(fin)} según la agenda '
                 f'(si es "por demanda", fechas con evidencia).') if hay_delegadas else ""
        texto = (
            f'Auditor de backups. Busca SOLO en los correos y adjuntos de la carpeta "{carpeta}". '
            f"No inventes: sin evidencia = sin backup.\n"
            f"Para cada ítem y fecha: ¿hay evidencia de backup del servidor (con cualquiera de sus nombres) ese día? "
            f"Si no, da la fecha con evidencia más cercana (máx. {ventana} días).{deleg}\n"
            f"ÍTEMS:\n{chr(10).join(lineas_items)}\n"
            f"RESPONDE SOLO con un bloque de código, sin texto adicional:\n"
            f"@@INICIO-<CÓDIGO>@@\nID ; FECHA_PEDIDA ; ESTADO ; FECHA_ENCONTRADA ; EVIDENCIA\n@@FIN-<CÓDIGO>@@\n"
            f"Una línea por fecha de cada ítem. Fechas dd/mm/aaaa. ESTADO: EXACTA (misma fecha), "
            f"CERCANA (otra fecha a máx. {ventana} días) o NO_ENCONTRADA (FECHA_ENCONTRADA = -). "
            f"EVIDENCIA: nombre del correo o adjunto (sin ;) o -.\n"
            f"Código de este lote: {codigo}"
        )
        return texto, mapa

    bloque_delegadas = ""
    if hay_delegadas:
        bloque_delegadas = (
            f'\nLos ítems marcados "ELEGIR n fechas" no traen fechas: elige tú n fechas distintas entre '
            f"{_fmt(inicio)} y {_fmt(fin)} que correspondan a la agenda indicada, y evalúa cada una igual que "
            f'las demás. Si la agenda no es periódica (por ejemplo "por demanda"), elige fechas de ese rango '
            f"en las que sí haya evidencia.\n"
        )

    texto = f"""Actúa como auditor de respaldos (backups) de servidores.

FUENTE: usa únicamente los correos y adjuntos de la carpeta "{carpeta}" (incluidas sus subcarpetas). No uses conocimiento general ni inventes datos: si no ves evidencia, esa fecha NO tiene respaldo.

QUÉ CUENTA COMO EVIDENCIA: un correo (asunto o cuerpo) o cualquier adjunto (Excel, CSV, PDF, TXT, HTML, imagen o comprimido) que muestre que el servidor tuvo backup ese día. El servidor puede aparecer con cualquiera de sus nombres, en mayúsculas o minúsculas, o dentro de un nombre más largo. La fecha que vale es la de ejecución del backup que dice el reporte; si el reporte no la dice, usa la fecha del correo.

TAREA: para cada ítem y cada fecha pedida, revisa si hay evidencia ese mismo día. Si no la hay, busca la fecha con evidencia más cercana a la pedida (antes o después), como máximo a {ventana} días.
{bloque_delegadas}
ÍTEMS ({len(lote)}):
{chr(10).join(lineas_items)}

FORMATO DE RESPUESTA (OBLIGATORIO: un programa lee tu respuesta y cualquier otro formato se pierde)
1. Responde solo con un bloque de código de texto plano. Nada antes ni después: sin saludo, sin explicación, sin tablas, sin viñetas, sin negritas y sin referencias.
2. Primera línea del bloque: @@INICIO-<CÓDIGO>@@
3. Luego, exactamente una línea por cada fecha de cada ítem, con 5 campos separados por " ; " en este orden:
ID ; FECHA_PEDIDA ; ESTADO ; FECHA_ENCONTRADA ; EVIDENCIA
4. Última línea del bloque: @@FIN-<CÓDIGO>@@
5. <CÓDIGO> es el código de este lote, que aparece al final de este mensaje.

Reglas de cada campo:
- ID: el del ítem, tal cual (S01, S02...).
- FECHA_PEDIDA y FECHA_ENCONTRADA: en formato dd/mm/aaaa.
- ESTADO: solo una de estas tres palabras:
  EXACTA = hay evidencia en la fecha pedida (FECHA_ENCONTRADA es igual a FECHA_PEDIDA).
  CERCANA = no hay en la fecha pedida, pero sí en otra fecha a máximo {ventana} días (FECHA_ENCONTRADA es esa otra fecha).
  NO_ENCONTRADA = no hay evidencia ni ese día ni cerca (FECHA_ENCONTRADA es -).
- EVIDENCIA: nombre del correo o adjunto donde lo viste (máximo 80 caracteres y sin ";"), o - si no hay.
- Si un ítem tiene 4 fechas, escribe 4 líneas para ese ítem. Escribe todas las líneas, también las NO_ENCONTRADA.

Ejemplo solo de la forma (datos y código ficticios):
```
@@INICIO-ZZ99@@
S99 ; 03/02/2026 ; EXACTA ; 03/02/2026 ; Respaldo diario SRVX 03-02-2026.xlsx
S99 ; 10/02/2026 ; CERCANA ; 11/02/2026 ; RE: Backup semanal SRVX.msg
S99 ; 17/02/2026 ; NO_ENCONTRADA ; - ; -
@@FIN-ZZ99@@
```

Código de este lote: {codigo}"""
    return texto, mapa


def ancla_prompt(codigo: str) -> str:
    """Texto final del prompt; lo que aparezca después de él en la página es la respuesta."""
    return f"Código de este lote: {codigo}"


# ----------------------------------------------------------------------------------------
# Lectura de la respuesta
# ----------------------------------------------------------------------------------------
_FECHA = r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}"
RE_LINEA = re.compile(
    r"(?P<id>S\d{1,3})\s*[;|]\s*"
    r"(?P<fp>" + _FECHA + r")\s*[;|]\s*"
    r"(?P<est>EXACTA|CERCANA|NO[\s_\-]?ENCONTRADA)\s*"
    r"(?:[;|]\s*(?P<fe>" + _FECHA + r"|-+|—|–|N/?A|ninguna|no aplica)?\s*)?"
    r"(?:[;|]\s*(?P<ev>[^\n]*))?$",
    re.I | re.M,
)


@dataclass
class LineaRespuesta:
    id_local: str
    fecha_pedida_txt: str
    estado: str
    fecha_encontrada_txt: str
    evidencia: str


@dataclass
class LineaDensa:
    id_local: str
    si: list[str]
    cerca: list[tuple[str, str]]
    no: list[str]
    evidencia: str


@dataclass
class RespuestaLote:
    hay_inicio: bool = False
    hay_fin: bool = False
    bloque: str = ""
    lineas: list[LineaRespuesta] = field(default_factory=list)
    densas: list[LineaDensa] = field(default_factory=list)
    aviso: str = ""

    @property
    def total(self) -> int:
        return len(self.lineas) + len(self.densas)


def _re_marca(tipo: str, codigo: str) -> re.Pattern:
    return re.compile(r"@@\s*" + tipo + r"\s*[-_ ]?\s*" + re.escape(codigo) + r"\s*@@", re.I)


def extraer_respuesta(texto_pagina: str, codigo: str) -> RespuestaLote:
    """Busca el bloque de este lote dentro del texto visible de la página/panel."""
    r = RespuestaLote()
    if not texto_pagina:
        return r
    ini = list(_re_marca("INICIO", codigo).finditer(texto_pagina))
    if ini:
        r.hay_inicio = True
        desde = ini[-1].end()
        m_fin = _re_marca("FIN", codigo).search(texto_pagina, desde)
        if m_fin:
            r.hay_fin = True
            r.bloque = texto_pagina[desde:m_fin.start()]
        else:
            r.bloque = texto_pagina[desde:]
    else:
        # plan B: Copilot ignoró los marcadores -> leer lo que está después del prompt
        ancla = ancla_prompt(codigo)
        pos = texto_pagina.rfind(ancla)
        if pos >= 0:
            r.bloque = texto_pagina[pos + len(ancla):]
            r.aviso = "Copilot no usó los marcadores; se leyó el texto posterior al prompt"
    r.lineas = parsear_lineas(r.bloque)
    r.densas = parsear_densas(r.bloque)
    return r


def _limpiar(bloque: str) -> str:
    b = bloque.replace("\r", "\n").replace(" ", " ")
    b = re.sub(r"[`*]+", "", b)                          # backticks y negritas
    b = re.sub(r"(?m)^\s*(?:[-•·>]|\d+[.)])\s+", "", b)  # viñetas o numeración
    # si Copilot juntó líneas en un párrafo, separar antes de cada "Sxx ;"
    b = re.sub(r"[ \t]+(?=S\d{1,3}\s*[;|]\s*\d)", "\n", b)
    return b


def parsear_lineas(bloque: str) -> list[LineaRespuesta]:
    out: list[LineaRespuesta] = []
    for m in RE_LINEA.finditer(_limpiar(bloque)):
        est = re.sub(r"[\s\-]+", "_", m.group("est").upper())
        if est == "NOENCONTRADA":
            est = "NO_ENCONTRADA"
        ev = (m.group("ev") or "").strip().strip("-").strip()
        ev = re.sub(r"\s*\[\d+\]\s*$", "", ev)            # referencias tipo [1]
        out.append(LineaRespuesta(
            id_local=m.group("id").upper(),
            fecha_pedida_txt=m.group("fp"),
            estado=est,
            fecha_encontrada_txt=(m.group("fe") or "").strip(),
            evidencia=ev,
        ))
    # normalizar IDs tipo S1 -> S01
    for ln in out:
        num = int(ln.id_local[1:])
        ln.id_local = f"S{num:02d}"
    return out


_DM = r"\d{1,2}/\d{1,2}(?:/\d{2,4})?"
RE_DENSA = re.compile(r"(?P<id>S\d{1,3})\s*[;|:]\s*(?P<resto>(?:SI|CERCA|NO|EV)\s*=.*)$", re.I | re.M)


def _campo(resto: str, nombre: str) -> str:
    m = re.search(r"\b" + nombre + r"\s*=\s*([^;|]*)", resto, re.I)
    return m.group(1).strip() if m else ""


def parsear_densas(bloque: str) -> list[LineaDensa]:
    b = bloque.replace("\r", "\n").replace("\u00a0", " ")
    b = re.sub(r"[`*]+", "", b)
    b = re.sub(r"(?m)^\s*(?:[-•·>]|\d+[.)])\s+", "", b)
    b = b.replace("→", ">").replace("->", ">").replace("=>", ">")
    b = re.sub(r"[ \t]+(?=S\d{1,3}\s*[;|:]\s*(?:SI|CERCA|NO|EV)\s*=)", "\n", b, flags=re.I)
    out = []
    for m in RE_DENSA.finditer(b):
        resto = m.group("resto")
        ev_m = re.search(r"\bEV\s*=\s*(.*)$", resto, re.I)
        ev = (ev_m.group(1) if ev_m else "").strip().strip("-").strip()
        ev = re.sub(r"\s*\[\d+\]\s*$", "", ev)
        out.append(LineaDensa(
            id_local=f"S{int(m.group('id')[1:]):02d}",
            si=re.findall(_DM, _campo(resto, "SI")),
            cerca=re.findall(r"(" + _DM + r")\s*>\s*(" + _DM + r")", _campo(resto, "CERCA")),
            no=re.findall(_DM, _campo(resto, "NO")),
            evidencia=ev[:200],
        ))
    return out


def _resolver_dm(txt: str, candidatas: list[date], cerca_de: date | None = None,
                 inicio: date | None = None, fin: date | None = None) -> date | None:
    """'25/08' -> date. Usa las fechas pedidas, la fecha de referencia o el rango para deducir el año."""
    partes = txt.split("/")
    try:
        d, m = int(partes[0]), int(partes[1])
        if len(partes) > 2:
            y = int(partes[2])
            return date(y + 2000 if y < 100 else y, m, d)
    except (ValueError, IndexError):
        return None
    for c in candidatas:
        if c.day == d and c.month == m:
            return c
    anios = []
    if cerca_de:
        anios = [cerca_de.year, cerca_de.year - 1, cerca_de.year + 1]
    elif inicio and fin:
        anios = list(range(fin.year, inicio.year - 1, -1))
    mejores = []
    for y in anios:
        try:
            f = date(y, m, d)
        except ValueError:
            continue
        if cerca_de:
            mejores.append((abs((f - cerca_de).days), f))
        elif inicio <= f <= fin:
            return f
    return min(mejores)[1] if mejores else None


def expandir_densas(densas: list[LineaDensa], mapa: dict[str, str], vistas: dict[str, Unidad],
                    inicio: date, fin: date) -> list[LineaRespuesta]:
    """Convierte cada línea densa (1 por servidor) en líneas por fecha, para validarlas igual que las demás."""
    out: list[LineaRespuesta] = []
    for dl in densas:
        uid = mapa.get(dl.id_local)
        u = vistas.get(uid) if uid else None
        if u is None:
            out.append(LineaRespuesta(dl.id_local, "", "NO_ENCONTRADA", "", ""))   # validar avisará el ID raro
            continue
        pedidas = [date.fromisoformat(x) for x in u.fechas]

        def res(txt):
            return _resolver_dm(txt, pedidas, inicio=inicio, fin=fin)
        for txt in dl.si:
            f = res(txt)
            if f:
                out.append(LineaRespuesta(dl.id_local, f.isoformat(), "EXACTA", f.isoformat(), dl.evidencia))
        for a, b in dl.cerca:
            fa = res(a)
            fb = _resolver_dm(b, [], cerca_de=fa) if fa else None
            if fa:
                out.append(LineaRespuesta(dl.id_local, fa.isoformat(), "CERCANA", fb.isoformat() if fb else "", dl.evidencia))
        for txt in dl.no:
            f = res(txt)
            if f:
                out.append(LineaRespuesta(dl.id_local, f.isoformat(), "NO_ENCONTRADA", "", ""))
    return out


def cuantos_caben(vistas: list[Unidad], max_unidades: int, max_fechas: int, max_caracteres: int,
                  carpeta: str, inicio: date, fin: date, ventana: int, modo: str) -> int:
    """Cuántas unidades (desde el inicio de la lista) caben en un prompt sin pasar los límites."""
    limite_n, peso = 0, 0
    for u in vistas[:max_unidades]:
        if limite_n and peso + u.peso > max_fechas:
            break
        peso += u.peso
        limite_n += 1
    if limite_n <= 1:
        return limite_n
    largo = lambda k: len(construir_prompt(vistas[:k], carpeta, inicio, fin, ventana, "XXXX", modo=modo)[0])
    if largo(limite_n) <= max_caracteres:
        return limite_n
    lo, hi = 1, limite_n          # búsqueda binaria: el mayor k que cabe
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if largo(mid) <= max_caracteres:
            lo = mid
        else:
            hi = mid - 1
    return lo


def parsear_fecha(txt: str, preferidas: set[date] | None = None) -> date | None:
    """dd/mm/aaaa (preferido), aaaa-mm-dd, dd-mm-aa. Si hay ambigüedad dd/mm vs mm/dd usa 'preferidas'."""
    if not txt:
        return None
    txt = txt.strip()
    m = re.match(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$", txt)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", txt)
    if not m:
        return None
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    candidatos = []
    for d_, m_ in ((a, b), (b, a)):
        try:
            candidatos.append(date(y, m_, d_))
        except ValueError:
            pass
    if not candidatos:
        return None
    if preferidas:
        for c in candidatos:
            if c in preferidas:
                return c
    return candidatos[0]


# ----------------------------------------------------------------------------------------
# Validación (Python es el árbitro final)
# ----------------------------------------------------------------------------------------
@dataclass
class ResultadoFecha:
    unidad: str
    fecha_pedida: str           # ISO
    estado_copilot: str
    estado: str                 # EXACTA | CERCANA | NO_ENCONTRADA (validado)
    fecha_encontrada: str       # ISO o ""
    dif_dias: int | None
    evidencia: str
    nota: str = ""
    lote: int = 0


def validar_lineas(lineas: list[LineaRespuesta], mapa: dict[str, str], unidades: dict[str, Unidad],
                   inicio: date, fin: date, ventana: int, n_lote: int) -> tuple[list[ResultadoFecha], list[str]]:
    """Convierte las líneas de Copilot en resultados validados. Retorna (resultados, avisos)."""
    resultados: list[ResultadoFecha] = []
    avisos: list[str] = []
    vistos: set[tuple[str, str]] = set()
    conteo_delegadas: dict[str, int] = {}

    for ln in lineas:
        uid = mapa.get(ln.id_local)
        if not uid:
            avisos.append(f"ID desconocido en la respuesta: {ln.id_local}")
            continue
        u = unidades[uid]
        pedidas = {date.fromisoformat(x) for x in u.fechas}
        fp = parsear_fecha(ln.fecha_pedida_txt, pedidas or None)
        nota = []
        if fp is None:
            avisos.append(f"{ln.id_local}: fecha pedida ilegible '{ln.fecha_pedida_txt}'")
            continue
        if u.delegada:
            if not (inicio <= fp <= fin):
                avisos.append(f"{ln.id_local}: Copilot eligió {fp:%d/%m/%Y}, fuera del rango; se descarta")
                continue
            if conteo_delegadas.get(uid, 0) >= u.n:
                continue
        elif fp not in pedidas:
            avisos.append(f"{ln.id_local}: Copilot respondió una fecha no pedida ({fp:%d/%m/%Y}); se descarta")
            continue
        if (uid, fp.isoformat()) in vistos:
            continue

        fe = None
        if ln.fecha_encontrada_txt and re.search(r"\d", ln.fecha_encontrada_txt):
            fe = parsear_fecha(ln.fecha_encontrada_txt, {fp})
        estado = ln.estado
        if estado == "EXACTA":
            if fe is None:
                fe = fp
            elif fe != fp:
                estado = "CERCANA"
                nota.append("Copilot dijo EXACTA pero la fecha encontrada es otra; se corrigió a CERCANA")
        elif estado == "CERCANA":
            if fe is None:
                estado = "NO_ENCONTRADA"
                nota.append("Copilot dijo CERCANA sin dar fecha; se tomó como NO_ENCONTRADA")
            elif fe == fp:
                estado = "EXACTA"
                nota.append("Copilot dijo CERCANA con la misma fecha; se corrigió a EXACTA")
        else:
            fe = None
        dif = (fe - fp).days if fe else None
        if estado == "CERCANA" and dif is not None and abs(dif) > ventana:
            nota.append(f"La fecha cercana está a {abs(dif)} días (ventana {ventana})")

        vistos.add((uid, fp.isoformat()))
        if u.delegada:
            conteo_delegadas[uid] = conteo_delegadas.get(uid, 0) + 1
        resultados.append(ResultadoFecha(
            unidad=uid, fecha_pedida=fp.isoformat(), estado_copilot=ln.estado, estado=estado,
            fecha_encontrada=fe.isoformat() if fe else "", dif_dias=dif,
            evidencia=ln.evidencia[:200], nota="; ".join(nota), lote=n_lote,
        ))
    return resultados, avisos


def unidades_completas(resultados: list[ResultadoFecha], lote: list[Unidad]) -> tuple[list[str], list[str]]:
    """Separa unidades del lote en (completas, incompletas) según las líneas recibidas."""
    por_u: dict[str, set[str]] = {}
    for r in resultados:
        por_u.setdefault(r.unidad, set()).add(r.fecha_pedida)
    completas, incompletas = [], []
    for u in lote:
        recibidas = por_u.get(u.id, set())
        # delegadas: basta con que Copilot haya devuelto al menos una fecha elegida
        ok = bool(recibidas) if u.delegada else set(u.fechas) <= recibidas
        (completas if ok else incompletas).append(u.id)
    return completas, incompletas


def fechas_faltantes(u: Unidad, resultados: list[ResultadoFecha]) -> list[str]:
    recibidas = {r.fecha_pedida for r in resultados if r.unidad == u.id}
    return [f for f in u.fechas if f not in recibidas]


def ahora_txt() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
