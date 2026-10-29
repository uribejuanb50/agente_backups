"""
Ajustes persistentes de la aplicación (se guardan en ajustes.json en la raíz del proyecto).

Los selectores de Copilot viven aquí para poder recalibrarlos sin tocar código:
si Microsoft cambia la interfaz, se edita ajustes.json (o se usa "Probar conexión",
que genera un diagnóstico con los elementos encontrados en la página).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ / "data"
ARCHIVO_AJUSTES = RAIZ / "ajustes.json"

URL_CARPETA_DEFECTO = (
    "https://claromovilco-my.sharepoint.com/my?id="
    "%2Fpersonal%2F38517232%5Fclaro%5Fcom%5Fco%2FDocuments%2FAgentesTI"
    "%2FAgentesTI%2FOPN11%2D%20Ejecucion%20de%20respaldos%20No%20SAP"
    "&viewid=9b341cd8%2D1416%2D49a1%2D84e7%2D22d26eba7dd1"
    "&share=cgpIf%5Fpy69RDQLlyw6eVnvrqEgUCers3r7vaTxJ9Kw4WmTfarg"
)

SELECTORES_DEFECTO: dict[str, list[str]] = {
    # Selectores basados en atributos estables (data-*, aria-label, clases "fai-"/"fui-").
    # Las clases tipo "r18fti29" o "___1ld5kid" las genera Fluent UI y cambian: NO usarlas.
    # botón flotante que abre Copilot en OneDrive ("Acciones de Copilot")
    "abrir_copilot": [
        'button[data-automation-id="copilot-fab-trigger"]',
        'button[aria-label="Acciones de Copilot"]',
        'button[data-automation-id*="copilot" i]',
        'button[aria-label="Copilot"]',
        'button[aria-label*="Copilot" i]',
    ],
    # botones para cerrar popups de bienvenida (XPath). Nunca cerrar un diálogo que tenga la caja del chat.
    "cerrar_popup_xpath": [
        '//div[@role="dialog"][not(.//*[@contenteditable="true"])][.//*[contains(normalize-space(.), "Copilot")]]'
        '//button[@aria-label="Cerrar" or @aria-label="Close"]',
    ],
    # caja de texto del chat (OneDrive usa un editor Lexical: <span role="textbox" contenteditable>)
    "entrada": [
        '[data-lexical-editor="true"][contenteditable="true"]',
        '.fai-EditorInput__input[contenteditable="true"]',
        '#m365-chat-input-shared-wrapper [contenteditable="true"]',
        '#m365-chat-editor-target-element',
        '[contenteditable="true"][role="textbox"]',
        'textarea[data-testid*="chat" i]',
        'textarea[placeholder*="Copilot" i]',
        'textarea[aria-label*="Copilot" i]',
        'textarea',                       # genéricos: solo se usan con el chat ya abierto
        '[contenteditable="true"]',
    ],
    # botón enviar
    "enviar": [
        'button.fai-SendButton',
        'button.fai-ChatInput__send',
        '#m365-chat-input-shared-wrapper button[type="submit"]',
        'button[aria-label="Send"]',
        'button[aria-label="Enviar"]',
        'button[data-testid*="send" i]',
    ],
    # botón "detener" visible mientras Copilot está generando
    "detener": [
        'button.fai-StopButton',
        '#m365-chat-input-shared-wrapper button[aria-label*="Stop" i]',
        '#m365-chat-input-shared-wrapper button[aria-label*="Detener" i]',
        'button[aria-label*="Stop generating" i]',
        'button[aria-label*="Detener" i]',
        'button[data-testid*="stop" i]',
    ],
    # botón para iniciar una conversación nueva (si no se encuentra, se recarga la página)
    "nuevo_chat": [
        'button[aria-label*="Nuevo chat" i]',
        'button[aria-label*="Nueva conversación" i]',
        'button[aria-label*="Nuevo tema" i]',
        'button[aria-label*="New chat" i]',
        'button[aria-label*="New conversation" i]',
        'button[aria-label*="New topic" i]',
        'button[data-testid*="new-chat" i]',
    ],
}

# selectores de versiones anteriores que causaban problemas (se eliminan al cargar ajustes.json)
SELECTORES_OBSOLETOS: dict[str, set[str]] = {
    "cerrar_popup_xpath": {
        '//div[@role="dialog"][.//*[contains(normalize-space(.), "Copilot")]]//button[@aria-label="Cerrar"]',
        '//div[@role="dialog"]//button[@aria-label="Close"]',
    },
    "enviar": {'button[aria-label*="Enviar" i]', 'button[aria-label*="Send" i]', 'button[type="submit"]'},
    "entrada": {'div[contenteditable="true"][role="textbox"]', 'div[contenteditable="true"][aria-label]',
                'div[contenteditable="true"]'},
}


def combinar_selectores(calibrados: dict, del_archivo: dict) -> dict[str, list[str]]:
    """calibrados (grabados con clics) > defectos del programa > extras del archivo (sin obsoletos)."""
    final: dict[str, list[str]] = {}
    for clave in set(SELECTORES_DEFECTO) | set(del_archivo or {}) | set(calibrados or {}):
        lista = list((calibrados or {}).get(clave, [])) + SELECTORES_DEFECTO.get(clave, []) + [
            x for x in (del_archivo or {}).get(clave, []) if x not in SELECTORES_OBSOLETOS.get(clave, set())]
        final[clave] = list(dict.fromkeys(x for x in lista if x))
    return final


FRASES_LIMITE_DEFECTO = [
    "has alcanzado el límite", "alcanzaste el límite", "límite de mensajes", "inténtalo de nuevo más tarde",
    "vuelve a intentarlo más tarde", "demasiadas solicitudes", "you've reached", "try again later",
    "too many requests", "something went wrong", "algo salió mal",
]


@dataclass
class Ajustes:
    # entrada
    archivo_control: str = str(DATA / "CONTROL_ANS_SOX.xlsx")
    hoja: str = "POLITICAS_CON _SLA"
    # Copilot / OneDrive
    url_carpeta: str = URL_CARPETA_DEFECTO
    nombre_carpeta: str = "OPN11- Ejecucion de respaldos No SAP"
    perfil_edge: str = "C:/SeleniumProfiles/AgenteOPN11_Edge"
    ventana_edge: str = "oculta"          # "visible" | "oculta" (fuera de pantalla) | "sin_ventana" (headless)
    ruta_driver: str = ""
    # salida
    carpeta_salida: str = ""
    # muestra
    fecha_inicio: str = ""
    fecha_fin: str = ""
    semilla: str = ""
    criterio_servidores: str = "cualquiera"   # varios servidores en una fila: "cualquiera" basta | "cada_uno"
    modo_fechas: str = "hibrido"          # "hibrido" (Python calcula, Copilot solo lo no reconocido) | "copilot"
    ventana_cercania: int = 7             # días hacia atrás/adelante para buscar fecha cercana
    repeticiones: dict = field(default_factory=dict)     # clave de regla -> N
    forzar_copilot: list = field(default_factory=list)   # claves de regla que se delegan a Copilot aunque Python las entienda
    # rendimiento: lotes GRANDES (menos idas y vueltas a Copilot); el límite real es de caracteres
    servidores_por_lote: int = 80
    fechas_por_lote: int = 400
    max_caracteres_prompt: int = 4000
    modo_prompt: str = "denso"            # "denso" (1 línea por servidor) | "compacto" | "detallado"
    prompt_compacto: bool = False         # (compatibilidad con versiones anteriores)
    pestanas: int = 1
    timeout_respuesta: int = 900
    estabilidad_seg: int = 15
    reintentos: int = 2
    nuevo_chat_por_lote: bool = True
    timeout_login: int = 300
    pausa_limite_seg: int = 90
    # calibración
    selectores: dict = field(default_factory=lambda: {k: list(v) for k, v in SELECTORES_DEFECTO.items()})
    selectores_calibrados: dict = field(default_factory=dict)   # grabados con "Calibrar con clics"
    version_ajustes: int = 3
    frases_limite: list = field(default_factory=lambda: list(FRASES_LIMITE_DEFECTO))

    # ------------------------------------------------------------------
    @classmethod
    def cargar(cls, ruta: Path = ARCHIVO_AJUSTES) -> "Ajustes":
        a = cls()
        if ruta.exists():
            try:
                datos = json.loads(ruta.read_text(encoding="utf-8"))
            except Exception:
                datos = {}
            nombres = {f.name for f in fields(cls)}
            for k, v in datos.items():
                if k in nombres:
                    setattr(a, k, v)
            a.selectores = combinar_selectores(a.selectores_calibrados, a.selectores)
            if int(datos.get("version_ajustes", 1)) < 2:
                # v1 usaba lotes pequeños (6 servidores / 30 fechas): pasar a lotes grandes por caracteres
                a.servidores_por_lote, a.fechas_por_lote = 80, 400
                a.max_caracteres_prompt = 4000
                a.modo_prompt = "denso"
                a.timeout_respuesta = max(900, int(a.timeout_respuesta or 0))
                a.version_ajustes = 2
            if int(datos.get("version_ajustes", 1)) < 3:
                a.timeout_respuesta = max(900, int(a.timeout_respuesta or 0))
                a.version_ajustes = 3
            if a.ventana_edge not in ("visible", "oculta", "sin_ventana"):
                a.ventana_edge = "oculta"
            if a.modo_prompt not in ("denso", "compacto", "detallado"):
                a.modo_prompt = "denso"
        return a

    def guardar(self, ruta: Path = ARCHIVO_AJUSTES) -> None:
        try:
            ruta.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def copia(self) -> "Ajustes":
        return Ajustes(**json.loads(json.dumps(asdict(self))))
