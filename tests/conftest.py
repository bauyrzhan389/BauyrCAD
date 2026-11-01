import pytest

from bauyrcad.core.document import Document
from bauyrcad.core.features import ExtrudeFeature, SketchFeature
from bauyrcad.core.plane import FRONT


@pytest.fixture
def beam_doc():
    """10 x 10 x 100 mm bar along +Z."""
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    s.sketch.add_rectangle((0, 0), (10, 10))
    doc.add_feature(ExtrudeFeature(s.id, 100))
    doc.regenerate()
    return doc


@pytest.fixture(scope="session")
def sw():
    """One private SolidWorks session shared by every real-SolidWorks test (tests that use it
    are marked with sw_support.needs_solidworks)."""
    from sw_support import sw_session
    yield from sw_session()


@pytest.fixture(autouse=True)
def close_sw_documents(request):
    """Close every SolidWorks document a test left open in the shared session, also when
    it failed half-way: SolidWorks refuses to open a second file with the same title
    (plate.SLDPRT in another folder), so a leaked document would fail later tests."""
    if "sw" not in request.fixturenames:
        yield
        return
    sw = request.getfixturevalue("sw")
    before = list(sw._docs)
    yield
    from sw_support import close_new_documents
    close_new_documents(sw, before)


@pytest.fixture(autouse=True)
def no_blocking_dialogs(monkeypatch):
    """A modal message box would hang an unattended test run - fail loudly instead."""
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:
        return

    def fail(*args, **kwargs):
        raise AssertionError(f"Unexpected dialog: {args[1:3]}")

    for name in ("warning", "critical", "question", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(fail))
