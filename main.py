import polars as pl

import src.config as config
import src.visuales as visuales
import src.manejo_fechas as manejo_fechas
import src.manejo_servidores as manejo_servidores
import src.auditoria_copilot as auditoria_copilot

def main():
    print("🚀 Cargando archivo de control...")
    df_control = pl.read_excel(
        config.DATA / "CONTROL_ANS_SOX.xlsx", 
        sheet_name="POLITICAS_CON _SLA",
        engine="calamine"
    ).with_columns(pl.all().cast(pl.String))

    # 1. UI: Capturar rango de fechas y número de repeticiones (N) por agenda
    valores_repeticion = df_control.select("AGENDA").drop_nulls().unique().to_series().to_list()
    config_usuario = visuales.solicitar_parametros_ejecucion(valores_repeticion)

    if not config_usuario:
        print("❌ Operación cancelada por el usuario.")
        return
        
    rango_fechas = config_usuario["rango_fechas"]
    repeticiones = config_usuario["repeticiones_seleccionadas"]

    # 2. Vectorización: Filtrar agendas evaluadas y generar la data objetivo
    agendas_evaluadas = list(repeticiones.keys())
    df_filtrado = df_control.filter(pl.col("AGENDA").is_in(agendas_evaluadas))
    
    print("⚙️ Mapeando fechas aleatorias y normalizando servidores...")
    
    # Transformación O(N) para inyectar la lógica de fechas aleatorias
    df_filtrado = df_filtrado.with_columns(
        pl.col("AGENDA")
        .map_elements(
            lambda agenda: manejo_fechas.generar_fechas_aleatorias_rango(
                agenda=agenda,
                fecha_inicio=rango_fechas["inicio"],
                fecha_fin=rango_fechas["fin"],
                cantidad=repeticiones.get(agenda, 0)
            ),
            return_dtype=pl.List(pl.String)
        )
        .alias("FECHAS_A_BUSCAR")
    )

    df_filtrado = df_filtrado.with_columns(
        pl.col("SERVIDOR")
        .fill_null("") 
        .map_elements(manejo_servidores.extraer_servidores, return_dtype=pl.List(pl.String))
        .alias("SERVIDORES_LISTA")
    )

    # 3. Traspaso al RPA: Navegación OneDrive e interacción con Copilot
    print("🌐 Iniciando orquestación con OneDrive y Copilot...")
    auditoria_copilot.ejecutar_validacion_copilot(df_filtrado)

if __name__ == "__main__":
    main()