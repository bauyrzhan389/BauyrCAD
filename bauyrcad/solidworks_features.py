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
import os

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
        self.planes: list = []          # (SketchPlane, IFeature): helper planes explicit planes reuse
        self.axes: dict = {}            # key -> IFeature (reference axes made for features)
        self.points: dict = {}          # key -> (IFeature, [ISketchPoint]) helper points
        self.ref_points: list = []      # (xyz mm, IFeature) reference points made (steps, helpers)
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


def axis_spec_issues(spec, ctx, circular=False, line=False) -> list:
    """Axis / direction specs select_axis can't select in SolidWorks. line=True: the spec
    must stand for a line (a planar face's normal can't be one in SolidWorks)."""
    from .core.refs import resolver
    t = (spec or {}).get("type")
    if t in ("std", "explicit", "sketch_line"):
        return []
    if t == "edge":
        try:
            r = resolver(ctx())
            info = r.edges[r.edge(spec["ref"])]
        except Exception:  # noqa: BLE001 - a broken reference is the feature's own error
            return []
        return ([] if info["kind"] in ("line", "circle", "arc") else
                [f"an axis on a {info['kind']} edge has no SolidWorks equivalent"])
    if t == "face":
        if not (circular or line):
            return []
        try:
            _, face = our_face_point(ctx(), spec["ref"])
        except Exception:  # noqa: BLE001
            return []
        if face.kind in ("cylinder", "cone"):
            return []
        return (["a planar face as a circular-pattern axis has no SolidWorks equivalent"] if circular
                else ["a planar face's normal as the rotation axis has no SolidWorks equivalent "
                      "(pick an edge, axis or line)"])
    if t == "feature":
        try:
            g = ctx().features.get(spec["id"])
        except Exception:  # noqa: BLE001
            return []
        if g is None or g.TYPE == "ref_axis":
            return []
        return [f"{g.name} ({g.TYPE}) can't be selected as an axis in SolidWorks"]
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
    if f.axis_sketch_id and f.axis_sketch_id != f.sketch_id:
        return ["a revolve about a line in another sketch is not transferred yet (SolidWorks "
                "makes no feature; draw the axis as a centerline in the profile sketch)"]
    if f.axis_id is not None:
        from .core.sketch import Line
        sk = ctx().features.get(f.sketch_id)
        axis = sk.sketch.entities.get(f.axis_id) if sk is not None else None
        if isinstance(axis, Line) and not axis.construction and sk.profile_error:
            return ["a revolve about an ordinary sketch line that is not part of the profile is "
                    "not transferred yet (SolidWorks makes no feature; make the line a "
                    "centerline)"]
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
    if f.mode not in ("offset", "mid", "angle", "points", "normal"):
        return [f"a {f.mode} reference plane is not transferred yet"]
    if f.mode in ("offset", "mid", "angle"):
        out += plane_spec_issues(f.base)
    if f.mode == "mid" and f.base2 is not None:
        out += plane_spec_issues(f.base2)
    if f.mode in ("angle", "normal") and f.axis is not None:
        out += axis_spec_issues(f.axis, ctx, line=f.mode == "angle")
    if f.mode == "angle" and f.axis is not None and not out:
        from .core.refs import resolve_axis, resolve_plane
        try:
            c = ctx()
            n = resolve_plane(f.base, c).normal
            a = resolve_axis(f.axis, c)[1]
        except Exception:  # noqa: BLE001 - a broken reference is the feature's own error
            return out
        if abs(float(np.asarray(a) @ np.asarray(n))) > 1e-9:
            out.append("the rotation axis is not parallel to the base plane: SolidWorks' 'At "
                       "angle' plane turns about a line lying in (or parallel to) the plane")
    return out


@precheck("ref_axis")
def _ref_axis_issues(f, ctx):
    if f.mode not in ("points", "face", "edge", "planes", "point_dir"):
        return [f"a {f.mode} reference axis is not transferred yet"]
    out = []
    if f.mode == "planes":
        out += plane_spec_issues(f.base) + plane_spec_issues(f.base2)
    if f.mode == "edge" and f.ref is not None:
        out += axis_spec_issues({"type": "edge", "ref": f.ref}, ctx)
    return out


