"""Lee el buzón de Gmail (IMAP) para detectar respuestas, bajas y rebotes de los prospectos."""
from __future__ import annotations

import email
import imaplib
import re
from datetime import date, timedelta
from email.message import Message
from email.utils import parseaddr

from . import oauth

_ASUNTOS_REBOTE = ("delivery status", "undelivered", "mail delivery", "returned mail",
                   "failure notice", "no se ha podido entregar", "notificación de estado de entrega")
_FRASES_BAJA = re.compile(
    r"\bbaja\b|darme de baja|no me (escriba|envíe|envie|contacte)|no deseo recibir|unsubscribe|dejen de escribir",
    re.IGNORECASE)
_CORTE_CITA = re.compile(r"^(el .{5,120} escribió:|on .{5,120} wrote:|-{2,}\s*(mensaje|original)|de:|from:)", re.IGNORECASE)
# Inicio típico de una firma o de un pie legal: a partir de ahí no se busca la palabra "baja"
# (muchos despachos explican en su pie cómo darse de baja de SUS envíos).
_CORTE_PIE = re.compile(
    r"^(--\s*$|_{3,}|este (documento|mensaje|correo|e-?mail)|aviso legal|confidencial|"
    r"(ley org[aá]nica|lopd|rgpd|protecci[oó]n de datos)|le informamos|en cumplimiento|"
    r"enviado desde mi)", re.IGNORECASE)
_LINEAS_REVISADAS = 6  # quien pide la baja lo dice al principio, no al final de la firma


def _texto(msg: Message) -> str:
    partes = []
    for parte in msg.walk():
        if parte.get_content_type() == "text/plain":
            carga = parte.get_payload(decode=True)
            if carga:
                partes.append(carga.decode(parte.get_content_charset() or "utf-8", errors="replace"))
    return "\n".join(partes)


def _solo_respuesta(texto: str) -> str:
    """Devuelve solo lo que escribió la persona: sin nuestro correo citado (que contiene la
    palabra BAJA), sin su firma ni su pie legal, y como mucho las primeras líneas con texto."""
    nuevas = []
    for linea in texto.splitlines():
        limpia = linea.strip()
        if linea.lstrip().startswith(">") or _CORTE_CITA.match(limpia) or _CORTE_PIE.match(limpia):
            break
        if limpia:
            nuevas.append(linea)
        if len(nuevas) >= _LINEAS_REVISADAS:
            break
    return "\n".join(nuevas)


def clasificar(msg: Message, emails_prospectos: set[str]) -> tuple[str, str] | None:
    """Devuelve (email_del_prospecto, 'respondido' | 'baja' | 'rebotado') o None."""
    remitente = parseaddr(msg.get("From", ""))[1].lower()
    asunto = (msg.get("Subject") or "").lower()
    texto = _texto(msg)

    if ("mailer-daemon" in remitente or "postmaster" in remitente
            or any(a in asunto for a in _ASUNTOS_REBOTE)):
        for candidato in emails_prospectos:
            if candidato in texto.lower():
                return candidato, "rebotado"
        return None

    if remitente in emails_prospectos:
        if _FRASES_BAJA.search(_solo_respuesta(texto)) or _FRASES_BAJA.search(asunto):
            return remitente, "baja"
        return remitente, "respondido"
    return None


def revisar_buzon(usuario: str, clave: str, emails_prospectos: set[str],
                  desde: date | None = None, servidor: str = "imap.gmail.com",
                  token: str | None = None) -> list[tuple[str, str]]:
    """Conecta al buzón por IMAP y clasifica los correos recibidos desde la fecha indicada."""
    desde = desde or (date.today() - timedelta(days=14))
    fecha_imap = desde.strftime("%d-%b-%Y")
    hallazgos: dict[str, str] = {}
    prioridad = {"baja": 3, "rebotado": 2, "respondido": 1}
    con = imaplib.IMAP4_SSL(servidor, 993)
    try:
        oauth.autenticar_imap(con, usuario, clave, token)
        con.select("INBOX", readonly=True)
        _, datos = con.search(None, "SINCE", fecha_imap)
        for num in datos[0].split():
            _, partes = con.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(partes[0][1])
            res = clasificar(msg, emails_prospectos)
            if res and prioridad[res[1]] > prioridad.get(hallazgos.get(res[0], ""), 0):
                hallazgos[res[0]] = res[1]
    finally:
        try:
            con.logout()
        except Exception:
            pass
    return list(hallazgos.items())


def sincronizar(salida=print, revisar=revisar_buzon) -> list[tuple[str, str]]:
    """Lee el buzón y actualiza prospectos y bajas. Lanza ValueError si faltan credenciales.

    Cualquier otro error (IMAP desactivado, red, contraseña) se propaga para que quien
    llama decida qué hacer; el autopiloto, por ejemplo, deja de mandar recordatorios.
    """
    from . import bajas, config, prospectos  # import local: evita ciclos al cargar el módulo

    aj = config.ajustes()
    faltan = config.falta_credenciales(aj)
    if faltan:
        raise ValueError(faltan)
    filas = prospectos.cargar()
    por_email = {f["email"].lower(): f for f in filas}
    activos = {e for e, f in por_email.items()
               if f["estado"] in ("enviado", "recordatorio_enviado", "respondido")}
    extra = {"token": oauth.token_de_acceso(aj)} if aj["auth"] == "oauth" else {}
    hallazgos = revisar(aj["gmail_user"], aj["gmail_app_password"], activos, servidor=aj["imap_host"], **extra)
    for email_p, tipo in hallazgos:
        por_email[email_p]["estado"] = tipo
        if tipo == "baja":
            bajas.anadir(email_p)
        salida(f"  {email_p}: {tipo}")
    prospectos.guardar(filas)
    return hallazgos
