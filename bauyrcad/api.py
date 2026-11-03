"""Scripting API: build, analyse, render and export BauyrCAD parts from Python.

This is the interface Claude (or any script) uses to work with BauyrCAD without the GUI.
A script produces an ordinary .bcad file that opens - fully editable - in the application.

    from bauyrcad.api import Part

    part = Part("bracket")
    sk = part.sketch("front")                      # front / top / right, or on=<face>
    sk.rect((0, 0), (120, 40))
    sk.circle((100, 20), d=12)
    part.extrude(sk, 10)

    top = part.face("+z")                          # planar face pointing +Z (outermost)
    pocket = part.sketch(on=top)
    pocket.rect((25, 10), (70, 30))
    part.cut(pocket, 4)

    st = part.study(material="Aluminium 6061-T6")
    st.fix(part.face("-x"))
    st.force((120, 40, 10), (0, -200, 0))          # point, force vector [N]
    print(st.run())                                # summary dict

    part.render("bracket.png")                     # model picture
    st.render("bracket_vm.png")                    # von Mises plot
    part.save("bracket.bcad")
    part.export("bracket.step")

Units: mm, N, MPa. Sketch coordinates (u, v) are the plane's local axes; for the standard
planes and for faces pointing along an axis they coincide with the global axes (see sketch_on).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .core import geometry
from .core.document import Document
from .core.features import ExtrudeFeature, SketchFeature
from .core.plane import FRONT, REFERENCE_PLANES, RIGHT, TOP, SketchPlane
from .core.sketch import Constraint, builders, constraints, ops, solver
from .fea.study import (MATERIAL_LIBRARY, FaceForce, FaceRef, FixedFace, MeshControl,
                        PointForce, Pressure)

_PLANES = {"front": FRONT, "xy": FRONT, "top": TOP, "xz": TOP, "right": RIGHT, "yz": RIGHT}
_AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0),
         "+z": (0, 0, 1), "-z": (0, 0, -1), "x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


class ApiError(Exception):
    pass


def face_plane(face) -> SketchPlane:
    """Sketch plane on a planar face, origin = projection of the global origin.

    Local axes for axis-aligned faces (so scripts can use global numbers):
        +Z face: u = x,  v = y        -Z face: u = x,  v = -y
        +Y face: u = x,  v = -z       -Y face: u = x,  v = z
        +X face: u = -z, v = y        -X face: u = z,  v = y
    Other faces use SketchPlane.from_face. Sketch.to_world / Sketch.local convert.
    """
    from .core.refs import RefError, plane_on_face
    try:
        return plane_on_face(face)
    except RefError:
        raise ApiError("Sketches need a planar face") from None


def faces_where(faces, kind=None, d=None, r=None, normal=None, tol=1e-3):
    out = []
    for f in faces:
        if kind is not None and f.kind != kind:
            continue
        if d is not None and (f.radius is None or abs(2 * f.radius - d) > tol * max(d, 1)):
            continue
        if r is not None and (f.radius is None or abs(f.radius - r) > tol * max(r, 1)):
            continue
        if normal is not None:
            n = np.asarray(_AXES.get(normal, normal) if isinstance(normal, str) else normal, float)
            if not f.is_planar or np.dot(f.normal, n / np.linalg.norm(n)) < 1 - 1e-6:
                continue
        out.append(f)
    return out


def _face_distance(shape, f, p):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.TopAbs import TopAbs_FACE
    from OCP.gp import gp_Pnt
    face = geometry.indexed_map(shape, TopAbs_FACE)[f.index]
    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, p))).Vertex()
    dist = BRepExtrema_DistShapeShape(face, v)
    return dist.Value() if dist.IsDone() else float(np.linalg.norm(f.centroid - p))


def select_face(faces, shape, normal=None, at=None, where="max", index=None, kind=None, d=None):
    """Shared face picker for parts and assembly components (coordinates of that shape)."""
    if index is not None:
        return faces[index]
    cands = faces
    if kind is not None or d is not None:
        cands = faces_where(faces, kind=kind, d=d)
        if not cands:
            raise ApiError(f"No face with kind={kind} d={d}")
        if normal is None and at is None:
            if len(cands) > 1:
                raise ApiError(f"{len(cands)} faces match kind={kind} d={d}; add at=(x, y, z)")
            return cands[0]
    if normal is not None:
        n = np.asarray(_AXES.get(normal, normal) if isinstance(normal, str) else normal, float)
        n = n / np.linalg.norm(n)
        cands = [f for f in cands if f.is_planar and np.dot(f.normal, n) > 1 - 1e-6]
        if not cands:
            raise ApiError(f"No planar face with normal {normal}")
        if at is None:
            key = (lambda f: np.dot(f.centroid, n))
            return max(cands, key=key) if where == "max" else min(cands, key=key)
    if at is not None:
        p = np.asarray(at, float)
        return min(cands, key=lambda f: _face_distance(shape, f, p))
    raise ApiError("Give normal=, at= or index=")


class Sketch:
    """Wrapper around a sketch feature with convenient drawing calls.

    All drawing calls return the created entities; dimensions/relations are optional
    (geometry given with numbers is exact already) but make the sketch editable by intent.
    """

    def __init__(self, part: "Part", feature: SketchFeature):
        self.part, self.feature = part, feature
        self.sk = feature.sketch

    @property
    def plane(self) -> SketchPlane:
        return self.feature.plane

    @property
    def name(self) -> str:
        return self.feature.name

    def to_world(self, u, v) -> np.ndarray:
        return self.plane.to_world(u, v)

    def local(self, xyz) -> tuple:
        """Global point -> this sketch's (u, v)."""
        return self.plane.to_local(xyz)

    # ---- drawing --------------------------------------------------------------------
    def line(self, a, b, construction=False):
        return builders.line(self.sk, a, b, construction)[0]

    def centerline(self, a, b):
        return builders.centerline(self.sk, a, b)[0]

    def polyline(self, pts, close=False):
        """Connected lines through pts (shared endpoints). close=True closes the loop."""
        ids = [self.sk.pid(p) for p in pts]
        if close:
            ids.append(ids[0])
        return [builders.line(self.sk, a, b)[0] for a, b in zip(ids, ids[1:])]

    def rect(self, p1, p2):
        return builders.corner_rectangle(self.sk, p1, p2)

    def center_rect(self, center, width, height):
        c = np.asarray(center, float)
        return builders.center_rectangle(self.sk, tuple(c), tuple(c + [width / 2, height / 2]))

    def circle(self, center, r=None, d=None):
        if (r is None) == (d is None):
            raise ApiError("Give r= or d=")
        return self.sk.add_circle(tuple(center), r if r is not None else d / 2)

    def arc(self, center, start, end, ccw=True):
        return builders.center_arc(self.sk, tuple(center), tuple(start), tuple(end), ccw)[0]

    def arc3(self, start, through, end):
        return builders.three_point_arc(self.sk, tuple(start), tuple(end), tuple(through))[0]

    def slot(self, a, b, width):
        """Straight slot between arc centres a and b, total width `width`."""
        A, B = np.asarray(a, float), np.asarray(b, float)
        d = (B - A) / np.linalg.norm(B - A)
        rim = A + np.array([-d[1], d[0]]) * width / 2
        return builders.straight_slot(self.sk, tuple(A), tuple(B), tuple(rim))

    def polygon(self, center, radius, sides=6, angle=0.0, inscribed=True):
        c = np.asarray(center, float)
        v = c + radius * np.array([math.cos(math.radians(angle)), math.sin(math.radians(angle))])
        return builders.polygon(self.sk, tuple(c), tuple(v), sides, inscribed)

    def spline(self, pts):
        return builders.spline(self.sk, [tuple(p) for p in pts])[0]

    def point(self, p):
        return builders.point(self.sk, tuple(p))[0]

    def text(self, text, at=(0, 0), height=5.0, font="Arial", bold=False, italic=False, angle=0.0):
        """Sketch text: `at` = left end of the baseline, `height` = capital letter height (mm).
        The letters are profiles: part.cut(sk, 0.5) engraves, part.extrude(sk, 1) embosses."""
        t = self.sk.add_text(text, at, height, font, bold, italic, angle)
        self.part._dirty = True
        return t

    # ---- modify ------------------------------------------------------------------------
    def _corner(self, at):
        at = np.asarray(at, float)
        used = {p for e in self.sk.entities.values() for p in e.point_ids()}
        pid = min(used, key=lambda p: np.linalg.norm(self.sk.pt(p) - at))
        if np.linalg.norm(self.sk.pt(pid) - at) > 1e-6 * max(self.sk.scale(), 1):
            raise ApiError(f"No sketch point at {tuple(at)}")
        return pid

    def fillet(self, at, r):
        """Round the corner at point `at` (sketch coordinates)."""
        arc = ops.fillet(self.sk, self._corner(at), r)
        self.solve()
        return arc

    def chamfer(self, at, d):
        ln = ops.chamfer(self.sk, self._corner(at), d)
        self.solve()
        return ln

    def fillet_all(self, r):
        """Fillet every corner where exactly two non-construction lines meet."""
        done = 0
        while True:
            corners = [p for p in {q for e in self.sk.entities.values() for q in e.point_ids()}
                       if sum(1 for e in self.sk.entities_using_point(p)
                              if e.kind == "line" and not e.construction) == 2
                       and all(e.kind == "line" for e in self.sk.entities_using_point(p))]
            if not corners:
                return done
            ops.fillet(self.sk, corners[0], r, add_dimension=done == 0)
            done += 1

    def trim(self, entity, at):
        ops.trim(self.sk, entity.id, at)

    def offset(self, entities, distance):
        return ops.offset(self.sk, [e.id for e in entities], distance)

    def mirror(self, entities, axis):
        return ops.mirror(self.sk, [e.id for e in entities], axis.id)

    def convert(self, edges=None, face=None, fix=True):
        """Convert Entities: project model edges into this sketch - `face=` its outline (all
        its edges), or `edges` (edges_where() items, points near edges, or indices). Lines,
        arcs and circles stay exact; the new curves are fixed unless fix=False."""
        from .core import modeling
        part = self.part
        part._ensure_built()
        body = part.doc.body
        if body is None:
            raise ApiError("There is no model to convert edges from")
        idx = []
        if face is not None:
            idx += modeling.face_edge_indices(body, [face.index])
        if edges is not None:
            infos = modeling.edge_infos(body)
            for r in part._edge_refs(edges):
                i = r.resolve(infos)
                if i is not None:
                    idx.append(i)
        infos = [e for e in modeling.convertible_edges(body, sorted(set(idx))) if not e.get("seam")]
        made = ops.convert(self.sk, infos, self.plane.to_local, self.plane.normal, fix)
        part._dirty = True
        return made

    def pattern_circular(self, entities, center, count, total_deg=360.0):
        return ops.circular_pattern(self.sk, [e.id for e in entities], center, count, total_deg)

    def pattern_linear(self, entities, dx_deg, spacing, count, dy_deg=None, spacing2=0.0, count2=1):
        return ops.linear_pattern(self.sk, [e.id for e in entities], dx_deg, spacing, count,
                                  dy_deg, spacing2, count2)

    # ---- intent (optional) ------------------------------------------------------------------
    def relate(self, rtype, *items):
        """Add a relation: items are entities or ("p", id) refs."""
        refs = [i if isinstance(i, tuple) else ("e", i.id) for i in items]
        try:
            made = constraints.make_relation(self.sk, rtype, refs)
        except ValueError as e:
            raise ApiError(f"{rtype}: {e}") from None
        for c in made:
            ok, msg = solver.try_add(self.sk, c)
            if not ok:
                raise ApiError(f"{rtype}: {msg}")

    def dim(self, entity, value=None, type=None):
        """Dimension a line (length), circle (diameter) or arc (radius); returns the constraint."""
        kind = type or {"line": "distance", "circle": "diameter", "arc": "radius"}[entity.kind]
        c = Constraint(0, kind, [("e", entity.id)])
        c.value = constraints.measure(self.sk, c)
        ok, msg = solver.try_add(self.sk, c)
        if not ok:
            raise ApiError(msg)
        if value is not None:
            ok, msg = solver.set_dimension(self.sk, c, value)
            if not ok:
                raise ApiError(msg)
        return c

    def solve(self):
        if not solver.solve(self.sk).ok:
            raise ApiError("Sketch could not be solved")

    def status(self) -> dict:
        a = solver.analyze(self.sk)
        try:
            regions = self.sk.regions()
            profile = f"{len(regions)} profile(s), {sum(len(r.holes) for r in regions)} hole(s)"
        except Exception as e:  # noqa: BLE001
            profile = str(e)
        return {"dof": a.dof, "fully_defined": a.fully_defined, "profiles": profile,
                "contours": [{"area": round(r.area, 4), "point": [round(float(x), 4) for x in r.point]}
                             for r in self.feature.regions()]}


