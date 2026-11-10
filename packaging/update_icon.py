import os
import struct
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QByteArray, QBuffer, QIODevice

# SVG optimizado para aprovechar todo el lienzo útil de 64x64 (máxima presencia visual en barra de tareas)
SVG_CONTENT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <g transform="translate(32, 32.5) scale(1.18) translate(-32, -34)">
    <path d="M6 16a4 4 0 0 1 4-4h14l6 6h24a4 4 0 0 1 4 4v30a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill="#1b65b6"/>
    <path d="M6 24h52v28a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill="#3b92f2"/>
    <line x1="31.25" y1="24" x2="31.25" y2="56" stroke="#124987" stroke-width="1.5" opacity="0.3"/>
    <line x1="32.5" y1="24" x2="32.5" y2="56" stroke="#ffffff" stroke-width="1.5" opacity="0.8"/>
    <g id="tux-left">
      <ellipse cx="11.2" cy="42" rx="2" ry="5.5" transform="rotate(15 11.2 42)" fill="#1c2027"/>
      <ellipse cx="26.8" cy="42" rx="2" ry="5.5" transform="rotate(-15 26.8 42)" fill="#1c2027"/>
      <ellipse cx="19" cy="41" rx="8" ry="9.5" fill="#20242c"/>
      <ellipse cx="19" cy="43.8" rx="5.2" ry="6.2" fill="#ffffff"/>
      <circle cx="16.5" cy="35.5" r="1.9" fill="#ffffff"/>
      <circle cx="21.5" cy="35.5" r="1.9" fill="#ffffff"/>
      <circle cx="16.8" cy="35.8" r="0.85" fill="#20242c"/>
      <circle cx="21.2" cy="35.8" r="0.85" fill="#20242c"/>
      <path d="M16.5 38.2 19 37.1l2.5 1.1L19 40.2z" fill="#f28c1e"/>
      <path d="M12 50.3c1.4-1.2 3.6-1.2 5 0 .4.4.1 1.1-.4 1.1h-4.2c-.6 0-.9-.7-.4-1.1zm9 0c1.4-1.2 3.6-1.2 5 0 .4.4.1 1.1-.4 1.1h-4.2c-.6 0-.9-.7-.4-1.1z" fill="#f28c1e"/>
    </g>
    <g id="tux-right">
      <ellipse cx="37.2" cy="42" rx="2" ry="5.5" transform="rotate(15 37.2 42)" fill="#1c2027"/>
      <ellipse cx="52.8" cy="42" rx="2" ry="5.5" transform="rotate(-15 52.8 42)" fill="#1c2027"/>
      <ellipse cx="45" cy="41" rx="8" ry="9.5" fill="#20242c"/>
      <ellipse cx="45" cy="43.8" rx="5.2" ry="6.2" fill="#ffffff"/>
      <circle cx="42.5" cy="35.5" r="1.9" fill="#ffffff"/>
      <circle cx="47.5" cy="35.5" r="1.9" fill="#ffffff"/>
      <circle cx="42.8" cy="35.8" r="0.85" fill="#20242c"/>
      <circle cx="47.2" cy="35.8" r="0.85" fill="#20242c"/>
      <path d="M42.5 38.2 45 37.1l2.5 1.1L45 40.2z" fill="#f28c1e"/>
      <path d="M38 50.3c1.4-1.2 3.6-1.2 5 0 .4.4.1 1.1-.4 1.1h-4.2c-.6 0-.9-.7-.4-1.1zm9 0c1.4-1.2 3.6-1.2 5 0 .4.4.1 1.1-.4 1.1h-4.2c-.6 0-.9-.7-.4-1.1z" fill="#f28c1e"/>
    </g>
  </g>
</svg>'''

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG_DEST = os.path.join(ROOT, "directory_commander_tux_tux", "resources", "directory-commander-tux-tux.svg")
ICO_DEST = os.path.join(ROOT, "packaging", "windows", "app.ico")

with open(SVG_DEST, "w", encoding="utf-8") as f:
    f.write(SVG_CONTENT.strip() + "\n")
print("SVG guardado en:", SVG_DEST)

renderer = QSvgRenderer(QByteArray(SVG_CONTENT.encode("utf-8")))
sizes = [16, 24, 32, 48, 64, 128, 256]
png_datas = []

for s in sizes:
    img = QImage(s, s, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    renderer.render(p)
    p.end()
    
    buf = QByteArray()
    qbuf = QBuffer(buf)
    qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(qbuf, "PNG")
    png_datas.append(bytes(buf))

count = len(sizes)
header = struct.pack("<HHH", 0, 1, count)
offset = 6 + (16 * count)
entries = []
for s, data in zip(sizes, png_datas):
    w = 0 if s == 256 else s
    h = 0 if s == 256 else s
    size_bytes = len(data)
    entry = struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, size_bytes, offset)
    entries.append(entry)
    offset += size_bytes

with open(ICO_DEST, "wb") as f:
    f.write(header)
    for entry in entries:
        f.write(entry)
    for data in png_datas:
        f.write(data)

print(f"ICO guardado en: {ICO_DEST} ({os.path.getsize(ICO_DEST)} bytes, {count} resoluciones)")
