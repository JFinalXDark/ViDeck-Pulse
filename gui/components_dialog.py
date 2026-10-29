# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ventana «Componentes» (Ajustes): ffmpeg y libmpv propios del programa y «Legacy Mode».

Aquí solo está la pantalla; la lógica (descarga verificada, preparación, paquete Legacy, errores en la barra de estado) está en
core/ffmpeg_tools.py, core/mpv_tools.py, core/legacy.py y core/components.py. Todo lo lento corre en un hilo (`Job`) y la ventana
lo consulta con un temporizador: nada bloquea la interfaz.
"""
import os

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QDialog, QGroupBox, QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout, QWidget

from core import components as comp
from core import ffmpeg_tools as ft
from core import legacy, mpv_tools as mt
from core import updatecheck, version
from core import status as stmod
from core.i18n import tr as T
from core.typo import keep_sentences
from gui import adaptive
from gui import ui_kit as ui
from gui.alerts import ask_restart_text, ask_yes_no

RED = "color: " + stmod.COLORS[stmod.ERROR] + ";"            # el mismo rojo de los errores de la barra de estado
PHASES = {"download": "Descargando…", "verify": "Verificando la suma SHA-256…", "extract": "Extrayendo…", "check": "Probando que funcione…"}


_Lbl, _label = ui.Lbl, ui.label                                  # (las etiquetas que se parten en líneas sin dejar huecos, compartidas)


class _Block:
    """La parte de un componente (ffmpeg o libmpv): estado, buscar, instalar, cancelar, avisar al iniciar."""

    def __init__(self, dialog, key, title, mod, src, install_text, update_text, note):
        self.d, self.key, self.mod, self.src = dialog, key, mod, src
        self.install_text, self.update_text = install_text, update_text
        self.job, self.release, self.mode, self.prog = None, None, "", ("", 0, 0)
        self.box = QGroupBox(T(title))
        lay = QVBoxLayout(self.box)
        self.lbl_use = _label()
        self.lbl_own = _label()
        self.lbl_extra = _label()
        self.lbl_latest = _label()
        self.lbl_note = _label(note)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        self.lbl_msg = _label()
        self.btn_check = QPushButton(T("Buscar actualización"))
        self.btn_install = QPushButton(T(install_text))
        self.btn_install.setEnabled(False)
        self.btn_cancel = QPushButton(T("Cancelar"))
        self.btn_cancel.setVisible(False)
        self.btn_extra = QPushButton("")
        self.btn_extra.setVisible(False)
        self.chk = QCheckBox(T("Verificar actualizaciones al iniciar"))
        self.chk.setChecked(bool(comp.section(dialog.cfg)[key].get("auto_check", True)))
        self.chk.toggled.connect(self.on_auto)
        row = QHBoxLayout()
        for b in (self.btn_check, self.btn_install, self.btn_cancel):
            row.addWidget(b)
        for w in (self.lbl_use, self.lbl_own, self.lbl_extra, self.lbl_latest, self.lbl_msg, self.bar):
            lay.addWidget(w)
        lay.addLayout(row)
        lay.addWidget(self.btn_extra)
        lay.addWidget(self.chk)
        lay.addWidget(self.lbl_note)
        self.btn_check.clicked.connect(self.check)
        self.btn_install.clicked.connect(self.install)
        self.btn_cancel.clicked.connect(self.cancel)

    # ------------------------------------------------------------------ estado
    def refresh(self):
        raise NotImplementedError

    def busy(self):
        return self.job is not None and self.job.is_alive()

    def set_busy(self, busy):
        self.btn_check.setEnabled(not busy)
        self.btn_install.setEnabled(not busy and self.release is not None and self._wants_install())
        self.btn_cancel.setVisible(busy)
        self.btn_cancel.setEnabled(True)
        self.bar.setVisible(busy)
        self.d.sync_buttons()

    def _wants_install(self):
        return True

    def on_auto(self, on):
        comp.section(self.d.cfg)[self.key]["auto_check"] = bool(on)
        self.d.save()

    def message(self, text, error=False):
        self.lbl_msg.setText(keep_sentences(T(text)))
        self.lbl_msg.setStyleSheet(RED if error else "")

    # ------------------------------------------------------------------ buscar
    def check(self):
        if self.busy():
            return
        self.release, self.mode = None, "check"
        self.btn_install.setEnabled(False)
        self.message("Consultando los repositorios…")
        self.job = ft.Job(self.mod.latest, ft.Net())
        self.job.start()
        self.set_busy(True)
        self.bar.setRange(0, 0)

    def _checked(self):
        e, rel = self.job.error, self.job.result
        self.bar.setRange(0, 100)
        if e is not None:
            self.fail(e)
            return
        self.release = rel
        inst = self.installed_info()
        size = f" · {rel.size / 1e6:.0f} MB" if rel.size else ""
        self.lbl_latest.setText(T(f"Última publicada: {rel.version}{size} · {rel.label}"))
        newer = ft.is_newer(rel, inst) if inst else True
        self.btn_install.setText(T(self.update_text if inst else self.install_text))
        self.btn_install.setEnabled(newer)
        self.message(("Hay una versión más nueva: puedes instalarla." if inst else "Puedes instalarlo desde el repositorio verificado.")
                     if newer else "Ya tienes la última versión.")

    def installed_info(self):
        raise NotImplementedError

    # ------------------------------------------------------------------ instalar
    def install(self):
        rel = self.release
        if self.busy() or rel is None:
            return
        size = f" ({rel.size / 1e6:.0f} MB)" if rel.size else ""
        if not ask_yes_no(self.d, "Descargar", f"¿Descargar {comp.NAMES[self.src]} {rel.version}{size} desde {rel.label}?",
                          "Se verifica con su suma SHA-256 publicada antes de abrirlo y se prueba antes de usarlo; si algo falla, "
                          "lo que tienes ahora no se toca."):
            return
        self.start_install(rel)

    def start_install(self, rel):
        self.mode, self.prog = "install", ("download", 0, 0)
        self.job = ft.Job(self.run_install, rel)
        self.job.start()
        self.message("Descargando…")
        self.set_busy(True)

    def run_install(self, rel):
        raise NotImplementedError

    def progress(self, phase, done, total):
        self.prog = (phase, done, total)

    def cancel(self):
        if self.busy():
            self.job.cancel.set()
            self.btn_cancel.setEnabled(False)
            self.message("Cancelando…")

    def _installed(self):
        e = self.job.error
        if e is not None:
            self.fail(e)
            return
        comp.ok(self.src)
        self.done(self.job.result)

    def done(self, info):
        raise NotImplementedError

    def fail(self, e):
        text = str(e)
        if isinstance(e, ft.Cancelled):
            self.message("Cancelado.")
        elif isinstance(e, ft.NetworkError):
            comp.fail(self.src, text, network=True)
            self.message(text, error=True)
        else:
            comp.fail(self.src, text)
            self.message(text, error=True)
        self.d.refresh_all()

    # ------------------------------------------------------------------ latido (cada 150 ms)
    def poll(self):
        if self.job is None:
            return
        if self.job.is_alive():
            if self.mode == "install":
                phase, done, total = self.prog
                self.message(PHASES.get(phase, "…") + (f" {done / 1e6:.0f} / {total / 1e6:.0f} MB" if phase == "download" and total else ""))
                self.bar.setRange(0, 100 if total else 0)
                self.bar.setValue(int(100 * done / total) if total else 0)
            return
        (self._checked if self.mode == "check" else self._installed)()
        self.job = None
        self.set_busy(False)


class _Pulse:
    """La fila «ViDeck Pulse» (solo la versión estable): busca a mano si hay una versión nueva en GitHub. Solo avisa y abre la página de
    descarga: el programa no descarga ni instala nada solo. (La búsqueda del inicio, una vez por arranque, está en la ventana principal.)"""

    def __init__(self, dialog):
        self.d, self.job, self.result = dialog, None, None
        self.box = QGroupBox(T("ViDeck Pulse"))
        lay = QVBoxLayout(self.box)
        self.lbl_use = _label()
        self.lbl_use.setText(T("Versión en marcha:") + " " + updatecheck.nice(version.release_tag()))
        self.lbl_msg = _label()
        self.btn_check = QPushButton(T("Buscar actualización"))
        self.btn_open = QPushButton(T("Abrir la página de descarga"))
        self.btn_open.setVisible(False)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        row = QHBoxLayout()
        row.addWidget(self.btn_check)
        row.addWidget(self.btn_open)
        for w in (self.lbl_use, self.lbl_msg, self.bar):
            lay.addWidget(w)
        lay.addLayout(row)
        lay.addWidget(_label("Solo avisa y te lleva a la página de descarga en GitHub: el programa no descarga ni instala nada solo."))
        self.btn_check.clicked.connect(self.check)
        self.btn_open.clicked.connect(self.open_page)

    def busy(self):
        return self.job is not None and self.job.is_alive()

    def message(self, text, error=False):
        self.lbl_msg.setText(keep_sentences(T(text)))
        self.lbl_msg.setStyleSheet(RED if error else "")

    def check(self):
        if self.busy():
            return
        self.result = None
        self.btn_open.setVisible(False)
        self.btn_check.setEnabled(False)
        self.bar.setVisible(True)
        self.message("Consultando GitHub…")
        shim = {"updates": dict(updatecheck.section(self.d.cfg))}        # (el hilo trabaja sobre una copia; el resultado se aplica en poll)
        self._shim = shim
        self.job = ft.Job(updatecheck.check, shim, None, None, True)
        self.job.start()

    def poll(self):
        job = self.job
        if job is None or job.is_alive():
            return
        self.job = None
        self.bar.setVisible(False)
        self.btn_check.setEnabled(True)
        if job.error is not None:
            self.message("No se pudo buscar una versión nueva (¿hay conexión con GitHub?).", True)
            return
        res = job.result or {}
        self.d.cfg.setdefault("updates", {}).update(self._shim["updates"])
        self.d.save()
        st = res.get("state")
        if st == "newer":
            self.result = res
            self.message("Hay una versión más nueva: puedes descargarla desde GitHub.")
            self.lbl_use.setText(T("Versión en marcha:") + " " + res.get("running", "") + " · " + T("Última publicada:") + " " + res.get("text", ""))
            self.btn_open.setVisible(True)
        elif st == "error":
            self.message("No se pudo buscar una versión nueva (¿hay conexión con GitHub?).", True)
        else:
            self.message("Ya tienes la última versión.")

    def open_page(self):
        if self.result:
            QDesktopServices.openUrl(QUrl(updatecheck.safe_url(self.result.get("url"))))


class _Ffmpeg(_Block):
    def __init__(self, dialog):
        super().__init__(dialog, "ffmpeg", "ffmpeg", ft, comp.SRC_FFMPEG, "Instalar ffmpeg", "Actualizar ffmpeg",
                         "ffmpeg lo usa «Preparar videos» (y el análisis de compatibilidad de los videos). Se instala en la carpeta «ffmpeg» "
                         "del programa: nada se instala en el sistema.")
        self.btn_extra.clicked.connect(self.remove_own)
        self.chk_base = QCheckBox(T("Instalar el ffmpeg base automáticamente si falta"))
        self.chk_base.setChecked(bool(comp.section(dialog.cfg)["ffmpeg"].get("auto_base", False)))
        self.chk_base.toggled.connect(self.on_base)
        lay = self.box.layout()
        lay.insertWidget(lay.indexOf(self.chk) + 1, self.chk_base)               # justo debajo de «Avisarme al iniciar…»

    def on_base(self, on):
        comp.section(self.d.cfg)["ffmpeg"]["auto_base"] = bool(on)
        self.d.save()

    def installed_info(self):
        return ft.installed()

    def refresh(self):
        d = comp.describe_ffmpeg(self.d.cfg.get("prepare", {}).get("ffmpeg_cmd", ""))
        self.lbl_use.setText(T(d["use"]))
        self.lbl_own.setText(T(d["own"]))
        self.lbl_extra.setText(T(d["note"]) if d["note"] else "")
        self.btn_extra.setText(T("Quitar el ffmpeg propio"))
        self.btn_extra.setVisible(bool(d["installed"]))
        self.btn_install.setText(T(self.update_text if d["installed"] else self.install_text))

    def run_install(self, rel):
        return ft.install(rel, ft.Net(), self.progress, self.job.cancel)

    def done(self, info):
        self.message(f"ffmpeg {info.get('version', '')} instalado y probado.")
        self.d.statusbar(T(f"ffmpeg {info.get('version', '')} instalado."))
        self.d.refresh_all()

    def remove_own(self):
        if self.busy():
            return
        if ask_yes_no(self.d, "Quitar ffmpeg", "¿Quitar el ffmpeg propio del programa?", "Se borra la carpeta «ffmpeg» del programa (no el del sistema)."):
            ft.remove()
            self.message("ffmpeg propio quitado.")
            self.d.refresh_all()


class _Mpv(_Block):
    def __init__(self, dialog):
        super().__init__(dialog, "mpv", "libmpv (el reproductor)", mt, comp.SRC_MPV, "Preparar libmpv", "Preparar libmpv nueva",
                         "libmpv es el motor de video del programa. Una DLL no se puede reemplazar mientras está en uso: la nueva se descarga, se "
                         "verifica y se prueba ahora, y se pone al reiniciar el programa (si no funciona, se vuelve sola a la anterior).")
        self.btn_extra.clicked.connect(self.discard)

    def installed_info(self):
        return mt.installed()

    def refresh(self):
        d = comp.describe_mpv(self.d.mpv_version)
        self.lbl_use.setText(T(d["use"]))
        self.lbl_own.setText(T(d["own"]))
        self.lbl_extra.setText(T(" · ".join(d["extra"])) if d["extra"] else "")
        self.btn_extra.setText(T("Descartar la libmpv preparada"))
        self.btn_extra.setVisible(bool(d["pending"]))
        self.btn_install.setText(T(self.update_text if d["installed"] else self.install_text))

    def run_install(self, rel):
        return mt.stage(rel, ft.Net(), self.progress, self.job.cancel)

    def done(self, info):
        self.message(f"libmpv {info.get('version', '')} preparada y probada: se pone al reiniciar el programa.")
        self.d.refresh_all()
        self.d.offer_restart("libmpv nueva preparada.")

    def discard(self):
        mt.discard_pending()
        self.message("La libmpv preparada se descartó.")
        self.d.refresh_all()


class ComponentsDialog(QDialog):
    """`restart_requested` queda en True si el usuario eligió reiniciar (para que la ventana principal se cierre y main.py abra otra copia)."""

    def __init__(self, cfg, save, engine_running=False, mpv_version="", statusbar=None, parent=None, auto=()):
        super().__init__(parent)
        self.setWindowTitle(T("Componentes: ffmpeg y libmpv"))
        adaptive.fit_window(self, 760, 760)                          # sin pasarse de la pantalla (si no cabe, el contenido se desplaza)
        self.cfg, self.save_cb, self.engine_running, self.mpv_version = cfg, save, engine_running, mpv_version
        self._statusbar = statusbar
        self.restart_requested = False
        outer = QVBoxLayout(self)                                    # el contenido va en una zona con scroll y «Cerrar» queda siempre abajo
        body = QWidget()
        self._body = body
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        intro = _label("ffmpeg y libmpv se descargan solo de repositorios públicos conocidos (GitHub), por HTTPS, y se verifican con su suma "
                       "SHA-256 publicada ANTES de abrirlos; después se prueban. Si algo falla se avisa como error en la barra de estado y en el "
                       "registro, y lo que ya tenías no se toca.")
        self.ffmpeg, self.mpv = _Ffmpeg(self), _Mpv(self)
        self.pulse = _Pulse(self) if version.STABLE else None        # las versiones Beta no buscan actualizaciones de sí mismas
        lay.addWidget(intro)
        if self.pulse is not None:
            lay.addWidget(self.pulse.box)
        lay.addWidget(self.ffmpeg.box)
        lay.addWidget(self.mpv.box)
        self.g_legacy = QGroupBox(T("Legacy Mode"))
        gl = QVBoxLayout(self.g_legacy)
        self.lbl_legacy = _label()
        self.lbl_legacy_msg = _label()
        self.btn_snap = QPushButton(T("Guardar ahora los repositorios actuales como Legacy"))
        self.btn_snap.clicked.connect(self.snapshot)
        self.btn_legacy = QPushButton("")
        self.btn_legacy.clicked.connect(self.toggle_legacy)
        self.bar_snap = QProgressBar()
        self.bar_snap.setRange(0, 100)
        self.bar_snap.setVisible(False)
        gl.addWidget(self.lbl_legacy)
        gl.addWidget(self.lbl_legacy_msg)
        gl.addWidget(self.bar_snap)
        gl.addWidget(self.btn_snap)
        gl.addWidget(self.btn_legacy)
        gl.addWidget(_label("Legacy Mode usa los últimos ffmpeg y libmpv que funcionaban con esta versión del programa, sin tocar los "
                            "actualizados. Se aplica al reiniciar el programa."))
        lay.addWidget(self.g_legacy)
        lay.addStretch(1)                                            # si sobra altura, el hueco queda aquí y «Cerrar» sigue abajo
        outer.addWidget(adaptive.scrolled(body), 1)
        btn_close = QPushButton(T("Cerrar"))
        btn_close.clicked.connect(self.accept)
        outer.addWidget(btn_close)
        self.snap_job, self.snap_prog = None, (0, 0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(150)
        self.refresh_all()
        for name in auto:                                            # «Actualizar ahora» desde el aviso del inicio: ya se confirmó, sigue solo
            blk = self.ffmpeg if name == "ffmpeg" else self.mpv
            blk.mode = "check"
            blk.job = ft.Job(blk.mod.latest, ft.Net())
            blk.job.start()
            blk.set_busy(True)
            blk.auto_install = True

    # ------------------------------------------------------------------ utilidades
    def save(self):
        self.save_cb()

    def statusbar(self, text):
        if self._statusbar:
            self._statusbar(text)

    def sync_buttons(self):
        self.btn_snap.setEnabled(self.snap_job is None and not comp.snapshot_busy())

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._fit)

    def _fit(self):
        """La altura justa para el contenido con el ancho actual (sin pasarse de la pantalla): los textos que se parten en líneas
        hacen que el tamaño inicial salga mayor que lo que de verdad hace falta."""
        try:
            lay = self._body.layout()
            w = self.width()
            h = lay.heightForWidth(w) if lay.hasHeightForWidth() else lay.sizeHint().height()
            if isinstance(h, int) and h > 0:
                h += 70                                              # el botón «Cerrar» y los márgenes de la ventana
                av = adaptive.available_size(self)
                nw, nh = adaptive.fit_size(w, max(320, h), av[0] if av else None, av[1] if av else None)
                self.resize(nw, nh)
        except Exception:
            pass

    def refresh_all(self):
        self.ffmpeg.refresh()
        self.mpv.refresh()
        info = comp.describe_legacy()
        self.lbl_legacy.setText(T(info["text"]))
        on = bool(self.cfg.get("components", {}).get("legacy"))
        self.btn_legacy.setText(T("Desactivar Legacy Mode" if on else "Activar Legacy Mode"))
        self.btn_legacy.setEnabled(info["available"] or on)
        self.sync_buttons()

    def poll(self):
        for blk in (self.ffmpeg, self.mpv):
            auto = getattr(blk, "auto_install", False)
            was_check = blk.mode == "check" and blk.job is not None and not blk.job.is_alive()
            blk.poll()
            if auto and was_check:                                    # terminó la búsqueda pedida al inicio: instala la versión encontrada
                blk.auto_install = False
                if blk.release is not None and blk.btn_install.isEnabled():
                    blk.start_install(blk.release)
        if self.pulse is not None:
            self.pulse.poll()
        self.poll_snapshot()

    # ------------------------------------------------------------------ reinicio
    def offer_restart(self, text):
        if ask_restart_text(self, "Reiniciar", text + " Para aplicarla hay que reiniciar ViDeck Pulse.", self.engine_running):
            self.restart_requested = True
            self.accept()

    # ------------------------------------------------------------------ Legacy Mode
    def snapshot(self):
        if self.snap_job is not None or comp.snapshot_busy():
            return
        cfg_ff = self.cfg.get("prepare", {}).get("ffmpeg_cmd", "")
        self.snap_prog = (0, 0)
        on_progress = lambda d, t: setattr(self, "snap_prog", (d, t))      # noqa: E731
        self.snap_job = ft.Job(comp.build_snapshot, cfg_ff, on_progress, None)
        self.snap_job.a = (cfg_ff, on_progress, self.snap_job.cancel)
        self.snap_job.start()
        self.lbl_legacy_msg.setText(T("Comprimiendo los repositorios actuales…"))
        self.bar_snap.setVisible(True)
        self.sync_buttons()

    def poll_snapshot(self):
        job = self.snap_job
        if job is None:
            return
        if job.is_alive():
            d, t = self.snap_prog
            self.bar_snap.setRange(0, 100 if t else 0)
            self.bar_snap.setValue(int(100 * d / t) if t else 0)
            return
        self.snap_job = None
        self.bar_snap.setVisible(False)
        if job.error is not None:
            comp.legacy_error(str(job.error))
            self.lbl_legacy_msg.setText(T(str(job.error)))
            self.lbl_legacy_msg.setStyleSheet(RED)
        else:
            comp.legacy_ok()
            self.lbl_legacy_msg.setText(T("Paquete Legacy guardado."))
            self.lbl_legacy_msg.setStyleSheet("")
        self.refresh_all()

    def toggle_legacy(self):
        sec = comp.section(self.cfg)
        want = not sec["legacy"]
        if want and not legacy.available():
            return
        sec["legacy"] = want
        self.save()
        running = comp.legacy_active()
        if want == running:
            self.statusbar(T("Legacy Mode sin cambios."))
            self.refresh_all()
            return
        title = "Activar Legacy Mode" if want else "Desactivar Legacy Mode"
        text = ("Legacy Mode elegido: se usarán los ffmpeg y libmpv guardados." if want
                else "Legacy Mode desactivado: se usarán los ffmpeg y libmpv actualizados.")
        if ask_restart_text(self, title, text + " Para aplicarlo hay que reiniciar ViDeck Pulse.", self.engine_running):
            self.restart_requested = True
            self.accept()
            return
        self.statusbar("Legacy Mode: se aplica al reiniciar el programa." if self.cfg.get("language", "es") == "es"
                       else "Legacy Mode: applied when the program restarts.")
        self.refresh_all()
