"""Native SolidWorks versions of BauyrCAD's features (used by solidworks.py).

Each handler gets the regeneration context just before the feature (so our edge and face
references resolve on the same body SolidWorks has at that point), selects the matching
SolidWorks edges / faces / planes / features by geometry, and creates the SolidWorks feature.
`attempt` selects alternatives (attempt 0 = the predicted definition, then e.g. the flipped
direction); solidworks._native_feature keeps the first attempt whose change of volume AND of
first moment (where the material went) matches BauyrCAD's and deletes the others. A handler
raises _NotNative when the feature can't be rebuilt.

Also here, shared by every family:

* PRECHECKS - per feature TYPE, the known reasons a feature can't be exported natively,
  checked without SolidWorks (solidworks.native_export_issues runs them before SolidWorks
  starts; each handler runs the same predicate first, so the two never disagree). Register
  more with ``@precheck("type")``.
* Helper reference geometry (product decision D1): when SolidWorks needs an entity BauyrCAD
  does not store as a feature (an axis along X, a plane for an explicit sketch plane...), a
  hidden helper named '<BauyrCAD feature> Axis|Plane|Points' is made and listed in
  report.helpers. Helpers are made inside keep_selection(), so they never clear a selection
  a handler is building.
* set_name: names are read back, and a name held by a feature SolidWorks named itself is
  moved out of the way; a refused name is a blocking report warning.
"""

from __future__ import annotations

import math

import numpy as np

MM = 1e-3
NO_MORE = object()          # no further attempt to try

# selection marks (SolidWorks API documentation of each method)
_PATTERN_DIR1, _PATTERN_DIR2, _PATTERN_SEED = 1, 2, 4
_MIRROR_PLANE, _MIRROR_SEED = 2, 1
_REVOLVE_AXIS = 4
_END_BLIND, _END_THROUGH, _END_MID = 0, 1, 6        # swEndConditions_e
_HIDDEN = 1                                         # swVisibilityStateHide
_DOC_PART, _DOC_ASSEMBLY = 1, 2                     # swDocumentTypes_e
# swSelectType_e whose selected object is an IFeature (planes, axes, points, sketches...)
_FEATURE_SELECTIONS = {4, 5, 6, 9, 22, 23, 26}
_BODY_SELECTIONS = {76, 81}
_SKETCH_SELECTIONS = {10, 11, 24, 25}
_SKETCH_TYPES = ("ProfileFeature", "3DProfileFeature")


class State:
    """What the export has built so far: our feature id -> SolidWorks feature, sketch maps,
    reference planes / axes / points (for later selections) and helper features."""

    def __init__(self, report=None):
        self.report = report
        self.features: dict = {}        # our feature id -> IFeature (or list of them)
        self.planes: list = []          # (SketchPlane, IFeature): planes later sketches can use
        self.axes: dict = {}            # key -> IFeature (reference axes made for features)
        self.points: dict = {}          # key -> (IFeature, [ISketchPoint]) helper points
        self.sketches: dict = {}        # sketch id -> (IFeature, _SketchMap, SketchFeature)
        self.helpers: list = report.helpers if report is not None else []
        self.owned: set = set()         # GetID of SolidWorks features that are steps / helpers

    def own(self, s, feat):
        self.owned.add(s.T(feat, "IFeature").GetID())

    def warn(self, msg):
        if self.report is not None:
            self.report.warnings.append(msg)


def _not_native(msg):
    from .solidworks import _NotNative
    return _NotNative(msg)


# ============================================================================================
# strict pre-checks (no SolidWorks needed)
# ============================================================================================
PRECHECKS: dict = {}        # TYPE -> [fn(f, ctx) -> [issue, ...]]


def precheck(*types):
    """Register a pre-check for feature TYPEs. fn(f, ctx) returns issue strings; ctx() gives
    the regeneration context just before f (computed only when a check needs geometry)."""
    def deco(fn):
        for t in types:
            PRECHECKS.setdefault(t, []).append(fn)
        return fn
    return deco


def feature_issues(f, ctx) -> list:
    """Known reasons feature f can't be exported natively (without starting SolidWorks)."""
    return [msg for fn in PRECHECKS.get(f.TYPE, ()) for msg in fn(f, ctx)]


def require(f, ctx=None):
    """Handlers run the pre-check predicate first, so the pre-check and the build agree."""
    issues = feature_issues(f, lambda: ctx)
    if issues:
        raise _not_native(f"{f.name}: {issues[0]}")


def plane_spec_issues(spec) -> list:
    t = (spec or {}).get("type")
    return [] if t in ("std", "feature", "face", "explicit") else [f"a plane given as {t} can't be selected"]


def axis_spec_issues(spec, ctx, circular=False) -> list:
    """Axis / direction specs select_axis can't select in SolidWorks."""
    from .core.refs import resolver
    t = (spec or {}).get("type")
    if t in ("std", "explicit"):
        return []
    if t == "edge":
        try:
            r = resolver(ctx())
            info = r.edges[r.edge(spec["ref"])]
        except Exception:  # noqa: BLE001 - a broken reference is the feature's own error
            return []
        return [] if info["kind"] == "line" else ["an axis on a circular edge is not transferred yet"]
    if t == "face":
        if not circular:
            return []
        try:
            _, face = our_face_point(ctx(), spec["ref"])
        except Exception:  # noqa: BLE001
            return []
        return ([] if face.kind in ("cylinder", "cone") else
                ["a planar face as a circular-pattern axis has no SolidWorks equivalent"])
    if t == "feature":
        return ["a reference-axis feature as the axis is not transferred yet"]
    return [f"an axis given as {t} is not transferred yet"]


@precheck("sketch")
def _sketch_issues(f, ctx):
    out = []
    for t in f.sketch.texts:
        if "<" in t["text"]:
            out.append(f"sketch text '{t['text']}': SolidWorks reads '<' as the start of a "
                       "format code")
        if len(t["text"].splitlines()) > 1:
            out.append(f"sketch text '{t['text']}': multi-line text is not transferred yet")
    if f.sketch.texts and f.sketch.curves(include_construction=False):
        # letters inside a closed profile would be holes in it; SolidWorks did not extrude
        # such a sketch when tried through the API (and its session failed afterwards)
        out.append("sketch text together with other (non-construction) curves in one sketch "
                   "is not transferred yet: put the text in a sketch of its own")
    return out + sketch_constraint_issues(f.sketch)


