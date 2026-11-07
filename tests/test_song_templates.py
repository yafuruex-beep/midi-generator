from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import mido
import pytest

from midi_generator.api import scan_midi_catalog
from test_guitar_analysis import write_midi
from test_guitar_generation import drum_notes

path=Path(__file__).parents[1]/'scripts/create_song_templates.py'
if not path.exists():
    path=Path(__file__).with_name('create_song_templates.py')
spec=importlib.util.spec_from_file_location('song_templates',path)
song=importlib.util.module_from_spec(spec)
spec.loader.exec_module(song)


def config(**changes):
    return dict(bpm=125,key='E',scale='minor',seed=42,song_template='standard',
                ending_style='original',**changes)


@pytest.fixture
def foundation(tmp_path,monkeypatch):
    path=write_midi(tmp_path/'data/sources/drums/sample.mid',drum_notes(16),
                    end_beats=64)
    midi=mido.MidiFile(path)
    for track in midi.tracks:
        for i,msg in enumerate(track):
            if msg.type=='set_tempo':
                track[i]=msg.copy(tempo=480000)
    midi.save(path)
    scan_midi_catalog(project_root=tmp_path)
    sections=[]
    for i,(ident,bars,style) in enumerate([
        ('intro',8,'tight_groove'),('verse',16,'stop_and_go'),('chorus',8,'chord_driven'),
        ('breakdown',8,'stutter_breakdown'),('outro',8,'groove_burst')]):
        sections.append(dict(id=ident,bars=bars,style=style,guitar_seed=401+i,drum_seed=401,
             drum_source='data/sources/drums/sample.mid',drum_sha256=song.pitch.sha(path)))
    foundation=tmp_path/'foundation.json'
    foundation.write_text(json.dumps(dict(bpm=125,key='E minor',catalog='data/catalog/midi_catalog.json',
                                          sections=sections)))
    monkeypatch.chdir(tmp_path)
    return foundation


@pytest.mark.parametrize('template',song.TEMPLATES)
@pytest.mark.parametrize('ending',('original','stop_final_hit'))
def test_template_endings_and_reproduction(foundation,tmp_path,template,ending):
    request=config(foundation_config=str(foundation))
    request.update(song_template=template,ending_style=ending)
    before=(tmp_path/'data/sources/drums/sample.mid').read_bytes()
    a=song.build(request,tmp_path/'a')
    b=song.build(request,tmp_path/'b')
    assert song.pitch.midi_bytes(a)==song.pitch.midi_bytes(b)
    meta=json.loads((tmp_path/'a'/'metadata.json').read_text())
    total=48 if template=='standard' else 56
    assert meta['total_bars']==total
    assert meta['duration_seconds']==pytest.approx(total*4*60/125)
    assert meta['sections'][0]['start_bar']==1 and meta['sections'][-1]['end_bar']==total
    assert all(a['end_bar']+1==b['start_bar'] for a,b in zip(meta['sections'],meta['sections'][1:]))
    assert (tmp_path/'data/sources/drums/sample.mid').read_bytes()==before
    if ending=='stop_final_hit':
        groups=song.pitch.groups(song.transition.part(a,0))[0]
        final=groups[-1]
        assert final['start']==(total-1)*1920+960 and final['end']==total*1920-240
        assert final['root']==52 and final['intervals']==(0,7)
        assert all(not any((total-1)*1920+480<=t<(total-1)*1920+960 and
                          m.type=='note_on' and m.velocity>0 for t,m in song.transition.absolute(track))
                   for track in a.tracks)
    with pytest.raises(FileExistsError):
        song.build(request,tmp_path/'a')


@pytest.mark.parametrize('field,value',[
    ('song_template','random'),('ending_style','sustain'),('seed',True),
    ('key','D'),('scale','major'),('bpm',float('nan')),('bpm',105)])
def test_invalid_template_config(field,value):
    request=config()
    request[field]=value
    with pytest.raises(ValueError):
        song.validate_config(request)


def test_legacy_entry_dispatch_and_default_preservation(monkeypatch):
    # New configuration dispatches only when song_template is present.
    marker=object()
    class Fake:
        def build(self, config, output):
            return marker
    monkeypatch.setattr(song.arrange,'_helper',lambda name:Fake())
    assert song.arrange.build(config(),'unused') is marker


def test_ending_patch_is_relative_to_length():
    assert 'end_tick' in song.ending.make_variant.__kwdefaults__
