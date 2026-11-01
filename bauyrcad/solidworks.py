"""Export to SolidWorks: native .SLDPRT parts with an editable feature tree, and .SLDASM
assemblies with floating components and real mates.

The .sldprt / .sldasm formats are closed, so BauyrCAD drives the user's own SolidWorks through
its official COM API (Windows, SolidWorks installed) and has SolidWorks build the files:

* Parts are rebuilt feature by feature: sketches (lines, arcs, circles, splines, points,
  relations and driving dimensions) on the same planes, then every operation through its
  handler in solidworks_features.HANDLERS (Boss/Cut-Extrude, Revolve, fillets, holes,
  patterns...). After every feature the change of SolidWorks' volume AND of its first moment
  (where the material went) is compared with BauyrCAD's, with a tolerance relative to the
  feature's own volume change; the predicted definition is tried first, then alternatives.
  The saved file is force-rebuilt and checked for What's Wrong errors, and its final volume
  and centre of mass are compared with BauyrCAD's.
* The report separates real losses (``warnings``: a step, relation, dimension or name that
  did not transfer; blocking with require_native=True) from approximations within tolerance
  (``notes``: spline and font-outline shapes, blend surfaces), and lists the helper features made
  for SolidWorks (``helpers``) and steps that became several SolidWorks features (``steps``).
* native_export_issues() lists the known gaps without starting SolidWorks; strict exports
  (the GUI's) check it first. A part that can't be rebuilt (non-strict scripts only) is
  brought in as a solid body instead, and the report says so.
* The export works on a private copy of the Document (the GUI's document is never touched
  by the export thread) and regenerates it once (cached per step).
* New documents get MMGS units (mm, g, s; degrees) whatever the user's template says.
* Assemblies get one component per BauyrCAD component, placed exactly, fixed / floating as in
  BauyrCAD, and each mate recreated as the SolidWorks mate of the same type, so the assembly
  moves in SolidWorks as it does here.

If SolidWorks is already running, the export uses that session but only touches the
documents it creates (and puts every preference it changes back); otherwise it starts a
private, hidden instance and closes it at the end.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MM = 1e-3                                   # BauyrCAD mm -> SolidWorks metres

# SolidWorks enum values (swconst)
_PREF_TOGGLES = {10: False,                 # swInputDimValOnCreate: no value popup per dim
                 249: False,                # swSketchInference
                 9: False,                  # swSketchAutomaticRelations
                 18: False,                 # swExtRefUpdateCompNames: component names are ours
                 100: False,                # swSketchOverdefiningDimsPromptToSetState: no prompt,
                 101: True}                 # ...SetDrivenByDefault: an over-defining dim is driven
# document units (IModelDocExtension.SetUserPreferenceInteger, swDetailingNoOptionSpecified):
# swUnitsLinear = mm, swUnitsAngular = degrees, decimal display, 2 / 2 decimals, and LAST
# swUnitSystem = MMGS (changing a single unit afterwards would turn the system into Custom)
_UNITS = ((47, 0), (51, 0), (48, 1), (49, 2), (52, 2), (263, 5))
_TPL_PART, _TPL_ASM = 8, 9
_CO_E_SERVER_EXEC_FAILURE = -2146959355     # SolidWorks did not register in time (a startup dialog)
_MATE = {"coincident": 0, "concentric": 1, "perpendicular": 2, "parallel": 3,
         "distance": 5, "angle": 6, "lock": 16}
_ALIGNED, _ANTI, _CLOSEST = 0, 1, 2
_DIM_LINEAR, _DIM_ANGULAR, _DIM_RADIAL, _DIM_DIAMETER, _DIM_H, _DIM_V = 2, 3, 5, 6, 11, 12
_REL = {"coincident": "sgCOINCIDENT", "midpoint": "sgATMIDDLE", "parallel": "sgPARALLEL",
        "perpendicular": "sgPERPENDICULAR", "collinear": "sgCOLINEAR", "equal": "sgSAMELENGTH",
        "concentric": "sgCONCENTRIC", "tangent": "sgTANGENT", "fix": "sgFIXED",
        "symmetric": "sgSYMMETRIC"}
# swConstraintType_e each relation call must add (CORADIAL 28: SolidWorks' concentric + equal
# arcs)
_REL_TYPES = {"sgCOINCIDENT": (9,), "sgATMIDDLE": (12,), "sgPARALLEL": (7,),
              "sgPERPENDICULAR": (8,), "sgCOLINEAR": (27,), "sgSAMELENGTH": (14, 28),
              "sgCONCENTRIC": (10, 28), "sgTANGENT": (6,), "sgFIXED": (17,), "sgSYMMETRIC": (11,),
              "sgHORIZONTAL2D": (4,), "sgVERTICAL2D": (5,), "sgHORIZONTALPOINTS2D": (25,),
              "sgVERTICALPOINTS2D": (26,)}
_DIM_TYPE_NAMES = {2: "aligned", 3: "angular", 5: "radial", 6: "diameter", 11: "horizontal",
                   12: "vertical"}


class SolidWorksError(Exception):
    pass


def _doubles(values):
    """A SAFEARRAY of doubles (SolidWorks misreads a plain Python list: VARIANT of VARIANTs)."""
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in values])


class _NotNative(Exception):
    """This part can't be rebuilt feature by feature (non-strict exports fall back to a solid
    body; strict ones fail)."""


# =====================================================================================
# finding and running SolidWorks
# =====================================================================================
def install_dir() -> Path | None:
    """SolidWorks install folder (from the registered COM server), or None."""
    try:
        import winreg
        clsid = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, r"SldWorks.Application\CLSID")
        exe = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32")
    except (OSError, ImportError):
        return None
    exe = exe.strip().strip('"').split('"')[0].split(" /")[0]
    p = Path(exe).parent
    return p if (p / "sldworks.tlb").exists() else None


def available() -> bool:
    """True when SolidWorks and the Python COM bridge (pywin32) are installed."""
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    return install_dir() is not None


def _sw_pids():
    """Process ids of running SolidWorks instances, or None when they cannot be listed
    (tasklist failed or timed out): callers must then assume the user's SolidWorks may be
    running (fail closed)."""
    try:
        import subprocess
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq SLDWORKS.exe", "/NH", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        return {int(line.split('","')[1]) for line in r.stdout.splitlines()
                if line.startswith('"SLDWORKS.exe"')}
    except Exception:  # noqa: BLE001
        return None


def _running() -> bool:
    """True when SolidWorks runs, or when that cannot be determined."""
    pids = _sw_pids()
    return pids is None or bool(pids)


def _owns_instance(before, after) -> tuple:
    """(own, new pids) after DispatchEx: the instance is ours only when both process probes
    worked and either a new SLDWORKS.exe appeared or none ran before. A failed probe means
    the user's SolidWorks may be the one we attached to: never hide, close or exit it."""
    if before is None or after is None:
        return False, set()
    new = after - before
    return bool(new) or not before, new


def _writable_gen_py():
    """In the packaged app the win32com cache lives inside the bundle (read-only): generate
    SolidWorks' bindings into the user's local app data instead (first export only)."""
    import sys
    if not getattr(sys, "frozen", False):
        return
    import win32com
    from win32com.client import gencache
    d = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "BauyrCAD" / "gen_py"
    d.mkdir(parents=True, exist_ok=True)
    win32com.__gen_path__ = str(d)
    gencache.is_readonly = False
    try:
        gencache.GetGeneratePath()
    except Exception:  # noqa: BLE001
        pass


_COM = threading.local()


def _com_enter():
    """COM for this thread, once for nested sessions: an inner session's CoUninitialize
    would disconnect every SolidWorks object of the outer one."""
    depth = getattr(_COM, "depth", 0)
    if depth == 0:
        import pythoncom
        pythoncom.CoInitialize()
    _COM.depth = depth + 1


def _com_exit():
    _COM.depth = max(getattr(_COM, "depth", 1) - 1, 0)
    if _COM.depth == 0:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


class Session:
    """A SolidWorks session for exporting (context manager). Sessions may nest in one
    thread (an inner one attaches to the same SolidWorks)."""

    def __init__(self, visible=False):
        self.visible = visible
        self.sw = None
        self._raw = None
        self._own = False
        self._prefs = {}
        self._docs = []
        self._com = False

    # ---- start / stop --------------------------------------------------------------------
    def __enter__(self):
        if not available():
            raise SolidWorksError("SolidWorks (and pywin32) must be installed on this PC to "
                                  "export .SLDPRT / .SLDASM")
        import pythoncom
        import win32com.client
        from win32com.client import gencache
        _com_enter()
        self._com = True
        self._pids = set()
        try:
            _writable_gen_py()
            tlb = install_dir() / "sldworks.tlb"
            a = pythoncom.LoadTypeLib(str(tlb)).GetLibAttr()
            self._mod = gencache.EnsureModule(str(a[0]), a[1], a[3], a[4])
            before = _sw_pids()
            try:
                self._raw = win32com.client.DispatchEx("SldWorks.Application")
            except pythoncom.com_error as e:
                if e.hresult == _CO_E_SERVER_EXEC_FAILURE:
                    raise SolidWorksError(
                        "SolidWorks did not finish starting (no answer within the COM time "
                        "limit). This happens when SolidWorks shows a message at startup, e.g. "
                        "about toolbar settings or the licence: start SolidWorks once by hand, "
                        "close the message, exit SolidWorks and export again.") from e
                raise
            self._own, self._pids = _owns_instance(before, _sw_pids())
            self.sw = self.T(self._raw, "ISldWorks")
            if self._own:
                self.sw.Visible = self.visible
            for k, v in _PREF_TOGGLES.items():
                old = bool(self.sw.GetUserPreferenceToggle(k))
                if old != v:                           # remembered before it is changed
                    self._prefs[k] = old
                    self.sw.SetUserPreferenceToggle(k, v)
            self.math = self.T(self.sw.GetMathUtility(), "IMathUtility")
        except BaseException:
            import sys
            self.__exit__(*sys.exc_info())             # puts back what was changed so far
            raise
        return self

    def __exit__(self, *exc):
        import gc
        try:
            for doc in self._docs:
                try:
                    self.sw.CloseDoc(doc.GetTitle())
                except Exception:  # noqa: BLE001
                    pass
            if self._own and self.sw is not None:
                # our private instance only: a document a failed feature call left behind
                # (no title) would keep the hidden SolidWorks from exiting
                try:
                    self.sw.CloseAllDocuments(True)
                except Exception:  # noqa: BLE001
                    pass
            for k, v in reversed(list(self._prefs.items())):
                try:
                    self.sw.SetUserPreferenceToggle(k, v)
                except Exception:  # noqa: BLE001 - restore the others anyway
                    pass
            self._prefs = {}
        finally:
            # release every COM object while SolidWorks is still there (releasing proxies of
            # a server that has exited raises RPC_S_SERVER_UNAVAILABLE)
            self.sw = self.math = None
            self._docs = []
            gc.collect()
            raw, self._raw = self._raw, None
            if self._own and raw is not None:
                try:
                    raw.ExitApp()
                except Exception:  # noqa: BLE001
                    pass
            del raw
            gc.collect()
            if self._own:
                for _ in range(60):                     # ExitApp returns before it is gone
                    if not ((_sw_pids() or set()) & self._pids):
                        break
                    time.sleep(0.5)
            if self._com:
                self._com = False
                _com_exit()
        return False

    # ---- helpers -------------------------------------------------------------------------
    def T(self, obj, iface):
        """Cast a returned COM object to its SolidWorks interface (typed calls)."""
        if obj is None:
            return None
        return getattr(self._mod, iface)(obj._oleobj_ if hasattr(obj, "_oleobj_") else obj)

    def new(self, kind: str):
        tpl = self.sw.GetUserPreferenceStringValue(_TPL_PART if kind == "part" else _TPL_ASM)
        d = self.sw.NewDocument(tpl, 0, 0, 0)
        if d is None:
            raise SolidWorksError(f"SolidWorks could not create a new {kind} (template {tpl})")
        doc = self.T(d, "IModelDoc2")
        self._docs.append(doc)
        self.set_units(doc)
        return doc

    def set_units(self, doc):
        """MMGS document units (mm, g, s; degrees), whatever the template says."""
        ext = self.T(doc.Extension, "IModelDocExtension")
        for pref, value in _UNITS:
            ext.SetUserPreferenceInteger(pref, 0, value)
        got = {pref: ext.GetUserPreferenceInteger(pref, 0) for pref, _ in _UNITS}
        bad = {p: v for p, v in _UNITS if got[p] != v}
        if bad:
            raise SolidWorksError(f"SolidWorks did not accept MMGS document units ({got})")

    def save(self, doc, path):
        path = str(Path(path).resolve())
        ext = self.T(doc.Extension, "IModelDocExtension")
        r = ext.SaveAs(path, 0, 1, None, 0, 0)
        ok = r[0] if isinstance(r, tuple) else r
        if not ok or not os.path.exists(path):
            raise SolidWorksError(f"SolidWorks could not save {path} (errors {r})")
        return path

    def close(self, doc):
        try:
            self.sw.CloseDoc(doc.GetTitle())
        except Exception:  # noqa: BLE001 - already closed with the assembly that used it
            pass
        finally:
            if doc in self._docs:
                self._docs.remove(doc)

    def open_step(self, step_path):
        r = self.sw.LoadFile4(str(step_path), "r", None, 0)
        d = r[0] if isinstance(r, tuple) else r
        if d is None:
            raise SolidWorksError(f"SolidWorks could not import {step_path}")
        doc = self.T(d, "IModelDoc2")
        self._docs.append(doc)
        self.set_units(doc)
        return doc

    def select_data(self, doc, mark=0):
        sm = self.T(doc.SelectionManager, "ISelectionMgr")
        sd = self.T(sm.CreateSelectData(), "ISelectData")
        sd.Mark = mark
        return sd

    def transform(self, R, t_m):
        """IMathTransform from a rotation (columns = local axes in world) + translation."""
        data = list(np.asarray(R, float).T.ravel()) + list(t_m) + [1.0, 0.0, 0.0, 0.0]
        return self.T(self.math.CreateTransform(_doubles(data)), "IMathTransform")


