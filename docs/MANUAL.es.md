# ViDeck Pulse (Windows)

Reproductor de visuales para DJ sets que corre en paralelo a Traktor Pro 4. No usa MIDI: lee **solo las zonas de los
decks y del mixer** de la ventana de Traktor (OCR + análisis de píxeles) y reproduce el video con mpv.
Soporte actual: **Windows 10/11**. (macOS se verá más adelante si hace falta.)

## Instalación
```bash
pip install -r requirements.txt
```
- **Tesseract OCR**: `winget install UB-Mannheim.TesseractOCR` (consola). Se detecta solo (PATH o `C:\Program Files\Tesseract-OCR`);
  si lo instalas con el programa abierto basta con pulsar «Iniciar motor». Sin él el programa avisa y solo puede usar
  Random Visuals (no lee título, BPM ni tiempos). Otra ruta: `config.json` → `ocr.tesseract_cmd`.
- **libmpv**: lo más cómodo es **Ajustes → «Componentes (ffmpeg y libmpv)…» → libmpv → «Instalar libmpv»**: se descarga de un repositorio público
  conocido, se verifica y queda en la carpeta `mpv/` del programa (ver «Componentes» más abajo). También vale copiar `libmpv-2.dll` (o `mpv-2.dll`)
  junto a `main.py`, o tenerla en el PATH.
- **ffmpeg** (solo para «Preparar videos» y el análisis de compatibilidad): **no hay que instalar nada**. Si el programa no encuentra ninguno, el **primer inicio**
  descarga el **ffmpeg base** solo (en segundo plano y con prioridad baja), lo verifica con su SHA-256, lo prueba y lo deja en la carpeta `ffmpeg/` del
  programa; no se instala nada en el sistema. Si ya tienes uno (`winget install ffmpeg`, el PATH, una carpeta `ffmpeg` junto a `main.py` o sitios típicos de
  Windows) se detecta solo y no se descarga nada; otra ruta: `config.json` → `prepare.ffmpeg_cmd`. Para que NO se descargue solo, apaga la casilla
  **«Instalar el ffmpeg base automáticamente si falta»** (Ajustes → «Componentes…») o pon `components.ffmpeg.auto_base` en `false`. Las **actualizaciones**
  se buscan a mano en **«Componentes»** (botón «Buscar actualización») o con el aviso del inicio.
- **Captura de la ventana de Traktor: PrintWindow (por defecto).** No abre ninguna sesión de captura de la ventana, así que
  puedes transmitir Traktor Pro 4 (Discord, OBS) a la vez que usas ViDeck Pulse. Lee como mucho 5 fps. Windows Graphics
  Capture (`pip install windows-capture`, opcional) se elige en la pestaña «Captura» (hasta 15 fps), pero puede chocar con la
  transmisión. Cada método ve un tamaño de ventana distinto (PrintWindow 1936×1048 con los bordes invisibles, WGC 1920×1032):
  **al cambiar de método hay que recalibrar los ROIs**; si no, las lecturas salen desplazadas (títulos ilegibles) y el panel
  avisa en la línea «Captura».
- **Transmitir Traktor Pro 4 en Discord/OBS a la vez.** ViDeck Pulse solo **lee** la imagen de la ventana de Traktor: no la mueve,
  no la enfoca, no la minimiza ni la restaura, no cambia su estilo ni su visibilidad (no usa `SetWindowDisplayAffinity`, que dejaría la
  ventana en negro al transmitir), no instala ganchos ni se inyecta en Traktor y no usa Desktop Duplication. Con PrintWindow (por defecto)
  no hay sesión de captura, así que no compite con la de Discord. Solo hay dos cosas que vigilar: (1) **WGC** (opcional) sí abre una
  sesión de captura y en Windows 10 dibuja el borde amarillo: déjalo en PrintWindow si transmites; (2) PrintWindow hace que Traktor
  repinte su ventana hasta 5 veces por segundo: si notaras tirones en Traktor o en la transmisión, baja los fps en la pestaña «Captura» (`capture.max_fps`; con menos fps tarda más en notar un cambio de track).
  Una prueba automática (`capture_noninvasive_test.py`) vigila que ninguna versión nueva añada llamadas de ese tipo.

## Estructura
```
ViDeck_Pulse/
├─ main.py                  # arranque (limita hilos de OpenCV/Tesseract a 1)
├─ LICENSE · THIRD_PARTY_NOTICES.md   # licencia GPL-3.0-or-later y avisos de las licencias de terceros
├─ config.json              # se crea solo (ROIs, umbrales, captura, sincronización)
├─ tracks_db.json           # relación track ↔ video (se crea al guardar; el zip NO lo trae, así no se pisa al actualizar)
├─ mpv.log                  # mensajes de mpv (si algo no se ve, aquí está el motivo)
├─ videck_pulse.log      # registro de ERRORES del programa (solo con «Activar registro de errores (Log)», pestaña Desarrollador)
├─ assets/                  # icon.png / icon.ico = icono de ViDeck Pulse (update 13: «V» con línea de pulso dentro de un anillo con un arco rojo, como la marca de posición del plato de un deck); icon_original.png = el icono anterior (conservado); iconos_alternativos/ = variantes más (barras, plato, V sin arco) para cambiar copiando sobre icon.png / icon.ico
├─ videos/                  # tus videos
├─ ffmpeg/ · mpv/ · legacy/ # componentes propios (se crean al instalarlos; el zip no los trae): ffmpeg, libmpv y el paquete Legacy
├─ core/
│  ├─ capture.py            # ventana de Traktor: PrintWindow (WGC opcional), recorta solo los ROIs
│  ├─ vision.py             # OCR con caché, vúmetro, reloj del deck, sensor por deck
│  ├─ engine.py             # hilo: sensores → OCR → doble check → sincronización → mpv
│  ├─ crossfade.py          # reproductor con fundido cruzado (2 instancias de libmpv)
│  ├─ layout.py             # detección automática de los ROIs (cabeceras de deck + vúmetros) sobre la captura
│  ├─ autolink.py           # autovinculación track de Traktor Pro 4 ↔ video (título ≥70 % + duración)
│  ├─ devlog.py             # registro solo de errores (con el detalle del momento del error), excepciones no controladas
│  ├─ winicon.py            # icono en Windows: ventana propia de mpv y barra de tareas
│  ├─ status.py             # texto y color de la barra de estado (Estado:/Alerta:, esfera verde/amarilla/roja) y conteo de errores/alertas
│  ├─ version.py            # versión de ViDeck Pulse (v2.0, Beta N / estable) que se ve en la pestaña Ajustes
│  ├─ i18n.py · i18n_en.py  # idioma de la interfaz (Español / English): `tr()` traduce al mostrar; i18n_en.py = catálogo español → English
│  ├─ restart.py            # reiniciar el programa (al cambiar el idioma): main.py abre otra copia después de cerrar todo
│  ├─ prepare.py            # «Preparar videos»: ffprobe/ffmpeg, clasificación de cada video, recodificar con el mismo bitrate, vínculos nuevos (sin Qt)
│  ├─ transfer.py · trash.py # traspaso directo del convertido (verificar → colocar → vincular → Papelera) y Papelera de Windows por unidad (sin Qt)
│  ├─ ffmpeg_tools.py       # ffmpeg propio: buscar, descargar de un repositorio conocido, verificar (SHA-256), instalar/actualizar, avisar al iniciar, ffmpeg base automático (BaseInstall)
│  ├─ mpv_tools.py          # libmpv propia: igual que ffmpeg, pero se prepara y se pone al reiniciar (Windows no deja reemplazar una DLL en uso)
│  ├─ legacy.py · sevenzip.py · archive.py   # «Legacy Mode»: paquete legacy/legacy_repos.7z con los ffmpeg y libmpv que funcionaban (lector/escritor de .7z propio)
│  ├─ components.py         # arranque de los componentes, errores de repositorios con el aviso «Activar Legacy Mode…», título «(Legacy Mode)»
│  ├─ videohealth.py · mpvutil.py   # vigilancia del video que suena (bajo rendimiento = alerta, pantalla negra = error) con lo que mpv sabe de él
│  ├─ synclog.py            # registro de sincronía (una línea por revisión; cuadro en «Desarrollador» con el panel avanzado)
│  ├─ db.py · config.py · media.py · aspect.py · player.py (respaldo: mpv incrustado + fundido a negro)
└─ gui/                     # ventana principal, configuración, calibración, visor · adaptive.py (tamaño y disposición adaptables a la pantalla) · prepare_dialog.py (ventana «Preparar videos») · update_dialog.py (ventana «Actualizando…») · ui_kit.py (tarjetas, estilos y etiquetas compartidas) · components_dialog.py (ventana «Componentes») · glview.py (mezcla OpenGL) · qtlog.py · icon.py · statusframe.py (barra de estado plana, con la esfera de color y los dos contadores) · sphere.py (la esfera dibujada)
```

## Actualizar a una versión nueva
Descomprime el zip **encima** de la carpeta actual: el zip no incluye `config.json` ni `tracks_db.json`, así que tus ROIs,
ajustes y vínculos track ↔ video se conservan. (Los archivos de código sí se reemplazan.)
En una instalación limpia, o si borras alguno de los dos, el programa **crea `config.json` y `tracks_db.json` vacíos** al arrancar (solo si faltan; nunca toca los existentes). Si uno está dañado, lo guarda como `config.json.bak` / `tracks_db.json.bak` y empieza de cero.

## Si la salida de video se ve negra
1. En la pestaña **Salida**, pulsa **«Probar salida»** (con el motor detenido): mpv dibuja un patrón de colores en la ventana.
   - Si el patrón **se ve**, la ventana y mpv funcionan: el problema está en el video o en el motor.
   - Si **no se ve**, prueba «Salida de video → Ventana propia de mpv» y/o «Decodificación → CPU» (se aplican al reiniciar).
2. La fila **«Reproductor»** del panel (con «Activar Panel de Reproductor Avanzado», pestaña Desarrollador) muestra el estado real de mpv (archivo, tamaño del video, si avanza, `hwdec`, último
   aviso o error). Los mensajes completos de mpv quedan en `mpv.log` junto a `main.py`.

## Primer uso
1. Pon videos en `videos/` y ejecuta `python main.py`.
2. Pestaña **Captura**: deja marcado «Leer solo la ventana de Traktor» y elige la ventana (Traktor.exe).
3. **ROIs automáticos** (por defecto): con Traktor maximizado o a pantalla completa y un track cargado en algún deck, al
   pulsar «Iniciar motor» el programa detecta solo los ROIs (ver «Layout automático»). También puedes calibrar a mano
   («Calibrar ROIs») con las **plantillas** medidas de tu layout o pulsando **«✨ Detectar automáticamente»** en ese diálogo:
   - *Encabezado del deck A / B*: arrastra de la esquina superior izquierda del deck a su borde derecho → título, BPM,
     tiempo restante y duración.
   - *Mixer completo*: arrastra todo el panel → vúmetros de ambos canales.
   - Activa «Monitor VU en vivo» para comprobar los recortes: con el deck en pausa el vúmetro debe marcar 0 %; sonando,
     debe subir y mostrar el color del círculo más alto (azul = nivel bajo, amarillo = alto). Usa la plantilla del mixer:
     un recorte dibujado a mano que incluya otros puntos de color (GAIN, KEY…) falsea el nivel. «Medir silencio» (también se
     captura sola cuando el reloj lleva 3 s quieto) guarda una referencia que hace que se ignoren esos puntos fijos.
4. **Iniciar motor** y carga un track. Si su título se parece (≥ 70 %) al nombre de un video y la duración coincide, se
   vincula solo con el BPM Base Pendiente (azul; ver «Autovinculación»): escríbelo en **Configuración**. Si no se vincula,
   en Configuración usa «Leer del deck activo (OCR)» (tempo al 0%) para rellenar nombre, BPM base y duración. Guarda.
5. Arrastra el visor al proyector y ponlo a pantalla completa desde la pestaña **Salida** → «Modo del Reproductor» (o con el botón «Visor a pantalla completa»; ver «Reproductor: modos»). El doble clic no cambia nada.

## Layout automático (Traktor maximizado / pantalla completa)
Pestaña **Captura → Layout (ROIs)**: **Automático** (por defecto) o **Manual**.
- **Cómo detecta.** Tesseract lee la franja superior de la ventana y busca en cada deck el trío inconfundible *tiempo restante
  («-02:52») + duración («02:52», debajo) + BPM («128.00», a la derecha)*; con sus cajas calcula los ROIs de restante, duración,
  BPM y tempo (%), y el del título (empieza en el margen del deck y termina antes del restante). Los **vúmetros** se buscan por
  estructura (dos columnas de puntos con paso de 5 px), así que no necesitan música ni canción cargada. La detección tarda
  unos 5-8 s y corre en un hilo aparte: el motor no se detiene. Se probó con una captura real de Traktor Pro 4 a 1920×1080.
- **Cuándo corre (modo automático).** Al iniciar el motor si falta algún ROI o si los ROIs se calibraron con otro tamaño o
  método de captura; y durante la sesión si la ventana cambia de tamaño (maximizar, pantalla completa, restaurar). Una config
  antigua con ROIs y sin tamaño guardado se respeta (se da por buena para el tamaño con el que arranca el motor).
  Al terminar guarda los ROIs y el tamaño con el que se calcularon.
