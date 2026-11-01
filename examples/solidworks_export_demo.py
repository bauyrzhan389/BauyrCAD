"""Build the README demo and export an editable SLDPRT without starting SolidWorks.

Run: python examples/solidworks_export_demo.py --output demo-output
Units: millimetres. SolidWorks is only needed to open and edit the exported file.
"""
from pathlib import Path
import argparse
import json
from bauyrcad.api import Part

WIDTH, HEIGHT, PLATE = 80.0, 45.0, 8.0
BOSS_DIAMETER, BOSS_HEIGHT, HOLE_DIAMETER = 28.0, 20.0, 12.0


def build():
    part = Part('Mounting plate')
    sk = part.sketch('front')
    lines = sk.rect((0, 0), (WIDTH, HEIGHT))
    sk.dim(lines[0], WIDTH)
    sk.dim(lines[1], HEIGHT)
    part.extrude(sk, PLATE)
    sk = part.sketch('front')
    c = sk.circle((WIDTH / 2, HEIGHT / 2), d=BOSS_DIAMETER)
    sk.dim(c, BOSS_DIAMETER)
    part.extrude(sk, BOSS_HEIGHT)
    sk = part.sketch('front')
    c = sk.circle((WIDTH / 2, HEIGHT / 2), d=HOLE_DIAMETER)
    sk.dim(c, HOLE_DIAMETER)
    part.cut(sk, through=True)
    return part


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('demo-output'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    part = build()
    part.save(args.output / 'mounting_plate.bcad')
    report = part.export_solidworks_offline(
        args.output / 'mounting_plate.SLDPRT', require_native=True)
    assert report['native'] and not report['warnings']
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
