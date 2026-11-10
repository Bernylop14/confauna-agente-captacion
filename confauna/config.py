"""Rutas y ajustes del agente. Lee el archivo .env sin dependencias externas."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOTES_DIR = BASE_DIR / "lotes"
TEMPLATES_DIR = BASE_DIR / "templates"
DOSSIER_PATH = BASE_DIR / "dossier" / "ConFauna-Dossier-Aves.pdf"
PROSPECTOS_CSV = DATA_DIR / "prospectos.csv"
BAJAS_TXT = DATA_DIR / "bajas.txt"
REGISTRO_CSV = DATA_DIR / "registro_envios.csv"


def cargar_env(ruta: Path | None = None) -> None:
    """Carga variables de un archivo .env (CLAVE=valor) sin pisar las ya definidas."""
    ruta = Path(ruta) if ruta else BASE_DIR / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


cargar_env()


def _env(*nombres: str, defecto: str = "") -> str:
    """Primer valor no vacío entre varias variables de entorno."""
    for nombre in nombres:
        valor = os.environ.get(nombre)
        if valor:
            return valor
    return defecto


def ajustes() -> dict:
    """Ajustes actuales (se leen en cada llamada para poder cambiarlos en pruebas).

    Las claves 'gmail_*' se llaman así por historia: valen para cualquier proveedor
    (EMAIL_USER / EMAIL_PASSWORD, o GMAIL_USER / GMAIL_APP_PASSWORD).
    """
    return {
        "gmail_user": _env("EMAIL_USER", "GMAIL_USER"),
        "gmail_app_password": _env("EMAIL_PASSWORD", "GMAIL_APP_PASSWORD").replace(" ", ""),
        # 'password' (contraseña) u 'oauth' (inicio de sesión con Google, necesario en Google Workspace)
        "auth": _env("EMAIL_AUTH", defecto="password").lower(),
        "google_client_id": _env("GOOGLE_CLIENT_ID"),
        "google_client_secret": _env("GOOGLE_CLIENT_SECRET"),
        "smtp_host": _env("SMTP_HOST", defecto="smtp.gmail.com"),
        "smtp_port": int(_env("SMTP_PORT", defecto="465")),
        "imap_host": _env("IMAP_HOST", defecto="imap.gmail.com"),
        "remitente_nombre": _env("REMITENTE_NOMBRE", defecto="Nombre Apellido"),
        "telefono": _env("TELEFONO", defecto="600 000 000"),
        "empresa": _env("EMPRESA", defecto="ConFauna"),
        "limite_diario": int(_env("LIMITE_DIARIO", defecto="20")),
        "pausa_min": int(_env("PAUSA_MIN_SEG", defecto="45")),
        "pausa_max": int(_env("PAUSA_MAX_SEG", defecto="90")),
        "dias_recordatorio": int(_env("DIAS_RECORDATORIO", defecto="7")),
        # Calentamiento: empieza con 5 correos al día y sube 2 cada día hasta el límite.
        "calentamiento": _env("CALENTAMIENTO", defecto="SI").upper() in ("SI", "SÍ", "TRUE", "1"),
        # El autopiloto solo envía de lunes a viernes dentro de este horario.
        "hora_inicio": int(_env("HORA_INICIO", defecto="9")),
        "hora_fin": int(_env("HORA_FIN", defecto="19")),
    }


def ruta_token() -> Path:
    """Dónde se guarda la autorización de Google (OAuth). Es una credencial: no se comparte."""
    return DATA_DIR / "google_token.json"


def falta_credenciales(aj: dict) -> str | None:
    """Mensaje de qué falta en el .env para poder iniciar sesión, o None si está todo."""
    if not aj["gmail_user"]:
        return "Falta EMAIL_USER en el archivo .env"
    if aj["auth"] == "oauth":
        if not aj["google_client_id"]:
            return "Falta GOOGLE_CLIENT_ID en el archivo .env (mira la guía GUIA_GOOGLE_OAUTH.md)"
        return None
    if aj["auth"] != "password":
        return "EMAIL_AUTH debe ser 'password' u 'oauth'"
    if not aj["gmail_app_password"]:
        return "Falta EMAIL_PASSWORD en el archivo .env"
    return None
