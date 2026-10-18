"""Persisted, unit-checked arithmetic and bindings. No eval, GUI or kernel calls."""
import ast
import copy
import keyword
import math
import re
from dataclasses import dataclass


class ParameterError(ValueError):
    pass


@dataclass(frozen=True)
class Quantity:
    value: float
    dims: tuple = (0, 0)  # powers of length and angle; base units mm and radians


UNITS = {"mm": Quantity(1, (1, 0)), "cm": Quantity(10, (1, 0)),
         "m": Quantity(1000, (1, 0)), "inch": Quantity(25.4, (1, 0)),
         "deg": Quantity(math.pi / 180, (0, 1)), "rad": Quantity(1, (0, 1))}
KINDS = {"length": ((1, 0), 1), "angle": ((0, 1), math.pi / 180), "scalar": ((0, 0), 1)}
# Explicit allowlist: expressions must never overwrite arbitrary feature attributes.
FIELDS = {"extrude": {"depth": "length"}, "revolve": {"angle": "angle"},
          "fillet": {"radius": "length", "radius2": "length"},
          "chamfer": {"distance": "length", "distance2": "length", "angle": "angle"},
          "shell": {"thickness": "length"}, "draft": {"angle": "angle"},
          "linear_pattern": {"spacing": "length", "spacing2": "length", "count": "scalar", "count2": "scalar"},
          "circular_pattern": {"angle": "angle", "count": "scalar"},
          "rib": {"thickness": "length"},
          "ref_plane": {"distance": "length", "angle": "angle"},
          "hole": {"d": "length", "depth": "length", "cbore_d": "length",
                   "cbore_depth": "length", "csk_d": "length", "csk_angle": "angle"},
          "helix": {"diameter": "length", "pitch": "length", "height": "length",
                    "revolutions": "scalar", "taper": "angle", "start_angle": "angle"}}


def feature_fields(feature):
    """Only dimensions actually consumed by the selected feature mode."""
    fields = dict(FIELDS.get(feature.TYPE, {}))
    if feature.TYPE == "ref_plane":
        fields = {k: v for k, v in fields.items()
                  if k == {"offset": "distance", "angle": "angle"}.get(feature.mode)}
    elif feature.TYPE == "hole":
        if feature.end != "blind":
            fields.pop("depth", None)
        if feature.kind != "counterbore":
            for k in ("cbore_d", "cbore_depth"):
                fields.pop(k, None)
        if feature.kind != "countersink":
            for k in ("csk_d", "csk_angle"):
                fields.pop(k, None)
    elif feature.TYPE == "helix":
        fields.pop({"pitch_revolutions": "height", "height_revolutions": "pitch",
                    "height_pitch": "revolutions"}.get(feature.defined_by), None)
        if feature.sketch:
            fields.pop("diameter", None)
    return {k: v for k, v in fields.items() if getattr(feature, k, None) is not None}


def check_name(name):
    if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) or name in UNITS or name == "pi":
        raise ParameterError(f"Invalid or reserved parameter name: {name!r}")


def expression_tree(expression):
    text = str(expression).strip()
    if len(text) > 1000:
        raise ParameterError("Expression is too long")
    text = re.sub(r'(?<![\w.])((?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(mm|cm|inch|deg|rad|m)\b', r'\1*\2', text)
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ParameterError("Use numbers, parameter names, + - * / and parentheses") from exc
    if sum(1 for _ in ast.walk(tree)) > 200:
        raise ParameterError("Expression is too complex")
    return tree


