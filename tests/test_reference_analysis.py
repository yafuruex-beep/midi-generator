import hashlib
import json
from pathlib import Path
import sys

import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")
pytest.importorskip("librosa")
jsonschema = pytest.importorskip("jsonschema")

from midi_generator.reference_analysis import analyze_reference_audio, save_reference_analysis
from midi_generator.cli import main


def clicks(path, *, bpm=120, seconds=10, stereo=False, subdivision=1):
    sr = 22050
    signal = np.zeros(round(sr * seconds), dtype=float)
    pulse_t = np.arange(round(sr * 0.025)) / sr
    pulse = np.sin(2 * np.pi * 1800 * pulse_t) * np.exp(-pulse_t / 0.006)
    times = np.arange(0.25, seconds - 0.05, 60 / bpm / subdivision)
    for index, time in enumerate(times):
        offset = round(time * sr)
        amplitude = 0.8 if subdivision == 1 or index % subdivision == 0 else 0.3
        signal[offset:offset + len(pulse)] += amplitude * pulse[:len(signal) - offset]
    data = np.column_stack((signal, signal * 0.7)) if stereo else signal
    sf.write(path, data, sr, subtype="PCM_16")
    return times


def validate(result):
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/reference_analysis.schema.json").read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(result)
    encoded = json.dumps(result, allow_nan=False)
    assert "NaN" not in encoded and "Infinity" not in encoded


@pytest.fixture(scope="module")
def click_audio(tmp_path_factory):
    directory = tmp_path_factory.mktemp("reference_clicks")
    path = directory / "clicks.wav"
    times = clicks(path, stereo=True)
    return path, times


@pytest.fixture(scope="module")
def click_result(click_audio):
    return analyze_reference_audio(click_audio[0])