def sketch_constraint_issues(sk) -> list:
    """Relations / dimensions SolidWorks can't hold as BauyrCAD does (no SolidWorks needed)."""
    from .core.sketch import Line
    from .core.sketch.constraints import (RELATION_NAMES, TYPES, format_value, mixed_equal,
                                          residual)
    out = []
    used = {p for e in sk.entities.values() for p in e.point_ids()} | {0}
    tol = 1e-5 * max(sk.scale(), 1.0)
    for c in sk.constraints:
        if c.type not in TYPES or c.type == "arc_internal":
            continue
        name = RELATION_NAMES.get(c.type) or f"the {c.type} dimension {format_value(c)}"
        if mixed_equal(sk, c):
            out.append("an Equal between a line and an arc / circle (length = radius) has no "
                       "SolidWorks equivalent (SolidWorks' Equal relates lines, or arcs and "
                       "circles): replace it with dimensions")
            continue
        if any(k == "p" and i not in used for k, i in c.refs):
            out.append(f"{name} refers to a point no sketch entity uses (SolidWorks can't "
                       "select it)")
            continue
        if c.type == "angle":
            l1, l2 = (sk.entities.get(i) for _, i in c.refs)
            if isinstance(l1, Line) and isinstance(l2, Line):
                d1, d2 = sk.pt(l1.p2) - sk.pt(l1.p1), sk.pt(l2.p2) - sk.pt(l2.p1)
                if abs(d1[0] * d2[1] - d1[1] * d2[0]) <= 1e-9 * np.linalg.norm(d1) * np.linalg.norm(d2):
                    out.append(f"{name}: an angle between parallel lines can't be dimensioned "
                               "in SolidWorks")
                    continue
        if c.driving:
            try:
                r = residual(sk, c)
            except Exception:  # noqa: BLE001 - a broken relation is the sketch's own error
                continue
            if r and max(abs(float(x)) for x in r) > tol:
                out.append(f"{name} does not hold in BauyrCAD's sketch (solve the sketch before "
                           "exporting: SolidWorks would move the geometry)")
    return out


@precheck("extrude")
def _extrude_issues(f, ctx):
    if f.operation != "cut" and f.end in ("through_all", "through_all_both"):
        return [f"a boss with the end condition {f.end} has no SolidWorks equivalent "
                "(BauyrCAD extrudes a computed length)"]
    return []


@precheck("revolve")
def _revolve_issues(f, ctx):
    if f.axis_spec is not None:
        return ["a revolve about a model edge, reference axis or X/Y/Z axis is not transferred "
                "yet (use a line in a sketch as the axis)"]
    return []


@precheck("import_step")
def _import_issues(f, ctx):
    from .core import geometry
    try:
        n = geometry.count_solids(f.shape())
    except Exception:  # noqa: BLE001 - an unreadable import is the feature's own error
        return []
    return [] if n == 1 else [f"an import with {n} solid bodies is not transferred yet"]


@precheck("fillet")
def _fillet_issues(f, ctx):
    return ["variable-radius fillets are not transferred yet"] if f.radius2 is not None else []


@precheck("ref_plane")
def _ref_plane_issues(f, ctx):
    out = []
    if f.mode not in ("offset", "mid") or (f.mode == "mid" and f.base2 is None):
        out.append(f"a {f.mode} reference plane is not transferred yet")
    out += plane_spec_issues(f.base)
    if f.mode == "mid" and f.base2 is not None:
        out += plane_spec_issues(f.base2)
    return out


@precheck("draft")
def _draft_issues(f, ctx):
    return plane_spec_issues(f.neutral)


@precheck("mirror")
def _mirror_issues(f, ctx):
    return plane_spec_issues(f.plane)


@precheck("linear_pattern", "circular_pattern")
def _pattern_issues(f, ctx):
    out = []
    if f.bodies or not f.seeds:
        out.append("patterns of the whole body are not transferred yet")
    if f.skip:
        out.append("skipped instances are not transferred yet")
    if f.TYPE == "linear_pattern":
        out += axis_spec_issues(f.direction, ctx)
        if f.direction2 is not None and f.count2 > 1:
            out += axis_spec_issues(f.direction2, ctx)
    else:
        out += axis_spec_issues(f.axis, ctx, circular=True)
    return out


@precheck("helix")
def _helix_issues(f, ctx):
    return ["tapered helices are not transferred yet"] if f.taper else []


@precheck("sweep")
def _sweep_issues(f, ctx):
    out = []
    if f.regions:
        out.append("sweeps with selected contours are not transferred yet")
    if f.inner_diameter:
        out.append("tube sweeps are not transferred yet")
    return out


@precheck("loft")
def _loft_issues(f, ctx):
    out = []
    if any(not isinstance(sec, str) for sec in f.sections):
        out.append("lofts to an explicit point are not transferred yet")
    if f.ruled:
        out.append("ruled lofts are not transferred yet")
    return out


# ============================================================================================
# names, visibility, selections
# ============================================================================================
def feature_by_name(s, doc, name):
    t = doc.GetType()
    try:
        if t == _DOC_PART:
            f = s.T(doc, "IPartDoc").FeatureByName(name)
        elif t == _DOC_ASSEMBLY:
            f = s.T(doc, "IAssemblyDoc").FeatureByName(name)
        else:
            return None
    except Exception:  # noqa: BLE001
        return None
    return s.T(f, "IFeature")


def free_name(s, doc, base):
    name, k = base, 2
    while feature_by_name(s, doc, name) is not None:
        name, k = f"{base} {k}", k + 1
    return name


def _try_name(feat, name) -> bool:
    try:
        if feat.Name != name:
            feat.Name = name
    except Exception:  # noqa: BLE001 - SolidWorks refuses clashing / reserved names
        pass
    try:
        return feat.Name == name
    except Exception:  # noqa: BLE001
        return False


def set_name(s, doc, feat, name, st=None) -> bool:
    """Name a SolidWorks feature and read the name back. When a feature SolidWorks named
    itself (not one of our steps or helpers) holds the name, it is renamed out of the way."""
    feat = s.T(feat, "IFeature")
    if _try_name(feat, name):
        return True
    other = feature_by_name(s, doc, name)
    if other is not None and other.GetID() != feat.GetID() and (st is None or other.GetID()
                                                                not in st.owned):
        _try_name(other, free_name(s, doc, f"{name} (SolidWorks)"))
        return _try_name(feat, name)
    return False


def name_children(s, doc, feat, step, st):
    """Sub-features SolidWorks made itself inside a step (Hole Wizard's sketches) are named
    after the step, so their automatic names ('Sketch7') never hold a later step's name."""
    sub = s.T(s.T(feat, "IFeature").GetFirstSubFeature(), "IFeature")
    while sub is not None:
        nxt = s.T(sub.GetNextSubFeature(), "IFeature")
        if sub.GetID() not in st.owned:
            kind = "Sketch" if sub.GetTypeName2() in _SKETCH_TYPES else sub.GetTypeName2()
            set_name(s, doc, sub, free_name(s, doc, f"{step} {kind}"), st)
            st.own(s, sub)
        sub = nxt


