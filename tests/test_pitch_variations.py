import hashlib
import importlib.util
from pathlib import Path

import mido
import pytest

_spec = importlib.util.spec_from_file_location('pitch_auditions', Path(__file__).parents[1]/'scripts/create_pitch_variation_auditions.py')
# The staging path permits pre-install validation of this same test file.
if not _spec.origin or not Path(_spec.origin).exists():
    _spec = importlib.util.spec_from_file_location('pitch_auditions', Path(__file__).with_name('create_pitch_variation_auditions.py'))
pitch = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(pitch)


def fixture():
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack([mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(105)),
                           mido.MetaMessage('time_signature',numerator=4,denominator=4),
                           mido.Message('program_change',program=30)])
    # Touching same-pitch chords verify release-before-retrigger ordering.
    track.extend([mido.Message('note_on',note=52,velocity=108),mido.Message('note_on',note=59,velocity=108),
                  mido.Message('note_off',note=52,time=120),mido.Message('note_off',note=59),
                  mido.Message('note_on',note=52,velocity=76),mido.Message('note_on',note=59,velocity=76),
                  mido.Message('note_off',note=52,time=120),mido.Message('note_off',note=59),
                  mido.MetaMessage('end_of_track',time=1680)])
    midi.tracks.append(track)
    return midi


def change(midi, roots, **kwargs):
    return pitch.transpose_groups(midi,roots,**{'allowed_roots':[52,54,55],
           'guitar_range':[52,62],'bass_range':[28,31],'max_jump':3,**kwargs})


def test_pitch_only_preserves_timing_shape_and_all_other_messages():
    original=fixture(); before=pitch.midi_bytes(original)
    result=change(original,[52,55])
    assert pitch.rhythm_signature(result)==pitch.rhythm_signature(original)
    groups,_=pitch.groups(result)
    assert [g['root'] for g in groups]==[52,55]
    assert groups[1]['intervals']==(0,7)
    assert pitch.midi_bytes(original)==before
    assert pitch.midi_bytes(result)==pitch.midi_bytes(change(original,[52,55]))
    assert result.tracks[0][6].type=='note_off'


def test_identity_bytes_and_releases_match_shifted_note_ons():
    original=fixture()
    assert pitch.midi_bytes(change(original,[52,52]))==pitch.midi_bytes(original)
    result=change(original,[55,55])
    gs,_=pitch.groups(result)
    assert len(gs)==2 and [g['root'] for g in gs]==[55,55]
    assert [g['end']-g['start'] for g in gs]==[120,120]


@pytest.mark.parametrize('roots,kwargs,error',[
    ([52],{},'One root'),([52,True],{},'allowed'),([52,53],{},'allowed'),
    ([52,55],{'max_jump':2},'jump'),([52,55],{'guitar_range':[52,61]},'Guitar'),
    ([52,55],{'bass_range':[28,30]},'Bass'),([52,55],{'max_jump':-1},'max_jump'),
])
def test_invalid_pitch_plan_rejected(roots,kwargs,error):
    with pytest.raises(ValueError,match=error):change(fixture(),roots,**kwargs)


def test_missing_release_rejected():
    midi=fixture();del midi.tracks[0][7]
    with pytest.raises(ValueError):pitch.groups(midi)


def test_shared_tick_retrigger_after_note_off_survives():
    midi=change(fixture(),[55,55])
    groups,_=pitch.groups(midi)
    assert groups[0]['end']==groups[1]['start']
    assert groups[0]['root']==groups[1]['root']==55


def test_a2_bass_pitches_keep_baseline_context_messages():
    reference=fixture()
    reference.tracks[0]=mido.MidiTrack(m.copy() for m in reference.tracks[0] if not (m.type in {'note_on','note_off'} and m.note==59))
    reference.tracks[0].insert(0,mido.MetaMessage('marker',text='baseline marker'))
    generated=change(reference,[52,55])
    generated.tracks[0].pop(0)
    generated.tracks[0][0]=generated.tracks[0][0].copy(time=0)
    result=pitch.retain_bass_context(reference,generated)
    assert pitch.rhythm_signature(result)==pitch.rhythm_signature(reference)
    assert result.tracks[0][0]==reference.tracks[0][0]
    assert [g['root'] for g in pitch.groups(result)[0]]==[52,55]
