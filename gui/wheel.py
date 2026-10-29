# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""La rueda del mouse sobre un campo (lista desplegable o número) NO lo cambia mientras el campo no tenga el foco.

Antes, al bajar con la rueda por una pestaña de ajustes, el campo que quedaba bajo el cursor cambiaba de valor sin querer (un método
de captura, una duración…). Ahora, sin foco, la rueda sigue su camino y desplaza la zona con scroll; el campo solo responde a la
rueda después de hacer clic en él (como hacen los programas de escritorio con los campos de una página larga)."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox


def _scroll_parent(w):
    p = w.parentWidget() if w is not None else None
    while p is not None:
        if isinstance(p, QAbstractScrollArea):
            return p
        p = p.parentWidget()
    return None


class WheelGuard(QObject):
    def eventFilter(self, obj, ev):
        try:
            if ev.type() == QEvent.Wheel and not obj.hasFocus():
                sa = _scroll_parent(obj)
                if sa is not None:
                    QApplication.sendEvent(sa.verticalScrollBar(), ev)       # la rueda desplaza la zona, no cambia el campo
                else:
                    ev.ignore()
                return True
        except Exception:
            pass
        return False


def guard_wheel(root, guard=None):
    """Protege del scroll con la rueda todas las listas desplegables y los campos numéricos que hay dentro de `root`."""
    guard = guard or WheelGuard(root)
    for kind in (QComboBox, QAbstractSpinBox):
        for w in root.findChildren(kind):
            w.setFocusPolicy(Qt.StrongFocus)                   # el clic da el foco; la rueda sola no lo da
            w.installEventFilter(guard)
    return guard
