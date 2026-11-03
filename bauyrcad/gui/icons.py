"""Built-in SVG icons (no external asset files needed)."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, qVersion
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

INK = "#27466d"
BLUE = "#2f7de1"
RED = "#d9363e"
GREEN = "#2f9e57"
ORANGE = "#f08a24"

_ICONS = {
    "properties": '<path d="M4 5h16M4 12h16M4 19h16" stroke="#555" stroke-width="2"/><path d="M8 2v6M16 9v6M10 16v6" stroke="#555" stroke-width="4"/>',
    "shaded": '<path d="M3 7l9-5 9 5v10l-9 5-9-5z" fill="#aaa" stroke="#444" stroke-width="1.4"/><path d="M3 7l9 5 9-5-9-5z" fill="#ddd"/><path d="M12 12l9-5v10l-9 5z" fill="#777"/>',
    "shaded_edges": '<path d="M3 7l9-5 9 5v10l-9 5-9-5zM3 7l9 5 9-5M12 12v10" fill="#ccc" stroke="#444" stroke-width="1.4"/>',
    "check_geometry": '<path d="M3 7l7-4 7 4v9l-7 4-7-4zM3 7l7 4 7-4M10 11v9" fill="#eee" stroke="#444" stroke-width="1.4"/><circle cx="17" cy="17" r="6" fill="#fff" stroke="#444" stroke-width="1.3"/><path d="M14 17l2 2 4-5" fill="none" stroke="#32834d" stroke-width="2"/>',
    "hide_component": '<path d="M2 12q10-13 20 0-10 13-20 0z" fill="none" stroke="#444" stroke-width="1.5"/><circle cx="12" cy="12" r="3" fill="none" stroke="#444" stroke-width="1.5"/><path d="M3 21L21 3" stroke="#444" stroke-width="2"/>',
    "show_components": '<path d="M2 12q10-13 20 0-10 13-20 0z" fill="none" stroke="#444" stroke-width="1.5"/><circle cx="12" cy="12" r="3" fill="#666"/>',
    "isolate_component": '<path d="M2 8V2h6M16 2h6v6M22 16v6h-6M8 22H2v-6" fill="none" stroke="#777" stroke-width="1.5"/><path d="M7 9l5-3 5 3v6l-5 3-5-3zM7 9l5 3 5-3M12 12v6" fill="#ddd" stroke="#333" stroke-width="1.3"/>',
    'bounds': '<path d="M5 8l7-4 7 4v9l-7 4-7-4zM5 8l7 4 7-4M12 12v9" fill="#f2f2f2" stroke="#353535" stroke-width="1.4"/><path d="M2 5v15M1 5h3M1 20h3M7 2h13M7 1v3M20 1v3" fill="none" stroke="#777" stroke-width="1.2"/>',
    'mass_properties': '<circle cx="12" cy="5" r="2.5" fill="none" stroke="#353535" stroke-width="1.6"/><path d="M7 8h10l4 13H3z" fill="#e5e5e5" stroke="#353535" stroke-width="1.6"/><path d="M8 17v-5l4 4 4-4v5" fill="none" stroke="#353535" stroke-width="1.4"/>',
    'interference': '<rect x="2" y="3" width="13" height="12" fill="#eee" stroke="#353535" stroke-width="1.5"/><rect x="9" y="9" width="13" height="12" fill="#ddd" stroke="#353535" stroke-width="1.5"/><path d="M9 9h6v6H9z" fill="#bc4545" stroke="#353535" stroke-width="1.3"/>',

    'view_image': '<path d="M3 7h5l2-3h5l2 3h4v13H3z" fill="#eee" stroke="#353535" stroke-width="1.6"/><circle cx="12" cy="13" r="4" fill="#fff" stroke="#353535" stroke-width="1.5"/><path d="M18 9h1" stroke="#353535" stroke-width="1.6"/>',
    'copy_view_image': '<path d="M3 16V3h14" fill="none" stroke="#777" stroke-width="1.5"/><rect x="7" y="7" width="14" height="14" fill="#f2f2f2" stroke="#353535" stroke-width="1.5"/><path d="M8 18l4-5 3 3 2-2 3 4" fill="none" stroke="#353535" stroke-width="1.4"/><circle cx="17" cy="11" r="1.3" fill="#777"/>',

    "clearance": '<path d="M2 4h5v16H2zM17 4h5v16h-5z" fill="#e5e5e5" stroke="#353535" stroke-width="1.5"/><path d="M8 12h8M8 12l3-2v4zM16 12l-3-2v4z" fill="#353535" stroke="#353535" stroke-width="1"/>',
    "section": '<path d="M4 8l8-4 8 4v10l-8 4-8-4z" fill="#eee" stroke="#333" stroke-width="1.5"/><path d="M4 13l8-4 8 4-8 4z" fill="#d4c7a6" stroke="#333" stroke-width="1.3"/><path d="M7 14l6-3m-2 5l6-3" stroke="#555" stroke-width="1"/>',
    "new": f'<path d="M6 3h8l4 4v14H6z" fill="#fff" stroke="{INK}" stroke-width="1.6"/><path d="M14 3v4h4" fill="none" stroke="{INK}" stroke-width="1.6"/>',
    "open": f'<path d="M3 7h6l2 2h10v10H3z" fill="#f7d68a" stroke="{INK}" stroke-width="1.6"/>',
    "save": f'<path d="M4 4h13l3 3v13H4z" fill="#fff" stroke="{INK}" stroke-width="1.6"/><rect x="7" y="4" width="8" height="5" fill="{BLUE}"/><rect x="7" y="13" width="10" height="7" fill="none" stroke="{INK}" stroke-width="1.4"/>',
    "import": f'<path d="M5 14v6h14v-6" fill="none" stroke="{INK}" stroke-width="1.8"/><path d="M12 3v11M8 10l4 4 4-4" fill="none" stroke="{GREEN}" stroke-width="2"/>',
    "export": f'<path d="M5 14v6h14v-6" fill="none" stroke="{INK}" stroke-width="1.8"/><path d="M12 14V3M8 7l4-4 4 4" fill="none" stroke="{BLUE}" stroke-width="2"/>',
    "undo": f'<path d="M9 5L4 10l5 5" fill="none" stroke="{INK}" stroke-width="2"/><path d="M4 10h10a6 6 0 010 12H9" fill="none" stroke="{INK}" stroke-width="2"/>',
    "redo": f'<path d="M15 5l5 5-5 5" fill="none" stroke="{INK}" stroke-width="2"/><path d="M20 10H10a6 6 0 000 12h5" fill="none" stroke="{INK}" stroke-width="2"/>',
    "sketch": f'<rect x="3" y="3" width="18" height="18" fill="#eaf2fd" stroke="{INK}" stroke-width="1.2"/><path d="M6 17l5-9 6 6" fill="none" stroke="{BLUE}" stroke-width="2"/><circle cx="6" cy="17" r="1.6" fill="{INK}"/><circle cx="11" cy="8" r="1.6" fill="{INK}"/><circle cx="17" cy="14" r="1.6" fill="{INK}"/>',
    "line": f'<path d="M4 19L20 5" stroke="{BLUE}" stroke-width="2.2"/><circle cx="4" cy="19" r="2" fill="{INK}"/><circle cx="20" cy="5" r="2" fill="{INK}"/>',
    "rectangle": f'<rect x="4" y="6" width="16" height="12" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="4" cy="18" r="2" fill="{INK}"/><circle cx="20" cy="6" r="2" fill="{INK}"/>',
    "circle": f'<circle cx="12" cy="12" r="8" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="12" cy="12" r="1.8" fill="{INK}"/><path d="M12 12h8" stroke="{INK}" stroke-width="1" stroke-dasharray="2 1.5"/>',
    "exit_sketch": f'<rect x="3" y="3" width="18" height="18" fill="#eaf2fd" stroke="{INK}" stroke-width="1.2"/><path d="M7 12l3.5 3.5L17 8" fill="none" stroke="{GREEN}" stroke-width="2.6"/>',
    "extrude": f'<path d="M4 16l6 4 10-5-6-3z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M4 9l6 4 10-5-6-3z" fill="#f2c14e" stroke="{INK}" stroke-width="1.2"/><path d="M4 9v7M10 13v7M20 8v7" stroke="{INK}" stroke-width="1.2"/><path d="M12 3v6" stroke="{ORANGE}" stroke-width="1.8"/>',
    "cut": f'<path d="M3 13l8 5 10-5-8-5z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><ellipse cx="12" cy="13" rx="4" ry="2" fill="#fff" stroke="{RED}" stroke-width="1.6"/><path d="M12 4v7M9 8l3 3 3-3" fill="none" stroke="{RED}" stroke-width="1.8"/>',
    "study": f'<path d="M4 18l5-9 4 5 3-3 4 7z" fill="url(#g)" stroke="{INK}" stroke-width="1.2"/><defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#2f6fe0"/><stop offset=".5" stop-color="#3fd06a"/><stop offset="1" stop-color="#e8412e"/></linearGradient></defs>',
    "material": f'<path d="M4 8l8-4 8 4-8 4z" fill="#d7dde6" stroke="{INK}" stroke-width="1.2"/><path d="M4 8v8l8 4V12z" fill="#aeb8c6" stroke="{INK}" stroke-width="1.2"/><path d="M20 8v8l-8 4V12z" fill="#8994a5" stroke="{INK}" stroke-width="1.2"/>',
    "fixed": f'<path d="M4 16h16" stroke="{GREEN}" stroke-width="2.4"/><path d="M6 16l-2 4M10 16l-2 4M14 16l-2 4M18 16l-2 4" stroke="{GREEN}" stroke-width="1.5"/><path d="M8 16l4-8 4 8" fill="#c9ecd4" stroke="{GREEN}" stroke-width="1.6"/>',
    "force": f'<path d="M12 3v13" stroke="{RED}" stroke-width="2.4"/><path d="M7 12l5 7 5-7z" fill="{RED}"/><circle cx="12" cy="20.5" r="1.5" fill="{INK}"/>',
    "mesh": f'<path d="M3 20L12 4l9 16z" fill="#eaf2fd" stroke="{INK}" stroke-width="1.3"/><path d="M7.5 12h9M12 20l-4.5-8M12 20l4.5-8" stroke="{INK}" stroke-width="1.1"/>',
    "run": f'<circle cx="12" cy="12" r="9" fill="{GREEN}"/><path d="M10 8l6 4-6 4z" fill="#fff"/>',
    "results": f'<rect x="3" y="3" width="4" height="18" fill="url(#r)"/><defs><linearGradient id="r" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#2f6fe0"/><stop offset=".5" stop-color="#3fd06a"/><stop offset="1" stop-color="#e8412e"/></linearGradient></defs><path d="M10 18c3-1 4-9 10-12" fill="none" stroke="{INK}" stroke-width="2"/>',
    "iso": f'<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" fill="#dbe6f5" stroke="{INK}" stroke-width="1.3"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9" fill="none" stroke="{INK}" stroke-width="1.3"/>',
    "front": f'<rect x="5" y="5" width="14" height="14" fill="#f2c14e" stroke="{INK}" stroke-width="1.4"/>',
    "top": f'<path d="M3 9l6-5h12l-6 5z" fill="#f2c14e" stroke="{INK}" stroke-width="1.3"/><path d="M3 9h12v11H3zM15 9l6-5v11l-6 5" fill="#dbe6f5" stroke="{INK}" stroke-width="1.3"/>',
    "right": f'<path d="M3 9h12v11H3z" fill="#dbe6f5" stroke="{INK}" stroke-width="1.3"/><path d="M15 9l6-5v11l-6 5z" fill="#f2c14e" stroke="{INK}" stroke-width="1.3"/><path d="M3 9l6-5h12" fill="none" stroke="{INK}" stroke-width="1.3"/>',
    "normal_to": f'<rect x="4" y="8" width="11" height="11" fill="#dbe6f5" stroke="{INK}" stroke-width="1.3"/><path d="M15 4l5 0 0 5M20 4l-7 7" fill="none" stroke="{BLUE}" stroke-width="1.8"/>',
    "fit": f'<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" fill="none" stroke="{INK}" stroke-width="2"/><rect x="8" y="8" width="8" height="8" fill="#dbe6f5" stroke="{INK}" stroke-width="1.2"/>',
    "delete": f'<path d="M6 7h12l-1 13H7z" fill="#fde2e2" stroke="{RED}" stroke-width="1.5"/><path d="M4 7h16M10 4h4" stroke="{RED}" stroke-width="1.8"/>',
    "part": f'<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.3"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9" fill="none" stroke="{INK}" stroke-width="1.3"/>',
    "import_feature": f'<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" fill="#e3d5f5" stroke="{INK}" stroke-width="1.3"/><path d="M9 11l3 3 3-3M12 6v8" fill="none" stroke="{GREEN}" stroke-width="1.8"/>',
    "plane": f'<path d="M3 16l5-8h13l-5 8z" fill="#cfe0f7" stroke="{INK}" stroke-width="1.3"/>',
    "folder": f'<path d="M3 7h6l2 2h10v10H3z" fill="#f7d68a" stroke="{INK}" stroke-width="1.3"/>',
    "warning": f'<path d="M12 3l10 18H2z" fill="#ffd34d" stroke="#8a5a00" stroke-width="1.3"/><path d="M12 9v6" stroke="#5a3a00" stroke-width="2"/><circle cx="12" cy="18" r="1.2" fill="#5a3a00"/>',
    "app": f'<path d="M12 2l9 5v10l-9 5-9-5V7z" fill="#2f6fe0"/><path d="M3 7l9 5 9-5M12 12v10" fill="none" stroke="#fff" stroke-width="1.4"/><path d="M5 14l3 1.7 3-3 3 4 4-3" fill="none" stroke="#ffcf3d" stroke-width="1.5"/>',
}

_ICONS.update({
    "select": f'<path d="M5 3l12 9-5.5 1 3 6.5-2.5 1.2-3-6.6L5 18z" fill="#fff" stroke="{INK}" stroke-width="1.5"/>',
    "centerline": f'<path d="M3 20L21 4" stroke="{BLUE}" stroke-width="2" stroke-dasharray="5 2 1.5 2"/>',
    "midpoint_line": f'<path d="M3 17L21 7" stroke="{BLUE}" stroke-width="2.2"/><circle cx="12" cy="12" r="2.4" fill="{ORANGE}"/>',
    "rect_center": f'<rect x="3" y="6" width="18" height="12" fill="none" stroke="{BLUE}" stroke-width="2.2"/><path d="M3 6l18 12M21 6L3 18" stroke="{INK}" stroke-width="0.8" stroke-dasharray="2 1.5"/><circle cx="12" cy="12" r="1.8" fill="{INK}"/>',
    "rect_3pt": f'<path d="M4 14l7-10 9 6-7 10z" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="4" cy="14" r="1.8" fill="{INK}"/><circle cx="11" cy="4" r="1.8" fill="{INK}"/><circle cx="20" cy="10" r="1.8" fill="{INK}"/>',
    "rect_3pt_center": f'<path d="M4 14l7-10 9 6-7 10z" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="12" cy="12" r="1.8" fill="{INK}"/><path d="M4 14l16-4M11 4l2 16" stroke="{INK}" stroke-width="0.8" stroke-dasharray="2 1.5"/>',
    "parallelogram": f'<path d="M3 18l5-12h13l-5 12z" fill="none" stroke="{BLUE}" stroke-width="2.2"/>',
    "slot": f'<path d="M8 7h8a5 5 0 010 10H8A5 5 0 018 7z" fill="none" stroke="{BLUE}" stroke-width="2.2"/><path d="M8 12h8" stroke="{INK}" stroke-width="0.9" stroke-dasharray="2 1.5"/>',
    "slot_center": f'<path d="M8 7h8a5 5 0 010 10H8A5 5 0 018 7z" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="12" cy="12" r="1.8" fill="{INK}"/>',
    "circle_perimeter": f'<circle cx="12" cy="12" r="8" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="4" cy="12" r="1.8" fill="{INK}"/><circle cx="12" cy="4" r="1.8" fill="{INK}"/><circle cx="18" cy="18" r="1.8" fill="{INK}"/>',
    "arc_center": f'<path d="M20 12A8 8 0 0112 20" fill="none" stroke="{BLUE}" stroke-width="2.2"/><path d="M12 12h8M12 12v8" stroke="{INK}" stroke-width="0.8" stroke-dasharray="2 1.5"/><circle cx="12" cy="12" r="1.8" fill="{INK}"/>',
    "arc_tangent": f'<path d="M2 18h9" stroke="{INK}" stroke-width="2"/><path d="M11 18a7 7 0 007-7V5" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="11" cy="18" r="1.8" fill="{ORANGE}"/>',
    "arc_3pt": f'<path d="M3 17a10 10 0 0118 0" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="3" cy="17" r="1.8" fill="{INK}"/><circle cx="21" cy="17" r="1.8" fill="{INK}"/><circle cx="12" cy="7.6" r="1.8" fill="{INK}"/>',
    "polygon": f'<path d="M12 3l8 5v8l-8 5-8-5V8z" fill="none" stroke="{BLUE}" stroke-width="2.2"/>',
    "spline": f'<path d="M3 17c4-12 8 6 11-4s5-3 7-6" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="3" cy="17" r="1.6" fill="{INK}"/><circle cx="14" cy="13" r="1.6" fill="{INK}"/><circle cx="21" cy="7" r="1.6" fill="{INK}"/>',
    "point": f'<circle cx="12" cy="12" r="3.2" fill="{BLUE}"/><path d="M12 4v4M12 16v4M4 12h4M16 12h4" stroke="{INK}" stroke-width="1.2"/>',
    "smart_dim": f'<path d="M4 16h16M4 12v8M20 12v8" stroke="{INK}" stroke-width="1.4"/><path d="M4 16l3-2v4zM20 16l-3-2v4z" fill="{INK}"/><path d="M8 9l2-5 2 5M9 7.5h2M14 4v5h2.5" fill="none" stroke="{BLUE}" stroke-width="1.5"/>',
    "relation": f'<path d="M4 18L20 6" stroke="{BLUE}" stroke-width="2"/><rect x="13" y="13" width="8" height="8" rx="1.5" fill="#d9f2df" stroke="{GREEN}"/><path d="M15 17h4" stroke="{GREEN}" stroke-width="1.6"/>',
    "trim": f'<path d="M3 12h18" stroke="{BLUE}" stroke-width="2.2"/><path d="M9 4v16M15 4v16" stroke="{INK}" stroke-width="1.4"/><path d="M10 12h4" stroke="#fff" stroke-width="3"/><path d="M10.5 9.5l3 5M13.5 9.5l-3 5" stroke="{RED}" stroke-width="1.6"/>',
    "extend": f'<path d="M3 12h9" stroke="{BLUE}" stroke-width="2.2"/><path d="M12 12h7" stroke="{BLUE}" stroke-width="2.2" stroke-dasharray="2 1.5"/><path d="M20 4v16" stroke="{INK}" stroke-width="1.6"/><path d="M15 9l3 3-3 3" fill="none" stroke="{ORANGE}" stroke-width="1.6"/>',
    "offset": f'<path d="M4 18V8a4 4 0 014-4h12" fill="none" stroke="{INK}" stroke-width="1.6"/><path d="M8 20V11a3 3 0 013-3h9" fill="none" stroke="{BLUE}" stroke-width="2.2"/>',
    "mirror": f'<path d="M12 2v20" stroke="{INK}" stroke-width="1.2" stroke-dasharray="3 1.5 1 1.5"/><path d="M9 6L4 17h5z" fill="#cfe0f7" stroke="{BLUE}" stroke-width="1.6"/><path d="M15 6l5 11h-5z" fill="none" stroke="{BLUE}" stroke-width="1.6" stroke-dasharray="2 1.2"/>',
    "fillet_sk": f'<path d="M4 20V11a7 7 0 017-7h9" fill="none" stroke="{BLUE}" stroke-width="2.2"/><path d="M4 11V4h7" fill="none" stroke="{INK}" stroke-width="1" stroke-dasharray="2 1.5"/>',
    "chamfer_sk": f'<path d="M4 20V10l6-6h10" fill="none" stroke="{BLUE}" stroke-width="2.2"/><path d="M4 10V4h6" fill="none" stroke="{INK}" stroke-width="1" stroke-dasharray="2 1.5"/>',
    "lin_pattern": f'<circle cx="6" cy="17" r="2.5" fill="none" stroke="{BLUE}" stroke-width="1.8"/><circle cx="12" cy="11" r="2.5" fill="none" stroke="{BLUE}" stroke-width="1.8" stroke-dasharray="1.5 1"/><circle cx="18" cy="5" r="2.5" fill="none" stroke="{BLUE}" stroke-width="1.8" stroke-dasharray="1.5 1"/>',
    "circ_pattern": f'<circle cx="12" cy="12" r="1.6" fill="{INK}"/><circle cx="12" cy="4.5" r="2.3" fill="none" stroke="{BLUE}" stroke-width="1.8"/><circle cx="18.5" cy="15.5" r="2.3" fill="none" stroke="{BLUE}" stroke-width="1.8" stroke-dasharray="1.5 1"/><circle cx="5.5" cy="15.5" r="2.3" fill="none" stroke="{BLUE}" stroke-width="1.8" stroke-dasharray="1.5 1"/>',
    "move": f'<path d="M12 2v20M2 12h20" stroke="{INK}" stroke-width="1.6"/><path d="M12 2l-3 3h6zM12 22l-3-3h6zM2 12l3-3v6zM22 12l-3-3v6z" fill="{BLUE}"/>',
    "copy": f'<rect x="3" y="7" width="11" height="11" fill="#fff" stroke="{INK}" stroke-width="1.5"/><rect x="9" y="3" width="11" height="11" fill="#e6f0fc" stroke="{BLUE}" stroke-width="1.6"/>',
    "rotate": f'<path d="M19 12a7 7 0 11-3-5.7" fill="none" stroke="{BLUE}" stroke-width="2.2"/><path d="M16 2v5h5" fill="none" stroke="{INK}" stroke-width="1.8"/>',
    "scale": f'<rect x="3" y="11" width="8" height="8" fill="none" stroke="{INK}" stroke-width="1.4"/><rect x="3" y="3" width="18" height="16" fill="none" stroke="{BLUE}" stroke-width="1.8" stroke-dasharray="2 1.5"/><path d="M11 11l7-6M14 5h4v4" fill="none" stroke="{ORANGE}" stroke-width="1.6"/>',
    "construction": f'<path d="M3 20L21 4" stroke="{ORANGE}" stroke-width="2.2" stroke-dasharray="4 2.5"/>',
    "export_as": f'<path d="M6 3h8l4 4v14H6z" fill="#fff" stroke="{INK}" stroke-width="1.5"/><path d="M9 14h6M12 11l3 3-3 3" fill="none" stroke="{BLUE}" stroke-width="1.8"/>',
    "revolve": f'<path d="M12 3v18" stroke="{RED}" stroke-width="1.2" stroke-dasharray="3 1.5"/><ellipse cx="12" cy="17" rx="8" ry="3" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M4 17V8a8 3 0 0116 0v9" fill="#f2c14e" stroke="{INK}" stroke-width="1.2"/><ellipse cx="12" cy="8" rx="8" ry="3" fill="#f7dc8e" stroke="{INK}" stroke-width="1.2"/>',
    "revolve_cut": f'<path d="M12 2v20" stroke="{RED}" stroke-width="1.2" stroke-dasharray="3 1.5"/><path d="M5 5h14v14H5z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M5 10h4v4H5zM15 10h4v4h-4z" fill="#fff" stroke="{RED}" stroke-width="1.5"/><path d="M9 12h6" stroke="{RED}" stroke-width="1" stroke-dasharray="2 1.5"/>',
    "assembly": f'<path d="M3 9l6-3 6 3-6 3z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.1"/><path d="M3 9v6l6 3v-6zM15 9v6l-6 3" fill="#aebdd3" stroke="{INK}" stroke-width="1.1"/><path d="M12 13l5-2.5 5 2.5-5 2.5z" fill="#f2c14e" stroke="{INK}" stroke-width="1.1"/><path d="M12 13v5l5 2.5v-5zM22 13v5l-5 2.5" fill="#d9a93a" stroke="{INK}" stroke-width="1.1"/>',
    "mate": f'<path d="M4 8h9v9H4z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.3"/><path d="M11 6h9v9h-9z" fill="#f2c14e" fill-opacity="0.85" stroke="{INK}" stroke-width="1.3"/><path d="M8 20l8-12" stroke="{GREEN}" stroke-width="1.8"/><circle cx="8" cy="20" r="1.8" fill="{GREEN}"/><circle cx="16" cy="8" r="1.8" fill="{GREEN}"/>',
    "command": f'<rect x="2" y="5" width="20" height="14" rx="2" fill="#1f2d3d"/><path d="M6 10l3 2-3 2M11 15h6" fill="none" stroke="#9fe870" stroke-width="1.6"/>',
    "solidworks": f'<path d="M3 9l6-3 6 3-6 3z" fill="#dbe6f5" stroke="{INK}" stroke-width="1.1"/><path d="M3 9v6l6 3v-6zM15 9v6l-6 3" fill="#c9d9ef" stroke="{INK}" stroke-width="1.1"/><path d="M14 4h7v7" fill="none" stroke="{BLUE}" stroke-width="1.8"/><path d="M21 4l-7 7" stroke="{BLUE}" stroke-width="1.8"/>',
    "measure": f'<path d="M3 16L16 3l5 5L8 21z" fill="#f2c14e" stroke="{INK}" stroke-width="1.3"/><path d="M7 12l2 2M9.5 9.5l1.5 1.5M12 7l2 2M14.5 4.5l1.5 1.5" stroke="{INK}" stroke-width="1.2"/>',
    "drawing": f'<rect x="3" y="4" width="18" height="16" fill="#fff" stroke="{INK}" stroke-width="1.4"/><rect x="13" y="15" width="8" height="5" fill="#dbe6f5" stroke="{INK}" stroke-width="1"/><rect x="6" y="7" width="5" height="4" fill="none" stroke="{BLUE}" stroke-width="1.4"/><rect x="6" y="13" width="5" height="3" fill="none" stroke="{BLUE}" stroke-width="1.4"/><rect x="13" y="7" width="3" height="4" fill="none" stroke="{BLUE}" stroke-width="1.4"/>',
    "std3": f'<rect x="3" y="3" width="9" height="8" fill="#f2c14e" stroke="{INK}" stroke-width="1.3"/><rect x="3" y="14" width="9" height="6" fill="#dbe6f5" stroke="{INK}" stroke-width="1.3"/><rect x="15" y="3" width="6" height="8" fill="#dbe6f5" stroke="{INK}" stroke-width="1.3"/><path d="M7.5 11v3M12 7h3" stroke="{INK}" stroke-width="0.8" stroke-dasharray="1.5 1"/>',
    "projected": f'<rect x="3" y="8" width="9" height="9" fill="#f2c14e" stroke="{INK}" stroke-width="1.3"/><rect x="15" y="8" width="6" height="9" fill="none" stroke="{BLUE}" stroke-width="1.5" stroke-dasharray="2 1.2"/><path d="M12 12.5h3M13.5 11l1.5 1.5-1.5 1.5" fill="none" stroke="{ORANGE}" stroke-width="1.5"/>',
    "auto_dim": f'<rect x="7" y="9" width="12" height="9" fill="#dbe6f5" stroke="{INK}" stroke-width="1.2"/><path d="M7 5h12M7 3.5v3M19 3.5v3M3 9v9M1.5 9h3M1.5 18h3" stroke="{BLUE}" stroke-width="1.3"/><circle cx="13" cy="13.5" r="2.2" fill="#fff" stroke="{INK}" stroke-width="1.1"/>',
    "note": f'<path d="M4 4h16v12H9l-5 4z" fill="#fff" stroke="{INK}" stroke-width="1.5"/><path d="M7.5 8h9M7.5 11h6" stroke="{BLUE}" stroke-width="1.6"/>',
    "hidden": f'<rect x="4" y="5" width="16" height="14" fill="#fff" stroke="{INK}" stroke-width="1.6"/><path d="M9 5v14M15 5v14" stroke="{BLUE}" stroke-width="1.5" stroke-dasharray="2.2 1.6"/>',
    "tangent": f'<path d="M4 18V11a7 7 0 017-7h9" fill="none" stroke="{INK}" stroke-width="1.8"/><path d="M4 11h16M11 4v14" stroke="#8a97a8" stroke-width="1.1"/>',
    "title": f'<rect x="3" y="4" width="18" height="16" fill="#fff" stroke="{INK}" stroke-width="1.3"/><path d="M9 13h12M9 13v7M15 13v7M9 16.5h12" stroke="{INK}" stroke-width="1.1"/><path d="M11 15h3" stroke="{BLUE}" stroke-width="1.2"/>',
    "pdf": f'<path d="M6 3h8l4 4v14H6z" fill="#fff" stroke="{INK}" stroke-width="1.5"/><rect x="3" y="11" width="15" height="7" rx="1" fill="{RED}"/><text x="10.5" y="16.6" font-family="Arial" font-size="5.6" font-weight="bold" fill="#fff" text-anchor="middle">PDF</text>',
    "dxf": f'<path d="M6 3h8l4 4v14H6z" fill="#fff" stroke="{INK}" stroke-width="1.5"/><rect x="3" y="11" width="15" height="7" rx="1" fill="{BLUE}"/><text x="10.5" y="16.6" font-family="Arial" font-size="5.6" font-weight="bold" fill="#fff" text-anchor="middle">DXF</text>',
    "dwg": f'<path d="M6 3h8l4 4v14H6z" fill="#fff" stroke="{INK}" stroke-width="1.5"/><rect x="3" y="11" width="15" height="7" rx="1" fill="{GREEN}"/><text x="10.5" y="16.6" font-family="Arial" font-size="5.2" font-weight="bold" fill="#fff" text-anchor="middle">DWG</text>',
})

_BOX = (f'<path d="M3 9l8-4 10 4-8 4z" fill="#dbe6f5" stroke="{INK}" stroke-width="1.1"/>'
        f'<path d="M3 9v8l10 4v-8zM21 9v8l-8 4" fill="#c9d9ef" stroke="{INK}" stroke-width="1.1"/>')
_ICONS.update({
    "fillet3d": f'<path d="M4 20V11a7 7 0 017-7h9v16z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M4 11a7 7 0 017-7" fill="none" stroke="{ORANGE}" stroke-width="2.6"/>',
    "chamfer3d": f'<path d="M4 20V10l6-6h10v16z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M4 10l6-6" stroke="{ORANGE}" stroke-width="2.6"/>',
    "shell": f'<path d="M3 8l9-4 9 4v10l-9 4-9-4z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M6 9.5l6-2.7 6 2.7-6 2.7z" fill="#fff" stroke="{INK}" stroke-width="1"/><path d="M6 9.5v6.5l6 2.7" fill="none" stroke="{INK}" stroke-width="0.9"/>',
    "draft": f'<path d="M6 20l3-15h6l3 15z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M3 20h18" stroke="{GREEN}" stroke-width="1.6"/><path d="M6 20V5" stroke="{RED}" stroke-width="1" stroke-dasharray="2 1.5"/>',
    "hole": f'{_BOX}<ellipse cx="12" cy="9" rx="3.4" ry="1.6" fill="#fff" stroke="{INK}" stroke-width="1.2"/><path d="M8.6 9v6M15.4 9v6" stroke="{RED}" stroke-width="1" stroke-dasharray="1.5 1"/><path d="M17 2l3 3-5 5-3-3z" fill="{ORANGE}" stroke="{INK}" stroke-width="0.8"/>',
    "lpattern3d": f'<path d="M2 16l3-1.5 3 1.5-3 1.5z" fill="#f2c14e" stroke="{INK}" stroke-width="0.9"/><path d="M9 12l3-1.5 3 1.5-3 1.5z" fill="#f2c14e" stroke="{INK}" stroke-width="0.9" stroke-dasharray="1.5 1"/><path d="M16 8l3-1.5 3 1.5-3 1.5z" fill="#f2c14e" stroke="{INK}" stroke-width="0.9" stroke-dasharray="1.5 1"/><path d="M3 21l18-9" stroke="{BLUE}" stroke-width="1.4"/><path d="M21 12l-3.6 0.3 1.6 2.5z" fill="{BLUE}"/>',
    "cpattern3d": f'<circle cx="12" cy="12" r="7.5" fill="none" stroke="{BLUE}" stroke-width="1" stroke-dasharray="2 1.5"/><circle cx="12" cy="12" r="1.5" fill="{INK}"/><rect x="10" y="2" width="4" height="4" fill="#f2c14e" stroke="{INK}" stroke-width="0.9"/><rect x="17" y="13" width="4" height="4" fill="#f2c14e" stroke="{INK}" stroke-width="0.9" stroke-dasharray="1.5 1"/><rect x="3" y="13" width="4" height="4" fill="#f2c14e" stroke="{INK}" stroke-width="0.9" stroke-dasharray="1.5 1"/>',
    "mirror3d": f'<path d="M12 2v20" stroke="{INK}" stroke-width="1.2" stroke-dasharray="3 1.5 1 1.5"/><path d="M3 9l5-3 3 2v10l-3 2-5-3z" fill="#f2c14e" stroke="{INK}" stroke-width="1.1"/><path d="M21 9l-5-3-3 2v10l3 2 5-3z" fill="#fbe7b3" stroke="{INK}" stroke-width="1.1" stroke-dasharray="2 1.2"/>',
    "sweep": f'<path d="M4 20c0-8 6-12 16-12" fill="none" stroke="{RED}" stroke-width="1.2" stroke-dasharray="3 1.5"/><path d="M2 20c0-9 7-14 18-14v5C11 11 7 14 7 20z" fill="#f2c14e" stroke="{INK}" stroke-width="1.2"/><ellipse cx="20" cy="8.5" rx="1.4" ry="2.5" fill="#f7dc8e" stroke="{INK}" stroke-width="1"/>',
    "sweep_cut": f'<rect x="3" y="4" width="18" height="16" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M5 18c0-6 5-10 14-10v4c-6 0-9 2-9 6z" fill="#fff" stroke="{RED}" stroke-width="1.4"/>',
    "loft": f'<path d="M4 18l8 3 8-3-8-3z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.1"/><ellipse cx="12" cy="6" rx="4" ry="1.8" fill="#f7dc8e" stroke="{INK}" stroke-width="1.1"/><path d="M4 18L8 6M20 18L16 6M12 21V7.8" fill="none" stroke="{INK}" stroke-width="1.1"/><path d="M8 6l-4 12 8 3 8-3-4-12" fill="#f2c14e" fill-opacity="0.6"/>',
    "loft_cut": f'<rect x="3" y="3" width="18" height="18" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M7 18h10l-2-10H9z" fill="#fff" stroke="{RED}" stroke-width="1.4"/>',
    "boundary": f'<path d="M3 19c3-2 6-2 9 0s6 2 9 0M3 7c3-2 6-2 9 0s6 2 9 0" fill="none" stroke="{INK}" stroke-width="1.2"/><path d="M3 7c3-2 6-2 9 0s6 2 9 0v12c-3 2-6 2-9 0s-6-2-9 0z" fill="#f2c14e" fill-opacity="0.7" stroke="{INK}" stroke-width="1.1"/><path d="M8 5.6v12M16 8.4v12" stroke="{BLUE}" stroke-width="1" stroke-dasharray="1.5 1"/>',
    "boundary_cut": f'<rect x="3" y="3" width="18" height="18" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M6 17c2-1.5 4-1.5 6 0s4 1.5 6 0V9c-2 1.5-4 1.5-6 0S8 7.5 6 9z" fill="#fff" stroke="{RED}" stroke-width="1.4"/>',
    "rib": f'<path d="M3 20h18v-3H6V3H3z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M6 8l11 9H6z" fill="#f2c14e" stroke="{INK}" stroke-width="1.1"/>',
    "ref_plane": f'<path d="M3 16l5-8h13l-5 8z" fill="#cfe0f7" stroke="{INK}" stroke-width="1.3"/><path d="M18 2v6M15 5h6" stroke="{GREEN}" stroke-width="1.8"/>',
    "ref_axis": f'<path d="M3 21L21 3" stroke="{INK}" stroke-width="1.6" stroke-dasharray="5 2 1.5 2"/><circle cx="12" cy="12" r="5" fill="none" stroke="{BLUE}" stroke-width="1.3"/>',
    "ref_point": f'<path d="M12 3v18M3 12h18" stroke="{INK}" stroke-width="1" stroke-dasharray="2 1.5"/><path d="M8 8l8 8M16 8l-8 8" stroke="{BLUE}" stroke-width="2"/>',
    "ref_geometry": f'<path d="M3 17l4-7h12l-4 7z" fill="#cfe0f7" stroke="{INK}" stroke-width="1.2"/><path d="M11 21V3" stroke="{INK}" stroke-width="1.3" stroke-dasharray="4 1.5 1 1.5"/><path d="M15 3l4 4M19 3l-4 4" stroke="{BLUE}" stroke-width="1.6"/>',
    "helix": f'<path d="M12 2v20" stroke="{INK}" stroke-width="0.9" stroke-dasharray="2 1.5"/><path d="M5 5c0-2 14-2 14 0s-14 2-14 4 14 2 14 4-14 2-14 4 14 2 14 4" fill="none" stroke="{BLUE}" stroke-width="1.8"/>',
    "curve3d": f'<path d="M3 18c3-10 7-12 10-6s6 4 8-6" fill="none" stroke="{BLUE}" stroke-width="2"/><circle cx="3" cy="18" r="1.6" fill="{INK}"/><circle cx="13" cy="12" r="1.6" fill="{INK}"/><circle cx="21" cy="6" r="1.6" fill="{INK}"/>',
    "curves": f'<path d="M12 2v20" stroke="{INK}" stroke-width="0.9" stroke-dasharray="2 1.5"/><path d="M5 6c0-2 14-2 14 0s-14 2-14 4 14 2 14 4" fill="none" stroke="{BLUE}" stroke-width="1.8"/><path d="M3 20c4-4 8 0 18-4" fill="none" stroke="{ORANGE}" stroke-width="1.6"/>',
    "wrap": f'<ellipse cx="12" cy="5" rx="7" ry="2.5" fill="#dbe6f5" stroke="{INK}" stroke-width="1.1"/><path d="M5 5v14a7 2.5 0 0014 0V5" fill="#c9d9ef" stroke="{INK}" stroke-width="1.1"/><path d="M8 10.5c2.5 1 5.5 1 8 0v4c-2.5 1-5.5 1-8 0z" fill="#f2c14e" stroke="{INK}" stroke-width="1"/>',
    "intersect": f'<rect x="3" y="9" width="12" height="12" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M9 3h12v12H9z" fill="#f2c14e" fill-opacity="0.6" stroke="{INK}" stroke-width="1.2"/><rect x="9" y="9" width="6" height="6" fill="{ORANGE}"/>',
    "instant3d": f'<path d="M4 16l6 3 10-5-6-3z" fill="#c9d9ef" stroke="{INK}" stroke-width="1.1"/><path d="M13 13V3" stroke="{BLUE}" stroke-width="2"/><path d="M13 2l-3 4h6z" fill="{BLUE}"/><circle cx="13" cy="13" r="1.8" fill="{ORANGE}"/>',
    "snap": f'<path d="M5 19l14-14" stroke="{BLUE}" stroke-width="2"/><rect x="2" y="16" width="6" height="6" fill="none" stroke="{ORANGE}" stroke-width="1.8"/><path d="M12 9l3 3" stroke="{INK}" stroke-width="1.2"/><path d="M16 2l4 4-2 2-4-4z" fill="{ORANGE}"/>',
    "convert": f'<rect x="4" y="4" width="12" height="12" fill="#c9d9ef" stroke="{INK}" stroke-width="1.2"/><path d="M8 20h12V8" fill="none" stroke="{BLUE}" stroke-width="2.2"/><path d="M14 14l5 5" stroke="{ORANGE}" stroke-width="1.6"/>',
    "text": f'<path d="M5 6V4h14v2M12 4v16M9 20h6" fill="none" stroke="{BLUE}" stroke-width="2.2"/>',
    "ellipse": f'<ellipse cx="12" cy="12" rx="9" ry="5.5" fill="none" stroke="{BLUE}" stroke-width="2.2"/><circle cx="12" cy="12" r="1.6" fill="{INK}"/>',
})


def svg(name: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">'
            f'{_ICONS[name]}</svg>')


class _SvgIconEngine(QIconEngine):
    """Render the vector artwork at the actual requested size and pixel density."""

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.renderer = QSvgRenderer(QByteArray(source.encode()))

    def clone(self):
        return _SvgIconEngine(self.source)

    def paint(self, painter, rect, mode, state):
        painter.save()
        if mode == QIcon.Disabled:
            painter.setOpacity(painter.opacity() * 0.45)
        painter.setRenderHint(QPainter.Antialiasing)
        self.renderer.render(painter, QRectF(rect))
        painter.restore()

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        # Qt < 6.8 passes a device-pixel size; newer Qt passes a logical size.
        if tuple(map(int, qVersion().split(".")[:2])) < (6, 8):
            size = QSize(round(size.width() / scale), round(size.height() / scale))
        pm = QPixmap(QSize(max(1, round(size.width() * scale)),
                           max(1, round(size.height() * scale))))
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        self.paint(painter, QRectF(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pm


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    if name not in _ICONS:
        return QIcon()
    return QIcon(_SvgIconEngine(svg(name)))
