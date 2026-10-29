# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""tracks_db.json: relación track de Traktor <-> video, y doble check título + duración."""
import json
import os
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from core import devlog as dl
from core import paths


def normalize(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def fmt_time(sec):
    """Segundos -> "mm:ss" (o "h:mm:ss"), igual que lo muestra Traktor."""
    if sec is None:
        return ""
    h, rem = divmod(int(round(sec)), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_bpm(v):
    """BPM como lo muestra Traktor: 3 enteros y 2 decimales («128.00»). Vacío si no es un número."""
    try:
        return f"{float(str(v).replace(',', '.')):.2f}"
    except (TypeError, ValueError):
        return ""


def parse_duration(v):
    """Acepta "02:52", "1:02:15" o segundos (formato antiguo) -> segundos, o None si no es válido."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(",", ".")
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", t)
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    try:
        return float(t)
    except ValueError:
        return None


def norm_path(p):
    return os.path.normcase(os.path.abspath(p))


class TracksDB:
    """`roots`: carpetas (o una función que las da) donde buscar un video que ya no está en su ruta guardada (la carpeta se movió)."""

    def __init__(self, path, roots=None):
        self.path = Path(path)
        self.roots = roots
        self.tracks = []
        self.healed = []              # (ruta guardada, ruta nueva) de los vínculos reparados al leer: el programa los avisa en el registro
        self._migrate = False         # el archivo trae rutas en el formato de antes (absolutas dentro de la carpeta de datos) o reparadas
        self.load()

    # ---- rutas: en memoria SIEMPRE absolutas; en el archivo, relativas a la carpeta de datos cuando están dentro de ella (update 13)
    def _roots(self):
        r = self.roots() if callable(self.roots) else self.roots
        return [x for x in (r or ()) if isinstance(x, str)]

    def _resolve_paths(self):
        self.healed = []
        roots = self._roots()
        for e in self.tracks:
            vp = e.get("video_path") if isinstance(e, dict) else None
            if not isinstance(vp, str) or not vp.strip():
                continue
            if os.path.isabs(vp) and paths.inside(vp):
                self._migrate = True                                    # absoluta dentro de la carpeta de datos: al guardar queda relativa
            full = paths.resolve(vp)
            if not os.path.exists(full):
                moved = paths.find_moved(vp, roots)
                if moved:
                    self.healed.append((full, moved))
                    full, self._migrate = moved, True
            e["video_path"] = full
        for old, new in self.healed:
            dl.info("db", f"vínculo reparado: {os.path.basename(new)} (estaba en {old})")

    def _backup_old_format(self):
        """Antes de reescribir un archivo en el formato de antes: una copia «.u12.bak» (una sola vez, no se pisa)."""
        if not self._migrate:
            return
        self._migrate = False
        bak = self.path.with_name(self.path.name + ".u12.bak")
        try:
            if self.path.exists() and not bak.exists():
                bak.write_bytes(self.path.read_bytes())
        except OSError as e:
            dl.warn("db", f"no se pudo copiar {self.path.name} a {bak.name}: {e}")

    def _disk_entries(self):
        out = []
        for e in self.tracks:
            if isinstance(e, dict) and isinstance(e.get("video_path"), str):
                e = {**e, "video_path": paths.store(e["video_path"])}
            out.append(e)
        return out

    def load(self):
        """Lee tracks_db.json; si no existe (instalación limpia) lo crea vacío. Uno ilegible se guarda como .bak."""
        if not self.path.exists():
            self.tracks = []
            self._try_save()
            return
        try:
            self.tracks = json.loads(self.path.read_text("utf-8")).get("tracks", [])
        except Exception as e:
            print("tracks_db.json ilegible, se empieza vacío (copia en .bak):", e)
            dl.exception("db", "tracks_db.json ilegible, se empieza vacío (copia en .bak)")
            self.tracks = []
            try:
                self.path.replace(self.path.with_name(self.path.name + ".bak"))
            except OSError:
                pass
            self._try_save()
            return
        try:                                                     # (un fallo aquí NUNCA debe dar el archivo por ilegible)
            self._resolve_paths()
        except Exception:                                        # noqa: BLE001
            dl.exception("db", "no se pudieron revisar las rutas de los videos de tracks_db.json")
        if self._migrate:
            self._try_save()                                     # pasa al formato nuevo ya (con la copia de seguridad previa)

    def _try_save(self):
        try:
            self.save()
        except OSError as e:
            print("No se pudo crear tracks_db.json:", e)
            dl.error("db", f"No se pudo crear tracks_db.json: {e}")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_old_format()
        self.path.write_text(json.dumps({"tracks": self._disk_entries()}, indent=2, ensure_ascii=False), "utf-8")

    def replace_all(self, tracks):
        self.tracks[:] = tracks      # en sitio: el motor ve el cambio al instante
        self.save()

    def entry_for_video(self, video):
        v = norm_path(video)
        for e in self.tracks:
            if e.get("traktor_track_name") and norm_path(e["video_path"]) == v:
                return e
        return None

    def orphans(self, videos):
        """Videos sin coincidencia en el JSON = 'Random Visuals'."""
        return [v for v in videos if self.entry_for_video(v) is None]

    def match(self, title, duration, ocr_cfg, alts=()):
        """Doble check. Devuelve (entrada | None, mensaje). `alts`: otras lecturas de la duración (con otro recorte del
        ROI): basta con que una coincida, así un dígito mal leído por un ROI justo no descarta el visual."""
        if not title:
            return None, "sin título leído"
        nt, best, best_r = normalize(title), None, 0.0
        for e in self.tracks:
            if not e.get("traktor_track_name"):
                continue
            nm = normalize(e["traktor_track_name"])
            r = SequenceMatcher(None, nt, nm).ratio()
            if len(nt) < len(nm):                   # el deck recorta los títulos largos: se compara con el principio
                r = max(r, SequenceMatcher(None, nt, nm[:len(nt) + 2]).ratio())
            if r > best_r:
                best, best_r = e, r
        if best is None or best_r < ocr_cfg["title_threshold"]:
            return None, f"título sin coincidencia ({best_r:.0%})"
        if ocr_cfg["check_duration"]:
            reads = [x for x in dict.fromkeys([duration, *alts]) if x is not None]
            if not reads:
                return None, "título OK, duración ilegible"
            target = parse_duration(best.get("duration"))
            if target is None:
                return None, "título OK, pero la duración guardada en ViDeck Pulse no es válida"
            tol = ocr_cfg["duration_tolerance"]
            if not any(abs(x - target) <= tol for x in reads):
                seen = " / ".join(fmt_time(x) for x in reads)
                return None, f"título OK, duración no coincide (Traktor Pro 4 {seen} vs ViDeck Pulse {fmt_time(target)})"
            if duration is None or abs(duration - target) > tol:
                return best, f"OK (título {best_r:.0%}; duración confirmada con otra lectura)"
        return best, f"OK (título {best_r:.0%})"
