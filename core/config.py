# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuración persistente (config.json) con valores por defecto."""
import copy
import json
import os
from pathlib import Path

from core import devlog as dl
from core import paths

CONFIG_PATH = Path("config.json")
CONFIG_VERSION = 10
_old_format = {"on": False}       # el config.json leído trae `videos_dir` en el formato de antes (absoluta dentro de la carpeta de datos)

DEFAULT_DECK = {"title": None, "bpm": None, "remaining": None, "duration": None, "pitch": None, "vu": None}

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "videos_dir": "videos",
    "db_path": "tracks_db.json",
    "language": "es",                  # idioma de la interfaz: "es" (Español) | "en" (English); se aplica al reiniciar (pestaña «Ajustes»)
    "ui": {"layout_mode": "auto",      # disposición de las ventanas: auto (adaptable al alto de la ventana) · full (Completo) · compact (Compacto);
                                       # los botones «Modo de Ventana» de «Ajustes» fijan uno a mano y se aplica al momento
           "autoscan": True},          # buscar videos nuevos solos (al iniciar, al detener el motor y al volver a la ventana con el motor detenido)
    "capture": {
        "mode": "window",              # "window" = solo la ventana de Traktor; "screen" = monitor completo
        "window_title": "traktor",     # texto que debe contener el proceso o el título de la ventana
        "backend": "printwindow",      # printwindow (por defecto: no interfiere con Discord/OBS) | wgc | auto (wgc y, si falla, printwindow)
        "boost_fps": 15,               # u14: fps de PrintWindow (normalmente 5) durante las revisiones precisas de la sincronía (0 = no subir)
        "max_fps": 15,                 # fotogramas/s que se leen de Traktor (menos = menos CPU)
        "ref": None,                   # tamaño y método con los que se calibraron los ROIs
        "profiles": {},                # ROIs ya conocidos por tamaño de captura ("printwindow:1920x1080": {deck: {roi…}}):
                                       # al pasar de ventana a pantalla completa (y volver) se recuperan al instante
        "layout": "auto",              # auto: los ROIs se detectan solos si faltan o si cambia el tamaño de la ventana
                                       # (p. ej. Traktor maximizado / a pantalla completa) · manual: solo los que calibres tú
    },
    "monitor": 1,                       # índice mss: 1 = monitor principal (donde corre Traktor)
    "dev": {"log_enabled": False,           # pestaña «Desarrollador»: registro de ERRORES en pantalla y en videck_pulse.log
            "status_bar": True,             # barra de estado siempre visible (por defecto; False: solo aparece mientras hay un mensaje)
            "show_sensors": False,          # fila «Sensores (debug)» en el Estado del Reproductor
            "advanced_player": False},      # fila «Reproductor» con todo el detalle (False: solo el nombre del archivo que suena)
    "embed_mpv": True,                       # visor de mpv incrustado en el Reproductor
    "output": {"resolution": "1080p",   # resolución de salida; los fps los marca cada video (no se fuerza ninguno)
        "window_mode": "window",        # Reproductor: window (con bordes) · borderless (sin bordes, p. ej. para Discord) · fullscreen
        "screen": 0,                    # pantalla (índice de la lista de la pestaña Salida) a la que va el modo «Pantalla completa»
        "mode_hotkey": "",              # atajo de teclado (texto de Qt, p. ej. "F9") que cambia el modo de la ventana; "" = sin atajo
               "black_on_pause": True,
               "hwdec": "auto-safe"},   # decodificación por hardware de mpv (pon "no" si el visor OpenGL da problemas)
    "prepare": {"ffmpeg_cmd": "",       # ruta a ffmpeg.exe si no está en el PATH (ventana «Preparar videos»)
                "max_height": 0},       # alto máximo de los videos preparados (0 = conservar la resolución; 1080 o 720)
    "ocr": {
        "interval": 1.0,                # segundos entre lecturas OCR (baja frecuencia)
        "tesseract_cmd": "",            # ruta a tesseract.exe si no está en PATH
        "title_threshold": 0.88,        # similitud mínima del título (tolera errores de OCR)
        "duration_tolerance": 3.0,      # segundos de tolerancia en el doble check
        "check_duration": True,
        "confirm_reads": 2,             # lecturas consecutivas iguales antes de cambiar de video
        "time_scaled_by_tempo": False,  # True si el tiempo RESTANTE de Traktor cambia con el pitch
    },
    "vu": {
        "interval": 0.1,                # 10 muestras/s
        "sat_min": 70, "val_min": 60,   # círculo ENCENDIDO = coloreado (azul…amarillo); los apagados son grises (S=0)
        "min_row_px": 2,                # píxeles coloreados que debe tener una fila para contar como encendida
        "min_lit_px": 8,                # píxeles encendidos mínimos para considerar que hay señal (≈ 1 círculo de 4×4)
        "pix_delta": 12,                # un píxel "cambió" si algún canal varía >= 12 niveles
        "change_px": 6,                 # píxeles que deben cambiar entre fotogramas = el vúmetro se mueve (≈ medio círculo)
        "recent": 0.8,                  # s: el vúmetro debe haberse movido hace menos que esto (uno congelado no cuenta)
        "window": 6, "on_samples": 2,   # activo si >=2 de las últimas 6 muestras son positivas
        "hold": 1.5,                    # segundos que se mantiene "activo" tras el último pico
    },
    "transport": {                      # reloj del deck: el tiempo restante solo avanza si el track suena
        "window": 2.5, "min_ticks": 2,  # >= 2 cambios de dígitos en 2.5 s = reproduciendo
        "tick_ratio": 0.01,             # fracción de píxeles del ROI que debe cambiar en un "tic"
    },
    "detect": {
        "signals": ["transport", "vu"],  # señales que se usan (con su ROI definido)
        "mode": "any",                   # any: el visual solo se detiene si el vúmetro está en silencio Y ningún deck
                                         #      avanza (un silencio del track no lo pausa); all: exige ambas señales
        "stall_s": 0,                    # opcional (0 = desactivado): s con el reloj quieto tras los cuales el deck se da por
                                         #    pausado aunque el vúmetro aún se mueva (colas de eco/reverb)
    },
    "transition": {                     # al cambiar de visual (archivo configurado <-> random, o de track)
        "style": "crossfade",           # crossfade: el nuevo visual se carga detrás y el actual se vuelve transparente
                                        # fade: fundido a negro y vuelta (respaldo sin OpenGL) · cut: sin transición
        "enabled": True,                # (lo fija «style»: cut = False)
        "duration": 2.0,                # s: fundido hacia el visual de un track (crossfade); en «fade», salida + entrada
        "random_duration": 5.0,         # s: fundido hacia un Random Visual (crossfade)
    },
    "random": {"change_every": 90},     # segundos; 0 = no rotar
    "autolink": {                       # vincular solo un video con el track que Traktor Pro 4 tiene cargado
        "enabled": True,
        "min_title": 0.70,              # similitud mínima entre el título del deck y el nombre del archivo de video
                                        # (además la duración del video debe coincidir con la del track: doble check)
        "fill_bpm": False,              # False: el BPM base queda PENDIENTE para escribirlo a mano (con el fader de tempo
                                        # movido el BPM mostrado no es el del track) · True: se calcula solo (BPM / (1 + tempo %))
    },
    "next": {                           # anticipación del visual del siguiente deck (con el track actual por terminar)
        "enabled": True,
        "watch": 60.0,                  # s restantes del track actual desde los que se vigila el otro deck: ¿hay un track
                                        # cargado? ¿su visual está en la lista? (doble check)
        "far_every": 2.0,               # s entre comprobaciones del deck en espera mientras quedan más de `mid_window` s
        "mid_window": 30.0,             # a partir de aquí se comprueba más seguido…
        "mid_every": 1.0,
        "fast_window": 15.0,            # …y en los últimos s, en cada ciclo: ¿ya arrancó el deck en espera?
        "lead": 10.0,                   # s restantes con los que entra el siguiente visual (el del track, o un Random)
    },
    "sync": {
        "check_every": 5.0,             # segundos entre revisiones de la posición del video (régimen normal)
        "initial_every": 3.0,           # s entre revisiones durante los primeros `initial_window` s del video
        "initial_window": 15.0,         # s tras empezar el video (o salir del pre-roll) con revisiones frecuentes
        "tempo_window": 10.0,           # s de revisiones frecuentes tras detectar un cambio de tempo
        "auto_resync": True,            # corregir solo cuando se confirma la deriva
        "resync_drift": 1.5,            # deriva (s) a partir de la cual se corrige (revisión normal, lectura de ±1 s)
        "fine_drift": 0.25,             # deriva (s) a partir de la cual se corrige en las revisiones precisas: se miden
                                        # en el instante en que cambian los dígitos del reloj de Traktor (mediana de varios cambios)
        "precise_every": 10.0,          # s entre revisiones precisas breves durante la reproducción normal (0 = solo las del inicio
        "precise_window": 5.0,          # y las de cambio de tempo); cada una dura `precise_window` s y lee el reloj en cada ciclo
        "seek_lead": 0.15,              # s: al corregir, el video se coloca esto por delante (lo que tarda el salto)
        "persist_learned": True,        # lo aprendido por video (adelanto del salto y compensación de arranque) se guarda en sync_learned.json y vale en la próxima sesión
        "adaptive_load": True,          # el punto de arranque de un video recién cargado se adelanta lo que se midió que arranca atrasado (aprendido por video)
        "adaptive_lead": True,          # el adelanto se aprende midiendo lo que tarda el salto de CADA video (parte de `seek_lead`)
        "slew": True,                   # u14: una deriva pequeña (≥ 0.05 s pero menor que `fine_drift`) se absorbe frenando/acelerando el video un 2 % un rato, sin saltar
        "trim": True,                   # ante una deriva sostenida se ajusta un poco la velocidad del video (hasta ±2 %) en vez de saltar cada vez
        "ahead": 0.10,                  # u14: s que el video se deja POR DELANTE del track (lo que se ve «perfecto» con las letras); la Δ del panel ya lo incluye
        "offset": 0.0,                  # ajuste manual (s) del video respecto a Traktor: + lo adelanta, − lo atrasa
        "preroll_tol": 0.5,             # restante > duración + esto = el track aún no empezó (pre-roll): el video espera
        "min_speed": 0.5, "max_speed": 2.0,   # límites de la velocidad del visual (factor = BPM_Traktor / base_bpm)
        "bpm_tol": 0.015,               # el BPM de Traktor se acepta al instante si cambia menos de un 1.5 %; un salto mayor
                                        # (lectura errónea del OCR o track nuevo) necesita 2 lecturas coherentes
    },
    "decks": {"A": dict(DEFAULT_DECK), "B": dict(DEFAULT_DECK)},
    "components": {                     # ffmpeg y libmpv propios (core/components.py): son datos de ESTA instalación, no ajustes de las pestañas
        "legacy": False,                # True: «Legacy Mode» (se usan los ffmpeg y libmpv guardados en legacy/legacy_repos.7z); se aplica al reiniciar
        "ffmpeg": {"auto_check": True, "last_check": 0, "skip_version": "", "auto_base": False},      # aviso al iniciar si hay un ffmpeg más nuevo (como mucho 1 vez/día); auto_base: instalar solo el ffmpeg base si falta
        "mpv": {"auto_check": True, "last_check": 0, "skip_version": ""},
    },
    "updates": {"check_on_start": True, "last_check": 0, "skip_tag": ""},   # aviso de versión nueva de ViDeck Pulse en GitHub (solo la versión estable; core/updatecheck.py)
    "firstrun": {"skip_ffmpeg": False},  # ventana «Componentes necesarios» (core/firstrun.py): True = el usuario ya dijo que no quiere ffmpeg ahora (no se insiste)
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def backup_corrupt(path):
    """Un archivo ilegible se conserva como «<nombre>.bak» (para que el siguiente guardado no lo pise)."""
    try:
        path.replace(path.with_name(path.name + ".bak"))
        return True
    except OSError:
        return False


