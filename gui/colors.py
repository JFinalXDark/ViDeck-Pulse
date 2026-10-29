# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Código de colores de las listas de videos y de la configuración de tracks."""
from core.i18n import tr as T

SYNC_COLOR = "#3ddc84"       # verde: visual configurado (vinculado a un track de Traktor, con BPM base)
RANDOM_COLOR = "#f0a030"     # naranja: Random Visual (sin vínculo)
PENDING_COLOR = "#4a9eff"    # azul: vinculado pero con el BPM Base Pendiente de escribir a mano
# registro de errores: como una consola (fondo negro, letras verdes, parecido al registro de una actualización en OpenMediaVault)
LOG_BG = "#000000"
LOG_FG = "#6dff6d"           # verde claro (más contraste sobre el negro)
LOG_DIM = "#5be85b"          # texto de relleno («Sin errores: todo funciona.»): también verde claro, apenas más tenue que el texto
LOG_SEL_BG = "#3aa83a"       # selección
LOG_SEL_FG = "#000000"
LOG_BORDER = "#2a5a2a"
LEGEND = (f'<span style="color:{SYNC_COLOR}">🎵 {T("Verde: Visual Configurado")}</span> · '
          f'<span style="color:{PENDING_COLOR}">🎵 {T("Azul: BPM Base Pendiente")}</span> · '
          f'<span style="color:{RANDOM_COLOR}">🎲 {T("Naranja: Random Visual")}</span>')


def row_color(title, bpm_text):
    """Color de una fila: sin título = Random Visual; con título y sin BPM = pendiente; con ambos = configurado."""
    if not str(title or "").strip():
        return RANDOM_COLOR
    return SYNC_COLOR if str(bpm_text or "").strip() else PENDING_COLOR
