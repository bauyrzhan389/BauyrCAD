import json
import zipfile
import pytest
from bauyrcad.api import Part, Assembly
from bauyrcad.core.assembly import Assembly as CoreAssembly, AssemblyError, MateRef


def make_assembly(tmp_path):
    a = Assembly()
    for i in range(2):
        folder = tmp_path / str(i)
        folder.mkdir()
        p = Part()
        s = p.sketch()
        s.circle((0, 0), d=10 + i * 4)
        p.extrude(s, 20)
        path = folder / 'same.bcad'
        p.save(path)
        a.add(path, at=(i * 30, 0, 0))
    a.add(tmp_path / '0' / 'same.bcad', at=(60, 0, 0))
    c = a.asm.components
    a.asm.mates.append(a.asm.new_mate('lock', MateRef(c[0].id, None), MateRef(c[1].id, None)))
    a.save(tmp_path / 'original.bcasm')
    return a


def test_package_moves_without_sources_and_preserves_mates(tmp_path):
    a = make_assembly(tmp_path)
    before, original_path = a.asm.to_dict(), a.asm.path
    result = a.pack_and_go(tmp_path / 'portable.zip')
    assert result['parts'] == 2 and result['components'] == 3
    assert a.asm.to_dict() == before and a.asm.path == original_path
    with zipfile.ZipFile(result['path']) as z:
        assert len([n for n in z.namelist() if n.startswith('parts/')]) == 2
        manifest = json.loads(z.read('manifest.json'))
        import hashlib
        for name, item in manifest['parts'].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == item['sha256']
        z.extractall(tmp_path / 'moved')  # only our generated archive
    for directory in ('0', '1'):
        (tmp_path / directory).rename(tmp_path / ('old_' + directory))
    loaded = CoreAssembly.load(tmp_path / 'moved' / 'assembly.bcasm')
    assert all(c.shape is not None and not c.load_error for c in loaded.components)
    assert loaded.to_dict()['mates'] == before['mates']
    for old, new in zip(before['components'], loaded.to_dict()['components']):
        assert old['pose'] == new['pose'] and old['fixed'] == new['fixed']
    assert loaded.solve().ok


def test_missing_part_does_not_replace_package(tmp_path):
    a = make_assembly(tmp_path)
    target = tmp_path / 'keep.zip'
    target.write_bytes(b'original')
    (tmp_path / '0' / 'same.bcad').rename(tmp_path / 'missing.bcad')
    with pytest.raises(AssemblyError, match='missing'):
        a.pack_and_go(target)
    assert target.read_bytes() == b'original'


def test_pack_gui(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from bauyrcad.gui.assembly_window import AssemblyWindow
    app = QApplication.instance() or QApplication([])
    a = make_assembly(tmp_path)
    w = AssemblyWindow(assembly=a.asm)
    w.show()
    QTest.qWait(200)
    try:
        assert w.pack_and_go(tmp_path / 'gui.zip')['parts'] == 2
        w.grab().save(str(tmp_path / 'pack-and-go.png'))
    finally:
        w.dirty = False
        w.close()
        app.processEvents()
