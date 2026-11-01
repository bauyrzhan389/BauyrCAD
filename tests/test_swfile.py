"""SolidWorks 2024 parts and assemblies written by BauyrCAD itself (bauyrcad/core/swfile.py):
the container, the body stream, the patched seed assemblies, and the files as BauyrCAD's own
SLDPRT reader sees them. SolidWorks opening them: tests/test_sw_swfile.py."""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from bauyrcad.core import parasolid as px, sldprt, swfile, xt_brep
from parasolid_models import build, pegboard, spline_shell

DATA = Path(__file__).parent / "data" / "sw"


@pytest.mark.parametrize("name", sorted(p.name for p in DATA.glob("*.SLD*")))
def test_container_rebuilds_solidworks_files_byte_for_byte(name):
    data = (DATA / name).read_bytes()
    c = swfile.Container(data)
    assert c.build() == data
    secs = sldprt.read_sections(data)
    assert {n for n in c.names() if n in secs} == set(secs)
    assert all(c.get(n) == secs[n] for n in secs)


def test_a_written_part_is_an_imported_body(tmp_path):
    """The seed's tree (one Imported feature) with our body in LocalBodies, under
    SolidWorks' schema key, and no geometry caches (SolidWorks rebuilds them)."""
    path = tmp_path / "shaft.SLDPRT"
    build("shaft").export(path)
    secs = sldprt.read_sections(path)
    assert [f.name for f in sldprt.feature_tree(path)] == ["Imported1"]
    assert not set(swfile.CACHES) & set(secs)
    (body,) = sldprt.inflate_chunks(secs[swfile.LOCAL_BODIES])
    assert body.startswith(b"PS\x00\x00\x00\x33: TRANSMIT FILE created by modeller version 3501256")
    assert b"SCH_3501256_35102_13006" in body[:100]
    want = px.to_binary(xt_brep.body_from_shape(build("shaft").doc.body), solidworks=True)
    assert body == want


def test_local_bodies_header_carries_the_chunk_length():
    seed = swfile.Container((swfile.SEEDS / "part.SLDPRT").read_bytes())
    tmpl = seed.get(swfile.LOCAL_BODIES)
    sec = swfile.local_bodies(tmpl, b"x" * 1000)
    i = sec.find(sldprt.ZLB_TAG)
    chunk = len(sec) - i
    assert struct.unpack_from("<I", sec, i - 4)[0] == chunk
    assert struct.unpack_from("<I", sec, i - 9)[0] == chunk + 5
    assert sec[:i - 9] == tmpl[:i - 9]                    # SolidWorks' header otherwise kept


@pytest.mark.parametrize("solidworks", [False, True])
def test_binary_streams_read_back(solidworks, tmp_path):
    """parasolid-kit (an independent reader) reads our neutral binary, in our own schema and
    under SolidWorks' key, to the same topology."""
    pk = pytest.importorskip("parasolid_kit")
    from test_parasolid import _schema_file
    schema = _schema_file("13006") if solidworks else _schema_file()
    if schema is None or schema.suffix != ".sch_txt":
        pytest.skip("parasolid-kit needs Parasolid's schema files")
    path = tmp_path / "b.x_b"
    path.write_bytes(px.to_binary(xt_brep.body_from_shape(build("holes").doc.body), solidworks))
    r = pk.read_brep(str(path), schema_dir=str(schema.parent))
    assert r.complete
    assert (r.summary.counts.faces, r.summary.counts.edges) == (13, 21)


def test_tolerant_and_multi_solid_parts_write(tmp_path):
    spline_shell().export(tmp_path / "shell.SLDPRT")
    from parasolid_models import two_bodies
    two_bodies().export(tmp_path / "two.SLDPRT")            # one body of two lumps
    for p in ("shell", "two"):
        assert sldprt.feature_tree(tmp_path / f"{p}.SLDPRT")[0].name == "Imported1"


def test_assembly_seeds_are_present_and_clean():
    """One seed per component count 1..max (SolidWorks-made), stored without the caches,
    component i the dummy part dII at its seed placement."""
    n = swfile.max_components()
    assert n >= 32
    assert sorted(p.name for p in swfile.ASM_SEEDS.glob("seed*.SLDASM")) == \
        [f"seed{k:02d}.SLDASM" for k in range(1, n + 1)]
    for k in (1, n):
        path = swfile.ASM_SEEDS / f"seed{k:02d}.SLDASM"
        assert not swfile.asm_caches(swfile.Container(path.read_bytes()).names())
        comps = swfile.assembly_components(path)
        assert [c[0] for c in comps] == [f"d{i:02d}-1" for i in range(1, k + 1)]
        assert all(not c[3] and np.allclose(c[2], (100 * i + 12.3, 45.6, 78.9))
                   for i, c in enumerate(comps, 1))


def test_a_written_assembly_references_its_parts_and_places_them(tmp_path):
    """The pegboard as a .SLDASM: the parts written once next to it, every component's file
    path in the model header, and Config-0's placements and fixed flags those of BauyrCAD
    (SolidWorks opening it: tests/test_sw_swfile.py)."""
    asm = pegboard(tmp_path)
    path = tmp_path / "out" / "pegboard.SLDASM"
    asm.export(path)
    assert sorted(p.name for p in path.parent.iterdir()) == ["peg.SLDPRT", "pegboard.SLDASM",
                                                            "plate.SLDPRT"]
    secs = sldprt.read_sections(path)
    assert not swfile.asm_caches(secs)
    for s in (swfile.MODEL_HEADER, swfile.HEADER2):
        texts = [t for _, _, t in swfile.cstrings(secs[s])]
        refs = [t for t in texts if t.lower().endswith(".sldprt")]
        assert refs == [str((path.parent / f).resolve()) for f in ("plate.SLDPRT", "peg.SLDPRT",
                                                                    "peg.SLDPRT")]
        assert {"plate-1", "peg-1", "peg-2", "plate", "peg"} <= set(texts)
        assert not any(t.startswith("d0") for t in texts)          # nothing left of the seed
    comps = swfile.assembly_components(path)
    assert [c[0] for c in comps] == ["plate-1", "peg-1", "peg-2"]
    for (_, R, t, fixed), c in zip(comps, asm.asm.components):
        assert np.allclose(R, c.pose.R, atol=1e-12) and np.allclose(t, c.pose.t, atol=1e-9)
        assert fixed == c.fixed
    assert [c[3] for c in comps] == [True, False, False]
    assert [f.name for f in sldprt.feature_tree(path.parent / "peg.SLDPRT")] == ["Imported1"]


def test_assembly_writer_limits_and_part_export(tmp_path):
    from bauyrcad.core import exchange
    n = swfile.max_components() + 1
    with pytest.raises(swfile.SwFileError, match="at most"):
        swfile.write_assembly([("p", None, None)], [(0, "p", np.eye(4), False)] * n,
                              tmp_path / "big.SLDASM")
    with pytest.raises(exchange.ExchangeError):                     # parts have no .sldasm
        build("box").export(tmp_path / "box.SLDASM")
    assert not list(tmp_path.iterdir())
