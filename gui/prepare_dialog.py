# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ventana «Preparar videos» (Ajustes): lista los videos de la carpeta con su clasificación (Compatible · Mejorable · No compatible ·
No se puede convertir), deja prepararlos uno por uno, los marcados o todos, y los deja en una carpeta nueva con el mismo bitrate.

La lógica (ffprobe/ffmpeg, decisiones, vínculos) está en core/prepare.py; aquí solo la pantalla:
  · arriba, el estado de ffmpeg (las actualizaciones se buscan desde «Componentes»). ffmpeg ya viene instalado en la carpeta del programa (se instala solo la
    primera vez, verificado; ver core/ffmpeg_tools.BASE): no hay que instalarlo ni elegirlo;
  · tarjetas con el resumen (también filtran la lista), destino y alto máximo, la lista con el estado de cada video en color, y abajo el
    progreso general con el tiempo que falta.
"""
import os
import shutil

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core import components as comp
from core import devlog as dl
from core import ffmpeg_tools as ft
from core import prepare as pr
from core import transfer, trash
from core.config import save_config
from core.db import norm_path
from core.i18n import tr as T
from core.typo import keep_sentences
from gui import adaptive
from gui import ui_kit as ui
from gui.alerts import ask_replace_original, ask_repo_update, ask_yes_no

HEIGHTS = (0, 1080, 720)                                   # alto máximo: 0 = conservar la resolución de cada video
HEIGHT_LABELS = {0: "Original (no cambiar)", 1080: "1080 p", 720: "720 p"}
OK_STATES = pr.DONE                                         # estados con el video ya en la carpeta de destino
COLS = ["", "Video", "Estado", "Detalle", ""]
C_CHECK, C_NAME, C_STATE, C_NOTE, C_BTN = range(5)
COLOR = {pr.COMPATIBLE: ui.GREEN, pr.IMPROVABLE: ui.AMBER, pr.INCOMPATIBLE: ui.RED, pr.UNREADABLE: ui.GRAY,
         "Listo": ui.GREEN, "Copiado": ui.GREEN, "Ya existía": ui.GREEN, "Revisar": ui.AMBER, "Preparando": ui.BLUE, "Verificando": ui.BLUE, "Cancelado": ui.GRAY}
ICON = {pr.COMPATIBLE: "✔", pr.IMPROVABLE: "▲", pr.INCOMPATIBLE: "✖", pr.UNREADABLE: "⊘", "Listo": "✔", "Copiado": "✔", "Ya existía": "✔",
        "Revisar": "▲", "Preparando": "↻", "Verificando": "✓", "Cancelado": "■", "Analizando": "…"}
CARDS = (("all", "Videos", ui.BLUE), (pr.COMPATIBLE, "Compatibles", ui.GREEN), (pr.IMPROVABLE, "Mejorables", ui.AMBER),
         (pr.INCOMPATIBLE, "No compatibles", ui.RED), (pr.UNREADABLE, "No se pueden convertir", ui.GRAY))
UNSEEN = ("Pendiente", "Analizando")                        # filas que todavía no se analizaron: no cuentan en las tarjetas


def default_dst(videos_dir):
    """Carpeta de destino por defecto: junto a la de videos, con «_preparados» (nunca dentro: se escanea con subcarpetas)."""
    return os.path.normpath(os.path.abspath(videos_dir)) + "_preparados"


def inside(a, b):
    """¿La carpeta `a` es `b` o está dentro de `b`?"""
    a, b = norm_path(a), norm_path(b)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def row_cat(r):
    """La clasificación con la que cuenta un video analizado."""
    return pr.UNREADABLE if (r.state == pr.UNREADABLE or r.plan is None) else r.plan.category


BTN_TEXTS = ("Preparar video", "Copiar tal cual", "Reintentar", "Hecho")     # los textos que puede llevar el botón de una fila


def row_button(r, busy=False):
    """(texto, ¿activo?, ayuda) del botón de un video según su estado."""
    if r.state in OK_STATES:
        return "Hecho", False, "Este video ya está en la carpeta de destino."
    if r.state in pr.WORKING:
        return "…", False, ""
    if r.state == pr.UNREADABLE:
        if r.info is None:
            return "—", False, r.note
        return "Reintentar", not busy, r.note
    if r.state == pr.COMPATIBLE:
        return "Copiar tal cual", not busy, "Ya funciona bien: no hace falta prepararlo; esto solo lo copia a la carpeta de destino."
    return "Preparar video", not busy, "Recodifica solo este video a la carpeta de destino, con el mismo bitrate."


def info_tip(r):
    """Los datos del video para el globo de ayuda de su fila: «1280×720 · 30 fps · h264 · 2.4 Mb/s · 87 MB»."""
    i = r.info
    if not i:
        return ""
    parts = []
    if i.get("width") and i.get("height"):
        parts.append(f"{i['width']}×{i['height']}")
    if i.get("fps"):
        parts.append(f"{i['fps']:.2f}".rstrip("0").rstrip(".") + " fps")
    if i.get("vcodec"):
        parts.append(str(i["vcodec"]))
    if i.get("vbitrate"):
        parts.append(pr.fmt_rate(i["vbitrate"]))
    if i.get("size"):
        parts.append(f"{i['size'] / 1e6:.0f} MB")
    return " · ".join(parts)


class PrepareDialog(QDialog):
    def __init__(self, cfg, db, engine_running=False, parent=None, release=None):
        super().__init__(parent)
        self.setWindowTitle(T("Preparar videos"))
        adaptive.fit_window(self, 1080, 760)                       # sin pasarse de la pantalla (si no cabe, el contenido se desplaza)
        self.setMinimumWidth(800)
        self._compact = False                                      # ventana baja: se esconde la descripción para dejar sitio a la lista
        self.setStyleSheet(ui.BUTTONS_CSS + ui.BAR_CSS + ui.TABLE_CSS + ui.PANEL_CSS)
        self.cfg, self.db, self.engine_running = cfg, db, engine_running
        self.batch, self.applied, self.moved, self._key = None, False, 0, None
        self.restart_requested, self._auto_done = False, False
        self.release = release or (lambda: None)                   # suelta los videos que el reproductor tiene abiertos (traspaso directo)
        self.ctx, self.transferred = None, 0                       # contexto del traspaso directo y cuántos videos se cerraron bien
        self.inplace = True            # (u14) siempre se convierte dentro de la carpeta de videos: el modo «carpeta aparte» ya no está en la pantalla
        if not engine_running:                                     # restos de una conversión interrumpida («.verificando»): se limpian al abrir
            try:
                transfer.clean_stale(os.path.abspath(cfg["videos_dir"]))
            except Exception:                                      # noqa: BLE001: limpiar nunca debe impedir abrir la ventana
                pass
        self.tools = pr.find_tools(cfg.get("prepare", {}).get("ffmpeg_cmd", ""))
        self.ffjob, self.ffmode, self.ff_prog, self.ff_note = None, "", ("", 0, 0), ("", False)      # trabajo de ffmpeg de esta ventana
        self.tools_text, self._filter, self._tipped, self._ff_was_busy = "", None, set(), None

        # ---- encabezado: título, qué hace, y el estado de ffmpeg con su único botón
        title = QLabel(T("Preparar videos"))
        title.setObjectName("title")
        self.lbl_sub = sub = ui.label("Deja tus videos listos para que se vean fluidos y sincronizados. Los originales no se tocan: todo sale a la carpeta de destino.",
                       name="dim")
        sub.setToolTip(keep_sentences(T(
            "Preparar recodifica a H.264 con el MISMO bitrate de video, fps constantes y un keyframe por segundo. Aquí ves cada video y si funciona "
            "bien tal cual (Compatible), si se puede mejorar (Mejorable), si conviene prepararlo (No compatible) o si no se puede convertir.")))
        self.lbl_src = ui.label("", name="dim")
        self.lbl_src.setText(T("Carpeta de origen:") + " " + os.path.abspath(cfg["videos_dir"]))
        chip = QFrame()
        chip.setObjectName("chip")
        self.lbl_tools = QLabel("")
        self.lbl_tools.setTextFormat(Qt.RichText)
        self.lbl_tools.setWordWrap(True)
        self.btn_check = QPushButton(T("Cancelar"))
        self.btn_check.setVisible(False)
        self.btn_check.clicked.connect(self.check_updates)
        chl = QHBoxLayout(chip)
        chl.setContentsMargins(12, 8, 8, 8)
        chl.addWidget(self.lbl_tools, 1)
        chl.addWidget(self.btn_check)
        self.bar_tools = QProgressBar()
        self.bar_tools.setRange(0, 100)
        self.bar_tools.setTextVisible(False)
        self.bar_tools.setVisible(False)
        self.lbl_tools_note = ui.label("", name="dim")

        # ---- tarjetas de resumen (también filtran la lista)
        self.cards = {}
        cards = QHBoxLayout()
        cards.setSpacing(10)
        for key, caption, color in CARDS:
            card = ui.StatCard(key, caption, color)
            card.clicked.connect(lambda k=key: self._toggle_filter(k))
            self.cards[key] = card
            cards.addWidget(card, 1)

        # ---- opciones: carpeta de destino y alto máximo
        panel = QFrame()
        panel.setObjectName("panel")
        self.ed_dst = QLineEdit(default_dst(cfg["videos_dir"]))
        self.ed_dst.textEdited.connect(lambda *_: self._buttons())
        btn_dst = QPushButton(T("Examinar…"))
        btn_dst.clicked.connect(lambda _checked=False: self.pick_dst())
        self.cb_h = QComboBox()
        for h in HEIGHTS:
            self.cb_h.addItem(T(HEIGHT_LABELS[h]), h)
        cur = cfg.get("prepare", {}).get("max_height", 0)
        self.cb_h.setCurrentIndex(max(0, self.cb_h.findData(cur)))
        self.cb_h.currentIndexChanged.connect(self._on_height)
        cap_h = QLabel(T("Alto máximo"))
        cap_h.setObjectName("caption")
        grid = QGridLayout(panel)
        grid.setContentsMargins(12, 8, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.addWidget(cap_h, 0, 0)
        grid.addWidget(self.cb_h, 1, 0)
        grid.setColumnStretch(1, 1)
        # (u14) La carpeta de destino aparte (campo, «Examinar…», casilla de modo y «Usar los videos preparados») ya no se muestra: los widgets
        # siguen creados, sin ponerlos en pantalla, para el código del modo «carpeta aparte», que queda sin uso.
        self.chk_inplace = QCheckBox(T("Dejar los videos convertidos en la carpeta de videos (sin carpeta de destino aparte)"))
        self.chk_inplace.setChecked(self.inplace)
        self.chk_inplace.setEnabled(not engine_running)
        self.chk_inplace.setToolTip(T(
            "Detén el motor para usar esta opción (hay que soltar los videos que el reproductor tiene abiertos)." if engine_running else
            "Al convertir, el programa pregunta si el convertido reemplaza al original o se guarda al lado con «[Converted]» al final del nombre. "
            "Los vínculos tracks ↔ videos pasan al convertido y la lista de videos se pone al día sola."))
        self.chk_inplace.toggled.connect(self._on_inplace)
        self.ed_dst.setEnabled(not self.inplace)
        btn_dst.setEnabled(not self.inplace)
        self.btn_dst = btn_dst
        grid.setColumnStretch(0, 1)

        # ---- selección rápida y lista
        self.btn_need = QPushButton(T("Los que lo necesitan"))
        self.btn_need.setToolTip(T("Marca los videos «No compatible» y «Mejorable» que todavía no están preparados."))
        self.btn_need.clicked.connect(lambda _checked=False: self.mark_needed())
        self.btn_all = QPushButton(T("Todos"))
        self.btn_all.clicked.connect(lambda *_: self.mark_all(True))
        self.btn_none = QPushButton(T("Ninguno"))
        self.btn_none.clicked.connect(lambda *_: self.mark_all(False))
        self.btn_an = QPushButton(T("Analizar de nuevo"))
        self.btn_an.clicked.connect(lambda _checked=False: self.analyze())
        for b in (self.btn_need, self.btn_all, self.btn_none, self.btn_an):
            b.setObjectName("link")
        self.lbl_ready = QLabel("")
        self.lbl_ready.setObjectName("dim")
        sel = QHBoxLayout()
        sel.addWidget(QLabel(T("Marcar:")))
        for b in (self.btn_need, self.btn_all, self.btn_none):
            sel.addWidget(b)
        sel.addStretch(1)
        sel.addWidget(self.lbl_ready)
        sel.addWidget(self.btn_an)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([T(c) if c else "" for c in COLS])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(C_CHECK, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        hh.setSectionResizeMode(C_STATE, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_NOTE, QHeaderView.Stretch)
        hh.setSectionResizeMode(C_BTN, QHeaderView.Fixed)             # (ResizeToContents no cuenta los botones de las celdas: se mide aparte)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.itemChanged.connect(self._on_item_changed)         # marcar o desmarcar un video actualiza «Preparar videos seleccionados (N)»
        self.lbl_empty = ui.label("", name="dim")
        self.lbl_empty.setAlignment(Qt.AlignCenter)

        # ---- abajo: progreso general, frase de resumen, avisos y acciones
        self.bar_run = QProgressBar()
        self.bar_run.setRange(0, 100)
        self.bar_run.setTextVisible(False)
        self.bar_run.setVisible(False)
        self.lbl_sum = ui.label("")
        self.lbl_cpu = ui.label("Recodificar usa mucho CPU (corre con prioridad baja): mejor no hacerlo durante un set.", name="dim")
        self.btn_sel = QPushButton(T("Preparar videos seleccionados"))
        self.btn_sel.clicked.connect(lambda _checked=False: self.prepare_selected())
        self.btn_go = QPushButton(T("Preparar todos"))
        self.btn_go.setObjectName("primary")
        self.btn_go.setToolTip(T("Prepara los que lo necesitan y copia tal cual los compatibles, para dejar la carpeta de destino completa."))
        self.btn_go.clicked.connect(lambda _checked=False: self.prepare())
        self.btn_stop = QPushButton(T("Detener"))
        self.btn_stop.clicked.connect(lambda _checked=False: self.stop())
        self.btn_use = QPushButton(T("Usar los videos preparados"))
        self.btn_use.setObjectName("success")
        self.btn_use.clicked.connect(lambda _checked=False: self.apply())
        self.btn_close = QPushButton(T("Cerrar"))
        self.btn_close.clicked.connect(lambda _checked=False: self.reject())

        buttons = QHBoxLayout()
        for b in (self.btn_sel, self.btn_go, self.btn_stop):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_close)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(title)
        left.addWidget(sub)
        left.addWidget(self.lbl_src)
        left.addStretch(1)
        right.addWidget(chip)
        right.addWidget(self.bar_tools)
        right.addWidget(self.lbl_tools_note)
        right.addStretch(1)
        head = QHBoxLayout()
        head.addLayout(left, 3)
        head.addLayout(right, 2)
        # lo de arriba (estado de ffmpeg, tarjetas, opciones y lista) va en una zona con scroll: si la ventana es más baja que su contenido
        # (pantallas chicas) se baja con la rueda en vez de quedar cortado; lo de abajo (progreso y botones) queda siempre a la vista
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        bl.addLayout(head)
        bl.addLayout(cards)
        bl.addWidget(panel)
        bl.addLayout(sel)
        bl.addWidget(self.table, 1)
        bl.addWidget(self.lbl_empty, 1)
        self.table.setMinimumHeight(120)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        lay.addWidget(adaptive.scrolled(body), 1)
        lay.addWidget(self.bar_run)
        lay.addWidget(self.lbl_sum)
        lay.addWidget(self.lbl_cpu)
        lay.addLayout(buttons)

        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self._tick)
        self.ff_timer = QTimer(self)
        self.ff_timer.setInterval(200)
        self.ff_timer.timeout.connect(self._ff_tick)
        if not self.tools[0]:
            comp.ensure_base(self.cfg)                              # sin ffmpeg: se instala solo (si está permitido); aquí se ve el avance
        self.btn_use.setVisible(not self.inplace)
        self._refresh_mode_texts()
        self._show_tools()
        self._show_pending()
        self._refresh_cards()
        self._buttons()
        self.ff_timer.start()
        self.table.setFocus()                                       # el foco en la lista: no deja seleccionado el texto de la carpeta de destino

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_layout()

    def _apply_layout(self):
        """Disposición compacta (sin la descripción de arriba) o completa: la que pide el «Modo de Ventana» de «Ajustes» (si está en
        «Completo» o «Compacto» manda ese) o, en automático, la que corresponde al alto de la ventana."""
        mode = adaptive.resolve_mode(adaptive.layout_pref(self.cfg), self.height(), "compact" if self._compact else "normal", below=640, above=680)
        if (mode == "compact") != self._compact:
            self._compact = mode == "compact"
            self.lbl_sub.setVisible(not self._compact)

    def showEvent(self, e):
        """Al abrirse por primera vez analiza la carpeta solo (es rápido y en segundo plano): así se ve enseguida qué hace falta."""
        super().showEvent(e)
        self._apply_layout()
        if not self._auto_done:
            self._auto_done = True
            QTimer.singleShot(250, self._auto_analyze)

    def _auto_analyze(self):
        if self.tools[0] and self.batch is None:
            self.analyze(quiet=True)

    # ----------------------------------------------------------------------------------- estado de ffmpeg
    def _ff_busy(self):
        return ft.BASE.running or (self.ffjob is not None and self.ffjob.is_alive())

    def _tools_view(self):
        """(texto plano, color del punto, texto del aviso, ¿aviso de error?, progreso o None) del estado de ffmpeg."""
        if ft.BASE.running:
            prog = ft.BASE.prog
            return T("Instalando ffmpeg base (solo la primera vez)…"), ui.BLUE, T(ft.progress_text(prog)), False, prog
        if self.ffjob is not None and self.ffjob.is_alive():
            if self.ffmode == "check":
                return T("Consultando los repositorios…"), ui.BLUE, "", False, ("latest", 0, 0)
            return T("Instalando ffmpeg…"), ui.BLUE, T(ft.progress_text(self.ff_prog)), False, self.ff_prog
        note, is_err = self.ff_note
        if self.tools[0]:
            ver, origin = comp.describe_tool(self.tools[0], self.cfg.get("prepare", {}).get("ffmpeg_cmd", ""))
            text = T(f"ffmpeg {ver} · {origin}") if ver else T("ffmpeg") + " · " + self.tools[0]
            return text, ui.GREEN, T(note) if note else "", is_err, None
        if not note and ft.BASE.error is not None:
            note, is_err = f"No se pudo instalar el ffmpeg base: {ft.BASE.error}", True
        if not note:
            note = "Instálalo desde «Ajustes» → «Componentes»."
        return T("ffmpeg no está disponible"), ui.RED, T(note), is_err, None

    def _show_tools(self):
        text, color, note, is_err, prog = self._tools_view()
        self.tools_text = text
        self.lbl_tools.setText(ui.dot(color, text))
        self.lbl_tools_note.setText(keep_sentences(note) if note else "")
        self.lbl_tools_note.setStyleSheet(f"color: {ui.RED};" if is_err else "")
        self.bar_tools.setVisible(prog is not None)
        if prog is not None:
            frac = ft.progress_fraction(prog)
            self.bar_tools.setRange(0, 100 if frac is not None else 0)
            self.bar_tools.setValue(int(100 * frac) if frac is not None else 0)

    def _show_pending(self):
        """Sin ffmpeg no se puede analizar, pero la lista de videos se ve igual (todos «Sin analizar») en vez de una tabla vacía."""
        if self.tools[0] or self.batch is not None:
            return
        src = os.path.abspath(self.cfg["videos_dir"])
        vids = pr.list_videos(src)
        self._set_empty(not vids)
        self.table.setRowCount(len(vids))
        for i, v in enumerate(vids):
            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(i, C_CHECK, chk)
            for c, text in ((C_NAME, os.path.relpath(v, src)), (C_STATE, T("Sin analizar")), (C_NOTE, T("Necesita ffmpeg para analizarlo"))):
                self.table.setItem(i, c, QTableWidgetItem(text))
        self._refresh_cards()

    def _set_empty(self, empty):
        """Sin videos en la carpeta: en vez de una tabla vacía se explica qué pasa."""
        self.table.setVisible(not empty)
        self.lbl_empty.setText(T("No hay videos en la carpeta de origen. Cámbiala con «Cambiar ruta…» en la ventana principal.") if empty else "")

    def _refresh_tools(self):
        self.tools = pr.find_tools(self.cfg.get("prepare", {}).get("ffmpeg_cmd", ""))
        self._show_tools()
        self._show_pending()
        self._buttons()

    def _tools_changed(self):
        """Cambió el ffmpeg (terminó de instalarse o de actualizarse): se vuelve a buscar y, si ya hay, se analiza."""
        self._refresh_tools()
        if self.tools[0] and not (self.batch and self.batch.busy) and (self.batch is None or not self.batch.analyzed):
            self.analyze(quiet=True)

    # ------------------------------------------------------------------ «Verificar actualizaciones»
    def check_updates(self, *_):
        """El único botón de ffmpeg: consulta si hay una versión nueva (no descarga nada hasta que se confirme). Mientras se instala, cancela."""
        if ft.BASE.running:
            ft.BASE.cancel()
            return
        if self.ffjob is not None and self.ffjob.is_alive():
            if self.ffmode == "install":
                self.ffjob.cancel.set()
            return
        if (self.batch and self.batch.busy) or comp.legacy_active():
            return
        self.ff_note, self.ffmode = ("", False), "check"
        job = ft.Job(ft.latest, ft.Net())
        self.ffjob = job
        job.start()
        self._show_tools()
        self._buttons()

    def _ff_progress(self, phase, done, total):
        self.ff_prog = (phase, done, total)

    def _ff_run(self, rel):
        return comp.run_install(comp.SRC_FFMPEG, rel, ft.Net(), self._ff_progress, self.ffjob.cancel)

    def _ff_install(self, rel):
        self.ffmode, self.ff_prog = "install", ("download", 0, rel.size)
        job = ft.Job(self._ff_run, rel)
        self.ffjob = job
        job.start()

    def _ff_checked(self, job):
        e, rel = job.error, job.result
        if e is not None:
            comp.fail(comp.SRC_FFMPEG, str(e), network=isinstance(e, ft.NetworkError))
            self.ff_note = (str(e), True)
            return
        inst = ft.installed()
        if inst and not ft.is_newer(rel, inst):
            self.ff_note = (f"Ya tienes la última versión ({inst.get('version') or rel.version}).", False)
            return
        size = f" ({rel.size / 1e6:.0f} MB)" if rel.size else ""
        if inst:                                                                # hay una versión más nueva que la instalada
            go = ask_repo_update(self, "ffmpeg", comp.describe_update("ffmpeg", rel, inst), allow_later=False) == "yes"
        else:                                                                   # ffmpeg propio sin instalar
            go = ask_yes_no(self, "Instalar ffmpeg", f"¿Instalar ffmpeg {rel.version}{size} desde {rel.label}?",
                            "Se verifica con su suma SHA-256 publicada antes de abrirlo y se prueba antes de usarlo; si algo falla, lo que tienes ahora no se toca.")
        if go:
            self._ff_install(rel)
        else:
            self.ff_note = ("", False)

    def _ff_installed(self, job):
        e = job.error
        if e is not None:
            if isinstance(e, ft.Cancelled):
                self.ff_note = ("Cancelado.", False)
            else:
                comp.fail(comp.SRC_FFMPEG, str(e), network=isinstance(e, ft.NetworkError))
                self.ff_note = (str(e), True)
            return
        comp.ok(comp.SRC_FFMPEG)
        ft.BASE.clear()
        self.ff_note = (f"ffmpeg {(job.result or {}).get('version', '')} instalado y probado.", False)
        self._tools_changed()

    def _ff_tick(self):
        """Cada 200 ms: recoge el final de la instalación del ffmpeg base (la empiece quien la empiece) y de lo que se pidió en esta ventana."""
        try:
            if comp.base_poll() is not None:
                self.ff_note = ("", False)
                self._tools_changed()
            job = self.ffjob
            if job is not None and not job.is_alive():
                self.ffjob = None
                (self._ff_checked if self.ffmode == "check" else self._ff_installed)(job)
        except Exception:                                                       # noqa: BLE001: nunca se cae la ventana por esto
            dl.exception("preparar", "error al revisar ffmpeg en «Preparar videos»")
        self._show_tools()
        busy = self._ff_busy()
        if busy != self._ff_was_busy:
            self._ff_was_busy = busy
            self._buttons()

    # ----------------------------------------------------------------------------------- opciones
    def pick_dst(self):
        d = QFileDialog.getExistingDirectory(self, T("Carpeta de destino"), self.ed_dst.text() or os.getcwd())
        if d:
            self.ed_dst.setText(d)
            self._buttons()

    def _on_inplace(self, on):
        """La casilla «Dejar los videos convertidos en la carpeta de videos»: se guarda y la lista se vuelve a analizar (cambia dónde quedan)."""
        self.inplace = bool(on) and not self.engine_running
        self.cfg.setdefault("prepare", {})["inplace"] = self.inplace
        save_config(self.cfg)
        self.ed_dst.setEnabled(not self.inplace)
        self.btn_dst.setEnabled(not self.inplace)
        self.btn_use.setVisible(not self.inplace)
        self._refresh_mode_texts()
        if self.batch is not None and not self.batch.busy and self.tools[0]:
            self.analyze(quiet=True)
        else:
            self._buttons()

    def _refresh_mode_texts(self):
        if self.inplace:
            self.lbl_sub.setText(T("Deja tus videos listos para que se vean fluidos y sincronizados. Al convertir, eliges si el convertido reemplaza al original o se guarda al lado."))
            self.btn_go.setToolTip(T("Prepara los que lo necesitan y deja el resto como está."))
            if self.engine_running:
                self.lbl_sub.setText(T("Detén el motor para preparar videos: el reproductor tiene abiertos los archivos. Mientras tanto puedes analizarlos."))
                self.btn_go.setToolTip(T("Detén el motor para preparar videos (hay que soltar los videos que el reproductor tiene abiertos)."))
        else:
            self.lbl_sub.setText(T("Deja tus videos listos para que se vean fluidos y sincronizados. Los originales no se tocan: todo sale a la carpeta de destino."))
            self.btn_go.setToolTip(T("Prepara los que lo necesitan y copia tal cual los compatibles, para dejar la carpeta de destino completa."))

    def _on_height(self, *_):
        self.cfg.setdefault("prepare", {})["max_height"] = self.height_value()
        save_config(self.cfg)
        if self.batch is not None and not self.batch.busy and self.tools[0]:      # el alto cambia qué hace falta: se vuelve a analizar
            self.analyze(quiet=True)
        else:
            self._buttons()

    def height_value(self):
        v = self.cb_h.currentData()
        return v if v in HEIGHTS else 0

    def _current_key(self):
        if self.inplace:
            return (os.path.abspath(self.cfg["videos_dir"]), "en la carpeta de videos", self.height_value())
        return (os.path.abspath(self.cfg["videos_dir"]), os.path.abspath(self.ed_dst.text().strip() or "."), self.height_value())

    def fresh(self):
        """¿La lista que se ve corresponde a la carpeta de destino y al alto que están elegidos ahora?"""
        return bool(self.batch is not None and self.batch.analyzed and self._key == self._current_key())

    # ----------------------------------------------------------------------------------- análisis y preparación
    def _new_batch(self, quiet=False):
        """Lote nuevo con la carpeta de destino y el alto elegidos; None (con un aviso, salvo `quiet`) si algo no está bien."""
        def warn(text, info=False):
            if not quiet:
                (QMessageBox.information if info else QMessageBox.warning)(self, T("Preparar videos"), T(text))
        src, dst = os.path.abspath(self.cfg["videos_dir"]), self.ed_dst.text().strip()
        if self.inplace:
            b = pr.Batch(self.tools, src, src, self.height_value(), mode="inplace")
            if not b.rows:
                self._set_empty(True)
                warn("No hay videos en la carpeta de origen.", info=True)
                return None
            self._set_empty(False)
            self._key = self._current_key()
            return b
        if not dst:
            warn("Elige una carpeta de destino.")
            return None
        dst = os.path.abspath(dst)
        if inside(dst, src) or inside(src, dst):
            warn("La carpeta de destino no puede estar dentro de la carpeta de videos (ni al revés).")
            return None
        b = pr.Batch(self.tools, src, dst, self.height_value())
        if not b.rows:
            self._set_empty(True)
            warn("No hay videos en la carpeta de origen.", info=True)
            return None
        self._set_empty(False)
        self._key = (src, dst, self.height_value())
        return b

    def _size_btn_column(self):
        """La columna del botón mide lo que el texto más largo de los botones necesita (en el idioma de ahora) más su relleno, para que ninguno se corte."""
        try:
            fm = self.table.fontMetrics()
            w = max(fm.horizontalAdvance(T(t)) for t in BTN_TEXTS) + 46        # 2 × 12 px de relleno del estilo + borde + un margen
        except TypeError:                                                      # (el simulador de Qt de las pruebas no mide texto)
            return
        self.table.setColumnWidth(C_BTN, w)

    def _fill_rows(self):
        self._tipped = set()
        self._size_btn_column()
        self.table.setRowCount(len(self.batch.rows))
        for i, r in enumerate(self.batch.rows):
            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(i, C_CHECK, chk)
            for c, text in ((C_NAME, os.path.relpath(r.src, self.batch.src_root)), (C_STATE, ""), (C_NOTE, "")):
                self.table.setItem(i, c, QTableWidgetItem(text))
            btn = QPushButton("")
            btn.setObjectName("rowbtn")
            btn.clicked.connect(lambda _=False, i=i: self.prepare_row(i))
            self.table.setCellWidget(i, C_BTN, btn)
        self._apply_filter()

    def analyze(self, quiet=False):
        if not self.tools[0] or (self.batch and self.batch.busy) or self._ff_busy():
            return
        b = self._new_batch(quiet)
        if b is None:
            return
        self.batch = b
        self._fill_rows()
        b.analyze()
        self.timer.start()
        self._buttons()

    def prepare(self):
        """«Preparar todos»: prepara lo que lo necesita y copia tal cual lo compatible (la carpeta de destino queda completa)."""
        if not self.tools[0] or (self.batch and self.batch.busy) or self._ff_busy():
            return
        if not self.fresh():
            b = self._new_batch()
            if b is None:
                return
            self.batch = b
            self._fill_rows()
        self._run(None)

    def _run(self, rows):
        mode = None
        if self.inplace:
            mode = self._ask_transfer(rows)
            if mode is None:
                return
        self.batch.prepare(rows, mode=mode)
        self.timer.start()
        self._buttons()

    def _ask_transfer(self, rows):
        """Traspaso directo: «¿Reemplazar el video original una vez convertido?». Devuelve «replace», «keep» o None (cancelado / no se puede)."""
        todo = [r for r in (rows if rows is not None else self.batch.rows)
                if r.plan is not None and r.plan.action == "encode" and r.state in pr.ELIGIBLE + (pr.UNREADABLE,)]
        if not todo:
            return "keep"                                          # nada que convertir: no hay nada que preguntar
        if self.engine_running:
            QMessageBox.information(self, T("Preparar videos"), T("Detén el motor para dejar los convertidos en la carpeta de videos."))
            return None
        recycle = trash.has_recycle_bin(self.cfg["videos_dir"])
        size = sum((r.info or {}).get("size") or 0 for r in todo)
        answer = ask_replace_original(self, len(todo), size, recycle, trash.drive_kind(self.cfg["videos_dir"]))
        if answer not in ("replace", "keep"):
            return None
        self.ctx = transfer.Context(self.db, "recycle" if recycle else "delete")
        try:
            self.release()                                         # el reproductor suelta el video que tenía cargado (Windows no deja reemplazar un archivo abierto)
        except Exception:                                          # noqa: BLE001
            dl.exception("preparar", "no se pudieron soltar los videos del reproductor", level=dl.WARN)
        return answer

    def marked_rows(self):
        out = []
        if self.batch is None:
            return out
        for i, r in enumerate(self.batch.rows):
            it = self.table.item(i, C_CHECK)
            if it is not None and it.checkState() == Qt.Checked:
                out.append(r)
        return out

    def _on_item_changed(self, item):
        """Una casilla cambió (el usuario o «Marcar»): el botón «Preparar videos seleccionados» y su contador se ponen al día."""
        try:
            if item is not None and item.column() == C_CHECK and getattr(self, "btn_sel", None) is not None:
                self._buttons()
        except Exception:                                                   # noqa: BLE001: solo estético
            dl.exception("preparar", "no se pudo actualizar el botón de videos seleccionados", level=dl.WARN)

    def mark_needed(self):
        self._mark(lambda r: self.batch.needs_work(r))

    def mark_all(self, on):
        self._mark(lambda r: on and r.state in pr.ELIGIBLE + (pr.UNREADABLE,) and r.plan is not None)

    def _mark(self, cond):
        if self.batch is None:
            return
        for i, r in enumerate(self.batch.rows):
            it = self.table.item(i, C_CHECK)
            if it is not None:
                it.setCheckState(Qt.Checked if cond(r) else Qt.Unchecked)
        self._buttons()

    def prepare_selected(self):
        """«Preparar videos seleccionados»: solo los marcados con la casilla."""
        if not self.tools[0] or not self.fresh() or self.batch.busy or self._ff_busy():
            return
        rows = [r for r in self.marked_rows() if r.plan is not None and r.state in pr.ELIGIBLE + (pr.UNREADABLE,)]
        if not rows:
            QMessageBox.information(self, T("Preparar videos"), T("Marca primero los videos que quieres preparar."))
            return
        self._run(rows)

    def prepare_row(self, i):
        """El botón de un video: prepara solo ese."""
        if not self.tools[0] or not self.fresh() or self.batch.busy or self._ff_busy() or not (0 <= i < len(self.batch.rows)):
            return
        r = self.batch.rows[i]
        if r.plan is None or r.state not in pr.ELIGIBLE + (pr.UNREADABLE,):
            return
        self._run([r])

    def stop(self):
        if self.batch and self.batch.busy:
            self.batch.cancel()

    # ----------------------------------------------------------------------------------- tarjetas y filtro
    def _counts(self):
        out = {c: 0 for c in pr.CATEGORIES}
        for r in (self.batch.rows if self.batch is not None else ()):
            if r.state not in UNSEEN:
                out[row_cat(r)] += 1
        return out

    def _refresh_cards(self):
        b = self.batch
        if b is None:
            vids = self.table.rowCount() if not self.tools[0] else None          # sin ffmpeg la lista se ve (sin analizar): solo se sabe cuántos hay
            for key, card in self.cards.items():
                card.set_count(vids if key == "all" else None)
                card.set_enabled_filter(False)
                card.set_active(False)
            self.lbl_ready.setText("")
            return
        counts = self._counts()
        self.cards["all"].set_count(len(b.rows))
        for key in (pr.COMPATIBLE, pr.IMPROVABLE, pr.INCOMPATIBLE, pr.UNREADABLE):
            self.cards[key].set_count(counts[key] if any(r.state not in UNSEEN for r in b.rows) else None)
        for key, card in self.cards.items():
            card.set_enabled_filter(True)
            card.set_active(self._filter == key)
        ready = sum(1 for r in b.rows if r.state in OK_STATES)
        self.lbl_ready.setText((T(f"✔ {ready} de {len(b.rows)} listos") if self.inplace else T(f"✔ {ready} de {len(b.rows)} listos en la carpeta de destino"))
                               if b.rows else "")

    def _toggle_filter(self, key):
        """Pulsar una tarjeta deja ver solo los videos de esa clasificación; pulsarla otra vez (o «Videos») los muestra todos."""
        if self.batch is None:
            return
        self._filter = None if (key == "all" or self._filter == key) else key
        self._refresh_cards()
        self._apply_filter()

    def _apply_filter(self):
        if self.batch is None:
            return
        for i, r in enumerate(self.batch.rows):
            hide = self._filter is not None and (r.state in UNSEEN or row_cat(r) != self._filter)
            self.table.setRowHidden(i, hide)

    # ----------------------------------------------------------------------------------- avance
    def _state_text(self, r):
        icon = ICON.get(r.state, "")
        text = T(f"Preparando {int(r.pct * 100)} %") if r.state == "Preparando" else T(r.state)
        return f"{icon} {text}" if icon else text

    def _paint(self, item, key):
        col = COLOR.get(key)
        if col:
            item.setForeground(QBrush(QColor(col)))

    def summary(self):
        """La frase de abajo cuando no hay nada en marcha: qué conviene hacer (los números están en las tarjetas)."""
        b = self.batch
        if b is None or not b.analyzed:
            return b.message if b is not None and b.message else ""
        c = b.counts()
        text = ""
        if b.analyzed and not b.busy:
            def pending(cat):                                    # los de esa clasificación que aún no están en la carpeta de destino
                return sum(1 for r in b.rows if r.plan is not None and r.plan.category == cat and r.state in pr.ELIGIBLE)
            pend_inc, pend_imp = pending(pr.INCOMPATIBLE), pending(pr.IMPROVABLE)
            did = sum(1 for r in b.rows if r.plan is not None and r.plan.category in (pr.INCOMPATIBLE, pr.IMPROVABLE) and r.state in OK_STATES)
            if pend_inc:
                text = T(f"Conviene preparar los {pend_inc} no compatibles.")
            elif pend_imp:
                text = T("No hace falta preparar nada, aunque algunos se pueden mejorar.")
            elif did:
                text = T("Todo lo que lo necesitaba ya está preparado.")
            elif not c[pr.UNREADABLE]:
                text = T("No hace falta preparar nada: todos funcionan bien.")
        done = [r.job for r in b.rows if r.job is not None and r.job.stage == "done"]
        if done and not b.busy:
            if b.mode == "replace":
                gone = sum(1 for j in done if j.disposal in ("papelera", "definitivo"))
                text = T(f"{len(done)} videos convertidos") + "; " + T(f"{gone} originales reemplazados")
                if len(done) - gone:
                    text += "; " + T(f"{len(done) - gone} originales conservados")
            else:
                text = T(f"{len(done)} videos convertidos con «[Converted]» al lado de su original")
        if b.message:
            text += ("  —  " if text else "") + T(b.message)
        return keep_sentences(text)

    def run_text(self):
        """La línea de progreso mientras trabaja: «Preparando 3 de 8 · video.mp4 · quedan ~2 min 10 s»."""
        kind, done, total, _frac, eta, cur = self.batch.progress()
        if kind == "analyze":
            parts = [T(f"Analizando {min(total, done + (1 if cur else 0))} de {total}")]
        elif kind == "prepare":
            parts = [T(f"Preparando {min(total, done + (1 if cur else 0))} de {total}")]
        else:
            return ""
        if cur:
            parts.append(cur)
        if eta is not None:
            parts.append(T(f"quedan ~{ui.eta_text(eta)}"))
        return " · ".join(parts)

    def _tick(self):
        b = self.batch
        if b is None:
            self.timer.stop()
            return
        busy = b.busy
        self._finish_pending()
        for i, r in enumerate(b.rows):
            if self.table.item(i, C_STATE) is None:
                continue
            st = self.table.item(i, C_STATE)
            st.setText(self._state_text(r))
            self._paint(st, r.state)
            self.table.item(i, C_NOTE).setText(T(r.note))
            if r.info is not None and i not in self._tipped:
                self._tipped.add(i)
                self.table.item(i, C_NAME).setToolTip(info_tip(r))
            btn = self.table.cellWidget(i, C_BTN)
            if btn is not None:
                text, on, tip = row_button(r, busy)
                btn.setText(T(text))
                btn.setEnabled(on)
                btn.setToolTip(T(tip) if tip else "")
        kind, _d, total, frac, _eta, _cur = b.progress() if busy else ("", 0, 0, 0.0, None, "")
        self.bar_run.setVisible(bool(busy and total))
        self.bar_run.setValue(int(frac * 100))
        self.lbl_sum.setText(self.run_text() if busy else self.summary())
        self._refresh_cards()
        self._apply_filter()
        if not busy:
            self.timer.stop()
        self._buttons()

    def _finish_pending(self):
        """Traspaso directo: cierra los videos que el hilo de trabajo ya colocó (vínculos, tracks_db y original); se hace aquí, en el hilo de la interfaz."""
        if self.ctx is None or self.batch is None:
            return
        try:
            self.transferred += self.batch.finish_pending(self.ctx)
        except Exception:                                          # noqa: BLE001: un fallo aquí no debe tumbar la ventana
            dl.exception("preparar", "no se pudo cerrar un video convertido")

    def _buttons(self):
        busy = bool(self.batch and self.batch.busy)
        ffb = self._ff_busy()
        has = bool(self.tools[0])
        fresh = self.fresh()
        idle = not busy and not ffb
        self.btn_an.setEnabled(has and idle)
        self.btn_need.setEnabled(fresh and idle)
        self.btn_all.setEnabled(fresh and idle)
        self.btn_none.setEnabled(self.batch is not None and idle)
        n = len(self.marked_rows())
        self.btn_sel.setText(T("Preparar videos seleccionados") + (f" ({n})" if n else ""))
        self.btn_sel.setEnabled(has and fresh and idle and bool(n) and not self.engine_running)
        self.btn_go.setEnabled(has and idle and not self.engine_running)
        self.btn_stop.setEnabled(busy)
        installing = ft.BASE.running or (self.ffjob is not None and self.ffjob.is_alive() and self.ffmode == "install")
        self.btn_check.setText(T("Cancelar"))                  # ya no se busca aquí (solo desde «Componentes»): el botón solo aparece para cancelar
        self.btn_check.setEnabled(installing)
        self.btn_check.setVisible(installing)
        stale = self.batch is not None and self.batch.analyzed and not fresh
        tip = T("Cambió la carpeta de destino o el alto: pulsa «Analizar de nuevo».") if stale else ""
        for b in (self.btn_sel, self.btn_need, self.btn_all):
            b.setToolTip(tip)
        why = self.cant_apply()
        self.btn_use.setEnabled(not why)
        self.btn_use.setToolTip(T(why or "Usa la carpeta de destino como carpeta de videos y apunta los vínculos tracks ↔ videos a los videos preparados."))

    # ----------------------------------------------------------------------------------- usar los preparados
    def cant_apply(self):
        """Motivo por el que todavía no se pueden usar los videos preparados ('' = se puede)."""
        b = self.batch
        if self.inplace:
            return "No hace falta: los videos convertidos ya están en la carpeta de videos."
        if self.engine_running:
            return "Detén el motor para usar los videos preparados."
        if b is None or b.busy:
            return "Prepara los videos primero."
        if not all(r.state in OK_STATES or (r.state == pr.UNREADABLE and r.info is None) for r in b.rows):
            return "Faltan videos por preparar (o alguno dio error)."
        if not any(r.state in OK_STATES for r in b.rows):
            return "Prepara los videos primero."
        return ""

    def apply(self):
        if self.cant_apply():
            return
        b = self.batch
        mapping = b.mapping()
        n = sum(1 for e in self.db.tracks if e.get("video_path") and norm_path(e["video_path"]) in mapping)
        left = sum(1 for r in b.rows if r.state == pr.UNREADABLE)
        text = f"Se usará «{b.dst_root}» como carpeta de videos y {n} vínculos tracks ↔ videos apuntarán a los videos preparados."
        if left:
            text += f" {left} videos que no se pueden convertir se quedan solo en la carpeta original."
        if not ask_yes_no(self, "Usar los videos preparados", text,
                          "Antes se guarda una copia de tracks_db.json. Los videos originales no se tocan."):
            return
        try:
            if self.db.path.exists():
                shutil.copy2(self.db.path, str(self.db.path) + ".antes-de-preparar.bak")
            pr.relink(self.db.tracks, mapping)
            self.db.save()
            self.cfg["videos_dir"] = b.dst_root
            save_config(self.cfg)
        except OSError as e:
            dl.exception("preparar", "No se pudieron usar los videos preparados")
            QMessageBox.warning(self, T("Usar los videos preparados"), T(f"No se pudo guardar: {e}"))
            return
        self.applied, self.moved = True, n
        self.accept()

    # ----------------------------------------------------------------------------------- cierre
    def _shutdown(self):
        """Si se cierra a mitad de una preparación, se cancela y se espera: nunca queda ffmpeg suelto ni un video a medias. Lo que se pidió de
        ffmpeg desde esta ventana (consultar/actualizar) se cancela; la instalación del ffmpeg base sigue en segundo plano."""
        self.timer.stop()
        self.ff_timer.stop()
        if self.ffjob is not None and self.ffjob.is_alive():
            self.ffjob.cancel.set()
            self.ffjob.join(15)
        if self.batch and self.batch.busy:
            self.batch.cancel()
            self.batch.thread.join(15)
        self._finish_pending()                                       # un video ya colocado se cierra aunque se cierre la ventana (vínculos al día)

    def reject(self):
        self._shutdown()
        super().reject()

    def closeEvent(self, e):
        self._shutdown()
        super().closeEvent(e)