def hide(s, doc, feat) -> bool:
    """Hide a sketch or reference feature (like right-click > Hide)."""
    feat = s.T(feat, "IFeature")
    doc.ClearSelection2(True)
    feat.Select2(False, 0)
    if feat.GetTypeName2() in _SKETCH_TYPES:
        doc.BlankSketch()
    else:
        doc.BlankRefGeom()
    doc.ClearSelection2(True)
    return feat.Visible == _HIDDEN


def newest_feature(s, doc, before_id):
    """The last feature in the tree, or None if it is still the one with id before_id
    (a creation call that made nothing)."""
    feat = s.T(doc.FeatureByPositionReverse(0), "IFeature")
    return None if feat is None or feat.GetID() == before_id else feat


def last_id(s, doc):
    feat = s.T(doc.FeatureByPositionReverse(0), "IFeature")
    return None if feat is None else feat.GetID()


class keep_selection:
    """Saves the current selection (objects and marks) and restores it on exit, so a
    helper feature can be made while a handler is building a multi-selection."""

    def __init__(self, s, doc):
        self.s, self.doc, self.items = s, doc, []

    def __enter__(self):
        sm = self.s.T(self.doc.SelectionManager, "ISelectionMgr")
        n = sm.GetSelectedObjectCount2(-1) or 0
        self.items = [(sm.GetSelectedObject6(i, -1), sm.GetSelectedObjectType3(i, -1),
                       sm.GetSelectedObjectMark(i)) for i in range(1, n + 1)]
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None or not self.items:
            return False
        s, doc = self.s, self.doc
        doc.ClearSelection2(True)
        for obj, typ, mark in self.items:
            if typ in _FEATURE_SELECTIONS:
                ok = s.T(obj, "IFeature").Select2(True, mark)
            elif typ in _BODY_SELECTIONS:
                ok = s.T(obj, "IBody2").Select2(True, s.select_data(doc, mark))
            elif typ in _SKETCH_SELECTIONS:
                iface = "ISketchPoint" if typ in (11, 25) else "ISketchSegment"
                ok = s.T(obj, iface).Select4(True, s.select_data(doc, mark))
            else:
                ok = s.T(obj, "IEntity").Select4(True, s.select_data(doc, mark))
            if not ok:
                raise _not_native("a selection could not be restored after making a helper feature")
        return False


# ============================================================================================
# helper reference geometry (D1): '<owner> Axis' / '<owner> Plane' / '<owner> Points'
# ============================================================================================
def helper_name(st, owner, kind):
    base = f"{owner} {kind}"
    name, k = base, 2
    while name in st.helpers or f"{name} Sketch" in st.helpers:
        name, k = f"{base}-{k}", k + 1
    return name


def add_helper(s, doc, st, feat, name):
    """Name, hide and list a helper feature (report.helpers)."""
    feat = s.T(feat, "IFeature")
    if not set_name(s, doc, feat, name, st):
        st.warn(f"{name}: SolidWorks refused the helper's name (it is called {feat.Name})")
    st.own(s, feat)
    if not hide(s, doc, feat):
        st.warn(f"{feat.Name}: the helper could not be hidden")
    st.helpers.append(feat.Name)
    return feat


def _unit(v):
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        raise _not_native("a helper direction has zero length")
    return v / n


def std_axis(s, doc, st, name, owner=None):
    """A reference axis along global x / y / z (two standard planes), made once."""
    key = ("std", name)
    if key in st.axes:
        return st.axes[key]
    pair = {"x": ("Front Plane", "Top Plane"), "y": ("Front Plane", "Right Plane"),
            "z": ("Top Plane", "Right Plane")}[name]
    with keep_selection(s, doc):
        ext = s.T(doc.Extension, "IModelDocExtension")
        doc.ClearSelection2(True)
        for k, pl in enumerate(pair):
            ext.SelectByID2(pl, "PLANE", 0, 0, 0, k > 0, 0, None, 0)
        before = last_id(s, doc)
        if not doc.InsertAxis2(True):
            raise _not_native(f"the {name} axis could not be created")
        feat = newest_feature(s, doc, before)
        if feat is None:
            raise _not_native(f"the {name} axis could not be created")
        doc.ClearSelection2(True)
        add_helper(s, doc, st, feat, helper_name(st, owner or name.upper(), "Axis"))
    st.axes[key] = feat
    return feat


def face_axis(s, doc, st, face, key, owner):
    """A reference axis through a cylindrical / conical SolidWorks face, made once."""
    if key in st.axes:
        return st.axes[key]
    with keep_selection(s, doc):
        doc.ClearSelection2(True)
        select(s, doc, face, 0, False)
        before = last_id(s, doc)
        if not doc.InsertAxis2(True):
            raise _not_native("an axis through the cylinder could not be created")
        feat = newest_feature(s, doc, before)
        if feat is None:
            raise _not_native("an axis through the cylinder could not be created")
        doc.ClearSelection2(True)
        add_helper(s, doc, st, feat, helper_name(st, owner, "Axis"))
    st.axes[key] = feat
    return feat


def _sketch3d(s, doc, draw):
    """Make a 3D sketch with draw(sketch_manager); returns (IFeature, draw's result)."""
    sm = s.T(doc.SketchManager, "ISketchManager")
    doc.ClearSelection2(True)
    before = last_id(s, doc)
    sm.Insert3DSketch(True)
    sm.AddToDB = True
    try:
        out = draw(sm)
    finally:
        sm.AddToDB = False
        sm.Insert3DSketch(True)
    feat = newest_feature(s, doc, before)
    if feat is None or feat.GetTypeName2() != "3DProfileFeature":
        raise _not_native("SolidWorks did not make a 3D sketch for a helper")
    return feat, out


def line_axis(s, doc, st, point, direction, owner):
    """A reference axis along any line: a hidden 3D-sketch line '<owner> Axis Sketch' and a
    hidden axis '<owner> Axis' on it. Axes along X / Y / Z through the origin use std_axis."""
    p = np.asarray(point, float)
    d = _unit(direction)
    for k, name in enumerate("xyz"):
        if abs(abs(d[k]) - 1) < 1e-9 and np.linalg.norm(np.delete(p, k)) < 1e-9:
            return std_axis(s, doc, st, name, owner)
    q = p - (p @ d) * d                                     # the line's point nearest 0
    key = ("line", tuple(np.round(q, 6)), tuple(np.round(d * (1 if _first_nonzero(d) > 0 else -1), 9)))
    if key in st.axes:
        return st.axes[key]
    with keep_selection(s, doc):
        a, b = p * MM, (p + 10.0 * d) * MM
        sk, seg = _sketch3d(s, doc, lambda sm: sm.CreateLine(*map(float, a), *map(float, b)))
        if seg is None:
            raise _not_native("SolidWorks rejected a helper axis line")
        doc.ClearSelection2(True)
        if not s.T(seg, "ISketchSegment").Select4(False, None):
            raise _not_native("the helper axis line could not be selected")
        before = last_id(s, doc)
        if not doc.InsertAxis2(True):
            raise _not_native("the helper axis could not be created")
        ax = newest_feature(s, doc, before)
        if ax is None:
            raise _not_native("the helper axis could not be created")
        doc.ClearSelection2(True)
        name = helper_name(st, owner, "Axis")
        add_helper(s, doc, st, sk, f"{name} Sketch")
        add_helper(s, doc, st, ax, name)
        prm = np.array(s.T(ax.GetSpecificFeature2(), "IRefAxis").GetRefAxisParams()) / MM
        for x in (prm[:3], prm[3:6]):
            if np.linalg.norm(np.cross(x - p, d)) > 1e-6 * max(1.0, float(np.linalg.norm(p))):
                raise _not_native(f"{name}: SolidWorks placed the helper axis off the line")
    st.axes[key] = ax
    return ax