class Study:
    def __init__(self, part: "Part", study):
        self.part, self.study = part, study
        self.results = None

    def material(self, name=None, **props):
        """Library material by name, or custom: material(E=..., nu=..., yield_strength=..., density=...)."""
        from .fea.study import Material
        if name is not None:
            if name not in MATERIAL_LIBRARY:
                raise ApiError(f"Unknown material {name!r}; library: {', '.join(MATERIAL_LIBRARY)}")
            self.study.material = MATERIAL_LIBRARY[name]
        else:
            self.study.material = Material(props.pop("name", "Custom"), **props)
        return self

    def fix(self, *faces):
        refs = [FaceRef.from_face(f) for f in faces]
        n = len(self.study.fixtures) + 1
        self.study.fixtures.append(FixedFace(self.part.doc.new_bc_id(), f"Fixed-{n}", refs))
        return self

    def force(self, point, vector):
        """Point force [N] at any point; it goes to the nearest surface corner node.
        Singular under the load - prefer face_force for design stresses."""
        n = len(self.study.loads) + 1
        self.study.loads.append(PointForce(self.part.doc.new_bc_id(), f"Force-{n}",
                                           list(map(float, point)), list(map(float, vector))))
        return self

    def face_force(self, faces, vector):
        """Total force vector [N] spread uniformly over one face or a list of faces."""
        faces = faces if isinstance(faces, (list, tuple)) else [faces]
        n = len(self.study.loads) + 1
        self.study.loads.append(FaceForce(self.part.doc.new_bc_id(), f"Face Force-{n}",
                                          [FaceRef.from_face(f) for f in faces],
                                          list(map(float, vector))))
        return self

    def pressure(self, faces, p):
        """Uniform pressure [MPa] normal to the faces; positive pushes into the material."""
        faces = faces if isinstance(faces, (list, tuple)) else [faces]
        n = len(self.study.loads) + 1
        self.study.loads.append(Pressure(self.part.doc.new_bc_id(), f"Pressure-{n}",
                                         [FaceRef.from_face(f) for f in faces], float(p)))
        return self

    def mesh(self, size=0.0, order=2):
        """Global element size (0 = automatic) and order (2 = tet10, 1 = tet4)."""
        self.study.mesh_size, self.study.element_order = float(size), int(order)
        return self

    def mesh_control(self, faces, size):
        """Local element size on faces (e.g. fillets, hole walls), blended into the global size."""
        faces = faces if isinstance(faces, (list, tuple)) else [faces]
        self.study.mesh_controls.append(MeshControl([FaceRef.from_face(f) for f in faces], float(size)))
        return self

    def run(self, verbose=False, progress=None) -> dict:
        """Mesh + solve. verbose=True prints timed progress; progress=callable(msg) also works.

        Summary keys: max_von_mises / min_fos (global, incl. singular peaks),
        max_von_mises_away_from_point_loads / min_fos_away_from_point_loads (if point loads),
        max_displacement, reaction, applied, nodes, elements, dof, element_size, method,
        warnings, material, solve_time.
        """
        import time
        from .fea.analysis import run_study
        self.part._ensure_built()
        t0 = time.perf_counter()
        cb = progress
        if verbose and cb is None:
            def cb(msg):
                print(f"[{time.perf_counter() - t0:7.1f} s] {msg}", flush=True)
        self.results = run_study(self.study, self.part.doc, progress=cb)
        self.study.results = self.results
        s = self.results.summary()
        s["warnings"] = self.results.warnings
        s["material"] = self.study.material.name
        if verbose:
            print(f"[{time.perf_counter() - t0:7.1f} s] done ({s['method']})", flush=True)
        return s

    def export_bdf(self, path, progress=None):
        """Mesh and export this setup as MSC Nastran SOL 101 (mm, N, MPa).

        Returns counts, applied force/moment and warnings. Does not run a solver.
        Fixed faces, point/face forces and pressure are supported; no contact.
        """
        from .fea.nastran import export_bdf
        self.part._ensure_built()
        return export_bdf(self.study, self.part.doc, path, progress)

    # ---- post-processing ------------------------------------------------------------------
    def _res(self):
        if self.results is None:
            raise ApiError("Run the study first")
        return self.results

    @property
    def nodes(self):
        """(N, 3) mesh node coordinates, matching field() arrays."""
        return self._res().mesh.nodes

    def probe(self, point, field="von Mises Stress"):
        """(value, node_xyz, distance) at the node nearest to point."""
        return self._res().probe(point, field)

    def max_in(self, field="von Mises Stress", box=None, center=None, radius=None,
               face=None, exclude_point_loads=True):
        """(max value, xyz) inside a box ((x0,y0,z0),(x1,y1,z1)), a sphere (center, radius)
        or on a face, excluding the singular zone around point loads by default."""
        nodes = self._res().mesh.face_nodes.get(face.index) if face is not None else None
        return self._res().max_in(field, box=box, center=center, radius=radius, nodes=nodes,
                                  exclude_point_loads=exclude_point_loads)

    def mean_on(self, face, field="Displacement Magnitude") -> float:
        """Average of a nodal field over a face (e.g. the loaded face's deflection)."""
        return self._res().mean_on_face(face.index, field)

    def field(self, name="von Mises Stress"):
        if self.results is None:
            raise ApiError("Run the study first")
        return self.results.field(name)

    def render(self, path, field="von Mises Stress", view="iso", deform=None, clip="auto",
               size=(1400, 1000)):
        """Contour plot. clip: legend max percentile ("auto" = 99 for stresses with point
        loads, none otherwise), a number, or None."""
        from .render import render_results
        if self.results is None:
            self.run()
        if clip == "auto":
            clip = 99.0 if ("Stress" in field and self.results.load_nodes) else None
        render_results(self.part.doc, self.results, path, field=field, view=view, scale=deform,
                       clip_percentile=clip, size=size)
        return path

    def export_vtu(self, path):
        self.results.vtk_grid().save(str(path))
        return path


