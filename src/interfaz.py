"""
Interfaz gráfica (Tkinter/ttk) de la auditoría de backups con Copilot.

Flujo en 3 pasos:
  1. Datos y conexión: Excel de control, carpeta de OneDrive/Copilot, carpeta de salida.
  2. Muestra: rango de fechas, semilla, repeticiones (N) por tipo de agenda.
  3. Ejecución: progreso en vivo, contadores por estado, bitácora, pausar/detener/reanudar.
"""
from __future__ import annotations

import json
import os
import queue
import random
import string
import subprocess
import sys
import tkinter as tk
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .ajustes import RAIZ, Ajustes
from .control import FilaControl, leer_control, listar_hojas
from .motor import Motor, PruebaConexion
from .planificador import Plan, armar_lote, construir_plan
from .prompt_copilot import construir_prompt

# ------------------------------------------------------------------ paleta
C = {
    "fondo": "#F4F5F7", "tarjeta": "#FFFFFF", "borde": "#E2E4E8", "texto": "#1F2328", "suave": "#6B7280",
    "rojo": "#DA291C", "rojo_osc": "#B3200F", "rojo_claro": "#FDECEA", "linea": "#EEF0F2",
    "verde": "#1E7B34", "ambar": "#9A6700", "gris": "#8A9099", "azul": "#1F5FBF",
}
ESTADO_KPI = [
    ("SI", "Coinciden", "#E6F4EA", "#1E7B34"),
    ("PARCIAL", "Parcial", "#FFF4D6", "#8A5A00"),
    ("CERCANA", "Cercanas", "#FDEBDD", "#A3480E"),
    ("NO COINCIDEN FECHAS", "No coinciden", "#FDE2E1", "#B3200F"),
    ("SIN RESPUESTA", "Sin respuesta", "#ECEEF1", "#4B5563"),
    ("PENDIENTE", "Pendientes", "#F4F5F7", "#6B7280"),
]
SEG_POR_LOTE_ESTIMADO = 90


def _dpi():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def parse_fecha_ui(txt: str) -> date | None:
    txt = (txt or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            pass
    return None


def fmt_dur(seg: float | None) -> str:
    if seg is None:
        return "—"
    seg = int(seg)
    h, r = divmod(seg, 3600)
    m, s = divmod(r, 60)
    return f"{h} h {m:02d} min" if h else (f"{m} min {s:02d} s" if m else f"{s} s")


def abrir_ruta(ruta: str | Path):
    ruta = str(ruta)
    try:
        if sys.platform == "win32":
            os.startfile(ruta)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", ruta])
        else:
            subprocess.Popen(["xdg-open", ruta])
    except Exception as e:
        messagebox.showerror("No se pudo abrir", str(e))


# ======================================================================================
class Tarjeta(tk.Frame):
    """Contenedor blanco con borde suave y título."""

    def __init__(self, padre, titulo: str, subtitulo: str = "", **kw):
        super().__init__(padre, bg=C["tarjeta"], highlightbackground=C["borde"], highlightthickness=1, **kw)
        cab = tk.Frame(self, bg=C["tarjeta"])
        cab.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(cab, text=titulo, bg=C["tarjeta"], fg=C["texto"], font=("Segoe UI Semibold", 11)).pack(anchor="w")
        if subtitulo:
            tk.Label(cab, text=subtitulo, bg=C["tarjeta"], fg=C["suave"], font=("Segoe UI", 9),
                     wraplength=560, justify="left").pack(anchor="w")
        self.cuerpo = tk.Frame(self, bg=C["tarjeta"])
        self.cuerpo.pack(fill="both", expand=True, padx=16, pady=(4, 14))


class KPI(tk.Frame):
    def __init__(self, padre, titulo: str, fondo: str, color: str):
        super().__init__(padre, bg=fondo, highlightbackground=C["borde"], highlightthickness=1)
        self.valor = tk.Label(self, text="0", bg=fondo, fg=color, font=("Segoe UI Semibold", 20))
        self.valor.pack(anchor="w", padx=12, pady=(8, 0))
        tk.Label(self, text=titulo, bg=fondo, fg=color, font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 8))

    def set(self, v):
        self.valor.configure(text=str(v))


