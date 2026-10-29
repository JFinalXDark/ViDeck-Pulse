# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Motor de video: python-mpv (libmpv), con fundido a negro al cambiar de visual."""
import locale
import os
import threading
import time

import mpv

from . import devlog as dl
from . import winicon
from .version import player_title
from .mpvutil import MpvLog, TEST_SOURCE, describe, health_sample, open_file, same_path, seek_exact, seeking

RES = {"1080p": (1920, 1080), "720p": (1280, 720)}
FADE_FPS = 30


def _smooth(x):
    """Suavizado 3x²−2x³: el fundido arranca y termina sin tirones."""
    return x * x * (3 - 2 * x)




class VisualPlayer:
    def __init__(self, wid=None, hwdec="auto-safe"):
        locale.setlocale(locale.LC_NUMERIC, "C")        # requerido por libmpv
        self.log = MpvLog("clásico")                    # mensajes de mpv -> mpv.log + último aviso en el panel
        opts = dict(input_default_bindings=False, input_vo_keyboard=False, osc=False,
                    aid="no", keep_open="yes", idle="yes", force_window="yes",
                    hwdec=hwdec, cursor_autohide="1000", title=player_title(),
                    log_handler=self.log.handler, loglevel="info")
        if wid:
            opts["wid"] = str(wid)
        self.m = mpv.MPV(**opts)
        self.embedded = bool(wid)
        if not wid:                                     # ventana propia de mpv: se le pone el icono de ViDeck Pulse
            winicon.watch_mpv_window(player_title())
        self.current = None                             # visual pedido (se actualiza al instante, aunque aún se esté fundiendo)
        self.black_on_pause = True
        self.last_error = ""                            # último fallo al cargar (se muestra en el panel)
        self._playing, self._pause_t = None, 0.0
        self._shown_kind = None                         # tipo del visual cargado («sync» / «random» / «manual»)
        self._hold_black = False                        # pausa con pantalla en negro (sin tocar las pistas de mpv)
        # transición: fundido a negro -> carga del nuevo visual -> fundido de vuelta (dict compartido con la config)
        self.fade = {"enabled": True, "duration": 1.2}
        self._level = 0.0                               # 0 = imagen normal, 1 = negro
        self._shown_level = None
        self._loaded, self._loading = False, False
        self._req, self._speed_want = None, 1.0
        self._lock, self._ev, self._alive = threading.Lock(), threading.Event(), True
        self._set_level(0.0)                            # ecualizador a cero: nunca se arranca con la imagen oscurecida
        threading.Thread(target=self._worker, name="visual-fade", daemon=True).start()

    # ------------------------------------------------------------- carga con transición
    def load(self, path, start=0.0, loop=False, live=False, kind=None):
        """Cambia de visual (no bloquea). `live=True`: el video va sincronizado con un track, así que `start` se
        corrige con lo que avanza el track mientras dura el fundido de salida."""
        self.current = path
        kind = kind or ("sync" if live else "random")
        dl.info("visual", f"Cargando «{os.path.basename(str(path))}» ({kind}, "
                          f"inicio {float(start):.1f} s{', en bucle' if loop else ''})")
        with self._lock:
            self._speed_want, self._loading = 1.0, True
            self._req = (path, start, loop, live, time.monotonic(), kind)
        self._ev.set()

    def set_speed(self, f):
        with self._lock:
            self._speed_want = float(f)
            if not self._loading:                       # si aún se está cambiando de video, se aplica al cargarlo
                self._speed_now()

    def _speed_now(self):
        try:
            self.m.speed = self._speed_want
        except Exception:
            pass

    def _half(self):
        """Duración de cada mitad del fundido (salida y entrada), en segundos."""
        if not self.fade.get("enabled", True):
            return 0.0
        return max(0.0, float(self.fade.get("duration", 0.0))) / 2

    def _visible_mode(self):
        """¿La salida muestra imagen (no está en pausa con negro)?"""
        return bool(self._playing) or not self.black_on_pause

    def _shown(self):
        """¿Hay un visual cargado y visible ahora mismo (merece la pena fundirlo)?"""
        return self._loaded and self._visible_mode()

    def _worker(self):
        while self._alive:
            self._ev.wait()
            with self._lock:
                self._ev.clear()
                req, self._req = self._req, None
            if req is None:
                continue
            path, start, loop, live, t_req, kind = req
            try:
                half = 0.0 if (kind == "random" and self._shown_kind == "random") else self._half()      # Random → Random: corte
                if half and self._shown():
                    self._ramp(1.0, half)               # el visual anterior sigue avanzando mientras se funde a negro
                    if self._req is not None:           # llegó otro cambio: este se descarta (ya está en negro)
                        continue
                elif half and self._visible_mode():
                    self._set_level(1.0)                # primer visual (o tras vaciar la salida): aparece desde negro
                skew = (time.monotonic() - t_req) * self._speed_want if live else 0.0
                begin = max(0.0, start + skew)
                self.m.loop_file = "inf" if loop else "no"
                self._apply_pause(force=True)           # con keep_open, al terminar un video mpv queda en pausa y la arrastra
                self.log.clear()                        # el aviso de mpv anterior ya no vale: si sigue, vuelve a avisar
                start_set = open_file(self.m, path, begin)
                self._loaded, self._shown_kind = True, kind
                with self._lock:
                    self._speed_now()
                    self._loading = self._req is not None
                ok = self._wait_loaded(path, 3.0)       # también verifica que mpv de verdad cargó el archivo
                if self._req is None:
                    self.last_error = "" if ok else self._load_problem(path)
                if ok:
                    dl.devlog.resolve("reproductor")        # un video cargó bien: los errores anteriores del reproductor quedaron solucionados
                if ok and not start_set and begin > 0:
                    seek_exact(self.m, begin)
                self._apply_pause(force=True)
                self._ramp(0.0, half, interruptible=True)
            except Exception as e:
                print("Transición:", e)
                dl.exception("reproductor", "falló la transición de visual")
                self.last_error = f"{type(e).__name__}: {e}"
                self._set_level(0.0)
                with self._lock:
                    self._loading = self._req is not None

    def _load_problem(self, path):
        why = self.log.problem()
        return f"mpv no cargó «{os.path.basename(path)}»" + (f" ({why})" if why else "")

    def _wait_loaded(self, path, timeout=2.0):
        """True cuando mpv ya tiene el archivo y mostró su primer fotograma (False si pasa el tiempo o llega otro cambio)."""
        t0 = time.monotonic()
        while self._alive and self._req is None and time.monotonic() - t0 < timeout:
            try:
                if same_path(self.m.path, path) and self.m.time_pos is not None:
                    return True
            except Exception:
                pass
            time.sleep(0.02)
        return False

    def _ramp(self, target, dur, interruptible=False):
        a = self._level
        steps = int(dur * abs(target - a) * FADE_FPS)
        for i in range(1, steps + 1):
            if not self._alive or (interruptible and self._req is not None):
                return
            self._set_level(a + (target - a) * _smooth(i / steps))
            time.sleep(1.0 / FADE_FPS)
        self._set_level(target if not (interruptible and self._req is not None) else self._level)

    def _set_level(self, v):
        """Nivel de negro 0..1 con el ecualizador de mpv: contraste (escala la imagen hacia negro) y brillo (en el
        tramo final, para asegurar un negro puro aunque el contraste pivote sobre el gris). Con la pausa en negro
        activa, la salida queda en negro sea cual sea el nivel del fundido."""
        self._level = v = min(1.0, max(0.0, v))
        self._push_level()

    def _push_level(self):
        v = 1.0 if self._hold_black else self._level
        if v == self._shown_level:
            return
        self._shown_level = v
        for prop, val in (("contrast", int(round(-100 * v))), ("brightness", int(round(-100 * v ** 3)))):
            try:
                self.m[prop] = val
            except Exception:
                pass

    # ------------------------------------------------------------------ resto
    def release_files(self):
        """Suelta el video abierto (Windows no deja reemplazar ni eliminar un archivo abierto): traspaso directo de «Preparar videos»."""
        try:
            self.m.command("stop")
            self._loaded, self._playing = False, None
        except Exception:                                    # noqa: BLE001
            pass
        self.current = None

    def set_playing(self, on):
        """Reproduce o pausa. Con `black_on_pause`, la pausa deja la pantalla en negro: se hace con el ecualizador, sin
        quitar la pista de video (con `vid=no` mpv puede cargar el archivo sin pistas, darlo por terminado y dejar la
        salida en negro al volver a reproducir)."""
        if on == self._playing:
            self._apply_pause()                         # por si mpv se quedó en pausa solo (fin de un video)
            return
        self._playing = on
        self._apply_pause(force=True)
        self._hold_black = bool(not on and self.black_on_pause)
        self._push_level()

    def _apply_pause(self, force=False):
        """Deja la pausa de mpv como la pide el motor. Con `keep_open`, al llegar al final de un video mpv se pone en
        pausa por su cuenta y esa pausa sigue activa al cargar el siguiente visual: el motor creía que estaba reproduciendo
        y el visual Random se quedaba congelado en el primer fotograma. Si el video terminó de verdad (eof) no se toca."""
        if self._playing is None:
            return
        now = time.monotonic()
        if not force and (self._loading or now - self._pause_t < 0.4):
            return
        self._pause_t = now
        want = not self._playing
        try:
            if bool(self.m.pause) != want and (force or not self._at_eof()):
                self.m.pause = want
        except Exception as e:
            dl.exception("reproductor", "no se pudo cambiar la pausa de mpv")
            self.last_error = f"pausa: {e}"

    def _at_eof(self):
        try:
            return bool(self.m.eof_reached)
        except Exception:
            return False

    def position(self):
        if self._loading:
            return None                         # mientras se funde/carga no hay posición fiable
        try:
            return self.m.time_pos
        except Exception:
            return None

    def seek(self, t):
        if self._loading:
            return
        try:
            seek_exact(self.m, t)
        except Exception:
            pass

    def health_sample(self):
        """Datos de mpv para vigilar el video que suena (core/videohealth.py); None mientras se carga o se funde."""
        return health_sample(self.m, self.current, self._loading)

    def seeking(self):
        """¿mpv sigue saltando? (el motor mide así lo que tarda cada salto de la sincronía; None si no se sabe)."""
        return seeking(self.m)

    def apply_window_mode(self, mode):
        """Modo de la ventana propia de mpv (cuando no va incrustada): «window», «borderless» (sin bordes) o «fullscreen»."""
        try:
            self.m.border = (mode != "borderless")
            self.m.fullscreen = (mode == "fullscreen")
        except Exception as e:
            dl.exception("reproductor", f"No se pudo cambiar el modo de la ventana de mpv ({mode}): {e}", level=dl.WARN)

    def apply_output(self, res):
        """Resolución de salida = tamaño de la ventana del visor (1080p → 1920×1080, 720p → 1280×720; ver OutputWindow).
        No se añade ningún filtro de escala: con la decodificación por GPU (d3d11va) un filtro por software falla
        («vf: Disabling filter…») y obligaría a bajar los fotogramas de la GPU; mpv ya escala a la ventana. Solo se
        limpia cualquier filtro que hubiera. Los fps los marca cada video."""
        try:
            self.m.command("vf", "clr", "")
        except Exception as e:
            print("No se pudo limpiar el filtro de salida:", e)
            dl.error("reproductor", f"No se pudo limpiar el filtro de salida: {e}")

    def test_pattern(self, on=True):
        """Patrón de prueba de mpv en la salida (sin motor ni archivos): sirve para separar «el Reproductor no
        muestra nada» de «el video/motor no carga»."""
        try:
            if on:
                self._req, self._loading, self._loaded = None, False, True
                self.current, self._playing, self._hold_black = None, True, False
                self._set_level(0.0)
                self.m.loop_file = "inf"
                self.m["start"] = "0"
                self.m.pause = False
                self.m.command("loadfile", TEST_SOURCE, "replace")
            else:
                self.m.command("stop")
                self._loaded, self._playing = False, None
        except Exception as e:
            dl.exception("reproductor", "falló la prueba de salida")
            self.last_error = f"prueba de salida: {e}"

    def status(self):
        """Estado real de mpv para el panel: qué tiene cargado, si avanza, decodificación y último aviso."""
        modo = "mpv incrustado" if self.embedded else "mpv en su propia ventana"
        txt = f"{modo} · {describe(self.m)}"
        prob = self.last_error or self.log.problem()
        note = getattr(self, "note", "")                     # p. ej. por qué no se pudo usar el fundido cruzado
        return txt + (f" · ⚠ {prob}" if prob else "") + (f" · ⚠ {note}" if note else "")

    def shutdown(self):
        self._alive = False
        self._ev.set()
        try:
            self.m.terminate()
        except Exception:
            pass
