"""Verificación de contactos por bloques: lo único que tienes que revisar a mano."""
from __future__ import annotations

from . import prospectos


def verificar_bloque(n: int = 10, entrada=input, salida=print) -> dict:
    """Muestra un bloque de contactos 'por_verificar' y pide tu visto bueno.

    Aprobados pasan a 'nuevo' (el autopiloto ya puede escribirles); descartados, a 'descartado'.
    """
    filas = prospectos.cargar()
    pendientes = [f for f in filas if f["estado"] == "por_verificar"]
    bloque = pendientes[:n]
    resumen = {"aprobados": 0, "descartados": 0, "pendientes": len(pendientes)}
    if not bloque:
        salida("No hay contactos pendientes de verificar.")
        return resumen

    salida(f"\nBLOQUE DE {len(bloque)} CONTACTOS ({len(pendientes)} pendientes en total)\n")
    for f in bloque:
        alerta = prospectos.avisos(f)
        salida(f"#{f['id']:>4} | {f['segmento']} | {f['nombre']} | {f['email']} | {f['municipio']}")
        detalle = f"       fuente: {f['fuente'] or '-'}"
        if f["web"] or f["notas"]:
            detalle += f" | {f['web'] or f['notas']}"
        salida(detalle + (f"\n       ⚠ {alerta}" if alerta else ""))

    ids_bloque = {f["id"] for f in bloque}
    while True:
        r = entrada("\n[t] aprobar todos · [e] aprobar todos menos algunos · [n] descartar todos · [q] salir: ").strip().lower()
        if r == "q":
            return resumen
        if r == "t":
            descartar: set[str] = set()
        elif r == "n":
            descartar = set(ids_bloque)
        elif r == "e":
            texto = entrada("IDs a descartar, separados por comas: ")
            descartar = {x.strip().lstrip("#") for x in texto.split(",") if x.strip()}
            if not descartar <= ids_bloque:
                salida(f"Estos IDs no están en el bloque: {', '.join(sorted(descartar - ids_bloque))}")
                continue
        else:
            continue
        break

    for f in bloque:
        if f["id"] in descartar:
            f["estado"] = "descartado"
            resumen["descartados"] += 1
        else:
            f["estado"] = "nuevo"
            resumen["aprobados"] += 1
    prospectos.guardar(filas)
    resumen["pendientes"] = len(pendientes) - len(bloque)
    return resumen
