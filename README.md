<p align="center">
  <img src="docs/logo.png" width="200" alt="ViDeck Pulse logo">
</p>

<h1 align="center">ViDeck Pulse</h1>

<p align="center"><strong>Tempo-synced visuals for DJ sets.</strong></p>

ViDeck Pulse follows your **Traktor Pro 4** decks: it reads each deck's title, BPM and clock from the Traktor window (OCR + pixel analysis, no MIDI) and plays tempo-synced videos with mpv, with OpenGL crossfades and random visuals. Windows 10/11. Independent project, not affiliated with Native Instruments.

> **Status:** Stable v2.00 u1. Current support: **Windows 10/11 + Traktor Pro 4, with 2 decks (A and B)**. Traktor's 4-deck mode is not supported yet. [Leer en español](README.es.md).

<p align="center">
  <a href="https://github.com/JFinalXDark/ViDeck-Pulse/releases/latest"><img src="https://img.shields.io/github/v/release/JFinalXDark/ViDeck-Pulse?label=download&color=e0283a&display_name=tag&labelColor=0b0f1a&style=flat" alt="download"></a>
  <a href="https://github.com/JFinalXDark/ViDeck-Pulse/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/JFinalXDark/ViDeck-Pulse/ci.yml?label=CI&labelColor=0b0f1a&style=flat" alt="CI"></a>
  <a href="https://github.com/JFinalXDark/ViDeck-Pulse/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-2f8fd8?labelColor=0b0f1a&style=flat" alt="license"></a>
  <a href="#requirements"><img src="https://img.shields.io/badge/platform-Windows%2010%2F11-2f8fd8?labelColor=0b0f1a&style=flat" alt="platform"></a>
  <a href="#requirements"><img src="https://img.shields.io/badge/Traktor-Pro%204-d04ac0?labelColor=0b0f1a&style=flat" alt="Traktor Pro 4"></a>
  <a href="#requirements"><img src="https://img.shields.io/badge/decks-2%20(A%20%2B%20B)-d04ac0?labelColor=0b0f1a&style=flat" alt="2 decks (A and B)"></a>
  <a href="https://github.com/JFinalXDark/ViDeck-Pulse/blob/main/THIRD_PARTY_NOTICES.md"><img src="https://img.shields.io/badge/components-not%20bundled-6b7280?labelColor=0b0f1a&style=flat" alt="components"></a>
</p>

## What it does

You keep one video per track (named after the track). When you load a track on a deck and press play, ViDeck Pulse finds its video, starts it at the right position, and speeds it up or slows it down so it follows the tempo Traktor shows. When the next deck takes over, the visual changes with a crossfade. Tracks without a video get a random visual, so the screen is never empty.

It works by **looking at the Traktor window**, not by listening to audio or talking to Traktor: no MIDI, no audio capture, no plug-in.

## Features

### Follows your decks

- **Reads only the deck and mixer areas** of the Traktor window (title, BPM, remaining time, level meter) using OCR and pixel analysis.
- **Knows what is really playing**: it combines the deck clock (the remaining-time digits only change while the track advances) with the level meter. The visual stops only when the meter is silent **and** no deck is advancing, so a silent break in a track does not interrupt the video.
- **Automatic layout detection**: it finds the reading areas by itself, also when Traktor is maximized or full screen, and can be recalibrated by hand.
- **Gentle on Traktor**: it only reads the window image. It does not move, focus, minimize or modify the Traktor window, does not inject code and does not use Desktop Duplication, so it does not compete with Discord or OBS for the window capture when you stream the Traktor window.

### Tempo sync

- **Speed follows the BPM**: playback speed = Traktor BPM / the video's base BPM (limited to 0.5x to 2x). Small tempo moves apply instantly; a misread digit never changes the speed.
- **Position sync**: the video is placed where the track is (track length minus remaining time) and corrected with precise seeks when it drifts; the checks become more frequent around starts and changes.
- **Waiting deck aware**: while deck A plays, the track loaded on deck B is already validated, and with Sync active its new BPM is anticipated, so B's video starts at the right speed and position when it takes over.
- **Next visual in advance**: when the current track is about to end, the next deck's visual is prepared and enters with the configured transition.

### Tracks and videos

