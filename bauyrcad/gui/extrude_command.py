"""Sketch-based feature commands (Extrude, Revolve) with the sketch shown and
SolidWorks-style contour selection."""

from __future__ import annotations

import json

import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QListWidget,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..core import geometry
from ..core.features import (END_CONDITIONS, REVOLVE_ENDS, ExtrudeFeature, RevolveFeature,
                             SketchFeature, FeatureError)
from ..core.sketch import Line
from ..core.refs import EdgeRef, edge_ref, resolver
from .reference_picker import ReferenceCandidate, ReferenceChoices, line_hits
from .commands import DIM_LIMIT, Command, hint, spin
from .icons import icon
from .sketch_draw import dashed

REGION_SELECTED = "#2f7de1"
REGION_HOVER = "#ff8a00"
SKETCH_SHOWN = "#1f5fd6"
AXIS_SHOWN = "#e0162b"


class SketchFeatureCommand(Command):
    """Common machinery: sketch choice, contour picking, rollback display, preview."""

    feature_class = None
    base_titles = ("Boss", "Cut")
    icons = ("extrude", "cut")
    copy_fields: tuple = ()

    def __init__(self, win, operation: str, feature=None, sketch_id: str | None = None):
        super().__init__(win)
        self.operation = feature.operation if feature else operation
        self.feature = feature
        self.sketch_id = feature.sketch_id if feature else sketch_id
        cut = self.operation == "cut"
        self.icon_name = self.icons[1] if cut else self.icons[0]
        base = self.base_titles[1] if cut else self.base_titles[0]
        self.title = f"Edit {feature.name}" if feature else base
        self.ctx = None
        self.picks: list = [list(p) for p in feature.regions] if feature else []
        self.regions: list = []
        self.hover_region = None
        self.sketch_feat = None

    # ---- hooks for subclasses ------------------------------------------------------------
    def build_params(self, form: QFormLayout):
        raise NotImplementedError

    def load_params(self, feature):
        pass

    def param_signals(self):
        return []

    def make(self):
        raise NotImplementedError

    def help_text(self) -> str:
        return ""

    def sketch_loaded(self):
        """Called after the sketch changed (e.g. to refresh an axis list)."""

    def extra_drawing(self):
        """Additional overlays (e.g. the revolve axis)."""

    # ---- panel ---------------------------------------------------------------------------
    def build_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.sketch_cb = QComboBox()
        form.addRow("Sketch", self.sketch_cb)
        self.build_params(form)
        lay.addLayout(form)
        lay.addWidget(hint(self.help_text()))

        box = QGroupBox("Selected Contours")
        bl = QVBoxLayout(box)
        bl.addWidget(hint("Click inside closed areas of the sketch to choose what to use "
                          "(click again to deselect). With nothing selected, all closed "
                          "profiles are used and inner loops become holes."))
        self.contour_list = QListWidget()
        self.contour_list.setMaximumHeight(120)
        bl.addWidget(self.contour_list)
        clear = QPushButton("Clear selection (use all profiles)")
        clear.clicked.connect(self._clear_picks)
        bl.addWidget(clear)
        lay.addWidget(box)

        self.msg = QLabel()
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        lay.addStretch(1)

        limit = self.doc.features.index(self.feature) if self.feature else len(self.doc.features)
        sketches = [f for f in self.doc.features[:limit] if isinstance(f, SketchFeature)]
        for s in sketches:
            self.sketch_cb.addItem(icon("sketch"), s.name, s.id)
        if self.sketch_id is None and sketches:
            unused = [s for s in sketches if not self.doc.is_consumed(s.id)]
            self.sketch_id = (unused or sketches)[-1].id
        if self.sketch_id is not None:
            self.sketch_cb.setCurrentIndex(max(self.sketch_cb.findData(self.sketch_id), 0))
        self.sketch_cb.currentIndexChanged.connect(self._sketch_changed)
        return w

    # ---- lifecycle -------------------------------------------------------------------------
    def start(self):
        if self.sketch_cb.count() == 0:
            self.msg.setText("<span style='color:#c0392b'>Create a sketch first.</span>")
            return
        self.ctx = (self.doc.context_before(self.feature) if self.feature
                    else self.doc.regenerate(upto=len(self.doc.features)))
        if self.feature is not None:
            # show the model as it was before this feature (SolidWorks rollback)
            body = self.ctx.body
            self.viewer.set_body(geometry.tessellate(body) if body is not None else None)
            self.win._shown_tess = "rolled back"
        self.viewer.pick_faces = False       # clicks pick sketch contours, not model faces
        self._load_sketch(initial=True)
        from .feature_equations import FeatureEquations
        kind = "revolve" if isinstance(self, RevolveCommand) else "extrude"
        self.equations = FeatureEquations(self, kind)
        for sig in self.param_signals():
            sig.connect(self.update_preview)
        self.win.status("Click inside closed areas of the sketch to select contours, then OK")

    def cleanup(self):
        if hasattr(self, "equations"):
            self.equations.close()
        self.viewer.set_preview(None)
        for name in ("extrude_sketch", "extrude_selected", "extrude_hover", "revolve_axis",
                     "revolve_candidates", "revolve_axis_hover"):
            self.viewer.plotter.remove_actor(name, render=False)
        self.viewer.pick_faces = True
        self.win._shown_tess = None          # forces the current body to be shown again

    def _sketch_changed(self):
        self.picks = []
        self._load_sketch()

    def _load_sketch(self, initial=False):
        sid = self.sketch_cb.currentData()
        self.sketch_feat = self.doc.feature(sid) if sid else None
        self.hover_region = None
        self.regions = []
        if self.sketch_feat is not None:
            try:
                self.regions = self.sketch_feat.regions()
            except Exception as e:  # noqa: BLE001 - reported in the panel
                self.msg.setText(f"<span style='color:#c0392b'>{e}</span>")
        if initial and self.feature is not None:
            self.load_params(self.feature)
        self.sketch_loaded()
        self._draw_sketch()
        self._refresh_contours()
        self.update_preview()

    def _selected(self):
        return [r for r in self.regions if any(r.contains(p) for p in self.picks)]

    def _clear_picks(self):
        self.picks = []
        self._refresh_contours()
        self.update_preview()

    # ---- drawing ---------------------------------------------------------------------------
    def _to3d(self, uv):
        return self.sketch_feat.plane.to_world_many(np.asarray(uv, float).reshape(-1, 2))

    def _tris3d(self, regions):
        if not regions:
            return np.zeros((0, 3, 3))
        return np.vstack([self._to3d(r.triangles).reshape(-1, 3, 3) for r in regions])

    def _draw_sketch(self):
        polys = []
        if self.sketch_feat is not None:
            sk = self.sketch_feat.sketch
            px = self.viewer.world_per_pixel()
            for e in sk.entities.values():
                if e.kind == "point":
                    continue
                smp = sk.sample(e)
                for part in (dashed(smp, px) if e.construction else [smp]):
                    polys.append(self._to3d(part))
            for t in sk.texts:
                try:
                    polys += [self._to3d(q) for q in geometry.text_outlines(t, self.sketch_feat.plane)]
                except Exception:  # noqa: BLE001
                    pass
        self.viewer.set_polylines("extrude_sketch", polys, SKETCH_SHOWN, 2.4)
        self.extra_drawing()
        self.viewer.render()

    def _draw_regions(self):
        sel = self._selected()
        self.viewer.set_fill("extrude_selected", self._tris3d(sel), REGION_SELECTED, 0.6)
        hov = [self.hover_region] if self.hover_region is not None and self.hover_region not in sel else []
        self.viewer.set_fill("extrude_hover", self._tris3d(hov), REGION_HOVER, 0.4)
        self.viewer.render()

    def _refresh_contours(self):
        self.contour_list.clear()
        sel = self._selected()
        for i, r in enumerate(self.regions):
            if r in sel:
                self.contour_list.addItem(f"Contour {i + 1}:  area {r.area:.4g} mm²")
        if not self.picks:
            self.contour_list.addItem("(all closed profiles)")
        self._draw_regions()

    # ---- viewer events ------------------------------------------------------------------------
    def region_at(self, display):
        if self.sketch_feat is None:
            return None
        uv = self.viewer.plane_point(display, self.sketch_feat.plane)
        if uv is None:
            return None
        return next((r for r in self.regions if r.contains(uv)), None)

    def on_hover(self, info):
        r = self.region_at(info.display)
        if r is not self.hover_region:
            self.hover_region = r
            self._draw_regions()

    def on_click(self, info):
        r = self.region_at(info.display)
        if r is None:
            return
        if r in self._selected():
            self.picks = [p for p in self.picks if not r.contains(p)]
        else:
            self.picks.append([float(r.point[0]), float(r.point[1])])
        self._refresh_contours()
        self.update_preview()

    # ---- feature ---------------------------------------------------------------------------------
    def update_preview(self, *_):
        if self.ctx is None or self.sketch_cb.currentData() is None:
            return
        self.extra_drawing()
        try:
            feat = self.make()
            if hasattr(self, "equations"):
                self.equations.apply(feat)
            tool = feat.tool_shape(self.ctx)
            color = "#e04848" if self.operation == "cut" else "#f2c14e"
            self.viewer.set_preview(geometry.tessellate(tool), color, opacity=0.22)
            self.msg.setText("<span style='color:#777'>Cut direction flipped into the material"
                             "</span>" if getattr(feat, "flipped", False) else "")
        except Exception as e:  # noqa: BLE001 - reported in the panel
            self.viewer.set_preview(None)
            self.msg.setText(f"<span style='color:#c0392b'>{e}</span>")

    def ok(self):
        if self.sketch_cb.currentData() is None:
            QMessageBox.warning(self.win, self.title, "Select a sketch.")
            return False
        new = self.make()
        from .parameters_dialog import validate_feature_edit
        self.equations.apply(new)
        bindings = validate_feature_edit(self.doc, self.feature, new, self.equations)
        self.win.checkpoint()
        if self.feature:
            for k in ("sketch_id", "regions", *self.copy_fields):
                setattr(self.feature, k, getattr(new, k))
            feat = self.feature
        else:
            feat = self.doc.add_feature(new)
        self.doc.parameter_bindings = bindings
        self.win.rebuild()
        if feat.error:
            QMessageBox.warning(self.win, feat.name, f"The feature could not be built:\n\n{feat.error}")
        return True