def test_wav_metadata_onsets_tempo_candidates_density_and_grid(click_audio, click_result):
    path, times = click_audio
    result = click_result
    validate(result)
    assert result["source"]["channels"] == 2
    assert result["source"]["sample_rate"] == 22050
    assert result["source"]["duration_seconds"] == 10
    assert result["tempo"]["estimated_bpm"] == pytest.approx(120, rel=0.04)
    candidates = result["tempo"]["candidates"]
    assert any(item["relationship"] == "half_tempo" for item in candidates)
    assert any(item["relationship"] == "double_tempo" for item in candidates)
    assert all(item["confidence"] is None for item in candidates)
    assert len(result["onsets"]) == pytest.approx(len(times), abs=1)
    assert result["onsets"][0]["relative_time_seconds"] == pytest.approx(float(times[0]), abs=0.06)
    assert result["rhythmic_activity"]["onsets_per_second"] == len(result["onsets"]) / 10
    assert result["grid_alignment"]["sixteenth_alignment_score"] > 0.75
    assert result["grid_alignment"]["confidence"] is None
    assert result["accent_pattern"]["strong_onset_count"] > 0
    assert len(result["energy"]["rms_over_time"]) == 100
    assert result["source"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_requested_range_absolute_relative_times_and_input_protection(click_audio):
    path, _ = click_audio
    before = path.read_bytes()
    result = analyze_reference_audio(path, start_seconds=2, end_seconds=6)
    validate(result)
    assert result["analysis_range"]["duration_seconds"] == 4
    assert all(2 <= item["time_seconds"] < 6 for item in result["onsets"])
    assert all(item["time_seconds"] == pytest.approx(2 + item["relative_time_seconds"]) for item in result["onsets"])
    assert path.read_bytes() == before


@pytest.mark.parametrize("start,end", [(-1, 2), (2, 2), (5, 3), (0, 11), (10, None), (float('nan'), 5), (0, float('inf'))])
def test_invalid_ranges_are_rejected(click_audio, start, end):
    with pytest.raises(ValueError):
        analyze_reference_audio(click_audio[0], start_seconds=start, end_seconds=end)


@pytest.mark.parametrize("seconds", [0.01, 4.0])
def test_silence_and_extremely_short_audio_return_null_estimates(tmp_path, seconds):
    path = tmp_path / "silent.wav"
    sf.write(path, np.zeros(round(22050 * seconds)), 22050)
    result = analyze_reference_audio(path)
    validate(result)
    assert result["tempo"]["estimated_bpm"] is None
    assert result["tempo"]["candidates"] == []
    assert result["onsets"] == []
    assert result["energy"]["segment_rms"] == 0
    assert result["grid_alignment"]["sixteenth_alignment_score"] is None
    assert result["rhythmic_activity"]["sixteenth_note_activity"] is None
    assert result["analysis_metadata"]["warnings"]


def test_exact_rms_measurement_and_stereo_cancellation_warning(tmp_path):
    path = tmp_path / "constant.wav"
    sf.write(path, np.full(22050 * 3, 0.25), 22050, subtype="FLOAT")
    result = analyze_reference_audio(path)
    assert result["energy"]["segment_rms"] == pytest.approx(0.25)
    assert result["energy"]["mean_square_energy"] == pytest.approx(0.0625)
    cancelled = tmp_path / "cancelled.wav"
    y = np.full(22050, 0.25)
    sf.write(cancelled, np.column_stack((y, -y)), 22050, subtype="FLOAT")
    report = analyze_reference_audio(cancelled)
    assert any("cancelled" in item for item in report["analysis_metadata"]["warnings"])


def test_activity_accent_measurements_on_eighth_clicks(tmp_path):
    path = tmp_path / "eighth.wav"
    clicks(path, bpm=120, subdivision=2)
    result = analyze_reference_audio(path)
    validate(result)
    assert result["rhythmic_activity"]["onsets_per_second"] > 3.5
    assert result["rhythmic_activity"]["eighth_note_activity"] is not None
    assert 0 <= result["rhythmic_activity"]["sixteenth_note_activity"] <= 1
    assert sum(result["accent_pattern"]["category_counts"].values()) == result["accent_pattern"]["strong_onset_count"]


def test_tempo_with_subdivisions_avoids_selecting_a_three_beat_period(tmp_path):
    path = tmp_path / "160_with_subdivisions.wav"
    clicks(path, bpm=160, seconds=16, subdivision=2)
    result = analyze_reference_audio(path)
    validate(result)
    assert result["tempo"]["estimated_bpm"] == pytest.approx(160, rel=0.02)
    assert any(candidate["bpm"] == pytest.approx(80, rel=0.02) for candidate in result["tempo"]["candidates"])
    assert result["grid_alignment"]["sixteenth_alignment_score"] > 0.8


def test_nonfinite_decoded_audio_is_rejected(tmp_path):
    path = tmp_path / "nan.wav"
    sf.write(path, np.array([0.0, np.nan, np.inf]), 22050, subtype="FLOAT")
    with pytest.raises(ValueError, match="NaN or Infinity"):
        analyze_reference_audio(path)


def test_json_saving_is_separate_utf8_exclusive_and_atomic(tmp_path, click_result):
    destination = tmp_path / "レポート.json"
    save_reference_analysis(click_result, destination)
    validate(json.loads(destination.read_text(encoding="utf-8")))
    before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        save_reference_analysis(click_result, destination)
    invalid = {**click_result, "extra": float('nan')}
    with pytest.raises(ValueError):
        save_reference_analysis(invalid, destination, overwrite=True)
    assert destination.read_bytes() == before
    save_reference_analysis(click_result, destination, overwrite=True)
    assert destination.read_bytes() == before
    with pytest.raises(ValueError, match="extension"):
        save_reference_analysis(click_result, click_result['source']['path'], overwrite=True)


def test_mp3_decode_when_available(tmp_path, click_audio):
    if not sf.check_format("MP3", "MPEG_LAYER_III"):
        pytest.skip("libsndfile MP3 encoder/decoder not available")
    data, sr = sf.read(click_audio[0])
    path = tmp_path / "sample.mp3"
    sf.write(path, data, sr, format="MP3", subtype="MPEG_LAYER_III")
    before = path.read_bytes()
    result = analyze_reference_audio(path, end_seconds=5)
    validate(result)
    assert result["source"]["format"] == "mp3"
    assert len(result["onsets"]) > 5
    assert path.read_bytes() == before


def test_flac_and_decode_errors(tmp_path, click_audio):
    data, sr = sf.read(click_audio[0])
    path = tmp_path / "sample.flac"
    sf.write(path, data, sr)
    validate(analyze_reference_audio(path, end_seconds=3))
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"invalid MP3")
    with pytest.raises(ValueError, match="Cannot decode"):
        analyze_reference_audio(broken)


def test_reference_cli_saves_report_and_refuses_implicit_overwrite(tmp_path, click_audio, monkeypatch, capsys):
    destination = tmp_path / "report.json"
    args = ["midi-generator", "reference", "analyze", str(click_audio[0]), "--start", "2", "--end", "6", "--output", str(destination)]
    monkeypatch.setattr(sys, "argv", args)
    main()
    summary = json.loads(capsys.readouterr().out)
    assert summary["onset_count"] > 0
    validate(json.loads(destination.read_text()))
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit):
        main()
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", [*args, "--overwrite", "--verbose"])
    main()
    assert json.loads(capsys.readouterr().out)["schema_version"] == "1.0"
