# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Componentes externos de ViDeck Pulse (ffmpeg y libmpv) y «Legacy Mode»: arranque, avisos y errores. Sin Qt.

  · `bootstrap()` (lo llama main.py ANTES de importar mpv): aplica la libmpv preparada, activa «Legacy Mode» si está pedido y deja el PATH
    (y la búsqueda de libmpv de python-mpv) apuntando a la carpeta correcta.
  · Todo fallo de un componente se informa como ERROR en el registro y la barra de estado (`fail`) con el nombre del repositorio que falló,
    y DESPUÉS de ese error aparece el aviso «Activar Legacy Mode para funcionar correctamente» (`HINT`, una alerta amarilla que se queda
    hasta que el problema se arregla o se activa Legacy Mode) siempre que haya un paquete Legacy al que volver.
  · Las fuentes del registro son «ffmpeg» y «libmpv» (así las agrupa la barra de estado).
"""
import ctypes.util
import json
import os
import shutil
import sys
import threading
import time

from . import ffmpeg_tools as ft
from . import legacy, mpv_tools, paths
from . import devlog as dl

HINT = "Activar Legacy Mode para funcionar correctamente"
TITLE_SUFFIX = "(Legacy Mode)"
SRC_FFMPEG, SRC_MPV, SRC_LEGACY = "ffmpeg", "libmpv", "legacy"
EVENTS_FILE = "components_events.json"
NAMES = {SRC_FFMPEG: "ffmpeg", SRC_MPV: "libmpv"}

_state = {"legacy": False, "legacy_failed": False, "events": [], "mpv_dir": "", "ffmpeg_dir": "", "skip": {}}
_failed = set()
_dll_dirs = []
_orig_find = {}


def app_dir():
    """Carpeta de los componentes descargados y sus archivos (portable: la del programa; instalado: la de datos, update 13)."""
    return paths.components_dir()


def defaults():
    return {"legacy": False,
            "ffmpeg": {"auto_check": True, "last_check": 0, "skip_version": "", "auto_base": False},
            "mpv": {"auto_check": True, "last_check": 0, "skip_version": ""}}


def section(cfg):
    """`cfg["components"]` completa (crea lo que falte)."""
    d = cfg.setdefault("components", {})
    for k, v in defaults().items():
        if isinstance(v, dict):
            sub = d.setdefault(k, {})
            for kk, vv in v.items():
                sub.setdefault(kk, vv)
        else:
            d.setdefault(k, v)
    return d


# ---------------------------------------------------------------------------------------------------- rutas de libmpv
_MPV_NAMES = ("mpv-2", "libmpv-2", "mpv-1", "mpv", "libmpv")


def activate_paths(folder):
    """Hace que python-mpv encuentre la libmpv de `folder` y no otra: la carpeta pasa al principio del PATH, se añade como carpeta de DLL
    y la búsqueda `ctypes.util.find_library` de los nombres de libmpv devuelve ESE archivo. Se puede llamar varias veces."""
    folder = os.path.abspath(folder)
    here = os.path.normcase(folder)
    rest = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and os.path.normcase(os.path.abspath(p)) != here]
    os.environ["PATH"] = os.pathsep.join([folder] + rest)
    if hasattr(os, "add_dll_directory") and os.path.isdir(folder):
        try:
            _dll_dirs.append(os.add_dll_directory(folder))
        except OSError:
            pass
    if os.name == "nt":
        orig = _orig_find.setdefault("f", ctypes.util.find_library)

        def find_library(name, _orig=orig, _folder=folder):
            low = str(name).lower()
            low = low[:-4] if low.endswith(".dll") else low
            if low in _MPV_NAMES:
                p = os.path.join(_folder, mpv_tools.DLL)
                if os.path.isfile(p):
                    return p
            return _orig(name)

        ctypes.util.find_library = find_library


# ---------------------------------------------------------------------------------------------------- estado de arranque
def state():
    return _state


def legacy_active():
    return bool(_state["legacy"])


def title_suffix():
    return " " + TITLE_SUFFIX if _state["legacy"] else ""


def ffmpeg_dir():
    """Carpeta con el ffmpeg de Legacy Mode (si está activo y el paquete lo trae) o None."""
    d = _state["ffmpeg_dir"]
    return d if _state["legacy"] and d and os.path.isdir(d) else None


def _persist_event(level, src, text, base=None):
    path = os.path.join(base or app_dir(), EVENTS_FILE)
    try:
        try:
            with open(path, encoding="utf-8") as f:
                cur = json.load(f)
        except (OSError, ValueError):
            cur = []
        cur.append({"level": level, "src": src, "text": text, "t": time.time()})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cur[-20:], f, ensure_ascii=False)
    except OSError:
        pass


def _load_events(base=None):
    path = os.path.join(base or app_dir(), EVENTS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        os.remove(path)
        return [(d.get("level", "INFO"), d.get("src", SRC_MPV), d.get("text", ""), {}) for d in data if isinstance(d, dict)]
    except (OSError, ValueError):
        return []


def bootstrap(config_path="config.json", base=None, check=None):
    """Prepara el entorno de libmpv/ffmpeg antes de importar mpv. Devuelve la lista de eventos [(nivel, fuente, texto, datos)] que main.py
    vuelca al registro cuando esté listo. Nunca lanza una excepción."""
    base = base or app_dir()
    events = []
    try:
        events += [(lv, src, tx, d) for lv, src, tx, d in _load_events(base)]
        requested = False
        try:
            with open(config_path, encoding="utf-8") as f:
                requested = bool(((json.load(f) or {}).get("components") or {}).get("legacy"))
        except (OSError, ValueError, AttributeError):
            pass
        mpv_dir = ""
        if requested:
            if legacy.available():
                try:
                    act = legacy.extract()
                    _state["legacy"] = True
                    mpv_dir = legacy.component_dir("mpv", ) or ""
                    _state["ffmpeg_dir"] = legacy.component_dir("ffmpeg") or ""
                    events.append(("INFO", SRC_LEGACY, f"Legacy Mode activo ({legacy.app_tag()}): se usan los repositorios guardados "
                                                       f"({', '.join(sorted(legacy.items()))})", {}))
                except legacy.LegacyError as e:
                    _state["legacy_failed"] = True
                    events.append(("ERROR", SRC_LEGACY, f"No se pudo activar Legacy Mode ({e}); se usan los repositorios actualizados", {}))
            else:
                _state["legacy_failed"] = True
                events.append(("WARN", SRC_LEGACY, "Legacy Mode estaba activado pero no hay paquete Legacy guardado: se usan los repositorios actuales", {}))
        if not _state["legacy"]:
            for lv, tx, data in mpv_tools.apply_pending(validate=check):
                events.append((lv, SRC_MPV, tx, data))
                if data.get("failed_version"):
                    _state["skip"][SRC_MPV] = data.get("tag") or data["failed_version"]
            mpv_dir = mpv_tools.own_dir() if mpv_tools.installed() else ""
        if mpv_dir and os.path.isdir(mpv_dir):
            _state["mpv_dir"] = mpv_dir
            activate_paths(mpv_dir)
    except Exception as e:                                                       # noqa: BLE001: el arranque nunca se cae por esto
        events.append(("ERROR", SRC_MPV, f"no se pudo preparar libmpv/ffmpeg al iniciar ({type(e).__name__}: {e})", {}))
    _state["events"] = events
    return events


def adopt_new_mpv(check=None):
    """Pone en uso, SIN reiniciar, la libmpv que acaba de quedar preparada (primer inicio: todavía no se importó mpv). Devuelve (eventos, ok)."""
    events = list(mpv_tools.apply_pending(validate=check))
    mpv_dir = mpv_tools.own_dir() if mpv_tools.installed() else ""
    if mpv_dir and os.path.isdir(mpv_dir):
        _state["mpv_dir"] = mpv_dir
        activate_paths(mpv_dir)
    for lv, tx, data in events:
        _state["events"].append((lv, SRC_MPV, tx, data))
    return [(lv, tx, data) for lv, tx, data in events], bool(mpv_dir)


def flush_events(log=None):
    """Vuelca los eventos del arranque al registro. Los ERROR de un repositorio dejan también el aviso de Legacy Mode."""
    log = log or dl
    for lv, src, tx, _d in _state["events"]:
        if lv == "ERROR":
            fail(src, tx, log=log)
        elif lv == "WARN":
            log.warn(src, tx)
        else:
            log.info(src, tx)
    _state["events"] = []


def rescue_import(exc, relaunch_env="VP_RESCUE"):
    """Si `import mpv` (o lo que lo importa) falló: ¿hay una libmpv recién puesta a la que volver? Vuelve a la anterior (o, si no hay,
    activa Legacy Mode) y devuelve True si vale la pena abrir el programa otra vez. Una sola vez por arranque (evita ciclos)."""
    if os.environ.get(relaunch_env):
        return False
    text = f"{type(exc).__name__}: {exc}"
    base = mpv_tools.own_dir()
    try:
        boot = mpv_tools.boot_state(base)
        if boot and boot.get("state") == "trying":
            back = mpv_tools.revert(base)
            mpv_tools._clear_boot(base)
            _persist_event("ERROR", SRC_MPV, f"libmpv {boot.get('version', '')} no cargó ({text}): se volvió a "
                                             f"{'la versión ' + str(back.get('version')) if back else 'la libmpv anterior'}")
            os.environ[relaunch_env] = "1"
            return True
        if not _state["legacy"] and legacy.available() and "mpv" in text.lower():
            _set_legacy_in_config(True)
            _persist_event("ERROR", SRC_MPV, f"libmpv no cargó ({text}): se activó Legacy Mode")
            os.environ[relaunch_env] = "1"
            return True
    except Exception:                                                             # noqa: BLE001
        pass
    return False


def _set_legacy_in_config(on, path="config.json"):
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.setdefault("components", {})["legacy"] = bool(on)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------------------------------- errores y aviso de Legacy Mode
def fail(src, text, network=False, log=None):
    """Un repositorio falló: ERROR en el registro y la barra de estado (con el nombre del repositorio) y, después, el aviso
    «Activar Legacy Mode para funcionar correctamente». Un fallo de conexión no es un fallo del repositorio: solo avisa (WARN)."""
    log = log or dl
    name = NAMES.get(src, src)
    msg = text if text.lower().startswith(name.lower()) else f"{name}: {text}"
    if network:
        log.add(dl.WARN, src, msg, _inc=False)                       # solo al registro: sin conexión no es un problema que dure en la barra
        return
    log.error(src, msg)
    _failed.add(src)
    sync_hint(log)


def ok(src, log=None):
    """El repositorio volvió a funcionar (se instaló/actualizó bien, o se activó Legacy Mode): cierra sus errores."""
    log = log or dl
    _failed.discard(src)
    try:
        log.devlog.resolve(src)
    except AttributeError:
        pass
    sync_hint(log)


def sync_hint(log=None):
    log = log or dl
    try:
        if _failed and not _state["legacy"] and legacy.available():
            log.devlog.condition(SRC_LEGACY, dl.WARN, HINT, rows=())
        else:
            log.devlog.clear_condition(SRC_LEGACY)
    except AttributeError:
        pass


def confirm_start(player_failed=False, log=None):
    """main.py, al terminar de abrir la ventana: la libmpv recién puesta arrancó bien → se quita la marca de «probando». Si el reproductor
    no pudo usarla, la marca se deja: el siguiente arranque vuelve a la libmpv anterior."""
    log = log or dl
    if player_failed:
        return None
    boot = mpv_tools.confirm_boot()
    if boot:
        log.info(SRC_MPV, f"libmpv {boot.get('version', '')} en uso (arrancó bien)")
        ok(SRC_MPV, log)
    return boot


def reset_failures(log=None):
    _failed.clear()
    sync_hint(log)


# ---------------------------------------------------------------------------------------------------- qué se está usando
def find_libmpv():
    """(ruta de la libmpv que se usa o usaría, origen) con origen «legacy» · «propia» · «programa» (junto a main.py) · «sistema» (PATH) · ""."""
    loaded = ""
    try:
        import mpv
        lib = getattr(mpv, "backend", None)
        if lib is not None:
            from .mpv_check import loaded_path
            loaded = loaded_path(lib)
    except Exception:                                                              # noqa: BLE001
        loaded = ""
    cands = []
    if _state["legacy"] and _state["mpv_dir"]:
        cands.append((os.path.join(_state["mpv_dir"], mpv_tools.DLL), "legacy"))
    cands.append((mpv_tools.dll_path(), "propia"))
    cands.append((os.path.join(app_dir(), mpv_tools.DLL), "programa"))
    cands.append((os.path.join(os.getcwd(), mpv_tools.DLL), "programa"))
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for n in mpv_tools.ALIASES:
            cands.append((os.path.join(d, n), "sistema"))
    if loaded and os.path.dirname(loaded):
        for p, origin in cands:
            if os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(loaded)):
                return loaded, origin
        return loaded, "sistema"
    for p, origin in cands:
        if os.path.isfile(p):
            return p, origin
    return "", ""


def _date_stamp(ts):
    """«20251105» (año, mes y día) de una hora: la versión de una libmpv ajena se identifica por la fecha de su archivo."""
    t = time.localtime(ts)
    return f"{t.tm_year:04d}{t.tm_mon:02d}{t.tm_mday:02d}"


def snapshot_sources(configured_ffmpeg=""):
    """Los archivos que hoy se usan, para guardarlos en el paquete Legacy."""
    out = {}
    tools = ft.resolve(configured_ffmpeg)
    if tools.ffmpeg:
        files = [tools.ffmpeg, tools.ffprobe]
        if tools.origin == "own":
            d = os.path.dirname(tools.ffmpeg)
            files += [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.lower().endswith(".dll")]
        out["ffmpeg"] = {"version": tools.version, "origin": tools.origin, "files": files}
    path, origin = find_libmpv()
    if path and os.path.isfile(path):
        info = mpv_tools.installed(os.path.dirname(path)) if origin in ("propia", "legacy") else None
        out["mpv"] = {"version": (info or {}).get("version", "") or _date_stamp(os.path.getmtime(path)),
                      "origin": origin, "files": [path]}
    return out


def snapshot_reason(sources, base=None):
    """Por qué hay que (re)crear el paquete Legacy ahora, o «» si no hace falta: no hay paquete, es de otra versión del programa o
    le falta un componente que ya tenemos. Con Legacy Mode activo nunca se recrea."""
    if _state["legacy"] or not sources:
        return ""
    if not legacy.available(base):
        return "no hay paquete Legacy guardado"
    if not legacy.is_current(base=base):
        return "el paquete Legacy es de otra versión del programa"
    miss = legacy.missing(sources.keys(), base)
    return f"al paquete Legacy le falta {', '.join(miss)}" if miss else ""


# ---------------------------------------------------------------------------------------------------- paquete Legacy (crear)
_snap_lock = threading.Lock()


def build_snapshot(configured_ffmpeg="", progress=None, cancel=None, base=None, reason=""):
    """Guarda en el paquete Legacy los ffmpeg y libmpv que se usan ahora (hilo de fondo, prioridad baja). Solo uno a la vez."""
    from .lowprio import lower_this_thread
    if not _snap_lock.acquire(blocking=False):
        raise legacy.LegacyError("ya se está creando el paquete Legacy")
    try:
        lower_this_thread()
        sources = snapshot_sources(configured_ffmpeg)
        if not sources:
            raise legacy.LegacyError("no se encontró ffmpeg ni libmpv para guardar")
        return legacy.build(sources, progress, cancel, base, note=reason)
    finally:
        _snap_lock.release()


def snapshot_busy():
    return _snap_lock.locked()


# ---------------------------------------------------------------------------------------------------- avisos de actualización al iniciar
def startup_updates(cfg, net=None, ffmpeg_base=None, mpv_base=None, now=None):
    """[(componente, Release, instalado)] de lo que tiene una versión más nueva que avisar (como mucho una consulta al día por componente,
    sin bloquear nada: se llama desde un hilo). Los fallos de red se ignoran."""
    sec = section(cfg)
    out = []
    for name, mod, base in (("ffmpeg", ft, ffmpeg_base), ("mpv", mpv_tools, mpv_base)):
        r = mod.pending_update(sec[name], net, base, now)
        if r:
            out.append((name, r[0], r[1]))
    return out


def describe_update(name, release, inst):
    """Una línea para el aviso: «ffmpeg 8.0 → 8.0.1 · 87 MB · gyan.dev (espejo oficial en GitHub)»."""
    label = NAMES.get(SRC_FFMPEG if name == "ffmpeg" else SRC_MPV, name)
    size = f" · {release.size / 1e6:.0f} MB" if release.size else ""
    return f"{label} {(inst or {}).get('version') or '?'} → {release.version}{size} · {release.label}"


def skip_updates(cfg, items):
    """«No» en el aviso: no se vuelve a avisar de ESAS versiones (sí de una más nueva)."""
    sec = section(cfg)
    for name, release, _inst in items:
        sec[name]["skip_version"] = release.tag


def defer_updates(cfg, items):
    """«Revisar después»: la consulta de hoy no cuenta, así que se vuelve a buscar (y a preguntar) la próxima vez que se abra el programa."""
    sec = section(cfg)
    for name, release, _inst in items:
        sec[name]["last_check"] = 0
        if sec[name].get("skip_version") == release.tag:
            sec[name]["skip_version"] = ""


def run_install(src, release, net=None, progress=None, cancel=None):
    """Descarga, verifica e instala (ffmpeg) o prepara para el próximo inicio (libmpv) la versión `release`. Para correr en un hilo."""
    if src == SRC_FFMPEG:
        return ft.install(release, net, progress, cancel)
    return mpv_tools.stage(release, net, progress, cancel)


# ---------------------------------------------------------------------------------------------------- ffmpeg base (automático)
def ensure_base(cfg, say=None, net=None, base=None):
    """Arranca la instalación automática del ffmpeg base si no hay ningún ffmpeg y está permitido (components.ffmpeg.auto_base). Una vez
    por sesión: un fallo no se reintenta solo en bucle (el botón «Verificar actualizaciones» sí reintenta). True si arrancó."""
    if not section(cfg)["ffmpeg"].get("auto_base", False) or ft.BASE.tried:
        return False
    if ft.BASE.start(net=net, base=base, configured=cfg.get("prepare", {}).get("ffmpeg_cmd", "")):
        dl.info(SRC_FFMPEG, "ffmpeg base no encontrado: se descarga a la carpeta «ffmpeg» del programa")
        if say:
            say("Instalando ffmpeg base (solo la primera vez)…")
        return True
    return False


def base_poll(say=None):
    """Para un temporizador: cuando termina la instalación del ffmpeg base la cierra (una sola vez, la vea quien la vea): ERROR en el registro y
    la barra de estado si falló, y todo en orden si salió bien. Devuelve (error, info) en ese momento, o None."""
    res = ft.BASE.finish()
    if res is None:
        return None
    err, info = res
    if err is None:
        ok(SRC_FFMPEG)
        ver = (info or {}).get("version", "")
        dl.info(SRC_FFMPEG, f"ffmpeg base {ver} instalado y probado")
        if say:
            say(f"ffmpeg {ver} instalado.")
    elif isinstance(err, ft.Cancelled):
        dl.info(SRC_FFMPEG, "instalación del ffmpeg base cancelada")
    else:
        fail(SRC_FFMPEG, f"ffmpeg base: no se pudo instalar ({err})", network=isinstance(err, ft.NetworkError))
    return res


def describe_tool(ffmpeg_path, configured=""):
    """(versión, origen en español) del ffmpeg que se usa, para mostrarlo en «Preparar videos»."""
    if not ffmpeg_path:
        return "", ""
    here = os.path.normcase(os.path.abspath(os.path.dirname(ffmpeg_path)))
    legacy_dir = ffmpeg_dir()
    if legacy_dir and here == os.path.normcase(os.path.abspath(legacy_dir)):
        origin = "legacy"
    elif here == os.path.normcase(os.path.abspath(ft.own_dir())):
        origin = "own"
    elif configured and os.path.normcase(os.path.abspath(configured)) in (os.path.normcase(os.path.abspath(ffmpeg_path)), here):
        origin = "config"
    else:
        found = shutil.which("ffmpeg")
        origin = "path" if found and os.path.normcase(os.path.abspath(found)) == os.path.normcase(os.path.abspath(ffmpeg_path)) else "other"
    return ft.version_of(ffmpeg_path), ORIGINS.get(origin, origin)


# ---------------------------------------------------------------------------------------------------- descripciones para la ventana
ORIGINS = {"own": "carpeta propia del programa", "legacy": "Legacy Mode", "config": "la ruta de ffmpeg elegida en la configuración",
           "path": "el PATH del sistema", "other": "una carpeta habitual", "propia": "carpeta propia del programa", "programa": "junto a main.py",
           "sistema": "el PATH del sistema"}


def describe_ffmpeg(configured=""):
    """Textos (en español) del estado de ffmpeg: {"use": en uso, "own": el propio, "note": aviso extra o ""}."""
    tools = ft.resolve(configured)
    inst = ft.installed(None)
    use = (f"En uso: ffmpeg {tools.version} ({ORIGINS.get(tools.origin, tools.origin)})" if tools.ffmpeg
           else "No se encontró ffmpeg: «Preparar videos» no puede funcionar sin él")
    own = (f"ffmpeg propio instalado: {inst.get('version', '?')}" + (f" · instalado el {inst['installed']}" if inst.get("installed") else "")
           if inst else "ffmpeg propio: no instalado")
    note = ""
    if inst and tools.ffmpeg and tools.origin not in ("own", "legacy"):
        note = f"Se usa {ORIGINS.get(tools.origin, tools.origin)} en vez del propio."
    return {"use": use, "own": own, "note": note, "tools": tools, "installed": inst}


def describe_mpv(mpv_version=""):
    """Textos (en español) del estado de libmpv."""
    path, origin = find_libmpv()
    inst, pend, prev = mpv_tools.installed(), mpv_tools.pending(), mpv_tools.previous()
    use = (f"En uso: {path} ({ORIGINS.get(origin, origin)})" + (f" · {mpv_version}" if mpv_version else "")) if path else \
        "No se encontró la libmpv que usa el programa"
    own = (f"libmpv propia instalada: {inst.get('version') or '?'}" + (f" · mpv {inst['mpv']}" if inst.get("mpv") else "")) if inst else "libmpv propia: no instalada"
    extra = []
    if pend:
        extra.append(f"Preparada para el próximo inicio: {pend.get('version') or '?'}")
    if prev:
        extra.append(f"Versión anterior guardada: {prev.get('version') or '?'}")
    return {"use": use, "own": own, "extra": extra, "installed": inst, "pending": pend, "previous": prev, "origin": origin}


def describe_legacy():
    """Texto (en español) del paquete Legacy: {"text": …, "available": bool, "current": bool}."""
    if not legacy.available():
        return {"text": "Aún no hay repositorios Legacy guardados: se guardan solos en el primer inicio correcto de esta versión.",
                "available": False, "current": False}
    m = legacy.manifest()
    parts = [f"{NAMES.get(SRC_FFMPEG if k == 'ffmpeg' else SRC_MPV, k)} {v.get('version') or '?'}" for k, v in m["items"].items()]
    cur = legacy.is_current()
    return {"text": f"Paquete Legacy de {m.get('app', '?')} (creado el {m.get('created', '?')}): {', '.join(parts)}"
                    + ("" if cur else ". Es de otra versión del programa: se renueva solo."), "available": True, "current": cur}


def legacy_error(text, log=None):
    """No se pudo guardar el paquete Legacy: error en el registro y la barra de estado (sin el aviso de «Activar Legacy Mode»: no es un fallo de
    un repositorio)."""
    (log or dl).error(SRC_LEGACY, f"Legacy: {text}")


def legacy_ok(log=None):
    try:
        (log or dl).devlog.resolve(SRC_LEGACY)
    except AttributeError:
        pass


def is_libmpv_failure(exc):
    """¿Esta excepción del reproductor es culpa de la libmpv (no carga, falta una función, versión incompatible…)? No lo son un python-mpv
    antiguo ni los problemas de OpenGL de la pantalla."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if "pip install" in text or "opengl" in text or "autoprueba" in text:
        return False
    return isinstance(exc, (OSError, AttributeError, ImportError)) or "libmpv" in text or "mpv-2" in text
