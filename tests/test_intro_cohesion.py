import importlib.util
from pathlib import Path

import pytest
from midi_generator.api import BackingStep

path=Path(__file__).parents[1]/'scripts/create_intro_cohesion_auditions.py'
if not path.exists():path=Path(__file__).with_name('create_intro_cohesion_auditions.py')
spec=importlib.util.spec_from_file_location('intro_cohesion',path)
intro=importlib.util.module_from_spec(spec);spec.loader.exec_module(intro)


def unequal_groups():
    return [{'start':0},{'start':480},{'start':7680},{'start':7920},{'start':8160}]


def test_reuse_matches_local_positions_not_event_indices():
    result,mapping=intro.reuse_by_position([52,55,59,57,52],unequal_groups(),0,4)
    assert result==[52,55,52,57,55]
    assert mapping['matched']==[(0,2,0),(1,4,480)]
    assert mapping['target_unmatched'][0]['target_index']==3


def test_invalid_root_count_rejected():
    with pytest.raises(ValueError,match='per group'):
        intro.reuse_by_position([52],unequal_groups(),0,4)


def test_motif_common_match_does_not_hide_unmatched_rhythmic_positions():
    r=intro.motif_comparison(unequal_groups(),[52,55,52,57,55])
    assert r['root_match_rate_on_common_positions']==1
    assert r['root_match_rate_over_position_union']==pytest.approx(2/3)
    assert r['rhythm_position_jaccard']==pytest.approx(2/3)
    assert r['unmatched_second_positions']==[240]


def test_direction_runs_break_on_same_note():
    assert intro.directional_runs([52,55,57,55,54,54,55])==[
        {'direction':'up','transitions':2},{'direction':'down','transitions':2},{'direction':'up','transitions':1}]


@pytest.mark.parametrize('entries',[
    [{'bar':1,'step':0,'root':55},{'bar':1,'step':0,'root':57}],
    [{'bar':1,'step':1,'root':55}],
    [{'bar':0,'step':0,'root':55}],
    [{'bar':1,'step':.5,'root':55}],
])
def test_bad_or_duplicate_assignment_position(entries):
    with pytest.raises(ValueError):intro.assignments([52],[{'start':0}],entries)


def test_bad_assignment_root():
    with pytest.raises(ValueError,match='MIDI'):
        intro.assignments([52],[{'start':0}],[{'bar':1,'step':0,'root':128}])


def test_assembly_uses_renderer_gates_and_eight_bar_offsets():
    bars=[(BackingStep(0,52,1,108),BackingStep(14,52,.5,76)) for _ in range(8)]
    midi=intro.assemble_guitar(bars,105)
    gs,end=intro.pitch.groups(midi)
    assert end==15360 and len(gs)==16
    assert [g['start'] for g in gs]==[bar*1920+offset for bar in range(8) for offset in [0,1680]]
    assert [g['end']-g['start'] for g in gs]==[120,60]*8
    assert [m.program for m in midi.tracks[0] if m.type=='program_change']==[30]
