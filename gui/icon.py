# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Icono de ViDeck Pulse para las ventanas de Qt (principal, de reproducción y diálogos)."""
from PySide6.QtGui import QIcon

from core.winicon import ICON_ICO, ICON_PNG


def app_icon():
    """QIcon del programa (assets/icon.png; si falta, assets/icon.ico). Sin archivo devuelve un icono vacío (sin romper nada)."""
    for p in (ICON_PNG, ICON_ICO):
        try:
            if p.exists():
                return QIcon(str(p))
        except Exception:
            pass
    return QIcon()