# =====================================================================================
# parts
# =====================================================================================
@dataclass
class PartReport:
    path: str
    native: bool = True
    features: list = field(default_factory=list)   # BauyrCAD steps rebuilt, in order
    warnings: list = field(default_factory=list)   # real losses (block require_native)
    volume: float = 0.0            # mm3, SolidWorks
    notes: list = field(default_factory=list)      # approximations within tolerance (D3)
    helpers: list = field(default_factory=list)    # hidden helper features made (D1)
    steps: dict = field(default_factory=dict)      # step -> its SolidWorks features (D6)
    attempts: dict = field(default_factory=dict)   # step -> handler attempt kept (0 = predicted)


def _mass(s, doc) -> tuple:
    """(volume mm³, centre of mass mm) of a part's solid bodies, at SolidWorks' Higher mass
    property accuracy (swMassPropertyAccuracyLevel_Higher). The default accuracy is off by up
    to ~3e-7 of the whole body on curved faces cut at an angle or splines; Higher agrees with
    OpenCASCADE to ~1e-9, which lets the per-step check stay relative to the step itself.
    Clears the selection (IMassProperty2 measures the selected bodies, else all of them)."""
    ext = s.T(doc.Extension, "IModelDocExtension")
    doc.ClearSelection2(True)
    mp = s.T(ext.CreateMassProperty2(), "IMassProperty2")
    if mp is None:                                  # no solid body
        return 0.0, np.zeros(3)
    mp.UseSystemUnits = True
    mp.AccuracyLevel = 2                            # swMassPropertyAccuracyLevel_Higher
    mp.Recalculate()
    return mp.Volume / MM ** 3, np.array(mp.CenterOfMass) / MM


def _volume(s, doc) -> float:
    return _mass(s, doc)[0]


def _center(s, doc) -> np.ndarray:
    return _mass(s, doc)[1]


def _close_enough(a, b, rel):
    return abs(a - b) <= rel * max(abs(b), 1.0) + 1e-3


class _SketchMap:
    """World <-> SolidWorks sketch coordinates, and our ids -> SolidWorks entities:
    segs (entity id -> ISketchSegment), pts (point id -> ISketchPoint), pent (point entity id
    -> its ISketchPoint)."""

    def __init__(self, s, sk_sw, plane):
        self.s, self.plane = s, plane
        xf = s.T(sk_sw.ModelToSketchTransform, "IMathTransform")
        self.xf = xf
        # affine map from 4 transformed points (convention-proof)
        o = self._apply(np.zeros(3))
        self.A = np.column_stack([self._apply(e) - o for e in np.eye(3)])
        self.o = o
        self.segs, self.pts, self.pent = {}, {}, {}
        self.origin = None          # ISketchPoint standing for our point 0 (None: the model origin)
        self.tied = None            # the curve point on our point 0, tied to SolidWorks' origin
        z = self.A @ plane.normal
        self.ccw = 1 if z[2] >= 0 else -1          # arcs: our CCW in SolidWorks' sketch frame
        u, v = self.A @ plane.xdir, self.A @ plane.ydir
        self.phi = math.atan2(u[1], u[0])           # our u axis in SolidWorks' sketch frame
        self.hv = None                              # our H/V -> SolidWorks H/V
        if abs(abs(u[0]) - 1) < 1e-9 and abs(abs(v[1]) - 1) < 1e-9:
            self.hv = {"h": "h", "v": "v"}
        elif abs(abs(u[1]) - 1) < 1e-9 and abs(abs(v[0]) - 1) < 1e-9:
            self.hv = {"h": "v", "v": "h"}

    def direction(self, angle_deg):
        """SolidWorks sketch direction (2D unit) of the in-plane direction at angle_deg from
        our u axis (towards v)."""
        a = math.radians(angle_deg)
        d = self.A @ (math.cos(a) * self.plane.xdir + math.sin(a) * self.plane.ydir)
        return d[:2] / np.linalg.norm(d[:2])

    def _apply(self, p_m):
        mp = self.s.T(self.s.math.CreatePoint(_doubles(p_m)), "IMathPoint")
        return np.array(self.s.T(mp.MultiplyTransform(self.xf), "IMathPoint").ArrayData)

    def sk(self, uv):
        """Our sketch (u, v) mm -> SolidWorks sketch coordinates (m)."""
        w = self.plane.to_world(*uv) * MM
        q = self.o + self.A @ w
        if abs(q[2]) > 1e-9:
            raise _NotNative("the sketch plane does not match")
        return q

    def world_m(self, uv):
        return self.plane.to_world(*uv) * MM


_STD_PLANES = {"front": "Front Plane", "top": "Top Plane", "right": "Right Plane"}


def _plane_selection(s, doc, plane, st, owner):
    """Select a plane BauyrCAD holds as fixed numbers (an explicit sketch plane): a standard
    plane or a reference plane made earlier in the same place, else (D1) a hidden helper
    plane '<owner> Plane' (an offset of a standard plane, or a plane normal to a hidden
    3D-sketch line for any other orientation). Such a plane does not follow model faces, as
    in BauyrCAD."""
    from . import solidworks_features as swf
    doc.ClearSelection2(True)
    feat = swf.helper_plane(s, doc, st, plane, owner)
    doc.ClearSelection2(True)
    if isinstance(feat, tuple):                      # ("std", name): a standard plane itself
        ext = s.T(doc.Extension, "IModelDocExtension")
        if not ext.SelectByID2(feat[1], "PLANE", 0, 0, 0, False, 0, None, 0):
            raise _NotNative(f"{owner}: {feat[1]} could not be selected")
        return
    if not s.T(feat, "IFeature").Select2(False, 0):
        raise _NotNative(f"{owner}: its sketch plane could not be selected")


def _select_sketch_plane(s, doc, sf, st, ctx, report):
    """Select what the sketch references in BauyrCAD (its plane_spec), never a coplanar
    stand-in: a standard plane by name, the reference plane feature itself, or the model face
    (found through a point inside BauyrCAD's face, so a face away from the origin or split by
    later features is found too). Explicit planes (no spec) use _plane_selection. A face
    reference that no longer resolves (BauyrCAD kept the sketch where it was) puts the sketch
    on a helper plane at that place, and is a blocking warning."""
    from . import solidworks_features as swf
    spec = sf.plane_spec or {}
    t = spec.get("type")
    ext = s.T(doc.Extension, "IModelDocExtension")
    doc.ClearSelection2(True)
    if t == "std":
        name = _STD_PLANES.get(str(spec["name"]).lower(), spec["name"])
        if not ext.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, None, 0):
            raise _NotNative(f"{sf.name}: {name} could not be selected")
        return
    if t == "feature":
        feat = st.features.get(spec["id"])
        if feat is None:
            raise _NotNative(f"{sf.name}: its reference plane was not transferred")
        if not s.T(feat, "IFeature").Select2(False, 0):
            raise _NotNative(f"{sf.name}: its reference plane could not be selected")
        return
    if t == "face" and not sf.warning:
        p, _ = swf.our_face_point(ctx, spec["ref"])
        face = swf.find_face(s, doc, p)
        _check_face_plane(s, face, sf)
        if not s.T(face, "IEntity").Select4(False, None):
            raise _NotNative(f"{sf.name}: its face could not be selected")
        return
    if t == "face":
        report.warnings.append(f"{sf.name}: {sf.warning}; in SolidWorks it sits on a fixed "
                               "helper plane there instead of on a face")
    _plane_selection(s, doc, sf.plane, st, sf.name)


def _check_face_plane(s, face, sf):
    surf = s.T(s.T(face, "IFace2").GetSurface(), "ISurface")
    if surf is None or not surf.IsPlane():
        raise _NotNative(f"{sf.name}: its face is not planar in SolidWorks")
    prm = np.array(surf.PlaneParams)
    n, root = prm[:3], prm[3:6] / MM
    if not (abs(abs(float(n @ sf.plane.normal)) - 1) < 1e-7
            and abs(float((root - sf.plane.origin) @ sf.plane.normal)) < 1e-6):
        raise _NotNative(f"{sf.name}: the face found in SolidWorks is not the sketch's plane")


def _needs_turning(sk, m) -> bool:
    """Must SolidWorks' sketch axes be turned onto ours? Horizontal / vertical relations and
    dimensions need our u along SolidWorks' X or Y (it is on inclined faces and explicit
    planes too, mostly), and sketch text runs along SolidWorks' X (otherwise it needs a
    construction line to run along)."""
    if sk.texts and abs(m.phi) > 1e-9:
        return True
    return m.hv is None and any(c.type in ("horizontal", "vertical", "hdistance", "vdistance")
                                for c in sk.constraints)


def _uses_origin(sk) -> bool:
    return (any(0 in e.point_ids() for e in sk.entities.values())
            or any(("p", 0) in c.refs for c in sk.constraints))


def _origin_elsewhere(plane) -> bool:
    """Is BauyrCAD's sketch origin (point 0) somewhere other than the model origin projected
    onto the plane (SolidWorks' sketch origin, Point1@Origin)? Only on explicit planes."""
    o, n = plane.origin, plane.normal
    return float(np.linalg.norm(o - (o @ n) * n)) > 1e-9


def _origin_point(s, doc, sk, sf, st):
    """D1: when the sketch refers to its origin point and that is not where SolidWorks'
    origin projects, relations / dimensions to it go to a hidden helper point there
    ('<sketch> Points', a 3D-sketch point made before the sketch); else None."""
    from . import solidworks_features as swf
    if not (_uses_origin(sk) and _origin_elsewhere(sf.plane)):
        return None
    _, pts = swf.helper_points(s, doc, st, [sf.plane.origin], sf.name)
    return pts[0]


