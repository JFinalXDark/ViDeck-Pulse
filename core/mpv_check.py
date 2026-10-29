# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Comprobación de una libmpv en un proceso aparte: «¿esta libmpv arranca con el python-mpv de ViDeck Pulse?».

Se ejecuta como `python -E -m core.mpv_check <carpeta> <salida.json>` (o, en una versión compilada, `programa --mpv-check <carpeta> <salida.json>`)
para no cargar la libmpv nueva dentro del programa en marcha: si estuviera mal, solo se cae este proceso auxiliar. Escribe un JSON con
lo que vio: la ruta de la libmpv que cargó python-mpv, la versión de mpv, si trae la API de render (fundido cruzado con OpenGL)…
"""
import json
import os
import sys


def loaded_path(lib):
    """Ruta real de la libmpv que quedó cargada (en Windows se pregunta al sistema; en otros, el nombre con que se abrió)."""
    name = str(getattr(lib, "_name", "") or "")
    if os.name != "nt":
        return name
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        k32.GetModuleFileNameW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(2048)
        return buf.value if k32.GetModuleFileNameW(lib._handle, buf, 2048) else name
    except Exception:                                    # noqa: BLE001: si no se puede preguntar, vale el nombre
        return name


def run(folder):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.components import activate_paths
    activate_paths(folder)
    out = {"folder": folder}
    import mpv
    lib = getattr(mpv, "backend", None)
    out["dll"] = loaded_path(lib) if lib is not None else str(getattr(mpv, "sofile", "") or "")
    out["render"] = bool(hasattr(mpv, "MpvRenderContext") and hasattr(mpv, "MpvGlGetProcAddressFn"))
    try:
        from importlib.metadata import version
        out["python_mpv"] = version("python-mpv")
    except Exception:                                    # noqa: BLE001: solo informativo
        out["python_mpv"] = ""
    m = mpv.MPV(vo="null", ao="null", idle=True, input_default_bindings=False, input_vo_keyboard=False)
    try:
        # (OJO: en python-mpv `m["x"]` lee OPCIONES, no propiedades: «mpv-version» y «ffmpeg-version» son propiedades → atributo con guion bajo)
        out["version"] = str(m.mpv_version)
        try:
            out["ffmpeg"] = str(m.ffmpeg_version)
        except Exception:                                # noqa: BLE001: no todas las versiones lo tienen
            out["ffmpeg"] = ""
    finally:
        m.terminate()
    return out


def main(argv):
    folder, dest = argv[-2], argv[-1]
    try:
        data = run(folder)
        data["ok"] = True
    except BaseException as e:                           # noqa: BLE001: se informa tal cual al proceso que llamó
        data = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