def migrate(saved):
    """Adapta un config.json de una versión anterior a la actual (también el que se carga desde un archivo externo)."""
    ver = saved.get("config_version", 1)
    if ver < 3:                                                  # migración: umbrales de vúmetro nuevos
        saved.pop("vu", None)
        for rois in saved.get("decks", {}).values():
            rois.pop("vu_baseline", None)
            rois.pop("elapsed", None)
    if ver < 4:                                                  # los fps ya no se configuran: los marca el video
        saved.get("output", {}).pop("fps", None)
        saved.get("transition", {}).pop("duration", None)        # la duración cambió de significado
    cap = saved.get("capture")
    if ver < 5 and cap is not None and cap.get("backend", "auto") in ("auto", "wgc"):
        cap["backend"] = "printwindow"                           # WGC solo si se pide: puede chocar con Discord/OBS
    if ver < 6 and isinstance(saved.get("dev"), dict):
        saved["dev"]["status_bar"] = True                        # la barra de estado ahora viene activada por defecto (una sola vez:
                                                                 # después se respeta lo que elijas con la casilla de «Desarrollador»)
    if ver < 7 and isinstance(saved.get("sync"), dict) and saved["sync"].get("fine_drift") == 0.35:
        saved["sync"]["fine_drift"] = 0.25                       # umbral fino nuevo (la medición ahora usa la mediana de varios cambios
                                                                 # de dígito); solo si no lo habías cambiado: 0.35 era el valor inicial
    had_ui = isinstance(saved.get("ui"), dict)
    if ver < 8 and not had_ui:
        saved["ui"] = {"layout_mode": "auto"}                    # ajuste nuevo: «Modo de Ventana» (automático)
    if ver < 9 and had_ui:                                       # (solo si «ui» ya existía: a un archivo AJENO no se le inventan más ajustes,
        saved["ui"].setdefault("autoscan", True)                 # o parecería un config.json de ViDeck Pulse) ajuste nuevo de la update 13:
                                                                 # búsqueda automática de videos nuevos
    sy = saved.get("sync")
    if ver < 10 and isinstance(sy, dict) and sy.get("precise_every") == 25.0:
        sy["precise_every"] = 10.0                                # u14: las revisiones precisas breves son más seguidas (el video se pegaba
                                                                 # a Traktor solo cada 25 s); solo si no las habías cambiado
    if "capture" not in saved:                                   # config antiguo: ROIs relativos al monitor
        saved["capture"] = {"mode": "screen"}
    return saved


