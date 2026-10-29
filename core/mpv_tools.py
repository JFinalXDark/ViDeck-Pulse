# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""libmpv propia de ViDeck Pulse: se descarga, se verifica y se actualiza como ffmpeg (ver core/ffmpeg_tools.py), con una diferencia.

libmpv es una DLL que el programa carga al arrancar (`import mpv`), y Windows no deja reemplazar una DLL en uso. Por eso una
actualización se hace en dos tiempos:
  1. «Preparar»: se descarga el paquete (.7z de las builds de Windows de mpv: shinchiro y zhongfly en GitHub), se verifica su SHA-256
     publicado, se extrae solo `libmpv-2.dll`, y se prueba en un proceso aparte con el python-mpv del programa (core/mpv_check.py: que
     cargue ESA dll, que cree un reproductor y que tenga la API de render del fundido cruzado). Si todo sale bien queda en `mpv/_pending/`.
  2. Al iniciar el programa, ANTES de importar mpv (`apply_pending`): la dll vigente pasa a `mpv/_previous/`, la preparada ocupa su lugar y se
     vuelve a probar en el sitio. Si algo falla, se vuelve a la anterior al instante y se informa como error. Si el arranque con la
     nueva no llega a terminar (queda la marca `boot.json`), el siguiente arranque también vuelve a la anterior.
