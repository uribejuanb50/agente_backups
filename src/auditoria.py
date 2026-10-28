import json
import io
from pathlib import Path
import polars as pl


def crear_cache_adjuntos(msg_adjuntos: list[str], eml_adjuntos: list[str], preprocesamiento_mod) -> list[dict]:
    """
    Procesa todos los correos una sola vez y convierte los contenidos de los adjuntos
    en un texto crudo en minúsculas dentro de memoria para búsquedas ultrarrápidas.
    """
    cache_adjuntos = []
    
    correos_totales = [(r, "msg") for r in msg_adjuntos] + [(r, "eml") for r in eml_adjuntos]
    print(f"📦 Pre-indexando {len(correos_totales)} correos con adjuntos en memoria...")

    for ruta_correo, tipo in correos_totales:
        resultados, _ = preprocesamiento_mod.procesar_adjuntos_correo(ruta_correo, tipo_fuente=tipo)
        
        for item in resultados:
            nombre_adjunto = item.get("archivo", "adjunto_desconocido")
            tipo_item = item.get("tipo")
            contenido = item.get("contenido")
            
            texto_unificado = ""
            
            if tipo_item == "dataframe" and isinstance(contenido, pl.DataFrame):
                # Genera una representación en texto del DataFrame completo
                buffer = io.StringIO()
                contenido.write_csv(buffer)
                texto_unificado = buffer.getvalue()
            elif isinstance(contenido, str):
                texto_unificado = contenido

            if texto_unificado.strip():
                cache_adjuntos.append({
                    "correo": ruta_correo,
                    "adjunto": nombre_adjunto,
                    "texto_lower": texto_unificado.lower()
                })

    print(f"✅ Pre-indexación finalizada: {len(cache_adjuntos)} adjuntos cargados en caché.")
    return cache_adjuntos


def ejecutar_auditoria(df_control: pl.DataFrame, cache_adjuntos: list[dict], ruta_log: Path) -> tuple[list[dict], list[dict]]:
    """
    Ejecuta la validación cruzada entre el Excel de control y el caché de adjuntos.
    Escribe resultados en tiempo real en `ruta_log`.
    """
    coincidencias = []
    sin_coincidencias = []

    # Abrir archivo de log para escritura en tiempo real (flush=True)
    with open(ruta_log, "w", encoding="utf-8") as f_log:
        f_log.write("=== INICIO DE AUDITORÍA DE BACKUPS EN TIEMPO REAL ===\n\n")
        f_log.flush()

        for idx, fila in enumerate(df_control.iter_rows(named=True), start=2): # Start=2 por cabecera Excel
            ans = fila.get("ANS", "")
            servidores = fila.get("SERVIDORES_LISTA", [])
            fechas = fila.get("FECHAS_ESPERADAS", [])
            servidor_orig = fila.get("SERVIDOR", "")
            agenda_orig = fila.get("AGENDA", "")

            encontrado = False

            if servidores and fechas and cache_adjuntos:
                for adjunto_info in cache_adjuntos:
                    texto_adj = adjunto_info["texto_lower"]

                    # Verificar si ALGUN servidor Y ALGUNAs de las fechas coinciden en el texto del adjunto
                    servidor_match = next((srv for srv in servidores if srv.lower() in texto_adj), None)
                    fecha_match = next((fch for fch in fechas if fch.lower() in texto_adj), None)

                    if servidor_match and fecha_match:
                        registro_hit = {
                            "fila_excel": idx,
                            "ans": ans,
                            "servidor_coincidio": servidor_match,
                            "fecha_coincidio": fecha_match,
                            "correo": adjunto_info["correo"],
                            "adjunto": adjunto_info["adjunto"]
                        }
                        coincidencias.append(registro_hit)
                        encontrado = True

                        # Log en tiempo real
                        msg = f"✅ [FILA {idx}][HIT] ANS: {ans} | Servidor: {servidor_match} | Fecha: {fecha_match} | Adjunto: {adjunto_info['adjunto']}\n"
                        f_log.write(msg)
                        f_log.flush()

            if not encontrado:
                registro_miss = {
                    "fila_excel": idx,
                    "ans": ans,
                    "servidor_original": servidor_orig,
                    "agenda_original": agenda_orig
                }
                sin_coincidencias.append(registro_miss)

                # Log en tiempo real
                msg = f"❌ [FILA {idx}][MISS] ANS: {ans} | Servidor: {servidor_orig} | Agenda: {agenda_orig}\n"
                f_log.write(msg)
                f_log.flush()

        f_log.write("\n=== AUDITORÍA FINALIZADA ===\n")
        f_log.flush()

    return coincidencias, sin_coincidencias