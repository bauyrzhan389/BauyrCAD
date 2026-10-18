"""Rich command help with self-contained schematic illustrations."""
from __future__ import annotations

import base64
import html
from functools import lru_cache

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


DESCRIPTIONS = {
    "new": "Create a new document.", "open": "Open an existing BauyrCAD document.",
    "save": "Save the current document.", "export": "Export geometry to another file format.",
    "import": "Bring an existing part or geometry file into the document.",
    "undo": "Undo the most recent change.", "redo": "Restore the last undone change.",
    "delete": "Remove the selected items.", "sketch": "Draw a 2D profile on a plane or planar face.",
    "exit_sketch": "Accept the sketch and return to the model.",
    "extrude": "Add material by extending a closed sketch profile through a distance.",
    "cut": "Remove material by extending a sketch profile through the body.",
    "revolve": "Rotate a sketch profile about an axis to add material.",
    "revolve_cut": "Rotate a sketch profile about an axis to remove material.",
    "sweep": "Carry a profile along a path to create a solid.",
    "sweep_cut": "Carry a profile along a path to remove material.",
    "loft": "Blend between sketch profiles to create a changing cross-section.",
    "loft_cut": "Remove material by blending between sketch profiles.",
    "boundary": "Create a blended solid between profiles.",
    "boundary_cut": "Create a blended cut between profiles.",
    "shell": "Hollow the body with a specified wall thickness and optional open faces.",
    "fillet3d": "Round selected solid edges or the edges of selected faces.",
    "chamfer3d": "Bevel selected solid edges.",
    "hole": "Create drilled, tapped, counterbored or countersunk holes on a face.",
    "draft": "Taper selected faces relative to a neutral plane.",
    "rib": "Add a thin reinforcing wall from an open sketch.",
    "wrap": "Emboss or deboss a sketch onto a cylindrical face.",
    "intersect": "Split the body into regions and choose which regions to keep.",
    "lpattern3d": "Repeat features in a line with a chosen spacing and count.",
    "cpattern3d": "Repeat features around an axis.",
    "mirror3d": "Reflect features or a body across a plane.",
    "ref_plane": "Create a plane for sketches, measurements or other features.",
    "ref_axis": "Create a reference axis for rotation and alignment.",
    "ref_point": "Create a reference point at a specified location.",
    "helix": "Create a helical or spiral path for a spring or thread sweep.",
    "curve3d": "Create a 3D curve through a sequence of points.",
    "mate": "Constrain the relative position and permitted movement of components.",
    "fixed": "Hold selected geometry or ground an assembly component.",
    "material": "Choose the material and its physical properties.",
    "results": "Display stress, displacement and other analysis results.",
    "iso": "Show an isometric view of the model.", "front": "Look at the model from the front.",
    "top": "Look at the model from above.", "right": "Look at the model from the right.",
    "drawing": "Create a drawing sheet with views and dimensions.",
    "command": "Find and run commands by typing their names or aliases.",
    "line": "Draw connected straight segments between points.",
    "centerline": "Draw construction lines for axes and symmetry.",
    "midpoint_line": "Draw a line symmetrically about its midpoint.",
    "rectangle": "Draw a rectangle by picking opposite corners.",
    "rect_center": "Draw a rectangle from its center and a corner.",
    "rect_3pt": "Draw an angled rectangle using three points.",
    "rect_3pt_center": "Draw an angled rectangle about its center.",
    "parallelogram": "Draw a four-sided profile with parallel opposite sides.",
    "slot": "Draw a rounded slot using its endpoints and width.",
    "slot_center": "Draw a rounded slot symmetrically about its center.",
    "circle": "Draw a circle from its center and radius.",
    "circle_perimeter": "Draw a circle through three points.",
    "arc_center": "Draw an arc from its center, start and end points.",
    "arc_tangent": "Continue a curve with an arc tangent at the start point.",
    "arc_3pt": "Draw an arc through three points.",
    "polygon": "Draw a regular polygon about a center point.",
    "spline": "Draw a smooth curve through selected points.",
    "point": "Place a sketch point.", "text": "Add text outlines to the sketch.",
    "smart_dim": "Add a driving length, angle, radius or diameter dimension.",
    "relation": "Add geometric relations such as parallel, tangent or coincident.",
    "trim": "Remove the portion of a sketch curve between intersections.",
    "extend": "Lengthen a sketch curve to meet another curve.",
    "offset": "Create a parallel copy of sketch curves at a specified distance.",
    "mirror": "Reflect sketch geometry across a construction line.",
    "fillet_sk": "Replace a sketch corner with a tangent arc.",
    "chamfer_sk": "Replace a sketch corner with a straight bevel.",
    "lin_pattern": "Repeat sketch geometry with a spacing and count.",
    "circ_pattern": "Repeat sketch geometry around a center point.",
    "move": "Translate selected sketch geometry.", "copy": "Make a translated copy of selected geometry.",
    "rotate": "Turn selected geometry about a point.", "scale": "Resize selected geometry about a point.",
    "construction": "Toggle geometry used for reference rather than solid profiles.",
    "convert": "Copy model edges into the active sketch.", "select": "Select geometry to edit or constrain.",
}

