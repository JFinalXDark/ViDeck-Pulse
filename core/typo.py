# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Retoques tipográficos de los textos de la interfaz (puro Python, sin Qt, para poder probarlo).

`keep_sentences` evita que una frase corta se parta entre dos líneas (p. ej. «Esc sale de pantalla completa.» que dejaba «Esc»
colgando al final de una línea y el resto en la siguiente): las frases cortas se unen con espacios duros para que bajen enteras
a la línea de abajo, y en las largas se unen las dos últimas palabras para que nunca quede una palabra sola en la última línea.
"""
import re

NBSP = " "                         # espacio duro: Qt no parte la línea en él
SHORT = 36                              # una frase de hasta tantos caracteres no se parte nunca (cabe en el panel más angosto)
_END = re.compile(r"(?<=[.!?…])[ \t]+(?=\S)")


def keep_sentences(text, short=SHORT):
    """Devuelve `text` con las frases cortas (≤ `short` caracteres) unidas por espacios duros y, en las demás, con las dos últimas
    palabras unidas. No toca texto con etiquetas HTML ni los saltos de línea explícitos."""
    if not isinstance(text, str) or " " not in text or ("<" in text and ">" in text):
        return text
    lines = []
    for line in text.split("\n"):
        out = []
        for sent in _END.split(line):
            words = sent.split(" ")
            if len(words) > 1:
                if len(sent) <= short:
                    sent = NBSP.join(words)
                elif len(words) >= 3:
                    sent = " ".join(words[:-2]) + " " + NBSP.join(words[-2:])
            out.append(sent)
        lines.append(" ".join(out))
    return "\n".join(lines)
