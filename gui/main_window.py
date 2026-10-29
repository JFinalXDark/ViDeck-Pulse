# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ventana principal: lista de videos, estado en tiempo real, ajustes y botones de configuración."""
import json
import os
import time
import traceback

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase, QGuiApplication, QKeySequence, QPalette
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QApplication, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from core.capture import list_windows, window_matches
from core import autolink as al
from core import components as comp
from core import devlog as dl
from core import ffmpeg_tools as ft
from core import i18n
from core.config import apply_settings, build_settings, inspect_settings, migrate, save_config
from core.db import fmt_bpm, fmt_time
from core.glyphs import PAUSE, PLAY
from core.engine import Engine
from core.media import scan_videos, video_duration
from core.player import RES, VisualPlayer
from core.vision import tesseract_status
from gui import adaptive
from gui.calibration_dialog import CalibrationDialog
from gui.alerts import ask_new_version, ask_repo_update, ask_restart, ask_restart_text, ask_yes_no, not_detected
from gui.colors import (LEGEND, LOG_BG, LOG_BORDER, LOG_DIM, LOG_FG, LOG_SEL_BG, LOG_SEL_FG, RANDOM_COLOR, SYNC_COLOR,
                        row_color)
from gui.components_dialog import ComponentsDialog
from gui.config_dialog import ConfigDialog
from gui.icon import app_icon
from gui.output_window import MODES, OutputWindow
from gui.prepare_dialog import PrepareDialog
from gui.statusframe import StatusFrame
from gui.update_dialog import UpdateDialog
from gui.wheel import guard_wheel
from core import status as stmod
from core.synclog import fmt_line, synclog
from core import version
from core import transfer
from core import updatecheck
from core import videoscan
from core.i18n import tr as T, tf as TF           # T: texto que se muestra (con mayúscula inicial) · TF: trozo que se pega en medio de otro
from core.typo import keep_sentences


FIELD_W = 230            # ancho común de todos los campos de los ajustes (combos y números), para que queden parejos


def wrap_policy(lb, horizontal=QSizePolicy.Ignored):
    """Política de tamaño de un texto que se parte en líneas. IMPORTANTE: `setSizePolicy(h, v)` borra la marca «la altura
    depende del ancho» que QLabel se pone sola al activar el ajuste de línea; sin ella el layout reserva una altura cualquiera
    (huecos enormes bajo la etiqueta o texto cortado). Aquí se vuelve a activar."""
    sp = QSizePolicy(horizontal, QSizePolicy.Minimum)
    sp.setHeightForWidth(True)
    lb.setSizePolicy(sp)


def wrapped_check(text):
    """Casilla cuyo texto largo se parte en varias líneas (un QCheckBox normal no puede y ensancha todo el panel).
    Devuelve (casilla, contenedor): la casilla se usa como siempre y el contenedor es lo que se añade al layout."""
    chk = QCheckBox()
    lab = QLabel(keep_sentences(T(text)))
    lab.setWordWrap(True)
    wrap_policy(lab)
    lab.setAlignment(Qt.AlignLeft | Qt.AlignTop)
    lab.mousePressEvent = lambda e: chk.toggle()                    # el texto también marca la casilla
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    h.addWidget(chk, 0, Qt.AlignTop)
    h.addWidget(lab, 1)
    return chk, box


def note(text):
    """Texto explicativo que se parte en líneas según el ancho disponible (una frase corta baja entera a la línea de abajo)."""
    lb = QLabel(keep_sentences(T(text)))
    lb.setWordWrap(True)
    wrap_policy(lb)
    return lb


def centered_note(text, bold=False):
    """Texto centrado (se parte en líneas si no cabe), p. ej. la versión al pie de la pestaña «Ajustes»."""
    lb = QLabel(keep_sentences(T(text)))
    lb.setWordWrap(True)
    wrap_policy(lb)
    lb.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    if bold:
        f = lb.font()
        f.setBold(True)
        lb.setFont(f)
    return lb