@precheck("ref_point")
def _ref_point_issues(f, ctx):
    return ([] if f.mode in ("coords", "arc_center", "face_center") else
            [f"a {f.mode} reference point is not transferred yet"])


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
    a standard plane itself, an earlier helper plane in the same place, an offset of a
    standard plane, or a plane normal to a hidden 3D-sketch line through the plane's origin
    ('<owner> Plane' + '<owner> Plane Sketch'). The standard planes come first, and
    transferred BauyrCAD reference planes are never reused: they may follow model faces,
    while an explicit plane is fixed (st.planes holds helper planes only)."""
    from .core.plane import REFERENCE_PLANES
    for name, ref in REFERENCE_PLANES.items():
        if (abs(abs(float(plane.normal @ ref.normal)) - 1) < 1e-9
                and abs(float((plane.origin - ref.origin) @ ref.normal)) < 1e-9):
            return ("std", name)
    for pl, feat in st.planes:
        if _same_plane(pl, plane):
            return feat
    for name, ref in REFERENCE_PLANES.items():
        if abs(abs(float(plane.normal @ ref.normal)) - 1) < 1e-9:
            off = float((plane.origin - ref.origin) @ ref.normal)
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


def body_vertices(s, doc):
    part = s.T(doc, "IPartDoc")
    out = []
    for b in part.GetBodies2(0, False) or ():
        out += [s.T(v, "IVertex") for v in (s.T(b, "IBody2").GetVertices() or ())]
    return out


def point_selectors(s, doc, st, points, owner):
    """For each point (mm) a function sel(mark, append) that selects an entity lying there:
    the origin, a model vertex, a reference point made earlier, else (D1) a point of ONE
    hidden 3D sketch '<owner> Points' for all the points nothing stands for. BauyrCAD keeps
    such points as coordinates only, so the coordinates decide."""
    ext = s.T(doc.Extension, "IModelDocExtension")
    verts = None
    sels, missing = [None] * len(points), []

    def entity_sel(obj):
        return lambda mark, append: select(s, doc, obj, mark, append)

    def feature_sel(feat):
        return lambda mark, append: select_feature(s, doc, feat, mark, append)

    def origin_sel(mark, append):
        if not ext.SelectByID2("Point1@Origin", "EXTSKETCHPOINT", 0, 0, 0, append, mark, None, 0):
            raise _not_native("the origin could not be selected")

    for k, p in enumerate(points):
        p = np.asarray(p, float)
        if float(np.linalg.norm(p)) < 1e-9:
            sels[k] = origin_sel
            continue
        hit = next((feat for q, feat in st.ref_points if np.linalg.norm(q - p) < 1e-6), None)
        if hit is not None:
            sels[k] = feature_sel(hit)
            continue
        if verts is None:
            verts = [(np.array(v.GetPoint()[:3]) / MM, v) for v in body_vertices(s, doc)]
        v = next((v for q, v in verts if np.linalg.norm(q - p) < 1e-6), None)
        if v is not None:
            sels[k] = entity_sel(v)
        else:
            missing.append(k)
    if missing:
        _, pts = helper_points(s, doc, st, [points[k] for k in missing], owner)

        def sketch_sel(sp):
            def sel(mark, append):
                if not s.T(sp, "ISketchPoint").Select4(append, s.select_data(doc, mark)):
                    raise _not_native("a helper point could not be selected")
            return sel
        for k, sp in zip(missing, pts):
            sels[k] = sketch_sel(sp)
    return sels


def _new_axis(s, doc, what):
    """InsertAxis2 on the current selection; the new RefAxis feature."""
    before = last_id(s, doc)
    ok = doc.InsertAxis2(True)
    feat = newest_feature(s, doc, before) if ok else None
    doc.ClearSelection2(True)
    if feat is None or feat.GetTypeName2() != "RefAxis":
        raise _not_native(f"SolidWorks could not create {what}")
    return feat


def axis_line(s, feat):
    """(point mm, unit direction) of a SolidWorks reference axis."""
    prm = np.array(s.T(s.T(feat, "IFeature").GetSpecificFeature2(), "IRefAxis").GetRefAxisParams())
    a, b = prm[:3] / MM, prm[3:6] / MM
    return a, (b - a) / max(float(np.linalg.norm(b - a)), 1e-300)


def same_line(p, d, q, e, tol=1e-6) -> bool:
    """Do the lines (p, d) and (q, e) coincide (mm, any sense)?"""
    d, e = _unit(d), _unit(e)
    return (float(np.linalg.norm(np.cross(d, e))) < 1e-9
            and float(np.linalg.norm(np.cross(np.asarray(q, float) - np.asarray(p, float), d))) < tol)


def helper_ref_point(s, doc, st, kind, select_fn, owner, want):
    """A hidden reference point '<owner> Points' (swRefPointType_e kind on the selection
    select_fn() makes) at want (mm), made once per place."""
    want = np.asarray(want, float)
    for q, feat in st.ref_points:
        if np.linalg.norm(q - want) < 1e-6:
            return feat
    with keep_selection(s, doc):
        doc.ClearSelection2(True)
        select_fn()
        fm = s.T(doc.FeatureManager, "IFeatureManager")
        before = last_id(s, doc)
        fm.InsertReferencePoint(kind, 0, 0.0, 1)
        feat = newest_feature(s, doc, before)
        doc.ClearSelection2(True)
        if feat is None or feat.GetTypeName2() != "RefPoint":
            raise _not_native(f"{owner}: SolidWorks could not create a helper point")
        if np.linalg.norm(ref_point_xyz(s, feat) - want) > 1e-6:
            _delete(s, doc, feat)
            raise _not_native(f"{owner}: SolidWorks placed a helper point elsewhere")
        add_helper(s, doc, st, feat, helper_name(st, owner, "Points"))
    st.ref_points.append((want, feat))
    return feat


def ref_point_xyz(s, feat):
    rp = s.T(s.T(feat, "IFeature").GetSpecificFeature2(), "IRefPoint")
    return np.array(s.T(rp.GetRefPoint(), "IMathPoint").ArrayData[:3]) / MM


def select_circle_axis(s, doc, st, info, owner):
    """Select what SolidWorks makes a reference axis from for a circular edge's axis: a
    coaxial cylindrical / conical face next to it, else the face the circle lies in plus a
    hidden 'Arc Center' reference point '<owner> Points'. The selection is for InsertAxis2."""
    edge = find_edge(s, doc, info)
    c, n = np.asarray(info["center"], float), _unit(info["axis"])
    faces = [s.T(f, "IFace2") for f in (s.T(edge, "IEdge").GetTwoAdjacentFaces2() or ()) if f is not None]
    for face in faces:
        surf = s.T(face.GetSurface(), "ISurface")
        prm = (surf.CylinderParams if surf.IsCylinder() else
               surf.ConeParams if surf.IsCone() else None)
        if prm is not None and same_line(c, n, np.array(prm[:3]) / MM, prm[3:6]):
            doc.ClearSelection2(True)
            select(s, doc, face, 0, False)
            return
    for face in faces:
        surf = s.T(face.GetSurface(), "ISurface")
        if surf.IsPlane() and float(np.linalg.norm(np.cross(surf.PlaneParams[:3], n))) < 1e-9:
            pt = helper_ref_point(s, doc, st, 3, lambda: select(s, doc, edge, 0, False), owner, c)
            doc.ClearSelection2(True)
            select(s, doc, face, 0, False)
            select_feature(s, doc, pt, 0, True)
            return
    raise _not_native("the axis of a circular edge with no plane or cylinder next to it can't "
                      "be made in SolidWorks")


def circle_axis(s, doc, st, info, owner):
    """A hidden reference axis '<owner> Axis' along a circular edge's axis (made once)."""
    key = ("circle", tuple(np.round(info["center"], 6)), tuple(np.round(np.abs(info["axis"]), 6)))
    if key in st.axes:
        return st.axes[key]
    with keep_selection(s, doc):
        select_circle_axis(s, doc, st, info, owner)
        feat = _new_axis(s, doc, "an axis through a circular edge")
        if not same_line(*axis_line(s, feat), info["center"], info["axis"]):
            _delete(s, doc, feat)
            raise _not_native("SolidWorks placed a circular edge's axis elsewhere")
        add_helper(s, doc, st, feat, helper_name(st, owner, "Axis"))
    st.axes[key] = feat
    return feat


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
    elif t == "edge":
        from .core.refs import resolver
        r = resolver(ctx)
        info = r.edges[r.edge(spec["ref"])]
        if info["kind"] in ("circle", "arc"):
            circle_axis(s, doc, st, info, owner)


