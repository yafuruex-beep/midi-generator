import importlib.util
from copy import deepcopy
import json
from pathlib import Path

import mido
import pytest

path=Path(__file__).parents[1]/'scripts/create_transition_auditions.py'
if not path.exists():
    path=Path(__file__).with_name('create_transition_auditions.py')
spec=importlib.util.spec_from_file_location('transition',path)
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
ROOT=Path.cwd()
REQUEST=ROOT/'examples/requests/intro_chorus_transition.json'
if not REQUEST.exists():REQUEST=Path(__file__).with_name('intro_chorus_transition.json')


@pytest.fixture
def sources():
    request=json.loads(REQUEST.read_text())
    return request,{key:mido.MidiFile(ROOT/row['path']) for key,row in request['sources'].items()}


def test_join_preserves_all_channel_events_and_retrigger_order(sources):
    _,s=sources
    band=t.concatenate(s['intro'],s['chorus'])
    for i,track in enumerate(band.tracks):
        assert t.musical(track)==t.musical(s['intro'].tracks[i])+t.musical(s['chorus'].tracks[i],15360)
        assert t.absolute(track)[-1][0]==30720
        assert [tick for tick,msg in t.absolute(track) if msg.type=='set_tempo']==[0]
    events=[(0,mido.Message('note_on',note=52,velocity=80)),
        (120,mido.Message('note_on',note=52,velocity=80)),
        (120,mido.Message('note_off',note=52)),(240,mido.Message('note_off',note=52))]
    track=t.schedule(events,240)
    assert [m.type for tick,m in t.absolute(track) if tick==120]==['note_off','note_on']
    t.validate_notes(track,240)


def test_b_changes_pitch_only_and_preserves_independent_bass_gates(sources):
    request,s=sources
    new=t.unified_chorus(s['chorus'],request['chorus_motif_by_bar'])
    for index in (0,1):
        assert t.pitch.rhythm_signature(t.part(new,index))==t.pitch.rhythm_signature(t.part(s['chorus'],index))
    assert new.tracks[2]==s['chorus'].tracks[2]
    gs,_=t.pitch.groups(t.part(new,0));bs,_=t.pitch.groups(t.part(new,1))
    assert sum(g['end']!=b['end'] for g,b in zip(gs,bs))==36
    assert all((g['start'],g['root']-24,g['velocity'])==(b['start'],b['root'],b['velocity']) for g,b in zip(gs,bs))
    assert t.intro.motif_comparison(gs,[g['root'] for g in gs])['root_match_rate_over_position_union']==1
    assert sum(a['root']!=b['root'] for a,b in zip(gs,gs[1:]))==50


def test_c_exact_local_exceptions_preserve_all_pitch_motifs(sources):
    request,s=sources
    b=t.concatenate(s['intro'],t.unified_chorus(s['chorus'],request['chorus_motif_by_bar']))
    c=t.refine_transition(b)
    diff=t.event_difference(b,c)
    assert [(len(diff[k]['removed']),len(diff[k]['added'])) for k in ['guitar','bass','drums']]==[(4,4),(2,2),(0,2)]
    assert all(row['tick'] in {15120,15240,15360,15480} for d in diff.values() for rows in d.values() for row in rows)
    bg,_=t.pitch.groups(t.part(b,0));cg,_=t.pitch.groups(t.part(c,0))
    assert [(g['start'],g['root'],g['intervals']) for g in bg]==[(g['start'],g['root'],g['intervals']) for g in cg]
    assert t.metrics(t.part(b,0))['boundary_guitar_gap_beats']==.25
    assert t.metrics(t.part(c,0))['boundary_guitar_gap_beats']==.5
    for track in c.tracks:t.validate_notes(track,30720)


@pytest.mark.parametrize('change',[ 'tempo','meter','cc','length','channel','pitch'])
def test_unsafe_source_rejected(sources,change):
    _,s=sources
    bad=deepcopy(s['intro'])
    if change in {'tempo','meter'}:
        typ='set_tempo' if change=='tempo' else 'time_signature'
        index=next(i for i,m in enumerate(bad.tracks[0]) if m.type==typ)
        bad.tracks[0][index]=bad.tracks[0][index].copy(**({'tempo':500000} if change=='tempo' else {'numerator':3}))
    elif change=='cc':bad.tracks[0].insert(0,mido.Message('control_change',control=64,value=127))
    elif change=='length':bad.tracks[0][-1]=bad.tracks[0][-1].copy(time=bad.tracks[0][-1].time+1)
    elif change=='channel':bad.tracks[0].insert(0,mido.Message('program_change',channel=5,program=30))
    else:
        for i,m in enumerate(bad.tracks[0]):
            if m.type in {'note_on','note_off'} and m.note==52:bad.tracks[0][i]=m.copy(note=53)
    with pytest.raises(ValueError):t.validate_source(bad)


def test_motif_cannot_copy_by_ordinal_or_omit_positions(sources):
    request,s=sources
    rows=deepcopy(request['chorus_motif_by_bar']);rows[1][0][0]=1
    with pytest.raises(ValueError,match='positions'):t.unified_chorus(s['chorus'],rows)


def test_note_pairing_rejects_duplicate_and_orphan():
    for events in [[(0,mido.Message('note_on',note=52)),(1,mido.Message('note_on',note=52))],
                   [(1,mido.Message('note_off',note=52))]]:
        with pytest.raises(ValueError):t.validate_notes(t.schedule(events,120),120)


def test_full_reproduction_and_no_overwrite(tmp_path):
    before={key:t.pitch.sha(ROOT/row['path']) for key,row in json.loads(REQUEST.read_text())['sources'].items()}
    a=t.run(REQUEST,tmp_path/'a',project_root=ROOT)
    b=t.run(REQUEST,tmp_path/'b',project_root=ROOT)
    assert a==b and len(a['hashes'])==12
    for name in a['hashes']:
        assert (tmp_path/'a'/name).read_bytes()==(tmp_path/'b'/name).read_bytes()
        midi=mido.MidiFile(tmp_path/'a'/name)
        assert all(t.absolute(track)[-1][0]==30720 for track in midi.tracks)
    with pytest.raises(FileExistsError):t.run(REQUEST,tmp_path/'a',project_root=ROOT)
    assert before=={key:t.pitch.sha(ROOT/row['path']) for key,row in json.loads(REQUEST.read_text())['sources'].items()}