def _first_nonzero(v):
    return next((float(x) for x in v if abs(x) > 1e-12), 1.0)


def offset_plane(s, doc, st, std_name, offset_mm, owner):
    """A reference plane parallel to a standard plane at a signed distance (made once)."""
    from .core.plane import REFERENCE_PLANES
    ref = REFERENCE_PLANES[std_name]
    want = ref.__class__(ref.origin + ref.normal * offset_mm, ref.normal, ref.xdir)
    for pl, feat in st.planes:
        if _same_plane(pl, want):
            return feat
    with keep_selection(s, doc):
        ext = s.T(doc.Extension, "IModelDocExtension")
        doc.ClearSelection2(True)
        ext.SelectByID2(std_name, "PLANE", 0, 0, 0, False, 0, None, 0)
        fm = s.T(doc.FeatureManager, "IFeatureManager")
        flags = 8 | (256 if offset_mm < 0 else 0)              # distance (+ flip)
        before = last_id(s, doc)
        rp = fm.InsertRefPlane(flags, abs(offset_mm) * MM, 0, 0, 0, 0)
        feat = newest_feature(s, doc, before) if rp is not None else None
        if feat is None:
            raise _not_native("could not create an offset plane")
        doc.ClearSelection2(True)
        _check_plane(s, feat, want)
        add_helper(s, doc, st, feat, helper_name(st, owner, "Plane"))
    st.planes.append((want, feat))
    return feat


def helper_plane(s, doc, st, plane, owner):
    """A reference plane for an explicit plane (no BauyrCAD feature or face stands for it):
    a standard plane itself, an offset of one, or a plane normal to a hidden 3D-sketch line
    through the plane's origin ('<owner> Plane' + '<owner> Plane Sketch')."""
    from .core.plane import REFERENCE_PLANES
    for pl, feat in st.planes:
        if _same_plane(pl, plane):
            return feat
    for name, ref in REFERENCE_PLANES.items():
        if abs(abs(float(plane.normal @ ref.normal)) - 1) < 1e-9:
            off = float((plane.origin - ref.origin) @ ref.normal)
            if abs(off) < 1e-9:
                return ("std", name)
            return offset_plane(s, doc, st, name, off, owner)
    n = _unit(plane.normal)
    with keep_selection(s, doc):
        a, b = plane.origin * MM, (plane.origin + 10.0 * n) * MM
        sk, seg = _sketch3d(s, doc, lambda sm: sm.CreateLine(*map(float, a), *map(float, b)))
        if seg is None:
            raise _not_native("SolidWorks rejected a helper plane line")
        seg = s.T(seg, "ISketchSegment")
        start = s.T(s.T(seg, "ISketchLine").GetStartPoint2(), "ISketchPoint")
        doc.ClearSelection2(True)
        if not (seg.Select4(False, s.select_data(doc, 0))
                and start.Select4(True, s.select_data(doc, 1))):
            raise _not_native("the helper plane references could not be selected")
        fm = s.T(doc.FeatureManager, "IFeatureManager")
        before = last_id(s, doc)
        rp = fm.InsertRefPlane(2, 0, 4, 0, 0, 0)             # perpendicular + coincident
        feat = newest_feature(s, doc, before) if rp is not None else None
        if feat is None:
            raise _not_native("the helper plane could not be created")
        doc.ClearSelection2(True)
        _check_plane(s, feat, plane)
        name = helper_name(st, owner, "Plane")
        add_helper(s, doc, st, sk, f"{name} Sketch")
        add_helper(s, doc, st, feat, name)
    st.planes.append((plane, feat))
    return feat


def helper_points(s, doc, st, points, owner):
    """Points by coordinates (mm): a hidden 3D sketch '<owner> Points'. Returns (IFeature,
    [ISketchPoint])."""
    key = tuple(tuple(np.round(np.asarray(p, float), 6)) for p in points)
    if key in st.points:
        return st.points[key]
    with keep_selection(s, doc):
        def draw(sm):
            return [s.T(sm.CreatePoint(*map(float, np.asarray(p, float) * MM)), "ISketchPoint")
                    for p in points]
        sk, pts = _sketch3d(s, doc, draw)
        if any(p is None for p in pts):
            raise _not_native("SolidWorks rejected a helper point")
        for p, want in zip(pts, points):
            if np.linalg.norm(np.array([p.X, p.Y, p.Z]) / MM - np.asarray(want, float)) > 1e-6:
                raise _not_native("SolidWorks placed a helper point elsewhere")
        add_helper(s, doc, st, sk, helper_name(st, owner, "Points"))
    st.points[key] = (sk, pts)
    return sk, pts


def _same_plane(a, b) -> bool:
    return (abs(abs(float(a.normal @ b.normal)) - 1) < 1e-9
            and abs(float((a.origin - b.origin) @ b.normal)) < 1e-9)


def _check_plane(s, feat, want):
    xf = s.T(s.T(feat.GetSpecificFeature2(), "IRefPlane").Transform, "IMathTransform")
    a = np.array(xf.ArrayData)
    origin, normal = a[9:12] / MM, a[6:9]
    if not (abs(abs(float(normal @ want.normal)) - 1) < 1e-7
            and abs(float((origin - want.origin) @ want.normal)) < 1e-6):
        raise _not_native("SolidWorks placed a helper plane elsewhere")


# ============================================================================================
# finding SolidWorks geometry
# ============================================================================================


def body_edges(s, doc):
    part = s.T(doc, "IPartDoc")
    out = []
    for b in part.GetBodies2(0, False) or ():
        out += [s.T(e, "IEdge") for e in (s.T(b, "IBody2").GetEdges() or ())]
    return out


def body_faces(s, doc):
    part = s.T(doc, "IPartDoc")
    out = []
    for b in part.GetBodies2(0, False) or ():
        out += [s.T(f, "IFace2") for f in (s.T(b, "IBody2").GetFaces() or ())]
    return out


def _dist_to(entity, p_m) -> float:
    q = np.array(entity.GetClosestPointOn(*[float(x) for x in p_m])[:3])
    return float(np.linalg.norm(q - p_m))


