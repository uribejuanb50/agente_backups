import io
import email
import zipfile
import tempfile
import os
import re
from pathlib import Path
import polars as pl
import extract_msg

# Dependencias opcionales
try:
    import py7zr
except ImportError:
    py7zr = None


def procesar_adjuntos_correo(fuente, tipo_fuente: str = "msg") -> tuple[list[dict], list[str]]:
    """
    Extrae y procesa de forma recursiva todos los adjuntos de un correo (.msg o .eml).
    """
    resultados = []
    no_procesados = []
    tipo_fuente = tipo_fuente.lower().replace(".", "")

    if tipo_fuente == "msg":
        if isinstance(fuente, extract_msg.Message):
            msg = fuente
            cerrar = False
        else:
            if isinstance(fuente, (str, Path)):
                msg = extract_msg.Message(str(fuente))
            else:
                msg = extract_msg.Message(fuente)
            cerrar = True

        try:
            for att in msg.attachments:
                nombre = att.longFilename or att.shortFilename
                
                if not nombre:
                    if isinstance(att.data, extract_msg.Message):
                        nombre = "correo_anidado.msg"
                    else:
                        nombre = "adjunto_sin_nombre.bin"
                
                contenido = att.data
                if contenido is not None:
                    res, no_proc = procesar_contenido_bytes(nombre, contenido)
                    resultados.extend(res)
                    no_procesados.extend(no_proc)
        finally:
            if cerrar:
                msg.close()

    elif tipo_fuente == "eml":
        if isinstance(fuente, (str, Path)):
            with open(fuente, "rb") as f:
                parsed_eml = email.message_from_binary_file(f)
        else:
            parsed_eml = email.message_from_bytes(fuente)

        if parsed_eml.is_multipart():
            for part in parsed_eml.walk():
                filename = part.get_filename()
                if filename:
                    payload = part.get_payload(decode=True)
                    if payload:
                        res, no_proc = procesar_contenido_bytes(filename, payload)
                        resultados.extend(res)
                        no_procesados.extend(no_proc)

    return resultados, no_procesados


