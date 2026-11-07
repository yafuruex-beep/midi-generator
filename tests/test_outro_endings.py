import importlib.util
from pathlib import Path

import mido
import pytest

path=Path(__file__).parents[1]/'scripts/create_outro_endings.py'
if not path.exists():
    path=Path(__file__).with_name('create_outro_endings.py')
spec=importlib.util.spec_from_file_location('endings',path)
endings=importlib.util.module_from_spec(spec)
spec.loader.exec_module(endings)


def original_band():
    band=mido.MidiFile(type=1,ticks_per_beat=480)
    notes=[]
    for channel,pitch in ((0,52),(1,28),(9,36)):
        events=[(0,mido.MetaMessage('set_tempo',tempo=480000)),
                (0,mido.MetaMessage('time_signature',numerator=4,denominator=4)),
                (0,mido.MetaMessage('marker',text='Intro')),
                (endings.START-120,mido.Message('note_on',note=pitch,channel=channel,velocity=100)),
                (endings.START,mido.Message('note_off',note=pitch,channel=channel)),
                (endings.START,mido.Message('note_on',note=pitch,channel=channel,velocity=100)),
                (endings.START+240,mido.Message('note_off',note=pitch,channel=channel))]
        band.tracks.append(endings.transition.schedule(events,endings.END))
        notes += [(endings.START-120,endings.START,pitch,100,channel+1),
                  (endings.START,endings.START+240,pitch,100,channel+1)]
    return band,notes


@pytest.mark.parametrize('stop_final',[False,True])
def test_endings_preserve_prefix_and_pair_notes(stop_final):
    original,notes=original_band()
    changed=endings.make_variant(original,notes,stop_final)
    for a,b in zip(original.tracks,changed.tracks):
        assert endings.prefix_signature(a,endings.START)==endings.prefix_signature(b,endings.START)
        endings.transition.validate_notes(b,endings.END)
        assert endings.transition.absolute(b)[-1][0]==endings.END
    groups=endings.note_rows(changed)
    final=groups[-1]
    assert (final['root'],final['intervals'])==(52,(0,7))
    assert final['end']-final['start']==(720 if stop_final else 1440)
    if stop_final:
        for track in changed.tracks:
            assert not any(endings.START+480<=t<endings.START+960 and m.type in {'note_on','note_off'}
                           for t,m in endings.transition.absolute(track))
    repeated=endings.make_variant(original,notes,stop_final)
    assert endings.pitch.midi_bytes(changed)==endings.pitch.midi_bytes(repeated)


def test_cut_refuses_to_hide_spanning_note():
    track=endings.transition.schedule([
        (endings.START-120,mido.Message('note_on',note=52)),
        (endings.START+120,mido.Message('note_off',note=52))],endings.END)
    with pytest.raises(ValueError,match='spanning'):
        endings.replace_tail(track,endings.START,[])


def test_tail_note_cannot_exceed_song_end():
    _,notes=original_band()
    track=mido.MidiTrack([mido.MetaMessage('end_of_track',time=endings.END)])
    with pytest.raises(ValueError,match='outside'):
        endings.replace_tail(track,endings.START,[(endings.END-60,endings.END+60,52,100,0)])
