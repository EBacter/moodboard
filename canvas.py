"""The board canvas.

BoardScene holds the content and performs every edit through undo commands.
BoardView handles navigation (pan/zoom), selection outlines and resize
handles, drag-and-drop/paste, the context menu and the eyedropper tool.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
    QUndoCommand,
)
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsScene, QGraphicsView, QMenu, QWidget

import storage
from items import (
    NOTE_COLORS,
    GroupItem,
    ImageItem,
    NoteItem,
    bake_transform,
    iter_images,
    qimage_to_png,
    top_level,
)

SCENE_EXTENT = 2_000_000  # "infinite" canvas: 2M x 2M scene units
BACKGROUND = QColor("#EBEBEE")
GRID_DOT = QColor("#D2D2D8")
ACCENT = QColor("#2F80ED")
HINT_COLOR = QColor("#9B9BA3")
HANDLE_PX = 8  # resize handle size on screen, independent of zoom
MIN_ITEM_PX = 20  # smallest an item can be resized to (scene units)
ZOOM_MIN, ZOOM_MAX = 0.03, 16.0
NEW_IMAGE_MAX_SIDE = 520  # new images are shrunk to fit this
GAP = 24


def _corners(rect: QRectF) -> list[QPointF]:
    # Order matters: corner i is opposite corner (i + 2) % 4.
    return [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]


# --------------------------------------------------------------------------- #
# Undo commands
# --------------------------------------------------------------------------- #
class AddItemsCommand(QUndoCommand):
    def __init__(self, scene: "BoardScene", items: list, text: str):
        super().__init__(text)
        self.scene, self.items = scene, items

    def redo(self):
        self.scene.clearSelection()
        for item in self.items:
            if item.scene() is None:
                self.scene.addItem(item)
            item.setSelected(True)

    def undo(self):
        for item in self.items:
            self.scene.removeItem(item)


class RemoveItemsCommand(QUndoCommand):
    def __init__(self, scene: "BoardScene", items: list):
        super().__init__("Delete")
        self.scene, self.items = scene, items

    def redo(self):
        for item in self.items:
            self.scene.removeItem(item)

    def undo(self):
        self.scene.clearSelection()
        for item in self.items:
            self.scene.addItem(item)
            item.setSelected(True)


class MoveItemsCommand(QUndoCommand):
    def __init__(self, moves: list[tuple]):
        super().__init__("Move")
        self.moves = moves  # (item, old_pos, new_pos)

    def redo(self):
        for item, _old, new in self.moves:
            item.setPos(new)

    def undo(self):
        for item, old, _new in self.moves:
            item.setPos(old)


class ResizeCommand(QUndoCommand):
    def __init__(self, item, old: tuple, new: tuple):
        super().__init__("Resize")
        self.item, self.old, self.new = item, old, new  # (pos, scale)

    def redo(self):
        self.item.setScale(self.new[1])
        self.item.setPos(self.new[0])

    def undo(self):
        self.item.setScale(self.old[1])
        self.item.setPos(self.old[0])


class GroupCommand(QUndoCommand):
    def __init__(self, scene: "BoardScene", items: list):
        super().__init__("Group")
        self.scene = scene
        self.items = sorted(items, key=lambda i: i.zValue())
        self.group = GroupItem()
        self.group.setZValue(self.items[-1].zValue())
        scene.retain([self.group])

    def redo(self):
        self.scene.clearSelection()
        self.scene.addItem(self.group)
        for item in self.items:
            self.group.addToGroup(item)
        self.group.setSelected(True)

    def undo(self):
        self.scene.clearSelection()
        for item in self.items:
            self.group.removeFromGroup(item)
            bake_transform(item)
        self.scene.removeItem(self.group)
        for item in self.items:
            item.setSelected(True)


class UngroupCommand(QUndoCommand):
    def __init__(self, scene: "BoardScene", group: GroupItem):
        super().__init__("Ungroup")
        self.scene, self.group = scene, group
        self.children = sorted(group.childItems(), key=lambda c: c.zValue())
        self.old_z = [c.zValue() for c in self.children]
        # Children's own z-values are only meaningful inside the group; squeeze
        # them between the group's z and the next integer so the ungrouped
        # items stay at the same depth relative to everything else.
        n = len(self.children)
        self.new_z = [group.zValue() + i / (n + 1) for i in range(n)]

    def redo(self):
        self.scene.clearSelection()
        for child, z in zip(self.children, self.new_z):
            self.group.removeFromGroup(child)
            bake_transform(child)
            child.setZValue(z)
        self.scene.removeItem(self.group)
        for child in self.children:
            child.setSelected(True)

    def undo(self):
        self.scene.clearSelection()
        self.scene.addItem(self.group)
        for child, z in zip(self.children, self.old_z):
            child.setZValue(z)
            self.group.addToGroup(child)
        self.group.setSelected(True)


class ZOrderCommand(QUndoCommand):
    def __init__(self, changes: list[tuple], text: str):
        super().__init__(text)
        self.changes = changes  # (item, old_z, new_z)

    def redo(self):
        for item, _old, new in self.changes:
            item.setZValue(new)

    def undo(self):
        for item, old, _new in self.changes:
            item.setZValue(old)


class EditNoteCommand(QUndoCommand):
    def __init__(self, note: NoteItem, before: str, after: str):
        super().__init__("Edit note")
        self.note, self.before, self.after = note, before, after

    def redo(self):
        if self.note.toHtml() != self.after:
            self.note.setHtml(self.after)

    def undo(self):
        self.note.setHtml(self.before)


class NoteColorCommand(QUndoCommand):
    def __init__(self, note: NoteItem, color: str):
        super().__init__("Note color")
        self.note, self.old, self.new = note, note.color, color

    def redo(self):
        self.note.set_color(self.new)

    def undo(self):
        self.note.set_color(self.old)


# --------------------------------------------------------------------------- #
# Scene
# --------------------------------------------------------------------------- #
class BoardScene(QGraphicsScene):
    def __init__(self, undo_stack, parent=None):
        super().__init__(parent)
        self.undo_stack = undo_stack
        # Strong Python references to every item made for this board. Items
        # move in and out of the scene (undo/redo, grouping) and PySide may
        # hand ownership back to Python when they do; without these refs an
        # item could be garbage-collected while an undo command still needs it.
        self._retained: list = []
        # Items move constantly; a BSP index buys nothing at mood-board scale.
        self.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        half = SCENE_EXTENT / 2
        self.setSceneRect(-half, -half, SCENE_EXTENT, SCENE_EXTENT)
        self.setBackgroundBrush(BACKGROUND)

    # -- bookkeeping -------------------------------------------------------- #
    def retain(self, items) -> None:
        self._retained.extend(items)

    def reset(self) -> None:
        """Remove everything. Clear the undo stack *before* calling this."""
        self.clearSelection()
        self.clear()
        self._retained.clear()

    def load_items(self, items: list, created: list) -> None:
        self.retain(created)
        for item in items:
            self.addItem(item)

    def board_items(self) -> list:
        items = [i for i in self.items() if i.parentItem() is None]
        return sorted(items, key=lambda i: i.zValue())

    def top_level_selected(self) -> list:
        selected = {id(top_level(i)): top_level(i) for i in self.selectedItems()}
        return sorted(selected.values(), key=lambda i: i.zValue())

    def next_z(self) -> float:
        items = self.board_items()
        return math.floor(items[-1].zValue()) + 1 if items else 0.0

    def editing_note(self) -> NoteItem | None:
        focus = self.focusItem()
        return focus if isinstance(focus, NoteItem) and focus.is_editing() else None

    def commit_edits(self) -> None:
        note = self.editing_note()
        if note is not None:
            note.finish_editing()
            note.clearFocus()

    # -- operations (all undoable) ----------------------------------------- #
    def add_images(self, sources: list[tuple[bytes, str, str]], anchor: QPointF) -> tuple[list, list[str]]:
        """Add images from (bytes, ext, name) tuples, laid out in a tidy grid
        centred on `anchor`. Returns (added_items, names_that_failed)."""
        items, failed = [], []
        for data, ext, name in sources:
            try:
                item = ImageItem(data, ext, name=name)
            except ValueError:
                failed.append(name)
                continue
            rect = item.boundingRect()
            item.setScale(min(1.0, NEW_IMAGE_MAX_SIDE / max(rect.width(), rect.height(), 1.0)))
            items.append(item)
        if not items:
            return [], failed

        def size(it):
            r = it.boundingRect()
            return r.width() * it.scale(), r.height() * it.scale()

        cols = max(1, math.ceil(math.sqrt(len(items))))
        placed, y, block_w = [], 0.0, 0.0
        for start in range(0, len(items), cols):
            row = items[start : start + cols]
            row_h = max(size(it)[1] for it in row)
            x = 0.0
            for it in row:
                w, h = size(it)
                placed.append((it, x, y + (row_h - h) / 2))
                x += w + GAP
            block_w = max(block_w, x - GAP)
            y += row_h + GAP
        origin = anchor - QPointF(block_w / 2, (y - GAP) / 2)
        z = self.next_z()
        for it, x, y in placed:
            it.setPos(origin + QPointF(x, y))
            it.setZValue(z)
            z += 1
        self.retain(items)
        text = "Add image" if len(items) == 1 else f"Add {len(items)} images"
        self.undo_stack.push(AddItemsCommand(self, items, text))
        return items, failed

    def add_note(self, center: QPointF, text: str = "", edit: bool = True) -> NoteItem:
        note = NoteItem(text or "New note")
        rect = note.boundingRect()
        note.setPos(center - QPointF(rect.width() / 2, rect.height() / 2))
        note.setZValue(self.next_z())
        self.retain([note])
        self.undo_stack.push(AddItemsCommand(self, [note], "Add note"))
        if edit:
            note.start_editing(select_all=True)
        return note

    def note_edited(self, note: NoteItem, before: str, after: str) -> None:
        self.undo_stack.push(EditNoteCommand(note, before, after))

    def set_note_color(self, note: NoteItem, color: str) -> None:
        if note.color != color:
            self.undo_stack.push(NoteColorCommand(note, color))

    def select_all(self) -> None:
        for item in self.board_items():
            item.setSelected(True)

    def delete_selected(self) -> None:
        self.commit_edits()
        items = self.top_level_selected()
        if items:
            self.undo_stack.push(RemoveItemsCommand(self, items))

    def group_selected(self) -> None:
        self.commit_edits()
        items = self.top_level_selected()
        if len(items) >= 2:
            self.undo_stack.push(GroupCommand(self, items))

    def ungroup_selected(self) -> None:
        groups = [i for i in self.top_level_selected() if isinstance(i, GroupItem)]
        if not groups:
            return
        self.undo_stack.beginMacro("Ungroup")
        for group in groups:
            self.undo_stack.push(UngroupCommand(self, group))
        self.undo_stack.endMacro()

    def bring_to_front(self) -> None:
        selected = self.top_level_selected()
        ids = {id(i) for i in selected}
        others = [i.zValue() for i in self.board_items() if id(i) not in ids]
        top = math.floor(max(others, default=0.0)) + 1
        changes = [(it, it.zValue(), top + n) for n, it in enumerate(selected)]
        if changes:
            self.undo_stack.push(ZOrderCommand(changes, "Bring to front"))

    def send_to_back(self) -> None:
        selected = self.top_level_selected()
        ids = {id(i) for i in selected}
        others = [i.zValue() for i in self.board_items() if id(i) not in ids]
        bottom = math.floor(min(others, default=0.0)) - len(selected)
        changes = [(it, it.zValue(), bottom + n) for n, it in enumerate(selected)]
        if changes:
            self.undo_stack.push(ZOrderCommand(changes, "Send to back"))


# --------------------------------------------------------------------------- #
# Eyedropper magnifier
# --------------------------------------------------------------------------- #
class Magnifier(QWidget):
    """Floating zoomed-in preview of the pixels around the cursor."""

    GRID = 11  # sampled pixels per side (odd, so there's a centre pixel)
    CELL = 11  # on-screen size of one sampled pixel
    LABEL_H = 30

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        side = self.GRID * self.CELL
        self.setFixedSize(side + 2, side + self.LABEL_H + 2)
        self._patch: QImage | None = None
        self._color = QColor()
        self._pickable = True
        self.hide()

    def set_sample(self, patch: QImage | None, color: QColor, pickable: bool = True) -> None:
        self._patch, self._color, self._pickable = patch, color, pickable
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(0, 0, 0, 70), 1))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

        side = self.GRID * self.CELL
        grid = QRectF(1, 1, side, side)
        if self._patch is not None:
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(grid, 7, 7)
            p.setClipPath(clip)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)  # crisp pixels
            p.drawImage(grid, self._patch)
            if not self._pickable:  # wash out: there's nothing to pick here
                p.fillRect(grid, QColor(255, 255, 255, 150))
            p.restore()

        # Centre-pixel marker: black + white outline reads on any colour.
        c = self.GRID // 2
        cell = QRectF(1 + c * self.CELL, 1 + c * self.CELL, self.CELL, self.CELL)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawRect(cell.adjusted(-1, -1, 0, 0))
        p.setPen(QPen(QColor("#FFFFFF"), 1))
        p.drawRect(cell)

        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        label = QRectF(1, 1 + side, side, self.LABEL_H)
        font = QFont(self.font())
        if not self._pickable:
            p.setFont(font)
            p.setPen(QColor("#8A8A93"))
            p.drawText(label, Qt.AlignmentFlag.AlignCenter, "Not on an image")
            return
        chip = QRectF(label.left() + 8, label.center().y() - 8, 16, 16)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        p.setBrush(self._color if self._color.isValid() else QColor("#FFFFFF"))
        p.drawRoundedRect(chip, 3, 3)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#24242A"))
        text = self._color.name().upper() if self._color.isValid() else "—"
        p.drawText(label.adjusted(32, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


# --------------------------------------------------------------------------- #
# View
# --------------------------------------------------------------------------- #
class BoardView(QGraphicsView):
    zoomChanged = Signal(float)
    colorPicked = Signal(QColor)
    eyedropperToggled = Signal(bool)
    extractPaletteRequested = Signal(object, bool)  # (item, into_new_group)
    addImageRequested = Signal()
    openBoardRequested = Signal(str)
    statusMessage = Signal(str)

    def __init__(self, scene: BoardScene, parent=None):
        super().__init__(scene, parent)
        self.board = scene
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.ItemSelectionMode.IntersectsItemShape)
        # We anchor zooming ourselves (see zoom_by), so Qt shouldn't re-centre.
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        # Handles are drawn outside item bounds, so partial updates would
        # leave trails; full updates are cheap at this scale.
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        # The viewport's own fill (normally the white Base colour) shows
        # through any pixel the background paint misses; make it canvas grey.
        viewport_palette = self.viewport().palette()
        viewport_palette.setColor(QPalette.ColorRole.Base, BACKGROUND)
        viewport_palette.setColor(QPalette.ColorRole.Window, BACKGROUND)
        self.viewport().setPalette(viewport_palette)

        self._space_held = False
        self._pan_last: QPoint | None = None
        self._resize: dict | None = None
        self._move_start: dict = {}
        self._eyedropper = False
        self._magnifier = Magnifier(self)
        self._cursor_shape = None

        scene.selectionChanged.connect(lambda: self.viewport().update())
        self.centerOn(0, 0)

    # -- state ------------------------------------------------------------- #
    def reset_state(self) -> None:
        self._pan_last = None
        self._resize = None
        self._move_start = {}
        self.setTransform(QTransform())
        self.centerOn(0, 0)
        self._after_view_change()

    def view_state(self) -> dict:
        center = self.mapToScene(self.viewport().rect().center())
        return {"center": [round(center.x(), 2), round(center.y(), 2)], "zoom": round(self.zoom(), 4)}

    def restore_view_state(self, state: dict) -> None:
        try:
            zoom = min(max(float(state.get("zoom", 1.0)), ZOOM_MIN), ZOOM_MAX)
            cx, cy = state.get("center", (0, 0))
            center = QPointF(float(cx), float(cy))
        except (TypeError, ValueError):
            self.fit_board()
            return
        self.setTransform(QTransform.fromScale(zoom, zoom))
        self.centerOn(center)
        self._after_view_change()

    def insertion_point(self) -> QPointF:
        """Where new content goes: under the mouse if it's over the canvas,
        otherwise the centre of the view."""
        pos = self.viewport().mapFromGlobal(QCursor.pos())
        if not self.viewport().rect().contains(pos):
            pos = self.viewport().rect().center()
        return self.mapToScene(pos)

    # -- zoom & pan ---------------------------------------------------------- #
    def zoom(self) -> float:
        return self.transform().m11()

    def zoom_by(self, factor: float, anchor: QPoint | None = None) -> None:
        current = self.zoom()
        target = min(max(current * factor, ZOOM_MIN), ZOOM_MAX)
        if math.isclose(target, current):
            return
        if anchor is None:
            anchor = self.viewport().rect().center()
        scene_anchor = self.mapToScene(anchor)
        self.setTransform(QTransform.fromScale(target, target))
        # Scroll so the scene point that was under the anchor stays there.
        drift = self.mapFromScene(scene_anchor) - anchor
        self._scroll_by(drift.x(), drift.y())
        self._after_view_change()

    def set_zoom(self, zoom: float) -> None:
        self.zoom_by(zoom / self.zoom())

    def fit_board(self) -> None:
        rect = self.board.itemsBoundingRect()
        if rect.isEmpty():
            self.reset_state()
            return
        viewport = self.viewport().rect()
        margin = 48
        zoom = min(
            max(viewport.width() - 2 * margin, 50) / rect.width(),
            max(viewport.height() - 2 * margin, 50) / rect.height(),
        )
        zoom = min(max(zoom, ZOOM_MIN), 2.0)
        self.setTransform(QTransform.fromScale(zoom, zoom))
        self.centerOn(rect.center())
        self._after_view_change()

    def _scroll_by(self, dx: float, dy: float) -> None:
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() + round(dx))
        v.setValue(v.value() + round(dy))

    def _after_view_change(self) -> None:
        self.zoomChanged.emit(self.zoom())
        self.viewport().update()
        if self._eyedropper:
            self._refresh_magnifier()

    def wheelEvent(self, event):
        # A mouse wheel zooms. Trackpads (which report pixel deltas or a
        # scroll phase) pan instead; Ctrl zooms either way, which also covers
        # Windows touchpad pinches, as they arrive as Ctrl + wheel.
        mods = event.modifiers()
        trackpad = not event.pixelDelta().isNull() or event.phase() != Qt.ScrollPhase.NoScrollPhase
        if mods & Qt.KeyboardModifier.ControlModifier or not (trackpad or mods & Qt.KeyboardModifier.ShiftModifier):
            delta = event.angleDelta().y() or event.angleDelta().x()
            if delta:
                self.zoom_by(1.0015 ** delta, event.position().toPoint())
            event.accept()
            return
        # Trackpad scroll / Shift + wheel pans the canvas; Shift swaps axes.
        pixel = event.pixelDelta()
        if not pixel.isNull():
            dx, dy = pixel.x(), pixel.y()
        else:
            dx, dy = event.angleDelta().x() / 2, event.angleDelta().y() / 2
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and not dx:
            dx, dy = dy, 0
        self._scroll_by(-dx, -dy)
        event.accept()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self.viewport().update()

    # -- painting ------------------------------------------------------------ #
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        # Overfill by a few device pixels: with fractional display scaling
        # (e.g. 125%) the edges of a partial repaint fall between physical
        # pixels, and an exact antialiased fill leaves faint light seams (seen
        # as trails behind the eyedropper magnifier). The painter is clipped
        # to the exposed region, so nothing outside it is touched.
        pad = 3 / max(self.zoom(), 1e-6)
        painter.fillRect(rect.adjusted(-pad, -pad, pad, pad), BACKGROUND)
        # Subtle dot grid aligned to scene coordinates; spacing doubles as you
        # zoom out so the dots never get denser than ~24px on screen.
        step = 40.0
        zoom = self.zoom()
        while step * zoom < 24:
            step *= 2
        left = math.floor(rect.left() / step) * step
        top = math.floor(rect.top() / step) * step
        xs = [left + i * step for i in range(int((rect.right() - left) / step) + 1)]
        ys = [top + i * step for i in range(int((rect.bottom() - top) / step) + 1)]
        if not xs or not ys or len(xs) * len(ys) > 20000:
            return
        pen = QPen(GRID_DOT, 2.0)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPoints(QPolygonF([QPointF(x, y) for y in ys for x in xs]))

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if self._eyedropper:
            return  # keep the picked pixels clean
        if not self.board.items():
            painter.save()
            painter.resetTransform()  # viewport pixel coordinates
            font = QFont(self.font())
            font.setPointSizeF(font.pointSizeF() * 1.25)
            painter.setFont(font)
            painter.setPen(HINT_COLOR)
            painter.drawText(
                self.viewport().rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Drop images here or paste with Ctrl+V\nDouble-click anywhere to add a note",
            )
            painter.restore()
            return
        zoom = self.zoom()
        handle = HANDLE_PX / zoom
        outline = QPen(ACCENT, 1.5)
        outline.setCosmetic(True)
        for item in self.board.top_level_selected():
            r = item.sceneBoundingRect()
            painter.setPen(outline)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r)
            painter.setBrush(QColor("#FFFFFF"))
            for c in _corners(r):
                painter.drawRect(QRectF(c.x() - handle / 2, c.y() - handle / 2, handle, handle))

    # -- cursor helpers ------------------------------------------------------ #
    def _set_cursor(self, shape) -> None:
        if shape == self._cursor_shape:
            return
        self._cursor_shape = shape
        if shape is None:
            self.viewport().unsetCursor()
        else:
            self.viewport().setCursor(shape)

    def _idle_cursor(self):
        if self._eyedropper:
            return Qt.CursorShape.CrossCursor
        if self._space_held:
            return Qt.CursorShape.OpenHandCursor
        return None

    # -- resize handles ----------------------------------------------------- #
    def _handle_at(self, pos: QPoint):
        """(item, corner_index) if `pos` is over a selected item's handle."""
        for item in reversed(self.board.top_level_selected()):
            for i, corner in enumerate(_corners(item.sceneBoundingRect())):
                c = self.mapFromScene(corner)
                if abs(c.x() - pos.x()) <= HANDLE_PX and abs(c.y() - pos.y()) <= HANDLE_PX:
                    return item, i
        return None

    def _begin_resize(self, item, corner: int) -> None:
        bake_transform(item)
        rect = item.boundingRect()
        corners = _corners(rect)
        anchor_local = corners[(corner + 2) % 4]
        self._resize = {
            "item": item,
            "anchor_local": anchor_local,
            "diag": corners[corner] - anchor_local,
            "anchor_scene": item.mapToScene(anchor_local),
            "min_scale": MIN_ITEM_PX / max(rect.width(), rect.height(), 1.0),
            "old": (item.pos(), item.scale()),
        }

    def _update_resize(self, scene_pos: QPointF) -> None:
        r = self._resize
        # Project the mouse onto the item's diagonal: one uniform scale factor
        # keeps the aspect ratio, and the opposite corner stays pinned.
        d, v = r["diag"], scene_pos - r["anchor_scene"]
        scale = (v.x() * d.x() + v.y() * d.y()) / max(d.x() ** 2 + d.y() ** 2, 1e-9)
        scale = max(scale, r["min_scale"])
        item = r["item"]
        item.setScale(scale)
        item.setPos(r["anchor_scene"] - r["anchor_local"] * scale)

    def _end_resize(self) -> None:
        r, self._resize = self._resize, None
        item = r["item"]
        new = (item.pos(), item.scale())
        if new != r["old"]:
            self.board.undo_stack.push(ResizeCommand(item, r["old"], new))

    # -- mouse --------------------------------------------------------------- #
    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        button = event.button()
        if self._eyedropper:
            if button == Qt.MouseButton.LeftButton:
                if not self._pickable_at(pos):
                    self.statusMessage.emit("The eyedropper picks colors from images — click on an image")
                else:
                    color, _ = self._sample(pos)
                    if color.isValid():
                        self.colorPicked.emit(color)
            elif button == Qt.MouseButton.RightButton:
                self.set_eyedropper(False)
            event.accept()
            return
        if button == Qt.MouseButton.MiddleButton or (button == Qt.MouseButton.LeftButton and self._space_held):
            self._pan_last = pos
            self._set_cursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if button == Qt.MouseButton.LeftButton and not self.board.editing_note():
            hit = self._handle_at(pos)
            if hit:
                self._begin_resize(*hit)
                event.accept()
                return
        selection_mods = Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
        if button == Qt.MouseButton.LeftButton and not event.modifiers() & selection_mods and not self.items(pos):
            # Left-drag on empty canvas pans; Shift/Ctrl + drag draws a
            # selection box. The scene still sees the press, so it clears the
            # selection and ends note editing as a click on the canvas should.
            mode = self.dragMode()
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            super().mousePressEvent(event)
            self.setDragMode(mode)
            self._pan_last = pos
            self._set_cursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)
        if button == Qt.MouseButton.LeftButton:
            self._move_start = {item: item.pos() for item in self.board.top_level_selected()}

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._eyedropper:
            self._update_magnifier(pos)
            event.accept()
            return
        if self._pan_last is not None:
            delta = pos - self._pan_last
            self._pan_last = pos
            self._scroll_by(-delta.x(), -delta.y())
            event.accept()
            return
        if self._resize is not None:
            self._update_resize(self.mapToScene(pos))
            event.accept()
            return
        if event.buttons() == Qt.MouseButton.NoButton:
            hit = None if self._space_held else self._handle_at(pos)
            if hit:
                diagonal = hit[1] in (0, 2)
                self._set_cursor(Qt.CursorShape.SizeFDiagCursor if diagonal else Qt.CursorShape.SizeBDiagCursor)
            else:
                self._set_cursor(self._idle_cursor())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._eyedropper:
            event.accept()
            return
        if self._pan_last is not None:
            self._pan_last = None
            self._set_cursor(self._idle_cursor())
            event.accept()
            return
        if self._resize is not None:
            self._end_resize()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self._move_start:
            moves = [
                (item, old, item.pos())
                for item, old in self._move_start.items()
                if item.scene() is self.board and item.pos() != old
            ]
            self._move_start = {}
            if moves:
                self.board.undo_stack.push(MoveItemsCommand(moves))

    def mouseDoubleClickEvent(self, event):
        if self._eyedropper:
            event.accept()
            return
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton and not self.items(pos):
            self.board.add_note(self.mapToScene(pos))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        self._magnifier.hide()
        super().leaveEvent(event)

    # -- keyboard ------------------------------------------------------------ #
    def keyPressEvent(self, event):
        if self.board.editing_note():
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Space:
            if not event.isAutoRepeat() and not self._space_held:
                self._space_held = True
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                if self._pan_last is None:
                    self._set_cursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            if self._eyedropper:
                self.set_eyedropper(False)
            else:
                self.board.clearSelection()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat() and self._space_held:
            self._release_space()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        if self._space_held:  # the key release will never arrive
            self._release_space()
        super().focusOutEvent(event)

    def _release_space(self) -> None:
        self._space_held = False
        if not self._eyedropper:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        if self._pan_last is None:
            self._set_cursor(self._idle_cursor())

    # -- eyedropper ---------------------------------------------------------- #
    def is_eyedropper(self) -> bool:
        return self._eyedropper

    def set_eyedropper(self, enabled: bool) -> None:
        if enabled == self._eyedropper:
            return
        self._eyedropper = enabled
        if enabled:
            self.board.commit_edits()
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setFocus()
            self.viewport().update()
            self._refresh_magnifier()
        else:
            self._magnifier.hide()
            if not self._space_held:
                self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.viewport().update()
        self._set_cursor(self._idle_cursor())
        self.eyedropperToggled.emit(enabled)

    def _pickable_at(self, pos: QPoint) -> bool:
        """True if the topmost thing under `pos` is an image (not the canvas
        background or a note). Groups are skipped: their bounding box covers
        the gaps between their children."""
        for item in self.items(pos):
            if not isinstance(item, GroupItem):
                return isinstance(item, ImageItem)
        return False

    def _sample(self, pos: QPoint) -> tuple[QColor, QImage | None]:
        """Colour under `pos` plus the surrounding pixel patch, read from what
        is actually rendered on screen (so notes and the background count)."""
        viewport = self.viewport()
        if not viewport.rect().contains(pos):
            return QColor(), None
        dpr = viewport.devicePixelRatioF()
        grid = Magnifier.GRID
        reach = math.ceil((grid // 2) / dpr) + 1  # logical px needed per side
        area = QRect(pos.x() - reach, pos.y() - reach, 2 * reach + 1, 2 * reach + 1)
        area = area.intersected(viewport.rect())
        image = viewport.grab(area).toImage()  # renders just this small region
        cx = min(int((pos.x() - area.x() + 0.5) * dpr), image.width() - 1)
        cy = min(int((pos.y() - area.y() + 0.5) * dpr), image.height() - 1)
        color = image.pixelColor(cx, cy)
        color.setAlpha(255)
        patch = image.copy(cx - grid // 2, cy - grid // 2, grid, grid)
        patch.setDevicePixelRatio(1.0)
        return color, patch

    def _update_magnifier(self, pos: QPoint) -> None:
        color, patch = self._sample(pos)
        if patch is None:
            self._magnifier.hide()
            return
        self._magnifier.set_sample(patch, color, self._pickable_at(pos))
        at = self.viewport().mapTo(self, pos)
        size = self._magnifier.size()
        x, y = at.x() + 20, at.y() + 20
        if x + size.width() > self.width():
            x = at.x() - 20 - size.width()
        if y + size.height() > self.height():
            y = at.y() - 20 - size.height()
        self._magnifier.move(x, y)
        self._magnifier.show()
        self._magnifier.raise_()

    def _refresh_magnifier(self) -> None:
        pos = self.viewport().mapFromGlobal(QCursor.pos())
        if self.viewport().rect().contains(pos):
            self._update_magnifier(pos)
        else:
            self._magnifier.hide()

    # -- drag & drop / paste ------------------------------------------------ #
    @staticmethod
    def _accepts(mime) -> bool:
        return mime is not None and (mime.hasUrls() or mime.hasImage() or mime.hasText())

    def dragEnterEvent(self, event):
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        # Must be overridden too: the default asks scene items, which decline.
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        self.board.commit_edits()
        if self.import_mime(event.mimeData(), self.mapToScene(event.position().toPoint())):
            event.acceptProposedAction()
        else:
            event.ignore()

    def paste(self, scene_pos: QPointF | None = None) -> None:
        mime = QGuiApplication.clipboard().mimeData()
        if not self.import_mime(mime, scene_pos if scene_pos is not None else self.insertion_point()):
            self.statusMessage.emit("Nothing to paste")

    def import_mime(self, mime, scene_pos: QPointF) -> bool:
        """Add whatever `mime` holds: image files, raw image data, image URLs
        (dragged from a browser) or, failing those, text as a note."""
        if mime is None:
            return False
        sources, failed = [], []
        urls = mime.urls() if mime.hasUrls() else []
        for url in urls:
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                if storage.is_board_folder(path):
                    self.openBoardRequested.emit(str(path))
                    return True
                continue
            try:
                sources.append(storage.read_image_file(path))
            except OSError:
                failed.append(path.name)
        if not sources and mime.hasImage():
            image = mime.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()
            if isinstance(image, QImage) and not image.isNull():
                sources.append((qimage_to_png(image), "png", "pasted image"))
        if not sources:
            for url in urls:
                if url.scheme() in ("http", "https"):
                    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                    try:
                        sources.append((storage.download(url.toString()), "", url.fileName() or url.host()))
                    except Exception:
                        failed.append(url.fileName() or url.toString())
                    finally:
                        QApplication.restoreOverrideCursor()

        if sources:
            added, bad = self.board.add_images(sources, scene_pos)
            failed += bad
            if failed:
                self.statusMessage.emit("Couldn't load: " + ", ".join(failed))
            elif len(added) > 1:
                self.statusMessage.emit(f"Added {len(added)} images")
            return bool(added)
        if failed:
            self.statusMessage.emit("Couldn't load: " + ", ".join(failed))
            return False
        if mime.hasText() and mime.text().strip():
            self.board.add_note(scene_pos, mime.text().strip(), edit=False)
            return True
        return False

    # -- context menu -------------------------------------------------------- #
    def contextMenuEvent(self, event):
        if self._eyedropper:
            return
        if self.board.editing_note():
            super().contextMenuEvent(event)  # the note's own cut/copy/paste menu
            return
        pos = event.pos()
        scene_pos = self.mapToScene(pos)
        under = self.items(pos)
        leaf = under[0] if under else None
        target = top_level(leaf) if leaf is not None else None
        if target is not None and not target.isSelected():
            self.board.clearSelection()
            target.setSelected(True)

        menu = QMenu(self)

        def add(text, slot, enabled=True):
            action = menu.addAction(text)
            action.triggered.connect(lambda _checked=False: slot())
            action.setEnabled(enabled)
            return action

        if target is None:
            add("Add note here", lambda: self.board.add_note(scene_pos))
            add("Add image…", self.addImageRequested.emit)
            add("Paste", lambda: self.paste(scene_pos))
            menu.addSeparator()
            add("Select all", self.board.select_all, bool(self.board.items()))
            add("Fit board to window", self.fit_board, bool(self.board.items()))
        else:
            source = leaf if isinstance(leaf, ImageItem) else target
            if any(True for _ in iter_images(source)):
                font = QFont(menu.font())
                font.setBold(True)
                add("Extract palette", lambda: self.extractPaletteRequested.emit(source, False)).setFont(font)
                add("Extract palette to new group", lambda: self.extractPaletteRequested.emit(source, True))
            if isinstance(target, NoteItem):
                add("Edit text", lambda: (self.setFocus(), target.start_editing()))
                colors = menu.addMenu("Note color")
                for name, value in NOTE_COLORS.items():
                    action = colors.addAction(name)
                    action.setCheckable(True)
                    action.setChecked(target.color.upper() == value.upper())
                    action.triggered.connect(lambda _c=False, v=value: self.board.set_note_color(target, v))
            menu.addSeparator()
            selected = self.board.top_level_selected()
            add("Bring to front", self.board.bring_to_front)
            add("Send to back", self.board.send_to_back)
            menu.addSeparator()
            if len(selected) >= 2:
                add("Group", self.board.group_selected)
            if any(isinstance(i, GroupItem) for i in selected):
                add("Ungroup", self.board.ungroup_selected)
            add("Delete", self.board.delete_selected)
        menu.exec(event.globalPos())
