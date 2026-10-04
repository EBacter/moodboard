"""Palette side panel: colour swatches organised into tab-like groups.

Groups stack vertically like a paint fan deck. The open group shows its
swatches as tiles; every other group collapses to its name plus a thin strip
of its colours. All edits go through the shared undo stack.

Swatches: click = select, Ctrl+click = toggle, Shift+click = range,
          double-click = copy hex, drag = reorder or drop onto another group,
          Delete = remove selected, Ctrl+A / Ctrl+C = select all / copy.
Groups:   click = open (click the open group again to minimise it),
          double-click name (or F2) = rename, drag = reorder, × = delete.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPolygonF, QUndoCommand
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

ACCENT = QColor("#2F80ED")
FIELD_BG = QColor("#F3F3F6")
CARD_BG = QColor("#FFFFFF")
CARD_HOVER_BG = QColor("#FAFAFC")
CARD_BORDER = QColor("#E1E1E7")
TEXT = QColor("#24242A")
MUTED = QColor("#8A8A93")

MARGIN = 10  # field edge -> card
CARD_GAP = 8  # between cards
HEADER_H = 34
PAD = 10  # card edge -> content
STRIP_H = 16  # collapsed group's colour slice
TILE_MIN = 56  # tiles stretch to fill a row but never get smaller than this
TILE_GAP = 6
EMPTY_H = 56  # drop zone shown by an empty open group
CLOSE_SIZE = 18


def normalise(color) -> str | None:
    """'#rgb', 'red', QColor... -> '#RRGGBB' (or None if invalid)."""
    q = QColor(color)
    return q.name().upper() if q.isValid() else None


def unique_colors(colors) -> list[str]:
    result: list[str] = []
    for c in colors:
        hex_code = normalise(c)
        if hex_code and hex_code not in result:
            result.append(hex_code)
    return result


def readable_text(color: QColor) -> QColor:
    """Dark text on light swatches, white text on dark ones."""
    luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
    return QColor("#1E1E22") if luminance > 150 else QColor("#FFFFFF")


def rgb_text(hex_code: str) -> str:
    c = QColor(hex_code)
    return f"rgb({c.red()}, {c.green()}, {c.blue()})"


@dataclass
class SwatchGroup:
    id: int  # stable identity across undo/redo; names can repeat
    name: str
    colors: list[str] = field(default_factory=list)  # unique within a group


@dataclass
class CardLayout:
    """Geometry of one group card, recomputed whenever content or width changes."""

    gid: int
    rect: QRectF
    header: QRectF
    name: QRectF
    count: QRectF
    close: QRectF
    strip: QRectF | None = None  # collapsed groups
    grid: QRectF | None = None  # the open group
    tiles: list[tuple[str, QRectF]] = field(default_factory=list)


class PaletteCommand(QUndoCommand):
    """Snapshot-based undo step: swaps the whole (small) palette state."""

    def __init__(self, panel: "PalettePanel", before, after, text: str, select_after=None):
        super().__init__(text)
        self.panel, self.before, self.after, self.select_after = panel, before, after, select_after

    def redo(self):
        self.panel._restore(self.after)
        if self.select_after is not None:
            self.panel.canvas.set_selection(self.select_after)

    def undo(self):
        self.panel._restore(self.before)


# --------------------------------------------------------------------------- #
# Panel: data model + public API
# --------------------------------------------------------------------------- #
class PalettePanel(QWidget):
    message = Signal(str)
    selectionChanged = Signal()

    def __init__(self, undo_stack, parent=None):
        super().__init__(parent)
        self.undo_stack = undo_stack
        self.groups: list[SwatchGroup] = []
        self.active_id: int | None = None  # current group: target for new colours
        self.collapsed = False  # current group minimised to its strip
        self._next_id = 1

        self.count_label = QLabel()
        self.count_label.setObjectName("paletteCount")
        new_button = QToolButton(text="+ New group")
        new_button.setAutoRaise(True)
        new_button.setToolTip("Add an empty group")
        new_button.clicked.connect(self.new_group)

        header = QHBoxLayout()
        header.setContentsMargins(12, 6, 6, 6)
        header.addWidget(self.count_label)
        header.addStretch(1)
        header.addWidget(new_button)

        self.canvas = PaletteCanvas(self)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("paletteScroll")
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.scroll, 1)
        self._refresh()

    # -- queries --------------------------------------------------------------- #
    def group(self, gid: int | None) -> SwatchGroup | None:
        return next((g for g in self.groups if g.id == gid), None)

    def active_group(self) -> SwatchGroup | None:
        return self.group(self.active_id)

    def is_expanded(self, gid: int) -> bool:
        return gid == self.active_id and not self.collapsed

    def open_group(self) -> SwatchGroup | None:
        """The current group if it's expanded (only then are swatches selectable)."""
        return None if self.collapsed else self.active_group()

    def all_colors(self) -> list[str]:
        return [c for g in self.groups for c in g.colors]

    def has_selection(self) -> bool:
        return bool(self.canvas.selected)

    def has_focus(self) -> bool:
        return self.canvas.hasFocus()

    # -- persistence (no undo) ---------------------------------------------- #
    def to_data(self) -> dict:
        active = next((i for i, g in enumerate(self.groups) if g.id == self.active_id), 0)
        return {
            "groups": [{"name": g.name, "colors": list(g.colors)} for g in self.groups],
            "active": active,
            "collapsed": self.collapsed,
        }

    def load_data(self, data) -> None:
        """Replace the palette (used for new/opened boards). Accepts the group
        format or the older flat list of hex strings."""
        parsed: list[tuple[str, list[str]]] = []
        active = 0
        self.collapsed = False
        if isinstance(data, list):
            colors = unique_colors(c for c in data if isinstance(c, str))
            if colors:
                parsed.append(("Palette", colors))
        elif isinstance(data, dict):
            for entry in data.get("groups", []):
                if isinstance(entry, dict):
                    colors = unique_colors(c for c in entry.get("colors", []) if isinstance(c, str))
                    parsed.append((str(entry.get("name") or "Group"), colors))
            if isinstance(data.get("active"), int):
                active = data["active"]
            self.collapsed = data.get("collapsed") is True
        self.groups = [self._make_group(name, colors) for name, colors in parsed]
        self.active_id = self.groups[min(max(active, 0), len(self.groups) - 1)].id if self.groups else None
        self.canvas.set_selection([])
        self._refresh()

    # -- undo plumbing ---------------------------------------------------------- #
    def _snapshot(self):
        return tuple((g.id, g.name, tuple(g.colors)) for g in self.groups), self.active_id

    def _restore(self, snapshot) -> None:
        groups, active = snapshot
        self.groups = [SwatchGroup(gid, name, list(colors)) for gid, name, colors in groups]
        if self.group(active) is None:
            active = self.groups[0].id if self.groups else None
        self.active_id = active
        self._next_id = max([self._next_id] + [g.id + 1 for g in self.groups])
        self._refresh()

    def _commit(self, text: str, mutate, select_after=None) -> bool:
        """Apply `mutate` to the live state and record it as one undo step."""
        before = self._snapshot()
        mutate()
        after = self._snapshot()
        if after == before:
            return False
        self.undo_stack.push(PaletteCommand(self, before, after, text, select_after))
        return True

    def _make_group(self, name: str, colors=()) -> SwatchGroup:
        group = SwatchGroup(self._next_id, name, list(colors))
        self._next_id += 1
        return group

    def _insert_after_active(self, group: SwatchGroup) -> None:
        index = next((i + 1 for i, g in enumerate(self.groups) if g.id == self.active_id), len(self.groups))
        self.groups.insert(index, group)

    def unique_name(self, base: str = "Group") -> str:
        names = {g.name for g in self.groups}
        if base != "Group" and base not in names:
            return base
        n = 1 if base == "Group" else 2
        while f"{base} {n}" in names:
            n += 1
        return f"{base} {n}"

    def _refresh(self) -> None:
        n_groups, n_colors = len(self.groups), len(self.all_colors())
        if not self.groups:
            self.count_label.setText("No colors")
        else:
            self.count_label.setText(
                f"{n_groups} group{'s' if n_groups != 1 else ''} · {n_colors} color{'s' if n_colors != 1 else ''}"
            )
        self.canvas.prune_selection()
        self.canvas.relayout()

    # -- operations ---------------------------------------------------------- #
    def set_active(self, gid: int) -> None:
        """Make a group current and expand it. Not an undo step: it's
        navigation, like switching tabs."""
        if self.group(gid) is None or self.is_expanded(gid):
            return
        anchor = self._header_anchor(gid)
        self.active_id = gid
        self.collapsed = False
        self.canvas.set_selection([])
        self._refresh()
        self.canvas.ensure_visible(gid, keep_header_at=anchor)

    def toggle_collapsed(self) -> None:
        """Minimise the current group to its colour strip, or expand it again.
        It stays current, so picked colours still go into it."""
        if self.active_id is None:
            return
        anchor = self._header_anchor(self.active_id)
        self.collapsed = not self.collapsed
        self._refresh()  # also drops the swatch selection when minimising
        self.canvas.ensure_visible(self.active_id, keep_header_at=anchor)

    def _header_anchor(self, gid: int) -> float | None:
        lay = self.canvas.layout_for(gid)
        return lay.header.top() - self.scroll.verticalScrollBar().value() if lay else None

    def add_colors(self, colors, text: str = "Add colors") -> int:
        """Append colours to the open group (creating one if needed).
        Returns how many were new to that group."""
        new = unique_colors(colors)
        added = 0
        if not new:
            return 0

        def mutate():
            nonlocal added
            group = self.active_group()
            if group is None:
                group = self._make_group("Palette")
                self.groups.append(group)
                self.active_id = group.id
                self.collapsed = False
            for c in new:
                if c not in group.colors:
                    group.colors.append(c)
                    added += 1

        self._commit(text, mutate)
        return added

    def add_group(self, name: str, colors=(), text: str = "New group") -> SwatchGroup:
        """Insert a group after the open one and open it."""
        group = self._make_group(name, unique_colors(colors))

        def mutate():
            self._insert_after_active(group)
            self.active_id = group.id
            self.collapsed = False

        self._commit(text, mutate)
        self.canvas.ensure_visible(group.id)
        return group

    def new_group(self) -> None:
        group = self.add_group(self.unique_name())
        self.canvas.start_rename(group.id)

    def group_from_selection(self) -> None:
        source = self.active_group()
        hexes = self.canvas.ordered_selection()
        if source is None or not hexes:
            return
        group = self._make_group(self.unique_name())

        def mutate():
            source.colors[:] = [c for c in source.colors if c not in hexes]
            group.colors.extend(hexes)
            self._insert_after_active(group)
            self.active_id = group.id
            self.collapsed = False

        self._commit("Group swatches", mutate, select_after=hexes)
        self.canvas.ensure_visible(group.id)
        self.canvas.start_rename(group.id)

    def rename_group(self, gid: int, name: str) -> None:
        group = self.group(gid)
        name = name.strip()
        if group is not None and name:
            self._commit("Rename group", lambda: setattr(group, "name", name))

    def delete_group(self, gid: int) -> None:
        index = next((i for i, g in enumerate(self.groups) if g.id == gid), None)
        if index is None:
            return
        name = self.groups[index].name

        def mutate():
            del self.groups[index]
            if self.active_id == gid:
                # Like closing a browser tab: the neighbour becomes active.
                self.active_id = self.groups[min(index, len(self.groups) - 1)].id if self.groups else None

        if self._commit("Delete group", mutate):
            self.message.emit(f"Deleted group “{name}” (Ctrl+Z to undo)")

    def clear_group(self, gid: int) -> None:
        group = self.group(gid)
        if group is not None:
            self._commit("Clear group", group.colors.clear)

    def delete_all_groups(self) -> None:
        self._commit("Delete all groups", self.groups.clear)

    def remove_colors(self, gid: int, hexes) -> None:
        group = self.group(gid)
        hexes = set(hexes)
        if group is None or not hexes:
            return
        n = len([c for c in group.colors if c in hexes])
        text = "Remove swatch" if n == 1 else f"Remove {n} swatches"

        def mutate():
            group.colors[:] = [c for c in group.colors if c not in hexes]

        self._commit(text, mutate)

    def delete_selected(self) -> None:
        if self.active_id is not None:
            self.remove_colors(self.active_id, self.canvas.selected)

    def select_all(self) -> None:
        group = self.open_group()
        self.canvas.set_selection(group.colors if group else [])

    def move_colors(self, src_id: int, hexes: list[str], dst_id: int, index: int | None = None) -> None:
        """Move swatches to `index` in group `dst_id` (append if None).
        Within one group this reorders; `index` is in pre-move positions."""
        src, dst = self.group(src_id), self.group(dst_id)
        if src is None or dst is None or not hexes:
            return
        moving = [c for c in src.colors if c in hexes]
        same = src_id == dst_id

        def mutate():
            if same:
                k = len(src.colors) if index is None else index
                k -= sum(1 for i, c in enumerate(src.colors) if c in moving and i < k)
                rest = [c for c in src.colors if c not in moving]
                src.colors[:] = rest[:k] + moving + rest[k:]
            else:
                src.colors[:] = [c for c in src.colors if c not in moving]
                incoming = [c for c in moving if c not in dst.colors]  # merge duplicates
                k = len(dst.colors) if index is None else index
                dst.colors[k:k] = incoming

        if same:
            self._commit("Reorder swatches", mutate, select_after=moving)
        elif self._commit("Move swatches", mutate):
            n = len(moving)
            self.message.emit(f"Moved {n} swatch{'es' if n != 1 else ''} to “{dst.name}”")

    def move_group(self, gid: int, index: int) -> None:
        current = next((i for i, g in enumerate(self.groups) if g.id == gid), None)
        if current is None:
            return

        def mutate():
            group = self.groups.pop(current)
            self.groups.insert(index - 1 if index > current else index, group)

        self._commit("Move group", mutate)

    def copy_hex(self, hexes: list[str]) -> None:
        if not hexes:
            return
        QGuiApplication.clipboard().setText("\n".join(hexes))
        if len(hexes) == 1:
            self.canvas.flash(hexes[0])
            self.message.emit(f"Copied {hexes[0]} to clipboard")
        else:
            self.message.emit(f"Copied {len(hexes)} hex codes to clipboard")

    def copy_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.message.emit(f"Copied {text} to clipboard")


