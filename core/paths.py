# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rutas de ViDeck Pulse (update 13): dónde están el programa y los datos, y cómo se guardan las rutas dentro de los archivos.

Hay dos carpetas distintas:

· carpeta del PROGRAMA (`app_dir`): el código (`main.py`) o, en el ejecutable, la carpeta de `ViDeckPulse.exe`.
· carpeta de DATOS (`data_dir`): `config.json`, `tracks_db.json`, `sync_learned.json`, el registro… y, en la versión instalada, también
  los componentes descargados (`mpv/`, `ffmpeg/`, `legacy/`).
    – Desde el código fuente o con un archivo `portable.flag` junto al ejecutable (versión portable): la misma carpeta del programa
      (como hasta la update 12: se puede copiar o mover la carpeta entera y todo sigue funcionando).
    – Ejecutable SIN `portable.flag` (versión instalada): `%APPDATA%\\ViDeck Pulse`, así que actualizar o reinstalar no toca los datos.
    – La variable de entorno `VIDECK_PULSE_DATA` fuerza otra carpeta (para pruebas o usos avanzados).

El resto del código usa nombres relativos («config.json»): `enter_data_dir()` (la llama `main.py` al arrancar) pone la carpeta de datos
como carpeta de trabajo.

RUTAS EN LOS ARCHIVOS: dentro de `config.json` (`videos_dir`), `tracks_db.json` (`video_path`) y `sync_learned.json` (claves) una ruta que
cae DENTRO de la carpeta de datos se guarda RELATIVA (con «/»); así mover o copiar la carpeta no rompe los vínculos. En memoria las
rutas son siempre absolutas (el resto del programa no cambia). Las rutas absolutas guardadas por versiones anteriores se siguen leyendo, y si
el archivo ya no está donde decía pero sí en la carpeta de videos actual (con la misma subcarpeta y nombre), `find_moved` lo encuentra.
"""
import os
import sys

APP_NAME = "ViDeck Pulse"            # (version.APP_NAME lo repite: este módulo no importa nada del programa)
PORTABLE_FLAG = "portable.flag"
ENV_DATA = "VIDECK_PULSE_DATA"

_data = {"dir": None}                # carpeta de datos fijada por `enter_data_dir()`; sin fijar = la carpeta de trabajo actual


def frozen():
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """Carpeta del programa: la del ejecutable (congelado) o la que contiene `main.py`."""
    if frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir():
    """Carpeta de los recursos de solo lectura (assets): la que desempaqueta PyInstaller (`_internal`) o la del programa."""
    if frozen():
        return getattr(sys, "_MEIPASS", None) or app_dir()
    return app_dir()


def portable():
    """True: los datos van junto al programa (código fuente, o ejecutable con `portable.flag`). False: versión instalada."""
    return (not frozen()) or os.path.exists(os.path.join(app_dir(), PORTABLE_FLAG))


def installed():
    return not portable()


def appdata_dir(env=None, home=None):
    """`%APPDATA%\\ViDeck Pulse` (Windows); fuera de Windows `~/.config/ViDeck Pulse` (solo para pruebas)."""
    env = os.environ if env is None else env
    base = env.get("APPDATA")
    if not base:
        base = os.path.join(home or os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_NAME)


def configured_data_dir():
    """La carpeta de datos que corresponde a esta ejecución (sin cambiar nada)."""
    forced = os.environ.get(ENV_DATA, "").strip()
    if forced:
        return os.path.abspath(forced)
    return appdata_dir() if installed() else app_dir()


def data_dir():
    """Carpeta de datos en uso: la fijada por `enter_data_dir()` o, si no se fijó (pruebas), la carpeta de trabajo actual."""
    return _data["dir"] or os.getcwd()


def components_dir():
    """Dónde viven los componentes que se descargan (`mpv/`, `ffmpeg/`, `legacy/`): la carpeta de datos si está instalado; si no, la del programa."""
    return configured_data_dir() if installed() or os.environ.get(ENV_DATA, "").strip() else app_dir()


LANG_MARKER = "install_language.txt"        # lo escribe el instalador en la carpeta de datos: «es» o «en» (el idioma que se eligió al instalar)


def _win_videos():
    import ctypes
    buf = ctypes.create_unicode_buffer(520)
    return buf.value if ctypes.windll.shell32.SHGetFolderPathW(None, 0x000E, None, 0, buf) == 0 else ""      # CSIDL_MYVIDEO


def _win_ui_language():
    import ctypes
    return ctypes.windll.kernel32.GetUserDefaultUILanguage()


def known_videos_dir(home=None, getter=None):
    """La carpeta «Videos» del usuario según Windows (puede estar redirigida a otra unidad o a OneDrive); sin Windows, `~/Videos`.
    `getter` (pruebas) sustituye la pregunta a Windows."""
    getter = getter or (_win_videos if os.name == "nt" else None)
    if getter:
        try:
            found = getter()
            if found:
                return found
        except Exception:                                                  # noqa: BLE001: se usa la carpeta de siempre
            pass
    home = home or os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, "Videos")


def windows_language(getter=None):
    """«es» si Windows está en español; «en» con cualquier otro idioma. Si no se puede saber, «es» (el idioma de siempre del programa)."""
    getter = getter or (_win_ui_language if os.name == "nt" else None)
    if getter:
        try:
            return "es" if (int(getter()) & 0x3FF) == 0x0A else "en"      # LANG_SPANISH (0x0A) en cualquiera de sus variantes
        except Exception:                                                  # noqa: BLE001
            pass
    return "es"


def install_language():
    """El idioma que se eligió en el instalador («es» | «en»), o «» si no hay (versión portable, o instalada antes de esta función)."""
    try:
        with open(os.path.join(configured_data_dir(), LANG_MARKER), encoding="utf-8-sig") as f:
            code = f.read().strip().lower()
        return code if code in ("es", "en") else ""
    except OSError:
        return ""


def initial_language():
    """Idioma de una instalación nueva: el del instalador; en la versión portable (sin instalador), el de Windows."""
    return (install_language() if installed() else "") or windows_language()


def default_videos_dir():
    """Carpeta de videos de una instalación nueva: «Videos» junto al programa (portable) o «<Videos de Windows>\\ViDeck Pulse\\Videos» (instalada).
    (El mismo nombre en español y en inglés. «Preparar videos» convierte dentro de esta carpeta: no hay otra carpeta aparte.)"""
    return "Videos" if portable() else os.path.join(known_videos_dir(), APP_NAME, "Videos")


def enter_data_dir():
    """Crea la carpeta de datos (si falta) y la pone como carpeta de trabajo. Devuelve su ruta. Un fallo al crearla/entrar deja la carpeta
    de trabajo como estaba (el programa sigue como antes) y devuelve esa carpeta."""
    d = configured_data_dir()
    try:
        os.makedirs(d, exist_ok=True)
        os.chdir(d)
    except OSError:
        d = os.getcwd()
    _data["dir"] = d
    return d


# --------------------------------------------------------------------------------------------- rutas guardadas en los archivos
def _is_str(p):
    return isinstance(p, str) and bool(p.strip())


def store(path, base=None, osp=os.path):
    """Texto con el que se guarda `path` en un archivo: relativo a `base` (por defecto la carpeta de datos) y con «/» si está dentro de ella;
    si no (otra carpeta, otra unidad) o si ya era relativa, igual que estaba."""
    if not _is_str(path):
        return path
    base = base or data_dir()
    win = osp.sep == "\\"
    if not osp.isabs(path):
        return path.replace("\\", "/") if win else path
    try:
        rel = osp.relpath(path, base)
    except ValueError:                                  # otra unidad (Windows)
        return path
    first = rel.replace("\\", "/").split("/", 1)[0]
    if osp.isabs(rel) or first == "..":
        return path
    return rel.replace("\\", "/") if rel != "." else "."


def resolve(stored, base=None, osp=os.path):
    """Lo contrario de `store`: una ruta relativa se une a `base` (por defecto la carpeta de datos); una absoluta (de versiones anteriores)
    se deja tal cual. Siempre devuelve una ruta normalizada (o el valor tal cual si no es un texto)."""
    if not _is_str(stored):
        return stored
    if osp.isabs(stored):
        return osp.normpath(stored)
    base = base or data_dir()
    rel = stored.replace("/", osp.sep) if osp.sep == "\\" else stored
    return osp.normpath(osp.join(base, rel))


def inside(path, base=None, osp=os.path):
    """¿`path` está dentro de `base` (o es `base`)?"""
    if not _is_str(path) or not osp.isabs(path):
        return False
    s = store(path, base, osp)
    return not osp.isabs(s) and s.split("/", 1)[0] != ".."


def _parts(p):
    return [x for x in str(p).replace("\\", "/").split("/") if x and not (len(x) == 2 and x[1] == ":")]


def find_moved(path, roots, exists=os.path.exists, osp=os.path, max_parts=4):
    """Para un archivo que ya no está en `path` (la carpeta se movió o se copió a otro sitio): lo busca dentro de cada carpeta de `roots`
    con las mismas últimas 4, 3, 2 o 1 partes de su ruta (subcarpeta + nombre). Devuelve la ruta nueva o None. No recorre carpetas."""
    parts = _parts(path)
    if not parts:
        return None
    for root in roots or ():
        if not _is_str(root):
            continue
        root = osp.abspath(root) if not osp.isabs(root) else root
        for n in range(min(max_parts, len(parts)), 0, -1):
            cand = osp.normpath(osp.join(root, *parts[-n:]))
            if exists(cand):
                return cand
    return None