def _face_key(f):
    return ("face", tuple(np.round(f.axis_point, 6)), tuple(np.round(np.abs(f.axis), 6)))


def select_axis(s, doc, st, spec, ctx, mark, append=True, owner="Axis", line=False):
    """Select an axis / direction spec: a reference axis made earlier (by its BauyrCAD
    feature id), a sketch line, a straight edge, std x/y/z or an explicit line (a helper axis
    is made), a circular edge or a cylindrical face (their axis: the reference axis made
    from the face, else a helper), or a planar face (its normal; line=True refuses it).
    Helpers keep any selection already made."""
    from .core.refs import resolver
    issues = axis_spec_issues(spec, lambda: ctx, line=line)
    if issues:
        raise _not_native(issues[0])
    t = spec.get("type")
    if t == "feature":
        feat = st.features.get(spec["id"])
        if feat is None:
            raise _not_native("a reference axis was not transferred")
        select_feature(s, doc, feat, mark, append)
        return 1.0
    if t == "sketch_line":
        sk = st.sketches.get(spec["sketch"])
        if sk is None:
            raise _not_native(f"{owner}: the sketch of its axis line was not transferred")
        seg = sk[1].segs.get(spec["entity"])
        if seg is None or not s.T(seg, "ISketchSegment").Select4(append, s.select_data(doc, mark)):
            raise _not_native("a sketch line could not be selected as the axis")
        return 1.0
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
        if info["kind"] in ("circle", "arc"):
            select_feature(s, doc, circle_axis(s, doc, st, info, owner), mark, append)
            return 1.0
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
    equal-distance bit is combined with distance-distance, not used as a standalone type."""
    if attempt == 0:
        require(f, ctx)
    d1, d2, ang = f.distance, f.distance2, f.angle
    if f.kind == "distance":
        # Equal setback is not a 45-degree chamfer when adjacent faces are not
        # perpendicular: swChamferDistanceDistance | swChamferEqualDistance.
        variants = [(18, d1, 0.0, d1)]
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


# swRefPlaneReferenceConstraints_e
_RP_PARALLEL, _RP_PERPENDICULAR, _RP_COINCIDENT, _RP_DISTANCE = 1, 2, 4, 8
_RP_ANGLE, _RP_MID, _RP_FLIP = 16, 128, 256


def plane_frame(s, feat):
    """(origin mm, unit normal) of a SolidWorks reference plane (IRefPlane.Transform)."""
    xf = s.T(s.T(s.T(feat, "IFeature").GetSpecificFeature2(), "IRefPlane").Transform,
             "IMathTransform")
    a = np.array(xf.ArrayData)
    return a[9:12] / MM, a[6:9]


def plane_match(s, feat, want):
    """(the plane lies where want does, its normal is the opposite of want's)."""
    origin, normal = plane_frame(s, feat)
    dot = float(normal @ want.normal)
    ok = abs(abs(dot) - 1) < 1e-7 and abs(float((origin - want.origin) @ want.normal)) < 1e-6
    return ok, dot < 0


def _plane_point(f, ctx):
    """The point a 'normal' plane goes through (BauyrCAD: f.point, else the axis point)."""
    from .core.refs import resolve_axis
    return np.asarray(f.point if f.point is not None else resolve_axis(f.axis, ctx)[0], float)


def _plane_variants(s, doc, f, ctx, st):
    """[(select(), InsertRefPlane arguments)] that may make BauyrCAD's plane, the predicted
    one first. SolidWorks has no 'Flip normal' through the API: the variants that reverse the
    normal swap the references (mid, three points) or turn the other way (angle)."""
    def plane(spec, mark, append):
        return lambda: select_plane(s, doc, st, spec, ctx, mark, append, owner=f.name)

    def seq(*fns):
        def run():
            for fn in fns:
                fn()
        return run
    if f.mode == "offset":
        d = float(f.distance)
        if abs(d) < 1e-12:
            return [(plane(f.base, 0, False), (_RP_COINCIDENT, 0.0, 0, 0, 0, 0))]
        flags = [_RP_FLIP if d < 0 else 0, 0 if d < 0 else _RP_FLIP]
        return [(plane(f.base, 0, False), (_RP_DISTANCE | fl, abs(d) * MM, 0, 0, 0, 0))
                for fl in flags]
    if f.mode == "mid":
        args = (_RP_MID, 0.0, _RP_MID, 0.0, 0, 0)
        return [(seq(plane(f.base, 0, False), plane(f.base2, 1, True)), args),
                (seq(plane(f.base2, 0, False), plane(f.base, 1, True)), args)]
    if f.mode == "angle":
        def axis():
            select_axis(s, doc, st, f.axis, ctx, 1, True, owner=f.name, line=True)
        a = float(f.angle) % 360.0
        out = []
        for ang in dict.fromkeys((round(a, 12), round((360.0 - a) % 360.0, 12))):
            for fl in (0, _RP_FLIP):
                out.append((seq(plane(f.base, 0, False), axis),
                            (_RP_ANGLE | fl, math.radians(ang), _RP_COINCIDENT, 0.0, 0, 0)))
        return out
    if f.mode == "points":
        sels = point_selectors(s, doc, st, f.points, f.name)

        def pts(order):
            def run():
                for mark, k in enumerate(order):
                    sels[k](mark, mark > 0)
            return run
        args = (_RP_COINCIDENT, 0.0, _RP_COINCIDENT, 0.0, _RP_COINCIDENT, 0.0)
        return [(pts((0, 1, 2)), args), (pts((1, 0, 2)), args)]
    if f.mode == "normal":
        (sel,) = point_selectors(s, doc, st, [_plane_point(f, ctx)], f.name)
        planar = f.axis.get("type") == "face" and our_face_point(ctx, f.axis["ref"])[1].kind not in (
            "cylinder", "cone")
        first = _RP_PARALLEL if planar else _RP_PERPENDICULAR    # parallel to a face = normal to its normal

        def run():
            select_axis(s, doc, st, f.axis, ctx, 0, False, owner=f.name)
            sel(1, True)
        return [(run, (first, 0.0, _RP_COINCIDENT, 0.0, 0, 0))]
    raise _not_native(f"{f.name}: a {f.mode} reference plane is not transferred yet")


def ref_plane(s, doc, f, ctx, st, attempt):
    """Reference Plane with BauyrCAD's references and constraints (offset / coincident,
    mid plane, at angle, three points, normal to a curve through a point). A plane changes no
    volume, so its place AND normal are checked here (IRefPlane.Transform). If no variant
    gives BauyrCAD's normal (an offset or 'normal' plane with Flip: SolidWorks keeps the
    reference's direction), the plane in the right place is kept and a note says so; the
    sketches on it are mapped onto it either way."""
    if attempt == 0:
        require(f, ctx)
        for spec in (f.base, f.base2):
            if f.mode in ("offset", "mid", "angle") and spec is not None and spec.get("type") == "explicit":
                select_plane(s, doc, st, spec, ctx, owner=f.name)   # make the helper first
        if f.mode in ("angle", "normal"):
            prepare_axis(s, doc, st, f.axis, ctx, f.name)
    variants = _plane_variants(s, doc, f, ctx, st)
    n = len(variants)
    if attempt >= 2 * n:
        return NO_MORE
    strict = attempt < n
    sel, args = variants[attempt % n]
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    doc.ClearSelection2(True)
    sel()
    before = last_id(s, doc)
    rp = fm.InsertRefPlane(*args)
    doc.ClearSelection2(True)
    feat = newest_feature(s, doc, before) if rp is not None else None
    if feat is None:
        return None
    ok, reversed_ = plane_match(s, feat, f.result_plane)
    if not ok or (reversed_ and strict):
        _delete(s, doc, feat)
        return None
    if reversed_:
        st.report.notes.append(f"{f.name}: SolidWorks keeps the plane's normal the other way "
                               "(it has no Flip Normal for this plane through the API); "
                               "sketches and features on it are mapped onto it")
    return feat


def ref_axis(s, doc, f, ctx, st, attempt):
    """Reference Axis: Cylindrical/Conical Face, One Line/Edge, Two Planes, Two Points and
    Point and Face (a planar face's normal through its centre; a circular edge's axis). A
    point + direction is an axis through two hidden helper points '<name> Points' (or the
    two standard planes when it is the X / Y / Z axis)."""
    from .core.refs import resolver
    if attempt == 0:
        require(f, ctx)
    if attempt > 0:
        return NO_MORE
    p, d = f.result_axis
    doc.ClearSelection2(True)
    face_key = None
    if f.mode == "face":
        q, info = our_face_point(ctx, f.ref)
        face = find_face(s, doc, q)
        if info.kind in ("cylinder", "cone"):
            face_key = _face_key(info)
            select(s, doc, face, 0, False)
        else:                         # planar: normal through its centre (Point and Face)
            pt = helper_ref_point(s, doc, st, 4, lambda: select(s, doc, face, 0, False), f.name, p)
            doc.ClearSelection2(True)
            select(s, doc, face, 0, False)
            select_feature(s, doc, pt, 0, True)
    elif f.mode == "edge":
        r = resolver(ctx)
        info = r.edges[r.edge(f.ref)]
        if info["kind"] == "line":
            select(s, doc, find_edge(s, doc, info), 0, False)
        else:
            select_circle_axis(s, doc, st, info, f.name)
    elif f.mode == "planes":
        for spec in (f.base, f.base2):
            if spec.get("type") == "explicit":
                select_plane(s, doc, st, spec, ctx, owner=f.name)   # helpers first
        select_plane(s, doc, st, f.base, ctx, 0, False, owner=f.name)
        select_plane(s, doc, st, f.base2, ctx, 0, True, owner=f.name)
    elif f.mode == "point_dir" and _std_line(p, d) is not None:
        ext = s.T(doc.Extension, "IModelDocExtension")
        for k, pl in enumerate(_STD_PAIRS[_std_line(p, d)]):
            if not ext.SelectByID2(pl, "PLANE", 0, 0, 0, k > 0, 0, None, 0):
                raise _not_native(f"{f.name}: {pl} could not be selected")
    else:                             # two points (point + direction: the point and 10 mm on)
        pts = f.points if f.mode == "points" else [p, np.asarray(p) + 10.0 * np.asarray(d)]
        sels = point_selectors(s, doc, st, pts, f.name)
        doc.ClearSelection2(True)
        for k, sel in enumerate(sels):
            sel(0, k > 0)
    feat = _new_axis(s, doc, f"{f.name}")
    if not same_line(*axis_line(s, feat), p, d):
        _delete(s, doc, feat)
        raise _not_native(f"{f.name}: SolidWorks placed the axis elsewhere")
    if face_key is not None:
        st.axes.setdefault(face_key, feat)          # later picks of the face use this axis
    st.axes[("feature", f.id)] = feat
    return feat


_STD_PAIRS = {"x": ("Front Plane", "Top Plane"), "y": ("Front Plane", "Right Plane"),
              "z": ("Top Plane", "Right Plane")}


def _std_line(p, d):
    """'x' / 'y' / 'z' when the line (p, d) is that global axis, else None."""
    d = _unit(d)
    for k, name in enumerate("xyz"):
        if abs(abs(d[k]) - 1) < 1e-12 and float(np.linalg.norm(np.delete(np.asarray(p, float), k))) < 1e-9:
            return name
    return None


def ref_point(s, doc, f, ctx, st, attempt):
    """Reference Point: Arc Center (circular edge), Center of Face, or a point at
    coordinates: 'On Point' of a hidden 3D-sketch point '<name> Points' (D1)."""
    from .core.refs import resolver
    if attempt == 0:
        require(f, ctx)
    if attempt > 0:
        return NO_MORE
    want = np.asarray(f.result_point, float)
    doc.ClearSelection2(True)
    if f.mode == "coords":
        kind = 7                                            # swRefPointSketchPoint
        _, pts = helper_points(s, doc, st, [want], f.name)
        doc.ClearSelection2(True)
        if not s.T(pts[0], "ISketchPoint").Select4(False, s.select_data(doc, 0)):
            raise _not_native(f"{f.name}: its helper point could not be selected")
    elif f.mode == "arc_center":
        kind = 3                                            # swRefPointCenterEdge
        r = resolver(ctx)
        select(s, doc, find_edge(s, doc, r.edges[r.edge(f.ref)]), 0, False)
    else:
        kind = 4                                            # swRefPointFaceCenter
        q, _ = our_face_point(ctx, f.ref)
        select(s, doc, find_face(s, doc, q), 0, False)
    fm = s.T(doc.FeatureManager, "IFeatureManager")
    before = last_id(s, doc)
    fm.InsertReferencePoint(kind, 0, 0.0, 1)
    feat = newest_feature(s, doc, before)
    doc.ClearSelection2(True)
    if feat is None or feat.GetTypeName2() != "RefPoint":
        raise _not_native(f"{f.name}: SolidWorks could not create the point")
    got = ref_point_xyz(s, feat)
    if np.linalg.norm(got - want) > 1e-6:
        _delete(s, doc, feat)
        raise _not_native(f"{f.name}: SolidWorks puts the point at {np.round(got, 4).tolist()}, "
                          f"BauyrCAD at {np.round(want, 4).tolist()}")
    st.ref_points.append((want, feat))
    return feat


def curve(s, doc, f, ctx, st, attempt):
    """Curve Through XYZ Points (CurveInFile) with BauyrCAD's points - SolidWorks interpolates
    them as BauyrCAD does (chord-length cubic spline, natural ends; a closed curve repeats its
    first point) - or, for polyline=True, a 3D sketch of lines through them."""
    from .core import modeling
    if attempt == 0:
        require(f, ctx)
    if attempt > 0:
        return NO_MORE
    pts = [np.asarray(p, float) for p in f.points]
    loop = pts + ([pts[0]] if f.closed else [])        # a closed curve repeats its first point
    doc.ClearSelection2(True)
    if f.polyline:
        def draw(sm):
            return [sm.CreateLine(*map(float, a * MM), *map(float, b * MM))
                    for a, b in zip(loop[:-1], loop[1:])]
        feat, segs = _sketch3d(s, doc, draw)
        if any(x is None for x in segs):
            _delete(s, doc, feat)
            raise _not_native(f"{f.name}: SolidWorks rejected a line of the polyline")
        sk = s.T(feat.GetSpecificFeature2(), "ISketch")
        got = [np.array([q.X, q.Y, q.Z]) / MM for q in
               (s.T(x, "ISketchPoint") for x in (sk.GetSketchPoints2() or ()))]
        if (len(sk.GetSketchSegments() or ()) != len(loop) - 1
                or len(got) != len(loop) - bool(f.closed)
                or any(min(np.linalg.norm(g - p) for g in got) > 1e-6 for p in pts)):
            _delete(s, doc, feat)
            raise _not_native(f"{f.name}: SolidWorks did not join the polyline's lines at "
                              "BauyrCAD's points")
        return feat                   # straight lines between the same points: the same curve
    before = last_id(s, doc)
    doc.InsertCurveFileBegin()
    for p in loop:
        doc.InsertCurveFilePoint(*map(float, p * MM))
    ok = doc.InsertCurveFileEnd()
    feat = newest_feature(s, doc, before) if ok else None
    if feat is None or feat.GetTypeName2() != "CurveInFile":
        raise _not_native(f"{f.name}: SolidWorks could not create the curve")
    data = s.T(feat.GetDefinition(), "IFreePointCurveFeatureData")
    got = np.array(data.PointArray or ()).reshape(-1, 3) / MM
    if got.shape != (len(loop), 3) or np.abs(got - np.array(loop)).max() > 1e-9:
        _delete(s, doc, feat)
        raise _not_native(f"{f.name}: SolidWorks stored other points")
    edges = [s.T(e, "IEdge") for e in
             (s.T(feat.GetSpecificFeature2(), "IReferenceCurve").GetSegments() or ())]
    # the curve itself: BauyrCAD's curve sampled must lie on SolidWorks'
    q = modeling.wire_points(f.result_wire, 24)
    chord = float(np.sum(np.linalg.norm(np.diff(q, axis=0), axis=1)))
    dev = max(min(_dist_to(e, np.asarray(p, float) * MM) for e in edges) for p in q) / MM
    if dev > 1e-3 * chord:
        _delete(s, doc, feat)
        raise _not_native(f"{f.name}: SolidWorks' curve is up to {dev:.3g} mm away from BauyrCAD's")
    if dev > 1e-6 * max(chord, 1.0):
        st.report.notes.append(f"{f.name}: SolidWorks' curve through the same points differs "
                               f"slightly from BauyrCAD's (up to {dev:.2g} mm)")
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


def curve_ends(s, feat) -> list:
    """The end points (mm) of a SolidWorks curve feature's segments (an IReferenceCurve)."""
    ends = []
    for e in s.T(feat.GetSpecificFeature2(), "IReferenceCurve").GetSegments() or ():
        cp = s.T(s.T(e, "IEdge").GetCurveParams3(), "ICurveParamData")
        ends += [np.array(cp.StartPoint, float) / MM, np.array(cp.EndPoint, float) / MM]
    return ends


def curve_deviation(s, feat, wire) -> tuple:
    """How far BauyrCAD's curve (a wire) is from a SolidWorks curve feature (an
    IReferenceCurve: helix, curve through points...): (largest distance of BauyrCAD's sampled
    points from SolidWorks' curve, largest distance between the two curves' end points, the
    curve's length), mm. The end points catch what lying on the curve can't: a helix started
    at another angle runs along the same spiral."""
    from .core import modeling
    edges = [s.T(e, "IEdge") for e in
             (s.T(feat.GetSpecificFeature2(), "IReferenceCurve").GetSegments() or ())]
    if not edges:
        return math.inf, math.inf, 0.0
    q = modeling.wire_points(wire, 24)
    chord = float(np.sum(np.linalg.norm(np.diff(q, axis=0), axis=1)))
    dev = max(min(_dist_to(e, np.asarray(p, float) * MM) for e in edges) for p in q) / MM
    ends = curve_ends(s, feat)
    ends_dev = max(min(float(np.linalg.norm(np.asarray(w) - x)) for x in ends)
                   for w in modeling.wire_ends(wire))
    return dev, ends_dev, chord


def helix(s, doc, f, ctx, st, attempt):
    """Helix / Spiral on its circle sketch; an explicit helix gets a circle sketch made for
    it ('<name> Sketch', a helper) on a plane normal to its axis. A helix has no volume, so it
    is checked here against BauyrCAD's curve (every point on SolidWorks' helix, the same end
    points): the derived Reverse Direction (SolidWorks grows it along its sketch normal) is
    tried first, then the other direction / hand / start-angle readings."""
    if attempt == 0:
        require(f, ctx)
    if attempt > 0:
        return NO_MORE
    from .core.plane import SketchPlane
    from .solidworks import _SketchMap, _plane_selection
    doc.ClearSelection2(True)
    if f.sketch:
        sk = st.features.get(f.sketch)
        if sk is None or f.sketch not in st.sketches:
            raise _not_native(f"{f.name}: its sketch was not transferred")
        m = st.sketches[f.sketch][1]
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
            st.warn(f"{name}: SolidWorks refused the helix sketch's name (it is called {sk.Name})")
        st.own(s, sk)
        st.helpers.append(sk.Name)
    pitch, turns = f.pitch_turns()
    by = {"pitch_revolutions": 0, "height_revolutions": 1, "height_pitch": 2}[f.defined_by]
    centre, axis = _helix_axis(f, ctx)
    from .core import modeling
    start = modeling.wire_ends(f.result_wire)[0]
    radial = start - centre - np.dot(start - centre, axis) * axis
    # SolidWorks grows the helix along its sketch normal unless reversed, and reads the hand
    # about that normal; its start angle has its own zero (not the sketch's x axis): it is
    # read from a first try and corrected (the sense of SolidWorks' angle is tried both ways)
    rev = bool(f.reverse) ^ (m.ccw < 0)
    got = []
    debug = os.environ.get("BAUYRCAD_SW_DEBUG")
    for r in (rev, not rev):
        for cw in (bool(f.left_hand) ^ r, not (bool(f.left_hand) ^ r)):
            angles, tried = [float(f.start_angle)], set()
            while angles:
                ang = angles.pop(0) % 360.0
                if round(ang, 9) in tried:
                    continue
                tried.add(round(ang, 9))
                doc.ClearSelection2(True)
                select_feature(s, doc, sk, 0, False)
                doc.InsertHelix(r, cw, False, False, by, pitch * turns * MM, pitch * MM,
                                float(turns), 0.0, math.radians(ang))
                feat = s.T(doc.FeatureByPositionReverse(0), "IFeature")
                if feat is None or feat.GetTypeName2() != "Helix" or feat.GetID() in st.owned:
                    raise _not_native(f"{f.name}: SolidWorks could not create the helix")
                dev, ends, chord = curve_deviation(s, feat, f.result_wire)
                if debug:
                    print(f"[sw] {f.name}: reverse={r} clockwise={cw} start={ang:g}: on-curve "
                          f"{dev:.3g} mm, ends {ends:.3g} mm (length {chord:.4g})", flush=True)
                if max(dev, ends) <= 1e-4 * chord + 1e-6:
                    doc.ClearSelection2(True)
                    return feat
                got.append(max(dev, ends))
                if len(tried) == 1:                  # where SolidWorks started: correct by it
                    p = min(curve_ends(s, feat), key=lambda q: abs(np.dot(q - start, axis)))
                    rr = p - centre - np.dot(p - centre, axis) * axis
                    d = math.degrees(math.atan2(np.dot(np.cross(rr, radial), axis),
                                                np.dot(rr, radial)))
                    angles += [ang - d, ang + d] if r else [ang + d, ang - d]
                _delete(s, doc, feat)
    raise _not_native(f"{f.name}: SolidWorks' helix does not run where BauyrCAD's does "
                      f"(off by {min(got):.3g} mm at best)")


def _helix_axis(f, ctx) -> tuple:
    """(centre, unit axis) of a BauyrCAD helix, world mm (as HelixFeature.execute)."""
    if f.sketch:
        sf = ctx.features.get(f.sketch)
        c = next(e for e in sf.sketch.curves(include_construction=True) if e.kind == "circle")
        return (np.asarray(sf.plane.to_world(*sf.sketch.pt(c.center)), float),
                np.asarray(sf.plane.normal, float))
    axis = np.asarray(f.axis, float)
    return np.asarray(f.center, float), axis / np.linalg.norm(axis)


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
            "ref_plane": ref_plane, "ref_axis": ref_axis, "ref_point": ref_point, "curve": curve,
            "linear_pattern": linear_pattern, "circular_pattern": circular_pattern,
            "mirror": mirror, "hole": hole, "sweep": sweep, "loft": loft}
