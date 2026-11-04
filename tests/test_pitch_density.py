import importlib.util
from pathlib import Path
import struct

import pytest

path=Path(__file__).parents[1]/'scripts/create_pitch_density_auditions.py'
if not path.exists():path=Path(__file__).with_name('create_pitch_density_auditions.py')
spec=importlib.util.spec_from_file_location('pitch_density',path)
density=importlib.util.module_from_spec(spec);spec.loader.exec_module(density)


def test_same_change_rate_can_have_different_actual_motion():
    baseline=[52]*4
    alternating=density.motion_metrics([52,55,52,55],baseline)
    clustered=density.motion_metrics([52,52,55,55],baseline)
    assert alternating['change_rate_vs_Step3B']==clustered['change_rate_vs_Step3B']==.5
    assert alternating['actual_motion_rate']==1
    assert clustered['actual_motion_rate']==pytest.approx(1/3)
    assert alternating['transition_denominator']==3


def test_up_down_and_semitone_distributions():
    m=density.motion_metrics([52,55,54,54,59],[52]*5)
    assert m['moving_transitions']==3
    assert m['up_transitions']==2 and m['down_transitions']==1
    assert m['same_root_transitions']==1
    assert m['signed_semitone_counts']=={-1:1,0:1,3:1,5:1}
    assert m['up_share_of_moves']==pytest.approx(2/3)
    assert m['absolute_moving_semitone_counts']=={1:1,3:1,5:1}
    assert m['maximum_jump_semitones']==5


def test_same_root_run_length_is_event_count_not_sustain():
    m=density.motion_metrics([52,52,52,55],[52]*4,starts=[0,240,960,1440],ends=[120,360,1080,1560],tpb=480)
    assert m['maximum_same_root_run_groups']==3
    assert m['run_length_histogram_groups']=={1:1,3:1}
    assert m['same_root_runs'][0]['elapsed_beats_including_gaps']==2.25


def test_constant_pitch_has_no_direction_or_motion():
    m=density.motion_metrics([55,55,55],[52,52,52])
    assert m['change_rate_vs_Step3B']==1
    assert m['actual_motion_rate']==0
    assert m['up_share_of_moves'] is None
    assert m['maximum_same_root_run_groups']==3


def test_one_group_does_not_divide_by_zero():
    m=density.motion_metrics([52],[52])
    assert m['actual_motion_rate']==0
    assert m['transition_denominator']==0


@pytest.mark.parametrize('roots,baseline', [([],[]),([52],[52,55]),([True],[52]),([52.0],[52]),([128],[52])])
def test_invalid_metric_sequences(roots,baseline):
    with pytest.raises(ValueError):density.motion_metrics(roots,baseline)


def test_track_chunks_compare_actual_bytes(tmp_path):
    header=b'MThd'+struct.pack('>IHHH',6,0,1,480)
    track=b'\x00\xff\x2f\x00'
    chunk=b'MTrk'+struct.pack('>I',len(track))+track
    path=tmp_path/'synthetic.mid';path.write_bytes(header+chunk)
    assert density.track_chunks(path)==[chunk]


def test_track_chunks_reject_truncation(tmp_path):
    path=tmp_path/'bad.mid'
    path.write_bytes(b'MThd'+struct.pack('>IHHH',6,0,1,480)+b'MTrk'+struct.pack('>I',4)+b'\x00')
    with pytest.raises(ValueError,match='Truncated'):density.track_chunks(path)