def find_edge(s, doc, info, edges=None):
    """The SolidWorks edge through our edge's midpoint and both ends (mm dict from
    modeling.edge_infos)."""
    edges = edges if edges is not None else body_edges(s, doc)
    pts = [np.asarray(info[k], float) * MM for k in ("mid", "p0", "p1") if info.get(k) is not None]
    tol = 1e-7 + 1e-7 * info.get("length", 1.0) * MM
    for e in edges:
        if all(_dist_to(e, p) < tol for p in pts):
            return e
    raise _not_native("an edge could not be found in SolidWorks")


def find_face(s, doc, point_mm, faces=None):
    """The SolidWorks face containing a point that lies strictly inside one of our faces."""
    faces = faces if faces is not None else body_faces(s, doc)
    p = np.asarray(point_mm, float) * MM
    best, bd = None, 1e-7
    for f in faces:
        d = _dist_to(f, p)
        if d < bd:
            best, bd = f, d
    if best is None:
        raise _not_native("a face could not be found in SolidWorks")
    return best


def our_face_point(ctx, ref):
    """A point strictly inside the face `ref` resolves to on the context body (mm)."""
    from .core import geometry
    from .core.refs import resolver
    r = resolver(ctx)
    i = r.face(ref)
    return geometry.point_on_face(ctx.body, i), r.faces[i]


def select(s, doc, obj, mark=0, append=True):
    ent = s.T(obj, "IEntity")
    if ent is None or not ent.Select4(append, s.select_data(doc, mark)):
        raise _not_native("SolidWorks refused a selection")


def select_feature(s, doc, feat, mark=0, append=True):
    if not s.T(feat, "IFeature").Select2(append, mark):
        raise _not_native("SolidWorks refused to select a feature")


def select_plane(s, doc, st, spec, ctx, mark=0, append=True, owner="Plane"):
    """Select a plane spec: a standard plane, a reference plane made earlier, a face, or
    (explicit planes) a helper plane."""
    from .core.plane import REFERENCE_PLANES, SketchPlane
    ext = s.T(doc.Extension, "IModelDocExtension")
    t = spec.get("type")
    if t == "explicit":
        feat = helper_plane(s, doc, st, SketchPlane.from_dict(spec["plane"]), owner)
        if isinstance(feat, tuple):                        # it is a standard plane
            spec = {"type": "std", "name": feat[1]}
            t = "std"
        else:
            select_feature(s, doc, feat, mark, append)
            return
    if t == "std":
        name = spec["name"]
        name = {"front": "Front Plane", "top": "Top Plane", "right": "Right Plane"}.get(name.lower(), name)
        if name not in REFERENCE_PLANES or not ext.SelectByID2(name, "PLANE", 0, 0, 0, append, mark,
                                                               None, 0):
            raise _not_native(f"plane {name} could not be selected")
        return
    if t == "feature":
        feat = st.features.get(spec["id"])
        if feat is None:
            raise _not_native("a reference plane was not transferred")
        select_feature(s, doc, feat, mark, append)
        return
    if t == "face":
        p, _ = our_face_point(ctx, spec["ref"])
        select(s, doc, find_face(s, doc, p), mark, append)
        return
    raise _not_native(f"a plane given as {t} can't be selected in SolidWorks")


def prepare_axis(s, doc, st, spec, ctx, owner):
    """Make the helper axis a spec needs (if any) before a handler starts selecting."""
    t = spec.get("type")
    if t == "std":
        std_axis(s, doc, st, spec["name"].lower().lstrip("+-"), owner)
    elif t == "explicit":
        line_axis(s, doc, st, spec.get("point", (0, 0, 0)), spec["dir"], owner)
    elif t == "face":
        p, f = our_face_point(ctx, spec["ref"])
        if f.kind in ("cylinder", "cone"):
            face_axis(s, doc, st, find_face(s, doc, p), _face_key(f), owner)


def _face_key(f):
    return ("face", tuple(np.round(f.axis_point, 6)), tuple(np.round(np.abs(f.axis), 6)))


def select_axis(s, doc, st, spec, ctx, mark, append=True, owner="Axis"):
    """Select an axis / direction spec: std x/y/z or an explicit line (a helper axis is
    made), a straight edge, a planar face (its normal) or a cylindrical face (a helper axis
    through it). Helpers keep any selection already made."""
    from .core.refs import resolver
    issues = axis_spec_issues(spec, lambda: ctx)
    if issues:
        raise _not_native(issues[0])
    t = spec.get("type")
    if t == "std":
        select_feature(s, doc, std_axis(s, doc, st, spec["name"].lower().lstrip("+-"), owner),
                       mark, append)
        return 1.0
    if t == "explicit":
        select_feature(s, doc, line_axis(s, doc, st, spec.get("point", (0, 0, 0)), spec["dir"],
                                         owner), mark, append)
        return 1.0
    if t == "edge":
        r = resolver(ctx)
        info = r.edges[r.edge(spec["ref"])]
        select(s, doc, find_edge(s, doc, info), mark, append)
        return 1.0
    if t == "face":
        p, f = our_face_point(ctx, spec["ref"])
        face = find_face(s, doc, p)
        if f.kind in ("cylinder", "cone"):
            select_feature(s, doc, face_axis(s, doc, st, face, _face_key(f), owner), mark, append)
            return 1.0
        select(s, doc, face, mark, append)
        return 1.0
    raise _not_native(f"axis {t} can't be selected in SolidWorks")


def select_edges(s, doc, ctx, idx, mark=0):
    from .core import modeling
    infos = modeling.edge_infos(ctx.body)
    edges = body_edges(s, doc)
    for i in idx:
        select(s, doc, find_edge(s, doc, infos[i], edges), mark)


def select_faces(s, doc, ctx, refs, mark=0):
    faces = body_faces(s, doc)
    for ref in refs:
        p, _ = our_face_point(ctx, ref)
        select(s, doc, find_face(s, doc, p, faces), mark)


def sketch_of(st, sketch_id, f):
    """(IFeature, _SketchMap, SketchFeature) of a transferred sketch."""
    sk = st.sketches.get(sketch_id)
    if sk is None:
        raise _not_native(f"{f.name}: its sketch was not transferred")
    return sk


# ============================================================================================
# features
# ============================================================================================


def extrude_reverse(f, m) -> bool:
    """SolidWorks' Reverse Direction flag that gives BauyrCAD's direction. SolidWorks
    extrudes a boss along its sketch normal and a cut against it; BauyrCAD's direction is
    +normal for a boss, -normal for a cut, flipped by reverse and by an auto-flipped cut.
    m.ccw is +1 when BauyrCAD's plane normal is SolidWorks' sketch normal."""
    return (m.ccw < 0) ^ bool(f.reverse) ^ bool(getattr(f, "flipped", False))


