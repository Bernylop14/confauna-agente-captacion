"""Envío por Gmail (SMTP) con límite diario, pausas y registro. Por defecto, solo simula."""
from __future__ import annotations

import csv
import random
import smtplib
import time
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from . import bajas, config, lotes, oauth, prospectos

COLUMNAS_REGISTRO = ["fecha", "hora", "lote", "id", "tipo", "email", "segmento", "resultado", "detalle"]


class ErrorEnvio(Exception):
    """Error que obliga a parar todo el lote (credenciales, límite del proveedor, red...).

    `resumen` trae lo que sí se llegó a enviar antes del error (None si falló antes de empezar).
    """

    def __init__(self, mensaje: str, resumen: dict | None = None):
        super().__init__(mensaje)
        self.resumen = resumen


def _leer_registro() -> list[dict]:
    if not config.REGISTRO_CSV.exists():
        return []
    with config.REGISTRO_CSV.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _anotar(fila: dict) -> None:
    nuevo = not config.REGISTRO_CSV.exists()
    config.REGISTRO_CSV.parent.mkdir(parents=True, exist_ok=True)
    with config.REGISTRO_CSV.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_REGISTRO)
        if nuevo:
            w.writeheader()
        w.writerow(fila)


def enviados_hoy(hoy: date) -> int:
    return sum(1 for r in _leer_registro() if r["fecha"] == hoy.isoformat() and r["resultado"] == "enviado")


def limite_efectivo(aj: dict, hoy: date) -> int:
    """Límite de hoy. Con calentamiento: 5 el primer día y +2 cada día hasta LIMITE_DIARIO.

    Una cuenta o dominio nuevo que de golpe manda decenas de correos en frío acaba en spam.
    """
    limite = aj["limite_diario"]
    if not aj["calentamiento"]:
        return limite
    fechas = [r["fecha"] for r in _leer_registro() if r["resultado"] == "enviado"]
    if not fechas:
        return min(limite, 5)
    dias = max(0, (hoy - date.fromisoformat(min(fechas))).days)
    return min(limite, 5 + 2 * dias)


def cupo_disponible(hoy: date, aj: dict | None = None) -> int:
    aj = aj or config.ajustes()
    return max(0, limite_efectivo(aj, hoy) - enviados_hoy(hoy))


def construir_mensaje(aj: dict, fila: dict, adjuntar_dossier: bool) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((aj["remitente_nombre"], aj["gmail_user"]))
    msg["To"] = fila["para"]
    msg["Subject"] = fila["asunto"]
    msg["Reply-To"] = aj["gmail_user"]
    msg["List-Unsubscribe"] = f"<mailto:{aj['gmail_user']}?subject=BAJA>"
    msg["Message-ID"] = make_msgid(domain=aj["gmail_user"].split("@")[-1] or "localhost")
    msg.set_content(fila["cuerpo"])
    if adjuntar_dossier:
        msg.add_attachment(
            config.DOSSIER_PATH.read_bytes(), maintype="application",
            subtype="pdf", filename=config.DOSSIER_PATH.name,
        )
    return msg


def _smtp_servidor():
    """Conexión al servidor del .env: puerto 465 con SSL, cualquier otro con STARTTLS."""
    aj = config.ajustes()
    if aj["smtp_port"] == 465:
        return smtplib.SMTP_SSL(aj["smtp_host"], 465, timeout=30)
    servidor = smtplib.SMTP(aj["smtp_host"], aj["smtp_port"], timeout=30)
    servidor.starttls()
    return servidor


