# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mensajes internos de Qt (OpenGL, fuentes, plataforma…) al registro de ViDeck Pulse."""
import sys

from core import devlog as dl


def install_qt_hook():
    """Los avisos y errores de Qt van al registro; todo sigue saliendo también por la consola como siempre."""
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return False

    def handler(mode, ctx, msg):
        try:
            print(msg, file=sys.stderr)
            if mode == QtMsgType.QtWarningMsg:
                dl.external("Qt", msg, dl.WARN)          # el ruido habitual de Qt (geometría de ventanas…) no es un error
            elif mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                dl.error("Qt", msg)
        except Exception:
            pass

    qInstallMessageHandler(handler)
    return True
