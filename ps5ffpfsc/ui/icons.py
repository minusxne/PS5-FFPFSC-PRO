"""Small line-icon set, drawn as SVG and tinted at runtime."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 24x24 viewBox, stroked with the tint colour.
_PATHS: dict[str, str] = {
    "compress": '<path d="M12 3v11"/><path d="m7.5 9.5 4.5 4.5 4.5-4.5"/>'
                '<path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "unpack": '<path d="M12 14V3"/><path d="m7.5 7.5 4.5-4.5 4.5 4.5"/>'
              '<path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "activity": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "settings": '<path d="M4 6h10"/><path d="M18 6h2"/><circle cx="16" cy="6" r="2"/>'
                '<path d="M4 12h3"/><path d="M11 12h9"/><circle cx="9" cy="12" r="2"/>'
                '<path d="M4 18h10"/><path d="M18 18h2"/><circle cx="16" cy="18" r="2"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "folder-open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v1"/>'
                   '<path d="M3 7v10a2 2 0 0 0 2 2h12.5a2 2 0 0 0 1.9-1.4L21.5 12H7.2a2 2 0 0 0-1.9 1.4L3 19"/>',
    "file": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    "disk": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>',
    "archive": '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/>'
               '<path d="M10 12h4"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "play": '<path d="M7 4.5v15l12-7.5z" fill="{c}"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="{c}"/>',
    "x": '<path d="M6 6l12 12"/><path d="M18 6 6 18"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/>'
             '<path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "minus": '<path d="M6 12h12"/>',
    "more": '<circle cx="5.5" cy="12" r="1.3" fill="{c}"/><circle cx="12" cy="12" r="1.3" fill="{c}"/>'
            '<circle cx="18.5" cy="12" r="1.3" fill="{c}"/>',
    "alert": '<path d="M12 3.5 2.5 20h19z"/><path d="M12 10v4.5"/><path d="M12 17.5h.01"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="m8.5 12 2.5 2.5 4.5-4.5"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "save": '<path d="M5 3h11l4 4v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M8 3v5h7V3"/><rect x="7" y="13" width="10" height="6"/>',
    "expand": '<path d="m7 9 5 5 5-5"/>',
    "collapse": '<path d="m7 15 5-5 5 5"/>',
    "gamepad": '<path d="M7 8h10a4 4 0 0 1 4 4l-.6 4.2a2.5 2.5 0 0 1-4.3 1.3L14.5 16h-5l-1.6 1.5a2.5 2.5 0 0 1-4.3-1.3L3 12a4 4 0 0 1 4-4z"/>'
               '<path d="M8 11v3"/><path d="M6.5 12.5h3"/><circle cx="15.5" cy="12" r=".6" fill="{c}"/><circle cx="17.5" cy="13.5" r=".6" fill="{c}"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
    "list": '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><path d="M4.5 6h.01"/><path d="M4.5 12h.01"/><path d="M4.5 18h.01"/>',
}


def svg(name: str, color: str, stroke: float = 1.8) -> bytes:
    body = _PATHS[name].replace("{c}", color)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    ).encode()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = 18, ratio: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color)))
    px = QPixmap(QSize(int(size * ratio), int(size * ratio)))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px.width(), px.height()))
    painter.end()
    px.setDevicePixelRatio(ratio)
    return px


def icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))


def icon_file(name: str, color: str, stroke: float = 2.6) -> str:
    """Write an SVG to the temp dir and return a stylesheet-friendly path."""
    folder = Path(tempfile.gettempdir()) / "ps5ffpfsc-icons"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}-{color.lstrip('#')}.svg"
    if not path.exists():
        path.write_bytes(svg(name, color, stroke))
    return path.as_posix()
