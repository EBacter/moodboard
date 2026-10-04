"""Persistence: boards are saved as portable project folders.

    MyBoard.moodboard/
        board.json        items, palette and view position
        images/           copies of every image, named by content hash

Hash names mean the same picture is stored once however often it's used,
and re-saving only writes images that aren't already there.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QRectF, QSettings
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsScene

from items import ImageItem, item_from_dict, item_to_dict

BOARD_FILE = "board.json"
IMAGES_DIR = "images"
BOARD_SUFFIX = ".moodboard"
FORMAT_VERSION = 1
MAX_RECENT = 8
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
EXPORT_MAX_SIDE = 8000
# Only files matching this pattern are ours to clean up inside images/.
_HASHED_NAME = re.compile(r"^[0-9a-f]{16}\.[a-z0-9]+$")


class BoardError(Exception):
    """A user-facing problem reading or writing a board."""


@dataclass
class LoadedBoard:
    items: list  # top-level items, not yet in a scene
    created: list  # every item object built (incl. group children)
    palette: object  # raw palette data; PalettePanel.load_data validates it
    view: dict
    warnings: list[str] = field(default_factory=list)


def is_board_folder(path: str | Path) -> bool:
    return (Path(path) / BOARD_FILE).is_file()


def save_board(folder: str | Path, items: list, palette, view_state: dict) -> None:
    folder = Path(folder)
    if folder.exists() and not folder.is_dir():
        raise BoardError(f"{folder} exists and is not a folder.")
    # Never write into (and later tidy up) an unrelated non-empty folder.
    if folder.is_dir() and any(folder.iterdir()) and not is_board_folder(folder):
        raise BoardError(f"{folder} already exists and isn't a mood board folder.")

    images_dir = folder / IMAGES_DIR
    images_dir.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()

    def save_image(item: ImageItem) -> str:
        name = f"{hashlib.sha1(item.data).hexdigest()[:16]}.{item.ext}"
        target = images_dir / name
        if not target.exists():
            target.write_bytes(item.data)
        used.add(name)
        return f"{IMAGES_DIR}/{name}"

    doc = {
        "format": "moodboard",
        "version": FORMAT_VERSION,
        "view": view_state,
        "palette": palette,
        "items": [d for d in (item_to_dict(i, save_image) for i in items) if d],
    }
    # Write-then-rename so a crash mid-save can't corrupt the existing board.
    tmp = folder / (BOARD_FILE + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / BOARD_FILE)

    # Remove image copies that are no longer referenced.
    for path in images_dir.iterdir():
        if path.is_file() and _HASHED_NAME.match(path.name) and path.name not in used:
            try:
                path.unlink()
            except OSError:
                pass


def load_board(folder: str | Path) -> LoadedBoard:
    folder = Path(folder)
    if folder.is_file() and folder.name == BOARD_FILE:
        folder = folder.parent
    board_file = folder / BOARD_FILE
    if not board_file.is_file():
        raise BoardError(f"{folder} isn't a mood board folder (no {BOARD_FILE} inside).")
    try:
        doc = json.loads(board_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BoardError(f"Couldn't read {board_file}:\n{exc}") from exc
    if not isinstance(doc, dict):
        raise BoardError(f"{board_file} is not a valid board file.")

    root = folder.resolve()
    warnings: list[str] = []

    def load_image(rel: str) -> bytes | None:
        target = (folder / rel).resolve()
        if not target.is_relative_to(root):  # ignore paths escaping the board folder
            warnings.append(f"Ignored image outside the board folder: {rel}")
            return None
        try:
            return target.read_bytes()
        except OSError:
            warnings.append(f"Missing image: {rel}")
            return None

    created: list = []
    items = []
    for data in doc.get("items", []):
        if isinstance(data, dict):
            item = item_from_dict(data, load_image, created, warnings)
            if item is not None:
                items.append(item)
    palette = doc.get("palette")  # {"groups": [...], "active": i}, or a flat list in older boards
    view = doc.get("view") if isinstance(doc.get("view"), dict) else {}
    return LoadedBoard(items, created, palette, view, warnings)


def export_png(scene: QGraphicsScene, path: str | Path, background: QColor, margin: float = 40) -> tuple[int, int]:
    """Render every item on the board to a PNG (2x resolution where it fits)."""
    rect = scene.itemsBoundingRect()
    if rect.isEmpty():
        raise BoardError("The board is empty - nothing to export.")
    rect = rect.adjusted(-margin, -margin, margin, margin)
    scale = min(2.0, EXPORT_MAX_SIDE / max(rect.width(), rect.height()))
    width, height = max(1, round(rect.width() * scale)), max(1, round(rect.height() * scale))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(background)
    painter = QPainter(image)
    painter.setRenderHints(
        QPainter.RenderHint.Antialiasing
        | QPainter.RenderHint.SmoothPixmapTransform
        | QPainter.RenderHint.TextAntialiasing
    )
    scene.render(painter, QRectF(0, 0, width, height), rect)
    painter.end()
    if not image.save(str(path), "PNG"):
        raise BoardError(f"Couldn't write {path}.")
    return width, height


# --------------------------------------------------------------------------- #
# Image sources
# --------------------------------------------------------------------------- #
def read_image_file(path: str | Path) -> tuple[bytes, str, str]:
    path = Path(path)
    return path.read_bytes(), path.suffix.lstrip("."), path.name


def download(url: str) -> bytes:
    """Fetch an image dragged in from a web browser."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Moodboard)"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("image is too large")
    return data


# --------------------------------------------------------------------------- #
# Recent boards
# --------------------------------------------------------------------------- #
def _settings() -> QSettings:
    return QSettings(
        QCoreApplication.organizationName() or "Moodboard",
        QCoreApplication.applicationName() or "Moodboard",
    )


def recent_boards() -> list[str]:
    value = _settings().value("recentBoards", [])
    if isinstance(value, str):  # QSettings returns a bare string for 1-item lists
        value = [value]
    return [p for p in (value or []) if is_board_folder(p)]


def add_recent(path: str | Path) -> None:
    path = str(Path(path).resolve())
    entries = [path] + [p for p in recent_boards() if Path(p).resolve() != Path(path)]
    _settings().setValue("recentBoards", entries[:MAX_RECENT])


def clear_recent() -> None:
    _settings().setValue("recentBoards", [])