class Part:
    """A BauyrCAD part document with a fluent modelling API."""

    def __init__(self, name: str = "Part1", doc: Document | None = None):
        self.doc = doc or Document()
        self.name = name
        self._dirty = True

    def properties(self, *, part_number=None, description=None):
        """Set persisted BOM metadata. Empty part number falls back to the file stem."""
        if part_number is not None:
            self.doc.part_number = str(part_number)
        if description is not None:
            self.doc.description = str(description)
        return self

    @classmethod
    def open(cls, path) -> "Part":
        doc = Document.load(path)
        return cls(Path(path).stem, doc)

    # ---- modelling ------------------------------------------------------------------------
    def sketch(self, plane="front", on=None, offset=None) -> Sketch:
        """New sketch on "front"/"top"/"right" (or xy/xz/yz), a reference plane from
        part.plane(), a SketchPlane, or on=<planar face>. offset=d puts it on a parallel
        plane at distance d (along the normal) - a reference plane is created for it.
        Sketches on faces and reference planes move with them when the model changes."""
        if offset is not None:
            plane = self.plane(on if on is not None else plane, offset=offset)
            on = None
        spec = None
        if on is not None:
            pl = face_plane(on)
            spec = {"type": "face", "ref": FaceRef.from_face(on).to_dict()}
        elif isinstance(plane, SketchPlane):
            pl = plane
        elif getattr(plane, "TYPE", None) == "ref_plane":
            self._ensure_built()
            if plane.result_plane is None:
                raise ApiError(f"{plane.name}: {plane.error}")
            pl, spec = plane.result_plane, {"type": "feature", "id": plane.id}
        else:
            pl = _PLANES.get(str(plane).lower()) or REFERENCE_PLANES.get(plane)
            if pl is None:
                raise ApiError(f"Unknown plane {plane!r}")
            spec = {"type": "std", "name": pl.name}      # it references that plane, as in the GUI
        f = self.doc.add_feature(SketchFeature(pl, plane_spec=spec))
        self._dirty = True
        return Sketch(self, f)

    def extrude(self, sketch: Sketch, depth=10.0, midplane=False, reverse=False, contours=None):
        """Extruded boss. contours: list of (u, v) points inside the closed areas to use."""
        f = ExtrudeFeature(sketch.feature.id, depth, "boss", "midplane" if midplane else "blind",
                           reverse, contours)
        return self._add(f)

    def cut(self, sketch: Sketch, depth=None, through=False, both=False, reverse=False, contours=None):
        """Extruded cut: blind `depth`, or through=True (both=True for both directions)."""
        if depth is None and not through:
            through = True
        end = ("through_all_both" if both else "through_all") if through else "blind"
        f = ExtrudeFeature(sketch.feature.id, depth or 0.0, "cut", end, reverse, contours)
        return self._add(f)

    def revolve(self, sketch: Sketch, angle=360.0, axis=None, midplane=False, reverse=False,
                contours=None):
        """Revolved boss about a sketch line, straight edge or reference axis (default: centerline)."""
        from .core.features import RevolveFeature
        f = RevolveFeature(sketch.feature.id, angle, "boss",
                           end="midplane" if midplane else "blind", reverse=reverse, regions=contours,
                           **self._revolve_axis_reference(axis))
        return self._add(f)

    def revolve_cut(self, sketch: Sketch, angle=360.0, axis=None, midplane=False, reverse=False,
                    contours=None):
        """Revolved cut about a sketch line, straight edge or reference axis (default: centerline)."""
        from .core.features import RevolveFeature
        f = RevolveFeature(sketch.feature.id, angle, "cut",
                           end="midplane" if midplane else "blind", reverse=reverse, regions=contours,
                           **self._revolve_axis_reference(axis))
        return self._add(f)

    def _revolve_axis_reference(self, axis):
        if axis is None:
            return {}
        from .core.sketch import Line
        if isinstance(axis, Line):
            for sk in self.doc.sketches():
                if sk.sketch.entities.get(axis.id) is axis:
                    return {"axis_sketch_id": sk.id, "axis_id": axis.id}
            raise ApiError("The revolve axis line must belong to this part")
        if getattr(axis, "TYPE", None) == "ref_axis" and not any(f is axis for f in self.doc.features):
            raise ApiError("The revolve reference axis must belong to this part")
        return {"axis_spec": self._axis_spec(axis)}

    # ---- applied features (fillet, chamfer, shell, draft, hole) -----------------------------
    def fillet(self, edges=(), r=2.0, faces=(), r2=None):
        """Fillet edges (points near them, edge indices or edges_where() items) and / or every
        edge of `faces`. Tangent edge chains are followed. r2: linear radius r -> r2."""
        from .core.features_more import FilletFeature
        return self._add(FilletFeature(r, self._edge_refs(edges), self._face_refs(faces), r2))

    def chamfer(self, edges=(), d=1.0, d2=None, angle=None, faces=(), flip=False):
        """Chamfer edges: equal distance d; d2 = second distance; angle = distance-angle
        (degrees). flip swaps the side the first distance is measured on."""
        from .core.features_more import ChamferFeature
        kind = "angle_distance" if angle is not None else (
            "distance_distance" if d2 is not None else "distance")
        return self._add(ChamferFeature(d, self._edge_refs(edges), self._face_refs(faces), kind,
                                        d2, 45.0 if angle is None else angle, flip))

    def shell(self, thickness, remove=(), outward=False):
        """Hollow the part: walls of `thickness`, `remove` = faces left open (none = closed
        hollow body). outward=True adds the walls outside."""
        from .core.features_more import ShellFeature
        return self._add(ShellFeature(thickness, self._face_refs(remove), outward))

    def draft(self, faces, angle, neutral="top", reverse=False):
        """Draft (taper) faces by `angle` degrees about the neutral plane / face; the faces lean
        inwards along the neutral plane's normal (reverse=True: the other way)."""
        from .core.features_more import DraftFeature
        return self._add(DraftFeature(angle, self._face_refs(faces), self._plane_spec(neutral),
                                      reverse))

    def hole(self, on, points, d=None, size=None, kind=None, depth=None, through=False,
             fit="normal", cbore=None, csk=None, thread_depth=None, drill_point=True,
             reverse=False):
        """Hole Wizard. on: planar face or plane; points: (u, v) in that face's sketch
        coordinates (global coordinates for faces along an axis, see face_plane).
        kind: "simple" (default; with size= a clearance hole, fit close/normal/loose),
        "tapped" (size="M6": tap drill + thread callout), "counterbore" / "countersink"
        (standard sizes for socket / flat head screws; override with cbore=(D, depth) or
        csk=D or (D, angle)). depth= blind depth, or through=True."""
        from .core.features_more import HoleFeature
        kind = kind or "simple"
        if depth is None and not through:
            through = True
        pts = [tuple(p) for p in (points if np.ndim(points) == 2 else [points])]
        cb_d, cb_h = (cbore if cbore is not None else (None, None))
        csk_d, csk_a = (csk, 90.0) if not isinstance(csk, (tuple, list)) else csk
        f = HoleFeature(self._plane_spec(on), pts, kind, size, d, depth or 0.0,
                        "through_all" if through else "blind", fit, cb_d, cb_h, csk_d, csk_a,
                        thread_depth, drill_point, reverse)
        return self._add(f)

    # ---- patterns and mirror ---------------------------------------------------------------------
    def pattern_linear(self, features=None, direction="x", spacing=10.0, count=2,
                       direction2=None, spacing2=10.0, count2=1, reverse=False, reverse2=False,
                       skip=(), bodies=False):
        """Linear pattern of features (extrudes, cuts, revolves, holes, sweeps, lofts, ribs,
        patterns) or of the whole body (features=None / bodies=True). Directions: "x"/"y"/"z",
        a vector, a straight edge (point near it / edges_where item) or a planar face (its
        normal). skip: [(i, j), ...] instances to leave out."""
        from .core.features_more import LinearPatternFeature
        d2 = self._axis_spec(direction2) if direction2 is not None else None
        return self._add(LinearPatternFeature(self._ids(features), self._axis_spec(direction),
                                              spacing, count, d2, spacing2, count2, reverse,
                                              reverse2, list(skip), bodies or not features))

    def pattern_circular(self, features=None, axis="z", count=4, angle=360.0,
                         equal_spacing=True, reverse=False, skip=(), bodies=False):
        """Circular pattern about an axis: "x"/"y"/"z" (through the origin), a cylindrical face
        (its axis), a circular or straight edge, a reference axis, or (point, direction).
        angle: total angle with equal_spacing (360 = full circle), else the step angle."""
        from .core.features_more import CircularPatternFeature
        return self._add(CircularPatternFeature(self._ids(features), self._axis_spec(axis),
                                                count, angle, equal_spacing, reverse, list(skip),
                                                bodies or not features))

    def mirror(self, features=None, plane="right"):
        """Mirror features (or the whole body with features=None) about a plane or face."""
        from .core.features_more import MirrorFeature
        return self._add(MirrorFeature(self._ids(features), self._plane_spec(plane),
                                       not features))

    # ---- sweep / loft / boundary / rib --------------------------------------------------------
    def sweep(self, path, profile=None, d=None, inner_d=None, cut=False, contours=None):
        """Swept boss (cut=True: swept cut) of a profile sketch - or a circle of diameter d
        (tube: inner_d) - along a path: a sketch, a helix() or a curve()."""
        from .core.features_more import SweepFeature
        return self._add(SweepFeature(self._path_spec(path),
                                      None if profile is None else profile.feature.id,
                                      "cut" if cut else "boss", d, inner_d, contours))

    def loft(self, sections, cut=False, ruled=False, closed=False):
        """Lofted boss / cut through profile sketches in order (a point (x, y, z) at either end
        lofts to a point)."""
        from .core.features_more import LoftFeature
        return self._add(LoftFeature(self._sections(sections), "cut" if cut else "boss",
                                     ruled, closed))

    def boundary(self, sections, cut=False, closed=False):
        """Boundary boss / cut through profile sketches (curvature-smooth loft)."""
        from .core.features_more import BoundaryFeature
        return self._add(BoundaryFeature(self._sections(sections), "cut" if cut else "boss",
                                         False, closed))

    def rib(self, sketch, thickness, normal=False, side="both", flip=False):
        """Rib from an open sketch line: parallel to the sketch (gusset, default) or
        normal=True (the line extruded normal to the sketch until it meets the body)."""
        from .core.features_more import RibFeature
        return self._add(RibFeature(sketch.feature.id, thickness,
                                    "normal" if normal else "parallel", side, flip))

    def wrap(self, sketch, face, depth=1.0, deboss=False, contours=None):
        """Wrap the sketch's closed profiles (text too) onto a cylindrical face: emboss
        (raised by `depth`) or deboss=True (recessed). Sketch on a plane parallel to the
        cylinder's axis, e.g. part.plane(tangent...) or an offset of a plane through the axis;
        lengths along the circumference become arc lengths."""
        from .core.features_more import WrapFeature
        return self._add(WrapFeature(sketch.feature.id, FaceRef.from_face(face).to_dict(), depth,
                                     "deboss" if deboss else "emboss", contours))

    def intersect(self, planes, exclude=(), fill=()):
        """Intersect with planes / planar faces: split the body into regions and remove the
        material regions containing the `exclude` points, or add the closed voids containing
        the `fill` points (e.g. the inside of a cup below a capping plane)."""
        from .core.features_more import IntersectFeature
        planes = planes if isinstance(planes, (list, tuple)) else [planes]
        return self._add(IntersectFeature([self._plane_spec(p) for p in planes],
                                          [tuple(p) for p in exclude], [tuple(p) for p in fill]))

    def regions(self, planes) -> list[dict]:
        """The regions an intersect() with these planes would make: [{"volume", "point",
        "material"}] (points to pass as exclude / fill)."""
        from .core.features import RegenContext
        from .core.features_more import IntersectFeature
        self._ensure_built()
        planes = planes if isinstance(planes, (list, tuple)) else [planes]
        ctx = RegenContext()
        ctx.body = self.doc.body
        ctx.features = {f.id: f for f in self.doc.features}
        regs = IntersectFeature([self._plane_spec(p) for p in planes]).regions(ctx)
        return [{"volume": r["volume"], "point": r["point"].round(6).tolist(),
                 "material": r["material"]} for r in regs]

    # ---- reference geometry and curves ---------------------------------------------------------
    def plane(self, base="front", offset=None, angle=None, axis=None, points=None,
              normal_to=None, at=None, mid=None, flip=False):
        """Reference plane: offset from a plane / face (plane("top", offset=40)), at an
        angle about an axis in it (plane("front", angle=30, axis="y")), through three points
        (points=[a, b, c]), normal to an axis / edge (normal_to=..., at=point) or midway
        between two parallel faces / planes (mid=(face1, face2)). Use it in part.sketch()."""
        from .core.features_more import RefPlaneFeature
        if points is not None:
            f = RefPlaneFeature("points", points=points, flip=flip)
        elif normal_to is not None:
            f = RefPlaneFeature("normal", axis=self._axis_spec(normal_to), point=at, flip=flip)
        elif mid is not None:
            f = RefPlaneFeature("mid", self._plane_spec(mid[0]), base2=self._plane_spec(mid[1]),
                                flip=flip)
        elif angle is not None:
            f = RefPlaneFeature("angle", self._plane_spec(base), axis=self._axis_spec(axis or "y"),
                                angle=angle, flip=flip)
        else:
            f = RefPlaneFeature("offset", self._plane_spec(base), offset or 0.0, flip=flip)
        return self._add(f)

    def axis(self, through=None, face=None, edge=None, planes=None, point=None, direction=None):
        """Reference axis: through two points, a cylinder face's axis, a straight edge, two
        planes' intersection, or a point + direction."""
        from .core.features_more import RefAxisFeature
        if through is not None:
            f = RefAxisFeature("points", points=through)
        elif face is not None:
            f = RefAxisFeature("face", ref=FaceRef.from_face(face).to_dict())
        elif edge is not None:
            f = RefAxisFeature("edge", ref=self._edge_refs([edge])[0].to_dict())
        elif planes is not None:
            f = RefAxisFeature("planes", base=self._plane_spec(planes[0]),
                               base2=self._plane_spec(planes[1]))
        else:
            f = RefAxisFeature("point_dir", point=point, dir=direction)
        return self._add(f)

    def point(self, xyz=None, arc_center=None, face_center=None):
        """Reference point: coordinates, the centre of a circular edge, or a face centroid."""
        from .core.features_more import RefPointFeature
        if arc_center is not None:
            f = RefPointFeature("arc_center", ref=self._edge_refs([arc_center])[0].to_dict())
        elif face_center is not None:
            f = RefPointFeature("face_center", ref=FaceRef.from_face(face_center).to_dict())
        else:
            f = RefPointFeature("coords", coords=xyz)
        return self._add(f)

    def helix(self, sketch=None, center=None, axis="y", d=None, pitch=None, turns=None,
              height=None, left_hand=False, taper=0.0, start_angle=0.0, reverse=False):
        """Helix / spiral (a sweep path): on a sketch with one circle, or center / axis / d.
        Give two of pitch, turns (revolutions) and height."""
        from .core.features_more import HelixFeature
        if height is not None and turns is not None:
            by, pitch, turns = "height_revolutions", pitch or 1.0, turns
        elif height is not None:
            by, turns = "height_pitch", turns or 1.0
        else:
            by = "pitch_revolutions"
        ax = None
        if sketch is None:
            ax = _AXES.get(axis, axis) if isinstance(axis, str) else axis
        f = HelixFeature(None if sketch is None else sketch.feature.id, center, ax, d, by,
                         pitch or 1.0, turns or 1.0, height, left_hand, taper, start_angle, reverse)
        return self._add(f)

    def curve(self, points, closed=False, polyline=False):
        """3D curve through XYZ points (spline; polyline=True for straight segments)."""
        from .core.features_more import CurveFeature
        return self._add(CurveFeature(points, closed, polyline))

    # ---- selection helpers --------------------------------------------------------------------
    def edges_where(self, kind=None, direction=None, face=None, r=None, d=None, length=None,
                    near=None, tol=1e-3, seams=False):
        """Model edges matching: kind ("line", "circle", "arc", "curve"), direction (lines along
        "x" / vector), on `face`, radius r / diameter d, length. near=(x, y, z) sorts them by
        distance. Seam edges (the closing line of a cylinder) are left out unless seams=True.
        Returns edge dicts usable in fillet / chamfer / patterns."""
        from .core import modeling
        self._ensure_built()
        es = [e for e in modeling.edge_infos(self.doc.body) if not e.get("degenerate")
              and (seams or not e.get("seam"))]
        if face is not None:
            on = set(modeling.face_edge_indices(self.doc.body, [face.index]))
            es = [e for e in es if e["index"] in on]
        if kind is not None:
            es = [e for e in es if e["kind"] == kind]
        if direction is not None:
            v = np.asarray(_AXES.get(direction, direction) if isinstance(direction, str) else direction, float)
            v = v / np.linalg.norm(v)
            es = [e for e in es if e["kind"] == "line" and abs(e["direction"] @ v) > 1 - 1e-6]
        if r is not None or d is not None:
            rr = r if r is not None else d / 2
            es = [e for e in es if e.get("radius") is not None and abs(e["radius"] - rr) <= tol * max(rr, 1)]
        if length is not None:
            es = [e for e in es if abs(e["length"] - length) <= tol * max(length, 1)]
        if near is not None:
            q = np.asarray(near, float)
            es.sort(key=lambda e: float(np.linalg.norm(e["mid"] - q)))
        return es

    def reference_status(self, feature):
        """Diagnose stored face/edge references against the model before a feature.

        Returns path, kind, status, index and candidate scores. Does not repair or
        mutate the document. Candidate indices belong to that historical body.
        """
        from .core.document import Document
        from .core.refs import edge_ref, face_ref
        from .core import geometry, modeling
        trial = Document.from_dict(self.doc.to_dict())
        target = trial.feature(feature.id if hasattr(feature, "id") else feature)
        if target is None:
            raise ApiError("Feature not found")
        ctx = trial.context_before(target)
        faces = geometry.face_infos(ctx.body) if ctx.body is not None else []
        edges = modeling.edge_infos(ctx.body) if ctx.body is not None else []
        result = []
        def visit(value, path):
            if isinstance(value, dict):
                if {"centroid", "area", "index"} <= value.keys():
                    result.append({"path": path, "kind": "face", **face_ref(value).match(faces)})
                elif {"mid", "length", "kind"} <= value.keys():
                    result.append({"path": path, "kind": "edge", **edge_ref(value).match(edges)})
                else:
                    for key, child in value.items():
                        visit(child, path + [key])
            elif isinstance(value, (list, tuple)):
                for i, child in enumerate(value):
                    visit(child, path + [i])
        visit(target.params(), [])
        return result

    def _edge_refs(self, edges):
        from .core import modeling
        from .core.refs import EdgeRef
        if not edges:
            return []
        self._ensure_built()
        infos = modeling.edge_infos(self.doc.body)
        if isinstance(edges, (dict, EdgeRef, int, np.integer)):
            items = [edges]
        elif (isinstance(edges, (tuple, np.ndarray)) and np.ndim(edges) == 1 and len(edges) == 3
              and all(isinstance(x, (int, float, np.number)) for x in edges)):
            items = [edges]                    # one point (x, y, z); a list of ints = indices
        else:
            items = edges
        out = []
        for it in items:
            if isinstance(it, EdgeRef):
                out.append(it)
            elif isinstance(it, dict):
                out.append(EdgeRef.from_info(it))
            elif isinstance(it, (int, np.integer)):
                out.append(EdgeRef.from_info(infos[int(it)]))
            else:
                out.append(EdgeRef.from_info(infos[self.edge(it)]))
        return out

    @staticmethod
    def _face_refs(faces):
        if faces is None:
            return []
        if hasattr(faces, "area"):
            faces = [faces]
        return [FaceRef.from_face(f) for f in faces]

    def _plane_spec(self, plane) -> dict:
        from .core.plane import SketchPlane as _SP
        if hasattr(plane, "area") and hasattr(plane, "is_planar"):
            return {"type": "face", "ref": FaceRef.from_face(plane).to_dict()}
        if getattr(plane, "TYPE", None) == "ref_plane":
            return {"type": "feature", "id": plane.id}
        if isinstance(plane, Sketch):
            # the sketch's own plane: the standard / reference plane it sits on (so the new
            # feature references it as a SolidWorks user would), else its plane as numbers
            spec = plane.feature.plane_spec or {}
            if spec.get("type") in ("std", "feature"):
                return dict(spec)
            pl = plane.feature.plane
            for name, ref in REFERENCE_PLANES.items():
                if (np.allclose(pl.origin, ref.origin, atol=1e-12) and np.allclose(pl.normal, ref.normal, atol=1e-12)
                        and np.allclose(pl.xdir, ref.xdir, atol=1e-12)):
                    return {"type": "std", "name": name}
            return {"type": "explicit", "plane": plane.plane.to_dict()}
        if isinstance(plane, _SP):
            return {"type": "explicit", "plane": plane.to_dict()}
        pl = _PLANES.get(str(plane).lower()) or REFERENCE_PLANES.get(plane)
        if pl is None:
            raise ApiError(f"Unknown plane {plane!r}")
        return {"type": "std", "name": pl.name}

    def _axis_spec(self, axis) -> dict:
        from .core.refs import EdgeRef
        if isinstance(axis, str):
            if axis.lower().lstrip("+-") not in ("x", "y", "z"):
                raise ApiError(f"Unknown axis {axis!r}")
            if axis.startswith("-"):
                return {"type": "explicit", "point": [0, 0, 0],
                        "dir": [-c for c in _AXES[axis.lstrip("-")]]}
            return {"type": "std", "name": axis.lower().lstrip("+")}
        if hasattr(axis, "area") and hasattr(axis, "kind"):
            return {"type": "face", "ref": FaceRef.from_face(axis).to_dict()}
        if getattr(axis, "TYPE", None) == "ref_axis":
            return {"type": "feature", "id": axis.id}
        if isinstance(axis, (dict, EdgeRef, int, np.integer)):
            return {"type": "edge", "ref": self._edge_refs([axis])[0].to_dict()}
        if isinstance(axis, (tuple, list)) and len(axis) == 2 and np.ndim(axis[0]) == 1:
            return {"type": "explicit", "point": list(map(float, axis[0])),
                    "dir": list(map(float, axis[1]))}
        v = np.asarray(axis, float)
        if v.shape == (3,):
            return {"type": "explicit", "point": [0, 0, 0], "dir": v.tolist()}
        raise ApiError(f"Can't use {axis!r} as an axis / direction")

    @staticmethod
    def _ids(features):
        if not features:
            return []
        if hasattr(features, "id") and hasattr(features, "TYPE"):
            features = [features]
        return [f.id for f in features]

    @staticmethod
    def _path_spec(path) -> dict:
        if isinstance(path, Sketch):
            return {"sketch": path.feature.id}
        if getattr(path, "TYPE", None) in ("helix", "curve"):
            return {"feature": path.id}
        if getattr(path, "TYPE", None) == "sketch":
            return {"sketch": path.id}
        raise ApiError("The sweep path must be a sketch, a helix() or a curve()")

    @staticmethod
    def _sections(sections):
        out = []
        for s in sections:
            if isinstance(s, Sketch):
                out.append(s.feature.id)
            else:
                out.append(list(map(float, s)))
        return out

    def import_file(self, path):
        self.doc.import_file(path)
        self._dirty = True
        self._ensure_built()

    def parameter(self, name, expression, kind="length"):
        """Create/update a persisted length (mm), angle (degrees) or scalar parameter."""
        from copy import deepcopy
        from .core.parameters import ParameterError, values
        parameters = deepcopy(self.doc.parameters)
        parameters[name] = {"expression": str(expression), "kind": kind}
        try:
            self.doc.configure_parameters(parameters, self.doc.parameter_bindings)
        except ParameterError as exc:
            raise ApiError(str(exc)) from exc
        self._dirty = False
        value = values(self.doc.parameters)[name].value
        return math.degrees(value) if kind == "angle" else value

    def bind_parameter(self, feature, field, expression):
        """Bind a feature field or sketch 'dimension:<constraint id>' to an expression."""
        from .core.parameters import ParameterError
        feature = getattr(feature, "feature", feature)
        if not any(f is feature for f in self.doc.features):
            raise ApiError("The parameter target must belong to this part")
        bindings = [b for b in self.doc.parameter_bindings
                    if (b["feature"], b["field"]) != (feature.id, field)]
        if expression is not None:
            bindings.append({"feature": feature.id, "field": field, "expression": str(expression)})
        try:
            self.doc.configure_parameters(self.doc.parameters, bindings)
        except ParameterError as exc:
            raise ApiError(str(exc)) from exc
        self._dirty = False

    def rename_parameter(self, old, new):
        from .core.parameters import ParameterError, renamed
        try:
            self.doc.configure_parameters(*renamed(self.doc.parameters, self.doc.parameter_bindings, old, new))
        except ParameterError as exc:
            raise ApiError(str(exc)) from exc
        self._dirty = False

    def _add(self, feature):
        self.doc.add_feature(feature)
        self._dirty = True
        self._ensure_built()
        if feature.error:
            raise ApiError(f"{feature.name}: {feature.error}")
        return feature

    def _ensure_built(self):
        if self._dirty:
            self.doc.regenerate()
            self._dirty = False
            errs = [f"{f.name}: {f.error}" for f in self.doc.features if f.error]
            if errs:
                raise ApiError("; ".join(errs))

    # ---- queries ------------------------------------------------------------------------------
    @property
    def faces(self):
        self._ensure_built()
        return self.doc.faces

    def faces_where(self, kind=None, d=None, r=None, normal=None, tol=1e-3):
        """All faces matching: kind ("plane", "cylinder", "cone", "sphere", "torus", "bspline"),
        diameter d / radius r (cylinders, spheres), planar normal ("+z" or vector)."""
        return faces_where(self.faces, kind, d, r, normal, tol)

    def holes(self, d=None):
        """Cylindrical faces (hole walls / bosses), optionally of diameter d."""
        return self.faces_where(kind="cylinder", d=d)

    def face(self, normal=None, at=None, where="max", index=None, kind=None, d=None):
        """Pick one face.

        normal: "+x".."-z" or a vector - planar faces pointing that way; where="max" takes the
                outermost one along the normal, "min" the innermost.
        at:     (x, y, z) - the face whose surface is nearest to that point (combine with the
                other filters, e.g. face(kind="cylinder", d=9, at=(0, 30, 20))).
        kind/d: surface type / cylinder diameter filter.
        index:  face index as printed by info().
        """
        return select_face(self.faces, self.doc.body, normal, at, where, index, kind, d)

    def vertex(self, near):
        """Model vertex nearest to a point (useful for point loads)."""
        self._ensure_built()
        vs = geometry.vertices(self.doc.body)
        return vs[int(np.argmin(np.linalg.norm(vs - np.asarray(near, float), axis=1)))]

    def edge(self, near) -> int:
        """Index of the model edge passing nearest to a point (for measure)."""
        self._ensure_built()
        tess = self.doc.tessellation
        q = np.asarray(near, float)
        best, bd = None, np.inf
        for pl, eid in zip(tess.edge_polylines, tess.edge_ids):
            a, b = pl[:-1], pl[1:]
            ab = b - a
            t = np.clip(np.sum((q - a) * ab, axis=1) / np.maximum(np.sum(ab * ab, axis=1), 1e-300), 0, 1)
            d = float(np.min(np.linalg.norm(a + t[:, None] * ab - q, axis=1)))
            if d < bd:
                best, bd = eid, d
        if best is None:
            raise ApiError("The part has no edges")
        return int(best)

    def measure(self, *items, mode='minimum', unit='mm', decimals=4) -> dict:
        """SolidWorks-style Measure of faces (from face()/faces_where()), edges
        (("edge", i)) and vertices (("vertex", i) or the vertex nearest a bare XYZ tuple).
        Use ("point", (x, y, z)) for exact coordinates. Modes: minimum, center, maximum
        (points/full circles in parallel planes only). Numeric results stay in mm/mm²;
        unit and decimals format "text". Three points give the angle at the second point.
        """
        from .core import measure as _measure
        self._ensure_built()
        refs = []
        for it in items:
            if hasattr(it, "area") and hasattr(it, "index"):
                refs.append(("face", it.index))
            elif isinstance(it, tuple) and len(it) == 2 and isinstance(it[0], str):
                refs.append(it)
            else:
                vs = geometry.vertices(self.doc.body)
                refs.append(("vertex", int(np.argmin(np.linalg.norm(vs - np.asarray(it, float), axis=1)))))
        r = _measure.measure(self.doc.body, refs, mode=mode)
        r["text"] = _measure.report(r, unit=unit, decimals=decimals)
        return r

    def check_geometry(self):
        """Read-only kernel validity/closed-solid report, including failed features."""
        from .core.geometry_check import part
        try:
            self._ensure_built()
        except ApiError:
            pass  # failed features are included in the incomplete report
        return part(self.doc)

    def dimensions(self) -> dict:
        """World-axis bounding-box minimum, maximum, XYZ size and diagonal, in mm."""
        from .core.measure import model_dimensions
        self._ensure_built()
        return model_dimensions(self.doc.body)

    def set_material(self, name=None, *, density=None):
        """Persist physical material for mass properties. Density: kg/m³; no args clears.

        FEA study materials remain independent.
        """
        from .core.mass_properties import material
        self.doc.mass_material = material(name, density)
        return self

    def mass_properties(self):
        """Volume/area in mm³/mm², mass in kg, inertia in kg mm², world-axis coordinates."""
        from .core.mass_properties import part
        self._ensure_built()
        return part(self.doc.body, self.doc.mass_material)

    def volume(self) -> float:
        self._ensure_built()
        return geometry.volume(self.doc.body)

    def bbox(self):
        self._ensure_built()
        lo, hi = geometry.bounding_box(self.doc.body)
        return lo.round(6).tolist(), hi.round(6).tolist()

    def info(self) -> dict:
        from .cli import document_info
        self._ensure_built()
        return document_info(self.doc)

    # ---- simulation -------------------------------------------------------------------------------
    def study(self, name=None, material=None) -> Study:
        st = Study(self, self.doc.add_study(name))
        if material:
            st.material(material)
        return st

    # ---- output ---------------------------------------------------------------------------------------
    def save(self, path):
        self._ensure_built()
        self.doc.save(path)
        return path

    def export(self, path, fmt=None, *, features=False, require_native=False):
        self._ensure_built()
        self.doc.export(path, fmt, features=features, require_native=require_native)
        return path

    def solidworks_compatibility(self) -> dict:
        """Check editable offline export without changing this part or writing an output file."""
        from .core.swcompatibility import check
        return check(self.doc)

    def export_solidworks_offline(self, path, *, features=True, require_native=False) -> dict:
        """Write .SLDPRT without COM; report native tree or whole-part solid fallback."""
        self._ensure_built()
        return self.doc.export(path, 'sldprt', features=features, require_native=require_native)

    def export_solidworks(self, path, native=True, require_native=False) -> dict:
        """Native SolidWorks part (.SLDPRT) with the feature tree rebuilt (needs SolidWorks
        on this PC). Returns a report: native (feature tree or solid body), features,
        warnings (real losses; require_native=True fails on them), notes (approximations
        within tolerance), helpers (hidden helper geometry made for SolidWorks), steps (a
        step that became several SolidWorks features), volume."""
        from . import solidworks
        self._ensure_built()
        return solidworks.export_part_file(self.doc, path, native, require_native=require_native)

    def render(self, path, view="iso", size=(1400, 1000), study=None, show_sketches=True):
        from .render import render_model
        self._ensure_built()
        render_model(self.doc, path, view=view, size=size,
                     study=study.study if isinstance(study, Study) else study,
                     show_sketches=show_sketches)
        return path


