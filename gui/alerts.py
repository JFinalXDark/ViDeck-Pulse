# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ventanas de alerta compartidas."""
from PySide6.QtWidgets import QMessageBox

from core.autolink import NOT_DETECTED
from core.i18n import tr as T
from core.prepare import size_text


def ask_restart(parent, language_name, engine_running=False):
    """Aviso al cambiar el idioma: «Sí» reinicia ahora y aplica el cambio; «No, después» lo deja para el próximo arranque.
    True si se pulsa «Sí». Con el motor en marcha el botón por defecto es «No, después» (un Enter sin querer no corta los visuales)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(T("Cambiar el idioma"))
    box.setText(T(f"Idioma elegido: {language_name}. Para aplicarlo hay que reiniciar ViDeck Pulse."))
    box.setInformativeText(T("¿Reiniciar ahora? Se detendrán el motor y los visuales." if engine_running else "¿Reiniciar ahora?"))
    yes = box.addButton(T("Sí"), QMessageBox.YesRole)
    later = box.addButton(T("No, después"), QMessageBox.NoRole)
    box.setDefaultButton(later if engine_running else yes)
    box.setEscapeButton(later)
    box.exec()
    return box.clickedButton() is yes


def ask_restart_text(parent, title, text, engine_running=False, later="No, después"):
    """Aviso de reinicio con el texto que se le dé (p. ej. «Legacy Mode»): «Sí» reinicia ya; `later` lo deja para el próximo arranque.
    True si se pulsa «Sí». Con el motor en marcha el botón por defecto es el de después."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(T(title))
    box.setText(T(text))
    box.setInformativeText(T("¿Reiniciar ahora? Se detendrán el motor y los visuales." if engine_running else "¿Reiniciar ahora?"))
    yes = box.addButton(T("Sí"), QMessageBox.YesRole)
    no = box.addButton(T(later), QMessageBox.NoRole)
    box.setDefaultButton(no if engine_running else yes)
    box.setEscapeButton(no)
    box.exec()
    return box.clickedButton() is yes


def ask_repo_update(parent, repo, detail, allow_later=True):
    """Aviso de que un repositorio (ffmpeg, libmpv) tiene una versión nueva: «Sí» (actualizar ahora), «No» (no actualizar esa versión) y, con
    `allow_later`, «Revisar después» (se vuelve a buscar la próxima vez que se abra el programa). Devuelve "yes", "no" o "later"; cerrar
    la ventana cuenta como «Revisar después» (lo más prudente: no actualiza ni descarta nada)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(T("Actualización disponible"))
    box.setText(T(f"Hay una actualización de «{repo}» disponible. ¿Quieres actualizarlo?"))
    box.setInformativeText(T(detail) + "\n\n" + T("Se descarga de GitHub, se verifica con su suma SHA-256 y se prueba antes de usarla; si algo falla, "
                                                  "se vuelve a lo que tenías."))
    yes = box.addButton(T("Sí"), QMessageBox.YesRole)
    no = box.addButton(T("No"), QMessageBox.NoRole)
    later = box.addButton(T("Revisar después"), QMessageBox.RejectRole) if allow_later else None
    box.setDefaultButton(later or no)
    box.setEscapeButton(later or no)
    box.exec()
    clicked = box.clickedButton()
    return "yes" if clicked is yes else "no" if clicked is no else "later"


def not_detected(parent, notes):
    """«Canción no detectada en ningún Deck», con el detalle de cada deck debajo."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(T("Autodetección"))
    box.setText(T(NOT_DETECTED))
    box.setInformativeText(T("\n".join(notes)))
    box.exec()


def ask_yes_no(parent, title, text, detail=""):
    """Pregunta con los botones «Sí» y «No» (por defecto «No»). True si se pulsa «Sí»."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(T(title))
    box.setText(T(text))
    if detail:
        box.setInformativeText(T(detail))
    yes = box.addButton(T("Sí"), QMessageBox.YesRole)
    no = box.addButton(T("No"), QMessageBox.NoRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    box.exec()
    return box.clickedButton() is yes


def ask_replace_original(parent, count, size, recycle, kind="fixed"):
    """Traspaso directo de «Preparar videos»: «¿Reemplazar el video original una vez convertido?». Devuelve «replace» («Sí»: el convertido ocupa
    el lugar del original, que va a la Papelera —o se elimina para siempre si la unidad no tiene— solo después de verificar el convertido),
    «keep» («No»: el original se queda y el convertido lleva «[Converted]» al final del nombre) o None («Cancelar»; cerrar la ventana también)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(T("Reemplazar el video original"))
    box.setText(T("¿Reemplazar el video original una vez convertido?"))
    yes_text = ("Sí: cada convertido ocupa el lugar de su original, que se envía a la Papelera de Windows solo después de verificar el convertido."
                if recycle else
                "Sí: cada convertido ocupa el lugar de su original, que se ELIMINARÁ DEFINITIVAMENTE (esta unidad no tiene Papelera) solo después de "
                "verificar el convertido.")
    what = T(f"Se va a convertir 1 video ({size_text(size)}).") if count == 1 else T(f"Se van a convertir {count} videos ({size_text(size)}).")
    box.setInformativeText(what + "\n\n" + T(yes_text) + "\n\n" +
                           T("No: los originales no se tocan y cada convertido se guarda al lado, con «[Converted]» al final del nombre."))
    yes = box.addButton(T("Sí"), QMessageBox.YesRole)
    no = box.addButton(T("No"), QMessageBox.NoRole)
    cancel = box.addButton(T("Cancelar"), QMessageBox.RejectRole)
    box.setDefaultButton(no)                                   # lo seguro por defecto: no borra nada
    box.setEscapeButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    return "replace" if clicked is yes else "keep" if clicked is no else None


def ask_new_version(parent, result):
    """Aviso de que hay una versión nueva de ViDeck Pulse en GitHub (core/updatecheck.py). Devuelve "open" (ir a la página de descarga),
    "later" (recordármelo en el próximo inicio) o "skip" (omitir ESA versión). Cerrar la ventana cuenta como «later»: lo más prudente."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(T("Hay una versión nueva de ViDeck Pulse"))
    box.setText(T(f"Ya está disponible ViDeck Pulse {result['text']} (tú tienes {result['running']})."))
    box.setInformativeText(T("Se descarga desde la página del programa en GitHub. ViDeck Pulse no descarga ni instala nada solo: lo haces tú, "
                             "cuando quieras."))
    open_ = box.addButton(T("Abrir la página de descarga"), QMessageBox.AcceptRole)
    later = box.addButton(T("Recordármelo después"), QMessageBox.RejectRole)
    skip = box.addButton(T("Omitir esta versión"), QMessageBox.DestructiveRole)
    box.setDefaultButton(open_)
    box.setEscapeButton(later)
    box.exec()
    clicked = box.clickedButton()
    return "open" if clicked is open_ else "skip" if clicked is skip else "later"
