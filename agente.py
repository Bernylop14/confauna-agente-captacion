#!/usr/bin/env python3
"""Agente de captación de ConFauna. Uso: python agente.py <comando> --ayuda"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from confauna import (autopiloto, bajas, buzon, config, diagnostico, envio, lotes, oauth,
                      prospectos, verificacion)


def cmd_importar(a) -> int:
    estado = "por_verificar" if a.por_verificar else "nuevo"
    r = prospectos.importar(Path(a.archivo), estado_inicial=estado)
    print("Importación terminada:", ", ".join(f"{k}: {v}" for k, v in r.items()))
    if estado == "por_verificar" and r["añadidos"]:
        print("Siguiente paso: python agente.py verificar")
    return 0


def cmd_preparar(a) -> int:
    ruta, n = lotes.preparar(a.segmento, a.max, tipo="primero")
    if not ruta:
        print(f"No hay prospectos 'nuevo' en el segmento {a.segmento}.")
        return 0
    print(f"Lote creado con {n} borradores:\n  {ruta}\n  {ruta.with_suffix('.txt')} (para leerlos de corrido)")
    print(f"Siguiente paso: python agente.py revisar {ruta}")
    return 0


def cmd_seguimiento(a) -> int:
    ruta, n = lotes.preparar(a.segmento, a.max, tipo="recordatorio")
    if not ruta:
        print("No hay nadie pendiente de recordatorio todavía.")
        return 0
    print(f"Lote de recordatorios con {n} borradores:\n  {ruta}")
    print(f"Siguiente paso: python agente.py revisar {ruta}")
    return 0


def cmd_revisar(a) -> int:
    r = lotes.revisar(Path(a.lote))
    print(f"Aprobados: {r['aprobados']} · Descartados: {r['descartados']} · Pendientes: {r['pendientes']}")
    if r["aprobados"]:
        print(f"Siguiente paso: python agente.py enviar {a.lote}   (simulación)\n"
              f"                python agente.py enviar {a.lote} --real")
    return 0


def cmd_enviar(a) -> int:
    try:
        r = envio.enviar_lote(Path(a.lote), real=a.real)
    except envio.ErrorEnvio as e:
        print(f"ERROR: {e}")
        return 1
    print("Resumen:", ", ".join(f"{k}: {v}" for k, v in r.items() if k != "simulacion"))
    if not a.real:
        print("Era una simulación. Para enviar de verdad, añade --real")
    return 0


def cmd_buzon(a) -> int:
    try:
        hallazgos = buzon.sincronizar()
    except ValueError as e:
        print(e)
        return 1
    except Exception as e:  # red, IMAP desactivado, credenciales...
        print(f"No se pudo leer el buzón: {e}\n¿Está IMAP activado en tu correo?")
        return 1
    print(f"{len(hallazgos)} novedades. Las respuestas ('respondido') las atiendes tú: ahí está el cliente.")
    return 0


def cmd_autorizar(a) -> int:
    try:
        datos = oauth.autorizar(config.ajustes())
    except oauth.ErrorOAuth as e:
        print(f"ERROR: {e}")
        return 1
    print(f"Autorización guardada para {datos['usuario']}.\nSiguiente paso: python agente.py probar")
    return 0


def cmd_probar(a) -> int:
    return 0 if diagnostico.probar() else 1


def cmd_verificar(a) -> int:
    r = verificacion.verificar_bloque(a.bloque)
    print(f"Aprobados: {r['aprobados']} · Descartados: {r['descartados']} · "
          f"Por verificar todavía: {r['pendientes']}")
    return 0


def cmd_autopiloto(a) -> int:
    segmentos = tuple(a.segmentos.split(",")) if a.segmentos else autopiloto.SEGMENTOS_POR_DEFECTO
    r = autopiloto.ejecutar(real=a.real, segmentos=segmentos, forzar_horario=a.sin_horario)
    if not a.real:
        print("Era una simulación. Para enviar de verdad, añade --real")
    return 1 if r["estado"] in ("error", "frenado") else 0


def cmd_marcar(a) -> int:
    if a.estado not in prospectos.ESTADOS:
        print("Estado no válido. Opciones:", ", ".join(prospectos.ESTADOS))
        return 1
    filas = prospectos.cargar()
    for f in filas:
        if f["id"] == str(a.id):
            f["estado"] = a.estado
            prospectos.guardar(filas)
            print(f"#{a.id} ({f['nombre']}) -> {a.estado}")
            return 0
    print(f"No existe el prospecto #{a.id}")
    return 1


def cmd_baja(a) -> int:
    nueva = bajas.anadir(a.email)
    filas = prospectos.cargar()
    for f in filas:
        if f["email"].lower() == a.email.strip().lower():
            f["estado"] = "baja"
    prospectos.guardar(filas)
    print("Baja registrada." if nueva else "Ya estaba en la lista de bajas.")
    return 0


def cmd_estado(a) -> int:
    filas = prospectos.cargar()
    if not filas:
        print("Aún no hay prospectos. Usa: python agente.py importar archivo.csv")
        return 0
    cuenta = Counter((f["segmento"], f["estado"]) for f in filas)
    for seg in prospectos.SEGMENTOS:
        partes = [f"{est}: {cuenta[(seg, est)]}" for est in prospectos.ESTADOS if cuenta[(seg, est)]]
        print(f"{seg:16} total {sum(cuenta[(seg, e)] for e in prospectos.ESTADOS):4}  " + " · ".join(partes))
    print(f"Enviados hoy: {envio.enviados_hoy(date.today())} / {envio.limite_efectivo(config.ajustes(), date.today())}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Agente de captación de ConFauna")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("importar", help="Añade contactos desde un CSV")
    s.add_argument("archivo")
    s.add_argument("--por-verificar", action="store_true",
                   help="Los contactos quedan pendientes de tu verificación por bloques")
    s.set_defaults(f=cmd_importar)

    s = sub.add_parser("autorizar", help="(Solo con EMAIL_AUTH=oauth) Da permiso a ConFauna para usar tu correo de Google")
    s.set_defaults(f=cmd_autorizar)

    s = sub.add_parser("probar", help="Comprueba el acceso al correo (envío y lectura) sin enviar nada")
    s.set_defaults(f=cmd_probar)

    s = sub.add_parser("verificar", help="Aprueba o descarta un bloque de contactos")
    s.add_argument("--bloque", type=int, default=10, help="Contactos por bloque (por defecto 10)")
    s.set_defaults(f=cmd_verificar)

    s = sub.add_parser("autopiloto", help="Envío diario automático a contactos verificados")
    s.add_argument("--real", action="store_true", help="Sin esto solo simula")
    s.add_argument("--segmentos", default=None, help="Por ejemplo: administradores,hoteles")
    s.add_argument("--sin-horario", action="store_true", help="Ignora el horario laboral")
    s.set_defaults(f=cmd_autopiloto)

    s = sub.add_parser("preparar", help="Crea un lote de borradores para un segmento")
    s.add_argument("segmento", choices=prospectos.SEGMENTOS)
    s.add_argument("--max", type=int, default=20); s.set_defaults(f=cmd_preparar)

    s = sub.add_parser("seguimiento", help="Lote de recordatorios para quien no ha respondido")
    s.add_argument("--segmento", choices=prospectos.SEGMENTOS, default=None)
    s.add_argument("--max", type=int, default=20); s.set_defaults(f=cmd_seguimiento)

    s = sub.add_parser("revisar", help="Revisa y aprueba un lote, correo a correo")
    s.add_argument("lote"); s.set_defaults(f=cmd_revisar)

    s = sub.add_parser("enviar", help="Envía los aprobados (sin --real solo simula)")
    s.add_argument("lote"); s.add_argument("--real", action="store_true"); s.set_defaults(f=cmd_enviar)

    s = sub.add_parser("buzon", help="Detecta respuestas, bajas y rebotes en el buzón")
    s.set_defaults(f=cmd_buzon)

    s = sub.add_parser("marcar", help="Cambia a mano el estado de un prospecto")
    s.add_argument("id", type=int); s.add_argument("estado"); s.set_defaults(f=cmd_marcar)

    s = sub.add_parser("baja", help="Registra una baja manualmente")
    s.add_argument("email"); s.set_defaults(f=cmd_baja)

    s = sub.add_parser("estado", help="Resumen de prospectos y envíos de hoy")
    s.set_defaults(f=cmd_estado)

    a = p.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