def extrude(s, doc, f, ctx, st, attempt):
    """Boss / Cut-Extrude: attempt 0 = the derived direction, attempt 1 = the opposite."""
    from .solidworks import _select_profile
    if attempt == 0:
        require(f, ctx)
    if attempt > 1:
        return NO_MORE
    sketch_feat, m, _ = sketch_of(st, f.sketch_id, f)
    reverse = extrude_reverse(f, m) != bool(attempt)
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    _select_profile(s, doc, sketch_feat, m, f.regions)
    sd, t1, t2 = True, _END_BLIND, _END_BLIND
    d1 = f.depth * MM
    if f.end == "midplane":
        t1 = _END_MID
    elif f.end == "through_all":
        t1 = _END_THROUGH
    elif f.end == "through_all_both":
        sd, t1, t2 = False, _END_THROUGH, _END_THROUGH
    if f.operation == "cut":
        return fm.FeatureCut4(sd, False, reverse, t1, t2, d1, d1, False, False, False, False,
                              0, 0, False, False, False, False, False, True, True, False, True,
                              False, 0, 0, False, False)
    return fm.FeatureExtrusion3(sd, False, reverse, t1, t2, d1, d1, False, False, False, False,
                                0, 0, False, False, False, False, True, True, True, 0, 0, False)


def revolve(s, doc, f, ctx, st, attempt):
    """Revolved Boss / Cut about a sketch line: attempt 0 = BauyrCAD's sense, 1 = the other.
    Both programs turn the profile right-handed about the axis line's start -> end
    direction (the line is recreated start -> end), so Reverse Direction = f.reverse
    whatever the plane or the side of the profile (checked on Front / Top / Right, both
    axis directions and both sides)."""
    from .solidworks import _select_profile
    if attempt == 0:
        require(f, ctx)
    if attempt > 1:
        return NO_MORE
    sketch_feat, m, _ = sketch_of(st, f.sketch_id, f)
    _, axis_map, axis_sketch = sketch_of(st, f.axis_sketch_id or f.sketch_id, f)
    axis = f.axis_line(axis_sketch)
    if axis.id not in axis_map.segs:
        raise _not_native(f"{f.name}: the revolve axis was not transferred")
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    _select_profile(s, doc, sketch_feat, m, f.regions)
    if not axis_map.segs[axis.id].Select4(True, s.select_data(doc, _REVOLVE_AXIS)):
        raise _not_native(f"{f.name}: the revolve axis could not be selected")
    reverse = bool(f.reverse) != bool(attempt)
    mid = f.end == "midplane"
    ang = math.radians(f.angle)
    return fm.FeatureRevolve2(not mid, True, False, f.operation == "cut", reverse, False,
                              _END_MID if mid else _END_BLIND, _END_BLIND, ang, 0.0, False, False,
                              0.0, 0.0, 0, 0.0, 0.0, True, True, True)


def fillet(s, doc, f, ctx, st, attempt):
    if attempt == 0:
        require(f, ctx)
    if attempt > 1:
        return NO_MORE
    doc.ClearSelection2(True)
    select_edges(s, doc, ctx, f.edge_indices(ctx))
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    r = f.radius * MM
    if attempt == 0:
        return fm.FeatureFillet3(195, r, 0.0, 0.0, 0, 0, 0, None, None, None, None, None, None, None)
    return doc.FeatureFillet(r, True, 0, 0, 0)


def chamfer(s, doc, f, ctx, st, attempt):
    """SolidWorks ignores the flip flag for these chamfers through the API, so the other side
    is made by swapping: distance-distance (d2, d1); angle-distance (d tan a, 90 - a). The
    equal-distance type makes nothing through the API: distance-angle 45 deg is the same."""
    if attempt == 0:
        require(f, ctx)
    d1, d2, ang = f.distance, f.distance2, f.angle
    if f.kind == "distance":
        variants = [(1, d1, 45.0, d1)]
    elif f.kind == "distance_distance":
        variants = [(2, d1, 0.0, d2), (2, d2, 0.0, d1)]
    else:
        variants = [(1, d1, ang, d1), (1, d1 * math.tan(math.radians(ang)), 90.0 - ang, d1)]
    if f.flip and len(variants) == 2:
        variants.reverse()
    if attempt >= len(variants):
        return NO_MORE
    typ, w, a, od = variants[attempt]
    doc.ClearSelection2(True)
    select_edges(s, doc, ctx, f.edge_indices(ctx))
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    return fm.InsertFeatureChamfer(4, typ, w * MM, math.radians(a), od * MM, 0.0, 0.0, 0.0)


def shell(s, doc, f, ctx, st, attempt):
    if attempt == 0:
        require(f, ctx)
    if attempt > 0:
        return NO_MORE
    doc.ClearSelection2(True)
    select_faces(s, doc, ctx, [r.to_dict() for r in f.faces])
    before = last_id(s, doc)
    doc.InsertFeatureShell(f.thickness * MM, bool(f.outward))
    return newest_feature(s, doc, before)            # None: SolidWorks made nothing


def draft(s, doc, f, ctx, st, attempt):
    if attempt == 0:
        require(f, ctx)
        if f.neutral.get("type") == "explicit":
            select_plane(s, doc, st, f.neutral, ctx, owner=f.name)   # make the helper first
    if attempt > 1:
        return NO_MORE
    doc.ClearSelection2(True)
    select_plane(s, doc, st, f.neutral, ctx, mark=1, append=True, owner=f.name)
    select_faces(s, doc, ctx, [r.to_dict() for r in f.faces], mark=2)
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    return fm.InsertMultiFaceDraft(math.radians(f.angle), bool(attempt), False, 0, False, False)


def ref_plane(s, doc, f, ctx, st, attempt):
    if attempt == 0:
        require(f, ctx)
        for spec in (f.base, f.base2):
            if spec is not None and spec.get("type") == "explicit":
                select_plane(s, doc, st, spec, ctx, owner=f.name)   # make the helper first
    if attempt > 1:
        return NO_MORE
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    doc.ClearSelection2(True)
    before = last_id(s, doc)
    if f.mode == "offset":
        select_plane(s, doc, st, f.base, ctx, 0, False, owner=f.name)
        flip = (f.distance < 0) != bool(attempt)
        rp = fm.InsertRefPlane(8 | (256 if flip else 0), abs(f.distance) * MM, 0, 0, 0, 0)
    else:                                               # "mid" (require() refused the others)
        select_plane(s, doc, st, f.base, ctx, 0, False, owner=f.name)
        select_plane(s, doc, st, f.base2, ctx, 1, True, owner=f.name)
        rp = fm.InsertRefPlane(128, 0, 128, 0, 0, 0)
    doc.ClearSelection2(True)
    feat = newest_feature(s, doc, before) if rp is not None else None
    if feat is None:
        raise _not_native(f"{f.name}: SolidWorks could not create the plane")
    # a reference plane changes no volume: check its position ourselves
    xf = s.T(s.T(rp, "IRefPlane").Transform, "IMathTransform")   # InsertRefPlane returns the plane
    a = np.array(xf.ArrayData)
    origin, normal = a[9:12] / MM, a[6:9]
    want = f.result_plane
    ok = (abs(abs(float(normal @ want.normal)) - 1) < 1e-7
          and abs(float((origin - want.origin) @ want.normal)) < 1e-6)
    if not ok:
        _delete(s, doc, feat)
        return None
    st.planes.append((want, feat))
    return feat


