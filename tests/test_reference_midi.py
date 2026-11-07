import json
from pathlib import Path
import sys

import mido
import pytest

from midi_generator.api import analyze_reference_midi, save_reference_midi_analysis
from midi_generator.cli import main
from test_guitar_analysis import write_midi


@pytest.fixture
def transcription(tmp_path):
    notes=[]
    for bar,root in enumerate((52,52,54,52)):
        positions=(0,1,2) if bar<3 else (0,.5,2.5)
        for beat in positions:
            notes += [(bar*4+beat,root,.25,108,0,0),(bar*4+beat,root+7,.25,108,1,0)]
        notes += [(bar*4,36,.125,110,2,9),(bar*4+1,38,.125,105,2,9)]
    return write_midi(tmp_path/'synthetic.mid',notes,tracks=3,end_beats=16,
                      tempos=[(0,480000)],meters=[(0,4,4)])


def analyze(path,**kwargs):
    return analyze_reference_midi(path,guitar_tracks=[1,2],drum_tracks=[3],**kwargs)


def test_observed_groups_drums_motifs_and_repeatability(transcription,tmp_path):
    before=transcription.read_bytes()
    result=analyze(transcription)
    meta=result['song_metadata']['midi']
    assert meta['status']=='observed'
    assert meta['value']['bar_count']==4
    assert meta['value']['duration_seconds']==pytest.approx(7.68)
    assert meta['value']['tempo_events'][0]['bpm']==125
    groups=result['rhythm_features']['guitar_groups']['value']
    assert len(groups)==12 and groups[3]['start_tick']==1920 and groups[3]['beat_in_bar']==0
    assert groups[0]['notes'][0]['end_tick']==120
    assert groups[0]['notes'][0]['duration_beats']==.25
    assert len(groups[0]['notes'])==2
    kinds={(m['first_bar'],m['second_bar']):m['kind'] for m in result['motifs']['value']}
    assert kinds[1,2]=='exact' and kinds[1,3]=='transposed' and kinds[1,4]=='pitch_sequence_match'
    drum=result['rhythm_features']['drum_relations']['value']
    assert len(drum['kick_events'])==len(drum['snare_events'])==4
    assert len(drum['guitar_kick_matches'])==4
    assert result['sections']['definitions']==[]
    assert result==analyze(transcription)
    a=save_reference_midi_analysis(result,tmp_path/'a')
    b=save_reference_midi_analysis(result,tmp_path/'b')
    assert all(x.read_bytes()==y.read_bytes() for x,y in zip(a,b))
    assert transcription.read_bytes()==before
    with pytest.raises(FileExistsError):
        save_reference_midi_analysis(result,tmp_path/'a')


def test_user_sections_annotations_and_section_changes(transcription):
    definition=dict(metadata={'title':dict(status='user_verified',value='Synthetic fixture',evidence='Test author')},
        sections=[dict(label='Intro',start_bar=1,end_bar=2),
                  dict(label='Verse',start_bar=3,end_bar=4,status='user_verified')])
    result=analyze(transcription,sections=definition)
    sections=result['sections']['definitions']
    assert all(s['status']=='user_verified' for s in sections)
    assert sections[1]['value']['start_tick']==3840
    assert len(result['sections']['changes']['value'])==1
    assert result['song_metadata']['user_annotations']==definition['metadata']
    assert result['pitch_features']['root_candidates']['status']=='inferred'
    assert result['pitch_features']['notes']['status']=='observed'


def test_meter_tempo_changes_partial_and_redundant_meter(tmp_path):
    path=write_midi(tmp_path/'meters.mid',[(0,52,.25),(4,52,.25),(7,55,.25)],end_beats=8,
                   tempos=[(0,500000),(4,1000000)],meters=[(0,4,4),(2,4,4),(4,3,4)])
    result=analyze_reference_midi(path,guitar_tracks=[1])
    meta=result['song_metadata']['midi']['value']
    assert meta['duration_seconds']==pytest.approx(6)
    assert [(b['quarter_beats'],b['complete'],b['time_signature']) for b in meta['bars']]==[
        (4,True,[4,4]),(3,True,[3,4]),(1,False,[3,4])]


def test_midbar_meter_change_is_explicit_partial(tmp_path):
    path=write_midi(tmp_path/'mid.mid',[(0,52,.25),(2,55,.25)],end_beats=5,
                    meters=[(0,4,4),(2,3,4)])
    bars=analyze_reference_midi(path,guitar_tracks=[1])['song_metadata']['midi']['value']['bars']
    assert [b['complete'] for b in bars]==[False,True]
    assert bars[1]['start_tick']==960


