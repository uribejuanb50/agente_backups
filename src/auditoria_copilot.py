import time
import polars as pl
from pathlib import Path
from selenium.webdriver.common.keys import Keys

from src import onedrive_navigator

def ejecutar_validacion_copilot(df_control: pl.DataFrame) -> None:
    """
    Abre la sesión de SharePoint/Copilot y realiza el recorrido fila a fila
    del DataFrame inyectando las validaciones.
    """
    onedrive_url = (
        "https://claromovilco-my.sharepoint.com/my?id="
        "%2Fpersonal%2F38517232%5Fclaro%5Fcom%5Fco%2FDocuments%2FAgentesTI"
        "%2FAgentesTI%2FOPN11%2D%20Ejecucion%20de%20respaldos%20No%20SAP"
        "&viewid=9b341cd8%2D1416%2D49a1%2D84e7%2D22d26eba7dd1"
        "&share=cgpIf%5Fpy69RDQLlyw6eVnvrqEgUCers3r7vaTxJ9Kw4WmTfarg"
    )
    expected_folder = "OPN11- Ejecucion de respaldos No SAP"
    
    # Asegurar uso de la persistencia de sesión corporativa
    browser_user_data_dir = Path("C:/SeleniumProfiles/AgenteOPN11_Edge")
    options = onedrive_navigator.build_edge_options(
        user_data_dir=browser_user_data_dir,
        start_maximized=True,
        detach_browser=True
    )
    
    driver = None
    try:
        driver = onedrive_navigator.create_edge_driver(options)
        
        onedrive_navigator.open_onedrive_folder(
            driver=driver,
            folder_url=onedrive_url,
            expected_folder_name=expected_folder,
            allow_manual_login=True,
            timeout_seconds=40
        )
        
        # Superar el bloqueo del modal inicial si existe
        onedrive_navigator.close_popup_if_present(
            driver, 
            close_button_xpath='//div[@role="dialog"][.//*[contains(normalize-space(.), "Copilot")]]//button[@aria-label="Cerrar"]'
        )
        
        # Invocar la interfaz lateral de Copilot
        onedrive_navigator.click_element(
            driver=driver,
            by="css selector",
            selector='button[data-automation-id="copilot-fab-trigger"]',
            expected_after_click=("xpath", '//div[@role="dialog"][.//*[contains(normalize-space(.), "Copilot")]]')
        )
        
        # Bucle de validación por requerimiento (Fila de Excel)
        for fila in df_control.iter_rows(named=True):
            servidores = fila.get("SERVIDORES_LISTA", [])
            fechas = fila.get("FECHAS_A_BUSCAR", [])
            
            if not servidores or not fechas:
                continue
                
            # Formateo de las variables extraídas en lenguaje natural
            servidores_str = ", ".join(servidores)
            fechas_str = ", ".join(fechas)
            
            # Prompt de ingeniería instruccional hacia el LLM integrado
            mensaje = (
                f"Encuentra estos servidores {servidores_str} en estas fechas {fechas_str}. "
                "Si no las encuentras devuélveme en qué fechas se encontró, y si no se encontró, "
                "devuélveme un 'no se encontró'."
            )
            
            # Inyección en DOM y envío mediante simulación de teclado
            onedrive_navigator.write_copilot_message(driver, mensaje)
            
            active_element = driver.switch_to.active_element
            active_element.send_keys(Keys.ENTER)
            
            # Delay táctico para permitir a la UI procesar y al LLM responder
            # *Nota: Si las respuestas demoran, podrías cambiar esto por un WebDriverWait
            # que verifique la aparición del div de respuesta de Copilot.
            time.sleep(8) 
            
        input("\n✅ Validaciones enviadas al flujo de Copilot. Pulsa Enter para cerrar Edge...")
        
    except Exception as e:
        print(f"❌ Excepción levantada durante la ejecución del RPA: {e}")
        input("Pulsa Enter para salir...")
    finally:
        if driver:
            onedrive_navigator.close_driver(driver)