def evaluate(expression, lookup):
    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Quantity(float(node.value))
        if isinstance(node, ast.Name):
            return UNITS[node.id] if node.id in UNITS else Quantity(math.pi) if node.id == "pi" else lookup(node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            q = visit(node.operand)
            return Quantity(q.value * (-1 if isinstance(node.op, ast.USub) else 1), q.dims)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if a.dims != b.dims:
                    raise ParameterError("Cannot add different units; write an explicit unit, such as 2 mm")
                return Quantity(a.value + (-b.value if isinstance(node.op, ast.Sub) else b.value), a.dims)
            if isinstance(node.op, ast.Mult):
                return Quantity(a.value * b.value, tuple(x + y for x, y in zip(a.dims, b.dims)))
            return Quantity(a.value / b.value, tuple(x - y for x, y in zip(a.dims, b.dims)))
        raise ParameterError("Only arithmetic is allowed; calls, attributes and indexing are not supported")
    try:
        result = visit(expression_tree(expression))
    except (ZeroDivisionError, OverflowError) as exc:
        raise ParameterError("Expression divides by zero or exceeds the numeric range") from exc
    if not math.isfinite(result.value):
        raise ParameterError("Expression must produce a finite value")
    return result


def typed(expression, kind, lookup):
    if kind not in KINDS:
        raise ParameterError(f"Unknown parameter type: {kind}")
    q = evaluate(expression, lookup)
    dims, factor = KINDS[kind]
    # Numeric expressions default to mm/degrees. Named scalars must not silently
    # become lengths or angles: a user must multiply them by a unit.
    names = {n.id for n in ast.walk(expression_tree(expression)) if isinstance(n, ast.Name)}
    if q.dims == (0, 0) and not names:
        q = Quantity(q.value * factor, dims)
    if q.dims != dims:
        raise ParameterError(f"Expected {kind}, but the expression has incompatible units")
    return q


def values(parameters):
    if len(parameters) > 200:
        raise ParameterError("At most 200 named parameters are supported")
    cache, pending, dependencies = {}, dict(parameters), {}
    for name, row in parameters.items():
        check_name(name)
        names = {n.id for n in ast.walk(expression_tree(row["expression"])) if isinstance(n, ast.Name)}
        dependencies[name] = names - set(UNITS) - {"pi"}
        unknown = dependencies[name] - parameters.keys()
        if unknown:
            raise ParameterError(f"Unknown parameter: {sorted(unknown)[0]}")
    # Resolve iteratively: even a 200-parameter forward chain must not consume
    # the Python call stack. A pass without a ready node identifies a cycle.
    while pending:
        ready = [name for name in pending if dependencies[name] <= cache.keys()]
        if not ready:
            raise ParameterError("Circular equation involving: " + ", ".join(pending))
        for name in ready:
            row = pending.pop(name)
            cache[name] = typed(row["expression"], row["kind"], cache.__getitem__)
    return cache


def parameter_value(parameters, expression, kind):
    """Evaluate an editor expression in base units (mm, radians or scalar)."""
    cache = values(parameters)
    def lookup(name):
        if name not in cache:
            raise ParameterError(f"Unknown parameter: {name}")
        return cache[name]
    return typed(expression, kind, lookup).value


def targets(doc):
    """(feature id, field, type, label), including existing driving sketch dimensions."""
    result = []
    for f in doc.features:
        for field, kind in feature_fields(f).items():
            if getattr(f, field, None) is not None:
                result.append((f.id, field, kind, f"{f.name}: {field}"))
        if f.TYPE == "sketch":
            for c in f.sketch.constraints:
                if c.driving and c.value is not None and c.type in {"distance", "hdistance", "vdistance", "point_line_distance", "diameter", "radius", "angle"}:
                    result.append((f.id, f"dimension:{c.id}", "angle" if c.type == "angle" else "length",
                                   f"{f.name}: {c.type} dimension {c.id}"))
    return result


def feature_value(field, kind, value, feature_type=None):
    """Convert a typed base-unit result to a validated feature-field value."""
    if field.startswith("count"):
        if value != int(value) or not 1 <= value <= 10000:
            raise ParameterError("Pattern count must be an integer from 1 to 10000")
        return int(value)
    positive = {"depth", "radius", "radius2", "distance", "distance2", "thickness",
                "d", "cbore_d", "cbore_depth", "csk_d", "diameter", "pitch", "height", "revolutions"}
    if feature_type == "ref_plane":
        positive.discard("distance")
    if field in positive and value <= 0:
        raise ParameterError(f"{field} must be positive")
    result = math.degrees(value) if kind == "angle" else value
    if field == "csk_angle" and not 0 < result < 180:
        raise ParameterError("Countersink angle must be between 0 and 180 degrees")
    if field == "taper" and not -90 < result < 90:
        raise ParameterError("Helix taper must be between -90 and 90 degrees")
    return result


def apply_bindings(doc):
    cache = values(doc.parameters)
    available = {(fid, field): kind for fid, field, kind, _ in targets(doc)}
    pending, seen = [], set()
    def lookup(name):
        if name not in cache:
            raise ParameterError(f"Unknown parameter: {name}")
        return cache[name]
    for binding in doc.parameter_bindings:
        key = binding["feature"], binding["field"]
        if key in seen:
            raise ParameterError("A dimension has more than one expression")
        seen.add(key)
        if key not in available:
            raise ParameterError(f"Missing or unsupported parameter target: {key}")
        q = typed(binding["expression"], available[key], lookup)
        value = q.value
        feature = doc.feature(key[0])
        if key[1].startswith("dimension:"):
            constraint = feature.sketch.constraint(int(key[1].split(":")[1]))
            if constraint.type in {"distance", "diameter", "radius"} and value <= 0:
                raise ParameterError("Lengths, diameters and radii must be positive")
        else:
            value = feature_value(key[1], available[key], value, feature.TYPE)
        pending.append((feature, key[1], value))
    sketches = {}
    for feature, field, value in pending:
        if field.startswith("dimension:"):
            feature.sketch.constraint(int(field.split(":")[1])).value = value
            sketches[feature.id] = feature
        else:
            setattr(feature, field, value)
    from .sketch.solver import solve
    for feature in sketches.values():
        if not solve(feature.sketch).ok:
            raise ParameterError(f"{feature.name}: dimensions cannot be solved with these values")


def renamed(parameters, bindings, old, new):
    check_name(new)
    if old not in parameters or new in parameters:
        raise ParameterError("Rename needs an existing name and an unused new name")
    def replace(expression):
        return re.sub(r'\b' + re.escape(old) + r'\b', new, str(expression))
    params = {new if name == old else name: {**row, "expression": replace(row["expression"])}
              for name, row in parameters.items()}
    return params, [{**b, "expression": replace(b["expression"])} for b in copy.deepcopy(bindings)]
