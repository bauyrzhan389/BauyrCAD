"""Constraint-based 2D sketcher (SolidWorks-style).

    model.py        points / curves / constraints data model and serialization
    constraints.py  relation and dimension equations
    solver.py       Newton constraint solver, drag, DOF analysis, over-definition checks
    builders.py     drawing tools with automatic relations
    ops.py          trim, extend, fillet, chamfer, offset, mirror, move/rotate, patterns
    profiles.py     closed-profile (region) detection for features
"""

from . import builders, constraints, ops, solver
from .model import (ORIGIN, TOL, Arc, Circle, Constraint, Entity, Line, PointEntity, Sketch,
                    SketchError, SPoint, Spline)
from .profiles import ArcSeg, CircleSeg, LineSeg, Loop, Region, SplineSeg

__all__ = ["Sketch", "SketchError", "Line", "Circle", "Arc", "Spline", "PointEntity", "SPoint",
           "Entity", "Constraint", "ORIGIN", "TOL", "Loop", "Region", "LineSeg", "ArcSeg",
           "CircleSeg", "SplineSeg", "builders", "constraints", "ops", "solver"]
