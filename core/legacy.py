# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repositorios Legacy: la copia de seguridad de ffmpeg y libmpv que funcionaban con esta versión de ViDeck Pulse.

ffmpeg y libmpv se pueden actualizar solos (core/ffmpeg_tools.py, core/mpv_tools.py). Si una versión nueva falla, «Legacy Mode» vuelve a
las que funcionaban:
  · `legacy/legacy_repos.7z` guarda los archivos (ffmpeg, ffprobe y la libmpv-2.dll que se usaban cuando esta versión del programa arrancó
    bien) y `legacy/legacy.json` su descripción (versión del programa, versiones, SHA-256 de cada archivo y del .7z);
  · con «Legacy Mode» activado, al iniciar se extraen (verificadas) a `legacy/active/` y el programa usa ESAS en vez de las actualizadas,
    sin tocar las actualizadas: al desactivarlo vuelven a usarse;
  · cada versión del programa tiene su propio paquete: el primer arranque correcto de una versión nueva lo reemplaza.
Sin Qt. Los archivos se comprimen con core/sevenzip.write_7z.
"""
import hashlib
import json
import os
import shutil
import time

from . import archive, paths, sevenzip, version

ARCHIVE_NAME = "legacy_repos.7z"
MANIFEST_NAME = "legacy.json"
READY = ".ready"
MAX_UNPACKED = 2_000_000_000


class LegacyError(Exception):
    pass


def app_dir():
    return paths.components_dir()


def legacy_dir():
    return os.path.join(app_dir(), "legacy")


def archive_path(base=None):
    return os.path.join(base or legacy_dir(), ARCHIVE_NAME)


def active_dir(base=None):
    return os.path.join(base or legacy_dir(), "active")


def app_tag():
    """Identifica la versión del programa a la que pertenece el paquete («v2.0 Beta 3 · update 10»; la estable, «v2.00 estable · u1»)."""
    if version.STABLE:
        return f"v{version.STABLE_MAJOR_MINOR} estable · {version.u_tag()}"
    return f"v{version.MAJOR_MINOR} {version.stage()} · update {version.UPDATES}"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(base=None):
    try:
        with open(os.path.join(base or legacy_dir(), MANIFEST_NAME), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("items"), dict) else None
    except (OSError, ValueError):
        return None


def available(base=None):
    """¿Hay un paquete Legacy (el .7z y su descripción)?"""
    return os.path.isfile(archive_path(base)) and manifest(base) is not None


def items(base=None):
    """{"ffmpeg": {"version", "files"}, "mpv": {…}} de lo que guarda el paquete (vacío si no hay)."""
    m = manifest(base)
    return dict(m["items"]) if m else {}


def is_current(app=None, base=None):
    """¿El paquete es de esta versión del programa?"""
    m = manifest(base)
    return bool(m) and m.get("app") == (app or app_tag())


def missing(have, base=None):
    """Componentes de `have` (nombres) que el paquete no guarda."""
    got = items(base)
    return [c for c in have if c not in got]


def build(sources, progress=None, cancel=None, base=None, app=None, note=""):
    """Crea (o reemplaza) el paquete. `sources` = {"ffmpeg": {"version": …, "files": [rutas]}, "mpv": {…}} (los componentes sin archivos
    se omiten). Escribe el .7z y, recién después, la descripción: si algo falla, el paquete anterior sigue intacto."""
    base = base or legacy_dir()
    os.makedirs(base, exist_ok=True)
    files, desc = [], {}
    for comp, src in sources.items():
        paths = [p for p in (src.get("files") or []) if p and os.path.isfile(p)]
        if not paths:
            continue
        names = {}
        for p in paths:
            names.setdefault(os.path.basename(p), p)
        desc[comp] = {"version": str(src.get("version", "")), "origin": str(src.get("origin", "")), "files": {}}
        for n, p in names.items():
            files.append((f"{comp}/{n}", p))
            desc[comp]["files"][n] = {"size": os.path.getsize(p), "sha256": sha256_of(p)}
    if not files:
        raise LegacyError("no hay ffmpeg ni libmpv que guardar")
    need = sum(os.path.getsize(p) for _n, p in files)
    free = shutil.disk_usage(base).free
    if free < need:
        raise LegacyError(f"falta espacio en el disco para el paquete Legacy: hacen falta ~{need / 1e6:.0f} MB y hay {free / 1e6:.0f} MB libres")
    dest = archive_path(base)
    try:
        sevenzip.write_7z(dest, files, progress, cancel)
    except sevenzip.Cancelled as e:
        raise LegacyError("cancelado") from e
    except (OSError, sevenzip.SevenZipError) as e:
        raise LegacyError(f"no se pudo crear el paquete Legacy ({e})") from e
    m = {"app": app or app_tag(), "created": time.strftime("%Y-%m-%d %H:%M:%S"), "archive_sha256": sha256_of(dest),
         "archive_size": os.path.getsize(dest), "items": desc, "note": note}
    tmp = os.path.join(base, MANIFEST_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(base, MANIFEST_NAME))
    shutil.rmtree(active_dir(base), ignore_errors=True)               # lo extraído era del paquete anterior
    return m


def extract(progress=None, cancel=None, base=None):
    """Deja los archivos del paquete, verificados, en `legacy/active/<componente>/`. Si ya estaban (y siguen intactos) no vuelve a
    extraer. Devuelve la carpeta `active/`. Lanza LegacyError si el paquete falta o está dañado."""
    base = base or legacy_dir()
    m = manifest(base)
    if m is None or not os.path.isfile(archive_path(base)):
        raise LegacyError("no hay paquete Legacy guardado")
    act = active_dir(base)
    if _intact(act, m):
        return act
    if sha256_of(archive_path(base)) != m.get("archive_sha256"):
        raise LegacyError("el paquete Legacy está dañado (su suma SHA-256 no coincide)")
    tmp = os.path.join(base, "active.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        wanted = {f"{c}/{n}" for c, it in m["items"].items() for n in it.get("files", {})}

        def pick(member):
            name = member.replace("\\", "/")
            return os.path.join(tmp, *name.split("/")) if name in wanted else None

        try:
            got = archive.extract_wanted(archive_path(base), pick, progress, cancel, MAX_UNPACKED)
        except archive.ArchiveCancelled as e:
            raise LegacyError("cancelado") from e
        except archive.ArchiveError as e:
            raise LegacyError(str(e)) from e
        for c, it in m["items"].items():
            for n, meta in it.get("files", {}).items():
                p = os.path.join(tmp, c, n)
                if not os.path.isfile(p) or os.path.getsize(p) != meta.get("size") or sha256_of(p) != meta.get("sha256"):
                    raise LegacyError(f"«{c}/{n}» del paquete Legacy no coincide con lo guardado")
                if os.name != "nt" and n.lower() in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"):
                    os.chmod(p, 0o755)
        with open(os.path.join(tmp, READY), "w", encoding="utf-8") as f:
            f.write(m.get("archive_sha256", ""))
        shutil.rmtree(act, ignore_errors=True)
        os.replace(tmp, act)
        return act
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _intact(act, m):
    """¿`active/` es la extracción de ESTE paquete y sigue completa (existen los archivos con el tamaño guardado)?"""
    try:
        with open(os.path.join(act, READY), encoding="utf-8") as f:
            if f.read().strip() != m.get("archive_sha256"):
                return False
    except OSError:
        return False
    for c, it in m["items"].items():
        for n, meta in it.get("files", {}).items():
            p = os.path.join(act, c, n)
            if not os.path.isfile(p) or os.path.getsize(p) != meta.get("size"):
                return False
    return True


def component_dir(comp, base=None):
    """Carpeta con los archivos extraídos de un componente («ffmpeg» o «mpv») o None si no hay."""
    d = os.path.join(active_dir(base), comp)
    return d if os.path.isdir(d) else None


def remove(base=None):
    shutil.rmtree(active_dir(base), ignore_errors=True)
    for n in (ARCHIVE_NAME, MANIFEST_NAME):
        try:
            os.remove(os.path.join(base or legacy_dir(), n))
        except OSError:
            pass