# =============================================================================================
# Assemblies
# =============================================================================================

class ComponentFace:
    """A face of an assembly component (selected in the part's own coordinates)."""

    def __init__(self, component: "Component", face):
        self.component, self.face = component, face

    @property
    def ref(self):
        from .core.assembly import MateRef
        return MateRef(self.component.comp.id, FaceRef.from_face(self.face))


class Component:
    """Handle for a placed part. Face selectors use the PART's coordinates."""

    def __init__(self, asm: "Assembly", comp):
        self.asm, self.comp = asm, comp

    @property
    def name(self):
        return self.comp.name

    @property
    def pose(self):
        return self.comp.pose

    @property
    def faces(self):
        return self.comp.faces

    def face(self, normal=None, at=None, where="max", index=None, kind=None, d=None) -> ComponentFace:
        return ComponentFace(self, select_face(self.comp.faces, self.comp.shape, normal, at, where,
                                               index, kind, d))

    def holes(self, d=None):
        return [ComponentFace(self, f) for f in faces_where(self.comp.faces, kind="cylinder", d=d)]

    def world(self, local_point):
        return self.comp.pose.point(local_point)

    def move(self, dx=0.0, dy=0.0, dz=0.0):
        """Translate in world coordinates (then re-solve the mates)."""
        from .core.assembly import Pose
        self.comp.pose = Pose(self.comp.pose.R, self.comp.pose.t + np.array([dx, dy, dz], float))
        return self

    def rotate(self, axis, degrees, about=None):
        """Rotate about a world axis through `about` (default: the component origin)."""
        from .core.assembly import Pose, rodrigues
        a = np.asarray(_AXES.get(axis, axis) if isinstance(axis, str) else axis, float)
        Rr = rodrigues(a / np.linalg.norm(a) * math.radians(degrees))
        c = self.comp.pose.t if about is None else np.asarray(about, float)
        self.comp.pose = Pose(Rr @ self.comp.pose.R, Rr @ (self.comp.pose.t - c) + c)
        return self

    def fix(self, fixed=True):
        self.comp.fixed = bool(fixed)
        return self

    def set_material(self, name=None, *, density=None):
        """Persist an instance material override; no args restores the source-part material."""
        from .core.mass_properties import material
        self.comp.mass_material = material(name, density)
        return self


