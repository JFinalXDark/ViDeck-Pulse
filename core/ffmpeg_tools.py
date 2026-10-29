# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""ffmpeg propio de ViDeck Pulse: se instala en la subcarpeta «ffmpeg» del programa, se verifica y se puede actualizar.

«Preparar videos» y el análisis de compatibilidad necesitan ffmpeg/ffprobe. Para que nadie tenga que buscarlos e instalarlos aparte:
  · si no hay ffmpeg, un botón lo descarga a `<carpeta del programa>/ffmpeg/` (nada se instala en el sistema ni en el PATH);
  · al iniciar el programa se mira (como mucho una vez al día, sin bloquear nada) si hay una versión más nueva y se avisa;
  · «Actualizar» descarga, verifica y reemplaza los archivos de esa carpeta (y vuelve a la versión anterior si algo sale mal).

Confianza: solo se descarga por HTTPS desde repositorios públicos conocidos (las builds de Windows que enlaza ffmpeg.org: gyan.dev
a través de su espejo oficial en GitHub, y BtbN/FFmpeg-Builds como alternativa). Cada paquete se verifica con su SHA-256 publicado
(el resumen que da la API del repositorio o su archivo de sumas) ANTES de abrirlo; si no hay suma publicada o no coincide, NO se instala.
Después se comprueba que el ffmpeg descargado arranca y trae el codificador libx264. Nada del paquete se ejecuta antes de eso.

