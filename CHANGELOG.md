# Registro de cambios / Changelog

El formato sigue «Keep a Changelog». Hasta la versión estable, cada **update** es un paso dentro de la Beta en curso
(5 updates = 1 Beta). / Until the stable release, each **update** is a step inside the current Beta (5 updates = 1 Beta).

## v2.00 u1 (estable / stable) — 2026-10-29
Primera versión estable (update 14). / First stable release (update 14).
- **Instalador y versión portable** (GitHub Releases: `ViDeckPulse-Setup-2.0.0-u1.exe`, `ViDeckPulse-2.0.0-u1-portable.zip` y `SHA256SUMS.txt`). El instalador pregunta
  el idioma (español / inglés) y lo deja como idioma del programa; la versión portable toma el idioma de Windows la primera vez. El instalador no está firmado
  digitalmente (SmartScreen puede avisar). / Installer and portable build; the installer asks for the language; unsigned.
- **Ventana «Componentes necesarios» en el primer inicio**: ffmpeg, libmpv y Tesseract no se incluyen; la ventana dice qué falta, de dónde viene y su licencia, descarga
  y verifica (SHA-256) lo que elijas y permite usar un archivo ya descargado. / First-run components window (nothing third-party is bundled).
- **Aviso de actualización**: una sola vez al iniciar (y a mano desde «Componentes») mira si hay una versión estable más nueva en este repositorio y solo avisa;
  no descarga ni instala nada. / Update notice: once at startup or from “Components”; notifies only.
- **Carpeta de videos fuera de la instalación** (`Videos\ViDeck Pulse\Videos` en la instalada, `Videos` junto al programa en la portable), con «Cambiar ruta…».
  «Preparar videos» siempre convierte dentro de esa carpeta; las medidas se muestran en MB o GB y se borran solos los `.verificando` que queden de una
  conversión interrumpida. / Videos folder outside the installation; “Prepare videos” works in place; sizes in MB/GB; stale temporary files are cleaned.
- **Visuales Random**: entre dos Random el cambio es siempre un corte; de un visual configurado a un Random (y al revés) se respeta la transición elegida. / Random → Random is always a cut.
- **Sincronía más pegada** (el video se deja 0.10 s por delante del track, `sync.ahead`): el adelanto del salto de cada video se corrige con la primera medición precisa
  posterior (antes aterrizaba ~0.4 s adelantado tras cada corrección), las revisiones precisas breves pasan de cada 25 s a cada 10 s, la captura de PrintWindow sube de 5 a
  15 fps mientras se mide con precisión (`capture.boost_fps`), una deriva pequeña se absorbe sin saltar frenando o acelerando el video un 2 % (`sync.slew`), un salto de posición
  en Traktor (hot cue, loop…) se detecta y se vuelve a sincronizar al instante, y el panel ya no muestra la Δ de ±1 s (solo redondeo del reloj) mientras haya una medición precisa
  reciente. / Tighter sync: jump-lead correction, precise checks every 10 s, 15 fps capture while measuring, gentle speed slew, Traktor position jumps detected.
- «Panel de Reproductor Avanzado» desactivado por defecto; nombre de producto de Native Instruments: «Traktor Pro 4» (soporte actual) y «Traktor Pro 3» / «Traktor Play» (sin soporte). / Advanced player panel off by default.

## Update 13
- Ajustes → «Modo de Ventana»: botón **«Automático»** (elige Completo o Compacto según el tamaño de la ventana). / New “Automático” button.
- **Ícono nuevo** (monograma «V» con línea de pulso). / New app icon.
- **Carpeta de datos y rutas relativas**: `config.json`, `tracks_db.json` y `sync_learned.json` guardan las rutas relativas a la carpeta de
  datos (junto al programa en la versión portable; `%APPDATA%\ViDeck Pulse` en la instalada), así que mover o copiar la carpeta no rompe
  los vínculos; los archivos viejos se migran solos y se guarda una copia `*.u12.bak`. / Relative paths + data folder.
- **Búsqueda de videos automática y progresiva** (en un hilo aparte, sin congelar la ventana): al iniciar, con el motor detenido y al volver
  a la ventana; detecta videos nuevos, quitados y renombrados (conserva los vínculos) y espera a los que aún se copian. Se desactiva en
  Ajustes. / Automatic, non-blocking video discovery.
- **«Preparar videos» en la misma carpeta**: el convertido reemplaza al original (que va a la Papelera de su unidad; borrado definitivo solo si la unidad
  no tiene Papelera y tras avisar) o se guarda al lado como `nombre [Converted].mp4`. Antes de tocar el original se verifica el convertido (ffprobe, audio,
  duración, h264/yuv420p, fotogramas, decodificación completa, SHA-256), no se pisa ningún archivo y, si algo falla, se deshace todo. Los vínculos pasan
  al convertido con copia `*.antes-de-traspasar.bak`. / “Prepare videos” in place: verified replace-or-keep, Recycle Bin, links moved, everything undone on failure.
- **Ícono definitivo**: «V» con línea de pulso y arco rojo de deck. / Final icon with the red deck arc.
- La barra del **vúmetro** muestra «(quieto)» si hay barras encendidas pero sin moverse. / VU bar shows “(still)” when lit but not moving.
- Arreglo: un archivo de ajustes ajeno ya no se reconoce como `config.json` de ViDeck Pulse tras migrarlo. / Fix: foreign settings files are no longer mistaken for ours.

## v2.0 (Beta 3) — Update 12
- **Nombre nuevo: «ViDeck Pulse»** (antes: «Traktor Visuals») / **New name: “ViDeck Pulse”** (formerly “Traktor Visuals”).
  Cambia el título de todas las ventanas; si capturas el Reproductor con OBS o Discord por su título, vuelve a elegir la ventana.
  The window titles changed; if you capture the Player window by title in OBS/Discord, pick it again.
- Soporte actual: **Traktor Pro 4**. / Current support: **Traktor Pro 4**.
- «Reproductor» (ES) / «Player» (EN) sustituye a «ventana de reproducción» / “playback window”.
- Mayúscula inicial y ortografía revisadas en español e inglés. / Capitalization and spelling reviewed in Spanish and English.
- Ajustes → «Modo de Ventana»: botones «Completo» / «Compacto» (a mano) sin quitar la disposición adaptable.
- Revisión 2: en modo Compacto el panel «Estado del Reproductor» ocupa todo el alto de la derecha (filas con aire), columnas 9/11,
  pestañas más bajas y los cuatro botones de abajo en dos filas; «PrintWindow» / «WGC» con su nombre propio en la fila «Captura».

## v2.0 (Beta 3) — Updates anteriores / earlier updates
- Ver el README, apartado «Versión de ViDeck Pulse». / See the README, section “Versión de ViDeck Pulse”.
