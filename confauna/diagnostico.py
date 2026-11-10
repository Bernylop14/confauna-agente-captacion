"""Comprueba el acceso al correo (envío e IMAP) sin enviar nada."""
from __future__ import annotations

import imaplib
import smtplib

from . import config, envio, oauth


def probar(salida=print, smtp_factory=None, imap_factory=None) -> bool:
    """True si se puede iniciar sesión tanto en SMTP (enviar) como en IMAP (leer respuestas)."""
    aj = config.ajustes()
    faltan = config.falta_credenciales(aj)
    if faltan:
        salida(faltan)
        return False
    metodo = "inicio de sesión con Google (OAuth)" if aj["auth"] == "oauth" else "contraseña"
    salida(f"Cuenta: {aj['gmail_user']} · acceso por {metodo}")

    token = None
    if aj["auth"] == "oauth":
        try:
            token = oauth.token_de_acceso(aj)
            salida("  ✓ Autorización de Google válida")
        except oauth.ErrorOAuth as e:
            salida(f"  ✗ {e}")
            return False

    todo_bien = True
    servidor = None
    try:
        servidor = (smtp_factory or envio._smtp_servidor)()
        oauth.autenticar_smtp(servidor, aj["gmail_user"], aj["gmail_app_password"], token)
        salida(f"  ✓ Envío (SMTP {aj['smtp_host']}): acceso correcto")
    except smtplib.SMTPAuthenticationError as e:
        salida(f"  ✗ Envío (SMTP): el servidor rechazó el acceso ({e.smtp_code}). "
               + ("Revisa la autorización con: python agente.py autorizar"
                  if token else "Revisa EMAIL_USER y EMAIL_PASSWORD en .env"))
        todo_bien = False
    except (smtplib.SMTPException, OSError) as e:
        salida(f"  ✗ Envío (SMTP): no se pudo conectar ({e})")
        todo_bien = False
    finally:
        if servidor is not None:
            try:
                servidor.quit()
            except Exception:
                pass

    con = None
    try:
        con = (imap_factory or (lambda: imaplib.IMAP4_SSL(aj["imap_host"], 993)))()
        oauth.autenticar_imap(con, aj["gmail_user"], aj["gmail_app_password"], token)
        salida(f"  ✓ Lectura (IMAP {aj['imap_host']}): acceso correcto")
    except (imaplib.IMAP4.error, OSError) as e:
        salida(f"  ✗ Lectura (IMAP): {e}. ¿Está IMAP activado en Gmail y la cuenta bien autorizada?")
        todo_bien = False
    finally:
        if con is not None:
            try:
                con.logout()
            except Exception:
                pass
    salida("Todo listo para enviar." if todo_bien else "Hay algo que arreglar antes de enviar.")
    return todo_bien
