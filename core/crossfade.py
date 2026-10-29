# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproductor con fundido cruzado real: dos instancias de libmpv que se mezclan en la GPU (ver gui/glview.py).

El visual siguiente se carga DETRÁS (ya en reproducción) y el actual se va volviendo transparente hasta dejarlo ver.
Misma interfaz que core/player.py (VisualPlayer), de modo que el motor no distingue cuál se usa."""
import locale
import os
import threading
import time

import mpv

from . import devlog as dl
from .mpvutil import MpvLog, TEST_SOURCE, describe, health_sample, open_file, same_path, seek_exact, seeking

RES = {"1080p": (1920, 1080), "720p": (1280, 720)}
FIRST_FADE = 1.0                  # s: el primer visual (o el que sale de una pantalla en negro) aparece desde negro
READY_TIMEOUT = 6.0               # s: máximo esperando a que el visual nuevo cargue (el fundido no empieza antes: sería negro)
FRAME_GRACE = 0.5                 # s: máximo esperando a que su primer fotograma esté ya dibujado en su textura


class Layer:
    """Una instancia de mpv que dibuja en una textura mediante la API de render de libmpv."""

    def __init__(self, index, hwdec="auto-safe"):
        locale.setlocale(locale.LC_NUMERIC, "C")                   # requerido por libmpv
        self.index, self.ctx, self.path, self._seek = index, None, None, None
        self.log = MpvLog(f"capa{index}")
        self.m = mpv.MPV(vo="libmpv", hwdec=hwdec, aid="no", keep_open="yes", idle="yes",
                         input_default_bindings=False, input_vo_keyboard=False, osc=False,
                         log_handler=self.log.handler, loglevel="info")

    def load(self, path, start, loop, pause):
        self.path = path
        self.m.speed = 1.0
        self.m.loop_file = "inf" if loop else "no"
        self.m.pause = bool(pause)
        # `loadfile(path, "replace", start=…)` falla según la versión de mpv/python-mpv (ver core/mpvutil.py)
        self.log.clear()                                           # el aviso de mpv anterior ya no vale: si sigue, vuelve a avisar
        self._seek = None if open_file(self.m, path, start) else max(0.0, start)

    def ready(self):
        """¿El archivo pedido ya está cargado y mostró su primer fotograma?"""
        try:
            ok = same_path(self.m.path, self.path) and self.m.time_pos is not None
            if ok and self._seek:                                  # no se pudo fijar `start` antes de cargar
                seek_exact(self.m, self._seek)
                self._seek = None
            return ok
        except Exception:
            return False

    def stop(self):
        try:
            self.m.pause = True
            self.m.command("stop")
        except Exception:
            pass
        self.path = None

    def free(self):
        try:
            self.m.terminate()
        except Exception:
            pass


class CrossfadePlayer:
    def __init__(self, view, hwdec="auto-safe"):
        self.view = view                                # gui.glview.GlVideoWidget (o un doble en las pruebas)
        self.layers = [Layer(0, hwdec), Layer(1, hwdec)]
        view.attach(self.layers)
        self.current = None                             # visual pedido (se actualiza al instante)
        self.black_on_pause = True
        # dict compartido con la config: enabled, duration (visual de un track), random_duration (visual random)
        self.fade = {"enabled": True, "duration": 2.0, "random_duration": 5.0}
        self._shown_kind = None                         # tipo del visual que se está mostrando («sync» / «random» / «manual»)
        self._playing, self._cur = None, 0              # _cur: capa del visual actual (la que controla el motor)
        self._loading, self._speed_want, self._req, self._err = False, 1.0, None, ""
        self._lock, self._ev, self._alive = threading.Lock(), threading.Event(), True
        threading.Thread(target=self._worker, name="crossfade", daemon=True).start()

    # ------------------------------------------------------------------ carga
    def load(self, path, start=0.0, loop=False, live=False, kind=None):
        """Pide un cambio de visual (no bloquea). kind: «sync» (visual de un track), «random» o «manual»."""
        self.current = path
        kind = kind or ("sync" if live else "random")
        dl.info("visual", f"Cargando «{os.path.basename(str(path))}» ({kind}, inicio {float(start):.1f} s{', en bucle' if loop else ''})")
        with self._lock:
            self._speed_want, self._loading = 1.0, True
            self._req = (path, start, loop, kind)
        self._ev.set()

    def _duration(self, kind):
        if not self.fade.get("enabled", True):
            return 0.0
        key = "random_duration" if kind == "random" else "duration"
        return max(0.0, float(self.fade.get(key, 0.0)))

    def _visible_mode(self):
        return bool(self._playing) or not self.black_on_pause

    def _worker(self):
        while self._alive:
            self._ev.wait()
            with self._lock:
                self._ev.clear()
                req, self._req = self._req, None
            if req is None:
                continue
            try:
                self._transition(*req)
            except Exception as e:
                print("Fundido cruzado:", e)
                dl.exception("fundido", "falló la transición")
                self._err = f"{type(e).__name__}: {e}"
                with self._lock:
                    self._loading = self._req is not None

    def _wait(self, cond, timeout):
        t0 = time.monotonic()
        while self._alive and time.monotonic() - t0 < timeout:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def _transition(self, path, start, loop, kind):
        v = self.view
        dur = self._duration(kind)
        idle = 1 - v.base if v.has_base else v.base           # capa que no se está mostrando
        lay = self.layers[idle]
        v.begin_pending(idle)                                 # el visor la dibuja ya, escondida, para que mpv avance
        lay.load(path, start, loop, pause=not self._playing)  # el nuevo visual arranca ya, escondido detrás
        ok = self._wait(lay.ready, READY_TIMEOUT)             # hasta que tenga su primer fotograma
        if ok:
            v.mark_ready(idle)
            self._wait(lambda: v.pending_fresh(idle), FRAME_GRACE)   # y esté dibujado: el fundido no parte desde negro
        if self._req is None:                                 # (si llegó otro cambio, este resultado ya no importa)
            why = lay.log.problem()
            self._err = "" if ok else (f"mpv no cargó «{os.path.basename(path)}»" + (f" ({why})" if why else ""))
            if ok:
                dl.devlog.resolve("fundido")                  # una transición salió bien: el error anterior quedó solucionado
        with self._lock:
            self._cur = idle
            self._speed_now()
            self._loading = self._req is not None
        if self._req is not None:                             # llegó otro cambio: este ni siquiera se muestra
            v.end_pending()
            return
        had_base = v.has_base
        cut = kind == "random" and self._shown_kind == "random"   # de un Random a otro Random siempre es un corte (con cualquier fundido)
        self._shown_kind = kind
        if not self._visible_mode() or (had_base and (dur <= 0 or cut)):
            v.show_base(idle)                                 # pantalla en negro, fundido desactivado o Random → Random: corte
        elif not had_base:
            v.start_fade(idle, min(dur, FIRST_FADE) if dur else 0.0)   # primer visual: aparece desde negro
        else:
            v.start_fade(idle, dur)                           # fundido cruzado: el actual se vuelve transparente
        sped, t_end = False, time.monotonic() + dur + 3.0
        while self._alive and v.fading:
            if time.monotonic() > t_end:                      # el visor no avanza el fundido (ventana sin pintar, error de GL)
                v.show_base(idle)
                break
            if self._req is not None and not sped:            # otro cambio en cola: se acelera el fundido en curso
                v.speed_up_fade(0.3)
                sped = True
            time.sleep(0.02)
        v.end_pending()
        if had_base:
            self.layers[1 - idle].stop()                      # el visual saliente libera su decodificador

    # ------------------------------------------------------------------ control
    def _speed_now(self):
        try:
            self.layers[self._cur].m.speed = self._speed_want
        except Exception:
            pass

    def set_speed(self, f):
        with self._lock:
            self._speed_want = float(f)
            if not self._loading:
                self._speed_now()

    def set_playing(self, on):
        if on == self._playing:
            return
        self._playing = on
        for lay in self.layers:
            try:
                lay.m.pause = not on
            except Exception:
                pass
        self.view.set_black(bool(not on and self.black_on_pause))

    def release_files(self):
        """Suelta todos los videos que las capas tienen abiertos: Windows no deja reemplazar ni eliminar un archivo abierto (traspaso directo de
        «Preparar videos»). Se usa con el motor detenido; la próxima carga vuelve a abrir lo que haga falta."""
        self._wait(lambda: not self._loading and not self.view.fading, 3.0)          # que termine un fundido o una carga en curso
        with self._lock:
            self._req = None
            for lay in self.layers:
                lay.stop()
            self._loading = False
        self.current = None

    def position(self):
        if self._loading:
            return None
        try:
            return self.layers[self._cur].m.time_pos
        except Exception:
            return None

    def seek(self, t):
        if self._loading:
            return
        try:
            seek_exact(self.layers[self._cur].m, t)
        except Exception:
            pass

    def health_sample(self):
        """Datos de mpv para vigilar el video que suena (core/videohealth.py); None mientras se carga o se funde."""
        if self.view.fading:
            return None
        return health_sample(self.layers[self._cur].m, self.current, self._loading)

    def seeking(self):
        """¿mpv sigue saltando? (el motor mide así lo que tarda cada salto de la sincronía; None si no se sabe)."""
        return seeking(self.layers[self._cur].m)

    @property
    def last_error(self):
        return self._err

    def status(self):
        """Diagnóstico para el panel de estado (visor OpenGL, capas, estado real de mpv, errores)."""
        lay = self.layers[self._cur]
        txt = f"{self.view.status()} · capa {self._cur}: {describe(lay.m)}"
        prob = self._err or self.layers[0].log.problem() or self.layers[1].log.problem()
        return txt + (f" · ⚠ {prob}" if prob else "")

    def test_pattern(self, on=True):
        """Patrón de prueba de mpv en la salida (sin motor ni archivos)."""
        if on:
            self.set_playing(True)
            self.load(TEST_SOURCE, loop=True, kind="manual")
            self.current = None
        else:
            self.set_playing(False)

    def apply_output(self, res):
        self.view.set_render_size(*RES[res])

    def shutdown(self):
        self._alive = False
        self._ev.set()
        try:
            self.view.shutdown()                        # libera los contextos de render (necesita el contexto GL)
        except Exception:
            pass
        for lay in self.layers:
            lay.free()
