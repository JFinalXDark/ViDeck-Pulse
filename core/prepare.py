# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preparar videos: deja la biblioteca de visuales lista para que la sincronía corrija con saltos (seek) sin tirones.

Un video pesado de decodificar o con keyframes muy espaciados hace que cada salto de la sincronía tarde y se vea un tirón. Este módulo
(sin Qt, usa ffmpeg/ffprobe) revisa cada video y, solo si hace falta, lo recodifica a H.264 con:
  · el MISMO bitrate de video que tenía el original (la calidad se mantiene; nunca se baja ni se sube a propósito),
  · fps constantes (los de origen; si eran variables, el estándar más cercano; máximo 60),
  · un keyframe por segundo,
  · la misma resolución (opcionalmente con un alto máximo; nunca se agranda),
  · el audio copiado tal cual (solo se recodifica si su formato no cabe en un .mp4).
Un video que ya cumple se COPIA sin tocarlo. Los originales nunca se modifican: todo sale a otra carpeta.

Hay una pérdida mínima por recodificar (aunque sea al mismo bitrate no es idéntico bit a bit): por eso solo se recodifica lo que lo necesita.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction

from . import devlog as dl
from . import paths
from .db import norm_path
from .lowprio import with_low_priority
from .media import VIDEO_EXTS

KEY_EVERY = 1.0              # s: un keyframe por segundo en lo que se recodifica
KEY_MAX = 2.0                # s: con keyframes más espaciados que esto el video es «Mejorable» (los saltos de la sincronía tardan algo más)
KEY_HARD = 5.0               # s: con keyframes más espaciados que esto es «No compatible» (cada salto tarda y se ve el tirón)
FPS_MAX = Fraction(60)
STD_FPS = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30), Fraction(50),
           Fraction(60000, 1001), Fraction(60)]
MIN_BITRATE = 300_000        # bps: por debajo de esto el dato no es fiable (se usa calidad constante)
MAXRATE_X, BUFSIZE_X = 2.0, 4.0          # tope instantáneo del bitrate (×) y tamaño del búfer (×): evitan picos, sin apretar la calidad
SCAN_S = 30                  # s del principio del video donde se miden los keyframes y la regularidad de los fotogramas
PROBE_TIMEOUT = 60
COPY_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac"}          # lo que cabe en un .mp4 sin recodificar
NO_WINDOW = 0x08000000                                       # Windows: sin ventana de consola para ffmpeg

# Clasificación de cada video (es también el texto que se ve en la tabla de «Preparar videos»)
COMPATIBLE = "Compatible"                  # funciona bien tal cual: no hace falta prepararlo
IMPROVABLE = "Mejorable"                   # funciona, pero preparándolo la sincronía sale más suave o pesa menos en el CPU
INCOMPATIBLE = "No compatible"             # puede tirones, mala sincronía o un uso alto de CPU/GPU: conviene prepararlo
UNREADABLE = "No se puede convertir"       # ffprobe/ffmpeg no pueden con él (archivo dañado, sin imagen, formato raro)
CATEGORIES = (COMPATIBLE, IMPROVABLE, INCOMPATIBLE, UNREADABLE)


# ---------------------------------------------------------------------------------------------------- herramientas
def _exe(name):
    return name + (".exe" if os.name == "nt" else "")


