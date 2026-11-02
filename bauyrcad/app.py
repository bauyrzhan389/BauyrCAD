"""Application entry point."""

from __future__ import annotations

import os
import sys


def selftest(log_path: str | None = None) -> int:
    """Headless end-to-end check (model -> STEP -> mesh -> solve -> render).

    Used to verify frozen builds: ``BauyrCAD.exe --selftest [log.txt]``.
    """
    import tempfile
    import traceback

    lines = []
    try:
        import numpy as np
        from PySide6.QtWidgets import QApplication

        from .core import geometry
        from .core.document import Document
        from .core.features import ExtrudeFeature, SketchFeature
        from .core.plane import FRONT
        from .fea.analysis import run_study
        from .fea.study import FaceRef, FixedFace, PointForce

        doc = Document()
        s = doc.add_feature(SketchFeature(FRONT))
        s.sketch.add_rectangle((0, 0), (10, 10))
        doc.add_feature(ExtrudeFeature(s.id, 100))
        doc.regenerate()
        lines.append(f"volume {geometry.volume(doc.body):.3f} (expected 10000)")
        step = os.path.join(tempfile.gettempdir(), "bauyrcad_selftest.step")
        doc.export_step(step)
        d2 = Document()
        d2.import_step(step)
        d2.regenerate()
        lines.append(f"step roundtrip volume {geometry.volume(d2.body):.3f}")
        st = doc.add_study()
        root = next(f for f in doc.faces if f.is_planar and f.normal[2] < -0.9)
        st.fixtures.append(FixedFace("bc1", "Fixed-1", [FaceRef.from_face(root)]))
        st.loads.append(PointForce("bc2", "Force-1", [5, 5, 100], [0, -100, 0]))
        res = run_study(st, doc)
        summ = res.summary()
        lines.append(f"fea max displacement {summ['max_displacement']:.5f} mm (expected ~0.19), "
                     f"solver {summ['method']}")

        # sketch solver, arcs/slots/splines, and every exchange format
        from .core import exchange
        from .core.sketch import Constraint, builders, constraints, ops, solver
        d3 = Document()
        sf = d3.add_feature(SketchFeature(FRONT))
        sk = sf.sketch
        L = builders.corner_rectangle(sk, (0, 0), (60, 30))
        c = Constraint(0, "distance", [("e", L[0].id)])
        c.value = constraints.measure(sk, c)
        solver.try_add(sk, c)
        solver.set_dimension(sk, c, 80.0)
        ops.fillet(sk, L[0].p2, 5)
        builders.straight_slot(sk, (20, 15), (45, 15), (20, 19))
        builders.spline(sk, [(5, 5), (8, 10), (12, 6)])[0].construction = True
        d3.add_feature(ExtrudeFeature(sf.id, 6))
        d3.regenerate()
        ok_sketch = d3.features[1].error is None and abs(
            float(np.linalg.norm(sk.pt(L[0].p1) - sk.pt(L[2].p2))) - 30) < 1e-6
        lines.append(f"sketch solver + profiles {'ok' if ok_sketch else 'FAILED'}, "
                     f"volume {geometry.volume(d3.body):.2f}")
        # revolve + assembly with mates
        import math
        from .api import Assembly, Part
        tmp = tempfile.mkdtemp()
        shaft = Part("shaft")
        sk2 = shaft.sketch("front")
        sk2.centerline((0, 0), (0, 10))
        sk2.rect((0, 0), (5, 40))
        shaft.revolve(sk2)
        ok_rev = abs(shaft.volume() - math.pi * 25 * 40) < 1e-6 * shaft.volume()
        plate = Part("plate")
        sk3 = plate.sketch("front")
        sk3.rect((0, 0), (40, 30))
        sk3.circle((20, 15), d=10)
        plate.extrude(sk3, 8)
        shaft.save(os.path.join(tmp, "shaft.bcad"))
        plate.save(os.path.join(tmp, "plate.bcad"))
        asm = Assembly()
        pl = asm.add(os.path.join(tmp, "plate.bcad"))
        sh = asm.add(os.path.join(tmp, "shaft.bcad"))
        asm.mate("concentric", sh.face(kind="cylinder"), pl.face(kind="cylinder"))
        asm.mate("coincident", sh.face("-y"), pl.face("+z"))
        ok_asm = asm.dof()["shaft<1>"] == 1 and abs(sh.world((0, 0, 0))[2] - 8) < 1e-6
        lines.append(f"revolve {'ok' if ok_rev else 'FAILED'}, assembly mates {'ok' if ok_asm else 'FAILED'}")
        ok_sketch = ok_sketch and ok_rev and ok_asm
        ring = Part("external-axis")
        axis_sk = ring.sketch("front")
        axis_line = axis_sk.line((0, -10), (0, 40))
        profile = ring.sketch("front")
        profile.rect((10, 0), (20, 30))
        ring.revolve(profile, axis=axis_line)
        ring_path = ring.save(os.path.join(tmp, "external-axis.bcad"))
        ring = Part.open(ring_path)
        ok_axis = abs(ring.volume() - math.pi * 300 * 30) < 1e-6 * ring.volume()
        lines.append(f"external sketch revolve axis {'ok' if ok_axis else 'FAILED'}")
        ok_sketch = ok_sketch and ok_axis
        # applied / advanced features (fillet, Hole Wizard, pattern, shell, sweep, text)
        feat = Part("features")
        s4 = feat.sketch("front")
        s4.rect((0, 0), (60, 40))
        feat.extrude(s4, 10)
        feat.fillet(feat.edges_where(direction="z"), r=4)
        h = feat.hole(feat.face("+z"), [(12, 12)], size="M5", kind="counterbore")
        feat.pattern_linear(h, "x", 18, 3)
        feat.shell(1.5, remove=feat.face("-z"))
        tx = feat.sketch(on=feat.face("+z"))
        tx.text("BC", at=(20, 25), height=5)
        feat.cut(tx, 0.4)
        spring = Part("spring")
        spring.sweep(spring.helix(center=(0, 0, 0), axis="y", d=20, pitch=5, turns=3), d=2)
        ok_feat = (all(f.error is None for f in feat.doc.features) and feat.volume() > 0
                   and abs(spring.volume() - math.pi / 4 * 4 * 3 * math.hypot(math.pi * 20, 5))
                   < 5e-3 * spring.volume())
        lines.append(f"features (fillet, hole, pattern, shell, text, sweep) {'ok' if ok_feat else 'FAILED'}")
        ok_sketch = ok_sketch and ok_feat
        wrote = []
        for f in exchange.EXPORT_FORMATS:
            p = os.path.join(tmp, f"part_{f.key}{f.extensions[0]}")
            d3.export(p, f.key)
            wrote.append(f.key if os.path.getsize(p) > 200 else f"{f.key}(EMPTY)")
        lines.append("exported: " + ", ".join(wrote))
        ok_exports = "EMPTY" not in lines[-1]
        # STEP assembly structure, and a drawing in every drawing format
        asm.save(os.path.join(tmp, "a.bcasm"))
        asm.export(os.path.join(tmp, "a.step"))
        st_asm = exchange.read_step_structure(os.path.join(tmp, "a.step"))
        ok_step_asm = len(st_asm["instances"]) == 2 and st_asm["parts"] == ["plate", "shaft"]
        # SolidWorks 2024 files written without SolidWorks (needs the bundled seed files)
        from .core import sldprt, swfile
        sw_tree = [f.name for f in sldprt.feature_tree(os.path.join(tmp, "part_sldprt.sldprt"))]
        asm.export(os.path.join(tmp, "sw", "a.SLDASM"))
        sw_comps = swfile.assembly_components(os.path.join(tmp, "sw", "a.SLDASM"))
        ok_swfiles = sw_tree == ["Imported1"] and len(sw_comps) == 2
        lines.append(f"SolidWorks .SLDPRT/.SLDASM without SolidWorks {'ok' if ok_swfiles else 'FAILED'}")
        ok_step_asm = ok_step_asm and ok_swfiles
        from .api import Drawing
        drw = Drawing(os.path.join(tmp, "plate.bcad"))
        drw.auto_dimension("Front")
        dwrote = []
        for ext in ("pdf", "dxf", "svg", "png"):
            p = drw.export(os.path.join(tmp, f"drawing.{ext}"))
            dwrote.append(ext if os.path.getsize(p) > 2000 else f"{ext}(EMPTY)")
        import ezdxf
        ndim = len(ezdxf.readfile(os.path.join(tmp, "drawing.dxf")).modelspace().query("DIMENSION"))
        ok_drw = ndim == 3 and all("EMPTY" not in x for x in dwrote)
        bom_drw = Drawing(os.path.join(tmp, "a.bcasm"))
        bom_drw.bom()
        bom_drw.balloon("Front", pl, (0, 0, 0), (40, 200))
        bom_path = bom_drw.save(os.path.join(tmp, "bom.bcdrw"))
        bom_drw = Drawing.open(bom_path)
        bom_drw.export(os.path.join(tmp, "bom.dxf"))
        bom_drw.export(os.path.join(tmp, "bom.pdf"))
        bom_text = [e.dxf.text for e in ezdxf.readfile(os.path.join(tmp, "bom.dxf")).modelspace().query("TEXT")]
        ok_bom = (len(bom_drw.dr.bom.rows) == 2 and "PART NUMBER" in bom_text
                  and bom_drw.dr.balloon_target(bom_drw.dr.balloons[0])[0] == "1")
        lines.append(f"assembly BOM + balloon persistence/PDF/DXF {'ok' if ok_bom else 'FAILED'}")
        ok_drw = ok_drw and ok_bom
        lines.append(f"step assembly {'ok' if ok_step_asm else 'FAILED'}, drawing: "
                     f"{', '.join(dwrote)}, {ndim} dimensions {'ok' if ok_drw else 'FAILED'}")
        ok_exports = ok_exports and ok_step_asm and ok_drw
        # Exercise the actual bundled native writer, including annotation and finishing
        # donors. This must work without a SolidWorks connection in the frozen app.
        from .core import sldprt
        offline_part = Part("offline-fillet")
        offline_sk = offline_part.sketch('front')
        offline_lines = offline_sk.rect((0, 0), (40, 20))
        offline_sk.dim(offline_lines[0], 40)
        offline_part.extrude(offline_sk, 10)
        offline_part.fillet([(0, 0, 5)], r=2)
        face_part = Part('offline-face-sketch')
        face_sk = face_part.sketch('front')
        face_sk.rect((0, 0), (40, 20))
        face_part.extrude(face_sk, 10)
        face_sk = face_part.sketch(on=face_part.face('+x'))
        face_sk.rect((-7, 8), (-3, 12))
        face_part.extrude(face_sk, 5)
        face_cut = Part('offline-cap-cut')
        face_sk = face_cut.sketch('front')
        face_sk.rect((0, 0), (40, 20))
        face_cut.extrude(face_sk, 10, midplane=True)
        face_sk = face_cut.sketch(on=face_cut.face('+z'))
        face_sk.rect((8, 6), (12, 10))
        face_cut.cut(face_sk, through=True)
        slot_part = Part('offline-slot')
        slot_sk = slot_part.sketch('front')
        slot_entities = slot_sk.slot((3, 5), (37, 16), 9)
        slot_sk.dim(slot_entities[0])
        slot_sk.dim(slot_entities[1], 4.5)
        slot_part.extrude(slot_sk, 11)
        polygon_part = Part('offline-polygon')
        polygon_sk = polygon_part.sketch('top')
        polygon_sk.polygon((12, 17), 20, sides=5, angle=17)
        polygon_sk.dim(next(e for e in polygon_sk.sk.entities.values() if e.kind == 'circle'))
        polygon_part.extrude(polygon_sk, 11)
        plane_part = Part('offline-reference-planes')
        offset_plane = plane_part.plane('right', offset=-18)
        middle_plane = plane_part.plane(mid=('right', offset_plane))
        coincident_plane = plane_part.plane(middle_plane, offset=0)
        plane_sk = plane_part.sketch(coincident_plane)
        plane_sk.rect((3, 5), (37, 23))
        plane_part.extrude(plane_sk, 11)
        shared_part = Part('offline-shared-fixed-profile')
        shared_sk = shared_part.sketch('top')
        shared_lines = shared_sk.rect((3, 5), (43, 27))
        shared_sk.relate('fix', shared_lines[0])
        shared_sk.dim(shared_lines[1])
        shared_part.extrude(shared_sk, 11)
        shared_part.extrude(shared_sk, 7, reverse=True)
        shell_part = Part('offline-shell')
        shell_sk = shell_part.sketch('right')
        shell_sk.rect((3, 5), (43, 27))
        shell_part.extrude(shell_sk, 18)
        shell_part.shell(2, remove=shell_part.face('+x'), outward=True)
        assert shell_part.solidworks_compatibility()['ready']
        for offline_index, offline_model in enumerate((offline_part, shaft, face_part, face_cut,
                                                       slot_part, polygon_part, plane_part, shared_part, shell_part)):
            offline_path = os.path.join(tmp, f'offline_{offline_index}.SLDPRT')
            offline_report = offline_model.export_solidworks_offline(offline_path, require_native=True)
            assert offline_report['native']
            assert not sldprt.tree_mismatches(offline_path,
                [f.name for f in offline_model.doc.features], exact=True)
        lines.append('offline editable SolidWorks export + compatibility check + bundled sketch/fillet/revolve/face/reference-plane/shell donors ok')
        # SolidWorks export: bridge present; a real export only when asked (it runs SolidWorks)
        from . import solidworks
        sw_line = "solidworks bridge ok, SolidWorks " + ("found" if solidworks.available() else "not installed")
        if os.environ.get("BAUYRCAD_SELFTEST_SW") and solidworks.available():
            rep = solidworks.export_part_file(d3, os.path.join(tmp, "selftest.SLDPRT"))
            ok_sw = rep["native"] and os.path.exists(rep["path"])
            sw_line += f", native export {'ok' if ok_sw else 'FAILED'} ({', '.join(rep['features'])})"
            ok_exports = ok_exports and ok_sw
        lines.append(sw_line)
        app = QApplication.instance() or QApplication(sys.argv[:1])
        from .gui.parameters_dialog import ParametersDialog
        from .api import Part
        parameter_part = Part()
        parameter_sketch = parameter_part.sketch()
        parameter_sketch.rect((0, 0), (10, 10))
        parameter_boss = parameter_part.extrude(parameter_sketch, 5)
        parameter_part.parameter("thickness", "8 mm")
        parameter_part.bind_parameter(parameter_boss, "depth", "thickness")
        assert abs(parameter_part.volume() - 800) < 1e-6
        parameter_dialog = ParametersDialog(parameter_part.doc)
        assert parameter_dialog.validate()
        parameter_dialog.close()
        lines.append("named parameters and equation dialog ok")
        from .gui.main_window import MainWindow
        win = MainWindow()
        win.doc = doc
        win.show()
        win.refresh_view()
        app.processEvents()
        win.viewer.render()
        lines.append("gui ok")
        win._set_document(parameter_part.doc)
        win.edit_feature(parameter_boss.id)
        win.command.equations.open("depth")
        assert win.command.equations.editor.on_ok("=thickness / 2")
        win.command_ok()
        assert abs(parameter_part.volume() - 400) < 1e-6
        lines.append("feature equation editing ok")
        win._set_document(doc)
        v = win.viewer
        x, y = np.array(v.renderer.GetSize()) / 2
        pivot = v.rotation_pivot(x, y)
        before = v.world_to_display(pivot)
        v._orbit_pivot = pivot
        v._orbit_last = (x, y)
        v._orbit_move(x + 30, y + 20)
        ok_orbit = np.allclose(v.world_to_display(pivot), before, atol=0.05)
        v._orbit_pivot = None
        lines.append(f"cursor rotation pivot {'ok' if ok_orbit else 'FAILED'}")
        win.dirty = False
        win.close()
        # the bundled Intel MKL must load: PARDISO is the solver big models depend on
        ok_pardiso = summ["method"] == "PARDISO"
        if not ok_pardiso:
            lines.append("PARDISO solver NOT available (MKL runtime missing from the build)")
        ok = (0.18 < summ["max_displacement"] < 0.21 and ok_sketch and ok_exports
              and ok_pardiso and ok_orbit)
        lines.append("SELFTEST PASSED" if ok else "SELFTEST FAILED")
        code = 0 if ok else 1
    except Exception:
        lines.append(traceback.format_exc())
        lines.append("SELFTEST FAILED")
        code = 1
    text = "\n".join(lines)
    if log_path:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    return code


def main(argv=None):
    argv = sys.argv if argv is None else argv
    from .cli import COMMANDS
    if len(argv) > 1 and argv[1] in COMMANDS:
        from .cli import main as cli_main
        return cli_main(argv[1:])
    if "--selftest" in argv:
        i = argv.index("--selftest")
        return selftest(argv[i + 1] if len(argv) > i + 1 else None)
    os.environ.setdefault("QT_API", "pyside6")
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME
    from .gui.icons import icon
    from .gui.main_window import MainWindow

    if sys.platform == "win32":
        try:  # own taskbar icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BauyrCAD.App")
        except Exception:
            pass

    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setWindowIcon(icon("app"))
    win = MainWindow()
    win.show()
    if len(argv) > 1 and os.path.isfile(argv[1]):
        win.open_initial(argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
