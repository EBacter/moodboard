"""Board item types (images, text notes, groups) and their JSON (de)serialisation.

Every item is placed with a plain ``pos`` + uniform ``scale`` (no rotation),
which keeps resizing, grouping and saving simple. Selection outlines and
resize handles are drawn by the view, so items never paint their own.
"""
from __future__ import annotations

import io
import math
from pathlib import PurePosixPath
from typing import Callable, Iterator

from PIL import Image, UnidentifiedImageError
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QTextCursor, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsTextItem,
    QStyle,
    QStyleOptionGraphicsItem,
)

# Images larger than this are downsampled for display; the original bytes are
# still what gets saved, so nothing is lost.
MAX_DISPLAY_SIDE = 4096

NOTE_COLORS = {
    "Yellow": "#FFF4B8",
    "White": "#FFFFFF",
    "Pink": "#FFDCE4",
    "Blue": "#DCEBFF",
    "Green": "#DDF4DD",
    "Lavender": "#E9E1FF",
}
DEFAULT_NOTE_COLOR = NOTE_COLORS["Yellow"]
EDIT_OUTLINE = QColor("#2F80ED")

_MOVABLE_SELECTABLE = (
    QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
)


# --------------------------------------------------------------------------- #
# Image decoding helpers
# --------------------------------------------------------------------------- #
def decode_image(data: bytes, ext_hint: str = "") -> tuple[QImage, bytes, str]:
    """Decode image bytes. Returns (image, bytes_to_store, extension).

    Formats Qt can't read are converted to PNG via Pillow once, so the stored
    bytes are always loadable the next time the board is opened.
    """
    image = QImage.fromData(data)
    if not image.isNull():
        return image, data, _image_ext(data, ext_hint)
    try:
        with Image.open(io.BytesIO(data)) as pil:
            pil.load()
            converted = pil.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("unsupported or corrupt image") from exc
    buf = io.BytesIO()
    converted.save(buf, "PNG")
    png = buf.getvalue()
    image = QImage.fromData(png)
    if image.isNull():
        raise ValueError("unsupported or corrupt image")
    return image, png, "png"


def _image_ext(data: bytes, hint: str) -> str:
    try:
        with Image.open(io.BytesIO(data)) as pil:
            fmt = (pil.format or "").lower()
    except Exception:
        fmt = ""
    if fmt == "jpeg":
        fmt = "jpg"
    return fmt or hint.lower().lstrip(".") or "png"


def qimage_to_png(image: QImage) -> bytes:
    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(array.data())


def _without_selection(option: QStyleOptionGraphicsItem) -> QStyleOptionGraphicsItem:
    """Copy of `option` with the selected/focus state removed, so Qt's built-in
    dashed selection rectangle isn't drawn (the view draws its own)."""
    plain = QStyleOptionGraphicsItem(option)
    plain.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
    return plain


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #
class ImageItem(QGraphicsItem):
    """A bitmap on the board. Keeps the original encoded bytes for saving."""

    def __init__(self, data: bytes, ext: str = "", image: QImage | None = None, name: str = ""):
        super().__init__()
        if image is None:
            image, data, ext = decode_image(data, ext)
        self.data = data
        self.ext = (ext or "png").lower()
        self.name = name  # source file name, used to name extracted palettes
        # Local coordinates are always the original pixel size, even if the
        # display copy below is downsampled.
        self._size = QSizeF(image.width(), image.height())
        if max(image.width(), image.height()) > MAX_DISPLAY_SIDE:
            image = image.scaled(
                MAX_DISPLAY_SIDE,
                MAX_DISPLAY_SIDE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._image = image
        self._levels: dict[int, QPixmap] = {}
        self.setFlags(_MOVABLE_SELECTABLE)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._size.width(), self._size.height())

    def paint(self, painter: QPainter, option, widget=None) -> None:
        # Mipmap-style level of detail: drawing a 4000px photo at 300px with
        # plain bilinear filtering looks grainy and is slow, so we keep
        # pre-shrunk copies at power-of-two sizes and pick the smallest one
        # that is still at least as large as what's on screen.
        t = painter.worldTransform()
        device = painter.device()
        ratio = device.devicePixelRatio() if device is not None else 1.0
        on_screen = self._size.width() * math.hypot(t.m11(), t.m12()) * ratio
        full = self._image.width()
        level = 0
        while level < 10 and full / (2 ** (level + 1)) >= on_screen:
            level += 1
        pixmap = self._levels.get(level)
        if pixmap is None:
            if level == 0:
                pixmap = QPixmap.fromImage(self._image)
            else:
                pixmap = QPixmap.fromImage(
                    self._image.scaled(
                        max(1, full >> level),
                        max(1, self._image.height() >> level),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            self._levels[level] = pixmap
        painter.drawPixmap(self.boundingRect(), pixmap, QRectF(pixmap.rect()))


class NoteItem(QGraphicsTextItem):
    """A sticky-note style text block. Double-click to edit, Esc to finish."""

    DEFAULT_WIDTH = 220.0

    def __init__(self, text: str = "", color: str = DEFAULT_NOTE_COLOR):
        super().__init__()
        self.color = color
        self._html_before: str | None = None
        self.document().setDocumentMargin(14)
        self.setFont(QFont("Segoe UI", 11))
        self.setDefaultTextColor(QColor("#24242A"))
        self.setTextWidth(self.DEFAULT_WIDTH)
        if text:
            self.setPlainText(text)
        self.setFlags(_MOVABLE_SELECTABLE)

    def set_color(self, color: str) -> None:
        self.color = color
        self.update()

    def is_editing(self) -> bool:
        return self.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction

    def start_editing(self, select_all: bool = False) -> None:
        if self.is_editing():
            return
        self._html_before = self.toHtml()
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if select_all:
            cursor = self.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            self.setTextCursor(cursor)
        self.update()

    def finish_editing(self) -> None:
        if not self.is_editing():
            return
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        before, self._html_before = self._html_before, None
        after = self.toHtml()
        scene = self.scene()
        # One undo step per editing session (the text control keeps its own
        # fine-grained undo while you type).
        if before is not None and before != after and hasattr(scene, "note_edited"):
            scene.note_edited(self, before, after)
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        if not self.is_editing():
            self.start_editing()
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # Opening the right-click menu steals focus; don't end editing for that.
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self.finish_editing()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.clearFocus()
            return
        super().keyPressEvent(event)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        rect = self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(0, 0, 0, 28), 1))
        painter.setBrush(QColor(self.color))
        painter.drawRoundedRect(rect, 6, 6)
        if self.is_editing():
            pen = QPen(EDIT_OUTLINE, 1.5)
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 4, 4)
        painter.restore()
        super().paint(painter, _without_selection(option), widget)