def _candidates(configured, only_configured=False):
    """Rutas donde puede estar ffmpeg, en orden: la configurada (archivo o carpeta), el PATH, junto al programa y sitios típicos."""
    out = []
    if not only_configured:
        from . import components                                  # con «Legacy Mode» activo, el ffmpeg del paquete Legacy va primero
        legacy_dir = components.ffmpeg_dir()
        if legacy_dir:
            out.append(os.path.join(legacy_dir, _exe("ffmpeg")))
    if configured:
        out.append(configured)
        out.append(os.path.join(configured, _exe("ffmpeg")))
        out.append(os.path.join(configured, "bin", _exe("ffmpeg")))
    if only_configured:
        return out
    here = paths.components_dir()
    for sub in ("ffmpeg", os.path.join("ffmpeg", "bin")):          # el ffmpeg propio (carpeta «ffmpeg» del programa) antes que el del PATH
        out.append(os.path.join(here, sub, _exe("ffmpeg")))
    found = shutil.which("ffmpeg")
    if found:
        out.append(found)
    out.append(os.path.join(here, _exe("ffmpeg")))
    if os.name == "nt":
        for base in (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
                     os.path.expandvars(r"%ProgramData%\chocolatey\bin")):
            out.append(os.path.join(base, _exe("ffmpeg")))
    return out


def find_tools(configured="", only_configured=False):
    """(ruta de ffmpeg, ruta de ffprobe) o (None, None). ffprobe tiene que estar junto a ffmpeg (o en el PATH). Con
    `only_configured` solo se mira la ruta dada (para comprobar un archivo que elige el usuario, sin caer en el del PATH)."""
    for c in _candidates(configured, only_configured):
        if not c or not os.path.isfile(c) or "ffmpeg" not in os.path.basename(c).lower():
            continue
        probe = os.path.join(os.path.dirname(c), _exe("ffprobe"))
        if not os.path.isfile(probe):
            probe = shutil.which("ffprobe")
        if not probe:
            continue
        try:
            r = subprocess.run([c, "-version"], capture_output=True, timeout=15, **with_low_priority({"creationflags": _flags()}))
            if r.returncode == 0:
                return c, probe
        except (OSError, subprocess.SubprocessError):
            continue
    return None, None


def _flags():
    return NO_WINDOW if os.name == "nt" else 0


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, **with_low_priority({"creationflags": _flags()}))