class Assembly:
    """Scripting facade for assemblies.

        asm = Assembly("hinge")
        base = asm.add("base.bcad")                  # first component is fixed
        pin = asm.add("pin.bcad", at=(0, 0, 60))
        asm.mate("concentric", pin.face(kind="cylinder"), base.face(kind="cylinder", d=10))
        asm.mate("coincident", pin.face("-z"), base.face("+z"))
        asm.solve(); print(asm.dof()); asm.render("hinge.png"); asm.save("hinge.bcasm")
    """

    def __init__(self, name="Assembly1", asm=None):
        from .core.assembly import Assembly as _Asm
        self.name = name
        self.asm = asm or _Asm()

    def bom(self):
        """Flat BOM of the current assembly; grouping uses full source file paths."""
        from .core.bom import BOMTable
        table = BOMTable("BOM")
        return table.refresh(self.asm)

    def check_geometry(self):
        """Check every component instance, including missing geometry and load failures."""
        from .core.geometry_check import assembly
        return assembly(self.asm)

    def mass_properties(self):
        """Sum instance mass properties in world coordinates, with per-component densities."""
        from .core.mass_properties import assembly
        return assembly(self.asm)

    @classmethod
    def open(cls, path) -> "Assembly":
        from .core.assembly import Assembly as _Asm
        return cls(Path(path).stem, _Asm.load(path))

    def add(self, source, at=None, rotate=None, fixed=None, name=None, pose=None,
            color=None) -> Component:
        """Insert a part (.bcad / STEP path, or a saved Part). at: world position of the part
        origin; rotate: (axis, degrees); or pose: a 4x4 placement matrix (rotation + translation,
        mm), e.g. a component transform read from another CAD assembly. color: (r, g, b), 0..255 or
        0..1."""
        from .core.assembly import Pose, rodrigues
        if isinstance(source, Part):
            if source.doc.path is None:
                raise ApiError("Save the Part before adding it to an assembly")
            source = source.doc.path
        if pose is not None:
            if at is not None or rotate is not None:
                raise ApiError("Give pose= or at= / rotate=, not both")
            M = np.asarray(pose, float)
            if M.shape != (4, 4) or not np.allclose(M[:3, :3] @ M[:3, :3].T, np.eye(3), atol=1e-6) \
                    or np.linalg.det(M[:3, :3]) < 0:
                raise ApiError("pose must be a 4x4 rotation + translation matrix")
            pose = Pose(M[:3, :3], M[:3, 3])
        if at is not None or rotate is not None:
            R = np.eye(3)
            if rotate is not None:
                ax, deg = rotate
                a = np.asarray(_AXES.get(ax, ax) if isinstance(ax, str) else ax, float)
                R = rodrigues(a / np.linalg.norm(a) * math.radians(deg))
            pose = Pose(R, at if at is not None else np.zeros(3))
        comp = self.asm.add_component(source, pose, fixed, name)
        if color is not None:                       # RGB 0..255 (as the GUI), or 0..1 floats
            c = [float(v) for v in color]
            comp.color = tuple(int(round(v * 255)) if max(c) <= 1.0 else int(round(v)) for v in c)
        return Component(self, comp)

    def component(self, name) -> Component:
        for c in self.asm.components:
            if c.name == name or c.id == name:
                return Component(self, c)
        raise ApiError(f"No component {name!r}")

    @property
    def components(self):
        return [Component(self, c) for c in self.asm.components]

    def mate(self, type, a, b=None, value=None, flip=False):
        """Add a mate between two ComponentFaces (or two Components for 'lock').
        value: distance [mm] or angle [deg]. flip: swap aligned / anti-aligned."""
        from .core.assembly import MateRef
        if type == "lock":
            ca = a.comp.id if isinstance(a, Component) else a.component.comp.id
            cb = b.comp.id if isinstance(b, Component) else b.component.comp.id
            refs = (MateRef(ca, None), MateRef(cb, None))
        else:
            refs = (a.ref, b.ref)
        v = 0.0 if value is None else float(value)
        if type == "angle":
            v = math.radians(v)
        m = self.asm.new_mate(type, refs[0], refs[1], v)
        if flip:
            m.align = -m.align
        ok, msg = self.asm.add_mate(m)
        if not ok:
            raise ApiError(f"{m.name}: {msg}")
        return m

    def solve(self):
        res = self.asm.solve()
        if not res.ok:
            raise ApiError("The mates could not be satisfied")
        return res

    def dof(self) -> dict:
        """{component name: remaining degrees of freedom}."""
        d = self.asm.dof()
        return {c.name: d[c.id] for c in self.asm.components}

    def mobility(self) -> int:
        """Total degrees of freedom of the assembly (e.g. 3 for a 3-axis arm)."""
        return self.asm.mobility()

    def render(self, path, view="iso", size=(1400, 1000)):
        from .render import render_assembly
        render_assembly(self.asm, path, view=view, size=size)
        return path

    # ---- motion ------------------------------------------------------------------------------
    def _mate(self, m):
        if isinstance(m, str):
            found = next((x for x in self.asm.mates if x.name == m or x.id == m), None)
            if found is None:
                raise ApiError(f"No mate {m!r}")
            return found
        return m

    def _mate_values(self, values: dict) -> dict:
        """{mate name or id: mm / deg} -> {mate id: internal value (mm / rad)}."""
        out = {}
        for key, v in values.items():
            m = self._mate(key)
            if m.type not in ("distance", "angle"):
                raise ApiError(f"{m.name}: only distance and angle mates can be driven")
            out[m.id] = math.radians(float(v)) if m.type == "angle" else float(v)
        return out

    def set(self, mate, value):
        """Change a driving distance [mm] / angle [deg] mate and re-solve: the mechanism
        moves (in small steps, so it stays on its branch)."""
        return self.drive({self._mate(mate).id: value})

    def drive(self, values: dict):
        """Set several driving mates at once: {mate name (or id): mm / deg}."""
        res = self.asm.drive(self._mate_values(values))
        if not res.ok:
            raise ApiError("The mechanism can't reach these mate values (locks up or singular)")
        return res

    def suppress(self, mate, suppressed=True):
        """Suppress / unsuppress a mate (e.g. free a driven joint so it can be dragged)."""
        self._mate(mate).suppressed = bool(suppressed)
        return self

    def motion(self, keyframes: list[dict], frames=12) -> list[dict]:
        """Poses along a motion through keyframes [{mate (or name): mm / deg}, ...]."""
        return self.asm.motion([self._mate_values(k) for k in keyframes], frames)

    def animate(self, path, keyframes: list[dict], frames=12, view="iso", size=(900, 680),
                fps=15, title=None, trace=None):
        """Motion study: drive mates through keyframes and write an animated GIF (or a
        folder of PNG frames when `path` has no .gif suffix). `trace=(component, local
        point)` draws the path of that point (e.g. the tool tip). Returns the poses."""
        from .render import render_assembly_frames
        poses = self.motion(keyframes, frames)
        tr = None
        if trace is not None:
            comp, lp = trace
            comp = comp if isinstance(comp, Component) else self.component(comp)
            tr = (comp.comp.id, np.asarray(lp, float))
        render_assembly_frames(self.asm, poses, path, view=view, size=size, fps=fps,
                               title=title, trace=tr)
        return poses

    def interference(self, min_volume=1e-3, ignore=()) -> list[dict]:
        """Overlapping components at the current position: [{a, b, volume}] by name."""
        ids = [(self._cid(a), self._cid(b)) for a, b in ignore]
        names = {c.id: c.name for c in self.asm.components}
        return [{"a": names[d["a"]], "b": names[d["b"]], "volume": d["volume"]}
                for d in self.asm.interference(min_volume, ids)]

    def _clearance_out(self, r):
        names = {c.id: c.name for c in self.asm.components}
        out = {"a": names[r["a"]], "b": names[r["b"]], "distance": r["distance"],
               "point_a": np.round(r["point_a"], 6).tolist(),
               "point_b": np.round(r["point_b"], 6).tolist(), "overlap": r["overlap"]}
        if "frame" in r:
            out["frame"], out["distances"] = r["frame"], r["distances"]
        return out

    def check_interference(self, min_volume=1e-3, ignore=()):
        """Complete overlap check; missing/invalid geometry raises instead of being skipped.

        Volumes are pairwise mm³. Their sum can count triple intersections more than once.
        """
        ids = [(self._cid(a), self._cid(b)) for a, b in ignore]
        names = {c.id: c.name for c in self.asm.components}
        hits = self.asm.interference(min_volume, ids, strict=True)
        return dict(overlaps=[dict(a=names[h['a']], b=names[h['b']], volume=h['volume']) for h in hits],
                    min_volume=min_volume, component_count=len(self.asm.components),
                    ignored_pairs=len({frozenset(pair) for pair in ids}),
                    pair_volume_sum=sum(h['volume'] for h in hits))

    def clearance(self, a, b=None) -> dict:
        """Minimum distance [mm] between two components, or from `a` to the nearest other
        component (b=None): {a, b, distance, point_a, point_b, overlap}. Distance 0 means
        touching or overlapping; overlap [mm3] > 0 means they interfere."""
        return self._clearance_out(self.asm.clearance(self._cid(a),
                                                      self._cid(b) if b is not None else None, strict=True))

    def clearance_sweep(self, a, b=None, keyframes=None, frames=12, poses=None) -> dict:
        """Smallest clearance between a and b (or anything) over a motion: drive the mates
        through `keyframes` (as in motion()), or pass `poses` from motion(). Returns the
        closest frame {a, b, distance, point_a, point_b, overlap, frame, distances}."""
        if poses is None:
            if not keyframes:
                raise ApiError("Pass keyframes (or poses from motion())")
            poses = self.motion(keyframes, frames)
        r = self.asm.clearance_sweep(self._cid(a), self._cid(b) if b is not None else None, poses)
        return self._clearance_out(r)

    def _cid(self, c):
        if isinstance(c, Component):
            return c.comp.id
        return self.component(c).comp.id

    def save(self, path):
        self.asm.save(path)
        return path

    def pack_and_go(self, path):
        """Portable ZIP of current assembly/mates and all referenced saved parts."""
        from .core.packaging import pack_and_go
        return pack_and_go(self.asm, path)

    def export(self, path, fmt=None):
        self.asm.export(path, fmt)
        return path

    def export_solidworks(self, folder, native=True, require_native=False) -> dict:
        """Native SolidWorks assembly: folder/<name>.SLDASM + a .SLDPRT per part, components
        floating / fixed as here and every mate recreated as a SolidWorks mate, so it moves in
        SolidWorks. Needs SolidWorks on this PC; save the assembly first (part paths)."""
        from . import solidworks
        return solidworks.export_assembly_files(self.asm, folder, native, require_native=require_native)

    def info(self) -> dict:
        d = self.asm.dof()
        return {"components": [{"name": c.name, "source": c.source, "fixed": c.fixed,
                                "dof": d[c.id], "t": np.round(c.pose.t, 6).tolist(),
                                "R": np.round(c.pose.R, 9).tolist(), "error": c.load_error}
                               for c in self.asm.components],
                "mates": [{"name": m.name, "type": m.type, "value": m.value,
                           "components": [self.asm.component(r.component).name for r in m.refs]}
                          for m in self.asm.mates]}