def load_config():
    """Lee config.json; si no existe (instalación limpia) lo crea con los valores por defecto."""
    cfg = copy.deepcopy(DEFAULTS)
    if not CONFIG_PATH.exists():
        cfg["language"] = paths.initial_language()              # instalación nueva: el idioma del instalador (o, en el portable, el de Windows)
        cfg["videos_dir"] = paths.default_videos_dir()          # «Videos» junto al programa (portable) o «<Videos de Windows>\\ViDeck Pulse\\Videos» (instalada)
        try:                                                    # la carpeta se crea sola (se ve desde el primer inicio)
            os.makedirs(cfg["videos_dir"], exist_ok=True)
        except OSError as e:
            dl.warn("config", f"no se pudo crear la carpeta de videos {cfg['videos_dir']}: {e}")
        try:
            save_config(cfg)
        except OSError as e:
            print("No se pudo crear config.json:", e)
            dl.error("config", f"No se pudo crear config.json: {e}")
        return cfg
    if CONFIG_PATH.exists():
        try:
            saved = migrate(json.loads(CONFIG_PATH.read_text("utf-8")))
            _merge(cfg, saved)
            cfg["config_version"] = CONFIG_VERSION
            vd = cfg.get("videos_dir")
            _old_format["on"] = isinstance(vd, str) and os.path.isabs(vd) and paths.inside(vd)
        except Exception as e:
            print("config.json inválido, usando valores por defecto (copia en config.json.bak):", e)
            dl.exception("config", "config.json inválido, se usan los valores por defecto (copia en config.json.bak)")
            cfg = copy.deepcopy(DEFAULTS)
            if backup_corrupt(CONFIG_PATH):
                try:
                    save_config(cfg)
                except OSError:
                    pass
    if _old_format["on"]:                                       # pasa ya al formato nuevo (rutas relativas), con su copia de seguridad
        try:
            save_config(cfg)
        except OSError as e:
            dl.warn("config", f"no se pudo pasar config.json al formato de rutas relativas: {e}")
    return cfg


