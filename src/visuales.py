import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from typing import Dict, List, Optional

# --- CONFIGURACIÓN DE DISEÑO ---
COLOR_FONDO = "#ffffff"
COLOR_PRIMARIO = "#ff1221"  # Rojo corporativo
COLOR_TEXTO = "#333333"
FUENTE_TITULO = ("Segoe UI", 12, "bold")
FUENTE_NORMAL = ("Segoe UI", 10)

class ContadorComponente(tk.Frame):
    """Componente atómico que maneja la lógica visual y de estado de un contador individual."""
    
    def __init__(self, parent, nombre_repeticion: str, bg_color: str):
        super().__init__(parent, bg=bg_color)
        self.nombre = nombre_repeticion
        self.valor = tk.IntVar(value=0)
        
        self._construir_ui()

    def _construir_ui(self):
        lbl_nombre = tk.Label(self, text=self.nombre, bg=self.master["bg"], fg=COLOR_TEXTO, 
                              font=FUENTE_NORMAL, anchor="w", width=25)
        lbl_nombre.pack(side=tk.LEFT, padx=10, pady=5)
        
        btn_restar = tk.Button(self, text="-", width=3, bg=COLOR_FONDO, fg=COLOR_PRIMARIO, 
                               font=FUENTE_TITULO, relief=tk.FLAT, borderwidth=1,
                               command=self._restar)
        btn_restar.pack(side=tk.LEFT, padx=5)
        
        lbl_valor = tk.Label(self, textvariable=self.valor, bg=self.master["bg"], 
                             fg=COLOR_TEXTO, font=FUENTE_NORMAL, width=3)
        lbl_valor.pack(side=tk.LEFT, padx=5)
        
        btn_sumar = tk.Button(self, text="+", width=3, bg=COLOR_PRIMARIO, fg=COLOR_FONDO, 
                              font=FUENTE_TITULO, relief=tk.FLAT, borderwidth=0,
                              command=self._sumar)
        btn_sumar.pack(side=tk.LEFT, padx=5)

    def _sumar(self):
        self.valor.set(self.valor.get() + 1)

    def _restar(self):
        if self.valor.get() > 0:
            self.valor.set(self.valor.get() - 1)

    def obtener_datos(self) -> int:
        return self.valor.get()