def _align_frame(s, doc, sm, sk, sf, m):
    """Modify Sketch (Translate, Rotate) SolidWorks' sketch coordinate system onto BauyrCAD's
    where it matters: the origin, when the sketch uses its origin point and SolidWorks put
    the sketch origin elsewhere (an explicit plane whose origin is not the projection of the
    model origin), and the axes (_needs_turning). An empty sketch can't be modified: a
    temporary point is drawn first and deleted again."""
    def origin_off(mm):
        return float(np.linalg.norm(mm.sk((0.0, 0.0))[:2]))
    move = _uses_origin(sk) and origin_off(m) > 1e-9
    if not (move or _needs_turning(sk, m)):
        return m
    ext = s.T(doc.Extension, "IModelDocExtension")
    sm.AddToDB = True
    try:
        tmp = s.T(sm.CreatePoint(0.0, 0.0, 0.0), "ISketchPoint")
    finally:
        sm.AddToDB = False
    if tmp is None:
        raise _NotNative(f"{sf.name}: SolidWorks' sketch axes could not be modified")

    def remap():
        return _SketchMap(s, s.T(doc.GetActiveSketch2(), "ISketch"), sf.plane)
    if move:
        q = m.sk((0.0, 0.0))
        doc.ClearSelection2(True)
        doc.SketchModifyTranslate(0.0, 0.0, float(q[0]), float(q[1]))
        m = remap()
        if origin_off(m) > 1e-9:
            raise _NotNative(f"{sf.name}: SolidWorks' sketch origin could not be moved onto "
                             "BauyrCAD's (relations to the origin would change meaning)")
    if _needs_turning(sk, m):
        doc.ClearSelection2(True)
        doc.SketchModifyRotate(0.0, 0.0, m.phi)
        m = remap()
        if abs(m.phi) > 1e-9 or m.hv != {"h": "h", "v": "v"}:
            raise _NotNative(f"{sf.name}: SolidWorks' sketch axes could not be turned onto "
                             "BauyrCAD's (horizontal / vertical would change meaning)")
    doc.ClearSelection2(True)
    if not (tmp.Select4(False, None) and ext.DeleteSelection2(0)):
        raise _NotNative(f"{sf.name}: a temporary sketch point could not be deleted")
    doc.ClearSelection2(True)
    return m


def _draw(s, doc, sm, sk, m, sf):
    """Every BauyrCAD entity as the SolidWorks entity of the same kind, construction flag
    included. Splines go through BauyrCAD's own points (D8: they stay editable spline
    points). SolidWorks interpolates them as BauyrCAD does: chord-length parameters,
    not-a-knot ends (natural ends for 3 points, SimulateNaturalEnds; its knots are BauyrCAD's).
    That is BauyrCAD's curve exactly for 3 and 4 points and within ~1e-5 of the feature's
    volume for more (a report note)."""
    from .core.sketch import Arc, Circle, Line, PointEntity, Spline
    sm.AddToDB = True
    try:
        for e in sk.entities.values():
            if isinstance(e, PointEntity):
                q = m.sk(sk.pt(e.p))
                pt = s.T(sm.CreatePoint(q[0], q[1], 0), "ISketchPoint")
                if pt is None:
                    raise _NotNative(f"{sf.name}: SolidWorks rejected a point")
                m.pent[e.id] = pt
                continue
            if isinstance(e, Line):
                a, b = m.sk(sk.pt(e.p1)), m.sk(sk.pt(e.p2))
                seg = sm.CreateLine(a[0], a[1], 0, b[0], b[1], 0)
            elif isinstance(e, Circle):
                c = m.sk(sk.pt(e.center))
                seg = sm.CreateCircleByRadius(c[0], c[1], 0, sk.radius(e) * MM)
            elif isinstance(e, Arc):
                c, a, b = (m.sk(sk.pt(p)) for p in (e.center, e.start, e.end))
                seg = sm.CreateArc(c[0], c[1], 0, a[0], a[1], 0, b[0], b[1], 0, m.ccw)
            elif isinstance(e, Spline):
                flat = []
                for pid in e.pts:
                    q = m.sk(sk.pt(pid))
                    flat += [q[0], q[1], 0.0]
                seg = sm.CreateSpline2(_doubles(flat), len(e.pts) == 3)
            else:
                raise _NotNative(f"{sf.name}: a {e.kind} can't be drawn in SolidWorks")
            if seg is None:
                raise _NotNative(f"{sf.name}: SolidWorks rejected {e.label.lower()} {e.id}")
            seg = s.T(seg, "ISketchSegment")
            if e.construction:
                seg.ConstructionGeometry = True
            m.segs[e.id] = seg
    finally:
        sm.AddToDB = False


_ROLE_TOL = 1e-8          # m: a SolidWorks point is where ours is


def _role_points(s, sk, e, m, sf) -> list:
    """[(our point id, ISketchPoint)] of one drawn curve, by role: line ends, centre, arc
    start / end (matched by position: SolidWorks may store a clockwise arc reversed) and
    every spline point in order."""
    from .core.sketch import Arc, Circle, Line, Spline
    seg = m.segs[e.id]

    def P(x):
        return s.T(x, "ISketchPoint")
    if isinstance(e, Line):
        ln = s.T(seg, "ISketchLine")
        out = [(e.p1, P(ln.GetStartPoint2())), (e.p2, P(ln.GetEndPoint2()))]
    elif isinstance(e, Circle):
        out = [(e.center, P(s.T(seg, "ISketchArc").GetCenterPoint2()))]
    elif isinstance(e, Arc):
        arc = s.T(seg, "ISketchArc")
        a, b = P(arc.GetStartPoint2()), P(arc.GetEndPoint2())
        q = m.sk(sk.pt(e.start))[:2]
        if np.linalg.norm([a.X - q[0], a.Y - q[1]]) > np.linalg.norm([b.X - q[0], b.Y - q[1]]):
            a, b = b, a
        out = [(e.center, P(arc.GetCenterPoint2())), (e.start, a), (e.end, b)]
    elif isinstance(e, Spline):
        pts = [P(x) for x in (s.T(seg, "ISketchSpline").GetPoints2() or ())]
        if len(pts) != len(e.pts):
            raise _NotNative(f"{sf.name}: SolidWorks' spline has {len(pts)} points, BauyrCAD's "
                             f"{len(e.pts)}")
        out = list(zip(e.pts, pts))
    else:
        return []
    for pid, p in out:
        q = m.sk(sk.pt(pid))[:2]
        if p is None or np.linalg.norm([p.X - q[0], p.Y - q[1]]) > _ROLE_TOL:
            raise _NotNative(f"{sf.name}: SolidWorks placed a point of {e.label.lower()} "
                             f"{e.id} elsewhere")
    return out


def _map_points(s, doc, sk, m, sf, report):
    """Our point ids -> SolidWorks sketch points, from the entities' roles. A point BauyrCAD
    shares between curves (connected ends, an arc centre on a line end, offset arcs on one
    centre) must be ONE SolidWorks point: SolidWorks joins touching ends itself, anything it
    left apart is merged (sgMERGEPOINTS). A Point entity on a curve's point (the GUI's
    Point tool snapped to an end or centre) is tied to it with a checked Coincident:
    SolidWorks keeps the two points, so the Point entity count still matches."""
    from .core.sketch import PointEntity

    def curve_points():
        got = {}
        for e in sk.entities.values():
            if not isinstance(e, PointEntity):
                for pid, p in _role_points(s, sk, e, m, sf):
                    got.setdefault(pid, {})[p.GetID()] = p
        return got
    got = curve_points()
    split = [pts for pts in got.values() if len(pts) > 1]
    for pts in split:
        doc.ClearSelection2(True)
        for p in pts.values():
            p.Select4(True, None)
        doc.SketchAddConstraints("sgMERGEPOINTS")
    doc.ClearSelection2(True)
    if split:
        got = curve_points()
        left = sum(1 for pts in got.values() if len(pts) > 1)
        if left:
            raise _NotNative(f"{sf.name}: SolidWorks did not merge {left} shared point(s)")
    m.pts = {pid: next(iter(pts.values())) for pid, pts in got.items()}
    for e in sk.entities.values():
        if isinstance(e, PointEntity):
            pt = m.pent[e.id]
            on = m.pts.get(e.p)
            if on is None:
                m.pts[e.p] = pt
            elif on.GetID() != pt.GetID():
                # SolidWorks keeps both points (the Point entity stays a user point) and
                # adds the Coincident; it must really be there, or the Point would be free
                before_rel = _sketch_relations(s, doc)
                doc.ClearSelection2(True)
                if not (pt.Select4(True, None) and on.Select4(True, None)):
                    raise _NotNative(f"{sf.name}: a Point entity on a curve's point could "
                                     "not be selected")
                doc.SketchAddConstraints("sgCOINCIDENT")
                doc.ClearSelection2(True)
                if not (_sketch_relations(s, doc) - before_rel)[9]:
                    raise _NotNative(f"{sf.name}: SolidWorks did not tie a Point entity to "
                                     "the curve point it sits on")
    # different BauyrCAD points SolidWorks joined because they lie on top of each other
    related = {frozenset(i for _, i in c.refs) for c in sk.constraints
               if c.type == "coincident" and all(k == "p" for k, _ in c.refs)}
    related |= {frozenset(sk.entities[i].center for _, i in c.refs) for c in sk.constraints
                if c.type == "concentric" and all(i in sk.entities for _, i in c.refs)}
    owners = {}
    for pid, p in m.pts.items():
        owners.setdefault(p.GetID(), []).append(pid)
    joined = [pids for pids in owners.values()
              if len(pids) > 1 and not all(frozenset(pr) in related for pr in _pairs(pids))]
    if joined:
        report.notes.append(f"{sf.name}: SolidWorks joined {len(joined)} set(s) of BauyrCAD "
                            "points that only lie on top of each other")


def _pairs(items):
    return [(a, b) for k, a in enumerate(items) for b in items[k + 1:]]


def _check_parity(s, sws, sk, m, sf):
    """Before the sketch is closed: SolidWorks holds exactly BauyrCAD's entities (segments
    by swSketchSegments_e type and construction state, sketch points, Point entities)."""
    from .core.sketch import Arc, Circle, Line, PointEntity, Spline
    want, got = {}, {}
    for e in sk.entities.values():
        kind = {Line: "line", Circle: "arc", Arc: "arc", Spline: "spline"}.get(type(e))
        if kind is not None:
            key = (kind, bool(e.construction))
            want[key] = want.get(key, 0) + 1
    names = {0: "line", 1: "arc", 2: "ellipse", 3: "spline", 4: "text", 5: "parabola"}
    for x in sws.GetSketchSegments() or ():
        seg = s.T(x, "ISketchSegment")
        key = (names.get(seg.GetType(), str(seg.GetType())), bool(seg.ConstructionGeometry))
        got[key] = got.get(key, 0) + 1
    if got != want:
        raise _NotNative(f"{sf.name}: SolidWorks' sketch holds {_counts(got)}, BauyrCAD's "
                         f"{_counts(want)}")
    n_pts = len({p.GetID() for p in m.pts.values()} | {p.GetID() for p in m.pent.values()})
    sw_pts = len(sws.GetSketchPoints2() or ())
    if sw_pts != n_pts:
        raise _NotNative(f"{sf.name}: SolidWorks' sketch has {sw_pts} points, BauyrCAD's "
                         f"{n_pts}")
    n_user = sum(1 for e in sk.entities.values() if isinstance(e, PointEntity))
    if (sws.GetUserPointsCount() or 0) != n_user:
        raise _NotNative(f"{sf.name}: SolidWorks' sketch has {sws.GetUserPointsCount()} Point "
                         f"entities, BauyrCAD's {n_user}")


def _counts(d) -> str:
    return ", ".join(f"{n} {'construction ' if c else ''}{k}(s)" for (k, c), n in sorted(d.items())) or "nothing"


