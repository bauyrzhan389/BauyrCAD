"""Generate the example projects in this folder (also a scripting-API example).

    python examples/make_examples.py
"""

from pathlib import Path

from bauyrcad.core.document import Document
from bauyrcad.core.features import ExtrudeFeature, SketchFeature
from bauyrcad.core.plane import FRONT, SketchPlane
from bauyrcad.fea.analysis import run_study
from bauyrcad.fea.study import MATERIAL_LIBRARY, FaceRef, FixedFace, PointForce

HERE = Path(__file__).parent


def bracket() -> Document:
    """Aluminium plate with a bolt hole and a slot, fixed at one end, loaded at the other."""
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    s.sketch.add_rectangle((0, 0), (120, 40))
    s.sketch.add_circle((100, 20), 6)
    doc.add_feature(ExtrudeFeature(s.id, 10))
    doc.regenerate()

    # pocket cut from the front face (z = 10), 4 mm deep
    top = next(f for f in doc.faces if f.is_planar and f.normal[2] > 0.9)
    s2 = doc.add_feature(SketchFeature(SketchPlane.from_face([0, 0, 10], top.normal)))
    s2.sketch.add_rectangle((25, 10), (70, 30))
    doc.add_feature(ExtrudeFeature(s2.id, 4, "cut"))
    doc.regenerate()

    st = doc.add_study("Bracket static")
    st.material = MATERIAL_LIBRARY["Aluminium 6061-T6"]
    root = next(f for f in doc.faces if f.is_planar and f.normal[0] < -0.9)
    st.fixtures.append(FixedFace(doc.new_bc_id(), "Fixed-1", [FaceRef.from_face(root)]))
    st.loads.append(PointForce(doc.new_bc_id(), "Force-1", [120, 40, 10], [0, -200, 0]))
    return doc


def cantilever() -> Document:
    """Verification case: 10 x 10 x 100 steel bar, 100 N tip load (δ ≈ 0.19 mm)."""
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    s.sketch.add_rectangle((0, 0), (10, 10))
    doc.add_feature(ExtrudeFeature(s.id, 100))
    doc.regenerate()
    st = doc.add_study("Cantilever check")
    root = next(f for f in doc.faces if f.is_planar and f.normal[2] < -0.9)
    st.fixtures.append(FixedFace(doc.new_bc_id(), "Fixed-1", [FaceRef.from_face(root)]))
    st.loads.append(PointForce(doc.new_bc_id(), "Tip load", [5, 5, 100], [0, -100, 0]))
    return doc


if __name__ == "__main__":
    for name, make in (("bracket", bracket), ("cantilever", cantilever)):
        doc = make()
        doc.save(HERE / f"{name}.bcad")
        doc.export_step(HERE / f"{name}.step")
        s = run_study(doc.studies[0], doc).summary()
        print(f"{name}: max von Mises {s['max_von_mises']:.1f} MPa, "
              f"max displacement {s['max_displacement']:.4f} mm")
