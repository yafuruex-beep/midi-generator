import hashlib
import json
from pathlib import Path
import sys

import jsonschema
import mido
import pytest

from midi_generator.api import GenerationRequest, generate, scan_midi_catalog, add_tags, analyze_catalog_guitars
from midi_generator.cli import main
from midi_generator.modules.guitar.backing import classify_guitar_role
from midi_generator.modules.guitar.render import render_backing
from midi_generator.modules.guitar.sync import (
    analyze_drum_sync, attack_pattern, load_sync_config, rhythm_sync_score,
)
from midi_generator.modules.guitar.timeline import read_timeline
from test_guitar_analysis import write_midi


KICKS = [0, .75, 1.5, 2, 2.75, 3.5]


def drum_notes(bars=8):
    return [(bar * 4 + beat, 36, .125, 100, 0, 9) for bar in range(bars) for beat in KICKS] + [
        (bar * 4 + beat, 38, .125, 110, 0, 9) for bar in range(bars) for beat in (1, 3)]


def guitar_notes(bars=4, positions=KICKS):
    return [(bar * 4 + beat, note, .125, 70 + note % 20) for bar in range(bars) for beat in positions for note in (36, 43, 48)]


def request(**changes):
    data = {"instrument": "guitar", "guitar_role": "backing", "section": "intro", "tempo": 120,
            "bars": 8, "time_signature": [4, 4], "style": ["metalcore"], "seed": 42,
            "sync_to_drums": "output/drums.mid"}
    return {**data, **changes}


@pytest.fixture
def library(tmp_path):
    write_midi(tmp_path / "output/drums.mid", drum_notes(), end_beats=32)
    write_midi(tmp_path / "data/sources/guitar/metalcore/matched.mid", guitar_notes(), end_beats=16)
    write_midi(tmp_path / "data/sources/guitar/metalcore/dense.mid", guitar_notes(positions=[i / 4 for i in range(16)]), end_beats=16)
    scan_midi_catalog(project_root=tmp_path)
    catalog = tmp_path / "data/catalog/midi_catalog.json"
    for name in ("matched.mid", "dense.mid"):
        add_tags(name, section="intro", style="metalcore", tags=["guitar_backing"], catalog_path=catalog)
    analyze_catalog_guitars(project_root=tmp_path)
    return tmp_path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.mark.parametrize("tpb", [96, 480, 960])
def test_drum_grid_normalization_and_snare_positions(tmp_path, tpb):
    path = write_midi(tmp_path / f"drums{tpb}.mid", drum_notes(2), tpb=tpb, end_beats=8)
    data = analyze_drum_sync(path, tempo=120, bars=2)
    assert [n["beat"] for n in data["kick"]] == [bar * 4 + b for bar in range(2) for b in KICKS]
    assert data["bar_patterns"][0] == {"bar": 1, "kick_steps": [0, 3, 6, 8, 11, 14], "snare_steps": [4, 12]}
    assert data["backbeat"] == "standard"
    assert data["kick_period_bars"] == 1


@pytest.mark.parametrize("meter,beats,steps", [((3, 4), 3, 12), ((6, 8), 3, 12), ((5, 8), 2.5, 10)])
def test_non_four_four_grids(tmp_path, meter, beats, steps):
    notes = [(bar * beats, 36, .125, 100, 0, 9) for bar in range(2)]
    path = write_midi(tmp_path / "meter.mid", notes, meters=[(0, *meter)], end_beats=beats * 2)
    result = analyze_drum_sync(path, tempo=120, bars=2, time_signature=meter)
    assert result["steps_per_bar"] == steps
    assert result["bar_patterns"][1]["kick_steps"] == [0]


def test_chords_are_one_attack_and_dense_sources_do_not_win(library):
    drum = analyze_drum_sync(library / "output/drums.mid", tempo=120, bars=8)
    scores = {}
    for name in ("matched", "dense"):
        data = read_timeline(library / f"data/sources/guitar/metalcore/{name}.mid", set())
        attacks = attack_pattern(data, length_beats=32, source_beats=16)
        scores[name] = rhythm_sync_score(drum, attacks)
    assert scores["matched"]["guitar_attack_count"] == 48
    assert scores["matched"]["kick_recall"] == 1
    assert scores["matched"]["guitar_precision"] == 1
    assert scores["dense"]["kick_recall"] == 1
    assert scores["dense"]["guitar_precision"] < 1
    assert scores["matched"]["overall_score"] > scores["dense"]["overall_score"]
    assert scores["matched"]["syncopation_match"] == 1
    assert scores["matched"]["rest_compatibility"] > scores["dense"]["rest_compatibility"]
    result = generate(request(), project_root=library)
    assert result.metadata["selected_source"]["path"].endswith("matched.mid")