- **Cambiar entre ventana y pantalla completa en pleno set.** Al cambiar el tamaño de la ventana de Traktor los ROIs de
  antes ya no valen: leídos sobre la captura nueva darían basura y el visual caería a Random Visual. Por eso, en modo
  automático, el motor **deja de leer y mantiene el visual actual** mientras consigue los ROIs del tamaño nuevo (el panel
  «Captura» dice «se mantiene el visual actual»). Si ese tamaño ya se había usado antes, sus ROIs se **recuperan al instante**
  (se guarda un layout por tamaño en `config.json`, clave `capture.profiles`): tras la primera detección en pantalla completa,
  ir y volver entre ventana y pantalla completa es inmediato (~1 s, lo que se espera a que el tamaño se estabilice). Si la
  detección no logra leer el layout nuevo, el visual se mantiene y puedes pulsar «✨ Detectar layout ahora» (con un track
  cargado en algún deck) o volver al tamaño anterior.
- **Un deck sin canción** no tiene texto que leer: sus ROIs se estiman por geometría a partir del otro deck y de las columnas
  del mixer (solo si lo leído confirma que la geometría encaja) y se reintenta a los 20 s, 60 s y 120 s; el panel «Captura»
  indica «parcial» hasta que los dos decks están leídos. Si ningún deck tiene canción solo se fijan los vúmetros.
- **Manual.** Nunca toca tus ROIs; avisa en «Captura» si el tamaño ya no coincide. El botón **«✨ Detectar layout ahora»**
  (pestaña Captura) y **«✨ Detectar automáticamente»** (diálogo de calibración) detectan cuando tú quieras, también en manual;
  después puedes corregir cualquier recuadro con las plantillas o a mano.
- **Límites.** Está medido con la interfaz de Traktor al 100 % y el layout «Mixer» (decks A y B a los lados y mixer en el
  centro). Con la interfaz escalada se ajusta por el tamaño del texto, pero no está probado. Si cambias el layout de Traktor
  (otro tipo de mixer, decks C/D) usa la calibración manual.

## Autovinculación (nombre, BPM y duración desde Traktor Pro 4)
Pestaña **Sincronización**: «Autovincular: nombre, BPM y duración desde Traktor Pro 4» (activado por defecto) y «Parecido mínimo título ↔ archivo» (70 %).
Cuando hay un track cargado en **cualquiera de los decks** y no tiene visual en ViDeck Pulse:
1. Se compara su título con el nombre de los videos **sin vincular** (sin extensión ni palabras como *visual*, *video*, *1080p*;
   si el deck recortó un título largo se compara con el principio del nombre). Debe coincidir al menos el 70 %.
2. **Doble check con la duración:** la del video (leída del archivo) debe coincidir con la que muestra Traktor Pro 4 (tolerancia
   `ocr.duration_tolerance`, 3 s; sirve cualquiera de las lecturas alternativas del ROI). Si el título coincide pero la duración no,
   **no se vincula** y la fila «Autovinculación» del panel explica por qué (para que lo hagas a mano si es el mismo track).
3. Se guarda en ViDeck Pulse el **título tal como lo lee Traktor Pro 4** y su **duración**. El **BPM base queda
   PENDIENTE para escribirlo a mano**: con el fader de tempo movido (o SYNC activo) el BPM que muestra Traktor no es el del
   track y la velocidad del visual saldría mal. Mientras esté pendiente el visual va a ×1 (la sincronización de posición
   funciona igual) y el panel lo indica; el mensaje de «Autovinculación» sugiere un valor (`BPM mostrado / (1 + tempo/100)`, con el
   tempo leído del ROI «pitch» bajo el BPM) pero **no lo guarda**. Escribe el BPM en **Configuración** (BPM del track con el tempo en
   0 %; «Leer del deck activo» lo copia). Si prefieres que se rellene solo, marca «Rellenar también el BPM base»
   (`autolink.fill_bpm`): se calcula con el tempo leído (dos lecturas que deben coincidir) y, si el tempo no se lee con
   seguridad, no se vincula.
- Solo actúa con la lectura estable unos segundos (mismo título y duración, BPM confirmado), nunca pisa un vínculo existente ni
  crea duplicados, y se detiene mientras la ventana de **Configuración** está abierta. Dos videos igual de parecidos y con la
  misma duración = ambiguo: no vincula.
- **Manual para corregir:** todo lo autovinculado aparece en **Configuración (tracks ↔ videos)**, donde puedes editarlo o
  desvincularlo. Para no usar la autovinculación, desmarca la casilla.
- **Botón manual «✨ Detectar canción de los decks»** (ventana principal) y **«✨ Detectar desde los decks»** (dentro de
  Configuración): hacen la misma comprobación en el momento, con el motor iniciado y una canción cargada en cualquier deck. El de la
  ventana principal vincula todos los videos sin vincular que coincidan; el de Configuración rellena la fila seleccionada (o las filas
  sin nombre si no hay selección) y tú guardas. Si ninguna canción coincide aparece la alerta **«Canción no detectada en ningún Deck»**,
  con el motivo de cada deck (sin canción cargada, el video más parecido y su porcentaje, duración distinta…).
- **Qué hacer para que se autovincule solo:** 1) pulsa «Iniciar motor» (con Traktor visible, no minimizado); 2) carga la canción en un
  deck; 3) espera unos 3 s con **Configuración cerrada** (mientras está abierta la autovinculación automática se pausa). Si el
  nombre del archivo es «Artista - Título … Visuals.mp4» también se compara solo la parte del título.
- **Colores** (lista de videos y tabla de Configuración): <span style="color:#f0a030">Naranja</span> = Random Visual (sin vínculo) ·
  <span style="color:#3ddc84">Verde</span> = Visual Configurado · <span style="color:#4a9eff">Azul</span> = BPM Base Pendiente (vinculado, con el BPM base
  por escribir a mano). En la tabla puedes dejar el BPM vacío (queda pendiente) y se guarda así.

## Reproductor: resolución
El Reproductor se titula **«ViDeck Pulse - Reproductor»** (en inglés, **«ViDeck Pulse - Player»**: el título cambia con el idioma del programa), también cuando mpv usa su propia ventana (conserva el icono de mpv).
La ventana del visor mide la **resolución elegida** (1080p → 1920×1080 px de área de video, 720p → 1280×720; se compensa el
escalado de Windows) y al cambiar la resolución se redimensiona. Puedes cambiarle el tamaño a tu gusto: **siempre se mantiene en
16:9**. A pantalla completa (modo «Pantalla completa» o botón «Visor a pantalla completa») se ajusta a la pantalla. En un monitor de 1080p una
ventana de 1920×1080 más su barra de título no cabe entera: arrástrala al proyector o ponla a pantalla completa.

## Reproductor: modos (Ventana · Ventana Sin Bordes · Pantalla completa)
En la pestaña **Salida** → **«Modo del Reproductor»** hay tres modos para el Reproductor (se aplican al momento y se
guardan en `config.json`, clave `output.window_mode`: `window` | `borderless` | `fullscreen`):

| Modo | Qué hace |
|---|---|
| **Ventana** | Ventana normal, con bordes y barra de título (el modo de siempre; es el que viene por defecto). |
| **Ventana Sin Bordes** | Sin bordes ni barra de título: al **transmitir la ventana por Discord** no se ve el borde superior. Como no hay barra de título de dónde agarrarla, **se mueve arrastrándola con el botón izquierdo** del ratón (la pantalla completa no se mueve). Mantiene la resolución elegida y el 16:9. |
| **Pantalla completa** | Ocupa toda la pantalla elegida en **«Pantalla»** (esa elección también se guarda: `output.screen`). |

- **Sin doble clic.** El doble clic en la ventana ya **no cambia el modo** (un doble clic sin querer en pleno set cambiaría la ventana). El modo solo cambia con el combo de «Salida», con el botón «Visor a pantalla completa» (alterna entre pantalla completa y el último modo con ventana), con **Esc** y con el atajo opcional.
- **Esc** sale de la pantalla completa y **vuelve al último modo con ventana** que tenías (no siempre a «Ventana»).
- **Atajo de teclado (opcional, en blanco por defecto)** — pestaña Salida → «Atajo para cambiar de modo» (clic en el campo y pulsa la tecla o combinación; mientras está en blanco el campo dice «Asignar Tecla»; «Quitar» lo deja en blanco; se guarda en `output.mode_hotkey`, p. ej. `"F9"` o `"Ctrl+M"`). Cada pulsación pasa al **siguiente modo**: Ventana → Ventana Sin Bordes → Pantalla completa → Ventana (se aplica y se guarda como si lo eligieras en el combo). **Solo actúa con el Reproductor seleccionado** (abierta, sin minimizar y con el foco): con otra ventana activa, minimizada u oculta no hace nada, así que no se dispara tecleando en Traktor, Discord u otro programa. Mantener la tecla pulsada no recorre los modos. **Esc no se puede usar** como atajo (está reservada para salir de la pantalla completa).
- El atajo y Esc funcionan con el Reproductor del programa («Salida de video → Reproductor del programa (incrustado)»). Con «Ventana propia de mpv» no llegan las teclas: usa el combo.
- Si cambias de modo con la ventana en pantalla completa, primero sale de ella y después cambia los bordes (Qt restaura el estilo que tenía al entrar, así que los bordes solo se tocan fuera de la pantalla completa).
- El cambio de bordes se hace sobre la ventana ya creada (no se recrea el handle que usan mpv y OpenGL). Si Qt no pudiera cambiarlos, queda una **alerta** (esfera amarilla) en la barra de estado; el programa sigue funcionando.
- Con «Salida de video → Ventana propia de mpv» el modo se aplica a la ventana de mpv (`border` / `fullscreen`); la pantalla elegida en «Pantalla» solo vale para la ventana del programa (mpv abre su pantalla completa donde esté la ventana).
- **Transmitir por Discord**: comparte la ventana **«ViDeck Pulse - Reproductor»** (no la de Traktor, que es la que lee el OCR). Si Discord mostrara la ventana en negro o con parpadeos, prueba a compartir la pantalla en vez de la ventana (no se pudo comprobar con Discord real: es lo primero a revisar).
  **Si vienes de una versión anterior a la update 12:** el programa cambió de nombre y la ventana del Reproductor ahora se llama **«ViDeck Pulse - Reproductor»** (en inglés **«ViDeck Pulse - Player»**: el título sigue al idioma del programa). En **OBS** (Captura de ventana) y en **Discord** hay que **volver a elegir esa ventana una vez**; si cambias el idioma, vuelve a elegirla otra vez.
- Al arrancar, el modo guardado se aplica **antes** de mostrar la ventana (no hay un parpadeo con bordes). «Restablecer por defecto» vuelve a «Ventana»; «Cargar ajustes» aplica el modo del archivo.

## Fundido cruzado: cómo saber si está activo
El fundido cruzado real (el siguiente visual se carga detrás y el actual se vuelve transparente) necesita la salida
**«Reproductor del programa (incrustado)»**, OpenGL y python-mpv con la API de render. Si algo falla al iniciar, el programa
**cae al fundido a negro** (el visual sale a negro, carga el siguiente y entra desde negro). Cómo saber cuál estás usando:
la fila **«Reproductor»** del panel (en modo avanzado: pestaña Desarrollador) empieza con «OpenGL …» si el fundido cruzado está activo, y con «mpv incrustado …» si se usa el
fundido a negro; en ese caso la fila lleva un **⚠** (el motivo está en el registro de la pestaña «Desarrollador»; la pestaña Reproductor solo dice que
se usa el fundido a negro) y el detalle técnico (traceback) queda en `crossfade_error.txt`. Con el fundido cruzado activo, el visual nuevo
ya se está dibujando detrás antes de que empiece la mezcla (el fundido espera a su primer fotograma, hasta 6 s): no pasa por negro.

