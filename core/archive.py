# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Abrir los paquetes que se descargan (zip y 7z) sacando SOLO los archivos que el programa pide.

`extract_wanted(paquete, pick, ...)`: `pick(nombre_dentro_del_paquete)` devuelve la ruta de destino o None (se salta). Nunca se escribe
fuera de lo que `pick` devuelve (no se confía en las rutas del paquete). Para .7z se usa el lector propio (core/sevenzip.py); si no
puede con el archivo (o falla su comprobación de CRC) se prueba, en este orden, py7zr (si está instalado), el «tar» de Windows
(bsdtar: abre 7z) y 7z/7za/7zr si el usuario los tiene. Con los extractores externos se extrae a una carpeta temporal aparte y de
ahí se toman solo los archivos pedidos (sin seguir enlaces simbólicos).
"""
import os
import shutil
import subprocess
import tempfile
import zipfile

from . import sevenzip
from .lowprio import with_low_priority

NO_WINDOW = 0x08000000


class ArchiveError(Exception):
    pass


class ArchiveCancelled(ArchiveError):
    pass


def kind(path):
    if zipfile.is_zipfile(path):
        return "zip"
    if sevenzip.is_7z(path):
        return "7z"
    return ""


def _flags():
    return NO_WINDOW if os.name == "nt" else 0


def _extract_zip(path, pick, progress, cancel, max_total):
    out, total = {}, 0
    with zipfile.ZipFile(path) as z:
        wanted = []
        for i in z.infolist():
            if i.is_dir():
                continue
            dest = pick(i.filename)
            if dest:
                wanted.append((i, dest))
                total += i.file_size
        if max_total is not None and total > max_total:
            raise ArchiveError("los archivos pedidos son demasiado grandes")
        done = 0
        for i, dest in wanted:
            if cancel is not None and cancel.is_set():
                raise ArchiveCancelled("cancelado")
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with z.open(i) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            done += i.file_size
            if progress:
                progress(done, total)
            out[i.filename] = dest
    return out


def _walk_regular(root):
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(base, d))]
        for f in files:
            p = os.path.join(base, f)
            if os.path.isfile(p) and not os.path.islink(p):
                yield p, os.path.relpath(p, root).replace(os.sep, "/")


def _pick_from_tree(tmp, pick, out):
    for p, rel in _walk_regular(tmp):
        dest = pick(rel)
        if dest:
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            shutil.copyfile(p, dest)
            out[rel] = dest


def _run(cmd, timeout=1800):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, **with_low_priority({"creationflags": _flags()}))


def _external_tools():
    """[(nombre, función(paquete, carpeta))] de los extractores que parecen disponibles."""
    tools = []
    try:
        import py7zr                                                   # noqa: F401

        def with_py7zr(pkg, folder):
            import py7zr as p
            with p.SevenZipFile(pkg) as z:
                z.extractall(folder)
        tools.append(("py7zr", with_py7zr))
    except Exception:                                                  # noqa: BLE001: no está instalado o no se puede importar
        pass
    tar = shutil.which("tar")
    if tar:
        def with_tar(pkg, folder):
            r = _run([tar, "-xf", pkg, "-C", folder])
            if r.returncode != 0:
                raise ArchiveError(r.stderr.decode("utf-8", "replace").strip()[:300] or f"tar terminó con {r.returncode}")
        tools.append(("tar", with_tar))
    for exe in ("7z", "7za", "7zr"):
        found = shutil.which(exe)
        if found:
            def with_7z(pkg, folder, found=found):
                r = _run([found, "x", "-y", f"-o{folder}", pkg])
                if r.returncode != 0:
                    raise ArchiveError(r.stdout.decode("utf-8", "replace").strip()[-300:] or f"7z terminó con {r.returncode}")
            tools.append((exe, with_7z))
    return tools


def _extract_7z(path, pick, progress, cancel, max_total, tools=None):
    reasons = []
    try:
        with sevenzip.SevenZip(path) as z:
            return z.extract(pick, progress, cancel, max_total)
    except sevenzip.Cancelled as e:
        raise ArchiveCancelled(str(e)) from e
    except sevenzip.SevenZipError as e:
        reasons.append(f"lector propio: {e}")
    except OSError as e:
        reasons.append(f"lector propio: {e}")
    for name, fn in (_external_tools() if tools is None else tools):
        if cancel is not None and cancel.is_set():
            raise ArchiveCancelled("cancelado")
        getattr(pick, "reset", lambda: None)()                       # (lo que el intento anterior alcanzó a pedir no cuenta: cada extractor parte de cero)
        tmp = tempfile.mkdtemp(prefix="tv7z_", dir=os.path.dirname(os.path.abspath(path)))
        try:
            fn(path, tmp)
            out = {}
            _pick_from_tree(tmp, pick, out)
            if out:
                return out
            reasons.append(f"{name}: no encontró los archivos pedidos")
        except (ArchiveError, OSError, subprocess.SubprocessError, Exception) as e:       # noqa: BLE001: se prueba el siguiente
            reasons.append(f"{name}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    raise ArchiveError("no se pudo abrir el paquete .7z (" + " · ".join(reasons) + ")")


def extract_wanted(path, pick, progress=None, cancel=None, max_total=None, tools=None):
    k = kind(path)
    if k == "zip":
        return _extract_zip(path, pick, progress, cancel, max_total)
    if k == "7z":
        return _extract_7z(path, pick, progress, cancel, max_total, tools)
    raise ArchiveError("el paquete descargado no es un .zip ni un .7z")
