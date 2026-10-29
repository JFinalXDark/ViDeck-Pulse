# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Traspaso directo de los videos convertidos a la carpeta de videos (update 13), sin Qt.

En «Preparar videos», con la opción de dejar los convertidos en la misma carpeta de videos (en vez de en una carpeta aparte), el programa
pregunta «¿Reemplazar el video original una vez convertido?»:
  · Sí → el convertido (`nombre.mp4`) ocupa el lugar del original, y el original se manda a la Papelera de Windows (o se elimina de forma
    definitiva si la unidad no tiene Papelera y el usuario lo aceptó). Antes se VERIFICA el convertido de arriba abajo.
  · No → el convertido se guarda junto al original como «nombre [Converted].mp4»; el vínculo con la canción pasa al convertido y el original
    se queda (la lista principal lo oculta mientras exista su «[Converted]»).

Orden estricto para cada video (si algo falla en cualquier paso, el original se conserva tal cual):
  1. ffmpeg escribe el convertido con el nombre «<original>.verificando» en la MISMA carpeta (un video a medias nunca tiene nombre de video);
  2. verificación (`verify_converted`): ffprobe lo abre, hay imagen (y audio si el original lo tenía), medidas/códec/formato de color como se
     planeó, duración dentro de la tolerancia, número de fotogramas coherente, decodificación completa sin errores y su SHA-256;
  3. `place` (hilo de trabajo): comprueba que el original no cambió desde el análisis, coloca el convertido con su nombre final (si se llama igual
     que el original, este pasa antes a «<nombre>.viejo») y vuelve a calcular el SHA-256 del archivo ya colocado;
  4. `finish` (hilo de la interfaz, porque toca tracks_db): copia de seguridad de tracks_db.json, vínculos nuevos, guardar, releer el archivo y
     comprobar que cada vínculo cambiado apunta a un archivo que existe;
  5. solo entonces (modo «Sí») `finish` comprueba otra vez que el original sigue igual (tamaño y fecha) y lo manda a la Papelera / lo elimina,
     dejando en el registro su ruta, tamaño y SHA-256.
Las pruebas de estas funciones se hacen SOLO con carpetas temporales del sandbox: nunca con videos reales de nadie.
"""
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass

from . import devlog as dl
from . import paths, trash
from .db import norm_path
from .lowprio import with_low_priority
from .media import VIDEO_EXTS

TAG = " [Converted]"
CONVERTED_RE = re.compile(r"^(?P<stem>.*) \[Converted\](?: \(\d+\))?(?P<ext>\.[^.\\/]+)$", re.I)
WORK_EXT = ".verificando"                 # el convertido mientras se verifica (no es una extensión de video: ni la búsqueda ni mpv lo ven)
OLD_EXT = ".viejo"                        # el original apartado cuando el convertido se llama igual
DURATION_TOL_S, DURATION_TOL_PCT = 0.25, 0.005
DECODE_TIMEOUT_MIN = 600                  # s mínimos para decodificar por completo (suma 3 × la duración del video)
BACKUP_SUFFIX = ".antes-de-traspasar.bak"



# ------------------------------------------------------------------------------------------------------------- nombres
def is_converted_name(path):
    return bool(CONVERTED_RE.match(os.path.basename(path)))


def unique_path(path, exists=os.path.exists):
    """`path` si está libre; si no «nombre (2).ext», «nombre (3).ext»…"""
    if not exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


def replace_target(src):
    """Nombre del convertido que ocupa el lugar del original: el mismo nombre con extensión .mp4."""
    return os.path.splitext(src)[0] + ".mp4"


def converted_target(src, exists=os.path.exists):
    """Nombre del convertido que se guarda junto al original: «nombre [Converted].mp4» (con « (2)» si ya existía)."""
    base = os.path.splitext(src)[0] + TAG
    cand, n = base + ".mp4", 2
    while exists(cand):
        cand, n = f"{base} ({n}).mp4", n + 1
    return cand


def hidden_originals(videos):
    """Rutas de `videos` que son el ORIGINAL de un «[Converted]» de la misma lista (misma carpeta y mismo nombre sin extensión): la lista
    principal no los muestra mientras existe su convertido."""
    keys = set()
    for p in videos:
        m = CONVERTED_RE.match(os.path.basename(p))
        if m:
            keys.add((os.path.normcase(os.path.dirname(p)), os.path.normcase(m.group("stem"))))
    if not keys:
        return set()
    out = set()
    for p in videos:
        b = os.path.basename(p)
        if CONVERTED_RE.match(b):
            continue
        if (os.path.normcase(os.path.dirname(p)), os.path.normcase(os.path.splitext(b)[0])) in keys:
            out.add(p)
    return out


def sig_of(path):
    """(tamaño, mtime_ns) de un archivo, o None si no se puede leer."""
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


# ------------------------------------------------------------------------------------------------------------- verificación
def file_sha256(path, cancel=None, chunk=1 << 22):
    """SHA-256 (hexadecimal) de un archivo, o None si se cancela a la mitad (o no se puede leer)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    return None
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
    except OSError:
        return None
    return h.hexdigest()


