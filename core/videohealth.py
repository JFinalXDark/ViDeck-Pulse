# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Salud de la reproducción: avisa cuando el video que suena NO es compatible.

El motor le pasa una muestra por segundo con lo que mpv sabe del video que se está reproduciendo (`mpvutil.health_sample`). Sin Qt ni
mpv: la lógica es pura y se prueba con muestras inventadas.

· ALERTA (barra de estado + registro): el video se reproduce, pero con bajo rendimiento: muestra menos fotogramas por segundo de los
  que tiene o mpv descarta fotogramas seguidos. Dura mientras siga así (o hasta que cambie el video).
· ERROR (barra de estado + registro, con el caso): pantalla negra. Casos: el archivo no tiene una imagen que mpv pueda mostrar,
  el decodificador no entrega fotogramas, o la imagen se quedó congelada con el reloj avanzando.

Después de cargar o saltar (sincronía) no se juzga durante unos segundos: son momentos en que el rendimiento baja sin que el video
tenga la culpa.
"""
import os
from collections import deque

from . import devlog as dl

SRC_ERROR = "video"                  # fuente del error de pantalla negra (se cierra al reproducirse bien otro video)
SRC_LOW = "video-rendimiento"        # fuente de la alerta de bajo rendimiento (condición: dura mientras siga pasando)

GRACE_S = 3.0                        # s tras cargar/saltar/reanudar sin juzgar
BLACK_S = 4.0                        # s sin imagen reproduciéndose => pantalla negra
FROZEN_S = 6.0                       # s con la imagen quieta y el reloj de mpv avanzando => congelado
LOW_RATIO = 0.80                     # fps mostrados / fps del video por debajo de esto => muestra mala
DROP_RATIO = 0.10                    # fotogramas descartados / fotogramas esperados (ventana) por encima de esto => muestra mala
LOW_N = 4                            # muestras malas seguidas => alerta
GOOD_N = 6                           # muestras buenas seguidas => se da por recuperado
WINDOW_S = 8.0                       # s de ventana para contar los fotogramas descartados
SEEK_JUMP_S = 1.0                    # un salto de posición mayor que esto (respecto de lo esperado) es un seek


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def video_facts(s):
    """(códec, ancho, alto, «GPU»/«CPU») de lo que mpv dice del video; «?» cuando no lo sabe."""
    p = s.get("params") or {}
    hw = s.get("hwdec")
    return (s.get("codec") or "?", p.get("w") or "?", p.get("h") or "?", "GPU" if hw and hw != "no" else "CPU")


def low_text(name, fps, expect, lost, s):
    """Texto de la alerta de bajo rendimiento (termina en un paréntesis con los datos: así se traduce por piezas)."""
    codec, w, h, dec = video_facts(s)
    base = f"{fps:.0f} de {expect:.0f} fps" if fps is not None else f"{expect:.0f} fps"
    tail = f"{base}, {codec} {w}×{h}, {dec}" + (f", {lost} fotogramas perdidos" if lost else "")
    return f"«{name}» va con bajo rendimiento: conviene prepararlo en Ajustes → Preparar videos ({tail})"


def black_text(name, case):
    return f"Pantalla negra: «{name}» no se puede mostrar ({case})"


CASE_NO_IMAGE = "no tiene una imagen que mpv pueda mostrar"
CASE_NO_FRAMES = "el decodificador descarta todos los fotogramas"
CASE_FROZEN = "la imagen se quedó congelada"


def case_codec(codec):
    return f"códec {codec} no compatible"


class _Track:
    def __init__(self, path, name):
        self.path, self.name = path, name
        self.t_play = None               # desde cuándo se reproduce de seguido (None: en pausa, cargando o recién saltado)
        self.last = None                 # (instante, posición) de la muestra anterior
        self.still_since = None          # desde cuándo la posición avanza pero la imagen no
        self.win = deque()               # (instante, descartados acumulados, posición)
        self.bad = self.good = 0
        self.low = False                 # alerta de bajo rendimiento abierta
        self.black = ""                  # error de pantalla negra abierto (el caso)
        self.pos_still = None            # desde cuándo la posición no se mueve


class Health:
    """Un vigilante por reproductor. `feed(muestra, sonando, ahora)` una vez por segundo; informa al registro y a la barra de estado
    (`log` = el módulo core.devlog, o un doble en las pruebas)."""

    def __init__(self, log=None):
        self.log = log or dl
        self.tr = None

    # ------------------------------------------------------------------ estado
    def reset(self):
        """El motor se detuvo o el reproductor se vació: lo que estaba avisado ya no se está reproduciendo."""
        self._close_low()
        self._close_black()
        self.tr = None

    def _close_low(self):
        if self.tr is not None and self.tr.low:
            self.tr.low = False
            self.log.devlog.clear_condition(SRC_LOW)

    def _close_black(self):
        if self.tr is not None and self.tr.black:
            self.tr.black = ""
            self.log.devlog.resolve(SRC_ERROR)

    def _pause(self, tr):
        tr.t_play, tr.last, tr.still_since, tr.pos_still = None, None, None, None
        tr.win.clear()
        tr.bad = tr.good = 0

    # ------------------------------------------------------------------ muestra
    def feed(self, s, playing, now):
        """`s`: dict de `mpvutil.health_sample` (o None si no hay nada que mirar). `playing`: ¿el motor quiere que suene?"""
        if not s or not s.get("path"):
            self.reset()
            return
        path = str(s["path"])
        if self.tr is None or self.tr.path != path:
            self.reset()                                   # otro video: lo avisado del anterior se cierra
            self.tr = _Track(path, os.path.basename(path))
            if path.startswith("av://"):
                self.tr = None                             # el patrón de prueba de mpv no se vigila
                return
        tr = self.tr
        pos = _num(s.get("time_pos"))
        if (not playing or s.get("loading") or s.get("pause") or s.get("eof") or s.get("cache_wait") or pos is None):
            self._pause(tr)
            return
        speed = _num(s.get("speed")) or 1.0
        if tr.last is not None:                            # ¿hubo un salto de posición (seek de la sincronía, bucle)?
            t0, p0 = tr.last
            if abs(pos - (p0 + (now - t0) * speed)) > SEEK_JUMP_S:
                self._pause(tr)
        tr.last = (now, pos)
        if tr.t_play is None:
            tr.t_play = now
            tr.win.clear()
        age = now - tr.t_play
        lost = int((_num(s.get("drops_dec")) or 0) + (_num(s.get("drops_vo")) or 0))
        tr.win.append((now, lost, pos))
        while tr.win and now - tr.win[0][0] > WINDOW_S:
            tr.win.popleft()
        if age < GRACE_S:
            return
        self._black(tr, s, pos, now, age)
        if not tr.black:
            self._performance(tr, s, now, lost)

    # ------------------------------------------------------------------ pantalla negra
    def _black(self, tr, s, pos, now, age):
        p = s.get("params")
        fps = _num(s.get("vf_fps"))
        cfps = _num(s.get("container_fps"))
        case = ""
        if not p or not (p.get("w") and p.get("h")):
            if age >= BLACK_S:
                case = case_codec(s["codec"]) if s.get("codec") else CASE_NO_IMAGE
        elif fps is not None and fps < 1.0 and cfps and cfps >= 5 and age >= BLACK_S and (_num(s.get("drops_dec")) or 0) > 0:
            case = CASE_NO_FRAMES
        else:
            case = self._frozen(tr, s, pos, now)
        if case:
            if not tr.black:
                text = black_text(tr.name, case)
                tr.black = text
                self._close_low()
                self.log.error(SRC_ERROR, text)
        elif tr.black:
            self._close_black()                            # volvió a haber imagen

    def _frozen(self, tr, s, pos, now):
        """Imagen congelada: el reloj de mpv avanza (reproduciendo) pero los fotogramas mostrados no cambian."""
        fps = _num(s.get("vf_fps"))
        cfps = _num(s.get("container_fps"))
        if fps is not None and fps < 0.5 and cfps and cfps >= 5:
            if tr.still_since is None:
                tr.still_since = now
            if now - tr.still_since >= FROZEN_S:
                return CASE_FROZEN
        else:
            tr.still_since = None
        return ""

    # ------------------------------------------------------------------ bajo rendimiento
    def _performance(self, tr, s, now, lost):
        speed = _num(s.get("speed")) or 1.0
        cfps = _num(s.get("container_fps"))
        fps = _num(s.get("vf_fps"))
        if not cfps or cfps <= 0:
            return
        expect = cfps * speed
        first = tr.win[0]
        span = max(0.001, now - first[0])
        dropped = lost - first[1]
        shown_bad = fps is not None and fps < LOW_RATIO * expect
        drops_bad = span >= 4.0 and dropped > 0 and dropped / (expect * span) > DROP_RATIO
        if shown_bad or drops_bad:
            tr.bad, tr.good = tr.bad + 1, 0
            if tr.bad >= LOW_N and not tr.low:
                tr.low = True
                text = low_text(tr.name, fps if shown_bad else None, expect, int(dropped) if dropped > 0 else 0, s)
                self.log.devlog.add(dl.WARN, SRC_LOW, text, _inc=False)           # al registro (una vez)
                self.log.devlog.condition(SRC_LOW, dl.WARN, text)                 # y a la barra de estado mientras dure
        else:
            tr.good, tr.bad = tr.good + 1, 0
            if tr.low and tr.good >= GOOD_N:
                self._close_low()
                self.log.devlog.add(dl.INFO, SRC_LOW, f"«{tr.name}» volvió a reproducirse con normalidad", _inc=False)
