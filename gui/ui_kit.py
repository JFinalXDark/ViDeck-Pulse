# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Piezas visuales compartidas por las ventanas de ayuda (Preparar videos, Actualizar, Componentes): etiquetas que se parten en líneas sin dejar
huecos, tarjetas de resumen, y las hojas de estilo (colores del programa; el resto sale de la paleta del sistema, claro u oscuro)."""
import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout

from core.i18n import tr as T
from core.typo import keep_sentences

GREEN, AMBER, RED, GRAY, BLUE = "#3ddc84", "#f0a030", "#e5383b", "#9a9a9a", "#4a9eff"      # los de la lista de videos y la barra de estado
PRIMARY, PRIMARY_HOVER, SUCCESS, SUCCESS_HOVER = "#2f7de1", "#4590f0", "#2e9e5b", "#36b66a"     # botones de acción (azul) y de «usar/terminar» (verde)

# --------------------------------------------------------------------------------------------------- hojas de estilo
# (Solo se tocan los widgets con nombre; los demás conservan el aspecto nativo. `palette(...)` sigue el tema claro/oscuro del sistema.)
BUTTONS_CSS = f"""
QPushButton#primary, QPushButton#success {{ color: white; border: none; border-radius: 5px; padding: 7px 16px; font-weight: 600; }}
QPushButton#primary {{ background: {PRIMARY}; }}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton#success {{ background: {SUCCESS}; }}
QPushButton#success:hover {{ background: {SUCCESS_HOVER}; }}
QPushButton#primary:disabled, QPushButton#success:disabled {{ background: palette(mid); color: palette(midlight); }}
QPushButton#link {{ border: none; background: transparent; color: {BLUE}; padding: 3px 8px; }}
QPushButton#link:hover {{ text-decoration: underline; }}
QPushButton#link:disabled {{ color: palette(mid); }}
QPushButton#rowbtn {{ padding: 3px 12px; }}
"""

BAR_CSS = f"""
QProgressBar {{ border: none; border-radius: 3px; background: palette(mid); max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ border-radius: 3px; background: {BLUE}; }}
"""

TABLE_CSS = """
QTableWidget { border: 1px solid palette(mid); border-radius: 6px; gridline-color: transparent; alternate-background-color: palette(alternate-base); }
QTableWidget::item { padding: 2px 6px; }
QHeaderView::section { background: transparent; border: none; border-bottom: 1px solid palette(mid); padding: 6px 8px; font-weight: 600; }
"""

PANEL_CSS = f"""
QFrame#panel, QFrame#chip {{ border: 1px solid palette(mid); border-radius: 8px; background: palette(alternate-base); }}
QFrame#panel QLabel, QFrame#chip QLabel {{ border: none; background: transparent; }}
QLabel#title {{ font-size: 20px; font-weight: 700; }}
QLabel#dim {{ color: {GRAY}; }}
QLabel#caption {{ color: {GRAY}; font-size: 11px; }}
"""


class Lbl(QLabel):
    """Texto que se parte en líneas: si queda vacío se oculta (una etiqueta vacía igual reserva su línea y deja huecos en la ventana)."""

    def setText(self, t):
        super().setText(t)
        self.setVisible(bool(t))


def label(text="", bold=False, name=""):
    lb = Lbl(keep_sentences(T(text)) if text else "")
    lb.setWordWrap(True)
    sp = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    sp.setHeightForWidth(True)
    lb.setSizePolicy(sp)
    lb.setAlignment(Qt.AlignLeft | Qt.AlignTop)              # el texto pegado arriba: si sobra altura, el hueco queda abajo y no en medio
    if not text:
        lb.setVisible(False)
    if bold:
        lb.setStyleSheet("font-weight: 600;")
    if name:
        lb.setObjectName(name)
    return lb


def dot(color, text):
    """HTML de una línea de estado: un punto de color y el texto (escapado)."""
    return f'<span style="color:{color}">●</span>&nbsp;{html.escape(text)}'


class StatCard(QFrame):
    """Una tarjeta del resumen: un número grande, su título y, si se pulsa, filtra la lista (la tarjeta activa queda marcada)."""
    clicked = Signal()

    def __init__(self, key, caption, color, parent=None):
        super().__init__(parent)
        self.key, self.color, self._active, self._enabled = key, color, False, False
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.num = QLabel("–")
        self.num.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {color}; border: none; background: transparent;")
        self.cap = QLabel(T(caption))
        self.cap.setWordWrap(True)
        self.cap.setStyleSheet("font-size: 11px; border: none; background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        lay.addWidget(self.num)
        lay.addWidget(self.cap)
        self._paint()

    def set_count(self, n):
        self.num.setText("–" if n is None else str(n))

    def set_active(self, on):
        self._active = bool(on)
        self._paint()

    def set_enabled_filter(self, on):
        self._enabled = bool(on)
        self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)

    def _paint(self):
        bg = "palette(midlight)" if self._active else "palette(alternate-base)"
        edge = self.color if self._active else "palette(mid)"
        self.setStyleSheet(f"QFrame#card {{ border: 1px solid {edge}; border-top: 3px solid {self.color}; border-radius: 6px; background: {bg}; }}")

    def mouseReleaseEvent(self, e):
        if self._enabled and e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


def eta_text(seconds):
    """«45 s» · «2 min 10 s» · «1 h 05 min» (las unidades son iguales en español y en English)."""
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"