def disk_view(cfg):
    """Lo que se escribe en config.json: igual que `cfg` salvo las rutas de carpetas (`videos_dir`, `db_path`), que se guardan RELATIVAS a
    la carpeta de datos si están dentro de ella (así mover o copiar la carpeta del programa no rompe nada). En memoria siguen igual."""
    out = cfg
    for k in ("videos_dir", "db_path"):
        v = cfg.get(k)
        s = paths.store(v) if isinstance(v, str) else v
        if s != v:
            out = dict(out)
            out[k] = s
    return out


def save_config(cfg):
    if _old_format["on"]:                                       # primera vez que se reescribe un archivo de la update 12 o anterior: copia de seguridad
        _old_format["on"] = False
        bak = CONFIG_PATH.with_name(CONFIG_PATH.name + ".u12.bak")
        try:
            if CONFIG_PATH.exists() and not bak.exists():
                bak.write_bytes(CONFIG_PATH.read_bytes())
        except OSError as e:
            dl.warn("config", f"no se pudo copiar config.json a {bak.name}: {e}")
    CONFIG_PATH.write_text(json.dumps(disk_view(cfg), indent=2, ensure_ascii=False), "utf-8")


# ------------------------------------------------------- restablecer / cargar ajustes
# Lo que NO es un ajuste de las pestañas y por tanto no se toca al restablecer ni al cargar un archivo: datos de ESTA
# instalación (carpeta de videos, base de datos, calibración de los ROIs, la ruta de Tesseract y la de ffmpeg, y los componentes ffmpeg/libmpv con «Legacy Mode»).
KEEP = (("videos_dir",), ("db_path",), ("language",), ("decks",), ("capture", "ref"), ("capture", "profiles"),
        ("ocr", "tesseract_cmd"), ("prepare", "ffmpeg_cmd"), ("components",), ("firstrun",), ("updates",), ("config_version",))
