from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

path=Path(__file__).parents[1]/'scripts/create_accent_grouping.py'
if not path.exists():
    path=Path(__file__).with_name('create_accent_grouping.py')
spec=importlib.util.spec_from_file_location('accent',path)
accent=importlib.util.module_from_spec(spec)
spec.loader.exec_module(accent)


@pytest.mark.parametrize('pattern',accent.PATTERNS)
def test_cycle_requires_all_accent_boundaries(pattern):
    origin=1440
    ticks=[origin+sum(pattern[:i])*120 for i in range(len(pattern)+1)]
    assert accent.cycle_hits(ticks,pattern)==[origin]
    assert accent.cycle_hits(ticks[:-1],pattern)==[]


def test_cycle_preserves_cross_bar_origin():
    assert accent.cycle_hits([1440,2040,2640,3360],(5,5,6))==[1440]
    assert 1440//1920!=3360//1920


def row(t,end=60,velocity=108):
    return dict(start=t,end=t+end,root=52,intervals=(0,7),velocity=velocity,channel=0)


def synthetic():
    groups=[]
    for bar in range(48):
        if bar in (9,17):
            steps=(1,6,12)
        elif bar in (11,13,19,21):
            steps=(1,6,11)
        else:
            steps=(0,4,12)
        groups += [row(bar*1920+step*120) for step in steps]
    drums=[(g['start'],g['start']+30,36,110,10) for g in groups]
    return groups,drums


def test_revision_preserves_duration_pitch_velocity_and_kick_pairing():
    original,drums=synthetic()
    before=deepcopy(original)
    revised,percussion,changes=accent.revise(original,drums,False)
    assert changes and original==before
    assert [(a['root'],a['intervals'],a['velocity'],a['end']-a['start']) for a in revised]==[
           (a['root'],a['intervals'],a['velocity'],a['end']-a['start']) for a in original]
    assert all(accent.is_kick(g['start'],percussion) for g in revised)
    controlled,_,small_changes=accent.revise(original,drums,True)
    assert len(small_changes)<len(changes)
    assert controlled[:6]==original[:6]


def test_valid_move_rejects_overlap_and_bar_crossing():
    rows=[row(0,120),row(240,120),row(480,120)]
    assert accent.valid_move(rows,1,120)
    assert not accent.valid_move(rows,1,60)
    assert not accent.valid_move(rows,1,420)
    assert not accent.valid_move(rows,1,-120)