## Panel de estado por deck
La fila de decks ahora se divide en **«Deck A»** y **«Deck B»** (cada uno con su estado completo: track, visual, BPM, factor
de velocidad) y el texto largo se ajusta a varias líneas. El lado derecho se divide en dos zonas que llenan toda la altura hasta
los botones, con un **divisor vertical que puedes arrastrar**: arriba el estado (un 73 % de la altura, o lo que necesite si es menos, con su
propio scroll) y abajo los ajustes (al menos un 27 %), donde **cada pestaña (Salida, Captura, Sincronización, Desarrollador y
Ajustes) tiene su propio scroll vertical**: el contenido se queda arriba, sin huecos en blanco, y se baja con la rueda o la barra solo
cuando no cabe. Los botones de abajo quedan siempre fijos («Iniciar motor» es un 25 % más bajo que antes, para dejar más espacio al panel y a los ajustes). En el panel de cada deck, el ícono de estado es el mismo estilo para los dos: **▶️** suena y **⏸** en pausa (cuadro azul de los emojis de Windows). La ventana abre con hasta 860 px de alto (sin pasarse de tu pantalla; en pantallas bajas cambia sola a la disposición compacta: ver «Ventanas que se adaptan a la pantalla»). Al pie, la **barra de estado** es una franja **plana** de un extremo a otro de la ventana (con su esquina para cambiar el tamaño):
tiene el mismo color que la ventana, sin marco ni bordes, y solo unas líneas finas verticales separan el texto de los dos contadores. Siempre empieza con una **esfera dibujada** y **«Estado:»** (verde = todo en orden, amarilla = alerta, roja = error; ver
«Barra de estado» más abajo). Viene **activada por defecto**: se ve siempre y en reposo dice
«Estado: Todo está funcionando correctamente». Con la casilla **Activar Barra de Estado** (pestaña Desarrollador) puedes apagarla; apagada,
solo aparece mientras tiene algo que decir (un mensaje pasajero como «se aplica al reiniciar», una alerta o un error) y se oculta después. El margen inferior del contenido es de 6 px.
**Errores en el panel:** cuando algo falla, solo la fila donde está el problema muestra un **⚠** (al pasar el ratón por encima se
ve el motivo corto; p. ej. «Captura» si no se ve la ventana de Traktor, «Reproductor» si mpv no carga un video, «Track (OCR)» y
«Doble check» si falta Tesseract) y el detalle completo va al registro (pestaña «Desarrollador»). **Las pestañas no llevan ⚠.**
Las demás filas siguen siempre visibles; la única fila que
se muestra u oculta a voluntad es «Sensores (debug)» (casilla de la pestaña «Desarrollador»; por defecto oculta). La fila
«Reproductor» muestra por defecto solo el nombre del archivo que suena (todo el detalle con «Activar Panel de Reproductor Avanzado»).
Entre la lista de videos y el panel hay un **divisor que puedes arrastrar**; al abrir el programa reparte el ancho 50/50. Todos los
campos de los ajustes (combos y números) tienen el mismo ancho y se encogen si falta espacio (los textos largos de un combo se ven
completos al desplegarlo); las casillas y notas largas se parten en varias líneas. Así no hace falta scroll horizontal: solo
aparece, abajo, si reduces tanto la ventana que algo no cabe.
La fila **«Autovinculación»** muestra el último vínculo creado o el motivo por el que no se creó.

## Ventanas que se adaptan a la pantalla (1280×720, 1366×768…)
Todas las ventanas se pueden **redimensionar** y se ajustan solas a la pantalla donde se abren (el área útil, sin la barra de tareas; funciona también con el escalado de Windows al 125–150 %). Nada queda cortado ni fuera de pantalla:
- **Ventana principal.** Abre en 1040×860 como máximo, recortada a la pantalla. Tiene dos disposiciones y cambia sola según su **alto** (con un margen para que no oscile al arrastrar el borde):
  - **Normal** (alto ≥ ~800 px): la lista de videos a la izquierda; a la derecha el estado del reproductor, las pestañas y los cuatro botones.
  - **Compacta** (alto < ~760 px, p. ej. laptops de 1366×768 o 1280×720): el **estado del reproductor pasa a la derecha**, ocupando todo el alto, y **las pestañas y los botones se colocan bajo la lista de videos**, en la columna izquierda (la lista recibe el 45 % de esa columna y se puede arrastrar el divisor).
  El ancho mínimo es lo que ocupan los tres botones bajo la lista (~770 px): ningún texto se corta, y si las pestañas no caben aparecen flechas para desplazarlas. Al volver a agrandar la ventana, vuelve la disposición normal.
  - **Modo de Ventana (a mano).** En **Ajustes**, arriba de «Preparar videos…», los tres botones chicos **«Automático»**, **«Completo»** y **«Compacto»** eligen la disposición (**«Automático»** viene marcado: la ventana decide según su alto, como se describe arriba; los otros dos la fijan a mano). Se aplican al momento, se guardan en `config.json` (`ui.layout_mode`: `auto` | `full` | `compact`) y también los usa «Preparar videos». Con uno fijado, cambiar el tamaño de la ventana no cambia la disposición; pulsar **«Automático»** (o «Restablecer por defecto») devuelve la ventana adaptable; volver a pulsar un botón ya marcado no cambia nada. Debajo de los botones, una línea dice si estás en automático (y cuál es el modo de ahora) o si está fijado a mano. Un valor inválido en el archivo cuenta como automático.
- **«Preparar videos» y «Componentes».** Abren recortadas a la pantalla; si la ventana es baja, la zona de arriba (descripción, tarjetas, opciones) pasa a una zona con scroll y los botones de acción de abajo (Preparar, Detener, Usar los videos preparados, Cerrar) quedan **siempre visibles**; la descripción se oculta si no cabe y vuelve al agrandar la ventana.
- **Calibración de ROIs** usa como máximo el 85 % × 55 % de la pantalla.
- **Reproductor** (la ventana donde se ven los visuales): se abre con la resolución de salida elegida (1080p = 1920×1080 px); en una pantalla más chica que eso puedes cambiarle el tamaño (mantiene el 16:9) o elegir 720p.
Código: `gui/adaptive.py`; prueba: `adaptive_test.py`.

## Pestaña «Desarrollador» (registro)
- **Activar registro de errores (Log)** (primera casilla): muestra debajo un pequeño visor (como una consola: **fondo negro y letras verdes**) y guarda también en `videck_pulse.log`, junto a
  `main.py` (se rota a `.1` al pasar de ~1,5 MB), **solo los errores**. Si todo funciona, el registro queda **vacío** (y el archivo no
  crece: ni siquiera se escribe la cabecera de sesión). Los eventos normales (cargar un visual, motor iniciado/detenido, layout…) no se
  muestran; se guardan en una memoria corta de contexto y **solo cuando ocurre un error** se detalla todo: el error con su traceback, lo
  que pasaba en los 20 s anteriores y lo que pasa en los 20 s siguientes. La cabecera (versiones, Tesseract, salida, captura, pantallas)
  va al copiar el registro y al inicio de la sesión en el archivo cuando hay algún error.
  El registro recoge las excepciones no controladas (también las de los hilos) y los errores y avisos reales de mpv, de Qt, de OpenGL,
  de la captura, del motor y del reproductor. El ruido inofensivo de las librerías (p. ej. el aviso de mpv «No key binding found for key
  'MBTN_LEFT'» al hacer clic en su ventana, o la geometría de ventanas de Qt) **no** se registra ni marca ⚠ (`mpv.log` lo conserva todo).
  Un mismo error no se repite durante 2 minutos («se repitió N veces más» al volver a salir) y una fuente que desborda se limita sola.
  Un aviso que sigue en el panel (p. ej. «Fundido cruzado no disponible») queda en **una sola línea**.
  Aunque la casilla esté apagada, los errores se guardan en memoria (últimas 3000 líneas) y al activarla aparece lo ocurrido antes.
- Botones del visor: **Copiar** (copia el registro con la cabecera de entorno y, al final, los últimos eventos normales como contexto, para
  pegarlo en un mensaje), **Limpiar** y **Abrir archivo**.
- Las pestañas **no** llevan ⚠ (el ⚠ solo va en la fila afectada del panel). Un error nuevo se avisa en la **barra de estado** (esfera
  roja) que se mantiene hasta que el error se soluciona (abrir esta pestaña, copiar o limpiar el registro no lo apaga).
- **Activar «Sensores» en el Estado del Reproductor**: muestra u oculta la fila «Sensores (debug)» del panel (vúmetro y reloj de
  cada deck en tiempo real). Por defecto está oculta. Es la única fila del panel que se activa o desactiva.
- **Activar Panel de Reproductor Avanzado**: con la casilla activada, la fila «Reproductor» del panel muestra todo el detalle (salida,
  archivo, resolución, posición, decodificación, capas del fundido…); desactivada (por defecto) muestra solo el nombre del archivo que
  está sonando. En los dos casos un error se marca con ⚠ y su detalle va al registro.
  Con esta casilla activada aparece también debajo el cuadro **«Registro de sincronía»** (ver «Revisión de la sincronización»), con **Copiar** y **Limpiar**.
- **Activar Barra de Estado** (**activada por defecto**): deja la barra de estado siempre visible al pie de la ventana (plana, del color de la
  ventana y de borde a borde; en reposo dice «Estado: Todo está funcionando correctamente» con la esfera verde y los contadores en 0 | 0).
  Desactivada, la barra solo aparece mientras hay una alerta, un error o un mensaje pasajero. Al actualizar desde una versión anterior
  (`config_version` 5), el `config.json` existente pasa **una sola vez** a tener la barra activada; después se respeta lo que elijas con la
  casilla. «Restablecer por defecto» también la deja activada.
- Claves en `config.json`: `dev.log_enabled`, `dev.status_bar`, `dev.show_sensors`, `dev.advanced_player`. (`dev.log_only_errors` ya no existe: el
  registro es siempre solo de errores; si un config.json antiguo la trae, se ignora.)

## Barra de estado: qué mensajes pueden salir
La barra siempre empieza con una **esfera dibujada** (con volumen y brillo, no un carácter de texto) y una palabra. La esfera **cambia de
color** según el aviso que se está viendo:

| Color | Texto | Qué significa | Cuánto dura |
|---|---|---|---|
| 🟢 verde | **Estado:** Todo está funcionando correctamente | Sin errores ni alertas y el programa funcionando bien (o un mensaje pasajero) | mientras no haya ninguna alerta ni error; un mensaje pasajero dura los segundos indicados abajo |
| 🟡 amarillo | **Alerta:** … | Algo no va como debería, pero **el programa sigue funcionando** | **Se mantiene hasta que el problema se soluciona** |
| 🔴 rojo | **Estado:** Error — *resumen*. Revisa el registro de errores (Log). | Algo **impide el funcionamiento correcto** | **Se mantiene hasta que el error se soluciona** (con una actualización del programa o arreglándolo en la configuración de ViDeck Pulse). Revisar o limpiar el registro **no** lo apaga |

Reglas: manda lo más grave (rojo > amarillo > verde). **Si hay más de un aviso (alertas y/o errores), se turnan en la barra cada 5
segundos** para que se alcancen a leer: primero los errores y después las alertas, cada uno con su propio color y su contador
(«(1/3)», «(2/3)», «(3/3)»). Un **error nuevo** salta a la barra al instante; una alerta nueva entra en el turno sin interrumpir lo que
estás leyendo. Con el **puntero encima de la barra** el turno se detiene, y el tooltip muestra todos los avisos abiertos a la vez. Los
5 s (y no 3) son porque un error con «Revisa el registro de errores (Log).» es un texto largo; se cambian en `core/status.py`
(`ROTATE_S`). Mientras no se
solucione, ni la alerta ni el error desaparecen, aunque abras la pestaña Desarrollador, copies o limpies el registro (al limpiarlo, lo
que sigue sin solucionarse se vuelve a anotar con «(sigue sin solucionarse)» para que el registro no contradiga la barra). Si el
problema se soluciona solo (p. ej. Traktor deja de estar minimizada, el siguiente video carga bien), el aviso se apaga solo. Un mensaje pasajero
nunca tapa una alerta o un error; si aún no caducó, se ve cuando estos se solucionan. Si el registro está desactivado, el error dice
«Activa y revisa el registro de errores (Log) en la pestaña Desarrollador» (los errores de la sesión se guardan igual y aparecen al
activarlo). Si el texto no cabe, se recorta el resumen y nunca el final («Revisa el registro…»); el texto completo está en el
tooltip. Cada aviso enciende además el **⚠ de su fila** del panel (la columna «Fila»).

**Contadores en el extremo derecho.** Al final de la barra hay **dos zonas separadas por líneas finas** con la **misma esfera** amarilla y
roja de los estados y un número: a la izquierda las **alertas** abiertas (esfera amarilla) y a la derecha los **errores** abiertos (esfera
roja) en este momento (por ejemplo 🟡 0 | 🔴 0 cuando todo está en orden). Se actualizan solos cada vez que aparece o se soluciona un aviso, suman lo mismo que el «(i/N)»
del turno, y el número se ve en negrita cuando es mayor que 0. Al pasar el puntero por una zona, el tooltip dice «Errores abiertos: N»
o «Alertas abiertas: N».

**🟢 Mensajes pasajeros (verde, «Estado: …»)**

| Mensaje | Cuándo | Dura |
|---|---|---|
| Todo está funcionando correctamente | en reposo, sin alertas ni errores (solo se ve con la casilla activada) | — |
| Hay actualizaciones de ffmpeg/libmpv: se preguntará al abrir el programa la próxima vez. | al iniciar con el motor en marcha, si hay una versión más nueva | 15 s |
| Registro de sincronía copiado al portapapeles. | pulsas «Copiar» en el cuadro de sincronía | 5 s |
| Se aplica al reiniciar el programa. | cambias «Salida de video» | 6 s |
| La decodificación se aplica al reiniciar el programa. | cambias «Decodificación» | 6 s |
| Detectando el layout de Traktor (unos segundos)… y luego su resultado («Layout: …») | pulsas «Detectar layout ahora» | 8 s |
| Ventana elegida; si cambia el tamaño, recalibra los ROIs. | eliges otra ventana de captura | 6 s |
| Modo de captura cambiado: reinicia el motor y recalibra los ROIs. | cambias el modo de captura | 8 s |
| ✨ Vinculado: Deck A «título» ↔ video.mp4. BPM Base Pendiente (azul): escríbelo en Configuración. | pulsas «Detectar canción de los decks» | 15 s |
| Registro copiado al portapapeles. | pulsas «Copiar» en Desarrollador | 5 s |
| Aún no hay archivo de registro: se crea cuando ocurre un error con el registro activado. | «Abrir archivo» sin errores aún | 6 s |
| Ajustes restablecidos a los valores por defecto. / Ajustes cargados desde «archivo». + «La salida de video y la decodificación se aplican al reiniciar el programa.» | restablecer o cargar ajustes | 12 s |
| Archivo descartado: los ajustes siguen como estaban. | dices «No» a un archivo de ajustes incompatible | 8 s |

