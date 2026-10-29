# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Esfera dibujada (no un carácter de texto): un círculo con volumen —degradado radial suave con la luz arriba a la izquierda, el
borde más oscuro, un reflejo que se desvanece y un contorno fino semitransparente— dibujado con antialiasing a la resolución real de
la pantalla (nítido también con escalado de Windows al 125 %, 150 % o 200 %), que cambia de color según el estado: verde (todo en orden), amarilla (alerta), roja (error).

La misma esfera se usa delante del texto de la barra de estado y en los dos contadores del extremo derecho (errores y alertas), así
que siempre tienen exactamente el mismo aspecto y los mismos colores (core/status.py · COLORS)."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

SIZE = 14                        # lado del widget, en píxeles
MARGIN = 1.0                     # aire alrededor para que el borde no se corte


def shade(color, toward, amount):
    """Mezcla un color «#rrggbb» con otro («#ffffff» = aclarar, «#000000» = oscurecer) en la proporción `amount` (0 a 1)."""
    a, b = color.lstrip("#"), toward.lstrip("#")
    out = []
    for i in (0, 2, 4):
        x, y = int(a[i:i + 2], 16), int(b[i:i + 2], 16)
        out.append(max(0, min(255, int(round(x + (y - x) * amount)))))
    return "#%02x%02x%02x" % tuple(out)


def tones(color):
    """Los tonos de una esfera de ese color: (brillo, centro, borde del degradado, contorno)."""
    return (shade(color, "#ffffff", 0.72), color, shade(color, "#000000", 0.36), shade(color, "#000000", 0.52))


class Sphere(QWidget):
    def __init__(self, color="#2fb344", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(SIZE, SIZE)

    def color(self):
        return self._color

    def set_color(self, color):
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, e):
        w, h = float(self.width()), float(self.height())
        d = min(w, h) - 2 * MARGIN                       # diámetro de la esfera
        if d <= 0:
            return
        x, y = (w - d) / 2.0, (h - d) / 2.0
        light, mid, dark, line = tones(self._color)
        body = QRadialGradient(QPointF(x + d * 0.5, y + d * 0.5), d * 0.60, QPointF(x + d * 0.38, y + d * 0.32))
        body.setColorAt(0.00, QColor(light))
        body.setColorAt(0.42, QColor(mid))
        body.setColorAt(0.80, QColor(shade(mid, "#000000", 0.18)))
        body.setColorAt(1.00, QColor(dark))
        glow = QRadialGradient(QPointF(x + d * 0.37, y + d * 0.31), d * 0.27)      # reflejo suave: blanco que se desvanece (sin borde duro)
        glow.setColorAt(0.00, QColor(255, 255, 255, 215))
        glow.setColorAt(0.50, QColor(255, 255, 255, 80))
        glow.setColorAt(1.00, QColor(255, 255, 255, 0))
        outline = QColor(line)
        outline.setAlpha(170)                            # contorno fino y semitransparente: la esfera se funde con el fondo
        p = QPainter(self)
        try:
            p.setRenderHint(getattr(QPainter, "RenderHint", QPainter).Antialiasing, True)
            p.setPen(QPen(outline, 1.0))
            p.setBrush(QBrush(body))
            p.drawEllipse(QRectF(x, y, d, d))
            p.setPen(getattr(Qt, "PenStyle", Qt).NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(QRectF(x + d * 0.10, y + d * 0.04, d * 0.54, d * 0.54))
        finally:
            p.end()
