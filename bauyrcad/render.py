"""Off-screen rendering to PNG (no GUI): models, fixtures/loads and FEA results.

Used by the scripting API and the command line so a model can be checked visually
without opening the application.
"""

from __future__ import annotations

import numpy as np
import pyvista as pv

from .core import geometry
from .fea.analysis import FIELDS

VIEWS = {
    "iso": ((1, 1, 1), (0, 1, 0)), "iso2": ((-1, 1, 1), (0, 1, 0)), "iso_back": ((-1, 1, -1), (0, 1, 0)),
    "iso_below": ((1, -1, 1), (0, 1, 0)),
    "front": ((0, 0, 1), (0, 1, 0)), "back": ((0, 0, -1), (0, 1, 0)),
    "top": ((0, 1, 0), (0, 0, -1)), "bottom": ((0, -1, 0), (0, 0, 1)),
    "right": ((1, 0, 0), (0, 1, 0)), "left": ((-1, 0, 0), (0, 1, 0)),
}
BODY = "#b0bac8"
FIXED = "#3cbe5a"
FORCE = "#e0162b"


def _plotter(size):
    p = pv.Plotter(off_screen=True, window_size=list(size))
    p.set_background("#fbfcfe", top="#b9c8dc")
    p.enable_parallel_projection()
    p.add_axes(line_width=2, color="black")
    return p


def _set_view(p, view, bounds_mesh=None):
    d, up = VIEWS.get(view, VIEWS["iso"])
    p.camera_position = [tuple(np.array(d, float) * 1000), (0, 0, 0), up]
    p.reset_camera()
    p.camera.parallel_scale *= 0.92


def lines_polydata(polylines, R=None, t=None) -> pv.PolyData:
    """All polylines as ONE line mesh (one VTK actor instead of one per edge - a vendor STEP
    has thousands of edges, and thousands of actors made renders take minutes)."""
    pts, lines, off = [], [], 0
    for pl in polylines:
        pl = np.asarray(pl, dtype=float)
        if len(pl) < 2:
            continue
        pts.append(pl)
        lines.append(np.concatenate([[len(pl)], np.arange(off, off + len(pl))]))
        off += len(pl)
    pd = pv.PolyData()
    if pts:
        P = np.vstack(pts)
        if R is not None:
            P = P @ np.asarray(R).T + np.asarray(t)
        pd.points = P
        pd.lines = np.concatenate(lines)
    return pd


def _body_meshes(doc, highlight=None):
    t = doc.tessellation
    faces = np.hstack([np.full((len(t.triangles), 1), 3), t.triangles]).ravel()
    mesh = pv.PolyData(t.points, faces)
    mesh.cell_data["face_id"] = t.face_ids
    mesh.point_data["Normals"] = t.normals
    return mesh, lines_polydata(t.edge_polylines)


def _model_size(doc):
    lo, hi = geometry.bounding_box(doc.body)
    return max(float(np.linalg.norm(hi - lo)), 1.0)


def render_model(doc, path, view="iso", size=(1400, 1000), study=None, show_sketches=True,
                 title=None):
    """Picture of the part (optionally with a study's fixtures and loads)."""
    p = _plotter(size)
    if doc.has_body():
        mesh, edges = _body_meshes(doc)
        colors = np.tile(np.array([176, 186, 200], np.uint8), (mesh.n_cells, 1))
        if study is not None:
            from .fea.glyphs import load_symbols
            for fx in study.fixtures:
                for ref in fx.faces:
                    i = ref.resolve(doc.faces)
                    if i is not None:
                        colors[mesh.cell_data["face_id"] == i] = (60, 190, 90)
            for ld in study.loads:
                for i in load_symbols(ld, doc.faces, 1.0)[2]:
                    colors[mesh.cell_data["face_id"] == i] = (240, 150, 60)
        mesh.cell_data["rgb"] = colors
        p.add_mesh(mesh, scalars="rgb", rgb=True, smooth_shading=True, ambient=0.35,
                   diffuse=0.7, specular=0.2, show_scalar_bar=False)
        if edges.n_points:
            p.add_mesh(edges, color="#1a1a1a", line_width=1.6)
        if study is not None:
            L = 0.18 * _model_size(doc)
            for ld in study.loads:
                arrows, pts, _ = load_symbols(ld, doc.faces, L)
                for tip, f, length in arrows:
                    n = np.linalg.norm(f)
                    if n > 0:
                        d = f / n
                        p.add_mesh(pv.Arrow(start=np.asarray(tip) - d * length, direction=d,
                                            scale=length, tip_length=0.3, tip_radius=0.08,
                                            shaft_radius=0.03), color=FORCE)
                if pts:
                    p.add_points(np.array(pts, dtype=float), color=FORCE, point_size=12,
                                 render_points_as_spheres=True)
    if show_sketches:
        for sk in doc.sketches():
            if doc.is_consumed(sk.id) or sk.suppressed:
                continue
            for e in sk.sketch.entities.values():
                if e.kind == "point":
                    continue
                pts = sk.plane.to_world_many(sk.sketch.sample(e))
                line = pv.lines_from_points(pts)
                p.add_mesh(line, color="#ff8a00" if e.construction else "#1f5fd6", line_width=2.5)
    if title:
        p.add_text(title, font_size=11, color="black")
    _set_view(p, view)
    p.screenshot(str(path))
    p.close()
    return path