def even_gap(extra, gaps, cap):
    """Aire (px) que se añade a cada separación entre filas para repartir `extra` px entre `gaps` separaciones: nunca negativo y
    nunca más que `cap` (en una pantalla altísima el resto queda abajo, junto al vúmetro, y no estira las filas hasta lo absurdo)."""
    if gaps <= 0 or extra <= 0:
        return 0
    return int(max(0, min(cap, extra // gaps)))


class _EvenFormLayout(QFormLayout):
    """QFormLayout que, con `fill` activo, reparte la altura que le sobra como aire entre sus filas (Qt las deja todas pegadas arriba y
    el resto en blanco). Lo que informa a los layouts de fuera (alto ideal, alto mínimo y alto para un ancho) es siempre el de las
    filas SIN el aire añadido: así el contenedor puede volver a encogerse al achicar la ventana (si no, el alto repartido de antes
    quedaría como suelo y la zona no bajaría nunca de ahí). Con la ventana baja o textos largos el aire es 0 y manda el scroll."""
    BASE_GAP = 6                                          # separación normal entre filas
    MAX_ADD = 48                                          # aire máximo añadido a cada separación

    def __init__(self, *args):
        super().__init__(*args)
        self.fill = False
        self._add = 0
        self.setVerticalSpacing(self.BASE_GAP)

    def _visible_rows(self):
        n = 0
        for i in range(self.rowCount()):
            for role in (QFormLayout.LabelRole, QFormLayout.FieldRole, QFormLayout.SpanningRole):
                it = self.itemAt(i, role)
                w = it.widget() if it is not None else None
                if w is not None and not w.isHidden():
                    n += 1
                    break
        return n

    def _added(self):
        return self._add * max(0, self._visible_rows() - 1)

    def heightForWidth(self, w):
        return max(0, super().heightForWidth(w) - self._added())

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width(), max(0, s.height() - self._added()))

    def minimumSize(self):
        s = super().minimumSize()
        return QSize(s.width(), max(0, s.height() - self._added()))

    def setGeometry(self, rect):
        try:
            n = self._visible_rows()
            add = even_gap(rect.height() - self.heightForWidth(rect.width()), n - 1, self.MAX_ADD) if self.fill else 0
            if add != self._add:
                self._add = add
                self.setVerticalSpacing(self.BASE_GAP + add)
        except Exception:                                  # noqa: BLE001: solo estético
            self._add = 0
        super().setGeometry(rect)


class _ScrollHost(QWidget):
    """Contenido de una zona con scroll. Con textos que se parten en líneas, Qt calcula la altura mínima del contenido con cada
    texto a su forma «ideal» (más angosta y alta que la real) y la usa como suelo de la altura de la zona: al bajar el scroll hasta
    el final quedaba un hueco en blanco enorme. Aquí la altura mínima sale de la altura REAL para el ancho actual (la da el propio
    layout), así que el scroll termina justo donde termina el contenido. Sin textos que se parten, vale la altura mínima normal."""

    def minimumSizeHint(self):
        base = super().minimumSizeHint()
        try:
            lay = self.layout()
            if lay is not None and lay.hasHeightForWidth():
                return QSize(base.width(), 0)
        except Exception:
            pass
        return base


def scroll_area(content, margin=8):
    """`content` dentro de una zona con su propio desplazamiento (vertical y, solo si hace falta, horizontal). Las pestañas de
    ajustes usan una cada una: el contenido nunca se estira para rellenar la altura de la pestaña más alta (sin huecos en
    blanco) y se baja con la rueda o la barra; al final del scroll no sobra espacio (ver `_ScrollHost`)."""
    host = _ScrollHost()
    v = QVBoxLayout(host)
    v.setContentsMargins(margin, margin, margin, margin)
    v.addWidget(content)
    v.addStretch(1)                                         # el espacio sobrante queda abajo, no repartido entre las filas
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    sa.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    sa.setWidget(host)
    sa.host_layout = v                                      # (el panel de estado cambia el reparto del espacio sobrante según la disposición)
    guard_wheel(host)                                       # la rueda desplaza la zona; no cambia un campo sin haberlo seleccionado
    return sa


def tidy_fields(root, skip=()):
    """Todos los campos (combos y números) con el mismo ancho máximo y sin forzar el ancho del panel: si falta espacio
    se encogen (los textos largos de un combo se recortan en el campo y se ven completos al desplegarlo)."""
    for kind in (QComboBox, QDoubleSpinBox, QSpinBox):
        for w in root.findChildren(kind):
            if w in skip:
                continue
            w.setMaximumWidth(FIELD_W)
            w.setMinimumWidth(70)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            if kind is QComboBox:
                fit_combo(w)


class _PlaceholderKeeper(QObject):
    """Mantiene el texto del campo de atajo en blanco. QKeySequenceEdit no tiene `setPlaceholderText` y vuelve a poner su
    «Press shortcut» cada vez que se reinicia (al asignarle un atajo, al pulsar «Quitar», al perder el foco…), así que no basta
    con ponerlo una vez: se vigila el cuadro de texto interno y, justo antes de dibujarlo, se corrige el texto si Qt lo cambió."""

    def __init__(self, line_edit, text):
        super().__init__(line_edit)
        self._text = text
        line_edit.installEventFilter(self)

    def eventFilter(self, obj, ev):
        try:
            if ev.type() == QEvent.Paint and obj.placeholderText() != self._text:
                obj.setPlaceholderText(self._text)
        except Exception:
            pass
        return False                                             # el evento sigue su camino: solo se corrige el texto


def _ks_placeholder(ks, text):
    """Texto del campo de atajo mientras está en blanco (Qt pone «Press shortcut»). Se pone en el cuadro de texto interno del
    editor y se mantiene (`_PlaceholderKeeper`). Nunca debe fallar."""
    try:
        text = T(text)
        if hasattr(ks, "setPlaceholderText"):                    # por si una versión de Qt lo trae
            ks.setPlaceholderText(text)
            return
        for le in ks.findChildren(QLineEdit):
            le.setPlaceholderText(text)
            try:
                _PlaceholderKeeper(le, text)
            except Exception:
                pass
    except Exception:
        pass


def fit_combo(cb):
    cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    cb.setMinimumContentsLength(6)
    fm = cb.fontMetrics()
    longest = max((fm.horizontalAdvance(cb.itemText(i)) for i in range(cb.count())), default=0)
    cb.view().setMinimumWidth(longest + 40)                        # la lista desplegable se ve completa
    cb.setToolTip(T(cb.currentText()))
    cb.currentIndexChanged.connect(lambda _=0, c=cb: c.setToolTip(T(c.currentText())))


class MainWindow(QMainWindow):
    def __init__(self, cfg, db):
        super().__init__()
        self.cfg, self.db = cfg, db
        self.engine, self.videos, self._active = None, [], None
        self._sig, self._partial, self._scanned = {}, {}, False    # videos de la última búsqueda completa {ruta: (tamaño, fecha)} · lo hallado en la que va · ¿ya hubo una completa?
        self._scan_job, self._scan_reason, self._scan_t, self._scan_tries = None, "", 0.0, 0
        self._prepare_open = False                              # «Preparar videos» abierto: la búsqueda automática espera (el diálogo mueve archivos)
        self.restart_requested = False                           # True: al cerrarse, main.py abre otra copia (cambio de idioma)
        self.libmpv_failed = False                               # True: el reproductor no pudo usar la libmpv (main.py no confirma la libmpv nueva)
        self._comp_job = None
        self._upd_job, self._upd_shim = None, None     # búsqueda de una versión nueva de ViDeck Pulse (solo la estable)
        self._closing = False                                    # True al cerrar: los hilos de fondo dejan de esperar
        self.setWindowTitle(T("ViDeck Pulse") + comp.title_suffix())      # «(Legacy Mode)» al final si está activo
        self.setWindowIcon(app_icon())
        self._balanced = False
        self._split_manual = False                               # True: el usuario arrastró el divisor estado/pestañas → no se reajusta solo
        self._split_seen = None                                  # alto que pedía el panel de estado en la última revisión (para esperar a que se estabilice)
        self._rebal_timer = QTimer(self)                         # reajuste del reparto estado/pestañas cuando cambia lo que muestra el panel
        self._rebal_timer.setSingleShot(True)
        self._rebal_timer.timeout.connect(self._auto_rebalance)
        self._mode, self._mode_pending, self._composing = "normal", False, False     # disposición: «normal» o «compact» (ver _compose)
        adaptive.fit_window(self, 1040, 860, min_w=620, min_h=470)     # sin pasarse de la pantalla (laptops de 1280×720 / 1366×768, o con escalado)

        self.embed = cfg["embed_mpv"]
        self.out = OutputWindow()
        self.out.mode_changed.connect(self._on_out_mode)            # Esc en el Reproductor (sale de pantalla completa)
        self.out.cycle_requested.connect(self._on_cycle_mode)       # atajo de teclado (opcional) para cambiar de modo
        self.out.set_mode(cfg["output"].get("window_mode", "window"), cfg["output"].get("screen", 0))   # antes de mostrarla
        self.startup_note, self.gl_note = "", ""
        self.startup_hint, self.selftest_note = "", ""     # la pestaña Reproductor solo dice si hay fundido cruzado; los datos técnicos van al panel
        self._layout_msg_until, self._view_seq, self._sync_seq = 0.0, 0, 0
        self._inc_seen, self._base = -1, {}                  # avisos abiertos ya dibujados · texto base de cada fila del panel
        self.ocr_ok, self.ocr_msg = tesseract_status(cfg["ocr"].get("tesseract_cmd", ""))
        if not self.ocr_ok:
            dl.error("ocr", self.ocr_msg)
            dl.devlog.condition("ocr", dl.ERROR, self.ocr_msg, rows=dl.OCR_ROWS)   # error abierto mientras no haya Tesseract
        self.player = self._make_player()
        if not self.embed:
            self.player.apply_window_mode(cfg["output"].get("window_mode", "window"))      # mpv con su propia ventana
        self.player.black_on_pause = cfg["output"]["black_on_pause"]
        self.player.fade = cfg["transition"]                  # mismo dict que la config: los cambios se aplican en vivo

        self._build_ui()
        self._apply_hotkey()
        self._apply_status_bar()
        self._scan_timer = QTimer(self)                       # recoge los avisos de la búsqueda de videos (que corre en otro hilo)
        self._scan_timer.timeout.connect(self._pump_scan)
        self.scan(reason="inicio")
        self.refresh_windows()
        self.apply_output()
        self._diag_timer = QTimer(self)                       # estado real de mpv en el panel aunque el motor esté detenido
        self._diag_timer.timeout.connect(self._refresh_player_row)
        self._diag_timer.start(1000)
        self._log_timer = QTimer(self)                        # registro: visor de la pestaña «Desarrollador» y marca ⚠
        self._log_timer.timeout.connect(self._pump_log)
        self._log_timer.start(500)
        self._log_startup()
        if not self.ocr_ok:                                   # sin Tesseract no se lee título/BPM/tiempos de Traktor
            QTimer.singleShot(600, self._warn_ocr)
        self._pump_log()
        self._sync_alerts(force=True)
        QTimer.singleShot(6000, self._components_startup)     # paquete Legacy y aviso de actualizaciones, sin apurar el arranque

    def statusBar(self):
        """Barra de estado plana (gui/statusframe.py): mismo uso que la QStatusBar de Qt."""
        return self._status

    def _apply_status_bar(self):
        """Casilla «Activar Barra de Estado» (pestaña Desarrollador): activada, la barra se ve siempre al pie de la ventana
        (plana, del color de la ventana); desactivada, solo aparece mientras muestra un mensaje."""
        self._status.set_always(bool(self.cfg["dev"].get("status_bar")))

    def _render_status(self):
        """«Estado:» + esfera verde (todo en orden) / «Alerta:» amarilla / error rojo, según los avisos abiertos del registro.
        Con varios avisos la barra los turna (5 s cada uno)."""
        try:
            self._status.set_states(stmod.compose_all(dl.devlog.incidents(), log_on=bool(self.cfg["dev"].get("log_enabled"))))
        except Exception:
            pass

    def _sync_alerts(self, force=False):
        """Si cambiaron los avisos abiertos: el ⚠ de cada fila del panel y la barra de estado."""
        v = dl.devlog.inc_ver
        if v == self._inc_seen and not force:
            return
        self._inc_seen = v
        for k in self.lbl:
            self._apply_mark(k)
        self._render_status()

    def _warn_ocr(self):
        QMessageBox.warning(self, T("Falta Tesseract (OCR)"),
                            T(self.ocr_msg + "\n\nSin él el programa no reconoce el track ni calcula la velocidad: "
                            "solo reproduce Random Visuals (la detección de sonido sí funciona)."))

    def _make_player(self):
        """Fundido cruzado real (dos videos mezclados en la GPU con OpenGL) o, si no está disponible, mpv clásico
        incrustado con fundido a negro."""
        tr = self.cfg["transition"]
        tr["enabled"] = tr.get("style", "crossfade") != "cut"
        if tr.get("style", "crossfade") == "crossfade" and not self.embed:
            self.startup_note = ("Fundido cruzado no disponible: necesita la salida «Reproductor del programa (incrustado)»; "
                                 "se usa el fundido a negro.")
            self.startup_hint = self.startup_note                  # corto y accionable: se ve también en la pestaña Reproductor
            dl.warn("fundido", self.startup_note)
        if tr.get("style", "crossfade") == "crossfade" and self.embed:
            view = player = None
            try:
                import mpv as _mpv
                if not hasattr(_mpv, "MpvRenderContext") or not hasattr(_mpv, "MpvGlGetProcAddressFn"):
                    raise RuntimeError("tu python-mpv es demasiado antiguo para el fundido cruzado (falta la API de render): "
                                       "actualiza con «pip install -U python-mpv»")
                from core.crossfade import CrossfadePlayer
                from gui.glview import GlVideoWidget
                view = GlVideoWidget()
                player = CrossfadePlayer(view, self.cfg["output"].get("hwdec", "auto-safe"))   # engancha sus capas al visor
                self.out.set_view(view)
                self.out.show()                                      # recién ahora Qt inicia OpenGL (ya con las capas)
                t0 = time.monotonic()
                while not (view.ready or view.error) and time.monotonic() - t0 < 4.0:   # espera al contexto OpenGL
                    QApplication.processEvents()
                    time.sleep(0.01)
                if not view.ready:
                    raise RuntimeError(view.error or "OpenGL no se inicializó")
                videos = scan_videos(self.cfg["videos_dir"])
                ok, detail = view.selftest(lambda: (QApplication.processEvents(), time.sleep(0.01)),
                                           sample=videos[0] if videos else None)
                if not ok:                                    # dibuja pero no llega imagen a la pantalla: no se usa
                    raise RuntimeError("la autoprueba no mostró imagen → " + detail)
                self.gl_note = f"{view.gl_info} · autoprueba OK ({detail})"
                self.selftest_note = f"autoprueba OK ({detail})"   # va a la fila «Reproductor» del panel avanzado
                return player
            except Exception as e:
                dl.exception("fundido", f"Fundido cruzado no disponible ({type(e).__name__}: {e})", level=dl.WARN)   # sigue con fundido a negro
                if comp.is_libmpv_failure(e):                  # la libmpv no sirve: error con su nombre y, después, el aviso de Legacy Mode
                    self.libmpv_failed = True
                    comp.fail(comp.SRC_MPV, f"el reproductor no pudo usar la libmpv ({type(e).__name__}: {e})")
                detail = traceback.format_exc()
                try:
                    vers = []
                    for name in ("mpv", "PySide6", "cv2", "numpy"):
                        try:
                            vers.append(f"{name} {getattr(__import__(name), '__version__', '?')}")
                        except Exception:
                            vers.append(f"{name} (no se pudo importar)")
                    with open("crossfade_error.txt", "w", encoding="utf-8") as fh:
                        fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + " · " + " · ".join(vers) + "\n\n" + detail)
                except OSError:
                    pass
                self.startup_note = (f"Fundido cruzado no disponible ({type(e).__name__}: {e}): se usa el fundido a negro "
                                     f"(detalle en crossfade_error.txt).")
                self.startup_hint = "Fundido cruzado no disponible: se usa el fundido a negro."   # el motivo va al panel avanzado y al registro
                print(self.startup_note)
                try:
                    if player:
                        player.shutdown()
                except Exception:
                    pass
                self.out.clear_view()
        wid = None
        if self.embed:
            self.out.show()
            wid = int(self.out.winId())
        player = VisualPlayer(wid, self.cfg["output"].get("hwdec", "auto-safe"))
        player.note = self.startup_note                       # el panel «Reproductor» muestra por qué no hay fundido cruzado
        return player

    def _set(self, section, key, value):
        self.cfg[section][key] = value
        save_config(self.cfg)

    # ------------------------------------------------------------------- UI
    def _build_ui(self):
        # izquierda: lista de videos
        self.listw = QListWidget()
        btn_dir = QPushButton(T("Cambiar ruta…"))
        btn_dir.setToolTip(T("Cambia la carpeta de la que se leen tus videos. Los videos que ya hay en la carpeta anterior no se mueven ni se borran."))
        btn_dir.clicked.connect(self.pick_folder)
        btn_rescan = QPushButton(T("Recargar"))
        btn_rescan.clicked.connect(self.scan)
        self.btn_force = QPushButton(T("Forzar video seleccionado"))
        self.btn_force.clicked.connect(self.toggle_force)
        row = QHBoxLayout()
        row.setSpacing(6)
        for b in (btn_dir, btn_rescan, self.btn_force):
            row.addWidget(b)
        try:                                                  # la columna de videos nunca es más angosta que sus tres botones (sin textos cortados)
            need = sum(int(b.sizeHint().width()) for b in (btn_dir, btn_rescan, self.btn_force)) + 2 * 6 + 16
        except Exception:                                     # noqa: BLE001: solo estético
            need = 0
        self._video_min_w = max(260, need)
        self.video_panel = QWidget()
        left = QVBoxLayout(self.video_panel)
        self._left_lay = left
        left.setContentsMargins(10, 10, 6, 6)                # la lista ya no queda pegada a los bordes (abajo, poco margen)
        left.setSpacing(8)
        legend = QLabel(T("<b>Videos</b><br>" + LEGEND))
        legend.setWordWrap(True)
        legend.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)   # «Videos» y la leyenda de colores centrados sobre la lista
        wrap_policy(legend)
        left.addWidget(legend, 0)
        left.addWidget(self.listw, 1)
        left.addLayout(row)

        # derecha: estado
        rows = ([("video", "Video activo"), ("track", "Track (OCR)"), ("bpm", "BPM detectado"),
                 ("speed", "Velocidad"), ("mode", "Modo"), ("deck", "Deck activo"), ("check", "Doble check")]
                + [(f"deck_{d}", f"Deck {d}") for d in self.cfg["decks"]]        # cada deck en su propia fila
                + [("sync", "Sincronización"), ("next", "Siguiente visual"), ("auto", "Autovinculación"),
                   ("capture", "Captura"), ("player", "Reproductor"), ("vu_dbg", "Sensores (debug)")])
        self.lbl = {k: QLabel("—") for k, _ in rows}
        form = _EvenFormLayout()
        self._form = form
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)                      # rótulo y texto de cada fila arrancan a la misma altura
        for key, text in rows:
            lb = self.lbl[key]
            lb.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            lb.setWordWrap(True)
            lb.setTextInteractionFlags(Qt.TextSelectableByMouse)               # se puede copiar un mensaje largo
            wrap_policy(lb, QSizePolicy.Expanding)
            form.addRow(T(text + ":"), lb)
        self.vu = QProgressBar()
        self.vu.setRange(0, 100)
        self.vu.setFormat(T("Vúmetro %p%"))
        g_state = QGroupBox(T("Estado del Reproductor"))
        v = QVBoxLayout(g_state)
        v.addLayout(form, 1)                                       # (las filas se llevan el alto sobrante; el vúmetro queda al pie)
        v.addWidget(self.vu)

        self.tabs = tabs = QTabWidget()                            # cada pestaña tiene su propio scroll vertical
        self._fill_tabs()
        tabs.currentChanged.connect(self._on_tab)

        btn_cfg = QPushButton(T("⚙  Configuración (tracks ↔ videos)"))
        btn_cfg.clicked.connect(self.open_config)
        self._det_texts = (T("✨  Detectar canción de los decks (vincular ahora)"), T("✨  Detectar canción de los decks"))    # (completo, compacto)
        btn_det = QPushButton(self._det_texts[0])
        btn_det.setToolTip(T("Compara la canción cargada en cada deck de Traktor con los videos sin vincular (título ≥ 70 % y\n"
                           "duración coincidente) y crea el vínculo con el BPM Base Pendiente. Con el motor iniciado."))
        btn_det.clicked.connect(self.detect_from_decks)
        btn_cal = QPushButton(T("🎯  Calibrar ROIs de Traktor"))
        btn_cal.clicked.connect(self.open_calibration)
        self.btn_run = QPushButton(T("▶  Iniciar motor"))
        self.btn_run.setMinimumHeight(30)                          # 25 % menos alto que antes (40): más espacio para los ajustes y el panel
        self.btn_run.clicked.connect(self.toggle_engine)

        # Cuatro zonas: videos · estado (con su scroll) · ajustes (pestañas, cada una con su scroll) · botones. Dos disposiciones (`_compose`):
        #   normal :  videos | [ estado / pestañas ] + botones      (ventanas altas)
        #   compact:  [ videos / pestañas ] + botones | estado      (ventanas bajas: el estado ocupa todo el alto y los ajustes tienen más)
        self.g_state = g_state
        self.status_scroll = status_scroll = scroll_area(g_state, margin=2)       # completo: lo que sobra queda abajo; compacto: se reparte entre filas (`_fill_status_column`)
        status_scroll.setMinimumHeight(130)
        tabs.setMinimumHeight(150)                                # (el reparto real lo marca _balance_split)
        self.actions = QWidget()                                  # los cuatro botones de abajo: van siempre junto a las pestañas
        self._act_grid = QGridLayout(self.actions)
        self._act_grid.setContentsMargins(0, 0, 0, 0)
        self._act_btns = (btn_cfg, btn_det, btn_cal, self.btn_run)
        self._arrange_actions(False)                              # (`_compose` los reparte en 1 columna o en 2 filas de 2 según la disposición)

        def vertical_splitter():
            sp = QSplitter(Qt.Vertical)
            sp.setChildrenCollapsible(False)
            sp.setHandleWidth(8)
            return sp
        self.vsplit = vertical_splitter()                         # normal : estado (73 %) / pestañas (27 %)
        self.vsplit.splitterMoved.connect(self._on_split_moved)   # (solo lo emite el arrastre del usuario, no `setSizes`)
        self.right_w = QWidget()
        self._right_lay = QVBoxLayout(self.right_w)
        self._right_lay.setContentsMargins(6, 10, 10, 6)          # mismos márgenes que la columna izquierda
        self._right_lay.addWidget(self.vsplit, 1)
        self.vsplit2 = vertical_splitter()                        # compact: videos / pestañas
        self.left2_w = QWidget()
        self._left2_lay = QVBoxLayout(self.left2_w)
        self._left2_lay.setContentsMargins(10, 10, 6, 6)
        self._left2_lay.addWidget(self.vsplit2, 1)
        self.status_holder = QWidget()                            # compact: el estado a la derecha, con sus márgenes
        self._status_lay = QVBoxLayout(self.status_holder)
        self._status_lay.setContentsMargins(6, 10, 10, 6)

        # videos | resto: mitad y mitad al abrir; el usuario arrastra el divisor para repartir el ancho
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(10)
        self._status = StatusFrame()                         # barra de estado plana de un extremo a otro, al pie de la ventana
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.split, 1)
        cl.addWidget(self._status, 0)
        self.setCentralWidget(central)                       # (el ancho mínimo lo marca el contenido de cada disposición, no un número fijo)
        self._compose(adaptive.resolve_mode(adaptive.layout_pref(self.cfg), self.height(), "normal"))      # la que corresponde al «Modo de Ventana» y al tamaño inicial
        self._apply_sensor_row()

    # ---------------------------------------------------------------- disposición adaptable (normal / compact)
    def _compose(self, mode):
        """Coloca videos, estado, pestañas y botones según `mode` («normal» o «compact»). Se puede llamar las veces que haga falta: primero
        suelta las cuatro zonas de donde estén (Qt las saca solas del divisor o del layout anterior) y luego las pone donde toca."""
        if self._composing:
            return
        self._composing = True
        try:
            compact = mode == "compact"
            sp, vn, vc = self.split, self.vsplit, self.vsplit2
            for w in (self.video_panel, self.status_scroll, self.tabs, self.actions, self.right_w, self.left2_w, self.status_holder):
                w.setParent(None)
            if compact:
                vc.insertWidget(0, self.video_panel)
                vc.insertWidget(1, self.tabs)
                self._left2_lay.addWidget(self.actions)
                self._status_lay.addWidget(self.status_scroll)
                sp.insertWidget(0, self.left2_w)
                sp.insertWidget(1, self.status_holder)
                self._left_lay.setContentsMargins(0, 0, 0, 0)         # (los márgenes los pone la columna que lo contiene)
                self.video_panel.setMinimumSize(0, 110)
                self.left2_w.setMinimumWidth(max(380, self._video_min_w))
                self.status_holder.setMinimumWidth(340)
                self.right_w.setMinimumWidth(0)
                vc.setStretchFactor(0, 52)                            # lista de videos algo más alta; las pestañas ~13 % más bajas (55 → 48)
                vc.setStretchFactor(1, 48)
                sp.setStretchFactor(0, 9)                             # el estado toma algo más de la mitad del ancho (11 de 20): con sus textos
                sp.setStretchFactor(1, 11)                            # largos en pantallas de 1280×720 se parte en menos líneas y cabe sin scroll
                sp.setSizes([900, 1100])
            else:
                vn.insertWidget(0, self.status_scroll)
                vn.insertWidget(1, self.tabs)
                self._right_lay.addWidget(self.actions)
                sp.insertWidget(0, self.video_panel)
                sp.insertWidget(1, self.right_w)
                self._left_lay.setContentsMargins(10, 10, 6, 6)       # la lista no queda pegada a los bordes (abajo, poco margen)
                self.video_panel.setMinimumSize(self._video_min_w, 0)
                self.right_w.setMinimumWidth(380)
                self.left2_w.setMinimumWidth(0)
                self.status_holder.setMinimumWidth(0)
                vn.setStretchFactor(0, 73)                            # al cambiar el alto de la ventana se mantiene 73 / 27
                vn.setStretchFactor(1, 27)
                sp.setStretchFactor(0, 1)
                sp.setStretchFactor(1, 1)
                sp.setSizes([1000, 1000])
            self._fill_status_column(compact)
            self._arrange_actions(compact)
            changed = mode != self._mode
            self._mode = mode
            if changed:
                self._split_manual = False                            # otra disposición: el reparto vuelve a ser automático
            if changed and self._balanced:                            # (la primera vez lo reparte showEvent)
                QTimer.singleShot(0, self._balance_split)             # (con la ventana ya repartida por el diseño de Qt)
        finally:
            self._composing = False
        self._refresh_layout_buttons()                                # (el aviso «ahora: …» de «Modo de Ventana» sigue a la disposición real)

    def _arrange_actions(self, compact):
        """Los cuatro botones de abajo: en una columna (completo) o en 2 filas de 2 (compacto: la lista y las pestañas ganan ~70 px de
        alto). En compacto «Detectar canción…» usa su rótulo corto (el largo sigue en su ayuda) para que no se corte en media columna."""
        try:
            g = self._act_grid
            for _ in self._act_btns:
                g.takeAt(0)                                   # (solo suelta los botones del layout; siguen siendo hijos de `actions`)
            cfg, det, cal, run = self._act_btns
            pos = ((cfg, 0, 0), (cal, 0, 1), (det, 1, 0), (run, 1, 1)) if compact else ((cfg, 0, 0), (det, 1, 0), (cal, 2, 0), (run, 3, 0))
            for b, r, c in pos:
                g.addWidget(b, r, c)
            g.setColumnStretch(0, 3 if compact else 1)        # (Configuración y Detectar son los rótulos más largos)
            g.setColumnStretch(1, 2 if compact else 0)
            det.setText(self._det_texts[1 if compact else 0])
        except Exception:
            dl.exception("interfaz", "no se pudo reordenar los botones de abajo", level=dl.WARN)       # solo estético

    def _fill_status_column(self, fill):
        """Compacto: «Estado del Reproductor» ocupa TODO el alto de la columna derecha y la altura que sobra se reparte como aire entre
        sus filas (el vúmetro queda al pie), sin hueco en blanco debajo; si las filas necesitan más espacio (textos largos o ventana
        baja) el aire se reduce solo y, si ni así caben, se baja con el scroll. Completo: como antes (lo que sobra queda abajo)."""
        try:
            lay = getattr(self.status_scroll, "host_layout", None)
            if lay is not None:
                lay.setStretch(0, 1 if fill else 0)                # contenido: toma el alto sobrante
                lay.setStretch(1, 0 if fill else 1)                # (espaciador de abajo)
            form = getattr(self, "_form", None)
            if form is not None:
                form.fill = bool(fill)                             # las filas reparten el aire entre ellas (ver `_EvenFormLayout`)
                form.invalidate()
        except Exception:
            dl.exception("interfaz", "no se pudo ajustar el panel de estado", level=dl.WARN)       # solo estético

    # ---------------------------------------------------------------- «Modo de Ventana» (a mano)
    LAYOUT_NAMES = {"normal": "Completo", "compact": "Compacto"}

    def _on_layout_button(self, kind):
        """Botón «Automático» / «Completo» / «Compacto» de «Modo de Ventana»: fija ese modo (o vuelve al automático, en el que la ventana
        sigue adaptándose a su alto). Se guarda y se aplica al momento, en la ventana principal y en «Preparar videos» (que lee el
        mismo ajuste). Volver a pulsar el botón ya marcado no cambia nada (para volver al automático está su botón)."""
        if kind not in adaptive.LAYOUT_MODES:
            return
        ui = self.cfg.setdefault("ui", {})
        ui["layout_mode"] = kind
        save_config(self.cfg)
        self._apply_layout_pref()

    def _apply_layout_pref(self):
        """Pone la disposición que corresponde al «Modo de Ventana» del config (también tras restablecer o cargar ajustes)."""
        want = adaptive.resolve_mode(adaptive.layout_pref(self.cfg), self.height(), self._mode)
        if want != self._mode:
            self._compose(want)
        self._refresh_layout_buttons()

    def _refresh_layout_buttons(self):
        """Marca el botón activo y escribe, debajo, si es automático (y cuál es ahora) o si está fijado a mano."""
        auto, full, comp, lbl = (getattr(self, n, None) for n in ("btn_lm_auto", "btn_lm_full", "btn_lm_compact", "lbl_lm_note"))
        if auto is None or full is None or comp is None or lbl is None:
            return
        pref = adaptive.layout_pref(self.cfg)
        for b, on in ((auto, pref == "auto"), (full, pref == "full"), (comp, pref == "compact")):
            b.blockSignals(True)
            b.setChecked(on)
            b.blockSignals(False)
        now = self.LAYOUT_NAMES.get(self._mode, "Completo")
        if pref == "auto":
            lbl.setText(keep_sentences(T(f"Automático: la ventana elige sola el modo según su alto (ahora: {now}).")))
        else:
            lbl.setText(keep_sentences(T("Modo fijado a mano. Pulsa «Automático» para que la ventana elija sola.")))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._queue_mode()
        self._queue_rebalance()                                   # (otro ancho → los textos se parten distinto → el estado pide otro alto)

    def _queue_mode(self):
        """Si por el tamaño actual corresponde otra disposición, la cambia (fuera del evento de tamaño, para no repartir mientras Qt reparte)."""
        if self._mode_pending or adaptive.resolve_mode(adaptive.layout_pref(self.cfg), self.height(), self._mode) == self._mode:
            return                                                # (con un modo fijado a mano esto nunca cambia de disposición)
        self._mode_pending = True
        QTimer.singleShot(0, self._apply_mode)

    def _apply_mode(self):
        self._mode_pending = False
        try:
            want = adaptive.resolve_mode(adaptive.layout_pref(self.cfg), self.height(), self._mode)
            if want != self._mode:
                self._compose(want)
        except Exception:
            dl.exception("interfaz", "no se pudo cambiar la disposición de la ventana", level=dl.WARN)      # solo estético

    TAB_DEV = 4                                                  # índice de la pestaña «Desarrollador»

    def showEvent(self, e):
        super().showEvent(e)
        if not self._balanced:                                   # solo al abrir: después manda el divisor del usuario
            self._balanced = True
            QTimer.singleShot(0, self._balance_split)

    def _status_need(self):
        """Alto que pide el panel «Estado del Reproductor» con el ancho REAL de su zona. Los textos que se parten en líneas dependen del
        ancho: `sizeHint` los mide con su forma «ideal» (no la real) y se equivoca, así que se pregunta al layout por el alto para ese ancho."""
        host = self.status_scroll.widget()
        lay = host.layout() if host is not None else None
        try:
            width = int(self.status_scroll.viewport().width())
            if lay is not None and width > 0 and lay.hasHeightForWidth():
                return int(lay.totalHeightForWidth(width)) + 8
        except Exception:                                         # noqa: BLE001: se usa la medida aproximada
            pass
        return int(self.g_state.sizeHint().height()) + 8

    def _balance_split(self):
        """Reparto de la altura del lado derecho: el panel de estado un 73 % (o lo que necesite, si es menos: sin huecos)
        y los ajustes el resto (27 % como mínimo). Lo que no quepa se baja con el scroll de cada zona."""
        try:
            if self._mode == "compact":                          # videos 52 % (como mínimo lo que cabe) / pestañas el resto
                total = int(self.vsplit2.height())
                if total > 0:
                    top = max(110, total * 52 // 100)
                    self.vsplit2.setSizes([top, max(1, total - top)])
                return
            total = int(self.vsplit.height())
            if total <= 0:
                return
            need = self._status_need()
            status = max(130, min(need, total * 73 // 100))
            self.vsplit.setSizes([status, total - status])
            self._split_seen = need
        except Exception:
            dl.exception("interfaz", "no se pudo repartir la altura del panel", level=dl.WARN)   # solo estético

    def _on_split_moved(self, *_):
        """El usuario arrastró el divisor estado/pestañas: se respeta (no se vuelve a reajustar solo) hasta que cambie lo que muestra el panel
        (panel avanzado, sensores) o la disposición."""
        if not self._composing:
            self._split_manual = True

    def _queue_rebalance(self, delay=300):
        """Pide revisar el reparto estado/pestañas (con retraso y una sola vez aunque lleguen muchos cambios seguidos)."""
        timer = getattr(self, "_rebal_timer", None)
        if timer is None or getattr(self, "_mode", "normal") != "normal" or getattr(self, "_split_manual", False) or getattr(self, "_composing", False):
            return
        try:
            if not timer.isActive():
                timer.start(delay)
        except Exception:                                         # noqa: BLE001: solo estético
            pass

    def _auto_rebalance(self):
        """Si el panel de estado pide un alto distinto del que tiene (porque cambió lo que muestra o el ancho), vuelve a repartir: las pestañas
        crecen para ocupar el espacio que deja el estado (sin hueco en blanco) y el estado crece si le falta. Espera a que el alto se
        estabilice (dos revisiones seguidas iguales) y solo mueve el divisor si la diferencia pasa de 12 px, para que no «baile»."""
        try:
            if self._mode != "normal" or self._split_manual or self._composing or not self.isVisible():
                return
            total = int(self.vsplit.height())
            if total <= 0:
                return
            need = self._status_need()
            if need != self._split_seen:
                self._split_seen = need                           # sigue cambiando: se mira otra vez en un momento
                self._rebal_timer.start(300)
                return
            want = max(130, min(need, total * 73 // 100))
            have = int(self.vsplit.sizes()[0])
            if abs(want - have) > 12:
                self.vsplit.setSizes([want, total - want])
        except Exception:
            dl.exception("interfaz", "no se pudo reajustar el reparto del panel", level=dl.WARN)   # solo estético

    def _status_content_changed(self):
        """El usuario cambió lo que muestra el panel de estado (panel avanzado, sensores): vuelve el reparto automático (aunque hubiera
        movido el divisor a mano) y se ajusta ya, y otra vez en un momento por si los textos aún no habían tomado su alto."""
        self._split_manual = False
        if self._mode == "normal":
            QTimer.singleShot(0, self._balance_split)
            self._queue_rebalance()

    def _fill_tabs(self):
        t = self.tabs
        t.addTab(scroll_area(self._tab_output()), T("Salida"))
        t.addTab(scroll_area(self._tab_player()), T("Reproductor"))
        t.addTab(scroll_area(self._tab_capture()), T("Captura"))
        t.addTab(scroll_area(self._tab_sync()), T("Sincronización"))
        t.addTab(scroll_area(self._tab_dev()), T("Desarrollador"))
        t.addTab(scroll_area(self._tab_settings()), T("Ajustes"))

    def _rebuild_tabs(self):
        """Vuelve a crear las pestañas de ajustes con lo que hay en el config (tras restablecer o cargar un archivo)."""
        cur = self.tabs.currentIndex()
        self.tabs.blockSignals(True)
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()                                      # (diferido: el botón que lo pidió aún está en su clic)
        self._fill_tabs()
        self.tabs.setCurrentIndex(max(0, min(cur, self.tabs.count() - 1)))
        self.tabs.blockSignals(False)
        self._view_seq = dl.devlog.last_seq

    def _tab_output(self):
        self.cb_res = QComboBox()
        self.cb_res.addItems(["1080p", "720p"])
        self.cb_res.setCurrentText(self.cfg["output"]["resolution"])
        self.cb_res.currentIndexChanged.connect(self.apply_output)
        self.cb_screen = QComboBox()
        for i, s in enumerate(QGuiApplication.screens()):
            self.cb_screen.addItem(T(f"Pantalla {i + 1}: {s.name()}"))
        self.cb_screen.setCurrentIndex(max(0, min(int(self.cfg["output"].get("screen", 0) or 0), self.cb_screen.count() - 1)))
        self.cb_screen.currentIndexChanged.connect(self._on_screen)
        self.cb_mode = QComboBox()                               # Reproductor: con bordes · sin bordes · pantalla completa
        for key, text in (("window", "Ventana"), ("borderless", "Ventana Sin Bordes"), ("fullscreen", "Pantalla completa")):
            self.cb_mode.addItem(T(text), key)
        self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(self.cfg["output"].get("window_mode", "window"))))
        self.cb_mode.currentIndexChanged.connect(self._on_window_mode)
        self.cb_mode.setToolTip(T("Ventana: ventana normal, con barra de título.\n"
                                "Ventana Sin Bordes: sin bordes ni barra de título (ideal para transmitir la ventana por Discord:\n"
                                "no se ve el borde superior); se mueve arrastrándola.\n"
                                "Pantalla completa: a toda la pantalla elegida en «Pantalla» (Esc para salir)."))
        self.ks_mode = QKeySequenceEdit()                        # atajo para cambiar de modo (en blanco por defecto)
        if hasattr(self.ks_mode, "setMaximumSequenceLength"):
            self.ks_mode.setMaximumSequenceLength(1)             # una sola tecla o combinación (no una secuencia de varias)
        _ks_placeholder(self.ks_mode, "Asignar Tecla")           # en lugar del «Press shortcut» de Qt: el espacio en blanco se puede asignar
        self.ks_mode.setKeySequence(QKeySequence(str(self.cfg["output"].get("mode_hotkey", "") or ""), QKeySequence.PortableText))
        self.ks_mode.keySequenceChanged.connect(self._on_hotkey_edit)
        self.ks_mode.setToolTip(T("Haz clic aquí y pulsa la tecla (o combinación) que quieras. En blanco = sin atajo.\n"
                                "Cambia el modo en orden: Ventana → Ventana Sin Bordes → Pantalla completa → Ventana.\n"
                                "Solo funciona con el Reproductor seleccionado (abierto y sin minimizar).\n"
                                "Esc no se puede usar: está reservada para salir de la pantalla completa."))
        self.btn_hk_clear = QPushButton(T("Quitar"))
        self.btn_hk_clear.setToolTip(T("Deja el atajo en blanco."))
        self.btn_hk_clear.clicked.connect(self._clear_hotkey)
        hk = QWidget()
        hkl = QHBoxLayout(hk)
        hkl.setContentsMargins(0, 0, 0, 0)
        hkl.addWidget(self.ks_mode, 1)
        hkl.addWidget(self.btn_hk_clear)
        btn_full = QPushButton(T("Visor a pantalla completa"))
        btn_full.clicked.connect(self.fullscreen_out)
        self.cb_embed = QComboBox()
        self.cb_embed.addItem(T("Reproductor del programa (incrustado)"), True)
        self.cb_embed.addItem(T("Ventana propia de mpv"), False)
        self.cb_embed.setCurrentIndex(0 if self.cfg["embed_mpv"] else 1)
        self.cb_embed.currentIndexChanged.connect(self._on_embed)
        self.cb_hw = QComboBox()
        self.cb_hw.addItem(T("GPU (automático)"), "auto-safe")
        self.cb_hw.addItem(T("CPU (sin GPU; el más compatible)"), "no")
        cur_hw = self.cfg["output"].get("hwdec", "auto-safe")
        if self.cb_hw.findData(cur_hw) < 0:
            self.cb_hw.addItem(T(f"Personalizado ({cur_hw})"), cur_hw)
        self.cb_hw.setCurrentIndex(self.cb_hw.findData(cur_hw))
        self.cb_hw.currentIndexChanged.connect(self._on_hwdec)
        self.btn_test = QPushButton(T("Probar salida (patrón de prueba)"))
        self.btn_test.setToolTip(T("Muestra el patrón de prueba de mpv en la salida (con el motor detenido)."))
        self.btn_test.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.btn_test.setCheckable(True)
        self.btn_test.toggled.connect(self.toggle_test)
        btn_full.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        w = QWidget()
        f = QFormLayout(w)
        f.setRowWrapPolicy(QFormLayout.WrapLongRows)
        f.addRow(T("Resolución:"), self.cb_res)
        f.addRow(note("Los fps los define cada video; la salida va a 60 Hz."))
        f.addRow(T("Modo del Reproductor:"), self.cb_mode)
        f.addRow(note("«Sin Bordes» oculta la barra de título (ideal para Discord) y se mueve arrastrando. Esc sale de pantalla completa."))
        f.addRow(T("Atajo para cambiar de modo:"), hk)
        f.addRow(note("Opcional: pasa de Ventana a Sin Bordes y a Pantalla completa. Solo con el Reproductor activo."))
        f.addRow(T("Pantalla:"), self.cb_screen)
        f.addRow(T("Salida de video:"), self.cb_embed)
        f.addRow(T("Decodificación:"), self.cb_hw)
        f.addRow(btn_full)
        f.addRow(self.btn_test)
        tidy_fields(w)
        return w

    # ----------------------------------------------- pestaña «Reproductor»
    def _tab_player(self):
        """Cómo y CUÁNDO cambia el visual: transición, duración del fundido y anticipación (cuántos segundos antes de que
        termine el track que suena empieza a entrar el visual del siguiente deck)."""
        n, t = self.cfg["next"], self.cfg["transition"]
        self.cb_style = QComboBox()
        for key, text in (("crossfade", "Fundido cruzado (el siguiente se carga detrás)"),
                          ("fade", "Fundido a negro"), ("cut", "Corte (sin transición)")):
            self.cb_style.addItem(T(text), key)
        self.cb_style.setCurrentIndex(max(0, self.cb_style.findData(t.get("style", "crossfade"))))
        self.cb_style.currentIndexChanged.connect(self._on_style)
        self.sp_fade = self._spin_s(t["duration"], "duration")
        self.sp_rand = self._spin_s(t["random_duration"], "random_duration")
        self.lbl_style = note("")
        self._style_note()

        def nspin(key, lo, hi, step):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(0)
            sp.setSuffix(" s")
            sp.setValue(n[key])
            sp.valueChanged.connect(lambda x, k=key: self._set("next", k, x))
            return sp

        self.chk_next, box_next = wrapped_check("Anticipar el visual del siguiente deck")
        box_next.setToolTip(T("Con el track que suena por terminar, el siguiente visual (el del track del otro deck, o un Random\n"
                            "Visual) entra con la transición elegida cuando quedan los segundos de «Empezar el fundido».\n"
                            "Desactivado, el visual cambia cuando el otro deck empieza a sonar y toma el mando."))
        self.chk_next.setChecked(n["enabled"])
        self.chk_next.toggled.connect(lambda x: (self._set("next", "enabled", x), self._timing_note()))
        self.sp_lead = nspin("lead", 2, 45, 1)
        self.sp_lead.setToolTip(T("Segundos que le quedan al track que suena cuando EMPIEZA a entrar el siguiente visual\n"
                                "(por defecto 10 s: el fundido se hace en los últimos 10 s del track)."))
        self.sp_watch = nspin("watch", 20, 180, 5)
        self.sp_watch.setToolTip(T("Desde cuándo se vigila el otro deck (¿hay un track cargado? ¿su visual está en la lista?).\n"
                                 "Se ajusta solo si es menos que la anticipación + 10 s."))
        for sp in (self.sp_fade, self.sp_rand, self.sp_lead):
            sp.valueChanged.connect(lambda _x: self._timing_note())
        self.lbl_timing = note("")
        self._timing_note()
        w = QWidget()
        f = QFormLayout(w)
        f.setRowWrapPolicy(QFormLayout.WrapLongRows)
        f.addRow(T("Transición:"), self.cb_style)
        f.addRow(T("Duración del fundido (visual de un track):"), self.sp_fade)
        f.addRow(T("Duración del fundido (hacia un Random Visual):"), self.sp_rand)
        f.addRow(self.lbl_style)
        f.addRow(box_next)
        f.addRow(T("Empezar el fundido cuando al track le queden:"), self.sp_lead)
        f.addRow(T("Vigilar el deck siguiente cuando queden:"), self.sp_watch)
        f.addRow(self.lbl_timing)
        tidy_fields(w)
        return w

    def _timing_note(self):
        """Una frase con cuándo empieza y cuándo termina el fundido, según la anticipación y las duraciones elegidas."""
        n, t = self.cfg["next"], self.cfg["transition"]
        lead, d1, d2 = float(n["lead"]), float(t["duration"]), float(t["random_duration"])
        if not n.get("enabled", True):
            txt = "Anticipación desactivada: el visual cambia cuando el otro deck empieza a sonar (casi al final del track)."
        elif t.get("style", "crossfade") == "cut":
            txt = f"El siguiente visual entra de golpe cuando al track le queden {lead:g} s."
        else:
            txt = (f"El fundido empieza cuando al track le queden {lead:g} s: dura {d1:g} s hacia el visual de un track "
                   f"(termina a los {max(0.0, lead - d1):g} s del final) y {d2:g} s hacia un Random Visual (a los {max(0.0, lead - d2):g} s).")
            if max(d1, d2) > lead:
                txt += " ⚠ Un fundido dura más que la anticipación."
        self.lbl_timing.setText(keep_sentences(T(txt)))

    def _on_embed(self):
        self.cfg["embed_mpv"] = bool(self.cb_embed.currentData())
        save_config(self.cfg)
        self.statusBar().showMessage(T("Se aplica al reiniciar el programa."), 6000)

    def _on_hwdec(self):
        self._set("output", "hwdec", self.cb_hw.currentData())
        self.statusBar().showMessage(T("La decodificación se aplica al reiniciar el programa."), 6000)

    def toggle_test(self, on):
        """Patrón de prueba de mpv en la salida: si se ve, la ventana y mpv funcionan y el problema está en el video."""
        if on and self.engine:
            self.btn_test.setChecked(False)
            QMessageBox.information(self, T("Motor en marcha"), T("Detén el motor para probar la salida."))
            return
        if on and self.embed:
            self.out.show()
        self.player.test_pattern(on)
        self.btn_test.setText(T("Detener la prueba de salida" if on else "Probar salida (patrón de prueba)"))
        self._refresh_player_row()

    def _refresh_player_row(self):
        if not self.engine:                      # con el motor en marcha, el panel se actualiza con cada estado
            try:
                self._put_player(self.player.status())
                self._sync_alerts()
            except Exception:
                pass

    def _put_player(self, full):
        """Fila «Reproductor»: con el panel avanzado, todo el detalle; si no, solo el nombre del archivo que suena. En los dos
        casos un error se marca con ⚠ y su detalle va al registro."""
        clean, alert = dl.route("player", full)
        if self.cfg["dev"].get("advanced_player"):
            text = (clean or "—") + (" · " + self.selftest_note if self.selftest_note and self._gl_active() else "")
        else:
            if self.btn_test.isChecked():
                name = "Patrón de prueba"
            else:
                name = os.path.basename(str(getattr(self.player, "current", "") or "")) or "—"
            text = name + (" " + dl.ALERT if alert else "")
        self._set_row("player", text, "", alert)

    def _put(self, key, text, tip=""):
        """Texto de una fila del panel: los avisos «⚠ …» pasan al registro y aquí solo queda el símbolo ⚠."""
        clean, alert = dl.route(key, text)
        self._set_row(key, clean or "—", tip, alert)

    def _set_row(self, key, text, tip="", alert=""):
        """Texto de una fila del panel «Estado del Reproductor». El ⚠ aparece solo en la fila donde está el problema: lo pone
        esta fila (`alert`: su propio aviso) o cualquier aviso abierto del registro que se ubique en ella (`_apply_mark`)."""
        self._base[key] = (text, ("⚠ " + alert) if alert else tip)
        self._apply_mark(key)
        self._queue_rebalance()                                   # (un texto más corto o más largo cambia el alto que pide el panel)

    def _apply_mark(self, key):
        """Dibuja una fila: su texto base y, si hay un aviso abierto para ella, el ⚠ al final (y su detalle en el tooltip)."""
        lb = self.lbl.get(key)
        if lb is None:
            return
        mark = dl.devlog.row_mark(key)
        if key not in self._base:
            if not mark:
                return                                           # fila que escribe otro código y no tiene ningún aviso: no se toca
            self._base[key] = (str(lb.text()), "")
        text, tip = self._base[key]
        if mark and not text.endswith(dl.ALERT):
            text = text + " " + dl.ALERT
        show = TF if key == "track" else T                       # el título del track es un dato de Traktor: no se le cambia la mayúscula
        lb.setText(show(text))
        lb.setToolTip(T(("⚠ " + mark) if mark else tip))

    def _spin_s(self, value, key):
        sp = QDoubleSpinBox()
        sp.setRange(0.2, 15.0)
        sp.setSingleStep(0.5)
        sp.setDecimals(1)
        sp.setSuffix(" s")
        sp.setValue(value)
        sp.valueChanged.connect(lambda x: self._set("transition", key, x))
        return sp

    def _on_style(self):
        style = self.cb_style.currentData()
        self.cfg["transition"]["style"], self.cfg["transition"]["enabled"] = style, style != "cut"
        save_config(self.cfg)
        self._style_note()
        self._timing_note()

    def _style_note(self):
        chosen, using = self.cfg["transition"].get("style", "crossfade"), "crossfade" if self._gl_active() else "fade"
        txt = "El tipo de transición se aplica al reiniciar. "
        if chosen == "crossfade" and using != "crossfade":
            txt += self.startup_hint or "Fundido cruzado no disponible: se usa el fundido a negro."
        elif chosen == "crossfade":
            txt += "Fundido cruzado activo."                      # OpenGL, GPU y autoprueba: en la fila «Reproductor» del panel avanzado
        self.lbl_style.setText(keep_sentences(T(txt)))

    def _gl_active(self):
        return type(self.player).__name__ == "CrossfadePlayer"

    def _tab_capture(self):
        self.chk_win, box_win = wrapped_check("Leer solo la ventana de Traktor (ignora ventanas encima; no minimizada)")
        self.chk_win.setChecked(self.cfg["capture"]["mode"] == "window")
        self.chk_win.toggled.connect(self.on_capture_mode)
        self.cb_win = QComboBox()
        self.cb_win.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)     # los títulos largos no ensanchan el panel
        self.cb_win.setMinimumContentsLength(10)
        self.cb_win.activated.connect(self.on_window_chosen)
        btn_ref = QPushButton(T("Actualizar lista"))
        btn_ref.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn_ref.clicked.connect(self.refresh_windows)
        self.cb_backend = QComboBox()
        self.cb_backend.addItem(T("PrintWindow (por defecto): no interfiere con Discord/OBS · 5 fps"), "printwindow")
        self.cb_backend.addItem(T("Windows Graphics Capture (WGC): hasta 15 fps, puede chocar con la transmisión"), "wgc")
        self.cb_backend.setCurrentIndex(1 if self.cfg["capture"].get("backend") in ("wgc", "auto") else 0)
        self.cb_backend.activated.connect(lambda i: self._set("capture", "backend", self.cb_backend.itemData(i)))
        self.cb_layout = QComboBox()
        self.cb_layout.addItem(T("Automático: detecta los ROIs si faltan o cambia el tamaño de la ventana (p. ej. Traktor a pantalla completa)"), "auto")
        self.cb_layout.addItem(T("Manual: solo los ROIs que calibres tú"), "manual")
        self.cb_layout.setCurrentIndex(1 if self.cfg["capture"].get("layout", "auto") == "manual" else 0)
        self.cb_layout.activated.connect(lambda i: self._set("capture", "layout", self.cb_layout.itemData(i)))
        btn_detect = QPushButton(T("✨ Detectar layout ahora"))
        btn_detect.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn_detect.setToolTip(T("Con el motor en marcha, lee la ventana de Traktor y recalcula los ROIs (título, BPM, restante,\n"
                              "duración, tempo y vúmetros). Necesita un track cargado en algún deck."))
        btn_detect.clicked.connect(self.detect_layout_now)
        self.sp_fps = QDoubleSpinBox()
        self.sp_fps.setRange(2, 30)
        self.sp_fps.setDecimals(0)
        self.sp_fps.setSuffix(T(" fps"))
        self.sp_fps.setValue(self.cfg["capture"]["max_fps"])
        self.sp_fps.valueChanged.connect(lambda x: self._set("capture", "max_fps", x))
        txt = note("Menos fps = menos CPU. En automático los ROIs se recalculan solos al cambiar de tamaño (p. ej. pantalla "
                   "completa). Si el visor da tirones, baja la lectura a 2 fps.")
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(box_win)
        lay.addWidget(QLabel(T("Ventana de Traktor:")))
        lay.addWidget(self.cb_win)
        lay.addWidget(btn_ref)
        row = QFormLayout()
        row.setRowWrapPolicy(QFormLayout.WrapLongRows)
        row.addRow(T("Método de captura:"), self.cb_backend)
        row.addRow(T("Layout (ROIs):"), self.cb_layout)
        row.addRow(T("Lectura de la ventana:"), self.sp_fps)
        lay.addLayout(row)
        lay.addWidget(btn_detect)
        lay.addWidget(txt)
        tidy_fields(w, skip=(self.cb_win,))
        return w

    def detect_layout_now(self):
        if not self.engine:
            QMessageBox.information(self, T("Motor detenido"),
                                    T("Inicia el motor para detectar el layout de la ventana de Traktor, o abre «Calibrar ROIs» "
                                    "y pulsa «Detectar automáticamente»."))
            return
        self.engine.redetect_layout()
        self._layout_msg_until = time.monotonic() + 30.0
        self.statusBar().showMessage(T("Detectando el layout de Traktor (unos segundos)…"), 8000)

    def _tab_sync(self):
        s = self.cfg["sync"]
        self.sp_check = QDoubleSpinBox()
        self.sp_check.setRange(1, 120)
        self.sp_check.setSingleStep(1)
        self.sp_check.setSuffix(" s")
        self.sp_check.setValue(s["check_every"])
        self.sp_check.valueChanged.connect(lambda x: self._set("sync", "check_every", x))
        self.chk_auto, box_auto = wrapped_check("Corregir la deriva automáticamente")
        self.chk_auto.setChecked(s["auto_resync"])
        self.chk_auto.toggled.connect(lambda x: self._set("sync", "auto_resync", x))
        def spin(key, lo, hi, step, dec=0, suffix=" s"):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(dec)
            sp.setSuffix(T(suffix))
            sp.setValue(s[key])
            sp.valueChanged.connect(lambda x, k=key: self._set("sync", k, x))
            return sp

        self.sp_first = spin("initial_every", 1, 10, 0.5, 1)
        self.sp_first_win = spin("initial_window", 0, 60, 5)
        self.sp_tempo_win = spin("tempo_window", 0, 60, 5)
        self.sp_offset = spin("offset", -2.0, 2.0, 0.05, 2)
        a = self.cfg["autolink"]
        self.chk_link, box_link = wrapped_check("Autovincular: nombre, BPM y duración desde Traktor Pro 4")
        box_link.setToolTip(T("Si el título del track cargado en un deck se parece al nombre de un video sin vincular y la\n"
                            "duración del video coincide con la del track, crea solo el vínculo (editable en Configuración)."))
        self.chk_link.setChecked(a["enabled"])
        self.chk_link.toggled.connect(lambda x: self._set("autolink", "enabled", x))
        self.chk_fill, box_fill = wrapped_check("Rellenar también el BPM Base (si no, queda pendiente para escribirlo a mano)")
        box_fill.setToolTip(T("Con el fader de tempo movido o SYNC activo, el BPM que muestra Traktor no es el del track y la\n"
                                 "velocidad del visual saldría mal. Por eso, por defecto, el BPM Base queda pendiente (el visual va\n"
                                 "a ×1) hasta que lo escribas en Configuración. Si lo activas, se calcula BPM / (1 + tempo %)."))
        self.chk_fill.setChecked(a.get("fill_bpm", False))
        self.chk_fill.toggled.connect(lambda x: self._set("autolink", "fill_bpm", x))
        self.sp_link = QDoubleSpinBox()
        self.sp_link.setRange(50, 100)
        self.sp_link.setSingleStep(5)
        self.sp_link.setDecimals(0)
        self.sp_link.setSuffix(" %")
        self.sp_link.setValue(round(a["min_title"] * 100))
        self.sp_link.valueChanged.connect(lambda x: self._set("autolink", "min_title", x / 100.0))
        self.sp_ocr = QDoubleSpinBox()
        self.sp_ocr.setRange(0.5, 5)
        self.sp_ocr.setSingleStep(0.5)
        self.sp_ocr.setSuffix(" s")
        self.sp_ocr.setValue(self.cfg["ocr"]["interval"])
        self.sp_ocr.valueChanged.connect(lambda x: self._set("ocr", "interval", x))
        btn_now = QPushButton(T("Sincronizar ahora"))
        btn_now.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn_now.clicked.connect(lambda: self.engine and self.engine.sync_now())
        w = QWidget()
        f = QFormLayout(w)
        f.setRowWrapPolicy(QFormLayout.WrapLongRows)
        f.addRow(T("Revisar sincronización cada:"), self.sp_check)
        f.addRow(T("Al empezar el video, revisar cada:"), self.sp_first)
        f.addRow(T("… durante:"), self.sp_first_win)
        f.addRow(T("Tras un cambio de tempo, revisar durante:"), self.sp_tempo_win)
        f.addRow(T("Ajuste fino del video (+ adelanta):"), self.sp_offset)
        f.addRow(box_auto)
        f.addRow(T("Leer metadatos (OCR) cada:"), self.sp_ocr)
        f.addRow(box_link)
        f.addRow(T("Parecido mínimo título ↔ archivo:"), self.sp_link)
        f.addRow(box_fill)
        f.addRow(btn_now)
        tidy_fields(w)
        return w

    # ----------------------------------------------- pestaña «Desarrollador»
    def _tab_dev(self):
        d = self.cfg["dev"]
        self.chk_log, box_log = wrapped_check("Activar registro de errores (Log)")
        self.chk_log.setChecked(bool(d.get("log_enabled")))
        self.chk_log.toggled.connect(self.on_log_toggled)
        self.chk_sens, box_sens = wrapped_check('Activar «Sensores» en el Estado del Reproductor')
        box_sens.setToolTip(T("Muestra en el panel la fila «Sensores (debug)» (vúmetro y reloj de cada deck, en tiempo real).\n"
                            "Desactivada, la fila no aparece en el panel."))
        self.chk_sens.setChecked(bool(d.get("show_sensors")))
        self.chk_sens.toggled.connect(self.on_sensors_toggled)
        self.chk_adv, box_adv = wrapped_check("Activar Panel de Reproductor Avanzado")
        box_adv.setToolTip(T("La fila «Reproductor» del panel muestra todo el detalle (salida, archivo, resolución, posición,\n"
                           "decodificación, capas del fundido…). Desactivado, solo muestra el nombre del archivo que suena."))
        self.chk_adv.setChecked(bool(d.get("advanced_player")))
        self.chk_adv.toggled.connect(self.on_player_adv_toggled)
        self.chk_sb, box_sb = wrapped_check("Activar Barra de Estado")
        box_sb.setToolTip(T("Muestra siempre, al pie de la ventana, la barra de estado (una franja de borde a borde del color de la ventana,\n"
                          "con el estado del programa y los contadores de errores y alertas). Activada por defecto; desactivada, solo aparece mientras hay un mensaje y se oculta al caducar."))
        self.chk_sb.setChecked(bool(d.get("status_bar")))
        self.chk_sb.toggled.connect(self.on_status_bar_toggled)
        info = note("Solo registra errores (vacío = todo bien); el panel marca con ⚠ la fila afectada. Barra de estado: "
                    "verde = bien, amarilla = alerta, roja = error; los contadores muestran los avisos abiertos.")
        # visor (solo visible con el registro activado)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        try:
            self.log_view.setPlaceholderText(T("Sin errores: todo funciona."))
        except Exception:
            pass
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.log_view.setFixedHeight(230)
        self._style_log_view()
        self.log_view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btns = QHBoxLayout()
        for text, fn in (("Copiar", self.copy_log), ("Limpiar", self.clear_log), ("Abrir archivo", self.open_log_file)):
            b = QPushButton(T(text))
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.clicked.connect(fn)
            btns.addWidget(b)
        self.log_box = QWidget()
        lv = QVBoxLayout(self.log_box)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.log_view)
        lv.addLayout(btns)
        self.log_box.setVisible(bool(d.get("log_enabled")))
        self.sync_box = self._build_sync_box(bool(d.get("advanced_player")))
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(box_log)                                   # primera opción
        lay.addWidget(box_sens)
        lay.addWidget(box_adv)
        lay.addWidget(box_sb)
        lay.addWidget(info)
        lay.addWidget(self.log_box)
        lay.addWidget(self.sync_box)
        self._reload_log_view()
        self._reload_sync_view()
        return w

    def _build_sync_box(self, visible):
        """Registro de sincronía (core/synclog.py): una línea por cada revisión de la posición del video frente a la del track, con lo que
        se corrigió y cuánto tardó el salto. Solo se ve con el panel de reproductor avanzado activado."""
        title = QLabel(T("Registro de sincronía"))
        title.setToolTip(T("Una línea por cada revisión de la sincronía: posición del video y del track, diferencia, si se corrigió y cuánto "
                           "tardó el salto. «Copiar» lo entrega entero para mandarlo si el video se desvía."))
        self.sync_view = QPlainTextEdit()
        self.sync_view.setReadOnly(True)
        try:
            self.sync_view.setPlaceholderText(T("Aún no hay revisiones: aparecen cuando el motor sincroniza un video."))
        except Exception:
            pass
        self.sync_view.setMaximumBlockCount(2000)
        self.sync_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.sync_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.sync_view.setFixedHeight(230)
        self.sync_view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.sync_view.setStyleSheet(f"QPlainTextEdit {{ background-color: {LOG_BG}; color: {LOG_FG}; border: 1px solid {LOG_BORDER}; "
                                     f"selection-background-color: {LOG_SEL_BG}; selection-color: {LOG_SEL_FG}; }}")
        btns = QHBoxLayout()
        for text, fn in (("Copiar", self.copy_sync), ("Limpiar", self.clear_sync)):
            b = QPushButton(T(text))
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.clicked.connect(fn)
            btns.addWidget(b)
        box = QWidget()
        lv = QVBoxLayout(box)
        lv.setContentsMargins(0, 8, 0, 0)
        lv.addWidget(title)
        lv.addWidget(self.sync_view)
        lv.addLayout(btns)
        box.setVisible(bool(visible))
        return box

    def _reload_sync_view(self):
        self.sync_view.clear()
        self._sync_seq = synclog.last_seq
        lines = [fmt_line(e, T) for e in synclog.entries()]
        if lines:
            self.sync_view.setPlainText("\n".join(lines))
            self.sync_view.verticalScrollBar().setValue(self.sync_view.verticalScrollBar().maximum())

    def _pump_sync(self):
        """Añade al cuadro las líneas nuevas del registro de sincronía (solo si se ve)."""
        if not self.cfg["dev"].get("advanced_player") or synclog.last_seq <= self._sync_seq:
            return
        new = synclog.since(self._sync_seq)
        self._sync_seq = synclog.last_seq
        lines = [fmt_line(e, T) for e in new]
        if lines:
            bar = self.sync_view.verticalScrollBar()
            at_end = bar.value() >= bar.maximum() - 4
            self.sync_view.appendPlainText("\n".join(lines))
            if at_end:
                bar.setValue(bar.maximum())

    def copy_sync(self):
        QGuiApplication.clipboard().setText(synclog.text(tr=T))
        self.statusBar().showMessage(T("Registro de sincronía copiado al portapapeles."), 5000)

    def clear_sync(self):
        synclog.clear()
        self._reload_sync_view()

    def _style_log_view(self):
        """El registro se ve como una consola: fondo negro y letras verdes (parecido al registro de una actualización en OpenMediaVault)."""
        self.log_view.setStyleSheet(f"QPlainTextEdit {{ background-color: {LOG_BG}; color: {LOG_FG}; border: 1px solid {LOG_BORDER}; "
                                    f"selection-background-color: {LOG_SEL_BG}; selection-color: {LOG_SEL_FG}; }}")
        pal = self.log_view.palette()                            # el texto de relleno («Sin errores…») sale de la paleta, no del estilo
        pal.setColor(QPalette.PlaceholderText, QColor(LOG_DIM))
        self.log_view.setPalette(pal)

    def _reload_log_view(self):
        """Vuelve a llenar el visor con lo que hay registrado en memoria."""
        self.log_view.clear()
        dv = dl.devlog
        self._view_seq = dv.last_seq
        lines = [dl.fmt_entry(e, T) for e in dv.entries()]
        if lines:
            self.log_view.setPlainText("\n".join(lines))
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def on_log_toggled(self, on):
        self.cfg["dev"]["log_enabled"] = bool(on)
        save_config(self.cfg)
        if on:
            dl.devlog.configure(True)                             # abre el archivo y vuelca lo ya registrado
            self._reload_log_view()
        else:
            dl.devlog.configure(False)
        self.log_box.setVisible(bool(on))
        self._sync_alerts(force=True)                             # el aviso rojo cambia de texto según el registro esté activo o no

    def on_sensors_toggled(self, on):
        self._set("dev", "show_sensors", bool(on))
        self._apply_sensor_row()
        self._status_content_changed()

    def on_status_bar_toggled(self, on):
        self._set("dev", "status_bar", bool(on))
        self._apply_status_bar()

    def on_player_adv_toggled(self, on):
        self._set("dev", "advanced_player", bool(on))
        self.sync_box.setVisible(bool(on))
        if on:
            self._reload_sync_view()                              # (mientras estaba oculto no se fue llenando)
        try:
            self._put_player(self.player.status())               # se ve el cambio al instante (con o sin motor)
        except Exception:
            pass
        self._status_content_changed()                            # el panel cambia de alto: las pestañas se adaptan al espacio que queda

    def _apply_sensor_row(self):
        """La fila «Sensores (debug)» del panel solo se ve si se activó en «Desarrollador»."""
        on = bool(self.cfg["dev"].get("show_sensors"))
        lb = self.lbl["vu_dbg"]
        lab = self._form.labelForField(lb)
        lb.setVisible(on)
        if lab is not None:
            lab.setVisible(on)

    def copy_log(self):
        QGuiApplication.clipboard().setText(dl.devlog.text(context=True))   # errores + los últimos eventos (contexto)
        self.statusBar().showMessage(T("Registro copiado al portapapeles."), 5000)

    def clear_log(self):
        dl.devlog.clear()                                        # lo que sigue sin solucionarse se vuelve a anotar (y sigue en la barra)
        self._reload_log_view()
        self._sync_alerts()

    def open_log_file(self):
        path = os.path.abspath(dl.devlog.path)
        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            self.statusBar().showMessage(T("Aún no hay archivo de registro: se crea cuando ocurre un error con el registro activado."), 6000)

    def _on_tab(self, i):
        pass                                                     # (las pestañas no llevan ⚠: el ⚠ va solo en la fila afectada del panel)

    def _pump_log(self):
        """Cada 0,5 s: añade las líneas nuevas al visor y actualiza los avisos (⚠ de las filas y barra de estado)."""
        dv = dl.devlog
        self._sync_alerts()
        self._pump_sync()
        if self.cfg["dev"].get("log_enabled") and dv.last_seq > self._view_seq:
            new = dv.since(self._view_seq)
            self._view_seq = dv.last_seq
            lines = [dl.fmt_entry(e, T) for e in new]
            if lines:
                bar = self.log_view.verticalScrollBar()
                at_end = bar.value() >= bar.maximum() - 4
                self.log_view.appendPlainText("\n".join(lines))
                if at_end:                                       # si el usuario subió a leer, no se le arrastra al final
                    bar.setValue(bar.maximum())

    def _log_startup(self):
        """Datos del equipo y de la configuración para diagnosticar sin preguntar. NO son líneas del registro (que solo tiene
        errores): van en su cabecera, al copiarlo y al inicio de la sesión en el archivo cuando hay algún error."""
        cfg = self.cfg
        lines = ["Carpeta de trabajo: " + os.getcwd()]
        try:
            m = getattr(self.player, "m", None) or self.player.layers[0].m
            lines.append("libmpv: " + str(getattr(m, "mpv_version", "?")))
        except Exception:
            pass
        path, origin = comp.find_libmpv()
        lines.append(f"libmpv en uso: {path or '?'} ({comp.ORIGINS.get(origin, origin)})")
        lines.append("Legacy Mode: " + ("ACTIVO" if comp.legacy_active() else "no"))
        lines.append(("OCR: " + self.ocr_msg) if self.ocr_ok else "OCR NO disponible")
        o, t, c = cfg["output"], cfg["transition"], cfg["capture"]
        lines.append(f"Salida: {o.get('resolution')} · {'incrustada' if self.embed else 'ventana propia de mpv'} · "
                     f"hwdec {o.get('hwdec')} · transición {t.get('style')} ({t.get('duration')} s / {t.get('random_duration')} s)")
        backend = {"printwindow": "PrintWindow", "wgc": "WGC"}.get(c.get("backend"), c.get("backend"))      # (nombre propio: no es una palabra suelta)
        lines.append(f"Captura: {c.get('mode')} · {backend} · layout {c.get('layout')} · {c.get('max_fps')} fps · "
                     f"ventana «{c.get('window_title')}»")
        try:
            lines.append("Pantallas: " + " · ".join(
                f"{s.name()} {s.size().width()}×{s.size().height()} @{s.refreshRate():.0f} Hz (x{s.devicePixelRatio():g})"
                for s in QGuiApplication.screens()))
        except Exception:
            pass
        lines.append(f"Reproductor: {type(self.player).__name__}" + (f" · {self.gl_note}" if self.gl_note else ""))
        dl.devlog.set_header(lines)
        sy = cfg["sync"]
        synclog.set_header(list(lines) + [
            f"Sincronía: revisión cada {sy['check_every']:g} s (al inicio cada {sy['initial_every']:g} s durante {sy['initial_window']:g} s) · "
            f"umbral {sy['resync_drift']:g} s / preciso {sy['fine_drift']:g} s · adelanto del salto {sy['seek_lead']:g} s "
            f"({'aprendido por video' if sy.get('adaptive_lead', True) else 'fijo'}) · "
            f"compensación de arranque: {'aprendida por video' if sy.get('adaptive_load', True) else 'no'} · "
            f"ajuste fino de velocidad {'sí' if sy.get('trim', True) else 'no'} · corrección automática {'sí' if sy['auto_resync'] else 'no'}"])

    # ---------------------------------------------------- pestaña «Ajustes»
    def _tab_settings(self):
        info = note("Restablecer o cargar cambia los ajustes de las pestañas, pero no toca tus videos, tracks_db.json, "
                    "los ROIs, la ruta de Tesseract ni el idioma.")
        btn_reset = QPushButton(T("Restablecer por defecto"))
        btn_reset.setToolTip(T("Deja todos los ajustes de las pestañas en sus valores por defecto (los de esta versión)."))
        btn_reset.clicked.connect(self.reset_settings)
        btn_load = QPushButton(T("Cargar ajustes desde archivo"))
        btn_load.setToolTip(T("Carga los ajustes de un config.json externo compatible con ViDeck Pulse."))
        btn_load.clicked.connect(self.load_settings)
        for b in (btn_reset, btn_load):
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setMinimumHeight(28)
        self.cb_lang = QComboBox()                               # idioma de la interfaz (se aplica al reiniciar el programa)
        for code, name in i18n.LANGS.items():
            self.cb_lang.addItem(name, code)                     # los nombres van en su propio idioma: no se traducen
        self.cb_lang.setCurrentIndex(max(0, self.cb_lang.findData(self.cfg.get("language", "es"))))
        self.cb_lang.currentIndexChanged.connect(self._on_language)
        lang = QWidget()
        lf = QFormLayout(lang)
        lf.setContentsMargins(0, 0, 0, 0)
        lf.addRow(T("Idioma:"), self.cb_lang)
        tidy_fields(lang)
        lang_note = note("El idioma se aplica al reiniciar el programa.")
        lm_title = centered_note("Modo de Ventana:")                  # «Modo de Ventana»: tres botones chicos, centrados (Automático · Completo · Compacto)
        self.btn_lm_auto = QPushButton(T("Automático"))
        self.btn_lm_full = QPushButton(T("Completo"))
        self.btn_lm_compact = QPushButton(T("Compacto"))
        self.btn_lm_auto.setToolTip(T("La ventana elige sola la disposición según su alto (y, por eso, según la resolución de tu pantalla): "
                                      "completa si hay sitio y compacta si no."))
        self.btn_lm_full.setToolTip(T("Disposición completa: la lista de videos a la izquierda y, a la derecha, el estado del Reproductor sobre las pestañas."))
        self.btn_lm_compact.setToolTip(T("Disposición compacta, para pantallas bajas: videos y pestañas a la izquierda y el estado del Reproductor a la derecha."))
        for b, kind in ((self.btn_lm_auto, "auto"), (self.btn_lm_full, "full"), (self.btn_lm_compact, "compact")):
            b.setCheckable(True)
            b.setMinimumHeight(24)
            b.setMinimumWidth(92)
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            b.clicked.connect(lambda _checked=False, k=kind: self._on_layout_button(k))
        lm_row = QWidget()
        lmh = QHBoxLayout(lm_row)
        lmh.setContentsMargins(0, 0, 0, 0)
        lmh.setSpacing(8)
        lmh.addStretch(1)
        lmh.addWidget(self.btn_lm_auto)
        lmh.addWidget(self.btn_lm_full)
        lmh.addWidget(self.btn_lm_compact)
        lmh.addStretch(1)
        self.lbl_lm_note = centered_note("")
        self.chk_autoscan, box_autoscan = wrapped_check("Buscar videos nuevos solo (al iniciar, al detener el motor y al volver a la ventana)")
        box_autoscan.setToolTip(T("Con el motor detenido, la lista de videos se actualiza sola cuando agregas, quitas o renombras archivos de la "
                                  "carpeta de videos. Con el motor en marcha la lista no cambia nunca. «Recargar» busca a mano."))
        self.chk_autoscan.setChecked(bool(self.cfg.get("ui", {}).get("autoscan", True)))
        self.chk_autoscan.toggled.connect(self._on_autoscan_toggled)
        btn_prep = QPushButton(T("Preparar videos…"))
        btn_prep.setToolTip(T("Muestra cada video de tu carpeta como Compatible, Mejorable, No compatible o No se puede convertir, y deja "
                              "listos los que lo necesiten (fps constantes, un keyframe por segundo) con el mismo bitrate de video. "
                              "Los originales no se tocan: salen a otra carpeta."))
        btn_prep.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn_prep.setMinimumHeight(28)
        btn_prep.clicked.connect(self.open_prepare)
        prep_note = note("Te dice qué videos funcionan bien tal cual y deja listos los demás para la sincronía, sin bajar su calidad. "
                         "Usa ffmpeg: se instala solo la primera vez (se actualiza desde «Componentes»).")
        btn_comp = QPushButton(T("Componentes (ffmpeg y libmpv)…"))
        btn_comp.setToolTip(T("Instala y actualiza ffmpeg y libmpv (el reproductor), descargados de repositorios conocidos y verificados con su "
                              "suma SHA-256."))
        btn_comp.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn_comp.setMinimumHeight(28)
        btn_comp.clicked.connect(lambda _checked=False: self.open_components())
        self.btn_legacy = QPushButton("")
        self.btn_legacy.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.btn_legacy.setMinimumHeight(28)
        self.btn_legacy.clicked.connect(self._on_legacy)
        comp_note = note("Si una versión nueva de ffmpeg o libmpv falla, «Legacy Mode» vuelve a los últimos que funcionaban con esta versión "
                         "del programa. Se aplica al reiniciar.")
        self.chk_upd = QCheckBox(T("Verificar actualizaciones al iniciar"))
        self.chk_upd.setChecked(bool(updatecheck.section(self.cfg)["check_on_start"]))
        self.chk_upd.toggled.connect(self._on_update_pref)
        self.upd_note = note("Mira una sola vez, al abrir el programa, si hay una versión nueva de ViDeck Pulse y solo avisa: no descarga ni instala "
                             "nada. Para buscar a mano usa «Componentes».")
        for _w in (self.chk_upd, self.upd_note):
            _w.setVisible(bool(version.STABLE))                  # las versiones Beta no buscan actualizaciones
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(lang)
        lay.addWidget(lang_note)
        lay.addSpacing(6)
        lay.addWidget(lm_title)
        lay.addWidget(lm_row)
        lay.addWidget(self.lbl_lm_note)
        lay.addSpacing(6)
        lay.addWidget(box_autoscan)
        lay.addSpacing(6)
        lay.addWidget(btn_prep)
        lay.addWidget(prep_note)
        lay.addSpacing(6)
        lay.addWidget(btn_comp)
        lay.addWidget(self.btn_legacy)
        lay.addWidget(comp_note)
        lay.addSpacing(6)
        lay.addWidget(self.chk_upd)
        lay.addWidget(self.upd_note)
        self._refresh_legacy_button()
        self._refresh_layout_buttons()
        lay.addSpacing(6)
        lay.addWidget(btn_reset)
        lay.addWidget(btn_load)
        lay.addWidget(info)
        lay.addSpacing(10)                                       # abajo de las opciones, centrado: la versión y, debajo, el mensaje
        self.lbl_version = centered_note(version.label(), bold=True)
        lay.addWidget(self.lbl_version)
        lay.addWidget(centered_note(version.CREDITS))
        return w

    def _on_language(self):
        """Idioma elegido: se guarda ya y un aviso pregunta si se reinicia ahora («Sí») o después («No, después»); el cambio se aplica
        al reiniciar. Si se pulsa «No, después», el mensaje de la barra de estado sale en el idioma elegido."""
        code = self.cb_lang.currentData()
        if code not in i18n.LANGS or code == self.cfg.get("language", "es"):
            return
        self.cfg["language"] = code
        save_config(self.cfg)
        if code == i18n.language():                              # volvió al idioma con el que está corriendo: no hay nada que reiniciar
            self.statusBar().showMessage(T("Idioma sin cambios."), 6000)
            return
        if ask_restart(self, i18n.LANGS[code], bool(self.engine)):     # «Sí»: se reinicia ya (se cierra todo como siempre y main.py abre otra copia)
            self.restart_requested = True
            QTimer.singleShot(0, self.close)
            return
        self.statusBar().showMessage("Idioma: Español. Se aplica al reiniciar el programa." if code == "es"
                                     else "Language: English. Applied when the program restarts.", 8000)

    def reset_settings(self):
        if not ask_yes_no(self, "Restablecer ajustes", "¿Restablecer todos los ajustes a sus valores por defecto?",
                          "No se tocan la carpeta de videos, tracks_db.json, la calibración de los ROIs, la ruta de Tesseract ni el idioma."):
            return
        self._apply_new_settings(build_settings(self.cfg), "Ajustes restablecidos a los valores por defecto.")

    def load_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, T("Cargar ajustes desde archivo"), os.getcwd(),
                                              T("Ajustes de ViDeck Pulse (*.json);;Todos los archivos (*)"))
        if not path:
            return
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except Exception as e:
            dl.error("ajustes", f"No se pudo leer «{name}»: {e}")
            QMessageBox.warning(self, T("No se pudo leer el archivo"), T(f"{name}\n\n{e}"))
            return
        if isinstance(data, dict):
            data = migrate(data)                                 # un config.json de una versión anterior se adapta primero
        res = inspect_settings(data)
        if not res["recognized"]:
            dl.info("ajustes", f"«{name}» no es un config.json de ViDeck Pulse: no se cambió nada")
            QMessageBox.warning(self, T("Archivo no válido"),
                                T("Este archivo no parece un config.json de ViDeck Pulse. No se cambió nada."))
            return
        if not res["compatible"]:
            bad = res["missing"] + res["wrong"]
            detail = ("Faltan en el archivo o no son válidos: " + ", ".join(bad[:15]) + (" …" if len(bad) > 15 else "")
                      + ".\n\nSi aceptas, se usan los ajustes compatibles y los demás quedan en su valor inicial.")
            if not ask_yes_no(self, "Archivo no compatible",
                              "Este archivo de configuraciones no es compatible con la versión actual de ViDeck Pulse, "
                              "¿actualizar ajustes de todos modos?", detail):
                dl.info("ajustes", f"Archivo descartado: «{name}» (no compatible); los ajustes siguen como estaban")
                self.statusBar().showMessage(T("Archivo descartado: los ajustes siguen como estaban."), 8000)
                return
            dl.info("ajustes", f"«{name}» no es del todo compatible; sin valor o inválidos: {', '.join(bad)}")
        self._apply_new_settings(build_settings(self.cfg, data), f"Ajustes cargados desde «{name}».")

    def _apply_new_settings(self, new, msg):
        """Aplica unos ajustes nuevos en el config EN SITIO (el motor y el reproductor comparten sus secciones) y refresca."""
        if self.btn_test.isChecked():
            self.btn_test.setChecked(False)
        apply_settings(self.cfg, new)
        tr = self.cfg["transition"]
        tr["enabled"] = tr.get("style", "crossfade") != "cut"
        self.player.black_on_pause = self.cfg["output"]["black_on_pause"]
        dl.devlog.configure(bool(self.cfg["dev"].get("log_enabled")))
        save_config(self.cfg)
        self._rebuild_tabs()
        self.refresh_windows()
        self.apply_output()
        self._apply_sensor_row()
        self._apply_status_bar()
        self._apply_layout_pref()                                 # el «Modo de Ventana» del config nuevo (restablecer lo deja en automático)
        self._apply_window_mode()                                 # el modo del Reproductor se aplica al momento
        self._apply_hotkey()                                      # y su atajo de teclado
        self._style_note()
        dl.info("ajustes", msg)
        dl.devlog.resolve("ajustes")                              # unos ajustes válidos: el archivo que no se pudo leer ya no importa
        self._sync_alerts()
        self.statusBar().showMessage(T(msg + " La salida de video y la decodificación se aplican al reiniciar el programa."), 12000)

    # --------------------------------------------------------------- videos
    AUTO_SCANS = ("foco", "motor", "preparar", "reintento")        # búsquedas que se lanzan solas (la casilla de «Ajustes» las apaga)
    SCAN_GAP = 5.0                                                 # s mínimos entre dos búsquedas al volver a la ventana
    SCAN_RETRIES = 8                                               # veces que se vuelve a mirar un video nuevo que aún se copia

    def scan(self, _checked=False, reason="manual"):
        """Busca los videos de la carpeta (update 13): en un hilo aparte y por lotes, así que la ventana no se cuelga aunque la carpeta esté en
        una unidad lenta. Se hace al iniciar y con el motor DETENIDO (al detenerlo, al volver a la ventana, al cerrar «Preparar videos»);
        con el motor en marcha la lista no cambia. `reason`: inicio · carpeta (otra carpeta) · manual («Recargar») · foco · motor · preparar."""
        auto = reason in self.AUTO_SCANS
        if auto and (self.engine or self._prepare_open or not self.cfg.get("ui", {}).get("autoscan", True)):
            return
        if reason == "manual" and self.engine:
            self.statusBar().showMessage(T("Detén el motor para recargar la lista de videos."), 6000)
            return
        if reason == "foco" and time.monotonic() - self._scan_t < self.SCAN_GAP:
            return
        job = self._scan_job
        if job is not None and not job.done:
            if auto or reason == "manual":
                return                                                # ya hay una en marcha: esa trae lo mismo
            self._cancel_scan()                                       # otra carpeta o el inicio: manda la nueva
        folder = self.cfg["videos_dir"]
        replace = reason in ("inicio", "carpeta") or not self._scanned
        try:
            os.makedirs(folder, exist_ok=True)                        # (como siempre: la carpeta se crea si falta)
        except OSError:
            pass
        if reason == "carpeta":
            self._sig, self._scanned, self._scan_tries = {}, False, 0
        self._partial = {}
        self._scan_reason, self._scan_t = reason, time.monotonic()
        self._scan_job = videoscan.ScanJob(folder, known=None if replace else set(self._sig)).start()
        if self._scan_job.wait_done(0.3):                             # carpeta chica: la lista queda lista al instante
            self._pump_scan()
        else:
            self._scan_timer.start(150)
            self.statusBar().showMessage(T("Buscando videos…"), 4000)

    def _cancel_scan(self):
        job, self._scan_job = self._scan_job, None
        self._scan_timer.stop()
        if job is not None:
            job.cancel()

    def _pump_scan(self):
        """Recoge lo que dejó la búsqueda (hilo de la interfaz). En la primera búsqueda la lista se va llenando; en las demás se cambia al terminar."""
        job = self._scan_job
        if job is None:
            self._scan_timer.stop()
            return
        progressive = self._scan_reason in ("inicio", "carpeta") or not self._scanned
        for ev in job.drain():
            if ev[0] == "batch":
                self._partial.update(ev[1])
                if progressive:
                    self.videos = self._visible(sorted(self._partial))
                    if self.engine:
                        self.engine.videos = list(self.videos)
                    self.refresh_list()
                    self.statusBar().showMessage(T(f"Buscando videos… {len(self._partial)}"), 4000)
            elif ev[0] == "done":
                self._scan_done(ev[1], ev[2])
            elif ev[0] == "error":
                dl.error("videos", f"no se pudo buscar videos en la carpeta ({ev[1]})")
                self.statusBar().showMessage(T(f"No se pudo buscar videos ({ev[1]})."), 8000)
        if job.done:
            self._scan_timer.stop()
            self._scan_job = None

    @staticmethod
    def _visible(videos):
        """La lista de videos que se usa: sin el original de un «[Converted]» (el convertido ya ocupa su lugar en la lista y en los vínculos)."""
        hidden = transfer.hidden_originals(videos)
        return [v for v in videos if v not in hidden] if hidden else videos

    def _scan_names(self, paths):
        base = [os.path.basename(p) for p in paths]
        return ", ".join(base[:3]) + (f" (+{len(base) - 3})" if len(base) > 3 else "")

    def _scan_done(self, sig, unstable):
        """Terminó una búsqueda completa: actualiza la lista, repara los vínculos de lo renombrado y avisa de lo nuevo y de lo que ya no está."""
        first = self._scan_reason in ("inicio", "carpeta") or not self._scanned
        added, removed, renamed = ([], [], []) if first else videoscan.diff(self._sig, sig)
        if renamed and not self.engine:
            renamed = self._relink_renamed(renamed)
        videos = self._visible(sorted(sig))
        changed = videos != self.videos
        self._sig, self.videos, self._scanned = dict(sig), videos, True
        if self.engine:
            self.engine.videos = list(videos)
        if changed:
            self.refresh_list()
        msgs, shown, kept_at = [], [], -1
        if added:
            msgs.append(f"Videos nuevos ({len(added)}): {self._scan_names(added)}")
        if removed:
            msgs.append(f"Videos que ya no están ({len(removed)}): {self._scan_names(removed)}")
            kept_at = len(msgs)                                            # (detrás de este aviso va «sus vínculos se conservan»)
        msgs += [f"Vínculo actualizado por cambio de nombre: {os.path.basename(o)} → {os.path.basename(n)}" for o, n in renamed]
        for m in msgs:
            dl.info("videos", m)
        if msgs:
            shown = list(msgs)
            if kept_at >= 0:                                               # (cada trozo entre « · » se traduce por separado)
                shown.insert(kept_at, "sus vínculos se conservan")
            self.statusBar().showMessage(T(" · ".join(shown)), 12000)
        elif not first and self._scan_reason == "manual":
            self.statusBar().showMessage(T(f"Lista de videos al día ({len(videos)})."), 5000)
        elif first:
            self.statusBar().clearMessage()
        if unstable and self._scan_tries < self.SCAN_RETRIES:         # un video nuevo que todavía se copia: se vuelve a mirar en unos segundos
            self._scan_tries += 1
            QTimer.singleShot(3000, lambda: self.scan(reason="reintento"))
        elif not unstable:
            self._scan_tries = 0

    def _relink_renamed(self, renamed):
        """Un video renombrado o movido (mismo tamaño y fecha): sus vínculos de tracks_db.json lo siguen. Devuelve los que de verdad se cambiaron."""
        from core.db import norm_path
        done = []
        for old, new in renamed:
            hit = [e for e in self.db.tracks if e.get("video_path") and norm_path(e["video_path"]) == norm_path(old)]
            for e in hit:
                e["video_path"] = new
            if hit:
                done.append((old, new))
        if done:
            try:
                self.db.save()
            except OSError as e:
                dl.error("videos", f"no se pudo guardar tracks_db.json tras el cambio de nombre ({e})")
        return done

    def _on_autoscan_toggled(self, on):
        self._set("ui", "autoscan", bool(on))
        if on:
            self.scan(reason="foco")

    def changeEvent(self, e):
        parent = getattr(super(), "changeEvent", None)
        if parent is not None:
            parent(e)
        try:
            if e.type() == getattr(QEvent, "ActivationChange", None) and self.isActiveWindow():
                self.scan(reason="foco")                              # volviste a la ventana: ¿hay videos nuevos? (solo con el motor detenido)
        except Exception:                                             # noqa: BLE001: solo comodidad
            dl.exception("videos", "no se pudo revisar la carpeta de videos al volver a la ventana", level=dl.WARN)

    def _view_state(self):
        """Lo que se conserva al rehacer la lista: el video seleccionado y cuánto se había bajado."""
        try:
            it = self.listw.currentItem()
            return (it.data(Qt.UserRole) if it is not None else None, int(self.listw.verticalScrollBar().value()))
        except Exception:                                             # noqa: BLE001
            return None, 0

    def _restore_view(self, sel, pos):
        try:
            if sel is not None:
                for i in range(self.listw.count()):
                    if self.listw.item(i).data(Qt.UserRole) == sel:
                        self.listw.setCurrentRow(i)
                        break
            self.listw.verticalScrollBar().setValue(pos)
        except Exception:                                             # noqa: BLE001
            pass

    def refresh_list(self):
        sel, pos = self._view_state()
        self.listw.clear()
        for v in self.videos:
            e = self.db.entry_for_video(v)
            if e:
                bpm = fmt_bpm(e.get("base_bpm"))
                text = (f"🎵  {os.path.basename(v)}   →   {e['traktor_track_name']}  "
                        + (f"({bpm} BPM)" if bpm else "(⏳ BPM Base Pendiente)"))
                color = row_color(e["traktor_track_name"], bpm)
            else:
                text, color = f"🎲  {os.path.basename(v)}   [Random Visuals]", RANDOM_COLOR
            it = QListWidgetItem(T(text))
            it.setData(Qt.UserRole, v)
            it.setForeground(QColor(color))
            self.listw.addItem(it)
        self._restore_view(sel, pos)
        self.mark_active()

    def mark_active(self):
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            f = it.font()
            f.setBold(os.path.basename(it.data(Qt.UserRole)) == self._active)
            it.setFont(f)

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, T("Carpeta de videos"), self.cfg["videos_dir"])
        if d:
            self.cfg["videos_dir"] = d
            save_config(self.cfg)
            self.scan(reason="carpeta")

    # ----------------------------------------------------- captura de ventana
    def refresh_windows(self):
        self.cb_win.clear()
        try:
            wins = list_windows()
        except Exception as e:
            dl.exception("ventanas", "no se pudo listar las ventanas")
            self.cb_win.addItem(T(f"(no se pudo listar las ventanas: {e})"))
            return
        dl.devlog.resolve("ventanas")                            # la lista se pudo leer: el error anterior quedó solucionado
        cur, pick = self.cfg["capture"]["window_title"], -1
        for i, w in enumerate(wins):
            label = f"{w['owner'] or '?'} — {w['title'] or '(sin título)'}  [{w['w']}×{w['h']}]"
            self.cb_win.addItem(T(label + ("  (minimizada)" if w["minimized"] else "")),
                                f"{w['owner']}::{w['title']}" if w["owner"] else w["title"])
            if pick < 0 and window_matches(cur, w):
                pick = i
        if pick >= 0:
            self.cb_win.setCurrentIndex(pick)

    def on_window_chosen(self, i):
        data = self.cb_win.itemData(i)
        if data:
            self._set("capture", "window_title", data)
            self.statusBar().showMessage(T("Ventana elegida; si cambia el tamaño, recalibra los ROIs."), 6000)

    def on_capture_mode(self, on):
        self._set("capture", "mode", "window" if on else "screen")
        self.statusBar().showMessage(T("Modo de captura cambiado: reinicia el motor y recalibra los ROIs."), 8000)

    # --------------------------------------------------------------- diálogos
    def open_config(self):
        if self.engine:
            self.engine.autolink_hold = True                 # el diálogo reescribe tracks_db.json al guardar: sin autovínculos a mitad
        try:
            if ConfigDialog(self.videos, self.db, self.engine, self).exec():
                self.refresh_list()
        finally:
            if self.engine:
                self.engine.autolink_hold = False

    def open_prepare(self):
        """«Preparar videos»: recodifica los videos que lo necesiten, en la carpeta de videos y con el mismo bitrate (core/prepare.py)."""
        if self.engine:                                      # el reproductor tiene abiertos los archivos: con el motor iniciado no se puede preparar nada
            QMessageBox.information(self, T("Motor en marcha"),
                                    T("No se pueden preparar videos con el motor iniciado: el reproductor tiene abiertos los archivos. "
                                      "Detén el motor y vuelve a abrir «Preparar videos»."))
            return
        if self.engine:
            self.engine.autolink_hold = True                 # si se usan los preparados, se reescribe tracks_db.json: sin autovínculos a mitad
        self._prepare_open = True
        try:
            dlg = PrepareDialog(self.cfg, self.db, bool(self.engine), self, release=self._release_videos)
            dlg.exec()
            self._prepare_open = False
            if dlg.applied:
                self.scan(reason="carpeta")
                self.statusBar().showMessage(T(f"Videos preparados en uso: {dlg.moved} vínculos actualizados."), 10000)
            elif getattr(dlg, "transferred", 0):                       # traspaso directo: la carpeta cambió; se lee de cero (sin avisos de «nuevos/quitados»)
                self.scan(reason="carpeta")
                self.refresh_list()
                self.statusBar().showMessage(T(f"Videos convertidos en la carpeta de videos: {dlg.transferred} (vínculos actualizados)."), 10000)
            else:
                self.scan(reason="preparar")                          # quizá se agregaron videos a la carpeta (solo con el motor detenido)
            if getattr(dlg, "restart_requested", False) is True:       # Legacy Mode / libmpv nueva elegidos desde la ventana de Componentes
                self.restart_requested = True
                QTimer.singleShot(0, self.close)
        finally:
            self._prepare_open = False
            if self.engine:
                self.engine.autolink_hold = False

    def _release_videos(self):
        """El reproductor suelta los videos que tiene abiertos (Windows no deja reemplazar ni eliminar un archivo abierto)."""
        fn = getattr(self.player, "release_files", None)
        if fn is not None:
            fn()

    # ---------------------------------------------------- componentes (ffmpeg, libmpv) y Legacy Mode
    def _mpv_version_text(self):
        try:
            m = getattr(self.player, "m", None) or self.player.layers[0].m
            return str(getattr(m, "mpv_version", "") or "")
        except Exception:
            return ""

    def open_components(self, auto=()):
        if not isinstance(auto, (list, tuple, set, frozenset)):      # (una señal `clicked` entrega un bool)
            auto = ()
        dlg = ComponentsDialog(self.cfg, lambda: save_config(self.cfg), bool(self.engine), self._mpv_version_text(),
                               lambda t: self.statusBar().showMessage(t, 8000), self, auto=auto)
        dlg.exec()
        save_config(self.cfg)
        self._refresh_legacy_button()
        if dlg.restart_requested:                                # «Sí» en el aviso de reinicio (libmpv nueva / Legacy Mode)
            self.restart_requested = True
            QTimer.singleShot(0, self.close)

    def _refresh_legacy_button(self):
        """El botón de Ajustes: «Activar Legacy Mode» / «Desactivar Legacy Mode» (si no hay paquete Legacy guardado, no se puede activar)."""
        on = bool(comp.section(self.cfg)["legacy"])
        info = comp.describe_legacy()
        self.btn_legacy.setText(T("Desactivar Legacy Mode" if on else "Activar Legacy Mode"))
        self.btn_legacy.setEnabled(info["available"] or on)
        self.btn_legacy.setToolTip(T(info["text"]))

    def _on_legacy(self):
        """Legacy Mode: se guarda ya y un aviso pregunta si se reinicia ahora («Sí») o después; el cambio se aplica al reiniciar."""
        sec = comp.section(self.cfg)
        want = not sec["legacy"]
        if want and not comp.legacy.available():
            return
        sec["legacy"] = want
        save_config(self.cfg)
        self._refresh_legacy_button()
        if want == comp.legacy_active():                         # volvió a lo que ya está en marcha: no hay nada que reiniciar
            self.statusBar().showMessage(T("Legacy Mode sin cambios."), 6000)
            return
        text = ("Legacy Mode elegido: se usarán los ffmpeg y libmpv guardados." if want
                else "Legacy Mode desactivado: se usarán los ffmpeg y libmpv actualizados.")
        if ask_restart_text(self, "Activar Legacy Mode" if want else "Desactivar Legacy Mode",
                            text + " Para aplicarlo hay que reiniciar ViDeck Pulse.", bool(self.engine)):
            self.restart_requested = True
            QTimer.singleShot(0, self.close)
            return
        self.statusBar().showMessage("Legacy Mode: se aplica al reiniciar el programa." if self.cfg.get("language", "es") == "es"
                                     else "Legacy Mode: applied when the program restarts.", 8000)

    def _components_startup(self):
        """Unos segundos después de abrir: instala el ffmpeg base si falta, guarda el paquete Legacy de esta versión (si hace falta) y consulta si
        hay ffmpeg/libmpv nuevos. Todo en hilos de fondo; con el motor en marcha el paquete Legacy espera a que se detenga (comprimir usa CPU)."""
        if self._comp_job is not None:
            return
        comp.ensure_base(self.cfg, say=lambda t: self.statusBar().showMessage(T(t), 10000))      # sin ffmpeg: se instala solo, verificado
        self._base_timer = QTimer(self)
        self._base_timer.timeout.connect(self._base_poll)
        self._base_timer.start(700)
        cfg_ff = self.cfg.get("prepare", {}).get("ffmpeg_cmd", "")
        self._comp_job = ft.Job(self._components_work, cfg_ff)
        self._comp_job.start()
        self._comp_timer = QTimer(self)
        self._comp_timer.timeout.connect(self._components_poll)
        self._comp_timer.start(1000)
        self._update_check_start()                          # ¿hay una versión nueva de ViDeck Pulse en GitHub? (solo la estable)

    # ------------------------------------------------------------------ versión nueva de ViDeck Pulse
    def _on_update_pref(self, on):
        updatecheck.section(self.cfg)["check_on_start"] = bool(on)
        save_config(self.cfg)

    def _update_check_start(self):
        """Mira en segundo plano si GitHub tiene una versión estable más nueva. Se llama UNA vez al iniciar el programa (la búsqueda a mano
        está en «Componentes»: no corre sola con el programa abierto)."""
        if self._upd_job is not None or not version.STABLE:
            return
        self._upd_shim = {"updates": dict(updatecheck.section(self.cfg))}      # (el hilo trabaja sobre una copia; el resultado se aplica aquí)
        self._upd_job = ft.Job(updatecheck.check, self._upd_shim, None, None, False)
        self._upd_job.start()
        self._upd_timer = QTimer(self)
        self._upd_timer.timeout.connect(self._update_poll)
        self._upd_timer.start(800)

    def _update_poll(self):
        job = self._upd_job
        if job is None or job.is_alive():
            return
        self._upd_timer.stop()
        self._upd_job = None
        if job.error is not None:
            dl.error("actualizaciones", f"no se pudo buscar una versión nueva ({type(job.error).__name__}: {job.error})")
            return
        res = job.result or {}
        self.cfg["updates"].update(self._upd_shim["updates"])
        save_config(self.cfg)
        st = res.get("state")
        if st == "error":
            dl.info("actualizaciones", "no se pudo buscar una versión nueva: " + str(res.get("error", "")))
        elif st == "newer":
            dl.info("actualizaciones", f"hay una versión nueva: {res['text']} (corre {res['running']})")
            if self.engine:                                   # en pleno set no se interrumpe con una ventana: se avisa al próximo inicio
                self.statusBar().showMessage(T("Hay una versión nueva de ViDeck Pulse: se avisará al abrir el programa la próxima vez."), 15000)
                return
            choice = ask_new_version(self, res)
            if choice == "open":
                QDesktopServices.openUrl(QUrl(res["url"]))
            updatecheck.answer(self.cfg, res, choice)
            save_config(self.cfg)

    def _base_poll(self):
        """Cierra la instalación del ffmpeg base cuando termina (error o éxito, en el registro y la barra de estado)."""
        comp.base_poll(lambda t: self.statusBar().showMessage(T(t), 8000))
        if not ft.BASE.running:
            self._base_timer.stop()

    def _components_work(self, cfg_ff):
        out = {"snap": "", "snap_error": "", "updates": []}
        try:
            while ft.BASE.running and not getattr(self, "_closing", False):      # el ffmpeg base primero: así el paquete Legacy y las
                time.sleep(0.5)                                                     # búsquedas de actualización ya lo ven instalado
            sources = comp.snapshot_sources(cfg_ff)
            reason = comp.snapshot_reason(sources)
            if reason:
                while self.engine and not getattr(self, "_closing", False):
                    time.sleep(10)
                try:
                    comp.build_snapshot(cfg_ff, reason=reason)
                    out["snap"] = reason
                except Exception as e:                       # noqa: BLE001: se informa en el registro
                    out["snap_error"] = str(e)
            if not comp.legacy_active():                     # con Legacy Mode activo no se busca ni se avisa de actualizaciones
                out["updates"] = comp.startup_updates(self.cfg)
        except Exception as e:                               # noqa: BLE001
            out["snap_error"] = out["snap_error"] or f"{type(e).__name__}: {e}"
        return out

    def _components_poll(self):
        job = self._comp_job
        if job is None or job.is_alive():
            return
        self._comp_timer.stop()
        self._comp_job = None
        if job.error is not None:
            dl.error("componentes", f"no se pudo revisar ffmpeg/libmpv ({type(job.error).__name__}: {job.error})")
            return
        res = job.result
        if res["snap_error"]:
            comp.legacy_error(res["snap_error"])
        elif res["snap"]:
            comp.legacy_ok()
            dl.info("legacy", f"Paquete Legacy guardado ({res['snap']})")
            self._refresh_legacy_button()
        save_config(self.cfg)                                 # quedó anotada la hora de la consulta
        items = [] if comp.legacy_active() else res["updates"]
        if not items:
            return
        for ln in (comp.describe_update(n, r, i) for n, r, i in items):
            dl.info("componentes", "Actualización disponible: " + ln)
        if self.engine:                                       # en pleno set no se interrumpe con una ventana: solo la barra de estado
            self.statusBar().showMessage(T("Hay actualizaciones de ffmpeg/libmpv: se preguntará al abrir el programa la próxima vez."), 15000)
            comp.defer_updates(self.cfg, items)               # (sin contestar: se vuelve a preguntar al próximo inicio)
            save_config(self.cfg)
            return
        self._offer_updates(items)

    def _offer_updates(self, items):
        """Un aviso por repositorio con actualización: «Sí» la instala ahora, «No» no actualiza esa versión (se avisa de una más nueva) y
        «Revisar después» vuelve a buscar la próxima vez que se abra el programa. Nada de esto se muestra con Legacy Mode activo."""
        if comp.legacy_active():
            return
        for item in items:
            name, rel, inst = item
            choice = ask_repo_update(self, comp.NAMES[comp.SRC_FFMPEG if name == "ffmpeg" else comp.SRC_MPV], comp.describe_update(name, rel, inst))
            if choice == "yes":
                self.install_update(name, rel)
            elif choice == "no":
                comp.skip_updates(self.cfg, [item])
            else:
                comp.defer_updates(self.cfg, [item])
            save_config(self.cfg)
            if self.restart_requested:                        # libmpv nueva: ya se eligió reiniciar, no hay que preguntar por el resto
                break

    def install_update(self, name, rel):
        """«Sí» al aviso: ventana de progreso con la descarga, la verificación y la prueba. Con libmpv puede pedir reiniciar."""
        dlg = UpdateDialog(name, rel, bool(self.engine), lambda t: self.statusBar().showMessage(t, 8000), self)
        dlg.exec()
        save_config(self.cfg)
        if dlg.restart_requested:
            self.restart_requested = True
            QTimer.singleShot(0, self.close)

    def open_calibration(self):
        if self.cfg["capture"]["mode"] == "window":     # la ventana se lee aunque estemos encima
            self._calibrate()
            return
        self.hide()
        self.out.hide()
        QTimer.singleShot(700, self._calibrate)       # modo pantalla: deja ver Traktor antes de capturar

    def _calibrate(self):
        try:
            dlg = CalibrationDialog(self.cfg, self)
        except Exception as e:
            dl.exception("calibración", "no se puede capturar Traktor")
            self.show()
            if self.embed:
                self.out.show()
            QMessageBox.warning(self, T("No se puede capturar Traktor"), str(e))
            return
        dl.devlog.resolve("calibración")                          # Traktor se pudo capturar: el error anterior quedó solucionado
        self._sync_alerts()
        self.show()
        if self.embed:
            self.out.show()
        dlg.exec()
        save_config(self.cfg)

    # ---------------------------------------------------------------- salida
    def apply_output(self):
        res = self.cb_res.currentText()
        self.cfg["output"]["resolution"] = res
        self.player.apply_output(res)
        self.out.set_resolution(*RES[res])               # el Reproductor mide la resolución elegida (16:9)
        save_config(self.cfg)

    # ------------------------------------------------- modo del Reproductor
    def _apply_window_mode(self):
        """Aplica el modo del config (ventana · sin bordes · pantalla completa) al Reproductor."""
        out = self.cfg["output"]
        if self.embed:
            self.out.set_mode(out.get("window_mode", "window"), out.get("screen", 0))
        else:
            self.player.apply_window_mode(out.get("window_mode", "window"))     # mpv con su propia ventana

    def _show_mode(self, mode):
        """Pone el combo del modo en `mode` sin volver a disparar su señal."""
        self.cb_mode.blockSignals(True)
        self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(mode)))
        self.cb_mode.blockSignals(False)

    def _on_window_mode(self):
        self._set("output", "window_mode", self.cb_mode.currentData())
        self._apply_window_mode()

    def _on_screen(self):
        self._set("output", "screen", max(0, self.cb_screen.currentIndex()))
        if self.cfg["output"].get("window_mode") == "fullscreen":
            self._apply_window_mode()                                          # la pantalla completa pasa a la pantalla elegida

    def _on_out_mode(self, mode):
        """El Reproductor cambió de modo solo (Esc): el config y el combo lo reflejan."""
        self._set("output", "window_mode", mode)
        self._show_mode(mode)

    def _on_cycle_mode(self):
        """Atajo de teclado (con el Reproductor seleccionado): siguiente modo en el orden ventana → sin bordes →
        pantalla completa → ventana. Es lo mismo que elegirlo en el combo (se aplica y se guarda)."""
        if not self.embed:
            return                                            # con la ventana propia de mpv no hay ventana nuestra que reciba la tecla
        cur = self.out.mode
        new = MODES[(MODES.index(cur) + 1) % len(MODES)] if cur in MODES else "window"
        self._show_mode(new)
        self._on_window_mode()

    # ------------------------------------------------- atajo de teclado
    def _show_hotkey(self, text):
        """Pone el atajo en el campo sin volver a disparar su señal."""
        self.ks_mode.blockSignals(True)
        self.ks_mode.setKeySequence(QKeySequence(text, QKeySequence.PortableText) if text else QKeySequence())
        self.ks_mode.blockSignals(False)

    def _apply_hotkey(self):
        """Aplica el atajo del config al Reproductor (Esc o un texto inválido se descartan) y lo muestra en el campo."""
        want = str(self.cfg["output"].get("mode_hotkey", "") or "").strip()
        got = self.out.set_hotkey(want)
        if got != want:
            self.cfg["output"]["mode_hotkey"] = got
        self._show_hotkey(got)

    def _on_hotkey_edit(self, seq):
        text = seq.toString(QKeySequence.PortableText) if seq is not None else ""
        got = self.out.set_hotkey(text)
        if text and not got:                                  # Esc (reservada) o una tecla que Qt no entiende
            self._show_hotkey("")
            self.statusBar().showMessage(T("Esc está reservada para salir de la pantalla completa: elige otra tecla."), 6000)
        self._set("output", "mode_hotkey", got)

    def _clear_hotkey(self):
        self._show_hotkey("")
        self._on_hotkey_edit(None)

    def fullscreen_out(self):
        """Botón «Visor a pantalla completa»: alterna entre pantalla completa y el último modo con ventana."""
        if self.cfg["output"].get("window_mode") == "fullscreen":
            new = self.out.windowed_mode if self.embed else "window"
        else:
            new = "fullscreen"
        if self.embed:
            self.out.show()
        self._show_mode(new)
        self._on_window_mode()

    # ----------------------------------------------------------------- motor
    def toggle_engine(self):
        if self.engine:
            self.engine.stop()
            self.engine.wait()
            self.engine = None
            self.btn_run.setText(T("▶  Iniciar motor"))
            self.scan(reason="motor")                                 # motor detenido: ¿hay videos nuevos?
            return
        if self.btn_test.isChecked():
            self.btn_test.setChecked(False)
        self._cancel_scan()                                           # con el motor en marcha la lista queda quieta (lo ya hallado se conserva)
        self.engine = Engine(self.cfg, self.db, self.player, self.videos)
        self.engine.state.connect(self.on_state)
        self.engine.log.connect(self._on_engine_log)
        dl.info("motor", "Iniciando el motor…")
        self.engine.changed.connect(self.on_engine_changed)
        self.engine.start(QThread.Priority.LowPriority)     # no le roba CPU a mpv ni a la GUI
        self.btn_run.setText(T("■  Detener motor"))

    def _on_engine_log(self, m):
        """El motor ya escribió el detalle en el registro (un error queda como aviso abierto y la barra lo muestra en rojo). Aquí
        solo se muestra el resultado de «Detectar layout ahora», que se pidió a mano."""
        if dl.classify(m) == dl.INFO and time.monotonic() < self._layout_msg_until and str(m).startswith("Layout:"):
            self.statusBar().showMessage(T(m), 8000)
        self._sync_alerts()

    def detect_from_decks(self):
        """Botón manual de autovinculación: vincula ya la canción cargada en cualquier deck; si ninguna coincide, alerta."""
        eng = self.engine
        if not eng:
            QMessageBox.information(self, T("Motor detenido"),
                                    T("Inicia el motor (con Traktor visible, no minimizado) para leer los decks."))
            return
        eng.autolink_hold = True                              # el motor no escribe tracks_db.json a la vez
        try:
            videos = [v for v in self.videos if self.db.entry_for_video(v) is None]
            matches, notes = al.scan_decks(eng.deck_snapshot(), videos, self.cfg["autolink"]["min_title"],
                                           self.cfg["ocr"]["duration_tolerance"], video_duration)
            if not matches:
                not_detected(self, notes + ([] if videos else ["No hay videos sin vincular en la carpeta."]))
                return
            for mt in matches:
                self.db.tracks.append({"traktor_track_name": mt["title"], "video_path": mt["video"], "base_bpm": None,
                                       "duration": fmt_time(mt["duration"]) if mt["duration"] is not None else ""})
            self.db.save()
            self.refresh_list()
            linked = " · ".join(f"Deck {mt['deck']} «{mt['title']}» ↔ {os.path.basename(mt['video'])}" for mt in matches)
            dl.info("autovinculación", "Vinculado a mano: " + linked)
            self.statusBar().showMessage(T("✨ Vinculado: " + linked + ". BPM Base Pendiente (azul): escríbelo en Configuración."), 15000)
        finally:
            eng.autolink_hold = False

    def on_engine_changed(self):
        """El motor detectó ROIs o autovinculó un video: se guarda la config y se actualiza la lista."""
        save_config(self.cfg)
        self.refresh_list()

    def toggle_force(self):
        if not self.engine:
            QMessageBox.information(self, T("Motor detenido"), T("Inicia el motor para forzar un video."))
            return
        if self.engine.forced:
            self.engine.forced = None
            self.btn_force.setText(T("Forzar video seleccionado"))
            return
        it = self.listw.currentItem()
        if it:
            self.engine.force(it.data(Qt.UserRole))
            self.btn_force.setText(T("Volver a automático"))

    def on_state(self, s):
        L = self.lbl
        L["video"].setText(TF(s["video"] or "—"))                 # (nombre de archivo: dato, sin cambiar su mayúscula)
        self._set_row("track", s["title"] or "—")
        L["bpm"].setText(T(f"{s['bpm']:.2f}" if s["bpm"] else "—"))
        L["speed"].setText(T(s.get("speed_info") or f"×{s['speed']:.3f}"))
        L["mode"].setText(T(s["mode"]))
        L["mode"].setStyleSheet(f"color:{SYNC_COLOR if s['mode'] == 'Sincronizado' else RANDOM_COLOR}; font-weight:bold;")
        self._put("deck", f"{s['deck']}  {PLAY + ' ' + TF('sonando') if s['playing'] else PAUSE + ' ' + TF('pausa')}"
                  + (f" — {s['why']}" if s.get("why") else ""))
        self._put("check", s["check"])
        self._put_player(s.get("player") or "—")
        for d, t in (s.get("decks") or {}).items():
            if f"deck_{d}" in L:
                self._put(f"deck_{d}", t, tip=t)
        self._put("auto", s.get("auto") or "—")
        self._put("sync", s.get("sync", "—"))
        self._put("next", s.get("next") or "—")
        self._put("capture", s.get("capture", "—"))
        if self.cfg["dev"].get("show_sensors"):                 # fila «Sensores (debug)»: solo si se activó en «Desarrollador»
            self._put("vu_dbg", s.get("vu_dbg", "—"))
        self.vu.setValue(int(s["level"] * 100))
        quiet = s["level"] > 0 and s.get("vu_moving") is False               # barras encendidas pero sin moverse: no es música
        self.vu.setFormat(T("Vúmetro %p% (quieto)") if quiet else T("Vúmetro %p%"))
        self._sync_alerts()                                     # ⚠ por fila y barra de estado según los avisos abiertos
        if s["video"] != self._active:
            self._active = s["video"]
            self.mark_active()

    def closeEvent(self, e):
        self._closing = True
        self._cancel_scan()
        if self.engine:
            self.engine.stop()
            self.engine.wait()
        self.player.shutdown()
        self.out.hide()
        e.accept()
