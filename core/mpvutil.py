# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Utilidades comunes de mpv: carga de archivos a prueba de versiones, registro de mensajes y estado para el panel."""
import os
import threading
import time
from collections import deque

from core import devlog as dl

TEST_SOURCE = "av://lavfi:testsrc2=size=1280x720:rate=30"      # patrón de prueba generado por mpv (sin archivos)
LOG_FILE = "mpv.log"


def open_file(m, path, start=0.0):
    """Carga `path` en mpv y devuelve True si `start` quedó fijado.

    No se usa `loadfile(path, "replace", start=…)`: el 3.er argumento de `loadfile` cambió en mpv 0.38 (ahora es un
    índice y las opciones pasaron al 4.º), y según la combinación python-mpv/libmpv el comando falla sin cargar nada
    (pantalla negra). Aquí `start` se fija como opción y `loadfile` va solo con (archivo, modo), igual en todas las
    versiones."""
    ok = True
    try:
        m["start"] = f"{max(0.0, float(start)):.3f}"
    except Exception:
        ok = False
    m.command("loadfile", str(path), "replace")
    return ok


def seek_exact(m, t):
    """Salta a `t` s (posición absoluta en el archivo) con un salto PRECISO: mpv decodifica desde el keyframe anterior hasta `t` y solo
    muestra desde ahí. El salto «normal» (`m.seek(t, reference="absolute")`) puede aterrizar en el keyframe anterior, a varios segundos
    de lo pedido en videos con keyframes espaciados: la sincronía no lograba alcanzar al track (cada corrección volvía al mismo
    keyframe). Con la bandera «exact» en el propio comando funciona igual en todas las versiones de mpv/python-mpv."""
    t = max(0.0, float(t))
    try:
        m.command("seek", f"{t:.3f}", "absolute+exact")
    except Exception:                                   # versión o doble que no acepta ese comando: el método de python-mpv
        m.seek(t, reference="absolute", precision="exact")


class MpvLog:
    """Recoge los mensajes de libmpv: los guarda en `mpv.log` y recuerda los últimos errores y avisos que indican un problema
    real (el ruido habitual de mpv no cuenta)."""

    def __init__(self, tag, path=LOG_FILE):
        self.tag, self._lines, self._lock = tag, deque(maxlen=30), threading.Lock()
        self._f = None
        try:
            big = os.path.exists(path) and os.path.getsize(path) > 2_000_000      # no crece sin límite
            self._f = open(path, "w" if big else "a", encoding="utf-8", buffering=1)
            self._f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} · {tag} =====\n")
        except Exception:
            self._f = None

    def handler(self, level, prefix, text):
        """log_handler de python-mpv (se llama desde el hilo de eventos de mpv)."""
        line = f"[{self.tag}] {level} {prefix}: {str(text).rstrip()}"
        if self._f:
            try:
                self._f.write(line + "\n")
            except Exception:
                pass
        if level in ("fatal", "error", "warn"):
            msg = f"{level} {prefix}: {str(text).rstrip()}"
            lv = dl.WARN if level == "warn" else dl.ERROR
            # Un aviso inofensivo de mpv (p. ej. «No key binding found for key 'MBTN_LEFT'» al hacer clic en su ventana) ni
            # se marca con ⚠ ni va al registro como error; solo queda como contexto por si luego ocurre un error real.
            # `mpv.log` conserva todo el detalle de todos modos.
            if lv == dl.ERROR or dl.significant(msg):
                with self._lock:
                    self._lines.append(line)
            dl.external(f"mpv/{self.tag}", msg, lv)

    def clear(self):
        """Se va a cargar otro video: el aviso anterior ya no vale (si sigue pasando, mpv lo vuelve a avisar y se vuelve a mostrar).
        Así un aviso de mpv dura hasta la siguiente carga y no se queda marcado toda la sesión."""
        with self._lock:
            self._lines.clear()

    def problem(self):
        """Último aviso/error de mpv (cadena vacía si no hubo)."""
        with self._lock:
            return self._lines[-1][len(self.tag) + 3:] if self._lines else ""


def same_path(a, b):
    """¿Son el mismo archivo/URL? (tolera URLs como av://lavfi:… que no son rutas válidas de Windows)."""
    if not a or not b:
        return False
    a, b = str(a), str(b)
    if a == b:
        return True
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return False


def get(m, name, default=None):
    """Lee una propiedad de mpv sin que un error rompa el panel."""
    try:
        v = getattr(m, name)
        return default if v is None else v
    except Exception:
        return default


def describe(m):
    """Estado real de mpv en una línea: qué cargó, si avanza, decodificación y tamaño del video."""
    path = get(m, "path")
    if not path:
        return "sin archivo cargado"
    vp = get(m, "video_params") or get(m, "video_out_params") or {}
    size = f"{vp.get('w')}×{vp.get('h')}" if isinstance(vp, dict) and vp.get("w") else "sin imagen de video"
    t = get(m, "time_pos")
    state = "en pausa" if get(m, "pause") else ("avanza" if t is not None else "cargando")
    parts = [os.path.basename(str(path)), size, state + (f" t={t:.1f}s" if isinstance(t, (int, float)) else ""),
             f"hwdec={get(m, 'hwdec_current') or 'no'}", f"vid={get(m, 'vid')}"]
    return " · ".join(str(p) for p in parts)


def health_sample(m, path, loading=False):
    """Lo que mpv sabe del video que suena, para core/videohealth.py (todas las lecturas toleran que mpv no responda)."""
    if loading or not path:
        return None
    vp = get(m, "video_params") or get(m, "video_out_params") or {}
    return {"path": get(m, "path") or path, "loading": False,
            "time_pos": get(m, "time_pos"), "pause": bool(get(m, "pause", False)), "eof": bool(get(m, "eof_reached", False)),
            "cache_wait": bool(get(m, "paused_for_cache", False)), "speed": get(m, "speed", 1.0),
            "container_fps": get(m, "container_fps"), "vf_fps": get(m, "estimated_vf_fps"),
            "drops_dec": get(m, "decoder_frame_drop_count", 0), "drops_vo": get(m, "frame_drop_count", 0),
            "params": vp if isinstance(vp, dict) else {}, "hwdec": get(m, "hwdec_current"), "codec": get(m, "video_codec")}


def seeking(m):
    """¿mpv está en pleno salto? (None si no lo dice)."""
    v = get(m, "seeking")
    return None if v is None else bool(v)
