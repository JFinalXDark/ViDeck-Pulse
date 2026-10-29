# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Papelera de Windows (update 13): mandar un archivo a la Papelera de SU unidad, esté donde esté el programa.

La Papelera es POR UNIDAD: depende de dónde está el VIDEO, no de dónde está instalado el programa (un video en D: va a la Papelera de D:,
aunque ViDeck Pulse esté en C:). Las unidades de red y las extraíbles normalmente NO tienen Papelera (ahí Windows borra de verdad).

Nada se borra en silencio: `recycle` usa `SHFileOperationW` con FOF_ALLOWUNDO y FOF_WANTNUKEWARNING (si Windows fuera a borrar de forma
definitiva en vez de reciclar, avisa y se puede decir que no) y después comprueba que el archivo ya no esté. `drive_kind` dice de antemano
qué clase de unidad es para que la ventana pregunte «eliminar definitivamente o conservar» cuando no hay Papelera. Fuera de Windows no hay
Papelera (solo existen para las pruebas, donde se sustituyen estas funciones).
"""
import ntpath
import os
import sys

# GetDriveTypeW
DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_CDROM, DRIVE_RAMDISK = 2, 3, 4, 5, 6

FO_DELETE = 0x0003
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI, FOF_WANTNUKEWARNING = 0x0004, 0x0010, 0x0040, 0x0400, 0x4000
FLAGS = FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI | FOF_WANTNUKEWARNING



def _drive_root(path):
    """«Z:\\» de «Z:\\videos\\a.mp4» o «\\\\servidor\\recurso\\» de una ruta de red; '' si no tiene."""
    p = os.path.abspath(path) if os.name == "nt" else str(path)
    drive, _ = ntpath.splitdrive(p)                                    # (rutas de Windows: «Z:\\…» o «\\\\servidor\\recurso\\…»)
    if not drive:
        return ""
    return drive + "\\"


def _get_drive_type(root):
    import ctypes
    return int(ctypes.windll.kernel32.GetDriveTypeW(root))


def drive_kind(path, get_type=None):
    """Clase de unidad donde está `path`: «fixed» (disco fijo: tiene Papelera), «removable», «network», «other» o «unknown» (no se pudo saber:
    se trata como sin Papelera). `get_type(raíz)` se puede sustituir (pruebas)."""
    root = _drive_root(path)
    if not root:
        return "unknown"
    if root.startswith("\\\\"):
        return "network"
    try:
        t = (get_type or _get_drive_type)(root)
    except (AttributeError, OSError, ValueError):                       # fuera de Windows no existe ctypes.windll
        return "unknown"
    return {DRIVE_FIXED: "fixed", DRIVE_REMOVABLE: "removable", DRIVE_REMOTE: "network"}.get(t, "other" if t in (DRIVE_CDROM, DRIVE_RAMDISK) else "unknown")


def has_recycle_bin(path, get_type=None):
    """¿Es de esperar que la unidad de `path` tenga Papelera? Solo los discos fijos."""
    return drive_kind(path, get_type) == "fixed"


def recycle(path):
    """Manda `path` a la Papelera. Devuelve (ok, mensaje): ok=True solo si el archivo ya no está. Si Windows no lo recicla (o el usuario
    responde «No» al aviso de borrado definitivo) devuelve (False, motivo) y el archivo queda donde estaba."""
    if sys.platform != "win32":
        return False, "la Papelera de Windows no está disponible en este sistema"
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
                        ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                        ("lpszProgressTitle", wintypes.LPCWSTR)]

        full = os.path.abspath(path).replace("/", "\\")
        buf = ctypes.create_unicode_buffer(full + "\0")                  # la lista de archivos termina con DOS caracteres nulos
        op = SHFILEOPSTRUCTW()
        op.hwnd, op.wFunc, op.pFrom, op.pTo, op.fFlags = None, FO_DELETE, ctypes.addressof(buf), None, FLAGS
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        aborted = bool(op.fAnyOperationsAborted)
    except (AttributeError, OSError, ValueError) as e:
        return False, f"{type(e).__name__}: {e}"
    if os.path.exists(path):
        return False, "se canceló" if aborted else f"Windows no pudo enviarlo a la Papelera (código {rc})"
    return True, ""


def delete_forever(path):
    """Borrado definitivo (sin Papelera). Devuelve (ok, mensaje)."""
    try:
        os.remove(path)
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    return (not os.path.exists(path)), ""
