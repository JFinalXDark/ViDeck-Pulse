# Avisos de terceros / Third-party notices

Copyright (C) 2026 JFinalXDark. ViDeck Pulse es software libre bajo **GPL-3.0-or-later** (ver `LICENSE`). Usa los siguientes componentes de terceros, cada uno bajo su propia
licencia. Esta lista es informativa: **verifica la licencia vigente en la página oficial de cada proyecto antes de publicar una versión.**
No es asesoría legal. / Copyright (C) 2026 JFinalXDark. ViDeck Pulse is free software under **GPL-3.0-or-later** (see `LICENSE`). It uses the third-party components below,
each under its own license. This list is informational: **check each project's current license on its official page before a release.**
This is not legal advice.

## Paquetes de Python (se instalan con `pip` o van dentro del ejecutable) / Python packages
| Componente | Para qué sirve | Licencia (a verificar) | Sitio |
|---|---|---|---|
| PySide6 (Qt for Python) | Interfaz gráfica | LGPL-3.0 (también GPL / comercial) | https://doc.qt.io/qtforpython-6/ |
| python-mpv | Enlace con libmpv | GPL-2.0-or-later o LGPL-2.1-or-later | https://github.com/jaseg/python-mpv |
| opencv-python / opencv-python-headless | Análisis de imagen | MIT (el paquete) + Apache-2.0 (OpenCV); el binario incluye otras bibliotecas (p. ej. FFmpeg bajo LGPL) | https://github.com/opencv/opencv-python |
| NumPy | Cálculo numérico | BSD-3-Clause | https://numpy.org |
| mss | Captura de pantalla (modo pantalla) | MIT | https://github.com/BoboTiG/python-mss |
| pytesseract | Enlace con Tesseract | Apache-2.0 | https://github.com/madmaze/pytesseract |
| windows-capture (opcional, método WGC) | Windows Graphics Capture | MIT | https://github.com/NiiightmareXD/windows-capture |

## Programas externos que **NO** se incluyen en este repositorio / External programs **not** shipped in this repository
El programa los descarga o los usa si el usuario los instala (gestor de componentes); cada usuario es responsable de su descarga e instalación.
The program downloads or uses them if the user installs them (component manager); each user is responsible for downloading and installing them.

| Componente | Para qué sirve | Licencia (a verificar) | Sitio oficial |
|---|---|---|---|
| libmpv (mpv) | Motor de video | GPL-2.0-or-later o LGPL-2.1-or-later según la compilación | https://mpv.io |
| FFmpeg | «Preparar videos» y análisis de compatibilidad | LGPL-2.1-or-later o GPL-2.0/3.0-or-later según la compilación | https://ffmpeg.org |
| Tesseract OCR | Lectura de títulos, BPM y tiempos | Apache-2.0 | https://github.com/tesseract-ocr/tesseract |

Las versiones concretas probadas con cada versión estable (con su SHA-256) se anotan en `tested_components.json` y en el CHANGELOG.
The exact versions tested with each stable release (with SHA-256) are listed in `tested_components.json` and in the CHANGELOG.

## Marcas / Trademarks
«Traktor» y «Native Instruments» son marcas de Native Instruments GmbH. «Serato DJ» es marca de Serato Audio Research Limited.
«VirtualDJ» es marca de Atomix Productions. ViDeck Pulse **no tiene relación con ninguna de esas empresas ni cuenta con su respaldo**.
Se nombra «Traktor Pro 4» solo para indicar con qué programa es compatible.
“Traktor” and “Native Instruments” are trademarks of Native Instruments GmbH. “Serato DJ” is a trademark of Serato Audio Research Limited.
“VirtualDJ” is a trademark of Atomix Productions. ViDeck Pulse is **not affiliated with or endorsed by any of these companies**.
“Traktor Pro 4” is named only to state which program it is compatible with.
