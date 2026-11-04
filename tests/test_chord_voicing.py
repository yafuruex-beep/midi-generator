import importlib.util
from pathlib import Path
import mido
import pytest

path=Path(__file__).parents[1]/'scripts/create_chord_voicing_auditions.py'
if not path.exists():path=Path(__file__).with_name('create_chord_voicing_auditions.py')
spec=importlib.util.spec_from_file_location('chord_auditions',path)
chord=importlib.util.module_from_spec(spec);spec.loader.exec_module(chord)


def fixture(first=(52,True),second=(55,False)):
    m=mido.MidiFile(ticks_per_beat=480)
    t=mido.MidiTrack([mido.MetaMessage('track_name',name='unchanged'),
                     mido.MetaMessage('marker',text='anchor'),mido.Message('program_change',program=30)])
    for i,(root,power) in enumerate([first,second]):
        t.append(mido.Message('note_on',note=root,velocity=96-i*20))
        if power:t.append(mido.Message('note_on',note=root+7,velocity=96-i*20))
        t.append(mido.Message('note_off',note=root,velocity=64,time=120))
        if power:t.append(mido.Message('note_off',note=root+7,velocity=64))
    t.append(mido.MetaMessage('end_of_track',time=1680));m.tracks.append(t)
    return m


def apply(m,indices):
    return chord.set_voicings(m,indices,scale_pitch_classes=[4,6,7,9,11,0,2],note_range=[52,66])


def test_identity_preserves_every_byte():
    original=fixture()
    assert chord.pitch.midi_bytes(apply(original,{0}))==chord.pitch.midi_bytes(original)


def test_swap_voices_preserves_roots_gates_velocity_and_metadata():
    original=fixture();before=chord.pitch.midi_bytes(original)
    result=apply(original,{1})
    assert chord.root_signature(result)==chord.root_signature(original)
    gs,_=chord.pitch.groups(result)
    assert [g['intervals'] for g in gs]==[(0,),(0,7)]
    assert [g['root'] for g in gs]==[52,55]
    assert result.tracks[0][:3]==original.tracks[0][:3]
    assert chord.pitch.midi_bytes(original)==before
    assert chord.pitch.midi_bytes(result)==chord.pitch.midi_bytes(apply(original,{1}))


def test_single_focus_only_removes_original_upper_notes():
    m=fixture(second=(55,True));result=apply(m,set())
    assert chord.root_signature(result)==chord.root_signature(m)
    gs,_=chord.pitch.groups(result)
    assert sum(len(g['members']) for g in gs)==2
    assert [g['members'][0]['pitch'] for g in gs]==[52,55]


def test_same_pitch_retrigger_note_off_stays_before_new_attack():
    result=apply(fixture((52,False),(52,True)),{0,1})
    tick=0;at=[]
    for msg in result.tracks[0]:
        tick+=msg.time
        if tick==120 and msg.type in {'note_on','note_off'}:at.append(msg.type)
    assert at==['note_off','note_off','note_on','note_on']
    gs,_=chord.pitch.groups(result);assert len(gs)==2


@pytest.mark.parametrize('root,match',[(54,'scale'),(64,'range')])
def test_ineligible_upper_note_rejected(root,match):
    with pytest.raises(ValueError,match=match):apply(fixture((root,False),(55,False)),{0})


@pytest.mark.parametrize('index',[-1,2,True])
def test_invalid_group_index(index):
    with pytest.raises(ValueError,match='index'):apply(fixture(),{index})


def test_exact_budget_respects_unit_weights_not_just_greedy_score():
    chosen=chord.choose_exact([(0,2),(1,),(3,)],{0:2,2:2,1:3,3:3},2)
    assert chosen=={1,3}


def test_odd_budget_conflicts_with_exact_four_bar_repetition():
    with pytest.raises(ValueError,match='repetition'):
        chord.choose_exact([(0,2),(1,3)],{i:1 for i in range(4)},3)


def test_equal_priority_tie_is_stable():
    assert chord.choose_exact([(0,),(1,),(2,)],{i:1 for i in range(3)},2)=={0,1}


def test_overlapping_selection_units_rejected():
    with pytest.raises(ValueError,match='overlap'):
        chord.choose_exact([(0,1),(1,2)],{i:1 for i in range(3)},2)
