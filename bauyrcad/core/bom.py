"""Flat, source-file based assembly BOMs. No geometry or GUI dependencies."""
from dataclasses import asdict, dataclass, field
import os


def assembly_rows(assembly):
    """Group by canonical full source path, never display part number or basename."""
    rows = {}
    instances = {}
    for c in assembly.components:
        path = c.resolve_path(assembly.base_dir).resolve()
        key = os.path.normcase(str(path))
        row = rows.setdefault(key, dict(key=key, part_number=(c.doc.part_number if c.doc else "")
                                       or path.stem,
                                       description=c.doc.description if c.doc else "",
                                       quantity=0, components=[], status=""))
        row["quantity"] += 1
        row["components"].append(c.id)
        if c.load_error or c.load_warning:
            row["status"] = c.load_error or c.load_warning
        instances[c.id] = dict(key=key, instance_id=c.instance_id, name=c.name,
                               pose=c.pose.to_dict(), status=c.load_error or c.load_warning or "")
    return list(rows.values()), instances


@dataclass
class BOMTable:
    id: str
    x: float = 225.0
    y: float = 275.0
    rows: list = field(default_factory=list)
    numbers: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)
    instances: dict = field(default_factory=dict)

    def refresh(self, assembly):
        rows, instances = assembly_rows(assembly)
        for row in rows:
            key = row["key"]
            if key not in self.numbers:
                self.numbers[key] = max(self.numbers.values(), default=0) + 1
            row["item"] = self.numbers[key]
        self.rows = sorted(rows, key=lambda r: r["item"])
        self.instances = instances
        return self.rows

    def edit(self, item, *, part_number=None, description=None):
        row = next((r for r in self.rows if r["item"] == item), None)
        if row is None:
            raise ValueError(f"No BOM item {item}")
        edits = self.overrides.setdefault(row["key"], {})
        for name, value in (("part_number", part_number), ("description", description)):
            if value is not None:
                edits[name] = str(value)

    def displayed(self, row):
        return dict(row, **self.overrides.get(row["key"], {}))

    def to_dict(self):
        return asdict(self)


@dataclass
class Balloon:
    id: str
    view: str
    component: str
    instance_id: str
    key: str
    anchor: tuple                 # component-local model point, mm
    x: float                     # bubble centre, sheet mm
    y: float

    def to_dict(self):
        return asdict(self)
