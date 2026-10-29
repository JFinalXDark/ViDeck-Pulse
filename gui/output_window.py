# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QVBoxLayout, QWidget

from core import devlog as dl
from core.aspect import fit_aspect
from core.i18n import tr as T
from core.version import player_title
from gui.icon import app_icon


MODES = ("window", "borderless", "fullscreen")      # con bordes · sin bordes · pantalla completa
DRAG_PX = 4                                          # cuánto hay que arrastrar la ventana sin bordes para empezar a moverla
RESERVED_KEYS = ("esc", "escape")                    # Esc está reservada: sale de la pantalla completa (no puede ser el atajo de modo)


class OutputWindow(QWidget):
    """Visor donde mpv renderiza (se incrusta con wid).

    La ventana mide la resolución elegida (1080p → 1920×1080 px, 720p → 1280×720) y conserva la proporción 16:9 si la
    redimensionas: lo que se ve es lo que se reproduce.

    Tres modos (`set_mode`): «window» (ventana normal), «borderless» (ventana sin bordes ni barra de título: al transmitir la
    ventana por Discord no se ve el borde superior; se mueve arrastrándola) y «fullscreen» (pantalla completa). El cambio de
    bordes se hace sobre el handle YA creado (QWindow.setFlag), sin recrear la ventana nativa: mpv y OpenGL usan ese handle.
    El modo NO cambia con el doble clic (un doble clic sin querer en pleno set cambiaría la ventana): solo con el combo de la
    pestaña «Salida», con el botón «Visor a pantalla completa», con **Esc** (sale de la pantalla completa) y con el **atajo de teclado**
    opcional (`set_hotkey`; en blanco por defecto), que solo actúa con ESTA ventana seleccionada, abierta y sin minimizar:
    `cycle_requested` pide a la ventana principal el modo siguiente (ventana → sin bordes → pantalla completa → ventana).
    `mode_changed(modo)` avisa cuando el modo cambia desde la propia ventana (Esc)."""

    mode_changed = Signal(str)
    cycle_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(player_title())
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setStyleSheet("background:black;")
        self._aspect, self._fixing = 16 / 9, False
        self.resize(1280, 720)                    # hasta que la ventana principal aplique la resolución elegida
        self._view = None
        self._res = None                          # (ancho, alto) del área de video elegida
        self._mode, self._windowed_mode, self._frameless = "window", "window", False
        self._press = None                        # dónde se pulsó el botón izquierdo (para arrastrar la ventana sin bordes)
        self._hotkey = ""                         # atajo para cambiar de modo (en blanco = sin atajo)
        self._shortcut = QShortcut(QKeySequence(), self)
        self._shortcut.setContext(Qt.WindowShortcut)        # solo con ESTA ventana seleccionada (activa); minimizada o cerrada no lo está
        self._shortcut.setAutoRepeat(False)                 # mantener la tecla pulsada no recorre los modos
        self._shortcut.activated.connect(self._on_hotkey)

    def set_resolution(self, w, h):
        """Tamaño del área de video = la resolución de salida (en píxeles reales: se compensa el escalado de Windows)."""
        self._aspect = w / h
        self._res = (w, h)
        if self.isFullScreen():
            return
        if self.isMaximized():
            self.showNormal()
        scr = self.screen() or QGuiApplication.primaryScreen()
        dpr = scr.devicePixelRatio() if scr else 1.0
        self._fixing = True
        try:
            self.resize(max(1, round(w / dpr)), max(1, round(h / dpr)))
        finally:
            self._fixing = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fixing or self.isFullScreen() or self.isMaximized():
            return
        old = e.oldSize()
        ow, oh = (old.width(), old.height()) if old.isValid() else (0, 0)
        w, h = fit_aspect(self.width(), self.height(), ow, oh, self._aspect)
        if (w, h) != (self.width(), self.height()):
            self._fixing = True
            try:
                self.resize(w, h)
            finally:
                self._fixing = False

    def set_view(self, view):
        """Visor OpenGL (fundido cruzado) que ocupa toda la ventana."""
        lay = self.layout() or QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(view)
        self._view = view

    def clear_view(self):
        if self._view is not None:
            self._view.hide()
            self._view.setParent(None)
            self._view.deleteLater()
            self._view = None

    # ---------------------------------------------------------------- modos de la ventana
    @property
    def mode(self):
        return self._mode

    @property
    def windowed_mode(self):
        """El último modo con ventana (con o sin bordes): al que se vuelve al salir de la pantalla completa."""
        return self._windowed_mode

    def _screen_pos(self, idx):
        screens = QGuiApplication.screens()
        return screens[idx].geometry().topLeft() if idx is not None and 0 <= idx < len(screens) else None

    def _set_frameless(self, on):
        """Quita o pone los bordes y la barra de título. Con la ventana ya creada se cambia el estilo del handle existente
        (QWindow.setFlag): recrearla con QWidget.setWindowFlags rompería el handle que usan mpv y OpenGL."""
        on = bool(on)
        if on == self._frameless:
            return
        self._frameless = on
        try:
            handle = self.windowHandle()
            if handle is not None:
                handle.setFlag(Qt.FramelessWindowHint, on)
            else:
                self.setWindowFlag(Qt.FramelessWindowHint, on)     # todavía no existe: basta con el flag del widget
        except Exception:
            dl.exception("ventana", "No se pudo cambiar el borde del Reproductor.", level=dl.WARN)

    def set_mode(self, mode, screen=None):
        """Aplica un modo: «window», «borderless» o «fullscreen» (en la pantalla `screen`, si se da). No muestra una ventana
        que esté oculta: cuando se muestre aparecerá ya en ese modo. Los bordes solo se cambian FUERA de la pantalla completa
        (al salir de ella Qt restaura el estilo que tenía al entrar)."""
        mode = mode if mode in MODES else "window"
        self._mode = mode
        if mode != "fullscreen":
            self._windowed_mode = mode
        visible = self.isVisible()
        if mode == "fullscreen":
            pos = self._screen_pos(screen)
            if visible:
                if pos is not None:
                    self.showNormal()                              # se mueve a la pantalla elegida y vuelve a pantalla completa
                    self.move(pos)
                if not self.isFullScreen() or pos is not None:
                    self.showFullScreen()
            else:
                if pos is not None:
                    self.move(pos)
                self.setWindowState(self.windowState() | Qt.WindowFullScreen)
            return
        if visible:
            if self.isFullScreen():
                self.showNormal()                                  # primero se sale de la pantalla completa…
        else:
            self.setWindowState(self.windowState() & ~Qt.WindowFullScreen)
        self._set_frameless(mode == "borderless")                  # …y después se cambian los bordes
        if self._res:
            self.set_resolution(*self._res)                        # el área de video vuelve a medir la resolución elegida

    def send_to_screen(self, idx):
        self.set_mode("fullscreen", idx)
        self.mode_changed.emit(self._mode)

    # ---------------------------------------------------------------- atajo de teclado para cambiar de modo
    @property
    def hotkey(self):
        return self._hotkey

    def set_hotkey(self, text):
        """Pone el atajo (texto portable de Qt, p. ej. «F9» o «Ctrl+M»; vacío = sin atajo). Esc está reservada para salir de la
        pantalla completa y un texto que Qt no entiende se descarta. Devuelve el atajo que quedó (normalizado, o «»)."""
        text = str(text or "").strip()
        keep = ""
        if text and text.replace(" ", "").lower() not in RESERVED_KEYS:
            seq = QKeySequence(text, QKeySequence.PortableText)
            if not seq.isEmpty():
                keep = seq.toString(QKeySequence.PortableText)
        self._hotkey = keep
        self._shortcut.setKey(QKeySequence(keep, QKeySequence.PortableText) if keep else QKeySequence())
        self._shortcut.setEnabled(bool(keep))
        return keep

    def _on_hotkey(self):
        """Se pulsó el atajo: pide el siguiente modo, pero solo con la ventana abierta, sin minimizar y seleccionada."""
        if not self._hotkey or not self.isVisible() or self.isMinimized() or not self.isActiveWindow():
            return
        self.cycle_requested.emit()

    def mousePressEvent(self, e):
        self._press = e.position().toPoint() if e.button() == Qt.LeftButton else None

    def mouseReleaseEvent(self, e):
        self._press = None

    def mouseMoveEvent(self, e):
        """Sin barra de título no hay de dónde agarrar la ventana: se mueve arrastrándola con el botón izquierdo."""
        if self._press is None or self._mode != "borderless" or self.isFullScreen():
            return
        d = e.position().toPoint() - self._press
        if abs(d.x()) + abs(d.y()) >= DRAG_PX:
            self._press = None
            handle = self.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                handle.startSystemMove()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.isFullScreen():
            self.set_mode(self._windowed_mode)
            self.mode_changed.emit(self._mode)

    def closeEvent(self, e):          # nunca destruir: mpv usa este handle
        e.ignore()
        self.hide()
