# Moodboard

A minimal desktop mood-board app: drop images on an infinite canvas, arrange
and group them, add notes, and collect colours into a palette.

Built with Python 3.11+ and PySide6.

## Download (Windows)

Get the latest version from the **Releases** page (on the right of the GitHub repository page):

- `Moodboard-Setup-<version>.exe` installs Moodboard with a Start-menu entry and an uninstaller.
  No admin rights needed.
- `Moodboard-<version>-windows.zip` is the portable version: unzip it, then run `Moodboard.exe`.

Windows may show *"Windows protected your PC"* because the app isn't code-signed.
Click **More info → Run anyway**.

## Run from source

### Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Run

```bash
python main.py                      # start with an empty board
python main.py MyBoard.moodboard    # open an existing board folder
```

## Using it

| Action | How |
| --- | --- |
| Pan | Middle-mouse drag, Space + drag, or scroll / trackpad |
| Zoom | Ctrl + scroll, Ctrl+= / Ctrl+-, Ctrl+0 = 100%, Ctrl+1 = fit board |
| Add images | Drag & drop files (or images from a browser), Ctrl+V, or Insert → Add Image… (Ctrl+I) |
| Select | Click, Ctrl+click, drag a box on empty canvas, Ctrl+A |
| Move / resize | Drag an item; drag a corner handle to resize (keeps aspect ratio) |
| Delete | Delete or Backspace |
| Group / ungroup | Ctrl+G / Ctrl+Shift+G |
| Stacking | Ctrl+] bring to front, Ctrl+[ send to back |
| Notes | T or double-click empty canvas; double-click a note to edit, Esc to finish; right-click → Note color |
| Eyedropper | I (or toolbar), hover an image for the magnifier, click to add a swatch to the open group; Esc or right-click to stop |
| Extract palette | Right-click an image → Extract palette (5 dominant colours into the open group) or Extract palette to new group (named after the image) |
| Undo / redo | Ctrl+Z / Ctrl+Y (covers canvas and palette changes) |

### Palette

Colours live in **groups**, stacked like a paint fan deck: the open group shows its swatches,
every other group collapses to a strip of its colours.

| Action | How |
| --- | --- |
| Select swatches | Click; Ctrl+click to add/remove; Shift+click for a range; Ctrl+A for all |
| Copy | Double-click a swatch (or Ctrl+C for the selection); right-click → Copy RGB |
| Remove | Delete / Backspace, or right-click → Remove |
| Reorder | Drag swatches within the group; drop them on another group to move them there |
| Group swatches | Select some, right-click → New group from selection (or Move to group) |
| Groups | Click to open · click the open group again to minimise it (it stays the current group, so picked colours still go there) · double-click the name or F2 to rename · drag to reorder · × to delete · **+ New group** |

## Saving

**File → Save** writes a project folder:

```
MyBoard.moodboard/
    board.json    # items, palette, view position
    images/       # copies of every image (named by content hash)
```

The folder is self-contained, so you can zip it, move it or share it. Open it again with
**File → Open Board…** (pick the `.moodboard` folder), **File → Open Recent**, or by
dropping the folder onto the canvas. **File → Export as PNG…** renders the whole board
to an image.

## Code layout

| File | Contents |
| --- | --- |
| `main.py` | Entry point, main window, menus, toolbar, file actions |
| `canvas.py` | `BoardScene` (content + undo commands) and `BoardView` (pan/zoom, handles, drag-drop, eyedropper) |
| `items.py` | Image, note and group items, plus JSON (de)serialisation |
| `palette.py` | Palette side panel |
| `storage.py` | Save/load board folders, PNG export, recent boards |
| `ai.py` | Image analysis: dominant-colour extraction (colorthief + Pillow) |
| `version.py` | App version (set from the git tag in release builds) |
| `selftest.py` | `--self-test` smoke test the build runs on the packaged app |

## Building the Windows app

```powershell
pip install -r requirements-build.txt   # pinned versions + PyInstaller
.\packaging\build.ps1
```

This produces, in `dist\`, the app folder, a portable zip and, if
[Inno Setup 6](https://jrsoftware.org/isdl.php) is installed, the installer.
The build runs `Moodboard.exe --self-test` on the packaged app and stops if
anything is missing.

| File | Role |
| --- | --- |
| `moodboard.spec` | PyInstaller recipe (what goes into the app folder) |
| `packaging/build.ps1` | One-command build: app → self-test → zip → installer |
| `packaging/installer.iss` | Inno Setup installer script |
| `packaging/make_icon.py` | Regenerates `assets/moodboard.ico` |
| `.github/workflows/build.yml` | Builds and publishes releases on GitHub |

## Releasing a new version

GitHub builds the app for you. Commit your changes, then tag the commit with
the new version and push the tag:

```powershell
git tag v1.1.0
git push origin v1.1.0
```

A few minutes later the installer and zip appear on the repository's
**Releases** page. The version number comes from the tag, and is shown in
Help → About. To try a build without publishing it, open the **Actions** tab,
choose **Build Windows app → Run workflow**, and download the result from
that run's page.

## Licence notes

The Windows build bundles Qt/PySide6 (LGPL v3), Python, Pillow and colorthief;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the `licenses` folder.