La carpeta propia es `<programa>/mpv/`. Nada se instala en el sistema.
"""
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
import time

from . import archive, paths
from .ffmpeg_tools import (MAX_UNPACKED, FfmpegError, InUseError, Job, Net, NetworkError, Release, VerifyError,  # noqa: F401
                           _from_github, due, is_newer, vtuple)
from .lowprio import with_low_priority

DLL = "libmpv-2.dll"
ALIASES = ("libmpv-2.dll", "mpv-2.dll")
VERSION_FILE = "version.json"
BOOT_FILE = "boot.json"
LICENSE_FILE = "MPV_COPYRIGHT.txt"
MIN_DLL_BYTES = 5_000_000          # una libmpv real pesa decenas de MB: algo menor es un paquete equivocado
CHECK_TIMEOUT = 90
NO_WINDOW = 0x08000000

_MPV_DEV = r"^mpv-dev-x86_64-(?P<v>\d{8})-git-[" + string.hexdigits + r"]+\.7z$"       # «mpv-dev-x86_64-20251105-git-abc1234.7z»
PROVIDERS = (
    {"id": "shinchiro", "label": "shinchiro/mpv-winbuild-cmake (GitHub)", "repo": "shinchiro/mpv-winbuild-cmake", "tag": None,
     "asset": _MPV_DEV},
    {"id": "zhongfly", "label": "zhongfly/mpv-winbuild (GitHub)", "repo": "zhongfly/mpv-winbuild", "tag": None,
     "asset": _MPV_DEV},
)


def app_dir():
    """Carpeta del programa (el código): desde ahí se lanza la prueba de libmpv en otro proceso."""
    return paths.app_dir()


def own_dir():
    return os.path.join(paths.components_dir(), "mpv")


def dll_path(base=None):
    return os.path.join(base or own_dir(), DLL)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def installed(base=None):
    """La libmpv propia vigente: {"version", "tag", …} o None si no hay."""
    base = base or own_dir()
    if not os.path.isfile(dll_path(base)):
        return None
    return _read_json(os.path.join(base, VERSION_FILE)) or {"version": ""}


def pending(base=None):
    """La libmpv ya preparada que espera al próximo arranque, o None."""
    d = os.path.join(base or own_dir(), "_pending")
    if not os.path.isfile(dll_path(d)):
        return None
    return _read_json(os.path.join(d, VERSION_FILE)) or {"version": ""}


def previous(base=None):
    d = os.path.join(base or own_dir(), "_previous")
    if not os.path.isfile(dll_path(d)):
        return None
    return _read_json(os.path.join(d, VERSION_FILE)) or {"version": ""}


def boot_state(base=None):
    return _read_json(os.path.join(base or own_dir(), BOOT_FILE))


# ---------------------------------------------------------------------------------------------------- consulta
def latest(net=None, providers=PROVIDERS):
    """La build más nueva publicada por el primer repositorio que responda bien; FfmpegError con todos los motivos si ninguno."""
    net, errors, kinds = net or Net(), [], []
    for prov in providers:
        try:
            return _from_github(net, prov)
        except FfmpegError as e:
            errors.append(f"{prov['label']}: {e}")
            kinds.append(isinstance(e, NetworkError))
    cls = NetworkError if kinds and all(kinds) else FfmpegError
    raise cls("no se pudo consultar libmpv · " + " · ".join(errors))


# ---------------------------------------------------------------------------------------------------- prueba en otro proceso
def check_command(folder, dest):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mpv-check", folder, dest]
    return [sys.executable, "-E", "-m", "core.mpv_check", folder, dest]


def check_folder(folder, timeout=CHECK_TIMEOUT):
    """Prueba la libmpv de `folder` con el python-mpv del programa, en un proceso aparte. Devuelve lo que vio
    ({"version", "dll", "render", …}) o lanza VerifyError explicando qué falló."""
    fd, dest = tempfile.mkstemp(prefix="tvmpv_", suffix=".json")
    os.close(fd)
    try:
        cmd = check_command(folder, dest)
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=app_dir(),
                               **with_low_priority({"creationflags": NO_WINDOW if os.name == "nt" else 0}))
        except subprocess.TimeoutExpired as e:
            raise VerifyError("la prueba de la libmpv nueva tardó demasiado y se canceló") from e
        except OSError as e:
            raise VerifyError(f"no se pudo ejecutar la prueba de la libmpv nueva ({e})") from e
        data = _read_json(dest)
        if not data:
            tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [f"código {r.returncode}"]
            raise VerifyError(f"la libmpv nueva hizo caer la prueba ({tail[0][:200]})")
        if not data.get("ok"):
            raise VerifyError(f"la libmpv nueva no es compatible con el python-mpv del programa: {data.get('error', '')[:300]}")
        loaded = str(data.get("dll", ""))
        if loaded and os.path.dirname(loaded):                     # con ruta completa se puede comprobar que sea LA dll de `folder`
            if os.path.normcase(os.path.realpath(os.path.dirname(loaded))) != os.path.normcase(os.path.realpath(folder)):
                raise VerifyError(f"python-mpv cargó otra libmpv ({loaded}) en vez de la nueva: no se puede usar")
        if not data.get("render"):
            raise VerifyError("tu python-mpv no trae la API de render que usa el fundido cruzado: actualiza con «pip install -U python-mpv»")
        return data
    finally:
        try:
            os.remove(dest)
        except OSError:
            pass


# ---------------------------------------------------------------------------------------------------- preparar
def _wanted(member):
    n = member.replace("\\", "/")
    parts = [p for p in n.split("/") if p]
    if ".." in parts or len(parts) > 2:
        return ""
    base = parts[-1].lower() if parts else ""
    if base in ALIASES:
        return DLL
    if base in ("copyright", "license.bsd", "license.gpl", "license.lgpl") and len(parts) <= 2:
        return LICENSE_FILE if base == "copyright" else ""
    return ""


def stage(release, net=None, progress=None, cancel=None, base=None, validate=None, tools=None):
    """Descarga `release`, verifica su SHA-256, extrae la dll, la prueba en otro proceso y la deja preparada en `_pending/`
    (se aplica al próximo arranque). Lo vigente no se toca. Devuelve la info guardada en `_pending/version.json`."""
    net, base, validate = net or Net(), base or own_dir(), validate or check_folder
    os.makedirs(base, exist_ok=True)
    tmp = os.path.join(base, "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    new, pkg = os.path.join(tmp, "new"), os.path.join(tmp, "paquete.part")
    os.makedirs(new)
    try:
        free = shutil.disk_usage(base).free
        need = int((release.size or 60_000_000) * 3.5 + 300_000_000)
        if free < need:
            raise FfmpegError(f"falta espacio en el disco: hacen falta ~{need / 1e6:.0f} MB y hay {free / 1e6:.0f} MB libres")
        got, _n = net.download(release.url, pkg, progress, cancel)
        if progress:
            progress("verify", 0, 0)
        if got.lower() != release.sha256.lower():
            raise VerifyError("la suma SHA-256 del paquete no coincide con la publicada: se descartó la descarga")
        if progress:
            progress("extract", 0, 0)
        taken = {}

        def pick(member):
            name = _wanted(member)
            if not name:
                return None
            if taken.get(member) != name and name in taken.values():          # (otro miembro ya se llevó ese nombre)
                return None
            taken[member] = name                                              # (el MISMO miembro puede pedirse otra vez: si el lector propio falla a medias,
            return os.path.join(new, name)                                    #  el extractor alternativo tiene que poder entregarlo)

        pick.reset = taken.clear

        try:
            archive.extract_wanted(pkg, pick, None, cancel, MAX_UNPACKED, tools)
        except archive.ArchiveCancelled as e:
            from .ffmpeg_tools import Cancelled
            raise Cancelled(str(e)) from e
        except archive.ArchiveError as e:
            raise VerifyError(str(e)) from e
        dll = dll_path(new)
        if not os.path.isfile(dll):
            raise VerifyError("el paquete no trae libmpv-2.dll")
        if os.path.getsize(dll) < MIN_DLL_BYTES:
            raise VerifyError("la libmpv-2.dll del paquete es demasiado pequeña: no parece una libmpv completa")
        if progress:
            progress("check", 0, 0)
        seen = validate(new)
        info = {"version": release.version, "tag": release.tag, "provider": release.provider, "package": release.name,
                "sha256": release.sha256, "mpv": str((seen or {}).get("version", "")), "python_mpv": str((seen or {}).get("python_mpv", "")),
                "staged": time.strftime("%Y-%m-%d %H:%M:%S")}
        _write_json(os.path.join(new, VERSION_FILE), info)
        dst = os.path.join(base, "_pending")
        shutil.rmtree(dst, ignore_errors=True)
        try:
            os.replace(new, dst)
        except OSError as e:
            raise InUseError(f"no se pudo dejar la libmpv preparada ({e})") from e
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def stage_local(path, progress=None, base=None, validate=None):
    """«Usar un archivo que ya descargué»: `path` es una libmpv (libmpv-2.dll / mpv-2.dll) o la carpeta que la contiene. La copia, la prueba en otro
    proceso igual que una descargada y la deja preparada en `_pending/` (sin sumas publicadas que comparar, la prueba de arranque es la garantía)."""
    validate, base = validate or check_folder, base or own_dir()
    src = path
    if os.path.isdir(path):
        src = next((os.path.join(path, n) for n in ALIASES if os.path.isfile(os.path.join(path, n))), "")
    if not src or os.path.basename(src).lower() not in ALIASES:
        raise VerifyError("el archivo elegido no es una libmpv (debe llamarse libmpv-2.dll o mpv-2.dll)")
    if os.path.getsize(src) < MIN_DLL_BYTES:
        raise VerifyError("ese archivo es demasiado pequeño: no parece una libmpv completa")
    os.makedirs(base, exist_ok=True)
    tmp = os.path.join(base, "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    new = os.path.join(tmp, "new")
    os.makedirs(new)
    try:
        if progress:
            progress("verify", 0, 0)
        dst_dll = dll_path(new)
        shutil.copyfile(src, dst_dll)
        sha = hashlib.sha256()
        with open(dst_dll, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha.update(chunk)
        if progress:
            progress("check", 0, 0)
        seen = validate(new)
        info = {"version": "manual", "tag": "manual", "provider": "manual", "package": os.path.basename(src), "sha256": sha.hexdigest(),
                "mpv": str((seen or {}).get("version", "")), "python_mpv": str((seen or {}).get("python_mpv", "")),
                "staged": time.strftime("%Y-%m-%d %H:%M:%S")}
        _write_json(os.path.join(new, VERSION_FILE), info)
        dst = os.path.join(base, "_pending")
        shutil.rmtree(dst, ignore_errors=True)
        try:
            os.replace(new, dst)
        except OSError as e:
            raise InUseError(f"no se pudo dejar la libmpv preparada ({e})") from e
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def discard_pending(base=None):
    shutil.rmtree(os.path.join(base or own_dir(), "_pending"), ignore_errors=True)


# ---------------------------------------------------------------------------------------------------- aplicar al iniciar
def _managed_files(folder):
    return [n for n in os.listdir(folder) if n in (DLL, VERSION_FILE, LICENSE_FILE)] if os.path.isdir(folder) else []


def _move_files(src, dst):
    os.makedirs(dst, exist_ok=True)
    for n in _managed_files(src):
        os.replace(os.path.join(src, n), os.path.join(dst, n))


def _clear_files(folder):
    for n in _managed_files(folder):
        try:
            os.remove(os.path.join(folder, n))
        except OSError:
            pass


def revert(base=None):
    """Vuelve a la libmpv anterior (si hay) y descarta la vigente. Devuelve la info de la que queda o None si no había anterior
    (entonces el programa usa la libmpv que ya tuviera fuera de la carpeta propia)."""
    base = base or own_dir()
    prev = os.path.join(base, "_previous")
    had = previous(base)
    _clear_files(base)
    if had is not None:
        _move_files(prev, base)
    shutil.rmtree(prev, ignore_errors=True)
    return had


def apply_pending(base=None, validate=None):
    """Para el arranque, antes de importar mpv. Devuelve una lista de eventos [(nivel, texto, datos)] ("INFO"/"ERROR") para el registro.
    Nunca lanza una excepción."""
    base, validate = base or own_dir(), validate or check_folder
    events = []
    try:
        boot = boot_state(base)
        if boot and boot.get("state") == "trying":                  # el arranque anterior con la libmpv nueva no terminó
            ver = boot.get("version", "")
            back = revert(base)
            _clear_boot(base)
            events.append(("ERROR", f"libmpv {ver or 'nueva'} no logró terminar de iniciar el programa: se volvió a "
                                    f"{'la versión ' + str(back.get('version') or '') if back else 'la libmpv anterior'}",
                           {"failed_version": ver, "tag": boot.get("tag", "")}))
        pend = pending(base)
        if pend is None:
            return events
        cur = installed(base)
        try:
            _swap_in(base, cur)
        except OSError as e:
            shutil.rmtree(os.path.join(base, "_pending"), ignore_errors=True)       # se descarta: hay que volver a prepararla
            events.append(("ERROR", f"no se pudo poner la libmpv {pend.get('version', '')} nueva ({e}); sigue la anterior y la nueva se descartó",
                           {"failed_version": pend.get("version", ""), "tag": pend.get("tag", "")}))
            return events
        shutil.rmtree(os.path.join(base, "_pending"), ignore_errors=True)
        try:
            seen = validate(base)
        except FfmpegError as e:
            back = revert(base)
            events.append(("ERROR", f"la libmpv {pend.get('version', '')} nueva no funciona aquí ({e}); se volvió a la anterior",
                           {"failed_version": pend.get("version", ""), "tag": pend.get("tag", "")}))
            return events
        _write_json(os.path.join(base, BOOT_FILE), {"state": "trying", "version": pend.get("version", ""), "tag": pend.get("tag", ""),
                                                    "previous": (cur or {}).get("version", ""), "t": time.time()})
        if seen and seen.get("version") and not pend.get("mpv"):
            pend["mpv"] = seen["version"]
            _write_json(os.path.join(base, VERSION_FILE), pend)
        events.append(("INFO", f"libmpv actualizada a la versión {pend.get('version', '')}"
                               f"{' (' + str(pend.get('mpv')) + ')' if pend.get('mpv') else ''}", {"applied": pend.get("version", "")}))
    except Exception as e:                                                   # noqa: BLE001: el arranque nunca debe caerse por esto
        events.append(("ERROR", f"no se pudo aplicar la libmpv preparada ({type(e).__name__}: {e})", {}))
    return events


def _swap_in(base, cur):
    """Pone la libmpv de `_pending/` en `base`: antes copia la vigente a `_previous/`, y cada archivo nuevo se copia a un temporal y
    reemplaza al vigente de un solo paso. Si algo falla (p. ej. el archivo está en uso) lo vigente se restaura desde `_previous/`
    y se vuelve a lanzar el OSError. `_pending/` no se toca (quien llama decide qué hacer con ella)."""
    prev, pend = os.path.join(base, "_previous"), os.path.join(base, "_pending")
    shutil.rmtree(prev, ignore_errors=True)
    if cur is not None:
        os.makedirs(prev)
        for n in _managed_files(base):
            shutil.copy2(os.path.join(base, n), os.path.join(prev, n))
    placed = []
    try:
        for n in _managed_files(pend):
            tmp = os.path.join(base, n + ".new")
            shutil.copy2(os.path.join(pend, n), tmp)
            os.replace(tmp, os.path.join(base, n))
            placed.append(n)
    except OSError:
        for n in os.listdir(base):
            if n.endswith(".new"):
                try:
                    os.remove(os.path.join(base, n))
                except OSError:
                    pass
        for n in placed:                                               # lo ya puesto vuelve a como estaba
            try:
                if cur is not None and os.path.isfile(os.path.join(prev, n)):
                    shutil.copy2(os.path.join(prev, n), os.path.join(base, n))
                else:
                    os.remove(os.path.join(base, n))
            except OSError:
                pass
        shutil.rmtree(prev, ignore_errors=True)
        raise


def _clear_boot(base):
    try:
        os.remove(os.path.join(base, BOOT_FILE))
    except OSError:
        pass


def confirm_boot(base=None):
    """El programa terminó de arrancar con la libmpv vigente: la marca de «probando» se borra. Devuelve la info de la marca si había."""
    base = base or own_dir()
    boot = boot_state(base)
    _clear_boot(base)
    return boot


def remove(base=None):
    """Borra la libmpv propia (y la preparada y la anterior): el programa vuelve a usar la que ya tuviera fuera de esta carpeta."""
    base = base or own_dir()
    for d in ("_pending", "_previous", "_tmp"):
        shutil.rmtree(os.path.join(base, d), ignore_errors=True)
    _clear_files(base)
    _clear_boot(base)
    try:
        os.rmdir(base)
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------------- aviso al iniciar
def pending_update(cfg_mpv, net=None, base=None, now=None):
    """Para el arranque: (Release, instalado) si hay una libmpv más nueva que avisar, o None. Solo si la libmpv propia ya está instalada
    (si usas otra, no se compara). Anota la hora de la consulta en `cfg_mpv["last_check"]`. Los fallos de red se ignoran."""
    inst = installed(base)
    if inst is None or not due(cfg_mpv, now):
        return None
    cfg_mpv["last_check"] = time.time() if now is None else now
    try:
        rel = latest(net)
    except FfmpegError:
        return None
    pend = pending(base)
    if cfg_mpv.get("skip_version") == rel.tag or (pend and pend.get("tag") == rel.tag) or not is_newer(rel, inst):
        return None
    return rel, inst