def _delete(s, doc, feat):
    doc.ClearSelection2(True)
    s.T(feat, "IFeature").Select2(False, 0)
    s.T(doc.Extension, "IModelDocExtension").DeleteSelection2(0)
    doc.ClearSelection2(True)


def _seeds(s, doc, f, st, mark):
    for sid in f.seeds:
        feat = st.features.get(sid)
        if feat is None:
            raise _not_native(f"{f.name}: a seed feature was not transferred")
        for x in (feat if isinstance(feat, list) else [feat]):
            select_feature(s, doc, x, mark, True)


def linear_pattern(s, doc, f, ctx, st, attempt):
    two = f.direction2 is not None and f.count2 > 1
    if attempt == 0:
        require(f, ctx)
        prepare_axis(s, doc, st, f.direction, ctx, f.name)
        if two:
            prepare_axis(s, doc, st, f.direction2, ctx, f.name)
    flips = [(a, b) for a in (False, True) for b in ((False, True) if two else (False,))]
    if attempt >= len(flips):
        return NO_MORE
    doc.ClearSelection2(True)
    select_axis(s, doc, st, f.direction, ctx, _PATTERN_DIR1, owner=f.name)
    if two:
        select_axis(s, doc, st, f.direction2, ctx, _PATTERN_DIR2, owner=f.name)
    _seeds(s, doc, f, st, _PATTERN_SEED)
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    f1, f2 = flips[attempt]
    return fm.FeatureLinearPattern5(f.count, f.spacing * MM, f.count2 if two else 1,
                                    f.spacing2 * MM if two else 0.01, f1, f2, "", "", False,
                                    False, False, False, False, False, False, False, False, False,
                                    0.0, 0.0, False, False)


def circular_pattern(s, doc, f, ctx, st, attempt):
    if attempt == 0:
        require(f, ctx)
        prepare_axis(s, doc, st, f.axis, ctx, f.name)
    total = math.radians(f.angle)
    variants = [(False, True, total), (True, True, total),
                (False, False, math.radians(f.step())), (True, False, math.radians(f.step()))]
    if attempt >= len(variants):
        return NO_MORE
    flip, equal, spacing = variants[attempt]
    doc.ClearSelection2(True)
    select_axis(s, doc, st, f.axis, ctx, _PATTERN_DIR1, owner=f.name)
    _seeds(s, doc, f, st, _PATTERN_SEED)
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    return fm.FeatureCircularPattern5(f.count, spacing, flip, "", False, equal, False, False,
                                      False, False, 1, 0.0, "", False)


def mirror(s, doc, f, ctx, st, attempt):
    if attempt == 0:
        require(f, ctx)
        if f.plane.get("type") == "explicit":
            select_plane(s, doc, st, f.plane, ctx, owner=f.name)    # make the helper first
    if attempt > 0:
        return NO_MORE
    doc.ClearSelection2(True)
    select_plane(s, doc, st, f.plane, ctx, _MIRROR_PLANE, True, owner=f.name)
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    if f.bodies or not f.seeds:
        part = s.T(doc, "IPartDoc")
        for b in part.GetBodies2(0, False) or ():
            if not s.T(b, "IBody2").Select2(True, s.select_data(doc, 256)):
                raise _not_native(f"{f.name}: the body could not be selected")
        return fm.InsertMirrorFeature2(True, False, True, False, 0)
    _seeds(s, doc, f, st, _MIRROR_SEED)
    return fm.InsertMirrorFeature2(False, False, False, False, 0)


def hole(s, doc, f, ctx, st, attempt):
    """Hole Wizard holes (one SolidWorks hole per position, named '<name>', '<name>-2'...).
    SolidWorks' own size tables differ from ours, so each hole is created and then given our
    exact dimensions."""
    if attempt == 0:
        require(f, ctx)
    if attempt > 1:
        return NO_MORE
    dm = f.dims()
    through = f.end == "through_all"
    pl, pts = f.world_points(ctx)
    u = -pl.normal * (-1 if f.reverse else 1)
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    ext = s.T(doc.Extension, "IModelDocExtension")
    # (generic type, ISO fastener table, a size that exists in it)
    kind, table, size = {"simple": (2, 143, "Ø8.0"), "tapped": (2, 145, f.size or "M6"),
                         "counterbore": (0, 139, "M6"), "countersink": (1, 140, "M6")}[f.kind]
    if f.kind == "counterbore" and f.size in _ISO_SIZES:
        size = f.size
    if f.kind == "countersink" and f.size in _ISO_SIZES:
        size = f.size
    made = []
    try:
        for k, p in enumerate(pts):
            doc.ClearSelection2(True)
            # select the face by a ray along the drilling direction: the hole goes exactly at
            # the ray's hit point (a point pick is placed up to ~0.1 mm off)
            q = (np.asarray(p, float) - u * 1e-3) * MM
            if not ext.SelectByRay(float(q[0]), float(q[1]), float(q[2]), float(u[0]),
                                   float(u[1]), float(u[2]), 1e-6, 2, False, 0, 0):
                raise _not_native(f"{f.name}: no model face at a hole position")
            feat = fm.HoleWizard5(kind, 8, table, size, 1 if through else 0, dm["d"] * MM,
                                  (f.depth if not through else 10.0) * MM, -1, *([0.0] * 12), "",
                                  bool(attempt), True, True, True, True, False)
            if feat is None:
                sel = s.T(doc.SelectionManager, "ISelectionMgr")
                raise _not_native(f"{f.name}: SolidWorks' Hole Wizard refused hole {k + 1} at "
                                  f"({p[0]:.4g}, {p[1]:.4g}, {p[2]:.4g}); selected type "
                                  f"{sel.GetSelectedObjectType3(1, -1)}")
            feat = s.T(feat, "IFeature")
            made.append(feat)
            d = s.T(feat.GetDefinition(), "IWizardHoleFeatureData2")
            d.HoleDiameter = dm["d"] * MM
            if not through:
                d.HoleDepth = f.depth * MM
                d.DrillAngle = math.radians(118.0 if f.drill_point else 180.0)
            if f.kind == "counterbore":
                d.CounterBoreDiameter = dm["cbore_d"] * MM
                d.CounterBoreDepth = dm["cbore_depth"] * MM
            elif f.kind == "countersink":
                d.CounterSinkDiameter = dm["csk_d"] * MM
                d.CounterSinkAngle = math.radians(dm.get("csk_angle", 90.0))
            if not feat.ModifyDefinition(d, doc, None):
                raise _not_native(f"{f.name}: the hole dimensions could not be set")
            _place_hole_point(s, doc, feat, p)
    except Exception:
        for m in reversed(made):
            _delete(s, doc, m)
        raise
    return made[0] if len(made) == 1 else made


