"""Step 3 persistence checks only; no generation or distribution execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

np = pytest.importorskip('numpy')
sf = pytest.importorskip('soundfile')
pytest.importorskip('librosa')
jsonschema = pytest.importorskip('jsonschema')

from midi_generator.api import (analyze_reference_section, analyze_reference_pattern_map,
    build_reference_pattern_map, load_reference_pattern_map, save_reference_pattern_map)
from midi_generator.cli import main
from test_reference_section import fixture_audio

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/reference_pattern_map.schema.json').read_text())


def validate(result):
    jsonschema.Draft202012Validator(SCHEMA).validate(result)
    json.dumps(result, allow_nan=False)


@pytest.fixture(scope='module')
def section(tmp_path_factory):
    directory = tmp_path_factory.mktemp('pattern_map')
    source = directory / 'repeated.wav'
    fixture_audio(source)
    before = source.read_bytes()
    section = analyze_reference_section(source, tempo_bpm=120, time_signature=[4,4], bar_start_seconds=0)
    assert source.read_bytes() == before
    return section


def test_build_from_step2_schema_roundtrip_and_input_unchanged(section, tmp_path):
    before = deepcopy(section)
    result = build_reference_pattern_map(section, section_name='イントロ')
    validate(result)
    assert section == before
    assert result['reference_id'] == section['source']['source_id']
    assert result['pattern_sequence'] == [b['pattern_id'] for b in section['bars']]
    assert result['bars'][0]['confidence']['repetition'] is None
    assert result['bars'][1]['confidence']['repetition'] > .95
    assert result['bars'][2]['activity_score'] == 0
    assert result['bars'][2]['silence_candidates']
    destination = tmp_path / 'nested' / 'pattern.json'
    save_reference_pattern_map(result, destination)
    assert load_reference_pattern_map(destination) == result
    assert 'イントロ' in destination.read_text()


def test_stable_content_address_reuse_and_no_overwrite(section, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = build_reference_pattern_map(section)
    again = build_reference_pattern_map(section)
    assert result == again
    path = save_reference_pattern_map(result)
    assert path.parent == Path('data/cache/reference_patterns')
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    assert save_reference_pattern_map(again) == path
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime
    other = build_reference_pattern_map(section, section_name='different')
    assert other['map_id'] != result['map_id']
    with pytest.raises(FileExistsError):
        save_reference_pattern_map(other, path)
    assert path.read_bytes() == before


@pytest.mark.parametrize('seconds', [.01, 4])
def test_silent_or_unavailable_estimates_are_valid_json(tmp_path, seconds):
    path = tmp_path / 'silent.wav'
    sf.write(path, np.zeros(round(22050 * seconds)), 22050)
    result = analyze_reference_pattern_map(path)
    validate(result)
    assert result['estimated_tempo']['estimated_bpm'] is None
    assert result['bars'] == result['pattern_sequence'] == []
    assert result['confidence']['bar_grid'] is None and result['warnings']
    out = save_reference_pattern_map(result, tmp_path / 'map.json')
    assert load_reference_pattern_map(out) == result


def test_legacy_cache_reserved_and_invalid_json_never_saved(section, tmp_path):
    result = build_reference_pattern_map(section)
    legacy = tmp_path / 'reference_analysis.json'
    legacy.write_text('{"original":true}')
    with pytest.raises(ValueError, match='reserved'):
        save_reference_pattern_map(result, legacy)
    assert legacy.read_text() == '{"original":true}'
    invalid = deepcopy(result)
    invalid['bars'][0]['activity_score'] = float('nan')
    with pytest.raises(ValueError):
        save_reference_pattern_map(invalid, tmp_path / 'invalid.json')
    assert not (tmp_path / 'invalid.json').exists()
    destination = save_reference_pattern_map(result, tmp_path / 'map.json')
    invalid = json.loads(destination.read_text());invalid['start_time'] = 99
    destination.write_text(json.dumps(invalid))
    with pytest.raises(ValueError, match='hash'):
        load_reference_pattern_map(destination)


def test_cli_api_repeated_input_and_real_legacy_cache_protection(tmp_path, monkeypatch, capsys):
    legacy = ROOT / 'data/cache/reference_analysis.json'
    before = hashlib.sha256(legacy.read_bytes()).hexdigest() if legacy.exists() else None
    source = tmp_path / 'input.wav'
    fixture_audio(source)
    destination = tmp_path / 'pattern.json'
    args = ['midi-generator','reference','pattern-map',str(source),'--start','0','--end','10',
            '--tempo','120','--time-signature','4/4','--bar-start','0','--section-name','intro','--output',str(destination)]
    monkeypatch.setattr(sys, 'argv', args)
    main()
    summary = json.loads(capsys.readouterr().out)
    assert summary['bar_count'] == 5
    first = destination.read_bytes()
    main()
    capsys.readouterr()
    assert destination.read_bytes() == first
    result = load_reference_pattern_map(destination)
    validate(result)
    assert result == analyze_reference_pattern_map(source, start_seconds=0, end_seconds=10,
        tempo_bpm=120, time_signature=[4,4], bar_start_seconds=0, section_name='intro')
    if before is not None:
        assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before


def test_failed_audio_decode_creates_no_map(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'broken.wav'
    source.write_bytes(b'not audio')
    destination = tmp_path / 'not-created.json'
    monkeypatch.setattr(sys, 'argv', ['midi-generator','reference','pattern-map',str(source),
                                    '--output',str(destination)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert not destination.exists() and source.read_bytes() == b'not audio'
    assert 'Cannot inspect' in capsys.readouterr().err
