# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Panel de configuración: vincula cada video con su track de Traktor (tracks_db.json)."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from core import autolink as al
from core.db import fmt_bpm, fmt_time, parse_duration
from core.media import video_duration
from core.i18n import tr as T
from gui import adaptive
from gui.alerts import not_detected
from gui.colors import LEGEND, PENDING_COLOR, row_color

COLS = ["Video", "Título en Traktor (1.ª línea del deck, sin artista)", "BPM Base", "Duración (Min:Seg)"]


class ConfigDialog(QDialog):
    def __init__(self, videos, db, engine, parent=None):
        super().__init__(parent)
        self.setWindowTitle(T("Configuración de tracks ↔ videos"))
        adaptive.fit_window(self, 900, 520)                    # sin pasarse de la pantalla
        self.db, self.engine = db, engine

        self.table = QTableWidget(len(videos), len(COLS))
        self.table.setHorizontalHeaderLabels([T(c) for c in COLS])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        for r, v in enumerate(videos):
            e = db.entry_for_video(v) or {}
            name = QTableWidgetItem(T(os.path.basename(v)))
            name.setData(Qt.UserRole, v)
            name.setFlags(name.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, QTableWidgetItem(T(e.get("traktor_track_name", ""))))
            self.table.setItem(r, 2, QTableWidgetItem(T(fmt_bpm(e.get("base_bpm")))))
            self.table.setItem(r, 3, QTableWidgetItem(T(fmt_time(parse_duration(e.get("duration"))))))
            self._paint_row(r)
        self.table.itemChanged.connect(lambda it: self._paint_row(it.row()) if it.column() in (1, 2) else None)

        btn_det = QPushButton(T("✨ Detectar desde los decks"))
        btn_det.setToolTip(T("Compara la canción cargada en cada deck de Traktor con el video seleccionado (o, sin selección,\n"
                           "con todos los videos sin vincular): título ≥ 70 % parecido y duración coincidente. Si coincide,\n"
                           "rellena la fila (título y duración; el BPM Base queda pendiente). Requiere el motor iniciado."))
        btn_det.clicked.connect(self.detect_from_decks)
        btn_ocr = QPushButton(T("Leer del deck activo (OCR)"))
        btn_ocr.setToolTip(T("Rellena la fila seleccionada con el título/BPM/duración que Traktor muestra ahora.\n"
                           "Requiere el motor iniciado. Úsalo con el tempo del track en 0%."))
        btn_ocr.clicked.connect(self.read_from_deck)
        btn_dur = QPushButton(T("Duración desde el archivo de video (todas)"))
        btn_dur.clicked.connect(self.detect_durations)
        btn_clear = QPushButton(T("Desvincular (→ Random Visuals)"))
        btn_clear.clicked.connect(self.clear_row)
        row = QHBoxLayout()
        for b in (btn_det, btn_ocr, btn_dur, btn_clear):
            row.addWidget(b)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        note = QLabel(LEGEND + T("<br>Filas sin nombre de Traktor quedan como «Random Visuals». Con el "
                      "<b>BPM Base Pendiente</b> (p. ej. las autovinculadas) el visual va a ×1 hasta que lo escribas (BPM del track "
                      "con el tempo en 0 %; «Leer del deck activo» lo copia de Traktor).<br>"
                      "La autovinculación automática se pausa mientras esta ventana está abierta (el motor la retoma al "
                      "cerrarla): usa «Detectar desde los decks» para vincular ahora la canción que tengas cargada."))
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addWidget(self.table)
        lay.addWidget(self.status)
        lay.addLayout(row)
        lay.addWidget(bb)

    def _paint_row(self, r):
        """Color del texto de la fila: verde = visual configurado · azul = BPM Base Pendiente · naranja = Random Visual."""
        items = [self.table.item(r, k) for k in range(self.table.columnCount())]
        if any(it is None for it in items):
            return
        col = row_color(items[1].text(), items[2].text())
        brush = QBrush(QColor(col))
        self.table.blockSignals(True)
        for it in items:
            it.setForeground(brush)
        items[2].setToolTip(T("BPM Base Pendiente: escríbelo (BPM del track con el tempo en 0 %)" if col == PENDING_COLOR else ""))
        self.table.blockSignals(False)

    def detect_from_decks(self):
        """Detección manual: ¿alguna canción cargada en los decks corresponde a un video sin vincular? Si no, alerta."""
        eng = self.engine
        if not eng or not hasattr(eng, "deck_snapshot"):
            QMessageBox.information(self, T("Motor detenido"),
                                    T("Inicia el motor (con Traktor visible, no minimizado) para leer los decks y vuelve a "
                                    "pulsar «Detectar desde los decks»."))
            return
        sel = self.table.currentRow()
        rows = [sel] if sel >= 0 else [r for r in range(self.table.rowCount()) if not self.table.item(r, 1).text().strip()]
        if not rows:
            QMessageBox.information(self, T("Nada que detectar"),
                                    T("Todos los videos ya tienen nombre. Selecciona una fila para volver a detectarla."))
            return
        by_path = {self.table.item(r, 0).data(Qt.UserRole): r for r in rows}
        matches, notes = al.scan_decks(eng.deck_snapshot(), list(by_path), eng.cfg["autolink"]["min_title"],
                                       eng.cfg["ocr"]["duration_tolerance"], video_duration)
        if not matches:
            scope = ("Se comparó solo con el video seleccionado." if sel >= 0
                     else f"Se comparó con los {len(rows)} videos sin vincular.")
            not_detected(self, notes + [scope])
            self.status.setText(T("Canción no detectada en ningún Deck."))
            return
        lines = []
        for m in matches:
            r = by_path[m["video"]]
            self.table.item(r, 1).setText(T(m["title"]))
            if m["duration"] is not None:
                self.table.item(r, 3).setText(T(fmt_time(m["duration"])))
            shown = f" (Traktor Pro 4 muestra {m['bpm']:.2f} BPM)" if m.get("bpm") else ""
            lines.append(f"✨ Deck {m['deck']}: «{m['title']}» ↔ {os.path.basename(m['video'])} (título {m['ratio']:.0%}, "
                         f"duración ≈ video {fmt_time(m['vdur'])}). BPM Base Pendiente: escríbelo{shown}.")
        self.table.selectRow(by_path[matches[0]["video"]])
        self.table.scrollToItem(self.table.item(by_path[matches[0]["video"]], 1))
        self.status.setText(T("\n".join(lines + notes)))

    def _row(self):
        r = self.table.currentRow()
        if r < 0:
            QMessageBox.information(self, T("Selecciona una fila"), T("Selecciona primero un video de la tabla."))
        return r

    def read_from_deck(self):
        r = self._row()
        rd = getattr(self.engine, "last_reading", None) if self.engine else None
        if r < 0:
            return
        if not rd or not rd.get("title"):
            QMessageBox.information(self, T("Sin lectura"), T("Inicia el motor, calibra los ROIs y carga un track en Traktor."))
            return
        self.table.item(r, 1).setText(T(rd["title"]))
        if rd.get("bpm"):
            self.table.item(r, 2).setText(T(fmt_bpm(rd["bpm"])))
        if rd.get("duration") is not None:
            self.table.item(r, 3).setText(T(fmt_time(rd["duration"])))

    def detect_durations(self):
        for r in range(self.table.rowCount()):
            d = video_duration(self.table.item(r, 0).data(Qt.UserRole))
            if d:
                self.table.item(r, 3).setText(T(fmt_time(d)))

    def clear_row(self):
        r = self._row()
        if r >= 0:
            for c in (1, 2, 3):
                self.table.item(r, c).setText("")

    def save(self):
        tracks = []
        for r in range(self.table.rowCount()):
            title = self.table.item(r, 1).text().strip()
            if not title:
                continue
            path = self.table.item(r, 0).data(Qt.UserRole)
            txt = self.table.item(r, 2).text().strip()
            try:
                bpm = round(float(txt.replace(",", ".")), 2) if txt else None            # vacío = pendiente (el visual va a ×1)
            except ValueError:
                QMessageBox.warning(self, T("BPM inválido"),
                                    T(f"Fila {r + 1}: el BPM Base debe ser un número con decimales, p. ej. 128.00 "
                                    f"(o déjalo vacío si aún no lo sabes)."))
                return
            dur = parse_duration(self.table.item(r, 3).text())
            if dur is None:
                QMessageBox.warning(self, T("Duración inválida"),
                                    T(f"Fila {r + 1}: la duración debe tener el formato de Traktor (Min:Seg), p. ej. 02:52."))
                return
            tracks.append({"traktor_track_name": title, "video_path": path, "base_bpm": bpm,
                           "duration": fmt_time(dur)})
        self.db.replace_all(tracks)
        self.accept()
