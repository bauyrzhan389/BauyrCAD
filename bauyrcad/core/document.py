"""The part document: feature tree, studies, regeneration and file I/O."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import geometry
from .features import ExtrudeFeature, Feature, ImportFeature, RegenContext, SketchFeature
from ..fea.study import Study

FILE_FORMAT = "bauyrcad-part"
FILE_VERSION = 2
FILE_EXTENSION = ".bcad"


class Document:
    def __init__(self):
        self.features: list[Feature] = []
        self.studies: list[Study] = []
        self.path: Path | None = None
        self.part_number = ""
        self.description = ""
        self.parameters = {}
        self.parameter_bindings = []
        self.body = None
        self._faces = None
        self._tess = None

    # ---- ids & names -----------------------------------------------------
    def _new_id(self, prefix: str) -> str:
        existing = {f.id for f in self.features} | {s.id for s in self.studies}
        existing |= {b.id for s in self.studies for b in s.all_bcs()}
        n = 1
        while f"{prefix}{n}" in existing:
            n += 1
        return f"{prefix}{n}"

    def _new_name(self, prefix: str, names) -> str:
        pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        nums = [int(m.group(1)) for n in names if (m := pat.match(n))]
        return f"{prefix}{max(nums, default=0) + 1}"

    def add_feature(self, f: Feature) -> Feature:
        if not f.id:
            f.id = self._new_id("f")
        if not f.name:
            f.name = self._new_name(f.NAME_PREFIX, [x.name for x in self.features])
        self.features.append(f)
        return f

    def add_study(self, name: str | None = None) -> Study:
        s = Study(self._new_id("study"),
                  name or self._new_name("Static ", [x.name for x in self.studies]))
        self.studies.append(s)
        return s

    def new_bc_id(self) -> str:
        return self._new_id("bc")

    # ---- look-up ---------------------------------------------------------
    def feature(self, fid: str) -> Feature | None:
        return next((f for f in self.features if f.id == fid), None)

    def study(self, sid: str) -> Study | None:
        return next((s for s in self.studies if s.id == sid), None)

    def sketches(self) -> list[SketchFeature]:
        return [f for f in self.features if isinstance(f, SketchFeature)]

    def dependents(self, fid: str) -> list[Feature]:
        """Features that (directly or transitively) depend on feature fid."""
        out, frontier = [], {fid}
        for f in self.features:
            if frontier & set(f.depends_on()):
                out.append(f)
                frontier.add(f.id)
        return out

    def is_consumed(self, sketch_id: str) -> bool:
        return any(sketch_id in f.depends_on() for f in self.features if not f.suppressed)

    def remove_feature(self, fid: str) -> list[str]:
        """Remove a feature and everything depending on it. Returns removed ids."""
        ids = {fid} | {f.id for f in self.dependents(fid)}
        self.features = [f for f in self.features if f.id not in ids]
        self.parameter_bindings = [b for b in self.parameter_bindings if b["feature"] not in ids]
        return sorted(ids)

    def configure_parameters(self, parameters, bindings):
        """Validate a complete proposal before touching live geometry or parameters."""
        from copy import deepcopy
        from .parameters import ParameterError
        trial = Document.from_dict(self.to_dict())
        trial.parameters, trial.parameter_bindings = deepcopy(parameters), deepcopy(bindings)
        trial.regenerate()
        errors = [f"{f.name}: {f.error}" for f in trial.features if f.error]
        if errors:
            raise ParameterError("; ".join(errors))
        self.parameters, self.parameter_bindings = trial.parameters, trial.parameter_bindings
        self.regenerate()

    # ---- regeneration ----------------------------------------------------
    def regenerate(self, upto: int | None = None, record: list | None = None) -> RegenContext:
        """Replay features. With ``upto`` only features[:upto] are built. With ``record`` (a
        list) the body after each built feature is appended to it, so one forward pass gives
        the state before / after every step (record[i] = body after features[i])."""
        if self.parameters or self.parameter_bindings:
            from .parameters import apply_bindings
            apply_bindings(self)
        ctx = RegenContext()
        feats = self.features if upto is None else self.features[:upto]
        for f in feats:
            ctx.features[f.id] = f
            f.error = None
            f.warning = None
            f.tools = []
            if not f.suppressed:
                try:
                    f.execute(ctx)
                except Exception as e:  # keep going so later features can still build
                    f.error = str(e) or type(e).__name__
            if record is not None:
                record.append(ctx.body)
        if upto is None:
            self.body = ctx.body
            self._faces = None
            self._tess = None
            for s in self.studies:
                s.results = None
        return ctx

    def context_before(self, feature: Feature) -> RegenContext:
        """Regeneration state just before ``feature`` (used for editing/preview)."""
        idx = self.features.index(feature) if feature in self.features else len(self.features)
        ctx = self.regenerate(upto=idx)
        # Sketches after the insertion point are also needed for look-up.
        for f in self.features:
            ctx.features.setdefault(f.id, f)
        return ctx

    @property
    def faces(self) -> list[geometry.FaceInfo]:
        if self._faces is None:
            self._faces = [] if self.body is None else geometry.face_infos(self.body)
        return self._faces

    @property
    def tessellation(self) -> geometry.Tessellation | None:
        if self._tess is None and self.body is not None:
            self._tess = geometry.tessellate(self.body)
        return self._tess

    def has_body(self) -> bool:
        return self.body is not None and not geometry.is_null(self.body)

    # ---- serialization ---------------------------------------------------
    def to_dict(self) -> dict:
        return {"format": FILE_FORMAT, "version": FILE_VERSION if self.parameters or self.parameter_bindings else 1, "units": "mm",
                "part_number": self.part_number, "description": self.description,
                "parameters": self.parameters, "parameter_bindings": self.parameter_bindings,
                "features": [f.to_dict() for f in self.features],
                "studies": [s.to_dict() for s in self.studies]}

    @classmethod
    def from_dict(cls, d: dict) -> "Document":
        if d.get("format") != FILE_FORMAT:
            raise ValueError("Not a BauyrCAD part file")
        if d.get("version", 1) > FILE_VERSION:
            raise ValueError("This file was written by a newer version of BauyrCAD")
        doc = cls()
        doc.part_number = str(d.get("part_number", ""))
        doc.description = str(d.get("description", ""))
        from copy import deepcopy
        doc.parameters = deepcopy(d.get("parameters", {}))
        doc.parameter_bindings = deepcopy(d.get("parameter_bindings", []))
        doc.features = [Feature.from_dict(fd) for fd in d.get("features", [])]
        doc.studies = [Study.from_dict(sd) for sd in d.get("studies", [])]
        return doc

    def save(self, path) -> None:
        path = Path(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)
        self.path = path

    @classmethod
    def load(cls, path) -> "Document":
        with open(path, encoding="utf-8") as f:
            doc = cls.from_dict(json.load(f))
        doc.path = Path(path)
        doc.regenerate()
        return doc

    # ---- exchange formats ----------------------------------------------------
    def import_file(self, path) -> ImportFeature:
        """Import a STEP / IGES / BREP file as a new body feature."""
        from .exchange import import_format_for
        path = Path(path)
        fmt = import_format_for(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        feat = ImportFeature(path.name, text, fmt.key)
        feat.shape()  # parse now so errors surface immediately
        self.add_feature(feat)
        return feat

    import_step = import_file

    def export(self, path, fmt: str | None = None) -> None:
        """Export the body; fmt is a key of exchange.FORMATS (default: by extension)."""
        from .exchange import export
        name = self.path.stem if self.path else "Part1"
        export(self.body, path, fmt, name=name)

    def export_step(self, path) -> None:
        self.export(path, "step214")


__all__ = ["Document", "ExtrudeFeature", "SketchFeature", "ImportFeature", "FILE_EXTENSION"]
