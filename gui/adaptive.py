# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tamaño y disposición adaptables de las ventanas: pantallas chicas (1280×720, 1366×768) o con el escalado de Windows al 125–150 %.

Todo en píxeles LÓGICOS (los de Qt, que ya descuentan el escalado). Piezas, todas puras salvo las que tocan una ventana:
- `fit_size`: un tamaño deseado recortado al área útil de la pantalla (sin la barra de tareas) menos un margen para bordes y barra de título.
- `pick_mode`: «normal» o «compacto» según el alto de la ventana, con histéresis (no oscila al arrastrar el borde).
- `resolve_mode`: lo mismo pero respetando el «Modo de Ventana» elegido a mano (`ui.layout_mode`: auto · full · compact); con «auto»
  manda `pick_mode` (la ventana sigue siendo adaptable) y con «full» / «compact» se queda fijo hasta que se vuelva a «auto».
- `available_size` / `fit_window`: aplican lo anterior a una ventana, en la pantalla donde está (o la principal).
- `scrolled`: contenido dentro de una zona con scroll (un diálogo nunca queda más grande que la pantalla ni pisa sus propios botones)."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QScrollArea

MARGIN_W, MARGIN_H = 24, 70           # lo que se deja libre: bordes de la ventana, barra de título y un respiro
COMPACT_BELOW, NORMAL_ABOVE = 760, 800   # alto de la ventana principal: por debajo de 760 pasa a «compacto», por encima de 800 vuelve a «normal»


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fit_size(want_w, want_h, avail_w=None, avail_h=None, min_w=0, min_h=0, margin_w=MARGIN_W, margin_h=MARGIN_H):
    """(ancho, alto) = lo deseado sin pasar del área útil menos el margen, y nunca por debajo del mínimo (si el mínimo no cabe, gana el
    mínimo: mejor una ventana apenas grande que una inservible). Sin datos de pantalla (None) se devuelve lo deseado."""
    w, h = want_w, want_h
    if _num(avail_w) and avail_w > 0:
        w = min(w, avail_w - margin_w)
    if _num(avail_h) and avail_h > 0:
        h = min(h, avail_h - margin_h)
    return int(max(min_w, w)), int(max(min_h, h))


def pick_mode(height, current="normal", below=COMPACT_BELOW, above=NORMAL_ABOVE):
    """«normal» o «compact» para una ventana de ese alto. Con histéresis: entre `below` y `above` se queda como está."""
    if not _num(height) or height <= 0:
        return current
    if current == "compact":
        return "normal" if height > above else "compact"
    return "compact" if height < below else "normal"


LAYOUT_MODES = ("auto", "full", "compact")


def layout_pref(cfg):
    """El «Modo de Ventana» del config (`ui.layout_mode`); cualquier valor que no sea válido cuenta como «auto»."""
    try:
        v = cfg["ui"]["layout_mode"]
    except (KeyError, TypeError):
        return "auto"
    return v if v in LAYOUT_MODES else "auto"


def resolve_mode(pref, height, current="normal", below=COMPACT_BELOW, above=NORMAL_ABOVE):
    """«normal» o «compact» para una ventana de ese alto según el modo elegido: «full» → normal, «compact» → compact,
    «auto» (o cualquier otra cosa) → `pick_mode` (por el alto, con histéresis)."""
    if pref == "full":
        return "normal"
    if pref == "compact":
        return "compact"
    return pick_mode(height, current, below, above)


def available_size(widget=None):
    """(ancho, alto) útil de la pantalla de `widget` (o la principal), sin la barra de tareas; None si no se puede saber."""
    try:
        scr = widget.screen() if widget is not None else None
        scr = scr or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        w, h = g.width(), g.height()
        return (int(w), int(h)) if _num(w) and _num(h) and w > 0 and h > 0 else None
    except Exception:                                                                  # noqa: BLE001: solo estético
        return None


def fit_window(win, want_w, want_h, min_w=0, min_h=0):
    """Pone la ventana en su tamaño deseado recortado a la pantalla. Devuelve (ancho, alto)."""
    av = available_size(win)
    w, h = fit_size(want_w, want_h, av[0] if av else None, av[1] if av else None, min_w, min_h)
    win.resize(w, h)
    return w, h


def scrolled(content):
    """`content` dentro de una zona con scroll (solo aparece la barra si hace falta). Si la ventana es más baja que el contenido,
    este se baja con la rueda en vez de quedar cortado o encimado."""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    sa.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    sa.setWidget(content)
    return sa
