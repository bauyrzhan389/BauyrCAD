"""The real-SolidWorks test helpers' own bookkeeping, checked without SolidWorks."""
from sw_support import close_new_documents, doc_steps


class _FakeSession:
    def __init__(self, docs):
        self._docs = list(docs)
        self.closed = []

    def close(self, doc):
        self.closed.append(doc)
        self._docs.remove(doc)


def test_cleanup_closes_only_what_the_test_opened():
    """conftest's per-test cleanup: documents opened by a test (failed or not) are closed,
    documents that were open before it stay open."""
    older, mine, other = object(), object(), object()
    s = _FakeSession([older])
    before = list(s._docs)
    s._docs += [mine, other]                      # a test reopened two files, then failed
    close_new_documents(s, before)
    assert s.closed == [mine, other] and s._docs == [older]


def test_expected_steps_come_from_the_document():
    from bauyrcad.api import Part
    p = Part("p")
    sk = p.sketch("front")
    sk.rect((0, 0), (10, 10))
    p.extrude(sk, 5)
    assert doc_steps(p.doc) == ["Sketch1", "Boss-Extrude1"]
