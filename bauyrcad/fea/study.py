"""Simulation study definitions (what to analyse, not how).

Units are consistent millimetre-newton-MPa: lengths in mm, forces in N,
Young's modulus and stresses in MPa. Density is kept in kg/m^3 for display.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass
class Material:
    name: str
    E: float                 # Young's modulus [MPa]
    nu: float                # Poisson's ratio [-]
    yield_strength: float    # [MPa]
    density: float           # [kg/m^3]
    ultimate_strength: float = 0.0   # [MPa], 0 = unknown

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Material":
        return cls(**d)


MATERIAL_LIBRARY: dict[str, Material] = {m.name: m for m in [
    # name, E [MPa], nu, yield [MPa], density [kg/m3], ultimate [MPa]
    Material("Plain Carbon Steel", 210000.0, 0.28, 220.6, 7800.0, 399.8),
    Material("AISI 1020 Steel", 200000.0, 0.29, 351.6, 7900.0, 420.5),
    Material("AISI 304 Stainless", 193000.0, 0.29, 206.8, 8000.0, 517.0),
    # EN 10025 structural steels (thickness <= 16 mm)
    Material("S235JR (EN 10025)", 210000.0, 0.30, 235.0, 7850.0, 360.0),
    Material("S275JR (EN 10025)", 210000.0, 0.30, 275.0, 7850.0, 430.0),
    Material("S355J2 (EN 10025)", 210000.0, 0.30, 355.0, 7850.0, 510.0),
    # GOST steels
    Material("St3sp / Ст3сп (GOST 380)", 200000.0, 0.30, 245.0, 7850.0, 370.0),
    Material("09G2S / 09Г2С (GOST 19281)", 206000.0, 0.30, 345.0, 7850.0, 490.0),
    Material("Steel 45 / Сталь 45 (GOST 1050, normalised)", 200000.0, 0.30, 355.0, 7826.0, 600.0),
    Material("40Kh / 40Х (GOST 4543, Q&T)", 214000.0, 0.30, 785.0, 7820.0, 980.0),
    Material("12Kh18N10T / 12Х18Н10Т (GOST 5632)", 198000.0, 0.30, 196.0, 7900.0, 510.0),
    # aluminium
    Material("Aluminium 6061-T6", 69000.0, 0.33, 275.0, 2700.0, 310.0),
    Material("Aluminium 7075-T6", 72000.0, 0.33, 505.0, 2810.0, 570.0),
    Material("AMg6 / АМг6 (GOST 4784)", 71000.0, 0.33, 160.0, 2640.0, 320.0),
    Material("D16T / Д16Т (GOST 4784)", 72000.0, 0.33, 290.0, 2780.0, 440.0),
    # other
    Material("Titanium Ti-6Al-4V", 113800.0, 0.342, 880.0, 4430.0, 950.0),
    Material("Copper", 110000.0, 0.37, 258.6, 8900.0, 394.8),
    Material("ABS", 2000.0, 0.394, 40.0, 1020.0, 40.0),
    Material("Nylon 6/6", 2800.0, 0.39, 60.0, 1140.0, 75.0),
]}
DEFAULT_MATERIAL = "Plain Carbon Steel"


@dataclass
class FaceRef:
    """Reference to a model face that survives feature-tree regeneration.

    Face indices change when the model is rebuilt, so the face is also
    remembered by its geometric signature (centroid, area) and re-found by
    matching on that.
    """

    index: int
    centroid: list
    area: float
    kind: str | None = None          # plane / cylinder / ... (newer files)
    normal: list | None = None       # planar faces
    radius: float | None = None      # cylinders

    def match(self, faces):
        """Exact/moved/ambiguous/missing, with compatible candidate scores."""
        from ..core.reference_match import choose
        size = np.sqrt(max(self.area, 1e-12))
        candidates = []
        for f in faces:
            if self.kind is not None and f.kind != self.kind:
                continue
            if self.normal is not None and (f.normal is None or
                    np.dot(f.normal, self.normal) < 1 - 1e-6):
                continue
            if self.radius is not None and (f.radius is None or
                    abs(f.radius - self.radius) > 0.25 * self.radius):
                continue
            distance = float(np.linalg.norm(np.asarray(f.centroid) - self.centroid))
            relative_area = abs(f.area - self.area) / max(self.area, 1e-12)
            exact = distance + relative_area * size <= 1e-3 * size + 1e-6
            if self.radius is not None:
                exact = exact and abs(f.radius - self.radius) <= 1e-3 * size + 1e-6
            score = distance / size + abs(np.log(max(f.area, 1e-12) / max(self.area, 1e-12)))
            candidates.append({"index": f.index, "score": float(score), "exact": bool(exact)})
        return choose(candidates, allow_moved=self.kind is not None)

    def resolve(self, faces) -> int | None:
        return self.match(faces)["index"]

    @classmethod
    def from_face(cls, info) -> "FaceRef":
        return cls(info.index, info.centroid.tolist(), float(info.area), info.kind,
                   None if info.normal is None else np.asarray(info.normal, float).tolist(),
                   None if info.radius is None else float(info.radius))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class FixedFace:
    id: str
    name: str
    faces: list[FaceRef] = field(default_factory=list)
    kind = "fixed"

    def to_dict(self):
        return {"type": "fixed", "id": self.id, "name": self.name,
                "faces": [f.to_dict() for f in self.faces]}


@dataclass
class PointForce:
    id: str
    name: str
    point: list                         # [x, y, z] mm
    force: list                         # [Fx, Fy, Fz] N
    kind = "point_force"

    @property
    def magnitude(self) -> float:
        return float(np.linalg.norm(self.force))

    def to_dict(self):
        return {"type": "point_force", "id": self.id, "name": self.name,
                "point": list(self.point), "force": list(self.force)}


@dataclass
class FaceForce:
    """Total force vector spread uniformly (per unit area) over one or more faces."""
    id: str
    name: str
    faces: list[FaceRef] = field(default_factory=list)
    force: list = field(default_factory=lambda: [0.0, 0.0, 0.0])   # [N] total
    kind = "face_force"

    @property
    def magnitude(self) -> float:
        return float(np.linalg.norm(self.force))

    def to_dict(self):
        return {"type": "face_force", "id": self.id, "name": self.name,
                "faces": [f.to_dict() for f in self.faces], "force": list(self.force)}


@dataclass
class Pressure:
    """Uniform pressure [MPa] normal to faces; positive pushes into the material."""
    id: str
    name: str
    faces: list[FaceRef] = field(default_factory=list)
    pressure: float = 0.0
    kind = "pressure"

    @property
    def magnitude(self) -> float:
        return abs(self.pressure)

    def to_dict(self):
        return {"type": "pressure", "id": self.id, "name": self.name,
                "faces": [f.to_dict() for f in self.faces], "pressure": self.pressure}


@dataclass
class MeshControl:
    """Local element size on faces."""
    faces: list[FaceRef] = field(default_factory=list)
    size: float = 1.0

    def to_dict(self):
        return {"faces": [f.to_dict() for f in self.faces], "size": self.size}


def _bc_from_dict(d):
    if d["type"] == "fixed":
        return FixedFace(d["id"], d["name"], [FaceRef.from_dict(f) for f in d["faces"]])
    if d["type"] == "point_force":
        return PointForce(d["id"], d["name"], d["point"], d["force"])
    if d["type"] == "face_force":
        return FaceForce(d["id"], d["name"], [FaceRef.from_dict(f) for f in d["faces"]], d["force"])
    if d["type"] == "pressure":
        return Pressure(d["id"], d["name"], [FaceRef.from_dict(f) for f in d["faces"]], d["pressure"])
    raise ValueError(f"Unknown boundary condition {d['type']!r}")


class Study:
    """Linear static study."""

    def __init__(self, id: str, name: str, material: Material | None = None):
        self.id = id
        self.name = name
        self.material = material or MATERIAL_LIBRARY[DEFAULT_MATERIAL]
        self.fixtures: list[FixedFace] = []
        self.loads: list = []           # PointForce | FaceForce | Pressure
        self.mesh_size: float = 0.0     # 0 -> automatic
        self.mesh_controls: list[MeshControl] = []
        self.element_order: int = 2
        self.results = None             # transient, not saved

    def all_bcs(self):
        return [*self.fixtures, *self.loads]

    def find(self, bc_id: str):
        return next((b for b in self.all_bcs() if b.id == bc_id), None)

    def remove(self, bc_id: str) -> None:
        self.fixtures = [b for b in self.fixtures if b.id != bc_id]
        self.loads = [b for b in self.loads if b.id != bc_id]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "material": self.material.to_dict(),
                "fixtures": [f.to_dict() for f in self.fixtures],
                "loads": [l.to_dict() for l in self.loads],
                "mesh_size": self.mesh_size, "element_order": self.element_order,
                "mesh_controls": [m.to_dict() for m in self.mesh_controls]}

    @classmethod
    def from_dict(cls, d: dict) -> "Study":
        s = cls(d["id"], d["name"], Material.from_dict(d["material"]))
        s.fixtures = [_bc_from_dict(x) for x in d.get("fixtures", [])]
        s.loads = [_bc_from_dict(x) for x in d.get("loads", [])]
        s.mesh_size = d.get("mesh_size", 0.0)
        s.element_order = d.get("element_order", 2)
        s.mesh_controls = [MeshControl([FaceRef.from_dict(f) for f in m["faces"]], m["size"])
                           for m in d.get("mesh_controls", [])]
        return s