def test_sustained_notes_reduce_rest_fit_and_snare_absence_is_neutral(library):
    drum = analyze_drum_sync(library / "output/drums.mid", tempo=120, bars=8)
    data = read_timeline(library / "data/sources/guitar/metalcore/matched.mid", set())
    short = attack_pattern(data, length_beats=32, source_beats=16)
    long = [{**a, "end_beat": min(32, a["beat"] + 1)} for a in short]
    short_score = rhythm_sync_score(drum, short)
    long_score = rhythm_sync_score(drum, long)
    assert short_score["kick_alignment"] == long_score["kick_alignment"]
    assert short_score["rest_compatibility"] > long_score["rest_compatibility"]
    assert short_score["snare_accent_relation"] == .5
    subset = rhythm_sync_score(drum, short[::2])
    assert subset["guitar_precision"] == 1
    assert subset["kick_recall"] == .5
    assert 0 < subset["overall_score"] < 1


def test_generation_preserves_pitch_velocity_chords_tracks_and_inputs(library):
    source = library / "data/sources/guitar/metalcore/matched.mid"
    drum = library / "output/drums.mid"
    before = [digest(source), digest(drum), digest(library / "data/catalog/midi_catalog.json")]
    result = generate(request(), project_root=library)
    midi = mido.MidiFile(result.output_files[0])
    assert midi.length == 16
    assert midi.ticks_per_beat == 480
    assert len(midi.tracks) == 2
    assert any(t.name == "Guitar Backing" for t in midi.tracks)
    assert [m.tempo for t in midi.tracks for m in t if m.type == "set_tempo"] == [500000]
    assert [(m.numerator, m.denominator) for t in midi.tracks for m in t if m.type == "time_signature"] == [(4, 4)]
    output = read_timeline(result.output_files[0], set())
    original = read_timeline(source, set())
    expected = [(n["start_tick"] + loop * 7680, n["note"], n["velocity"], n["end_tick"] - n["start_tick"]) for loop in range(2) for n in original["notes"]]
    actual = [(n["start_tick"], n["note"], n["velocity"], n["end_tick"] - n["start_tick"]) for n in output["notes"]]
    assert actual == expected
    assert output["pairing"]["unmatched_note_offs"] == output["pairing"]["inferred_note_ends"] == 0
    assert output["channels"] == [1]
    assert [digest(source), digest(drum), digest(library / "data/catalog/midi_catalog.json")] == before
    sidecar = json.loads(Path(str(result.output_files[0]) + ".json").read_text())
    assert sidecar["algorithm_version"] == "6.2-A.1"
    assert sidecar["drum_reference"]["sha256"] == digest(drum)
    assert sidecar["selected_source"]["source_id"]
    assert 0 <= sidecar["rhythm_sync"]["overall_score"] <= 1
    assert sum(v["contribution"] for v in sidecar["rhythm_sync"]["score_breakdown"].values()) == pytest.approx(sidecar["rhythm_sync"]["overall_score"])


def test_seed_and_cli_api_are_identical(library, monkeypatch, capsys):
    first = generate(request(), project_root=library)
    second = generate(request(), project_root=library)
    assert first.output_files != second.output_files
    assert first.output_files[0].read_bytes() == second.output_files[0].read_bytes()
    assert first.metadata["selected_source"]["source_id"] == second.metadata["selected_source"]["source_id"]
    config = library / "request.json"
    config.write_text(json.dumps(request()))
    monkeypatch.setattr(sys, "argv", ["midi-generator", "generate", "--config", str(config), "--project-root", str(library), "--output", "output/from_cli.mid"])
    main()
    cli = json.loads(capsys.readouterr().out)
    assert Path(cli["output_files"][0]).read_bytes() == first.output_files[0].read_bytes()
    assert cli["metadata"]["rhythm_sync"] == first.metadata["rhythm_sync"]
    monkeypatch.setattr(sys, "argv", ["midi-generator", "generate", "--instrument", "guitar", "--guitar-role", "backing", "--sync-to-drums", "output/drums.mid", "--tempo", "120", "--bars", "8", "--section", "intro", "--style", "metalcore", "--seed", "42", "--project-root", str(library), "--output", "output/direct.mid"])
    main()
    assert Path(json.loads(capsys.readouterr().out)["output_files"][0]).read_bytes() == first.output_files[0].read_bytes()


