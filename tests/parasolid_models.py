"""BauyrCAD parts for the Parasolid (.x_t) export tests, with the topology Parasolid gives
them: (vertices, edges, faces, loops). Where SolidWorks wrote its own .x_t of the same part
(shaft, frustum, cone_tip and every B-spline part), our counts equal its counts."""
from __future__ import annotations

from functools import lru_cache

from bauyrcad.api import Assembly, Part


def _box():
    p = Part("box"); s = p.sketch("front"); s.rect((0, 0), (20, 30)); p.extrude(s, 40)
    return p


def _cylinder():
    p = Part("cylinder"); s = p.sketch("front"); s.circle((0, 0), d=20); p.extrude(s, 30)
    return p


def _frustum():
    p = Part("frustum"); s = p.sketch("front"); s.centerline((0, 0), (0, 10))
    s.polyline([(0, 0), (15, 0), (5, 25), (0, 25)], close=True); p.revolve(s)
    return p


def _cone_tip():
    p = Part("cone-tip"); s = p.sketch("front"); s.centerline((0, 0), (0, 10))
    s.polyline([(0, 0), (12, 0), (0, 30)], close=True); p.revolve(s)
    return p


def _sphere():
    p = Part("sphere"); s = p.sketch("front"); s.centerline((0, -5), (0, 5))
    s.arc((0, 0), (0, -15), (0, 15)); s.line((0, 15), (0, -15)); p.revolve(s)
    return p


def _torus():
    p = Part("torus"); s = p.sketch("front"); s.centerline((0, 0), (0, 10))
    s.circle((25, 0), d=10); p.revolve(s)
    return p


def _shaft():                                   # tests/data/sw/shaft.SLDPRT's model
    q = Part("shaft"); s = q.sketch("front"); s.centerline((0, 0), (0, 10))
    s.polyline([(0, 0), (12, 0), (12, 50), (0, 50)], close=True); q.revolve(s)
    s2 = q.sketch("front"); s2.centerline((0, 0), (0, 10)); s2.rect((10, 20), (13, 26))
    q.revolve_cut(s2, 270)
    return q


def _plate():
    p = Part("plate"); s = p.sketch("front"); s.rect((0, 0), (80, 50)); s.circle((40, 25), d=20)
    p.extrude(s, 10)
    s2 = p.sketch(on=p.face("+z")); s2.rect((5, 5), (20, 15)); p.cut(s2, 4)
    return p


def _block():
    r = Part("block"); s = r.sketch("top"); s.rect((-20, -15), (20, 15))
    r.extrude(s, 30, midplane=True)
    s2 = r.sketch("right"); s2.circle((0, 0), d=8); r.cut(s2, through=True, both=True)
    return r


def _plain(name, h=20):
    p = Part(name); s = p.sketch("front"); s.rect((0, 0), (60, 40)); p.extrude(s, h)
    return p


def _fillet_top():
    p = _plain("fillet-top"); p.fillet(faces=[p.face("+z")], r=4)
    return p


def _fillet_both():
    p = _plain("fillet-both"); p.fillet(faces=[p.face("+z"), p.face("-z")], r=3)
    return p


def _fillet_corner():
    p = _plain("fillet-corner")
    p.fillet([e["index"] for e in p.edges_where(direction="z")], r=6)
    p.fillet(faces=[p.face("+z")], r=3)
    return p


def _chamfer():
    p = _plain("chamfer"); p.chamfer(faces=[p.face("+z")], d=3)
    return p


def _shell():
    p = _plain("shell", 30); p.shell(2, remove=[p.face("+z")])
    return p


def _holes():
    p = Part("holes"); s = p.sketch("front"); s.rect((0, 0), (80, 50)); p.extrude(s, 12)
    p.hole(p.face("+z"), [(15, 15)], size="M6", kind="counterbore")
    p.hole(p.face("+z"), [(40, 25)], size="M8", kind="countersink")
    p.hole(p.face("+z"), [(65, 35)], size="M6", kind="tapped", depth=8)
    return p