- **Auto-link**: loads a track on a deck and, if a video's name matches the title (and the duration matches too), it links them automatically. Every link can be edited by hand.
- **Double check** (title and duration) before a video is used, so a similar name never shows the wrong visual.
- **Random visuals** for tracks without a video, with their own transition.
- **Automatic video discovery**: new, removed and renamed videos are detected without freezing the window, and links follow renamed files.
- **Color-coded list**: configured visual, pending BPM, random visual.

### Player

- **Real crossfade on the GPU** (OpenGL, 60 Hz) between visuals; crossfade, fade-to-black or cut. A start-up self-test checks that the image actually reaches the screen and falls back automatically if it does not.
- **Three window modes**: Window, Borderless (no title bar, handy when sharing the window) and Full screen on the screen you choose; optional hotkey to switch.
- **Output resolution** 1080p or 720p; each video keeps its own frame rate.
- **Video health alerts**: warns when a video runs with poor performance and detects a black screen.

### Prepare videos

- Analyzes your videos (ffprobe) and tells you which ones will play smoothly and which should be converted (constant frame rate, frequent keyframes, H.264 8-bit 4:2:0).
- Converts with ffmpeg keeping the **same video bitrate**; audio is copied untouched.
- **Convert in place, safely**: the converted file replaces the original (which goes to the **Windows Recycle Bin**) or is saved next to it as `name [Converted].mp4`. Before touching the original, the new file is verified (duration, codec, frames, audio, full decode, SHA-256), nothing is ever overwritten, track links move to the converted file, and everything is undone if a step fails.

### Comfort

- **Status panel** per deck (track, validated video, expected speed, sync state), a status bar with warnings and errors, and an optional error log.
- **Spanish and English** interface, switchable from Settings; the installer asks for the language and uses it as the program's language.
- **Adaptive windows** for small screens (Full / Compact modes, automatic or manual).
- **Low CPU usage** and low-priority background work.
- **Components manager**: ffmpeg and libmpv are downloaded from known public sources, verified with SHA-256 and kept in the program's own folder; **Legacy Mode** can go back to the versions each release was tested with.
- **Update notice**: once at startup (and from the Components window) it checks whether a newer stable release exists in this repository and only tells you; it never downloads or installs anything by itself.
- **Portable or installed**: data live next to the program (portable) or in `%APPDATA%\ViDeck Pulse` (installed), with relative paths so you can move the folder. The default videos folder is `Videos\ViDeck Pulse\Videos` inside your Windows Videos folder (installed) or a `Videos` folder next to the program (portable); change it with **Change path…**.

## Requirements

- Windows 10 or 11.
- **Traktor Pro 4** (the window the program reads), using **2 decks (A and B)**: the 4-deck layout is not supported yet.
- **Tesseract OCR**, **libmpv** and **ffmpeg** (used for "Prepare videos"). They are **not included** in this repository or its releases: the first-run window lists what is missing, where it comes from and its license, and you decide whether to install it.
- To run from source: Python 3.12 and `pip install -r requirements.txt`, then `python main.py`.

## Quick start

1. Download the installer or the portable zip from [Releases](../../releases) and verify its SHA-256 against `SHA256SUMS.txt`.
2. Open ViDeck Pulse, choose the folder with your videos and press **Start engine** with Traktor visible.
3. Load a track on a deck: if a video matches its title and duration, it is linked automatically (otherwise link it in Settings). Write the video's base BPM once.
4. Press play on the deck. Share or full-screen the **ViDeck Pulse - Player** window.

> [!NOTE]
> SmartScreen may warn about the Windows installer the first time, because it isn't code-signed yet: choose **More info**, then **Run anyway**. The portable zip needs no installation.

## Not included / not promised

**Decks C and D are not supported yet:** to date the program follows 2 decks (A and B), although Traktor can show 4. The code is open source: anyone may adapt it to other DJ software, but **support or compatibility with other programs or versions is not promised** (for example Traktor Pro 3, Traktor Play or other brands).

## License

**GPL-3.0-or-later.** Copyright (C) 2026 JFinalXDark. You may use, study, modify and redistribute it under the terms of the GNU General Public License, version 3 or (at your option) any later version. It is distributed in the hope that it will be useful, but without any warranty. See `LICENSE`. Third-party components and their licenses are listed in `THIRD_PARTY_NOTICES.md`.

*"Traktor" is a trademark of Native Instruments. ViDeck Pulse is an independent project and has no relation to Native Instruments.*
