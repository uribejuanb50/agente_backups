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
    # botón flotante / de la barra que abre el panel de Copilot
    "abrir_copilot": [
        'button[data-automation-id="copilot-fab-trigger"]',
        'button[data-automation-id*="copilot" i]',
        'button[aria-label="Copilot"]',
        'button[aria-label*="Copilot" i]',
        '[role="button"][aria-label*="Copilot" i]',
    ],
    # botón para cerrar el popup de bienvenida (XPath)
    "cerrar_popup_xpath": [
        '//div[@role="dialog"][.//*[contains(normalize-space(.), "Copilot")]]//button[@aria-label="Cerrar"]',
        '//div[@role="dialog"]//button[@aria-label="Close"]',
    ],
    # caja de texto del chat
    "entrada": [
        '#m365-chat-editor-target-element',
        'textarea[data-testid*="chat" i]',
        'div[contenteditable="true"][role="textbox"]',
        'textarea[placeholder*="Copilot" i]',
        'textarea[aria-label*="Copilot" i]',
        'div[contenteditable="true"][aria-label]',
        'textarea',
        'div[contenteditable="true"]',
    ],
    # botón enviar
    "enviar": [
        'button[aria-label="Enviar"]',
        'button[aria-label="Send"]',
        'button[aria-label*="Enviar" i]',
        'button[aria-label*="Send" i]',
        'button[data-testid*="send" i]',
        'button[type="submit"]',
    ],
    # botón "detener" visible mientras Copilot está generando
    "detener": [
        'button[aria-label*="Detener" i]',
        'button[aria-label*="Stop" i]',
        'button[data-testid*="stop" i]',
    ],
    # botón para iniciar una conversación nueva
    "nuevo_chat": [
        'button[aria-label*="Nuevo chat" i]',
        'button[aria-label*="Nueva conversación" i]',
        'button[aria-label*="New chat" i]',
        'button[aria-label*="New conversation" i]',
        'button[data-testid*="new-chat" i]',
    ],
}

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
    ruta_driver: str = ""
    # salida
    carpeta_salida: str = ""
    # muestra
    fecha_inicio: str = ""
    fecha_fin: str = ""
    semilla: str = ""
    modo_fechas: str = "hibrido"          # "hibrido" (Python calcula, Copilot solo lo no reconocido) | "copilot"
    ventana_cercania: int = 7             # días hacia atrás/adelante para buscar fecha cercana
    repeticiones: dict = field(default_factory=dict)     # clave de regla -> N
    forzar_copilot: list = field(default_factory=list)   # claves de regla que se delegan a Copilot aunque Python las entienda
    # rendimiento
    servidores_por_lote: int = 6
    fechas_por_lote: int = 30
    max_caracteres_prompt: int = 7000
    prompt_compacto: bool = False         # instrucciones cortas (se activa solo si Copilot corta el texto)
    pestanas: int = 1
    timeout_respuesta: int = 420
    estabilidad_seg: int = 15
    reintentos: int = 2
    nuevo_chat_por_lote: bool = True
    timeout_login: int = 300
    pausa_limite_seg: int = 90
    # calibración
    selectores: dict = field(default_factory=lambda: {k: list(v) for k, v in SELECTORES_DEFECTO.items()})
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
            # completar selectores nuevos que no estén en el archivo
            sel = {k: list(v) for k, v in SELECTORES_DEFECTO.items()}
            sel.update({k: v for k, v in (a.selectores or {}).items() if v})
            a.selectores = sel
        return a

    def guardar(self, ruta: Path = ARCHIVO_AJUSTES) -> None:
        try:
            ruta.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def copia(self) -> "Ajustes":
        return Ajustes(**json.loads(json.dumps(asdict(self))))
