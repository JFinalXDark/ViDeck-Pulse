# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Calibración de ROIs sobre una captura de Traktor, con plantillas medidas de tu layout."""
import threading
import time

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QRubberBand,
                               QVBoxLayout)

from core.capture import make_grabber
from core.layout import DECK_REF_W, DECK_TPL, MIXER_REF_W, MIXER_VU, Layout, apply_layout, detect_layout
from core.vision import DeckSensor, analyze_vu, motion, ocr_text, save_baseline, tesseract_status
from core.i18n import tr as T

KEYS = ["title", "bpm", "remaining", "duration", "pitch", "vu"]
KINDS = {"title": "text", "bpm": "bpm", "remaining": "time", "duration": "time", "pitch": "pitch"}

# Las plantillas (DECK_TPL, MIXER_VU…) están en core/layout.py: las usan también la detección automática del layout.


class RoiLabel(QLabel):
    selected = Signal(QRect)

    def __init__(self):
        super().__init__()
        self.band = QRubberBand(QRubberBand.Rectangle, self)
        self.origin = QPoint()

    def mousePressEvent(self, e):
        self.origin = e.position().toPoint()
        self.band.setGeometry(QRect(self.origin, QSize()))
        self.band.show()

    def mouseMoveEvent(self, e):
        self.band.setGeometry(QRect(self.origin, e.position().toPoint()).normalized())

    def mouseReleaseEvent(self, e):
        r = self.band.geometry()
        self.band.hide()
        if r.width() > 3 and r.height() > 3:
            self.selected.emit(r)


def to_pixmap(bgr, height=None):
    h, w = bgr.shape[:2]
    pm = QPixmap.fromImage(QImage(bgr.data, w, h, 3 * w, QImage.Format_BGR888).copy())
    return pm.scaledToHeight(height, Qt.FastTransformation) if height else pm


class CalibrationDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle(T("Calibrar ROIs de Traktor"))
        self.cfg = cfg
        tesseract_status(cfg["ocr"].get("tesseract_cmd", ""))      # localiza Tesseract aunque se instalara con la app abierta
        self.grabber = make_grabber(cfg)                # ventana de Traktor (o pantalla), igual que el motor
        try:
            if hasattr(self.grabber, "wait_ready"):
                self.grabber.wait_ready(10.0)
            shot = self.grabber.full()                  # lanza WindowUnavailable si no se puede leer
        except Exception:
            self.grabber.close()                        # si no, el hilo de captura queda vivo y se acumulan al reintentar
            raise
        self.shot = shot                                 # captura completa (BGR): la usa «Detectar automáticamente»
        h, w = shot.shape[:2]
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.base = to_pixmap(shot).scaled(int(scr.width() * 0.85), int(scr.height() * 0.55),
                                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.k = w / self.base.width()                  # factor pantalla-real / vista

        self.combo = QComboBox()
        for d in cfg["decks"]:
            self.combo.addItem(T(f"PLANTILLA · encabezado del deck {d} (arrastra de su esquina sup. izq. a su borde der.)"),
                               (d, "tpl_deck"))
        self.combo.addItem(T("PLANTILLA · mixer completo (vúmetros de todos los decks)"), ("*", "tpl_mixer"))
        for d in cfg["decks"]:
            for key in KEYS:
                self.combo.addItem(T(f"Deck {d} · {key} (ajuste manual)"), (d, key))
        self.btn_auto = QPushButton(T("✨ Detectar automáticamente"))
        self.btn_auto.setToolTip(T("Calcula solos los ROIs de título, BPM, restante, duración, tempo y vúmetros leyendo esta\n"
                                 "captura. Funciona con Traktor maximizado o a pantalla completa. Necesita un track cargado\n"
                                 "en algún deck (el otro se calcula por geometría). Luego puedes corregir a mano."))
        self.btn_auto.clicked.connect(self.auto_detect)
        btn_test = QPushButton(T("Probar ROI"))
        btn_test.clicked.connect(self.test)
        btn_sil = QPushButton(T("Medir silencio (decks en pausa)"))
        btn_sil.clicked.connect(self.measure_silence)
        self.btn_live = QPushButton(T("Monitor VU en vivo"))
        self.btn_live.setCheckable(True)
        self.btn_live.toggled.connect(self._toggle_live)
        btn_close = QPushButton(T("Listo"))
        btn_close.clicked.connect(self.accept)
        top = QHBoxLayout()
        top.addWidget(self.combo, 1)
        for wdg in (self.btn_auto, btn_test, btn_sil, self.btn_live, btn_close):
            top.addWidget(wdg)

        self.view = RoiLabel()
        self.view.setFixedSize(self.base.size())
        self.view.selected.connect(self.on_selected)
        self.info = QLabel(T("Elige una plantilla y arrastra un rectángulo sobre la captura."))
        self.info.setWordWrap(True)

        # monitor en vivo: recorte ampliado de cada vúmetro + métricas
        self.crops = {d: QLabel(T(f"{d}")) for d in cfg["decks"]}
        crow = QHBoxLayout()
        for lbl in self.crops.values():
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setMinimumWidth(90)
            crow.addWidget(lbl)
        self.live_info = QLabel("")
        self.live_info.setWordWrap(True)
        crow.addWidget(self.live_info, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick_live)
        self.meters = {}
        self._lay_thread, self._lay_res = None, None
        self._lay_timer = QTimer(self)
        self._lay_timer.timeout.connect(self._poll_auto)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.view)
        lay.addWidget(self.info)
        lay.addLayout(crow)
        self._redraw()

    # ----------------------------------------------------------- dibujo / ROIs
    def _g(self, roi):
        """Recorte de un ROI (lo registra y espera a que el grabador lo tenga)."""
        self.grabber.register([roi])
        return self.grabber.grab(roi, wait=2.0)

    def _all_rois(self):
        return [r for rois in self.cfg["decks"].values() for k in KEYS if (r := rois.get(k))]

    def _save_ref(self):
        """Recuerda con qué tamaño y método se calibró; el motor avisa si cambian."""
        self.cfg["capture"]["ref"] = {"backend": self.grabber.backend_name, "size": list(self.grabber.size)}

    def _redraw(self):
        pm = self.base.copy()
        p = QPainter(pm)
        for d, rois in self.cfg["decks"].items():
            for key in KEYS:
                r = rois.get(key)
                if r:
                    p.setPen(QPen(QColor("#ff5090" if key == "vu" else "#00e0ff"), 2))
                    x, y = int(r[0] / self.k), int(r[1] / self.k)
                    p.drawRect(x, y, max(1, int(r[2] / self.k)), max(1, int(r[3] / self.k)))
                    p.drawText(x, y - 3, f"{d}.{key}")
        p.end()
        self.view.setPixmap(pm)

    def on_selected(self, rect):
        d, key = self.combo.currentData()
        self._save_ref()
        x, y, w, h = (v * self.k for v in (rect.x(), rect.y(), rect.width(), rect.height()))
        if key == "tpl_deck":
            s = w / DECK_REF_W
            for name, (rx, ry, rw, rh) in DECK_TPL.items():
                self.cfg["decks"][d][name] = [int(x + rx * s), int(y + ry * s), int(rw * s), int(rh * s)]
            self._redraw()
            self.info.setText(T(f"Deck {d}: ROIs de título, BPM, restante y duración generados. "
                              f"Lectura OCR ahora → {self._ocr_summary(d)}"))
        elif key == "tpl_mixer":
            s = w / MIXER_REF_W
            for deck, (rx, ry, rw, rh) in zip(self.cfg["decks"], MIXER_VU):
                self.cfg["decks"][deck]["vu"] = [int(x + rx * s), int(y + ry * s), int(rw * s), int(rh * s)]
            self._redraw()
            self.info.setText(T("Vúmetros generados (izquierda = primer deck). Activa «Monitor VU en vivo» para comprobar "
                              "que el recorte enmarca la columna de puntos; luego pausa los decks y pulsa «Medir silencio»."))
        else:
            self.cfg["decks"][d][key] = [int(x), int(y), int(w), int(h)]
            self._redraw()
            self.test()

    # ------------------------------------------------------- detección automática
    def auto_detect(self):
        if self._lay_thread is not None:
            return
        self.btn_auto.setEnabled(False)
        self.info.setText(T("Detectando el layout (unos segundos: se lee la cabecera de los decks con OCR)…"))
        names, img = list(self.cfg["decks"]), self.shot

        def job():
            try:
                self._lay_res = detect_layout(img, names)
            except Exception as e:                        # Tesseract ausente, etc.
                self._lay_res = e

        self._lay_res = None
        self._lay_thread = threading.Thread(target=job, daemon=True)
        self._lay_thread.start()
        self._lay_timer.start(150)

    def _poll_auto(self):
        if self._lay_thread is None or self._lay_thread.is_alive():
            return
        self._lay_timer.stop()
        self._lay_thread, res = None, self._lay_res
        self.btn_auto.setEnabled(True)
        if not isinstance(res, Layout):
            self.info.setText(T(f"No se pudo detectar el layout: {res}. ¿Tesseract instalado?"))
            return
        if not res.usable:
            self.info.setText(T("No se encontró nada que detectar. " + " ".join(res.notes)))
            return
        apply_layout(self.cfg, res, self.grabber.backend_name, self.grabber.size)
        self._redraw()
        lines = [f"Layout detectado: {res.summary()}."]
        lines += [f"Deck {d} → {txt}" for d, txt in res.readings.items()]
        lines += res.notes
        lines.append("Comprueba los recuadros sobre la captura; si algo no encaja, ajústalo con las plantillas o a mano. "
                     "Con los decks en pausa pulsa «Medir silencio».")
        self.info.setText(T("\n".join(lines)))

    def _ocr_summary(self, d):
        try:
            parts = []
            for key in ("title", "bpm", "remaining", "duration", "pitch"):
                roi = self.cfg["decks"][d].get(key)
                parts.append(f"{key}=«{ocr_text(self._g(roi), KINDS[key]) if roi else ''}»")
            return "  ".join(parts)
        except Exception as e:
            return f"error de OCR (¿Tesseract instalado?): {e}"

    def test(self):
        d, key = self.combo.currentData()
        if key.startswith("tpl"):
            self.info.setText(T("Dibuja la plantilla; luego elige un ROI concreto y pulsa «Probar ROI»."))
            return
        roi = self.cfg["decks"][d].get(key)
        if not roi:
            self.info.setText(T("Ese ROI aún no está definido."))
            return
        try:
            img = self._g(roi)
            if key == "vu":
                a = analyze_vu(img, self.cfg["vu"])
                self.info.setText(T(f"Vúmetro {d}: nivel {a['level']:.0%}"
                                  f"{' (círculo más alto: ' + a['top'] + ')' if a['top'] else ''}. "
                                  f"Con el deck en pausa debe marcar 0 %; sonando, entre ~5 % y 100 %."))
            else:
                self.info.setText(T(f"OCR {d}.{key}: «{ocr_text(img, KINDS[key])}»"))
        except Exception as e:
            self.info.setText(T(f"Error de OCR (¿Tesseract instalado?): {e}"))

    # -------------------------------------------------------------- vúmetros
    def measure_silence(self):
        rois = {d: r["vu"] for d, r in self.cfg["decks"].items() if r.get("vu")}
        if not rois:
            self.info.setText(T("Define primero los ROIs de vúmetro (plantilla del mixer)."))
            return
        frames = {d: [] for d in rois}
        try:
            self.grabber.prime(list(rois.values()))
            for _ in range(10):                          # 1 s de muestras
                for d, roi in rois.items():
                    frames[d].append(self.grabber.grab(roi))
                time.sleep(0.1)
        except Exception as e:
            self.info.setText(T(f"No se pudo leer la ventana: {e}"))
            return
        out = []
        for d, fr in frames.items():
            if motion(fr, self.cfg["vu"]["pix_delta"]) >= self.cfg["vu"]["change_px"]:
                out.append(f"{d}: el vúmetro SE MUEVE, no se guardó (pausa ese deck o sube su fader a cero)")
            else:
                save_baseline(d, fr)
                out.append(f"{d}: referencia de silencio guardada")
        self.info.setText(T("  ·  ".join(out)))

    def _toggle_live(self, on):
        if on:
            self.grabber.prime(self._all_rois())
            self.meters = {d: DeckSensor(d, self.cfg) for d in self.cfg["decks"]}
            self.timer.start(150)
        else:
            self.timer.stop()

    def _tick_live(self):
        lines = []
        self.grabber.register(self._all_rois())
        for d, rois in self.cfg["decks"].items():
            if not (rois.get("vu") or rois.get("remaining")):
                continue
            sen = self.meters[d]
            try:
                sen.update(self.grabber, rois, time.time())
            except Exception as e:
                lines.append(f"Deck {d}: ⚠ {e}")
                continue
            if sen.last_img is not None:
                self.crops[d].setPixmap(to_pixmap(sen.last_img, 220))
            lines.append(sen.debug())
        self.live_info.setText(T("\n".join(lines) or "Sin ROIs de vúmetro."))

    def done(self, r):
        self.timer.stop()
        self._lay_timer.stop()
        self.grabber.close()
        super().done(r)
