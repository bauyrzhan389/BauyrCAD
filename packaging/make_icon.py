"""Render the built-in SVG app icon to packaging/bauyrcad.ico."""
import io
import os
import sys

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtWidgets import QApplication

from bauyrcad.gui.icons import icon

app = QApplication.instance() or QApplication(sys.argv)
img_bytes = QByteArray()
buf = QBuffer(img_bytes)
buf.open(QIODevice.WriteOnly)
icon("app").pixmap(256, 256).save(buf, "PNG")
img = Image.open(io.BytesIO(bytes(img_bytes)))
out = os.path.join(os.path.dirname(__file__), "bauyrcad.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out)
