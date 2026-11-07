from copy import deepcopy
import importlib.util
from pathlib import Path

from test_guitar_analysis import write_midi
from test_guitar_generation import drum_notes
from midi_generator.api import scan_midi_catalog

path=Path(__file__).parents[1]/'scripts/create_song_arrangement.py'
if not path.exists():
    path=Path(__file__).with_name('create_song_arrangement.py')
spec=importlib.util.spec_from_file_location('arrangement',path)
song=importlib.util.module_from_spec(spec)
spec.loader.exec_module(song)


def group(tick,gate=120,root=52,velocity=82):
    return dict(start=tick,end=tick+gate,root=root,velocity=velocity,intervals=(0,7),channel=0)


def test_unify_uses_beat_roles_and_keeps_burst_root():
    donor=[group(0,root=52,velocity=108),group(240,root=55,velocity=82)]
    rows=[group(180,45),group(240,45),group(300,45),group(960,240)]
    new=song.unify(rows,donor,'breakdown',8)
    assert [g['root'] for g in new[:3]]==[52,52,52]
    assert [g['start'] for g in new]==[g['start'] for g in rows]
    assert new[1]['velocity']==new[2]['velocity']==92


def test_end_cadence_and_verse_development():
    rows=[group(i*1920,root=52,velocity=108) for i in range(16)]
    new=song.develop_verse(rows)
    assert new[:12]==rows[:12] and new[12]!=rows[12]
    last=song.finish([group(15120,45,55)],8)[-1]
    assert last['root']==52 and last['end']==15360


def test_arrangement_public_api_reproduction(tmp_path,monkeypatch):
    drums=write_midi(tmp_path/'data/sources/drums/sample.mid',drum_notes(16),
                     end_beats=64)
    scan_midi_catalog(project_root=tmp_path)
    config=dict(bpm=125,key='E minor',catalog=str(tmp_path/'data/catalog/midi_catalog.json'),sections=[])
    for i,(ident,bars,style) in enumerate([
        ('intro',8,'tight_groove'),('verse',16,'stop_and_go'),('chorus',8,'chord_driven'),
        ('breakdown',8,'stutter_breakdown'),('outro',8,'groove_burst')]):
        config['sections'].append(dict(id=ident,bars=bars,style=style,guitar_seed=401+i,
              drum_seed=401,drum_source=str(drums),drum_sha256=song.pitch.sha(drums)))
    # Export drums at the requested 125 BPM through the normal API.
    import mido
    source=mido.MidiFile(drums)
    for track in source.tracks:
        for i,msg in enumerate(track):
            if msg.type=='set_tempo':
                track[i]=msg.copy(tempo=480000)
    source.save(drums)
    for section in config['sections']:
        section['drum_sha256']=song.pitch.sha(drums)
    scan_midi_catalog(project_root=tmp_path)
    # Catalog file paths are project-relative.
    for section in config['sections']:
        section['drum_source']='data/sources/drums/sample.mid'
    monkeypatch.chdir(tmp_path)
    before=drums.read_bytes()
    first=song.build(config,tmp_path/'one')
    second=song.build(config,tmp_path/'two')
    assert len(first)==len(second)==3
    assert all(song.pitch.midi_bytes(a)==song.pitch.midi_bytes(b) for a,b in zip(first,second))
    assert drums.read_bytes()==before
    for a,b in zip(sorted((tmp_path/'one').glob('*.mid')),sorted((tmp_path/'two').glob('*.mid'))):
        assert a.read_bytes()==b.read_bytes()
    import pytest
    with pytest.raises(FileExistsError):
        song.build(config,tmp_path/'one')
