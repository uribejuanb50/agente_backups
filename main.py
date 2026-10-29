r"""
Auditoría de backups con Copilot.

    venv\Scripts\activate
    pip install -r requirements.txt
    python main.py

Sin consola: doble clic en "Auditoria Backups.bat" (usa pythonw).
"""
import sys
from pathlib import Path

if sys.stdout is None or sys.stderr is None:          # pythonw: sin consola -> registrar en archivo
    carpeta = Path(__file__).resolve().parent / "logs"
    carpeta.mkdir(exist_ok=True)
    salida = open(carpeta / "consola.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or salida
    sys.stderr = sys.stderr or salida

from src.interfaz import ejecutar

if __name__ == "__main__":
    ejecutar()
