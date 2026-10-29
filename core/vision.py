# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Visión local: captura por ROI, OCR (Tesseract + OpenCV) y detector de actividad del vúmetro."""
import hashlib
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path

import cv2
import mss
import numpy as np
import pytesseract

from .glyphs import PAUSE, PLAY
from .lowprio import lower_tesseract_priority

lower_tesseract_priority()          # el OCR corre con prioridad baja: no le quita CPU al visor

OCR_CONFIGS = {
    "text": "--psm 7",
    "time": "--psm 7 -c tessedit_char_whitelist=0123456789:-.",
    "bpm": "--psm 7 -c tessedit_char_whitelist=0123456789.,",
    "pitch": "--psm 7 -c tessedit_char_whitelist=0123456789.,+-%",
}


class ScreenGrabber:
    """ROIs en píxeles relativos al monitor elegido. Crear una instancia por hilo."""

    def __init__(self, monitor_index=1):
        self.sct = mss.mss()
        self.mon = self.sct.monitors[monitor_index]

    def grab(self, roi, wait=0.0):
        l, t, w, h = roi
        shot = self.sct.grab({"left": self.mon["left"] + l, "top": self.mon["top"] + t,
                              "width": max(1, w), "height": max(1, h)})
        return np.ascontiguousarray(np.array(shot)[:, :, :3])      # BGR

    def full(self):
        return self.grab([0, 0, self.mon["width"], self.mon["height"]])

    backend_name = "pantalla"

    def register(self, rois):
        pass

    def prime(self, rois, timeout=0.0):
        pass

    @property
    def size(self):
        return (self.mon["width"], self.mon["height"])

    def status(self):
        return f"pantalla completa · {self.mon['width']}×{self.mon['height']}"

    def close(self):
        self.sct.close()


def preprocess(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if th.mean() < 127:               # tema oscuro de Traktor: texto claro -> invertir
        th = 255 - th
    return cv2.copyMakeBorder(th, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)


def ocr_text(img, kind="text"):
    return pytesseract.image_to_string(preprocess(img), config=OCR_CONFIGS[kind]).strip()


TESSERACT_PATHS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                   r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                   r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
                   r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"]
INSTALL_HINT = ("Instálalo con «winget install UB-Mannheim.TesseractOCR» (en una consola) o desde "
                "github.com/UB-Mannheim/tesseract/wiki, y reinicia el programa. Si lo tienes en otra carpeta, "
                "pon la ruta de tesseract.exe en config.json → ocr.tesseract_cmd.")


def find_tesseract(cmd=""):
    """Ruta de tesseract.exe: la de la config, el PATH o las carpetas de instalación habituales (el instalador de
    Windows no lo añade al PATH)."""
    cands = [cmd, shutil.which("tesseract")] + [os.path.expandvars(p) for p in TESSERACT_PATHS]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def tesseract_status(cmd=""):
    """(ok, mensaje). Si lo encuentra, deja configurado pytesseract con esa ruta."""
    path = find_tesseract(cmd)
    if not path:
        return False, "Tesseract (el OCR que lee título, BPM y tiempos de Traktor) no está instalado. " + INSTALL_HINT
    pytesseract.pytesseract.tesseract_cmd = path
    try:
        return True, f"Tesseract {pytesseract.get_tesseract_version()} ({path})"
    except Exception as e:
        return False, f"Tesseract no responde ({e}). " + INSTALL_HINT


class OcrReader:
    """OCR con caché: solo se reprocesa un ROI si sus píxeles cambiaron."""

    def __init__(self, cmd=""):
        self.ok, self.status = tesseract_status(cmd)
        self.cache = {}

    def read(self, key, img, kind):
        if not self.ok:
            return ""                                   # sin Tesseract no hay lectura (el motor sigue con Random Visuals)
        h = hashlib.md5(img.tobytes()).digest()
        c = self.cache.get(key)
        if c and c[0] == h:
            return c[1]
        text = ocr_text(img, kind)
        self.cache[key] = (h, text)
        return text


def parse_time(text):
    m = re.search(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})", re.sub(r"[.;,]", ":", text or ""))
    if not m:
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def parse_pitch(text):
    """Tempo que muestra Traktor bajo el BPM («+3.7%», «-0.0%») -> porcentaje, o None si no es fiable. Sin signo solo se
    acepta el cero: un «3.7%» sin signo podría ser + o − y daría un BPM base equivocado."""
    m = re.fullmatch(r"\s*([+\-−–—]?)\s*(\d{1,2})[.,](\d)\s*%?\s*", text or "")
    if not m:
        return None
    val = int(m.group(2)) + int(m.group(3)) / 10.0
    if val == 0:
        return 0.0
    if not m.group(1):
        return None
    return -val if m.group(1) in "-−–—" else val


