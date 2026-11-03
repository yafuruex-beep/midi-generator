"""Plans do not import or execute a MIDI generation pipeline."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import jsonschema

from midi_generator.api import (create_reference_generation_plan as create,
    save_reference_generation_plan as save, load_reference_generation_plan as load)
from midi_generator.reference_analysis.pattern_map import _identity

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def map_path(tmp_path):
    bars=[]
    for i in range(1,7):
        bars.append({'bar_index':i,'start_time':(i-1)*2,'end_time':i*2,
            'pattern_id':['P001','P002','P003','P004','P001','P002'][i-1],
            'partial':i==6,'activity_score':.5,'rms':.1,'onset_count':8,'onset_density':4,
            'silence_candidates':[],'confidence':{'bar_grid':None,'repetition':None}})
    d={'schema_version':'1.1','analyzer_version':'7.1-bpm.1','reference_id':'sha256:'+'a'*64,
       'source':{'sha256':'a'*64},'start_time':0,'end_time':12,'bars':bars,
       'pattern_sequence':[b['pattern_id'] for b in bars],
       'reference_tempo':{'bpm':120,'source':'estimated','confidence':None},
       'effective_tempo':{'bpm':120,'source':'estimated','confidence':None},
       'time_signature':{'value':[4,4],'confidence':None,'source':'user'},
       'bar_grid':{'origin_seconds':0,'confidence':None,'downbeat_verified':False,'source':'user'},
       'beat_positions':[],'silence_candidates':[],'warnings':['Test grid is unverified.']}
    d['map_id']=_identity(d)
    path=tmp_path/'map.json';path.write_text(json.dumps(d));return path


def edit(path,fn):
    d=json.loads(path.read_text());fn(d);d['map_id']=_identity(d);path.write_text(json.dumps(d))


def plan(path,**options):
    return create(path,**({'export_bpm':105,'bar_range':[1,6],'section':'verse',
        'time_signature':[4,4]}|options))


def test_normal_roundtrip_schema_and_no_source_changes(map_path,tmp_path,monkeypatch):
    import midi_generator.api as api
    monkeypatch.setattr(api,'generate',lambda *a,**k:pytest.fail('No generation allowed'))
    before=map_path.read_bytes();result=plan(map_path)
    assert result['excluded_partial_bars']==[6] and result['included_reference_bars']==[1,2,3,4,5]
    assert result['total_export_beats']==20
    assert not result['execution']['eligible'] and not result['execution']['implemented']
    assert result['blocks'][0]['generation_conditions']['section']=='verse'
    assert result['blocks'][0]['action']=='generate_pair'
    schema=json.loads((ROOT/'schemas/reference_generation_plan.schema.json').read_text())
    jsonschema.Draft202012Validator(schema).validate(result)
    dest=tmp_path/'nested/plan.json';save(result,dest);assert load(dest)==result
    with pytest.raises(FileExistsError):save(result,dest)
    with pytest.raises(ValueError):save(result,map_path)
    assert map_path.read_bytes()==before
    assert not list(tmp_path.rglob('*.mid'))


def test_explicit_reuse_and_no_implicit_merging(map_path):
    edit(map_path,lambda d:d['bars'][5].update(partial=False))
    result=plan(map_path,repeat_blocks=[{'source':[1,2],'reuse':[5,6]}])
    assert len(result['blocks'])==4
    a,b=result['blocks'][0],result['blocks'][-1]
    assert a['pattern_sequence']==b['pattern_sequence']==['P001','P002']
    assert a['reuse_key']==b['reuse_key'] and b['reuse_from']==a['block_id']
    assert b['action']=='reuse_pair' and b['export_start_beat']==16
    independent=plan(map_path)
    assert all(b['action']=='generate_pair' for b in independent['blocks'])
    assert len({b['reuse_key'] for b in independent['blocks']})==6


def test_export_bpm_changes_seconds_not_beats_or_reference_bpm(map_path):
    a,b=plan(map_path,export_bpm=90),plan(map_path,export_bpm=150)
    assert a['reference_tempo']==b['reference_tempo']=={'bpm':120,'source':'estimated','confidence':None}
    assert [(x['export_start_beat'],x['export_length_beats']) for x in a['blocks']]==[(x['export_start_beat'],x['export_length_beats']) for x in b['blocks']]
    assert a['plan_id']!=b['plan_id'] and a['blocks'][0]['reuse_key']!=b['blocks'][0]['reuse_key']


def test_confirmation_gates_and_unknown_reference(map_path):
    unconfirmed=plan(map_path,reference_bpm_confirmed=True)
    assert unconfirmed['execution']['blockers']==['bar_start_unconfirmed']
    approved=plan(map_path,reference_bpm_confirmed=True,bar_start_confirmed=True)
    assert approved['execution']['eligible'] and approved['reference_tempo']['confidence'] is None
    edit(map_path,lambda d:(d['reference_tempo'].update(bpm=None),d['effective_tempo'].update(bpm=None)))
    unknown=plan(map_path,reference_bpm_confirmed=True,bar_start_confirmed=True)
    assert not unknown['execution']['eligible'] and 'reference_bpm_unknown' in unknown['execution']['blockers']


@pytest.mark.parametrize('options',[
    {'bar_range':[0,2]},{'bar_range':[3,2]},{'bar_range':[1,7]},
    {'bar_range':[True,2]},{'bar_range':[6,6]},
    {'time_signature':[3,4]},{'export_bpm':float('nan')},{'section':''},
    {'reference_bpm_confirmed':'yes'},
    {'repeat_blocks':[{'source':[1,2],'reuse':[5,6]}]},
    {'repeat_blocks':[{'source':[1,1],'reuse':[5,6]}]},
    {'repeat_blocks':[{'source':[1,2],'reuse':[3,4]}]},
    {'repeat_blocks':[{'source':[1,2],'reuse':[8,9]}]},
    {'repeat_blocks':[{'source':[3,4],'reuse':[1,2]}]},
    {'repeat_blocks':[{'source':[1,1],'reuse':[5,5],'section':'chorus'}]},
])
def test_invalid_ranges_meters_repetition_and_conditions(map_path,options):
    with pytest.raises(ValueError):plan(map_path,**options)


def test_reuse_overlap_duplicate_and_legacy(map_path):
    edit(map_path,lambda d:d['bars'][5].update(partial=False))
    with pytest.raises(ValueError,match='overlap'):
        plan(map_path,repeat_blocks=[{'source':[1,2],'reuse':[5,6]},{'source':[2,2],'reuse':[6,6]}])
    edit(map_path,lambda d:(d.update(schema_version='1.0',analyzer_version='7.1-step3.1'),d.pop('reference_tempo')))
    with pytest.warns(FutureWarning):
        legacy=plan(map_path,reference_bpm_confirmed=True,bar_start_confirmed=True)
    assert not legacy['execution']['eligible'] and legacy['reference_tempo']['source']=='legacy_unverified'


def test_cli_matches_api_without_generation(map_path,tmp_path,monkeypatch,capsys):
    import sys
    from midi_generator.cli import main
    config=tmp_path/'input.json';out=tmp_path/'plan.json'
    config.write_text(json.dumps({'pattern_map_path':str(map_path),'export_bpm':105,
        'bar_range':[1,5],'section':'verse','time_signature':[4,4]}))
    monkeypatch.setattr(sys,'argv',['midi-generator','reference','plan','--config',str(config),'--output',str(out)])
    main();summary=json.loads(capsys.readouterr().out)
    assert not summary['execution']['implemented']
    assert load(out)==plan(map_path,bar_range=[1,5])
    assert not list(tmp_path.rglob('*.mid'))


def test_tampered_plan_readback_rejected(map_path,tmp_path):
    p=tmp_path/'plan.json';save(plan(map_path),p)
    d=json.loads(p.read_text());d['export_bpm']=90;p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='identity'):load(p)
