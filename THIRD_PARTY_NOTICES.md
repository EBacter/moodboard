# Third-party notices

The Windows build of Moodboard bundles the following open-source software.
Full licence texts are in the `licenses` folder next to this file.

| Component | Licence | Source |
| --- | --- | --- |
| Qt 6 and Qt for Python (PySide6, Shiboken6) | GNU LGPL v3 (`LGPL-3.0.txt`, which builds on `GPL-3.0.txt`) | https://code.qt.io, https://download.qt.io/official_releases/QtForPython/ |
| Python 3 runtime | Python Software Foundation License (`Python-LICENSE.txt`) | https://www.python.org |
| Pillow (and the image libraries it bundles) | MIT-CMU (`Pillow-LICENSE.txt`) | https://github.com/python-pillow/Pillow |
| colorthief | BSD (`colorthief-LICENSE.txt`) | https://github.com/fengsp/color-thief-py |

## About the Qt libraries (LGPL v3)

Moodboard uses unmodified Qt and PySide6 libraries, linked dynamically: they
are the separate `Qt6*.dll`, `pyside6*.dll` and `shiboken6*.dll` files in the
installation folder, and you may replace them with your own compatible builds.
The corresponding source code is available from the Qt Project at the links
above, and the exact versions used are listed in `requirements-build.txt` in
Moodboard's source repository.
