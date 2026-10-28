import re

def extraer_servidores(texto: str) -> list[str]:
    """
    Limpia y separa una cadena de servidores sucia en una lista de servidores individuales.
    - Protege contenidos entre paréntesis (ej. IPs).
    - Detecta separadores mezclados (;, ,, espacios, " Y ", guiones).
    - El '-' separa solo si la palabra contiene mayúsculas. 
    """
    if not texto or not isinstance(texto, str):
        return []

    # 1. PROTEGER LOS PARÉNTESIS (Ej: IPs)
    parentesis_guardados = {}
    
    def proteger_parentesis(match):
        # Reemplazamos el paréntesis temporalmente por un tag como __TAG_0__
        tag = f"__TAG_{len(parentesis_guardados)}__"
        parentesis_guardados[tag] = match.group(0).strip()
        return f" {tag} " # Añadimos espacios alrededor para que no se pegue
        
    # Encuentra todo lo que esté entre ( y )
    texto_seguro = re.sub(r'\(.*?\)', proteger_parentesis, texto)

    # 2. HOMOLOGAR SEPARADORES EXPLÍCITOS A UN PIPE (|)
    # Reemplaza " Y " o " y " rodeados de espacios
    texto_seguro = re.sub(r'\s+[yY]\s+', '|', texto_seguro)
    # Reemplaza guiones que estén sueltos (rodeados de espacios) ej: SRV1 - SRV2
    texto_seguro = re.sub(r'\s+[-—–]\s+', '|', texto_seguro)
    # Reemplaza comas y punto y comas
    texto_seguro = re.sub(r'[;,]', '|', texto_seguro)
    # Reemplaza múltiples espacios o pipes seguidos por un solo pipe
    texto_seguro = re.sub(r'[\s|]+', '|', texto_seguro)

    # 3. DIVIDIR EN TOKENS INICIALES
    tokens_crudos = [t for t in texto_seguro.split('|') if t.strip()]
    
    resultados = []
    
    # 4. APLICAR REGLAS DE NEGOCIO A CADA PALABRA
    for token in tokens_crudos:
        token = token.strip()
        
        # Si es un paréntesis que habíamos guardado, lo restauramos intacto
        if token in parentesis_guardados:
            resultados.append(parentesis_guardados[token])
            continue
            
        # Regla del Guion (-) y sus variantes
        if '-' in token or '—' in token or '–' in token:
            token_norm = re.sub(r'[—–]', '-', token) # Normalizar a guion normal
            
            # Condición: si TIENE mayúsculas, el guion SÍ separa
            if any(c.isupper() for c in token_norm):
                sub_servidores = token_norm.split('-')
                for sub in sub_servidores:
                    if sub.strip():
                        resultados.append(sub.strip())
            else:
                # Si todo es minúscula/números, el guion NO separa (ej: servidor-linux-01)
                resultados.append(token_norm)
        else:
            # Palabra normal
            resultados.append(token)
            
    # 5. LIMPIEZA FINAL DE CARACTERES EXTRAÑOS
    finales = []
    for res in resultados:
        if res.startswith('(') and res.endswith(')'):
            finales.append(res)
        else:
            # Si no es un paréntesis, dejamos SOLO alfanuméricos, guiones bajos (_) y puntos (.)
            res_limpio = re.sub(r'[^a-zA-Z0-9_.-]', '', res)
            if res_limpio:
                finales.append(res_limpio)

    # Devolvemos la lista eliminando duplicados si los hubiere
    return list(dict.fromkeys(finales))