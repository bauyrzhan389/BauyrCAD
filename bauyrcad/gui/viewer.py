"""3D viewport: model display, picking, sketch-plane input and FEA results.

Mouse (SolidWorks-like):
    left click          select / place sketch point
    left drag           sketch / component editing only (never rotates the view)
    middle drag         rotate        (Ctrl/Shift + middle: pan)
    right drag          pan           (right click in sketch: end line chain)
    wheel               zoom about the cursor
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
import pyvista as pv
import vtk
from pyvistaqt import QtInteractor
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from ..core.plane import REFERENCE_PLANES, SketchPlane

BODY_RGB = np.array([176, 186, 200], dtype=np.uint8)
HOVER_RGB = np.array([255, 170, 60], dtype=np.uint8)
SELECT_RGB = np.array([70, 140, 240], dtype=np.uint8)
FIXED_RGB = np.array([60, 190, 90], dtype=np.uint8)

SKETCH_COLOR = "#1c5fd6"
SKETCH_SELECTED = "#ff7a00"
SKETCH_PREVIEW = "#00a0a0"
GRID_MINOR = "#cfd8e3"
GRID_MAJOR = "#a9b7c8"

_VTKIS_NONE, _VTKIS_ROTATE, _VTKIS_PAN = 0, 1, 2

# standard views: (direction towards the viewer, screen up)
VIEWS = {
    "front": ((0, 0, 1), (0, 1, 0)), "back": ((0, 0, -1), (0, 1, 0)),
    "top": ((0, 1, 0), (0, 0, -1)), "bottom": ((0, -1, 0), (0, 0, 1)),
    "right": ((1, 0, 0), (0, 1, 0)), "left": ((-1, 0, 0), (0, 1, 0)),
    "isometric": ((1, 1, 1), (0, 1, 0)),
    "dimetric": ((1, 0.55, 2), (0, 1, 0)), "trimetric": ((1.4, 1, 2.4), (0, 1, 0)),
}


def _rot(axis, deg):
    """Rotation matrix about a unit axis (right-handed, degrees)."""
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    t = math.radians(deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * (K @ K)


def _frame(back, up):
    """Orthonormal camera frame (right, up, back) as matrix columns."""
    b = np.asarray(back, float)
    b = b / np.linalg.norm(b)
    u = np.asarray(up, float)
    u = u - b * (u @ b)
    if np.linalg.norm(u) < 1e-9:                      # up parallel to the view: pick any
        u = np.array([0.0, 0.0, 1.0]) if abs(b[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
        u = u - b * (u @ b)
    u = u / np.linalg.norm(u)
    return np.column_stack([np.cross(u, b), u, b])


def _log_rot(R):
    c = np.clip((np.trace(R) - 1) / 2, -1.0, 1.0)
    th = math.acos(c)
    if th < 1e-9:
        return np.zeros(3)
    if math.pi - th < 1e-6:                            # 180 deg: axis from R + I
        M = (R + np.eye(3)) / 2
        k = np.sqrt(np.maximum(np.diag(M), 0))
        i = int(np.argmax(k))
        for j in range(3):
            if j != i:
                k[j] = M[i, j] / max(k[i], 1e-12)
        return k / np.linalg.norm(k) * th
    return np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * math.sin(th)) * th
CLICK_TOLERANCE_PX = 4
SNAP_PX = 10


@dataclass
class SketchEvent:
    display: tuple            # VTK display pixels (x, y)
    ctrl: bool = False
    shift: bool = False
    dragging: bool = False    # left button held and moved


@dataclass
class PickInfo:
    kind: str | None = None          # "face", "plane", "asm_face" or None
    face: int | None = None
    plane: str | None = None
    point: np.ndarray | None = None
    display: tuple = (0, 0)
    component: str | None = None     # assembly component id (kind == "asm_face")
    topology: tuple | None = None    # explicit Select Other (kind, index, point); do not repick


def _rgb(color):
    if isinstance(color, str):
        c = color.lstrip("#")
        return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return tuple(color)


def nice_step(x: float) -> float:
    """Smallest 1/2/5 x 10^k value >= x."""
    if x <= 0:
        return 1.0
    k = math.floor(math.log10(x))
    for m in (1, 2, 5, 10):
        if m * 10 ** k >= x - 1e-12:
            return m * 10 ** k
    return 10 ** (k + 1)


def _lines_polydata(polylines) -> pv.PolyData:
    pts, lines, off = [], [], 0
    for pl in polylines:
        pl = np.asarray(pl, dtype=float)
        if len(pl) < 2:
            continue
        pts.append(pl)
        lines.append(np.concatenate([[len(pl)], np.arange(off, off + len(pl))]))
        off += len(pl)
    if not pts:
        return pv.PolyData()
    # Note: pv.PolyData(points) would also add a vertex cell per point.
    pd = pv.PolyData()
    pd.points = np.vstack(pts)
    pd.lines = np.concatenate(lines)
    return pd


class Viewer(QWidget):
    clicked = Signal(object)          # PickInfo
    hovered = Signal(object)          # PickInfo
    rightClicked = Signal(object)     # PickInfo
    # sketch mode: raw (unsnapped) plane coordinates + display pixels
    sketchPress = Signal(float, float, object)      # u, v, SketchEvent
    sketchMove = Signal(float, float, object)
    sketchRelease = Signal(float, float, object)
    sketchDouble = Signal(float, float, object)
    sketchFinish = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.plotter = QtInteractor(self, multi_samples=8, line_smoothing=True)
        lay.addWidget(self.plotter.interactor)
        p = self.plotter
        from .appearance import register_viewer
        register_viewer(self)
        p.enable_parallel_projection()
        p.add_axes(interactive=False, line_width=2, color="black")
        for key in ("q", "b", "v", "C", "Up", "Down", "plus", "minus", "S"):
            try:
                p.clear_events_for_key(key)
            except Exception:
                pass
        vtk.vtkMapper.SetResolveCoincidentTopologyToPolygonOffset()
        vtk.vtkMapper.SetResolveCoincidentTopologyPolygonOffsetParameters(1.0, 1.0)
        vtk.vtkMapper.SetResolveCoincidentTopologyLineOffsetParameters(-1.0, -1.0)

        self.style = vtk.vtkInteractorStyleTrackballCamera()
        p.iren.style = self.style
        st = self.style
        st.AddObserver("LeftButtonPressEvent", self._left_down)
        st.AddObserver("LeftButtonReleaseEvent", self._left_up)
        st.AddObserver("MiddleButtonPressEvent", self._middle_down)
        st.AddObserver("MiddleButtonReleaseEvent", self._middle_up)
        st.AddObserver("RightButtonPressEvent", self._right_down)
        st.AddObserver("RightButtonReleaseEvent", self._right_up)
        st.AddObserver("MouseMoveEvent", self._move)
        st.AddObserver("MouseWheelForwardEvent", lambda *a: self._wheel(True))
        st.AddObserver("MouseWheelBackwardEvent", lambda *a: self._wheel(False))
        st.AddObserver("CharEvent", lambda *a: None)      # disable VTK hot-keys
        st.AddObserver("KeyPressEvent", lambda *a: None)

        # keep the camera outside the model before every render (see _guard_camera)
        self.renderer.AddObserver("StartEvent", self._guard_camera)

        self.picker = vtk.vtkCellPicker()
        self.picker.SetTolerance(0.0005)
        self.picker.PickFromListOn()

        self.mode = "select"             # "select" | "sketch"
        self.hover_enabled = True
        self.pick_faces = True
        self.pick_planes = False
        self._press = None
        self._moved = False

        self._body_mesh: pv.PolyData | None = None
        self._body_actor = None
        self._face_overrides: dict[int, np.ndarray] = {}
        self._hover_face: int | None = None
        self._hover_plane: str | None = None
        self._plane_actors: dict[str, vtk.vtkActor] = {}
        self._plane_size = 100.0
        self.extra_planes: dict = {}      # name -> SketchPlane (reference plane features)
        self._plane_sig = None

        self.drag_handler = None          # object with press(info, x, y)/move(x, y)/release(x, y, moved)
        self._asm: dict = {}              # component id -> (actor, mesh, edge actor)
        self._asm_colors: dict = {}       # component id -> base rgb
        self._asm_overrides: dict = {}    # (component id, face or None) -> rgb
        self._asm_hover = None
        self.sketch_plane: SketchPlane | None = None
        self._sketch_layers: set = set()
        self._text_layers: dict = {}
        self._grid_sig = None
        self._grid_step = 1.0
        self.results_visible = False

        self.anim_duration = 0.35         # s; camera transitions (0 = jump)
        self._anim = None
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(15)
        self._anim_timer.timeout.connect(self._anim_step)
        self._numpad_last = None          # (key, camera frame it produced) for repeat presses
        self.normal_to_provider = None    # callable -> SketchPlane | None (numpad 0)

        from .section_view import SectionDisplay
        self.section = SectionDisplay(self)
        self.view_isometric(animate=False)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def renderer(self):
        return self.plotter.renderer

    def render(self):
        from .appearance import sync_model_edges
        sync_model_edges(self)
        if hasattr(self, "section"):
            self.section.sync()
            if self.section.control is not None:
                self.section.control.manipulator.update()
        self.plotter.render()

    def _event_pos(self):
        return self.plotter.iren.interactor.GetEventPosition()

    def _display_to_world(self, x, y, z):
        r = self.renderer
        r.SetDisplayPoint(x, y, z)
        r.DisplayToWorld()
        w = r.GetWorldPoint()
        return np.array(w[:3]) / (w[3] if w[3] else 1.0)

    def _focal_depth(self):
        r = self.renderer
        fp = r.GetActiveCamera().GetFocalPoint()
        r.SetWorldPoint(*fp, 1.0)
        r.WorldToDisplay()
        return r.GetDisplayPoint()[2]

    def _guard_camera(self, *_):
        """Parallel projection clips everything behind the camera, so a camera that ends up
        inside a big model (after Normal To / a view preset / rotating) slices it. Slide the
        camera back along the view direction until it is outside the bounding sphere - that
        changes nothing on screen - and re-fit the clipping range."""
        r = self.renderer
        b = r.ComputeVisiblePropBounds()
        if b[0] > b[1]:
            return
        lo, hi = np.array(b[0::2]), np.array(b[1::2])
        center, radius = (lo + hi) / 2, float(np.linalg.norm(hi - lo)) / 2
        cam = r.GetActiveCamera()
        pos = np.array(cam.GetPosition())
        dop = np.array(cam.GetDirectionOfProjection())
        need = radius * 1.1 + 1.0
        ahead = float((center - pos) @ dop)                  # centre's depth in front of camera
        if ahead < need:
            cam.SetPosition(*(pos - dop * (need - ahead)))
        r.ResetCameraClippingRange()

    def world_per_pixel(self) -> float:
        h = max(self.renderer.GetSize()[1], 1)
        return 2.0 * self.renderer.GetActiveCamera().GetParallelScale() / h

    def project(self, pts) -> np.ndarray:
        """Display pixels (n, 2) and depths (n,) of world points (vectorised)."""
        pts = np.asarray(pts, float).reshape(-1, 3)
        r = self.renderer
        w, h = r.GetSize()
        cam = r.GetActiveCamera()
        M = cam.GetCompositeProjectionTransformMatrix(w / max(h, 1), -1, 1)
        A = np.array([[M.GetElement(i, j) for j in range(4)] for i in range(4)])
        H = np.hstack([pts, np.ones((len(pts), 1))]) @ A.T
        ndc = H[:, :3] / H[:, 3:4]
        x0, y0 = r.GetOrigin()
        disp = np.column_stack([x0 + (ndc[:, 0] + 1) * w / 2, y0 + (ndc[:, 1] + 1) * h / 2])
        dop = np.array(cam.GetDirectionOfProjection())
        return disp, (pts - np.array(cam.GetPosition())) @ dop

    def pick_topology(self, x, y, vertices=None, tol_px=8.0, *, kinds=None, tessellation=None, face_lookup=None):
        """Vertex, edge or face under a pixel (for Measure): ("vertex", i, p) |
        ("edge", i, p) | ("face", i, p) | None. Points and edges hidden behind the body are
        not picked; vertices win over edges, edges over faces."""
        kinds = {'vertex', 'edge', 'face'} if kinds is None else set(kinds)
        info = self.pick(x, y)
        cam = self.renderer.GetActiveCamera()
        dop = np.array(cam.GetDirectionOfProjection())
        pos = np.array(cam.GetPosition())
        wpp = self.world_per_pixel()
        hit_depth = np.inf if info.point is None else float((np.asarray(info.point) - pos) @ dop)
        dtol = 4 * wpp + 1e-6 * max(abs(hit_depth), 1.0) if np.isfinite(hit_depth) else np.inf
        q = np.array([x, y], float)
        if 'vertex' in kinds and vertices is not None and len(vertices):
            disp, depth = self.project(vertices)
            d = np.linalg.norm(disp - q, axis=1)
            ok = (d < tol_px) & (depth <= hit_depth + dtol) & self.section.contains(vertices)
            if np.any(ok):
                i = int(np.argmin(np.where(ok, d, np.inf)))
                return ("vertex", i, np.asarray(vertices[i], float))
        tess = tessellation if tessellation is not None else getattr(self, "_tess", None)
        if 'edge' in kinds and tess is not None and tess.edge_polylines:
            best = None
            segments = ((line, eid) for pl, eid in zip(tess.edge_polylines, getattr(tess, "edge_ids", range(len(tess.edge_polylines))))
                        for line in self.section.lines(pl))
            for pl, eid in segments:
                disp, depth = self.project(pl)
                A, B = disp[:-1], disp[1:]
                AB = B - A
                L2 = np.maximum(np.sum(AB * AB, axis=1), 1e-12)
                t = np.clip(np.sum((q - A) * AB, axis=1) / L2, 0, 1)
                X = A + t[:, None] * AB
                dd = np.linalg.norm(X - q, axis=1)
                k = int(np.argmin(dd))
                if dd[k] < tol_px and (best is None or dd[k] < best[0]):
                    z = depth[k] + t[k] * (depth[k + 1] - depth[k])
                    if z <= hit_depth + dtol:
                        best = (dd[k], eid, pl[k] + t[k] * (pl[k + 1] - pl[k]))
            if best is not None:
                return ("edge", int(best[1]), np.asarray(best[2]))
        if 'face' in kinds and info.kind == "face":
            return ("face", info.face, info.point)
        if 'face' in kinds and info.kind == 'asm_face' and face_lookup is not None:
            index = face_lookup.get((info.component, info.face))
            if index is not None:
                return ('face', index, info.point)
        return None

    def set_overlay(self, name, polylines=(), points=(), color="#ff7a00", width=4.0,
                    labels=()):
        """Highlight layer on top of the model: thick polylines, points and text labels
        [(xyz, text)] (Measure uses it)."""
        for suffix in ("_lines", "_pts"):
            self.plotter.remove_actor(name + suffix, render=False)
        for a in getattr(self, "_overlay_text", {}).pop(name, []):
            self.renderer.RemoveActor(a)
        pd = _lines_polydata(list(polylines))
        if pd.n_points:
            a = self.plotter.add_mesh(pd, color=color, line_width=width, name=name + "_lines",
                                      pickable=False, render=False, reset_camera=False)
            a.GetProperty().SetRenderLinesAsTubes(False)
        pts = np.asarray(points, float).reshape(-1, 3)
        if len(pts):
            self.plotter.add_points(pts, color=color, point_size=12, render_points_as_spheres=True,
                                    name=name + "_pts", pickable=False, render=False,
                                    reset_camera=False)
        acts = []
        for xyz, text in labels:
            t = vtk.vtkBillboardTextActor3D()
            t.SetInput(text)
            t.SetPosition(*xyz)
            tp = t.GetTextProperty()
            tp.SetFontSize(15)
            tp.SetColor(0.1, 0.1, 0.1)
            tp.SetBackgroundColor(1.0, 0.97, 0.85)
            tp.SetBackgroundOpacity(0.95)
            tp.SetFrame(True)
            tp.SetFrameColor(0.85, 0.5, 0.1)
            t.PickableOff()
            self.renderer.AddActor(t)
            acts.append(t)
        if not hasattr(self, "_overlay_text"):
            self._overlay_text = {}
        self._overlay_text[name] = acts
        self.render()

    def world_to_display(self, p):
        r = self.renderer
        r.SetWorldPoint(float(p[0]), float(p[1]), float(p[2]), 1.0)
        r.WorldToDisplay()
        return np.array(r.GetDisplayPoint()[:2])

    # ------------------------------------------------------------------
    # mouse handling
    # ------------------------------------------------------------------
    def _sketch_event(self, x, y, dragging=False):
        iren = self.plotter.iren.interactor
        return SketchEvent((x, y), bool(iren.GetControlKey()), bool(iren.GetShiftKey()), dragging)

    def _left_down(self, obj, ev):
        self.stop_animation()
        self._section_drag=False
        control=self.section.control
        if control is not None and control.manipulator.press(*self._event_pos()):
            self._section_drag=True
            self._press=None
            return
        self._press = self._event_pos()
        self._moved = False
        if self.mode == "sketch":
            x, y = self._press
            uv = self._sketch_uv(x, y)
            if uv is None:
                return
            now = time.monotonic()
            last = getattr(self, "_last_click", None)
            self._last_click = (now, x, y)
            if last and now - last[0] < 0.35 and abs(x - last[1]) < 5 and abs(y - last[2]) < 5:
                self._last_click = None
                self.sketchDouble.emit(*uv, self._sketch_event(x, y))
                return
            self.sketchPress.emit(*uv, self._sketch_event(x, y))
            return
        self._hdrag = False
        if self.drag_handler is not None:
            x, y = self._press
            if self.drag_handler.press(self.pick(x, y), x, y, "left"):
                self._hdrag = True
                return
        # Left drag is reserved for selection/editing, never camera rotation.

    def _left_up(self, obj, ev):
        if getattr(self,'_section_drag',False):
            self._section_drag=False
            self.section.control.manipulator.release()
            self._press=None
            return
        if self.mode == "sketch":
            x, y = self._event_pos()
            uv = self._sketch_uv(x, y)
            if uv is not None and self._press is not None:
                self.sketchRelease.emit(*uv, self._sketch_event(x, y, self._moved))
            self._press = None
            return
        if getattr(self, "_hdrag", False):
            self._hdrag = False
            x, y = self._event_pos()
            self.drag_handler.release(x, y, self._moved)
            if not self._moved:
                self.clicked.emit(self.pick(x, y))
            self._press = None
            return
        if self._press is not None and not self._moved:
            self.clicked.emit(self.pick(*self._event_pos()))
        self._press = None

    def _middle_down(self, obj, ev):
        self.stop_animation()
        self._orbit_pivot = None
        x, y = self._event_pos()
        self.style.FindPokedRenderer(x, y)
        iren = self.plotter.iren.interactor
        if iren.GetControlKey() or iren.GetShiftKey():
            self.style.StartPan()
        else:
            self._orbit_pivot = self.rotation_pivot(x, y)
            self._orbit_last = np.array([x, y], float)
            self.style.StartRotate()

    def _middle_up(self, obj, ev):
        s = self.style.GetState()
        if s == _VTKIS_ROTATE:
            self.style.EndRotate()
        elif s == _VTKIS_PAN:
            self.style.EndPan()
        self._orbit_pivot = None

    def rotation_pivot(self, x, y):
        """Visible model surface under the cursor, or its closest projected surface point.

        Ignore planes, highlights and selection filters. Pick only once per drag;
        assembly actor transforms and visible result/preview surfaces are respected.
        """
        candidates = [self._body_actor] + [entry[0] for entry in self._asm.values()]
        candidates += self.section.caps
        candidates += [self.plotter.actors.get(name) for name in ("results", "preview")]
        actors = [a for a in candidates if a is not None and a.GetVisibility()]
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.00001)
        picker.PickFromListOn()
        old_flags = [a.GetPickable() for a in actors]
        try:
            for actor in actors:
                actor.PickableOn()
                picker.AddPickList(actor)
            if picker.Pick(x, y, 0, self.renderer):
                return np.array(picker.GetPickPosition())
        finally:
            for actor, flag in zip(actors, old_flags):
                actor.SetPickable(flag)
        # Off the silhouette (or in a hole): nearest projected triangle edge, with
        # depth breaking ties so a front surface wins over a coincident back edge.
        best, pivot = (float("inf"), float("inf")), None
        q = np.array([x, y], float)
        for actor in actors:
            data = actor.GetMapper().GetInput()
            if data is None or not data.GetNumberOfPoints():
                continue
            mesh = pv.wrap(data).triangulate()
            if not mesh.GetNumberOfPolys():
                continue
            matrix = actor.GetMatrix()
            transform = np.array([[matrix.GetElement(i, j) for j in range(4)] for i in range(4)])
            world = mesh.points @ transform[:3, :3].T + transform[:3, 3]
            if self.section.active and (actor is self._body_actor or
                                        any(actor is entry[0] for entry in self._asm.values())):
                mesh = pv.PolyData(world, mesh.faces).clip(normal=self.section.plane.GetNormal(),
                    origin=self.section.plane.GetOrigin(), invert=False).triangulate()
                if not mesh.n_cells:
                    continue
                world = mesh.points
            xy, depth = self.project(world)
            triangles = mesh.faces.reshape(-1, 4)[:, 1:]
            for start in range(0, len(triangles), 4096):
                tri = triangles[start:start + 4096]
                ia, ib = tri.ravel(), tri[:, [1, 2, 0]].ravel()
                delta = xy[ib] - xy[ia]
                t = np.clip(np.sum((q - xy[ia]) * delta, axis=1) /
                            np.maximum(np.sum(delta * delta, axis=1), 1e-20), 0, 1)
                distance = np.linalg.norm(xy[ia] + t[:, None] * delta - q, axis=1)
                z = depth[ia] + t * (depth[ib] - depth[ia])
                distance[z <= 0] = np.inf
                nearest = distance.min()
                if not np.isfinite(nearest):
                    continue
                ties = np.flatnonzero(distance <= nearest + 1e-7)
                k = ties[np.argmin(z[ties])]
                if distance[k] < best[0] - 1e-7 or (abs(distance[k] - best[0]) <= 1e-7 and z[k] < best[1]):
                    best = float(distance[k]), float(z[k])
                    pivot = world[ia[k]] + t[k] * (world[ib[k]] - world[ia[k]])
        return pivot if pivot is not None else np.array(self.renderer.GetActiveCamera().GetFocalPoint())

    def _orbit_move(self, x, y):
        cam = self.renderer.GetActiveCamera()
        old_frame, focal = self.camera_frame(), np.array(cam.GetFocalPoint())
        delta = np.array([x, y], float) - self._orbit_last
        self._orbit_last = np.array([x, y], float)
        width, height = self.renderer.GetSize()
        factor = -20.0 * self.style.GetMotionFactor()
        cam.Azimuth(factor * delta[0] / max(width, 1))
        cam.Elevation(factor * delta[1] / max(height, 1))
        cam.OrthogonalizeViewUp()
        rotation = self.camera_frame() @ old_frame.T
        offset = self._orbit_pivot - focal
        shift = offset - rotation @ offset
        cam.SetFocalPoint(*(focal + shift))
        cam.SetPosition(*(np.array(cam.GetPosition()) + shift))
        self.renderer.ResetCameraClippingRange()
        self.renderer.UpdateLightsGeometryToFollowCamera()
        if self.mode == "sketch":
            self._update_grid()
        self.render()

    def _right_down(self, obj, ev):
        self.stop_animation()
        x, y = self._event_pos()
        self._rpress = (x, y)
        self._rmoved = False
        self._rhdrag = False
        if self.mode != "sketch" and self.drag_handler is not None:
            if self.drag_handler.press(self.pick(x, y), x, y, "right"):
                self._rhdrag = True
                return
        self.style.FindPokedRenderer(x, y)
        self.style.StartPan()

    def _right_up(self, obj, ev):
        if getattr(self, "_rhdrag", False):
            self._rhdrag = False
            x, y = self._event_pos()
            moved = getattr(self, "_rmoved", False)
            self.drag_handler.release(x, y, moved)
            if not moved:
                self.rightClicked.emit(self.pick(x, y))   # plain right-click: context menu
            self._rpress = None
            return
        if self.style.GetState() == _VTKIS_PAN:
            self.style.EndPan()
        if not getattr(self, "_rmoved", True):
            if self.mode == "sketch":
                self.sketchFinish.emit()
            else:
                self.rightClicked.emit(self.pick(*self._event_pos()))

    def _move(self, obj, ev):
        x, y = self._event_pos()
        if getattr(self,'_section_drag',False):
            self.section.control.manipulator.move(x,y)
            return
        if self._press is not None and max(abs(x - self._press[0]), abs(y - self._press[1])) > CLICK_TOLERANCE_PX:
            self._moved = True
        rp = getattr(self, "_rpress", None)
        if rp is not None and max(abs(x - rp[0]), abs(y - rp[1])) > CLICK_TOLERANCE_PX:
            self._rmoved = True
        if getattr(self, "_hdrag", False):
            if self._moved:
                self.drag_handler.move(x, y)
            return
        if getattr(self, "_rhdrag", False):
            if getattr(self, "_rmoved", False):
                self.drag_handler.move(x, y)
            return
        if self.style.GetState() == _VTKIS_ROTATE and getattr(self, "_orbit_pivot", None) is not None:
            self._orbit_move(x, y)
            return
        self.style.OnMouseMove()
        if self.style.GetState() != _VTKIS_NONE:
            if self.mode == "sketch":
                self._update_grid()
            return
        if self.mode == "sketch":
            self._update_grid()
            uv = self._sketch_uv(x, y)
            if uv is not None:
                self.sketchMove.emit(*uv, self._sketch_event(x, y, self._press is not None and self._moved))
        elif self.hover_enabled:
            info = self.pick(x, y)
            face = info.face if info.kind == "face" else None
            plane = info.plane if info.kind == "plane" else None
            asm_hover = (info.component, info.face) if info.kind == "asm_face" else None
            if face != self._hover_face or plane != self._hover_plane:
                self._hover_face, self._hover_plane = face, plane
                self._apply_face_colors()
                self._apply_plane_colors()
                self.render()
            if asm_hover != self._asm_hover:
                self._asm_hover = asm_hover
                self._apply_assembly_colors()
                self.render()
            self.hovered.emit(info)

    def _wheel(self, forward: bool):
        self.stop_animation()
        x, y = self._event_pos()
        cam = self.renderer.GetActiveCamera()
        z = self._focal_depth()
        before = self._display_to_world(x, y, z)
        cam.SetParallelScale(cam.GetParallelScale() * (0.85 if forward else 1 / 0.85))
        after = self._display_to_world(x, y, z)
        d = before - after
        cam.SetFocalPoint(*(np.array(cam.GetFocalPoint()) + d))
        cam.SetPosition(*(np.array(cam.GetPosition()) + d))
        self.renderer.ResetCameraClippingRange()
        if self.mode == "sketch":
            self._update_grid()
        self.render()

    # ------------------------------------------------------------------
    # picking
    # ------------------------------------------------------------------
    def _pick_meshes(self):
        if not self.pick_faces or self.results_visible:
            return
        if self._body_actor is not None and self._body_actor.GetVisibility():
            yield None, self._body_actor, self._body_mesh
        for cid, (actor, mesh, _) in self._asm.items():
            if actor.GetVisibility():
                yield cid, actor, mesh

    @staticmethod
    def _actor_points(actor, points):
        matrix = actor.GetMatrix()
        a = np.array([[matrix.GetElement(i, j) for j in range(4)] for i in range(4)])
        return np.asarray(points) @ a[:3, :3].T + a[:3, 3]

    def pick_candidates(self, x, y, kinds=("face",), vertices=None):
        """Explicit depth selection: includes occluded geometry of visible objects."""
        from ..core.picking import ray_faces
        start = self._display_to_world(x, y, 0.0)
        end = self._display_to_world(x, y, 1.0)
        found = []
        if "face" in kinds:
            for cid, actor, mesh in self._pick_meshes():
                points = self._actor_points(actor, mesh.points)
                triangles = mesh.faces.reshape(-1, 4)[:, 1:]
                for face, depth, point in ray_faces(points, triangles, mesh.cell_data["face_id"], start, end):
                    if not self.section.contains(point):
                        continue
                    info = PickInfo(kind="asm_face" if cid is not None else "face", face=face,
                                    point=point, display=(x, y), component=cid,
                                    topology=("face", face, point))
                    found.append((1, depth, info))
        visible_body = (self.pick_faces and not self.results_visible and self._body_actor is not None
                        and self._body_actor.GetVisibility())
        tolerance = 8 * self.devicePixelRatioF()
        if visible_body and "edge" in kinds and self._tess is not None:
            segments = ((line, eid) for pl, eid in zip(self._tess.edge_polylines, self._tess.edge_ids)
                        for line in self.section.lines(pl))
            for pl, eid in segments:
                xy, depth = self.project(pl)
                delta = xy[1:] - xy[:-1]
                t = np.clip(np.sum((np.array([x, y]) - xy[:-1]) * delta, axis=1)
                            / np.maximum(np.sum(delta * delta, axis=1), 1e-12), 0, 1)
                distances = np.linalg.norm(xy[:-1] + t[:, None] * delta - (x, y), axis=1)
                valid = (depth[:-1] > 0) & (depth[1:] > 0)
                if not len(distances) or not np.any(valid):
                    continue
                k = int(np.argmin(np.where(valid, distances, np.inf)))
                if distances[k] < tolerance:
                    point = pl[k] + t[k] * (pl[k + 1] - pl[k])
                    found.append((0, float(distances[k]), PickInfo(kind="edge", point=point,
                                 display=(x, y), topology=("edge", int(eid), point))))
        if visible_body and "vertex" in kinds and vertices is not None and len(vertices):
            xy, depth = self.project(vertices)
            distances = np.linalg.norm(xy - (x, y), axis=1)
            for i in np.flatnonzero((distances < tolerance) & (depth > 0) & self.section.contains(vertices)):
                point = np.asarray(vertices[i])
                found.append((-1, float(distances[i]), PickInfo(kind="vertex", point=point,
                             display=(x, y), topology=("vertex", int(i), point))))
        found.sort(key=lambda h: (h[0], h[1]))
        return [h[2] for h in found]

    def preview_candidate(self, info=None):
        """Screen-space outline stays visible even when the chosen face is occluded."""
        old = getattr(self, "_candidate_outline", None)
        if old is not None:
            self.renderer.RemoveViewProp(old)
            self._candidate_outline = None
        lines = []
        if info is not None and info.kind in ("face", "asm_face"):
            for cid, actor, mesh in self._pick_meshes():
                if cid == info.component:
                    subset = mesh.extract_cells(np.flatnonzero(mesh.cell_data["face_id"] == info.face))
                    edges = subset.extract_surface(algorithm="dataset_surface").extract_feature_edges()
                    points = self._actor_points(actor, edges.points)
                    ids, offset = edges.lines, 0
                    while offset < len(ids):
                        count = ids[offset]
                        lines.append(points[ids[offset + 1:offset + 1 + count]])
                        offset += count + 1
        elif info is not None and info.kind == "edge":
            lines = [pl for pl, eid in zip(self._tess.edge_polylines, self._tess.edge_ids)
                     if eid == info.topology[1]]
        elif info is not None and info.kind == "vertex":
            xy, _ = self.project([info.point])
            x, y = xy[0]
            # Already projected below through a separate small display-space cross.
            lines = []
        screen_lines = [np.column_stack([self.project(pl)[0], np.zeros(len(pl))]) for pl in lines]
        if info is not None and info.kind == "vertex":
            screen_lines = [np.array([[x - 6, y, 0], [x + 6, y, 0]]),
                            np.array([[x, y - 6, 0], [x, y + 6, 0]])]
        if screen_lines:
            mapper = vtk.vtkPolyDataMapper2D()
            mapper.SetInputData(_lines_polydata(screen_lines))
            actor = vtk.vtkActor2D()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(1.0, 0.45, 0.0)
            actor.GetProperty().SetLineWidth(4)
            actor.PickableOff()
            self.renderer.AddViewProp(actor)
            self._candidate_outline = actor
        self.render()

    def select_other(self, display, kinds=("face",), vertices=None, receiver=None, names=None):
        self.close_selection_menu()
        menu = QMenu("Select Other", self)
        self._selection_menu = menu
        candidates = self.pick_candidates(*display, kinds=kinds, vertices=vertices)
        menu.addSection("Select Other — hover to preview")
        for info in candidates:
            kind, index, _ = info.topology
            prefix = (names or {}).get(info.component, info.component or "")
            action = menu.addAction(f"{prefix + ' — ' if prefix else ''}{kind.title()} {index + 1}")
            action.setData(info)
            action.hovered.connect(lambda info=info: self.preview_candidate(info))
            action.triggered.connect(lambda checked=False, info=info: (receiver or self.clicked.emit)(info))
        if not candidates:
            menu.addAction("No matching geometry here").setEnabled(False)
        menu.aboutToHide.connect(lambda: self.preview_candidate())
        menu.aboutToHide.connect(menu.deleteLater)
        menu.aboutToHide.connect(lambda: setattr(self, "_selection_menu", None))
        menu.popup(QCursor.pos())
        return menu

    def close_selection_menu(self):
        old = getattr(self, "_selection_menu", None)
        if old is not None:
            old.close()

    def pick(self, x, y) -> PickInfo:
        info = PickInfo(display=(x, y))
        self.picker.InitializePickList()
        n = 0
        if self.pick_faces and self._body_actor is not None and not self.results_visible:
            self.picker.AddPickList(self._body_actor)
            n += 1
        if self.pick_planes:
            for a in self._plane_actors.values():
                if a.GetVisibility():
                    self.picker.AddPickList(a)
                    n += 1
        if self.pick_faces:
            for actor, _, _ in self._asm.values():
                self.picker.AddPickList(actor)
                n += 1
        if self.pick_faces and self.section.active:
            for cap in self.section.caps:
                self.picker.AddPickList(cap)
                n += 1
        if n == 0 or not self.picker.Pick(x, y, 0, self.renderer):
            return info
        actor = self.picker.GetActor()
        info.point = np.array(self.picker.GetPickPosition())
        if actor in self.section.caps:
            info.kind = "section_cap"
            return info
        for cid, (a, mesh, _) in self._asm.items():
            if a is actor:
                cell = self.picker.GetCellId()
                if cell >= 0:
                    info.kind, info.component = "asm_face", cid
                    info.face = int(mesh.cell_data["face_id"][cell])
                return info
        if actor is self._body_actor:
            cid = self.picker.GetCellId()
            if cid >= 0:
                info.kind = "face"
                info.face = int(self._body_mesh.cell_data["face_id"][cid])
        else:
            for name, a in self._plane_actors.items():
                if a is actor:
                    info.kind, info.plane = "plane", name
        return info

    # ------------------------------------------------------------------
    # body
    # ------------------------------------------------------------------
    def set_body(self, tess):
        self.close_selection_menu()
        self.plotter.remove_actor("body", render=False)
        self.plotter.remove_actor("body_edges", render=False)
        self._body_actor = None
        self._body_mesh = None
        self._tess = None
        self._hover_face = None
        if tess is None or len(tess.triangles) == 0:
            self.render()
            return
        faces = np.hstack([np.full((len(tess.triangles), 1), 3), tess.triangles]).ravel()
        mesh = pv.PolyData(tess.points, faces)
        mesh.cell_data["face_id"] = tess.face_ids
        mesh.cell_data["rgb"] = np.tile(BODY_RGB, (len(tess.triangles), 1))
        mesh.point_data["Normals"] = tess.normals
        mesh.point_data.active_normals_name = "Normals"
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(mesh)
        mapper.SetScalarModeToUseCellFieldData()
        mapper.SelectColorArray("rgb")
        mapper.SetColorModeToDirectScalars()
        mapper.ScalarVisibilityOn()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetInterpolationToPhong()
        prop.SetAmbient(0.38)
        prop.SetDiffuse(0.72)
        prop.SetSpecular(0.25)
        prop.SetSpecularPower(30)
        self.plotter.add_actor(actor, name="body", reset_camera=False, render=False)
        self._body_actor, self._body_mesh = actor, mesh
        self._tess = tess
        edges = _lines_polydata(tess.edge_polylines)
        if edges.n_points:
            self.plotter.add_mesh(edges, color="#1a1a1a", line_width=1.6, name="body_edges",
                                  pickable=False, render=False, reset_camera=False)
        self._apply_face_colors()
        self.render()

    def set_body_visible(self, visible: bool):
        for name in ("body", "body_edges"):
            a = self.plotter.actors.get(name)
            if a is not None:
                a.SetVisibility(visible)

    def set_face_colors(self, overrides: dict[int, np.ndarray]):
        self._face_overrides = dict(overrides)
        self._apply_face_colors()
        self.render()

    def _apply_face_colors(self):
        if self._body_mesh is None:
            return
        fid = self._body_mesh.cell_data["face_id"]
        rgb = np.tile(BODY_RGB, (len(fid), 1))
        for f, c in self._face_overrides.items():
            rgb[fid == f] = c
        if self._hover_face is not None and self.hover_enabled and self.pick_faces:
            rgb[fid == self._hover_face] = HOVER_RGB
        self._body_mesh.cell_data["rgb"][:] = rgb
        self._body_mesh.Modified()

    def clear_hover(self):
        self._hover_face = self._hover_plane = None
        self._apply_face_colors()
        self._apply_plane_colors()

    # ------------------------------------------------------------------
    # reference planes
    # ------------------------------------------------------------------
    def all_planes(self) -> dict:
        return {**REFERENCE_PLANES, **self.extra_planes}

    def show_reference_planes(self, visible: bool, size: float = 100.0):
        sig = (round(size, 9), tuple((n, tuple(np.round(p.origin, 9)), tuple(np.round(p.normal, 9)))
                                     for n, p in self.extra_planes.items()))
        if visible and (not self._plane_actors or sig != self._plane_sig):
            self._plane_sig = sig
            self._plane_size = size
            for name in list(self._plane_actors):
                self.plotter.remove_actor(f"plane_{name}", render=False)
                self.plotter.remove_actor(f"plane_lbl_{name}", render=False)
            self._plane_actors = {}
            for name, pl in self.all_planes().items():
                h = size / 2
                corners = [pl.to_world(u, v) for u, v in ((-h, -h), (h, -h), (h, h), (-h, h))]
                quad = pv.PolyData(np.array(corners), faces=[4, 0, 1, 2, 3])
                actor = self.plotter.add_mesh(quad, color="#6a9ad6", opacity=0.18, name=f"plane_{name}",
                                              show_edges=True, edge_color="#3f6fae", line_width=1.5,
                                              render=False, reset_camera=False)
                self._plane_actors[name] = actor
                txt = vtk.vtkBillboardTextActor3D()
                txt.SetInput(name.replace(" Plane", ""))
                txt.SetPosition(*pl.to_world(-h, h))
                txt.GetTextProperty().SetFontSize(15)
                txt.GetTextProperty().SetColor(0.16, 0.30, 0.49)
                txt.PickableOff()
                self.plotter.add_actor(txt, name=f"plane_lbl_{name}", reset_camera=False,
                                       render=False)
        for name, a in self._plane_actors.items():
            a.SetVisibility(visible)
            lbl = self.plotter.actors.get(f"plane_lbl_{name}")
            if lbl is not None:
                lbl.SetVisibility(visible)
        self.pick_planes = visible
        self.render()

    def highlight_plane(self, name: str | None, size: float = 100.0):
        """Show one reference plane selected (e.g. picked in the tree), or none."""
        if name is None:
            self.show_reference_planes(False)
            return
        self.show_reference_planes(True, size)
        for n, a in self._plane_actors.items():
            a.SetVisibility(n == name)
            lbl = self.plotter.actors.get(f"plane_lbl_{n}")
            if lbl is not None:
                lbl.SetVisibility(n == name)
            if n == name:
                a.GetProperty().SetColor(0.27, 0.55, 0.94)
                a.GetProperty().SetOpacity(0.3)
        self.render()

    def _apply_plane_colors(self):
        for name, a in self._plane_actors.items():
            hot = name == self._hover_plane
            a.GetProperty().SetColor((1.0, 0.66, 0.24) if hot else (0.42, 0.6, 0.84))
            a.GetProperty().SetOpacity(0.35 if hot else 0.18)

    # ------------------------------------------------------------------
    # sketches
    # ------------------------------------------------------------------
    def set_passive_sketches(self, polylines3d):
        """Sketches that are shown but not being edited."""
        self.plotter.remove_actor("passive_sketches", render=False)
        pd = _lines_polydata(polylines3d)
        if pd.n_points:
            self.plotter.add_mesh(pd, color="#4d6f99", line_width=1.5, name="passive_sketches",
                                  pickable=False, render=False, reset_camera=False)
        self.render()

    def enter_sketch_mode(self, plane: SketchPlane):
        self.mode = "sketch"
        self.sketch_plane = plane
        self.hover_enabled = False
        self.clear_hover()
        self._grid_sig = None
        self.view_normal_to(plane)
        self._update_grid(force=True)
        self._draw_sketch_axes()
        self.render()

    def _lifted(self, uv, k: float) -> np.ndarray:
        """Plane points moved k pixels towards the viewer so they draw on top of the grid
        and of coincident model faces."""
        pl = self.sketch_plane
        return pl.to_world_many(uv) + pl.normal * (k * self.world_per_pixel())

    def _draw_sketch_axes(self):
        L = 50 * self.world_per_pixel()
        pts = self._lifted([(0, 0), (L, 0), (0, 0), (0, L)], 1.0)
        axes = pv.PolyData()
        axes.points = pts
        axes.lines = [2, 0, 1, 2, 2, 3]
        axes.cell_data["c"] = np.array([[220, 40, 40], [40, 160, 40]], dtype=np.uint8)
        self.plotter.add_mesh(axes, scalars="c", rgb=True, line_width=2.5, name="sketch_axes",
                              pickable=False, render=False, reset_camera=False,
                              show_scalar_bar=False)

    def exit_sketch_mode(self):
        self.mode = "select"
        self.sketch_plane = None
        self.hover_enabled = True
        for name in ("sketch_grid_minor", "sketch_grid_major", "sketch_axes"):
            self.plotter.remove_actor(name, render=False)
        self.clear_sketch_layers()
        self.render()

    def _sketch_uv(self, x, y):
        """Raw (unsnapped) sketch-plane coordinates under display pixel (x, y)."""
        pl = self.sketch_plane
        if pl is None:
            return None
        hit = pl.intersect_ray(self._display_to_world(x, y, 0.0), self._display_to_world(x, y, 1.0))
        if hit is None:
            return None
        return pl.to_local(hit)

    def sketch_to_display(self, uv):
        return self.world_to_display(self.sketch_plane.to_world(*uv))

    def grid_step(self) -> float:
        return self._grid_step

    def _update_grid(self, force=False):
        pl = self.sketch_plane
        if pl is None:
            return
        wpp = self.world_per_pixel()
        step = nice_step(18 * wpp)
        cam = self.renderer.GetActiveCamera()
        fu, fv = pl.to_local(cam.GetFocalPoint())
        w, h = self.renderer.GetSize()
        half = 0.6 * max(w, h) * wpp
        major = step * 5
        sig = (step, round(fu / major), round(fv / major), round(half / major))
        if sig == self._grid_sig and not force:
            return
        self._grid_sig, self._grid_step = sig, step
        u0, u1 = (math.floor((fu - half) / major) * major, math.ceil((fu + half) / major) * major)
        v0, v1 = (math.floor((fv - half) / major) * major, math.ceil((fv + half) / major) * major)
        minor, majr = [], []
        for u in np.arange(u0, u1 + step / 2, step):
            (majr if abs(u / major - round(u / major)) < 1e-6 else minor).append([(u, v0), (u, v1)])
        for v in np.arange(v0, v1 + step / 2, step):
            (majr if abs(v / major - round(v / major)) < 1e-6 else minor).append([(u0, v), (u1, v)])
        for name, segs, color in (("sketch_grid_minor", minor, GRID_MINOR),
                                  ("sketch_grid_major", majr, GRID_MAJOR)):
            self.plotter.remove_actor(name, render=False)
            if segs:
                pd = _lines_polydata([pl.to_world_many(s) for s in segs])
                a = self.plotter.add_mesh(pd, color=color, line_width=1, name=name, pickable=False,
                                          render=False, reset_camera=False)
                a.GetProperty().SetOpacity(0.8)

    # ---- assembly components ---------------------------------------------------------------
    @staticmethod
    def _vtk_matrix(m):
        vm = vtk.vtkMatrix4x4()
        for i in range(4):
            for j in range(4):
                vm.SetElement(i, j, float(m[i][j]))
        return vm

    def set_assembly(self, items):
        """items: [(component id, tessellation (part coords), 4x4 pose, rgb)]."""
        self.close_selection_menu()
        for cid, (a, _, e) in self._asm.items():
            self.renderer.RemoveActor(a)
            if e is not None:
                self.renderer.RemoveActor(e)
        self._asm, self._asm_colors = {}, {}
        for cid, tess, matrix, rgb in items:
            if tess is None or not len(tess.triangles):
                continue
            faces = np.hstack([np.full((len(tess.triangles), 1), 3), tess.triangles]).ravel()
            mesh = pv.PolyData(tess.points, faces)
            mesh.cell_data["face_id"] = tess.face_ids
            mesh.cell_data["rgb"] = np.tile(np.asarray(rgb, np.uint8), (len(tess.triangles), 1))
            mesh.point_data["Normals"] = tess.normals
            mesh.point_data.active_normals_name = "Normals"
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(mesh)
            mapper.SetScalarModeToUseCellFieldData()
            mapper.SelectColorArray("rgb")
            mapper.SetColorModeToDirectScalars()
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            prop.SetInterpolationToPhong()
            prop.SetAmbient(0.38)
            prop.SetDiffuse(0.72)
            prop.SetSpecular(0.25)
            prop.SetSpecularPower(30)
            vm = self._vtk_matrix(matrix)
            actor.SetUserMatrix(vm)
            self.renderer.AddActor(actor)
            edge_actor = None
            edges = _lines_polydata(tess.edge_polylines)
            if edges.n_points:
                em = vtk.vtkPolyDataMapper()
                em.SetInputData(edges)
                edge_actor = vtk.vtkActor()
                edge_actor.SetMapper(em)
                edge_actor.GetProperty().SetColor(0.1, 0.1, 0.1)
                edge_actor.GetProperty().SetLineWidth(1.5)
                edge_actor.PickableOff()
                edge_actor.SetUserMatrix(vm)
                self.renderer.AddActor(edge_actor)
            self._asm[cid] = (actor, mesh, edge_actor)
            self._asm_colors[cid] = np.asarray(rgb, np.uint8)
        self._apply_assembly_colors()
        self.render()

    def set_assembly_pose(self, cid, matrix):
        self.close_selection_menu()
        entry = self._asm.get(cid)
        if entry is None:
            return
        vm = self._vtk_matrix(matrix)
        entry[0].SetUserMatrix(vm)
        if entry[2] is not None:
            entry[2].SetUserMatrix(vm)

    def set_assembly_overrides(self, overrides: dict):
        """{(component id, face index or None for the whole component): rgb}."""
        self._asm_overrides = dict(overrides)
        self._apply_assembly_colors()
        self.render()

    def set_assembly_opacities(self, overrides=None):
        """Temporary Evaluate transparency; omitted components return to full opacity."""
        for cid, (actor, _, edges) in self._asm.items():
            opacity = float((overrides or {}).get(cid, 1.0))
            actor.GetProperty().SetOpacity(opacity)
            if edges is not None:
                edges.GetProperty().SetOpacity(opacity)
        self.render()

    def _apply_assembly_colors(self):
        for cid, (_, mesh, _) in self._asm.items():
            fid = mesh.cell_data["face_id"]
            rgb = np.tile(self._asm_colors[cid], (len(fid), 1))
            if (cid, None) in self._asm_overrides:
                rgb[:] = self._asm_overrides[(cid, None)]
            for (c, f), col in self._asm_overrides.items():
                if c == cid and f is not None:
                    rgb[fid == f] = col
            if self._asm_hover is not None and self._asm_hover[0] == cid and self.hover_enabled:
                rgb[fid == self._asm_hover[1]] = HOVER_RGB
            mesh.cell_data["rgb"][:] = rgb
            mesh.Modified()

    def ray_plane(self, display, point, normal=None):
        """World point under a display pixel on the plane through `point` (default: facing
        the camera)."""
        x, y = display
        p0, p1 = self._display_to_world(x, y, 0.0), self._display_to_world(x, y, 1.0)
        n = np.asarray(normal if normal is not None else
                       self.renderer.GetActiveCamera().GetDirectionOfProjection(), float)
        d = p1 - p0
        den = float(np.dot(d, n))
        if abs(den) < 1e-12:
            return None
        return p0 + d * float(np.dot(np.asarray(point, float) - p0, n)) / den

    def camera_axes(self):
        """(right, up, view direction) unit vectors of the current camera."""
        cam = self.renderer.GetActiveCamera()
        d = np.array(cam.GetDirectionOfProjection())
        up = np.array(cam.GetViewUp())
        right = np.cross(d, up)
        right /= np.linalg.norm(right)
        return right, np.cross(right, d), d

    # ---- generic sketch drawing layers (all coordinates in plane u, v) ----
    def sketch_layer(self, name, polylines, color, width=2.0, lift=1.5, opacity=1.0):
        self.plotter.remove_actor(name, render=False)
        pl = self.sketch_plane
        if pl is None:
            return
        pd = _lines_polydata([self._lifted(p, lift) for p in polylines if len(p) >= 2])
        if pd.n_points:
            a = self.plotter.add_mesh(pd, color=color, line_width=width, name=name, pickable=False,
                                      render=False, reset_camera=False)
            if opacity < 1:
                a.GetProperty().SetOpacity(opacity)
        self._sketch_layers.add(name)

    def sketch_points(self, name, pts, color, size=7.0, lift=2.0):
        self.plotter.remove_actor(name, render=False)
        pts = np.asarray(pts, float).reshape(-1, 2)
        if self.sketch_plane is not None and len(pts):
            self.plotter.add_points(self._lifted(pts, lift), color=color, point_size=size,
                                    render_points_as_spheres=True, name=name, pickable=False,
                                    render=False, reset_camera=False)
        self._sketch_layers.add(name)

    def sketch_texts(self, name, items):
        """items: list of (uv, text, color, font_size, background or None)."""
        for a in self._text_layers.pop(name, []):
            self.renderer.RemoveActor(a)
        actors = []
        for uv, text, color, size, bg in items:
            t = vtk.vtkBillboardTextActor3D()
            t.SetInput(text)
            t.SetPosition(*self._lifted([uv], 3.0)[0])
            tp = t.GetTextProperty()
            tp.SetFontSize(int(size))
            tp.SetColor(*_rgb(color))
            tp.SetJustificationToCentered()
            tp.SetVerticalJustificationToCentered()
            tp.SetBold(False)
            if bg:
                tp.SetBackgroundColor(*_rgb(bg))
                tp.SetBackgroundOpacity(0.85)
                tp.SetFrame(True)
                tp.SetFrameColor(*_rgb(color))
            t.PickableOff()
            self.renderer.AddActor(t)
            actors.append(t)
        self._text_layers[name] = actors

    def clear_sketch_layers(self):
        for name in list(self._sketch_layers):
            self.plotter.remove_actor(name, render=False)
        self._sketch_layers.clear()
        for name in list(self._text_layers):
            self.sketch_texts(name, [])

    # ------------------------------------------------------------------
    # previews & boundary-condition symbols
    # ------------------------------------------------------------------
    def set_preview(self, tess, color="#f2c14e", opacity=0.45, name="preview", render=True):
        """Translucent preview body (drawn in front of coincident model faces). Several
        layers can be shown at once by name (e.g. added and removed material)."""
        self.plotter.remove_actor(name, render=False)
        self.plotter.remove_actor(name + "_edges", render=False)
        if tess is not None and len(tess.triangles):
            faces = np.hstack([np.full((len(tess.triangles), 1), 3), tess.triangles]).ravel()
            a = self.plotter.add_mesh(pv.PolyData(tess.points, faces), color=color, opacity=opacity,
                                      name=name, pickable=False, render=False, reset_camera=False,
                                      smooth_shading=False)
            m = a.GetMapper()
            m.SetResolveCoincidentTopologyToPolygonOffset()
            m.SetRelativeCoincidentTopologyPolygonOffsetParameters(-1.5, -3.0)
            edges = _lines_polydata(tess.edge_polylines)
            if edges.n_points:
                self.plotter.add_mesh(edges, color=color, line_width=2, name=name + "_edges",
                                      pickable=False, render=False, reset_camera=False)
        if render:
            self.render()

    def set_fill(self, name: str, triangles3d, color, opacity=0.5):
        """Filled triangles (k, 3, 3) drawn on top of coplanar faces (region highlights)."""
        self.plotter.remove_actor(name, render=False)
        tri = np.asarray(triangles3d, float).reshape(-1, 3, 3)
        if len(tri):
            pts = tri.reshape(-1, 3)
            faces = np.hstack([np.full((len(tri), 1), 3), np.arange(len(pts)).reshape(-1, 3)]).ravel()
            a = self.plotter.add_mesh(pv.PolyData(pts, faces), color=color, opacity=opacity, name=name,
                                      pickable=False, render=False, reset_camera=False,
                                      smooth_shading=False, lighting=False)
            m = a.GetMapper()
            m.SetResolveCoincidentTopologyToPolygonOffset()
            m.SetRelativeCoincidentTopologyPolygonOffsetParameters(-2.0, -4.0)

    def set_polylines(self, name: str, polylines3d, color, width=2.0):
        self.plotter.remove_actor(name, render=False)
        pd = _lines_polydata(polylines3d)
        if pd.n_points:
            self.plotter.add_mesh(pd, color=color, line_width=width, name=name, pickable=False,
                                  render=False, reset_camera=False)

    def plane_point(self, display, plane):
        """Sketch-plane (u, v) under a display pixel, or None."""
        x, y = display
        hit = plane.intersect_ray(self._display_to_world(x, y, 0.0), self._display_to_world(x, y, 1.0))
        return None if hit is None else np.array(plane.to_local(hit))

    def set_arrows(self, name: str, arrows, color="#e0162b"):
        """arrows: list of (tip_point, direction, length)."""
        self.plotter.remove_actor(name, render=False)
        meshes = []
        for tip, d, L in arrows:
            d = np.asarray(d, float)
            n = np.linalg.norm(d)
            if n < 1e-12 or L <= 0:
                continue
            d = d / n
            start = np.asarray(tip, float) - d * L
            meshes.append(pv.Arrow(start=start, direction=d, tip_length=0.3, tip_radius=0.08, shaft_radius=0.03,
                                   scale=L))
        if meshes:
            merged = meshes[0].merge(meshes[1:]) if len(meshes) > 1 else meshes[0]
            self.plotter.add_mesh(merged, color=color, name=name, pickable=False, render=False,
                                  reset_camera=False)
        self.render()

    def set_markers(self, name: str, points, color="#e0162b", size=12):
        self.plotter.remove_actor(name, render=False)
        if len(points):
            self.plotter.add_points(np.asarray(points, float), color=color, point_size=size,
                                    render_points_as_spheres=True, name=name, pickable=False,
                                    render=False, reset_camera=False)
        self.render()

    # ------------------------------------------------------------------
    # results
    # ------------------------------------------------------------------
    def show_results(self, grid: pv.UnstructuredGrid, field: str, unit: str, scale: float,
                     show_mesh: bool, show_undeformed: bool, clim=None):
        self.hide_results(render=False)
        self.results_visible = True
        self.set_body_visible(False)
        warped = grid.warp_by_vector("Displacement", factor=scale) if scale else grid
        surf = warped.extract_surface(nonlinear_subdivision=2, algorithm="dataset_surface")
        raw = grid.point_data[field]
        surf.point_data[field] = np.clip(surf.point_data[field], raw.min(), raw.max())
        title = f"{field} [{unit}]" if unit else field
        cmap = "jet_r" if field == "Factor of Safety" else "jet"
        self.plotter.add_mesh(surf, scalars=field, cmap=cmap, name="results", clim=clim,
                              n_colors=12, smooth_shading=False, pickable=False,
                              ambient=0.35, diffuse=0.75, specular=0.1,
                              render=False, reset_camera=False, show_scalar_bar=True,
                              scalar_bar_args={"title": title, "vertical": True,
                                               "position_x": 0.86, "position_y": 0.2,
                                               "height": 0.6, "width": 0.05,
                                               "fmt": "%.3g", "color": "black",
                                               "title_font_size": 13, "label_font_size": 12})
        if show_mesh:
            edges = warped.extract_surface(nonlinear_subdivision=0, algorithm="dataset_surface").extract_all_edges()
            self.plotter.add_mesh(edges, color="#202020", line_width=0.6, opacity=0.5,
                                  name="results_mesh", pickable=False, render=False,
                                  reset_camera=False)
        if show_undeformed and scale:
            a = self.plotter.actors.get("body_edges")
            if a is not None:
                a.SetVisibility(True)
        vals = surf.point_data[field]
        imax = int(np.argmax(vals))
        imin = int(np.argmin(vals))
        self.plotter.add_point_labels(
            [surf.points[imax], surf.points[imin]],
            [f"Max: {vals[imax]:.4g}", f"Min: {vals[imin]:.4g}"], font_size=12, point_size=10,
            point_color="black", text_color="black", shape_color="#ffffffcc", name="results_labels",
            render=False, always_visible=True)
        self.render()

    def hide_results(self, render=True):
        was = self.results_visible
        self.results_visible = False
        for name in ("results", "results_mesh", "results_labels"):
            self.plotter.remove_actor(name, render=False)
        try:
            self.plotter.remove_scalar_bar()
        except Exception:
            pass
        if was:
            self.set_body_visible(True)
        if render:
            self.render()

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------
    # ---- camera state and transitions ---------------------------------------------
    def camera_frame(self) -> np.ndarray:
        """Current (right, up, back) as matrix columns; back points at the viewer."""
        cam = self.renderer.GetActiveCamera()
        return _frame(-np.array(cam.GetDirectionOfProjection()), cam.GetViewUp())

    def _target_frame(self) -> np.ndarray:
        """Where the camera is heading (the end of a running transition) or is."""
        return self._anim["F1"] if self._anim is not None else self.camera_frame()

    def _apply_camera(self, F, focal, scale):
        cam = self.renderer.GetActiveCamera()
        dist = max(float(np.linalg.norm(np.array(cam.GetPosition()) - cam.GetFocalPoint())), 1.0)
        cam.SetFocalPoint(*focal)
        cam.SetPosition(*(np.asarray(focal) + F[:, 2] * dist))
        cam.SetViewUp(*F[:, 1])
        cam.SetParallelScale(scale)

    def animate_camera(self, back, up, focal=None, scale=None, duration=None, done=None):
        """Glide the camera to look from `back` (towards the viewer) with screen `up`: the
        orientation turns about one axis (shortest rotation), the target and zoom blend."""
        cam = self.renderer.GetActiveCamera()
        self.stop_animation()
        F0 = self.camera_frame()
        F1 = _frame(back, up)
        f0 = np.array(cam.GetFocalPoint())
        f1 = f0 if focal is None else np.asarray(focal, float)
        s0 = cam.GetParallelScale()
        s1 = s0 if scale is None else float(scale)
        T = self.anim_duration if duration is None else duration
        if T <= 0:
            self._apply_camera(F1, f1, s1)
            self._after_camera_move()
            if done:
                done()
            return
        self._anim = {"t0": time.monotonic(), "T": T, "F0": F0, "w": _log_rot(F1 @ F0.T),
                      "F1": F1, "f0": f0, "f1": f1, "s0": s0, "s1": s1, "done": done}
        self._anim_timer.start()
        self._anim_step()

    def _anim_step(self):
        a = self._anim
        if a is None:
            self._anim_timer.stop()
            return
        u = min((time.monotonic() - a["t0"]) / a["T"], 1.0)
        e = u * u * (3 - 2 * u)
        if u >= 1.0:
            F = a["F1"]
        else:
            w = a["w"]
            n = float(np.linalg.norm(w))
            F = (_rot(w / n, math.degrees(n * e)) if n > 1e-12 else np.eye(3)) @ a["F0"]
        f = a["f0"] + (a["f1"] - a["f0"]) * e
        sc = a["s0"] * (a["s1"] / a["s0"]) ** e if a["s0"] > 0 and a["s1"] > 0 else a["s1"]
        self._apply_camera(F, f, sc)
        if u >= 1.0:
            self._anim_timer.stop()
            self._anim = None
            self._after_camera_move()
            if a["done"]:
                a["done"]()
        else:
            self.render()

    def stop_animation(self):
        """Stop a transition where it is (the user grabbed the view)."""
        if self._anim is not None:
            self._anim_timer.stop()
            self._anim = None
            self._after_camera_move()

    def finish_animation(self):
        """Jump a running transition to its end (scripts and tests)."""
        a = self._anim
        if a is not None:
            self._anim_timer.stop()
            self._anim = None
            self._apply_camera(a["F1"], a["f1"], a["s1"])
            self._after_camera_move()
            if a["done"]:
                a["done"]()

    @property
    def animating(self) -> bool:
        return self._anim is not None

    def _after_camera_move(self):
        if self.mode == "sketch" and self.sketch_plane is not None:
            self._update_grid(force=True)
            self._draw_sketch_axes()
        self.render()

    # ---- views ----------------------------------------------------------------------
    def _fit_params(self, F=None, margin: float = 1.12):
        """(centre, parallel scale) that fit everything visible for camera frame F."""
        r = self.renderer
        grid = [self.plotter.actors.get(n) for n in ("sketch_grid_minor", "sketch_grid_major")]
        grid = [a for a in grid if a is not None and a.GetVisibility()]
        for a in grid:  # the grid follows the view - never fit to it
            a.SetVisibility(False)
        b = r.ComputeVisiblePropBounds()
        for a in grid:
            a.SetVisibility(True)
        if b[0] > b[1]:  # nothing visible
            return np.zeros(3), 60.0
        F = self.camera_frame() if F is None else F
        corners = np.array([[x, y, z] for x in b[:2] for y in b[2:4] for z in b[4:]])
        center = corners.mean(axis=0)
        rel = corners - center
        half_w = np.abs(rel @ F[:, 0]).max()
        half_h = np.abs(rel @ F[:, 1]).max()
        w, h = r.GetSize()
        aspect = w / h if h else 1.0
        return center, max(half_h, half_w / max(aspect, 1e-6), 1e-3) * margin

    def set_view(self, name_or_dir, up=None, animate=True, fit=True):
        """Standard view by name (front/back/top/bottom/left/right/isometric/dimetric/
        trimetric) or a direction towards the viewer; zoomed to fit."""
        if isinstance(name_or_dir, str):
            d, u = VIEWS[name_or_dir]
        else:
            d, u = name_or_dir, (up if up is not None else (0, 1, 0))
        F = _frame(d, u)
        center, scale = self._fit_params(F) if fit else (None, None)
        self.animate_camera(F[:, 2], F[:, 1], center, scale, duration=None if animate else 0)

    def _set_view(self, direction, up, animate=True):
        self.set_view(direction, up, animate=animate)

    def view_isometric(self, animate=True):
        self.set_view("isometric", animate=animate)

    def view_front(self, animate=True):
        self.set_view("front", animate=animate)

    def view_back(self, animate=True):
        self.set_view("back", animate=animate)

    def view_top(self, animate=True):
        self.set_view("top", animate=animate)

    def view_bottom(self, animate=True):
        self.set_view("bottom", animate=animate)

    def view_right(self, animate=True):
        self.set_view("right", animate=animate)

    def view_left(self, animate=True):
        self.set_view("left", animate=animate)

    def view_normal_to(self, plane: SketchPlane, animate=True):
        """Look straight at a sketch plane (a transition when switching planes)."""
        self.animate_camera(plane.normal, plane.ydir, plane.origin, None,
                            duration=None if animate else 0)

    def rotate_view(self, axis: str, deg: float, animate=True):
        """Orbit the camera about the screen's vertical ("v") or horizontal ("h") axis, or
        roll it about the view direction ("roll"), keeping the target and the zoom."""
        F = self._target_frame()
        ax = {"v": F[:, 1], "h": F[:, 0], "roll": F[:, 2]}[axis]
        F1 = _rot(ax, deg) @ F
        self.animate_camera(F1[:, 2], F1[:, 1], duration=None if animate else 0)

    def spin_view_about(self, world_axis, deg, animate=True):
        """Orbit about a world axis (e.g. Y: from one isometric corner to the next)."""
        F1 = _rot(world_axis, deg) @ self._target_frame()
        self.animate_camera(F1[:, 2], F1[:, 1], duration=None if animate else 0)

    # ---- numpad --------------------------------------------------------------------
    NUMPAD_TURN_DEG = 45.0          # numpad 4 / 6 / 8 / 2 pressed again
    NUMPAD_HELP = ("Numpad: 1 isometric (again: next corner) | 3 front (again: back) | "
                   "4 / 6 left / right (again: turn 45 deg about the vertical) | 8 / 2 top / "
                   "bottom (again: tip 45 deg about the horizontal) | 7 / 9 roll 90 deg ccw / "
                   "cw | 5 view selector | 0 normal to | . zoom to fit")

    def numpad(self, key: str) -> bool:
        """SolidWorks-style view keys. Pressing the same key again keeps turning the view."""
        target = self._target_frame()
        repeat = (self._numpad_last is not None and self._numpad_last[0] == key
                  and np.allclose(self._numpad_last[1], target, atol=1e-6))
        presets = {"1": "isometric", "3": "front", "4": "left", "6": "right",
                   "8": "top", "2": "bottom"}
        if key in presets and not repeat:
            self.set_view(presets[key])
        elif key == "1":
            self.spin_view_about((0, 1, 0), -90)
        elif key == "3":
            self.rotate_view("v", 180)
        elif key == "4":
            self.rotate_view("v", -self.NUMPAD_TURN_DEG)
        elif key == "6":
            self.rotate_view("v", self.NUMPAD_TURN_DEG)
        elif key == "8":
            self.rotate_view("h", -self.NUMPAD_TURN_DEG)
        elif key == "2":
            self.rotate_view("h", self.NUMPAD_TURN_DEG)
        elif key == "7":
            self.rotate_view("roll", -90)
        elif key == "9":
            self.rotate_view("roll", 90)
        elif key == "5":
            self.show_view_selector()
            return True
        elif key == "0":
            plane = self.normal_to_provider() if self.normal_to_provider else None
            plane = plane or self.sketch_plane
            if plane is not None:
                self.view_normal_to(plane)
            else:
                self.fit(animate=True)
        elif key == ".":
            self.fit(animate=True)
        else:
            return False
        self._numpad_last = (key, self._target_frame().copy())
        return True

    def show_view_selector(self):
        from .view_selector import ViewSelector
        self._selector = ViewSelector(self)
        self._selector.popup_over(self)

    def fit(self, margin: float = 1.12, animate=False):
        """Zoom so everything visible fits both the width and the height of the view."""
        F = self._target_frame()
        center, scale = self._fit_params(F, margin=margin)
        animate = animate or self.animating       # joins a running transition (Normal To + fit)
        self.animate_camera(F[:, 2], F[:, 1], center, scale, duration=None if animate else 0)

    def fit_bounds(self, bounds, margin=1.12):
        """Fit a chosen world-space box without changing camera orientation."""
        b = np.asarray(bounds, float)
        if b.shape != (6,) or not np.all(np.isfinite(b)) or np.any(b[::2] > b[1::2]):
            raise ValueError('Expected finite ordered world bounds')
        F = self._target_frame()
        corners = np.array([[x, y, z] for x in b[:2] for y in b[2:4] for z in b[4:]])
        center = corners.mean(axis=0)
        rel = corners - center
        width, height = self.renderer.GetSize()
        aspect = width / max(height, 1)
        scale = max(np.abs(rel @ F[:, 1]).max(),
                    np.abs(rel @ F[:, 0]).max() / max(aspect, 1e-6), 1e-3) * margin
        self.animate_camera(F[:, 2], F[:, 1], center, scale, duration=0)

    def screenshot(self, path: str):
        self.plotter.screenshot(path)

    def close(self):
        self.plotter.close()
        super().close()