def decode_check(ffmpeg, path, cancel=None, timeout=3600):
    """Decodifica el video entero (y su audio) sin guardar nada: (True, '') si no hubo ni un error; (False, texto); (None, '') si se canceló."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", path, "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"]
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             **with_low_priority({"creationflags": flags}))
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    t0 = time.time()
    while True:
        if cancel is not None and cancel.is_set():
            p.kill()
            p.communicate()
            return None, ""
        try:
            _, err = p.communicate(timeout=0.4)
            break
        except subprocess.TimeoutExpired:
            if time.time() - t0 > timeout:
                p.kill()
                p.communicate()
                return False, "tardó demasiado en decodificarse"
    text = (err or b"").decode("utf-8", "replace").strip()
    if p.returncode != 0 or text:
        return False, (text.splitlines() or [f"código {p.returncode}"])[0][:160]
    return True, ""


def packet_count(ffprobe, path):
    """Paquetes de video del archivo (lee el índice, sin decodificar) o None."""
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets",
                            "-of", "csv=p=0", path], capture_output=True, timeout=120,
                           **with_low_priority({"creationflags": 0x08000000 if os.name == "nt" else 0}))
        return int(r.stdout.decode("utf-8", "replace").strip().split()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


@dataclass
class Verification:
    problems: list
    after: dict = None
    sha: str = ""
    cancelled: bool = False

    @property
    def ok(self):
        return not self.problems and not self.cancelled


def verify_converted(work, before, plan, tools, cancel=None, decode=True):
    """Verifica el convertido `work` contra el análisis del original (`before`) y el plan. Devuelve una `Verification`: lista de problemas
    (vacía = todo bien), análisis del convertido y su SHA-256."""
    from . import prepare as pr
    ffmpeg, ffprobe = tools
    problems = []
    after = pr.probe(work, ffprobe)
    if after is None:
        return Verification(["el video convertido no se puede abrir"])
    if before.get("acodec") and not after.get("acodec"):
        problems.append("al convertido le falta el audio")
    d0, d1 = before.get("duration"), after.get("duration")
    if d0 and not d1:
        problems.append("el convertido no trae su duración")
    elif d0 and d1 and abs(d0 - d1) > max(DURATION_TOL_S, DURATION_TOL_PCT * d0):
        problems.append(f"la duración cambió ({d0:.2f} s → {d1:.2f} s)")
    if (after.get("vcodec") or "").lower() != "h264":
        problems.append(f"el códec no es h264 ({after.get('vcodec') or '?'})")
    if after.get("pix_fmt") not in ("yuv420p", "yuvj420p"):
        problems.append(f"el formato de color no es yuv420p ({after.get('pix_fmt') or '?'})")
    w0, h0 = before.get("width") or 0, before.get("height") or 0
    w1, h1 = after.get("width") or 0, after.get("height") or 0
    if plan is not None and plan.height and h0 > plan.height:
        if abs(h1 - plan.height) > 1:
            problems.append(f"el alto no es el pedido ({h1} p, no {plan.height} p)")
    elif w0 and h0 and (abs(w1 - w0) > 1 or abs(h1 - h0) > 1):
        problems.append(f"las medidas cambiaron ({w0}×{h0} → {w1}×{h1})")
    n, fps = packet_count(ffprobe, work), after.get("fps")
    if n is not None and d1 and fps:
        expect = d1 * float(fps)
        if abs(n - expect) > max(3.0, 0.02 * expect):
            problems.append(f"el número de fotogramas no coincide ({n} de ~{expect:.0f})")
    elif n is not None and n < 1:
        problems.append("el convertido no tiene fotogramas")
    if cancel is not None and cancel.is_set():
        return Verification(problems, after, "", True)
    if decode:
        ok, text = decode_check(ffmpeg, work, cancel, max(DECODE_TIMEOUT_MIN, 3 * (d1 or 0)))
        if ok is None:
            return Verification(problems, after, "", True)
        if not ok:
            problems.append(f"errores al decodificarlo: {text}")
    sha = file_sha256(work, cancel)
    if sha is None:
        cancelled = bool(cancel is not None and cancel.is_set())
        return Verification(problems if cancelled else problems + ["no se pudo leer el convertido"], after, "", cancelled)
    return Verification(problems, after, sha)


# ------------------------------------------------------------------------------------------------------------- trabajo de un video
@dataclass
class Job:
    src: str                        # el original
    mode: str                       # "replace" | "keep"
    work: str                       # el convertido ya verificado («<original>.verificando»)
    sha: str                        # SHA-256 del convertido verificado
    sig: tuple                      # (tamaño, mtime_ns) del original cuando se analizó
    src_sha: str = ""               # SHA-256 del original (para el registro de lo que se elimina)
    final: str = ""                 # nombre final del convertido (cuando ya está colocado)
    old: str = ""                   # el original apartado («.viejo») cuando el convertido se llama igual
    stage: str = "verified"         # verified → placed → done | failed
    note: str = ""
    disposal: str = ""              # qué pasó con el original: "papelera" · "definitivo" · "conservado" · ""
    relinked: int = 0
    after: dict = None


def work_path(src):
    return src + WORK_EXT


def _remove_quiet(path, tries=8, wait=0.25):
    """Borra un archivo temporal. En Windows un archivo recién usado por ffmpeg/ffprobe (o mirado por el antivirus o el Explorador) tarda un
    momento en soltarse: se reintenta unos 2 s antes de rendirse (y si no se pudo, queda en el registro; se limpia en la próxima apertura)."""
    for i in range(tries):
        try:
            if path and os.path.exists(path):
                os.remove(path)
            return True
        except OSError:
            if i + 1 < tries:
                time.sleep(wait)
    dl.warn("preparar", f"no se pudo borrar el archivo temporal {path}")
    return False


def clean_stale(folder):
    """Borra los «.verificando» que quedaron de una conversión interrumpida (cierre forzado, corte de luz): son convertidos sin verificar, nunca
    el original. NO toca los «.viejo» (si un corte pilló justo el cambio de nombre, ese puede ser el único original). Devuelve cuántos borró."""
    n = 0
    try:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if f.endswith(WORK_EXT) and _remove_quiet(os.path.join(root, f), tries=1):
                    n += 1
    except OSError:
        pass
    if n:
        dl.info("preparar", f"se borraron {n} archivos temporales de conversiones interrumpidas")
    return n


def work_one(src, mode, tools, plan, info, sig, progress=None, cancel=None, on_stage=None):
    """Convierte, verifica y coloca UN video (hilo de trabajo). Devuelve (Result, Job o None): Result("ok"/"revisar") con el `Job` listo para
    `finish`; Result("error"/"cancelado") sin Job (y sin dejar archivos a medias). `on_stage("Verificando")` avisa al pasar a verificar."""
    from . import prepare as pr
    work = work_path(src)
    _remove_quiet(work)
    res = pr.prepare_one(src, work, tools, plan, info, progress, cancel)
    if res.status in ("error", "cancelado"):
        _remove_quiet(work)
        return res, None
    try:
        if on_stage is not None:
            on_stage("Verificando")
        v = verify_converted(work, info, plan, tools, cancel)
        if v.cancelled:
            _remove_quiet(work)
            return pr.Result("cancelado", "Cancelado"), None
        if not v.ok:
            _remove_quiet(work)
            return pr.Result("error", "Verificación fallida: " + "; ".join(v.problems) + "; original conservado"), None
        src_sha = file_sha256(src, cancel) if mode == "replace" else ""
        if mode == "replace" and src_sha is None:
            _remove_quiet(work)
            return (pr.Result("cancelado", "Cancelado") if cancel is not None and cancel.is_set()
                    else pr.Result("error", "No se pudo leer el original; original conservado")), None
        job = Job(src, mode, work, v.sha, sig, src_sha or "", after=v.after)
        err = place(job, cancel)
        if err:
            _remove_quiet(work)
            return pr.Result("error", err), None
        return pr.Result("revisar" if res.status == "revisar" else "ok", res.message if res.status == "revisar" else "", v.after), job
    except OSError as e:
        _remove_quiet(work)
        return pr.Result("error", f"{type(e).__name__}: {e}; original conservado"), None


def place(job, cancel=None, hasher=None):
    """Coloca el convertido verificado con su nombre final. Devuelve '' si todo salió bien (job.stage = «placed») o el motivo del fallo
    (el original queda como estaba y el convertido se descarta)."""
    cur = sig_of(job.src)
    if cur is None or (job.sig is not None and cur != job.sig):
        job.stage, job.note = "failed", "El original cambió mientras se convertía; original conservado"
        return job.note
    if job.mode == "keep":
        final = converted_target(job.src)
    else:
        final = replace_target(job.src)
        if norm_path(final) != norm_path(job.src):
            final = unique_path(final)
    same = norm_path(final) == norm_path(job.src)
    old = ""
    try:
        if same:
            old = unique_path(job.src + OLD_EXT)
            os.replace(job.src, old)                                       # (un video abierto en Windows no se puede apartar: PermissionError)
        try:
            if os.path.lexists(final):                                     # el nombre final se ocupó en el medio: NUNCA se pisa un archivo ajeno
                raise FileExistsError(final)
            os.rename(job.work, final)                                     # (en Windows rename tampoco pisa nada; aquí se comprueba además a mano)
        except OSError:
            if old:
                os.replace(old, job.src)
            raise
    except OSError as e:
        job.stage = "failed"
        if isinstance(e, PermissionError):
            job.note = f"El original está en uso o la carpeta no se puede escribir ({type(e).__name__}); original conservado"
        elif isinstance(e, FileExistsError):
            job.note = f"Ya existe un archivo con ese nombre ({os.path.basename(final)}); original conservado"
        else:
            job.note = f"{type(e).__name__}: {e}; original conservado"
        return job.note
    got = (hasher or file_sha256)(final, cancel)
    if got != job.sha:
        _undo_place(job, final, old)
        job.stage = "failed"
        job.note = ("Se canceló; original conservado" if got is None and cancel is not None and cancel.is_set()
                    else "El convertido cambió al colocarlo; original conservado")
        return job.note
    job.final, job.old, job.stage = final, old, "placed"
    return ""


def _undo_place(job, final, old):
    """Deshace `place`: el convertido colocado se descarta y el original vuelve a su nombre."""
    _remove_quiet(final)
    if old and os.path.exists(old):
        try:
            os.replace(old, job.src)
        except OSError as e:
            dl.error("preparar", f"No se pudo devolver el original a su nombre: {old}: {e}")


# ------------------------------------------------------------------------------------------------------------- cierre de un video
@dataclass
class Context:
    """Lo que `finish` necesita (hilo de la interfaz): la base de tracks y qué hacer con los originales."""
    db: object
    dispose: str = "recycle"               # "recycle" (Papelera) | "delete" (definitivo)
    recycle: object = None                 # función(ruta) → (ok, mensaje); por defecto trash.recycle (las pruebas la sustituyen)
    delete: object = None
    backup_made: bool = False
    backup_path: str = ""

    def __post_init__(self):
        self.recycle = self.recycle or trash.recycle
        self.delete = self.delete or trash.delete_forever


def _backup_db(ctx):
    if ctx.backup_made:
        return
    ctx.backup_made = True
    p = str(ctx.db.path)
    ctx.backup_path = p + BACKUP_SUFFIX
    try:
        if os.path.exists(p):
            with open(p, "rb") as f, open(ctx.backup_path, "wb") as g:
                g.write(f.read())
    except OSError as e:
        dl.warn("preparar", f"No se pudo copiar tracks_db.json antes de traspasar: {e}")
        ctx.backup_path = ""


def _read_back_ok(db, final, n):
    """Relee tracks_db.json del disco: al menos `n` vínculos apuntan a `final` y ese archivo existe."""
    try:
        with open(str(db.path), encoding="utf-8") as f:
            tracks = json.load(f).get("tracks", [])
    except (OSError, ValueError):
        return False
    want = norm_path(final)
    hits = sum(1 for e in tracks if isinstance(e, dict) and isinstance(e.get("video_path"), str)
               and norm_path(paths.resolve(e["video_path"])) == want)
    return hits >= n and os.path.exists(final)


def finish(job, ctx):
    """Cierra un video ya colocado (hilo de la interfaz): vínculos, tracks_db y, en el modo «Sí», el original. Deja `job.stage` en «done» o
    «failed» y `job.note`/`job.disposal` con lo ocurrido. Un fallo ANTES de tocar el original deshace la colocación."""
    if job.stage != "placed":
        return job
    tracks = ctx.db.tracks
    want = norm_path(job.src)
    mine = [e for e in tracks if isinstance(e, dict) and e.get("video_path") and norm_path(e["video_path"]) == want]
    olds = [e["video_path"] for e in mine]
    try:
        if mine:
            _backup_db(ctx)
            for e in mine:
                e["video_path"] = job.final
            ctx.db.save()
            if not _read_back_ok(ctx.db, job.final, len(mine)):
                raise OSError("tracks_db.json no quedó como se esperaba")
    except OSError as e:
        for e2, o in zip(mine, olds):
            e2["video_path"] = o
        try:
            ctx.db.save()
        except OSError:
            pass
        _undo_place(job, job.final, job.old)
        job.stage, job.old, job.final = "failed", "", ""
        job.note = f"No se pudieron actualizar los vínculos ({type(e).__name__}: {e}); original conservado"
        dl.error("preparar", f"{os.path.basename(job.src)}: {job.note}")
        return job
    job.relinked = len(mine)
    if job.mode == "keep":
        job.stage, job.disposal = "done", ""
        job.note = f"Guardado como {os.path.basename(job.final)}"
        return job
    victim = job.old or job.src
    if sig_of(victim) != job.sig:
        job.stage, job.disposal = "done", "conservado"
        job.note = "El original cambió: se conserva (el convertido ya ocupa su lugar)" if not job.old else "El original cambió: se conserva apartado"
        dl.warn("preparar", f"{os.path.basename(job.src)}: {job.note}")
        return job
    size = job.sig[0] if job.sig else 0
    ok, msg = ctx.recycle(victim) if ctx.dispose == "recycle" else ctx.delete(victim)
    if ok and not os.path.exists(victim):
        job.stage = "done"
        job.disposal = "papelera" if ctx.dispose == "recycle" else "definitivo"
        job.note = "Reemplazó al original (Papelera)" if job.disposal == "papelera" else "Reemplazó al original (eliminado)"
        if job.disposal == "papelera":
            dl.info("preparar", f"original enviado a la Papelera: {job.src} ({size} bytes, SHA-256 {job.src_sha}); convertido: {job.final} (SHA-256 {job.sha})")
        else:
            dl.info("preparar", f"original eliminado: {job.src} ({size} bytes, SHA-256 {job.src_sha}); convertido: {job.final} (SHA-256 {job.sha})")
    else:
        job.stage, job.disposal = "done", "conservado"
        job.note = f"No se pudo eliminar el original ({msg or 'sigue ahí'}); se conserva"
        dl.warn("preparar", f"{os.path.basename(job.src)}: {job.note}")
    return job