def _place_hole_point(s, doc, feat, p_mm):
    """Move the hole's positioning sketch point exactly onto p_mm if SolidWorks put it off."""
    d = s.T(feat.GetDefinition(), "IWizardHoleFeatureData2")
    pts = d.GetSketchPoints() or ()
    if len(pts) != 1:
        return
    sp = s.T(pts[0], "ISketchPoint")
    skt = s.T(sp.GetSketch(), "ISketch")
    xf = s.T(skt.ModelToSketchTransform, "IMathTransform")
    from .solidworks import _doubles
    mp = s.T(s.math.CreatePoint(_doubles(np.asarray(p_mm, float) * MM)), "IMathPoint")
    q = np.array(s.T(mp.MultiplyTransform(xf), "IMathPoint").ArrayData)
    if np.linalg.norm(q[:2] - np.array([sp.X, sp.Y])) < 1e-9:
        return
    doc.ClearSelection2(True)
    sp.Select4(False, None)
    doc.EditSketch()
    sp.SetCoords(float(q[0]), float(q[1]), 0.0)
    s.T(doc.SketchManager, "ISketchManager").InsertSketch(True)
    doc.ClearSelection2(True)
    doc.EditRebuild3()


_ISO_SIZES = ("M2", "M2.5", "M3", "M4", "M5", "M6", "M8", "M10", "M12", "M16", "M20", "M24")


def helix(s, doc, f, ctx, st, attempt):
    """Helix / Spiral on its circle sketch; an explicit helix gets a circle sketch made for
    it ('<name> Sketch', a helper) on a plane normal to its axis."""
    if attempt == 0:
        require(f, ctx)
    if attempt > 0:
        return NO_MORE
    from .core.plane import SketchPlane
    from .solidworks import _SketchMap, _plane_selection
    doc.ClearSelection2(True)
    if f.sketch:
        sk = st.features.get(f.sketch)
        if sk is None:
            raise _not_native(f"{f.name}: its sketch was not transferred")
        select_feature(s, doc, sk, 0, False)
    else:
        axis = np.asarray(f.axis, float)
        axis = axis / np.linalg.norm(axis)
        x = np.cross(axis, [0, 0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0, 0])
        plane = SketchPlane(f.center, axis, x)
        _plane_selection(s, doc, plane, st, f.name)
        sm = s.T(doc.SketchManager, "ISketchManager")
        sm.InsertSketch(True)
        sws = s.T(doc.GetActiveSketch2(), "ISketch")
        if sws is None:
            raise _not_native(f"{f.name}: SolidWorks did not open a sketch for the helix")
        m = _SketchMap(s, sws, plane)
        c = m.sk((0.0, 0.0))
        sm.AddToDB = True
        sm.CreateCircleByRadius(c[0], c[1], 0, f.diameter / 2 * MM)
        sm.AddToDB = False
        sm.InsertSketch(True)
        sk = s.T(doc.FeatureByPositionReverse(0), "IFeature")
        name = helper_name(st, f.name, "Sketch")
        if not set_name(s, doc, sk, name, st):
            st.warn(f"{name}: SolidWorks refused the helper's name (it is called {sk.Name})")
        st.own(s, sk)
        st.helpers.append(sk.Name)
        doc.ClearSelection2(True)
        select_feature(s, doc, sk, 0, False)
    pitch, turns = f.pitch_turns()
    by = {"pitch_revolutions": 0, "height_revolutions": 1, "height_pitch": 2}[f.defined_by]
    doc.InsertHelix(bool(f.reverse), bool(f.left_hand), False, False, by, pitch * turns * MM,
                    pitch * MM, float(turns), 0.0, math.radians(f.start_angle))
    feat = s.T(doc.FeatureByPositionReverse(0), "IFeature")
    if feat is None or feat.GetTypeName2() != "Helix":
        raise _not_native(f"{f.name}: SolidWorks could not create the helix")
    doc.ClearSelection2(True)
    return feat


def sweep(s, doc, f, ctx, st, attempt):
    if attempt == 0:
        require(f, ctx)
    if attempt > 0:
        return NO_MORE
    path = f.path.get("sketch") or f.path.get("feature")
    if path is None or path not in st.features:
        raise _not_native(f"{f.name}: the sweep path was not transferred")
    doc.ClearSelection2(True)
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    circ = f.profile is None
    if not circ:
        if f.profile not in st.features:
            raise _not_native(f"{f.name}: the profile sketch was not transferred")
        select_feature(s, doc, st.features[f.profile], 1, True)
    select_feature(s, doc, st.features[path], 4, True)
    d = (f.diameter or 0.0) * MM
    if f.operation == "cut":
        return fm.InsertCutSwept5(False, True, 0, False, False, 0, 0, False, 0, 0, 0, 0, True,
                                  True, 0, True, True, True, False, circ, d, 0)
    return fm.InsertProtrusionSwept4(False, True, 0, False, False, 0, 0, False, 0, 0, 0, 0, True,
                                     True, True, 0, True, circ, d, 0)


def loft(s, doc, f, ctx, st, attempt):
    if attempt == 0:
        require(f, ctx)
    if attempt > 0:
        return NO_MORE
    doc.ClearSelection2(True)
    for sec in f.sections:
        if sec not in st.features:
            raise _not_native(f"{f.name}: a section sketch was not transferred")
        select_feature(s, doc, st.features[sec], 1, True)
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    if f.operation == "cut":
        return fm.InsertCutBlend(bool(f.closed), False, False, 1.0, 0, 0, False, 0, 0, 0, True, True)
    return fm.InsertProtrusionBlend2(bool(f.closed), False, False, 1.0, 0, 0, 1.0, 1.0, False,
                                     False, False, 0, 0, 0, True, True, True, 0)


HANDLERS = {"extrude": extrude, "revolve": revolve,
            "fillet": fillet, "chamfer": chamfer, "shell": shell, "draft": draft, "helix": helix,
            "ref_plane": ref_plane, "linear_pattern": linear_pattern,
            "circular_pattern": circular_pattern, "mirror": mirror, "hole": hole,
            "sweep": sweep, "loft": loft}
# reference features without a SolidWorks counterpart yet: skipped with a (blocking) warning;
# the strict pre-check refuses them before SolidWorks starts
SKIPPED = ("ref_axis", "ref_point", "curve")
