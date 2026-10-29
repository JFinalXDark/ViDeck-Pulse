# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ventana «Componentes necesarios» (primer inicio): libmpv, Tesseract y ffmpeg, con su finalidad, licencia, origen y tamaño.

Se abre desde `main.py` ANTES de cargar la ventana principal (en un equipo limpio, sin libmpv, la principal ni se puede importar). Toda la lógica
(estado, instalación en un hilo, verificación de archivos propios) está en core/firstrun.py; aquí solo está la pantalla, que consulta el
progreso con un temporizador. Por eso este módulo NO importa nada que necesite libmpv. Resultado: `outcome` = "ready" (se puede abrir el
programa) o "quit" (el usuario salió).
"""
import os

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from core import ffmpeg_tools as ft
from core import firstrun as fr
from core import i18n
from core import status as stmod
from core.i18n import tr as T
from core.typo import keep_sentences
from gui import adaptive
from gui import ui_kit as ui

RED = "color: " + stmod.COLORS[stmod.ERROR] + ";"
GREEN = "color: " + stmod.COLORS[stmod.OK] + ";"
FILTERS = {"libmpv": "libmpv (libmpv-2.dll mpv-2.dll);;Todos los archivos (*)", "tesseract": "tesseract.exe (tesseract.exe);;Todos los archivos (*)",
           "ffmpeg": "ffmpeg.exe (ffmpeg.exe);;Todos los archivos (*)"}
PICK_TITLES = {"libmpv": "Elegir libmpv-2.dll", "tesseract": "Elegir tesseract.exe", "ffmpeg": "Elegir ffmpeg.exe"}
WAIT_ELEVATION = {"tesseract": "Windows te pedirá permiso de administrador para instalarlo."}


def _text(s):
    return keep_sentences(T(s))


class _Row:
    """Un componente: casilla, finalidad, licencia/origen/tamaño, estado, barra de progreso y sus dos botones."""

    def __init__(self, dlg, info):
        self.d, self.info, self.key = dlg, info, info["key"]
        self.ok, self.version, self.path = bool(info["ok"]), info.get("version", ""), info.get("path", "")
        self.run = {}                                   # lo que informa el instalador: state/phase/done/total/error
        self.msg, self.msg_error = "", False            # resultado de «usar un archivo»
        self.picking = False
        self.box = QGroupBox()
        lay = QVBoxLayout(self.box)
        self.chk = QCheckBox()
        self.chk.toggled.connect(dlg.on_selection)
        self.lbl_purpose, self.lbl_meta, self.lbl_status, self.lbl_note = ui.label(), ui.label(), ui.label(), ui.label()
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        self.btn_page = QPushButton()
        self.btn_page.clicked.connect(lambda: dlg.open_page(self.key))
        self.btn_file = QPushButton()
        self.btn_file.clicked.connect(lambda: dlg.pick_file(self.key))
        row = QHBoxLayout()
        row.addWidget(self.btn_page)
        row.addWidget(self.btn_file)
        for w in (self.chk, self.lbl_purpose, self.lbl_meta, self.lbl_status, self.bar, self.lbl_note):
            lay.addWidget(w)
        lay.addLayout(row)
        self.render()

    # ------------------------------------------------------------------ estado
    def busy(self):
        return self.run.get("state") in ("waiting", "running") or self.picking

    def done(self):
        return self.ok

    def render(self):
        i = self.info
        self.box.setTitle(T(i["name"]) + "  ·  " + T("Imprescindible" if i["required"] else "Opcional"))
        self.chk.setText(T("Instalar este componente"))
        self.chk.setEnabled(not self.ok and not self.d.running())
        self.chk.setVisible(not self.ok)
        self.lbl_purpose.setText(_text(i["purpose"]))
        self.lbl_meta.setText(keep_sentences(T("Licencia") + ": " + T(i["license"]) + "\n" + T("Origen") + ": " + T(i["source"]) + "  ·  "
                                             + T(f"unos {round(i['size'] / 1e6)} MB")))
        self.lbl_note.setText(_text(WAIT_ELEVATION.get(self.key, "")) if not self.ok else "")
        self.btn_page.setText(T("Abrir la página oficial"))
        self.btn_file.setText(T("Usar un archivo que ya descargué…"))
        self.btn_file.setEnabled(not self.d.running() and not self.picking)
        self.btn_file.setVisible(not self.ok)
        self.render_status()

    def render_status(self):
        st = self.run.get("state", "")
        self.bar.setVisible(st == "running" or self.picking)
        if st == "running":
            prog = (self.run.get("phase", ""), self.run.get("done", 0), self.run.get("total", 0))
            frac = ft.progress_fraction(prog)
            self.bar.setRange(0, 100 if frac is not None else 0)
            if frac is not None:
                self.bar.setValue(int(frac * 100))
            self.lbl_status.setText(T("Consultando los repositorios…") if prog[0] == "latest" else T(ft.progress_text(prog)))
            self.lbl_status.setStyleSheet("")
        elif self.picking:
            self.bar.setRange(0, 0)
            self.lbl_status.setText(T("Comprobando el archivo elegido…"))
            self.lbl_status.setStyleSheet("")
        elif self.ok:
            self.lbl_status.setText(T(f"Listo: {self.version}") if self.version else T("Listo"))
            self.lbl_status.setStyleSheet(GREEN)
        elif st == "waiting":
            self.lbl_status.setText(T("En cola"))
            self.lbl_status.setStyleSheet("")
        elif st == "error":
            self.lbl_status.setText(T("No se pudo instalar") + ": " + T(self.run.get("error", "")))
            self.lbl_status.setStyleSheet(RED)
        elif st == "cancelled":
            self.lbl_status.setText(T("Cancelado"))
            self.lbl_status.setStyleSheet("")
        elif self.msg:
            self.lbl_status.setText(T(self.msg))
            self.lbl_status.setStyleSheet(RED if self.msg_error else "")
        else:
            self.lbl_status.setText(T("No encontrado"))
            self.lbl_status.setStyleSheet(RED if self.info["required"] else "")


class FirstRunDialog(QDialog):
    def __init__(self, cfg, save, rows=None, installer_factory=None, file_dialog=None, open_url=None, parent=None):
        super().__init__(parent)
        self.cfg, self.save_cb = cfg, save
        self.installer_factory = installer_factory or (lambda keys: fr.Installer(keys, cfg))
        self.file_dialog = file_dialog or (lambda parent, title, flt: QFileDialog.getOpenFileName(parent, title, os.path.expanduser("~"), flt)[0])
        self.open_url = open_url or (lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.outcome = "quit"
        self.installer, self.file_job = None, None
        adaptive.fit_window(self, 820, 780)
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        self.lbl_lang = ui.label()
        self.cb_lang = QComboBox()
        for name, code in (("Español", "es"), ("English", "en")):
            self.cb_lang.addItem(name, code)
        self.cb_lang.setCurrentIndex(max(0, self.cb_lang.findData(i18n.language())))
        self.cb_lang.currentIndexChanged.connect(self.on_language)
        top.addStretch(1)
        top.addWidget(self.lbl_lang)
        top.addWidget(self.cb_lang)
        outer.addLayout(top)
        body = QWidget()
        self._body = body
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_intro = ui.label()
        lay.addWidget(self.lbl_intro)
        self.rows = [_Row(self, r) for r in (rows if rows is not None else fr.rows(cfg))]
        default = fr.default_selection(cfg, [r.info for r in self.rows])
        for r in self.rows:
            lay.addWidget(r.box)
            r.chk.setChecked(r.key in default)
        self.lbl_total = ui.label()
        self.lbl_result = ui.label()
        lay.addWidget(self.lbl_total)
        lay.addWidget(self.lbl_result)
        lay.addStretch(1)
        outer.addWidget(adaptive.scrolled(body), 1)
        buttons = QHBoxLayout()
        self.btn_quit, self.btn_install, self.btn_cancel, self.btn_go = QPushButton(), QPushButton(), QPushButton(), QPushButton()
        self.btn_quit.clicked.connect(self.reject)
        self.btn_install.clicked.connect(self.start_install)
        self.btn_cancel.clicked.connect(self.cancel_install)
        self.btn_go.clicked.connect(self.finish)
        buttons.addWidget(self.btn_quit)
        buttons.addStretch(1)
        for b in (self.btn_cancel, self.btn_install, self.btn_go):
            buttons.addWidget(b)
        outer.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(150)
        self.render()

    # ------------------------------------------------------------------ pantalla
    def running(self):
        return self.installer is not None and not self.installer.finished()

    def busy(self):
        return self.running() or any(r.picking for r in self.rows)

    def selected(self):
        return [r.key for r in self.rows if r.chk.isChecked() and not r.ok]

    def required_missing(self):
        return [r.key for r in self.rows if r.info["required"] and not r.ok]

    def render(self):
        self.setWindowTitle(T("Componentes necesarios"))
        self.lbl_lang.setText(T("Idioma / Language"))
        self.lbl_intro.setText(_text("ViDeck Pulse usa tres programas de otros autores que no vienen dentro de él, cada uno con su propia licencia. "
                                     "Se descargan de su fuente oficial por HTTPS, se verifican y se prueban antes de usarlos. Marca los que "
                                     "quieras instalar ahora; nada se instala sin que lo pidas."))
        for r in self.rows:
            r.render()
        self.btn_quit.setText(T("Salir"))
        self.btn_install.setText(T("Reintentar la instalación" if any(r.run.get("state") in ("error", "cancelled") for r in self.rows)
                                   else "Instalar lo marcado"))
        self.btn_cancel.setText(T("Cancelar la instalación"))
        self.btn_go.setText(T("Continuar"))
        self.sync()

    def sync(self):
        sel = self.selected()
        run = self.running()
        self.btn_cancel.setVisible(run)
        self.btn_install.setVisible(not run)
        self.btn_install.setEnabled(bool(sel) and not self.busy())
        miss = self.required_missing()
        self.btn_go.setEnabled(not miss and not self.busy())
        self.btn_quit.setEnabled(True)
        if not sel and not run:
            self.lbl_total.setText(_text("No hay nada marcado para instalar."))
        else:
            self.lbl_total.setText(_text(f"Se descargarán unos {round(fr.total_size(sel) / 1e6)} MB en total. Hace falta conexión a Internet."))
        if self.busy():
            return
        if miss:
            names = ", ".join(T(fr.INFO[k]["name"]) for k in miss)
            self.lbl_result.setText(_text(f"Todavía falta algo imprescindible: {names}."))
            self.lbl_result.setStyleSheet(RED)
        else:
            self.lbl_result.setText(_text("Todo lo imprescindible está listo. Pulsa «Continuar»."))
            self.lbl_result.setStyleSheet(GREEN)

    def on_selection(self, *_):
        if hasattr(self, "lbl_total"):
            self.sync()

    def on_language(self, *_):
        code = self.cb_lang.currentData()
        if code not in i18n.LANGS or code == i18n.language():
            return
        i18n.set_language(code)                                          # los textos del programa pasan de inmediato al nuevo idioma
        self.cfg["language"] = code                                      # …y queda guardado para la ventana principal
        self.save_cb()
        self.render()

    # ------------------------------------------------------------------ acciones
    def open_page(self, key):
        self.open_url(fr.INFO[key]["page"])

    def start_install(self):
        keys = self.selected()
        if not keys or self.busy():
            return
        for r in self.rows:
            r.run, r.msg = {}, ""
        self.installer = self.installer_factory(keys)
        self.installer.start()
        self.lbl_result.setText("")
        self.render()                                                    # (casillas y botones de «usar un archivo» se desactivan mientras corre)
        self.poll()

    def cancel_install(self):
        if self.installer is not None:
            self.installer.cancel()
            self.lbl_result.setText(_text("Cancelando…"))
            self.lbl_result.setStyleSheet("")

    def poll(self):
        if self.installer is not None:
            snap = self.installer.snapshot()
            for r in self.rows:
                r.run = snap.get(r.key, r.run)
            if self.installer.finished():
                self.installer = None
                self.refresh_state()
                self.render()
            else:
                for r in self.rows:
                    r.render_status()
                self.sync()
        if self.file_job is not None:
            key, job = self.file_job
            if not job.is_alive():
                self.file_job = None
                row = next(r for r in self.rows if r.key == key)
                row.picking = False
                if job.error is not None:
                    row.msg, row.msg_error = str(job.error) or type(job.error).__name__, True
                else:
                    row.msg = ""
                self.refresh_state()
                self.render()

    def refresh_state(self):
        """Vuelve a mirar qué hay realmente instalado (lo que instaló el hilo o el archivo elegido)."""
        new = {r["key"]: r for r in fr.rows(self.cfg)}
        for r in self.rows:
            n = new[r.key]
            r.ok, r.version, r.path = bool(n["ok"]), n.get("version", ""), n.get("path", "")
            if r.ok:
                r.run = {}
                r.chk.setChecked(False)

    def pick_file(self, key):
        if self.busy():
            return
        path = self.file_dialog(self, T(PICK_TITLES[key]), T(FILTERS[key]))
        if not path:
            return
        row = next(r for r in self.rows if r.key == key)
        row.picking, row.msg = True, ""
        self.file_job = (key, ft.Job(fr.use_file, key, path, self.cfg))
        self.file_job[1].start()
        self.render()

    def finish(self):
        if self.required_missing() or self.busy():
            return
        fr.remember_choice(self.cfg, self.selected(), [r.info | {"ok": r.ok} for r in self.rows])
        self.save_cb()
        self.outcome = "ready"
        self.accept()

    def reject(self):
        if self.installer is not None:
            self.installer.cancel()
        super().reject()
