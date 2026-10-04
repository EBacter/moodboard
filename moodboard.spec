# PyInstaller build recipe. Use packaging/build.ps1 rather than running this directly.
# Produces dist/Moodboard/ (Moodboard.exe plus its libraries in _internal/).
import re
from pathlib import Path

from PyInstaller.utils.win32 import versioninfo as vi

ROOT = Path(SPECPATH)
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "version.py").read_text()).group(1)
NUMBERS = tuple(([int(n) for n in re.findall(r"\d+", VERSION)] + [0, 0, 0, 0])[:4])

# Shown in the .exe's Properties > Details and in Task Manager.
version_info = vi.VSVersionInfo(
    ffi=vi.FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        vi.StringFileInfo(
            [
                vi.StringTable(
                    "040904B0",
                    [
                        vi.StringStruct("CompanyName", "Moodboard"),
                        vi.StringStruct("FileDescription", "Moodboard"),
                        vi.StringStruct("FileVersion", VERSION),
                        vi.StringStruct("InternalName", "Moodboard"),
                        vi.StringStruct("OriginalFilename", "Moodboard.exe"),
                        vi.StringStruct("ProductName", "Moodboard"),
                        vi.StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        vi.VarFileInfo([vi.VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets" / "moodboard.ico"), "assets")],
    excludes=["tkinter", "PySide6.QtNetwork"],
)

# Qt pieces the app never uses (it needs QtCore/QtGui/QtWidgets plus the
# platform and image-format plugins). Dropping them takes ~60 MB off the build;
# the build's --self-test run checks nothing needed went missing.
UNUSED = (
    "opengl32sw.dll",  # software OpenGL fallback; the canvas uses the raster engine
    "qtvirtualkeyboardplugin",  # input-method plugin that drags in Qt Quick/QML:
    "qt6virtualkeyboard",
    "qt6quick",
    "qt6qml",
    "qt6opengl.dll",
    "qpdf.dll",  # PDF image plugin and its library
    "qt6pdf",
    "qt6network",  # Qt networking and its TLS backends (downloads use Python's urllib)
    "/tls/",
    "/networkinformation/",
    "libcrypto-3-x64",
    "libssl-3-x64",
)


def needed(entry) -> bool:
    dest = entry[0].lower().replace("\\", "/")
    if "pyside6/translations/" in dest:  # Qt's own UI translations; the app is English-only
        return False
    return not any(token in dest for token in UNUSED)


a.binaries = [b for b in a.binaries if needed(b)]
a.datas = [d for d in a.datas if needed(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Moodboard",
    icon=str(ROOT / "assets" / "moodboard.ico"),
    version=version_info,
    console=False,  # GUI app: no console window
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Moodboard", upx=False)
