# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Aviso de versión nueva de ViDeck Pulse (solo la versión estable): al iniciar, mira la última Release publicada en el repositorio de GitHub
del programa y, si es más nueva, invita al usuario a ir a su página de descarga. NO descarga ni instala nada solo.

  · consulta `releases/latest` del repositorio (GitHub no incluye ahí las versiones de prueba ni los borradores);
  · solo cuenta una etiqueta con el formato «v<mayor>.<menor>.<parche>-u<N>» (p. ej. `v2.0.0-u2`) mayor que la que corre;
  · la dirección que se abre es siempre una página de ese mismo repositorio (cualquier otra se reemplaza por su página de Releases);
  · sin red, con el repositorio aún privado o con la API limitada, el aviso simplemente no sale (queda en el registro);
  · «Omitir esta versión» guarda su etiqueta; «Recordármelo después» no guarda nada (se vuelve a avisar al próximo inicio).
"""
import re
import time

from . import ffmpeg_tools as ft
from . import version

RELEASES_URL = f"https://github.com/{version.REPO}/releases"
API_URL = f"https://api.github.com/repos/{version.REPO}/releases/latest"
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-u(\d+)$")


def parse_tag(tag):
    """(mayor, menor, parche, N) de «v2.0.0-u1» o None si no tiene ese formato."""
    m = TAG_RE.match(str(tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def current_tuple():
    """Versión en marcha como tupla, o None si no es una versión estable (las Betas no buscan nada)."""
    return parse_tag(version.release_tag()) if version.STABLE else None


def nice(tag):
    """«v2.0.0-u2» → «v2.00 u2» (como se muestra en el programa)."""
    t = parse_tag(tag)
    return f"v{t[0]}.{t[1]:02d} u{t[3]}" if t else str(tag)


def section(cfg):
    sec = cfg.setdefault("updates", {})
    sec.setdefault("check_on_start", True)
    sec.setdefault("last_check", 0)
    sec.setdefault("skip_tag", "")
    return sec


def safe_url(url):
    """Solo páginas del repositorio del programa (https://github.com/<repo>/…); cualquier otra cosa → su página de Releases."""
    u = str(url or "")
    return u if u.startswith(f"https://github.com/{version.REPO}/") else RELEASES_URL


def due(cfg, now=None, force=False):
    """¿Se consulta? Sin plazos: una vez en cada inicio del programa si «Verificar actualizaciones al iniciar» está activado, o a mano
    (`force`) desde «Componentes»."""
    return bool(force) or bool(section(cfg)["check_on_start"])


def query(net=None):
    """La última Release publicada: {"tag", "url", "name", "published"} (FfmpegError/NetworkError si no se puede)."""
    data = (net or ft.Net()).json(API_URL)
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise ft.FfmpegError("la consulta no devolvió una versión publicada")
    tag = str(data.get("tag_name") or "")
    return {"tag": tag, "url": safe_url(data.get("html_url")), "name": str(data.get("name") or tag), "published": str(data.get("published_at") or "")[:10]}


def check(cfg, net=None, now=None, force=False, current=None, fetch=None):
    """Resultado de mirar si hay una versión nueva: {"state": …, …} con state en
    «disabled» (no es la estable o está desactivado) · «not_due» (desactivado en «Verificar actualizaciones al iniciar») · «error» (+ "error") · «current» (al día) ·
    «skipped» (hay una nueva pero el usuario la omitió) · «newer» (+ tag, text, url, published, running).
    `current` (tupla) y `fetch()` se pueden sustituir (pruebas). Anota la hora de la consulta (salvo si hay una nueva sin contestar: ver `answer`)."""
    cur = current if current is not None else current_tuple()
    if cur is None:
        return {"state": "disabled"}
    sec = section(cfg)
    if not due(cfg, now, force):
        return {"state": "not_due"}
    try:
        rel = (fetch or (lambda: query(net)))()
    except ft.FfmpegError as e:
        return {"state": "error", "error": str(e)}
    except Exception as e:                                               # noqa: BLE001: el aviso nunca debe romper el arranque
        return {"state": "error", "error": f"{type(e).__name__}: {e}"}
    tag = parse_tag(rel["tag"])
    stamp = now if now is not None else time.time()
    if tag is None or tag <= cur:
        sec["last_check"] = stamp
        return {"state": "current", "tag": rel["tag"]}
    out = {"state": "newer", "tag": rel["tag"], "text": nice(rel["tag"]), "url": rel["url"], "published": rel.get("published", ""),
           "running": nice(version.release_tag())}
    if sec.get("skip_tag") == rel["tag"] and not force:
        sec["last_check"] = stamp
        out["state"] = "skipped"
    return out


def answer(cfg, result, choice, now=None):
    """Anota la respuesta al aviso: «skip» guarda la etiqueta (no se vuelve a avisar de ESA versión) · «open» la da por vista · «later» no
    guarda nada (se avisará de nuevo al próximo inicio)."""
    sec = section(cfg)
    stamp = now if now is not None else time.time()
    if choice == "skip":
        sec["skip_tag"] = result.get("tag", "")
        sec["last_check"] = stamp
    elif choice == "open":
        sec["last_check"] = stamp
