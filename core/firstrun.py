# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Primer inicio (update 14): qué componentes externos faltan, cómo conseguirlos y dejarlos listos ANTES de abrir la ventana principal.

ViDeck Pulse no trae dentro libmpv, Tesseract ni ffmpeg (son programas de otros, con sus propias licencias). En un equipo limpio, sin ellos,
el programa ni siquiera puede importar el reproductor, así que `main.py` abre primero la ventana «Componentes necesarios» (gui/firstrun_dialog.py)
y solo cuando todo lo imprescindible está listo carga la ventana principal.

Aquí está la lógica sin Qt (se prueba entera):
  · `rows(cfg)` / `needs_window(cfg)`: el estado de cada componente y si hay que mostrar la ventana;
  · `Installer`: instala los elegidos uno tras otro en un hilo, con progreso por componente, cancelable;
  · `use_file(...)`: «Usar un archivo que ya descargué» (verifica el archivo antes de aceptarlo);
  · variables de entorno de PRUEBA, para ver la ventana en un equipo que ya lo tiene todo sin tocar nada:
        VP_FIRSTRUN=force                      muestra la ventana aunque no falte nada
        VP_FIRSTRUN=fake:libmpv,tesseract,ffmpeg   hace como si faltaran esos componentes
        VP_FIRSTRUN_DRY=1                      las «instalaciones» son simuladas: no se descarga ni se instala nada
