"""Autopiloto: un pase diario que escribe a los contactos YA verificados, sin pedir aprobación correo a correo.

Cada ejecución, en este orden:
  1. Respeta el freno de seguridad (data/PAUSA.txt) y el horario laboral.
  2. Lee el buzón: respuestas, bajas y rebotes.
  3. Si la tasa de rebote es alta, se frena solo y te lo dice.
  4. Reanuda lotes automáticos que quedaron a medias.
  5. Manda recordatorios pendientes y, con el cupo que quede, primeros correos.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

from . import buzon, config, envio, lotes, prospectos

SEGMENTOS_POR_DEFECTO = ("administradores", "fotovoltaica", "agroindustria", "hoteles", "ayuntamientos")
UMBRAL_REBOTE = 0.05      # 5 %
MINIMO_PARA_FRENAR = 20   # no se juzga la tasa con menos de 20 enviados


def _ruta_pausa() -> Path:
    return config.DATA_DIR / "PAUSA.txt"


def _log(mensaje: str, ahora: datetime) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (config.DATA_DIR / "autopiloto.log").open("a", encoding="utf-8") as f:
        f.write(f"{ahora:%Y-%m-%d %H:%M:%S}  {mensaje}\n")


def tasa_rebote() -> tuple[int, int]:
    """(rebotados, escritos): contactos a los que se escribió alguna vez y cuántos rebotaron."""
    escritos = [f for f in prospectos.cargar() if f["fecha_envio"]]
    return sum(1 for f in escritos if f["estado"] == "rebotado"), len(escritos)


def _hay_que_frenar() -> bool:
    rebotados, escritos = tasa_rebote()
    return escritos >= MINIMO_PARA_FRENAR and rebotados / escritos > UMBRAL_REBOTE


def _aprobar_todo(ruta: Path) -> None:
    filas = lotes.leer_lote(ruta)
    for f in filas:
        f["aprobado"] = "SI"
    lotes._escribir_lote(ruta, filas)


def _lotes_pendientes_auto() -> list[Path]:
    """Lotes 'auto_' con envíos que quedaron a medias (por un error o por falta de cupo)."""
    nombres = {f["lote"] for f in prospectos.cargar()
               if f["estado"] in ("borrador", "recordatorio_borrador") and f["lote"].startswith("auto_")}
    rutas = [config.LOTES_DIR / f"{n}.csv" for n in sorted(nombres)]
    return [r for r in rutas if r.exists()]


def _plan(segmentos, hoy: date, aj: dict) -> dict:
    """Qué haría ahora mismo, sin tocar nada."""
    filas = prospectos.cargar()
    recordatorios = len(lotes.candidatos_recordatorio(filas, None, hoy, aj["dias_recordatorio"]))
    primeros = {s: len(lotes.candidatos_primero(filas, s)) for s in segmentos}
    return {"recordatorios": recordatorios, "primeros": primeros}


def ejecutar(real: bool = False, segmentos=SEGMENTOS_POR_DEFECTO, ahora: datetime | None = None,
             forzar_horario: bool = False, smtp_factory=None, dormir=time.sleep, salida=print,
             sincronizar_fn=None) -> dict:
    aj = config.ajustes()
    ahora = ahora or datetime.now()
    hoy = ahora.date()
    sincronizar_fn = sincronizar_fn or buzon.sincronizar
    res = {"estado": "ok", "enviados": 0, "recordatorios": 0, "primeros": 0, "rebotados": 0}

    if _ruta_pausa().exists():
        salida(f"AUTOPILOTO EN PAUSA. Lee {_ruta_pausa()} y bórralo cuando hayas revisado la lista.")
        res["estado"] = "pausado"
        return res

    if not forzar_horario and (ahora.weekday() >= 5 or not aj["hora_inicio"] <= ahora.hour < aj["hora_fin"]):
        salida(f"Fuera de horario ({aj['hora_inicio']}-{aj['hora_fin']} h, lunes a viernes). No se envía nada.")
        res["estado"] = "fuera_de_horario"
        return res

    cupo = envio.cupo_disponible(hoy, aj)

    if not real:
        plan = _plan(segmentos, hoy, aj)
        salida(f"SIMULACIÓN. Cupo de hoy: {cupo}. Recordatorios pendientes: {plan['recordatorios']}. "
               f"Primeros correos disponibles: {plan['primeros']}. No se ha enviado nada.")
        res["estado"] = "simulacion"
        return res

    # --- 2. Buzón: respuestas, bajas y rebotes ---
    buzon_ok = True
    try:
        for email_p, tipo in sincronizar_fn(salida=salida):
            res["rebotados"] += tipo == "rebotado"
    except Exception as e:  # sin buzón no se manda ningún recordatorio, por si alguien ha respondido
        buzon_ok = False
        salida(f"AVISO: no se pudo leer el buzón ({e}). Hoy no se mandan recordatorios.")
        _log(f"buzon_error {e}", ahora)

    # --- 3. Freno por rebotes ---
    if _hay_que_frenar():
        rebotados, escritos = tasa_rebote()
        _ruta_pausa().write_text(
            f"Autopiloto frenado el {ahora:%Y-%m-%d %H:%M}: {rebotados} de {escritos} contactos han rebotado "
            f"({rebotados / escritos:.0%}).\nUna tasa alta daña la reputación de tu correo. Revisa y depura "
            f"los contactos (python agente.py estado) y borra este archivo para reanudar.\n", encoding="utf-8")
        salida(f"FRENO DE SEGURIDAD: {rebotados} de {escritos} contactos han rebotado. Autopiloto en pausa.")
        _log(f"freno rebotes {rebotados}/{escritos}", ahora)
        res["estado"] = "frenado"
        return res

    def enviar(ruta: Path, tipo: str) -> bool:
        """Envía un lote automático. False si hay que parar todo (error grave)."""
        try:
            r = envio.enviar_lote(ruta, real=True, hoy=hoy, smtp_factory=smtp_factory,
                                  dormir=dormir, salida=salida)
        except envio.ErrorEnvio as e:
            parcial = e.resumen or {}
            res["enviados"] += parcial.get("enviados", 0)
            res["recordatorios" if tipo == "recordatorio" else "primeros"] += parcial.get("enviados", 0)
            res["rebotados"] += parcial.get("rebotados", 0)
            salida(f"ERROR: {e}")
            _log(f"error {e} (enviados antes del error: {parcial.get('enviados', 0)})", ahora)
            res["estado"] = "error"
            return False
        res["enviados"] += r["enviados"]
        res["recordatorios" if tipo == "recordatorio" else "primeros"] += r["enviados"]
        res["rebotados"] += r["rebotados"]
        return True

    # --- 4. Reanudar lotes automáticos a medias ---
    for ruta in _lotes_pendientes_auto():
        if not enviar(ruta, "recordatorio" if ruta.stem.endswith("recordatorio") else "primero"):
            return res

    # --- 5. Recordatorios y primeros correos con el cupo que quede ---
    cupo = envio.cupo_disponible(hoy, aj)
    if buzon_ok and cupo > 0:
        ruta, n = lotes.preparar(None, cupo, tipo="recordatorio", hoy=hoy, ahora=ahora, auto=True)
        if ruta:
            _aprobar_todo(ruta)
            if not enviar(ruta, "recordatorio"):
                return res
    for segmento in segmentos:
        cupo = envio.cupo_disponible(hoy, aj)
        if cupo <= 0:
            break
        ruta, n = lotes.preparar(segmento, cupo, tipo="primero", hoy=hoy, ahora=ahora, auto=True)
        if ruta:
            _aprobar_todo(ruta)
            if not enviar(ruta, "primero"):
                return res

    _log(f"enviados={res['enviados']} (recordatorios={res['recordatorios']}, primeros={res['primeros']}) "
         f"rebotados={res['rebotados']}", ahora)
    salida(f"Autopiloto: {res['enviados']} enviados "
           f"({res['recordatorios']} recordatorios, {res['primeros']} primeros), cupo restante "
           f"{envio.cupo_disponible(hoy, aj)}.")
    return res