def _pattern():
    p = Part("pattern"); s = p.sketch("front"); s.rect((0, 0), (100, 40)); p.extrude(s, 8)
    s2 = p.sketch(on=p.face("+z")); s2.circle((10, 20), d=6); c = p.cut(s2, through=True)
    p.pattern_linear([c], "x", 20, 5)
    return p


def _incline():                                 # ellipse edges; exact volume 6750 pi
    p = Part("incline"); s = p.sketch("top"); s.circle((0, 0), d=30); p.extrude(s, 40)
    s2 = p.sketch("front"); s2.polyline([(-20, 22), (20, 38), (20, 60), (-20, 60)], close=True)
    p.cut(s2, through=True, both=True)
    return p


def _slot():
    p = Part("slot"); s = p.sketch("front"); s.rect((0, 0), (60, 30)); p.extrude(s, 10)
    s2 = p.sketch(on=p.face("+z")); s2.slot((15, 15), (45, 15), 8); p.cut(s2, 5)
    return p


def _revolve120():
    p = Part("revolve-120"); s = p.sketch("front"); s.centerline((0, 0), (0, 10))
    s.polyline([(5, 0), (20, 0), (20, 10), (5, 10)], close=True); p.revolve(s, 120)
    return p


def _spline_profile(s):
    s.spline([(0, 0), (10, 8), (25, 4), (40, 10)])
    s.line((40, 10), (40, -10)); s.line((40, -10), (0, -10)); s.line((0, -10), (0, 0))


def _spline_extrude():                          # extrusion of a B-spline -> B-surface
    p = Part("spline-extrude"); s = p.sketch("front"); _spline_profile(s); p.extrude(s, 15)
    return p


def _spline_revolve():                          # revolution of a B-spline, periodic
    p = Part("spline-revolve"); s = p.sketch("front"); s.centerline((0, 0), (0, 10))
    s.spline([(10, 0), (16, 10), (12, 25), (18, 40)])
    s.line((18, 40), (0, 40)); s.line((0, 40), (0, 0)); s.line((0, 0), (10, 0))
    p.revolve(s)
    return p


def _loft():
    p = Part("loft"); a = p.sketch("front"); a.rect((-15, -10), (15, 10))
    b = p.sketch("front", offset=30); b.circle((0, 0), d=16); p.loft([a, b])
    return p


def _sweep_open():                              # its surface needs the knot split
    p = Part("sweep"); p.sweep(p.curve([(0, 0, 0), (20, 10, 5), (40, 0, 10), (60, -10, 0)]), d=6)
    return p


def _sweep_closed():                            # periodic both ways: no edges at all
    p = Part("ring")
    p.sweep(p.curve([(20, 0, 0), (0, 20, 3), (-20, 0, 0), (0, -20, -3)], closed=True), d=2)
    return p


def _spring():
    p = Part("spring"); p.sweep(p.helix(center=(0, 0, 0), axis="y", d=20, pitch=5, turns=3), d=2)
    return p


def _text():                                    # Bezier outlines
    p = Part("text"); s = p.sketch("front"); s.rect((0, 0), (60, 25)); p.extrude(s, 5)
    t = p.sketch(on=p.face("+z")); t.text("BC", at=(10, 6), height=12); p.cut(t, 1)
    return p


def _cavity():                                  # a closed void inside: two shells
    p = Part("cavity"); s = p.sketch("front"); s.rect((0, 0), (40, 40)); p.extrude(s, 30)
    s2 = p.sketch("front", offset=10); s2.rect((10, 10), (30, 30)); p.cut(s2, 8)
    return p


def two_bodies():                               # two separate solids in one part
    p = Part("two-bodies"); s = p.sketch("front"); s.rect((0, 0), (20, 20)); p.extrude(s, 10)
    s2 = p.sketch("front"); s2.rect((40, 0), (60, 30)); p.extrude(s2, 15)
    return p


