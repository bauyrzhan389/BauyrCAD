"""Compare the interference broad phase with exhaustive box checks."""
from types import SimpleNamespace

import numpy as np
import pytest

from bauyrcad.core import assembly, geometry


@pytest.mark.parametrize("axis", [0, 1, 2])
def test_sweep_matches_exhaustive_pairs(monkeypatch, axis):
    rng = np.random.default_rng(42)
    lows = rng.uniform(-10, 10, (70, 3))
    lows[:, axis] *= 5
    highs = lows + rng.uniform(1, 12, (70, 3))
    # Include touching boxes and identical boxes; ignore an actual overlap.
    lows[1], highs[1] = lows[0], highs[0]
    lows[2], highs[2] = lows[0], highs[0]
    lows[2, axis] = highs[0, axis]
    highs[2, axis] = lows[2, axis] + 5
    boxes = dict(enumerate(zip(lows, highs)))
    calls = []
    monkeypatch.setattr(geometry, "bounding_box", boxes.__getitem__)
    def common(a, b):
        calls.append((a, b))
        return float(np.prod(np.maximum(0, np.minimum(highs[a], highs[b]) - np.maximum(lows[a], lows[b]))))
    monkeypatch.setattr(geometry, "common_volume", common)
    comps = [SimpleNamespace(id=i, shape=i, world_shape=lambda i=i: i) for i in range(70)]
    model = SimpleNamespace(components=comps)
    expected = []
    for a in range(70):
        for b in range(a + 1, 70):
            if (a, b) == (0, 1):
                continue
            volume = common(a, b)
            if volume > 1e-3:
                expected.append({"a": a, "b": b, "volume": volume})
    calls.clear()
    actual = assembly.Assembly.interference(model, ignore=[(0, 1)])
    assert actual == sorted(expected, key=lambda hit: -hit["volume"])
    assert len(calls) < 70 * 69 // 2


def test_empty_and_single_component(monkeypatch):
    monkeypatch.setattr(geometry, "bounding_box", lambda _: (np.zeros(3), np.ones(3)))
    for comps in ([], [SimpleNamespace(id=0, shape=0, world_shape=lambda: 0)]):
        assert assembly.Assembly.interference(SimpleNamespace(components=comps)) == []
