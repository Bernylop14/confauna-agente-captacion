"""Lista de bajas: quien pide no recibir más correos queda aquí para siempre."""
from __future__ import annotations

from . import config


def _normalizar(valor: str) -> str:
    return valor.strip().lower()


def cargar() -> set[str]:
    ruta = config.BAJAS_TXT
    if not ruta.exists():
        return set()
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    return {_normalizar(l) for l in lineas if l.strip() and not l.strip().startswith("#")}


def es_baja(email: str, bajas: set[str]) -> bool:
    """True si el email, o su dominio escrito como @dominio, está en la lista de bajas."""
    email = _normalizar(email)
    dominio = "@" + email.split("@")[-1] if "@" in email else ""
    return email in bajas or (dominio and dominio in bajas)


def anadir(email: str) -> bool:
    """Añade una baja. Devuelve False si ya estaba."""
    email = _normalizar(email)
    if email in cargar():
        return False
    ruta = config.BAJAS_TXT
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a", encoding="utf-8") as f:
        f.write(email + "\n")
    return True