def render_results(doc, results, path, field="von Mises Stress", view="iso", scale=None,
                   clip_percentile=None, size=(1400, 1000), show_mesh=False):
    """Contour plot of a result field on the (deformed) mesh, with a legend and max/min."""
    if field not in FIELDS:
        raise ValueError(f"Unknown field {field!r}; choose from {list(FIELDS)}")
    grid = results.vtk_grid()
    s = results.summary()
    if scale is None:
        scale = 0.08 * _model_size(doc) / s["max_displacement"] if s["max_displacement"] > 0 else 0.0
    warped = grid.warp_by_vector("Displacement", factor=scale) if scale else grid
    surf = warped.extract_surface(nonlinear_subdivision=2, algorithm="dataset_surface")
    raw_all = results.field(field)
    # quadratic subdivision interpolates nodal values and can overshoot (e.g. negative
    # von Mises): keep the plotted values inside the true nodal range
    surf.point_data[field] = np.clip(surf.point_data[field], raw_all.min(), raw_all.max())
    vals = surf.point_data[field]
    clim = None
    if field == "Factor of Safety":
        clim = [float(vals.min()), float(min(vals.max(), max(10 * vals.min(), 10)))]
    elif clip_percentile and clip_percentile < 100:
        clim = [float(vals.min()), float(np.percentile(vals, clip_percentile))]
    unit = FIELDS[field]
    p = _plotter(size)
    p.add_mesh(surf, scalars=field, cmap="jet_r" if field == "Factor of Safety" else "jet",
               clim=clim, n_colors=12, ambient=0.35, diffuse=0.75,
               scalar_bar_args={"title": f"{field} [{unit}]" if unit else field, "vertical": True,
                                "position_x": 0.86, "position_y": 0.2, "height": 0.6, "width": 0.05,
                                "fmt": "%.3g", "color": "black"})
    if show_mesh:
        p.add_mesh(warped.extract_surface(nonlinear_subdivision=0, algorithm="dataset_surface")
                   .extract_all_edges(), color="#202020", line_width=0.5, opacity=0.5)
    if doc.has_body() and scale:
        _, edges = _body_meshes(doc)
        if edges.n_points:
            p.add_mesh(edges, color="#303030", line_width=1.0, opacity=0.6)
    raw = results.field(field)
    txt = (f"{field}: max {raw.max():.4g}  min {raw.min():.4g} {unit}\n"
           f"max displacement {s['max_displacement']:.4g} mm   deformation x{scale:.3g}\n"
           f"{results.material.name}   {s['nodes']} nodes / {s['elements']} elements")
    p.add_text(txt, font_size=10, color="black")
    _set_view(p, view)
    p.screenshot(str(path))
    p.close()
    return path