def _add_texts(s, doc, sm, sk, m, sf, report):
    """Sketch text as editable SolidWorks Sketch Text (D7): the text, font, bold / italic and
    BauyrCAD's capital height (SolidWorks' character height is the capital height too), at
    BauyrCAD's baseline start. Text at an angle (or on a mirrored sketch frame) runs along a
    construction line from that point, which is how SolidWorks angles sketch text."""
    for t in sk.texts:
        q = m.sk(t["at"])
        d = m.direction(t.get("angle", 0.0))
        up = m.direction(t.get("angle", 0.0) + 90.0)
        flip = float(d[0] * up[1] - d[1] * up[0]) < 0          # the frame is mirrored
        along = flip or abs(d[0] - 1) > 1e-9
        doc.ClearSelection2(True)
        if along:
            length = (1.5 * len(t["text"]) + 2.0) * t["height"] * MM   # longer than the text
            sm.AddToDB = True
            try:
                ln = sm.CreateLine(q[0], q[1], 0, q[0] + d[0] * length, q[1] + d[1] * length, 0)
            finally:
                sm.AddToDB = False
            if ln is None:
                raise _NotNative(f"{sf.name}: the line for text '{t['text']}' was refused")
            ln = s.T(ln, "ISketchSegment")
            ln.ConstructionGeometry = True
            if not ln.Select4(False, s.select_data(doc, 1)):
                raise _NotNative(f"{sf.name}: the line for text '{t['text']}' could not be selected")
        txt = doc.InsertSketchText(q[0], q[1], 0.0, t["text"], 0, 1 if flip else 0, 0, 100, 100)
        doc.ClearSelection2(True)
        txt = s.T(txt, "ISketchText")
        if txt is None:
            raise _NotNative(f"{sf.name}: SolidWorks refused the text '{t['text']}'")
        fmt = s.T(txt.GetTextFormat(), "ITextFormat")
        fmt.TypeFaceName = t["font"]
        fmt.Bold, fmt.Italic = bool(t["bold"]), bool(t["italic"])
        fmt.CharHeight = t["height"] * MM
        fmt.Escapement = 0.0                       # (that would turn every letter instead)
        if not txt.SetTextFormat(False, fmt):
            raise _NotNative(f"{sf.name}: the format of text '{t['text']}' was refused")
        got = s.T(txt.GetTextFormat(), "ITextFormat")
        if (txt.Text != t["text"] or got.TypeFaceName != t["font"] or bool(got.Bold) != t["bold"]
                or bool(got.Italic) != t["italic"]
                or abs(got.CharHeight - t["height"] * MM) > 1e-9):
            raise _NotNative(f"{sf.name}: SolidWorks changed text '{t['text']}' (font "
                             f"{got.TypeFaceName}, height {got.CharHeight / MM:.4g} mm)")
        if along:
            report.notes.append(f"{sf.name}: text '{t['text']}' runs along a construction line "
                                "(SolidWorks' way to angle sketch text)")


def _empty_sketch(s, doc, sm, sf, before):
    """An empty sketch, as in BauyrCAD. SolidWorks drops a sketch that is left empty when the
    exit rebuilds the part (InsertSketch(True)); exiting without that rebuild keeps it (later
    rebuilds and the saved file keep it too)."""
    from . import solidworks_features as swf
    sm.InsertSketch(False)
    feat = swf.newest_feature(s, doc, before)
    if feat is None or feat.GetTypeName2() != "ProfileFeature" or doc.GetActiveSketch2() is not None:
        raise _NotNative(f"{sf.name}: SolidWorks did not keep the empty sketch")
    skt = s.T(feat.GetSpecificFeature2(), "ISketch")
    if skt.GetSketchPoints2() or skt.GetSketchSegments():
        raise _NotNative(f"{sf.name}: SolidWorks did not keep the sketch empty")
    return feat


def _build_sketch(s, doc, sf, report, st, ctx=None):
    """One BauyrCAD sketch as a SolidWorks sketch (ProfileFeature) on what it references,
    with the same entities, shared points, relations, dimensions and text; returns
    (IFeature, _SketchMap). ctx: the regeneration context before the sketch (face lookup)."""
    from . import solidworks_features as swf
    sk = sf.sketch
    swf.require(sf)
    origin = _origin_point(s, doc, sk, sf, st)
    _select_sketch_plane(s, doc, sf, st, ctx, report)
    sm = s.T(doc.SketchManager, "ISketchManager")
    before = swf.last_id(s, doc)
    sm.InsertSketch(True)
    sws = s.T(doc.GetActiveSketch2(), "ISketch")
    if sws is None:
        raise _NotNative(f"{sf.name}: SolidWorks did not open a sketch")
    m = _SketchMap(s, sws, sf.plane)
    if sk.is_empty():
        feat = _empty_sketch(s, doc, sm, sf, before)
    else:
        m = _align_frame(s, doc, sm, sk, sf, m)
        m.origin = origin
        _draw(s, doc, sm, sk, m, sf)
        _map_points(s, doc, sk, m, sf, report)
        _check_parity(s, s.T(doc.GetActiveSketch2(), "ISketch"), sk, m, sf)
        if 0 in m.pts:                 # geometry on our origin point: tie it to SolidWorks'
            on = m.pts.pop(0)          # later references to point 0 use the origin itself
            before_rel = _sketch_relations(s, doc)
            if not (_select_refs(s, doc, [("p", 0)], m) and on.Select4(True, None)):
                raise _NotNative(f"{sf.name}: the sketch origin could not be selected")
            doc.SketchAddConstraints("sgCOINCIDENT")
            doc.ClearSelection2(True)
            if not (_sketch_relations(s, doc) - before_rel)[9]:
                raise _NotNative(f"{sf.name}: SolidWorks did not tie the sketch to its origin")
            m.tied = on
        _add_relations(s, doc, sk, m, sf, report)
        _add_dimensions(s, doc, sk, m, sf, report)
        _add_texts(s, doc, sm, sk, m, sf, report)
        _check_defined(s, doc, sk, m, sf, report)
        sm.InsertSketch(True)
        feat = swf.newest_feature(s, doc, before)
        if feat is None or feat.GetTypeName2() != "ProfileFeature":
            raise _NotNative(f"{sf.name}: SolidWorks did not make the sketch")
        _check_dimension_count(s, feat, sk, sf, report)
    if not swf.set_name(s, doc, feat, sf.name, st):
        report.warnings.append(f"{sf.name}: SolidWorks refused the name (the sketch is called "
                               f"{feat.Name})")
    return feat, m


def _select_refs(s, doc, refs, m) -> bool:
    doc.ClearSelection2(True)
    ext = s.T(doc.Extension, "IModelDocExtension")
    for kind, i in refs:
        if kind == "e" and i in m.segs:
            ok = m.segs[i].Select4(True, None)
        elif kind == "e" and i in m.pent:
            ok = m.pent[i].Select4(True, None)
        elif kind == "p" and i == 0 and i not in m.pts:
            if m.origin is not None:                   # D1 helper point at our origin
                ok = m.origin.Select4(True, None)
            else:
                ok = ext.SelectByID2("Point1@Origin", "EXTSKETCHPOINT", 0, 0, 0, True, 0, None, 0)
        elif kind == "p" and i in m.pts:
            ok = m.pts[i].Select4(True, None)
        else:
            return False
        if not ok:
            return False
    return True


def _ref_object(s, doc, ref, m):
    """The SolidWorks sketch object standing for one of our refs (None: not selectable)."""
    kind, i = ref
    if kind == "e":
        return m.segs.get(i) or m.pent.get(i)
    if i == 0 and i not in m.pts:
        if m.origin is not None:
            return m.origin
        doc.ClearSelection2(True)
        ext = s.T(doc.Extension, "IModelDocExtension")
        if not ext.SelectByID2("Point1@Origin", "EXTSKETCHPOINT", 0, 0, 0, False, 0, None, 0):
            return None
        o = s.T(doc.SelectionManager, "ISelectionMgr").GetSelectedObject6(1, -1)
        doc.ClearSelection2(True)
        return s.T(o, "ISketchPoint")
    return m.pts.get(i)


def _sketch_relations(s, doc):
    """Relation kinds of the active SolidWorks sketch (swConstraintType_e -> count)."""
    from collections import Counter
    sws = s.T(doc.GetActiveSketch2(), "ISketch")
    rm = s.T(sws.RelationManager, "ISketchRelationManager")
    return Counter(int(s.T(r, "ISketchRelation").GetRelationType())
                   for r in (rm.GetRelations(0) or ()) if r is not None)


def _rel_code(c, m):
    """SketchAddConstraints code of one of our relations (None: a dimension / internal)."""
    if c.type in ("horizontal", "vertical"):
        hv = m.hv["h" if c.type == "horizontal" else "v"]
        two_points = len(c.refs) == 2 and all(k == "p" for k, _ in c.refs)
        return {("h", False): "sgHORIZONTAL2D", ("v", False): "sgVERTICAL2D",
                ("h", True): "sgHORIZONTALPOINTS2D", ("v", True): "sgVERTICALPOINTS2D"}[
            (hv, two_points)]
    return _REL.get(c.type)


def _sw_point(m, i):
    """The SolidWorks point of our point i ('origin' for point 0 when no curve uses it, or
    when the curve point on it was tied to SolidWorks' origin; None: none)."""
    return m.pts.get(i, "origin" if i == 0 else None)


def _same_point(m, a, b) -> bool:
    ids = []
    for p in (a, b):
        if isinstance(p, str):                     # the origin: or the point tied to it
            p = m.tied
        ids.append(None if p is None else p.GetID())
    if isinstance(a, str) and isinstance(b, str):
        return True
    return ids[0] is not None and ids[0] == ids[1]


def _implied(s, sk, c, m, sf) -> bool:
    """Does the SolidWorks sketch hold this relation by construction already? Coincident (and
    horizontal / vertical) points SolidWorks joined into one point; a coincident of a curve's
    own end (a point SolidWorks joined with that end) with the curve."""
    from .core.sketch import Arc, Line, Spline
    pts = [i for k, i in c.refs if k == "p"]
    ents = [sk.entities.get(i) for k, i in c.refs if k == "e"]
    if c.type in ("coincident", "horizontal", "vertical") and len(pts) == 2 and not ents:
        a, b = (_sw_point(m, i) for i in pts)
        return a is not None and b is not None and _same_point(m, a, b)
    if c.type == "coincident" and len(pts) == 1 and len(ents) == 1 and isinstance(
            ents[0], (Line, Arc, Spline)):
        p = _sw_point(m, pts[0])
        p = m.tied if isinstance(p, str) else p
        if p is None or ents[0].id not in m.segs:
            return False
        on = {q.GetID() for pid, q in _role_points(s, sk, ents[0], m, sf)
              if not (isinstance(ents[0], Arc) and pid == ents[0].center)}
        return p.GetID() in on
    return False


def _rel_text(sk, c) -> str:
    from .core.sketch.constraints import RELATION_NAMES
    what = []
    for k, i in c.refs:
        e = sk.entities.get(i) if k == "e" else None
        what.append(f"point {i}" if k == "p" else f"{e.label.lower() if e else 'entity'} {i}")
    return f"{RELATION_NAMES.get(c.type, c.type)} ({', '.join(what)})"


def _add_relations(s, doc, sk, m, sf, report):
    """Every BauyrCAD relation as the SolidWorks relation of the same kind. SketchAddConstraints
    returns nothing, so each call is checked: the relation kinds of the sketch must gain one of
    the expected swConstraintType_e. A relation SolidWorks refuses (redundant, conflicting or a
    wrong selection) is a blocking warning, never dropped silently."""
    from .core.sketch.constraints import RELATION_NAMES
    refused = []
    have = _sketch_relations(s, doc)
    for c in sk.constraints:
        if c.type not in RELATION_NAMES:
            continue                                   # dimensions, internal constraints
        if c.type in ("horizontal", "vertical") and m.hv is None:
            refused.append(f"{_rel_text(sk, c)}: SolidWorks' sketch axes are not BauyrCAD's")
            continue
        if _implied(s, sk, c, m, sf):
            continue
        code = _rel_code(c, m)
        if not _select_refs(s, doc, c.refs, m):
            refused.append(f"{_rel_text(sk, c)}: an entity SolidWorks can't select")
            continue
        doc.SketchAddConstraints(code)
        doc.ClearSelection2(True)
        now = _sketch_relations(s, doc)
        if not any((now - have)[t] for t in _REL_TYPES[code]) and c.type == "symmetric":
            # Add Relations wants a centerline as the axis of symmetry; the relation manager
            # takes any line (as SolidWorks' own Mirror Entities about a line does)
            objs = [_ref_object(s, doc, r, m) for r in c.refs]
            if all(o is not None for o in objs):
                import pythoncom
                from win32com.client import VARIANT
                rm = s.T(s.T(doc.GetActiveSketch2(), "ISketch").RelationManager,
                         "ISketchRelationManager")
                rm.AddRelation(VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH,
                                       [o._oleobj_ for o in objs]), 11)
                now = _sketch_relations(s, doc)
        if not any((now - have)[t] for t in _REL_TYPES[code]):
            refused.append(f"{_rel_text(sk, c)}: refused by SolidWorks (redundant or conflicting "
                           "there)")
        have = now
    doc.ClearSelection2(True)
    if refused:
        report.warnings.append(f"{sf.name}: {len(refused)} relation(s) not transferred: "
                               + "; ".join(refused))