class GroupItem(QGraphicsItemGroup):
    """Items grouped with Ctrl+G; moves and resizes as one unit."""

    def __init__(self):
        super().__init__()
        self.setFlags(_MOVABLE_SELECTABLE)

    def paint(self, painter, option, widget=None) -> None:
        pass  # the view draws the selection outline


# --------------------------------------------------------------------------- #
# Transform helpers
# --------------------------------------------------------------------------- #
def _unwrap(value):
    """Some PySide6 methods return (value, ok) tuples; keep just the value."""
    return value[0] if isinstance(value, tuple) else value


def local_transform(item: QGraphicsItem) -> QTransform:
    """Item -> parent transform, combining pos, scale and any extra matrix."""
    parent = item.parentItem()
    if parent is None:
        return item.sceneTransform()
    return item.sceneTransform() * _unwrap(parent.sceneTransform().inverted())


def bake_transform(item: QGraphicsItem) -> None:
    """Fold any leftover QTransform into plain pos + scale.

    QGraphicsItemGroup.addToGroup/removeFromGroup preserve an item's on-screen
    geometry by setting an extra transform matrix on it. Our resize maths and
    file format assume only pos + uniform scale, so we normalise afterwards.
    """
    t = local_transform(item)
    item.setTransform(QTransform())
    item.setScale(t.m11())
    item.setPos(t.dx(), t.dy())


def top_level(item: QGraphicsItem) -> QGraphicsItem:
    while item.parentItem() is not None:
        item = item.parentItem()
    return item


def iter_images(item: QGraphicsItem) -> Iterator[ImageItem]:
    if isinstance(item, ImageItem):
        yield item
    for child in item.childItems():
        yield from iter_images(child)


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def item_to_dict(item: QGraphicsItem, save_image: Callable[[ImageItem], str]) -> dict | None:
    t = local_transform(item)
    data = {
        "pos": [round(t.dx(), 3), round(t.dy(), 3)],
        "scale": round(t.m11(), 6),
        "z": round(item.zValue(), 4),
    }
    if isinstance(item, ImageItem):
        data.update(type="image", file=save_image(item), name=item.name)
    elif isinstance(item, NoteItem):
        data.update(
            type="note",
            text=item.toPlainText(),
            html=item.toHtml(),
            color=item.color,
            width=round(item.textWidth(), 2),
        )
    elif isinstance(item, GroupItem):
        children = sorted(item.childItems(), key=lambda c: c.zValue())
        data.update(type="group", children=[d for d in (item_to_dict(c, save_image) for c in children) if d])
    else:
        return None
    return data


def item_from_dict(
    data: dict,
    load_image: Callable[[str], bytes | None],
    created: list,
    warnings: list[str],
) -> QGraphicsItem | None:
    """Rebuild an item. Every item created is appended to `created` so the
    caller can hold Python references to it (see BoardScene.retain)."""
    kind = data.get("type")
    if kind == "image":
        rel = str(data.get("file", ""))
        raw = load_image(rel)
        if raw is None:
            return None
        try:
            item = ImageItem(raw, PurePosixPath(rel).suffix.lstrip("."), name=str(data.get("name", "")))
        except ValueError:
            warnings.append(f"Unreadable image: {rel}")
            return None
    elif kind == "note":
        item = NoteItem(color=str(data.get("color", DEFAULT_NOTE_COLOR)))
        item.setTextWidth(float(data.get("width", NoteItem.DEFAULT_WIDTH)))
        if data.get("html"):
            item.setHtml(str(data["html"]))
        else:
            item.setPlainText(str(data.get("text", "")))
    elif kind == "group":
        item = GroupItem()
        created.append(item)
        # Children are positioned relative to the group; adding them while
        # the group still has an identity transform keeps those values.
        for child_data in data.get("children", []):
            child = item_from_dict(child_data, load_image, created, warnings)
            if child is not None:
                item.addToGroup(child)
        if not item.childItems():
            return None
    else:
        return None
    if kind != "group":
        created.append(item)
    item.setScale(float(data.get("scale", 1.0)))
    x, y = data.get("pos", (0, 0))
    item.setPos(float(x), float(y))
    item.setZValue(float(data.get("z", 0)))
    return item