class ExtrudeCommand(SketchFeatureCommand):
    base_titles = ("Boss-Extrude", "Cut-Extrude")
    icons = ("extrude", "cut")
    copy_fields = ("depth", "end", "reverse")

    def start(self):
        super().start()
        # Defer until the panel and its preview are ready, so toolbar activation
        # cannot reclaim the focus after the command starts.
        QTimer.singleShot(0, self._focus_depth)

    def _focus_depth(self):
        if self.win.command is self and self.sketch_feat is not None and self.depth.isEnabled():
            self.depth.setFocus(Qt.ShortcutFocusReason)
            self.depth.selectAll()

    def build_params(self, form):
        self.end_cb = QComboBox()
        ends = (["blind", "through_all", "through_all_both"] if self.operation == "cut"
                else ["blind", "midplane"])
        for k in ends:
            self.end_cb.addItem(END_CONDITIONS[k], k)
        form.addRow("End condition", self.end_cb)
        self.depth = spin(10.0, 0.001, DIM_LIMIT)
        form.addRow("Depth", self.depth)
        self.reverse = QCheckBox("Reverse direction")
        form.addRow(self.reverse)
        if self.feature:
            self.end_cb.setCurrentIndex(max(self.end_cb.findData(self.feature.end), 0))
            self.depth.setValue(self.feature.depth)
            self.reverse.setChecked(self.feature.reverse)

    def param_signals(self):
        return [self.end_cb.currentIndexChanged, self.depth.valueChanged, self.reverse.toggled]

    def help_text(self):
        return ("Cuts go into the material: against the sketch normal, or along it when "
                "only that side has material. Reverse direction forces the other side."
                if self.operation == "cut" else
                "Extrudes closed areas of the sketch along its normal.")

    def update_preview(self, *_):
        self.depth.setEnabled(self.end_cb.currentData() in ("blind", "midplane"))
        super().update_preview()

    def make(self) -> ExtrudeFeature:
        return ExtrudeFeature(self.sketch_cb.currentData(), self.depth.value(), self.operation,
                              self.end_cb.currentData(), self.reverse.isChecked(), self.picks)


