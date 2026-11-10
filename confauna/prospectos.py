"""Base de prospectos en CSV (se abre bien en Excel y en PyCharm)."""
from __future__ import annotations

import csv
import os
import re
import tempfile
from pathlib import Path

from . import bajas, config

COLUMNAS = [
    "id", "segmento", "nombre", "persona", "email", "municipio", "web",
    "telefono", "fuente", "estado", "lote", "fecha_envio",
    "fecha_recordatorio", "notas",
]
SEGMENTOS = ("administradores", "hoteles", "ayuntamientos", "fotovoltaica", "agroindustria")
ESTADOS = (
    "por_verificar", "nuevo", "borrador", "enviado", "recordatorio_borrador",
    "recordatorio_enviado", "respondido", "baja", "rebotado", "descartado",
)
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")
CORREOS_PERSONALES = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es",
    "live.com", "yahoo.com", "yahoo.es", "icloud.com", "telefonica.net", "movistar.es", "terra.es",
}


def email_valido(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


def avisos(fila: dict) -> str:
    """Señales que conviene mirar al verificar un contacto (vacío si no hay ninguna)."""
    alertas = []
    dominio = fila.get("email", "").strip().lower().split("@")[-1]
    if dominio in CORREOS_PERSONALES:
        alertas.append(f"correo personal ({dominio}): mejor un correo corporativo")
    if not (fila.get("fuente") or "").strip():
        alertas.append("sin fuente: el correo la cita en el pie")
    return "; ".join(alertas)


def cargar(ruta: Path | None = None) -> list[dict]:
    ruta = Path(ruta) if ruta else config.PROSPECTOS_CSV
    if not ruta.exists():
        return []
    with ruta.open(newline="", encoding="utf-8-sig") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        for col in COLUMNAS:
            fila.setdefault(col, "")
    return filas


def guardar(filas: list[dict], ruta: Path | None = None) -> None:
    """Escritura atómica: nunca deja el CSV a medias si algo falla."""
    ruta = Path(ruta) if ruta else config.PROSPECTOS_CSV
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNAS, extrasaction="ignore")
            w.writeheader()
            w.writerows(filas)
        os.replace(tmp, ruta)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _siguiente_id(filas: list[dict]) -> int:
    ids = [int(f["id"]) for f in filas if str(f["id"]).isdigit()]
    return (max(ids) if ids else 0) + 1


def importar(ruta_nuevos: Path, estado_inicial: str = "nuevo") -> dict:
    """Añade contactos de un CSV nuevo. Requiere columnas: segmento, nombre, email.

    Opcionales: persona, municipio, web, telefono, fuente, notas.
    Descarta emails inválidos, duplicados, bajas y segmentos desconocidos.
    Con estado_inicial='por_verificar' quedan a la espera de que los verifiques por bloques.
    """
    if estado_inicial not in ("nuevo", "por_verificar"):
        raise ValueError("estado_inicial debe ser 'nuevo' o 'por_verificar'")
    filas = cargar()
    existentes = {f["email"].strip().lower() for f in filas}
    lista_bajas = bajas.cargar()
    resumen = {"añadidos": 0, "duplicados": 0, "invalidos": 0, "bajas": 0, "segmento_desconocido": 0}
    with Path(ruta_nuevos).open(newline="", encoding="utf-8-sig") as f:
        for nueva in csv.DictReader(f):
            email = (nueva.get("email") or "").strip().lower()
            segmento = (nueva.get("segmento") or "").strip().lower()
            if segmento not in SEGMENTOS:
                resumen["segmento_desconocido"] += 1
            elif not email_valido(email):
                resumen["invalidos"] += 1
            elif email in existentes:
                resumen["duplicados"] += 1
            elif bajas.es_baja(email, lista_bajas):
                resumen["bajas"] += 1
            else:
                fila = {c: (nueva.get(c) or "").strip() for c in COLUMNAS}
                fila.update(id=str(_siguiente_id(filas)), segmento=segmento,
                            email=email, estado=estado_inicial)
                filas.append(fila)
                existentes.add(email)
                resumen["añadidos"] += 1
    guardar(filas)
    return resumen