def _check_defined(s, doc, sk, m, sf, report):
    """The SolidWorks sketch is as defined as BauyrCAD's: never over-defined, fully defined
    where BauyrCAD's is (text adds a construction line / SolidWorks text has its own state,
    so text sketches are only checked for over-definition)."""
    from .core.sketch import solver
    sws = s.T(doc.GetActiveSketch2(), "ISketch")
    rm = s.T(sws.RelationManager, "ISketchRelationManager")
    status = int(sws.GetConstrainedStatus())
    if _over_defining(s, rm) or status in (4, 5, 6):
        report.warnings.append(f"{sf.name}: the SolidWorks sketch is over defined or has no "
                               f"solution (swConstrainedStatus_e {status})")
        return
    if sk.texts:
        return
    ours = solver.analyze(sk).fully_defined
    if ours and status != 3:
        report.warnings.append(f"{sf.name}: BauyrCAD's sketch is fully defined, SolidWorks' is "
                               f"not (swConstrainedStatus_e {status})")
    elif not ours and status == 3:
        report.notes.append(f"{sf.name}: SolidWorks' sketch is fully defined where BauyrCAD's is "
                            "not (points SolidWorks joined)")


def _over_defining(s, rm) -> int:
    """Over-defining relations of a sketch (swOverDefining), without driven (reference)
    dimensions: SolidWorks lists a driven dimension that a driving one would duplicate there
    too, though it defines nothing."""
    n = 0
    for r in rm.GetRelations(2) or ():
        r = s.T(r, "ISketchRelation")
        dd = s.T(r.GetDisplayDimension(), "IDisplayDimension") if r is not None else None
        d = s.T(dd.GetDimension2(0), "IDimension") if dd is not None else None
        if d is None or d.DrivenState != 1:                 # swDimensionDriven
            n += 1
    return n


def _check_dimension_count(s, feat, sk, sf, report):
    """The closed sketch holds exactly BauyrCAD's dimensions (a wrong one SolidWorks made and
    the export deleted must be gone). SolidWorks also lists the dimension of a reference plane
    the sketch sits on ('D1@Sketch1 Plane'): only the sketch's own ones count."""
    from .core.sketch.constraints import is_dimension
    want = sum(1 for c in sk.constraints if is_dimension(c))
    got, dd = 0, feat.GetFirstDisplayDimension()
    while dd is not None:
        d = s.T(s.T(dd, "IDisplayDimension").GetDimension2(0), "IDimension")
        if d is not None and d.FullName.split("@")[1] == feat.Name:
            got += 1
        dd = feat.GetNextDisplayDimension(dd)
    if got != want:
        report.warnings.append(f"{sf.name}: the SolidWorks sketch has {got} dimension(s), "
                               f"BauyrCAD's {want}")


def _dim_kind(c, m):
    """swDimensionType_e SolidWorks must make for one of our dimensions."""
    if c.type in ("hdistance", "vdistance"):
        return _DIM_H if m.hv["h" if c.type == "hdistance" else "v"] == "h" else _DIM_V
    return {"distance": _DIM_LINEAR, "point_line_distance": _DIM_LINEAR, "diameter": _DIM_DIAMETER,
            "radius": _DIM_RADIAL, "angle": _DIM_ANGULAR}[c.type]


def _round(sk, i) -> bool:
    from .core.sketch import Arc, Circle
    return isinstance(sk.entities.get(i), (Arc, Circle))


def _dim_items(sk, c) -> list:
    """What to select in SolidWorks for one of our dimensions: the points / curves it measures
    (constraints.r_*: an arc or circle stands for its centre)."""
    from .core.sketch import Line
    if c.type == "distance":
        items, owner = [], []
        for k, i in c.refs:
            if k == "p" or _round(sk, i):
                items.append((k, i))
                owner.append(None)
            else:
                e = sk.entities[i]
                items += [("p", e.p1), ("p", e.p2)]
                owner += [i, i]
        if owner[:2] == [owner[0], owner[0]] and owner[0] is not None:
            return [("e", owner[0])]                   # a line's length: the line itself
        return items[:2]
    if c.type == "point_line_distance":
        (k0, a), line = c.refs
        e = sk.entities.get(a) if k0 == "e" else None
        if isinstance(e, Line):
            ln = sk.entities[line[1]]
            d1, d2 = sk.pt(e.p2) - sk.pt(e.p1), sk.pt(ln.p2) - sk.pt(ln.p1)
            cr = abs(d1[0] * d2[1] - d1[1] * d2[0])
            if cr <= 1e-9 * np.linalg.norm(d1) * np.linalg.norm(d2):
                return [(k0, a), line]                 # parallel lines: line to line
            return [("p", e.p1), line]                 # (BauyrCAD measures from its start)
        return [(k0, a), line]
    return list(c.refs)


def _item_point(sk, ref):
    from .core.sketch import PointEntity
    k, i = ref
    if k == "p":
        return sk.pt(i)
    e = sk.entities[i]
    if _round(sk, i):
        return sk.pt(e.center)
    return sk.pt(e.p if isinstance(e, PointEntity) else e.p1)


def _dim_labels(sk, c, items) -> list:
    """Text positions to try, ours (c.label) first: SolidWorks picks what a dimension measures
    (aligned / horizontal / vertical, which of the four angles) from where its text is, so the
    defaults put it where SolidWorks' Smart Dimension measures what BauyrCAD does."""
    from .core.sketch import Arc, Line
    out = [tuple(c.label)] if c.label is not None else []
    size = max(sk.scale() * 0.05, 1.0)
    if c.type == "angle":
        l1, l2 = (sk.entities[i] for _, i in c.refs)
        a1, a2 = sk.pt(l1.p1), sk.pt(l2.p1)
        d1, d2 = sk.pt(l1.p2) - a1, sk.pt(l2.p2) - a2
        cr = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(cr) > 1e-12:
            t = ((a2 - a1)[0] * d2[1] - (a2 - a1)[1] * d2[0]) / cr
            v = a1 + t * d1
            r1 = d1 * c.data.get("s1", 1.0) / np.linalg.norm(d1)
            r2 = d2 * c.data.get("s2", 1.0) / np.linalg.norm(d2)
            b = r1 + r2
            if np.linalg.norm(b) > 1e-9:
                b = b / np.linalg.norm(b)
                r = max(0.4 * min(np.linalg.norm(d1), np.linalg.norm(d2)), 1.0)
                out += [tuple(v + b * r), tuple(v + b * 2 * r), tuple(v + b * 0.5 * r)]
        return out
    if c.type in ("diameter", "radius"):
        e = sk.entities[c.refs[0][1]]
        cen, r = sk.pt(e.center), sk.radius(e)
        if isinstance(e, Arc):
            a0 = math.atan2(*(sk.pt(e.start) - cen)[::-1])
            a1 = math.atan2(*(sk.pt(e.end) - cen)[::-1])
            mid = a0 + ((a1 - a0) % (2 * math.pi)) / 2
            u = np.array([math.cos(mid), math.sin(mid)])
        else:
            u = np.array([math.cos(math.pi / 4), math.sin(math.pi / 4)])
        return out + [tuple(cen + u * r * 1.3)]
    if c.type == "point_line_distance":
        ln = sk.entities[items[1][1]]
        p = _item_point(sk, items[0])
        a, b = sk.pt(ln.p1), sk.pt(ln.p2)
        d = (b - a) / np.linalg.norm(b - a)
        foot = a + d * float(np.dot(p - a, d))
        mid = (p + foot) / 2
        return out + [tuple(mid), tuple(mid + d * size), tuple(mid - d * size)]
    pts = []
    for it in items:
        e = sk.entities.get(it[1]) if it[0] == "e" else None
        if isinstance(e, Line):
            pts += [sk.pt(e.p1), sk.pt(e.p2)]
        else:
            pts.append(_item_point(sk, it))
    if c.type == "distance" and len(pts) == 2:
        p, q = pts
        mid = (p + q) / 2
        L = float(np.linalg.norm(q - p))
        n = np.array([-(q - p)[1], (q - p)[0]]) / max(L, 1e-12)
        off = max(0.15 * L, 1.0)
        return out + [tuple(mid + n * off), tuple(mid - n * off), tuple(mid)]
    return out + [tuple(np.mean(pts, axis=0))] if pts else out + [(0.0, 0.0)]


def _dim_state(s, dd):
    """(Type2, SystemValue, DrivenState) of a display dimension SolidWorks made."""
    ddi = s.T(dd, "IDisplayDimension")
    d = s.T(ddi.GetDimension2(0), "IDimension")
    return ddi.Type2, d.SystemValue, d.DrivenState


def _centre_arc_ends(s, dd, items, sk):
    """Linear dimensions to arcs / circles measure from their centres (as BauyrCAD's)."""
    if not any(k == "e" and _round(sk, i) for k, i in items):
        return
    d = s.T(s.T(dd, "IDisplayDimension").GetDimension2(0), "IDimension")
    for end in (1, 2):
        try:
            if d.GetArcEndCondition(end) in (2, 3):          # min / max: the nearest / furthest
                d.SetArcEndCondition(end, 1)
        except Exception:  # noqa: BLE001
            pass


def _add_dimensions(s, doc, sk, m, sf, report):
    """Every BauyrCAD dimension as the SolidWorks dimension of the same kind: driving ones
    first, then driven (reference) ones, made driven (IDimension.DrivenState). Every one is
    read back: its kind (IDisplayDimension.Type2), value (BauyrCAD's, or the measurement of a
    driven one) and driven state; a wrong one is deleted and made again with its text
    elsewhere (SolidWorks picks what it measures from there). The text ends up where BauyrCAD
    keeps it (c.label)."""
    from .core.sketch.constraints import is_dimension
    dims = [c for c in sk.constraints if is_dimension(c)]
    failed = []
    for c in [c for c in dims if c.driving] + [c for c in dims if not c.driving]:
        why = _add_dimension(s, doc, sk, c, m, sf, report)
        if why:
            failed.append(why)
    doc.ClearSelection2(True)
    if failed:
        report.warnings.append(f"{sf.name}: {len(failed)} dimension(s) not transferred: "
                               + "; ".join(failed))


def _dim_text(c) -> str:
    from .core.sketch.constraints import format_value
    return f"{c.type} {format_value(c)}{'' if c.driving else ' (driven)'}"


def _add_dimension(s, doc, sk, c, m, sf, report):
    """One dimension (see _add_dimensions); returns why it failed, or None."""
    from .core.sketch.constraints import measure
    if c.type in ("hdistance", "vdistance") and m.hv is None:
        return f"{_dim_text(c)}: SolidWorks' sketch axes are not BauyrCAD's"
    kind = _dim_kind(c, m)
    items = _dim_items(sk, c)
    want = abs(c.value if c.driving else measure(sk, c))
    want_sw = want if c.type == "angle" else want * MM
    ext = s.T(doc.Extension, "IModelDocExtension")
    got = []
    labels = _dim_labels(sk, c, items)
    for k, lab in enumerate(labels):
        if not _select_refs(s, doc, items, m):
            return f"{_dim_text(c)}: an entity SolidWorks can't select"
        w = m.world_m(lab)
        try:
            if kind == _DIM_LINEAR:      # (AddSpecificDimension does not make aligned ones)
                dd = doc.AddDimension2(float(w[0]), float(w[1]), float(w[2]))
            else:
                r = ext.AddSpecificDimension(float(w[0]), float(w[1]), float(w[2]), kind, 0)
                dd = r[0] if isinstance(r, tuple) else r
        except Exception:  # noqa: BLE001
            dd = None
        if dd is None:
            got.append("no dimension")
            continue
        _centre_arc_ends(s, dd, items, sk)
        t2, v, state = _dim_state(s, dd)
        if t2 != kind or abs(abs(v) - want_sw) > 1e-9 + 1e-7 * want_sw:
            got.append(f"{_DIM_TYPE_NAMES.get(t2, t2)} "
                       + (f"{math.degrees(v):.6g} deg" if t2 == _DIM_ANGULAR else f"{v / MM:.6g} mm"))
            _delete_selected_dim(s, doc, dd)
            continue
        if c.driving and state != 2:
            _delete_selected_dim(s, doc, dd)
            return (f"{_dim_text(c)}: it would over-define the SolidWorks sketch (SolidWorks made "
                    "it driven)")
        if not c.driving and state != 1:
            s.T(s.T(dd, "IDisplayDimension").GetDimension2(0), "IDimension").DrivenState = 1
            if _dim_state(s, dd)[2] != 1:
                _delete_selected_dim(s, doc, dd)
                return f"{_dim_text(c)}: SolidWorks did not make it a driven dimension"
        if c.label is not None and k > 0:
            _move_dim_text(s, dd, m, c, lab, kind, want_sw, sf, report)
        return None
    return f"{_dim_text(c)}: SolidWorks made {', '.join(got) or 'nothing'}"


