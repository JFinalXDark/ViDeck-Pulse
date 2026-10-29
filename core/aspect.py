# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Proporción del Reproductor (16:9). Lógica pura: la usa gui/output_window.py y se prueba sin Qt."""


def fit_aspect(w, h, old_w, old_h, aspect):
    """Tamaño que respeta `aspect` (ancho/alto) y más se parece a lo que el usuario arrastró. Si cambió más la altura
    (en proporción) se toma esa y se deriva el ancho; si no, se toma el ancho. `old_*` = tamaño anterior (0 si no hay)."""
    if w <= 0 or h <= 0 or aspect <= 0:
        return w, h
    if abs(w - h * aspect) <= 1.5:                       # ya es 16:9 (con tolerancia de redondeo)
        return w, h
    if old_w > 0 and old_h > 0 and abs(h - old_h) * aspect > abs(w - old_w):
        return max(1, round(h * aspect)), h
    return w, max(1, round(w / aspect))
