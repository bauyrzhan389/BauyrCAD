"""PropertyManager commands for the applied / advanced features and reference geometry
(Fillet, Chamfer, Shell, Draft, Hole Wizard, patterns, Mirror, Sweep, Loft, Boundary, Rib,
Plane, Axis, Point, Helix, Curve).

Every command shows the model as it was before the feature (rolled back, so its edges and
faces can be picked), collects picks into SolidWorks-style selection boxes (the active box is
pink), and previews the result: added material yellow, removed material red.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QMessageBox, QMenu,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core import geometry, modeling
from ..core.features import FeatureError, RegenContext, SketchFeature
from ..core.features_more import (CHAMFER_TYPES, HOLE_KINDS, METRIC, TOOL_TYPES,
                                  BoundaryFeature, ChamferFeature, CircularPatternFeature,
                                  CurveFeature, DraftFeature, FilletFeature, HelixFeature,
                                  HoleFeature, LinearPatternFeature, LoftFeature, MirrorFeature,
                                  RefAxisFeature, RefPlaneFeature, RefPointFeature, RibFeature,
                                  ShellFeature, SweepFeature)
from ..core.plane import REFERENCE_PLANES
from ..core.refs import EdgeRef, RefError, edge_ref, face_ref, plane_on_face
from ..fea.study import FaceRef
from .commands import DIM_LIMIT, Command, hint, spin
from .icons import icon
from .viewer import SELECT_RGB, PickInfo

EDGE_SEL = "#e0287a"          # SolidWorks-like magenta selection
ADD_COLOR = "#f2c14e"
CUT_COLOR = "#e04848"
REF_COLOR = "#2f9e57"
ACTIVE_STYLE = ("QGroupBox{border:2px solid #e070b0;border-radius:4px;margin-top:8px;"
                "background:#fdf2f8;} QGroupBox::title{subcontrol-origin:margin;left:8px;}")
IDLE_STYLE = ("QGroupBox{border:1px solid #c9d3df;border-radius:4px;margin-top:8px;}"
              "QGroupBox::title{subcontrol-origin:margin;left:8px;}")

STD_AXES = [("X axis", {"type": "std", "name": "x"}), ("Y axis", {"type": "std", "name": "y"}),
            ("Z axis", {"type": "std", "name": "z"})]


# ============================================================================================
# selection box
# ============================================================================================


class PickBox(QObject):
    """A selection list that receives model picks of some kinds while it is active."""

    changed = Signal()

    def __init__(self, cmd, title, kinds, tip="", single=False):
        super().__init__()
        self.cmd, self.kinds, self.single = cmd, set(kinds), single
        self.items: list[dict] = []
        self.box = QGroupBox(title)
        lay = QVBoxLayout(self.box)
        if tip:
            lay.addWidget(hint(tip))
        self.list = QListWidget()
        self.list.setMaximumHeight(40 if single else 110)
        lay.addWidget(self.list)
        self.repair_hint = hint("Select a missing or ambiguous row, then pick its replacement on the model. Remove deletes the reference explicitly.")
        self.repair_hint.hide()
        lay.addWidget(self.repair_hint)
        self.replacements = QPushButton("Preview replacements")
        self.replacements.setEnabled(False)
        self.replacements.hide()
        self.replacements.clicked.connect(self.show_replacements)
        self.list.currentRowChanged.connect(self._repair_row_changed)
        self.repair_menu = None
        lay.addWidget(self.replacements)
        self.list.viewport().installEventFilter(self)
        self.box.installEventFilter(self)
        row = QHBoxLayout()
        rm = QPushButton(icon("delete"), "Remove")
        rm.clicked.connect(self.remove_selected)
        clr = QPushButton("Clear")
        clr.clicked.connect(self.clear)
        row.addWidget(rm)
        row.addWidget(clr)
        row.addStretch(1)
        lay.addLayout(row)
        self.set_active(False)

    def eventFilter(self, obj, ev):  # noqa: N802 - Qt API
        if ev.type() == QEvent.MouseButtonPress:
            self.cmd.activate(self)
        return False

    def set_active(self, on):
        if not on:
            self.close_replacements()
        self.box.setStyleSheet(ACTIVE_STYLE if on else IDLE_STYLE)

    def _repair_row_changed(self, row):
        self.close_replacements()
        self.replacements.setEnabled(0 <= row < len(self.items) and bool(self.items[row].get("unresolved")))

    def close_replacements(self):
        if self.repair_menu is not None:
            self.repair_menu.close()

    def show_replacements(self):
        self.close_replacements()
        row = self.list.currentRow()
        if not 0 <= row < len(self.items) or not self.items[row].get("unresolved"):
            return
        self.cmd.activate(self)
        self.cmd.viewer.close_selection_menu()
        original = self.items[row]
        kind = original["kind"]
        factory, infos = (face_ref, self.cmd.faces_) if kind == "face" else (edge_ref, self.cmd.edges_)
        match = factory(original["ref"]).match(infos)
        menu = QMenu("Replacement candidates", self.box)
        self.repair_menu = menu
        menu.addSection("Hover to preview; click to replace")
        count = 0
        for candidate in match["candidates"]:
            index = candidate["index"]
            if self.has(kind, index):
                continue
            item = self.cmd.face_item(index) if kind == "face" else self.cmd.edge_item(index)
            point = infos[index].centroid if kind == "face" else infos[index]["mid"]
            info = PickInfo(kind=kind, face=index if kind == "face" else None,
                            point=point, topology=(kind, index, point))
            proximity = "nearby" if candidate["score"] <= 1 else "distant"
            action = menu.addAction(f"{item[3]} — {proximity}")
            action.setData(index)
            action.hovered.connect(lambda info=info: self.cmd.viewer.preview_candidate(info))
            def use(checked=False, item=item):
                # A stale menu must never replace a different row after an edit.
                if row < len(self.items) and self.items[row] is original:
                    self.list.setCurrentRow(row)
                    self.toggle(*item)
            action.triggered.connect(use)
            count += 1
        if not count:
            menu.addAction("No compatible replacements; pick on the model").setEnabled(False)
        def closed():
            self.cmd.viewer.preview_candidate()
            if self.repair_menu is menu:
                self.repair_menu = None
            menu.deleteLater()
        menu.aboutToHide.connect(closed)
        menu.popup(self.replacements.mapToGlobal(self.replacements.rect().bottomLeft()))
        return menu

    def accepts(self, kind):
        return kind in self.kinds

    def has(self, kind, key):
        return any(it["kind"] == kind and it["key"] == key for it in self.items)

    def toggle(self, kind, key, ref, label, extra=None):
        row = self.list.currentRow()
        if 0 <= row < len(self.items) and self.items[row].get("unresolved"):
            if self.items[row]["kind"] != kind:
                return
            if any(it["kind"] == kind and it["key"] == key for it in self.items):
                return
            self.items[row] = {"kind": kind, "key": key, "ref": ref, "label": label, "extra": extra}
            self.refresh()
            return
        for it in self.items:
            if it["kind"] == kind and it["key"] == key:
                self.items.remove(it)
                break
        else:
            if self.single:
                self.items = []
            self.items.append({"kind": kind, "key": key, "ref": ref, "label": label,
                               "extra": extra})
        self.refresh()

    def set_items(self, items):
        self.items = list(items)
        self.refresh()

    def refresh(self):
        self.close_replacements()
        self.replacements.setVisible(any(it.get("unresolved") for it in self.items))
        self.repair_hint.setVisible(any(it.get("unresolved") for it in self.items))
        self.list.clear()
        for it in self.items:
            self.list.addItem(QListWidgetItem(icon({"edge": "line", "face": "front", "vertex": "point",
                                                   "plane": "plane"}.get(it["kind"], "part")),
                                              it["label"]))
        self.changed.emit()

    def remove_selected(self):
        rows = sorted({self.list.row(i) for i in self.list.selectedItems()}, reverse=True)
        for r in rows:
            del self.items[r]
        self.refresh()

    def clear(self):
        self.items = []
        self.refresh()

    def refs(self, kind=None):
        return [it["ref"] for it in self.items if kind is None or it["kind"] == kind]


# ============================================================================================
# base command
# ============================================================================================


class FeatureCommand(Command):
    TITLE = "Feature"
    ICON = "part"
    HELP = ""
    PRIMARY_INPUT = None

    def __init__(self, win, feature=None, **opts):
        super().__init__(win)
        self.feature = feature
        self.opts = opts
        self.title = f"Edit {feature.name}" if feature else self.TITLE
        self.icon_name = self.ICON
        self.boxes: list[PickBox] = []
        self.active: PickBox | None = None
        self.ctx = None
        self.base = None
        self.faces_: list = []
        self.edges_: list = []
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self.update_preview)
        self._built = None

    # ---- panel helpers -----------------------------------------------------------------------
    def build_panel(self):
        w = QWidget()
        self.lay = QVBoxLayout(w)
        if self.HELP:
            self.lay.addWidget(hint(self.HELP))
        self.build()
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        self.lay.addWidget(self.msg)
        self.lay.addStretch(1)
        return w

    def build(self):
        raise NotImplementedError

    def pick_box(self, title, kinds, tip="", single=False) -> PickBox:
        b = PickBox(self, title, kinds, tip, single)
        b.changed.connect(self.changed)
        self.boxes.append(b)
        self.lay.addWidget(b.box)
        if self.active is None:
            self.activate(b)
        return b

    def form(self, title=None) -> QFormLayout:
        f = QFormLayout()
        if title:
            g = QGroupBox(title)
            g.setLayout(f)
            self.lay.addWidget(g)
        else:
            self.lay.addLayout(f)
        return f

    def watch(self, *signals):
        for s in signals:
            s.connect(self.changed)

    def activate(self, box):
        self.active = box
        for b in self.boxes:
            b.set_active(b is box)

    def earlier_features(self):
        limit = self.doc.features.index(self.feature) if self.feature in self.doc.features \
            else len(self.doc.features)
        return self.doc.features[:limit]

    def sketch_combo(self, filt=None, none_label=None) -> QComboBox:
        cb = QComboBox()
        if none_label:
            cb.addItem(none_label, None)
        for f in self.earlier_features():
            if isinstance(f, SketchFeature) and (filt is None or filt(f)):
                cb.addItem(icon("sketch"), f.name, f.id)
        return cb

    def plane_combo(self) -> QComboBox:
        cb = QComboBox()
        for name in REFERENCE_PLANES:
            cb.addItem(icon("plane"), name, {"type": "std", "name": name})
        for f in self.earlier_features():
            if isinstance(f, RefPlaneFeature):
                cb.addItem(icon("ref_plane"), f.name, {"type": "feature", "id": f.id})
        return cb

    def axis_combo(self) -> QComboBox:
        cb = QComboBox()
        for label, spec in STD_AXES:
            cb.addItem(icon("ref_axis"), label, spec)
        for f in self.earlier_features():
            if isinstance(f, RefAxisFeature):
                cb.addItem(icon("ref_axis"), f.name, {"type": "feature", "id": f.id})
        return cb

    @staticmethod
    def select_data(cb, data):
        for i in range(cb.count()):
            if cb.itemData(i) == data:
                cb.setCurrentIndex(i)
                return True
        return False

    # ---- lifecycle ---------------------------------------------------------------------------
    def start(self):
        self.ctx = (self.doc.context_before(self.feature) if self.feature
                    else self.doc.regenerate(upto=len(self.doc.features)))
        for f in self.doc.features:
            self.ctx.features.setdefault(f.id, f)
        self.base = self.ctx.body
        self.base_tess = geometry.tessellate(self.base) if self.base is not None else None
        self.viewer.set_body(self.base_tess)
        self.win._shown_tess = "rolled back"
        self.faces_ = geometry.face_infos(self.base) if self.base is not None else []
        self.edges_ = modeling.edge_infos(self.base) if self.base is not None else []
        self.verts_ = geometry.vertices(self.base) if self.base is not None else np.zeros((0, 3))
        self.viewer.pick_faces = True
        if self.feature is not None:
            self.load(self.feature)
        from .feature_equations import FeatureEquations
        kind = next((key for key, cls in COMMAND_FOR_TYPE.items() if isinstance(self, cls)), None)
        self.equations = FeatureEquations(self, kind)
        self.changed()
        if self.PRIMARY_INPUT:
            QTimer.singleShot(0, self._focus_primary_input)

    def _focus_primary_input(self):
        if self.win.command is self:
            field = getattr(self, self.PRIMARY_INPUT)
            if field.isEnabled():
                field.setFocus(Qt.ShortcutFocusReason)
                field.selectAll()

    def load(self, feature):
        pass

    def changed(self, *_):
        if self.ctx is None:
            return
        self.highlight()
        self._timer.start()

    def cleanup(self):
        for box in self.boxes:
            box.close_replacements()
        self.equations.close()
        self._timer.stop()
        for name in ("preview", "preview_cut", "feat_edges", "feat_ref", "feat_ref_pts"):
            self.viewer.plotter.remove_actor(name, render=False)
            self.viewer.plotter.remove_actor(name + "_edges", render=False)
        self.viewer.set_overlay("feat_sel", [])
        self.viewer.set_face_colors({})
        self.viewer.show_reference_planes(False)
        self.win._shown_tess = None

    # ---- selections --------------------------------------------------------------------------
    def face_item(self, i):
        f = self.faces_[i]
        return ("face", i, FaceRef.from_face(f).to_dict(),
                f"Face <{i + 1}> ({f.kind}{', Ø%.4g' % f.diameter if f.diameter else ''})")

    def edge_item(self, i):
        e = self.edges_[i]
        extra = f", R{e['radius']:.4g}" if e.get("radius") else f", {e['length']:.4g} mm"
        return ("edge", i, EdgeRef.from_info(e).to_dict(), f"Edge <{i + 1}> ({e['kind']}{extra})")

    def resolve_face(self, ref):
        return face_ref(ref).resolve(self.faces_)

    def resolve_edge(self, ref):
        return edge_ref(ref).resolve(self.edges_)

    def load_refs(self, box, faces=(), edges=()):
        items = []
        for kind, refs, infos, factory, item in (
                ("face", faces, self.faces_, face_ref, self.face_item),
                ("edge", edges, self.edges_, edge_ref, self.edge_item)):
            for r in refs:
                match = factory(r).match(infos)
                if match["index"] is not None:
                    k, key, ref, label = item(match["index"])
                    items.append({"kind": k, "key": key, "ref": ref, "label": label, "extra": None})
                else:
                    items.append({"kind": kind, "key": f"unresolved-{len(items)}", "ref": r,
                                  "label": f"{match['status'].title()} {kind} - replacement required",
                                  "extra": None, "unresolved": True})
        box.set_items(items)

    def check_references(self):
        if any(it.get("unresolved") for box in self.boxes for it in box.items):
            raise FeatureError("Repair unresolved references: select the missing or ambiguous row and pick a replacement, or remove it explicitly.")

    def on_click(self, info):
        box = self.active
        if box is None:
            return
        if info.kind == "plane" and box.accepts("plane"):
            name = info.plane
            spec = self.win.plane_spec_by_name(name)
            if spec is not None:
                box.toggle("plane", name, spec, name)
            return
        want_vertex = box.accepts("vertex")
        hit = info.topology or self.viewer.pick_topology(*info.display, vertices=self.verts_ if want_vertex else None)
        if hit is None:
            return
        kind, idx, p = hit
        if kind == "vertex" and box.accepts("vertex"):
            box.toggle("vertex", idx, [float(x) for x in p], f"Vertex ({p[0]:.4g}, {p[1]:.4g}, {p[2]:.4g})")
            return
        if kind == "edge" and not box.accepts("edge") and info.kind == "face":
            kind, idx = "face", info.face
        target = box if box.accepts(kind) else next((b for b in self.boxes if b.accepts(kind)), None)
        if target is None:
            return
        k, key, ref, label = self.face_item(idx) if kind == "face" else self.edge_item(idx)
        target.toggle(k, key, ref, label, extra=np.asarray(p, float))
        self.picked(target, k, key, p)

    def select_other(self, display):
        if self.active is not None:
            return self.viewer.select_other(display, kinds=self.active.kinds, vertices=self.verts_,
                                             receiver=self.on_click)

    def picked(self, box, kind, key, point):
        """Hook: a pick went into a box."""

    def highlight(self):
        faces, edges = set(), set()
        for b in self.boxes:
            for it in b.items:
                if it.get("unresolved"):
                    continue
                if it["kind"] == "face":
                    faces.add(it["key"])
                elif it["kind"] == "edge":
                    edges.add(it["key"])
        self.viewer.set_face_colors({i: SELECT_RGB for i in faces})
        polys = []
        if self.base_tess is not None and edges:
            for pl, eid in zip(self.base_tess.edge_polylines, self.base_tess.edge_ids):
                if eid in edges:
                    polys.append(pl)
        self.viewer.set_overlay("feat_sel", polys, color=EDGE_SEL, width=4.0)

    # ---- feature / preview ---------------------------------------------------------------------
    def make(self):
        raise NotImplementedError

    def build_trial(self):
        """Execute the feature on a copy of the context; returns (feature, context)."""
        self.check_references()
        feat = self.make()
        self.equations.apply(feat)
        ctx = RegenContext()
        ctx.body = self.base
        ctx.features = dict(self.ctx.features)
        if self.feature is not None:
            feat.id = self.feature.id
            ctx.features[feat.id] = feat
        feat.execute(ctx)
        return feat, ctx

    def update_preview(self):
        self.viewer.set_preview(None, name="preview", render=False)
        self.viewer.set_preview(None, name="preview_cut", render=False)
        try:
            feat, ctx = self.build_trial()
        except Exception as e:  # noqa: BLE001 - shown in the panel
            self.msg.setText(f"<span style='color:#c0392b'>{e}</span>")
            self.viewer.render()
            return
        self.msg.setText(self.status_text(feat) or "")
        self.show_result(feat, ctx)
        self.viewer.render()

    def status_text(self, feat) -> str:
        return ""

    def show_result(self, feat, ctx):
        body = ctx.body
        if body is None or body is self.base:
            return
        try:
            if self.base is None:
                added, removed = body, None
            else:
                added = geometry.cut(body, self.base)
                removed = geometry.cut(self.base, body)
            for shape, name, color, op in ((added, "preview", ADD_COLOR, 0.5),
                                           (removed, "preview_cut", CUT_COLOR, 0.45)):
                if shape is not None and geometry.volume(shape) > 1e-9:
                    self.viewer.set_preview(geometry.tessellate(shape), color, op, name=name,
                                            render=False)
        except Exception:  # noqa: BLE001 - preview only
            pass

    def ok(self):
        try:
            self.check_references()
            new = self.make()
            from .parameters_dialog import validate_feature_edit
            self.equations.apply(new)
            bindings = validate_feature_edit(self.doc, self.feature, new, self.equations)
        except (FeatureError, ValueError) as e:
            QMessageBox.warning(self.win, self.title, str(e))
            return False
        self.win.checkpoint()
        if self.feature is not None:
            new.id, new.name, new.suppressed = self.feature.id, self.feature.name, self.feature.suppressed
            i = self.doc.features.index(self.feature)
            self.doc.features[i] = new
            feat = new
        else:
            feat = self.doc.add_feature(new)
            self.win.select_feature_later = feat.id
        self.doc.parameter_bindings = bindings
        self.win.rebuild()
        if feat.error:
            QMessageBox.warning(self.win, feat.name, f"The feature could not be built:\n\n{feat.error}")
        return True


# ============================================================================================
# Fillet / Chamfer
# ============================================================================================


class FilletCommand(FeatureCommand):
    PRIMARY_INPUT = "r"
    TITLE = "Fillet"
    ICON = "fillet3d"

    def build(self):
        f = self.form("Fillet Parameters")
        self.r = spin(2.0, 0.001, DIM_LIMIT)
        f.addRow("Radius", self.r)
        self.var = QCheckBox("Variable: end radius")
        self.r2 = spin(4.0, 0.001, DIM_LIMIT)
        f.addRow(self.var, self.r2)
        self.items = self.pick_box("Items To Fillet", {"edge", "face"},
                                   "Click edges (tangent chains are followed) or faces (all "
                                   "their edges).")
        self.watch(self.r.valueChanged, self.var.toggled, self.r2.valueChanged)

    def load(self, f):
        self.r.setValue(f.radius)
        if f.radius2 is not None:
            self.var.setChecked(True)
            self.r2.setValue(f.radius2)
        self.load_refs(self.items, f.faces, f.edges)

    def make(self):
        return FilletFeature(self.r.value(), self.items.refs("edge"), self.items.refs("face"),
                             self.r2.value() if self.var.isChecked() else None)


class ChamferCommand(FeatureCommand):
    PRIMARY_INPUT = "d"
    TITLE = "Chamfer"
    ICON = "chamfer3d"

    def build(self):
        f = self.form("Chamfer Parameters")
        self.kind = QComboBox()
        for k, v in CHAMFER_TYPES.items():
            self.kind.addItem(v, k)
        f.addRow("Type", self.kind)
        self.d = spin(1.0, 0.001, DIM_LIMIT)
        f.addRow("Distance", self.d)
        self.d2 = spin(1.0, 0.001, DIM_LIMIT)
        f.addRow("Distance 2", self.d2)
        self.angle = spin(45.0, 0.01, 89.99, 3, "°")
        f.addRow("Angle", self.angle)
        self.flip = QCheckBox("Flip direction")
        f.addRow(self.flip)
        self.items = self.pick_box("Items To Chamfer", {"edge", "face"},
                                   "Click edges or faces (all their edges).")
        self.watch(self.kind.currentIndexChanged, self.d.valueChanged, self.d2.valueChanged,
                   self.angle.valueChanged, self.flip.toggled)

    def load(self, f):
        self.select_data(self.kind, f.kind)
        self.d.setValue(f.distance)
        self.d2.setValue(f.distance2)
        self.angle.setValue(f.angle)
        self.flip.setChecked(f.flip)
        self.load_refs(self.items, f.faces, f.edges)

    def changed(self, *a):
        if hasattr(self, "kind"):
            k = self.kind.currentData()
            self.d2.setEnabled(k == "distance_distance")
            self.angle.setEnabled(k == "angle_distance")
            self.flip.setEnabled(k != "distance")
        super().changed(*a)

    def make(self):
        return ChamferFeature(self.d.value(), self.items.refs("edge"), self.items.refs("face"),
                              self.kind.currentData(), self.d2.value(), self.angle.value(),
                              self.flip.isChecked())


# ============================================================================================
# Shell / Draft
# ============================================================================================


class ShellCommand(FeatureCommand):
    PRIMARY_INPUT = "t"
    TITLE = "Shell"
    ICON = "shell"

    def build(self):
        f = self.form("Parameters")
        self.t = spin(2.0, 0.001, DIM_LIMIT)
        f.addRow("Thickness", self.t)
        self.out = QCheckBox("Shell outward")
        f.addRow(self.out)
        self.faces = self.pick_box("Faces To Remove", {"face"},
                                   "Faces left open (none = a closed hollow body).")
        self.watch(self.t.valueChanged, self.out.toggled)

    def load(self, f):
        self.t.setValue(f.thickness)
        self.out.setChecked(f.outward)
        self.load_refs(self.faces, f.faces)

    def make(self):
        return ShellFeature(self.t.value(), self.faces.refs("face"), self.out.isChecked())


class DraftCommand(FeatureCommand):
    TITLE = "Draft"
    ICON = "draft"
    HELP = ("Neutral plane draft: the faces tilt about their line on the neutral plane and "
            "lean inwards along its normal (the direction of pull).")

    def build(self):
        f = self.form("Draft Angle")
        self.angle = spin(1.0, -89.0, 89.0, 3, "°", 0.5)
        f.addRow("Angle", self.angle)
        self.rev = QCheckBox("Reverse direction of pull")
        f.addRow(self.rev)
        self.neutral_cb = self.plane_combo()
        f.addRow("Neutral plane", self.neutral_cb)
        self.neutral = self.pick_box("Neutral Plane (face)", {"face", "plane"},
                                     "Or click a planar face / plane (overrides the list).", single=True)
        self.faces = self.pick_box("Faces To Draft", {"face"})
        self.activate(self.faces)
        self.watch(self.angle.valueChanged, self.rev.toggled, self.neutral_cb.currentIndexChanged)

    def load(self, f):
        self.angle.setValue(f.angle)
        self.rev.setChecked(f.reverse)
        if f.neutral.get("type") == "face":
            self.load_refs(self.neutral, [f.neutral["ref"]])
        else:
            self.select_data(self.neutral_cb, f.neutral)
        self.load_refs(self.faces, f.faces)

    def neutral_spec(self):
        it = self.neutral.items[0] if self.neutral.items else None
        if it is None:
            return self.neutral_cb.currentData()
        return it["ref"] if it["kind"] == "plane" else {"type": "face", "ref": it["ref"]}

    def make(self):
        return DraftFeature(self.angle.value(), self.faces.refs("face"), self.neutral_spec(),
                            self.rev.isChecked())


# ============================================================================================
# Hole Wizard
# ============================================================================================


class HoleCommand(FeatureCommand):
    TITLE = "Hole Specification"
    ICON = "hole"
    HELP = ("Pick the face (or plane) for the holes, then click on it to place hole centres - "
            "or type coordinates. Standard sizes fill in the diameters (ISO metric).")

    def build(self):
        f = self.form("Hole Type")
        self.kind = QComboBox()
        for k, v in HOLE_KINDS.items():
            self.kind.addItem(icon("hole"), v, k)
        f.addRow("Type", self.kind)
        self.size = QComboBox()
        self.size.addItem("(custom diameter)", None)
        for s in METRIC:
            self.size.addItem(s, s)
        self.size.setCurrentIndex(self.size.findData("M6"))
        f.addRow("Size", self.size)
        self.fit = QComboBox()
        for k in ("close", "normal", "loose"):
            self.fit.addItem(k.capitalize(), k)
        self.fit.setCurrentIndex(1)
        f.addRow("Fit", self.fit)
        self.d = spin(6.0, 0.01, DIM_LIMIT)
        f.addRow("Diameter", self.d)
        g = self.form("Counterbore / countersink")
        self.cb_d = spin(11.0, 0.01, DIM_LIMIT)
        self.cb_h = spin(6.4, 0.01, DIM_LIMIT)
        self.csk_d = spin(13.4, 0.01, DIM_LIMIT)
        self.csk_a = spin(90.0, 1.0, 179.0, 2, "°")
        g.addRow("C'bore Ø", self.cb_d)
        g.addRow("C'bore depth", self.cb_h)
        g.addRow("C'sink Ø", self.csk_d)
        g.addRow("C'sink angle", self.csk_a)
        self.override = QCheckBox("Override the standard dimensions")
        g.addRow(self.override)
        e = self.form("End Condition")
        self.end = QComboBox()
        self.end.addItem("Through All", "through_all")
        self.end.addItem("Blind", "blind")
        e.addRow("End", self.end)
        self.depth = spin(10.0, 0.01, DIM_LIMIT)
        e.addRow("Depth", self.depth)
        self.point_cb = QCheckBox("Drill point (118°)")
        self.point_cb.setChecked(True)
        e.addRow(self.point_cb)
        self.rev = QCheckBox("Reverse direction")
        e.addRow(self.rev)
        self.face = self.pick_box("Placement Face / Plane", {"face", "plane"}, single=True)
        self.plane_cb = self.plane_combo()
        self.lay.addWidget(QLabel("or on a plane:"))
        self.lay.addWidget(self.plane_cb)
        pbox = QGroupBox("Hole Positions")
        pl = QVBoxLayout(pbox)
        pl.addWidget(hint("Click on the placement face to add a hole centre. (u, v) are the "
                          "face's sketch coordinates (global coordinates on faces along an axis)."))
        self.pts = QListWidget()
        self.pts.setMaximumHeight(110)
        pl.addWidget(self.pts)
        row = QHBoxLayout()
        self.pu, self.pv = spin(0.0), spin(0.0)
        add = QPushButton("Add (u, v)")
        add.clicked.connect(lambda: self._add_point(self.pu.value(), self.pv.value()))
        rm = QPushButton(icon("delete"), "")
        rm.setToolTip("Remove the selected position")
        rm.clicked.connect(self._remove_point)
        for w_ in (self.pu, self.pv, add, rm):
            row.addWidget(w_)
        pl.addLayout(row)
        self.lay.addWidget(pbox)
        self.points: list = []
        self.callout = QLabel()
        self.callout.setStyleSheet("font-weight:600;color:#1f3b5c;")
        self.lay.addWidget(self.callout)
        self.watch(self.kind.currentIndexChanged, self.size.currentIndexChanged,
                   self.fit.currentIndexChanged, self.d.valueChanged, self.cb_d.valueChanged,
                   self.cb_h.valueChanged, self.csk_d.valueChanged, self.csk_a.valueChanged,
                   self.override.toggled, self.end.currentIndexChanged, self.depth.valueChanged,
                   self.point_cb.toggled, self.rev.toggled, self.plane_cb.currentIndexChanged)
        if self.opts.get("face") is not None:
            self._pending_face = self.opts["face"]

    def start(self):
        super().start()
        i = getattr(self, "_pending_face", None)
        if i is not None and self.feature is None and 0 <= i < len(self.faces_) \
                and self.faces_[i].is_planar:
            k, key, ref, label = self.face_item(i)
            self.face.toggle(k, key, ref, label)

    def load(self, f):
        self.select_data(self.kind, f.kind)
        self.select_data(self.size, f.size)
        self.select_data(self.fit, f.fit)
        if f.d is not None:
            self.d.setValue(f.d)
        explicit = any(getattr(f, k) is not None for k in ("cbore_d", "cbore_depth", "csk_d")) \
            or (f.size and f.d is not None)
        self.override.setChecked(bool(explicit))
        for sp, k in ((self.cb_d, "cbore_d"), (self.cb_h, "cbore_depth"), (self.csk_d, "csk_d")):
            if getattr(f, k) is not None:
                sp.setValue(getattr(f, k))
        self.csk_a.setValue(f.csk_angle)
        self.select_data(self.end, f.end)
        self.depth.setValue(f.depth)
        self.point_cb.setChecked(f.drill_point)
        self.rev.setChecked(f.reverse)
        if f.plane.get("type") == "face":
            self.load_refs(self.face, [f.plane["ref"]])
        else:
            self.select_data(self.plane_cb, f.plane)
        self.points = [list(p) for p in f.points]
        self._refresh_points()

    def plane_spec(self):
        it = self.face.items[0] if self.face.items else None
        if it is None:
            return self.plane_cb.currentData()
        return it["ref"] if it["kind"] == "plane" else {"type": "face", "ref": it["ref"]}

    def current_plane(self):
        it = self.face.items[0] if self.face.items else None
        if it is not None and it["kind"] == "face":
            return plane_on_face(self.faces_[it["key"]])
        from ..core.refs import resolve_plane
        return resolve_plane(self.plane_spec(), self.ctx)

    def picked(self, box, kind, key, point):
        if box is self.face and kind == "face" and self.face.items:
            self.activate(self.face)          # next clicks on that face place holes
            if len(self.points) == 0 and point is not None:
                self._add_point(*self.current_plane().to_local(point))

    def on_click(self, info):
        # clicks on the chosen face (after it is picked) place hole centres
        if self.face.items and info.kind == "face" and info.point is not None:
            it = self.face.items[0]
            if it["kind"] == "face" and info.face == it["key"]:
                self._add_point(*self.current_plane().to_local(info.point))
                return
        super().on_click(info)

    def _add_point(self, u, v):
        self.points.append([float(u), float(v)])
        self._refresh_points()

    def _remove_point(self):
        r = self.pts.currentRow()
        if 0 <= r < len(self.points):
            del self.points[r]
            self._refresh_points()

    def _refresh_points(self):
        self.pts.clear()
        for i, (u, v) in enumerate(self.points):
            self.pts.addItem(f"{i + 1}:  u = {u:.4g},  v = {v:.4g}")
        self.changed()

    def changed(self, *a):
        if hasattr(self, "kind"):
            k = self.kind.currentData()
            std = self.size.currentData() is not None
            ov = self.override.isChecked()
            self.fit.setEnabled(std and k != "tapped")
            self.d.setEnabled(not std or ov)
            for w_ in (self.cb_d, self.cb_h):
                w_.setEnabled(k == "counterbore" and (not std or ov))
            for w_ in (self.csk_d, self.csk_a):
                w_.setEnabled(k == "countersink" and (not std or ov))
            self.depth.setEnabled(self.end.currentData() == "blind")
            self.point_cb.setEnabled(self.end.currentData() == "blind")
            if std and not ov:
                from ..core.features_more import hole_sizes
                dm = hole_sizes(k, self.size.currentData(), self.fit.currentData())
                for sp, key in ((self.d, "d"), (self.cb_d, "cbore_d"), (self.cb_h, "cbore_depth"),
                                (self.csk_d, "csk_d")):
                    if key in dm:
                        sp.blockSignals(True)
                        sp.setValue(dm[key])
                        sp.blockSignals(False)
        super().changed(*a)

    def make(self):
        k = self.kind.currentData()
        std = self.size.currentData()
        ov = self.override.isChecked() or std is None
        f = HoleFeature(self.plane_spec(), self.points, k, std,
                        self.d.value() if ov else None, self.depth.value(), self.end.currentData(),
                        self.fit.currentData(),
                        self.cb_d.value() if ov and k == "counterbore" else None,
                        self.cb_h.value() if ov and k == "counterbore" else None,
                        self.csk_d.value() if ov and k == "countersink" else None,
                        self.csk_a.value(), None, self.point_cb.isChecked(), self.rev.isChecked())
        return f

    def status_text(self, feat):
        try:
            self.callout.setText(feat.callout())
        except FeatureError:
            self.callout.setText("")
        return ""

    def show_result(self, feat, ctx):
        super().show_result(feat, ctx)
        try:
            pl = self.current_plane()
            pts = [pl.to_world(*p) for p in self.points]
            self.viewer.set_markers("feat_ref_pts", pts, color=EDGE_SEL, size=10)
        except Exception:  # noqa: BLE001
            pass


# ============================================================================================
# Patterns / mirror
# ============================================================================================


class _SeedsMixin:
    def seed_box(self):
        g = QGroupBox("Features To Pattern")
        gl = QVBoxLayout(g)
        self.bodies_cb = QCheckBox("Bodies (the whole part)")
        gl.addWidget(self.bodies_cb)
        self.seed_list = QListWidget()
        self.seed_list.setMaximumHeight(130)
        for f in self.earlier_features():
            if f.TYPE in TOOL_TYPES:
                it = QListWidgetItem(icon(self.win.feature_icon(f)), f.name)
                it.setData(Qt.UserRole, f.id)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Unchecked)
                self.seed_list.addItem(it)
        gl.addWidget(self.seed_list)
        self.lay.addWidget(g)
        self.seed_list.itemChanged.connect(self.changed)
        self.bodies_cb.toggled.connect(self.changed)
        pre = self.opts.get("seed")
        if pre:
            self._check_seeds([pre])

    def _check_seeds(self, ids):
        for i in range(self.seed_list.count()):
            it = self.seed_list.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in ids else Qt.Unchecked)

    def seeds(self):
        return [self.seed_list.item(i).data(Qt.UserRole) for i in range(self.seed_list.count())
                if self.seed_list.item(i).checkState() == Qt.Checked]

    def load_seeds(self, f):
        self.bodies_cb.setChecked(f.bodies)
        self._check_seeds(f.seeds)


class _AxisPick:
    """A single edge / face pick with a combo fallback (X / Y / Z / reference axes)."""

    def __init__(self, cmd, title, tip):
        self.cmd = cmd
        self.box = cmd.pick_box(title, {"edge", "face"}, tip, single=True)
        self.cb = cmd.axis_combo()
        cmd.lay.addWidget(self.cb)
        self.cb.currentIndexChanged.connect(cmd.changed)

    def spec(self):
        if self.box.items:
            it = self.box.items[0]
            return {"type": it["kind"], "ref": it["ref"]}
        return self.cb.currentData()

    def load(self, spec):
        if spec is None:
            return
        if spec.get("type") in ("edge", "face"):
            if spec["type"] == "edge":
                self.cmd.load_refs(self.box, edges=[spec["ref"]])
            else:
                self.cmd.load_refs(self.box, faces=[spec["ref"]])
        else:
            self.cmd.select_data(self.cb, spec)


class LinearPatternCommand(_SeedsMixin, FeatureCommand):
    TITLE = "Linear Pattern"
    ICON = "lpattern3d"

    def build(self):
        self.dir1 = _AxisPick(self, "Direction 1", "A straight edge, a planar face (its normal) "
                                                   "or pick X / Y / Z below.")
        f = self.form()
        self.rev1 = QCheckBox("Reverse")
        self.sp1 = spin(10.0, 0.001, DIM_LIMIT)
        self.n1 = QSpinBox()
        self.n1.setRange(1, 10000)
        self.n1.setValue(3)
        f.addRow(self.rev1)
        f.addRow("Spacing", self.sp1)
        f.addRow("Instances", self.n1)
        self.use2 = QCheckBox("Direction 2")
        self.lay.addWidget(self.use2)
        self.dir2 = _AxisPick(self, "Direction 2", "")
        f2 = self.form()
        self.rev2 = QCheckBox("Reverse")
        self.sp2 = spin(10.0, 0.001, DIM_LIMIT)
        self.n2 = QSpinBox()
        self.n2.setRange(1, 10000)
        self.n2.setValue(2)
        f2.addRow(self.rev2)
        f2.addRow("Spacing", self.sp2)
        f2.addRow("Instances", self.n2)
        self.dir2.cb.setCurrentIndex(1)
        self.seed_box()
        self.activate(self.dir1.box)
        self.watch(self.rev1.toggled, self.sp1.valueChanged, self.n1.valueChanged, self.use2.toggled,
                   self.rev2.toggled, self.sp2.valueChanged, self.n2.valueChanged)

    def load(self, f):
        self.dir1.load(f.direction)
        self.rev1.setChecked(f.reverse)
        self.sp1.setValue(f.spacing)
        self.n1.setValue(f.count)
        if f.direction2 is not None:
            self.use2.setChecked(True)
            self.dir2.load(f.direction2)
        self.rev2.setChecked(f.reverse2)
        self.sp2.setValue(f.spacing2)
        self.n2.setValue(f.count2)
        self.load_seeds(f)
        self._skip = f.skip

    def make(self):
        use2 = self.use2.isChecked()
        seeds = self.seeds()
        return LinearPatternFeature(seeds, self.dir1.spec(), self.sp1.value(), self.n1.value(),
                                    self.dir2.spec() if use2 else None, self.sp2.value(),
                                    self.n2.value() if use2 else 1, self.rev1.isChecked(),
                                    self.rev2.isChecked(), getattr(self, "_skip", None),
                                    self.bodies_cb.isChecked() or not seeds)


class CircularPatternCommand(_SeedsMixin, FeatureCommand):
    TITLE = "Circular Pattern"
    ICON = "cpattern3d"

    def build(self):
        self.axis = _AxisPick(self, "Pattern Axis", "A cylindrical / conical face, a circular or "
                                                    "straight edge, or pick an axis below.")
        f = self.form()
        self.rev = QCheckBox("Reverse")
        self.angle = spin(360.0, 0.001, 360.0, 3, "°", 15.0)
        self.equal = QCheckBox("Equal spacing (angle = total)")
        self.equal.setChecked(True)
        self.n = QSpinBox()
        self.n.setRange(1, 10000)
        self.n.setValue(4)
        f.addRow(self.rev)
        f.addRow("Angle", self.angle)
        f.addRow(self.equal)
        f.addRow("Instances", self.n)
        self.axis.cb.setCurrentIndex(2)
        self.seed_box()
        self.activate(self.axis.box)
        self.watch(self.rev.toggled, self.angle.valueChanged, self.equal.toggled, self.n.valueChanged)

    def load(self, f):
        self.axis.load(f.axis)
        self.rev.setChecked(f.reverse)
        self.angle.setValue(f.angle)
        self.equal.setChecked(f.equal_spacing)
        self.n.setValue(f.count)
        self.load_seeds(f)
        self._skip = f.skip

    def make(self):
        seeds = self.seeds()
        return CircularPatternFeature(seeds, self.axis.spec(), self.n.value(), self.angle.value(),
                                      self.equal.isChecked(), self.rev.isChecked(),
                                      getattr(self, "_skip", None),
                                      self.bodies_cb.isChecked() or not seeds)


class MirrorCommand(_SeedsMixin, FeatureCommand):
    TITLE = "Mirror"
    ICON = "mirror3d"

    def build(self):
        self.face = self.pick_box("Mirror Face / Plane", {"face", "plane"},
                                  "Click a planar face or a plane - or choose one below.", single=True)
        self.plane_cb = self.plane_combo()
        self.plane_cb.setCurrentIndex(2)
        self.lay.addWidget(self.plane_cb)
        self.seed_box()
        self.watch(self.plane_cb.currentIndexChanged)

    def load(self, f):
        if f.plane.get("type") == "face":
            self.load_refs(self.face, [f.plane["ref"]])
        elif f.plane.get("type") in ("std", "feature"):
            self.select_data(self.plane_cb, f.plane)
        self.load_seeds(f)

    def make(self):
        it = self.face.items[0] if self.face.items else None
        if it is None:
            spec = self.plane_cb.currentData()
        else:
            spec = it["ref"] if it["kind"] == "plane" else {"type": "face", "ref": it["ref"]}
        seeds = self.seeds()
        return MirrorFeature(seeds, spec, self.bodies_cb.isChecked() or not seeds)


# ============================================================================================
# Sweep / Loft / Boundary / Rib
# ============================================================================================


def _is_curve_feature(f):
    return isinstance(f, (HelixFeature, CurveFeature))


class SweepCommand(FeatureCommand):
    ICON = "sweep"

    def __init__(self, win, feature=None, operation="boss", **opts):
        self.operation = feature.operation if feature else operation
        self.TITLE = "Cut-Sweep" if self.operation == "cut" else "Sweep"
        self.ICON = "sweep_cut" if self.operation == "cut" else "sweep"
        super().__init__(win, feature, **opts)

    def build(self):
        f = self.form("Profile and Path")
        self.circ = QCheckBox("Circular profile")
        self.circ.setToolTip("Sweep a circle of the given diameter along the path (no profile sketch)")
        f.addRow(self.circ)
        self.d = spin(5.0, 0.001, DIM_LIMIT)
        self.di = spin(0.0, 0.0, DIM_LIMIT)
        f.addRow("Diameter", self.d)
        f.addRow("Inner Ø (tube)", self.di)
        self.profile = self.sketch_combo(lambda s: bool(s.faces))
        f.addRow("Profile", self.profile)
        self.path = QComboBox()
        for x in self.earlier_features():
            if isinstance(x, SketchFeature):
                self.path.addItem(icon("sketch"), x.name, {"sketch": x.id})
            elif _is_curve_feature(x):
                self.path.addItem(icon("helix" if isinstance(x, HelixFeature) else "curve3d"),
                                  x.name, {"feature": x.id})
        if self.path.count():
            self.path.setCurrentIndex(self.path.count() - 1)
        f.addRow("Path", self.path)
        self.circ.setChecked(self.profile.count() == 0)
        self.watch(self.circ.toggled, self.d.valueChanged, self.di.valueChanged,
                   self.profile.currentIndexChanged, self.path.currentIndexChanged)

    def load(self, f):
        self.circ.setChecked(f.profile is None)
        if f.diameter:
            self.d.setValue(f.diameter)
        if f.inner_diameter:
            self.di.setValue(f.inner_diameter)
        if f.profile:
            self.select_data(self.profile, f.profile)
        self.select_data(self.path, f.path)

    def changed(self, *a):
        if hasattr(self, "circ"):
            c = self.circ.isChecked()
            self.d.setEnabled(c)
            self.di.setEnabled(c)
            self.profile.setEnabled(not c)
        super().changed(*a)

    def make(self):
        if self.path.currentData() is None:
            raise FeatureError("Pick a path (a sketch, helix or curve)")
        c = self.circ.isChecked()
        return SweepFeature(self.path.currentData(), None if c else self.profile.currentData(),
                            self.operation, self.d.value() if c else None,
                            (self.di.value() or None) if c else None)

    def show_result(self, feat, ctx):
        super().show_result(feat, ctx)
        try:
            from ..core.features_more import _path_wire
            w = _path_wire(ctx, feat.path)
            self.viewer.set_polylines("feat_ref", [modeling.wire_points(w)], REF_COLOR, 2.5)
        except Exception:  # noqa: BLE001
            pass


class LoftCommand(FeatureCommand):
    ICON = "loft"
    CLS = LoftFeature

    def __init__(self, win, feature=None, operation="boss", **opts):
        self.operation = feature.operation if feature else operation
        cut = self.operation == "cut"
        boundary = self.CLS is BoundaryFeature
        self.TITLE = ("Boundary" if boundary else "Loft") + ("-Cut" if cut and boundary else
                                                             (" Cut" if cut else ""))
        self.ICON = ("boundary" if boundary else "loft") + ("_cut" if cut else "")
        super().__init__(win, feature, **opts)

    def build(self):
        g = QGroupBox("Profiles (in order)")
        gl = QVBoxLayout(g)
        self.list = QListWidget()
        self.list.setMaximumHeight(140)
        gl.addWidget(self.list)
        row = QHBoxLayout()
        self.add_cb = self.sketch_combo()
        add = QPushButton("Add")
        add.clicked.connect(self._add)
        up = QPushButton("Up")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Down")
        down.clicked.connect(lambda: self._move(1))
        rm = QPushButton(icon("delete"), "")
        rm.clicked.connect(self._remove)
        for w_ in (self.add_cb, add, up, down, rm):
            row.addWidget(w_)
        gl.addLayout(row)
        self.lay.addWidget(g)
        f = self.form()
        self.ruled = QCheckBox("Ruled (straight between profiles)")
        self.closed = QCheckBox("Close loft")
        if self.CLS is not BoundaryFeature:
            f.addRow(self.ruled)
        f.addRow(self.closed)
        self.sections: list = []
        # default: the sketches not used yet, in tree order
        for f_ in self.earlier_features():
            if isinstance(f_, SketchFeature) and not self.doc.is_consumed(f_.id) and f_.faces:
                self.sections.append(f_.id)
        self._refresh()
        self.watch(self.ruled.toggled, self.closed.toggled)

    def load(self, f):
        self.sections = list(f.sections)
        self.ruled.setChecked(f.ruled)
        self.closed.setChecked(f.closed)
        self._refresh()

    def _add(self):
        sid = self.add_cb.currentData()
        if sid:
            self.sections.append(sid)
            self._refresh()

    def _move(self, d):
        r = self.list.currentRow()
        j = r + d
        if 0 <= r < len(self.sections) and 0 <= j < len(self.sections):
            self.sections[r], self.sections[j] = self.sections[j], self.sections[r]
            self._refresh()
            self.list.setCurrentRow(j)

    def _remove(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.sections):
            del self.sections[r]
            self._refresh()

    def _refresh(self):
        self.list.clear()
        for s in self.sections:
            if isinstance(s, str):
                f = self.doc.feature(s)
                self.list.addItem(QListWidgetItem(icon("sketch"), f.name if f else "?"))
            else:
                self.list.addItem(f"Point ({s[0]:g}, {s[1]:g}, {s[2]:g})")
        self.changed()

    def make(self):
        return self.CLS(self.sections, self.operation, self.ruled.isChecked(), self.closed.isChecked())


class BoundaryCommand(LoftCommand):
    CLS = BoundaryFeature


class RibCommand(FeatureCommand):
    TITLE = "Rib"
    ICON = "rib"
    HELP = ("Sketch an open line where the rib goes. Parallel to sketch: the area between the "
            "line and the body is filled (a gusset). Normal to sketch: the line is thickened "
            "and extruded until it meets the body.")

    def build(self):
        f = self.form("Parameters")
        self.sketch = self.sketch_combo()
        if self.sketch.count():
            self.sketch.setCurrentIndex(self.sketch.count() - 1)
        f.addRow("Sketch", self.sketch)
        self.t = spin(3.0, 0.001, DIM_LIMIT)
        f.addRow("Thickness", self.t)
        self.side = QComboBox()
        for k, v in (("both", "Both sides"), ("first", "First side"), ("second", "Second side")):
            self.side.addItem(v, k)
        f.addRow("Thickness side", self.side)
        self.direction = QComboBox()
        self.direction.addItem("Parallel to sketch", "parallel")
        self.direction.addItem("Normal to sketch", "normal")
        f.addRow("Extrusion direction", self.direction)
        self.flip = QCheckBox("Flip material side")
        f.addRow(self.flip)
        self.watch(self.sketch.currentIndexChanged, self.t.valueChanged, self.side.currentIndexChanged,
                   self.direction.currentIndexChanged, self.flip.toggled)

    def load(self, f):
        self.select_data(self.sketch, f.sketch_id)
        self.t.setValue(f.thickness)
        self.select_data(self.side, f.side)
        self.select_data(self.direction, f.direction)
        self.flip.setChecked(f.flip)

    def make(self):
        if self.sketch.currentData() is None:
            raise FeatureError("Sketch the rib line first")
        return RibFeature(self.sketch.currentData(), self.t.value(), self.direction.currentData(),
                          self.side.currentData(), self.flip.isChecked())


class WrapCommand(FeatureCommand):
    TITLE = "Wrap"
    ICON = "wrap"
    HELP = ("Sketch the profiles (or text) on a plane parallel to the cylinder's axis, e.g. "
            "a plane tangent to it, then pick the cylindrical face. Lengths along the "
            "circumference are kept as arc lengths.")

    def build(self):
        f = self.form("Wrap Parameters")
        self.kind = QComboBox()
        self.kind.addItem("Emboss (raised)", "emboss")
        self.kind.addItem("Deboss (recessed)", "deboss")
        f.addRow("Type", self.kind)
        self.depth = spin(1.0, 0.001, DIM_LIMIT)
        f.addRow("Thickness", self.depth)
        self.sketch = self.sketch_combo(lambda s: bool(s.faces))
        if self.sketch.count():
            self.sketch.setCurrentIndex(self.sketch.count() - 1)
        f.addRow("Source sketch", self.sketch)
        self.face = self.pick_box("Face To Wrap On", {"face"}, "A cylindrical face.", single=True)
        self.watch(self.kind.currentIndexChanged, self.depth.valueChanged,
                   self.sketch.currentIndexChanged)

    def load(self, f):
        self.select_data(self.kind, f.kind)
        self.depth.setValue(f.depth)
        self.select_data(self.sketch, f.sketch_id)
        if f.face is not None:
            self.load_refs(self.face, [f.face])

    def make(self):
        from ..core.features_more import WrapFeature
        if self.sketch.currentData() is None:
            raise FeatureError("Sketch the profiles to wrap first")
        ref = self.face.items[0]["ref"] if self.face.items else None
        return WrapFeature(self.sketch.currentData(), ref, self.depth.value(), self.kind.currentData())


class IntersectCommand(FeatureCommand):
    TITLE = "Intersect"
    ICON = "intersect"
    HELP = ("Pick planes (or planar faces) that cut the body. The regions they make are "
            "listed: untick material regions to remove them, tick closed voids to fill them "
            "(e.g. the inside of a shelled part under a capping plane).")

    def build(self):
        self.planes = self.pick_box("Planes / Faces", {"face", "plane"}, "Click planar faces, or "
                                                                          "add planes below.")
        row = QHBoxLayout()
        self.plane_cb = self.plane_combo()
        add = QPushButton("Add plane")
        add.clicked.connect(self._add_plane)
        row.addWidget(self.plane_cb, 1)
        row.addWidget(add)
        self.lay.addLayout(row)
        g = QGroupBox("Regions")
        gl = QVBoxLayout(g)
        self.reg_list = QListWidget()
        self.reg_list.setMinimumHeight(140)
        gl.addWidget(self.reg_list)
        self.lay.addWidget(g)
        self.reg_list.itemChanged.connect(self.changed)
        self._regions = []
        self._keep_state: dict = {}

    def _add_plane(self):
        spec = self.plane_cb.currentData()
        self.planes.toggle("plane", str(spec), spec, self.plane_cb.currentText())

    def plane_specs(self):
        out = []
        for it in self.planes.items:
            out.append(it["ref"] if it["kind"] == "plane" else {"type": "face", "ref": it["ref"]})
        return out

    def load(self, f):
        items = []
        for spec in f.planes:
            if spec.get("type") == "face":
                i = self.resolve_face(spec["ref"])
                if i is not None:
                    k, key, ref, label = self.face_item(i)
                    items.append({"kind": k, "key": key, "ref": ref, "label": label, "extra": None})
            else:
                from ..core.refs import describe_plane
                items.append({"kind": "plane", "key": str(spec), "ref": spec, "extra": None,
                              "label": describe_plane(spec, self.doc.features)})
        self.planes.set_items(items)
        self._init_exclude, self._init_fill = f.exclude, f.fill

    def _refresh_regions(self):
        from ..core.features_more import IntersectFeature
        specs = self.plane_specs()
        regs = []
        if specs:
            try:
                regs = IntersectFeature(specs).regions(self.ctx)
            except Exception:  # noqa: BLE001 - reported by the preview
                regs = []
        sig = [(r["material"], round(r["volume"], 6)) for r in regs]
        if sig == [(r["material"], round(r["volume"], 6)) for r in self._regions]:
            return
        # keep the user's choices for regions that still exist
        for i in range(self.reg_list.count()):
            it = self.reg_list.item(i)
            self._keep_state[it.data(Qt.UserRole)] = it.checkState() == Qt.Checked
        self._regions = regs
        self.reg_list.blockSignals(True)
        self.reg_list.clear()
        init_x = getattr(self, "_init_exclude", [])
        init_f = getattr(self, "_init_fill", [])
        m = v = 0
        for r in regs:
            key = (r["material"], round(r["volume"], 3))
            if r["material"]:
                m += 1
                label = f"Region {m}: material, {r['volume']:.4g} mm³"
                default = not any(modeling.point_in_solid(r["solid"], np.asarray(p)) for p in init_x)
            else:
                v += 1
                label = f"Void {v}: closed empty space, {r['volume']:.4g} mm³ (tick to fill)"
                default = any(modeling.point_in_solid(r["solid"], np.asarray(p)) for p in init_f)
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if self._keep_state.get(key, default) else Qt.Unchecked)
            self.reg_list.addItem(it)
        self.reg_list.blockSignals(False)

    def update_preview(self):
        self._refresh_regions()
        super().update_preview()

    def make(self):
        from ..core.features_more import IntersectFeature
        exclude, fill = [], []
        for i, r in enumerate(self._regions):
            it = self.reg_list.item(i)
            on = it is not None and it.checkState() == Qt.Checked
            if r["material"] and not on:
                exclude.append(r["point"].tolist())
            elif not r["material"] and on:
                fill.append(r["point"].tolist())
        return IntersectFeature(self.plane_specs(), exclude, fill)


# ============================================================================================
# Reference geometry and curves
# ============================================================================================


class _RefCommand(FeatureCommand):
    def show_result(self, feat, ctx):
        L = self.win.model_size() * 0.6
        polys, pts = [], []
        pl = getattr(feat, "result_plane", None)
        if pl is not None:
            h = L / 2
            q = [pl.to_world(u, v) for u, v in ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h))]
            polys.append(np.array(q))
        ax = getattr(feat, "result_axis", None)
        if ax is not None:
            p, d = ax
            polys.append(np.array([p - d * L, p + d * L]))
        pt = getattr(feat, "result_point", None)
        if pt is not None:
            pts.append(pt)
        w = getattr(feat, "result_wire", None)
        if w is not None:
            polys.append(modeling.wire_points(w, 64))
        self.viewer.set_polylines("feat_ref", polys, REF_COLOR, 2.5)
        self.viewer.set_markers("feat_ref_pts", pts, color=REF_COLOR, size=12)


class RefPlaneCommand(_RefCommand):
    TITLE = "Plane"
    ICON = "ref_plane"

    MODES = [("offset", "Offset distance"), ("angle", "At angle"), ("mid", "Mid plane"),
             ("points", "Through 3 points"), ("normal", "Normal to curve / axis")]

    def build(self):
        f = self.form("Plane")
        self.pform = f
        self.mode = QComboBox()
        for k, v in self.MODES:
            self.mode.addItem(v, k)
        f.addRow("Type", self.mode)
        self.dist = spin(10.0)
        f.addRow("Distance", self.dist)
        self.angle = spin(45.0, -360, 360, 3, "°", 5.0)
        f.addRow("Angle", self.angle)
        self.flip = QCheckBox("Flip")
        f.addRow(self.flip)
        self.base_cb = self.plane_combo()
        self.base_cb.setCurrentIndex(1)
        f.addRow("Base plane", self.base_cb)
        self.base_box = self.pick_box("First Reference (face / plane)", {"face", "plane"},
                                  "Or click a planar face / plane (overrides the list).", single=True)
        self.base2_box = self.pick_box("Second Reference (mid plane)", {"face", "plane"}, single=True)
        self.axis = _AxisPick(self, "Axis / Curve", "Angle: the line to rotate about; Normal: the "
                                                    "edge / axis the plane is normal to.")
        self.pts = self.pick_box("Points", {"vertex"}, "Three model vertices.")
        self.activate(self.base_box)
        self.mode.currentIndexChanged.connect(self._mode_ui)
        self._mode_ui()

    def _mode_ui(self, *_):
        m = self.mode.currentData()
        rows = {self.dist: m == "offset", self.angle: m == "angle",
                self.base_cb: m in ("offset", "angle", "mid"), self.flip: True}
        for w_, on in rows.items():
            self.pform.setRowVisible(w_, on)
        self.base_box.box.setVisible(m in ("offset", "angle", "mid"))
        self.base2_box.box.setVisible(m == "mid")
        self.axis.box.box.setVisible(m in ("angle", "normal"))
        self.axis.cb.setVisible(m in ("angle", "normal"))
        self.pts.box.setVisible(m == "points")
        target = {"points": self.pts, "normal": self.axis.box, "mid": self.base_box}.get(m, self.base_box)
        self.activate(target)
        self.watch(self.mode.currentIndexChanged, self.dist.valueChanged, self.angle.valueChanged,
                   self.flip.toggled, self.base_cb.currentIndexChanged)

    def _spec(self, box, cb=None):
        it = box.items[0] if box.items else None
        if it is None:
            return cb.currentData() if cb is not None else None
        return it["ref"] if it["kind"] == "plane" else {"type": "face", "ref": it["ref"]}

    def load(self, f):
        self.select_data(self.mode, f.mode)
        self.dist.setValue(f.distance)
        self.angle.setValue(f.angle)
        self.flip.setChecked(f.flip)
        if f.base.get("type") == "face":
            self.load_refs(self.base_box, [f.base["ref"]])
        else:
            self.select_data(self.base_cb, f.base)
        if f.base2 and f.base2.get("type") == "face":
            self.load_refs(self.base2_box, [f.base2["ref"]])
        self.axis.load(f.axis)
        self.pts.set_items([{"kind": "vertex", "key": i, "ref": p, "extra": None,
                             "label": f"Point ({p[0]:.4g}, {p[1]:.4g}, {p[2]:.4g})"}
                            for i, p in enumerate(f.points)])

    def make(self):
        m = self.mode.currentData()
        return RefPlaneFeature(m, self._spec(self.base_box, self.base_cb), self.dist.value(),
                               self.axis.spec(), self.angle.value(),
                               self.pts.refs("vertex") if m == "points" else None,
                               self._spec(self.base2_box), None, self.flip.isChecked())


class RefAxisCommand(_RefCommand):
    TITLE = "Axis"
    ICON = "ref_axis"
    MODES = [("face", "Cylindrical / conical face"), ("edge", "One line / edge"),
             ("planes", "Two planes"), ("points", "Two points / vertices")]

    def build(self):
        f = self.form("Axis")
        self.aform = f
        self.mode = QComboBox()
        for k, v in self.MODES:
            self.mode.addItem(v, k)
        f.addRow("Type", self.mode)
        self.ref = self.pick_box("Reference", {"face", "edge"}, single=True)
        self.p1 = self.plane_combo()
        self.p2 = self.plane_combo()
        self.p2.setCurrentIndex(1)
        f.addRow("Plane 1", self.p1)
        f.addRow("Plane 2", self.p2)
        self.pts = self.pick_box("Points", {"vertex"})
        self.activate(self.ref)
        self.watch(self.mode.currentIndexChanged, self.p1.currentIndexChanged, self.p2.currentIndexChanged)
        self.mode.currentIndexChanged.connect(self._mode_ui)
        self._mode_ui()

    def _mode_ui(self, *_):
        m = self.mode.currentData()
        self.aform.setRowVisible(self.p1, m == "planes")
        self.aform.setRowVisible(self.p2, m == "planes")
        self.ref.box.setVisible(m in ("face", "edge"))
        self.pts.box.setVisible(m == "points")
        self.activate(self.pts if m == "points" else self.ref)

    def picked(self, box, kind, key, point):
        if box is self.ref:
            self.select_data(self.mode, kind)

    def load(self, f):
        self.select_data(self.mode, f.mode)
        if f.mode == "face":
            self.load_refs(self.ref, faces=[f.ref])
        elif f.mode == "edge":
            self.load_refs(self.ref, edges=[f.ref])
        if f.base:
            self.select_data(self.p1, f.base)
        if f.base2:
            self.select_data(self.p2, f.base2)
        self.pts.set_items([{"kind": "vertex", "key": i, "ref": p, "extra": None,
                             "label": f"Point ({p[0]:.4g}, {p[1]:.4g}, {p[2]:.4g})"}
                            for i, p in enumerate(f.points)])

    def make(self):
        m = self.mode.currentData()
        ref = self.ref.items[0]["ref"] if self.ref.items else None
        return RefAxisFeature(m, self.pts.refs("vertex"), ref, self.p1.currentData(),
                              self.p2.currentData())


class RefPointCommand(_RefCommand):
    TITLE = "Point"
    ICON = "ref_point"
    MODES = [("arc_center", "Arc center"), ("face_center", "Center of face"), ("coords", "Coordinates")]

    def build(self):
        f = self.form("Point")
        self.qform = f
        self.mode = QComboBox()
        for k, v in self.MODES:
            self.mode.addItem(v, k)
        f.addRow("Type", self.mode)
        self.x, self.y, self.z = spin(0.0), spin(0.0), spin(0.0)
        f.addRow("X", self.x)
        f.addRow("Y", self.y)
        f.addRow("Z", self.z)
        self.ref = self.pick_box("Reference", {"face", "edge"}, single=True)
        self.watch(self.mode.currentIndexChanged, self.x.valueChanged, self.y.valueChanged,
                   self.z.valueChanged)
        self.mode.currentIndexChanged.connect(self._mode_ui)
        self._mode_ui()

    def _mode_ui(self, *_):
        coords = self.mode.currentData() == "coords"
        for w_ in (self.x, self.y, self.z):
            self.qform.setRowVisible(w_, coords)
        self.ref.box.setVisible(not coords)

    def picked(self, box, kind, key, point):
        self.select_data(self.mode, "arc_center" if kind == "edge" else "face_center")

    def load(self, f):
        self.select_data(self.mode, f.mode)
        for sp, v in zip((self.x, self.y, self.z), f.coords):
            sp.setValue(v)
        if f.mode == "arc_center" and f.ref:
            self.load_refs(self.ref, edges=[f.ref])
        elif f.mode == "face_center" and f.ref:
            self.load_refs(self.ref, faces=[f.ref])

    def make(self):
        ref = self.ref.items[0]["ref"] if self.ref.items else None
        return RefPointFeature(self.mode.currentData(), [self.x.value(), self.y.value(), self.z.value()], ref)


class HelixCommand(_RefCommand):
    TITLE = "Helix/Spiral"
    ICON = "helix"

    def build(self):
        f = self.form("Placement")
        self.sketch = self.sketch_combo(
            lambda s: sum(1 for e in s.sketch.entities.values() if e.kind == "circle") == 1,
            none_label="(explicit center / axis)")
        if self.sketch.count() > 1:
            self.sketch.setCurrentIndex(self.sketch.count() - 1)
        f.addRow("Circle sketch", self.sketch)
        self.cx, self.cy, self.cz = spin(0.0), spin(0.0), spin(0.0)
        f.addRow("Center X", self.cx)
        f.addRow("Center Y", self.cy)
        f.addRow("Center Z", self.cz)
        self.axis = QComboBox()
        for lbl, v in (("X", (1, 0, 0)), ("Y", (0, 1, 0)), ("Z", (0, 0, 1))):
            self.axis.addItem(lbl, v)
        self.axis.setCurrentIndex(1)
        f.addRow("Axis", self.axis)
        self.dia = spin(20.0, 0.001, DIM_LIMIT)
        f.addRow("Diameter", self.dia)
        g = self.form("Defined By")
        self.by = QComboBox()
        for k, v in (("pitch_revolutions", "Pitch and Revolution"), ("height_revolutions", "Height and Revolution"),
                     ("height_pitch", "Height and Pitch")):
            self.by.addItem(v, k)
        g.addRow("Defined by", self.by)
        self.pitch = spin(5.0, 0.001, DIM_LIMIT)
        self.revs = spin(5.0, 0.01, 1e5, 3, "", 1.0)
        self.height = spin(25.0, 0.001, DIM_LIMIT)
        g.addRow("Pitch", self.pitch)
        g.addRow("Revolutions", self.revs)
        g.addRow("Height", self.height)
        self.rev = QCheckBox("Reverse direction")
        self.left = QCheckBox("Counterclockwise (left-hand)")
        self.taper = spin(0.0, -89, 89, 3, "°")
        self.start_a = spin(0.0, -360, 360, 3, "°")
        g.addRow(self.rev)
        g.addRow(self.left)
        g.addRow("Taper angle", self.taper)
        g.addRow("Start angle", self.start_a)
        self.watch(self.sketch.currentIndexChanged, self.cx.valueChanged, self.cy.valueChanged,
                   self.cz.valueChanged, self.axis.currentIndexChanged, self.dia.valueChanged,
                   self.by.currentIndexChanged, self.pitch.valueChanged, self.revs.valueChanged,
                   self.height.valueChanged, self.rev.toggled, self.left.toggled,
                   self.taper.valueChanged, self.start_a.valueChanged)

    def changed(self, *a):
        if hasattr(self, "by"):
            explicit = self.sketch.currentData() is None
            for w_ in (self.cx, self.cy, self.cz, self.axis, self.dia):
                w_.setEnabled(explicit)
            by = self.by.currentData()
            self.pitch.setEnabled(by != "height_revolutions")
            self.revs.setEnabled(by != "height_pitch")
            self.height.setEnabled(by != "pitch_revolutions")
        super().changed(*a)

    def load(self, f):
        self.select_data(self.sketch, f.sketch)
        if f.center:
            for sp, v in zip((self.cx, self.cy, self.cz), f.center):
                sp.setValue(v)
        if f.axis:
            self.select_data(self.axis, tuple(int(round(x)) for x in f.axis))
        if f.diameter:
            self.dia.setValue(f.diameter)
        self.select_data(self.by, f.defined_by)
        self.pitch.setValue(f.pitch)
        self.revs.setValue(f.revolutions)
        if f.height:
            self.height.setValue(f.height)
        self.rev.setChecked(f.reverse)
        self.left.setChecked(f.left_hand)
        self.taper.setValue(f.taper)
        self.start_a.setValue(f.start_angle)

    def make(self):
        sk = self.sketch.currentData()
        return HelixFeature(sk, None if sk else [self.cx.value(), self.cy.value(), self.cz.value()],
                            None if sk else list(self.axis.currentData()),
                            None if sk else self.dia.value(), self.by.currentData(),
                            self.pitch.value(), self.revs.value(), self.height.value(),
                            self.left.isChecked(), self.taper.value(), self.start_a.value(),
                            self.rev.isChecked())


class CurveCommand(_RefCommand):
    TITLE = "Curve Through XYZ Points"
    ICON = "curve3d"

    def build(self):
        self.lay.addWidget(hint("One point per line: x, y, z (mm). Click model vertices to "
                                "append them."))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("0, 0, 0\n50, 20, 0\n100, 0, 30")
        self.lay.addWidget(self.text)
        self.closed = QCheckBox("Closed curve")
        self.poly = QCheckBox("Straight segments (polyline)")
        self.lay.addWidget(self.closed)
        self.lay.addWidget(self.poly)
        self.vbox = self.pick_box("Pick vertices", {"vertex"})
        self.vbox.changed.connect(self._append_vertex)
        self.watch(self.text.textChanged, self.closed.toggled, self.poly.toggled)

    def _append_vertex(self):
        if self.vbox.items:
            p = self.vbox.items[-1]["ref"]
            self.text.appendPlainText(f"{p[0]:g}, {p[1]:g}, {p[2]:g}")
            self.vbox.items = []
            self.vbox.list.clear()

    def points(self):
        out = []
        for line in self.text.toPlainText().splitlines():
            parts = [x for x in line.replace(";", ",").replace(" ", ",").split(",") if x.strip()]
            if len(parts) == 3:
                try:
                    out.append([float(x) for x in parts])
                except ValueError:
                    continue
        return out

    def load(self, f):
        self.text.setPlainText("\n".join(f"{p[0]:g}, {p[1]:g}, {p[2]:g}" for p in f.points))
        self.closed.setChecked(f.closed)
        self.poly.setChecked(f.polyline)

    def make(self):
        return CurveFeature(self.points(), self.closed.isChecked(), self.poly.isChecked())


COMMAND_FOR_TYPE = {
    "fillet": FilletCommand, "chamfer": ChamferCommand, "shell": ShellCommand,
    "draft": DraftCommand, "hole": HoleCommand, "linear_pattern": LinearPatternCommand,
    "circular_pattern": CircularPatternCommand, "mirror": MirrorCommand, "sweep": SweepCommand,
    "loft": LoftCommand, "boundary": BoundaryCommand, "rib": RibCommand,
    "ref_plane": RefPlaneCommand, "ref_axis": RefAxisCommand, "ref_point": RefPointCommand,
    "helix": HelixCommand, "curve": CurveCommand, "wrap": WrapCommand,
    "intersect": IntersectCommand,
}
