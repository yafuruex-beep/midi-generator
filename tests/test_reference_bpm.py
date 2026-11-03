"""Reference and Export tempo responsibilities and legacy compatibility."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest
np = pytest.importorskip('numpy')
sf = pytest.importorskip('soundfile')
pytest.importorskip('librosa')
jsonschema = pytest.importorskip('jsonschema')
from midi_generator.api import (analyze_reference_section, analyze_reference_pattern_map,
    save_reference_pattern_map, load_reference_pattern_map)
from midi_generator.reference_analysis.pattern_map import _identity
from midi_generator.cli import main
from test_reference_analysis import clicks

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def audio(tmp_path):
    path=tmp_path/'pulse.wav'
    clicks(path,bpm=120,seconds=10)
    return path


def test_auto_manual_alias_equivalence_and_grid_origin(audio):
    automatic=analyze_reference_pattern_map(audio,bar_start_seconds=0)
    assert automatic['reference_tempo']['source']=='estimated'
    assert automatic['reference_tempo']['bpm']==pytest.approx(120,rel=.04)
    assert automatic['reference_tempo']['confidence'] is None
    assert any('half/double' in w for w in automatic['warnings'])
    manual=analyze_reference_pattern_map(audio,reference_tempo_bpm=100,bar_start_seconds=0)
    assert manual['reference_tempo']=={'bpm':100,'source':'manual','confidence':None}
    assert manual['estimated_tempo']['estimated_bpm']==automatic['estimated_tempo']['estimated_bpm']
    assert manual['bars'][0]['end_time']==pytest.approx(2.4)
    assert manual['bar_grid']['origin_seconds']==0
    with pytest.warns(FutureWarning,match='deprecated'):
        old=analyze_reference_pattern_map(audio,tempo_bpm=100,bar_start_seconds=0)
    assert old==manual
    schema=json.loads((ROOT/'schemas/reference_pattern_map.schema.json').read_text())
    for result in (automatic,manual):jsonschema.Draft202012Validator(schema).validate(result)


@pytest.mark.parametrize('api',[analyze_reference_section,analyze_reference_pattern_map])
def test_conflicting_api_arguments_rejected_before_audio_load(api):
    with pytest.raises(ValueError,match='not both'):
        api('missing.wav',reference_tempo_bpm=120,tempo_bpm=100)


def test_unknown_never_falls_back(tmp_path):
    path=tmp_path/'silent.wav';sf.write(path,np.zeros(22050*3),22050)
    result=analyze_reference_pattern_map(path)
    assert result['reference_tempo']=={'bpm':None,'source':'estimated','confidence':None}
    assert result['bars']==[] and result['beat_positions']==[]
    assert any('No supported tempo' in w for w in result['warnings'])


@pytest.mark.parametrize('option',['export_bpm','export_tempo_bpm'])
def test_export_option_not_accepted(audio,option):
    with pytest.raises(TypeError):analyze_reference_pattern_map(audio,**{option:105})


def test_cli_alias_warning_conflict_and_generator_unchanged(audio,tmp_path,monkeypatch,capsys):
    out=tmp_path/'map.json'
    base=['midi-generator','reference','pattern-map',str(audio),'--bar-start','0','--output',str(out)]
    monkeypatch.setattr(sys,'argv',base+['--reference-tempo','100']);main()
    assert 'deprecated' not in capsys.readouterr().err
    first=out.read_bytes()
    monkeypatch.setattr(sys,'argv',base+['--tempo','100']);main()
    assert 'deprecated' in capsys.readouterr().err and out.read_bytes()==first
    monkeypatch.setattr(sys,'argv',base+['--tempo','100','--reference-tempo','100'])
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2
    from midi_generator.cli import build_parser
    args=build_parser().parse_args(['generate','--tempo','105'])
    assert args.tempo==105


def test_old_json_read_without_mutation_and_new_cache_distinct(audio,tmp_path):
    current=analyze_reference_pattern_map(audio,reference_tempo_bpm=105)
    legacy=deepcopy(current);legacy.pop('reference_tempo')
    legacy.update(schema_version='1.0',analyzer_version='7.1-step3.1',section_analyzer_version='7.0-step2.1')
    legacy['map_id']=_identity(legacy)
    schema=json.loads((ROOT/'schemas/reference_pattern_map_v1.schema.json').read_text())
    jsonschema.Draft202012Validator(schema).validate(legacy)
    path=tmp_path/'legacy.json';path.write_text(json.dumps(legacy));before=path.read_bytes()
    with pytest.warns(FutureWarning,match='unverified'):
        assert load_reference_pattern_map(path)==legacy
    assert path.read_bytes()==before and legacy['map_id']!=current['map_id']
    new=save_reference_pattern_map(current,tmp_path/'new.json')
    assert load_reference_pattern_map(new)==current
    with pytest.warns(FutureWarning):
        with pytest.raises(FileExistsError):save_reference_pattern_map(current,path)
    assert path.read_bytes()==before
