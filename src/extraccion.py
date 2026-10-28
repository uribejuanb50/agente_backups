import email
import extract_msg

from pathlib import Path

def clasificar_correos_adjuntos(ruta_carpeta: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """
    Explora una carpeta de forma recursiva buscando archivos .msg y .eml.
    Valida su integridad y verifica si contienen archivos adjuntos.
    
    Retorna 4 listas:
        1. msg_con_adjuntos: Archivos .msg válidos con 1 o más adjuntos.
        2. eml_con_adjuntos: Archivos .eml válidos con 1 o más adjuntos.
        3. sin_adjuntos: Archivos .msg o .eml válidos pero sin adjuntos.
        4. descartados: Archivos corruptos, vacíos o de otras extensiones.
    """
    msg_con_adjuntos = []
    eml_con_adjuntos = []
    sin_adjuntos = []
    descartados = []
    
    ruta_raiz = Path(ruta_carpeta)
    
    for archivo in ruta_raiz.rglob('*'):
        if archivo.is_file():
            extension = archivo.suffix.lower()
            ruta_str = str(archivo)
            
            if extension == '.msg':
                try:
                    # Intenta abrir el archivo .msg
                    msg = extract_msg.Message(ruta_str)
                    
                    # Verificamos si la lista de adjuntos es mayor a 0
                    if len(msg.attachments) > 0:
                        msg_con_adjuntos.append(ruta_str)
                    else:
                        sin_adjuntos.append(ruta_str)
                        
                    msg.close()
                except Exception:
                    descartados.append(ruta_str)
                    
            elif extension == '.eml':
                try:
                    # Intenta abrir y parsear el EML
                    with open(archivo, 'rb') as f:
                        parsed_eml = email.message_from_binary_file(f)
                        
                        # Si no tiene llaves, está corrupto o no es un correo real
                        if not parsed_eml.keys():
                            descartados.append(ruta_str)
                            continue
                        
                        tiene_adjunto = False
                        
                        # Los correos con adjuntos siempre son "multipart" (multiparte)
                        if parsed_eml.is_multipart():
                            # Iteramos por cada parte del correo (texto, html, adjuntos)
                            for part in parsed_eml.walk():
                                # Si la parte tiene un nombre de archivo asignado, es un adjunto
                                if part.get_filename():
                                    tiene_adjunto = True
                                    break # Con encontrar uno es suficiente
                        
                        if tiene_adjunto:
                            eml_con_adjuntos.append(ruta_str)
                        else:
                            sin_adjuntos.append(ruta_str)
                            
                except Exception:
                    descartados.append(ruta_str)
                    
            else:
                # Cualquier archivo que no sea .msg o .eml cae aquí
                descartados.append(ruta_str)
                
    return msg_con_adjuntos, eml_con_adjuntos, sin_adjuntos, descartados
# Ejemplo de uso:
# msg_validos, eml_validos, descartados = clasificar_correos(r"C:\Ruta\A\Tu\Carpeta")