def _move_dim_text(s, dd, m, c, lab, kind, want_sw, sf, report):
    """Put a dimension made with its text elsewhere where BauyrCAD keeps it (cosmetic: if
    SolidWorks would measure something else there, it stays put and that is a note)."""
    ann = s.T(s.T(dd, "IDisplayDimension").GetAnnotation(), "IAnnotation")
    w = m.world_m(c.label)
    ann.SetPosition2(float(w[0]), float(w[1]), float(w[2]))
    t2, v, _ = _dim_state(s, dd)
    if t2 != kind or abs(abs(v) - want_sw) > 1e-9 + 1e-7 * want_sw:
        w = m.world_m(lab)
        ann.SetPosition2(float(w[0]), float(w[1]), float(w[2]))
        report.notes.append(f"{sf.name}: the text of {_dim_text(c)} sits elsewhere in SolidWorks")


def _delete_selected_dim(s, doc, display_dim):
    try:
        doc.ClearSelection2(True)
        ann = s.T(s.T(display_dim, "IDisplayDimension").GetAnnotation(), "IAnnotation")
        ann.Select3(False, None)
        s.T(doc.Extension, "IModelDocExtension").DeleteSelection2(0)
    except Exception:  # noqa: BLE001
        pass
    doc.ClearSelection2(True)


def _region_outline(s, region, m) -> list:
    """Boundary segments of a SolidWorks sketch region, in sketch coordinates (m). (A region
    of a sketch on a face may be bounded by the face's own edges too.)"""
    segs = []
    for e in region.GetEdges() or ():
        pts = [m.o + m.A @ p for p in _edge_points(s, s.T(e, "IEdge"))]
        segs += [(pts[k][:2], pts[k + 1][:2]) for k in range(len(pts) - 1)]
    return segs


def _edge_points(s, edge) -> list:
    """Points along a region edge (model m), start to end. A region edge's curve does not
    evaluate in the edge's own parameter range (ICurve.Evaluate2 gave points beyond a line's
    ends), so lines are taken from their end points and curves are tessellated between them."""
    cp = s.T(edge.GetCurveParams3(), "ICurveParamData")
    curve = s.T(edge.GetCurve(), "ICurve")
    a, b = np.array(cp.StartPoint, float), np.array(cp.EndPoint, float)
    if curve.IsLine():
        return [a, b]
    tess = curve.GetTessPts(1e-7, 1e-9, _doubles(a), _doubles(b))
    if tess and len(tess) >= 6:
        return list(np.array(tess, float).reshape(-1, 3))
    if curve.IsCircle() and np.linalg.norm(a - b) < 1e-12:      # a whole circle
        prm = np.array(curve.CircleParams, float)
        c, n = prm[:3], prm[3:6] / np.linalg.norm(prm[3:6])
        x = a - c                                                # radius long
        y = np.cross(n, x)
        return [c + math.cos(t) * x + math.sin(t) * y for t in np.linspace(0, 2 * math.pi, 129)]
    raise _NotNative("a sketch region boundary could not be read in SolidWorks")


def _inside(segs, q) -> bool:
    """Even-odd ray test of point q against boundary segments (holes work too)."""
    x, y = q
    inside = False
    for (x1, y1), (x2, y2) in segs:
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def _select_profile(s, doc, sketch_feat, m, regions):
    """The whole sketch, or (SolidWorks' Selected Contours) the regions holding the points
    BauyrCAD picked."""
    doc.ClearSelection2(True)
    if not regions:
        sketch_feat.Select2(False, 0)
        return
    sk = s.T(sketch_feat.GetSpecificFeature2(), "ISketch")
    sw_regions = [s.T(r, "ISketchRegion") for r in (sk.GetSketchRegions() or ())]
    outlines = [_region_outline(s, r, m) for r in sw_regions]
    picked = 0
    for uv in regions:
        q = m.sk(uv)[:2]
        for reg, segs in zip(sw_regions, outlines):
            if _inside(segs, q):
                reg.Select2(True, s.select_data(doc, 0))
                picked += 1
                break
    if picked != len(regions):
        raise _NotNative("the selected contours could not be matched in SolidWorks")


class _History:
    """A private copy of a BauyrCAD Document, regenerated ONCE for the export.

    The export (often on a worker thread) never touches the caller's Document, and each
    step reads the body before / after it from this cache instead of regenerating the
    history from scratch (O(n), not O(n^2))."""

    def __init__(self, bdoc):
        from .core.document import Document
        self.source = bdoc
        self.doc = Document.from_dict(bdoc.to_dict())
        self.doc.path = getattr(bdoc, "path", None)
        self.after: list = []                   # body after features[i]
        self.doc.regenerate(record=self.after)
        self.by_id = {f.id: f for f in self.doc.features}

    @property
    def body(self):
        return self.doc.body

    def before(self, i):
        return self.after[i - 1] if i > 0 else None

    def ctx(self, i):
        """The regeneration context just before features[i] (as Document.context_before)."""
        from .core.features import RegenContext
        ctx = RegenContext()
        ctx.body = self.before(i)
        ctx.features = dict(self.by_id)
        return ctx


def _history(bdoc, history=None) -> _History:
    return history if history is not None and history.source is bdoc else _History(bdoc)


# Relative tolerances on a feature's own change of volume and of first moment (D3): exact
# by default. SolidWorks' splines through BauyrCAD's points are BauyrCAD's curve to a tiny
# amount along the curve (_SPLINE is a margin, _SPLINE_GAP the absolute part); it shapes
# blends between different profiles a little differently from OpenCASCADE, and draws font
# outlines its own way (a few % of the letters' area, the baseline and spacing agree to
# ~0.1 mm).
_EXACT = 1e-5
_SPLINE = 1e-4
# Plus, for a step that depends on a sketch spline, an absolute allowance (D8: the spline's
# shape difference is a note, never a refusal): this distance (mm) times the free-form face
# area of BauyrCAD's result. SolidWorks' spline through BauyrCAD's points and its mass
# integration of the spline faces differ from OpenCASCADE's exact measure by an amount that
# follows the spline (seen: 0.006 mm³ on 66 mm² of spline face, ~1e-4 mm), not the
# feature's volume, so a narrow spline strip would fail a purely relative tolerance.
_SPLINE_GAP = 1e-3
_TEXT = 0.1             # letters' volume (seen: 0.8 - 5.7 %), plus their centroid within
_TEXT_PLACE = 0.1       # this fraction of the text height
_FEATURE_TOL = {"sweep": 1e-3, "loft": 1e-2, "boundary": 1e-2}
# What the two kernels' mass properties may disagree by on an exact model, relative to the
# whole body (measured: ~2e-9 at SolidWorks' Higher accuracy, see _mass). Only a noise floor:
# each step's own tolerance is relative to its own change of volume (D3), and the first
# moment is taken about the step's neighbourhood, not the world origin.
_MEASURE = 1e-8


def _uses_sketch(f, by_id, pred, seen=None) -> bool:
    """Does the feature depend (directly or through other features) on a sketch for which
    pred(sketch) holds?"""
    from .core.features import SketchFeature
    seen = set() if seen is None else seen
    for fid in f.depends_on():
        if fid in seen or fid not in by_id:
            continue
        seen.add(fid)
        g = by_id[fid]
        if isinstance(g, SketchFeature):
            if pred(g.sketch):
                return True
        elif _uses_sketch(g, by_id, pred, seen):
            return True
    return False


def _uses_spline(f, by_id) -> bool:
    from .core.sketch import Spline
    return _uses_sketch(f, by_id, lambda sk: any(isinstance(e, Spline)
                                                 for e in sk.entities.values()))


def _uses_text(f, by_id) -> bool:
    return _uses_sketch(f, by_id, lambda sk: bool(sk.texts))


def _text_height(f, by_id) -> float:
    """The tallest sketch text the feature depends on (mm; 0 without text)."""
    h = [0.0]

    def seen(sk):
        h.extend(t["height"] for t in sk.texts)
        return False                                    # keep walking every sketch
    _uses_sketch(f, by_id, seen)
    return max(h)


def _feature_tol(f, by_id) -> float:
    tol = _FEATURE_TOL.get(f.TYPE, _EXACT)
    if _uses_spline(f, by_id):
        tol = max(tol, _SPLINE)
    if _uses_text(f, by_id):
        tol = max(tol, _TEXT)
    return tol


def _measure(shape) -> tuple:
    """(volume, first moment = volume * centroid, (adaptive volume, adaptive first moment)) of
    a BauyrCAD shape, mm: measured on the shape split at its B-spline knots
    (geometry.accurate_mass_properties), so a spline-bounded step is judged against its exact
    volume, not against OpenCASCADE's whole-face integration error (which does not shrink with
    the feature: a narrow spline strip would fail on it alone)."""
    from .core import geometry
    if shape is None:
        return 0.0, np.zeros(3), (0.0, np.zeros(3))
    (v, c), (va, ca) = geometry.accurate_mass_properties(shape)
    return v, v * c, (va, va * ca)


def _moments(shape):
    """(volume, first moment = volume * centroid) of a shape, mm (accurately, _measure)."""
    v, m, _ = _measure(shape)
    return v, m


def _measuring_noise(measured, centre) -> tuple:
    """How uncertain the accurate measure (_measure) still is: the difference between its fixed
    Gauss and its adaptive integration of the volume and of the first moment about ``centre``
    (0 on analytic faces and on split B-spline spans, to round-off)."""
    v, m, (va, ma) = measured
    return abs(va - v), float(np.linalg.norm((ma - va * centre) - (m - v * centre)))


def _box(*shapes) -> tuple:
    """(centre, diagonal) of the shapes' common bounding box (mm; diagonal at least 1)."""
    from .core import geometry
    lo, hi = [], []
    for sh in shapes:
        if sh is not None:
            a, b = geometry.bounding_box(sh)
            lo.append(a)
            hi.append(b)
    if not lo:
        return np.zeros(3), 1.0
    lo, hi = np.min(lo, axis=0), np.max(hi, axis=0)
    return (lo + hi) / 2, max(float(np.linalg.norm(hi - lo)), 1.0)


def _size(*shapes) -> float:
    return _box(*shapes)[1]


def _sw_moments(s, doc):
    v, c = _mass(s, doc)
    return v, v * c


def _native_part(s, hist, report):
    """Rebuild the history copy feature by feature in a new SolidWorks part (closed again
    if anything fails)."""
    from . import solidworks_features as swf
    from .core.features import ImportFeature, SketchFeature
    bdoc = hist.doc
    doc = s.new("part")
    st = swf.State(report)
    try:
        for i, f in enumerate(bdoc.features):
            if f.suppressed:
                report.warnings.append(f"{f.name} is suppressed in BauyrCAD and was left out")
                continue
            if f.error:
                raise _NotNative(f"{f.name}: the feature has an error in BauyrCAD ({f.error})")
            if isinstance(f, ImportFeature):
                if i != 0:
                    raise _NotNative(f"{f.name}: only an imported base at the start of history "
                                     "is supported")
                swf.require(f)
                s.close(doc)
                doc = None
                doc = _solid_part(s, f.shape())
                bases = []
                feat = s.T(doc.FirstFeature(), "IFeature")
                while feat is not None:
                    if feat.GetTypeName2() == "BaseBody":
                        bases.append(feat)
                    feat = s.T(feat.GetNextFeature(), "IFeature")
                if len(bases) != 1:
                    raise _NotNative(f"{f.name}: expected one editable Imported base feature")
                if not swf.set_name(s, doc, bases[0], f.name, st):
                    report.warnings.append(f"{f.name}: SolidWorks refused the name (the import "
                                           f"is called {bases[0].Name})")
                st.own(s, bases[0])
                st.features[f.id] = bases[0]
            elif isinstance(f, SketchFeature):
                feat, m = _build_sketch(s, doc, f, report, st, hist.ctx(i))
                st.own(s, feat)
                st.sketches[f.id] = (feat, m, f)
                st.features[f.id] = feat
            elif f.TYPE in swf.HANDLERS:
                st.features[f.id] = _native_feature(s, doc, hist, i, f, st, swf, report)
            else:
                raise _NotNative(f"{f.name}: {type(f).__name__} is not supported yet")
            report.features.append(f.name)
    except BaseException:
        if doc is not None:
            s.close(doc)
        raise
    return doc