def test_defaults_and_low_notes_retained(tmp_path):
    path=write_midi(tmp_path/'low.mid',[(0,12,.125),(0,52,.5)],end_beats=4)
    midi=mido.MidiFile(path)
    for track in midi.tracks:
        track[:]=[m for m in track if m.type not in ('set_tempo','time_signature')]
    midi.save(path)
    result=analyze_reference_midi(path,guitar_tracks=[1])
    assert len(result['song_metadata']['defaults']['value'])==2
    assert result['pitch_features']['root_candidates']['value'][0]['note']==12
    assert not result['pitch_features']['power_chord_candidates']['value']


def test_midi_markers_are_observed_without_inferred_names(transcription):
    midi=mido.MidiFile(transcription)
    midi.tracks[0].insert(0,mido.MetaMessage('marker',text='Theme'))
    midi.save(transcription)
    definition=analyze(transcription)['sections']['definitions'][0]
    assert definition['status']=='observed'
    assert definition['value']['labels']==['Theme']


def test_accent_cycle_requires_closure_not_short_gates(tmp_path):
    for closed in (False,True):
        positions=(0,.75,1.5,2) if closed else (0,.75,1.5)
        path=write_midi(tmp_path/f'{closed}.mid',[(t,52,.125,108) for t in positions],end_beats=4)
        result=analyze_reference_midi(path,guitar_tracks=[1])
        cycles=result['rhythm_features']['accent_cycles']['value']
        assert any(row['sixteenth_intervals']==[3,3,2] for row in cycles)==closed


@pytest.mark.parametrize('sections',[
    {'sections':[dict(label='X',start_bar=0,end_bar=1)]},
    {'sections':[dict(label='X',start_bar=2,end_bar=1)]},
    {'sections':[dict(label='X',start_bar=1,end_bar=3),dict(label='Y',start_bar=3,end_bar=4)]},
    {'sections':[dict(label='X',start_bar=1,end_bar=4,status='certain')]},
    {'metadata':{'title':'Unsupported unclassified field'},'sections':[]},
])
def test_invalid_sections(transcription,sections):
    with pytest.raises(ValueError):
        analyze(transcription,sections=sections)


@pytest.mark.parametrize('guitar,drums',[([],[]),([0],[]),([1,1],[]),([4],[]),([1],[1])])
def test_track_roles_must_be_explicit_and_valid(transcription,guitar,drums):
    with pytest.raises(ValueError):
        analyze_reference_midi(transcription,guitar_tracks=guitar,drum_tracks=drums)


def test_unmatched_and_conflicting_events_rejected(transcription):
    midi=mido.MidiFile(transcription)
    midi.tracks[1]=mido.MidiTrack([m for m in midi.tracks[1] if m.type!='note_off'])
    midi.save(transcription)
    with pytest.raises(ValueError,match='unmatched'):
        analyze(transcription)


def test_cli_report_pair_and_input_hash(transcription,tmp_path,monkeypatch):
    out=tmp_path/'cli'
    monkeypatch.setattr(sys,'argv',['midi-generator','reference','analyze-midi',str(transcription),
                '--guitar-tracks','1','2','--drum-tracks','3','--output',str(out)])
    main()
    assert json.loads((out/'analysis.json').read_text())==analyze(transcription)
    assert (out/'report.md').read_text().startswith('# Reference MIDI analysis')
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code==1


def test_all_inferred_claims_include_reasons(transcription):
    def walk(value):
        if isinstance(value,dict):
            if value.get('status')=='inferred':
                assert 'method' in value and 'thresholds' in value and 'evidence' in value
            for child in value.values():
                walk(child)
        elif isinstance(value,list):
            for child in value:
                walk(child)
    walk(analyze(transcription))


def test_conflicting_metadata_is_rejected(transcription):
    midi=mido.MidiFile(transcription)
    midi.tracks[0].insert(0,mido.MetaMessage('set_tempo',tempo=500000))
    midi.save(transcription)
    with pytest.raises(ValueError,match='Conflicting'):
        analyze(transcription)


def test_invalid_midi_rejected(tmp_path):
    path=tmp_path/'invalid.mid'
    path.write_bytes(b'not MIDI')
    with pytest.raises((OSError,ValueError,EOFError)):
        analyze_reference_midi(path,guitar_tracks=[0])
