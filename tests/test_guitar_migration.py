from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import jsonschema
import pytest

from midi_generator.api import (
    add_tags, analyze_catalog_guitars, get_guitar_riff_analysis, get_tags,
    reconcile_guitar_sources, recover_guitar_references, scan_midi_catalog, search_patterns,
)
from midi_generator.cli import main
from midi_generator.core.instruments import classify_source_path
from midi_generator.storage import guitar_migration as migration
from midi_generator.storage.catalog import REFERENCE_LOCK
from midi_generator.storage.sources import organize_sources
from test_guitar_analysis import repeated, write_midi


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def snapshot(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def library(tmp_path):
    path=write_midi(tmp_path/'data/sources/guitar/metalcore/original.mid',repeated(),end_beats=16)
    write_midi(tmp_path/'data/sources/drums/metalcore/drums.mid',[(0,36,.25,100,0,9)],end_beats=4)
    scan_midi_catalog(project_root=tmp_path)
    catalog=tmp_path/'data/catalog/midi_catalog.json'
    add_tags('original.mid',section='intro',style='metalcore',tags=['guitar_backing','keep'],catalog_path=catalog)
    data=read(catalog)
    guitar=next(r for r in data['files'] if r['path'].endswith('original.mid'))
    guitar['provenance']={'pack':'Fixture','license':'synthetic'}
    guitar['user_notes']='retain custom fields'
    save(catalog,data)
    analyze_catalog_guitars(project_root=tmp_path)
    return tmp_path,path,catalog,tmp_path/'data/catalog/guitar_riff_analysis.json'


def move_manually(path, root, role='backing', name='renamed.mid'):
    new=root/'data/sources/guitar'/role/name
    new.parent.mkdir(parents=True,exist_ok=True)
    path.rename(new)
    return new


@pytest.mark.parametrize('relative,role,genre',[
    ('data/sources/guitar/backing/riff.mid','backing',None),
    ('data/sources/guitar/lead/phrase.mid','lead',None),
    ('data/sources/guitar/metalcore/riff.mid',None,'metalcore'),
    ('data/sources/guitar/hardcore/phrase.mid',None,'hardcore'),
    ('data/sources/guitar/other/Lead_in_name.mid',None,'other'),
    ('data/sources/guitar/metalcore/backing/riff.mid',None,'metalcore'),
    ('data/sources/guitar/backing/metalcore/riff.mid',None,None),
    ('data/sources/guitar/Backing_in_name.mid',None,None),
])
def test_roles_come_only_from_the_formal_direct_role_directory(relative,role,genre):
    result=classify_source_path(relative)
    assert result['instrument']=='guitar'
    assert result['guitar_role']==role
    assert result['genre_directory']==genre


def test_role_search_is_strict_and_genre_tags_are_preserved(tmp_path):
    for path,pitch in [('backing/anything.mid',36),('lead/anything.mid',72),('metalcore/Lead.mid',40)]:
        write_midi(tmp_path/'data/sources/guitar'/path,repeated(pitch=pitch))
    scan_midi_catalog(project_root=tmp_path)
    catalog=tmp_path/'data/catalog/midi_catalog.json'
    for file in ['data/sources/guitar/backing/anything.mid','data/sources/guitar/lead/anything.mid','data/sources/guitar/metalcore/Lead.mid']:
        add_tags(file,style='metalcore',tags='guitar_backing',catalog_path=catalog)
    records=read(catalog)['files']
    assert {r['guitar_role'] for r in records}=={'backing','lead',None}
    assert len(search_patterns(catalog_path=catalog,instrument='guitar'))==3
    backing=search_patterns(catalog_path=catalog,instrument='guitar',guitar_role='backing',style='metalcore')
    lead=search_patterns(catalog_path=catalog,guitar_role='lead')
    assert len(backing)==len(lead)==1
    assert '/backing/' in backing[0]['file_path']
    assert '/lead/' in lead[0]['file_path']
    assert backing[0]['guitar_role']=='backing' and lead[0]['guitar_role']=='lead'
    assert all('backing' not in r['tags']['style'] and 'lead' not in r['tags']['style'] for r in records)
    assert next(r for r in records if '/metalcore/' in r['path'])['genre_directory']=='metalcore'
    with pytest.raises(ValueError,match='instrument'): search_patterns(catalog_path=catalog,instrument='drums',guitar_role='backing')
    with pytest.raises(ValueError,match='backing or lead'): search_patterns(catalog_path=catalog,guitar_role='rhythm')


def test_unique_move_dry_run_does_not_write_then_apply_preserves_every_feature(library):
    root,old,catalog,analysis=library
    identity=old.relative_to(root).as_posix()
    old_catalog,old_report=read(catalog),read(analysis)
    old_entry=old_report['sources'][0]
    old_record=next(r for r in old_catalog['files'] if r['file_id']==identity)
    new=move_manually(old,root)
    before=snapshot(root)
    plan=reconcile_guitar_sources(project_root=root)
    assert snapshot(root)==before
    assert plan['dry_run'] and plan['change_count']==1 and plan['unresolved_count']==0
    assert plan['updates'][0]['old_source_id']==plan['updates'][0]['new_source_id']==identity
    assert plan['updates'][0]['new_path']==new.relative_to(root).as_posix()
    result=reconcile_guitar_sources(project_root=root,apply=True)
    backup=Path(result['backup_directory'])
    assert (backup/'0.before.json').read_bytes()==before['data/catalog/midi_catalog.json']
    assert (backup/'1.before.json').read_bytes()==before['data/catalog/guitar_riff_analysis.json']
    assert read(backup/'manifest.json')['state']=='complete'
    record=next(r for r in read(catalog)['files'] if r['file_id']==identity)
    assert record['path']==new.relative_to(root).as_posix()
    assert record['guitar_role']=='backing' and record['genre_directory'] is None
    assert record['legacy_genre_directory']=='metalcore'
    assert record['tags']==old_record['tags'] and record['tag_metadata']==old_record['tag_metadata']
    assert record['provenance']==old_record['provenance'] and record['user_notes']=='retain custom fields'
    assert record['path_history']==[identity]
    migrated=get_guitar_riff_analysis(identity,analysis_path=analysis)
    assert {k:v for k,v in migrated.items() if k not in {'file_path','guitar_role'}}=={k:v for k,v in old_entry.items() if k not in {'file_path','guitar_role'}}
    assert migrated['guitar_role']=='backing'
    assert new.read_bytes()==before[new.relative_to(root).as_posix()] and not old.exists()
    assert not list(catalog.parent.glob('*lock'))
    jsonschema.validate(read(analysis),read(Path(__file__).resolve().parents[1]/'schemas/guitar_riff_analysis.schema.json'))
    scan_midi_catalog(project_root=root)
    rescanned=next(r for r in read(catalog)['files'] if r['file_id']==identity)
    assert rescanned['path_history']==[identity] and rescanned['legacy_genre_directory']=='metalcore'
    assert rescanned['tags']==old_record['tags'] and rescanned['user_notes']=='retain custom fields'
    assert read(analysis)['sources'][0]==migrated
    assert search_patterns(catalog_path=catalog,instrument='guitar',guitar_role='backing',subdivision='sixteenth')[0]['file_id']==identity
    no_op=reconcile_guitar_sources(project_root=root,apply=True)
    assert no_op['change_count']==0 and no_op['backup_directory'] is None


def test_current_paths_are_not_reclassified_by_name_tags_or_analysis(library):
    root,_,catalog,_=library
    before=snapshot(root)
    plan=reconcile_guitar_sources(project_root=root)
    assert plan['change_count']==plan['unresolved_count']==0
    assert plan['unclassified_count']==1
    assert snapshot(root)==before
    assert search_patterns(catalog_path=catalog,guitar_role='backing')==[]


def duplicate_library(root):
    first=write_midi(root/'data/sources/guitar/metalcore/one.mid',repeated())
    second=first.with_name('two.mid');second.write_bytes(first.read_bytes())
    scan_midi_catalog(project_root=root)
    catalog=root/'data/catalog/midi_catalog.json'
    add_tags('one.mid',section='intro',style='metalcore',tags='one',catalog_path=catalog)
    add_tags('two.mid',section='verse',style='hardcore',tags='two',catalog_path=catalog)
    analyze_catalog_guitars(project_root=root)
    return first,second,catalog


def test_identical_contents_are_not_merged_and_explicit_mapping_resolves_ambiguity(tmp_path):
    first,second,catalog=duplicate_library(tmp_path)
    old_ids=[p.relative_to(tmp_path).as_posix() for p in (first,second)]
    new1=move_manually(first,tmp_path,'backing','one.mid')
    new2=move_manually(second,tmp_path,'lead','two.mid')
    before=snapshot(tmp_path)
    plan=reconcile_guitar_sources(project_root=tmp_path)
    assert plan['unresolved_count']==2 and plan['change_count']==0
    assert all(p['reason']=='ambiguous_hash_match' for p in plan['unresolved'])
    with pytest.raises(ValueError,match='Unresolved'): reconcile_guitar_sources(project_root=tmp_path,apply=True)
    assert snapshot(tmp_path)==before
    mapping={old_ids[0]:new1.relative_to(tmp_path).as_posix(),old_ids[1]:new2.relative_to(tmp_path).as_posix()}
    reconcile_guitar_sources(project_root=tmp_path,mapping=mapping,apply=True)
    records=read(catalog)['files']
    assert len(records)==2 and {r['file_id'] for r in records}==set(old_ids)
    assert next(r for r in records if r['file_id']==old_ids[0])['tags']['custom']==['one']
    assert next(r for r in records if r['file_id']==old_ids[1])['tags']['custom']==['two']


def test_two_old_identities_and_one_destination_cannot_steal_each_others_metadata(tmp_path):
    first,second,_=duplicate_library(tmp_path)
    move_manually(first,tmp_path,'backing','only.mid')
    second.unlink()
    plan=reconcile_guitar_sources(project_root=tmp_path)
    assert plan['change_count']==0 and plan['unresolved_count']==2


def test_one_old_and_multiple_matching_destinations_needs_user_mapping(library):
    root,old,_,_=library
    new=move_manually(old,root)
    copy=root/'data/sources/guitar/lead/copy.mid';copy.parent.mkdir();copy.write_bytes(new.read_bytes())
    plan=reconcile_guitar_sources(project_root=root)
    assert plan['unresolved'][0]['reason']=='ambiguous_hash_match'


@pytest.mark.parametrize('kind',['changed_current','changed_destination','missing','missing_analysis','orphan_analysis','duplicate_id','duplicate_analysis_id','hash_conflict'])
def test_inconsistencies_stop_all_updates_without_writes(library,kind):
    root,old,catalog,analysis=library
    if kind=='changed_current': old.write_bytes(old.read_bytes()+b'changed')
    elif kind=='changed_destination':
        new=move_manually(old,root);new.write_bytes(new.read_bytes()+b'changed')
    elif kind=='missing': old.unlink()
    elif kind=='missing_analysis': analysis.unlink()
    elif kind=='orphan_analysis':
        data=read(analysis);entry=deepcopy(data['sources'][0]);entry['source_id']='unknown';data['sources'].append(entry);save(analysis,data)
    elif kind=='duplicate_id':
        data=read(catalog);data['files'][1]['file_id']=data['files'][0]['file_id'];save(catalog,data)
    elif kind=='duplicate_analysis_id':
        data=read(analysis);data['sources'].append(deepcopy(data['sources'][0]));save(analysis,data)
    else:
        data=read(catalog);next(r for r in data['files'] if r.get('instrument')=='guitar')['source_sha256']='0'*64;save(catalog,data)
    before=snapshot(root)
    plan=reconcile_guitar_sources(project_root=root)
    assert plan['unresolved_count']>0
    with pytest.raises(ValueError,match='Unresolved'): reconcile_guitar_sources(project_root=root,apply=True)
    assert snapshot(root)==before


def test_mapping_hash_mismatch_conflict_and_unknown_id_are_reported(library):
    root,old,_,_=library
    identity=old.relative_to(root).as_posix()
    new=move_manually(old,root)
    bad=root/'data/sources/guitar/backing/bad.mid';bad.write_bytes(b'bad')
    plan=reconcile_guitar_sources(project_root=root,mapping={identity:bad.relative_to(root).as_posix()})
    assert plan['unresolved'][0]['reason']=='mapped_hash_mismatch'
    plan=reconcile_guitar_sources(project_root=root,mapping={'unknown':new.relative_to(root).as_posix()})
    assert any(p['reason']=='unknown_mapping_source_id' for p in plan['unresolved'])
    with pytest.raises(ValueError,match='outside'): reconcile_guitar_sources(project_root=root,mapping={identity:'../elsewhere.mid'})


def test_scan_prevents_accidental_id_reassignment_before_reconciliation(library):
    root,old,catalog,_=library
    move_manually(old,root)
    before=catalog.read_bytes()
    with pytest.raises(ValueError,match='reconcile-guitars'): scan_midi_catalog(project_root=root)
    assert catalog.read_bytes()==before


def test_registered_destinations_are_never_merged_by_matching_hash(library):
    root,old,catalog,_=library
    new=move_manually(old,root)
    data=read(catalog);duplicate=deepcopy(next(r for r in data['files'] if r.get('instrument')=='guitar'))
    duplicate.update(path=new.relative_to(root).as_posix(),file_id='another-id')
    data['files'].append(duplicate);save(catalog,data)
    plan=reconcile_guitar_sources(project_root=root)
    assert any(p['reason']=='destination_already_registered' for p in plan['unresolved'])


def test_metadata_failure_rolls_back_both_json_files_and_keeps_verified_backups(library,monkeypatch):
    root,old,catalog,analysis=library
    move_manually(old,root)
    before=[catalog.read_bytes(),analysis.read_bytes()]
    real=migration._install
    def fail_once(staged,target):
        if target==analysis: raise OSError('simulated analysis write failure')
        real(staged,target)
    monkeypatch.setattr(migration,'_install',fail_once)
    with pytest.raises(ValueError,match='rolled_back'): reconcile_guitar_sources(project_root=root,apply=True)
    assert [catalog.read_bytes(),analysis.read_bytes()]==before
    backup=next((root/'data/backups').iterdir())
    assert read(backup/'manifest.json')['state']=='rolled_back'
    assert (backup/'0.before.json').read_bytes()==before[0]
    assert not (catalog.parent/REFERENCE_LOCK).exists()


def test_abrupt_exit_keeps_a_lock_and_recovery_restores_before_images(library,monkeypatch):
    root,old,catalog,analysis=library
    new=move_manually(old,root)
    before=[catalog.read_bytes(),analysis.read_bytes()]
    real=migration._install
    class AbruptExit(BaseException): pass
    def interrupt(staged,target):
        if target==analysis: raise AbruptExit()
        real(staged,target)
    monkeypatch.setattr(migration,'_install',interrupt)
    with pytest.raises(AbruptExit): reconcile_guitar_sources(project_root=root,apply=True)
    lock=catalog.parent/REFERENCE_LOCK
    assert lock.exists()
    with pytest.raises(ValueError,match='locked'): get_tags('original.mid',catalog_path=catalog)
    with pytest.raises(ValueError,match='locked'): get_guitar_riff_analysis('original.mid',analysis_path=analysis)
    backup=Path(read(lock)['backup_directory'])
    monkeypatch.setattr(migration,'_install',real)
    with pytest.raises(ValueError,match='live'): recover_guitar_references(backup,project_root=root)
    monkeypatch.setattr(migration,'_pid_active',lambda pid:False)
    result=recover_guitar_references(backup,project_root=root)
    assert result['recovered'] and result['midi_files_moved']==0
    assert [catalog.read_bytes(),analysis.read_bytes()]==before and not lock.exists()
    assert new.exists() and not old.exists()


def test_recovery_will_not_overwrite_later_user_edits(library):
    root,old,catalog,_=library
    move_manually(old,root)
    result=reconcile_guitar_sources(project_root=root,apply=True)
    data=read(catalog);data['user_edit']='keep';save(catalog,data)
    before=catalog.read_bytes()
    with pytest.raises(ValueError,match='edited after'): recover_guitar_references(result['backup_directory'],project_root=root)
    assert catalog.read_bytes()==before


def test_cli_preview_apply_role_search_and_mapping_file(library,monkeypatch,capsys):
    root,old,_,_=library
    new=move_manually(old,root)
    mapping=root/'mapping.json';save(mapping,{old.relative_to(root).as_posix():new.relative_to(root).as_posix()})
    for mode in ['--dry-run','--apply']:
        monkeypatch.setattr(sys,'argv',['midi-generator','source','reconcile-guitars',mode,'--project-root',str(root),'--mapping','mapping.json'])
        main();result=json.loads(capsys.readouterr().out)
        assert result['change_count']==1 and result['dry_run']==(mode=='--dry-run')
    monkeypatch.setattr(sys,'argv',['midi-generator','search','--instrument','guitar','--guitar-role','backing','--catalog',str(root/'data/catalog/midi_catalog.json')])
    main();assert json.loads(capsys.readouterr().out)[0]['guitar_role']=='backing'
    monkeypatch.setattr(sys,'argv',['midi-generator','source','reconcile-guitars','--recover',result['backup_directory'],'--project-root',str(root)])
    main();assert json.loads(capsys.readouterr().out)['recovered']
    assert new.exists() and not old.exists()


def test_old_migrate_command_does_not_create_guitar_genre_folders_or_move_guitars(library):
    root,old,_,_=library
    before=old.read_bytes()
    with pytest.raises(ValueError,match='manual'): organize_sources(project_root=root,instrument='guitar',genre='metalcore')
    assert old.read_bytes()==before


def test_failed_rollback_leaves_a_recoverable_lock_and_backups(library,monkeypatch):
    root,old,catalog,analysis=library
    move_manually(old,root)
    before=[catalog.read_bytes(),analysis.read_bytes()]
    real=migration._install
    calls={'catalog':0}
    def fail(staged,target):
        if target==analysis: raise OSError('cannot install analysis')
        if target==catalog:
            calls['catalog']+=1
            if calls['catalog']>1: raise OSError('cannot roll back catalog')
        real(staged,target)
    monkeypatch.setattr(migration,'_install',fail)
    with pytest.raises(ValueError,match='recovery_required'): reconcile_guitar_sources(project_root=root,apply=True)
    lock=catalog.parent/REFERENCE_LOCK
    backup=Path(read(lock)['backup_directory'])
    assert not read(lock)['active']
    assert read(backup/'manifest.json')['state']=='recovery_required'
    monkeypatch.setattr(migration,'_install',real)
    recover_guitar_references(backup,project_root=root)
    assert [catalog.read_bytes(),analysis.read_bytes()]==before
    assert not lock.exists()


def test_changes_after_planning_are_not_overwritten(library,monkeypatch):
    root,old,catalog,_=library
    new=move_manually(old,root)
    real=migration._commit
    def changed(targets,observed,project_root,backup_directory):
        new.write_bytes(new.read_bytes()+b'changed while planning')
        return real(targets,observed,project_root,backup_directory)
    before=catalog.read_bytes()
    monkeypatch.setattr(migration,'_commit',changed)
    with pytest.raises(ValueError,match='Source changed during planning'): reconcile_guitar_sources(project_root=root,apply=True)
    assert catalog.read_bytes()==before
    assert new.read_bytes().endswith(b'changed while planning')


def test_reusing_an_old_path_cannot_duplicate_the_stable_source_id(library):
    root,old,catalog,_=library
    new=move_manually(old,root)
    reconcile_guitar_sources(project_root=root,apply=True)
    old.write_bytes(new.read_bytes())
    before=catalog.read_bytes()
    with pytest.raises(ValueError,match='ID collision'): scan_midi_catalog(project_root=root)
    assert catalog.read_bytes()==before


def test_original_and_copy_are_not_implicitly_reassigned(library):
    root,old,_,_=library
    target=root/'data/sources/guitar/backing/copy.mid';target.parent.mkdir();target.write_bytes(old.read_bytes())
    before=snapshot(root)
    plan=reconcile_guitar_sources(project_root=root)
    assert plan['change_count']==0
    identity=old.relative_to(root).as_posix()
    plan=reconcile_guitar_sources(project_root=root,mapping={identity:target.relative_to(root).as_posix()})
    assert plan['unresolved'][0]['reason']=='original_still_exists_copy_not_relocation'
    assert snapshot(root)==before


def test_recovery_refuses_tampered_backups(library):
    root,old,_,_=library
    move_manually(old,root)
    result=reconcile_guitar_sources(project_root=root,apply=True)
    backup=Path(result['backup_directory'])
    (backup/'0.before.json').write_text('{}')
    with pytest.raises(ValueError,match='hash is invalid'): recover_guitar_references(backup,project_root=root)


def test_even_one_missing_file_needs_mapping_when_another_identity_has_identical_content(tmp_path):
    first,second,_=duplicate_library(tmp_path)
    identity=first.relative_to(tmp_path).as_posix()
    new=move_manually(first,tmp_path)
    plan=reconcile_guitar_sources(project_root=tmp_path)
    assert plan['change_count']==0 and plan['unresolved_count']==1
    assert plan['unresolved'][0]['reason']=='ambiguous_hash_match'
    result=reconcile_guitar_sources(project_root=tmp_path,mapping={identity:new.relative_to(tmp_path).as_posix()},apply=True)
    assert result['change_count']==1 and second.exists()


def test_project_root_relocation_can_refresh_only_analysis_context_without_reanalysis(library):
    root,_,catalog,analysis=library
    data=read(analysis);features=deepcopy(data['sources'])
    data['project_root']='/previous/project';data['catalog_file']='/previous/project/midi_catalog.json'
    save(analysis,data)
    before=snapshot(root)
    plan=reconcile_guitar_sources(project_root=root)
    assert plan['change_count']==0 and len(plan['analysis_context_changes'])==2
    assert snapshot(root)==before
    reconcile_guitar_sources(project_root=root,apply=True)
    updated=read(analysis)
    assert updated['sources']==features and updated['project_root']==str(root.resolve())
    assert updated['catalog_file']==str(catalog.resolve())


def test_unrecognized_path_history_is_not_silently_rewritten(library):
    root,old,catalog,_=library
    data=read(catalog)
    next(r for r in data['files'] if r.get('instrument')=='guitar')['path_history']='custom old format'
    save(catalog,data)
    move_manually(old,root)
    before=catalog.read_bytes()
    plan=reconcile_guitar_sources(project_root=root)
    assert plan['unresolved'][0]['reason']=='invalid_history_metadata'
    assert catalog.read_bytes()==before


def test_formal_role_search_uses_folder_even_when_cached_role_is_wrong(tmp_path):
    write_midi(tmp_path/'data/sources/guitar/backing/free_name.mid',repeated())
    scan_midi_catalog(project_root=tmp_path)
    catalog=tmp_path/'data/catalog/midi_catalog.json'
    data=read(catalog)
    data['files'][0]['instrument']=None
    data['files'][0]['guitar_role']='lead'
    save(catalog,data)
    result=search_patterns(catalog_path=catalog,guitar_role='backing')
    assert len(result)==1 and result[0]['guitar_role']=='backing'
    assert search_patterns(catalog_path=catalog,guitar_role='lead')==[]


def test_interrupted_recovery_can_be_retried_in_the_same_process(library,monkeypatch):
    root,old,_,analysis=library
    move_manually(old,root)
    result=reconcile_guitar_sources(project_root=root,apply=True)
    backup=Path(result['backup_directory'])
    real=migration._install
    def fail(staged,target):
        if target==analysis: raise OSError('temporary restore failure')
        real(staged,target)
    monkeypatch.setattr(migration,'_install',fail)
    with pytest.raises(ValueError,match='Recovery incomplete'): recover_guitar_references(backup,project_root=root)
    lock=analysis.parent/REFERENCE_LOCK
    assert lock.exists() and not read(lock)['active']
    monkeypatch.setattr(migration,'_install',real)
    assert recover_guitar_references(backup,project_root=root)['recovered']
    assert not lock.exists()