def _native_feature(s, doc, hist, i, f, st, swf, report):
    """Create a feature through its handler; keep the first attempt whose change of volume
    AND of first moment (so also where the material went) matches BauyrCAD's, within a
    tolerance relative to the feature's own change (D3)."""
    ctx = hist.ctx(i)
    want = hist.after[i]
    meas1, meas0 = _measure(want), _measure(ctx.body)
    v1, m1, _ = meas1
    v0, m0, _ = meas0
    # first moments about the centre of the step's neighbourhood, not the world origin: an
    # accepted volume difference must not turn into a placement error just because the part
    # sits far from the origin (moving the model changes nothing below)
    centre, size = _box(want, ctx.body)
    m1, m0 = m1 - v1 * centre, m0 - v0 * centre
    dv_want, dm_want = v1 - v0, m1 - m0
    sv0, sm0 = _sw_moments(s, doc)
    sm0 = sm0 - sv0 * centre
    # the noise floor: the kernels' measuring agreement (tiny, relative to the body), plus the
    # difference SolidWorks' model ALREADY had before this step (an earlier loft or spline
    # surface it shapes its own way; 0 on an exact model), which this step may carry or undo
    # and OpenCASCADE's own measuring uncertainty on the two bodies (0 on analytic faces: it is
    # measured, so it only loosens the check where B-spline faces make the measure uncertain)
    nv1, nm1 = _measuring_noise(meas1, centre)
    nv0, nm0 = _measuring_noise(meas0, centre)
    floor_v = _MEASURE * max(v1, v0, 1.0) + 1e-6 + abs(sv0 - v0) + nv1 + nv0
    floor_m = ((_MEASURE * max(v1, v0, 1.0) + 1e-6) * size + float(np.linalg.norm(sm0 - m0))
               + nm1 + nm0)
    rel = _feature_tol(f, hist.by_id)
    gap = 0.0                  # the spline allowance (a difference within it is a note)
    if _uses_spline(f, hist.by_id):
        from .core import geometry
        gap = _SPLINE_GAP * geometry.freeform_area(want)
    vol_tol = rel * abs(dv_want) + floor_v + gap
    mom_tol = (rel * abs(dv_want) + gap) * size + floor_m
    exact_tol = _EXACT * abs(dv_want) + floor_v
    text_h = _text_height(f, hist.by_id)
    attempt, got = 0, []
    debug = os.environ.get("BAUYRCAD_SW_DEBUG")
    while True:
        feat = swf.HANDLERS[f.TYPE](s, doc, f, ctx, st, attempt)
        attempt += 1
        if feat is swf.NO_MORE:
            break
        if feat is None:
            continue
        feats = [s.T(x, "IFeature") for x in (feat if isinstance(feat, list) else [feat])]
        if any(x is None for x in feats) or any(x.GetID() in st.owned for x in feats):
            # the call made nothing and handed back an earlier step: never keep or delete it
            got.append("no new feature")
            continue
        sv1, sm1 = _sw_moments(s, doc)
        sm1 = sm1 - sv1 * centre
        dv_err = abs((sv1 - sv0) - dv_want)
        dm_err = float(np.linalg.norm((sm1 - sm0) - dm_want))      # mm^4
        ok = dv_err <= vol_tol and dm_err <= mom_tol
        got.append(f"{sv1:.3f} mm³" + ("" if dm_err <= mom_tol else
                                         f" (material {dm_err / max(abs(dv_want), 1e-9):.3g} mm off)"))
        if ok and text_h and abs(sv1 - sv0) > 1e-9 and abs(dv_want) > 1e-9:
            # font outlines differ by a few % of the letters' area, so the first moment
            # alone would allow text in the wrong place: the letters' centroid must agree
            off = float(np.linalg.norm((sm1 - sm0) / (sv1 - sv0) - dm_want / dv_want))
            if off > _TEXT_PLACE * text_h:
                ok = False
                got[-1] += f" (letters {off:.3g} mm off)"
            if debug:
                print(f"[sw] {f.name}: letters' centroid {off:.3g} mm off "
                      f"(tol {_TEXT_PLACE * text_h:.3g})", flush=True)
        if debug:
            print(f"[sw] {f.name} attempt {attempt - 1}: dV err {dv_err:.3g} (tol {vol_tol:.3g}) "
                  f"dM err {dm_err:.3g} (tol {mom_tol:.3g}) -> {'ok' if ok else 'rejected'} "
                  f"[SW-OCC before {sv0 - v0:.3g}, after {sv1 - v1:.3g}]",
                  flush=True)
        if ok:
            if dv_err > exact_tol:
                kind = ("font outlines" if _uses_text(f, hist.by_id) else
                        "spline through the same points" if _uses_spline(f, hist.by_id) else "surface")
                verb = "differ" if kind == "font outlines" else "differs"
                report.notes.append(f"{f.name}: SolidWorks' {kind} {verb} slightly from "
                                    f"BauyrCAD's ({100 * dv_err / max(abs(dv_want), 1e-9):.2g} % "
                                    "of the feature's volume)")
            names = [f.name] + [f"{f.name}-{k}" for k in range(2, len(feats) + 1)]   # D6
            for x, name in zip(feats, names):
                if not swf.set_name(s, doc, x, name, st):
                    report.warnings.append(f"{f.name}: SolidWorks refused the name '{name}' "
                                           f"(the feature is called {x.Name})")
                st.own(s, x)
            for x in feats:
                swf.name_children(s, doc, x, f.name, st)
            if len(feats) > 1:
                report.steps[f.name] = [x.Name for x in feats]
            report.attempts[f.name] = attempt - 1
            doc.ClearSelection2(True)
            return feats[0] if len(feats) == 1 else feats
        for x in reversed(feats):
            swf._delete(s, doc, x)
    raise _NotNative(f"{f.name}: SolidWorks' result did not match BauyrCAD's "
                     f"({', '.join(got) or 'no feature created'}; BauyrCAD {v1:.3f} mm³)")


def native_export_issues(bdoc, history=None) -> list:
    """Why this Document can't be exported with its complete native feature tree, found
    WITHOUT SolidWorks (strict exports check this before SolidWorks starts). Each feature
    TYPE's mode checks live next to its handler (solidworks_features.PRECHECKS); handlers
    run the same checks, so the two never disagree. [] does not guarantee success: the
    result of every step is still validated in SolidWorks."""
    from . import solidworks_features as swf
    hist = _history(bdoc, history)
    issues = []
    n_par, n_eq = len(hist.doc.parameters or {}), len(hist.doc.parameter_bindings or [])
    if n_par or n_eq:
        issues.append(f"Parameters: {n_par} parameter(s) and {n_eq} equation link(s) are not "
                      "transferred to SolidWorks yet (they would arrive as plain values)")
    for i, f in enumerate(hist.doc.features):
        if f.suppressed:
            issues.append(f"{f.name}: suppressed feature transfer is not implemented")
            continue
        if f.error:
            issues.append(f"{f.name}: the feature has an error in BauyrCAD ({f.error}); fix or "
                          "delete it before exporting")
            continue
        if f.warning:                  # e.g. a sketch whose face is gone (kept its last place)
            issues.append(f"{f.name}: {f.warning}; fix the reference before exporting (it would "
                          "not follow the model in SolidWorks)")
        if f.TYPE == "import_step" and i != 0:
            issues.append(f"{f.name}: only an imported base at the start of history is supported")
            continue
        if f.TYPE not in swf.HANDLERS and f.TYPE not in ("sketch", "import_step"):
            issues.append(f"{f.name}: native {f.TYPE} transfer is not implemented")
            continue
        issues += [f"{f.name}: {msg}" for msg in swf.feature_issues(f, lambda i=i: hist.ctx(i))]
    return issues


def unsupported_types() -> list:
    """BauyrCAD feature TYPEs without a native SolidWorks translator (the strict gate refuses
    them; the coverage matrix lists them)."""
    from . import solidworks_features as swf
    from .core.features import FEATURE_TYPES
    return sorted(set(FEATURE_TYPES) - {"sketch", "import_step", *swf.HANDLERS})


def _require_complete(issues):
    if issues:
        raise SolidWorksError("Complete editable SolidWorks export is not available for this model:\n"
                             + "\n".join(issues)
                             + "\nNo simplified replacement will be saved. Use STEP for geometry-only exchange.")


def assembly_native_issues(asm) -> list:
    """native_export_issues for every part of an assembly, plus its mates (no SolidWorks)."""
    from .core.document import Document
    issues = []
    for c in asm.components:
        src = c.resolve_path(asm.base_dir)
        if c.shape is None:
            issues.append(f"{c.name}: part is not loaded")
        elif Path(src).suffix.lower() != ".bcad":
            issues.append(f"{c.name}: source has no BauyrCAD feature history")
        elif not Path(src).exists():
            issues.append(f"{c.name}: part file {src} not found")
        else:
            issues.extend(f"{c.name} / {issue}" for issue in native_export_issues(Document.load(src)))
    issues.extend(f"{m.name}: suppressed or unsupported mate transfer" for m in asm.mates
                  if m.suppressed or m.type not in _MATE)
    return issues


_assembly_native_issues = assembly_native_issues


# swFeatureError_e: What's Wrong codes worth spelling out
_FEATURE_ERRORS = {1: "unknown error", 30: "the feature would create a disjoint body",
                   31: "the end of the feature was not found",
                   32: "the extrusion failed on the geometric conditions",
                   10: "a fillet/chamfer loop does not exist", 11: "a fillet/chamfer face does not exist",
                   13: "a fillet/chamfer edge does not exist", 18: "the fillet radius is too big",
                   51: "sketch error"}


def rebuild_health(s, doc) -> tuple:
    """Force a full rebuild of a SolidWorks document and read What's Wrong:
    (rebuilt ok, [(feature name, swFeatureError_e code, is_warning)])."""
    ok = bool(doc.ForceRebuild3(False))
    ext = s.T(doc.Extension, "IModelDocExtension")
    out = []
    if ext.GetWhatsWrongCount():
        r = ext.GetWhatsWrong()
        feats, codes, warns = (list(x or ()) for x in r[1:4]) if len(r) >= 4 else ([], [], [])
        for f, c, w in zip(feats, codes, warns):
            name = s.T(f, "IFeature").Name if f is not None else "?"
            out.append((name, int(c), bool(w)))
    # and every top-level feature's own error state (IFeature.GetErrorCode2)
    seen = {name for name, _, _ in out}
    feat = s.T(doc.FirstFeature(), "IFeature")
    while feat is not None:
        code, warning = feat.GetErrorCode2()
        if code and feat.Name not in seen:
            out.append((feat.Name, int(code), bool(warning)))
        feat = s.T(feat.GetNextFeature(), "IFeature")
    return ok, out


def _check_health(s, doc, report):
    """XC-06: a saved part must rebuild without What's Wrong items (errors and warnings
    both block: the user would see them on the first rebuild)."""
    ok, items = rebuild_health(s, doc)
    if not ok:
        report.warnings.append("SolidWorks could not rebuild the exported part")
    for name, code, warning in items:
        what = _FEATURE_ERRORS.get(code, f"swFeatureError_e {code}")
        report.warnings.append(f"{name}: SolidWorks rebuild {'warning' if warning else 'error'} "
                               f"({what})")


