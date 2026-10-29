# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Detección automática de los ROIs de Traktor (cabecera de cada deck y vúmetros) a partir de una captura de su ventana.

No depende de Qt: la usan el motor (al iniciar o si cambia el tamaño de la ventana) y el diálogo de calibración.

Cómo funciona
  1. Tesseract lee la franja superior de la ventana. En cada cabecera de deck aparecen siempre tres textos con una forma
     inconfundible: el tiempo restante («-02:52», arriba), la duración («02:52», justo debajo) y el BPM («128.00», a su
     derecha). Con sus cajas se calculan los ROIs de restante, duración y BPM; el título empieza donde la plantilla de tu
     layout dice (margen fijo respecto al borde izquierdo del deck) y termina antes del restante.
  2. Los vúmetros son dos columnas de puntos de 5 px de paso que ninguna otra cosa de Traktor imita; se buscan por
     estructura (no por OCR), así que no hace falta que haya música ni canción cargada.
  3. Si un deck está vacío (sin canción no hay texto que leer), sus ROIs se calculan por geometría a partir del otro deck y
     de las columnas del mixer, siempre que la medición del deck leído confirme que la geometría encaja.

Todas las medidas de referencia (px) salen de tus capturas con la interfaz de Traktor al 100 %; si la interfaz está
escalada, el factor `scale` (alto del texto / alto de referencia, o paso de los puntos / 5) las ajusta.
"""
import re
from dataclasses import dataclass, field

import cv2
import numpy as np
import pytesseract

from .lowprio import lower_tesseract_priority

lower_tesseract_priority()          # la detección (varios segundos de Tesseract) corre con prioridad baja

# ---- plantillas de referencia (px, interfaz al 100 %) ----------------------------------------------------------------
# Cabecera de un deck, relativa a su esquina superior izquierda (ancho de referencia 820 px).
DECK_REF_W = 820
DECK_TPL = {"title": (76, 11, 524, 22),       # "Born Funky" (primera línea, con margen para títulos largos)
            "remaining": (610, 11, 72, 20),   # "-02:52"
            "bpm": (696, 11, 74, 20),         # "128.00"
            "duration": (626, 32, 56, 17),    # "02:52" (duración total)
            "pitch": (696, 32, 74, 17)}       # "-0.0%" (tempo respecto al BPM del track; bajo el BPM)
# Mixer completo (ancho de referencia 250 px): columnas de 24 puntos en x=67 y x=183 (y 48-163); el ROI deja ~7 px de margen.
MIXER_REF_W = 250
MIXER_VU = [(57, 42, 21, 128), (173, 42, 21, 128)]

# ---- medidas de la captura real a pantalla completa (1920×1080, Traktor Pro 4, layout «Mixer») --------------------------
TOK_H = 13.0            # alto del texto del restante y del BPM
DOT_PITCH = 5.0         # paso vertical de los puntos del vúmetro
VU_LAST_TO_TOP = 121    # del último punto al borde superior del ROI del vúmetro
VU_HALF_W, VU_W, VU_H = 10, 21, 128
EDGE_TO_REM_R = 142     # borde derecho del deck -> borde derecho del texto del restante
LEFT_MARGIN = 7.5       # borde izquierdo de la ventana -> borde izquierdo del deck A (los decks son simétricos)
TITLE_OFF = 75.5        # borde izquierdo del deck -> primera letra del título
VU_L_TO_EDGE = 74.5     # borde derecho del deck A = columna izquierda del vúmetro - esto
VU_R_TO_EDGE = 72.5     # borde izquierdo del deck B = columna derecha del vúmetro + esto
MIXER_GAP = 263.0       # borde derecho del deck A -> borde izquierdo del deck B
DUR_DX = 0.5            # fin del texto de la duración respecto al del restante
BPM_DX = 88.0           # fin del texto del BPM respecto al del restante
ROI_PAD_R = 4.0         # margen a la derecha del texto en los ROIs de restante, BPM y duración

TIME_RE = re.compile(r"^[-−–—~]?\d{1,2}:\d{2}(?::\d{2})?$")
BPM_RE = re.compile(r"^\d{2,3}[.,]\d{2}$")
ALPHA_RE = re.compile(r"[A-Za-zÀ-ÿ]{2,}")


@dataclass
class Word:
    text: str
    x: float
    y: float
    w: float
    h: float
    conf: float

    @property
    def r(self):
        return self.x + self.w

    @property
    def cy(self):
        return self.y + self.h / 2


@dataclass
class DeckFound:
    rem: Word
    dur: Word
    bpm: Word
    scale: float


@dataclass
class Layout:
    decks: dict = field(default_factory=dict)       # {"A": {"title": [x,y,w,h], "bpm":…, "remaining":…, "duration":…}}
    vu: dict = field(default_factory=dict)          # {"A": [x,y,w,h]}
    source: dict = field(default_factory=dict)      # {"A": "leído" | "estimado" | "no encontrado"}
    scale: float = 1.0
    notes: list = field(default_factory=list)
    readings: dict = field(default_factory=dict)    # {"A": "title=«…» bpm=«…» …"} lectura de comprobación

    @property
    def complete(self):
        """Todos los decks con ROIs de cabecera y de vúmetro, y todos confirmados por lectura."""
        return bool(self.decks) and all(s == "leído" for s in self.source.values()) and len(self.vu) == len(self.source)

    @property
    def usable(self):
        return bool(self.decks) or bool(self.vu)

    def summary(self):
        parts = []
        for d, s in self.source.items():
            vu = "vúmetro OK" if d in self.vu else "sin vúmetro"
            parts.append(f"Deck {d}: cabecera {s}, {vu}")
        return " · ".join(parts) + (f" (escala {self.scale:.2f})" if abs(self.scale - 1) > 0.02 else "")


# =========================================================================================================== OCR
def read_words(img, max_y=None, up=2):
    """Palabras de la franja superior (donde están las cabeceras de los decks) con su caja en px de la captura."""
    max_y = max_y or max(420, int(img.shape[0] * 0.4))
    top = img[: min(img.shape[0], max_y)]
    g = cv2.cvtColor(top, cv2.COLOR_BGR2GRAY)
    g = 255 - cv2.resize(g, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)      # tema oscuro: texto oscuro sobre claro
    d = pytesseract.image_to_data(g, config="--psm 11", output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            continue
        if t and conf >= 30:
            out.append(Word(t, d["left"][i] / up, d["top"][i] / up, d["width"][i] / up, d["height"][i] / up, conf))
    return out


def _snap(s):
    return 1.0 if abs(s - 1) < 0.1 else s


def find_decks(words):
    """Cabeceras de deck: BPM + restante a su izquierda en la misma línea + duración justo debajo del restante."""
    times = [w for w in words if TIME_RE.match(w.text)]
    out = []
    for b in (w for w in words if BPM_RE.match(w.text)):
        sc = b.h / TOK_H
        rs = [r for r in times if abs(r.cy - b.cy) <= 0.5 * b.h and r.r <= b.x + 2 and b.x - r.r <= 70 * sc]
        if not rs:
            continue
        r = max(rs, key=lambda w: w.r)
        ds = [d for d in times if d is not r and 0.5 * r.h <= d.y - r.y <= 2.6 * r.h and abs(d.r - r.r) <= 8 * sc]
        if ds:
            out.append(DeckFound(r, min(ds, key=lambda d: d.y), b, _snap((b.h + r.h) / 2 / TOK_H)))
    out.sort(key=lambda f: f.bpm.x)
    # un mismo restante no puede pertenecer a dos decks
    uniq, seen = [], set()
    for f in out:
        if id(f.rem) not in seen:
            seen.add(id(f.rem))
            uniq.append(f)
    return uniq


# ===================================================================================================== vúmetros
def find_vu_columns(img, max_frac=0.5):
    """Columnas de puntos regulares (paso ≈5 px, ≥20 puntos): [{"x", "y_last", "n", "pitch"}] de izquierda a derecha.
    Un tramo continuo de puntos con paso constante, el más largo de cada columna: así no importa que arriba haya otros
    elementos (la fila de puntos del medidor de CPU) ni cuántos puntos tenga la parte superior."""
    h = img.shape[0]
    g = cv2.cvtColor(img[: int(h * max_frac)], cv2.COLOR_BGR2GRAY)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    m = (cv2.morphologyEx(g, cv2.MORPH_TOPHAT, k) >= 14).astype(np.uint8)
    n, _, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
    dots = sorted((cen[i][0], cen[i][1]) for i in range(1, n) if 5 <= st[i][4] <= 60 and st[i][2] <= 9 and st[i][3] <= 9)
    cols = []
    for x, y in dots:
        for c in cols:
            if abs(c[-1][0] - x) <= 2:
                c.append((x, y))
                break
        else:
            cols.append([(x, y)])
    out = []
    for c in cols:
        if len(c) < 20:
            continue
        ys = sorted(p[1] for p in c)
        steps = np.diff(ys)
        near = steps[(steps >= 3) & (steps <= 12)]
        if len(near) < 19:
            continue
        pitch = float(np.median(near))
        best, cur = [], [ys[0]]
        for y in ys[1:]:
            if abs((y - cur[-1]) - pitch) <= 1.2:
                cur.append(y)
            else:
                best = cur if len(cur) > len(best) else best
                cur = [y]
        best = cur if len(cur) > len(best) else best
        if len(best) >= 20:
            out.append({"x": float(np.mean([p[0] for p in c])), "y_last": float(best[-1]), "n": len(best),
                        "pitch": float(np.median(np.diff(best)))})
    # los dos medidores de un mixer comparten paso y último punto; quédate con el grupo más numeroso
    groups = {}
    for col in out:
        groups.setdefault((round(col["pitch"] * 2) / 2, round(col["y_last"] / 3)), []).append(col)
    if not groups:
        return []
    best = max(groups.values(), key=lambda g: (len(g), sum(c["n"] for c in g)))
    return sorted(best, key=lambda c: c["x"])


# ===================================================================================================== geometría
def _rect(x, y, w, h, shape):
    H, W = shape[:2]
    x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
    x1, y1 = min(W, int(round(x + w))), min(H, int(round(y + h)))
    return [x0, y0, max(1, x1 - x0), max(1, y1 - y0)]


def _deck_rois(rem_r, y_rem, y_bpm, y_dur, title_left, s, shape):
    """ROIs de cabecera a partir del borde derecho del restante y de las alturas de sus textos."""
    x_rem = rem_r + ROI_PAD_R * s - 72 * s
    rois = {"remaining": _rect(x_rem, y_rem - 3.5 * s, 72 * s, 20 * s, shape),
            "bpm": _rect(rem_r + BPM_DX * s + ROI_PAD_R * s - 74 * s, y_bpm - 3.5 * s, 74 * s, 20 * s, shape),
            "duration": _rect(rem_r + DUR_DX * s + ROI_PAD_R * s - 56 * s, y_dur - 3 * s, 56 * s, 17 * s, shape)}
    x0, x1 = title_left - 3 * s, rem_r - 58 * s          # el título puede llegar hasta ~5 px del restante (que mide ~53 px)
    rois["title"] = _rect(x0, y_rem - 3.5 * s, x1 - x0, 22 * s, shape)
    rois["pitch"] = _rect(rem_r + BPM_DX * s + ROI_PAD_R * s - 74 * s, y_dur - 3 * s, 74 * s, 17 * s, shape)   # misma fila que la duración
    return {k: rois[k] for k in ("title", "bpm", "remaining", "duration", "pitch")}


def _title_left(words, f, expected, s):
    """x donde empieza el título: el de la primera palabra legible de la línea (si coincide con lo esperado)."""
    cand = [w for w in words if ALPHA_RE.search(w.text) and w.conf >= 50 and abs(w.cy - f.rem.cy) <= 8 * s
            and w.r < f.rem.x - 10 * s]
    if expected is not None:
        cand = [w for w in cand if abs(w.x - expected) <= 14 * s]
    return min((w.x for w in cand), default=None)


def detect_layout(img, deck_names, words=None, verify=True):
    """Calcula los ROIs de `deck_names` (en orden, izquierda→derecha) sobre una captura BGR de la ventana de Traktor."""
    lay = Layout()
    names = list(deck_names)
    H, W = img.shape[:2]
    words = read_words(img) if words is None else words
    found = find_decks(words)
    cols = find_vu_columns(img)
    scale = None
    if found:
        scale = float(np.median([f.scale for f in found]))
    elif cols:
        scale = _snap(float(np.median([c["pitch"] for c in cols])) / DOT_PITCH)
    s = lay.scale = scale or 1.0

    # --- vúmetros: la columna de más a la izquierda es el primer deck, etc.
    if len(cols) >= len(names) or (cols and len(names) == 1):
        for name, c in zip(names, cols):
            lay.vu[name] = _rect(c["x"] - VU_HALF_W * s, c["y_last"] - VU_LAST_TO_TOP * s, VU_W * s, VU_H * s, img.shape)
    elif cols:
        lay.notes.append(f"Solo se encontraron {len(cols)} columnas de vúmetro para {len(names)} decks: no se tocan los vúmetros")
    else:
        lay.notes.append("No se encontraron los vúmetros (columnas de puntos): se conservan los ROIs de vúmetro guardados")
    vu_x = [c["x"] for c in cols] if len(cols) >= len(names) else []

    # --- a qué deck pertenece cada cabecera leída
    by_name = {}
    if len(found) >= len(names):
        by_name = dict(zip(names, found))
    else:
        for f in found:                                  # menos cabeceras que decks: se reparte por mitades de la ventana
            idx = min(len(names) - 1, int(((f.bpm.x + f.bpm.w / 2) / W) * len(names)))
            by_name.setdefault(names[idx], f)

    # --- posiciones esperadas del inicio del título de cada deck (si se conocen las columnas del mixer)
    exp_left = {}
    if len(names) == 2 and len(vu_x) >= 2:
        exp_left[names[0]] = (LEFT_MARGIN + TITLE_OFF) * s
        exp_left[names[1]] = vu_x[1] + (VU_R_TO_EDGE + TITLE_OFF) * s

    for name, f in by_name.items():
        el = exp_left.get(name)
        tl = _title_left(words, f, el, f.scale)
        if tl is None:
            tl = el if el is not None else f.rem.r + ROI_PAD_R * f.scale - (DECK_TPL["remaining"][0] + DECK_TPL["remaining"][2] - DECK_TPL["title"][0]) * f.scale
        lay.decks[name] = _deck_rois(f.rem.r, f.rem.y, f.bpm.y, f.dur.y, tl, f.scale, img.shape)
        lay.source[name] = "leído"

    # --- decks sin texto que leer (sin canción cargada): por geometría, solo si lo leído la confirma
    missing = [n for n in names if n not in lay.decks]
    if missing and by_name and len(names) == 2:
        ref_name = next(iter(by_name))
        f = by_name[ref_name]
        edge = f.rem.r + EDGE_TO_REM_R * s                    # borde derecho del deck leído
        o = missing[0]
        if ref_name == names[0]:                              # leído A -> estimar B
            a_right = edge
            ok = not vu_x or abs(a_right - (vu_x[0] - VU_L_TO_EDGE * s)) <= 8 * s
            b_left = vu_x[1] + VU_R_TO_EDGE * s if vu_x else a_right + MIXER_GAP * s
            other_rem_r = b_left + (a_right - LEFT_MARGIN * s) - EDGE_TO_REM_R * s
            other_title = b_left + TITLE_OFF * s
            why = f"el borde del deck A ({a_right:.0f}) no encaja con las columnas del mixer"
        else:                                                 # leído B -> estimar A (necesita las columnas del mixer)
            ok = bool(vu_x) and abs(edge - (W - LEFT_MARGIN * s)) <= 12 * s
            a_right = vu_x[0] - VU_L_TO_EDGE * s if vu_x else 0.0
            other_rem_r = a_right - EDGE_TO_REM_R * s
            other_title = (LEFT_MARGIN + TITLE_OFF) * s
            why = "no encaja con los bordes de la ventana" if vu_x else "no se vieron los vúmetros para comprobarlo"
        if ok:
            lay.decks[o] = _deck_rois(other_rem_r, f.rem.y, f.bpm.y, f.dur.y, other_title, s, img.shape)
            lay.source[o] = "estimado"
            lay.notes.append(f"Deck {o} sin canción cargada: ROIs estimados por geometría (se confirman al detectar de nuevo)")
        else:
            lay.notes.append(f"Deck {o} sin canción cargada y la geometría no encaja ({why}): "
                             f"carga una canción en él y detecta de nuevo")
    for n in names:
        lay.source.setdefault(n, "no encontrado")
    if not found:
        lay.notes.append("No se leyó ninguna cabecera de deck (restante + duración + BPM): carga una canción en algún deck")

    if verify:
        lay.readings = verify_layout(img, lay)
    return lay


def verify_layout(img, lay):
    """Lee con el OCR de la app los ROIs calculados sobre la misma captura (comprobación visible para el usuario)."""
    from .vision import ocr_text
    kinds = {"title": "text", "bpm": "bpm", "remaining": "time", "duration": "time", "pitch": "pitch"}
    out = {}
    for d, rois in lay.decks.items():
        parts = []
        for k, kind in kinds.items():
            x, y, w, h = rois[k]
            try:
                parts.append(f"{k}=«{ocr_text(img[y:y + h, x:x + w], kind)}»")
            except Exception as e:                       # sin Tesseract utilizable, etc.
                parts.append(f"{k}=error({e})")
        out[d] = "  ".join(parts)
    return out


def apply_layout(cfg, lay, backend, size):
    """Escribe los ROIs detectados en la config (lo no detectado se conserva) y anota con qué captura se calibró."""
    for d, rois in lay.decks.items():
        cfg["decks"].setdefault(d, {}).update(rois)
    for d, roi in lay.vu.items():
        cfg["decks"].setdefault(d, {})["vu"] = roi
    cfg["capture"]["ref"] = {"backend": backend, "size": list(size)}
