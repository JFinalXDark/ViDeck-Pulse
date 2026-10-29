# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Versión de ViDeck Pulse.

El programa es la **v2** (v2.0). Todos los updates hechos hasta ahora son la base de la **Beta 1** de la v2. Cada **5 updates**
es una versión Beta: 5 updates = Beta 1, otros 5 = Beta 2… (con `UPDATES` = 0 a 4 se ve «Beta 1»; con 5 a 9, «Beta 2»; con 10 a 14,
«Beta 3»). Cuando terminan todas las pruebas (las de desarrollo y las
del usuario en su PC) se pone `STABLE = True` y el texto pasa a «ViDeck Pulse v2.0 (estable)».

Para publicar un update: sumar 1 a `UPDATES` (y poner `STABLE = True` cuando la versión se dé por estable).
El texto se muestra abajo de las opciones de la pestaña «Ajustes».
"""
APP_NAME = "ViDeck Pulse"          # nombre del programa (único lugar: el resto del código y de los textos lo usa tal cual)
MAJOR_MINOR = "2.0"
STABLE = True                    # True: «v2.00 (estable) u1» · False: «v2.0 (Beta N)»
STABLE_MAJOR_MINOR = "2.00"      # cómo se escribe la versión estable
STABLE_UPDATE = 1                # número «u<n>» de la versión estable: la primera es u1, la siguiente u2… (las Betas siguen con `UPDATES`, sin «u»)
REPO = "JFinalXDark/ViDeck-Pulse"   # repositorio de GitHub donde se publican las versiones (el programa estable avisa si hay una más nueva ahí)
UPDATES = 14                     # updates entregados después de la base de la Beta 1 (1.º: ajuste del turno de avisos; 2.º: esfera + contadores; 3.º: modos del Reproductor; 4.º: atajo de modo, sin doble clic; 5.º: botón más bajo, reparto 73/27, registro en verde sobre negro, ícono ▶️ de los decks; 6.º: captura que se espacia sola con Traktor a pantalla completa, Tesseract en prioridad baja, cuadros/s del visor; 7.º: pestaña «Reproductor» con cuándo empieza el fundido y su duración; 8.º: selector de idioma Español / English; 9.º: «Sin Bordes» con mayúsculas, «Asignar Tecla» que se mantiene, la pestaña Reproductor sin datos técnicos, verde más claro en el registro; 10.º: aviso de reinicio al cambiar el idioma, captura más rápida, «Preparar videos» (mismo bitrate), sincronía más precisa: hora real de cada fotograma, mediana de varios cambios de dígito y revisiones precisas breves cada 25 s; 11.º: ffmpeg y libmpv propios con descarga verificada (el ffmpeg base se instala solo si falta; «Verificar actualizaciones»), aviso de actualización al iniciar por repositorio (Sí / No / Revisar después) con ventana «Actualizando…», «Legacy Mode», «Preparar videos» rediseñado con tarjetas que filtran, progreso general con tiempo restante y la lista de videos (Compatible / Mejorable / No compatible / No se puede convertir) y botón por video, alertas de bajo rendimiento y error de pantalla negra, registro de sincronía en «Desarrollador», saltos exactos (el salto normal de mpv aterrizaba en el keyframe anterior y la sincronía no convergía), adelanto del salto aprendido por video (un video nuevo parte de lo aprendido con los demás), compensación de arranque aprendida por separado para el deck que arranca y para la anticipación, lo aprendido guardado entre sesiones (`sync_learned.json`) y primera revisión 1,5 s tras cargar, y ajuste fino de velocidad; corrección del botón «Componentes» y la lista de videos de «Preparar videos» también sin ffmpeg; ventanas adaptables a pantallas chicas (1280×720, 1366×768): la principal cambia sola a una disposición compacta con el estado a la derecha y las pestañas y botones bajo la lista de videos, y «Preparar videos» y «Componentes» se recortan a la pantalla con scroll; 12.º: el programa pasa a llamarse ViDeck Pulse (soporte actual: Traktor Pro 4), «Reproductor» / «Player» en lugar de «ventana de reproducción» (también en el título de la ventana), todo texto en pantalla empieza con mayúscula en español y en inglés, ortografía revisada en los dos idiomas y «Modo de Ventana» en Ajustes: botones «Completo» / «Compacto» para fijar la disposición a mano sin quitar la ventana adaptable; 13.º: autoescaneo de videos nuevos, «Preparar videos» dejando el convertido en la carpeta de videos, rutas relativas, icono nuevo y nombre «Traktor Pro 4»; 14.º: carpeta de videos fuera de la instalación (en «Videos» de Windows) con «Cambiar ruta…», sin carpeta «Videos Preparados», idioma del instalador, ventana «Componentes necesarios» del primer inicio, instalador y versión portable, y búsqueda de actualizaciones una sola vez al iniciar) (cada UPDATES_PER_BETA = 5 sube 1 el número de Beta)
UPDATES_PER_BETA = 5
CREDITS = "Con amor, de parte de JFX y Claude"


def beta_number(updates=None):
    """Número de Beta: 1 al principio y +1 cada `UPDATES_PER_BETA` updates."""
    n = UPDATES if updates is None else updates
    return 1 + max(0, int(n)) // UPDATES_PER_BETA


def stage(stable=None, updates=None):
    stable = STABLE if stable is None else stable
    return "estable" if stable else f"Beta {beta_number(updates)}"


def u_tag(n=None):
    """«u1», «u2»… (solo la versión estable; sin la palabra «build»)."""
    return f"u{STABLE_UPDATE if n is None else int(n)}"


def label(stable=None, updates=None, stable_update=None):
    """«ViDeck Pulse v2.0 (Beta 3)» o, en la estable, «ViDeck Pulse v2.00 (estable) u1»."""
    stable = STABLE if stable is None else stable
    if stable:
        return f"{APP_NAME} v{STABLE_MAJOR_MINOR} (estable) {u_tag(stable_update)}"
    return f"{APP_NAME} v{MAJOR_MINOR} ({stage(stable, updates)})"


def release_tag(n=None):
    """La etiqueta de GitHub de la versión estable: «v2.0.0-u1»."""
    return f"v{MAJOR_MINOR}.0-{u_tag(n)}"


def player_title():
    """Título de la ventana del Reproductor (la de Qt y la propia de mpv): «ViDeck Pulse - Reproductor» / «ViDeck Pulse - Player».
    Se traduce al pedirlo; OBS y Discord identifican la ventana por este título (cambia con el idioma del programa)."""
    from .i18n import tr
    return tr(f"{APP_NAME} - Reproductor")