# Valores permitidos de los ajustes de tipo lista (un valor fuera de la lista se considera incompatible).
ENUMS = {("language",): ("es", "en"), ("ui", "layout_mode"): ("auto", "full", "compact"), ("output", "window_mode"): ("window", "borderless", "fullscreen"), ("capture", "mode"): ("window", "screen"), ("capture", "backend"): ("printwindow", "wgc", "auto"),
         ("capture", "layout"): ("auto", "manual"), ("output", "resolution"): ("1080p", "720p"),
         ("prepare", "max_height"): (0, 1080, 720), ("transition", "style"): ("crossfade", "fade", "cut"), ("detect", "mode"): ("any", "all")}


def _type_ok(default, value):
    if default is None:
        return True
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


def _walk(defaults, loaded, path, out):
    """Compara los ajustes del archivo con los de esta versión. out: listas `missing`, `wrong` y `found`."""
    for k, d in defaults.items():
        p = path + (k,)
        if p in KEEP:
            continue
        if not isinstance(loaded, dict) or k not in loaded:
            out["missing"].append(".".join(p))
        elif isinstance(d, dict):
            if isinstance(loaded[k], dict):
                _walk(d, loaded[k], p, out)
            else:
                out["wrong"].append(".".join(p))
        elif not _type_ok(d, loaded[k]) or (p in ENUMS and loaded[k] not in ENUMS[p]):
            out["wrong"].append(".".join(p))
        else:
            out["found"].append(".".join(p))


def inspect_settings(loaded):
    """Revisa un config.json externo (ya migrado). Devuelve un dict:
    recognized — parece un config.json de ViDeck Pulse; compatible — trae todos los ajustes de esta versión y bien
    escritos; missing / wrong — los ajustes que faltan o tienen un valor/tipo inválido."""
    out = {"missing": [], "wrong": [], "found": []}
    if not isinstance(loaded, dict):
        return {"recognized": False, "compatible": False, "missing": [], "wrong": []}
    _walk(DEFAULTS, loaded, (), out)
    return {"recognized": len(out["found"]) >= 3, "compatible": not out["missing"] and not out["wrong"],
            "missing": out["missing"], "wrong": out["wrong"]}


def _get(d, path):
    for k in path:
        d = d[k]
    return d


def _build(defaults, loaded, current, path):
    """Ajustes nuevos: lo compatible del archivo; lo que falte o no valga, el valor inicial de esta versión; lo de KEEP, lo actual."""
    new = {}
    for k, d in defaults.items():
        p = path + (k,)
        if p in KEEP:
            try:
                new[k] = copy.deepcopy(_get(current, p))
            except (KeyError, TypeError):
                new[k] = copy.deepcopy(d)
        elif isinstance(d, dict):
            sub = loaded.get(k) if isinstance(loaded, dict) else None
            new[k] = _build(d, sub if isinstance(sub, dict) else {}, current, p)
        elif isinstance(loaded, dict) and k in loaded and _type_ok(d, loaded[k]) and (p not in ENUMS or loaded[k] in ENUMS[p]):
            new[k] = copy.deepcopy(loaded[k])
        else:
            new[k] = copy.deepcopy(d)
    try:                                                         # claves que esta versión no define: se dejan como estaban
        for k, v in _get(current, path).items():
            if k not in defaults:
                new[k] = copy.deepcopy(v)
    except (KeyError, TypeError, AttributeError):
        pass
    return new


def build_settings(current, loaded=None):
    """Config completo con los ajustes de `loaded` (compatibles) o, sin `loaded`, los valores por defecto de esta versión."""
    return _build(DEFAULTS, loaded if isinstance(loaded, dict) else {}, current, ())


def replace_inplace(dst, src, path=()):
    """Reemplaza el contenido de `dst` por el de `src` SIN cambiar los objetos dict que ya existen: el motor y el reproductor
    guardan referencias a secciones del config (p. ej. `transition`) y deben ver los cambios. Lo de KEEP no se toca."""
    for k in [k for k in dst if k not in src]:
        if path + (k,) not in KEEP:
            del dst[k]
    for k, v in src.items():
        p = path + (k,)
        if p in KEEP:
            continue
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            replace_inplace(dst[k], v, p)
        else:
            dst[k] = copy.deepcopy(v)


def apply_settings(cfg, new):
    replace_inplace(cfg, new)
    cfg["config_version"] = CONFIG_VERSION
