# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Procesos auxiliares con prioridad baja: el OCR (Tesseract) nunca debe quitarle CPU al visor, a mpv ni a Traktor."""
import os

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000           # Windows: prioridad «por debajo de lo normal» para el proceso hijo


def with_low_priority(kwargs):
    """Argumentos de `subprocess.Popen` con la prioridad del proceso hijo por debajo de lo normal (solo Windows; en otros
    sistemas se devuelven tal cual)."""
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | BELOW_NORMAL_PRIORITY_CLASS
    return kwargs


def lower_tesseract_priority():
    """Hace que todo `tesseract.exe` que lance pytesseract (la detección del layout tarda varios segundos con un núcleo al
    100 %, y el OCR de los ROIs se lanza continuamente) corra con prioridad baja. Se puede llamar varias veces."""
    try:
        import pytesseract.pytesseract as pt
        orig = pt.subprocess_args
    except (ImportError, AttributeError):                  # sin pytesseract, o una versión que lanza Tesseract de otra forma
        return False
    if getattr(orig, "low_priority", False):
        return False

    def subprocess_args(*a, **k):
        return with_low_priority(orig(*a, **k))

    subprocess_args.low_priority = True
    pt.subprocess_args = subprocess_args
    return True


def lower_this_thread():
    """Pone el hilo actual en prioridad baja (solo Windows): para trabajos largos de fondo (comprimir el paquete Legacy) que no deben
    quitarle CPU al visor ni a la sincronía. En otros sistemas no hace nada. Devuelve True si se pudo."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        return bool(k32.SetThreadPriority(k32.GetCurrentThread(), -2))          # THREAD_PRIORITY_LOWEST
    except Exception:                                                            # noqa: BLE001: es solo una mejora
        return False
