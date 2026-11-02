# Componentes de terceros

El código de este repositorio se publica bajo la licencia MIT (archivo `LICENSE`). El repositorio no incluye el código ni los binarios de los componentes siguientes, pero los instaladores y las versiones portables sí llevan sus bibliotecas, sin modificar.

| Componente | Licencia | Dónde está su código |
|---|---|---|
| [Qt](https://www.qt.io/) y [PySide6 / Shiboken6](https://doc.qt.io/qtforpython/) | LGPL-3.0 (también GPL-2.0 o GPL-3.0) | https://code.qt.io/ |
| [FFmpeg](https://ffmpeg.org/), incluido con Qt Multimedia | LGPL | https://ffmpeg.org/download.html |
| [Python](https://www.python.org/) | PSF-2.0 | https://github.com/python/cpython |
| [OpenSSL](https://www.openssl.org/), incluido con Python y Qt | Apache-2.0 | https://github.com/openssl/openssl |
| [Mesa](https://www.mesa3d.org/) (`opengl32sw.dll`, solo en Windows) | MIT | https://gitlab.freedesktop.org/mesa/mesa |
| Microsoft Visual C++ Runtime (solo en Windows) | Licencia de redistribución de Microsoft | — |

Los paquetes se generan con [PyInstaller](https://pyinstaller.org/), cuya licencia (GPL con una excepción) permite repartir lo que genera bajo cualquier licencia. En Linux, PyInstaller añade además las bibliotecas del sistema que necesita Qt, cada una con la licencia de su proyecto.

## Sustituir las bibliotecas

La LGPL permite sustituir estas bibliotecas por otras versiones compatibles. Los paquetes las llevan como archivos separados, en la carpeta `_internal` junto al programa: basta con reemplazarlas ahí. En un AppImage, primero hay que extraerlo con `--appimage-extract`.

Los textos de las licencias GNU están en la carpeta `licenses`, junto al programa, en los paquetes publicados, y en https://www.gnu.org/licenses/.