**🟡 Alertas (amarillo, «Alerta: …»): el programa sigue funcionando**

| Alerta | Fila con ⚠ | Se apaga cuando |
|---|---|---|
| Fundido cruzado no disponible: necesita la salida «Reproductor del programa (incrustado)»; se usa el fundido a negro. | Reproductor | al reiniciar con la salida incrustada |
| Fundido cruzado no disponible (tipo de error: detalle): se usa el fundido a negro (detalle en crossfade_error.txt). (la autoprueba no mostró imagen, OpenGL no se inicializó, python-mpv antiguo…) | Reproductor | al reiniciar con el problema resuelto |
| Aviso de mpv que indica un problema real (`warn …`, p. ej. un fallo de decodificación por GPU) | Reproductor | al cargarse el siguiente video sin que vuelva a avisar |
| La ventana de Traktor está minimizada (no se puede leer nada mientras siga así) | Captura | solo, cuando Traktor deja de estar minimizada |
| «<método> no disponible: …» (la captura pasó a otro método) | Captura | al reiniciar el programa |
| Aviso significativo de Qt, Python o una librería | — | al reiniciar el programa |
| No se pudo poner el icono a la ventana de mpv · No se pudo repartir la altura del panel (solo estéticos) | — | al reiniciar el programa |
| Demasiados mensajes seguidos de «fuente»: se omiten los siguientes unos segundos | — | al reiniciar el programa |
| “video.mp4” va con bajo rendimiento: conviene prepararlo en Ajustes → Preparar videos (14 de 30 fps, hevc 1920×1080, CPU) | Reproductor | solo, cuando vuelve a ir bien o cambia el video |
| Falló la vigilancia del video (…) | Reproductor | solo, cuando la vigilancia vuelve a funcionar |
| **Activar Legacy Mode para funcionar correctamente** (después del error del repositorio que falló) | — | al arreglarse el repositorio o al activar Legacy Mode |

**🔴 Errores (rojo, «Estado: Error — …. Revisa el registro de errores (Log).»): algo no funciona**

| Error | Fila con ⚠ | Se apaga cuando |
|---|---|---|
| Falta Tesseract (OCR): no se lee título, BPM ni tiempos de Traktor | Track (OCR) y Doble check | al reiniciar con Tesseract instalado |
| No se encontró ninguna ventana «…» · Error de captura: … · La captura de la ventana no entregó imagen | Captura | solo, cuando la captura se recupera |
| Los ROIs se calibraron en W×H (método) y ahora se captura otro tamaño: las lecturas saldrán desplazadas | Captura | al recalibrar, detectar el layout o volver al tamaño |
| No se pudo detectar el layout: … | Captura | solo, cuando una detección sale bien |
| No se pudo iniciar la captura: … | Captura | al iniciar el motor otra vez y funcionar |
| mpv no cargó «video.mp4» (motivo) · Reproductor: … · Prueba de salida: … · error o fatal de mpv | Reproductor | al cargarse bien el siguiente video |
| Excepción en el bucle del motor | — | cuando el motor completa ciclos sin fallar durante 5 s (o al iniciarlo de nuevo) |
| Excepción en la transición de visual / del fundido cruzado, o «no se pudo cambiar la pausa de mpv» | Reproductor | al cargarse bien el siguiente video |
| Excepción al listar las ventanas · al capturar Traktor desde «Calibrar ROIs» · «fallo inesperado: se reabre la captura» | Captura | cuando la lista se puede leer / Traktor se puede capturar / la captura entrega imagen otra vez |
| Falló la detección del layout (excepción) | Captura | cuando una detección sale bien |
| No se pudo leer «archivo» de ajustes | — | al aplicar unos ajustes válidos |
| config.json inválido (se usan los valores por defecto) · No se pudo crear config.json / tracks_db.json · tracks_db.json ilegible (se empieza vacío) | — | al reiniciar el programa con el archivo bien |
| Autovinculación: no se pudo guardar tracks_db.json (el vínculo vale hasta cerrar el programa) | Autovinculación | al iniciar el motor otra vez |
| Error del visor OpenGL (no se pudo iniciar / falló el dibujado) | Reproductor | al reiniciar el programa |
| Pantalla negra: “video.mp4” no se puede mostrar (códec X no compatible · no tiene una imagen que mpv pueda mostrar · el decodificador descarta todos los fotogramas · la imagen se quedó congelada) | Reproductor | al verse bien otro video o al detener el motor |
| ffmpeg: … · libmpv: … (una descarga, verificación o prueba falló, o la libmpv nueva no cargó y se volvió a la anterior) | — | al instalarse bien otra vez o al activar Legacy Mode |
| Error crítico de Qt · excepción no controlada (también de un hilo) | — | al reiniciar el programa |

Los avisos que dicen «al reiniciar el programa» no tienen todavía forma de comprobar que se solucionaron mientras el programa está
abierto: se quedan hasta reiniciarlo, y si el problema sigue, vuelven a aparecer. Si uno de estos se soluciona en una actualización
futura, se apagará solo.

## Icono
El icono de ViDeck Pulse (`assets/icon.png`, con `assets/icon.ico` para Windows) se usa en la ventana de ajustes, en el **Reproductor**
(el del programa y, con «Salida de video → Ventana propia de mpv», la propia de mpv: se le pone al aparecer) y
en la barra de tareas (el programa se identifica como «ViDeckPulse.App», así que Windows no muestra el icono de python.exe).
Para cambiarlo, sustituye esos dos archivos (el `.ico` con varios tamaños: 16, 24, 32, 48, 64, 128 y 256 px).

## Versión de ViDeck Pulse
ViDeck Pulse es la **versión 2 (v2.0)**. Todos los updates hechos hasta ahora son la base de la **Beta 1** de la v2.
- **Beta N**: cada **5 updates** es una versión Beta (5 updates = Beta 1, otros 5 = Beta 2…): con `UPDATES` de 0 a 4 se ve «Beta 1», de 5 a 9 «Beta 2», de 10 a 14 «Beta 3»…
- **estable**: cuando terminen todas las pruebas (las de Claude y las de tu PC) la versión pasa a «v2.0 (estable)».
- Se ve como texto **centrado abajo de las opciones de la pestaña «Ajustes»**, con «Con amor, de parte de JFX y Claude» debajo, también
  centrado. Ejemplos: «ViDeck Pulse v2.0 (Beta 1)» · «ViDeck Pulse v2.0 (estable)».
- Dónde se cambia: `core/version.py` (`UPDATES` suma 1 con cada update; `STABLE = True` para marcar la versión como estable).
- **Update 12** (Beta 3): el programa pasa a llamarse **ViDeck Pulse**. El soporte actual es para **Traktor Pro 4** (no hay que confundirlo con Traktor Pro 3 ni con Traktor Play); quien quiera modificar el código puede darle soporte a otros programas de mezcla (como Serato DJ o VirtualDJ). La «ventana de reproducción» / «ventana de salida» pasa a llamarse **Reproductor** (en inglés **Player**) en todos los textos y en el título de la ventana. Cambia el nombre del archivo de registro (`videck_pulse.log`) y el identificador de Windows de la barra de tareas (`ViDeckPulse.App`). Todo texto en pantalla empieza con mayúscula (y también tras « · », « — » y los puntos), en español y en inglés. Nuevo: **Modo de Ventana** (botones «Completo» / «Compacto», `config_version` 8).

- **Update 13** (Beta 3): botón **«Automático»** en «Modo de Ventana»; **ícono nuevo** (una «V» con línea de pulso y un arco rojo de deck; variantes en `assets/iconos_alternativos/`); **carpeta de datos y rutas relativas** (portable/instalada, migración con copia de seguridad); **búsqueda automática de videos** (progresiva, solo con el motor detenido); **«Preparar videos» puede dejar el convertido en la misma carpeta** (reemplaza al original, que va a la Papelera, o se guarda al lado con «[Converted]»; todo verificado antes de tocar nada); la barra del vúmetro dice **«(quieto)»** si hay barras encendidas pero sin moverse; el programa de mezcla se llama **«Traktor Pro 4»** en toda la interfaz; `config_version` 9.
- **Update 14** (Beta 3): la carpeta de videos por defecto sale de la instalación (**«Videos» de Windows**, `ViDeck Pulse\Videos`; portable: `Videos` junto al programa) con **«Cambiar ruta…»**; se **elimina «Videos Preparados»** (siempre se convierte en la carpeta de videos); **aviso** si se intenta preparar videos con el motor iniciado; el **instalador** pregunta el idioma y la versión portable usa el de Windows; **de un Random Visual a otro el cambio es un corte** (el fundido elegido se mantiene entre un visual configurado y un Random); **ventana «Componentes necesarios»** al primer inicio; búsqueda de actualizaciones **una sola vez al iniciar** (a mano, desde «Componentes»); casillas **«Verificar actualizaciones al iniciar»**; licencia **GPL-3.0-or-later**.

## Pestaña «Ajustes»: restablecer y cargar desde archivo
- **Restablecer por defecto**: deja todos los ajustes de las pestañas (Salida, Reproductor, Captura, Sincronización, Desarrollador y los
  parámetros internos como umbrales del vúmetro, OCR, transición…) en los valores por defecto **de la versión instalada**: cada
  versión nueva trae sus propios valores. Pide confirmación (Sí / No). No se tocan: la carpeta de videos, `tracks_db.json`, la
  calibración de los ROIs (ni el tamaño con el que se calibraron ni los layouts guardados por tamaño), la ruta de Tesseract, la de ffmpeg y el **idioma**.
  Ejemplo: el **ajuste fino del video** (Sincronización, `sync.offset`) vuelve a **0,00 s**.
- **Cargar ajustes desde archivo**: elige un `config.json` de ViDeck Pulse (también de una versión anterior: se adapta solo).
  - Si trae todos los ajustes de esta versión, se aplican directamente.
  - Si le falta alguno (o tiene un valor que esta versión no acepta) aparece: «Este archivo de configuraciones no es compatible con
    la versión actual de ViDeck Pulse, ¿actualizar ajustes de todos modos?» con el detalle de lo que falta. **Sí**: se usan los
    ajustes compatibles y los que el archivo no trae quedan en su **valor inicial** (el de «Restablecer por defecto»; no se pone
    un 0 literal porque muchos ajustes no admiten 0, p. ej. los fps). **No**: se descarta el archivo y todo queda como estaba.
  - Un archivo que no es de ViDeck Pulse, o que no es JSON, solo muestra un aviso y no cambia nada.
  - Tampoco se toman del archivo la carpeta de videos, la base de datos, los ROIs, la ruta de Tesseract, la de ffmpeg ni el idioma.
- Tras restablecer o cargar, las pestañas se redibujan con los valores nuevos. La salida de video y la decodificación se aplican al
  reiniciar el programa.
- Abajo de las opciones de la pestaña se ve la versión («ViDeck Pulse v2.0 (Beta 1)») y «Con amor, de parte de JFX y Claude», ambas centradas.

## Componentes: ffmpeg y libmpv propios, actualizaciones y Legacy Mode
ViDeck Pulse puede instalar y mantener al día **ffmpeg** (para «Preparar videos») y **libmpv** (el reproductor) en carpetas propias, sin tocar el
sistema. Ventana: **Ajustes → «Componentes (ffmpeg y libmpv)…»**.
- **Dónde se descarga**: solo por HTTPS desde repositorios públicos conocidos de GitHub: ffmpeg de las builds de Windows que enlaza ffmpeg.org
  (gyan.dev a través de su espejo oficial en GitHub; BtbN/FFmpeg-Builds como alternativa) y libmpv de las builds de mpv para Windows
  (shinchiro/mpv-winbuild-cmake y zhongfly/mpv-winbuild). Cualquier otro servidor se rechaza.
- **Verificación**: cada paquete se comprueba con su suma **SHA-256 publicada** (la que da la API del repositorio o su archivo de sumas) **antes**
  de abrirlo; sin suma o si no coincide, **no se instala**. Después se prueba: ffmpeg debe arrancar y traer el codificador libx264; libmpv se carga en un
  proceso aparte con el python-mpv del programa (que cree un reproductor y tenga la API de render del fundido cruzado). Lo que ya tenías no se toca hasta que
  lo nuevo ha pasado todas las pruebas.
- **ffmpeg** se cambia en el momento (atómico; si algo sale mal vuelve a la versión anterior). **libmpv** es una DLL que el programa carga al arrancar:
  la nueva queda **preparada y probada** en `mpv/_pending/` y se pone **al reiniciar** (la anterior pasa a `mpv/_previous/`). Si el arranque con la nueva no
  llega a terminar, el siguiente arranque vuelve solo a la anterior y lo avisa.
