# Auditoría de backups con Copilot

## Uso
```
venv\Scripts\activate
pip install -r requirements.txt      (agrega openpyxl)
python main.py
```
1. **Datos y conexión**: Excel de control, URL/nombre de la carpeta de OneDrive, carpeta de salida.
   La primera vez usa **Probar conexión**: abre Edge con su propio perfil (inicia sesión ahí una vez),
   llega al chat de Copilot, hace una pregunta mínima y deja un diagnóstico en la carpeta de salida.
2. **Muestra**: rango de fechas, semilla y N por tipo de agenda. "Ver prompt de ejemplo" muestra
   exactamente lo que se enviará (se puede pegar a mano en Copilot para probar).
3. **Ejecución**: progreso, contadores y bitácora. Pausar / Detener / Reanudar desde archivo.

Sin consola: doble clic en **`Auditoria Backups.bat`** (abre la app con `pythonw`).
Edge trabaja oculto por defecto (paso 1 → "Ventana de Edge"); en el paso 3 está "Mostrar Edge".

## Cómo funciona
- `agenda.py` traduce AGENDA a una regla de calendario ("Primer domingo del mes", "Lunes a sábado",
  "Días 7, 15, 21 y 30"...). Lo que no se entiende ("Por demanda") se delega a Copilot.
- `servidores.py` limpia SERVIDOR: código + hostname = un servidor con alias; IPs como alias; notas fuera.
- `planificador.py` sortea fechas (reproducible con la semilla), agrupa por servidor y arma lotes.
- `prompt_copilot.py` arma el prompt con un contrato de salida (`@@INICIO-código@@ ... @@FIN-código@@`,
  una línea `ID ; FECHA_PEDIDA ; ESTADO ; FECHA_ENCONTRADA ; EVIDENCIA` por fecha), lo lee y lo valida.
  Python decide el estado final: **SI / PARCIAL / CERCANA / NO COINCIDEN FECHAS**.
- `copilot_web.py` maneja Edge/Copilot (iframes, shadow DOM, pegar, esperar, leer).
- `motor.py` reparte lotes en 1–4 pestañas, reintenta lo que falte y guarda avance tras cada lote.
- `consolidado.py` escribe el Excel: Resumen, Consolidado, Detalle por fecha, Lotes Copilot.

## Si Microsoft cambia la interfaz de Copilot
1. Paso 1 → **Calibrar con clics**: se abre Edge y una ventanita roja te guía en 3 clics
   (botón de Copilot → caja del chat → botón Enviar). Lo grabado queda en `ajustes.json`
   → `selectores_calibrados` y tiene prioridad sobre los selectores por defecto.
2. **Probar conexión** para verificar. Genera `diagnostico_copilot_*/elementos.json` con los
   botones y cajas encontrados, por si hay que ajustar algo a mano en `ajustes.json` → `selectores`.

Los selectores usan atributos estables (`data-automation-id`, `data-lexical-editor`, `aria-label`,
clases `fai-…`). Las clases tipo `r18fti29` / `___1ld5kid` las genera Fluent UI y cambian: no sirven.

Los módulos anteriores (`manejo_*`, `visuales`, `auditoria*`, `extraccion`, `preprocesamiento`)
quedan intactos pero ya no se usan.
