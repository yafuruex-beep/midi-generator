import hashlib
import json

import pytest

np = pytest.importorskip('numpy')
sf = pytest.importorskip('soundfile')
pytest.importorskip('librosa')

from midi_generator.api import analyze_reference_section


def fixture_audio(path):
    sr = 22050
    samples = np.zeros(sr * 10)
    t = np.arange(441) / sr
    pulse = .8 * np.sin(2 * np.pi * 1800 * t) * np.exp(-t / .006)
    for time in [bar * 2 + offset for bar in (0, 1, 3, 4) for offset in (.25, .5, 1, 1.5)]:
        i = round(time * sr)
        samples[i:i + len(pulse)] += pulse
    sf.write(path, samples, sr)


def test_repeat_pause_dynamics_range_and_reproducibility(tmp_path):
    path = tmp_path / 'repeated.wav'
    fixture_audio(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = analyze_reference_section(path, end_seconds=10, tempo_bpm=120,
                                       time_signature=[4, 4], bar_start_seconds=0)
    again = analyze_reference_section(path, end_seconds=10, tempo_bpm=120,
                                      time_signature=[4, 4], bar_start_seconds=0)
    assert json.dumps(result, allow_nan=False, sort_keys=True) == json.dumps(again, allow_nan=False, sort_keys=True)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    bars = result['bars']
    assert len(bars) == 5
    assert all(not bar['partial'] for bar in bars)
    assert bars[0]['pattern_id'] == bars[1]['pattern_id'] == bars[3]['pattern_id']
    assert bars[1]['repetition_similarity'] > .95
    assert bars[2]['onset_count'] == 0 and bars[2]['pattern_id'] is None
    assert bars[2]['rms_change'] < 0 and bars[3]['density_change'] > 0
    assert any(p['start_seconds'] <= 4 and p['end_seconds'] >= 6 for p in result['pause_candidates'])
    assert all(0 <= p['confidence'] <= 1 for p in result['pause_candidates'])
    assert len(result['beats']) == 20
    assert result['meter']['source'] == 'user'
    subset = analyze_reference_section(path, start_seconds=2, end_seconds=8, tempo_bpm=120, bar_start_seconds=2)
    assert subset['bars'][0]['start_seconds'] == 2
    assert all(2 <= e['time_seconds'] < 8 for e in subset['onsets'])


@pytest.mark.parametrize('seconds', [.01, 4])
def test_silence_short_and_unavailable_beats(tmp_path, seconds):
    path = tmp_path / 'silent.wav'
    sf.write(path, np.zeros(round(22050 * seconds)), 22050)
    result = analyze_reference_section(path)
    assert result['tempo']['estimated_bpm'] is None
    assert result['bars'] == result['beats'] == []
    assert result['meter']['source'] == 'provisional_assumption'
    assert result['meter']['confidence'] is None
    assert result['analysis']['warnings']
    json.dumps(result, allow_nan=False)


def test_partial_bars_not_classified_and_meter_not_claimed(tmp_path):
    path = tmp_path / 'repeated.wav'
    fixture_audio(path)
    result = analyze_reference_section(path, start_seconds=.1, end_seconds=9.7, tempo_bpm=120)
    assert result['meter']['source'] == 'provisional_assumption'
    assert result['bar_grid']['downbeat_verified'] is False
    assert any(b['partial'] for b in result['bars'])
    assert all(b['pattern_id'] is None for b in result['bars'] if b['partial'])
    assert len(result['tempo']['candidates']) > 1
    assert result['tempo']['confidence'] is None


@pytest.mark.parametrize('options', [
    {'tempo_bpm': True}, {'tempo_bpm': float('nan')}, {'tempo_bpm': 600},
    {'time_signature': [4, 3]}, {'time_signature': [True, 4]},
    {'bar_start_seconds': -1}, {'bar_start_seconds': 11},
    {'start_seconds': -1}, {'end_seconds': 11}, {'end_seconds': float('inf')},
])
def test_invalid_settings(tmp_path, options):
    path = tmp_path / 'repeated.wav'
    fixture_audio(path)
    with pytest.raises(ValueError):
        analyze_reference_section(path, **options)


def test_section_duration_budget_checked_before_decoding(tmp_path, monkeypatch):
    path = tmp_path / 'long.wav'
    sf.write(path, np.zeros(8000 * 121), 8000)
    import midi_generator.reference_analysis.section_analysis as section
    monkeypatch.setattr(section, 'load_audio', lambda *a, **k: pytest.fail('must reject before decoding'))
    with pytest.raises(ValueError, match='120 seconds'):
        analyze_reference_section(path)


def test_tempo_failure_preserves_measurements(tmp_path, monkeypatch):
    path = tmp_path / 'repeated.wav'
    fixture_audio(path)
    import midi_generator.reference_analysis.section_analysis as section
    monkeypatch.setattr(section, 'analyze_tempo', lambda *args: {
        'estimated_bpm': None, 'confidence': None, 'candidates': [], 'status': 'insufficient_evidence'})
    result = analyze_reference_section(path)
    assert result['onsets'] and result['energy']['segment_rms'] > 0
    assert result['pause_candidates'] and result['bars'] == []
    manual = analyze_reference_section(path, tempo_bpm=120, bar_start_seconds=0)
    assert len(manual['bars']) == 5 and manual['effective_tempo']['source'] == 'user'


def test_decoded_memory_budget_checked_before_load(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import midi_generator.reference_analysis.section_analysis as section
    _, sf_module, _ = section.audio_dependencies()
    monkeypatch.setattr(sf_module, 'info', lambda path: SimpleNamespace(duration=90, samplerate=192000, channels=8))
    monkeypatch.setattr(section, 'load_audio', lambda *a, **k: pytest.fail('must reject before decoding'))
    with pytest.raises(ValueError, match='128 MiB'):
        analyze_reference_section(tmp_path / 'large.wav')
