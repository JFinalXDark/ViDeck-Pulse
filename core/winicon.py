# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Icono de ViDeck Pulse en Windows para lo que Qt no dibuja: la ventana propia de mpv («Ventana propia de mpv») y la
barra de tareas. No depende de Qt; fuera de Windows no hace nada."""
import os
import sys
import threading
import time
from pathlib import Path

from . import devlog as dl

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ICON_PNG, ICON_ICO = ASSETS / "icon.png", ASSETS / "icon.ico"
APP_ID = "ViDeckPulse.App"

WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
SM_CXICON, SM_CYICON, SM_CXSMICON, SM_CYSMICON = 11, 12, 49, 50


def _user32():
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.FindWindowW.argtypes, u.FindWindowW.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.HWND
    u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.LoadImageW.restype = wintypes.HANDLE
    u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SendMessageW.restype = ctypes.c_ssize_t
    return u


def set_taskbar_identity():
    """Que Windows agrupe las ventanas bajo «ViDeck Pulse» con su icono (y no bajo el de python.exe)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def apply_icon(hwnd, ico=None, u=None):
    """Pone el icono (.ico) a la ventana `hwnd` de este proceso: grande (Alt+Tab) y pequeño (barra de título y de tareas)."""
    ico = str(ico or ICON_ICO)
    if not os.path.exists(ico):
        return False
    u = u or _user32()
    done = False
    for kind, (cx, cy) in ((ICON_BIG, (SM_CXICON, SM_CYICON)), (ICON_SMALL, (SM_CXSMICON, SM_CYSMICON))):
        hicon = u.LoadImageW(None, ico, IMAGE_ICON, u.GetSystemMetrics(cx), u.GetSystemMetrics(cy), LR_LOADFROMFILE)
        if hicon:
            u.SendMessageW(hwnd, WM_SETICON, kind, hicon)
            done = True
    return done


def watch_mpv_window(title, ico=None, seconds=40.0, step=0.5, u=None, sleep=time.sleep):
    """mpv crea su propia ventana (clase «mpv») cuando le conviene: se espera a que aparezca (y si la recrea, se vuelve a
    poner) durante `seconds` y se le pone el icono. Corre en un hilo; devuelve el hilo (None si no aplica)."""
    if sys.platform != "win32" and u is None:
        return None

    def run():
        try:
            uu = u or _user32()
            last, t0 = None, time.monotonic()
            while time.monotonic() - t0 < seconds:
                hwnd = uu.FindWindowW("mpv", title)
                if hwnd and hwnd != last and apply_icon(hwnd, ico, uu):
                    last = hwnd
                sleep(step)
        except Exception:
            dl.exception("icono", "no se pudo poner el icono a la ventana de mpv", level=dl.WARN)   # solo estético

    th = threading.Thread(target=run, name="icono-mpv", daemon=True)
    th.start()
    return th
