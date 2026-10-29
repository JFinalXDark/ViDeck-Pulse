# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registro de sincronía: una línea por cada revisión que el motor hace de la posición del video frente a la del track en Traktor
(y por cada carga, cambio de tempo, salto y ajuste fino). Sirve para ver POR QUÉ el video se desvía: qué posición tenía el video,
cuál el track, cuánto era la diferencia, si se corrigió, y cuánto tardó el salto en terminar.

Las líneas se guardan en español, con piezas separadas por « · » (y la acción final por « — »), de modo que la pantalla las traduce
pieza por pieza (core/i18n.py) sin tocar lo guardado: «Copiar» entrega el texto tal como se ve.
"""
import os
import threading
import time
from collections import deque

MAXLEN = 800
SEP = " · "


class SyncLog:
    def __init__(self, maxlen=MAXLEN):
        self._buf, self._seq, self._lock = deque(maxlen=maxlen), 0, threading.Lock()
        self._header = []

    def add(self, deck, video, kind, parts=(), action="", now=None):
        """Anota una línea: «[A] · «video.mp4» · revisión · video 62.31 s · … — corrige a 62.20 s»."""
        head = [f"[{deck}]" if deck else "[—]", f"«{os.path.basename(str(video))}»" if video else "«—»", kind]
        text = SEP.join(head + [p for p in parts if p])
        if action:
            text += " — " + action
        with self._lock:
            self._seq += 1
            self._buf.append((self._seq, time.time() if now is None else now, text))
        return text

    @property
    def last_seq(self):
        with self._lock:
            return self._seq

    def entries(self):
        with self._lock:
            return list(self._buf)

    def since(self, seq):
        with self._lock:
            return [e for e in self._buf if e[0] > seq]

    def clear(self):
        with self._lock:
            self._buf.clear()

    def set_header(self, lines):
        self._header = list(lines)

    def text(self, tr=None, header=True):
        """El registro entero como texto (con `tr`, traducido igual que en pantalla), listo para copiar."""
        lines = [fmt_line(e, tr) for e in self.entries()]
        if header:
            title = f"ViDeck Pulse · registro de sincronía copiado {time.strftime('%Y-%m-%d %H:%M:%S')}"
            lines = [tr(title) if tr else title] + [tr(h) if tr else h for h in self._header] + [""] + lines
        return "\n".join(lines)


def fmt_line(entry, tr=None):
    """«HH:MM:SS.mmm texto» (con `tr`, el texto traducido pieza por pieza)."""
    _seq, t, text = entry
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d} " + (tr(text) if tr else text)


synclog = SyncLog()               # instancia única: `from core.synclog import synclog`


def signed(x, nd=2):
    return f"{x:+.{nd}f}"
