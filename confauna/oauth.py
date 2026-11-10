"""Inicio de sesión con Google (OAuth 2.0 + XOAUTH2) para SMTP e IMAP, sin contraseña.

Google Workspace ya no admite aplicaciones que entren solo con usuario y contraseña, así que el
agente guarda una autorización (un 'refresh token') que obtienes UNA vez con `python agente.py autorizar`
y la renueva solo en cada ejecución. El archivo data/google_token.json es una credencial: no lo compartas.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://mail.google.com/"  # el que Google exige para IMAP/SMTP con OAuth

_cache: dict[str, dict] = {}


class ErrorOAuth(Exception):
    """Problema con la autorización de Google; el mensaje dice qué hacer."""


def cadena_xoauth2(usuario: str, token: str) -> str:
    """Cadena de autenticación XOAUTH2 que entienden los servidores de Gmail."""
    return f"user={usuario}\x01auth=Bearer {token}\x01\x01"


def _post_form(url: str, datos: dict, timeout: int = 30) -> dict:
    cuerpo = urllib.parse.urlencode(datos).encode()
    peticion = urllib.request.Request(
        url, data=cuerpo, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # Google responde 400 con un JSON que explica el error
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": f"http_{e.code}"}
    except (urllib.error.URLError, OSError) as e:
        raise ErrorOAuth(f"No se pudo conectar con Google: {e}") from e


def _leer_token() -> dict | None:
    ruta = config.ruta_token()
    if not ruta.exists():
        return None
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _guardar_token(datos: dict) -> None:
    ruta = config.ruta_token()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # en Windows no tiene efecto, pero no molesta
    except OSError:
        pass
    os.replace(tmp, ruta)


def esta_autorizado() -> bool:
    datos = _leer_token()
    return bool(datos and datos.get("refresh_token"))


def _credenciales_cliente(aj: dict) -> dict:
    if not aj["google_client_id"]:
        raise ErrorOAuth("Falta GOOGLE_CLIENT_ID en el archivo .env")
    datos = {"client_id": aj["google_client_id"]}
    if aj["google_client_secret"]:
        datos["client_secret"] = aj["google_client_secret"]
    return datos


def token_de_acceso(aj: dict, post=_post_form, ahora=time.time) -> str:
    """Devuelve un token de acceso válido (dura ~1 hora), renovándolo con la autorización guardada."""
    datos = _leer_token()
    if not datos or not datos.get("refresh_token"):
        raise ErrorOAuth("Falta autorizar el acceso a Google. Ejecuta: python agente.py autorizar")
    usuario = (datos.get("usuario") or "").lower()
    if usuario and aj["gmail_user"] and usuario != aj["gmail_user"].lower():
        raise ErrorOAuth(f"La autorización guardada es de {usuario}, no de {aj['gmail_user']}. "
                         "Ejecuta de nuevo: python agente.py autorizar")
    refresco = datos["refresh_token"]
    guardado = _cache.get(refresco)
    if guardado and guardado["caduca"] - 60 > ahora():
        return guardado["token"]
    respuesta = post(TOKEN_URL, {**_credenciales_cliente(aj),
                                 "refresh_token": refresco, "grant_type": "refresh_token"})
    if "access_token" not in respuesta:
        if respuesta.get("error") == "invalid_grant":
            raise ErrorOAuth("Google ha invalidado la autorización (revocada, caducada o cambio de "
                             "contraseña). Ejecuta de nuevo: python agente.py autorizar")
        raise ErrorOAuth("Google rechazó la renovación del acceso: "
                         f"{respuesta.get('error')} {respuesta.get('error_description', '')}".strip())
    _cache[refresco] = {"token": respuesta["access_token"],
                        "caduca": ahora() + int(respuesta.get("expires_in", 3600))}
    return respuesta["access_token"]


def autorizar(aj: dict, abrir=webbrowser.open, post=_post_form, espera_max: int = 240,
              salida=print) -> dict:
    """Pide a Google permiso (una sola vez) abriendo el navegador. Guarda la autorización."""
    mensaje = config.falta_credenciales({**aj, "auth": "oauth"})
    if mensaje:
        raise ErrorOAuth(mensaje)

    verificador = secrets.token_urlsafe(64)  # 86 caracteres válidos (Google admite de 43 a 128)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    estado = secrets.token_urlsafe(16)
    recibido: dict[str, str] = {}

    class Manejador(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (nombre fijado por http.server)
            consulta = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in consulta or "error" in consulta:
                recibido.update({k: v[0] for k, v in consulta.items()})
            bien = "code" in consulta
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            texto = ("Listo. Ya puedes cerrar esta pestaña y volver a PyCharm." if bien
                     else "No se ha podido completar la autorización. Vuelve a PyCharm para ver el motivo.")
            self.wfile.write(f"<html><body style='font-family:sans-serif;padding:2em'>"
                             f"<h2>ConFauna</h2><p>{texto}</p></body></html>".encode("utf-8"))

        def log_message(self, *args):  # silencia el log por consola
            pass

    servidor = HTTPServer(("127.0.0.1", 0), Manejador)
    servidor.timeout = 1
    redireccion = f"http://127.0.0.1:{servidor.server_address[1]}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": aj["google_client_id"], "redirect_uri": redireccion, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": estado,
        "code_challenge": desafio, "code_challenge_method": "S256", "login_hint": aj["gmail_user"],
    })
    salida("Abriendo el navegador para que aceptes el permiso con tu cuenta de Google.\n"
           f"Si no se abre solo, copia este enlace en el navegador:\n\n{url}\n")
    try:
        abrir(url)
    except Exception:  # sin navegador: el usuario usa el enlace de arriba
        pass
    limite = time.time() + espera_max
    try:
        while not recibido and time.time() < limite:
            servidor.handle_request()
    finally:
        servidor.server_close()

    if not recibido:
        raise ErrorOAuth("No se recibió respuesta de Google a tiempo. Vuelve a ejecutar: python agente.py autorizar")
    if "error" in recibido:
        raise ErrorOAuth(f"Google no concedió el permiso ({recibido['error']}).")
    if recibido.get("state") != estado:
        raise ErrorOAuth("La respuesta de Google no corresponde a esta solicitud. Inténtalo de nuevo.")

    respuesta = post(TOKEN_URL, {**_credenciales_cliente(aj), "code": recibido["code"],
                                 "code_verifier": verificador, "redirect_uri": redireccion,
                                 "grant_type": "authorization_code"})
    if "refresh_token" not in respuesta:
        detalle = respuesta.get("error_description") or respuesta.get("error") or "respuesta sin refresh_token"
        raise ErrorOAuth(f"Google no entregó la autorización permanente: {detalle}")
    datos = {"refresh_token": respuesta["refresh_token"], "usuario": aj["gmail_user"],
             "scope": respuesta.get("scope", SCOPE), "creado": datetime.now().isoformat(timespec="seconds")}
    _guardar_token(datos)
    _cache.clear()
    return datos


def autenticar_smtp(servidor, usuario: str, clave: str, token: str | None = None) -> None:
    """Inicia sesión en SMTP con contraseña o, si hay token, con XOAUTH2."""
    if token is None:
        servidor.login(usuario, clave)
        return
    servidor.ehlo_or_helo_if_needed()
    cadena = cadena_xoauth2(usuario, token)
    # Si Google contesta con un error, el protocolo pide responder vacío para que cierre con 535.
    servidor.auth("XOAUTH2", lambda challenge=None: cadena if challenge is None else "")


def autenticar_imap(con, usuario: str, clave: str, token: str | None = None) -> None:
    """Inicia sesión en IMAP con contraseña o, si hay token, con XOAUTH2."""
    if token is None:
        con.login(usuario, clave)
        return
    cadena = cadena_xoauth2(usuario, token).encode()
    intentos = []

    def respuesta(_):
        intentos.append(1)
        return cadena if len(intentos) == 1 else b""

    con.authenticate("XOAUTH2", respuesta)
