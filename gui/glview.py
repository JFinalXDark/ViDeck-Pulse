# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Visor OpenGL: mezcla dos videos de libmpv (render API) con transparencia, a la frecuencia de la pantalla (60 Hz).

Incluye una autoprueba (selftest) que comprueba que lo que mpv dibuja llega de verdad a la pantalla, y datos de
diagnóstico (status) para el panel de estado."""
import ctypes
import os
import threading
import time

import mpv
import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QMatrix4x4, QOpenGLContext, QSurfaceFormat
from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLTextureBlitter
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from core import devlog as dl

GL_FRAMEBUFFER, GL_BLEND, GL_SCISSOR_TEST, GL_DEPTH_TEST = 0x8D40, 0x0BE2, 0x0C11, 0x0B71
GL_COLOR_BUFFER_BIT, GL_TEXTURE_2D, GL_LINEAR, GL_RGBA, GL_UNSIGNED_BYTE = 0x4000, 0x0DE1, 0x2601, 0x1908, 0x1401
GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER = 0x2801, 0x2800
GL_CONSTANT_ALPHA, GL_ONE_MINUS_CONSTANT_ALPHA = 0x8003, 0x8004
GL_VENDOR, GL_RENDERER, GL_VERSION = 0x1F00, 0x1F01, 0x1F02
_f, _u, _i, _b, _p = ctypes.c_float, ctypes.c_uint, ctypes.c_int, ctypes.c_ubyte, ctypes.c_void_p
GL_FUNCS = {                                    # solo estas llamadas de OpenGL, resueltas con el contexto de Qt
    "glClearColor": (None, [_f, _f, _f, _f]), "glClear": (None, [_u]), "glViewport": (None, [_i, _i, _i, _i]),
    "glEnable": (None, [_u]), "glDisable": (None, [_u]), "glBlendFunc": (None, [_u, _u]),
    "glBlendColor": (None, [_f, _f, _f, _f]), "glColorMask": (None, [_b, _b, _b, _b]),
    "glBindFramebuffer": (None, [_u, _u]), "glBindTexture": (None, [_u, _u]),
    "glTexParameteri": (None, [_u, _u, _i]), "glReadPixels": (None, [_i, _i, _i, _i, _u, _u, _p]),
    "glGetString": (ctypes.c_char_p, [_u]),
}
TEST_SOURCE = "av://lavfi:testsrc=size=640x360:rate=30"      # señal de prueba generada por mpv (sin archivos)


def _smooth(x):
    return x * x * (3 - 2 * x)                  # 3x²−2x³: la mezcla arranca y termina sin tirones


class GlVideoWidget(QOpenGLWidget):
    frame = Signal()                            # lo emiten los hilos de mpv / el reproductor para pedir un repintado

    def __init__(self):
        super().__init__()
        fmt = QSurfaceFormat()
        fmt.setSwapInterval(1)                  # sincronía vertical: la mezcla se anima a la frecuencia de la pantalla
        self.setFormat(fmt)
        self.layers, self.gl, self.ready, self.error, self.gl_info = [], None, False, None, ""
        self.base, self.has_base, self.black, self.fade = 0, False, True, None
        self.pending, self._fresh = None, {}      # capa que se está cargando detrás: se dibuja ya (aunque aún no se vea)
        self.render_size, self._nonblock = (1920, 1080), True
        self._lock, self._fbo, self._blitter, self._get_proc = threading.Lock(), {}, None, None
        self._frames, self._probing, self._probe, self._probe_layer, self._last_probe = 0, False, None, None, None
        self._rate_t, self._rate_n, self._rate = time.monotonic(), 0, 0.0      # cuadros/s reales del visor (para el diagnóstico)
        self.frame.connect(self.update)
        # Latido a ~60 Hz: no depende de los avisos de mpv. Con la ventana oculta/minimizada Qt no pinta: se pinta a
        # mano para que mpv siga recibiendo fotogramas (si no, el video se detendría).
        self._beat = QTimer(self)
        self._beat.timeout.connect(self._beat_tick)
        self._beat.start(16)

    # --------------------------------------------------- API para el reproductor (cualquier hilo)
    def attach(self, layers):
        self.layers = layers

    def set_render_size(self, w, h):
        self.render_size = (w, h)

    def set_black(self, on):
        with self._lock:
            self.black = on
        self.frame.emit()                       # señal: se atiende en el hilo de la interfaz

    def show_base(self, idx):
        with self._lock:
            self.base, self.has_base, self.fade = idx, True, None
        self.frame.emit()

    def begin_pending(self, idx):
        """Se va a cargar un visual en la capa `idx`: se dibuja desde ya para que mpv entregue su primer fotograma (así,
        cuando empieza el fundido, su textura ya tiene imagen en vez de negro)."""
        with self._lock:
            self.pending, self._fresh[idx] = idx, False
        self.frame.emit()

    def mark_ready(self, idx):
        """El archivo de la capa ya está cargado: a partir de aquí cuenta el aviso de fotograma nuevo."""
        with self._lock:
            self._fresh[idx] = False

    def pending_fresh(self, idx):
        return bool(self._fresh.get(idx))

    def end_pending(self):
        with self._lock:
            self.pending = None

    def start_fade(self, idx, dur):
        if dur <= 0:
            return self.show_base(idx)
        with self._lock:
            self.fade = {"top": idx, "t0": time.monotonic(), "dur": dur}
        self.frame.emit()

    @property
    def fading(self):
        return self.fade is not None

    def speed_up_fade(self, seconds):
        """Acorta el fundido en curso para que termine en `seconds` desde ahora, sin saltos."""
        with self._lock:
            f = self.fade
            if f:
                x = min(1.0, (time.monotonic() - f["t0"]) / f["dur"])
                new = seconds / max(1e-3, 1.0 - x)          # misma fracción ya recorrida, resto en `seconds`
                f["t0"], f["dur"] = time.monotonic() - x * new, new

    def render_fps(self):
        """Cuadros por segundo que dibuja el visor (se renueva como mucho 1 vez por s). Es el dato para saber si el video «va a
        tirones»: con un video a 60 fps y la pantalla a 60 Hz debería marcar ~60."""
        now = time.monotonic()
        if now - self._rate_t >= 1.0:
            self._rate = (self._frames - self._rate_n) / (now - self._rate_t)
            self._rate_t, self._rate_n = now, self._frames
        return self._rate

    def status(self):
        """Texto de diagnóstico para el panel de estado."""
        parts = [self.gl_info or "OpenGL sin iniciar"]
        for i, lay in enumerate(self.layers):
            try:
                tp, path = lay.m.time_pos, lay.m.path
                parts.append(f"capa {i}: {os.path.basename(str(path)) if path else '—'}"
                             + (f" {tp:.1f} s" if tp is not None else ""))
            except Exception:
                parts.append(f"capa {i}: ?")
        showing = not self.black and (self.has_base or self.fade is not None)
        parts.append(f"{self._frames} cuadros dibujados" + (f" ({self.render_fps():.0f} cuadros/s)" if showing else ""))
        if self._last_probe:
            parts.append("píxel medio capa/salida: {} / {}".format(*self._last_probe))
        if self.black:
            parts.append("salida en negro (pausa)")
        if self.error:
            parts.append("⚠ " + self.error)
        return " · ".join(parts)

    # ------------------------------------------------------------------ OpenGL
    def _addr(self, name):
        ctx = QOpenGLContext.currentContext()
        if ctx is None:
            return 0
        try:
            a = ctx.getProcAddress(name)
        except TypeError:
            a = ctx.getProcAddress(name.decode())
        try:
            return int(a) if a else 0
        except TypeError:
            return ctypes.cast(a, ctypes.c_void_p).value or 0

    def _ensure_contexts(self):
        """Crea el contexto de render de mpv de cada capa que aún no lo tenga. Hace falta el contexto OpenGL actual. Se llama
        al iniciar OpenGL y en cada cuadro: Qt puede iniciar OpenGL (al mostrar la ventana) ANTES de que el reproductor
        enganche sus capas con attach(); sin esto las capas se quedaban sin contexto y el primer cuadro fallaba."""
        for lay in self.layers:
            if lay.ctx is None:
                lay.ctx = mpv.MpvRenderContext(lay.m, "opengl", opengl_init_params={"get_proc_address": self._get_proc})
                lay.ctx.update_cb = lambda: self.frame.emit()

    def initializeGL(self):
        try:
            self.gl = type("GL", (), {})()
            for name, (res, args) in GL_FUNCS.items():
                addr = self._addr(name.encode())
                if not addr:
                    raise RuntimeError(f"OpenGL: falta {name}")
                setattr(self.gl, name, ctypes.CFUNCTYPE(res, *args)(addr))
            ver = (self.gl.glGetString(GL_VERSION) or b"?").decode(errors="replace").split()[0]
            gpu = (self.gl.glGetString(GL_RENDERER) or b"?").decode(errors="replace")[:40]
            self.gl_info = f"OpenGL {ver} · {gpu}"
            self._get_proc = mpv.MpvGlGetProcAddressFn(lambda _ctx, n: self._addr(n))
            self._ensure_contexts()
            self._blitter = QOpenGLTextureBlitter()
            if not self._blitter.create():
                raise RuntimeError("no se pudo crear el mezclador de texturas (QOpenGLTextureBlitter)")
            self.ready = True
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            dl.exception("OpenGL", "no se pudo iniciar el visor")

    def paintGL(self):
        if self.ready:
            try:
                self._paint()
            except Exception as e:
                self.ready, self.error = False, f"{type(e).__name__}: {e}"
                print("Visor OpenGL:", self.error)
                dl.exception("OpenGL", "falló el dibujado")

    def _beat_tick(self):
        if not self.ready:
            return
        if not self.isVisible() or self.window().isMinimized():
            self.makeCurrent()
            try:
                self._paint()
            finally:
                self.doneCurrent()
        elif (not self.black and (self.has_base or self.fade)) or self.pending is not None:
            self.update()

    def _target(self, idx, w, h):
        f = self._fbo.get(idx)
        if f is None or f.width() != w or f.height() != h:
            f = self._fbo[idx] = QOpenGLFramebufferObject(w, h)      # RGBA8 sin profundidad
            self.gl.glBindTexture(GL_TEXTURE_2D, f.texture())
            self.gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            self.gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            self.gl.glBindTexture(GL_TEXTURE_2D, 0)
        return f

    def _read_mean(self, w, h):
        """Media RGBA (0-255) de un cuadro de 128×128 en el centro del framebuffer enlazado."""
        bw, bh = min(w, 128), min(h, 128)
        buf = (ctypes.c_ubyte * (bw * bh * 4))()
        self.gl.glReadPixels((w - bw) // 2, (h - bh) // 2, bw, bh, GL_RGBA, GL_UNSIGNED_BYTE, buf)
        return tuple(int(v) for v in np.frombuffer(buf, np.uint8).reshape(-1, 4).mean(axis=0).round())

    def _render_layer(self, idx, w, h):
        lay, f = self.layers[idx], self._target(idx, w, h)
        if lay.ctx.update():                            # mpv tiene un fotograma nuevo para esta capa
            self._fresh[idx] = True
        f.bind()
        kw = {"opengl_fbo": {"w": w, "h": h, "fbo": f.handle()}, "flip_y": True}
        if self._nonblock:                              # que render() no duerma esperando la hora del fotograma
            try:
                lay.ctx.render(block_for_target_time=False, **kw)
            except Exception:                           # versión de python-mpv sin ese parámetro (TypeError, ValueError, KeyError…)
                self._nonblock = False
                lay.ctx.render(**kw)
        else:
            lay.ctx.render(**kw)
        if self._probing or self._frames % 120 == 0:    # muestreo de diagnóstico (no en cada cuadro)
            f.bind()                                    # mpv deja enlazado otro framebuffer al terminar: se lee el de la capa
            self._probe_layer = self._read_mean(w, h)
        f.release()
        return f.texture()

    def _blit(self, tex, rect):
        self.gl.glViewport(*rect)
        self._blitter.bind()
        self._blitter.blit(tex, QMatrix4x4(), QOpenGLTextureBlitter.Origin.OriginBottomLeft)
        self._blitter.release()

    def _paint(self):
        self._ensure_contexts()
        gl, dpr = self.gl, self.devicePixelRatioF()
        W, H = max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr))
        rw, rh = self.render_size
        with self._lock:
            base, has_base, black, fade, pend = self.base, self.has_base, self.black, self.fade, self.pending
        alpha, top, done = 0.0, None, False
        if fade:
            x = (time.monotonic() - fade["t0"]) / fade["dur"]
            top, done, alpha = fade["top"], x >= 1.0, _smooth(min(1.0, max(0.0, x)))
        tex_base = tex_top = None
        want = []                                       # capas a dibujar en este cuadro (cada una una sola vez)
        if not black:
            if has_base:
                want.append(base)
            if top is not None and top not in want:
                want.append(top)
        if pend is not None and pend not in want:       # la que se está cargando detrás también, aunque no se vea aún
            want.append(pend)
        tex = {i: self._render_layer(i, rw, rh) for i in want}   # cada visual en su textura, a la resolución elegida
        if not black:
            tex_base = tex.get(base) if has_base else None
            tex_top = tex.get(top) if top is not None else None
        gl.glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        for cap in (GL_BLEND, GL_SCISSOR_TEST, GL_DEPTH_TEST):
            gl.glDisable(cap)
        gl.glColorMask(1, 1, 1, 1)
        gl.glViewport(0, 0, W, H)
        gl.glClearColor(0.0, 0.0, 0.0, 1.0)             # fondo negro y OPACO (Qt compone este framebuffer con su alfa)
        gl.glClear(GL_COLOR_BUFFER_BIT)
        gl.glColorMask(1, 1, 1, 0)                      # los videos nunca tocan el alfa: la salida siempre es opaca
        ar = rw / rh                                    # el cuadro conserva su proporción dentro de la ventana
        w2, h2 = (int(H * ar), H) if W / H > ar else (W, int(W / ar))
        rect = ((W - w2) // 2, (H - h2) // 2, w2, h2)
        if tex_base is not None:
            self._blit(tex_base, rect)
        if tex_top is not None:                         # mezcla: resultado = visual nuevo·α + lo que hay debajo·(1−α)
            gl.glBlendColor(0.0, 0.0, 0.0, alpha)
            gl.glBlendFunc(GL_CONSTANT_ALPHA, GL_ONE_MINUS_CONSTANT_ALPHA)
            gl.glEnable(GL_BLEND)
            self._blit(tex_top, rect)
            gl.glDisable(GL_BLEND)
        gl.glColorMask(1, 1, 1, 1)
        self._frames += 1
        if (self._probing or self._frames % 120 == 0) and (tex_base is not None or tex_top is not None):
            out = self._read_mean(W, H)
            self._last_probe = (self._probe_layer, out)
            if self._probing:
                self._probe = self._last_probe
        if fade:
            if done:
                with self._lock:
                    if self.fade is fade:
                        self.base, self.has_base, self.fade = top, True, None
            else:
                self.update()                           # sigue la animación al ritmo de la pantalla

    # ------------------------------------------------------------------ autoprueba
    def _probe_source(self, src, pump, timeout):
        """Reproduce `src` en la capa 0 y mide lo que llega a la capa y a la pantalla. Devuelve (ok, medidas)."""
        lay = self.layers[0]
        self._probe, self._probing = None, True
        lay.path = src
        lay.m.pause, lay.m.speed, lay.m.loop_file = False, 1.0, "inf"
        lay.m.command("loadfile", src, "replace")             # (no `m.loadfile`: ver core/mpvutil.open_file)
        with self._lock:
            self.black, self.base, self.has_base, self.fade = False, 0, True, None
        t0, got = time.monotonic(), None
        while time.monotonic() - t0 < timeout:
            pump()
            got = self._probe
            # Lo que cuenta es la SALIDA (lo que se ve en la ventana): si ahí hay imagen, la capa la entregó. La medida de la capa
            # es solo diagnóstico (en la autoprueba de una máquina real se leyó mal y dio un falso negativo).
            if got and got[1] and sum(got[1][:3]) > 24:
                self._probing = False
                return True, got
        self._probing = False
        return False, got

    def selftest(self, pump, sample=None, timeout=3.0):
        """Comprueba que lo que mpv dibuja llega de verdad a la pantalla: 1) una señal de prueba que genera mpv;
        2) un video tuyo (si no se ve con la decodificación por hardware, se prueba por software y se deja así).
        Devuelve (ok, detalle). `pump()` mantiene viva la interfaz mientras se espera."""
        fmt = lambda g: f"capa RGBA={g[0]}, salida RGBA={g[1]}" if g else "mpv no entregó ningún fotograma"
        ok, got = self._probe_source(TEST_SOURCE, pump, timeout)
        notes = [f"señal de prueba: {fmt(got)}"]
        if ok and sample:
            ok2, got2 = self._probe_source(sample, pump, timeout)
            notes.append(f"{os.path.basename(sample)}: {fmt(got2)}")
            if not ok2:                                 # ¿decodificación por hardware sin imagen? se prueba por software
                orig = self.layers[0].m.hwdec
                for lay in self.layers:
                    lay.m.hwdec = "no"
                ok3, got3 = self._probe_source(sample, pump, timeout)
                if ok3:
                    notes.append("con hwdec=no sí se ve: se usa decodificación por software")
                else:                                   # probablemente el video es oscuro: se acepta y se restaura
                    for lay in self.layers:
                        lay.m.hwdec = orig
        self.layers[0].stop()
        with self._lock:
            self.black, self.has_base, self.fade = True, False, None
        return ok, " | ".join(notes)

    def shutdown(self):
        self._beat.stop()
        self.makeCurrent()
        try:
            for lay in self.layers:
                if lay.ctx is not None:
                    lay.ctx.free()
                    lay.ctx = None
        finally:
            self.doneCurrent()