- **Aviso al iniciar**: **una sola vez en cada inicio** del programa (en segundo plano; nunca mientras está abierto ni cada cierto número de horas) se mira si hay una
  versión más nueva de ViDeck Pulse (solo la estable), de ffmpeg y de libmpv. Para buscar a mano está el botón **«Buscar actualización»** de cada fila de «Componentes». Por **cada repositorio** con novedad aparece un aviso: *«Hay una actualización de «<repositorio>» disponible. ¿Quieres
  actualizarlo?»* con **«Sí»** (abre la ventana «Actualizando…» con la descarga, la verificación y la prueba; con libmpv pregunta si reiniciar), **«No»** (esa versión no se
  vuelve a ofrecer; de una más nueva sí) y **«Revisar después»** (no instala ni descarta nada y se vuelve a preguntar la próxima vez que se abra el programa;
  cerrar la ventana cuenta como «Revisar después»). Con **Legacy Mode activo** no hay avisos ni búsquedas, y con el **motor en marcha** no se abre ninguna ventana (en pleno
  set no se interrumpe): solo una nota en la barra de estado y se pregunta al próximo inicio. Se desactiva con la casilla **«Verificar actualizaciones al iniciar»** (la de ViDeck Pulse está en Ajustes y la de ffmpeg y libmpv en «Componentes»).
- **Errores**: si un comando o una versión nueva falla con ViDeck Pulse, es un **ERROR** en el registro y en la barra de estado, con el nombre del
  repositorio que falló («ffmpeg: …» / «libmpv: …»). **Después** de ese error aparece (alerta amarilla) **«Activar Legacy Mode para funcionar correctamente»**.
  Un fallo de conexión no cuenta como fallo del repositorio: solo queda en el registro.
- **Legacy Mode**: cada versión del programa guarda en `legacy/legacy_repos.7z` (con `legacy/legacy.json`: versiones y SHA-256 de cada archivo) los ffmpeg
  y libmpv **que funcionaban cuando esa versión arrancó bien**. El primer arranque correcto de una versión lo guarda solo, en segundo plano y con el motor detenido; «Guardar ahora los repositorios actuales como Legacy» lo rehace a mano. Con
  **«Activar Legacy Mode»** (botón en Ajustes y en la ventana Componentes) el programa pide **reiniciar** (el mismo aviso que al cambiar el idioma) y al arrancar
  usa ESOS archivos verificados, sin borrar los actualizados; **«Desactivar Legacy Mode»** vuelve a los actualizados. Con Legacy activo la **ventana lleva
  «(Legacy Mode)» al final del título**. Si `import mpv` falla al arrancar, el programa vuelve solo a la libmpv anterior o, si no la hay, activa Legacy Mode
  y se reabre una vez.
- Claves en `config.json` (`components`): `legacy`, `ffmpeg` / `mpv` → `auto_check`, `last_check`, `skip_version`, y solo en `ffmpeg` → `auto_base` (descargar el ffmpeg base si falta). «Restablecer por defecto» no las toca.
- Pruebas (sin red: los repositorios se simulan): `components_test.py`, `ffmpeg_tools_test.py`, `sevenzip_test.py`, `sevenzip_write_test.py`,
  `components_gui_test.py` (aviso Sí / No / Revisar después, ventana «Actualizando…», casilla del ffmpeg base). Lo que solo se puede comprobar en un PC con internet: que los repositorios reales sigan publicando con ese formato, la libmpv
  real cargando en el python-mpv instalado y que el 7-Zip de Windows abra el `.7z` que escribe el programa.

## Preparar videos (pestaña «Ajustes»)
Para que la sincronía corrija con saltos (`seek`) sin tirones, los videos conviene que tengan **fps constantes** y un **keyframe cada
segundo o menos** (un video con keyframes cada 8 s obliga a decodificar muchos fotogramas en cada salto). El botón **«Preparar videos…»**
abre una ventana con **la lista de los videos de tu carpeta** y, por cada uno, si funciona bien tal cual. Los convertidos se dejan **en la misma carpeta de videos** (ya no hay una carpeta aparte): tú eliges si el convertido reemplaza al original o se guarda al lado; **el original nunca se pisa sin que lo elijas**.
- **Clasificación** (se analiza con ffprobe, sin decodificar; el motivo de cada video está en la columna «Detalle»):
  - **Compatible**: H.264, 8 bits 4:2:0, fps constantes, keyframes cada ≤ 2 s, ≤ 60 fps, sin rotación ni entrelazado y dentro del «alto máximo» si lo
    elegiste. Funciona bien tal cual: el botón de su fila es **«Copiar tal cual»** (idéntico byte a byte).
  - **Mejorable**: funciona, pero sale más suave o pesa menos en el CPU si se prepara: otro códec que H.264 (más pesado de decodificar), fps variables,
    keyframes entre 2 y 5 s, rotado o más alto que el máximo elegido.
  - **No compatible**: puede dar tirones, mala sincronía o mucho uso de CPU/GPU: formato de color que no es 8 bits 4:2:0 (conversión por software, colores o
    pantalla negra según la tarjeta), entrelazado, más de 60 fps o keyframes a más de 5 s (cada salto de la sincronía tarda y se ve el tirón).
  - **No se puede convertir**: ffprobe/ffmpeg no pueden con él (archivo dañado, sin imagen o formato no admitido). No tiene casilla; se queda solo en la carpeta
    original y no impide usar el resto. Su botón es **«Reintentar»** si el análisis llegó a leerlo.
- **Qué se hace con los que se preparan**: se recodifica a H.264 con el **MISMO bitrate de video que tenía** (se lee del original; la calidad no baja ni se infla),
  la misma resolución (nunca se agranda; con «Alto máximo» 1080 p o 720 p solo se reduce lo que sea más alto), fps constantes
  (los del original; si eran variables, el estándar más cercano: 23,976 · 24 · 25 · 29,97 · 30 · 50 · 59,94 · 60; más de 60 se baja a 60) y un
  keyframe por segundo. El **audio se copia sin recodificar** (solo pasa a AAC si su formato no cabe en un .mp4). Sale siempre en `.mp4`.
  Si el bitrate original no se puede leer, se usa calidad constante (CRF 17).
- **Pérdida de calidad**: recodificar, aunque sea al mismo bitrate, **no es idéntico bit a bit** (hay una pérdida mínima de generación, que a ese bitrate
  normalmente no se nota). Por eso solo se recodifica lo que lo necesita. Tras cada video se comprueba la duración, los fps constantes, los keyframes y que el
  bitrate no haya bajado más de un 25 %; si algo no cuadra, el video queda como **«Revisar»** con el motivo (y también en el registro de errores).
  En las pruebas con ffmpeg real el bitrate de salida quedó entre ×0,98 y ×1,04 del original.
- **Uso**: la ventana **analiza sola al abrirse**. De arriba abajo:
  - **Estado de ffmpeg**: una línea con la versión que se usa y de dónde sale (carpeta propia, PATH, la ruta elegida o Legacy Mode). Aquí ya no hay botones para instalar,
    elegir ni buscar actualizaciones de ffmpeg: eso se hace en **Ajustes → «Componentes»**. Si falta ffmpeg, la línea lo dice y remite a «Componentes»; mientras se instala
    el ffmpeg base aparece un botón **«Cancelar»**. Un fallo del repositorio es un **ERROR** (barra de estado y registro, con su nombre); sin conexión solo se avisa.
  - **Tarjetas de resumen**: *Videos · Compatibles · Mejorables · No compatibles · No se pueden convertir*, con su cantidad. **Pulsar una tarjeta filtra la lista** a ese
    estado (otra vez, o la tarjeta «Videos», quita el filtro).
  - **Alto máximo**.
  - **Marcar**: *Los que lo necesitan* (no compatibles y mejorables aún sin preparar), *Todos* y *Ninguno*; a la derecha, «✔ N de M listos» y
    **«Analizar de nuevo»**.
  - **La lista**: casilla · video · estado (con icono y color) · detalle · botón de la fila («Preparar video» solo ese, «Copiar tal cual» el compatible, «Reintentar»).
  - **Progreso general** mientras se analiza o se prepara: «Preparando 1 de 2 · video.mp4 · quedan ~2 min 10 s» con una barra que solo avanza.
  - **Botones**: **«Preparar videos seleccionados (n)»** prepara solo lo marcado y no escribe nada más; **«Preparar todos»** prepara lo que lo necesita y deja el resto como está;
    **«Detener»** cancela y no deja ningún archivo a medias; **«Cerrar»**. Si cambias el alto máximo, la lista vuelve a analizar sola.
  - **Con el motor iniciado no se puede preparar** (el Reproductor tiene abiertos los archivos): al pulsar «Preparar videos» sale un aviso y la ventana no se abre. Detén el motor y vuelve a intentarlo.
- **Traspaso directo (siempre; desde la update 14 ya no existe la carpeta «Videos Preparados»).** Cada video se convierte **en la carpeta de videos**, **se verifica**
  y se traspasa (hay que soltar los videos que el Reproductor tiene abiertos, por eso solo con el motor detenido). Al pulsar **«Preparar…»** el programa pregunta **«¿Reemplazar el video original una vez convertido?»**:
  - **Sí** → el convertido ocupa el lugar del original (mismo nombre, extensión `.mp4`) y **el original va a la Papelera de Windows** (la de SU unidad, estés donde estés
    instalado). Las unidades de red y las extraíbles normalmente no tienen Papelera: ahí la pregunta dice con todas las letras que se **ELIMINARÁ DEFINITIVAMENTE**.
  - **No** → los originales no se tocan y el convertido se guarda al lado como `nombre [Converted].mp4` (`nombre [Converted] (2).mp4` si ya existía).
  - Los videos que **ya cumplen** se quedan como están (no se recodifican ni se copian). Un original que ya tiene su `[Converted]` al lado **se oculta** de la lista de
    videos y del motor (queda el convertido), pero el archivo sigue en la carpeta.
  - **Qué se comprueba antes de tocar el original** (si algo falla, el original se conserva y la fila dice el motivo): que el convertido se abra, tenga audio, la misma
    duración (tolerancia de 0,25 s o 0,5 %), H.264 yuv420p, las medidas y el alto pedidos, el número de fotogramas y que se **decodifique completo sin errores**; se guarda su
    **SHA-256** y se vuelve a comprobar ya colocado. Antes de eliminar el original se confirma que **no cambió** (tamaño y fecha) desde que se empezó a convertir.
  - **Nunca se pisa un archivo**: el convertido se escribe como `original.verificando` (en la misma carpeta, así el traspaso es un simple renombrado) y, si el nombre
    final se ocupó mientras tanto, no se coloca. Con el **mismo nombre** (`.mp4` → `.mp4`) el original se aparta un instante como `.viejo` y vuelve a su sitio si algo falla.
  - **Los vínculos** track ↔ video pasan al convertido: antes se hace una copia `tracks_db.json.antes-de-traspasar.bak`, se guarda, se vuelve a leer para comprobar que
    quedó bien y, si algo falla, se **deshace todo** (el original sigue en su sitio y el vínculo igual).
  - Mientras la ventana está abierta se pausa la búsqueda automática de videos; al cerrarla, la lista se pone al día sola. Cancelar no deja ningún `.verificando` a medias.
  - Se prueba con videos de verdad en el sandbox (`transfer_test.py`, `prepare_inplace_gui_test.py`) y con 10 «mutaciones» que rompen cada garantía a propósito
    (`mutate_transfer.py`): todas las detecta la prueba. En tu PC conviene probar primero con una carpeta de **copias**.
- Lo ya preparado no se rehace (puedes repetir el proceso tras añadir videos nuevos). Antes de empezar se comprueba que quepan en el disco.
- ffmpeg corre con prioridad baja, pero usa mucho CPU: **no lo hagas durante un set**. Cerrar la ventana a mitad cancela ffmpeg.
- El alto máximo elegido se guarda en `config.json` (`prepare.max_height`: 0 = conservar, 1080 o 720) y la ruta de ffmpeg en `prepare.ffmpeg_cmd`.
- Pruebas: `prepare_test.py` (ffmpeg real: bitrate, duración, fps, keyframes, audio, originales intactos, cancelar, vínculos, clasificación) y
  `prepare_gui_test.py` (la ventana, en español e inglés: tarjetas, filtro, progreso, ffmpeg base, estado de ffmpeg).

## Salud del video: avisos de bajo rendimiento y pantalla negra
Mientras suena un video, el motor mira una vez por segundo lo que mpv sabe de él (`core/videohealth.py`): fotogramas por segundo del archivo y mostrados,
fotogramas descartados, resolución, códec y si decodifica por GPU o CPU. No se juzga durante 3 s tras cargar, saltar (sincronía) o reanudar: son momentos
en que el rendimiento baja sin que el video tenga la culpa.
- **Alerta (amarilla, barra de estado + registro)** — el video se reproduce, pero con **bajo rendimiento**: muestra menos del 80 % de sus fps (o descarta más
  de un 10 % de los fotogramas) durante 4 muestras seguidas. Texto: «“video.mp4” va con bajo rendimiento: conviene prepararlo en Ajustes → Preparar videos
  (14 de 30 fps, hevc 1920×1080, CPU)». Dura mientras siga así (6 muestras buenas seguidas la cierran, y también cambiar de video) y el registro lo anota una vez.
- **Error (rojo, barra de estado + registro)** — **pantalla negra**, diciendo el caso: «Pantalla negra: “video.mp4” no se puede mostrar (…)» con
  *códec X no compatible* / *no tiene una imagen que mpv pueda mostrar* (4 s sin imagen), *el decodificador descarta todos los fotogramas* o *la imagen se quedó
  congelada* (6 s con el reloj avanzando). Se cierra solo cuando otro video se ve bien o al detener el motor.
