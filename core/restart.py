# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reiniciar ViDeck Pulse (p. ej. para aplicar el idioma elegido).

La ventana principal marca `restart_requested` y se cierra como siempre (se detienen el motor, el reproductor y el visor);
cuando `main.py` termina de cerrar, abre otra copia del programa con el mismo intérprete, el mismo `main.py` y la misma carpeta
de trabajo con la que arrancó la primera. Así la copia nueva nunca convive con la anterior (mpv, OpenGL, config.json, el registro).
"""
import os
import subprocess
import sys


def command(cwd, argv, executable=None):
    """Línea de comandos de la copia nueva: [intérprete, main.py (ruta completa), argumentos originales…]; con el ejecutable
    (PyInstaller) no hay `main.py`: [ViDeckPulse.exe, argumentos originales…]."""
    if getattr(sys, "frozen", False):
        return [executable or sys.executable] + list(argv[1:])
    script = argv[0] if argv else "main.py"
    if not os.path.isabs(script):
        script = os.path.join(cwd, script)
    return [executable or sys.executable, script] + list(argv[1:])


def relaunch(cwd, argv):
    """Abre la copia nueva. Devuelve (True, "") o (False, motivo). Nunca lanza una excepción."""
    try:
        subprocess.Popen(command(cwd, argv), cwd=cwd, close_fds=True)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
