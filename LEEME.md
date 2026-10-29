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
Los selectores están en `ajustes.json` → `selectores` (listas; gana el primero visible).
"Probar conexión" genera `diagnostico_copilot_*/elementos.json` con los botones y cajas encontrados
en cada frame: con eso se ajusta el selector que falle (`abrir_copilot`, `entrada`, `enviar`,
`detener`, `nuevo_chat`).

Los módulos anteriores (`manejo_*`, `visuales`, `auditoria*`, `extraccion`, `preprocesamiento`)
quedan intactos pero ya no se usan.