@pytest.mark.parametrize("changes,message", [
    ({"guitar_role": "lead"}, "not implemented"),
    ({"guitar_role": "invalid"}, "not implemented"),
    ({"sync_to_drums": "missing.mid"}, "does not exist"),
    ({"sync_to_drums": None}, "requires sync_to_drums"),
    ({"tempo": 160}, "BPM mismatch"),
    ({"time_signature": [3, 4]}, "Time-signature mismatch"),
    ({"section": "verse"}, "mandatory conditions"),
    ({"style": ["hardcore"]}, "mandatory conditions"),
    ({"tags": ["not_present"]}, "mandatory conditions"),
    ({"key": "E"}, "not implemented"),
    ({"generation_options": {"midi_channel": 0}}, "integer from 1 to 16"),
])
def test_invalid_requests_do_not_generate(library, changes, message):
    with pytest.raises(ValueError, match=message):
        generate(request(**changes), project_root=library)
    assert not list((library / "output").glob('guitar*.mid'))


def test_manual_lead_exclusion_and_missing_role_warning(library):
    catalog = library / "data/catalog/midi_catalog.json"
    add_tags("matched.mid", tags="guitar_lead", catalog_path=catalog)
    result = generate(request(), project_root=library)
    assert result.metadata["selected_source"]["path"].endswith("dense.mid")
    assert any("conflicting" in r["reason"] for r in result.metadata["rejected_candidates"])
    c = json.loads(catalog.read_text())
    for record in c['files']:
        if record.get('instrument') == 'guitar': record['tags']['custom'] = []
    catalog.write_text(json.dumps(c))
    result = generate(request(), project_root=library)
    assert result.metadata["selected_source"]["role"]["source"] == "feature_inference"
    assert any("unverified" in warning for warning in result.warnings)


def test_role_directories_and_filenames_are_not_required():
    analysis = {"riff_characteristics": ["power_chord_riff"], "source_context": {"tracks": []},
                "polyphony": {"classification": "chord_based"}, "pitch": {"min_note":36,"max_note":48},
                "rhythm": {"density": "medium"}, "possible_control_notes": []}
    assert classify_guitar_role({"path":"data/sources/guitar/metalcore/lead/anything.mid"}, analysis)["eligible"] is False
    assert classify_guitar_role({"path":"data/sources/guitar/metalcore/backing/arbitrary.mid"}, analysis)["source"] == "directory"
    assert classify_guitar_role({"path":"data/sources/guitar/metalcore/Lead_in_filename.mid"}, analysis)["eligible"] is True
    assert classify_guitar_role({"path":"anything.mid", "tags":{"custom":["guitar_lead"]}}, analysis)["eligible"] is False


def test_phrase_fit_prefers_exact_then_divisor(library):
    for name, bars in [("eight",8),("six",6)]:
        write_midi(library / f"data/sources/guitar/metalcore/{name}.mid",guitar_notes(bars),end_beats=bars*4)
    scan_midi_catalog(project_root=library)
    for name in ["eight.mid","six.mid"]:
        add_tags(name,section="intro",style="metalcore",tags="guitar_backing",catalog_path=library/'data/catalog/midi_catalog.json')
    analyze_catalog_guitars(project_root=library)
    result=generate(request(),project_root=library)
    ranking=result.metadata['candidate_ranking']
    assert ranking[0]['path'].endswith('eight.mid')
    assert ranking[0]['phrase_fit_reason']=='exact_length'
    assert next(c for c in ranking if c['path'].endswith('matched.mid'))['score'] > next(c for c in ranking if c['path'].endswith('six.mid'))['score']