class RevolveCommand(SketchFeatureCommand):
    base_titles = ("Revolve", "Cut-Revolve")
    icons = ("revolve", "revolve_cut")
    copy_fields = ("angle", "axis_id", "axis_sketch_id", "axis_spec", "end", "reverse")

    def start(self):
        super().start()
        QTimer.singleShot(0, self._focus_angle)

    def _focus_angle(self):
        if self.win.command is self and self.sketch_feat is not None:
            self.angle.setFocus(Qt.ShortcutFocusReason)
            self.angle.selectAll()

    def build_params(self, form):
        self.axis_cb = QComboBox()
        self.axis_cb.setToolTip("Any straight line or centerline from the profile or another "
                                "earlier sketch, straight model edge or reference axis. It must lie in the profile plane.")
        form.addRow("Axis", self.axis_cb)
        self.pick_axis = QPushButton("Pick axis line in view")
        self.pick_axis.setCheckable(True)
        self.pick_axis.toggled.connect(self._axis_pick_mode)
        form.addRow(self.pick_axis)
        self.axis_filter = QComboBox()
        for label, kind in (("All axis references", None), ("Sketch lines", "sketch"),
                            ("Model edges", "edge"), ("Reference axes", "axis")):
            self.axis_filter.addItem(label, kind)
        self.axis_filter.currentIndexChanged.connect(self._filter_changed)
        form.addRow("Pick filter", self.axis_filter)
        self.axis_choices = ReferenceChoices()
        self.axis_choices.preview.connect(self._preview_candidate)
        self.axis_choices.chosen.connect(self._choose_candidate)
        form.addRow(self.axis_choices)
        self.end_cb = QComboBox()
        for k, label in REVOLVE_ENDS.items():
            self.end_cb.addItem(label, k)
        form.addRow("Direction", self.end_cb)
        self.angle = spin(360.0, 0.01, 360.0, 3, "°", 15.0)
        form.addRow("Angle", self.angle)
        self.reverse = QCheckBox("Reverse direction")
        form.addRow(self.reverse)
        if self.feature:
            self.end_cb.setCurrentIndex(max(self.end_cb.findData(self.feature.end), 0))
            self.angle.setValue(self.feature.angle)
            self.reverse.setChecked(self.feature.reverse)

    def sketch_loaded(self):
        self.axis_cb.blockSignals(True)
        prev = self.axis_cb.currentData() if self.axis_cb.count() else None
        want = None
        if self.feature is not None and not getattr(self, "_axis_initialized", False):
            want = self._axis_key(self.feature.axis_sketch_id or self.feature.sketch_id,
                                  self.feature.axis_id) if self.feature.axis_id is not None else None
            if self.feature.axis_spec is not None:
                want = self._spec_key(self.feature.axis_spec)
        self._axis_initialized = True
        self.axis_cb.clear()
        self._axis_candidates = []
        self._axis_specs = {}
        self.axis_choices.clear()
        limit = self.doc.features.index(self.feature) if self.feature else len(self.doc.features)
        sources = [s for s in self.doc.features[:limit] if isinstance(s, SketchFeature) and not s.suppressed]
        sources.sort(key=lambda s: s is not self.sketch_feat)
        centerlines = [e for e in self.sketch_feat.sketch.entities.values()
                       if isinstance(e, Line) and e.construction] if self.sketch_feat else []
        if len(centerlines) == 1:
            self.axis_cb.addItem("Auto (the sketch centerline)", None)
        else:
            self.axis_cb.addItem("Select an axis line…", None)
        for source in sources:
            lines = [e for e in source.sketch.entities.values() if isinstance(e, Line)]
            lines.sort(key=lambda e: not e.construction)
            for e in lines:
                key = self._axis_key(source.id, e.id)
                kind = "Centerline" if e.construction else "Line"
                self.axis_cb.addItem(icon("centerline" if e.construction else "line"),
                                     f"{source.name} — {kind} {e.id}", key)
                points = source.plane.to_world_many([source.sketch.pt(e.p1), source.sketch.pt(e.p2)])
                self._axis_candidates.append(ReferenceCandidate(key, f"{source.name}  -  {kind} {e.id}", "sketch", points))
        if self.ctx is not None:
            for e in resolver(self.ctx).edges:
                if e["kind"] == "line":
                    spec = {"type": "edge", "ref": EdgeRef.from_info(e).to_dict()}
                    self._add_axis_reference(spec, f"Model edge {e['index'] + 1}", "edge",
                                             np.array([e["p0"], e["p1"]]))
            for f in self.doc.features[:limit]:
                ax = getattr(f, "result_axis", None)
                if f.TYPE == "ref_axis" and not f.suppressed and not f.error and ax is not None:
                    origin, direction = map(np.asarray, ax)
                    length = self.win.model_size()
                    self._add_axis_reference({"type": "feature", "id": f.id}, f.name, "axis",
                                             np.array([origin - direction * length, origin + direction * length]))
        target = want if want is not None else prev
        if target is not None:
            unresolved = None
            if target.startswith("ref:") and self.ctx is not None:
                spec = json.loads(target[4:])
                if spec.get("type") == "edge":
                    edges = resolver(self.ctx).edges
                    match = edge_ref(spec["ref"]).match(edges)
                    if match["index"] is not None:
                        current = next(e for e in edges if e["index"] == match["index"])
                        target = self._spec_key({"type": "edge", "ref": EdgeRef.from_info(current).to_dict()})
                    else:
                        unresolved = match["status"].title()
            index = self.axis_cb.findData(target)
            if unresolved or index < 0:
                self.axis_cb.addItem(f"{unresolved or 'Missing'} axis line — select a replacement", target)
                index = self.axis_cb.count() - 1
            self.axis_cb.setCurrentIndex(index)
        self.axis_cb.blockSignals(False)
        if self.pick_axis.isChecked():
            self._axis_pick_mode(True)

    @staticmethod
    def _spec_key(spec):
        return "ref:" + json.dumps(spec, sort_keys=True)

    def _add_axis_reference(self, spec, label, kind, points):
        key = self._spec_key(spec)
        self._axis_specs[key] = spec
        self.axis_cb.addItem(label, key)
        self._axis_candidates.append(ReferenceCandidate(key, label, kind, points))

    def _filtered_candidates(self):
        kind = self.axis_filter.currentData()
        return [c for c in getattr(self, "_axis_candidates", []) if kind is None or c.kind == kind]

    def _filter_changed(self):
        self.axis_choices.clear()
        if self.pick_axis.isChecked():
            self._axis_pick_mode(True)

    def _preview_candidate(self, candidate):
        self.viewer.set_polylines("revolve_axis_hover", [candidate.points] if candidate else [], REGION_HOVER, 4)
        self.viewer.render()

    def _choose_candidate(self, candidate):
        self.axis_cb.setCurrentIndex(self.axis_cb.findData(candidate.key))
        self.pick_axis.setChecked(False)

    @staticmethod
    def _axis_key(sketch_id, entity_id):
        return f"{sketch_id}/{entity_id}"

    def _axis_pick_mode(self, enabled):
        self.axis_choices.clear()
        self.viewer.plotter.remove_actor("revolve_axis_hover", render=False)
        self.viewer.set_polylines("revolve_candidates",
                                  [c.points for c in self._filtered_candidates()] if enabled else [],
                                  SKETCH_SHOWN, 3.0)
        self.viewer.render()
        self.win.status("Click an axis reference; overlapping candidates appear in Select Other" if enabled else
                        "Click closed areas to select contours, then OK")

    def _axis_hits(self, display):
        return line_hits(self._filtered_candidates(), self.viewer.project, display,
                         10 * self.viewer.devicePixelRatioF())

    def on_hover(self, info):
        if self.pick_axis.isChecked():
            if not self.axis_choices.isVisible():
                hits = self._axis_hits(info.display)
                self._preview_candidate(hits[0] if hits else None)
        else:
            super().on_hover(info)

    def on_click(self, info):
        if self.pick_axis.isChecked():
            hits = self._axis_hits(info.display)
            if len(hits) == 1:
                self._choose_candidate(hits[0])
            else:
                self.axis_choices.show_candidates(hits)
                if not hits:
                    self._preview_candidate(None)
            return
        super().on_click(info)

    def update_preview(self, *_):
        if self.ctx is not None and self.sketch_feat is not None:
            try:
                ctx = self.make().profile_context(self.ctx)
                self.regions = ctx.features[self.sketch_feat.id].regions()
            except Exception:
                self.regions = []
            self._refresh_contours()
        super().update_preview()

    def param_signals(self):
        return [self.axis_cb.currentIndexChanged, self.end_cb.currentIndexChanged,
                self.angle.valueChanged, self.reverse.toggled]

    def help_text(self):
        return ("Choose a sketch line, straight model edge or reference axis, or click Pick axis line "
                "in view. Other sketches are shown while picking. The axis must lie in the "
                "profile plane; the profile may touch it but must not cross it.")

    def ok(self):
        if self.sketch_feat is not None:
            try:
                self.make().axis_world(self.sketch_feat, self.ctx)
            except FeatureError as exc:
                QMessageBox.warning(self.win, self.title, str(exc))
                return False
        return super().ok()

    def make(self) -> RevolveFeature:
        key = self.axis_cb.currentData()
        spec = json.loads(key[4:]) if key and key.startswith("ref:") else None
        sid, eid = key.rsplit("/", 1) if key and spec is None else (None, None)
        return RevolveFeature(self.sketch_cb.currentData(), self.angle.value(), self.operation,
                              int(eid) if eid is not None else None, self.end_cb.currentData(),
                              self.reverse.isChecked(), self.picks, axis_sketch_id=sid, axis_spec=spec)

    def extra_drawing(self):
        """Highlight the axis, extended across the model."""
        self.viewer.plotter.remove_actor("revolve_axis", render=False)
        if self.sketch_feat is None or not hasattr(self, "axis_cb"):
            return
        try:
            origin, d, _ = self.make().axis_world(self.sketch_feat, self.ctx)
        except Exception:  # noqa: BLE001 - no valid axis yet
            return
        L = self.win.model_size()
        pts = np.array([origin - d * L, origin + d * L])
        px = self.viewer.world_per_pixel()
        self.viewer.set_polylines("revolve_axis", dashed(pts, px, 14, 5), AXIS_SHOWN, 2.2)
