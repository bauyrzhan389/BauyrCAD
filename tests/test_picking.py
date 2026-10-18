import numpy as np
from bauyrcad.core.picking import ray_faces


def test_depth_order_duplicates_backfaces_and_misses():
    points = np.array(
        [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
         [0, 0, 3], [1, 0, 3], [1, 1, 3], [0, 1, 3]], float)
    triangles = [[0, 1, 2], [0, 2, 3], [6, 5, 4], [7, 6, 4]]
    hits = ray_faces(points, triangles, [4, 4, 8, 8], (.5, .5, 5), (.5, .5, 0))
    assert [h[0] for h in hits] == [8, 4]
    assert np.allclose([h[2][2] for h in hits], [3, 1])
    assert not ray_faces(points, triangles, [4, 4, 8, 8], (2, 2, 5), (2, 2, 0))
    assert not ray_faces(points, triangles, [4, 4, 8, 8], (0, 0, 5), (1, 1, 5))
    assert not ray_faces(points, triangles, [4, 4, 8, 8], (.5, .5, 5), (.5, .5, 4))