def test_trim_long_notes_and_same_tick_pairing(tmp_path):
    source=write_midi(tmp_path/'source.mid',[(0,36,0),(0,43,8),(0.25,36,1),(0.5,36,1)],end_beats=8)
    data=read_timeline(source,set())
    req=GenerationRequest(instrument='guitar',section='intro',tempo=120,bars=1)
    output, operations, warnings=render_backing(data,req,2,3)
    destination=tmp_path/'out.mid';output.save(destination)
    notes=read_timeline(destination,set())
    assert notes['pairing']['inferred_note_ends']==notes['pairing']['unmatched_note_offs']==0
    assert notes['end_tick']==1920
    assert notes['channels']==[3]
    assert max(n['end_tick'] for n in notes['notes'])==1920
    assert operations['closed_boundary_notes']==1
    assert any(n['start_tick']==n['end_tick']==0 for n in notes['notes'])
    assert warnings


def test_no_overwrite_and_custom_channel(library):
    existing=library/'output/protected.mid';existing.write_bytes(b'keep')
    sidecar=library/'output/protected_1.mid.json';sidecar.write_text('keep')
    result=generate(request(generation_options={'midi_channel':4}),project_root=library,output_path='output/protected.mid')
    assert result.output_files[0].name=='protected_2.mid'
    assert existing.read_bytes()==b'keep' and sidecar.read_text()=='keep'
    assert read_timeline(result.output_files[0],set())['channels']==[4]


def test_variable_tempo_meter_and_corrupt_drums_are_explicit_errors(library):
    drum=library/'output/drums.mid'
    write_midi(drum,drum_notes(),end_beats=32,tempos=[(0,500000),(4,600000)])
    with pytest.raises(ValueError,match='Variable'): generate(request(),project_root=library)
    write_midi(drum,drum_notes(),end_beats=32,meters=[(0,4,4),(4,3,4)])
    with pytest.raises(ValueError,match='Time-signature'): generate(request(),project_root=library)
    drum.write_bytes(b'bad midi')
    with pytest.raises(ValueError,match='Could not read drum'): generate(request(),project_root=library)


def test_grid_and_missing_mapping_are_not_guessed(tmp_path):
    path=write_midi(tmp_path/'offgrid.mid',[(.1,36,.1,100,0,9)],end_beats=4)
    with pytest.raises(ValueError,match='off the straight'): analyze_drum_sync(path,tempo=120,bars=1)
    path=write_midi(tmp_path/'no_kick.mid',[(0,42,.1,100,0,9)],end_beats=4)
    with pytest.raises(ValueError,match='No mapped kick'): analyze_drum_sync(path,tempo=120,bars=1)
    with pytest.raises(ValueError,match='shorter'): analyze_drum_sync(path,tempo=120,bars=2)


def test_missing_meta_uses_defaults_and_config_validation(tmp_path):
    path=write_midi(tmp_path/'default.mid',[(0,36,.1,100,0,9)],end_beats=4)
    midi=mido.MidiFile(path)
    midi.tracks[0]=mido.MidiTrack([mido.MetaMessage('end_of_track',time=1920)]);midi.save(path)
    result=analyze_drum_sync(path,tempo=120,bars=1)
    assert any('default 120' in w for w in result['warnings'])
    with pytest.raises(ValueError,match='BPM mismatch'): analyze_drum_sync(path,tempo=160,bars=1)
    for value in [{'sync_weights':{'kick_alignment':-1}}, {'onset_tolerance_beats':.2}, {'kick_notes':[True]}, {'invalid':1}]:
        config=tmp_path/'bad.json';config.write_text(json.dumps(value))
        with pytest.raises(ValueError): load_sync_config(config)


def test_stale_analysis_and_missing_section_are_not_substituted(library):
    source=library/'data/sources/guitar/metalcore/matched.mid'
    midi=mido.MidiFile(source);midi.tracks[0].append(mido.MetaMessage('text',text='changed'));midi.save(source)
    result=generate(request(),project_root=library)
    assert result.metadata['selected_source']['path'].endswith('dense.mid')
    assert any('Stale' in r['reason'] for r in result.metadata['rejected_candidates'])
    catalog=library/'data/catalog/midi_catalog.json';data=json.loads(catalog.read_text())
    for record in data['files']: record['tags']['section']=[]
    catalog.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='mandatory conditions'): generate(request(),project_root=library)


