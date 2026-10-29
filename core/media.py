# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import cv2

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def scan_videos(folder):
    Path(folder).mkdir(parents=True, exist_ok=True)
    return sorted(os.path.abspath(str(p)) for p in Path(folder).rglob("*")  # abspath y no resolve(): una unidad de red mapeada (Z:) debe seguir siendo «Z:»
                  if p.suffix.lower() in VIDEO_EXTS)


def video_duration(path):
    """Duración exacta en segundos (frames / fps) o None."""
    cap = cv2.VideoCapture(path)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        return round(frames / fps, 2) if fps > 0 and frames > 0 else None
    finally:
        cap.release()


def same(a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
