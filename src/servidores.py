"""
Extracción de servidores desde la columna SERVIDOR.

Mejora sobre manejo_servidores.py:
  * "WFM0393-LNXINSREP1", "TEM0205_AMXDEVECC01", "VHQ0426 - WTURNOS01PROD",
    "XQL0739 LNXRMMITPRD03" son UN servidor (código de inventario + hostname),
    no dos. Se busca por cualquiera de los dos nombres (alias).
  * Las IPs (entre paréntesis o sueltas) quedan como alias del servidor anterior,
    no como un servidor aparte.
  * Las notas "(Cubierto bajo el OLA 040)", "Servidores Web Usuario Final)",
    "DOMINIO: ...", "CLUSTER: ..." se descartan.
  * Hostnames partidos por salto de línea ("bogexaprvm1-\\nao73q4") se reparan.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Código de inventario: 2-4 letras + 3-10 dígitos (WFM0393, XLQ0001439, ZKQR305, TEM0205)
RE_CODIGO = re.compile(r"^[A-Z]{2,4}\d{3,10}$")
RE_IP = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
RE_TOKEN_HOST = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-\\]*$")

PALABRAS_NOTA = {
    "servidor", "servidores", "de", "del", "la", "el", "los", "las", "y", "web", "aplicacion",
    "usuario", "final", "capa", "resolutor", "autenticacion", "cubierto", "cubierta", "bajo",
    "ola", "ans", "base", "datos", "reportes", "logging", "monitoring", "txn", "atrium", "cmdb",
    "oci", "produccion", "bogota", "nodos", "nodo", "dominio", "cluster", "instancia", "vip",
    "y/o", "o", "bd", "db", "app", "prod", "qa", "dev", "ambiente",
}
ETIQUETAS_DESCARTAR_SIGUIENTE = {"dominio", "cluster", "instancia"}


@dataclass
class Servidor:
    nombre: str                       # nombre principal (hostname si existe, si no el código)
    alias: list[str] = field(default_factory=list)

    @property
    def clave(self) -> str:
        return self.nombre.upper()

    def todos_los_nombres(self) -> list[str]:
        vistos, out = set(), []
        for n in [self.nombre, *self.alias]:
            if n and n.upper() not in vistos:
                vistos.add(n.upper())
                out.append(n)
        return out

    def a_dict(self) -> dict:
        return {"nombre": self.nombre, "alias": list(self.alias)}

    @staticmethod
    def desde_dict(d: dict) -> "Servidor":
        return Servidor(d["nombre"], list(d.get("alias", [])))


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _es_hostname(tok: str) -> bool:
    if not tok or not RE_TOKEN_HOST.match(tok):
        return False
    base = _sin_tildes(tok).lower().strip(".")
    if base in PALABRAS_NOTA:
        return False
    if len(tok) < 4:
        return False
    tiene_digito = any(c.isdigit() for c in tok)
    if tiene_digito or "." in tok.strip("."):
        return True
    # sin dígitos: solo si es TODO mayúsculas (AMXDEVHANA) y no es palabra común
    letras = re.sub(r"[^A-Za-z]", "", tok)
    return letras.isupper() and len(letras) >= 6


def _servidor_desde_host(host: str, alias_extra: list[str] | None = None) -> Servidor:
    host = host.strip(".").strip()
    alias = list(alias_extra or [])
    nombre = host
    # instancia SQL "WCOSQLPRO03\SQL3"
    if "\\" in host:
        nombre = host.split("\\", 1)[0]
        alias.append(host)
    # FQDN "WFILE01.comcel.com.co" -> nombre corto si parece hostname
    elif "." in host:
        corto = host.split(".", 1)[0]
        if any(c.isdigit() for c in corto) and len(corto) >= 5:
            nombre = corto
            alias.append(host)
    return Servidor(nombre, alias)


def extraer_servidores(texto: str) -> list[Servidor]:
    if not texto or not isinstance(texto, str):
        return []
    t = texto.replace("\xa0", " ").replace("\r", "\n").strip().strip('"').strip("'")

    # 1) reparar hostnames partidos: "bogexaprvm1-\nao73q4" / "bogexaprvm1- ao73q1"
    t = re.sub(r"(?<=[a-z0-9])-[ \t]*\n?[ \t]*(?=[a-z0-9])", "-", t)

    # 1b) etiquetas "DOMINIO: X", "CLUSTER: Y" (el valor puede venir en la línea siguiente)
    t = re.sub(r"\b(" + "|".join(ETIQUETAS_DESCARTAR_SIGUIENTE) + r")\s*:\s*[^\s,;]+", " ", t, flags=re.I)

    # 2) líneas de descripción ("Servidores de Weblogic12 ...") -> fuera
    lineas = []
    for ln in t.split("\n"):
        if re.match(r"^\s*servidor(es)?\b", _sin_tildes(ln), re.I):
            continue
        # "XQL0741 LNXRRSSO01) Capa Web..." y "... Usuario Final)": cortar en paréntesis sin abrir
        prof, corte = 0, None
        for i, ch in enumerate(ln):
            if ch == "(":
                prof += 1
            elif ch == ")":
                if prof == 0:
                    corte = i
                    break
                prof -= 1
        if corte is not None:
            previo = ln[:corte]
            # si lo anterior al ")" termina en una descripción ("Servidores Web Usuario Final"), quitarla
            previo = re.sub(r"\b[Ss]ervidor(es)?\b.*$", "", previo)
            ln = previo
        # "Servidores ..." a mitad de línea también es nota
        ln = re.sub(r"\b[Ss]ervidor(es)?\b[^,;]*$", "", ln)
        lineas.append(ln)
    t = "\n".join(lineas)

    # 3) paréntesis: IP -> alias del anterior; cualquier otra cosa -> nota (fuera)
    def _par(m: re.Match) -> str:
        inner = m.group(1).strip()
        ips = re.findall(r"\d{1,3}(?:\.\d{1,3}){3}", inner)
        return " " + " ".join(f"§IP:{ip}" for ip in ips) + " " if ips else " "
    t = re.sub(r"\(([^()]*)\)", _par, t)
    t = t.replace("(", " ").replace(")", " ")

    # 4) etiquetas "DOMINIO: X", "CLUSTER: Y" -> fuera; "NODOS:" -> solo la etiqueta
    t = re.sub(r"\b[A-Za-zÁÉÍÓÚáéíóú]+\s*:\s+", " ", t)          # etiqueta con espacio después
    t = re.sub(r"(?<=[A-Za-z0-9]):(?=[A-Za-z0-9])", ",", t)        # "COMHP36:COMHP35"

    # 5) unir "CODIGO - HOST", "CODIGO -HOST", "CODIGO- HOST", "CODIGO HOST"
    t = re.sub(r"\b([A-Z]{2,4}\d{3,10})\s*[-_—–]\s*(?=[A-Za-z])", r"\1§", t)
    t = re.sub(r"\b([A-Z]{2,4}\d{3,10})\s+(?=[A-Z][A-Z0-9_\-]*\d)", r"\1§", t)

    # 6) separadores: coma, punto y coma, " y ", " - ", saltos y espacios
    t = re.sub(r"\s+[yY]\s+", ",", t)
    t = re.sub(r"\s+[-—–]\s+", ",", t)
    tokens = [x for x in re.split(r"[,;\s|]+", t) if x]

    servidores: list[Servidor] = []
    for tok in tokens:
        tok = tok.strip(".").strip("-").strip()
        if not tok:
            continue
        if tok.startswith("§IP:"):
            if servidores:
                servidores[-1].alias.append(tok[4:])
            continue
        if RE_IP.match(tok):
            if servidores:
                servidores[-1].alias.append(tok)
            continue
        if "§" in tok:
            codigo, host = tok.split("§", 1)
            host = host.strip(".").strip("-")
            if _es_hostname(host):
                servidores.append(_servidor_desde_host(host, [codigo]))
            elif _es_hostname(codigo):
                servidores.append(Servidor(codigo))
            continue
        # "AMXPRDHANA01_KLN0024": host + código al final
        m = re.match(r"^([A-Za-z][A-Za-z0-9\-]*\d)_([A-Z]{2,4}\d{3,10})$", tok)
        if m and not RE_CODIGO.match(m.group(1)):
            servidores.append(Servidor(m.group(1), [m.group(2), tok]))
            continue
        if _es_hostname(tok):
            servidores.append(_servidor_desde_host(tok))

    # 7) deduplicar por nombre (mezclando alias)
    unicos: dict[str, Servidor] = {}
    for s in servidores:
        if s.clave in unicos:
            for a in s.alias:
                if a.upper() not in {x.upper() for x in unicos[s.clave].alias}:
                    unicos[s.clave].alias.append(a)
        else:
            unicos[s.clave] = s
    for s in unicos.values():
        s.alias = [a for a in dict.fromkeys(s.alias) if a.upper() != s.nombre.upper()]
    return list(unicos.values())