# Each diagram shows the actual operation's input and result, rather than an enlarged icon.
_RECT = '<rect x="14" y="18" width="84" height="58" fill="#dbe8fa" stroke="#27466d"/>'
_BLOCK = '<path d="M10 28l28-16 64 0-28 16z" fill="#eef5ff"/><path d="M10 28h64v48H10z" fill="#c6dcfa"/><path d="M74 28l28-16v48L74 76z" fill="#83ace1"/><path d="M10 28l28-16h64v48L74 76H10zM10 28h64l28-16M74 28v48" fill="none" stroke="#27466d"/>'
_CYL = '<path d="M26 22v48a34 10 0 0068 0V22" fill="#f5d16a" stroke="#27466d"/><ellipse cx="60" cy="22" rx="34" ry="10" fill="#ffe9a3" stroke="#27466d"/>'
_PATTERN = _RECT + ''.join(f'<circle cx="{x}" cy="{y}" r="6" fill="white" stroke="#2f7de1"/>' for x in (32,56,80) for y in (35,59))
_DIAGRAMS = {
    "extrude": ("Closed profile", _RECT, "Solid + depth", _BLOCK),
    "cut": ("Profile on solid", _BLOCK + '<rect x="29" y="41" width="27" height="21" fill="#e97979"/>', "Removed material", _BLOCK + '<rect x="29" y="41" width="27" height="21" fill="white" stroke="#d9363e"/>'),
    "revolve": ("Profile + axis", '<path d="M30 12v67" stroke="#d9363e" stroke-dasharray="4 3"/><path d="M33 23h47v44H33z" fill="#dbe8fa" stroke="#27466d"/>', "Rotated solid", _CYL),
    "sweep": ("Profile + path", '<circle cx="20" cy="69" r="10" fill="#dbe8fa" stroke="#27466d"/><path d="M20 69C44 66 31 20 96 20" fill="none" stroke="#d9363e" stroke-dasharray="4 3"/>', "Swept solid", '<path d="M20 69C44 66 31 20 96 20" fill="none" stroke="#27466d" stroke-width="19"/><path d="M20 69C44 66 31 20 96 20" fill="none" stroke="#f5d16a" stroke-width="15"/>'),
    "loft": ("Different profiles", '<ellipse cx="60" cy="18" rx="22" ry="8" fill="none" stroke="#2f7de1"/><path d="M18 65h84v16H18z" fill="none" stroke="#2f7de1"/><path d="M38 18L18 65M82 18l20 47" stroke="#8797ab" stroke-dasharray="4 3"/>', "Blended solid", '<path d="M38 18L18 72q42 15 84 0L82 18z" fill="#f5d16a" stroke="#27466d"/><ellipse cx="60" cy="18" rx="22" ry="8" fill="#ffe9a3" stroke="#27466d"/>'),
    "shell": ("Solid body", _BLOCK, "Hollow + walls", _BLOCK + '<path d="M30 24l15-8h43l-15 8z" fill="white" stroke="#27466d"/><path d="M24 35h38v32H24z" fill="white" stroke="#27466d"/>'),
    "fillet3d": ("Sharp corner", _RECT, "Rounded corner", '<path d="M14 76V18h59q25 0 25 25v33z" fill="#dbe8fa" stroke="#27466d"/><path d="M73 18q25 0 25 25" fill="none" stroke="#f08a24" stroke-width="5"/>'),
    "chamfer3d": ("Sharp corner", _RECT, "Beveled corner", '<path d="M14 76V18h61l23 23v35z" fill="#dbe8fa" stroke="#27466d"/><path d="M75 18l23 23" stroke="#f08a24" stroke-width="5"/>'),
    "hole": ("Hole position", _BLOCK + '<path d="M43 40v22M32 51h22" stroke="#d9363e"/>', "Hole in body", _BLOCK + '<ellipse cx="43" cy="50" rx="15" ry="9" fill="white" stroke="#27466d"/><ellipse cx="43" cy="52" rx="8" ry="5" fill="#8797ab" stroke="#27466d"/>'),
    "draft": ("Straight walls", _RECT, "Tapered walls", '<path d="M14 76l15-58h54l15 58z" fill="#dbe8fa" stroke="#27466d"/><path d="M14 76h84" stroke="#2f9e57" stroke-width="4"/>'),
    "rib": ("Open sketch", '<path d="M18 15v60h82" fill="none" stroke="#8797ab" stroke-width="5"/><path d="M23 22l63 48" stroke="#2f7de1" stroke-dasharray="4 3"/>', "Reinforcing rib", '<path d="M18 15v60h82" fill="none" stroke="#8797ab" stroke-width="5"/><path d="M23 22v48h63z" fill="#f5d16a" stroke="#27466d"/>'),
    "lpattern3d": ("One feature", _RECT + '<circle cx="32" cy="35" r="6" fill="white" stroke="#2f7de1"/>', "Repeated features", _PATTERN),
    "cpattern3d": ("One feature", '<circle cx="60" cy="46" r="34" fill="#dbe8fa" stroke="#27466d"/><circle cx="60" cy="23" r="6" fill="white" stroke="#2f7de1"/>', "Around an axis", '<circle cx="60" cy="46" r="34" fill="#dbe8fa" stroke="#27466d"/>' + ''.join(f'<circle cx="{x}" cy="{y}" r="6" fill="white" stroke="#2f7de1"/>' for x,y in ((60,23),(83,46),(60,69),(37,46))) + '<circle cx="60" cy="46" r="2" fill="#d9363e"/>'),
    "mirror3d": ("Feature + plane", '<path d="M60 8v75" stroke="#8797ab" stroke-dasharray="4 3"/><path d="M25 72l24-44v44z" fill="#dbe8fa" stroke="#27466d"/>', "Mirrored feature", '<path d="M60 8v75" stroke="#8797ab" stroke-dasharray="4 3"/><path d="M25 72l24-44v44z" fill="#dbe8fa" stroke="#27466d"/><path d="M95 72L71 28v44z" fill="#f5d16a" stroke="#27466d"/>'),
    "wrap": ("Flat sketch", '<rect x="20" y="15" width="80" height="60" fill="none" stroke="#2f7de1"/><text x="60" y="54" font-size="23" text-anchor="middle" fill="#27466d" stroke="none">ABC</text>', "On a cylinder", _CYL + '<text x="60" y="54" font-size="18" text-anchor="middle" fill="#27466d" stroke="none">ABC</text>'),
    "intersect": ("Crossing regions", '<rect x="15" y="30" width="56" height="47" fill="#dbe8fa" stroke="#27466d"/><rect x="48" y="12" width="56" height="47" fill="#f5d16a" fill-opacity="0.65" stroke="#27466d"/>', "Choose regions", '<rect x="15" y="30" width="56" height="47" fill="none" stroke="#8797ab"/><rect x="48" y="12" width="56" height="47" fill="none" stroke="#8797ab"/><rect x="48" y="30" width="23" height="29" fill="#2f7de1" stroke="#27466d"/>'),
    "mate": ("Separate components", '<rect x="10" y="13" width="22" height="25" fill="#dbe8fa" stroke="#27466d"/><rect x="67" y="53" width="38" height="21" fill="#f5d16a" stroke="#27466d"/><path d="M8 26h97M8 64h97" stroke="#8797ab" stroke-dasharray="4 3"/>', "Aligned by mates", '<rect x="17" y="31" width="22" height="25" fill="#dbe8fa" stroke="#27466d"/><rect x="48" y="33" width="49" height="21" fill="#f5d16a" stroke="#27466d"/><path d="M8 44h101" stroke="#2f9e57" stroke-dasharray="4 3"/>'),
    "helix": ("Axis + diameter", '<ellipse cx="60" cy="70" rx="33" ry="10" fill="none" stroke="#2f7de1"/><path d="M60 10v72" stroke="#8797ab" stroke-dasharray="4 3"/>', "Helical path", '<path d="M60 5v83" stroke="#8797ab" stroke-dasharray="4 3"/><path d="M28 16c0-7 64-7 64 0s-64 7-64 17 64 7 64 17-64 7-64 17 64 7 64 17" fill="none" stroke="#2f7de1" stroke-width="4"/>'),
    "ref_plane": ("Reference plane", '<path d="M20 70l25-28h62L82 70z" fill="#dbe8fa" stroke="#27466d"/>', "Offset plane", '<path d="M20 75l25-28h62L82 75z" fill="#dbe8fa" stroke="#27466d"/><path d="M20 40l25-28h62L82 40z" fill="#f5d16a" stroke="#27466d"/><path d="M25 43v29" stroke="#d9363e" stroke-dasharray="4 3"/>'),
}
_ALIASES = {"revolve_cut":"revolve", "sweep_cut":"sweep", "loft_cut":"loft",
            "boundary":"loft", "boundary_cut":"loft", "fillet_sk":"fillet3d",
            "chamfer_sk":"chamfer3d", "lin_pattern":"lpattern3d",
            "circ_pattern":"cpattern3d", "mirror":"mirror3d"}


