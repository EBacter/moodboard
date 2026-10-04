"""Moodboard - a desktop mood-board app.

Run:  python main.py [path/to/board.moodboard]
      python main.py --self-test [result.txt]   (smoke test used by the build)
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPalette, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
)

import ai
import storage
from canvas import BACKGROUND, BoardScene, BoardView
from items import GroupItem, iter_images
from palette import PalettePanel
from version import __version__

APP_NAME = "Moodboard"
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tif *.tiff *.ico);;All files (*)"
PALETTE_SIZE = 5

STYLE = """
QMainWindow { background: #F6F6F8; }
QToolBar { background: #FFFFFF; border: none; border-bottom: 1px solid #E2E2E7; padding: 4px 8px; spacing: 2px; }
QToolBar QToolButton { padding: 5px 10px; border-radius: 6px; color: #2A2A30; }
QToolBar QToolButton:hover { background: #EDEDF1; }
QToolBar QToolButton:pressed { background: #E2E2E8; }
QToolBar QToolButton:checked { background: #DCE8FB; color: #1C5FC4; }
QToolBar QToolButton:disabled { color: #B4B4BB; }
QToolBar::separator { width: 1px; background: #E2E2E7; margin: 5px 6px; }
QStatusBar { background: #FFFFFF; border-top: 1px solid #E2E2E7; color: #5A5A62; }
QStatusBar QLabel { color: #5A5A62; padding: 0 8px; }
QDockWidget { color: #2A2A30; font-weight: 600; }
QDockWidget::title { background: #FFFFFF; padding: 8px 12px; border-bottom: 1px solid #E2E2E7; }
PalettePanel { background: #FFFFFF; font-weight: normal; }
QScrollArea#paletteScroll, QScrollArea#paletteScroll > QWidget { background: #F3F3F6; border-top: 1px solid #E2E2E7; }
QLabel#paletteCount { color: #7A7A82; font-weight: normal; }
QScrollArea#paletteScroll QScrollBar:vertical { width: 10px; background: transparent; margin: 2px 2px 2px 0; }
QScrollArea#paletteScroll QScrollBar::handle:vertical { background: #CDCDD4; border-radius: 3px; min-height: 30px; margin: 0 2px; }
QScrollArea#paletteScroll QScrollBar::handle:vertical:hover { background: #B4B4BC; }
QScrollArea#paletteScroll QScrollBar::add-line, QScrollArea#paletteScroll QScrollBar::sub-line { height: 0; }
QScrollArea#paletteScroll QScrollBar::add-page, QScrollArea#paletteScroll QScrollBar::sub-page { background: transparent; }
QLineEdit#groupNameEditor { border: 1.5px solid #2F80ED; border-radius: 4px; padding: 0 4px; background: #FFFFFF; font-weight: 600; }
QMenu { background: #FFFFFF; border: 1px solid #DADADF; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 22px; border-radius: 4px; }
QMenu::item:selected { background: #E8F0FD; color: #1A1A1F; }
QMenu::item:disabled { color: #B4B4BB; }
QMenu::separator { height: 1px; background: #E6E6EA; margin: 4px 6px; }
"""

SHORTCUTS_HTML = """
<table cellpadding="3">
<tr><td><b>Pan</b></td><td>Middle-drag, or hold Space + drag, or scroll</td></tr>
<tr><td><b>Zoom</b></td><td>Ctrl + scroll · Ctrl+= / Ctrl+- · Ctrl+0 (100%) · Ctrl+1 (fit)</td></tr>
<tr><td><b>Add image</b></td><td>Drag &amp; drop · Ctrl+V · Ctrl+I</td></tr>
<tr><td><b>Add note</b></td><td>T, or double-click empty canvas (Esc finishes editing)</td></tr>
<tr><td><b>Resize</b></td><td>Drag a corner handle (aspect ratio kept)</td></tr>
<tr><td><b>Select</b></td><td>Click · Ctrl+click · drag a box on empty canvas · Ctrl+A</td></tr>
<tr><td><b>Delete</b></td><td>Delete / Backspace</td></tr>
<tr><td><b>Group</b></td><td>Ctrl+G · Ungroup Ctrl+Shift+G</td></tr>
<tr><td><b>Order</b></td><td>Ctrl+] bring to front · Ctrl+[ send to back</td></tr>
<tr><td><b>Eyedropper</b></td><td>I, then click an image to add a swatch (Esc/right-click to stop)</td></tr>
<tr><td><b>Undo / Redo</b></td><td>Ctrl+Z · Ctrl+Y</td></tr>
</table>
<p><b>Palette</b></p>
<table cellpadding="3">
<tr><td><b>Swatches</b></td><td>Click to select · Ctrl+click / Shift+click for more · double-click copies the hex</td></tr>
<tr><td></td><td>Delete removes · Ctrl+A selects all · Ctrl+C copies · drag to reorder or onto another group</td></tr>
<tr><td><b>Groups</b></td><td>Click to open · click the open group again to minimise · double-click the name (or F2) to rename · drag to reorder · × deletes</td></tr>
<tr><td></td><td>Right-click swatches → New group from selection / Move to group</td></tr>
</table>
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings()
        self.board_path: Path | None = None

        self.undo_stack = QUndoStack(self)
        self.scene = BoardScene(self.undo_stack, self)
        self.view = BoardView(self.scene, self)
        self.setCentralWidget(self.view)

        self.palette = PalettePanel(self.undo_stack)
        self.palette_dock = QDockWidget("Palette", self)
        self.palette_dock.setObjectName("paletteDock")
        self.palette_dock.setWidget(self.palette)
        self.palette_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.palette_dock.setMinimumWidth(240)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.palette_dock)

        self.zoom_label = QLabel("100%")
        self.statusBar().addPermanentWidget(self.zoom_label)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._connect_signals()
        self._restore_window()
        self._update_title()
        self._update_actions()
        self.view.setFocus()

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #
    def _action(self, text, slot=None, shortcut=None, icon_text=None, checkable=False) -> QAction:
        action = QAction(text, self)
        if shortcut:
            keys = shortcut if isinstance(shortcut, (list, tuple)) else [shortcut]
            action.setShortcuts([QKeySequence(k) for k in keys])
            action.setToolTip(f"{text.rstrip('…')} ({QKeySequence(keys[0]).toString()})")
        if icon_text:
            action.setIconText(icon_text)
        action.setCheckable(checkable)
        if slot is not None and not checkable:
            # Wrap so Qt's `checked` argument isn't passed into our slots.
            action.triggered.connect(lambda _checked=False: slot())
        return action

    def _build_actions(self) -> None:
        a = self._action
        self.act_new = a("New Board", self.new_board, "Ctrl+N")
        self.act_open = a("Open Board…", self.open_board_dialog, "Ctrl+O")
        self.act_save = a("Save", self.save, "Ctrl+S")
        self.act_save_as = a("Save As…", self.save_as, "Ctrl+Shift+S")
        self.act_export = a("Export as PNG…", self.export_png, "Ctrl+E")
        self.act_quit = a("Quit", self.close, "Ctrl+Q")

        # Own undo/redo actions (rather than QUndoStack.createUndoAction) so an
        # in-progress note edit is committed before undoing.
        self.act_undo = a("Undo", self.undo, "Ctrl+Z", icon_text="Undo")
        self.act_redo = a("Redo", self.redo, ["Ctrl+Y", "Ctrl+Shift+Z"], icon_text="Redo")
        self.act_undo.setEnabled(False)
        self.act_redo.setEnabled(False)
        self.undo_stack.canUndoChanged.connect(self.act_undo.setEnabled)
        self.undo_stack.canRedoChanged.connect(self.act_redo.setEnabled)
        self.undo_stack.undoTextChanged.connect(lambda t: self.act_undo.setText(f"Undo {t}" if t else "Undo"))
        self.undo_stack.redoTextChanged.connect(lambda t: self.act_redo.setText(f"Redo {t}" if t else "Redo"))

        self.act_paste = a("Paste", lambda: self.view.paste(), "Ctrl+V")
        self.act_delete = a("Delete", self.delete_selected, ["Del", "Backspace"])
        self.act_select_all = a("Select All", self.select_all, "Ctrl+A")

        self.act_add_image = a("Add Image…", self.add_images_dialog, "Ctrl+I", icon_text="Image")
        self.act_add_note = a("Add Note", self.add_note, "T", icon_text="Note")

        self.act_group = a("Group", self.scene.group_selected, "Ctrl+G")
        self.act_ungroup = a("Ungroup", self.scene.ungroup_selected, "Ctrl+Shift+G")
        self.act_front = a("Bring to Front", self.scene.bring_to_front, "Ctrl+]", icon_text="Front")
        self.act_back = a("Send to Back", self.scene.send_to_back, "Ctrl+[", icon_text="Back")

        self.act_zoom_in = a("Zoom In", lambda: self.view.zoom_by(1.25), ["Ctrl+=", "Ctrl++"])
        self.act_zoom_out = a("Zoom Out", lambda: self.view.zoom_by(0.8), "Ctrl+-")
        self.act_zoom_reset = a("Actual Size", lambda: self.view.set_zoom(1.0), "Ctrl+0", icon_text="100%")
        self.act_fit = a("Fit Board", self.view.fit_board, "Ctrl+1", icon_text="Fit")

        self.act_eyedropper = a("Eyedropper", shortcut="I", checkable=True)
        self.act_eyedropper.toggled.connect(self.view.set_eyedropper)
        self.act_extract = a("Extract Palette from Selection", self.extract_from_selection, "Ctrl+Shift+E")
        self.act_extract_group = a(
            "Extract Palette to New Group", lambda: self.extract_from_selection(new_group=True), "Ctrl+Alt+E"
        )

        self.act_shortcuts = a("Keyboard Shortcuts", self.show_shortcuts, "F1")

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addActions([self.act_new, self.act_open])
        self.recent_menu = file_menu.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self._populate_recent)
        file_menu.addSeparator()
        file_menu.addActions([self.act_save, self.act_save_as, self.act_export])
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        edit_menu = bar.addMenu("&Edit")
        edit_menu.addActions([self.act_undo, self.act_redo])
        edit_menu.addSeparator()
        edit_menu.addActions([self.act_paste, self.act_delete, self.act_select_all])

        insert_menu = bar.addMenu("&Insert")
        insert_menu.addActions([self.act_add_image, self.act_add_note])

        arrange_menu = bar.addMenu("&Arrange")
        arrange_menu.addActions([self.act_group, self.act_ungroup])
        arrange_menu.addSeparator()
        arrange_menu.addActions([self.act_front, self.act_back])

        view_menu = bar.addMenu("&View")
        view_menu.addActions([self.act_zoom_in, self.act_zoom_out, self.act_zoom_reset, self.act_fit])
        view_menu.addSeparator()
        view_menu.addAction(self.palette_dock.toggleViewAction())

        tools_menu = bar.addMenu("&Tools")
        tools_menu.addActions([self.act_eyedropper, self.act_extract, self.act_extract_group])

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self.act_shortcuts)
        help_menu.addSeparator()
        help_menu.addAction(f"About {APP_NAME}").triggered.connect(self.show_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Tools", self)
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        tb.addActions([self.act_add_image, self.act_add_note, self.act_eyedropper])
        tb.addSeparator()
        tb.addActions([self.act_undo, self.act_redo])
        tb.addSeparator()
        tb.addActions([self.act_group, self.act_ungroup, self.act_front, self.act_back])
        tb.addSeparator()
        tb.addActions([self.act_fit, self.act_zoom_reset])
        self.addToolBar(tb)

    def _connect_signals(self) -> None:
        self.view.zoomChanged.connect(lambda z: self.zoom_label.setText(f"{round(z * 100)}%"))
        self.view.colorPicked.connect(self._on_color_picked)
        self.view.extractPaletteRequested.connect(self.extract_palette_from)
        self.view.eyedropperToggled.connect(self.act_eyedropper.setChecked)
        self.view.statusMessage.connect(self._status)
        self.view.addImageRequested.connect(self.add_images_dialog)
        self.view.openBoardRequested.connect(self.open_board)
        self.palette.message.connect(self._status)
        self.palette.selectionChanged.connect(self._update_actions)
        self.scene.selectionChanged.connect(self._update_actions)
        self.undo_stack.indexChanged.connect(self._update_actions)
        self.undo_stack.cleanChanged.connect(self._update_title)

    def _restore_window(self) -> None:
        self.resize(1400, 900)
        geometry = self.settings.value("window/geometry")
        state = self.settings.value("window/state")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if state is not None:
            self.restoreState(state)
        else:
            self.resizeDocks([self.palette_dock], [290], Qt.Orientation.Horizontal)

    # ------------------------------------------------------------------ #
    # UI state
    # ------------------------------------------------------------------ #
    def _status(self, text: str, timeout: int = 4000) -> None:
        self.statusBar().showMessage(text, timeout)

    def _update_title(self, *_args) -> None:
        name = self.board_path.stem if self.board_path else "Untitled"
        self.setWindowTitle(f"{name}[*] — {APP_NAME}")
        self.setWindowModified(not self.undo_stack.isClean())

    def _update_actions(self, *_args) -> None:
        try:
            selected = self.scene.top_level_selected()
            has_selection = bool(selected)
            self.act_delete.setEnabled(has_selection)
            self.act_front.setEnabled(has_selection)
            self.act_back.setEnabled(has_selection)
            self.act_group.setEnabled(len(selected) >= 2)
            self.act_ungroup.setEnabled(any(isinstance(i, GroupItem) for i in selected))
            has_images = any(True for i in selected for _ in iter_images(i))
            self.act_extract.setEnabled(has_images)
            self.act_extract_group.setEnabled(has_images)
            self.act_delete.setEnabled(has_selection or self.palette.has_selection())
        except RuntimeError:  # Qt objects already destroyed during shutdown
            pass

    # Delete / Select All act on whichever panel has keyboard focus.
    def delete_selected(self) -> None:
        if self.palette.has_focus():
            self.palette.delete_selected()
        else:
            self.scene.delete_selected()

    def select_all(self) -> None:
        if self.palette.has_focus():
            self.palette.select_all()
        else:
            self.scene.select_all()

    def undo(self) -> None:
        self.scene.commit_edits()
        self.undo_stack.undo()

    def redo(self) -> None:
        self.scene.commit_edits()
        self.undo_stack.redo()

    # ------------------------------------------------------------------ #
    # Canvas actions
    # ------------------------------------------------------------------ #
    def add_images_dialog(self) -> None:
        start = self.settings.value("lastImageDir", str(Path.home()))
        paths, _ = QFileDialog.getOpenFileNames(self, "Add images", start, IMAGE_FILTER)
        if not paths:
            return
        self.settings.setValue("lastImageDir", str(Path(paths[0]).parent))
        sources, failed = [], []
        for path in paths:
            try:
                sources.append(storage.read_image_file(path))
            except OSError:
                failed.append(Path(path).name)
        added, bad = self.scene.add_images(sources, self.view.mapToScene(self.view.viewport().rect().center()))
        failed += bad
        if failed:
            self._status("Couldn't load: " + ", ".join(failed), 6000)
        elif added:
            self._status(f"Added {len(added)} image{'s' if len(added) != 1 else ''}")

    def add_note(self) -> None:
        self.view.setFocus()
        self.scene.add_note(self.view.insertion_point())

    def _on_color_picked(self, color: QColor) -> None:
        hex_code = color.name().upper()
        added = self.palette.add_colors([hex_code], "Pick color")
        group = self.palette.active_group().name
        self._status(f"Added {hex_code} to “{group}”" if added else f"{hex_code} is already in “{group}”")
        self.palette_dock.show()

    def extract_palette_from(self, item, new_group: bool = False) -> None:
        self._extract([img for img in iter_images(item)], new_group)

    def extract_from_selection(self, new_group: bool = False) -> None:
        self._extract([img for sel in self.scene.top_level_selected() for img in iter_images(sel)], new_group)

    def _extract(self, images: list, new_group: bool = False) -> None:
        if not images:
            self._status("Select an image to extract its palette")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            colors = [c for image in images for c in ai.extract_palette(image.data, PALETTE_SIZE)]
        except Exception as exc:
            self._status(f"Couldn't extract a palette: {exc}", 6000)
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.palette_dock.show()
        if new_group:
            # Name the group after the image file, e.g. "sunset.jpg" -> "sunset".
            stem = Path(images[0].name).stem if images[0].name else ""
            name = stem or "Image palette"
            if len(images) > 1:
                name = f"{name} + {len(images) - 1} more"
            group = self.palette.add_group(self.palette.unique_name(name), colors, "Extract palette")
            self._status(f"Extracted {len(group.colors)} colors into new group “{group.name}”")
            return
        added = self.palette.add_colors(colors, "Extract palette")
        group = self.palette.active_group().name
        skipped = len(set(colors)) - added
        note = f" ({skipped} already there)" if skipped else ""
        self._status(f"Extracted {len(colors)} colors — added {added} to “{group}”{note}")

    def show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        box.setIconPixmap(self.windowIcon().pixmap(64, 64))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)  # clickable links
        box.setText(
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>A minimal mood board: collect images, notes and colour palettes on an infinite canvas.</p>"
            "<p>Built with Python, <a href='https://doc.qt.io/qtforpython-6/'>Qt for Python (PySide6)</a>, "
            "<a href='https://python-pillow.org'>Pillow</a> and "
            "<a href='https://github.com/fengsp/color-thief-py'>colorthief</a>.<br>"
            "Qt and PySide6 are used under the "
            "<a href='https://www.gnu.org/licenses/lgpl-3.0.html'>GNU LGPL v3</a>; "
            "see THIRD_PARTY_NOTICES.md in the install folder.</p>"
        )
        box.exec()

    def show_shortcuts(self) -> None:
        QMessageBox.information(self, "Keyboard shortcuts", SHORTCUTS_HTML)

    # ------------------------------------------------------------------ #
    # Files
    # ------------------------------------------------------------------ #
    def _confirm_discard(self) -> bool:
        """True if it's OK to throw away the current board."""
        self.scene.commit_edits()
        if self.undo_stack.isClean():
            return True
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Save changes to this board first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def _clear_board(self) -> None:
        self.view.set_eyedropper(False)
        # Order matters: commands reference items, so drop them first.
        self.undo_stack.clear()
        self.scene.reset()
        self.palette.load_data(None)
        self.view.reset_state()

    def new_board(self) -> None:
        if not self._confirm_discard():
            return
        self._clear_board()
        self.board_path = None
        self._update_title()

    def open_board_dialog(self) -> None:
        if not self._confirm_discard():
            return
        start = self.settings.value("lastBoardDir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Open board folder (*.moodboard)", start)
        if folder:
            self.open_board(folder, confirmed=True)

    def open_board(self, folder: str, confirmed: bool = False) -> bool:
        if not confirmed and not self._confirm_discard():
            return False
        try:
            loaded = storage.load_board(folder)
        except storage.BoardError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return False
        self._clear_board()
        self.scene.load_items(loaded.items, loaded.created)
        self.palette.load_data(loaded.palette)
        if loaded.view:
            self.view.restore_view_state(loaded.view)
        else:
            self.view.fit_board()
        self.board_path = Path(folder).resolve()
        if self.board_path.name == storage.BOARD_FILE:
            self.board_path = self.board_path.parent
        self.settings.setValue("lastBoardDir", str(self.board_path.parent))
        storage.add_recent(self.board_path)
        self._update_title()
        self._update_actions()
        if loaded.warnings:
            QMessageBox.warning(self, APP_NAME, "Some content couldn't be loaded:\n\n" + "\n".join(loaded.warnings[:15]))
        self._status(f"Opened {self.board_path.name}")
        return True

    def save(self) -> bool:
        if self.board_path is None:
            return self.save_as()
        return self._save_to(self.board_path)

    def save_as(self) -> bool:
        start_dir = Path(self.settings.value("lastBoardDir", str(Path.home())))
        suggested = start_dir / ((self.board_path.stem if self.board_path else "Untitled") + storage.BOARD_SUFFIX)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save board as", str(suggested), f"Mood board folder (*{storage.BOARD_SUFFIX})"
        )
        if not path:
            return False
        folder = Path(path)
        if folder.suffix.lower() != storage.BOARD_SUFFIX:
            folder = folder.with_name(folder.name + storage.BOARD_SUFFIX)
        if self._save_to(folder):
            self.board_path = folder.resolve()
            self.settings.setValue("lastBoardDir", str(self.board_path.parent))
            self._update_title()
            return True
        return False

    def _save_to(self, folder: Path) -> bool:
        self.scene.commit_edits()
        try:
            storage.save_board(folder, self.scene.board_items(), self.palette.to_data(), self.view.view_state())
        except (storage.BoardError, OSError) as exc:
            QMessageBox.warning(self, APP_NAME, f"Couldn't save the board:\n\n{exc}")
            return False
        self.undo_stack.setClean()
        storage.add_recent(folder)
        self._status(f"Saved to {folder}")
        return True

    def export_png(self) -> None:
        self.scene.commit_edits()
        if not self.scene.items():
            QMessageBox.information(self, APP_NAME, "The board is empty — add some images first.")
            return
        start_dir = Path(self.settings.value("lastExportDir", str(Path.home())))
        name = (self.board_path.stem if self.board_path else "moodboard") + ".png"
        path, _ = QFileDialog.getSaveFileName(self, "Export board as PNG", str(start_dir / name), "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            width, height = storage.export_png(self.scene, path, BACKGROUND)
        except storage.BoardError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self.settings.setValue("lastExportDir", str(Path(path).parent))
        self._status(f"Exported {width}×{height} PNG to {path}", 6000)

    def _populate_recent(self) -> None:
        self.recent_menu.clear()
        recent = storage.recent_boards()
        if not recent:
            self.recent_menu.addAction("No recent boards").setEnabled(False)
            return
        for path in recent:
            action = self.recent_menu.addAction(Path(path).stem)
            action.setToolTip(path)
            action.setStatusTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self.open_board(p))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear Recent").triggered.connect(storage.clear_recent)

    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        try:
            self.scene.selectionChanged.disconnect(self._update_actions)
        except (RuntimeError, TypeError):
            pass
        event.accept()


def light_palette() -> QPalette:
    """Force a light theme so the app looks the same in OS dark mode."""
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#F6F6F8",
        QPalette.ColorRole.WindowText: "#1F1F24",
        QPalette.ColorRole.Base: "#FFFFFF",
        QPalette.ColorRole.AlternateBase: "#F2F2F5",
        QPalette.ColorRole.Text: "#1F1F24",
        QPalette.ColorRole.Button: "#FFFFFF",
        QPalette.ColorRole.ButtonText: "#1F1F24",
        QPalette.ColorRole.Highlight: "#2F80ED",
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.ToolTipBase: "#FFFFFF",
        QPalette.ColorRole.ToolTipText: "#1F1F24",
        QPalette.ColorRole.PlaceholderText: "#9B9BA3",
        QPalette.ColorRole.Link: "#2F80ED",
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#A8A8B0"))
    return p


def resource_path(relative: str) -> Path:
    """Locate bundled data files, both when run from source and from the
    PyInstaller build (which unpacks them under sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent
    return Path(base) / relative


def _set_windows_app_id() -> None:
    # Without this, Windows groups the window under python.exe's taskbar icon
    # when running from source.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Moodboard.Moodboard")
        except Exception:
            pass


def main() -> int:
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationName(APP_NAME)
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        import selftest

        return selftest.run(MainWindow, sys.argv[2] if len(sys.argv) > 2 else None)

    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(resource_path("assets/moodboard.ico"))))
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(STYLE)

    window = MainWindow()

    def excepthook(exc_type, exc, tb):
        # Keep the app alive on unexpected errors in Qt callbacks.
        traceback.print_exception(exc_type, exc, tb)
        window.statusBar().showMessage(f"Unexpected error: {exc}", 8000)

    sys.excepthook = excepthook
    window.show()
    window.view.setFocus()
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        window.open_board(sys.argv[1], confirmed=True)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
