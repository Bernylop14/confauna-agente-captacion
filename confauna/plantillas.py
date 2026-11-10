"""Plantillas de correo por segmento. Formato: primera línea 'ASUNTO: ...', línea en blanco, cuerpo."""
from __future__ import annotations

from . import config


class _Seguro(dict):
    """Si falta un campo en el contexto, lo deja vacío en lugar de romper."""

    def __missing__(self, clave: str) -> str:
        return ""


def contexto(ajustes: dict, fila: dict) -> dict:
    persona = (fila.get("persona") or "").strip()
    saludo = f"Buenos días, {persona}:" if persona else "Buenos días:"
    firma = (
        f"{ajustes['remitente_nombre']}\n"
        f"{ajustes['empresa']} · Control y prevención profesional de aves\n"
        f"Málaga · Costa del Sol · Tel./WhatsApp {ajustes['telefono']}"
    )
    return {
        "saludo": saludo,
        "nombre": (fila.get("nombre") or "").strip(),
        "municipio": (fila.get("municipio") or "").strip(),
        "origen": (fila.get("fuente") or "").strip() or "fuentes públicas",
        "remitente": ajustes["remitente_nombre"],
        "telefono": ajustes["telefono"],
        "empresa": ajustes["empresa"],
        "firma": firma,
    }


def renderizar(nombre_plantilla: str, ctx: dict) -> tuple[str, str]:
    """Devuelve (asunto, cuerpo) listos para enviar."""
    ruta = config.TEMPLATES_DIR / f"{nombre_plantilla}.txt"
    texto = ruta.read_text(encoding="utf-8")
    primera, _, resto = texto.partition("\n")
    if not primera.upper().startswith("ASUNTO:"):
        raise ValueError(f"{ruta.name}: la primera línea debe empezar por 'ASUNTO:'")
    datos = _Seguro(ctx)
    asunto = primera.split(":", 1)[1].strip().format_map(datos)
    cuerpo = resto.strip("\n").format_map(datos)
    return asunto, cuerpo
