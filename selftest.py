"""Smoke test for packaged builds:  Moodboard.exe --self-test [result.txt]

A broken bundle usually lacks a Qt image plugin or a library that is only
imported lazily, so this exercises those plus a save/load/export round trip.
Exit code 0 means OK. A windowed .exe has no console, so the outcome is also
written to `result.txt` when given.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import traceback
from pathlib import Path

REQUIRED_IMAGE_FORMATS = {"png", "jpg", "gif", "bmp", "webp", "ico", "tiff"}


def run(window_class, result_path: str | None = None) -> int:
    # The window class is passed in because main.py is the frozen app's entry
    # script, not an importable module.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window flash during builds
    lines: list[str] = []
    try:
        from PIL import Image
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QImageReader
        from PySide6.QtWidgets import QApplication

        import ai
        import storage
        from canvas import BACKGROUND

        app = QApplication.instance() or QApplication(sys.argv[:1])
        formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
        missing = REQUIRED_IMAGE_FORMATS - formats
        if missing:
            raise RuntimeError(f"Qt image plugins missing: {sorted(missing)}")
        lines.append("image formats: ok")

        buf = io.BytesIO()
        image = Image.new("RGB", (120, 80), (200, 60, 40))
        image.paste((30, 90, 200), (60, 0, 120, 80))
        image.save(buf, "JPEG")
        jpeg = buf.getvalue()
        colors = ai.extract_palette(jpeg, 5)
        if len(colors) != 5:
            raise RuntimeError(f"palette extraction returned {colors}")
        lines.append(f"palette extraction: ok {colors}")

        window = window_class()  # never shown or closed, so saved settings aren't touched
        added, failed = window.scene.add_images([(jpeg, "jpg", "test.jpg")], QPointF(0, 0))
        if len(added) != 1 or failed:
            raise RuntimeError("could not add a JPEG to the board")
        window.palette.add_colors(colors)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Test.moodboard"
            storage.save_board(folder, window.scene.board_items(), window.palette.to_data(), window.view.view_state())
            loaded = storage.load_board(folder)
            if len(loaded.items) != 1 or loaded.warnings:
                raise RuntimeError(f"board round trip failed: {loaded.warnings}")
            width, height = storage.export_png(window.scene, Path(tmp) / "export.png", BACKGROUND)
        lines.append(f"save/load/export: ok ({width}x{height} PNG)")
        window.undo_stack.clear()
        app.processEvents()
        result = 0
    except Exception:
        lines.append(traceback.format_exc())
        result = 1
    lines.append("SELF-TEST " + ("PASSED" if result == 0 else "FAILED"))
    text = "\n".join(lines)
    if result_path:
        Path(result_path).write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        print(text)
    return result