- Si la propia vigilancia falla (p. ej. mpv no responde) queda una alerta «Falló la vigilancia del video (…)» y una sola línea en el registro; se cierra cuando
  vuelve a funcionar.
- Pruebas: `videohealth_test.py` (lógica con muestras inventadas, ES y EN) y `engine_health_test.py` (el motor real con un video simulado: sano, bajo
  rendimiento, pantalla negra, monitor que falla, falla pasajera y reproductor sin monitor). Con mpv real solo se puede verificar en el PC.

## Idioma (Español / English)
- En la pestaña **«Ajustes»**, el menú **«Idioma»** ofrece **Español** y **English**. El idioma elegido se guarda en `config.json`
  (clave `language`: `"es"` o `"en"`) y se aplica **al reiniciar el programa**. Al elegir otro idioma aparece un aviso («Idioma elegido: English. Para aplicarlo hay que
  reiniciar ViDeck Pulse. ¿Reiniciar ahora?»):
  - **Sí**: el programa se cierra como siempre (detiene el motor, el reproductor y el visor) y se abre solo de nuevo, ya en el
    idioma elegido. Si el motor está en marcha, el aviso lo dice («Se detendrán el motor y los visuales») y el botón marcado por
    defecto pasa a ser «No, después» (un Enter sin querer no corta los visuales).
  - **No, después**: el idioma queda guardado y se aplica la próxima vez que abras el programa; la barra de estado lo recuerda
    («Language: English. Applied when the program restarts.» / «Idioma: Español. Se aplica al reiniciar el programa.»).
  - Si vuelves al idioma con el que está corriendo el programa, no hay nada que reiniciar («Idioma sin cambios.»).
  Los textos que ya están en pantalla no se cambian a medias.
- Con **English** se traduce todo el texto de la interfaz: pestañas, opciones, notas bajo las opciones, tooltips, ventanas de
  configuración y calibración, cuadros de confirmación, panel de estado por deck, barra de estado (Estado / Alerta / Error), pestaña
  «Desarrollador» y su registro en pantalla, y el Reproductor. El campo del atajo vacío dice **«Unassigned»**
  (en español, «Asignar Tecla»).
- **Cómo funciona** (para quien toque el código): el programa sigue pensando en español (la lógica, el registro y las pruebas usan los
  mismos textos de siempre) y cada texto se traduce **solo al llegar a la pantalla** (`core/i18n.py`, función `tr`). Con Español `tr`
  no hace nada, así que el comportamiento es idéntico al de antes. El catálogo «texto en español → English» está en `core/i18n_en.py`
  (las partes variables, como números o nombres de tracks, se escriben `{0}`, `{1}`… y se conservan).
- **Lo que no se traduce**: los **nombres de tus videos y tracks**; el archivo `videck_pulse.log` y el registro que se copia al
  portapapeles (quedan en español para que se puedan comparar entre sí); el texto de ayuda de este README; los botones estándar de
  Qt (Sí / No, Abrir, Cancelar…) que siguen el idioma de Windows.
- Un texto que el catálogo todavía no conoce **se muestra en español** (nunca se pierde un mensaje). Para añadir una traducción:
  agrega la línea «español»: «English» a `core/i18n_en.py`.
- Cambiar de idioma **no** cambia ningún ajuste del programa, y «Restablecer por defecto» y «Cargar ajustes desde archivo» no tocan el idioma.

## Carpeta de datos y rutas (update 13)
- **Carpeta del programa** (el código, `assets/`…) y **carpeta de datos** (`config.json`, `tracks_db.json`, `sync_learned.json`, el registro y, en la versión
  instalada, los componentes descargados: `mpv/`, `ffmpeg/`, `legacy/`). Código fuente o versión **portable** (un archivo `portable.flag` junto al
  `.exe`): la misma carpeta, como siempre, y se puede **copiar o mover la carpeta entera**. Versión **instalada** (ejecutable sin `portable.flag`):
  `%APPDATA%\ViDeck Pulse`, así que actualizar o reinstalar no toca tus datos. La variable de entorno `VIDECK_PULSE_DATA` fuerza otra carpeta.
- **Rutas relativas.** Dentro de `config.json` (`videos_dir`, `db_path`), `tracks_db.json` (`video_path`) y `sync_learned.json` (las claves de cada video) una ruta
  que cae **dentro de la carpeta de datos se guarda relativa** (`videos/sub/a.mp4`), así mover la carpeta ya no rompe los vínculos. En memoria las rutas siguen siendo
  absolutas. Una ruta fuera de la carpeta (otra unidad, `D:\VJ\…`) se guarda tal cual.
- **Migración automática.** Al abrir una carpeta de la update 12 o anterior, las rutas absolutas de dentro pasan a relativas y antes se guardan copias de seguridad
  **una sola vez**: `tracks_db.json.u12.bak` y `config.json.u12.bak` (no se pisan). Si un video ya no está donde decía el vínculo pero **sí** en la carpeta de videos con
  la misma subcarpeta y nombre, el vínculo se repara solo (queda en el registro: «vínculo reparado»).
- `scan_videos` ya no resuelve enlaces ni unidades mapeadas (`Z:` sigue siendo `Z:`).

## Carpeta de videos, idioma e instalador (update 14)
- **Carpeta de videos por defecto** (solo en instalaciones **nuevas**: una `config.json` que ya existe no se toca y no se mueve ni se borra nada):
  - **Instalada:** `<carpeta «Videos» de Windows>\ViDeck Pulse\Videos`, fuera de la instalación, así reinstalar o desinstalar no toca tus videos. Se crea sola.
  - **Portable:** `Videos`, junto al programa.
  - El nombre es «Videos» en español y en inglés. No hay carpeta «Videos Preparados»: «Preparar videos» convierte siempre dentro de la carpeta de videos.
- **«Cambiar ruta…»** (Videos, en la ventana principal; antes «Carpeta de videos…») elige otra carpeta de la que se leen tus videos. Los videos de la carpeta anterior **no se mueven ni se borran**.
- **Idioma:** el **instalador** pregunta el idioma al empezar (español / English) y ese pasa a ser el idioma inicial de ViDeck Pulse (queda en `install_language.txt`, solo si todavía no hay `config.json`). La
  versión **portable** usa el idioma de Windows la primera vez (español si Windows está en español; si no, English). Después manda el selector de «Ajustes».
- **Búsqueda de actualizaciones:** una sola vez en cada inicio (ViDeck Pulse estable, ffmpeg y libmpv) y a mano desde «Componentes» (fila «ViDeck Pulse» solo en la estable). Casillas **«Verificar actualizaciones al iniciar»**.
- **Panel del reproductor avanzado:** apagado por defecto (`dev.advanced_player`).

## Búsqueda automática de videos (update 13)
La lista de videos se busca **en un hilo aparte y por lotes** (la ventana no se cuelga aunque la carpeta esté en una unidad lenta; al abrir la lista se va llenando). Se busca
**al iniciar** y **solo con el motor detenido**: al detenerlo, al volver a la ventana (como mucho cada 5 s), al cerrar «Preparar videos» y con el botón «Recargar»; con el motor
en marcha la lista **no cambia** (y «Recargar» pide detenerlo). La barra de estado avisa de los videos **nuevos**, de los que **ya no están** (sus vínculos de `tracks_db.json` se
conservan) y de los **renombrados o movidos** (mismo tamaño y fecha: el vínculo los sigue y se guarda). Un video nuevo que todavía se está copiando no se lista hasta que
deja de crecer (se vuelve a mirar solo). La lista conserva el video seleccionado y el scroll. Casilla **«Buscar videos nuevos solo…»** en Ajustes (`ui.autoscan`, `config_version` 9):
apagada, solo «Recargar» busca.

## tracks_db.json
```json
{"tracks": [{"traktor_track_name": "Born Funky", "video_path": "videos/born_funky.mp4",
             "base_bpm": 128.0, "duration": "02:52"}]}
```
`video_path` es **relativa a la carpeta de datos** cuando el video está dentro de ella (update 13; ver «Carpeta de datos y rutas»); una ruta
absoluta (`C:/videos/born_funky.mp4`, de una versión anterior o de otra carpeta/unidad) también se lee.
`traktor_track_name` es **solo la primera línea del deck** (el título, sin el artista ni el álbum), tal como lo lee el OCR;
lo más seguro es rellenarlo con «Leer del deck activo (OCR)». `duration` usa el formato de Traktor (`mm:ss`; también se leen segundos). `base_bpm` puede ser `null` = BPM **pendiente** (autovinculación): el visual va a ×1 hasta que lo escribas. Videos sin entrada = **Random Visuals**.

## Lógica
- **¿Hay reproducción? Todo se lee de la imagen de Traktor; no se captura audio.** Dos señales visuales: el **reloj**
  del deck (los dígitos del tiempo restante solo cambian si el track avanza) y el **vúmetro** (círculos *coloreados*
  —azul/amarillo de Traktor; los apagados son grises— que además se *mueven*: uno congelado no cuenta; mide la altura
  encendida, así que sirve con círculos nítidos o borrosos).
- **Regla de parada** (`detect.mode: "any"`): el visual **solo se detiene si el vúmetro está en silencio Y ningún deck
  avanza**. Si el track tiene un corte sin sonido pero el deck sigue corriendo, el video (sincronizado o random) sigue igual,
  sin recargarse ni reiniciarse. Con dos decks basta con que uno avance. Una pausa real se nota a los ~1.5–2 s.
  En «Sensores (debug)» (se activa en la pestaña «Desarrollador») verás el motivo, p. ej. «vúmetro en silencio pero el deck sigue avanzando».
  - `detect.signals` (`["transport"]` o `["vu"]` para usar una sola señal) · `detect.mode: "all"` exige ambas señales.
  - `detect.stall_s` (0 = desactivado): si lo subes a, p. ej., 2, el deck se da por pausado cuando su reloj lleva ese tiempo
    quieto aunque el vúmetro aún se mueva (colas de eco/reverb tras pausar).
- **Doble check**: título (similitud ≥ `title_threshold`) **y** duración total leída del deck (± tolerancia).
  Si falla → Random Visuals. Hacen falta 2 lecturas iguales para cambiar de video.
- **Sincronizado**: `velocidad = BPM_Traktor / base_bpm`; posición = duración − restante.
- **Velocidad del visual** = BPM que Traktor muestra en la cabecera del deck ÷ `base_bpm` (el BPM original del video, que
  indicas en la configuración), limitada a `sync.min_speed`…`sync.max_speed` (0.5–2.0). Ejemplo: Traktor a 133.12 BPM y
  video de 128 BPM → ×1.040. El panel muestra el cálculo («×1.040 = 133.12 (Traktor Pro 4) / 128.00 (ViDeck Pulse)»).
  Un dígito mal leído no altera la velocidad: los cambios pequeños (< `sync.bpm_tol`, 1.5 %, p. ej. mover el pitch) se
  aplican al instante y los saltos grandes solo si la lectura siguiente los confirma.
  Los Random Visuals no tienen `base_bpm`, así que van a ×1.
- **Decks en espera y Sync**: la cabecera de **todos** los decks se lee continuamente (caché por píxeles, casi gratis).
  Mientras suena el deck A, el track que cargas en B se valida ya con el doble check (título + duración) y, si activas
  **Sync** en B, su BPM pasa a ser el del master: el programa lo ve cambiar y calcula la velocidad que tendrá su video
  (BPM de B ÷ `base_bpm` de su video). El panel «Estado del Reproductor» muestra, por deck, el track, el video validado
  y la velocidad prevista. Cuando B toma el relevo (A se detiene o B suena claramente más fuerte) el video de B se
  carga al instante con su velocidad y su posición correctas, sin los 1-2 s de lecturas previas.
- **Transición entre visuales** (pestaña «Reproductor»): al cambiar de visual (archivo ↔ random, otro track o rotación del random)
  el visual siguiente se **carga detrás, ya en reproducción**, y el actual se va volviendo **transparente** hasta
  dejarlo ver (fundido cruzado real, mezclado en la GPU con OpenGL, a 60 Hz).
  - Hacia el visual de un track: `transition.duration` (2 s). Hacia un Random Visual: `transition.random_duration` (5 s). **De un Random a otro Random el cambio es siempre un corte** (con fundido cruzado y con fundido a negro); de un visual configurado a un Random, y al revés, se mantiene el fundido elegido.
  - Al primer visual (o tras una pantalla en negro por pausa) se entra desde negro en 1 s; en pausa con negro y con
    transición en «Corte» el cambio es directo.
  - Si llega otro cambio durante un fundido, este se acelera (0.3 s) y se atiende el último; si dos llegan casi a la vez,
    el intermedio ni se muestra. El visual saliente libera su decodificador al terminar.
  - La posición del video sincronizado ya es correcta desde que arranca detrás (no hace falta compensar nada).
  - `transition.style`: `crossfade` (por defecto) · `fade` (fundido a negro con mpv incrustado; respaldo si OpenGL no
    arranca, se activa solo y el panel lo indica) · `cut`. El tipo se cambia en «Reproductor» y se aplica al reiniciar.
  - **Autoprueba al arrancar**: el visor OpenGL reproduce una señal de prueba de mpv (y un video tuyo) y comprueba que la
    imagen llega a la pantalla. Si no es así, o si OpenGL no inicia, se usa el fundido a negro y la pestaña «Reproductor» avisa de que se usa el fundido a negro (el motivo está en la fila «Reproductor» del panel avanzado y en el registro).
    Si un video solo se ve sin decodificación por hardware, se pasa a `hwdec=no` automáticamente.
  - La fila «Reproductor» del panel de estado (modo avanzado) muestra el diagnóstico en vivo: OpenGL, GPU, archivo y posición de cada capa, cuadros
    dibujados, el resultado de la autoprueba del arranque y el color medio del centro de la capa y de la pantalla (si ambos son 0,0,0 la imagen está llegando negra).
  - Requiere libmpv con API de render (cualquier build normal de `libmpv-2.dll`). Si tu GPU/driver da problemas:
    `output.hwdec: "no"` o `style: "fade"`.