def procesar_contenido_bytes(nombre_archivo: str, contenido_bytes) -> tuple[list[dict], list[str]]:
    resultados = []
    no_procesados = []
    ext = Path(nombre_archivo).suffix.lower()

    # --- 1. INTERCEPTAR CORREOS MSG ANIDADOS ---
    if isinstance(contenido_bytes, extract_msg.Message):
        res_anidados, no_proc_anidados = procesar_adjuntos_correo(
            fuente=contenido_bytes,
            tipo_fuente="msg"
        )
        resultados.extend(res_anidados)
        no_procesados.extend(no_proc_anidados)
        return resultados, no_procesados

    # --- 2. DETECCIÓN DINÁMICA DE FORMATOS OCULTOS ---
    if ext in [".bin", ".dat", "", ".pendingscan", ".tmp"] or "adjunto_sin_nombre" in nombre_archivo:
        if zipfile.is_zipfile(io.BytesIO(contenido_bytes)):
            ext = ".zip"
            nombre_archivo += ".zip"
        elif py7zr and py7zr.is_7zfile(io.BytesIO(contenido_bytes)):
            ext = ".7z"
            nombre_archivo += ".7z"
        elif contenido_bytes.startswith(b'%PDF'):
            ext = ".pdf"
            nombre_archivo += ".pdf"

    try:
        match ext:
            case ".zip":
                with zipfile.ZipFile(io.BytesIO(contenido_bytes)) as z:
                    for zip_info in z.infolist():
                        if not zip_info.is_dir():
                            sub_bytes = z.read(zip_info.filename)
                            res, no_proc = procesar_contenido_bytes(zip_info.filename, sub_bytes)
                            resultados.extend(res)
                            no_procesados.extend(no_proc)

            case ".7z":
                if py7zr is None:
                    no_procesados.append(f"{nombre_archivo} (falta py7zr)")
                else:
                    with tempfile.TemporaryDirectory() as tmpdirname:
                        with py7zr.SevenZipFile(io.BytesIO(contenido_bytes), mode='r') as archive:
                            archive.extractall(path=tmpdirname)
                        
                        for root, dirs, files in os.walk(tmpdirname):
                            for f in files:
                                file_path = os.path.join(root, f)
                                with open(file_path, 'rb') as f_in:
                                    sub_bytes = f_in.read()
                                
                                rel_name = os.path.relpath(file_path, tmpdirname)
                                res, no_proc = procesar_contenido_bytes(rel_name, sub_bytes)
                                resultados.extend(res)
                                no_procesados.extend(no_proc)

            case ".msg" | ".eml":
                res_anidados, no_proc_anidados = procesar_adjuntos_correo(
                    fuente=contenido_bytes,
                    tipo_fuente=ext.replace(".", "")
                )
                resultados.extend(res_anidados)
                no_procesados.extend(no_proc_anidados)

            # --- RESCATE DE XLS FALSOS DE COMMVAULT ---
            case ".xlsx" | ".xls" | ".xlsm" | ".xlsb":
                try:
                    df = pl.read_excel(io.BytesIO(contenido_bytes), engine="calamine")
                    resultados.append({"archivo": nombre_archivo, "tipo": "dataframe", "contenido": df})
                except Exception:
                    try:
                        import pandas as pd
                        tablas = pd.read_html(io.BytesIO(contenido_bytes))
                        if tablas:
                            df = pl.from_pandas(tablas[0])
                            resultados.append({"archivo": nombre_archivo + " (HTML)", "tipo": "dataframe", "contenido": df})
                    except Exception:
                        try:
                            df = pl.read_csv(io.BytesIO(contenido_bytes), separator="\t", infer_schema_length=0)
                            resultados.append({"archivo": nombre_archivo + " (TSV estricto)", "tipo": "dataframe", "contenido": df})
                        except Exception:
                            # --- PLAN COMMVAULT CUSTOM (Recorte Dinámico por Geometría) ---
                            try:
                                import pandas as pd
                                try:
                                    texto = contenido_bytes.decode('utf-8', errors='ignore')
                                except UnicodeDecodeError:
                                    texto = contenido_bytes.decode('latin1', errors='ignore')
                                
                                # 1. Limpiar etiquetas HTML basuras como <BR>
                                texto = re.sub(r'<[^>]+>', '', texto)
                                lineas = texto.splitlines()
                                
                                # 2. Determinar si usa Tabs o Comas como separador principal
                                conteo_tabs = sum(1 for l in lineas if '\t' in l)
                                conteo_comas = sum(1 for l in lineas if ',' in l)
                                sep = '\t' if conteo_tabs > conteo_comas else ','
                                
                                # 3. Encontrar la línea más ancha para saber cuántas columnas reales tiene la tabla
                                max_separadores = max([l.count(sep) for l in lineas] + [0])
                                
                                if max_separadores < 2:
                                    raise ValueError("No se encontraron separadores para armar una tabla.")
                                
                                # 4. Buscar la fila donde arranca la tabla (primera línea con > 70% de las columnas máximas)
                                inicio_idx = 0
                                for i, linea in enumerate(lineas):
                                    if linea.count(sep) >= int(max_separadores * 0.7):
                                        inicio_idx = i
                                        break
                                
                                # 5. Buscar la fila donde termina (cuando se acaban las columnas y empiezan las advertencias/notas)
                                fin_idx = len(lineas)
                                for i in range(inicio_idx + 1, len(lineas)):
                                    linea_actual = lineas[i].lower()
                                    # Si caen drásticamente las columnas y vemos palabras clave de cierre
                                    if lineas[i].count(sep) < int(max_separadores * 0.5):
                                        if "notes" in linea_actual or "warning:" in linea_actual:
                                            fin_idx = i
                                            break
                                
                                # Extraer la tabla exacta
                                lineas_tabla = [l for l in lineas[inicio_idx:fin_idx] if l.strip() != ""]
                                texto_limpio = "\n".join(lineas_tabla)
                                
                                # Procesar con pandas (es muy tolerante a errores) y pasar a Polars
                                df_pd = pd.read_csv(io.StringIO(texto_limpio), sep=sep, on_bad_lines="skip", engine="python")
                                df_pd = df_pd.astype(str)
                                df = pl.from_pandas(df_pd)
                                
                                resultados.append({
                                    "archivo": nombre_archivo + " (Rescate Dinámico Commvault)",
                                    "tipo": "dataframe",
                                    "contenido": df
                                })
                            except Exception as e:
                                no_procesados.append(f"{nombre_archivo} (Falso XLS, fallo total: {e})")

            case ".csv":
                try:
                    # Plan A: Lectura estándar asumiendo separador ";" (CSV tradicionales en español)
                    df = pl.read_csv(io.BytesIO(contenido_bytes), separator=";", infer_schema_length=0)
                    
                    # Si quedó todo en 1 columna, significa que el separador no era ";" o tiene basura de Commvault
                    if df.width == 1:
                        raise ValueError("Posible CSV de Commvault con metadata o separador de coma.")
                        
                except Exception:
                    try:
                        # Plan B: Lectura estándar asumiendo separador "," (CSV en inglés)
                        df = pl.read_csv(io.BytesIO(contenido_bytes), separator=",", infer_schema_length=0)
                        
                        if df.width == 1:
                            raise ValueError("Sigue en 1 columna con coma, necesita recorte geométrico.")
                    except Exception:
                        # Plan C: Ramificación de Rescate Dinámico Commvault para CSV
                        import pandas as pd
                        try:
                            texto = contenido_bytes.decode('utf-8', errors='ignore')
                        except UnicodeDecodeError:
                            texto = contenido_bytes.decode('latin1', errors='ignore')
                        
                        # Limpiar etiquetas HTML basuras si las hay
                        texto = re.sub(r'<[^>]+>', '', texto)
                        lineas = texto.splitlines()
                        
                        # Detectar el separador real
                        conteo_tabs = sum(1 for l in lineas if '\t' in l)
                        conteo_comas = sum(1 for l in lineas if ',' in l)
                        sep = '\t' if conteo_tabs > conteo_comas else ','
                        
                        max_separadores = max([l.count(sep) for l in lineas] + [0])
                        
                        if max_separadores >= 2:
                            # Recorte geométrico: buscar dónde se ensancha la tabla
                            inicio_idx = 0
                            for i, linea in enumerate(lineas):
                                if linea.count(sep) >= int(max_separadores * 0.7):
                                    inicio_idx = i
                                    break
                            
                            fin_idx = len(lineas)
                            for i in range(inicio_idx + 1, len(lineas)):
                                linea_actual = lineas[i].lower()
                                if lineas[i].count(sep) < int(max_separadores * 0.5):
                                    if "notes" in linea_actual or "warning:" in linea_actual or "summary" in linea_actual:
                                        fin_idx = i
                                        break
                            
                            lineas_tabla = [l for l in lineas[inicio_idx:fin_idx] if l.strip() != ""]
                            texto_limpio = "\n".join(lineas_tabla)
                            
                            df_pd = pd.read_csv(io.StringIO(texto_limpio), sep=sep, on_bad_lines="skip", engine="python")
                            df_pd = df_pd.astype(str)
                            df = pl.from_pandas(df_pd)
                            nombre_archivo += " (Rescate CSV)"
                        else:
                            # Si de verdad no tiene separadores, lo dejamos como viene
                            df = pl.read_csv(io.BytesIO(contenido_bytes), separator=",", infer_schema_length=0)

                # Limpieza de caracteres BOM de seguridad
                columnas_limpias = [col.replace("ï»¿", "").replace("\ufeff", "") for col in df.columns]
                df = df.rename(dict(zip(df.columns, columnas_limpias)))
                
                resultados.append({
                    "archivo": nombre_archivo,
                    "tipo": "dataframe",
                    "contenido": df
                })

            case ".txt":
                df = pl.read_csv(io.BytesIO(contenido_bytes), separator="\t")
                resultados.append({
                    "archivo": nombre_archivo,
                    "tipo": "dataframe",
                    "contenido": df
                })

            case ".pdf":
                try:
                    import pypdf
                    reader = pypdf.PdfReader(io.BytesIO(contenido_bytes))
                    texto = "\n".join([page.extract_text() or "" for page in reader.pages])
                    resultados.append({
                        "archivo": nombre_archivo,
                        "tipo": "texto_pdf",
                        "contenido": texto
                    })
                except ImportError:
                    no_procesados.append(f"{nombre_archivo} (falta pypdf)")

            case ".docx":
                try:
                    import docx
                    doc = docx.Document(io.BytesIO(contenido_bytes))
                    texto = "\n".join([p.text for p in doc.paragraphs if p.text])
                    resultados.append({
                        "archivo": nombre_archivo,
                        "tipo": "texto_word",
                        "contenido": texto
                    })
                except ImportError:
                    no_procesados.append(f"{nombre_archivo} (falta python-docx)")

            case _:
                no_procesados.append(nombre_archivo)

    except Exception as e:
        no_procesados.append(f"{nombre_archivo} (Error: {str(e)})")

    return resultados, no_procesados