class InterfazAuditoria:
    """Maneja el ciclo de vida y recolección de datos construyendo directamente sobre root."""
    
    def __init__(self, master: tk.Tk, repeticiones: List[str]):
        self.master = master
        self.repeticiones = repeticiones
        self.resultado_final: Optional[Dict] = None
        
        self.master.title("Configuración de Parámetros de Auditoría")
        self.master.geometry("500x650")
        self.master.configure(bg=COLOR_FONDO)
        self.master.resizable(False, False)
        
        # --- FORZAR VENTANA AL FRENTE (Evita que se esconda tras la consola) ---
        self.master.lift()
        self.master.attributes('-topmost', True)
        # Soltamos el topmost después de 100ms para que el usuario pueda cambiar de ventana si quiere
        self.master.after(100, lambda: self.master.attributes('-topmost', False))
        
        # Variables de estado
        self.str_fecha_inicio = tk.StringVar()
        self.str_fecha_fin = tk.StringVar()
        self.componentes_contadores: List[ContadorComponente] = []
        
        self._configurar_estilos()
        self._construir_ui()
        
        # Interceptar el cierre manual de la ventana (la 'X')
        self.master.protocol("WM_DELETE_WINDOW", self._cancelar)

    def _configurar_estilos(self):
        style = ttk.Style(self.master)
        style.theme_use('clam')
        style.configure("TEntry", fieldbackground=COLOR_FONDO, foreground=COLOR_TEXTO)

    def _construir_ui(self):
        # 1. Título
        lbl_titulo = tk.Label(self.master, text="Parámetros de Ejecución", bg=COLOR_FONDO, 
                              fg=COLOR_PRIMARIO, font=("Segoe UI", 16, "bold"))
        lbl_titulo.pack(pady=(20, 10))

        # 2. Rango de Fechas
        frame_fechas = tk.LabelFrame(self.master, text="Rango de Fechas Global (YYYY-MM-DD)", 
                                     bg=COLOR_FONDO, fg=COLOR_TEXTO, font=FUENTE_NORMAL,
                                     padx=15, pady=15)
        frame_fechas.pack(fill=tk.X, padx=20, pady=10)

        # Fecha Inicio
        tk.Label(frame_fechas, text="Fecha Inicial:", bg=COLOR_FONDO, fg=COLOR_TEXTO, font=FUENTE_NORMAL).grid(row=0, column=0, sticky="w", pady=5)
        entry_inicio = ttk.Entry(frame_fechas, textvariable=self.str_fecha_inicio, font=FUENTE_NORMAL, width=15)
        entry_inicio.grid(row=0, column=1, padx=10, pady=5)
        
        # Fecha Fin (Hasta hoy)
        hoy = datetime.now().strftime("%Y-%m-%d")
        self.str_fecha_fin.set(hoy)
        tk.Label(frame_fechas, text="Fecha Final:", bg=COLOR_FONDO, fg=COLOR_TEXTO, font=FUENTE_NORMAL).grid(row=0, column=2, sticky="w", padx=(10,0), pady=5)
        entry_fin = ttk.Entry(frame_fechas, textvariable=self.str_fecha_fin, font=FUENTE_NORMAL, width=15)
        entry_fin.grid(row=0, column=3, padx=10, pady=5)

        # 3. Lista Deslizable de Repeticiones
        lbl_repeticiones = tk.Label(self.master, text="Frecuencias y Repeticiones a Evaluar", 
                                    bg=COLOR_FONDO, fg=COLOR_TEXTO, font=FUENTE_TITULO)
        lbl_repeticiones.pack(anchor="w", padx=20, pady=(15, 5))

        frame_scroll = tk.Frame(self.master, bg=COLOR_FONDO, highlightbackground="#cccccc", highlightthickness=1)
        frame_scroll.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        canvas = tk.Canvas(frame_scroll, bg=COLOR_FONDO, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame_scroll, orient=tk.VERTICAL, command=canvas.yview)
        
        self.frame_interior = tk.Frame(canvas, bg=COLOR_FONDO)
        
        self.frame_interior.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.frame_interior, anchor="nw", width=430)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        for repeticion in self.repeticiones:
            comp = ContadorComponente(self.frame_interior, nombre_repeticion=repeticion, bg_color=COLOR_FONDO)
            comp.pack(fill=tk.X, pady=2)
            self.componentes_contadores.append(comp)

        # Scroll con el ratón
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # 4. Botón de Continuar
        frame_footer = tk.Frame(self.master, bg=COLOR_FONDO)
        frame_footer.pack(fill=tk.X, padx=20, pady=20)
        
        btn_continuar = tk.Button(frame_footer, text="Continuar con la Auditoría", 
                                  bg=COLOR_PRIMARIO, fg=COLOR_FONDO, font=FUENTE_TITULO,
                                  relief=tk.FLAT, cursor="hand2", padx=10, pady=5,
                                  command=self._validar_y_procesar)
        btn_continuar.pack(side=tk.RIGHT)

    def _validar_fecha(self, fecha_str: str) -> bool:
        try:
            datetime.strptime(fecha_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def _validar_y_procesar(self):
        f_inicio = self.str_fecha_inicio.get().strip()
        f_fin = self.str_fecha_fin.get().strip()
        
        if not f_inicio or not f_fin:
            messagebox.showwarning("Fechas Incompletas", "Debes establecer el rango de fechas para continuar.", parent=self.master)
            return
            
        if not self._validar_fecha(f_inicio) or not self._validar_fecha(f_fin):
            messagebox.showerror("Formato Inválido", "Las fechas deben tener el formato YYYY-MM-DD.", parent=self.master)
            return

        if f_inicio > f_fin:
            messagebox.showerror("Rango Inválido", "La fecha inicial no puede ser mayor a la fecha final.", parent=self.master)
            return

        repeticiones_dict = {
            comp.nombre: comp.obtener_datos() 
            for comp in self.componentes_contadores 
            if comp.obtener_datos() > 0 
        }

        self.resultado_final = {
            "rango_fechas": {
                "inicio": f_inicio,
                "fin": f_fin
            },
            "repeticiones_seleccionadas": repeticiones_dict
        }
        
        # Terminamos la ejecución gráfica
        self.master.quit()

    def _cancelar(self):
        self.resultado_final = None
        self.master.quit()


def solicitar_parametros_ejecucion(repeticiones_excel: List[str]) -> Optional[Dict]:
    """
    Despliega la interfaz directamente sin Toplevels anidados para evitar 
    bloqueos en el motor de renderizado.
    """
    root = tk.Tk()  # Instancia única base (no se oculta)
    app = InterfazAuditoria(root, repeticiones_excel)
    
    root.mainloop()  # Bloquea la consola hasta que app ejecute master.quit()
    
    resultado = app.resultado_final
    root.destroy()  # Limpia la memoria completamente tras salir del mainloop
    
    return resultado