- **Carga de videos**: se hace con `loadfile <archivo> replace` y la posición inicial se fija antes con la opción `start` (`core/mpvutil.py`). No se usa `loadfile(..., start=…)`: desde mpv 0.38 ese argumento cambió y, según la versión, no carga nada.
- **Pausa en negro**: se hace con el ecualizador de mpv (contraste/brillo) y la pausa, sin quitar la pista de video.
- **Resolución y fps**: solo se configura la **resolución de salida** (1080p / 720p; en el visor OpenGL es la resolución
  a la que se dibuja cada visual antes de mezclarlos). No hay selector de fps: **cada video conserva sus propios fps** y
  la mezcla/animación se compone a la frecuencia de la pantalla (60 Hz). En el fundido a negro (respaldo, sin OpenGL) la
  resolución es el tamaño del Reproductor: no se aplica ningún filtro de escala (con la GPU, d3d11va, ese filtro
  fallaba con «vf: Disabling filter»).

## Pre-roll: el track aún no empieza
Si Traktor muestra un tiempo restante **mayor que la duración del track** (la cabeza está antes del inicio), y el título/duración
corresponden a un visual configurado (no Random), el programa lo interpreta como «preparándose»: el video se **carga y queda en
espera** (pantalla en negro) y arranca cuando el restante coincide con la duración total. El panel («Sincronización») muestra
«preparándose: el track empieza en N s». Para arrancar con precisión se mide el instante en que cambian los dígitos del reloj.
Con el deck en pausa antes del inicio el video espera igual. `sync.preroll_tol` (0.5 s) es la tolerancia de «mayor que».

## Revisión de la sincronización (pestaña «Sincronización»)
- **Primeros 15 s del video** (`sync.initial_window`): se revisa **cada 3 s** (`sync.initial_every`) para dejar el video lo más
  fiel posible a la música. Empiezan cuando el video arranca (o cuando termina el pre-roll).
- **Cambio de tempo detectado** (el BPM de Traktor cambia el factor de velocidad): vuelve el régimen frecuente durante
  **10 s** (`sync.tempo_window`), también cada 3 s. Solo se activa al detectar el cambio.
- **Después**: el intervalo normal `sync.check_every` (5 s). Esa revisión lee los segundos enteros (±1 s) y corrige a partir de
  `sync.resync_drift` (1.5 s). La lectura de BPM/velocidad sigue con `ocr.interval`.
- **Revisiones precisas breves** (`sync.precise_every` = 10 s, `sync.precise_window` = 5 s; hasta la update 13 `precise_every` era 25 s): durante la reproducción normal, cada 10 s se abre
  una ventana de 5 s en la que el reloj se lee en cada ciclo y la deriva se mide con la medición precisa de abajo. Así una deriva lenta
  (un BPM base algo desafinado, por ejemplo) se corrige cuando pasa de `sync.fine_drift` en vez de crecer hasta 1,5 s sin que se note.
  Cuesta poco: el OCR tiene caché por píxeles, así que Tesseract solo trabaja cuando cambia un dígito (unas 5 llamadas cada 10 s). Las
  ventanas del inicio y del cambio de tempo cuentan como revisiones precisas (la siguiente breve llega 10 s después de que acaben).
  En el panel se ve «revisión precisa (quedan N s)». `precise_every` = 0 las desactiva.
- **Medición precisa**: Traktor muestra segundos enteros, así que una lectura cualquiera tiene ±1 s de error. En las revisiones
  frecuentes y precisas el reloj se lee en cada ciclo y la posición se mide **en el instante en que cambia un dígito**:
  - **Hora real de cada fotograma**: el capturador anota cuándo se tomó cada imagen (la mitad de lo que tardó la captura con PrintWindow).
    El cambio de dígito se sitúa entre el último fotograma que mostraba el número viejo y el primero que muestra el nuevo, sin depender
    de cuándo le tocó leer al motor (antes se estimaba con la hora del ciclo menos medio fotograma). La incertidumbre queda en la mitad del
    intervalo entre fotogramas (≈0.1 s a 5 fps).
  - **Mediana de varios cambios**: se guardan los últimos 4 cambios de dígito (hasta 4,5 s) y la deriva se calcula con su mediana; un cambio
    aislado mal medido se descarta (si difiere más de 0,4 s de la mediana) y hace falta que concuerde la mayoría. En una revisión precisa
    breve se esperan 3 cambios (~3 s); en las del inicio, con 1 basta (el deck acaba de arrancar). Se olvidan al cambiar de deck o de video, al
    pausar y al cambiar el tempo.
  - Con esa medición se corrige (saltando) a partir de `sync.fine_drift` (0.25 s; antes 0.35 s). **Desde la update 14**, una deriva más chica (≥ 0.05 s) no se deja: se absorbe **sin saltar**, frenando o acelerando el video un 2 % el rato justo (un video adelantado 0.1 s se frena ~5 s); queda en el registro de sincronía como «ajuste suave» y se desactiva con `sync.slew` = `false`. **También desde la update 14**, la Δ de las revisiones de ±1 s (el reloj de Traktor da segundos enteros, así que oscila hasta ±0.5 s solo por redondeo y, con revisiones cada ~5 s, parecía que el video «se separaba» poco a poco) ya no se muestra tal cual: mientras concuerde con la última medición precisa se muestra ESA («última medición precisa»); si se aparta más de 0.6 s o pasaron más de 25 s se muestra la lectura gruesa y se pide ya una ventana de medición precisa. **También desde la update 14** (sincronía más pegada, objetivo «+0.10 s de adelanto»): (1) `sync.ahead` = 0.10: el video se deja 0.10 s POR DELANTE del track (lo que se ve «perfecto» con las letras; la Δ del panel ya lo incluye, así que lo ideal es +0.05…+0.15) y solo se corrige lo que se aparta de ese objetivo (el ajuste manual `sync.offset` se suma). (2) El adelanto del salto de cada video se corrige con la 1.ª medición precisa DESPUÉS de saltar (antes solo se aprendía de lo que tardaba el salto, y un salto de menos de un ciclo del motor no se podía medir: el video aterrizaba ~0.4 s adelantado en cada corrección). (3) Con PrintWindow la captura es de 5 fps, lo que deja ±0.1 s de incertidumbre en cada medición precisa: mientras dura una ventana de medición precisa sube a `capture.boost_fps` = 15 fps (±0.03 s; solo si cada captura tarda menos de 30 ms; 0 = no subir). Esa misma lectura rápida y precisa se mantiene en un **cambio de BPM** (ventana de `sync.tempo_window`) y en un **salto de posición en Traktor** (hot cue, clic en la onda, loop…): si el tiempo restante del track cambia más de lo que explica el tiempo transcurrido (más de 1.8 s) y la lectura siguiente lo confirma —un OCR que lee mal el reloj no cuenta—, se descartan las mediciones anteriores, se abre una ventana precisa de `sync.initial_window` y la primera corrección salta sin esperar confirmación («salto en Traktor» en el registro de sincronía) Un config.json anterior que conserva 0.35 se actualiza solo
    a 0.25; si lo cambiaste tú, se respeta.
  - El pre-roll usa la misma mediana para decidir cuándo arranca el video.
- **Saltos exactos**: todos los saltos del video (al cargarlo, al corregir la deriva, «Sincronizar ahora») se piden **«exactos»** (`seek … absolute+exact`). El salto normal de mpv
  aterriza en el **keyframe anterior** al instante pedido, y con videos de keyframes espaciados (5–10 s) eso dejaba el video hasta 3–5 s antes de lo pedido: cada corrección volvía al
  mismo keyframe y la sincronía no lograba alcanzar al track. Con el salto exacto, mpv decodifica desde el keyframe hasta el instante pedido (≈0,6–0,8 s en esos videos) y el video
  cae a ±0,05 s. Se midió en un PC real (Traktor Pro 4 + este programa): el deck recién arrancado queda alineado tras 1–2 correcciones (≈10 s) y el cambio de deck por anticipación
  entra ya alineado (Δ −0,10 s en la 1.ª revisión, sin correcciones).
- **Salto con adelanto aprendido** (`sync.seek_lead` = 0,15 s, `sync.adaptive_lead`): al corregir, el video se coloca `adelanto × velocidad` por delante de la
  posición del track, porque el salto de mpv tarda en terminar (y el track sigue avanzando). Un video con keyframes muy espaciados tarda mucho más en saltar
  que uno preparado, así que el motor **mide cuánto tardó cada salto** (con la propiedad `seeking` de mpv; sin ella, con la posición) y **aprende el adelanto de
  ese video**: sube deprisa (un salto lento se nota) y baja despacio, entre 0,05 y 1,5 s. Cada video aprende lo suyo mientras el programa esté abierto; **un video sin medidas
  parte de la mediana de lo aprendido con los demás** (todos suelen tardar parecido en saltar) en vez de partir del 0,15 s fijo.
- **Compensación de arranque aprendida** (`sync.adaptive_load`): un video recién cargado (p. ej. al pasar de un deck a otro) arrancaba ~1 s atrasado (la lectura del reloj ya tiene
  su edad y mpv tarda en empezar a avanzar) y se corregía recién a los ~4 s. Ahora la **1.ª revisión precisa** tras cargar —**1,5 s después de la carga**, ya no a los 3 s— mide ese atraso, lo corrige **de inmediato** (sin esperar
  la confirmación) y lo **aprende por video** (se adelanta el punto de arranque de la próxima carga, hasta 2,5 s; solo con medidas creíbles: Δ de 0,15 a 3 s); un video que aún no se midió usa la
  mediana de los demás **de su mismo tipo de carga**: un deck que acaba de arrancar se detecta ~2 s tarde (el video parte ~1,5 s atrasado), mientras que el visual que entra por
  anticipación se lee de cerca (~0,1 s), así que cada tipo aprende por separado. En el registro de sincronía queda como «aprende la compensación de arranque: … s».
- **Lo aprendido se guarda** (`sync.persist_learned`, por defecto sí): el adelanto del salto y la compensación de arranque de cada video van a `sync_learned.json` (junto a `config.json`;
  archivo pequeño, escritura atómica) y la próxima sesión parte de ahí: el primer deck que arranca ya sale alineado en vez de necesitar 1–2 correcciones. Si el archivo falta o está roto se
  ignora; los valores fuera de rango se acotan. Si cambias la codificación de un video, lo aprendido se va corrigiendo solo; para empezar de cero basta borrar el archivo.
- **Ajuste fino de velocidad** (`sync.trim`): si las revisiones precisas muestran una deriva **sostenida** (el video va siempre un poco más lento o más rápido que el track,
  una recta clara de al menos 4 revisiones durante 12 s o más y 0,4 % o más), en vez de saltar cada vez se corrige la **velocidad** del video un poco (hasta ±2 %). Una deriva
  puntual —un tirón, un salto de otro deck— no la mueve. Cada video empieza sin ajuste.
- **Registro de sincronía**: cada revisión (y cada carga, cambio de tempo, pre-roll, salto y ajuste fino) deja una línea con el deck, el video, la posición del video y del track,
  la diferencia, el umbral y lo que se hizo («dentro del umbral», «confirmando la deriva», «corrige: salta a …») y, después de cada salto, **cuánto tardó** y el adelanto
  nuevo. Se ve en **Desarrollador** (con «Activar Panel de Reproductor Avanzado»), con **Copiar** (incluye una cabecera con los ajustes) y **Limpiar**. Sirve para diagnosticar por qué un
  video se desvía: pásalo copiado.
- **Ajuste fino del video** (`sync.offset`, −2…+2 s): si ves que el video va siempre un poco adelantado (o atrasado) respecto a
  la música —el redondeo del reloj de Traktor puede dejar un error fijo de hasta ~0.5 s—, ajústalo aquí (+ adelanta el video).
- La deriva se confirma con una 2.ª lectura (en la misma dirección) antes de mover el video, y solo si la lectura coincide con
  el track cargado. Al reanudar tras una pausa se revisa de inmediato.
- «Corregir la deriva automáticamente» (`sync.auto_resync`), «Sincronizar ahora» y «Leer metadatos (OCR) cada» (`ocr.interval`).

## Anticipar el visual del siguiente deck
Con el track que suena por terminar, el programa vigila el **otro deck** (A o B), sin esperar a que el actual se detenga:
- **Desde que quedan 60 s** (`next.watch`): ¿hay un track cargado en el otro deck? ¿su visual está en la lista? (doble check de
  título + duración y el archivo existe). El panel («Siguiente visual») lo muestra: «Deck A termina en 38 s → Deck B «…»:
  video.mp4 ✓ · en espera (aún no empezó) · entra cuando queden 10 s».
