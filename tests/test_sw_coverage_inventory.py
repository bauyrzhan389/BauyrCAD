"""Keep the export audit complete when modelling capabilities are added."""
from pathlib import Path
import re
from bauyrcad.core import features, features_more, assembly
from bauyrcad.core.sketch.constraints import TYPES


def rows(heading):
    text=(Path(__file__).parents[1]/'docs/SW_FEATURE_COVERAGE.md').read_text()
    section=text.split('## '+heading+'\n',1)[1].split('\n## ',1)[0]
    return re.findall(r'^\| `([^`]+)` \|',section,re.M)


def test_all_persisted_features_have_a_coverage_entry():
    entries=rows('Part feature families')
    assert len(entries)==len(set(entries))
    assert set(entries)==set(features.FEATURE_TYPES)


def test_all_sketch_constraints_have_a_coverage_entry():
    assert set(rows('Sketch relation inventory'))==set(TYPES)


def test_all_selectable_mates_have_a_coverage_entry():
    types={t for a in ('plane','axis') for b in ('plane','axis') for t in assembly.mate_options(a,b)}
    assert set(rows('Assembly mate inventory'))==types
