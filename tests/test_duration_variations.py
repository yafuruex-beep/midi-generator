import importlib.util
from pathlib import Path
import mido
import pytest

path=Path(__file__).parents[1]/'scripts/create_duration_variation_auditions.py'
if not path.exists():path=Path(__file__).with_name('create_duration_variation_auditions.py')
spec=importlib.util.spec_from_file_location('duration_auditions',path)
duration=importlib.util.module_from_spec(spec);spec.loader.exec_module(duration)


def fixture(rows=None):
    if rows is None:rows=[(0,.25,52,True,108),(.5,.25,55,False,76),(1,.5,57,True,96)]
    m=mido.MidiFile(ticks_per_beat=480);events=[]
    for start,gate,root,power,vel in rows:
        for p in [root]+([root+7] if power else []):
            events.extend([(round(start*480),1,mido.Message('note_on',note=p,velocity=vel)),
                           (round((start+gate)*480),0,mido.Message('note_off',note=p,velocity=64))])
    t=mido.MidiTrack([mido.MetaMessage('track_name',name='baseline'),mido.Message('program_change',program=30)])
    last=0
    for tick,priority,msg in sorted(events,key=lambda x:x[:2]):t.append(msg.copy(time=tick-last));last=tick
    t.append(mido.MetaMessage('end_of_track',time=1920-last));m.tracks.append(t)
    return m


def test_identity_is_byte_identical():
    m=fixture();new,changes=duration.change_gates(m,[.25,.25,.5])
    assert duration.pitch.midi_bytes(m)==duration.pitch.midi_bytes(new)
    assert not any(c['shortened_by_safety_cap'] for c in changes)


def test_only_releases_move_and_chord_members_share_gate():
    m=fixture();before=duration.pitch.midi_bytes(m)
    new,_=duration.change_gates(m,[.5,.125,.25])
    assert duration.attack_signature(new)==duration.attack_signature(m)
    assert duration.absolute_non_releases(new)==duration.absolute_non_releases(m)
    gs,_=duration.pitch.groups(new)
    assert [g['end'] for g in gs]==[240,300,600]
    assert all(len({n['end'] for n in g['members']})==1 for g in gs)
    assert duration.pitch.midi_bytes(m)==before
    assert duration.pitch.midi_bytes(new)==duration.pitch.midi_bytes(duration.change_gates(m,[.5,.125,.25])[0])


def test_final_note_caps_at_bar_boundary():
    new,changes=duration.change_gates(fixture([(3.75,.125,52,False,96)]),[.5])
    assert changes[0]['actual_beats']==.25
    assert changes[0]['shortened_by_safety_cap']
    assert duration.pitch.groups(new)[0][0]['end']==1920


def test_next_attack_cap_and_retrigger_release_order():
    m=fixture([(0,.25,52,True,96),(.25,.25,52,True,96)])
    new,changes=duration.change_gates(m,[.5,.5])
    assert changes[0]['actual_beats']==.25
    tick=0;at=[]
    for msg in new.tracks[0]:
        tick+=msg.time
        if tick==120 and msg.type in {'note_on','note_off'}:at.append(msg.type)
    assert at==['note_off','note_off','note_on','note_on']
    assert len(duration.pitch.groups(new)[0])==2


@pytest.mark.parametrize('value',[0,-1,float('nan'),float('inf'),True,'.5'])
def test_invalid_duration(value):
    with pytest.raises(ValueError,match='Duration'):duration.change_gates(fixture(),[value,.25,.5])


def test_unrepresentable_ticks_rejected():
    with pytest.raises(ValueError,match='representable'):duration.change_gates(fixture(),[.0001,.25,.5])


def test_group_and_note_statistics_and_ioi_are_separate():
    m=fixture([(0,.25,52,True,96),(1,.5,55,False,76)])
    data=duration.duration_metrics(m)
    assert data['groups']==2 and data['notes']==3
    assert data['group_mean_gate_beats']==.375
    assert data['note_mean_gate_beats']==pytest.approx(1/3)
    assert data['IOI_histogram']==[{'beats':1,'count':1}]
    assert data['next_onset_gap_histogram']==[{'beats':.75,'count':1}]
    assert data['gate_to_IOI_mean']==.25
    assert data['terminal_gap_beats']==2.5


def test_plan_keeps_strong_and_old_long_gates_while_targeting_accent():
    m=fixture([(0,.25,52,True,108),(.5,.25,55,False,76),(2,.25,57,True,100),(3,.5,52,True,96)])
    gs,_=duration.pitch.groups(m)
    req={'short_beats':.125,'weak_velocity_below':96,'baseline_short_beats':.25,
         'contrast_step':8,'contrast_min_velocity':100,'long_beats':.5}
    plans=duration.requested_gates(gs,480,req)
    assert plans['A']==[.25,.25,.25,.5]
    assert plans['B']==[.25,.125,.25,.5]
    assert plans['C']==[.25,.125,.5,.5]


def test_wrong_duration_count_rejected():
    with pytest.raises(ValueError,match='per attack'):duration.change_gates(fixture(),[.25])