@lru_cache(maxsize=None)
def illustration(name: str) -> str:
    """Inline PNG URI. Qt rich-text tooltips load it without external asset files."""
    key = _ALIASES.get(name, name)
    diagram = _DIAGRAMS.get(key)
    if diagram is None:
        return ""
    label_a, before, label_b, after = diagram
    if name in ("revolve_cut", "sweep_cut", "loft_cut", "boundary_cut"):
        label_b = "Cut volume"
    source = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 310 150">
      <rect width="310" height="150" rx="7" fill="#f6f8fc"/>
      <g stroke-width="2" stroke-linejoin="round" font-family="Segoe UI, Arial">
      <g transform="translate(8 20)">{before}</g>
      <path d="M139 65h28m-8-7 8 7-8 7" fill="none" stroke="#2f7de1" stroke-width="3"/>
      <g transform="translate(181 20)">{after}</g></g>
      <g fill="#27466d" font-family="Segoe UI, Arial" font-size="12" text-anchor="middle">
      <text x="66" y="134">{html.escape(label_a)}</text>
      <text x="241" y="134">{html.escape(label_b)}</text></g></svg>'''
    renderer = QSvgRenderer(QByteArray(source.encode()))
    pm = QPixmap(620, 300)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, 620, 300))
    painter.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode()


def description(text: str, tip: str | None, icon_name: str) -> str:
    name = text.replace("&", "").replace("\n", " ").strip().rstrip(".")
    # Older sketch actions supplied their name as the entire tooltip.
    if tip and tip.replace("\n", " ").strip().rstrip(".") != name:
        return tip
    return DESCRIPTIONS.get(icon_name) or f"Run the {name} command."


@lru_cache(maxsize=512)
def tooltip(text: str, tip: str | None = None, icon_name: str = "") -> str:
    name = text.replace("&&", "&").replace("\n", " ").strip()
    desc = description(text, tip, icon_name)
    image = illustration(icon_name)
    content = f'<b>{html.escape(name)}</b><p>{html.escape(desc)}</p>'
    if image:
        content += f'<img src="{image}" width="310" height="150">'
    return f'<div style="width:310px">{content}</div>'