Sin Qt: toda la red pasa por `Net` (se reemplaza en las pruebas por una versión local).
"""
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import namedtuple

from . import paths
from .lowprio import with_low_priority

USER_AGENT = "ViDeckPulse/2.0 (+https://github.com/; ffmpeg-updater)"
ALLOWED_HOSTS = ("api.github.com", "github.com", "gyan.dev", "www.gyan.dev")        # + cualquier *.githubusercontent.com
ALLOWED_SUFFIX = (".githubusercontent.com",)
NET_TIMEOUT = 20
MAX_UNPACKED = 1_500_000_000         # tope de lo que se descomprime (protege de un zip malicioso)
NO_WINDOW = 0x08000000

Release = namedtuple("Release", "provider label tag version url size sha256 name")
Tools = namedtuple("Tools", "ffmpeg ffprobe origin version")


class FfmpegError(Exception):
    """Un fallo explicable al usuario (la descripción ya viene en español). También lo usa core/mpv_tools.py."""


class NetworkError(FfmpegError):
    """No hubo conexión con el servidor (no es un fallo del repositorio ni del programa)."""


class Cancelled(FfmpegError):
    pass


class VerifyError(FfmpegError):
    pass


class InUseError(FfmpegError):
    pass


# ---------------------------------------------------------------------------------------------------- rutas
def app_dir():
    return paths.components_dir()


def own_dir():
    return os.path.join(app_dir(), "ffmpeg")


def exe(name):
    return name + (".exe" if os.name == "nt" else "")


def _flags():
    return NO_WINDOW if os.name == "nt" else 0


def _run(cmd, timeout=20):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, **with_low_priority({"creationflags": _flags()}))


def version_file(base=None):
    return os.path.join(base or own_dir(), "version.json")


def installed(base=None):
    """Lo instalado en la carpeta propia: {"version", "tag", "provider", "sha256", "installed"} o None si no hay (o está incompleto)."""
    base = base or own_dir()
    if not (os.path.isfile(os.path.join(base, exe("ffmpeg"))) and os.path.isfile(os.path.join(base, exe("ffprobe")))):
        return None
    try:
        with open(version_file(base), encoding="utf-8") as f:
            info = json.load(f)
        return info if isinstance(info, dict) and info.get("version") else {"version": version_of(os.path.join(base, exe("ffmpeg")))}
    except (OSError, ValueError):
        return {"version": version_of(os.path.join(base, exe("ffmpeg")))}


def version_of(ffmpeg_path):
    """El número de versión que declara un ffmpeg («9.0.2», «n9.0-…», «N-12345-g…») o «» si no arranca."""
    try:
        r = _run([ffmpeg_path, "-version"], 15)
    except (OSError, subprocess.SubprocessError):
        return ""
    if r.returncode != 0:
        return ""
    line = (r.stdout.decode("utf-8", "replace").splitlines() or [""])[0]
    m = re.search(r"ffmpeg version\s+(\S+)", line)
    return m.group(1) if m else ""


def vtuple(text):
    """«9.0.2-essentials_build-…» / «n9.0-3-g…» → (9, 0, 2) para comparar; () si no hay número de versión (builds «N-xxxxx»)."""
    m = re.match(r"^[nN]?(\d+(?:\.\d+){0,3})", str(text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(release, inst):
    """¿La versión publicada es más nueva que la instalada? (Sin número de versión comparable en alguna de las dos: se compara la etiqueta.)"""
    cur = (inst or {}).get("version", "") if inst else ""
    a, b = vtuple(release.version), vtuple(cur)
    if a and b:
        return a > b
    return bool(release.tag) and release.tag != (inst or {}).get("tag", "")


def resolve(configured="", base=None):
    """El ffmpeg que se va a usar: el de `prepare.ffmpeg_cmd` (si el usuario eligió uno), el propio de la carpeta del programa, el
    del PATH y, al final, sitios típicos de Windows. Devuelve Tools(ffmpeg, ffprobe, origen, versión) o Tools(None, None, "", "")."""
    own = base or own_dir()
    cands = []
    if base is None:
        from . import components                                  # con «Legacy Mode» activo, el ffmpeg del paquete Legacy va primero
        legacy_dir = components.ffmpeg_dir()
        if legacy_dir:
            cands.append((os.path.join(legacy_dir, exe("ffmpeg")), "legacy"))
    if configured:
        for p in (configured, os.path.join(configured, exe("ffmpeg")), os.path.join(configured, "bin", exe("ffmpeg"))):
            cands.append((p, "config"))
    cands.append((os.path.join(own, exe("ffmpeg")), "own"))
    found = shutil.which("ffmpeg")
    if found:
        cands.append((found, "path"))
    if os.name == "nt":
        for b in (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
                  os.path.expandvars(r"%ProgramData%\chocolatey\bin")):
            cands.append((os.path.join(b, exe("ffmpeg")), "other"))
    for path, origin in cands:
        if not path or not os.path.isfile(path) or "ffmpeg" not in os.path.basename(path).lower():
            continue
        probe = os.path.join(os.path.dirname(path), exe("ffprobe"))
        if not os.path.isfile(probe):
            probe = shutil.which("ffprobe")
        ver = version_of(path)
        if probe and ver:
            return Tools(path, probe, origin, ver)
    return Tools(None, None, "", "")


# ---------------------------------------------------------------------------------------------------- red
def host_ok(url):
    p = urllib.parse.urlparse(url)
    h = (p.hostname or "").lower()
    return p.scheme == "https" and (h in ALLOWED_HOSTS or any(h.endswith(s) for s in ALLOWED_SUFFIX))


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Cada salto de una redirección tiene que seguir siendo HTTPS y de un servidor permitido."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_ok(newurl):
            raise FfmpegError(f"redirección a un servidor no permitido: {urllib.parse.urlparse(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Net:
    """Acceso a la red (HTTPS con verificación de certificados). Las pruebas lo reemplazan por una clase con los mismos métodos."""

    def _open(self, url, timeout=NET_TIMEOUT):
        if not host_ok(url):
            raise FfmpegError(f"dirección no permitida: {url}")
        opener = urllib.request.build_opener(_Redirects)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json, */*"})
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise FfmpegError(f"el servidor respondió {e.code} ({urllib.parse.urlparse(url).hostname})") from e
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise NetworkError(f"no se pudo conectar con {urllib.parse.urlparse(url).hostname}: {reason}") from e

    def text(self, url, limit=2_000_000):
        with self._open(url) as r:
            return r.read(limit).decode("utf-8", "replace")

    def json(self, url):
        try:
            return json.loads(self.text(url))
        except ValueError as e:
            raise FfmpegError("la respuesta del servidor no se pudo leer") from e

    def download(self, url, dest, progress=None, cancel=None):
        """Descarga `url` en `dest` (calculando el SHA-256 sobre la marcha). Devuelve (sha256 hex, bytes)."""
        h, done = hashlib.sha256(), 0
        with self._open(url, 30) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("descarga cancelada")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress("download", done, total)
        return h.hexdigest(), done


# ---------------------------------------------------------------------------------------------------- repositorios
PROVIDERS = (
    {"id": "gyan", "label": "gyan.dev (espejo oficial en GitHub)", "repo": "GyanD/codexffmpeg", "tag": None,
     "asset": r"^ffmpeg-(?P<v>\d+(?:\.\d+)*)-essentials_build\.zip$"},
    {"id": "btbn", "label": "BtbN/FFmpeg-Builds (GitHub)", "repo": "BtbN/FFmpeg-Builds", "tag": "latest",
     "asset": r"^ffmpeg-n(?P<v>\d+(?:\.\d+)*)-latest-win64-gpl-shared-(?P=v)\.zip$"},
)
_HEX64 = re.compile(r"\b([" + string.hexdigits + r"]{64})\b")


def _sha_for(net, assets, asset):
    """La suma SHA-256 publicada de un archivo: el campo `digest` que da GitHub, o la de un archivo de sumas del mismo lanzamiento
    («<nombre>.sha256», «checksums.sha256»). Sin ninguna suma publicada no se instala nada."""
    dg = str(asset.get("digest") or "")
    if dg.lower().startswith("sha256:") and _HEX64.fullmatch(dg[7:]):
        return dg[7:].lower()
    by_name = {a.get("name"): a for a in assets}
    for side in (asset["name"] + ".sha256", "checksums.sha256", "checksums.sha256.txt"):
        a = by_name.get(side)
        if not a or not host_ok(a.get("browser_download_url", "")):
            continue
        try:
            body = net.text(a["browser_download_url"])
        except FfmpegError:
            continue
        for line in body.splitlines():
            m = _HEX64.search(line)
            if m and (side.startswith(asset["name"]) or asset["name"] in line):
                return m.group(1).lower()
    return ""


def _from_github(net, prov):
    url = (f"https://api.github.com/repos/{prov['repo']}/releases/tags/{prov['tag']}" if prov["tag"]
           else f"https://api.github.com/repos/{prov['repo']}/releases/latest")
    data = net.json(url)
    assets = [a for a in (data.get("assets") or []) if isinstance(a, dict) and a.get("name")]
    pat, best = re.compile(prov["asset"]), None
    for a in assets:
        m = pat.match(a["name"])
        if m and (best is None or vtuple(m.group("v")) > vtuple(best[0])):
            best = (m.group("v"), a)
    if best is None:
        raise FfmpegError(f"{prov['repo']}: no hay un paquete de Windows con el nombre esperado")
    ver, a = best
    link = a.get("browser_download_url", "")
    if not host_ok(link):
        raise FfmpegError(f"{prov['repo']}: dirección de descarga no permitida")
    sha = _sha_for(net, assets, a)
    if not sha:
        raise FfmpegError(f"{prov['repo']}: el paquete no trae una suma SHA-256 publicada, no se puede verificar")
    return Release(prov["id"], prov["label"], str(data.get("tag_name") or ver), ver, link, int(a.get("size") or 0), sha, a["name"])


def latest(net=None, providers=PROVIDERS):
    """La versión más nueva publicada por el primer repositorio que responda bien. Lanza FfmpegError con todos los motivos si ninguno."""
    net, errors, kinds = net or Net(), [], []
    for prov in providers:
        try:
            return _from_github(net, prov)
        except FfmpegError as e:
            errors.append(f"{prov['label']}: {e}")
            kinds.append(isinstance(e, NetworkError))
    cls = NetworkError if kinds and all(kinds) else FfmpegError            # sin conexión en ninguno: no es un fallo de los repositorios
    raise cls("no se pudo consultar ffmpeg · " + " · ".join(errors))


# ---------------------------------------------------------------------------------------------------- instalación
def _wanted(member):
    """Archivos del paquete que se instalan: los de su carpeta «bin» (ffmpeg, ffprobe y, si es una build «shared», sus .dll). ffplay no."""
    n = member.replace("\\", "/")
    if member.endswith("/") or "/bin/" not in "/" + n:
        return ""
    base = n.rsplit("/", 1)[-1]
    low = base.lower()
    if low.startswith("ffplay") or ".." in n.split("/"):
        return ""
    if low in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe") or low.endswith(".dll"):
        return base
    return ""


def _license_member(names):
    for n in names:
        b = n.replace("\\", "/").rsplit("/", 1)[-1].upper()
        if b in ("LICENSE", "LICENSE.TXT", "COPYING.GPLV3") and n.replace("\\", "/").count("/") <= 1:
            return n
    return ""


def _check_new(folder):
    """El ffmpeg descargado tiene que arrancar, traer libx264 y tener a su ffprobe."""
    ff, fp = os.path.join(folder, exe("ffmpeg")), os.path.join(folder, exe("ffprobe"))
    if not (os.path.isfile(ff) and os.path.isfile(fp)):
        raise VerifyError("el paquete no trae ffmpeg y ffprobe")
    if os.name != "nt":
        for p in (ff, fp):
            os.chmod(p, 0o755)
    ver = version_of(ff)
    if not ver:
        raise VerifyError("el ffmpeg descargado no arranca")
    try:
        enc = _run([ff, "-hide_banner", "-encoders"], 20).stdout.decode("utf-8", "replace")
        if "libx264" not in enc:
            raise VerifyError("este ffmpeg no trae el codificador libx264")
        if _run([fp, "-version"], 15).returncode != 0:
            raise VerifyError("el ffprobe descargado no arranca")
    except (OSError, subprocess.SubprocessError) as e:
        raise VerifyError(f"no se pudo probar el ffmpeg descargado ({e})") from e
    return ver


def install(release, net=None, progress=None, cancel=None, base=None):
    """Descarga `release`, verifica su SHA-256, lo descomprime aparte, lo prueba y recién entonces reemplaza lo instalado. Si algo
    falla (red, suma, archivo en uso…) lo que había sigue intacto. Devuelve la info guardada en version.json."""
    net, base = net or Net(), base or own_dir()
    os.makedirs(base, exist_ok=True)
    tmp = os.path.join(base, "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    new, old, pkg = os.path.join(tmp, "new"), os.path.join(tmp, "old"), os.path.join(tmp, "paquete.zip.part")
    os.makedirs(new)
    os.makedirs(old)
    try:
        free = shutil.disk_usage(base).free
        need = int((release.size or 120_000_000) * 3.2)
        if free < need:
            raise FfmpegError(f"falta espacio en el disco: hacen falta ~{need / 1e6:.0f} MB y hay {free / 1e6:.0f} MB libres")
        got, _n = net.download(release.url, pkg, progress, cancel)
        if progress:
            progress("verify", 0, 0)
        if got.lower() != release.sha256.lower():
            raise VerifyError("la suma SHA-256 del paquete no coincide con la publicada: se descartó la descarga")
        if progress:
            progress("extract", 0, 0)
        total = 0
        with zipfile.ZipFile(pkg) as z:
            infos = z.infolist()
            lic = _license_member([i.filename for i in infos])
            for i in infos:
                name = _wanted(i.filename)
                if not name:
                    continue
                total += i.file_size
                if total > MAX_UNPACKED:
                    raise VerifyError("el paquete descomprimido es demasiado grande")
                with z.open(i) as src, open(os.path.join(new, name), "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
            if lic:
                with z.open(lic) as src, open(os.path.join(new, "FFMPEG_LICENSE.txt"), "wb") as dst:
                    shutil.copyfileobj(src, dst)
        if progress:
            progress("check", 0, 0)
        ver = _check_new(new)
        info = {"version": release.version or ver, "tag": release.tag, "provider": release.provider, "package": release.name,
                "sha256": release.sha256, "ffmpeg_banner": ver, "installed": time.strftime("%Y-%m-%d %H:%M:%S")}
        with open(os.path.join(new, "version.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        moved = []                                           # (destino, copia de lo anterior) para poder volver atrás
        try:
            for name in sorted(os.listdir(new)):
                dst = os.path.join(base, name)
                if os.path.exists(dst):
                    keep = os.path.join(old, name)
                    os.replace(dst, keep)
                    moved.append((dst, keep))
                os.replace(os.path.join(new, name), dst)
                moved.append((dst, None))
        except OSError as e:
            for dst, keep in reversed(moved):                # vuelve atrás: lo anterior queda como estaba
                try:
                    if keep is None:
                        os.remove(dst)
                    else:
                        os.replace(keep, dst)
                except OSError:
                    pass
            raise InUseError("no se pudo reemplazar ffmpeg (¿hay un video preparándose?): espera a que termine y reintenta") from e
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remove(base=None):
    """Borra lo instalado por el programa (solo los archivos que él instaló dentro de su carpeta «ffmpeg»)."""
    base = base or own_dir()
    for name in os.listdir(base) if os.path.isdir(base) else ():
        low = name.lower()
        if low in ("version.json", "ffmpeg_license.txt") or low.endswith((".dll", ".exe")) or low in ("ffmpeg", "ffprobe"):
            try:
                os.remove(os.path.join(base, name))
            except OSError:
                pass
    try:
        os.rmdir(base)
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------------- aviso al iniciar
def due(cfg_ffmpeg, now=None):
    """¿Se consulta si hay una versión nueva? Una vez en cada inicio del programa, si «Verificar actualizaciones al iniciar» está activado
    (sin plazos: nunca con el programa abierto)."""
    return bool(cfg_ffmpeg.get("auto_check", True))


def pending_update(cfg_ffmpeg, net=None, base=None, now=None):
    """Para el arranque: (Release, instalado) si hay una versión nueva que avisar, o None. Anota la hora de la consulta en
    `cfg_ffmpeg["last_check"]` (el que llama guarda la config). Cualquier fallo de red se ignora (no es un error del programa)."""
    inst = installed(base)
    if not inst or not due(cfg_ffmpeg, now):
        return None
    cfg_ffmpeg["last_check"] = time.time() if now is None else now
    try:
        rel = latest(net)
    except FfmpegError:
        return None
    if cfg_ffmpeg.get("skip_version") == rel.tag or not is_newer(rel, inst):
        return None
    return rel, inst


class Job(threading.Thread):
    """Hilo de fondo para consultar o instalar sin bloquear la interfaz: el resultado queda en `.result` / `.error`."""

    def __init__(self, fn, *a, **k):
        super().__init__(daemon=True)
        self.fn, self.a, self.k = fn, a, k
        self.result, self.error, self.cancel = None, None, threading.Event()

    def run(self):
        try:
            self.result = self.fn(*self.a, **self.k)
        except Exception as e:                      # noqa: BLE001: se informa al usuario con el texto del error
            self.error = e


# ---------------------------------------------------------------------------------------------------- progreso legible
PHASE_TEXT = {"latest": "Consultando los repositorios…", "download": "Descargando…", "verify": "Verificando la suma SHA-256…",
              "extract": "Extrayendo…", "check": "Probando que funcione…",
              "install": "Instalando (Windows puede pedir permiso de administrador)…"}


def progress_text(prog):
    """«Descargando… 38 / 91 MB» a partir de (fase, hecho, total) (es lo que reciben los `progress(phase, done, total)`)."""
    phase, done, total = prog
    text = PHASE_TEXT.get(phase, "…")
    if phase == "download" and total:
        text += f" {done / 1e6:.0f} / {total / 1e6:.0f} MB"
    return text


def progress_fraction(prog):
    """0..1 de la descarga (el resto de las fases son cortas: sin porcentaje, None → barra indeterminada)."""
    phase, done, total = prog
    return min(1.0, done / total) if phase == "download" and total else None


# ---------------------------------------------------------------------------------------------------- ffmpeg base
class BaseInstall:
    """El ffmpeg base. Si el programa no encuentra ningún ffmpeg (ni el propio, ni el elegido a mano, ni el del PATH), lo descarga solo a
    su carpeta «ffmpeg» —verificado con su SHA-256 y probado, como cualquier actualización— para que «Preparar videos» ya funcione sin
    que nadie tenga que instalar ni elegir nada. Un único trabajo a la vez, compartido por la ventana principal y «Preparar videos»:
    quien lo mira (`running`, `prog`) no lo bloquea, y `finish()` entrega el resultado UNA sola vez."""

    def __init__(self):
        self.job, self.prog, self.error, self.info, self.release = None, ("", 0, 0), None, None, None
        self.tried, self._reported = False, True

    @property
    def running(self):
        return self.job is not None and self.job.is_alive()

    def needed(self, configured="", base=None):
        return resolve(configured, base).ffmpeg is None

    def start(self, net=None, base=None, configured="", force=False):
        """Empieza a instalar si hace falta (o si `force`) y no hay otra instalación en marcha. True si arrancó."""
        if self.running or (not force and not self.needed(configured, base)):
            return False
        self.tried, self.error, self.info, self.release, self._reported = True, None, None, None, False
        self.prog = ("latest", 0, 0)
        net = net or Net()
        job = Job(lambda: self._work(net, base, job))
        self.job = job
        job.start()
        return True

    def _work(self, net, base, job):
        from .lowprio import lower_this_thread
        lower_this_thread()
        rel = latest(net)
        self.release = rel
        self.prog = ("download", 0, rel.size)
        return install(rel, net, self._progress, job.cancel, base)

    def _progress(self, phase, done, total):
        self.prog = (phase, done, total)

    def cancel(self):
        if self.running:
            self.job.cancel.set()

    def finish(self):
        """(error, info) cuando el trabajo terminó (solo la primera vez que se pregunta); None si sigue en marcha o ya se entregó."""
        if self.job is None or self.job.is_alive() or self._reported:
            return None
        self._reported = True
        self.error, self.info = self.job.error, self.job.result
        return self.error, self.info

    def clear(self):
        """Se instaló bien por otro camino (p. ej. el botón «Verificar actualizaciones»): el fallo anterior ya no vale."""
        self.error = None


BASE = BaseInstall()