# ======================================================================================
class App(tk.Tk):
    def __init__(self):
        _dpi()
        super().__init__()
        self.a = Ajustes.cargar()
        self.title("Auditoría de Backups · Copilot")
        self.configure(bg=C["fondo"])
        self.geometry("1180x800")
        self.minsize(1020, 680)
        self.filas: list[FilaControl] = []
        self.grupos: dict[str, dict] = {}
        self.motor: Motor | None = None
        self.prueba: PruebaConexion | None = None
        self.cola: queue.Queue = queue.Queue()
        self.ruta_resultado: Path | None = None
        self._job_resumen = None
        self._editor = None

        self._estilos()
        self._construir()
        self._cargar_valores()
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        self.after(150, self._leer_cola)
        self.after(300, self._autocargar)
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))

    # ------------------------------------------------------------------ estilos
    def _estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        base = ("Segoe UI", 10)
        s.configure(".", font=base, background=C["tarjeta"], foreground=C["texto"])
        s.configure("TFrame", background=C["tarjeta"])
        s.configure("Fondo.TFrame", background=C["fondo"])
        s.configure("TLabel", background=C["tarjeta"], foreground=C["texto"])
        s.configure("Suave.TLabel", foreground=C["suave"], font=("Segoe UI", 9))
        s.configure("Fondo.TLabel", background=C["fondo"])
        s.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=C["borde"], lightcolor=C["borde"],
                    darkcolor=C["borde"], padding=6)
        s.map("TEntry", bordercolor=[("focus", C["rojo"])], lightcolor=[("focus", C["rojo"])])
        s.configure("Invalido.TEntry", fieldbackground="#FFF5F5", bordercolor=C["rojo"])
        s.configure("TCombobox", padding=5, arrowsize=14, bordercolor=C["borde"])
        s.configure("TSpinbox", padding=4, arrowsize=12, bordercolor=C["borde"])
        s.configure("TCheckbutton", background=C["tarjeta"])
        s.configure("TRadiobutton", background=C["tarjeta"])
        # botones
        s.configure("Primario.TButton", background=C["rojo"], foreground="#FFFFFF", borderwidth=0,
                    focusthickness=0, padding=(18, 9), font=("Segoe UI Semibold", 10))
        s.map("Primario.TButton", background=[("disabled", "#E8A39D"), ("active", C["rojo_osc"])],
              foreground=[("disabled", "#FFFFFF")])
        s.configure("Secundario.TButton", background="#FFFFFF", foreground=C["texto"], bordercolor=C["borde"],
                    borderwidth=1, padding=(14, 8))
        s.map("Secundario.TButton", background=[("active", C["linea"])], foreground=[("disabled", C["gris"])])
        s.configure("Chico.TButton", background="#FFFFFF", foreground=C["texto"], bordercolor=C["borde"],
                    borderwidth=1, padding=(8, 4), font=("Segoe UI", 9))
        s.map("Chico.TButton", background=[("active", C["linea"])])
        # notebook tipo "pasos"
        s.configure("Pasos.TNotebook", background=C["fondo"], borderwidth=0, tabmargins=(16, 8, 16, 0))
        s.configure("Pasos.TNotebook.Tab", background=C["fondo"], foreground=C["suave"], padding=(18, 10),
                    borderwidth=0, font=("Segoe UI Semibold", 10))
        s.map("Pasos.TNotebook.Tab", background=[("selected", C["tarjeta"])],
              foreground=[("selected", C["rojo"])])
        # tabla
        s.configure("Treeview", rowheight=28, fieldbackground="#FFFFFF", background="#FFFFFF",
                    bordercolor=C["borde"], font=("Segoe UI", 10))
        s.configure("Treeview.Heading", background=C["linea"], foreground=C["texto"], relief="flat",
                    font=("Segoe UI Semibold", 9), padding=(6, 6))
        s.map("Treeview", background=[("selected", C["rojo_claro"])], foreground=[("selected", C["texto"])])
        s.configure("Rojo.Horizontal.TProgressbar", troughcolor=C["linea"], background=C["rojo"],
                    bordercolor=C["linea"], lightcolor=C["rojo"], darkcolor=C["rojo"], thickness=10)

    # ------------------------------------------------------------------ estructura
    def _construir(self):
        # cabecera
        cab = tk.Frame(self, bg=C["rojo"], height=64)
        cab.pack(fill="x")
        cab.pack_propagate(False)
        tk.Label(cab, text="Auditoría de Backups", bg=C["rojo"], fg="#FFFFFF",
                 font=("Segoe UI Semibold", 16)).pack(side="left", padx=(22, 10))
        tk.Label(cab, text="Muestreo de fechas y verificación con Copilot", bg=C["rojo"], fg="#FFD9D5",
                 font=("Segoe UI", 10)).pack(side="left", pady=(4, 0))
        self.pildora = tk.Label(cab, text="●  Listo", bg=C["rojo_osc"], fg="#FFFFFF", font=("Segoe UI", 9),
                                padx=12, pady=4)
        self.pildora.pack(side="right", padx=22)

        self.nb = ttk.Notebook(self, style="Pasos.TNotebook")
        self.nb.pack(fill="both", expand=True)
        self.tab1 = tk.Frame(self.nb, bg=C["fondo"])
        self.tab2 = tk.Frame(self.nb, bg=C["fondo"])
        self.tab3 = tk.Frame(self.nb, bg=C["fondo"])
        self.nb.add(self.tab1, text="  1 · Datos y conexión  ")
        self.nb.add(self.tab2, text="  2 · Muestra de fechas  ")
        self.nb.add(self.tab3, text="  3 · Ejecución  ")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._al_cambiar_tab())

        self._construir_tab1()
        self._construir_tab2()
        self._construir_tab3()

        # barra inferior
        pie = tk.Frame(self, bg=C["tarjeta"], highlightbackground=C["borde"], highlightthickness=1)
        pie.pack(fill="x", side="bottom")
        self.lbl_estado = tk.Label(pie, text="", bg=C["tarjeta"], fg=C["suave"], font=("Segoe UI", 9), anchor="w")
        self.lbl_estado.pack(side="left", fill="x", expand=True, padx=16, pady=10)
        self.btn_sig = ttk.Button(pie, text="Siguiente  →", style="Primario.TButton", command=self._siguiente)
        self.btn_sig.pack(side="right", padx=(6, 16), pady=8)
        self.btn_ant = ttk.Button(pie, text="←  Atrás", style="Secundario.TButton", command=self._anterior)
        self.btn_ant.pack(side="right", pady=8)

    def _fila_campo(self, padre, fila: int, etiqueta: str, var: tk.Variable, ayuda: str = "",
                    boton: tuple | None = None, ancho: int = 60) -> ttk.Entry:
        ttk.Label(padre, text=etiqueta).grid(row=fila, column=0, sticky="w", pady=(6, 0))
        e = ttk.Entry(padre, textvariable=var, width=ancho)
        e.grid(row=fila, column=1, sticky="ew", padx=(12, 6), pady=(6, 0))
        if boton:
            ttk.Button(padre, text=boton[0], style="Chico.TButton", command=boton[1]).grid(row=fila, column=2,
                                                                                         sticky="w", pady=(6, 0))
        if ayuda:
            ttk.Label(padre, text=ayuda, style="Suave.TLabel").grid(row=fila + 1, column=1, columnspan=2,
                                                                     sticky="w", padx=(12, 0))
        padre.columnconfigure(1, weight=1)
        return e

    # ---------------- paso 1
    def _construir_tab1(self):
        cont = tk.Frame(self.tab1, bg=C["fondo"])
        cont.pack(fill="both", expand=True, padx=18, pady=14)
        cont.columnconfigure(0, weight=3, uniform="c")
        cont.columnconfigure(1, weight=2, uniform="c")

        # Excel de control
        t1 = Tarjeta(cont, "Excel de control", "Archivo con las políticas de backup (columnas SERVIDOR y AGENDA).")
        t1.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 12))
        b = t1.cuerpo
        self.v_archivo = tk.StringVar()
        self.v_hoja = tk.StringVar()
        self._fila_campo(b, 0, "Archivo", self.v_archivo, boton=("Examinar…", self._elegir_excel))
        ttk.Label(b, text="Hoja").grid(row=2, column=0, sticky="w", pady=(8, 0))
        fh = tk.Frame(b, bg=C["tarjeta"])
        fh.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(8, 0))
        self.cb_hoja = ttk.Combobox(fh, textvariable=self.v_hoja, width=34, state="readonly")
        self.cb_hoja.pack(side="left")
        ttk.Button(fh, text="Cargar", style="Chico.TButton", command=self._cargar_excel).pack(side="left", padx=8)
        self.lbl_excel = tk.Label(b, text="Sin cargar", bg=C["tarjeta"], fg=C["suave"], font=("Segoe UI", 9),
                                  justify="left", anchor="w", wraplength=600)
        self.lbl_excel.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        # Copilot
        t2 = Tarjeta(cont, "Copilot en OneDrive",
                     "Edge se abre con un perfil propio: la primera vez inicia sesión en esa ventana y queda guardada.")
        t2.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(0, 12))
        b = t2.cuerpo
        self.v_url = tk.StringVar()
        self.v_carpeta = tk.StringVar()
        self.v_perfil = tk.StringVar()
        self.v_driver = tk.StringVar()
        self._fila_campo(b, 0, "URL de la carpeta", self.v_url)
        self._fila_campo(b, 1, "Nombre de la carpeta", self.v_carpeta, ayuda="Es el nombre que se le dice a Copilot en el prompt.")
        self._fila_campo(b, 3, "Perfil de Edge", self.v_perfil, boton=("…", lambda: self._elegir_dir(self.v_perfil)))
        self._fila_campo(b, 4, "msedgedriver (opcional)", self.v_driver,
                         boton=("…", lambda: self._elegir_archivo(self.v_driver, [("msedgedriver", "*.exe"), ("Todos", "*.*")])),
                         ayuda="Solo si la red bloquea la descarga automática del driver.")
        fp = tk.Frame(b, bg=C["tarjeta"])
        fp.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.btn_probar = ttk.Button(fp, text="Probar conexión", style="Secundario.TButton", command=self._probar)
        self.btn_probar.pack(side="left")
        self.lbl_probar = tk.Label(fp, text="Abre Edge, llega al chat y hace una pregunta de prueba.",
                                   bg=C["tarjeta"], fg=C["suave"], font=("Segoe UI", 9))
        self.lbl_probar.pack(side="left", padx=12)

        # Salida
        t3 = Tarjeta(cont, "Ubicación de salida", "Aquí se deja el Excel consolidado y el archivo de avance para reanudar.")
        t3.grid(row=0, column=1, sticky="nsew", pady=(0, 12))
        b = t3.cuerpo
        self.v_salida = tk.StringVar()
        self.e_salida = self._fila_campo(b, 0, "Carpeta", self.v_salida, ancho=30,
                                         boton=("Examinar…", lambda: self._elegir_dir(self.v_salida)))
        self.lbl_nombre_salida = tk.Label(b, text="", bg=C["tarjeta"], fg=C["suave"], font=("Segoe UI", 9),
                                          justify="left", anchor="w", wraplength=380)
        self.lbl_nombre_salida.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.v_salida.trace_add("write", lambda *_: self._refrescar_nombre_salida())

        # Rendimiento
        t4 = Tarjeta(cont, "Rendimiento", "Más pestañas = más rápido, pero Copilot puede limitar si se exagera.")
        t4.grid(row=1, column=1, sticky="nsew", pady=(0, 12))
        b = t4.cuerpo
        self.v_pestanas = tk.IntVar()
        self.v_srv_lote = tk.IntVar()
        self.v_fch_lote = tk.IntVar()
        self.v_timeout = tk.IntVar()
        self.v_reint = tk.IntVar()
        self.v_nuevo_chat = tk.BooleanVar()
        campos = [
            ("Pestañas de Copilot en paralelo", self.v_pestanas, 1, 4),
            ("Servidores por consulta", self.v_srv_lote, 1, 20),
            ("Fechas por consulta (máx.)", self.v_fch_lote, 5, 120),
            ("Espera máx. por respuesta (s)", self.v_timeout, 60, 1800),
            ("Reintentos por servidor", self.v_reint, 0, 5),
        ]
        for i, (txt, var, lo, hi) in enumerate(campos):
            ttk.Label(b, text=txt).grid(row=i, column=0, sticky="w", pady=4)
            ttk.Spinbox(b, from_=lo, to=hi, textvariable=var, width=7,
                        command=self._programar_resumen).grid(row=i, column=1, sticky="e", padx=(12, 0), pady=4)
        ttk.Checkbutton(b, text="Chat nuevo en cada consulta (recomendado)", variable=self.v_nuevo_chat).grid(
            row=len(campos), column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.v_compacto = tk.BooleanVar()
        ttk.Checkbutton(b, text="Prompt compacto (se activa solo si Copilot corta el texto)",
                        variable=self.v_compacto).grid(row=len(campos) + 1, column=0, columnspan=2, sticky="w")
        b.columnconfigure(0, weight=1)
        cont.rowconfigure(1, weight=1)

    # ---------------- paso 2
    def _construir_tab2(self):
        cont = tk.Frame(self.tab2, bg=C["fondo"])
        cont.pack(fill="both", expand=True, padx=18, pady=14)

        t = Tarjeta(cont, "Parámetros de la muestra",
                    "Las fechas se sortean dentro del rango respetando la agenda de cada política. "
                    "Con la misma semilla se obtiene exactamente la misma muestra.")
        t.pack(fill="x", pady=(0, 12))
        b = t.cuerpo
        self.v_ini = tk.StringVar()
        self.v_fin = tk.StringVar()
        self.v_semilla = tk.StringVar()
        self.v_modo = tk.StringVar()
        self.v_ventana = tk.IntVar()

        f = tk.Frame(b, bg=C["tarjeta"])
        f.pack(fill="x")
        ttk.Label(f, text="Desde").pack(side="left")
        self.e_ini = ttk.Entry(f, textvariable=self.v_ini, width=12)
        self.e_ini.pack(side="left", padx=(6, 10))
        ttk.Label(f, text="hasta").pack(side="left")
        self.e_fin = ttk.Entry(f, textvariable=self.v_fin, width=12)
        self.e_fin.pack(side="left", padx=(6, 10))
        for txt, fn in (("Mes anterior", self._preset_mes), ("Últimos 3 meses", lambda: self._preset_dias(91)),
                        ("Últimos 6 meses", lambda: self._preset_dias(182)), ("Este año", self._preset_anio)):
            ttk.Button(f, text=txt, style="Chico.TButton", command=fn).pack(side="left", padx=2)
        ttk.Label(f, text="AAAA-MM-DD o DD/MM/AAAA", style="Suave.TLabel").pack(side="left", padx=10)

        f2 = tk.Frame(b, bg=C["tarjeta"])
        f2.pack(fill="x", pady=(12, 0))
        ttk.Label(f2, text="Semilla").pack(side="left")
        ttk.Entry(f2, textvariable=self.v_semilla, width=16).pack(side="left", padx=(6, 4))
        ttk.Button(f2, text="↻  Nueva", style="Chico.TButton", command=self._nueva_semilla).pack(side="left")
        ttk.Label(f2, text="Cercanía").pack(side="left", padx=(24, 6))
        ttk.Spinbox(f2, from_=1, to=31, textvariable=self.v_ventana, width=5).pack(side="left")
        ttk.Label(f2, text="días antes/después", style="Suave.TLabel").pack(side="left", padx=6)

        f3 = tk.Frame(b, bg=C["tarjeta"])
        f3.pack(fill="x", pady=(12, 0))
        ttk.Label(f3, text="¿Quién asigna las fechas?").pack(side="left")
        ttk.Radiobutton(f3, text="Python (reproducible; Copilot solo las agendas que no se entienden)",
                        variable=self.v_modo, value="hibrido", command=self._refrescar_tabla).pack(side="left", padx=(10, 6))
        ttk.Radiobutton(f3, text="Copilot (todas)", variable=self.v_modo, value="copilot",
                        command=self._refrescar_tabla).pack(side="left")

        for v in (self.v_ini, self.v_fin, self.v_semilla):
            v.trace_add("write", lambda *_: self._programar_tabla())

        # tabla de agendas
        t2 = Tarjeta(cont, "Repeticiones por tipo de agenda",
                     "N = cuántas fechas se auditan por cada fila con esa agenda. Doble clic en N para editar, "
                     "o selecciona varias y usa 'Aplicar'. Doble clic en 'Fechas por' para pasarla a Copilot.")
        t2.pack(fill="both", expand=True)
        b = t2.cuerpo
        barra = tk.Frame(b, bg=C["tarjeta"])
        barra.pack(fill="x", pady=(0, 8))
        self.v_filtro = tk.StringVar()
        ef = ttk.Entry(barra, textvariable=self.v_filtro, width=28)
        ef.pack(side="left")
        ef.insert(0, "")
        ttk.Label(barra, text="filtrar agendas", style="Suave.TLabel").pack(side="left", padx=(6, 18))
        self.v_filtro.trace_add("write", lambda *_: self._refrescar_tabla())
        ttk.Label(barra, text="N para seleccionadas").pack(side="left")
        self.v_n_masivo = tk.IntVar(value=2)
        ttk.Spinbox(barra, from_=0, to=99, textvariable=self.v_n_masivo, width=5).pack(side="left", padx=6)
        ttk.Button(barra, text="Aplicar", style="Chico.TButton", command=self._aplicar_n).pack(side="left")
        ttk.Button(barra, text="Todas", style="Chico.TButton",
                   command=lambda: (self.tabla.selection_set(self.tabla.get_children()), self._aplicar_n())).pack(side="left", padx=4)
        ttk.Button(barra, text="Todo en 0", style="Chico.TButton", command=self._todo_cero).pack(side="left")

        marco = tk.Frame(b, bg=C["tarjeta"])
        marco.pack(fill="both", expand=True)
        cols = ("regla", "textos", "filas", "dias", "origen", "n")
        self.tabla = ttk.Treeview(marco, columns=cols, show="headings", selectmode="extended")
        for c, txt, w, anc in (("regla", "Agenda interpretada", 260, "w"), ("textos", "Cómo aparece en el Excel", 330, "w"),
                               ("filas", "Filas", 60, "center"), ("dias", "Días posibles", 100, "center"),
                               ("origen", "Fechas por", 110, "center"), ("n", "N", 60, "center")):
            self.tabla.heading(c, text=txt)
            self.tabla.column(c, width=w, anchor=anc, stretch=(c in ("regla", "textos")))
        sb = ttk.Scrollbar(marco, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=sb.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tabla.tag_configure("activa", foreground=C["texto"])
        self.tabla.tag_configure("cero", foreground=C["gris"])
        self.tabla.tag_configure("copilot", background="#F7F2FF")
        self.tabla.tag_configure("par", background="#FAFBFC")
        self.tabla.bind("<Double-1>", self._doble_clic)
        self.tabla.bind("<plus>", lambda e: self._sumar(1))
        self.tabla.bind("<KP_Add>", lambda e: self._sumar(1))
        self.tabla.bind("<minus>", lambda e: self._sumar(-1))
        self.tabla.bind("<KP_Subtract>", lambda e: self._sumar(-1))

        # resumen
        pie = tk.Frame(cont, bg=C["fondo"])
        pie.pack(fill="x", pady=(12, 0))
        self.lbl_resumen = tk.Label(pie, text="", bg=C["fondo"], fg=C["texto"], font=("Segoe UI Semibold", 10),
                                    anchor="w", justify="left")
        self.lbl_resumen.pack(side="left", fill="x", expand=True)
        ttk.Button(pie, text="Ver prompt de ejemplo", style="Secundario.TButton",
                   command=self._ver_prompt).pack(side="right")

    # ---------------- paso 3
    def _construir_tab3(self):
        cont = tk.Frame(self.tab3, bg=C["fondo"])
        cont.pack(fill="both", expand=True, padx=18, pady=14)

        kp = tk.Frame(cont, bg=C["fondo"])
        kp.pack(fill="x")
        self.kpis: dict[str, KPI] = {}
        for i, (clave, titulo, fondo, color) in enumerate(ESTADO_KPI):
            k = KPI(kp, titulo, fondo, color)
            k.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            kp.columnconfigure(i, weight=1, uniform="k")
            self.kpis[clave] = k

        tp = Tarjeta(cont, "Progreso")
        tp.pack(fill="x", pady=12)
        b = tp.cuerpo
        self.barra = ttk.Progressbar(b, style="Rojo.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.barra.pack(fill="x")
        self.lbl_prog = tk.Label(b, text="Aún no se ha iniciado.", bg=C["tarjeta"], fg=C["suave"],
                                 font=("Segoe UI", 9), anchor="w")
        self.lbl_prog.pack(fill="x", pady=(8, 0))
        bot = tk.Frame(b, bg=C["tarjeta"])
        bot.pack(fill="x", pady=(12, 0))
        self.btn_iniciar = ttk.Button(bot, text="▶  Iniciar auditoría", style="Primario.TButton", command=self._iniciar)
        self.btn_iniciar.pack(side="left")
        self.btn_pausa = ttk.Button(bot, text="⏸  Pausar", style="Secundario.TButton", command=self._pausar, state="disabled")
        self.btn_pausa.pack(side="left", padx=6)
        self.btn_parar = ttk.Button(bot, text="■  Detener", style="Secundario.TButton", command=self._detener, state="disabled")
        self.btn_parar.pack(side="left")
        self.btn_reanudar = ttk.Button(bot, text="Reanudar desde archivo…", style="Secundario.TButton",
                                       command=self._reanudar_archivo)
        self.btn_reanudar.pack(side="left", padx=(18, 0))
        self.btn_carpeta = ttk.Button(bot, text="Abrir carpeta", style="Secundario.TButton", state="disabled",
                                      command=lambda: abrir_ruta(self.ruta_resultado.parent if self.ruta_resultado else self.v_salida.get()))
        self.btn_carpeta.pack(side="right")
        self.btn_excel = ttk.Button(bot, text="Abrir Excel", style="Secundario.TButton", state="disabled",
                                    command=lambda: self.ruta_resultado and abrir_ruta(self.ruta_resultado))
        self.btn_excel.pack(side="right", padx=6)

        tl = Tarjeta(cont, "Bitácora")
        tl.pack(fill="both", expand=True)
        b = tl.cuerpo
        top = tk.Frame(b, bg=C["tarjeta"])
        top.pack(fill="x")
        self.v_detalle = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Ver detalle técnico", variable=self.v_detalle).pack(side="right")
        marco = tk.Frame(b, bg=C["tarjeta"])
        marco.pack(fill="both", expand=True, pady=(6, 0))
        self.log_txt = tk.Text(marco, height=12, wrap="word", font=("Consolas", 9), bg="#FBFBFC", fg=C["texto"],
                               relief="flat", highlightthickness=1, highlightbackground=C["borde"], padx=10, pady=8)
        sb = ttk.Scrollbar(marco, orient="vertical", command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=sb.set, state="disabled")
        self.log_txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, color in (("ok", C["verde"]), ("aviso", C["ambar"]), ("error", C["rojo"]), ("debug", C["gris"]),
                           ("info", C["texto"]), ("hora", C["gris"])):
            self.log_txt.tag_configure(tag, foreground=color)

    # ------------------------------------------------------------------ valores
    def _cargar_valores(self):
        a = self.a
        self.v_archivo.set(a.archivo_control)
        self.v_hoja.set(a.hoja)
        self.v_url.set(a.url_carpeta)
        self.v_carpeta.set(a.nombre_carpeta)
        self.v_perfil.set(a.perfil_edge)
        self.v_driver.set(a.ruta_driver)
        self.v_salida.set(a.carpeta_salida)
        self.v_pestanas.set(a.pestanas)
        self.v_srv_lote.set(a.servidores_por_lote)
        self.v_fch_lote.set(a.fechas_por_lote)
        self.v_timeout.set(a.timeout_respuesta)
        self.v_reint.set(a.reintentos)
        self.v_nuevo_chat.set(a.nuevo_chat_por_lote)
        self.v_compacto.set(a.prompt_compacto)
        hoy = date.today()
        self.v_ini.set(a.fecha_inicio or (hoy - timedelta(days=91)).isoformat())
        self.v_fin.set(a.fecha_fin or hoy.isoformat())
        self.v_semilla.set(a.semilla or self._semilla_aleatoria())
        self.v_modo.set(a.modo_fechas or "hibrido")
        self.v_ventana.set(a.ventana_cercania)
        self.repeticiones: dict[str, int] = dict(a.repeticiones or {})
        self.forzar: set[str] = set(a.forzar_copilot or [])
        self._refrescar_nombre_salida()
        self._actualizar_botones_paso()

    def _volcar_valores(self) -> Ajustes:
        a = self.a

        def entero(var, defecto):
            try:
                return int(var.get())
            except Exception:
                return defecto
        a.archivo_control = self.v_archivo.get().strip()
        a.hoja = self.v_hoja.get().strip()
        a.url_carpeta = self.v_url.get().strip()
        a.nombre_carpeta = self.v_carpeta.get().strip()
        a.perfil_edge = self.v_perfil.get().strip()
        a.ruta_driver = self.v_driver.get().strip()
        a.carpeta_salida = self.v_salida.get().strip()
        a.pestanas = max(1, min(4, entero(self.v_pestanas, 1)))
        a.servidores_por_lote = max(1, entero(self.v_srv_lote, 6))
        a.fechas_por_lote = max(1, entero(self.v_fch_lote, 30))
        a.timeout_respuesta = max(60, entero(self.v_timeout, 420))
        a.reintentos = max(0, entero(self.v_reint, 2))
        a.nuevo_chat_por_lote = bool(self.v_nuevo_chat.get())
        a.prompt_compacto = bool(self.v_compacto.get())
        a.fecha_inicio = self.v_ini.get().strip()
        a.fecha_fin = self.v_fin.get().strip()
        a.semilla = self.v_semilla.get().strip()
        a.modo_fechas = self.v_modo.get()
        a.ventana_cercania = max(1, entero(self.v_ventana, 7))
        a.repeticiones = {k: v for k, v in self.repeticiones.items() if v}
        a.forzar_copilot = sorted(self.forzar)
        a.guardar()
        return a

    @staticmethod
    def _semilla_aleatoria() -> str:
        return "AUD-" + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(5))

    def _nueva_semilla(self):
        self.v_semilla.set(self._semilla_aleatoria())

    # ------------------------------------------------------------------ paso 1: acciones
    def _elegir_excel(self):
        inicial = Path(self.v_archivo.get()).parent if self.v_archivo.get() else RAIZ / "data"
        ruta = filedialog.askopenfilename(title="Excel de control", initialdir=str(inicial),
                                          filetypes=[("Excel", "*.xlsx *.xlsm"), ("Todos", "*.*")])
        if ruta:
            self.v_archivo.set(ruta)
            self._cargar_hojas(ruta)
            self._cargar_excel()

    def _elegir_dir(self, var: tk.StringVar):
        ruta = filedialog.askdirectory(title="Elegir carpeta", initialdir=var.get() or str(Path.home()))
        if ruta:
            var.set(ruta)

    def _elegir_archivo(self, var: tk.StringVar, tipos):
        ruta = filedialog.askopenfilename(title="Elegir archivo", filetypes=tipos)
        if ruta:
            var.set(ruta)

    def _cargar_hojas(self, ruta: str):
        try:
            hojas = listar_hojas(ruta)
        except Exception as e:
            self.lbl_excel.configure(text=f"No se pudo abrir el archivo: {e}", fg=C["rojo"])
            return
        self.cb_hoja.configure(values=hojas)
        if self.v_hoja.get() not in hojas:
            prefer = [h for h in hojas if "POLITICA" in h.upper()]
            self.v_hoja.set(prefer[0] if prefer else hojas[0])

    def _autocargar(self):
        ruta = self.v_archivo.get()
        if ruta and Path(ruta).exists():
            self._cargar_hojas(ruta)
            self._cargar_excel(silencioso=True)
        else:
            self.lbl_excel.configure(text="Elige el archivo de control para empezar.", fg=C["suave"])

    def _cargar_excel(self, silencioso: bool = False):
        ruta = self.v_archivo.get().strip()
        if not ruta or not Path(ruta).exists():
            if not silencioso:
                messagebox.showwarning("Excel de control", "El archivo no existe.")
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self.filas, encabezados, avisos = leer_control(ruta, self.v_hoja.get() or None)
        except Exception as e:
            self.filas = []
            self.lbl_excel.configure(text=f"Error al leer: {e}", fg=C["rojo"])
            return
        finally:
            self.config(cursor="")
        servidores = {s.clave for f in self.filas for s in f.servidores}
        self._agrupar()
        reconocidas = sum(1 for g in self.grupos.values() if g["reconocida"])
        txt = (f"✓  {len(self.filas)} filas  ·  {len(servidores)} servidores únicos  ·  "
               f"{len(self.grupos)} tipos de agenda ({reconocidas} entendidos por Python, "
               f"{len(self.grupos) - reconocidas} se delegan a Copilot)")
        if avisos:
            txt += "\n⚠  " + "\n⚠  ".join(avisos)
        self.lbl_excel.configure(text=txt, fg=C["verde"] if not avisos else C["ambar"])
        self._refrescar_tabla()
        self._estado(f"Excel cargado: {Path(ruta).name}")

    def _agrupar(self):
        g: dict[str, dict] = {}
        for f in self.filas:
            r = f.regla
            d = g.setdefault(r.clave, {"regla": r, "textos": defaultdict(int), "filas": 0,
                                       "reconocida": r.reconocida, "nota": r.nota})
            d["textos"][" ".join(f.agenda_texto.split()) or "(vacía)"] += 1
            d["filas"] += 1
        self.grupos = dict(sorted(g.items(), key=lambda kv: -kv[1]["filas"]))

    def _refrescar_nombre_salida(self):
        carpeta = self.v_salida.get().strip()
        if not carpeta:
            self.lbl_nombre_salida.configure(text="Obligatorio: elige dónde guardar el resultado.", fg=C["rojo"])
        elif not Path(carpeta).exists():
            self.lbl_nombre_salida.configure(text="La carpeta no existe (se creará al iniciar).", fg=C["ambar"])
        else:
            self.lbl_nombre_salida.configure(
                text=f"Se creará: Auditoria_Backups_{datetime.now():%Y%m%d_%H%M}.xlsx\n"
                     "(hojas: Resumen, Consolidado, Detalle por fecha, Lotes Copilot)", fg=C["suave"])

    def _probar(self):
        if self.prueba and self.prueba.is_alive():
            return
        a = self._volcar_valores().copia()
        carpeta = Path(a.carpeta_salida) if a.carpeta_salida else RAIZ / "diagnostico"
        self.cola = self.cola or queue.Queue()
        self.prueba = PruebaConexion(a, carpeta, self.cola)
        self.prueba.start()
        self.btn_probar.configure(state="disabled")
        self.lbl_probar.configure(text="Probando… mira la ventana de Edge (inicia sesión si te lo pide).", fg=C["azul"])
        self._pildora("Probando conexión", C["azul"])

    # ------------------------------------------------------------------ paso 2: tabla
    def _rango(self) -> tuple[date | None, date | None]:
        ini, fin = parse_fecha_ui(self.v_ini.get()), parse_fecha_ui(self.v_fin.get())
        self.e_ini.configure(style="TEntry" if ini else "Invalido.TEntry")
        self.e_fin.configure(style="TEntry" if fin and (not ini or fin >= ini) else "Invalido.TEntry")
        if ini and fin and fin < ini:
            return None, None
        return ini, fin

    def _programar_tabla(self):
        if self._job_resumen:
            self.after_cancel(self._job_resumen)
        self._job_resumen = self.after(350, self._refrescar_tabla)

    def _programar_resumen(self):
        self.after(50, self._actualizar_resumen)

    def _origen(self, clave: str, g: dict) -> str:
        if self.v_modo.get() == "copilot" or not g["reconocida"] or clave in self.forzar:
            return "Copilot"
        return "Python"

    def _refrescar_tabla(self):
        self._job_resumen = None
        self._cerrar_editor()
        sel = set(self.tabla.selection())
        self.tabla.delete(*self.tabla.get_children())
        ini, fin = self._rango()
        filtro = self.v_filtro.get().strip().lower()
        for i, (clave, g) in enumerate(self.grupos.items()):
            textos = " · ".join(f"{t} ({n})" if len(g["textos"]) > 1 else t for t, n in
                                sorted(g["textos"].items(), key=lambda kv: -kv[1]))
            if filtro and filtro not in clave.lower() and filtro not in textos.lower():
                continue
            origen = self._origen(clave, g)
            if origen == "Copilot":
                dias = "—"
            else:
                dias = str(len(g["regla"].dias_en_rango(ini, fin))) if ini and fin else "?"
            n = self.repeticiones.get(clave, 0)
            tags = ["activa" if n else "cero"]
            if origen == "Copilot":
                tags.append("copilot")
            elif i % 2:
                tags.append("par")
            self.tabla.insert("", "end", iid=clave, values=(clave, textos, g["filas"], dias,
                                                           ("◆ Copilot" if origen == "Copilot" else "Python"), n),
                              tags=tags)
        for s in sel:
            if self.tabla.exists(s):
                self.tabla.selection_add(s)
        self._actualizar_resumen()

    def _doble_clic(self, ev):
        fila = self.tabla.identify_row(ev.y)
        col = self.tabla.identify_column(ev.x)
        if not fila:
            return
        if col == "#6":
            self._editar_n(fila)
        elif col == "#5":
            g = self.grupos[fila]
            if not g["reconocida"]:
                messagebox.showinfo("Agenda no reconocida",
                                    f"«{fila}» no se puede calcular en Python, así que Copilot elige las fechas.\n\n{g['nota']}")
                return
            if self.v_modo.get() == "copilot":
                return
            self.forzar.symmetric_difference_update({fila})
            self._refrescar_tabla()
        elif col == "#1" and self.grupos[fila]["nota"]:
            self._estado(f"{fila}: {self.grupos[fila]['nota']}")

    def _editar_n(self, fila: str):
        self._cerrar_editor()
        bbox = self.tabla.bbox(fila, "#6")
        if not bbox:
            return
        x, y, w, h = bbox
        var = tk.IntVar(value=self.repeticiones.get(fila, 0))
        sp = ttk.Spinbox(self.tabla, from_=0, to=99, textvariable=var, width=4, justify="center")
        sp.place(x=x + 2, y=y + 2, width=w - 4, height=h - 4)
        sp.focus_set()
        sp.selection_range(0, "end")

        def guardar(_=None):
            try:
                self.repeticiones[fila] = max(0, int(var.get()))
            except Exception:
                pass
            self._cerrar_editor()
            self._refrescar_tabla()
        sp.bind("<Return>", guardar)
        sp.bind("<FocusOut>", guardar)
        sp.bind("<Escape>", lambda e: self._cerrar_editor())
        self._editor = sp

    def _cerrar_editor(self):
        if self._editor is not None:
            try:
                self._editor.destroy()
            except Exception:
                pass
            self._editor = None

    def _aplicar_n(self):
        try:
            n = max(0, int(self.v_n_masivo.get()))
        except Exception:
            return
        sel = self.tabla.selection()
        if not sel:
            self._estado("Selecciona una o varias agendas en la tabla (Ctrl/Shift + clic).")
            return
        for k in sel:
            self.repeticiones[k] = n
        self._refrescar_tabla()

    def _sumar(self, d: int):
        for k in self.tabla.selection():
            self.repeticiones[k] = max(0, self.repeticiones.get(k, 0) + d)
        self._refrescar_tabla()

    def _todo_cero(self):
        self.repeticiones = {}
        self._refrescar_tabla()

    def _plan_actual(self) -> Plan | None:
        ini, fin = self._rango()
        if not (ini and fin) or not self.filas:
            return None
        return construir_plan(self.filas, self.repeticiones, ini, fin, self.v_semilla.get().strip() or "AUD",
                              self.v_modo.get(), sorted(self.forzar), self._entero(self.v_fch_lote, 30))

    @staticmethod
    def _entero(var, d):
        try:
            return int(var.get())
        except Exception:
            return d

    def _estimar_lotes(self, plan: Plan) -> int:
        unidades = list(plan.unidades.values())
        n = 0
        while unidades:
            l = armar_lote(unidades, self._entero(self.v_srv_lote, 6), self._entero(self.v_fch_lote, 30))
            unidades = unidades[len(l):]
            n += 1
        return n

    def _actualizar_resumen(self):
        if not self.filas:
            self.lbl_resumen.configure(text="Carga el Excel de control en el paso 1.", fg=C["suave"])
            return
        ini, fin = self._rango()
        if not (ini and fin):
            self.lbl_resumen.configure(text="Revisa el rango de fechas.", fg=C["rojo"])
            return
        plan = self._plan_actual()
        r = plan.resumen()
        if not r["filas"]:
            self.lbl_resumen.configure(text="Pon N > 0 en al menos una agenda para armar la muestra.", fg=C["suave"])
            return
        lotes = self._estimar_lotes(plan)
        pest = max(1, min(4, self._entero(self.v_pestanas, 1)))
        t = lotes * SEG_POR_LOTE_ESTIMADO / pest
        extra = f"  ·  {r['delegadas']} consultas con fechas elegidas por Copilot" if r["delegadas"] else ""
        self.lbl_resumen.configure(
            text=(f"{r['filas']} filas  →  {r['servidores']} servidores  ·  {r['fechas']} fechas a verificar{extra}\n"
                  f"≈ {lotes} consultas a Copilot  ·  tiempo estimado ≈ {fmt_dur(t)} con {pest} pestaña(s)"),
            fg=C["texto"])

    def _ver_prompt(self):
        plan = self._plan_actual()
        if not plan or not plan.unidades:
            messagebox.showinfo("Prompt de ejemplo", "Primero carga el Excel y pon N > 0 en alguna agenda.")
            return
        unidades = list(plan.unidades.values())
        lote = armar_lote(unidades, self._entero(self.v_srv_lote, 6), self._entero(self.v_fch_lote, 30))
        ini, fin = self._rango()
        texto, _ = construir_prompt(lote, self.v_carpeta.get().strip(), ini, fin,
                                    self._entero(self.v_ventana, 7), "EJ42", compacto=bool(self.v_compacto.get()))
        win = tk.Toplevel(self)
        win.title("Prompt de ejemplo (primer lote)")
        win.geometry("860x640")
        win.configure(bg=C["fondo"])
        txt = tk.Text(win, wrap="word", font=("Consolas", 10), relief="flat", padx=12, pady=10)
        txt.insert("1.0", texto)
        txt.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        barra = tk.Frame(win, bg=C["fondo"])
        barra.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(barra, text=f"{len(texto)} caracteres · puedes pegarlo a mano en Copilot para probar el formato",
                 bg=C["fondo"], fg=C["suave"], font=("Segoe UI", 9)).pack(side="left")

        def copiar():
            self.clipboard_clear()
            self.clipboard_append(texto)
            self._estado("Prompt copiado al portapapeles.")
        ttk.Button(barra, text="Copiar", style="Primario.TButton", command=copiar).pack(side="right")

    # presets de rango
    def _preset_mes(self):
        hoy = date.today().replace(day=1)
        fin = hoy - timedelta(days=1)
        self.v_ini.set(fin.replace(day=1).isoformat())
        self.v_fin.set(fin.isoformat())

    def _preset_dias(self, n: int):
        hoy = date.today()
        self.v_ini.set((hoy - timedelta(days=n)).isoformat())
        self.v_fin.set(hoy.isoformat())

    def _preset_anio(self):
        hoy = date.today()
        self.v_ini.set(date(hoy.year, 1, 1).isoformat())
        self.v_fin.set(hoy.isoformat())

    # ------------------------------------------------------------------ navegación
    def _al_cambiar_tab(self):
        self._actualizar_botones_paso()
        if self.nb.index("current") == 1:
            self._refrescar_tabla()

    def _actualizar_botones_paso(self):
        i = self.nb.index("current") if hasattr(self, "nb") else 0
        self.btn_ant.configure(state="normal" if i > 0 else "disabled")
        if i < 2:
            self.btn_sig.configure(text="Siguiente  →", state="normal")
        else:
            self.btn_sig.configure(text="Siguiente  →", state="disabled")

    def _siguiente(self):
        i = self.nb.index("current")
        if i == 0:
            if not self.filas:
                self._cargar_excel()
                if not self.filas:
                    return
            if not self.v_salida.get().strip():
                messagebox.showwarning("Falta la ubicación de salida", "Elige la carpeta donde se guardará el Excel consolidado.")
                self.e_salida.focus_set()
                return
        self._volcar_valores()
        self.nb.select(min(2, i + 1))

    def _anterior(self):
        self.nb.select(max(0, self.nb.index("current") - 1))

    # ------------------------------------------------------------------ ejecución
    def _validar_inicio(self) -> tuple[Plan, Ajustes] | None:
        a = self._volcar_valores()
        if not self.filas:
            messagebox.showwarning("Falta el Excel", "Carga el Excel de control (paso 1).")
            self.nb.select(0)
            return None
        if not a.carpeta_salida:
            messagebox.showwarning("Falta la salida", "Elige la carpeta de salida (paso 1).")
            self.nb.select(0)
            return None
        try:
            Path(a.carpeta_salida).mkdir(parents=True, exist_ok=True)
            prueba = Path(a.carpeta_salida) / ".escritura_ok"
            prueba.write_text("ok")
            prueba.unlink()
        except Exception as e:
            messagebox.showerror("Carpeta de salida", f"No puedo escribir en la carpeta:\n{e}")
            return None
        ini, fin = self._rango()
        if not (ini and fin):
            messagebox.showwarning("Rango de fechas", "Revisa las fechas (paso 2).")
            self.nb.select(1)
            return None
        plan = self._plan_actual()
        if not plan or not plan.unidades:
            messagebox.showwarning("Muestra vacía", "Pon N > 0 en al menos una agenda (paso 2).")
            self.nb.select(1)
            return None
        if not a.url_carpeta or not a.nombre_carpeta:
            messagebox.showwarning("Copilot", "Completa la URL y el nombre de la carpeta (paso 1).")
            self.nb.select(0)
            return None
        return plan, a

    def _iniciar(self):
        if self.motor and self.motor.is_alive():
            return
        v = self._validar_inicio()
        if not v:
            return
        plan, a = v
        r = plan.resumen()
        lotes = self._estimar_lotes(plan)
        if not messagebox.askokcancel(
                "Iniciar auditoría",
                f"Se consultarán {r['servidores']} servidores ({r['fechas']} fechas) en ≈ {lotes} consultas a Copilot.\n\n"
                "Edge se abrirá con su propio perfil. Mientras corre, no uses el portapapeles "
                "(el programa pega el prompt con Ctrl+V).\n\n¿Continuar?"):
            return
        ruta = Path(a.carpeta_salida) / f"Auditoria_Backups_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        self._lanzar(Motor(plan, a.copia(), ruta, self.cola))

    def _reanudar_archivo(self):
        if self.motor and self.motor.is_alive():
            return
        ruta = filedialog.askopenfilename(title="Archivo de avance", initialdir=self.v_salida.get() or str(Path.home()),
                                          filetypes=[("Avance de auditoría", "*.avance.json"), ("JSON", "*.json")])
        if not ruta:
            return
        try:
            datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
            plan = Plan.desde_dict(datos["plan"])
        except Exception as e:
            messagebox.showerror("Reanudar", f"El archivo no es válido: {e}")
            return
        a = self._volcar_valores().copia()
        excel = Path(datos.get("excel") or Path(ruta).with_suffix("").with_suffix(".xlsx"))
        hechas = sum(1 for s in datos.get("estado_unidades", {}).values() if s.get("estado") == "ok")
        if not messagebox.askokcancel("Reanudar", f"{hechas} de {len(plan.unidades)} consultas ya estaban listas.\n"
                                                  f"Se continuará con el resto y se actualizará:\n{excel.name}"):
            return
        self._lanzar(Motor(plan, a, excel, self.cola, avance=datos))

    def _lanzar(self, motor: Motor):
        self.motor = motor
        self.ruta_resultado = motor.ruta_excel
        for k in self.kpis.values():
            k.set(0)
        self.barra.configure(value=0)
        self._log("info", f"Resultado: {motor.ruta_excel}")
        self.nb.select(2)
        motor.start()
        self.btn_iniciar.configure(state="disabled")
        self.btn_reanudar.configure(state="disabled")
        self.btn_pausa.configure(state="normal", text="⏸  Pausar")
        self.btn_parar.configure(state="normal")
        self.btn_excel.configure(state="normal")
        self.btn_carpeta.configure(state="normal")
        self._pildora("En curso", C["verde"])

    def _pausar(self):
        if not self.motor:
            return
        if self.motor.pausa.is_set():
            self.motor.pausa.clear()
            self.btn_pausa.configure(text="⏸  Pausar")
            self._pildora("En curso", C["verde"])
            self._log("info", "Reanudado.")
        else:
            self.motor.pausa.set()
            self.btn_pausa.configure(text="▶  Continuar")
            self._pildora("En pausa", C["ambar"])
            self._log("aviso", "En pausa: las consultas en curso terminan, no se envían nuevas.")

    def _detener(self):
        if self.motor and self.motor.is_alive():
            if messagebox.askyesno("Detener", "¿Detener la auditoría? El avance queda guardado y se puede reanudar."):
                self.motor.parar.set()
                self.btn_parar.configure(state="disabled")
                self._log("aviso", "Deteniendo…")

    # ------------------------------------------------------------------ cola de eventos
    def _leer_cola(self):
        try:
            while True:
                tipo, d = self.cola.get_nowait()
                if tipo == "log":
                    self._log(d["nivel"], d["msg"])
                elif tipo == "estado":
                    self._estado(d["texto"])
                elif tipo == "progreso":
                    self._mostrar_progreso(d)
                elif tipo == "fin":
                    self._fin(d)
                elif tipo == "prueba_fin":
                    self.btn_probar.configure(state="normal")
                    if d["ok"]:
                        self.lbl_probar.configure(text="✓  Conexión correcta: Copilot respondió en el formato esperado.", fg=C["verde"])
                        self._pildora("Listo", C["rojo_osc"])
                    else:
                        self.lbl_probar.configure(text="✗  Revisa la bitácora (paso 3) y el diagnóstico generado.", fg=C["rojo"])
                        self._pildora("Revisar conexión", C["ambar"])
        except queue.Empty:
            pass
        self.after(150, self._leer_cola)

    def _mostrar_progreso(self, d: dict):
        conteo = d.get("conteo", {})
        for clave, k in self.kpis.items():
            k.set(conteo.get(clave, 0))
        tot = max(1, d["unidades_total"])
        hechas = d["unidades_ok"] + d["unidades_sin"]
        pct = 100 * hechas / tot
        self.barra.configure(value=pct)
        partes = [f"{hechas} de {tot} consultas de servidor ({pct:.0f} %)", f"lotes enviados: {d['lotes']}",
                  f"transcurrido: {fmt_dur(d.get('transcurrido'))}"]
        if d.get("prom_lote"):
            partes.append(f"≈ {d['prom_lote']:.0f} s por lote")
        if d.get("eta") is not None and d.get("lotes_restantes"):
            partes.append(f"restante ≈ {fmt_dur(d['eta'])}")
        self.lbl_prog.configure(text="   ·   ".join(partes), fg=C["texto"])

    def _fin(self, d: dict):
        if self.motor and self.motor.a.prompt_compacto and not self.v_compacto.get():
            self.v_compacto.set(True)
            self.a.max_caracteres_prompt = self.motor.a.max_caracteres_prompt
            self._volcar_valores()
        self.ruta_resultado = Path(d["ruta"])
        estado = d["estado"]
        self.btn_iniciar.configure(state="normal")
        self.btn_reanudar.configure(state="normal")
        self.btn_pausa.configure(state="disabled", text="⏸  Pausar")
        self.btn_parar.configure(state="disabled")
        ok = estado == "Completada"
        self._pildora("Terminado" if ok else "Detenido", C["verde"] if ok else C["ambar"])
        self._log("ok" if ok else "aviso", f"{estado}. Excel: {self.ruta_resultado}")
        if ok:
            if messagebox.askyesno("Auditoría terminada", f"Listo. ¿Abrir el Excel?\n\n{self.ruta_resultado}"):
                abrir_ruta(self.ruta_resultado)

    # ------------------------------------------------------------------ utilidades
    def _log(self, nivel: str, msg: str):
        if nivel == "debug" and not self.v_detalle.get():
            return
        self.log_txt.configure(state="normal")
        self.log_txt.insert("end", datetime.now().strftime("%H:%M:%S  "), "hora")
        icono = {"ok": "✓ ", "aviso": "⚠ ", "error": "✗ ", "debug": "· "}.get(nivel, "")
        self.log_txt.insert("end", icono + msg + "\n", nivel)
        self.log_txt.see("end")
        self.log_txt.configure(state="disabled")
        if nivel in ("ok", "aviso", "error", "info"):
            self._estado(msg)
        if nivel in ("aviso", "error") and self.prueba and self.prueba.is_alive():
            self.lbl_probar.configure(text=msg[:120], fg=C["ambar"] if nivel == "aviso" else C["rojo"])

    def _estado(self, txt: str):
        self.lbl_estado.configure(text=txt[:200])

    def _pildora(self, txt: str, color: str):
        self.pildora.configure(text=f"●  {txt}", bg=color)

    def _al_cerrar(self):
        corriendo = (self.motor and self.motor.is_alive()) or (self.prueba and self.prueba.is_alive())
        if corriendo:
            if not messagebox.askyesno("Salir", "Hay una ejecución en curso. El avance queda guardado.\n¿Salir de todas formas?"):
                return
            for hilo in (self.motor, self.prueba):
                if hilo and hilo.is_alive():
                    hilo.parar.set()
                    hilo.join(timeout=8)          # dejar que guarde el avance y cierre Edge
        try:
            self._volcar_valores()
        except Exception:
            pass
        self.destroy()


def ejecutar():
    App().mainloop()
