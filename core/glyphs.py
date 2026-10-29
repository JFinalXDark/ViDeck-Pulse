# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Íconos de texto del panel (decks): el mismo estilo para «suena» y «pausa».

El ⏸ de Windows se dibuja con la fuente de emojis (cuadro azul con las dos barras blancas). El ▶ suelto se dibuja con la fuente de
símbolos (un triángulo blanco, de otro estilo), así que se le agrega el selector de variación U+FE0F, que pide la presentación de
emoji: el ▶️ sale con el mismo cuadro azul que el ⏸. (Si una fuente no lo soporta, se ve el ▶ de siempre: no se rompe nada.)"""

PLAY = "▶️"          # ▶️  suena
PAUSE = "⏸"               # ⏸  en pausa (ya se dibuja como emoji: no necesita el selector)