def test_request_schema_existing_model_and_drum_compatibility(library):
    data=request()
    schema=json.loads((Path(__file__).resolve().parents[1]/'schemas/generation_request.schema.json').read_text())
    jsonschema.validate(data,schema)
    req=GenerationRequest.from_dict(data)
    assert req.options['guitar_role']=='backing'
    assert req.options['sync_to_drums']=='output/drums.mid'
    result=generate(req,project_root=library)
    assert result.output_files
    with pytest.raises(ValueError,match='only supported'):
        generate({**data,'instrument':'drums'},project_root=library)


def test_seed_selects_only_near_top_candidates_with_stable_order(library):
    source=library/'data/sources/guitar/metalcore/matched.mid'
    copy=source.with_name('equal.mid');copy.write_bytes(source.read_bytes())
    scan_midi_catalog(project_root=library)
    catalog=library/'data/catalog/midi_catalog.json'
    add_tags('equal.mid',section='intro',style='metalcore',tags='guitar_backing',catalog_path=catalog)
    analyze_catalog_guitars(project_root=library)
    a=generate(request(seed=0),project_root=library)
    b=generate(request(seed=1),project_root=library)
    assert len(a.metadata['seed_shortlist_ids'])==2
    assert a.metadata['selected_source']['source_id']!=b.metadata['selected_source']['source_id']
    data=json.loads(catalog.read_text());data['files'].reverse();catalog.write_text(json.dumps(data))
    repeat=generate(request(seed=0),project_root=library)
    assert repeat.metadata['selected_source']['source_id']==a.metadata['selected_source']['source_id']
    assert repeat.output_files[0].read_bytes()==a.output_files[0].read_bytes()


def test_analysis_exclusions_never_remove_output_notes(library):
    config=library/'exclude.json';config.write_text(json.dumps({'excluded_notes':[36]}))
    analyze_catalog_guitars(project_root=library,config_path=config)
    result=generate(request(),project_root=library)
    notes=read_timeline(result.output_files[0],set())['notes']
    assert {n['note'] for n in notes}=={36,43,48}


def test_multi_track_guitar_is_preserved_without_channel_merging(library):
    source=library/'data/sources/guitar/metalcore/matched.mid'
    notes=[(bar*4+beat,note,.125,90,index) for bar in range(4) for beat in KICKS for index,note in enumerate((36,43))]
    write_midi(source,notes,tracks=2,end_beats=16)
    scan_midi_catalog(project_root=library);analyze_catalog_guitars(project_root=library)
    result=generate(request(),project_root=library)
    assert result.metadata['selected_source']['path'].endswith('matched.mid')
    assert len(mido.MidiFile(result.output_files[0]).tracks)==3
    assert read_timeline(result.output_files[0],set())['pairing']['inferred_note_ends']==0


def test_source_tempo_is_set_and_source_meter_changes_are_rejected(library):
    source=library/'data/sources/guitar/metalcore/matched.mid'
    write_midi(source,guitar_notes(),end_beats=16,tempos=[(0,375000)])
    scan_midi_catalog(project_root=library);analyze_catalog_guitars(project_root=library)
    result=generate(request(),project_root=library)
    assert mido.MidiFile(result.output_files[0]).length==16
    write_midi(source,guitar_notes(),end_beats=16,meters=[(0,4,4),(4,3,4)])
    scan_midi_catalog(project_root=library);analyze_catalog_guitars(project_root=library)
    result=generate(request(),project_root=library)
    assert result.metadata['selected_source']['path'].endswith('dense.mid')
    assert any('Time-signature' in c['reason'] for c in result.metadata['rejected_candidates'])


def test_incomplete_phrase_has_boundary_warning(library):
    source=library/'data/sources/guitar/metalcore/matched.mid'
    write_midi(source,[(bar*4+beat,note+bar,.125) for bar in range(3) for beat in KICKS for note in (36,43,48)],end_beats=12)
    scan_midi_catalog(project_root=library)
    catalog=library/'data/catalog/midi_catalog.json'
    data=json.loads(catalog.read_text())
    data['files']=[r for r in data['files'] if not r['path'].endswith('dense.mid')]
    catalog.write_text(json.dumps(data));analyze_catalog_guitars(project_root=library)
    result=generate(request(bars=2),project_root=library)
    assert result.metadata['selected_source']['partial_phrase']
    assert any('partial source phrase' in w for w in result.warnings)
    assert mido.MidiFile(result.output_files[0]).length==4


