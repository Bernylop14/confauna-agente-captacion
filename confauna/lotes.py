"""Lotes de borradores: el agente prepara, tú revisas y apruebas, y solo entonces se envía."""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from . import bajas, config, plantillas, prospectos

COLUMNAS_LOTE = ["id", "para", "nombre", "segmento", "tipo", "asunto", "cuerpo", "aprobado"]


def candidatos_primero(filas: list[dict], segmento: str) -> list[dict]:
    """Contactos verificados y todavía sin escribir."""
    return [f for f in filas if f["segmento"] == segmento and f["estado"] == "nuevo"]


def candidatos_recordatorio(filas: list[dict], segmento: str | None, hoy: date, dias: int) -> list[dict]:
    """Contactos escritos hace al menos `dias` días que no han respondido (un solo recordatorio)."""
    corte = hoy - timedelta(days=dias)
    return [
        f for f in filas
        if f["estado"] == "enviado" and f["fecha_envio"]
        and (segmento is None or f["segmento"] == segmento)
        and date.fromisoformat(f["fecha_envio"]) <= corte
    ]


def preparar(segmento: str | None, maximo: int, tipo: str = "primero",
             hoy: date | None = None, ahora: datetime | None = None,
             auto: bool = False) -> tuple[Path | None, int]:
    """Crea un lote de borradores. Devuelve (ruta, cantidad); (None, 0) si no hay candidatos.

    Con auto=True el lote lo crea el autopiloto (su nombre empieza por 'auto_').
    """
    if tipo not in ("primero", "recordatorio"):
        raise ValueError("tipo debe ser 'primero' o 'recordatorio'")
    if tipo == "primero" and segmento not in prospectos.SEGMENTOS:
        raise ValueError(f"segmento debe ser uno de: {', '.join(prospectos.SEGMENTOS)}")

    aj = config.ajustes()
    hoy = hoy or date.today()
    ahora = ahora or datetime.now()
    filas = prospectos.cargar()
    lista_bajas = bajas.cargar()

    # Quien está en la lista de bajas no vuelve a recibir nada.
    for f in filas:
        if f["estado"] not in ("baja",) and bajas.es_baja(f["email"], lista_bajas):
            f["estado"] = "baja"

    if tipo == "primero":
        candidatos = candidatos_primero(filas, segmento)
        plantilla_nombre, estado_borrador = segmento, "borrador"
    else:
        candidatos = candidatos_recordatorio(filas, segmento, hoy, aj["dias_recordatorio"])
        plantilla_nombre, estado_borrador = "recordatorio", "recordatorio_borrador"

    candidatos = candidatos[:maximo]
    if not candidatos:
        prospectos.guardar(filas)
        return None, 0

    config.LOTES_DIR.mkdir(parents=True, exist_ok=True)
    etiqueta = segmento or "todos"
    nombre_lote = f"{'auto' if auto else 'lote'}_{ahora:%Y%m%d_%H%M%S}_{etiqueta}_{tipo}"
    ruta = config.LOTES_DIR / f"{nombre_lote}.csv"

    filas_lote = []
    for f in candidatos:
        asunto, cuerpo = plantillas.renderizar(plantilla_nombre, plantillas.contexto(aj, f))
        filas_lote.append({
            "id": f["id"], "para": f["email"], "nombre": f["nombre"],
            "segmento": f["segmento"], "tipo": tipo,
            "asunto": asunto, "cuerpo": cuerpo, "aprobado": "",
        })
        f["estado"], f["lote"] = estado_borrador, nombre_lote

    _escribir_lote(ruta, filas_lote)
    ruta.with_suffix(".txt").write_text(_vista_previa(filas_lote), encoding="utf-8")
    prospectos.guardar(filas)
    return ruta, len(filas_lote)


def _escribir_lote(ruta: Path, filas: list[dict]) -> None:
    with Path(ruta).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_LOTE)
        w.writeheader()
        w.writerows(filas)


def leer_lote(ruta: Path) -> list[dict]:
    with Path(ruta).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _vista_previa(filas: list[dict]) -> str:
    bloques = []
    for f in filas:
        bloques.append(
            f"=== #{f['id']} · {f['nombre']} · {f['para']} ===\n"
            f"Asunto: {f['asunto']}\n\n{f['cuerpo']}\n"
        )
    return "\n".join(bloques)


def revisar(ruta: Path, entrada=input, salida=print) -> dict:
    """Revisión interactiva: s = aprobar, n = descartar, q = guardar y salir."""
    filas = leer_lote(ruta)
    resumen = {"aprobados": 0, "descartados": 0, "pendientes": 0}
    salir = False
    for f in filas:
        if salir or f["aprobado"].strip():
            continue
        salida(f"\n=== #{f['id']} · {f['nombre']} · {f['para']} ===")
        salida(f"Asunto: {f['asunto']}\n")
        salida(f["cuerpo"])
        while True:
            r = entrada("\n¿Aprobar este envío? [s]í / [n]o / [q] guardar y salir: ").strip().lower()
            if r in ("s", "si", "sí"):
                f["aprobado"] = "SI"
                break
            if r in ("n", "no"):
                f["aprobado"] = "NO"
                break
            if r in ("q", "salir"):
                salir = True
                break
    for f in filas:
        v = f["aprobado"].strip().upper()
        resumen["aprobados" if v == "SI" else "descartados" if v == "NO" else "pendientes"] += 1
    _escribir_lote(ruta, filas)
    return resumen