def enviar_lote(ruta: Path, real: bool = False, hoy: date | None = None,
                smtp_factory=None, dormir=time.sleep, salida=print) -> dict:
    """Envía solo las filas con aprobado=SI. Sin real=True no envía nada ni cambia estados."""
    aj = config.ajustes()
    smtp_factory = smtp_factory or _smtp_servidor
    hoy = hoy or date.today()
    filas_lote = lotes.leer_lote(ruta)
    nombre_lote = Path(ruta).stem
    aprobadas = [f for f in filas_lote if f["aprobado"].strip().upper() == "SI"]
    ya_enviadas = {(r["lote"], r["id"], r["tipo"]) for r in _leer_registro() if r["resultado"] == "enviado"}
    pendientes = [f for f in aprobadas if (nombre_lote, f["id"], f["tipo"]) not in ya_enviadas]

    cupo = cupo_disponible(hoy, aj)
    resumen = {"aprobadas": len(aprobadas), "ya_enviadas": len(aprobadas) - len(pendientes),
               "enviados": 0, "rebotados": 0, "bajas": 0, "aplazados": 0, "simulacion": not real}

    if not real:
        salida(f"SIMULACIÓN: se enviarían hasta {min(cupo, len(pendientes))} de {len(pendientes)} "
               f"correos aprobados (cupo de hoy: {cupo}). No se ha enviado nada.")
        for f in pendientes[:cupo]:
            salida(f"  -> {f['para']} | {f['asunto']}")
        resumen["aplazados"] = max(0, len(pendientes) - cupo)
        return resumen

    faltan = config.falta_credenciales(aj)
    if faltan:
        raise ErrorEnvio(faltan)
    if any(f["tipo"] == "primero" for f in pendientes) and not config.DOSSIER_PATH.exists():
        raise ErrorEnvio(f"No encuentro el dossier en {config.DOSSIER_PATH}")

    prospectos_filas = prospectos.cargar()
    por_id = {p["id"]: p for p in prospectos_filas}
    lista_bajas = bajas.cargar()
    a_enviar = pendientes[:cupo]
    resumen["aplazados"] = len(pendientes) - len(a_enviar)

    servidor = None
    try:
        if a_enviar:
            token = None
            if aj["auth"] == "oauth":
                try:
                    token = oauth.token_de_acceso(aj)
                except oauth.ErrorOAuth as e:
                    raise ErrorEnvio(str(e)) from e
            servidor = smtp_factory()
            try:
                oauth.autenticar_smtp(servidor, aj["gmail_user"], aj["gmail_app_password"], token)
            except smtplib.SMTPAuthenticationError as e:
                raise ErrorEnvio("El servidor de correo rechazó el acceso (usuario, contraseña o "
                                 "autorización de Google). Prueba: python agente.py probar") from e

        for i, f in enumerate(a_enviar):
            p = por_id.get(f["id"])
            base = {"fecha": hoy.isoformat(), "hora": datetime.now().strftime("%H:%M:%S"),
                    "lote": nombre_lote, "id": f["id"], "tipo": f["tipo"],
                    "email": f["para"], "segmento": f["segmento"]}
            if bajas.es_baja(f["para"], lista_bajas):
                resumen["bajas"] += 1
                if p:
                    p["estado"] = "baja"
                _anotar({**base, "resultado": "omitido_baja", "detalle": ""})
                continue
            try:
                servidor.send_message(construir_mensaje(aj, f, f["tipo"] == "primero"))
            except smtplib.SMTPRecipientsRefused as e:
                resumen["rebotados"] += 1
                if p:
                    p["estado"] = "rebotado"
                _anotar({**base, "resultado": "rechazado", "detalle": str(e)[:200]})
                salida(f"  ✗ {f['para']}: rechazado por el servidor")
                continue
            except (smtplib.SMTPException, OSError) as e:
                _anotar({**base, "resultado": "error", "detalle": str(e)[:200]})
                raise ErrorEnvio(f"Se detiene el envío por un error: {e}", resumen) from e

            resumen["enviados"] += 1
            _anotar({**base, "resultado": "enviado", "detalle": ""})
            if p:
                if f["tipo"] == "primero":
                    p["estado"], p["fecha_envio"] = "enviado", hoy.isoformat()
                else:
                    p["estado"], p["fecha_recordatorio"] = "recordatorio_enviado", hoy.isoformat()
            salida(f"  ✓ {f['para']}")
            if i < len(a_enviar) - 1:
                dormir(random.uniform(aj["pausa_min"], aj["pausa_max"]))
    finally:
        if servidor is not None:
            try:
                servidor.quit()
            except Exception:
                pass
        # Lo que no se aprobó vuelve a su estado anterior para poder prepararlo otra vez.
        no_aprobadas = {f["id"]: f["tipo"] for f in filas_lote if f["aprobado"].strip().upper() != "SI"}
        for pid, tipo in no_aprobadas.items():
            p = por_id.get(pid)
            if p and p["lote"] == nombre_lote:
                p["estado"] = "nuevo" if tipo == "primero" else "enviado"
        prospectos.guardar(prospectos_filas)
    return resumen
