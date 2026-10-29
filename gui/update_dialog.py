# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ventana corta «Actualizando…»: descarga, verifica y prueba una actualización ya elegida (ffmpeg o libmpv) con su barra de progreso.

Se abre cuando se contesta «Sí» al aviso de actualización del inicio. Lo lento corre en un hilo (`Job`); la ventana lo consulta con un
temporizador, así que no bloquea nada. Si algo falla se informa como ERROR en el registro y en la barra de estado (con el nombre del repositorio
que falló) y se queda la ventana con el motivo en rojo; lo que ya estaba instalado no se toca. libmpv no se puede reemplazar mientras está en uso:
queda preparada, probada, y se pone al reiniciar (se ofrece reiniciar).
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from core import components as comp
from core import ffmpeg_tools as ft
from core.i18n import tr as T
from gui import ui_kit as ui
from gui.alerts import ask_restart_text


class UpdateDialog(QDialog):
    """`restart_requested` queda en True si se eligió reiniciar (libmpv nueva): quien la abrió cierra el programa y main.py abre otra copia."""

    def __init__(self, name, release, engine_running=False, statusbar=None, parent=None):
        super().__init__(parent)
        self.name, self.release, self.engine_running, self._statusbar = name, release, engine_running, statusbar
        self.src = comp.SRC_FFMPEG if name == "ffmpeg" else comp.SRC_MPV
        self.label = comp.NAMES[self.src]
        self.restart_requested, self.ok, self.error = False, False, None
        self.prog = ("download", 0, release.size)
        self.setWindowTitle(T(f"Actualizando {self.label}"))
        self.resize(520, 190)
        self.setStyleSheet(ui.BUTTONS_CSS + ui.BAR_CSS + ui.PANEL_CSS)
        head = QLabel(T(f"Actualizando {self.label} a {release.version}"))
        head.setObjectName("title")
        head.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.lbl_detail = ui.label("", name="dim")
        self.lbl_detail.setText(T(release.label) + (f" · {release.size / 1e6:.0f} MB" if release.size else ""))
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.lbl_phase = ui.label("")
        self.btn_cancel = QPushButton(T("Cancelar"))
        self.btn_cancel.clicked.connect(lambda _checked=False: self.cancel())
        self.btn_close = QPushButton(T("Cerrar"))
        self.btn_close.clicked.connect(lambda _checked=False: self.reject())
        self.btn_close.setVisible(False)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_close)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        lay.addWidget(head)
        lay.addWidget(self.lbl_detail)
        lay.addSpacing(6)
        lay.addWidget(self.bar)
        lay.addWidget(self.lbl_phase)
        lay.addStretch(1)
        lay.addLayout(row)
        self.job = ft.Job(self._run)
        self.job.start()
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.poll()

    def _run(self):
        return comp.run_install(self.src, self.release, ft.Net(), self._progress, self.job.cancel)

    def _progress(self, phase, done, total):
        self.prog = (phase, done, total)

    def cancel(self):
        if self.job is not None and self.job.is_alive():
            self.job.cancel.set()
            self.btn_cancel.setEnabled(False)
            self.lbl_phase.setText(T("Cancelando…"))

    def _say(self, text):
        if self._statusbar:
            self._statusbar(text)

    def poll(self):
        job = self.job
        if job is None:
            return
        if job.is_alive():
            frac = ft.progress_fraction(self.prog)
            self.bar.setRange(0, 100 if frac is not None else 0)
            self.bar.setValue(int(100 * frac) if frac is not None else 0)
            if not job.cancel.is_set():
                self.lbl_phase.setText(T(ft.progress_text(self.prog)))
            return
        self.job = None
        self.timer.stop()
        self._finished(job)

    def _finished(self, job):
        self.bar.setRange(0, 100)
        self.btn_cancel.setVisible(False)
        e = job.error
        if e is not None:
            self.error = e
            if isinstance(e, ft.Cancelled):
                self.lbl_phase.setText(T("Cancelado."))
            else:
                comp.fail(self.src, str(e), network=isinstance(e, ft.NetworkError))
                self.lbl_phase.setText(T(str(e)))
                self.lbl_phase.setStyleSheet(f"color: {ui.RED};")
            self.btn_close.setVisible(True)
            return
        comp.ok(self.src)
        self.ok = True
        self.bar.setValue(100)
        ver = (job.result or {}).get("version", "") or self.release.version
        if self.src == comp.SRC_FFMPEG:
            ft.BASE.clear()
            self.lbl_phase.setText(T(f"ffmpeg {ver} instalado y probado."))
            self._say(T(f"ffmpeg {ver} instalado."))
            self.accept()
            return
        self.lbl_phase.setText(T(f"libmpv {ver} preparada y probada: se pone al reiniciar el programa."))
        if ask_restart_text(self, "Reiniciar", "libmpv nueva preparada. Para aplicarla hay que reiniciar ViDeck Pulse.", self.engine_running):
            self.restart_requested = True
        self.accept()

    def _shutdown(self):
        self.timer.stop()
        if self.job is not None and self.job.is_alive():
            self.job.cancel.set()
            self.job.join(15)

    def reject(self):
        self._shutdown()
        super().reject()

    def closeEvent(self, e):
        self._shutdown()
        super().closeEvent(e)