def test_bad_source_and_missing_report_are_isolated(library):
    source=library/'data/sources/guitar/metalcore/matched.mid'
    write_midi(source,guitar_notes()+[(0,60,1,100,0,1)],end_beats=16)
    scan_midi_catalog(project_root=library);analyze_catalog_guitars(project_root=library)
    result=generate(request(),project_root=library)
    assert result.metadata['selected_source']['path'].endswith('dense.mid')
    assert any('Multi-channel' in r['reason'] for r in result.metadata['rejected_candidates'])
    (library/'data/catalog/guitar_riff_analysis.json').unlink()
    with pytest.raises(ValueError,match='analysis is missing'): generate(request(),project_root=library)


def test_sparse_offgrid_snare_ornaments_are_retained_not_quantized(tmp_path):
    notes=[(i,36,.125,100,0,9) for i in range(8)]
    notes += [(i+.5,38,.125,100,0,9) for i in range(8)]
    notes += [(7.575,38,.0625,50,0,9)]
    path=write_midi(tmp_path/'flam.mid',notes,end_beats=8)
    result=analyze_drum_sync(path,tempo=120,bars=2)
    ornament=next(a for a in result['snare'] if a['step'] is None)
    assert ornament['beat']==pytest.approx(7.575)
    assert ornament['grid_error_beats']==pytest.approx(.075)
    assert any('retained at their actual positions' in w for w in result['warnings'])
    assert ornament['beat'] not in result['snare_accent_beats']


def test_early_attack_bar_coordinates_remain_explicit(tmp_path):
    path=write_midi(tmp_path/'early.mid',[(3.995,36,.125,100,0,9)],end_beats=8,tpb=960)
    result=analyze_drum_sync(path,tempo=120,bars=2)
    attack=result['kick'][0]
    assert attack['bar']==1 and attack['grid_bar']==2 and attack['step']==0
    assert result['bar_patterns'][1]['kick_steps']==[0]


def test_generic_guitar_request_example_is_schema_valid():
    root=Path(__file__).resolve().parents[1]
    data=json.loads((root/'examples/requests/guitar_backing_intro.json').read_text())
    schema=json.loads((root/'schemas/generation_request.schema.json').read_text())
    jsonschema.validate(data,schema)
    assert GenerationRequest.from_dict(data).options['guitar_role']=='backing'


def test_selection_weights_can_be_changed_without_code_edits(library):
    write_midi(library/'data/sources/guitar/metalcore/exact_dense.mid',guitar_notes(8,positions=[i/4 for i in range(16)]),end_beats=32)
    scan_midi_catalog(project_root=library)
    add_tags('exact_dense.mid',section='intro',style='metalcore',tags='guitar_backing',catalog_path=library/'data/catalog/midi_catalog.json')
    analyze_catalog_guitars(project_root=library)
    normal=generate(request(),project_root=library)
    assert normal.metadata['selected_source']['path'].endswith('matched.mid')
    config=library/'weights.json'
    config.write_text(json.dumps({'selection_weights':{'rhythm_sync':0,'phrase_fit':1,'backing_suitability':0,'riff_support':0}}))
    changed=generate(request(),project_root=library,guitar_sync_config_path='weights.json')
    assert changed.metadata['selected_source']['path'].endswith('exact_dense.mid')
    assert changed.metadata['selected_source']['phrase_fit_reason']=='exact_length'


def test_legacy_free_tags_can_explicitly_exclude_lead():
    result=classify_guitar_role({'path':'data/sources/guitar/other/anything.mid','tags':['guitar_lead']},{})
    assert result['source']=='manual' and not result['eligible']


def test_controller_order_at_an_attack_and_release_velocity_are_preserved(library):
    source=library/'data/sources/guitar/metalcore/matched.mid'
    midi=mido.MidiFile(source)
    midi.tracks[1].insert(2,mido.Message('control_change',control=1,value=7,channel=0,time=0))
    midi.save(source)
    scan_midi_catalog(project_root=library);analyze_catalog_guitars(project_root=library)
    result=generate(request(),project_root=library)
    emitted=mido.MidiFile(result.output_files[0])
    def at_zero(track):
        tick=0;events=[]
        for msg in track:
            tick+=msg.time
            if tick>0: break
            if not msg.is_meta: events.append(msg.type)
        return events
    assert at_zero(emitted.tracks[1])==at_zero(midi.tracks[1])
    assert {msg.velocity for t in emitted.tracks for msg in t if msg.type=='note_off'}=={64}