# =============================================================================================
# Drawings
# =============================================================================================

class Drawing:
    """A 2D drawing sheet of a part or an assembly (SolidWorks-style), exported to PDF, DXF,
    DWG, SVG or PNG.

        dr = Drawing(part, sheet="A3")                 # standard 3 views + isometric
        front = dr.view("Front")
        dr.dim(front, (0, 0, 0), (120, 0, 0))          # model points (3D) or view points (2D)
        dr.diameter(front, d=12)                       # the 12 mm hole
        dr.auto_dimension(front)                       # overall sizes + hole diameters
        dr.title(drawing_no="BC-001", material="S235")
        dr.save("bracket.bcdrw"); dr.export("bracket.pdf"); dr.export("bracket.dxf")
        dr.export("front.dxf", view=front)             # one view at 1:1 (laser / CNC)
    """

    def __init__(self, model=None, sheet="A3", portrait=False, projection="first", scale=None,
                 standard=True, iso=True, drawing=None):
        from .core.drawing import Drawing as _Drawing
        if drawing is not None:
            self.dr = drawing
            return
        path, shape = None, None
        if isinstance(model, Part):
            model._ensure_built()
            if model.doc.path is not None and Path(model.doc.path).suffix.lower() == ".bcad":
                path = model.doc.path
            else:
                shape = model.doc.body
        elif isinstance(model, Assembly):
            if model.asm.path is not None:
                path = model.asm.path
            else:
                shape = model.asm.world_shape()
        elif model is not None:
            path = Path(model)
        self.dr = _Drawing(str(path) if path else None, sheet, portrait, 1.0, projection)
        if shape is not None:                 # unsaved model: drawn, but not linked to a file
            self.dr.shape_override = shape
            self.dr.title["title"] = getattr(model, "name", "") or ""
        if path is not None or shape is not None:
            self.dr.load_model()
            if standard:
                self.dr.standard_views(iso=iso, scale=_parse_scale(scale))
            elif scale is not None:
                self.dr.scale = _parse_scale(scale)

    @classmethod
    def open(cls, path) -> "Drawing":
        from .core.drawing import Drawing as _Drawing
        d = _Drawing.load(path)
        d.load_model()
        return cls(drawing=d)

    # ---- views ------------------------------------------------------------------------------
    @property
    def views(self):
        return self.dr.views

    def bom(self, at=None):
        """Insert/refresh the BOM. at is its top-left corner in sheet mm."""
        return self.dr.add_bom(at)

    def refresh_bom(self):
        return self.dr.refresh_bom()

    def balloon(self, view, component, anchor, at):
        """Link a balloon to a component; anchor is local 3D, at is sheet mm."""
        cid = component.comp.id if isinstance(component, Component) else component
        return self.dr.add_balloon(self._v(view), cid, anchor, at)

    def view(self, name):
        """A view by name ("Front", "Top", "Left", "Isometric", ...) or id."""
        v = self.dr.view(name)
        if v is None:
            v = next((v for v in self.dr.views if v.name.lower() == str(name).lower()), None)
        if v is None:
            raise ApiError(f"No view {name!r}; views: {[v.name for v in self.dr.views]}")
        return v

    def add_view(self, orientation="front", at=None, scale=None, hidden=None):
        return self.dr.add_view(orientation, at=at, scale=_parse_scale(scale), hidden=hidden)

    def projected(self, parent, side="right", at=None):
        """View projected from parent, placed on side left/right/up/down of it."""
        return self.dr.projected_view(self._v(parent), side, at)

    def move(self, view, x, y):
        self.dr.move_view(self._v(view), x, y)

    def _v(self, view):
        return self.view(view) if isinstance(view, str) else view

    def _to_view(self, v, p):
        p = np.asarray(p, float)
        if p.shape == (3,):
            from .core.geometry import view_frame
            _, x, y = view_frame(v.direction, v.xdir)
            return np.array([p @ x, p @ y])
        return p

    # ---- dimensions ---------------------------------------------------------------------------
    def dim(self, view, p1, p2, orient=None, offset=None, text=None, at=None):
        """Linear dimension between two points (3D model points or 2D view points).
        orient: "h", "v", "a" or None (automatic). offset: distance of the dimension line
        from the view's outline in sheet mm (default 10), on the side the points are on.
        at: a point (3D model or 2D view) the dimension line passes through instead - puts
        it on either side, or inside the outline."""
        v = self._v(view)
        a, b = self._to_view(v, p1), self._to_view(v, p2)
        d = self.dr.add_linear(v, a, b, orient, text=text)
        if at is not None:
            d.pos = tuple(float(c) for c in self._to_view(v, at))
            return d
        if offset is not None:
            g = self.dr.geometry(v)
            s = self.dr.view_scale(v)
            mid = (a + b) / 2
            if d.orient == "h":
                up = mid[1] > g.center[1]
                d.pos = (mid[0], (g.hi[1] + offset / s) if up else (g.lo[1] - offset / s))
            elif d.orient == "v":
                right = mid[0] > g.center[0]
                d.pos = ((g.hi[0] + offset / s) if right else (g.lo[0] - offset / s), mid[1])
            else:
                u = d.direction()
                n = np.array([-u[1], u[0]])
                if n @ (mid - g.center) < 0:
                    n = -n
                d.pos = tuple(mid + n * offset / s)
        return d

    def _circle(self, v, near=None, d=None, arcs=False):
        g = self.dr.geometry(v)
        cands = list(g.circles)
        if arcs:                          # radius dimensions also take arcs (rounded ends, fillets)
            for e in g.edges:
                if e.kind == "circle" and e.visible and e.a1 - e.a0 < 360 - 1e-6 and not any(
                        np.linalg.norm(e.center - c) < 1e-6 and abs(e.r - r) < 1e-6 for c, r in cands):
                    cands.append((e.center.copy(), e.r))
        if not cands:
            raise ApiError(f"The {v.name} view has no {'circles or arcs' if arcs else 'circles'}")
        if d is not None:
            cands = [c for c in cands if abs(2 * c[1] - d) < 1e-3 * max(d, 1)]
            if not cands:
                raise ApiError(f"No circle of diameter {d} in the {v.name} view")
        if near is not None:
            q = self._to_view(v, near)
            if arcs:                      # a point ON the arc picks it (concentric hole vs end)
                return min(cands, key=lambda c: abs(np.linalg.norm(c[0] - q) - c[1]))
            return min(cands, key=lambda c: np.linalg.norm(c[0] - q))
        return max(cands, key=lambda c: c[1])

    def diameter(self, view, near=None, d=None, at=None, text=None):
        """Diameter of the circle nearest `near` (or with diameter d). at: text position (3D
        model point or 2D view point), default up-right of the circle."""
        v = self._v(view)
        c, r = self._circle(v, near, d)
        return self.dr.add_diameter(v, c, r, pos=self._to_view(v, at) if at is not None else None,
                                    text=text)

    def radius(self, view, near=None, d=None, at=None, text=None):
        """Radius of the circle or arc passing nearest to `near` (a point on it; arcs too:
        rounded slot ends, fillets)."""
        v = self._v(view)
        c, r = self._circle(v, near, d, arcs=True)
        return self.dr.add_radius(v, c, r, pos=self._to_view(v, at) if at is not None else None,
                                  text=text)

    def auto_dimension(self, view):
        return self.dr.auto_dimension(self._v(view))

    def note(self, text, at=(30, 60), height=3.5):
        """A text note at a sheet position (mm from the sheet's bottom-left corner)."""
        return self.dr.add_note(text, at, height)

    def title(self, **fields):
        """Title block: title, drawing_no, material, company, drawn_by, checked_by, date,
        revision, finish."""
        from .core.drawing import TITLE_FIELDS
        keys = {k for k, _ in TITLE_FIELDS}
        bad = set(fields) - keys
        if bad:
            raise ApiError(f"Unknown title block fields {sorted(bad)}; use {sorted(keys)}")
        self.dr.title.update({k: str(v) for k, v in fields.items()})

    # ---- output ---------------------------------------------------------------------------------
    def save(self, path):
        return self.dr.save(path)

    def export(self, path, view=None):
        """.pdf .svg .png .dxf .dwg; view= one view at 1:1 in model mm (DXF/DWG)."""
        v = self._v(view) if view is not None else None
        return self.dr.export(path, view=v.id if v is not None else None)

    def render(self, path):
        """PNG of the sheet (to look at)."""
        return self.dr.export(Path(path).with_suffix(".png"))

    def info(self) -> dict:
        from .core.drawing import scale_text
        return {"sheet": self.dr.sheet, "portrait": self.dr.portrait,
                "scale": scale_text(self.dr.scale), "projection": self.dr.projection,
                "model": self.dr.model,
                "views": [{"id": v.id, "name": v.name, "at": [round(v.x, 2), round(v.y, 2)],
                           "scale": scale_text(self.dr.view_scale(v)),
                           "box": [round(float(c), 2) for c in self.dr.view_box(v)]}
                          for v in self.dr.views],
                "dims": [{"id": d.id, "view": self.dr.view(d.view).name, "kind": d.kind,
                          "value": round(d.value, 6), "text": d.label(self.dr.decimals)}
                         for d in self.dr.dims]}


