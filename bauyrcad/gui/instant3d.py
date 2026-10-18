"""Instant3D (SolidWorks): click a face made by an extrude, then drag the arrow on it to
change the extrude's depth live. The depth snaps to round values (finer when zoomed in)."""

from __future__ import annotations

import time

import numpy as np

from ..core.features import ExtrudeFeature, SketchFeature
from .viewer import nice_step

ARROW = "#ff7a00"          # orange: stands out on the blue selected face


class Instant3D:
    def __init__(self, win):
        self.win = win
        self.enabled = True
        self.target = None       # dict: feature, origin, axis (unit), depth0, scale
        self.drag = None
        self._last = 0.0

    @property
    def viewer(self):
        return self.win.viewer

    def set_enabled(self, on: bool):
        self.enabled = bool(on)
        if not on:
            self.clear()

    # ---- which extrude made a face -----------------------------------------------------------
    def extrude_for_face(self, face):
        """(feature, cap origin, unit direction, depth per unit move) of the blind / mid-plane
        extrude whose end cap lies on this planar face, newest first."""
        if face is None or not face.is_planar:
            return None
        doc = self.win.doc
        n = np.asarray(face.normal, float)
        c = np.asarray(face.centroid, float)
        for f in reversed(doc.features):
            if any(b["feature"] == f.id and b["field"] == "depth" for b in doc.parameter_bindings):
                continue
            if not isinstance(f, ExtrudeFeature) or f.suppressed or f.error or f.end not in ("blind", "midplane"):
                continue
            sk = doc.feature(f.sketch_id)
            if not isinstance(sk, SketchFeature):
                continue
            d = f.direction(sk) * (-1.0 if f.flipped else 1.0)
            if abs(float(n @ d)) < 1 - 1e-6:
                continue
            reach = f.depth if f.end == "blind" else f.depth / 2
            off = float((c - sk.plane.origin) @ d)
            if abs(off - reach) <= 1e-6 * max(f.depth, 1.0):
                cap = c
                return f, cap, d, (1.0 if f.end == "blind" else 2.0)
        return None

    # ---- display -----------------------------------------------------------------------------
    def face_selected(self, face_index):
        self.clear()
        if not self.enabled or face_index is None or self.win.command is not None:
            return
        face = self.win.doc.faces[face_index]
        hit = self.extrude_for_face(face)
        if hit is None:
            return
        f, cap, d, k = hit
        self.target = {"feature": f, "cap": cap, "axis": d, "k": k, "depth0": f.depth}
        self._draw(f.depth)

    def _arrow_len(self):
        """World length that shows about 80 px on screen, however foreshortened the axis is."""
        wpp = self.viewer.world_per_pixel()
        t = self.target
        if t is None:
            return 80 * wpp
        disp, _ = self.viewer.project([t["cap"], t["cap"] + t["axis"]])
        ppu = float(np.linalg.norm(disp[1] - disp[0]))            # pixels per mm along it
        return 80.0 / max(ppu, 0.3 / max(wpp, 1e-12))

    def _draw(self, depth):
        t = self.target
        L = self._arrow_len()
        tip = t["cap"] + t["axis"] * L
        self.viewer.set_arrows("instant3d", [(tip, t["axis"], L)], color=ARROW)
        cam = self.viewer.renderer.GetActiveCamera()
        up = np.array(cam.GetViewUp())
        right = np.cross(np.array(cam.GetDirectionOfProjection()), up)
        right = right / max(np.linalg.norm(right), 1e-12)
        at = tip + right * 70 * self.viewer.world_per_pixel()
        self.viewer.set_overlay("instant3d_lbl", labels=[(at, f"{t['feature'].name}: {depth:g} mm")])

    def clear(self):
        if self.target is not None or self.drag is not None:
            self.viewer.set_arrows("instant3d", [])
            self.viewer.set_overlay("instant3d_lbl", [])
        self.target = None
        self.drag = None

    # ---- dragging (Viewer.drag_handler) ------------------------------------------------------
    def _axis_param(self, x, y, origin=None):
        """Parameter along the arrow's line (from `origin`, default the cap) closest to the
        mouse ray."""
        t = self.target
        origin = t["cap"] if origin is None else origin
        p0 = self.viewer._display_to_world(x, y, 0.0)
        p1 = self.viewer._display_to_world(x, y, 1.0)
        r = p1 - p0
        r = r / max(np.linalg.norm(r), 1e-300)
        a = t["axis"]
        w = origin - p0
        b = float(a @ r)
        den = 1.0 - b * b
        if den < 1e-9:                         # looking straight along the arrow
            return None
        return float((b * (w @ r) - (w @ a)) / den)

    def near_arrow(self, x, y) -> bool:
        if self.target is None:
            return False
        t = self.target
        L = self._arrow_len()
        pts = np.array([t["cap"], t["cap"] + t["axis"] * L])
        disp, _ = self.viewer.project(pts)
        a, b = disp
        q = np.array([x, y], float)
        ab = b - a
        s = np.clip(float((q - a) @ ab) / max(float(ab @ ab), 1e-12), 0, 1)
        return float(np.linalg.norm(a + s * ab - q)) < 12

    def press(self, info, x, y, button):
        if button != "left" or not self.enabled or self.win.command is not None:
            return False
        if not self.near_arrow(x, y):
            return False
        s = self._axis_param(x, y)
        if s is None:
            return False
        self.win.checkpoint()
        # measured from where the drag started: the cap moves while the model rebuilds
        self.drag = {"s0": s, "depth0": self.target["feature"].depth,
                     "origin": np.array(self.target["cap"], float)}
        return True

    def move(self, x, y):
        if self.drag is None:
            return
        s = self._axis_param(x, y, self.drag["origin"])
        if s is None:
            return
        t = self.target
        step = nice_step(8 * self.viewer.world_per_pixel())
        depth = self.drag["depth0"] + t["k"] * (s - self.drag["s0"])
        depth = max(step, round(depth / step) * step)
        f = t["feature"]
        if abs(depth - f.depth) < 1e-12:
            return
        f.depth = float(depth)
        now = time.monotonic()
        if now - self._last > 0.08:
            self._last = now
            self._rebuild_quick()
        self._draw(f.depth)

    def _rebuild_quick(self):
        doc = self.win.doc
        doc.regenerate()
        self.win._shown_tess = None
        self.win.refresh_view()
        self._update_cap()

    def _update_cap(self):
        t = self.target
        f = t["feature"]
        sk = self.win.doc.feature(f.sketch_id)
        if sk is None:
            return
        reach = f.depth if f.end == "blind" else f.depth / 2
        base = np.asarray(t["cap"]) - t["axis"] * float((np.asarray(t["cap"]) - sk.plane.origin) @ t["axis"])
        t["cap"] = base + t["axis"] * reach

    def release(self, x, y, moved):
        if self.drag is None:
            return
        self.drag = None
        self.win.rebuild()
        self._update_cap()
        self._draw(self.target["feature"].depth)
        self.win.status(f"{self.target['feature'].name}: depth {self.target['feature'].depth:g} mm "
                        "(Instant3D - drag the arrow again, or Ctrl+Z)")

