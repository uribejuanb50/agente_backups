import random
from datetime import datetime, timedelta

def generar_fechas_aleatorias_rango(agenda: str, fecha_inicio: str, fecha_fin: str, cantidad: int) -> list[str]:
    """
    Evalúa el rango calendario [fecha_inicio, fecha_fin], aísla los días 
    que coinciden con la 'agenda' (ej. domingos) y retorna una muestra aleatoria.
    """
    if not agenda or cantidad <= 0:
        return []
        
    inicio = datetime.strptime(fecha_inicio, "%Y-%m-%d")
    fin = datetime.strptime(fecha_fin, "%Y-%m-%d")
    agenda_norm = agenda.lower().strip()
    
    fechas_validas = []
    dia_actual = inicio
    
    # 1. Construir la población estadística de días válidos
    while dia_actual <= fin:
        es_valido = False
        
        if "diario" in agenda_norm or "diariamente" in agenda_norm:
            es_valido = True
        elif "sabado" in agenda_norm or "sábado" in agenda_norm:
            es_valido = (dia_actual.weekday() == 5)
        elif "domingo" in agenda_norm:
            es_valido = (dia_actual.weekday() == 6)
        elif "viernes" in agenda_norm:
            es_valido = (dia_actual.weekday() == 4)
        else:
            es_valido = True # Fallback a diario si la agenda no es reconocida
            
        if es_valido:
            fechas_validas.append(dia_actual)
            
        dia_actual += timedelta(days=1)
        
    if not fechas_validas:
        return []
        
    # 2. Muestreo aleatorio sin reemplazo
    muestra = random.sample(fechas_validas, min(cantidad, len(fechas_validas)))
    muestra.sort()  # Mantener orden cronológico para el Prompt de Copilot
    
    return [f.strftime("%d/%m/%Y") for f in muestra]