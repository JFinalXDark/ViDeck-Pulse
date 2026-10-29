# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tesseract OCR en ViDeck Pulse (update 14): dónde está, qué versión es y su instalación guiada. Sin Qt.

Tesseract NO se incluye con el programa ni con sus versiones publicadas. La ventana del primer inicio (core/firstrun.py) baja de la
Universität Mannheim el instalador de la versión PROBADA (tamaño y SHA-256 fijos en `TESTED`), comprueba su suma y lo ejecuta en silencio.
Comprobado en un Windows limpio (GitHub Actions): ese instalador (NSIS) siempre deja Tesseract en «C:\\Program Files\\Tesseract-OCR»
(ignora `/D=`) y pide permisos de administrador (`requestedExecutionLevel=highestAvailable`), por eso se lanza con `ShellExecuteExW` y el
verbo «runas» (Windows muestra su aviso de permisos). El programa ya busca Tesseract en esa carpeta (`gui`/`core.vision.TESSERACT_PATHS`).
"""
import json
import os
import re
import shutil
import subprocess
import time

from . import ffmpeg_tools as ft
from . import paths

HOST = "digi.bib.uni-mannheim.de"
BASE_URL = "https://" + HOST + "/tesseract/"
PAGE = "https://github.com/UB-Mannheim/tesseract/wiki"
NAME = "tesseract-ocr-w64-setup-5.4.0.20240606.exe"
# Versión probada: la que se bajó y se ejecutó en un Windows limpio (tamaño y SHA-256 medidos allí).
TESTED = ft.Release("ub-mannheim", "UB Mannheim (Universität Mannheim)", "5.4.0.20240606", "5.4.0.20240606", BASE_URL + NAME,
                    50_175_248, "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9", NAME)
INSTALLED_SIZE = 240_000_000           # lo que ocupa instalado (idiomas inglés y osd)
INSTALL_TIMEOUT = 900
CANDIDATES = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
              r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe", r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"]
ERROR_CANCELLED = 1223

if HOST not in ft.ALLOWED_HOSTS:
    ft.ALLOWED_HOSTS = tuple(ft.ALLOWED_HOSTS) + (HOST,)


class ElevationDenied(ft.FfmpegError):
    """El usuario no dio el permiso de administrador que pide el instalador de Tesseract."""


def own_dir():
    return os.path.join(paths.components_dir(), "tesseract")


def find(configured=""):
    """Ruta de tesseract.exe: la configurada, la del PATH o las carpetas habituales del instalador. '' si no está."""
    exe = "tesseract" + (".exe" if os.name == "nt" else "")
    cands = [configured, os.path.join(configured, exe) if configured else "", shutil.which("tesseract")] + [os.path.expandvars(p) for p in CANDIDATES]
    return next((c for c in cands if c and os.path.isfile(c)), "")


def version_of(path):
    """«5.4.0.20240606» de `tesseract --version` («tesseract v5.4.0.20240606») o '' si no arranca."""
    try:
        r = subprocess.run([path, "--version"], capture_output=True, timeout=20, creationflags=ft.NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (r.stdout + r.stderr).decode("utf-8", "replace")
    m = re.search(r"tesseract\s+v?(\d+(?:\.\d+)+\S*)", text, re.I)
    return m.group(1) if (m and r.returncode == 0) else ""


def installed(configured="", finder=None):
    """{"path", "version"} si hay un Tesseract que arranca, o None."""
    path = (finder or find)(configured)
    if not path:
        return None
    ver = version_of(path)
    return {"path": path, "version": ver} if ver else None


def vtuple(text):
    return tuple(int(x) for x in re.findall(r"\d+", str(text or ""))[:4])


# ---------------------------------------------------------------------------------------------------- ejecutar el instalador con permisos
def run_elevated(path, params, timeout=INSTALL_TIMEOUT, cancel=None):
    """Ejecuta `path params` pidiendo permisos de administrador (aviso de Windows) y espera a que termine. Devuelve el código de salida.
    ElevationDenied si el usuario dice que no; FfmpegError si no arranca o tarda más de `timeout` segundos. Solo en Windows. Una vez en
    marcha el instalador NO se interrumpe (se dejaría a medias): `cancel` solo vale antes de lanzarlo."""
    if os.name != "nt":
        raise ft.FfmpegError("el instalador de Tesseract solo se puede ejecutar en Windows")
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100                         # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb, info.lpFile, info.lpParameters, info.nShow = "runas", path, params, 0
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == ERROR_CANCELLED:
            raise ElevationDenied("no diste el permiso de administrador que pide el instalador de Tesseract")
        raise ft.FfmpegError(f"Windows no pudo abrir el instalador de Tesseract (error {err})")
    try:
        deadline = time.time() + timeout
        while True:
            r = kernel32.WaitForSingleObject(info.hProcess, 500)
            if r == 0:                                           # WAIT_OBJECT_0: terminó
                break
            if time.time() > deadline:
                raise ft.FfmpegError(f"el instalador de Tesseract tardó más de {timeout // 60} minutos")
        code = wintypes.DWORD(1)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def _setup_disk():
    pf = os.path.expandvars("%ProgramFiles%") if os.name == "nt" else ""
    return pf if pf and os.path.isdir(pf) else paths.components_dir()


def install(release=None, net=None, progress=None, cancel=None, base=None, runner=None, finder=None):
    """Baja el instalador de `release` (por defecto la versión probada), verifica su SHA-256, lo ejecuta en silencio y comprueba que
    Tesseract arranque. Devuelve {"version", "path", "sha256", "installed"}. Para correr en un hilo. `runner`/`finder` se sustituyen en las pruebas."""
    rel = release or TESTED
    net, base = net or ft.Net(), base or own_dir()
    tmp = os.path.join(base, "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    setup = os.path.join(tmp, rel.name)
    try:
        need_dl, need_inst = int((rel.size or 60_000_000) * 1.2), INSTALLED_SIZE + 100_000_000
        if shutil.disk_usage(base).free < need_dl:
            raise ft.FfmpegError(f"falta espacio en el disco: hacen falta ~{need_dl / 1e6:.0f} MB y hay {shutil.disk_usage(base).free / 1e6:.0f} MB libres")
        if shutil.disk_usage(_setup_disk()).free < need_inst:
            raise ft.FfmpegError(f"falta espacio en el disco donde se instala Tesseract: hacen falta ~{need_inst / 1e6:.0f} MB")
        got, _n = net.download(rel.url, setup, progress, cancel)
        if progress:
            progress("verify", 0, 0)
        if got.lower() != rel.sha256.lower():
            raise ft.VerifyError("la suma SHA-256 del instalador de Tesseract no coincide con la esperada: se descartó la descarga")
        if cancel is not None and cancel.is_set():
            raise ft.Cancelled("cancelado")
        if progress:
            progress("install", 0, 0)
        code = (runner or run_elevated)(setup, "/S", INSTALL_TIMEOUT, cancel)
        path = (finder or find)("")
        if not path:
            hint = " (cancelaste la instalación)" if code == 1 else ""
            raise ft.VerifyError(f"el instalador de Tesseract terminó con código {code} y no dejó tesseract.exe{hint}")
        if progress:
            progress("check", 0, 0)
        ver = version_of(path)
        if not ver:
            raise ft.VerifyError("Tesseract se instaló pero tesseract.exe no arranca")
        info = {"version": ver, "tag": rel.tag, "provider": rel.provider, "package": rel.name, "sha256": rel.sha256, "path": path,
                "installed": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            with open(os.path.join(base, "version.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=1)
        except OSError:
            pass
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def use_existing(path, finder=None):
    """«Usar un archivo que ya descargué» (tesseract.exe ya instalado): comprueba que arranque y devuelve {"path","version"}."""
    path = os.path.abspath(path)
    if os.path.isdir(path):
        path = next((c for c in (os.path.join(path, "tesseract.exe"), os.path.join(path, "tesseract")) if os.path.isfile(c)), os.path.join(path, "tesseract.exe"))
    if not os.path.isfile(path) or "tesseract" not in os.path.basename(path).lower():
        raise ft.VerifyError("el archivo elegido no es tesseract.exe")
    ver = version_of(path)
    if not ver:
        raise ft.VerifyError("el tesseract.exe elegido no arranca")
    return {"path": path, "version": ver}