def parse_bpm(text):
    m = re.search(r"\d{2,3}(?:\.\d{1,2})?", (text or "").replace(",", "."))
    if not m:
        return None
    v = float(m.group())
    return v if 40 <= v <= 250 else None


def color_name(hue):
    """Tono OpenCV (0-179) -> nombre legible para el panel de depuración."""
    if 15 <= hue <= 45:
        return "amarillo"
    if 85 <= hue <= 135:
        return "azul"
    return f"tono {int(hue)}"


def analyze_vu(img, cfg, base=None):
    """Mide el vúmetro: píxeles encendidos, altura del nivel (0-1) y color del círculo más alto.

    Encendido = COLOREADO (saturado: el azul y el amarillo de Traktor; los apagados son grises, S=0), o distinto de
    la imagen de silencio guardada (respaldo para temas con círculos sin color). No cuenta círculos uno a uno:
    mide hasta qué fila llega lo encendido, así funciona igual con círculos nítidos (4×4 px) o borrosos."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = (hsv[..., 1] >= cfg["sat_min"]) & (hsv[..., 2] >= cfg["val_min"])
    if base is not None and base.shape == img.shape:
        bh = cv2.cvtColor(base, cv2.COLOR_BGR2HSV)         # lo que ya estaba coloreado en silencio (puntos fijos,
        mask &= ~((bh[..., 1] >= cfg["sat_min"]) & (bh[..., 2] >= cfg["val_min"]))   # de un recorte demasiado grande)
        mask |= cv2.absdiff(img, base).max(axis=2) >= cfg["pix_delta"]
    px = int(mask.sum())
    ys = np.flatnonzero(mask.sum(axis=1) >= cfg["min_row_px"])
    if px < cfg["min_lit_px"] or len(ys) == 0:
        return {"lit_px": px, "level": 0.0, "top": None}
    h_roi, top = img.shape[0], int(ys[0])
    sel = mask[top:top + 5]                                # el círculo más alto encendido
    hs, ss = hsv[top:top + 5][sel][:, 0], hsv[top:top + 5][sel][:, 1]
    name = "sin color" if np.median(ss) < cfg["sat_min"] else color_name(float(np.median(hs)))
    return {"lit_px": px, "level": min(1.0, (h_roi - top) / (0.95 * h_roi)), "top": name}


def baseline_path(deck):
    return Path(f"vu_baseline_{deck}.png")


def motion(frames, pix_delta):
    """Mayor número de píxeles que cambian entre dos fotogramas consecutivos."""
    return max((int((cv2.absdiff(a, b).max(axis=2) >= pix_delta).sum()) for a, b in zip(frames, frames[1:])),
               default=0)


def save_baseline(deck, frames):
    """Guarda la imagen media del vúmetro en silencio (respaldo para círculos encendidos sin color)."""
    mean = np.mean(np.stack(frames).astype(np.float32), axis=0).astype(np.uint8)
    cv2.imwrite(str(baseline_path(deck)), mean)


class VuMeter:
    """Hay música si hay círculos encendidos Y el vúmetro se está moviendo (uno congelado no cuenta)."""

    def __init__(self, cfg, deck=None):
        self.c, self.deck = cfg, deck
        self.prev, self._prev_lit = None, 0
        self.hist = deque(maxlen=cfg["window"])
        self.last_on, self.last_change = 0.0, 0.0
        self._base, self._base_key = None, None
        self.last = {"lit_px": 0, "level": 0.0, "top": None, "change": 0}

    def _baseline(self, shape):
        if not self.deck:
            return None
        p = baseline_path(self.deck)
        mt = p.stat().st_mtime if p.exists() else None
        key = (mt, shape)
        if key != self._base_key:                       # se recarga si cambia el archivo o el ROI
            img = cv2.imread(str(p), cv2.IMREAD_COLOR) if mt else None
            self._base = img if img is not None and img.shape == shape else None
            self._base_key = key
        return self._base

    def update(self, img, now=None):
        now = now or time.time()
        c = self.c
        change = 0
        if self.prev is not None and self.prev.shape == img.shape:
            change = int((cv2.absdiff(img, self.prev).max(axis=2) >= c["pix_delta"]).sum())
        self.prev = img
        a = analyze_vu(img, c, self._baseline(img.shape))
        lit = a["lit_px"] >= c["min_lit_px"]
        self.last = dict(a, change=change)
        # solo cuenta el movimiento del propio vúmetro (con círculos encendidos antes o ahora)
        if change >= c["change_px"] and (lit or self._prev_lit):
            self.last_change = now
        self._prev_lit = lit
        self.hist.append(lit and now - self.last_change < c["recent"])
        if sum(self.hist) >= c["on_samples"]:
            self.last_on = now
        return a["level"], (now - self.last_on) < c["hold"]


class TransportDetector:
    """¿Avanza el reloj del deck? Los dígitos del tiempo restante cambian cada segundo solo si el track suena."""

    def __init__(self, cfg, pix_delta):
        self.c, self.pix = cfg, pix_delta
        self.prev, self.ticks, self.last_tick, self.t0 = None, deque(), None, None
        self.ever = False                                  # ¿el reloj ya avanzó alguna vez? (el ROI lee un reloj real)

    def update(self, img, now):
        if self.t0 is None:
            self.t0 = now
        if self.prev is not None and self.prev.shape == img.shape:
            if float((cv2.absdiff(img, self.prev).max(axis=2) >= self.pix).mean()) >= self.c["tick_ratio"]:
                self.ticks.append(now)
                self.last_tick, self.ever = now, True
        self.prev = img
        while self.ticks and now - self.ticks[0] > self.c["window"]:
            self.ticks.popleft()
        return len(self.ticks) >= self.c["min_ticks"]

    def idle(self, now):
        ref = self.last_tick if self.last_tick is not None else self.t0
        return 0.0 if ref is None else now - ref


class DeckSensor:
    """Combina vúmetro y reloj de un deck para decidir si suena, y captura sola la referencia de silencio."""

    def __init__(self, deck, cfg):
        self.deck, self.cfg = deck, cfg
        self.vu = VuMeter(cfg["vu"], deck)
        self.tr = TransportDetector(cfg["transport"], cfg["vu"]["pix_delta"])
        self.frames = deque(maxlen=10)
        self.level, self.vu_on, self.tr_on, self.on, self.reason = 0.0, None, None, None, ""
        self.last_img, self.just_saved = None, False

    def update(self, grab, rois, now):
        self.vu_on = self.tr_on = None
        self.just_saved = False
        if rois.get("vu"):
            img = grab.grab(rois["vu"])
            self.level, self.vu_on = self.vu.update(img, now)
            self.frames.append(img)
            self.last_img = img
        if rois.get("remaining"):
            self.tr_on = self.tr.update(grab.grab(rois["remaining"]), now)
        self._decide(now)
        self._auto_baseline(now)
        return self.on

    def _decide(self, now):
        """¿Suena el deck? Con mode «any», si el vúmetro se queda quieto se mira el reloj: si el deck sigue avanzando
        (un silencio o un breakdown del track), sigue contando como sonando."""
        d = self.cfg["detect"]
        sig = {"vu": self.vu_on, "transport": self.tr_on}
        used = {k: sig[k] for k in d["signals"] if sig.get(k) is not None}
        self.reason = ""
        if not used:
            self.on = None
            return
        if d.get("mode", "any") == "all":
            self.on = all(used.values())
            return
        self.on = any(used.values())
        if not self.on:
            return
        stall = d.get("stall_s", 0)
        if stall and used.get("transport") is False and "vu" in used and self.tr.ever and self.tr.idle(now) >= stall:
            self.on, self.reason = False, f"reloj quieto {self.tr.idle(now):.0f} s: deck pausado"   # cola de eco, no música
        elif used.get("transport") and used.get("vu") is False:
            self.reason = "vúmetro en silencio pero el deck sigue avanzando"
        elif used.get("vu") and used.get("transport") is False:
            self.reason = "vúmetro activo (el reloj aún no avanzó)"

    def _auto_baseline(self, now):
        """Si el reloj lleva >= 3 s quieto y el vúmetro no se mueve, eso ES el silencio: se guarda como referencia."""
        if self.vu_on is None or self.tr_on is None or baseline_path(self.deck).exists():
            return
        v = self.cfg["vu"]
        if (len(self.frames) == self.frames.maxlen and self.tr.idle(now) >= 3.0
                and motion(list(self.frames), v["pix_delta"]) < v["change_px"]):
            save_baseline(self.deck, list(self.frames))
            self.just_saved = True

    def debug(self):
        m = self.vu.last
        parts = []
        if self.vu_on is not None:
            top = f" ({m['top']})" if m["top"] else ""
            parts.append(f"VU {m['level']:.0%}{top} mov{m['change']} {PLAY if self.vu_on else PAUSE}")
        if self.tr_on is not None:
            parts.append(f"reloj {PLAY if self.tr_on else PAUSE}")
        verdict = "SONANDO" if self.on else "pausa"
        if self.reason:
            verdict += f" ({self.reason})"
        return f"{self.deck}: " + " · ".join(parts) + f" → {verdict}" if parts else f"{self.deck}: sin ROIs"