def export_solidworks_tree(path, children, name=None) -> list:
    """A SolidWorks 2024 assembly of sub-assemblies (and parts) written by BauyrCAD itself, no
    SolidWorks needed: `path` = the top `.SLDASM`; `children` = [(child, pose, fixed)] with child
    an `Assembly`, a `.bcasm` path, an existing `.SLDASM` in the same folder (e.g. one saved by
    SolidWorks: referenced as it is), a saved `Part` or a `.bcad` / STEP path, pose a 4x4 matrix
    (mm; None = identity) and fixed True / False. Each child assembly is written first as
    `<its name>.SLDASM` next to the top (with its parts, like `asm.export(".SLDASM")`), then the
    top references it as a sub-assembly component. Every assembly in the tree may have up to
    `swfile.max_components()` components, so large machines go in as a tree of sub-assemblies.
    No mates and no colours (as `asm.export(".SLDASM")`). Returns the files written."""
    from .core import swfile, parasolid
    from .core.exchange import ExchangeError
    path = Path(path)
    if path.suffix.lower() != ".sldasm":
        raise ApiError("export_solidworks_tree writes a .SLDASM")
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True)
    parts, instances, written, index = [], [], [], {}
    for child, pose, fixed in children:
        M = np.eye(4) if pose is None else np.asarray(pose, float)
        if isinstance(child, (str, Path)) and str(child).lower().endswith(".bcasm"):
            child = Assembly.open(child)
        if isinstance(child, (str, Path)) and str(child).lower().endswith(".sldasm"):
            sub = Path(child)                     # an existing SolidWorks assembly: referenced as is
            if not sub.exists():
                raise ApiError(f"{sub} not found")
            if sub.resolve().parent != folder.resolve():
                raise ApiError(f"{sub.name} must be in the same folder as {path.name}")
            key = ("asm", sub.stem.lower())
            if key not in index:
                index[key] = len(parts)
                parts.append((sub.stem, sub, None))
        elif isinstance(child, Assembly):
            stem = child.asm.path.stem if child.asm.path else child.name
            key = ("asm", stem.lower())
            if key not in index:
                sub = folder / f"{stem}.SLDASM"
                try:
                    child.asm.export(sub, "sldasm")
                except ExchangeError as e:
                    raise ApiError(f"sub-assembly {stem}: {e}") from e
                index[key] = len(parts)
                parts.append((stem, sub, None))
                written.append(sub)
        else:
            if isinstance(child, Part):
                if child.doc.path is None:
                    raise ApiError("Save the Part before adding it to an assembly tree")
                child = child.doc.path
            key = ("part", str(Path(child).resolve()).lower())
            if key not in index:
                from .core.assembly import Component as _C
                c = _C("tmp", Path(child).stem, str(child)).load()
                if c.load_error:
                    raise ApiError(c.load_error)
                index[key] = len(parts)
                parts.append((Path(child).stem, c.shape, None))
        instances.append((index[key], None, M, bool(fixed)))
    try:
        written += swfile.write_assembly(parts, instances, path, name=name or path.stem)
    except (swfile.SwFileError, parasolid.XtError) as e:
        raise ApiError(f"SolidWorks assembly (.SLDASM) export: {e}") from e
    written.append(path)
    return written


def _parse_scale(scale):
    """1:2 / "2:1" / 0.5 -> float."""
    if scale is None:
        return None
    if isinstance(scale, str) and ":" in scale:
        a, b = scale.split(":")
        return float(a) / float(b)
    return float(scale)


def _make_drawing(self, **kw) -> Drawing:
    """A drawing of this part / assembly: Drawing(self, **kw)."""
    return Drawing(self, **kw)


Part.drawing = _make_drawing
Assembly.drawing = _make_drawing