def _check_final(s, doc, hist, report):
    """The whole part: SolidWorks' volume and centre of mass against BauyrCAD's (a body-less
    part must stay body-less)."""
    from .core import geometry
    body = hist.body
    report.volume = _volume(s, doc)
    if body is None:
        bodies = s.T(doc, "IPartDoc").GetBodies2(0, False) or ()
        if bodies or abs(report.volume) > 1e-6:
            raise _NotNative("SolidWorks made a solid body where BauyrCAD has none")
        return
    rel = max([_feature_tol(f, hist.by_id) for f in hist.doc.features] + [_EXACT])
    v_ref, m_ref = _moments(body)                  # accurate on spline faces too
    gap = (_SPLINE_GAP * geometry.freeform_area(body)
           if any(_uses_spline(f, hist.by_id) for f in hist.doc.features) else 0.0)
    if abs(report.volume - v_ref) > rel * v_ref + _MEASURE * v_ref + 1e-6 + gap:
        raise _NotNative(f"final volume differs (SolidWorks {report.volume:.3f} mm³, "
                         f"BauyrCAD {v_ref:.3f} mm³)")
    size = _size(body)
    off = float(np.linalg.norm(_center(s, doc) - m_ref / v_ref))
    if off > (rel + _MEASURE + gap / max(v_ref, 1e-9)) * size + 1e-6:
        raise _NotNative(f"the final centre of mass is {off:.3g} mm away from BauyrCAD's")


def export_part(s: Session, bdoc, path, native=True, require_native=False,
                history=None) -> PartReport:
    """Write one BauyrCAD Document as a SolidWorks part. native=True rebuilds the feature tree
    (non-strict: falls back to a solid body when that is not exact; require_native=True:
    fails instead, and also on any blocking report warning). The Document itself is never
    modified: the export works on a private copy (``history``: a _History of it)."""
    if require_native and not native:
        raise SolidWorksError("require_native needs native=True")
    hist = _history(bdoc, history)
    if require_native:
        _require_complete(native_export_issues(bdoc, hist))
    path = str(Path(path).with_suffix(".SLDPRT"))
    report = PartReport(path)
    doc = None
    if native:
        try:
            doc = _native_part(s, hist, report)
            _check_health(s, doc, report)
            _check_final(s, doc, hist, report)
        except _NotNative as e:
            if doc is not None:
                s.close(doc)
                doc = None
            if require_native:
                _require_complete([str(e)])
            report.warnings.append(f"Feature tree not rebuilt ({e}); imported as a solid body")
            report.native, report.features = False, []
            report.notes, report.helpers, report.steps, report.attempts = [], [], {}, {}
        except BaseException:
            if doc is not None:
                s.close(doc)
            raise
    try:
        if doc is None:
            report.native = False
            if hist.body is None:
                raise SolidWorksError(f"{Path(path).name}: the part has no solid body to export "
                                      "as a solid, and its feature tree could not be rebuilt")
            doc = _solid_part(s, hist.body)
            report.volume = _volume(s, doc)
        if require_native:
            _require_complete(report.warnings)
        s.save(doc, path)
    finally:
        if doc is not None:
            s.close(doc)
    return report


def _solid_part(s, shape):
    from .core import exchange
    fd, tmp = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        exchange.export(shape, tmp, "step214")
        return s.open_step(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# =====================================================================================
# assemblies
# =====================================================================================
@dataclass
class AssemblyReport:
    path: str
    parts: dict = field(default_factory=dict)       # source -> PartReport
    mates: list = field(default_factory=list)       # (mate name, ok, note)
    warnings: list = field(default_factory=list)


def _pose_of(s, comp_sw):
    xf = s.T(comp_sw.Transform2, "IMathTransform")
    a = np.array(xf.ArrayData)
    R = a[:9].reshape(3, 3).T
    return R, a[9:12] / MM


def _find_comp_face(s, comp_sw, p_local_mm, n_local=None):
    """The face of a component at a point given in the part's own coordinates."""
    body = comp_sw.GetBody()
    if body is None:
        return None
    q = np.asarray(p_local_mm, float) * MM
    best, bd = None, 1e-7
    for f in s.T(body, "IBody2").GetFaces() or ():
        face = s.T(f, "IFace2")
        c = np.array(face.GetClosestPointOn(*q)[:3])
        d = float(np.linalg.norm(c - q))
        if d < bd:
            best, bd = face, d
    return best


def export_assembly(s: Session, asm, folder, native=True, require_native=False,
                    prechecked=False) -> AssemblyReport:
    """Write an Assembly as .SLDASM (+ one .SLDPRT per part file) into folder."""
    from .core.document import Document
    if require_native:
        if not native:
            raise SolidWorksError("require_native needs native=True")
        if not prechecked:
            _require_complete(assembly_native_issues(asm))
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = asm.path.stem if asm.path else "Assembly1"
    rep = AssemblyReport(str(folder / f"{name}.SLDASM"))
    part_files = {}
    for c in asm.components:
        if c.shape is None:
            rep.warnings.append(f"{c.name}: part not loaded ({c.load_error}), left out")
            continue
        src = str(c.resolve_path(asm.base_dir))
        if src in part_files:
            continue
        stem = Path(src).stem
        target = folder / f"{stem}.SLDPRT"
        if src.lower().endswith(".bcad") and os.path.exists(src):
            bdoc = Document.load(src)
            pr = export_part(s, bdoc, target, native, require_native=require_native)
        else:                                          # STEP / IGES part: a solid body
            doc = _solid_part(s, c.shape)
            try:
                pr = PartReport(str(target), native=False, volume=_volume(s, doc))
                s.save(doc, target)
            finally:
                s.close(doc)
        rep.parts[src] = pr
        part_files[src] = str(target.resolve())

    # SolidWorks only inserts components whose part documents are loaded
    loaded = []
    for f in part_files.values():
        r = s.sw.OpenDoc6(f, 1, 1, "", 0, 0)
        d = r[0] if isinstance(r, tuple) else r
        if d is not None:
            pd = s.T(d, "IModelDoc2")
            s._docs.append(pd)
            loaded.append(pd)
    try:
        _assemble(s, asm, rep, part_files, require_native)
    finally:
        for pd in loaded:
            s.close(pd)
    return rep


def _assemble(s, asm, rep, part_files, require_native):
    doc = s.new("assembly")
    try:
        _assemble_into(s, doc, asm, rep, part_files, require_native)
    finally:
        s.close(doc)


def _assemble_into(s, doc, asm, rep, part_files, require_native):
    adoc = s.T(doc, "IAssemblyDoc")
    s.sw.ActivateDoc3(doc.GetTitle(), False, 0, 0)
    swc = {}
    for c in asm.components:
        src = str(c.resolve_path(asm.base_dir))
        if src not in part_files:
            continue
        t = c.pose.t * MM
        comp = adoc.AddComponent5(part_files[src], 0, "", False, "", float(t[0]), float(t[1]),
                                  float(t[2]))
        if comp is None:
            rep.warnings.append(f"{c.name}: SolidWorks could not insert the component")
            continue
        comp = s.T(comp, "IComponent2")
        comp.Transform2 = s.transform(c.pose.R, t)
        try:
            comp.Name2 = c.name.replace("<", "-").replace(">", "")
        except Exception:  # noqa: BLE001
            pass
        swc[c.id] = comp
    # fixed / floating exactly as in BauyrCAD
    for c in asm.components:
        if c.id not in swc:
            continue
        doc.ClearSelection2(True)
        swc[c.id].Select4(False, None, False)
        adoc.FixComponent() if c.fixed else adoc.UnfixComponent()
    doc.ClearSelection2(True)
    for mt in asm.mates:
        if mt.suppressed:
            rep.mates.append((mt.name, False, "suppressed in BauyrCAD - left out"))
            continue
        rep.mates.append(_add_mate(s, doc, adoc, asm, mt, swc))
    doc.EditRebuild3()
    moved = []
    for c in asm.components:                           # did SolidWorks keep every position?
        if c.id in swc:
            R, t = _pose_of(s, swc[c.id])
            if not (np.allclose(R, c.pose.R, atol=1e-6) and np.allclose(t, c.pose.t, atol=1e-4)):
                moved.append(c.name)
    if moved:
        rep.warnings.append("SolidWorks moved " + ", ".join(moved) + " while solving the mates")
    if require_native:
        _require_complete(rep.warnings + [f"{name}: {note}" for name, ok, note in rep.mates if not ok or note])
    s.save(doc, rep.path)


def _add_mate(s, doc, adoc, asm, mt, swc):
    from .core import geometry
    if mt.type not in _MATE:
        return (mt.name, False, f"{mt.type} mates are not supported in the export")
    comps = [asm.component(r.component) for r in mt.refs]
    if any(c is None or c.id not in swc for c in comps):
        return (mt.name, False, "a component is missing")
    doc.ClearSelection2(True)
    if mt.type == "lock":
        for c in comps:
            swc[c.id].Select4(True, None, False)
    else:
        for r, c in zip(mt.refs, comps):
            idx = r.face.resolve(c.faces)
            if idx is None:
                return (mt.name, False, "a mated face was not found")
            p = geometry.point_on_face(c.shape, idx)
            face = _find_comp_face(s, swc[c.id], p)
            if face is None:
                return (mt.name, False, "the face was not found in SolidWorks")
            s.T(face, "IEntity").Select4(True, None)
    align = _CLOSEST
    if mt.type in ("coincident", "distance"):
        align = _ALIGNED if mt.align > 0 else _ANTI
    dist = abs(mt.value) * MM if mt.type == "distance" else 0.0
    ang = abs(mt.value) if mt.type == "angle" else 0.0
    before = {c.id: _pose_of(s, swc[c.id]) for c in comps}
    for flip in (False, True):
        r = adoc.AddMate5(_MATE[mt.type], align, flip, dist, dist, dist, 1.0, 1.0, ang, ang, ang,
                          False, False, 0, 0)
        mate = r[0] if isinstance(r, tuple) else r
        if mate is None:
            return (mt.name, False, "SolidWorks rejected the mate")
        doc.EditRebuild3()
        ok = all(np.allclose(_pose_of(s, swc[c.id])[0], c.pose.R, atol=1e-6)
                 and np.allclose(_pose_of(s, swc[c.id])[1], c.pose.t, atol=1e-4) for c in comps)
        if ok or mt.type not in ("distance", "angle"):
            return (mt.name, True, "" if ok else "SolidWorks moved a component")
        # the other side / sense: undo this mate, put the parts back and try flipped
        s.T(mate, "IFeature").Select2(False, 0) if hasattr(mate, "Select2") else None
        s.T(doc.Extension, "IModelDocExtension").DeleteSelection2(0)
        for c in comps:
            R, t = before[c.id]
            swc[c.id].Transform2 = s.transform(R, t * MM)
        doc.ClearSelection2(True)
        for r_, c in zip(mt.refs, comps):
            idx = r_.face.resolve(c.faces)
            face = _find_comp_face(s, swc[c.id], geometry.point_on_face(c.shape, idx))
            s.T(face, "IEntity").Select4(True, None)
    return (mt.name, False, "no orientation of the mate kept the BauyrCAD positions")


# =====================================================================================
# one-call helpers (API, CLI, GUI)
# =====================================================================================
def _part_dict(r: PartReport) -> dict:
    return {"path": r.path, "native": r.native, "features": r.features, "warnings": r.warnings,
            "notes": r.notes, "helpers": r.helpers, "steps": r.steps, "volume": r.volume}


def export_part_file(bdoc, path, native=True, visible=False, require_native=False) -> dict:
    """Document -> .SLDPRT in one call (starts / reuses SolidWorks). With require_native
    the known gaps are reported before SolidWorks is started."""
    if require_native and not native:
        raise SolidWorksError("require_native needs native=True")
    hist = _History(bdoc)
    if require_native:
        _require_complete(native_export_issues(bdoc, hist))
    with Session(visible) as s:
        r = export_part(s, bdoc, path, native, require_native=require_native, history=hist)
    return _part_dict(r)


def export_assembly_files(asm, folder, native=True, visible=False, require_native=False) -> dict:
    """Assembly -> folder with .SLDASM + one .SLDPRT per part (starts / reuses SolidWorks).
    With require_native the known gaps are reported before SolidWorks is started."""
    if require_native and not native:
        raise SolidWorksError("require_native needs native=True")
    if require_native:
        _require_complete(assembly_native_issues(asm))
    with Session(visible) as s:
        r = export_assembly(s, asm, folder, native, require_native=require_native,
                            prechecked=True)
    return {"path": r.path, "warnings": r.warnings,
            "parts": {Path(k).name: _part_dict(p) for k, p in r.parts.items()},
            "mates": [{"name": n, "ok": ok, "note": note} for n, ok, note in r.mates]}