# ---------------------------------------------------------------------------------------------------- análisis
def _frac(text):
    try:
        f = Fraction(str(text))
        return f if f > 0 else None
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def probe(path, ffprobe):
    """Datos del video o None si ffprobe no pudo leerlo. Claves: path, size, duration, vcodec, pix_fmt, width, height (de cómo se
    ve, con la rotación aplicada), fps (media), cfr (¿fotogramas a intervalos regulares?), key_gap (s, el mayor entre keyframes),
    vbitrate (bps), acodec, abitrate, interlaced."""
    try:
        r = _run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], PROBE_TIMEOUT)
        d = json.loads(r.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    if v is None:
        return None
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = d.get("format") or {}
    dur = _num(v.get("duration")) or _num(fmt.get("duration"))
    size = _num(fmt.get("size")) or (os.path.getsize(path) if os.path.exists(path) else None)
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = _rotation(v)
    if rot in (90, 270):
        w, h = h, w
    abr = _num(a.get("bit_rate")) if a else None
    vbr = _num(v.get("bit_rate"))
    if not vbr and size and dur:                                 # mkv / webm no traen el bitrate de cada pista: sale del archivo
        vbr = size * 8 / dur - (abr or 0)
    fps = _frac(v.get("avg_frame_rate")) or _frac(v.get("r_frame_rate"))
    info = {"path": path, "size": size, "duration": dur, "vcodec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
            "width": w, "height": h, "fps": fps, "rotation": rot, "vbitrate": int(vbr) if vbr and vbr > 0 else None,
            "acodec": a.get("codec_name") if a else None, "abitrate": int(abr) if abr else None,
            "interlaced": (v.get("field_order") or "progressive") not in ("progressive", "unknown"),
            "cfr": None, "key_gap": None}
    info["cfr"], info["key_gap"] = _packets(path, ffprobe)
    return info


def _rotation(v):
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"]))) % 360
    try:
        return int((v.get("tags") or {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0


def _packets(path, ffprobe):
    """(¿intervalos regulares?, mayor distancia entre keyframes en s) leyendo los paquetes de los primeros SCAN_S segundos (sin
    decodificar, es rápido). None si no se pudo medir."""
    try:
        r = _run([ffprobe, "-v", "error", "-select_streams", "v:0", "-read_intervals", f"%+{SCAN_S}", "-show_entries",
                  "packet=pts_time,dts_time,flags", "-of", "json", path], PROBE_TIMEOUT)
        pk = json.loads(r.stdout.decode("utf-8", "replace")).get("packets") or []
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, None
    return packets_stats([(_num(p.get("pts_time")) if _num(p.get("pts_time")) is not None else _num(p.get("dts_time")),
                           "K" in (p.get("flags") or "")) for p in pk])


def packets_stats(pk):
    """Lógica pura de `_packets`: pk = [(tiempo_s, ¿keyframe?)]. Devuelve (cfr, key_gap) o (None, None) si hay muy pocos datos."""
    pk = sorted((t, k) for t, k in pk if t is not None)
    if len(pk) < 4:
        return None, None
    times = [t for t, _ in pk]
    deltas = sorted(b - a for a, b in zip(times, times[1:]))
    med = deltas[len(deltas) // 2]
    cfr = med > 0 and sum(1 for x in deltas if abs(x - med) <= 0.08 * med) >= 0.97 * len(deltas)
    keys = [t for t, k in pk if k]
    if not keys:
        return cfr, None
    gaps = [b - a for a, b in zip(keys, keys[1:])]
    end = times[-1]
    if keys[-1] < end:                                           # el tramo final sin keyframe (cuenta si es lo último que se leyó)
        gaps.append(end - keys[-1] + med)
    return cfr, (max(gaps) if gaps else end - keys[0] + med)


# ---------------------------------------------------------------------------------------------------- decisión

def size_text(n):
    """Un tamaño para mostrar: en GB (1 decimal) desde 1 GB; por debajo, en MB (sin decimales)."""
    n = float(n or 0)
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"

@dataclass
class Plan:
    action: str                                       # "copy" (ya cumple) | "encode"
    reasons: list = field(default_factory=list)       # por qué hay que recodificar (textos en español, para la tabla)
    fps: Fraction = None                              # fps de salida (siempre fijos)
    height: int = None                                # alto de salida si se reduce (None = el original)
    bitrate: int = None                               # bps de video de salida (None = calidad constante, no se conocía el original)
    audio_copy: bool = True
    category: str = COMPATIBLE                        # Compatible · Mejorable · No compatible
    must: list = field(default_factory=list)          # de `reasons`, lo que hace al video «No compatible»


def snap_fps(f):
    """fps constantes para un video de fps variables: el estándar más cercano a su media (máximo 60)."""
    if f is None:
        return Fraction(30)
    if f > FPS_MAX + Fraction(1, 2):
        return FPS_MAX
    if f < STD_FPS[0] * Fraction(95, 100):                       # un video de pocos fps (p. ej. 15) no se sube a 24: se conserva su media
        return f.limit_denominator(1001)
    return min(STD_FPS, key=lambda s: abs(float(s / f) - 1))


def assess(info, max_height=0):
    """(graves, leves) de un video ya analizado: textos en español de lo que le impide ir perfecto.
    Graves («No compatible»): formato de color que no es 8 bits 4:2:0 (conversión por software, colores o pantalla negra según la
    tarjeta), entrelazado, más de 60 fps y keyframes a más de KEY_HARD s (cada salto de la sincronía tarda y se ve el tirón).
    Leves («Mejorable»): otro códec que H.264 (más pesado de decodificar), fps variables, keyframes a más de KEY_MAX s, rotado
    o más alto que el máximo elegido. Con la lista vacía el video es «Compatible»: tal cual funciona bien."""
    must, nice = [], []
    codec = (info.get("vcodec") or "").lower()
    if codec != "h264":
        nice.append(f"códec {codec or '?'}")
    if info.get("pix_fmt") not in ("yuv420p", "yuvj420p"):
        must.append(f"formato de color {info.get('pix_fmt') or '?'}")
    fps = info.get("fps")
    if info.get("cfr") is False:
        nice.append("fps variables")
    if fps is not None and fps > FPS_MAX + Fraction(1, 2):
        must.append("más de 60 fps")
    gap = info.get("key_gap")
    if gap is not None and gap > KEY_HARD:
        must.append(f"keyframes cada {gap:.1f} s")
    elif gap is not None and gap > KEY_MAX:
        nice.append(f"keyframes cada {gap:.1f} s")
    if info.get("interlaced"):
        must.append("entrelazado")
    if info.get("rotation"):
        nice.append("rotado")
    h = info.get("height") or 0
    if max_height and h > max_height:
        nice.append(f"alto {h} p > {max_height} p")
    return must, nice


def decide(info, max_height=0):
    """Plan para un video ya analizado. `max_height`: 0 = conservar la resolución."""
    must, nice = assess(info, max_height)
    why = must + nice
    if not why:
        return Plan("copy")
    fps = info.get("fps")
    h = info.get("height") or 0
    small = bool(max_height and h > max_height)
    out_fps = fps if info.get("cfr") and fps and fps <= FPS_MAX + Fraction(1, 2) else snap_fps(fps)
    br = info.get("vbitrate")
    return Plan("encode", why, fps=out_fps, height=max_height if small else None,
                bitrate=br if br and br >= MIN_BITRATE else None, audio_copy=(info.get("acodec") or "aac") in COPY_AUDIO,
                category=INCOMPATIBLE if must else IMPROVABLE, must=must)


def fps_arg(f):
    return f"{f.numerator}/{f.denominator}" if f.denominator != 1 else str(f.numerator)


def build_command(ffmpeg, src, dst, plan, info):
    """Línea de comandos de ffmpeg para `plan` (action «encode»). Escribe a `dst` (un .part se renombra al terminar)."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", src,
           "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_chapters", "-1"]
    vf = []
    if plan.height:
        vf.append(f"scale=-2:{plan.height}:flags=lanczos")
    elif (info.get("width") or 0) % 2 or (info.get("height") or 0) % 2:      # H.264 yuv420p necesita medidas pares (se recorta 1 px)
        vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    vf.append(f"fps={fps_arg(plan.fps)}")
    cmd += ["-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if plan.bitrate:
        br = int(plan.bitrate)
        cmd += ["-b:v", str(br), "-maxrate", str(int(br * MAXRATE_X)), "-bufsize", str(int(br * BUFSIZE_X))]
    else:
        cmd += ["-crf", "17"]
    g = max(1, int(round(float(plan.fps) * 2)))
    cmd += ["-g", str(g), "-force_key_frames", f"expr:gte(t,n_forced*{KEY_EVERY:g})"]
    cmd += ["-c:a", "copy"] if plan.audio_copy else ["-c:a", "aac", "-b:a", "256k"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", dst]
    return cmd


# ---------------------------------------------------------------------------------------------------- ejecución
@dataclass
class Result:
    status: str                    # ok · copiado · cancelado · error · revisar
    message: str = ""
    after: dict = None             # análisis del video preparado


def fmt_rate(bps):
    return "—" if not bps else f"{bps / 1e6:.1f} Mb/s"


def prepare_one(src, dst, tools, plan, info, progress=None, cancel=None):
    """Prepara un video. `progress(0..1)`; `cancel` = threading.Event. Escribe a un .part y lo renombra solo si todo salió bien
    (un video a medias nunca queda en la carpeta de destino)."""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    part = dst + ".part"
    try:
        if plan.action == "copy":
            shutil.copy2(src, part)
            os.replace(part, dst)
            return Result("copiado", "Ya cumplía: copiado sin cambios")
        cmd = build_command(tools[0], src, part, plan, info)
        tail = []
        dur = info.get("duration") or 0
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             **with_low_priority({"creationflags": _flags()}))
        for raw in iter(p.stdout.readline, b""):
            if cancel is not None and cancel.is_set():
                p.terminate()
                break
            line = raw.decode("utf-8", "replace").strip()
            m = re.match(r"out_time_(?:us|ms)=(\d+)", line)
            if m:
                if progress and dur:
                    progress(min(1.0, int(m.group(1)) / 1e6 / dur))
            elif line and not line.startswith(("progress=", "frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time",
                                               "dup_frames=", "drop_frames=", "speed=")):
                tail = (tail + [line])[-6:]
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        if cancel is not None and cancel.is_set():
            return Result("cancelado", "Cancelado")
        if p.returncode != 0:
            detail = " | ".join(tail) or f"código {p.returncode}"
            return Result("error", f"ffmpeg falló: {detail}")
        after = probe(part, tools[1])
        if after is None:
            return Result("error", "El video preparado no se puede leer")
        problems = check_result(info, after, plan)
        os.replace(part, dst)
        after["path"] = dst
        if progress:
            progress(1.0)
        return Result("revisar" if problems else "ok", "; ".join(problems), after)
    except OSError as e:
        return Result("error", f"{type(e).__name__}: {e}")
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass


def check_result(before, after, plan):
    """Lo que no quedó como se esperaba (lista vacía = todo bien): duración, fps constantes, keyframes y bitrate."""
    out = []
    d0, d1 = before.get("duration"), after.get("duration")
    if d0 and d1 and abs(d0 - d1) > max(0.25, 2 / float(plan.fps or 30)):
        out.append(f"la duración cambió ({d0:.2f} s → {d1:.2f} s)")
    if after.get("cfr") is False:
        out.append("los fps no quedaron constantes")
    gap = after.get("key_gap")
    if gap is not None and gap > KEY_MAX:
        out.append(f"keyframes cada {gap:.1f} s")
    b0, b1 = before.get("vbitrate"), after.get("vbitrate")
    if plan.bitrate and b0 and b1 and b1 < 0.75 * b0:
        out.append(f"el bitrate bajó ({fmt_rate(b0)} → {fmt_rate(b1)})")
    return out


# ---------------------------------------------------------------------------------------------------- biblioteca entera
PENDING = (COMPATIBLE, IMPROVABLE, INCOMPATIBLE)          # estados de un video analizado que todavía no se preparó
ELIGIBLE = PENDING + ("Cancelado",)                        # lo que se puede preparar (también lo que se canceló a medias)
DONE = ("Listo", "Copiado", "Ya existía", "Revisar")       # estados con el video ya en la carpeta de destino
WORKING = ("Preparando", "Verificando")                    # estados de un video en el que se está trabajando ahora mismo
INPLACE = ("inplace", "replace", "keep")                   # modos de traspaso directo (update 13): el convertido queda en la carpeta de videos


@dataclass
class Row:
    src: str
    dst: str = ""
    state: str = "Pendiente"
    pct: float = 0.0
    info: dict = None
    plan: Plan = None
    note: str = ""
    result: Result = None
    sig: tuple = None                  # (tamaño, mtime_ns) del original al analizarlo (traspaso directo: se comprueba que no cambió)
    job: object = None                 # `transfer.Job` del traspaso directo (hilo de trabajo → hilo de la interfaz)


def _sig(path):
    """(tamaño, mtime_ns) de un archivo o None."""
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def list_videos(folder):
    """Videos de la carpeta (y subcarpetas), sin crearla si no existe."""
    if not os.path.isdir(folder):
        return []
    out = []
    for root, _, files in os.walk(folder):
        out += [os.path.join(root, f) for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXTS]
    return sorted(out)


def assign_outputs(rows, src_root, dst_root):
    """Ruta de salida de cada video: la misma ruta relativa dentro de la carpeta de destino; lo recodificado sale en .mp4 y un
    nombre repetido (a.mov y a.mkv → a.mp4) recibe « (2)»."""
    used = set()
    for r in rows:
        rel = os.path.relpath(r.src, src_root)
        if r.plan is not None and r.plan.action == "encode":
            rel = os.path.splitext(rel)[0] + ".mp4"
        base, ext = os.path.splitext(rel)
        cand, n = rel, 2
        while os.path.normcase(cand) in used:
            cand, n = f"{base} ({n}){ext}", n + 1
        used.add(os.path.normcase(cand))
        r.dst = os.path.join(dst_root, cand)


def relink(tracks, mapping):
    """Cambia el `video_path` de las entradas de tracks_db según {ruta normalizada del original: ruta nueva}. Devuelve cuántas."""
    n = 0
    for e in tracks:
        new = mapping.get(norm_path(e["video_path"])) if e.get("video_path") else None
        if new:
            e["video_path"] = new
            n += 1
    return n


class Batch:
    """Analiza y prepara toda la carpeta en un hilo aparte (la interfaz solo lee `rows`, `busy` y `done`)."""

    def __init__(self, tools, src_root, dst_root, max_height=0, mode="folder"):
        """`mode`: «folder» (lo de siempre: los preparados salen a la carpeta de destino) o, con el traspaso directo, «inplace» (aún sin decidir
        qué hacer con los originales), «replace» (el convertido reemplaza al original) o «keep» (el convertido se guarda como «[Converted]»).
        En los tres últimos la carpeta de destino es la de videos."""
        self.mode = mode
        if mode in INPLACE:
            dst_root = src_root
        self.tools, self.src_root, self.dst_root, self.max_height = tools, src_root, dst_root, max_height
        self.rows = [Row(p) for p in list_videos(src_root)]
        self.cancel_evt = threading.Event()
        self.thread = None
        self.analyzed = False
        self.message = ""
        self._only = None
        self.run_kind, self.run_rows, self.run_t0 = "", [], 0.0       # qué se está haciendo (para el progreso general de la ventana)

    @property
    def busy(self):
        return bool(self.thread and self.thread.is_alive())

    def _start(self, fn):
        if self.busy:
            return False
        self.cancel_evt.clear()
        self.run_kind, self.run_rows = "", []                          # el avance de la tarea anterior no cuenta
        self.thread = threading.Thread(target=fn, daemon=True, name="preparar-videos")
        self.thread.start()
        return True

    def analyze(self):
        return self._start(self._analyze)

    def prepare(self, only=None, mode=None):
        """Prepara toda la carpeta, o solo los videos de `only` (lista de Row) si se da. Con el traspaso directo, `mode` («replace» o «keep»)
        dice qué se hace con los originales."""
        if mode is not None and self.mode in INPLACE:
            self.mode = mode
            self._assign_inplace()
        self._only = None if only is None else {id(r) for r in only}
        for r in self.rows:
            if r.state == UNREADABLE and r.plan is not None and self._in_scope(r):
                r.state, r.note = r.plan.category, "; ".join(r.plan.reasons)       # un intento fallido se puede reintentar
        return self._start(self._prepare)

    def cancel(self):
        self.cancel_evt.set()

    def _analyze(self):
        self.run_kind, self.run_rows, self.run_t0 = "analyze", list(self.rows), time.time()
        for r in self.rows:
            if self.cancel_evt.is_set():
                break
            r.state, r.pct = "Analizando", 0.0
            r.sig = _sig(r.src)
            info = probe(r.src, self.tools[1])
            if info is None:
                r.info, r.plan, r.state, r.note = None, None, UNREADABLE, "ffprobe no puede leer el video (dañado, sin imagen o formato no admitido)"
                continue
            r.info, r.plan = info, decide(info, self.max_height)
            r.state = r.plan.category
            r.note = "; ".join(r.plan.reasons)
        if self.mode in INPLACE:
            self._mark_converted_pairs()
            self._assign_inplace()
        else:
            assign_outputs(self.rows, self.src_root, self.dst_root)
        self.analyzed = not self.cancel_evt.is_set()

    def _mark_converted_pairs(self):
        """Un original que ya tiene su «[Converted]» al lado no hay que prepararlo otra vez."""
        from . import transfer
        hidden = transfer.hidden_originals([r.src for r in self.rows])
        for r in self.rows:
            if r.src in hidden and r.state in ELIGIBLE:
                r.state, r.note, r.pct = "Ya existía", "ya tiene su versión [Converted]", 1.0

    def _assign_inplace(self):
        """Dónde quedará cada video en el traspaso directo: sin cambios si ya cumple; «nombre.mp4» (reemplazo) o «nombre [Converted].mp4»."""
        from . import transfer
        for r in self.rows:
            if r.plan is None or r.plan.action != "encode":
                r.dst = r.src
            elif self.mode == "replace":
                r.dst = transfer.replace_target(r.src)
            elif self.mode == "keep":
                r.dst = transfer.converted_target(r.src)
            else:
                r.dst = ""

    def _in_scope(self, r):
        return self._only is None or id(r) in self._only

    def free_space_problem(self):
        """Mensaje si en el disco de destino no cabe lo que se va a preparar (con margen), o ''."""
        need = sum((r.info or {}).get("size") or 0 for r in self.rows if r.plan is not None and self._in_scope(r) and r.state in ELIGIBLE)
        try:
            os.makedirs(self.dst_root, exist_ok=True)
            free = shutil.disk_usage(self.dst_root).free
        except OSError:
            return ""
        if need and free < need * 1.2:
            return f"No hay espacio en el disco de destino: hacen falta ~{size_text(need * 1.2)} y hay {size_text(free)} libres"
        return ""

    def _prepare(self):
        if not self.analyzed:
            self._analyze()
        t0 = time.time()
        problem = self.free_space_problem()
        if problem:
            self.message = problem
            return
        self.run_kind, self.run_t0 = "prepare", t0
        self.run_rows = [r for r in self.rows if self._in_scope(r) and r.state in ELIGIBLE]
        for r in self.rows:
            if not self._in_scope(r) or r.state not in ELIGIBLE:
                continue                                      # fuera de lo pedido, ya preparado o que no se puede convertir
            if self.cancel_evt.is_set():
                r.state = "Cancelado"
                continue
            if self.mode in INPLACE:
                self._prepare_inplace(r)
                continue
            if os.path.exists(r.dst):
                r.state, r.note, r.pct = "Ya existía", "ya estaba en la carpeta de destino", 1.0
                continue
            r.state, r.pct = "Preparando", 0.0

            def prog(x, row=r):
                row.pct = x
            res = prepare_one(r.src, r.dst, self.tools, r.plan, r.info, prog, self.cancel_evt)
            r.result = res
            r.state = {"ok": "Listo", "copiado": "Copiado", "cancelado": "Cancelado", "error": UNREADABLE, "revisar": "Revisar"}[res.status]
            if res.status in ("ok", "revisar") and res.after:
                r.note = f"{fmt_rate(r.info.get('vbitrate'))} → {fmt_rate(res.after.get('vbitrate'))}" + (f"; {res.message}" if res.message else "")
            elif res.status != "cancelado":                   # (al cancelar se conserva el motivo de la clasificación: «keyframes cada 5.1 s»…)
                r.note = res.message
            r.pct = 1.0 if res.status in ("ok", "copiado", "revisar") else r.pct
            if res.status in ("error", "revisar"):
                dl.warn("preparar", f"{os.path.basename(r.src)}: {res.message}")
        took = f"{time.time() - t0:.0f}"
        self.message = f"Detenido a los {took} s" if self.cancel_evt.is_set() else f"Terminado en {took} s"

    def _prepare_inplace(self, r):
        """Un video en el traspaso directo (hilo de trabajo): convierte, verifica y coloca; el cierre (vínculos y original) lo hace `finish_pending`
        en el hilo de la interfaz."""
        from . import transfer
        if r.plan is None or r.plan.action != "encode":
            r.state, r.note, r.pct = "Ya existía", "ya cumple: se queda como está", 1.0
            return
        r.state, r.pct, r.job = "Preparando", 0.0, None

        def prog(x, row=r):
            row.pct = x

        def stage(text, row=r):
            row.state = text
        res, job = transfer.work_one(r.src, self.mode, self.tools, r.plan, r.info, r.sig, prog, self.cancel_evt, stage)
        r.result = res
        if job is None:
            r.state = {"cancelado": "Cancelado"}.get(res.status, UNREADABLE)
            if res.status != "cancelado":
                r.note = res.message
                dl.warn("preparar", f"{os.path.basename(r.src)}: {res.message}")
            return
        r.pct = 1.0
        r.job = job                                           # (queda en «Verificando» hasta que la interfaz lo cierre)

    def finish_pending(self, ctx):
        """Cierra (en el hilo de la interfaz) los videos que el hilo de trabajo ya colocó: vínculos, tracks_db y original. Devuelve cuántos."""
        from . import transfer
        n = 0
        for r in self.rows:
            j = r.job
            if j is None or j.stage != "placed" or r.state not in WORKING:
                continue
            transfer.finish(j, ctx)
            n += 1
            if j.stage == "done":
                res = r.result
                rates = f"{fmt_rate(r.info.get('vbitrate'))} → {fmt_rate((j.after or {}).get('vbitrate'))}"
                r.note = rates + (f"; {res.message}" if res is not None and res.status == "revisar" and res.message else "") + f"; {j.note}"
                r.state = "Revisar" if res is not None and res.status == "revisar" else "Listo"
                r.dst = j.final
            else:
                r.state, r.note = UNREADABLE, j.note
                dl.warn("preparar", f"{os.path.basename(r.src)}: {j.note}")
        return n

    def progress(self):
        """El avance general de lo que está haciendo: (qué, hechos, total, fracción 0..1, segundos que faltan o None, video actual).
        «qué» es "analyze", "prepare" o "" (nada en marcha). Los segundos que faltan salen del ritmo llevado hasta ahora."""
        rows, kind = self.run_rows, self.run_kind
        if not kind or not rows:
            return "", 0, 0, 0.0, None, ""
        if kind == "analyze":
            done = sum(1 for r in rows if r.state not in ("Pendiente", "Analizando"))
            cur = next((r for r in rows if r.state == "Analizando"), None)
            frac = done / len(rows)
        else:
            working = [r for r in rows if r.state in WORKING]
            done = sum(1 for r in rows if r.state not in ELIGIBLE and r.state not in WORKING)
            cur = working[0] if working else None
            frac = min(1.0, (done + sum(max(0.0, min(1.0, r.pct)) for r in working)) / len(rows))
        eta = None
        el = time.time() - self.run_t0
        if self.busy and frac >= 0.03 and el > 1.0:
            eta = max(0.0, el * (1 - frac) / frac)
        return kind, done, len(rows), frac, eta, os.path.basename(cur.src) if cur is not None else ""

    def mapping(self):
        """{ruta normalizada del original: ruta preparada} de todo lo que ya está en la carpeta de destino."""
        return {norm_path(r.src): r.dst for r in self.rows if r.dst and os.path.exists(r.dst) and r.state in DONE}

    def counts(self):
        """Cuántos videos hay de cada clasificación (los ya preparados cuentan con la que tenían antes de prepararse)."""
        out = {c: 0 for c in CATEGORIES}
        for r in self.rows:
            if r.state == UNREADABLE or r.plan is None:
                out[UNREADABLE] += 1
            else:
                out[r.plan.category] += 1
        return out

    def needs_work(self, r):
        """¿Conviene preparar este video (no compatible o mejorable) y aún no está hecho?"""
        return r.plan is not None and r.plan.category in (INCOMPATIBLE, IMPROVABLE) and r.state in ELIGIBLE
