# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prueba de humo del ejecutable compilado (`ViDeckPulse.exe --smoke-test <archivo>`), pensada para la automatización de GitHub Actions.

Comprueba, sin abrir ninguna ventana, que el programa congelado trae todo lo que necesita: sus módulos, las bibliotecas de terceros y los
recursos (iconos). Escribe el resultado en el archivo indicado (el ejecutable es de ventanas: no tiene consola) y sale con código 0 si todo
está bien. Que libmpv no esté instalada es lo ESPERADO en un equipo limpio (la instala la ventana de primer inicio), así que no cuenta como fallo.
"""
import importlib
import os
import pkgutil
import sys

THIRD_PARTY = ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtOpenGLWidgets", "cv2", "numpy", "mss", "pytesseract", "mpv")
ASSETS = ("icon.png", "icon.ico")
MIN_MODULES = {"core": 25, "gui": 8}


def _is_missing_libmpv(exc):
    return isinstance(exc, OSError) and "mpv" in str(exc).lower()


def collect():
    """Devuelve (líneas del informe, cantidad de fallos)."""
    from core import paths, version
    lines, bad = [f"program: {version.label()}", f"frozen: {paths.frozen()}", f"python: {sys.version.split()[0]}"], 0
    res = paths.resource_dir()
    for name in ASSETS:
        ok = os.path.isfile(os.path.join(res, "assets", name))
        lines.append(f"{'OK  ' if ok else 'FAIL'} asset assets/{name}")
        bad += 0 if ok else 1
    for name in THIRD_PARTY:
        try:
            importlib.import_module(name)
            lines.append(f"OK   import {name}")
        except Exception as e:                                         # noqa: BLE001
            if name == "mpv" and _is_missing_libmpv(e):
                lines.append("OK   import mpv (libmpv is not installed yet: expected on a clean computer)")
            else:
                lines.append(f"FAIL import {name}: {type(e).__name__}: {e}")
                bad += 1
    for pkg_name in ("core", "gui"):
        pkg = importlib.import_module(pkg_name)
        infos = sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name)
        if len(infos) < MIN_MODULES[pkg_name]:                         # (si el ejecutable no deja listar sus módulos, la prueba no vale: que avise)
            lines.append(f"FAIL only {len(infos)} modules found in package {pkg_name} (expected at least {MIN_MODULES[pkg_name]})")
            bad += 1
        for info in infos:
            full = f"{pkg_name}.{info.name}"
            try:
                importlib.import_module(full)
                lines.append(f"OK   import {full}")
            except Exception as e:                                     # noqa: BLE001
                if _is_missing_libmpv(e):
                    lines.append(f"OK   import {full} (needs libmpv, not installed yet: expected)")
                else:
                    lines.append(f"FAIL import {full}: {type(e).__name__}: {e}")
                    bad += 1
    lines.append(f"RESULT: {'FAILURES: ' + str(bad) if bad else 'ALL OK'}")
    return lines, bad


def run(out_path):
    """Escribe el informe en `out_path` y devuelve el código de salida (0 = todo bien)."""
    try:
        lines, bad = collect()
    except Exception as e:                                             # noqa: BLE001
        lines, bad = [f"FAIL smoke test crashed: {type(e).__name__}: {e}"], 1
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 1 if bad else 0