"""
import os
import threading
import time

from . import components as comp
from . import ffmpeg_tools as ft
from . import mpv_tools as mt
from . import tesseract_tools as tt

KEYS = ("tesseract", "libmpv", "ffmpeg")        # en este orden se instalan: Tesseract primero, porque es el que pide permiso de administrador
REQUIRED = ("libmpv", "tesseract")              # sin ellos el programa no puede funcionar
ENV, ENV_DRY = "VP_FIRSTRUN", "VP_FIRSTRUN_DRY"

INFO = {
    "libmpv": {"name": "libmpv (motor de video mpv)",
               "purpose": "Reproduce los videos de los decks y hace los fundidos entre ellos.",
               "license": "GPL-2.0 o posterior / LGPL-2.1 o posterior, según la compilación (mpv)",
               "source": "GitHub: shinchiro/mpv-winbuild-cmake",
               "page": "https://github.com/shinchiro/mpv-winbuild-cmake/releases",
               "size": 32_000_000, "required": True, "file": "libmpv-2.dll"},
    "tesseract": {"name": "Tesseract OCR",
                  "purpose": "Lee en la pantalla de Traktor el título, el BPM y los tiempos de cada pista.",
                  "license": "Apache-2.0",
                  "source": "Universität Mannheim (instalador oficial para Windows)",
                  "page": tt.PAGE,
                  "size": tt.TESTED.size, "required": True, "file": "tesseract.exe"},
    "ffmpeg": {"name": "ffmpeg",
               "purpose": "Convierte («Preparar videos») tus videos para que se reproduzcan sin tirones. Se puede instalar más tarde.",
               "license": "GPL-3.0 o posterior (compilación «essentials» de gyan.dev)",
               "source": "GitHub: GyanD/codexffmpeg (gyan.dev)",
               "page": "https://www.gyan.dev/ffmpeg/builds/",
               "size": 115_000_000, "required": False, "file": "ffmpeg.exe"},
}


# ---------------------------------------------------------------------------------------------------- variables de prueba
def _env():
    return os.environ.get(ENV, "").strip().lower()


def forced():
    return _env() == "force" or _env().startswith("fake:")


def fake_missing():
    v = _env()
    return {k.strip() for k in v[5:].split(",") if k.strip()} if v.startswith("fake:") else set()


def dry():
    return os.environ.get(ENV_DRY, "") not in ("", "0")


_dry_done = set()          # (solo simulación) lo «instalado» en esta sesión


# ---------------------------------------------------------------------------------------------------- estado
def _probe(key, cfg):
    """{"ok", "version", "path"} de un componente en este equipo."""
    if key in _dry_done:                                                 # (solo simulación) ya «instalado» en esta sesión
        return {"ok": True, "version": "simulada", "path": "(simulado)"}
    if key in fake_missing():
        return {"ok": False, "version": "", "path": ""}
    if key == "libmpv":
        path, _origin = comp.find_libmpv()
        inst = mt.installed() or {}
        return {"ok": bool(path), "version": str(inst.get("mpv") or inst.get("version") or ""), "path": path}
    if key == "tesseract":
        found = tt.installed((cfg.get("ocr") or {}).get("tesseract_cmd", ""))
        return {"ok": bool(found), "version": (found or {}).get("version", ""), "path": (found or {}).get("path", "")}
    tools = ft.resolve((cfg.get("prepare") or {}).get("ffmpeg_cmd", ""))
    return {"ok": bool(tools.ffmpeg), "version": tools.version if tools.ffmpeg else "", "path": tools.ffmpeg or ""}


def rows(cfg):
    """Una fila por componente: la descripción fija (INFO) y su estado actual."""
    out = []
    for key in KEYS:
        r = dict(INFO[key])
        r.update(key=key, **_probe(key, cfg))
        out.append(r)
    return out


def needs_window(cfg, current=None):
    """¿Hay que mostrar la ventana? Sí si falta algo imprescindible; si solo falta ffmpeg, solo la primera vez (si el usuario ya dijo que no, no se insiste)."""
    if forced():
        return True
    cur = current if current is not None else rows(cfg)
    miss = {r["key"] for r in cur if not r["ok"]}
    if miss & set(REQUIRED):
        return True
    return "ffmpeg" in miss and not (cfg.get("firstrun") or {}).get("skip_ffmpeg", False)


def missing_required(cfg, current=None):
    cur = current if current is not None else rows(cfg)
    return [r["key"] for r in cur if r["key"] in REQUIRED and not r["ok"]]


def default_selection(cfg, current=None):
    """Lo que viene marcado: todo lo que falta (ffmpeg también, salvo que el usuario ya lo haya rechazado)."""
    cur = current if current is not None else rows(cfg)
    skip = (cfg.get("firstrun") or {}).get("skip_ffmpeg", False)
    return [r["key"] for r in cur if not r["ok"] and not (r["key"] == "ffmpeg" and skip)]


def total_size(keys):
    return sum(INFO[k]["size"] for k in keys)


def remember_choice(cfg, keys_selected, current=None):
    """Si ffmpeg faltaba y el usuario no lo marcó, se guarda para no volver a mostrar la ventana solo por él."""
    cur = current if current is not None else rows(cfg)
    ff = next((r for r in cur if r["key"] == "ffmpeg"), None)
    sec = cfg.setdefault("firstrun", {})
    sec["skip_ffmpeg"] = bool(ff and not ff["ok"] and "ffmpeg" not in keys_selected)


# ---------------------------------------------------------------------------------------------------- instalación
class Installer:
    """Instala los componentes elegidos, uno tras otro, en un hilo. `snapshot()` (para un temporizador) devuelve el estado de cada uno:
    {"state": "waiting|running|ok|error|cancelled", "phase", "done", "total", "error", "info"}."""

    def __init__(self, keys, cfg=None, net=None, installers=None):
        self.keys = [k for k in KEYS if k in keys]
        self.cfg, self.net = cfg or {}, net
        self._lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.state = {k: {"state": "waiting", "phase": "", "done": 0, "total": 0, "error": "", "info": {}} for k in self.keys}
        self.installers = installers or {"tesseract": self._tesseract, "libmpv": self._libmpv, "ffmpeg": self._ffmpeg}
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def cancel(self):
        self.cancel_event.set()

    def finished(self):
        return self.thread is not None and not self.thread.is_alive()

    def snapshot(self):
        with self._lock:
            return {k: dict(v) for k, v in self.state.items()}

    def _set(self, key, **kw):
        with self._lock:
            self.state[key].update(kw)

    def _run(self):
        for key in self.keys:
            if self.cancel_event.is_set():
                self._set(key, state="cancelled")
                continue
            self._set(key, state="running", phase="latest")

            def progress(phase, done, total, _key=key):
                self._set(_key, phase=phase, done=done, total=total)

            try:
                if dry():
                    info = _fake_install(key, progress, self.cancel_event)
                else:
                    info = self.installers[key](progress)
                self._set(key, state="ok", info=info or {}, phase="", done=0, total=0)
                if dry():
                    _dry_done.add(key)
            except ft.Cancelled:
                self._set(key, state="cancelled", phase="")
            except tt.ElevationDenied as e:
                self._set(key, state="error", error=str(e), phase="")
            except Exception as e:                                       # noqa: BLE001: el motivo se muestra en la fila
                self._set(key, state="error", error=str(e) or type(e).__name__, phase="", network=isinstance(e, ft.NetworkError))

    # -- los tres instaladores reales (los mismos que usan «Componentes» y la sonda de GitHub)
    def _net(self):
        return self.net or ft.Net()

    def _tesseract(self, progress):
        return tt.install(None, self._net(), progress, self.cancel_event)

    def _libmpv(self, progress):
        net = self._net()
        rel = mt.latest(net)
        info = mt.stage(rel, net, progress, self.cancel_event)
        events, ok = comp.adopt_new_mpv()
        if not ok:
            raise ft.FfmpegError("libmpv se descargó, pero no quedó activa: " + "; ".join(e[1] for e in events if e[0] in ("ERROR", "WARN")))
        return info

    def _ffmpeg(self, progress):
        net = self._net()
        rel = ft.latest(net)
        return ft.install(rel, net, progress, self.cancel_event)


def _fake_install(key, progress, cancel):
    """Instalación SIMULADA (VP_FIRSTRUN_DRY): recorre las fases con esperas cortas y no toca nada."""
    steps = [("download", i * 10, 100) for i in range(0, 11)] + [("verify", 0, 0), ("install" if key == "tesseract" else "extract", 0, 0), ("check", 0, 0)]
    for phase, done, total in steps:
        if cancel.is_set():
            raise ft.Cancelled("cancelado")
        progress(phase, done, total)
        time.sleep(0.12)
    return {"version": "simulada", "dry": True}


# ---------------------------------------------------------------------------------------------------- «Usar un archivo que ya descargué»
def use_file(key, path, cfg, progress=None):
    """Acepta un archivo/carpeta que el usuario ya tenía: lo verifica de verdad y lo deja configurado. Devuelve {"version", "path"} o lanza
    FfmpegError/VerifyError con el motivo (en español)."""
    path = (path or "").strip().strip('"')
    if not path or not os.path.exists(path):
        raise ft.VerifyError("ese archivo o carpeta no existe")
    if key == "tesseract":
        found = tt.use_existing(path)
        cfg.setdefault("ocr", {})["tesseract_cmd"] = found["path"]
        return found
    if key == "ffmpeg":
        exe = path
        if os.path.isdir(path):
            exe = next((c for c in (os.path.join(path, ft.exe("ffmpeg")), os.path.join(path, "bin", ft.exe("ffmpeg"))) if os.path.isfile(c)), "")
        if not exe or os.path.splitext(os.path.basename(exe))[0].lower() != "ffmpeg":
            raise ft.VerifyError("el archivo elegido no es ffmpeg.exe")
        ver = ft.version_of(exe)
        if not ver:
            raise ft.VerifyError("ese ffmpeg.exe no arranca")
        cfg.setdefault("prepare", {})["ffmpeg_cmd"] = exe
        return {"version": ver, "path": exe}
    info = mt.stage_local(path, progress)                                   # libmpv
    events, ok = comp.adopt_new_mpv()
    if not ok:
        raise ft.FfmpegError("la libmpv elegida no quedó activa: " + "; ".join(e[1] for e in events if e[0] in ("ERROR", "WARN")))
    return {"version": info.get("mpv") or info.get("version", ""), "path": mt.dll_path()}