# --------------------------------------------------------------------------- #
# Canvas: painting and interaction
# --------------------------------------------------------------------------- #
class PaletteCanvas(QWidget):
    def __init__(self, panel: PalettePanel):
        super().__init__()
        self.panel = panel
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.layouts: list[CardLayout] = []
        self.selected: set[str] = set()  # hex codes in the open group
        self.anchor: str | None = None  # Shift+click range start
        self.hover = None  # hit tuple under the mouse
        self.flashing: str | None = None  # swatch showing "Copied!"
        self._press: dict | None = None
        self._last_press_hit = None  # what the first click of a double-click hit
        self._click_toggled = None  # group the last click minimised/expanded
        self._drag: dict | None = None
        self._editing_gid: int | None = None

        self._flash_timer = QTimer(self, singleShot=True, interval=900)
        self._flash_timer.timeout.connect(self._end_flash)
        self._scroll_timer = QTimer(self, interval=30)
        self._scroll_timer.timeout.connect(self._autoscroll)

        self._editor = QLineEdit(self)
        self._editor.setObjectName("groupNameEditor")
        self._editor.hide()
        self._editor.returnPressed.connect(self._finish_rename)
        self._editor.installEventFilter(self)

    # -- layout ------------------------------------------------------------------ #
    def relayout(self) -> None:
        card_w = max(self.width(), 160) - 2 * MARGIN
        y = MARGIN
        self.layouts = []
        for group in self.panel.groups:
            top = y
            header = QRectF(MARGIN, y, card_w, HEADER_H)
            close = QRectF(header.right() - PAD - CLOSE_SIZE, header.center().y() - CLOSE_SIZE / 2, CLOSE_SIZE, CLOSE_SIZE)
            count = QRectF(close.left() - 40, header.top(), 34, HEADER_H)
            name_left = header.left() + PAD + 16  # room for the chevron
            name = QRectF(name_left, header.top(), count.left() - name_left - 4, HEADER_H)
            lay = CardLayout(group.id, QRectF(), header, name, count, close)
            y += HEADER_H
            if self.panel.is_expanded(group.id):
                inner_x, inner_w = MARGIN + PAD, card_w - 2 * PAD
                cols = max(1, int((inner_w + TILE_GAP) // (TILE_MIN + TILE_GAP)))
                tile = (inner_w - TILE_GAP * (cols - 1)) / cols
                for i, hex_code in enumerate(group.colors):
                    row, col = divmod(i, cols)
                    rect = QRectF(inner_x + col * (tile + TILE_GAP), y + row * (tile + TILE_GAP), tile, tile)
                    lay.tiles.append((hex_code, rect))
                rows = math.ceil(len(group.colors) / cols)
                grid_h = rows * tile + (rows - 1) * TILE_GAP if rows else EMPTY_H
                lay.grid = QRectF(inner_x, y, inner_w, grid_h)
                y += grid_h + PAD
            else:
                lay.strip = QRectF(MARGIN + PAD, y, card_w - 2 * PAD, STRIP_H)
                y += STRIP_H + PAD
            lay.rect = QRectF(MARGIN, top, card_w, y - top)
            self.layouts.append(lay)
            y += CARD_GAP
        self.setMinimumHeight(math.ceil(y - CARD_GAP + MARGIN) if self.layouts else 0)
        self._place_editor()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()

    def layout_for(self, gid: int) -> CardLayout | None:
        return next((lay for lay in self.layouts if lay.gid == gid), None)

    def ensure_visible(self, gid: int, keep_header_at: float | None = None) -> None:
        """Scroll so the group's header and first rows are visible.

        `keep_header_at` (a viewport y) first tries to keep the header where it
        was: opening a tab collapses the one above it, and without this the
        clicked header would jump away from the cursor.
        """

        # Deferred: the scroll area only adopts our new minimum height once
        # its layout runs, so scrolling immediately could fall short.
        def scroll():
            lay = self.layout_for(gid)
            if lay is None:
                return
            if keep_header_at is not None:
                self.panel.scroll.verticalScrollBar().setValue(int(lay.header.top() - keep_header_at))
            half = min(lay.rect.height(), 140) / 2
            self.panel.scroll.ensureVisible(0, int(lay.rect.top() + half), 0, int(half + MARGIN))

        QTimer.singleShot(0, scroll)

    def hit(self, pos: QPointF):
        """(kind, group_id, hex) for what's at `pos`, or None.
        kinds: close, header, tile, grid (open group's free space), card (collapsed body)."""
        for lay in self.layouts:
            if not lay.rect.contains(pos):
                continue
            if lay.close.adjusted(-3, -3, 3, 3).contains(pos):
                return "close", lay.gid, None
            if lay.header.contains(pos):
                return "header", lay.gid, None
            for hex_code, rect in lay.tiles:
                if rect.contains(pos):
                    return "tile", lay.gid, hex_code
            return ("grid" if lay.grid is not None else "card"), lay.gid, None
        return None

    # -- selection --------------------------------------------------------------- #
    def ordered_selection(self) -> list[str]:
        group = self.panel.open_group()
        return [c for c in group.colors if c in self.selected] if group else []

    def set_selection(self, hexes) -> None:
        group = self.panel.open_group()
        selection = {h for h in hexes if group and h in group.colors}
        if selection != self.selected:
            self.selected = selection
            self.panel.selectionChanged.emit()
        if self.anchor not in self.selected:
            self.anchor = next(iter(self.ordered_selection()), None)
        self.update()

    def prune_selection(self) -> None:
        self.set_selection(self.selected)

    def _select_range(self, to_hex: str, add: bool) -> None:
        colors = self.panel.active_group().colors
        anchor = self.anchor if self.anchor in colors else to_hex
        a, b = sorted((colors.index(anchor), colors.index(to_hex)))
        span = set(colors[a : b + 1])
        self.set_selection(self.selected | span if add else span)
        self.anchor = anchor

    # -- painting --------------------------------------------------------------- #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), FIELD_BG)
        if not self.layouts:
            p.setPen(MUTED)
            visible = self.visibleRegion().boundingRect().adjusted(24, 24, -24, -24)
            p.drawText(
                visible,
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "No colors yet.\n\nPick one with the eyedropper (I), or right-click an image and "
                "choose “Extract palette”.",
            )
            return
        dragged_group = self._drag["gid"] if self._drag and self._drag["kind"] == "group" else None
        for lay in self.layouts:
            p.save()
            if lay.gid == dragged_group:
                p.setOpacity(0.3)
            self._paint_card(p, lay)
            p.restore()
        self._paint_drop_indicator(p)
        self._paint_drag_ghost(p)

    def _paint_card(self, p: QPainter, lay: CardLayout) -> None:
        group = self.panel.group(lay.gid)
        active = lay.gid == self.panel.active_id  # current group (may be minimised)
        expanded = lay.grid is not None
        target = self._drag.get("target") if self._drag else None
        is_drop_target = bool(target and target["type"] == "group" and target["gid"] == lay.gid)
        hovered = bool(self.hover and self.hover[1] == lay.gid and self.hover[0] in ("header", "card", "close"))

        border = QPen(ACCENT, 1.5) if (active or is_drop_target) else QPen(CARD_BORDER, 1)
        p.setPen(border)
        p.setBrush(CARD_HOVER_BG if (hovered and not expanded) or is_drop_target else CARD_BG)
        p.drawRoundedRect(lay.rect.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        # Chevron: ▾ expanded, ▸ collapsed
        cx, cy = lay.header.left() + PAD + 5, lay.header.center().y()
        if expanded:
            arrow = QPolygonF([QPointF(cx - 4, cy - 2), QPointF(cx + 4, cy - 2), QPointF(cx, cy + 3)])
        else:
            arrow = QPolygonF([QPointF(cx - 2, cy - 4), QPointF(cx + 3, cy), QPointF(cx - 2, cy + 4)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ACCENT if active else MUTED)
        p.drawPolygon(arrow)

        font = QFont(self.font())
        font.setBold(active)
        p.setFont(font)
        p.setPen(TEXT)
        name = p.fontMetrics().elidedText(group.name, Qt.TextElideMode.ElideRight, int(lay.name.width()))
        if lay.gid != self._editing_gid:
            p.drawText(lay.name, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)

        font.setBold(False)
        p.setFont(font)
        p.setPen(MUTED)
        p.drawText(lay.count, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(len(group.colors)))

        # Close button (×), emphasised on hover like a browser tab
        close_hovered = bool(self.hover and self.hover[0] == "close" and self.hover[1] == lay.gid)
        if close_hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#E6E6EB"))
            p.drawEllipse(lay.close)
        pen = QPen(TEXT if close_hovered else MUTED, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        c, r = lay.close.center(), 3.5
        p.drawLine(QPointF(c.x() - r, c.y() - r), QPointF(c.x() + r, c.y() + r))
        p.drawLine(QPointF(c.x() - r, c.y() + r), QPointF(c.x() + r, c.y() - r))

        if lay.strip is not None:
            self._paint_strip(p, lay.strip, group.colors)
        elif not lay.tiles:
            self._paint_empty_zone(p, lay, target)
        else:
            self._paint_tiles(p, lay)

    def _paint_strip(self, p: QPainter, rect: QRectF, colors: list[str]) -> None:
        """Collapsed group: every colour as a slice of one strip, fan-deck style."""
        if not colors:
            pen = QPen(CARD_BORDER, 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 4, 4)
            font = QFont(self.font())
            font.setPointSizeF(font.pointSizeF() * 0.85)
            p.setFont(font)
            p.setPen(MUTED)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Empty")
            return
        clip = QPainterPath()
        clip.addRoundedRect(rect, 4, 4)
        p.save()
        p.setClipPath(clip)
        width = rect.width() / len(colors)
        for i, hex_code in enumerate(colors):
            # +0.6px overlap hides hairline seams between slices
            p.fillRect(QRectF(rect.left() + i * width, rect.top(), width + 0.6, rect.height()), QColor(hex_code))
        p.restore()
        p.setPen(QPen(QColor(0, 0, 0, 28), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 4, 4)

    def _paint_empty_zone(self, p: QPainter, lay: CardLayout, target) -> None:
        is_target = bool(target and target["type"] == "insert" and target["gid"] == lay.gid)
        p.setPen(QPen(ACCENT if is_target else CARD_BORDER, 1.2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(lay.grid, 6, 6)
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.9)
        p.setFont(font)
        p.setPen(MUTED)
        p.drawText(
            lay.grid,
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            "Empty group\nPick colors with the eyedropper (I)",
        )

    def _paint_tiles(self, p: QPainter, lay: CardLayout) -> None:
        dragging = set(self._drag["hexes"]) if self._drag and self._drag["kind"] == "swatches" else set()
        font = QFont(self.font())
        font.setBold(True)
        for hex_code, rect in lay.tiles:
            font.setPointSizeF(max(7.0, min(self.font().pointSizeF() * 0.85, rect.width() / 7.2)))
            p.save()
            if hex_code in dragging:
                p.setOpacity(0.3)
            self._paint_tile(p, rect, hex_code, font)
            p.restore()

    def _paint_tile(self, p: QPainter, rect: QRectF, hex_code: str, font: QFont) -> None:
        color = QColor(hex_code)
        selected = hex_code in self.selected
        hovered = bool(self.hover and self.hover[0] == "tile" and self.hover[2] == hex_code)
        if selected:
            p.setPen(QPen(ACCENT, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-2.5, -2.5, 2.5, 2.5), 8, 8)
        p.setPen(QPen(QColor(0, 0, 0, 80 if hovered else 28), 1))
        p.setBrush(color)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.setFont(font)
        p.setPen(readable_text(color))
        label = "Copied!" if self.flashing == hex_code else hex_code
        p.drawText(rect.adjusted(0, 0, 0, -5), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, label)
        if selected:  # check badge, readable on any swatch colour
            badge = QRectF(rect.right() - 17, rect.top() + 4, 13, 13)
            p.setPen(QPen(QColor("#FFFFFF"), 1.5))
            p.setBrush(ACCENT)
            p.drawEllipse(badge)
            tick = QPen(QColor("#FFFFFF"), 1.6)
            tick.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(tick)
            b = badge
            p.drawPolyline(
                QPolygonF(
                    [
                        QPointF(b.left() + 3.3, b.center().y() + 0.2),
                        QPointF(b.left() + 5.6, b.center().y() + 2.5),
                        QPointF(b.right() - 3.0, b.center().y() - 2.3),
                    ]
                )
            )

    def _paint_drop_indicator(self, p: QPainter) -> None:
        target = self._drag.get("target") if self._drag else None
        if not target:
            return
        pen = QPen(ACCENT, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        if target["type"] == "insert" and target.get("point") is not None:
            pt, h = target["point"], target["h"]
            p.drawLine(QPointF(pt.x(), pt.y() - h / 2 + 2), QPointF(pt.x(), pt.y() + h / 2 - 2))
        elif target["type"] == "tab":
            y = target["y"]
            p.drawLine(QPointF(MARGIN + 4, y), QPointF(self.width() - MARGIN - 4, y))
            p.setBrush(ACCENT)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(MARGIN + 4, y), 3.5, 3.5)

    def _paint_drag_ghost(self, p: QPainter) -> None:
        if not self._drag:
            return
        pos = self._drag["pos"]
        p.save()
        p.setOpacity(0.92)
        if self._drag["kind"] == "swatches":
            hexes = self._drag["hexes"]
            size = 40
            for i, hex_code in reversed(list(enumerate(hexes[:3]))):
                rect = QRectF(pos.x() + 8 + i * 5, pos.y() + 8 + i * 5, size, size)
                p.setPen(QPen(QColor(0, 0, 0, 60), 1))
                p.setBrush(QColor(hex_code))
                p.drawRoundedRect(rect, 6, 6)
            if len(hexes) > 1:
                badge = QRectF(pos.x() + 8 + size - 4, pos.y() + 2, 20, 16)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ACCENT)
                p.drawRoundedRect(badge, 8, 8)
                font = QFont(self.font())
                font.setBold(True)
                font.setPointSizeF(font.pointSizeF() * 0.8)
                p.setFont(font)
                p.setPen(QColor("#FFFFFF"))
                p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(len(hexes)))
        else:
            group = self.panel.group(self._drag["gid"])
            card_w = self.width() - 2 * MARGIN
            top = pos.y() - self._drag["offset"]
            rect = QRectF(MARGIN, top, card_w, HEADER_H + STRIP_H + PAD)
            p.setPen(QPen(ACCENT, 1.5))
            p.setBrush(CARD_BG)
            p.drawRoundedRect(rect, 10, 10)
            p.setPen(TEXT)
            font = QFont(self.font())
            font.setBold(True)
            p.setFont(font)
            p.drawText(
                QRectF(rect.left() + PAD + 16, top, card_w - 2 * PAD - 16, HEADER_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                p.fontMetrics().elidedText(group.name, Qt.TextElideMode.ElideRight, int(card_w - 2 * PAD - 16)),
            )
            self._paint_strip(p, QRectF(rect.left() + PAD, top + HEADER_H, card_w - 2 * PAD, STRIP_H), group.colors)
        p.restore()

    # -- mouse ------------------------------------------------------------------- #
    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        hit = self.hit(pos)
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        self._press = {"pos": pos, "hit": hit, "select_only": False}
        self._last_press_hit = hit
        self._click_toggled = None
        kind = hit[0] if hit else None
        if kind == "tile":
            hex_code = hit[2]
            if shift:
                self._select_range(hex_code, add=ctrl)
            elif ctrl:
                self.set_selection(self.selected ^ {hex_code})
                self.anchor = hex_code
            elif hex_code in self.selected and len(self.selected) > 1:
                # Might be the start of dragging the whole selection; decide on release.
                self._press["select_only"] = True
            else:
                self.set_selection({hex_code})
                self.anchor = hex_code
        elif kind in (None, "grid") and not ctrl:
            self.set_selection([])

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._press and not self._drag:
            if (pos - self._press["pos"]).manhattanLength() >= QApplication.startDragDistance():
                self._start_drag(pos)
        if self._drag:
            self._drag["pos"] = pos
            self._update_drop_target()
            self.update()
            return
        if event.buttons() == Qt.MouseButton.NoButton:
            hit = self.hit(pos)
            if hit != self.hover:
                self.hover = hit
                pointer = hit is not None and hit[0] in ("close", "header", "card")
                self.setCursor(Qt.CursorShape.PointingHandCursor if pointer else Qt.CursorShape.ArrowCursor)
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._drag:
            self._finish_drag()
            return
        press, self._press = self._press, None
        if not press or not press["hit"]:
            return
        kind, gid, hex_code = press["hit"]
        now = self.hit(event.position())
        if kind == "close" and now == press["hit"]:
            self.panel.delete_group(gid)
        elif kind in ("header", "card"):
            if gid == self.panel.active_id:
                self.panel.toggle_collapsed()
                self._click_toggled = gid
            else:
                self.panel.set_active(gid)
        elif kind == "tile" and press["select_only"]:
            self.set_selection({hex_code})
            self.anchor = hex_code

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Use what the *first* click hit: if it opened a tab, the cards below
        # may have shifted, so the cursor could now be over something else.
        hit = self._last_press_hit
        if not hit:
            return
        kind, gid, hex_code = hit
        if kind == "tile":
            self.panel.copy_hex([hex_code])
        elif kind == "header":
            if self._click_toggled == gid:
                # A double-click means rename: undo the first click's minimise/expand.
                self.panel.toggle_collapsed()
            elif gid != self.panel.active_id:
                self.panel.set_active(gid)
            self.start_rename(gid)

    def leaveEvent(self, event):
        if self.hover is not None:
            self.hover = None
            self.update()
        super().leaveEvent(event)

    # -- drag & drop (internal; no QDrag needed) ----------------------------- #
    def _start_drag(self, pos: QPointF) -> None:
        kind, gid, hex_code = self._press["hit"] or (None, None, None)
        self._press = None
        if kind == "tile":
            if hex_code not in self.selected:
                self.set_selection({hex_code})
                self.anchor = hex_code
            self._drag = {"kind": "swatches", "gid": gid, "hexes": self.ordered_selection(), "target": None, "pos": pos}
        elif kind in ("header", "card"):
            lay = self.layout_for(gid)
            self._drag = {"kind": "group", "gid": gid, "target": None, "pos": pos, "offset": pos.y() - lay.rect.top()}
        else:
            return
        self.hover = None
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._scroll_timer.start()

    def _update_drop_target(self) -> None:
        pos = self._drag["pos"]
        if self._drag["kind"] == "group":
            self._drag["target"] = self._tab_target(pos)
        else:
            self._drag["target"] = self._swatch_target(pos)

    def _tab_target(self, pos: QPointF) -> dict | None:
        if not self.layouts:
            return None
        index = next((i for i, lay in enumerate(self.layouts) if pos.y() < lay.rect.center().y()), len(self.layouts))
        if index < len(self.layouts):
            y = self.layouts[index].rect.top() - CARD_GAP / 2
        else:
            y = self.layouts[-1].rect.bottom() + CARD_GAP / 2
        return {"type": "tab", "index": index, "y": y}

    def _swatch_target(self, pos: QPointF) -> dict | None:
        for lay in self.layouts:
            if not lay.rect.adjusted(0, -CARD_GAP / 2, 0, CARD_GAP / 2).contains(pos):
                continue
            if lay.grid is None:  # a collapsed group: drop = move into it
                return None if lay.gid == self._drag["gid"] else {"type": "group", "gid": lay.gid}
            if not lay.tiles:
                return {"type": "insert", "gid": lay.gid, "index": 0, "point": None}
            # Nearest gap between tiles (row distance weighted so rows win).
            best = None
            for i, (_hex, rect) in enumerate(lay.tiles):
                slots = [(i, QPointF(rect.left() - TILE_GAP / 2, rect.center().y()))]
                row_end = i == len(lay.tiles) - 1 or lay.tiles[i + 1][1].top() > rect.top() + 1
                if row_end:
                    slots.append((i + 1, QPointF(rect.right() + TILE_GAP / 2, rect.center().y())))
                for index, point in slots:
                    d = (point.x() - pos.x()) ** 2 + (2 * (point.y() - pos.y())) ** 2
                    if best is None or d < best[0]:
                        best = (d, index, point, rect.height())
            return {"type": "insert", "gid": lay.gid, "index": best[1], "point": best[2], "h": best[3]}
        return None

    def _finish_drag(self) -> None:
        drag, self._drag = self._drag, None
        self._scroll_timer.stop()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        target = drag.get("target")
        if target:
            if drag["kind"] == "group":
                self.panel.move_group(drag["gid"], target["index"])
            elif target["type"] == "group":
                self.panel.move_colors(drag["gid"], drag["hexes"], target["gid"], None)
            else:
                self.panel.move_colors(drag["gid"], drag["hexes"], target["gid"], target["index"])
        self.update()

    def _cancel_drag(self) -> None:
        self._drag = None
        self._scroll_timer.stop()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _autoscroll(self) -> None:
        """Scroll the panel while dragging near its top or bottom edge."""
        if not self._drag:
            self._scroll_timer.stop()
            return
        viewport = self.panel.scroll.viewport()
        y = viewport.mapFromGlobal(QCursor.pos()).y()
        bar = self.panel.scroll.verticalScrollBar()
        edge = 28
        if y < edge:
            bar.setValue(bar.value() - max(4, (edge - y) // 2))
        elif y > viewport.height() - edge:
            bar.setValue(bar.value() + max(4, (y - viewport.height() + edge) // 2))
        else:
            return
        self._drag["pos"] = QPointF(self.mapFromGlobal(QCursor.pos()))
        self._update_drop_target()
        self.update()

    # -- keyboard / tooltips ------------------------------------------------------ #
    def _owns_key(self, event) -> bool:
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace, Qt.Key.Key_Escape, Qt.Key.Key_F2):
            return True
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        return bool(ctrl) and key in (Qt.Key.Key_A, Qt.Key.Key_C)

    def event(self, event):
        # Claim keys that would otherwise trigger the main window's canvas
        # shortcuts (Delete, Ctrl+A...) while the palette has focus.
        if event.type() == QEvent.Type.ShortcutOverride and self._owns_key(event):
            event.accept()
            return True
        if event.type() == QEvent.Type.ToolTip:
            hit = self.hit(QPointF(event.pos()))
            if hit and hit[0] == "tile":
                QToolTip.showText(
                    event.globalPos(), f"{hit[2]}  ·  {rgb_text(hit[2])}\nDouble-click to copy", self
                )
            elif hit and hit[0] in ("header", "card"):
                group = self.panel.group(hit[1])
                n = len(group.colors)
                action = "minimize" if self.panel.is_expanded(group.id) else "open"
                QToolTip.showText(
                    event.globalPos(), f"{group.name} — {n} color{'s' if n != 1 else ''}\nClick to {action}", self
                )
            elif hit and hit[0] == "close":
                QToolTip.showText(event.globalPos(), "Delete group", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.panel.delete_selected()
        elif key == Qt.Key.Key_Escape:
            if self._drag:
                self._cancel_drag()
            else:
                self.set_selection([])
        elif key == Qt.Key.Key_F2 and self.panel.active_id is not None:
            self.start_rename(self.panel.active_id)
        elif ctrl and key == Qt.Key.Key_A:
            self.panel.select_all()
        elif ctrl and key == Qt.Key.Key_C:
            self.panel.copy_hex(self.ordered_selection())
        else:
            super().keyPressEvent(event)

    # -- context menu -------------------------------------------------------------- #
    def contextMenuEvent(self, event):
        hit = self.hit(QPointF(event.pos()))
        kind, gid, hex_code = hit or (None, None, None)
        panel = self.panel
        menu = QMenu(self)

        def add(text, slot, enabled=True, menu_=menu):
            action = menu_.addAction(text)
            action.triggered.connect(lambda _checked=False: slot())
            action.setEnabled(enabled)
            return action

        if kind == "tile":
            if hex_code not in self.selected:
                self.set_selection({hex_code})
                self.anchor = hex_code
            hexes = self.ordered_selection()
            n = len(hexes)
            if n == 1:
                add(f"Copy {hex_code}", lambda: panel.copy_hex([hex_code]))
                add(f"Copy {rgb_text(hex_code)}", lambda: panel.copy_text(rgb_text(hex_code)))
            else:
                add(f"Copy {n} hex codes", lambda: panel.copy_hex(hexes))
            menu.addSeparator()
            add("New group from selection" if n > 1 else "New group from swatch", panel.group_from_selection)
            move_menu = menu.addMenu("Move to group")
            others = [g for g in panel.groups if g.id != gid]
            for other in others:
                add(other.name, lambda o=other.id: panel.move_colors(gid, hexes, o), menu_=move_menu)
            move_menu.setEnabled(bool(others))
            menu.addSeparator()
            add("Remove swatch" if n == 1 else f"Remove {n} swatches", lambda: panel.remove_colors(gid, hexes))
        elif kind is not None:
            group = panel.group(gid)
            if gid != panel.active_id:
                add("Open", lambda: panel.set_active(gid))
            else:
                add("Expand" if panel.collapsed else "Minimize", panel.toggle_collapsed)
            add("Rename…", lambda: self.start_rename(gid))
            add("Copy all hex codes", lambda: panel.copy_hex(list(group.colors)), bool(group.colors))
            if panel.is_expanded(gid):
                add("Select all swatches", panel.select_all, bool(group.colors))
            add("Clear colors", lambda: panel.clear_group(gid), bool(group.colors))
            menu.addSeparator()
            add("Delete group", lambda: panel.delete_group(gid))
            menu.addSeparator()
            add("New group", panel.new_group)
        else:
            add("New group", panel.new_group)
            add("Delete all groups", panel.delete_all_groups, bool(panel.groups))
        menu.exec(event.globalPos())

    # -- rename editor ------------------------------------------------------------ #
    def start_rename(self, gid: int) -> None:
        group = self.panel.group(gid)
        if group is None or self.layout_for(gid) is None:
            return
        self._editing_gid = gid
        self._editor.setText(group.name)
        self._editor.selectAll()
        self._place_editor()
        self._editor.show()
        self._editor.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def _place_editor(self) -> None:
        if self._editing_gid is None:
            return
        lay = self.layout_for(self._editing_gid)
        if lay is None:
            self._cancel_rename()
            return
        rect = QRectF(lay.name.left() - 5, lay.header.top() + 5, lay.count.right() - lay.name.left() + 5, HEADER_H - 10)
        self._editor.setGeometry(rect.toRect())

    def _finish_rename(self) -> None:
        if self._editing_gid is None:
            return
        gid, self._editing_gid = self._editing_gid, None
        self._editor.hide()
        self.panel.rename_group(gid, self._editor.text())
        self.update()

    def _cancel_rename(self) -> None:
        self._editing_gid = None
        self._editor.hide()
        self.update()

    def eventFilter(self, obj, event):
        if obj is self._editor:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._cancel_rename()
                self.setFocus()
                return True
            if event.type() == QEvent.Type.FocusOut:
                self._finish_rename()
        return super().eventFilter(obj, event)

    # -- copy feedback --------------------------------------------------------------- #
    def flash(self, hex_code: str) -> None:
        self.flashing = hex_code
        self._flash_timer.start()
        self.update()

    def _end_flash(self) -> None:
        self.flashing = None
        self.update()
