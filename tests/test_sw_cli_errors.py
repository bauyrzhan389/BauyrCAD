import pytest

from bauyrcad.api import Part
from bauyrcad.cli import main


@pytest.mark.parametrize('problem', ['reserved_name', 'unsupported_feature'])
def test_native_export_cli_returns_error_and_preserves_output(tmp_path, capsys, problem):
    part = Part()
    sketch = part.sketch('front')
    sketch.rect((3, 5), (43, 27))
    part.extrude(sketch, 11)
    if problem == 'reserved_name':
        part.doc.features[0].name = 'Front Plane'
    else:
        part.shell(2, remove=part.face('+x'))  # side-face shells are still unsupported offline
    source = tmp_path / 'source.bcad'
    part.save(source)
    destination = tmp_path / 'existing.SLDPRT'
    destination.write_bytes(b'existing output')
    assert main(['export', str(source), str(destination), '--features', '--require-native']) == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert output.err.startswith('Could not export: ')
    assert 'Traceback' not in output.err
    assert destination.read_bytes() == b'existing output'