def spline_shell():                             # an offset surface: edges up to 7e-5 mm off
    p = Part("spline-shell"); s = p.sketch("front"); _spline_profile(s); p.extrude(s, 15)
    p.shell(1, remove=[p.face("+z")])
    return p


# name: (builder, (vertices, edges, faces, loops))
MODELS = {
    "box": (_box, (8, 12, 6, 6)),
    "cylinder": (_cylinder, (0, 2, 3, 4)),
    "frustum": (_frustum, (0, 2, 3, 4)),
    "cone_tip": (_cone_tip, (1, 1, 2, 3)),          # the apex is an isolated vertex loop
    "sphere": (_sphere, (0, 0, 1, 0)),
    "torus": (_torus, (0, 0, 1, 0)),
    "shaft": (_shaft, (8, 14, 8, 10)),
    "plate": (_plate, (16, 26, 12, 16)),
    "block": (_block, (8, 14, 7, 10)),
    "fillet_top": (_fillet_top, (12, 20, 10, 10)),
    "fillet_both": (_fillet_both, (16, 28, 14, 14)),
    "fillet_corner": (_fillet_corner, (24, 40, 18, 18)),
    "chamfer": (_chamfer, (12, 20, 10, 10)),
    "shell": (_shell, (16, 24, 11, 12)),
    "holes": (_holes, (9, 21, 13, 25)),
    "pattern": (_pattern, (8, 22, 11, 26)),
    "incline": (_incline, (0, 2, 3, 4)),
    "slot": (_slot, (16, 24, 11, 12)),
    "revolve120": (_revolve120, (8, 12, 6, 6)),
    "spline_extrude": (_spline_extrude, (8, 12, 6, 6)),
    "spline_revolve": (_spline_revolve, (0, 2, 3, 4)),
    "loft": (_loft, (10, 15, 7, 7)),
    "sweep_open": (_sweep_open, (0, 2, 3, 4)),
    "sweep_closed": (_sweep_closed, (0, 0, 1, 0)),
    "spring": (_spring, (0, 2, 3, 4)),
    "text": (_text, (118, 177, 65, 69)),
    "cavity": (_cavity, (16, 24, 12, 12)),
    "spline_shell": (spline_shell, (16, 24, 11, 12)),   # one tolerant edge
}
# SolidWorks closes the shell's 7e-5 mm crack its own way: its volume of our file and of the
# STEP differ by 9e-5 (a fine mesh of BauyrCAD's solid agrees with OpenCASCADE's volume)
SW_VOLUME_REL = {"spline_shell": (2e-4, 3e-4)}          # (against STEP, against BauyrCAD)
# B-spline parts: OpenCASCADE's own volume of some of them is ~2e-3 off (the closed ring)
BSPLINE = {"spline_extrude", "spline_revolve", "loft", "sweep_open", "sweep_closed", "spring",
           "text"}


# a plate and one peg placed twice (the second turned 30 deg about z), coloured 0-255
PEGBOARD_COLOURS = {"Plate": (50, 100, 200), "Peg A": (230, 25, 25), "Peg B": (25, 180, 50)}


def pegboard(folder) -> Assembly:
    plate = Part("plate"); s = plate.sketch("front"); s.rect((0, 0), (60, 40)); plate.extrude(s, 8)
    plate.save(f"{folder}/plate.bcad")
    peg = Part("peg"); s = peg.sketch("front"); s.rect((0, 0), (6, 10)); peg.extrude(s, 25)
    peg.save(f"{folder}/peg.bcad")
    asm = Assembly("pegboard")
    comps = [asm.add(f"{folder}/plate.bcad", name="Plate"),
             asm.add(f"{folder}/peg.bcad", at=(10, 10, 8), name="Peg A"),
             asm.add(f"{folder}/peg.bcad", at=(40, 20, 8), rotate=("z", 30), name="Peg B")]
    for c in comps:
        c.comp.color = PEGBOARD_COLOURS[c.comp.name]
    return asm


@lru_cache(maxsize=None)
def build(name) -> Part:
    return MODELS[name][0]()