def render_assembly(asm, path, view="iso", size=(1400, 1000), title=None):
    """Picture of an assembly, one colour per component."""
    p = _plotter(size)
    edges = []
    for c in asm.components:
        t = c.tessellation
        if t is None or not len(t.triangles):
            continue
        pts = t.points @ c.pose.R.T + c.pose.t
        faces = np.hstack([np.full((len(t.triangles), 1), 3), t.triangles]).ravel()
        mesh = pv.PolyData(pts, faces)
        mesh.point_data["Normals"] = t.normals @ c.pose.R.T
        color = np.array(c.color or (176, 186, 200)) / 255.0
        p.add_mesh(mesh, color=color, smooth_shading=True, ambient=0.35, diffuse=0.7, specular=0.2)
        edges.append(lines_polydata(t.edge_polylines, c.pose.R, c.pose.t))
    edges = [e for e in edges if e.n_points]
    if edges:                                   # every component's edges in one actor
        p.add_mesh(pv.merge(edges), color="#1a1a1a", line_width=1.4)
    if title:
        p.add_text(title, font_size=11, color="black")
    _set_view(p, view)
    p.screenshot(str(path))
    p.close()
    return path


def render_assembly_frames(asm, poses, path, view="iso", size=(900, 680), fps=15, title=None,
                           trace=None):
    """Animation of an assembly through a list of {component id: Pose}. Writes an animated
    GIF (path ending in .gif) or numbered PNG frames into the folder `path`. The camera is
    fixed and fits the whole motion. trace = (component id, local point) draws its path."""
    from pathlib import Path

    from PIL import Image
    path = Path(path)
    p = _plotter(size)
    actors = []
    for c in asm.components:
        t = c.tessellation
        if t is None or not len(t.triangles):
            continue
        faces = np.hstack([np.full((len(t.triangles), 1), 3), t.triangles]).ravel()
        mesh = pv.PolyData(t.points, faces)
        mesh.point_data["Normals"] = t.normals
        color = np.array(c.color or (176, 186, 200)) / 255.0
        a1 = p.add_mesh(mesh, color=color, smooth_shading=True, ambient=0.35, diffuse=0.7,
                        specular=0.2)
        acts = [a1]
        if t.edge_polylines:
            acts.append(p.add_mesh(lines_polydata(t.edge_polylines), color="#1a1a1a",
                                   line_width=1.2))
        actors.append((c.id, acts))
    # camera fitted to the swept volume
    lo, hi = None, None
    for fr in poses:
        for c in asm.components:
            if c.shape is None or c.id not in fr:
                continue
            l, h = geometry.bounding_box(c.shape)
            corners = np.array([[x, y, z] for x in (l[0], h[0]) for y in (l[1], h[1]) for z in (l[2], h[2])])
            w = corners @ fr[c.id].R.T + fr[c.id].t
            lo = w.min(0) if lo is None else np.minimum(lo, w.min(0))
            hi = w.max(0) if hi is None else np.maximum(hi, w.max(0))
    if lo is not None:
        box = pv.Box(bounds=(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))
        ghost = p.add_mesh(box, opacity=0.0)
        _set_view(p, view)
        p.remove_actor(ghost)
    else:
        _set_view(p, view)
    if title:
        p.add_text(title, font_size=10, color="black")
    trace_pts = []
    trace_actor = None
    images = []
    for fr in poses:
        for cid, acts in actors:
            m = fr[cid].matrix if cid in fr else np.eye(4)
            for a in acts:
                a.user_matrix = m
        if trace is not None and trace[0] in fr:
            trace_pts.append(fr[trace[0]].point(trace[1]))
            if trace_actor is not None:
                p.remove_actor(trace_actor)
                trace_actor = None
            if len(trace_pts) > 1:
                trace_actor = p.add_mesh(pv.lines_from_points(np.array(trace_pts)),
                                         color="#d62728", line_width=3)
        p.render()
        images.append(Image.fromarray(p.screenshot(return_img=True)))
    p.close()
    if path.suffix.lower() == ".gif":
        pal = [im.convert("P", palette=Image.ADAPTIVE, colors=128) for im in images]
        pal[0].save(path, save_all=True, append_images=pal[1:], duration=int(1000 / fps),
                    loop=0, optimize=True)
    else:
        path.mkdir(parents=True, exist_ok=True)
        for i, im in enumerate(images):
            im.save(path / f"frame_{i:04d}.png")
    return path