- **Comprobaciones del deck en espera con frecuencia creciente**: cada 2 s hasta los 30 s, cada 1 s hasta los 15 s y **en cada
  ciclo** en los últimos 15 s (`next.far_every`, `next.mid_window`, `next.mid_every`, `next.fast_window`): ¿ya arrancó el deck
  que estaba en pausa?
- **Cuando quedan 10 s** (`next.lead`) **entra el siguiente visual**, con la transición configurada:
  - si su video está disponible, entra ese video (si el deck ya suena, sincronizado con su reloj; si sigue en pausa, el video
    entra desde su posición y se sincroniza en cuanto el deck arranca, sin esperar confirmación);
  - si el track cargado no tiene visual (no está en la lista, la duración no coincide o falta el archivo), entra un
    **Random Visual**;
  - si el otro deck está antes del inicio de su track (pre-roll), el visual actual sigue y el siguiente entra cuando el track
    empieza;
  - sin track cargado en el otro deck no cambia nada.
- Si rebobinas el track o cargas otro, la anticipación se cancela y se vuelve a evaluar. Se puede desactivar o ajustar en la
  pestaña «Reproductor» (ver abajo).

## Pestaña «Reproductor»: cuándo empieza el fundido y cuánto dura
Reúne todo lo que decide **cómo y cuándo cambia el visual** (la pestaña «Salida» conserva resolución, modo de la ventana, atajo,
pantalla, salida de video, decodificación y los botones de prueba):
- **Transición**: fundido cruzado · fundido a negro · corte (se aplica al reiniciar el programa). Debajo solo se dice si el fundido
  cruzado está activo (o que se usa el fundido a negro); los datos técnicos (versión de OpenGL, GPU y resultado de la autoprueba) no
  se muestran aquí: están en la fila **«Reproductor»** del panel de estado, con «Activar Panel de Reproductor Avanzado».
- **Duración del fundido (visual de un track)** y **(hacia un Random Visual)**: cuánto tarda en mezclarse (por defecto 2 s y 5 s).
- **Anticipar el visual del siguiente deck** (casilla) y **«Empezar el fundido cuando al track le queden»** (`next.lead`, de 2 a
  45 s, **10 s por defecto**): con el track que suena por terminar, el visual del otro deck (o un Random) empieza a entrar cuando
  quedan esos segundos. Con 10 s y un fundido de 2 s, el visual nuevo se ve del todo a los 8 s del final; con un Random (5 s),
  a los 5 s. Si la anticipación está desactivada, el visual solo cambia cuando el otro deck empieza a sonar y toma el mando
  (suele ser casi al final del track).
- **«Vigilar el deck siguiente cuando queden»** (`next.watch`, 60 s por defecto): desde cuándo se comprueba que el otro deck
  tiene un track cargado y que su visual está en la lista. Nunca empieza después de la anticipación: se usa el mayor entre este
  valor y la anticipación + 10 s (así, con 30 s de anticipación el deck siguiente ya está verificado cuando toca entrar).
- Debajo, una frase resume lo elegido («El fundido empieza cuando al track le queden 10 s: dura 2 s hacia el visual de un
  track (termina a los 8 s del final) y 5 s hacia un Random Visual (a los 5 s)») y avisa con ⚠ si un fundido dura más que la
  anticipación. Los cambios de anticipación y duración se aplican al instante, sin reiniciar.

## BPM con 2 decimales
El BPM se muestra y se guarda como en Traktor: 3 enteros y 2 decimales (`128.00`), en la lista de videos, la tabla de
configuración («BPM Base») y el panel (`×1.0323 = 128.00 (Traktor Pro 4) / 124.00 (ViDeck Pulse)`). La velocidad del video sigue ahora
cambios del BPM de Traktor desde 0.03 % (antes 0.3 %), así que acompaña de cerca los movimientos del pitch durante la sesión.
Las revisiones frecuentes de sincronía solo se abren cuando el tempo se mueve más de un 0.3 %.

## Lectura de la duración / «duración no coincide»
Si el recuadro (ROI) de la duración queda justo por la derecha, el OCR puede leer mal el último dígito (un «6» cortado se lee
como «1»: 02:16 → 02:11) y el track caía en Random. Ahora cada ROI de texto se lee con un **margen horizontal** y el doble check
acepta otra lectura (sin margen / margen doble) si coincide con el JSON. Si aun así no coincide, el panel lista todas las
lecturas («Traktor Pro 4 02:11 / 02:16 vs ViDeck Pulse 02:16»); en ese caso conviene recalibrar ese ROI un poco más ancho.

## Visual en pausa tras terminar un video
Al terminar un video, mpv se queda en pausa (`keep_open`) y esa pausa seguía activa al cargar el siguiente (p. ej. un Random):
la imagen quedaba congelada en el primer fotograma aunque el deck sonara. Ahora la pausa de mpv se reaplica al cargar cada visual
y se revisa mientras suena.

## Bajo consumo de CPU
- **Solo se copian los ROIs** (cabecera de cada deck + vúmetros, ~110 KB por fotograma frente a ~6 MB de la ventana
  entera). La captura se limita a `capture.max_fps` (PrintWindow: 5 fps como mucho; WGC: 15, con 10 suele bastar) y ni siquiera se lee el fotograma que sobra.
- Título, BPM y duración tienen **caché por píxeles**; el tiempo restante (cambia cada segundo) solo se lee con OCR al cargar
  un video o cuando toca revisar la sincronización: ≈ 16 llamadas a Tesseract por minuto en vez de ≈ 63.
- Una sola búsqueda de la ventana (después solo se comprueba si sigue abierta/minimizada); motor en prioridad baja;
  OpenCV y Tesseract con 1 hilo.
- PrintWindow pinta la ventana entera en cada captura (por eso se limita a 5 fps). Si diera negro, el panel lo indica y
  se puede probar WGC en la pestaña «Captura».
- **Captura más rápida (ms por captura)**: PrintWindow ya no reserva, rellena y copia ~8 MB nuevos en cada lectura. La imagen se
  pinta directamente en una memoria que se reutiliza (`CreateDIBSection`, sin `GetDIBits`) y, salvo la copia completa de cada 10 s
  (y las que piden calibrar o detectar el layout), **solo se lee desde arriba hasta la fila donde termina el ROI más bajo**: lo de
  abajo en Traktor es el explorador de música, que no se mira. La fila «Captura» del panel muestra el tiempo real
  («· 23 ms por captura»). Protecciones: si la memoria reutilizable no se puede crear o no entrega imagen, se usa la lectura clásica
  de siempre (se anota en el registro como contexto); y la primera lectura parcial se compara con la copia completa: si no coincide,
  se vuelve a leer la ventana entera.
- La carga de mpv depende del video: H.264 a 1080p es lo más ligero; con 720p el visor OpenGL dibuja menos píxeles.

## Traktor a pantalla completa y el visor a tirones
A 1920×1080 PrintWindow cuesta más (Windows entrega la ventana entera desde la GPU en cada lectura) y, a 5 lecturas por
segundo, el visor puede ir a 5-10 cuadros/s aunque el layout se detecte bien. Qué hace el programa:
- **Mide lo que tarda cada captura** y espacia solo la lectura para que la captura ocupe como mucho 1/4 del tiempo (espera ≥ 4 ×
  lo que tardó, con 1,5 s de tope): 45 ms → 5 fps como siempre; 100 ms → 2,5 fps; 250 ms → 1 fps. La fila **«Captura»** del panel
  muestra «· 85 ms por captura (espaciada a 2.8 fps para no frenar el visor)» y el registro guarda una línea de contexto.
- **Tesseract corre con prioridad por debajo de lo normal** (la detección del layout, la primera vez a pantalla completa, son
  varios segundos de un núcleo al 100 %): no le quita CPU al visor, a mpv ni a Traktor.
- La comprobación de «¿imagen negra?» de PrintWindow mira una muestra de píxeles en vez de promediar toda la imagen.
- El estado del visor muestra los **cuadros/s reales** que dibuja («1234 cuadros dibujados (58 cuadros/s)»): activa «Activar Panel
  de Reproductor Avanzado» (pestaña Desarrollador) y míralo en la fila «Reproductor». Con un video a 60 fps debería marcar ~60; si
  marca 5-10, el cuello de botella está en la GPU/ventana y no en el layout.
- Si aun así hay tirones: **Captura → «Lectura de la ventana»** a 2 fps (o prueba WGC si no transmites por Discord/OBS) y, en
  **Salida**, resolución 720p. Después de la primera detección en pantalla completa el layout queda guardado y volver a ese
  tamaño es inmediato.

## Ventana minimizada
Funciona aunque haya otras ventanas encima, pero **no si Traktor está minimizada**: el motor conserva el último estado,
avisa en «Captura» y retoma al restaurar. Si cambia el tamaño de la ventana o el método de captura respecto a la
calibración, en **layout automático** los ROIs se recalculan solos; en **manual** el motor avisa y debes recalibrar (o pulsar
«Detectar layout ahora»). Un `config.json` antiguo se migra en modo «pantalla completa».

## Si la calibración no puede capturar Traktor
El mensaje «No se puede capturar Traktor — …» ahora termina con **(paso: …)**: indica en qué punto se quedó la captura
(«buscando la ventana de Traktor», «abriendo WGC», «abriendo PrintWindow» o «capturando»). Pasa ese texto si se repite. WGC
tiene hasta 7 s para abrirse; si no entrega imagen, se usa PrintWindow. Al cerrar el diálogo de calibración se libera la
captura (ya no quedan hilos de captura colgados). Traktor no puede estar minimizada.

## Ajustes finos (config.json)
`ocr.time_scaled_by_tempo` (true si el restante cambia con el pitch; compruébalo a +4%), `vu.sat_min` / `vu.val_min`
(cuánto color y brillo debe tener un círculo para contar como encendido; si tus círculos quedan muy tenues, baja `val_min`),
`capture.layout` (`auto` | `manual`), `autolink.enabled` / `autolink.min_title` / `autolink.fill_bpm` (autovinculación), `sync.bpm_tol` (tolerancia del filtro de BPM), `next.enabled` / `next.watch` / `next.lead` / `next.far_every` / `next.mid_window` / `next.mid_every` / `next.fast_window` (anticipación del siguiente deck), `sync.initial_every` / `sync.initial_window` / `sync.tempo_window` / `sync.offset` / `sync.fine_drift` / `sync.preroll_tol` (revisión frecuente, ajuste fino y pre-roll), `transition.style` / `transition.duration` / `transition.random_duration`, `output.hwdec`, `output.window_mode` / `output.screen` / `output.mode_hotkey` (modo, pantalla y atajo de teclado del Reproductor), `vu.min_lit_px` / `vu.change_px` / `vu.pix_delta` / `vu.recent` (sensibilidad y movimiento del vúmetro), `detect.mode` / `detect.stall_s` (regla de parada), `capture.window_title` (proceso o título; el proceso tiene
prioridad), `monitor` (índice mss en modo pantalla), `sync.fine_drift` / `sync.precise_every` / `sync.precise_window` (umbral y ritmo de las revisiones precisas), `ui.layout_mode` («Modo de Ventana»: `auto` | `full` | `compact`), `dev.log_enabled` / `dev.status_bar` / `dev.show_sensors` / `dev.advanced_player` (registro de errores, barra de estado, fila
«Sensores (debug)» y detalle de la fila «Reproductor»), `prepare.ffmpeg_cmd` / `prepare.max_height` («Preparar videos»), `sync.seek_lead` / `sync.adaptive_lead` / `sync.adaptive_load` / `sync.persist_learned` / `sync.trim` (adelanto del salto, aprendizaje por video, compensación de arranque y ajuste fino de velocidad), `components.*` (ffmpeg, libmpv y Legacy Mode; `components.ffmpeg.auto_base` descarga el ffmpeg base si falta).

## Nombres en los mensajes del panel
Para distinguir los dos programas: **Traktor Pro 4** es lo que lee el OCR de la ventana de Traktor Pro 4 (título, BPM, duración,
reloj) y **ViDeck Pulse** es lo que guarda este programa (`tracks_db.json`: título, BPM base y duración de cada video).

## Licencia
Copyright (C) 2026 JFinalXDark.

ViDeck Pulse es software libre: **GPL-3.0-or-later** (el texto completo está en `LICENSE`; cada archivo `.py` lo indica en sus primeras líneas con
`# SPDX-FileCopyrightText: 2026 JFinalXDark` y `# SPDX-License-Identifier: GPL-3.0-or-later`). Puedes usarlo, estudiarlo, modificarlo y
redistribuirlo bajo los términos de la GPL versión 3 o, a tu elección, cualquier versión posterior. Se distribuye con la esperanza de que sea útil,
pero **sin ninguna garantía**, ni siquiera la implícita de comerciabilidad o de idoneidad para un fin concreto. Los programas y bibliotecas que usa (mpv/libmpv, ffmpeg, Tesseract, PySide6…) tienen sus propias
licencias: ver `THIRD_PARTY_NOTICES.md`. «Traktor» es una marca de Native Instruments; este proyecto es independiente y no tiene relación con ellos.
