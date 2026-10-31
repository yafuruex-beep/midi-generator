# Reference audio analysis — Phase 5.1

Phases 1–4.1 are the accepted, frozen MIDI baseline. This optional pipeline only
reads audio and produces JSON. It does not transcribe drums, separate instruments,
generate MIDI, assign source tags, or feed audio features into MIDI selection.

## Installation and decoding

```bash
source .venv/bin/activate
python -m pip install -e '.[audio,dev]'
```

The `audio` extra contains librosa 0.11, NumPy, and soundfile 0.13. Their transitive
DSP dependencies are installed by pip. No trained model is downloaded or used.
`jsonschema` is a development dependency for report validation. MIDI-only users
can install the base package; audio imports are deferred until an analysis request.
Missing audio dependencies produce an installation instruction, not a CLI import failure.

WAV, MP3, and FLAC are decoded through soundfile/libsndfile. Current macOS wheels
bundle libsndfile. MP3 support depends on that decoder build; inspect it with:

```bash
python -c "import soundfile as sf; print(sf.__libsndfile_version__); print(sf.available_formats().get('MP3'))"
```

If MP3 decoding is unavailable, install a current MP3-capable libsndfile and a
soundfile build using it (macOS: `brew install libsndfile`, then install soundfile
from source with `python -m pip install --force-reinstall --no-binary=soundfile 'soundfile>=0.13,<0.14'`).
Alternatively, convert a separate copy to WAV using an installed FFmpeg:
`ffmpeg -i input.mp3 converted.wav`. FFmpeg is not a runtime dependency of this project.
Decode errors report the input path, backend error, and MP3 guidance when applicable.

## CLI and API

```bash
midi-generator reference analyze data/references/example.wav \
  --start 12 --end 28 --output data/cache/reference_analysis.json
```

Omit the range for full-file analysis. `--verbose` prints the full result;
`--overwrite` explicitly permits replacing an existing JSON report. Existing
outputs are refused by default. The input audio can never be a save destination.

```python
from midi_generator.reference_analysis import (
    analyze_reference_audio, save_reference_analysis,
)

result = analyze_reference_audio(
    audio_path="data/references/example.wav", start_seconds=12.0, end_seconds=28.0,
)
save_reference_analysis(result, "data/cache/reference_analysis.json")
```

The same functions are exported from `midi_generator.api`. Analysis has no output
side effects; saving is a separate atomic operation with UTF-8 and strict JSON
serialization (`allow_nan=False`). Concurrent creation cannot silently overwrite a
report. Serialization failures leave existing files intact.

Negative, non-finite, empty/reversed, or out-of-bounds ranges are rejected. The end
is not silently clamped. Actual sample-aligned boundaries and requested boundaries
are recorded separately (at most one source sample of rounding). Audio under two
seconds or fewer than four detected onsets has no tempo/grid estimate. Zero-length
files/ranges are rejected; nonempty short and silent signals still report energy.

## Measurements, estimates, and units

- Source duration/rate/channels/format come from the decoder. SHA-256, byte size,
  absolute path, and content-based source ID identify the input. License is unknown
  (`null`) until separately documented by the user.
- Only the requested source frames are read. Channels are averaged and resampled
  to 22,050 Hz for analysis. Downmix cancellation generates a warning.
- Onsets use librosa's positive spectral flux on a log-Mel spectrogram (FFT 1024,
  hop 256, 64 Mel bands), then peak picking. Strength is relative to this range's
  maximum envelope value; raw strength is also retained. Events near the segment
  boundary may be missed. These events are not assigned to drums or other instruments.
- Tempo uses a tempogram with librosa's standard 120 BPM log-tempo prior. A nearby
  supported global autocorrelation peak is refined, when at least eight coherent
  pulse events exist, by a weighted onset-phase regression (maximum 3% adjustment).
  This reduces frame-resolution drift without snapping or modifying audio events.
  Primary peak search is 40–240 BPM; half/double candidates and other measured
  periodicities are retained. A globally strongest autocorrelation peak may represent
  several beats rather than the perceived pulse, so it need not be the primary BPM.
- Candidate confidence is always `null`. `periodicity_score` is positive normalized
  autocorrelation, and `relative_score` is relative to the strongest observed peak.
  Neither is a calibrated probability. Primary selection also uses the documented
  tempo prior, so candidates are not claimed to be sorted by confidence.
- Beat phase is the strongest weighted onset-phase cluster. It is an inferred
  quarter-note pulse origin, not a bar downbeat. Measure position is unknown.
- Activity is detected onsets/second plus one-second windows. Eighth/sixteenth
  activity is the fraction of estimated grid cells occupied by an onset. These
  grid-dependent values are `null` without tempo evidence. Candidate tempo aliases
  can change their interpretation.
- Accent threshold is the larger of 0.5 and the 75th percentile of relative onset
  strengths. Accents are classified as inferred on-beat, off-eighth, off-sixteenth,
  or unknown. The offbeat ratio uses grid-classifiable strong onsets as denominator.
  It never implies a kick/snare role or a known bar downbeat.
- For each grid, event error is distance to its nearest point. Alignment score is
  mean `max(0, 1 - error / (grid_spacing / 2))`; aligned fractions additionally use
  tolerance `min(25 ms, 20% of spacing)`. Random phases can have a score near 0.5.
  This is audio-onset alignment, not drum-performance tightness. Null confidence
  and alternative-BPM grid scores expose tempo/phase uncertainty.
- `segment_rms` is the RMS of all analyzed mono samples; `mean_square_energy` is
  their mean square. `mean_rms` is the arithmetic mean of non-overlapping 100 ms
  frame RMS values, including a final short frame. Values are normalized linear
  PCM amplitudes, not SPL, loudness, or a calibrated acoustic energy measurement.

The result includes `schema_version`, `algorithm_version`, library versions,
field measurement/estimate categories, and explicit warnings. Non-finite input
samples are rejected. Unknown quantities use JSON null.

## Schema and verification

Schema: `schemas/reference_analysis.schema.json` (JSON Schema 2020-12).

```python
import json
from jsonschema import Draft202012Validator, FormatChecker

schema = json.load(open("schemas/reference_analysis.schema.json"))
Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)
json.dumps(result, allow_nan=False)
```

Tests synthesize local click signals, silence, short signals, stereo data, and
encoded MP3/FLAC fixtures; no online audio samples are required. MP3 encoding tests
skip when the local libsndfile build cannot create the fixture. Independent subprocess
tests block audio imports and verify that existing CLI help still works.

No user reference recording was present during Phase 5.1 verification. The example
`data/references/synthetic_160bpm_phase51.wav` and its source manifest are explicitly
synthetic. The CLI analyzes seconds 12–28 into `data/cache/reference_analysis.json`.
The verification report records known pulse rate, measured features, schema result,
test result, and frozen MIDI source/catalog/code hashes.

Limitations: no instrument separation, variable-tempo tracking, time-signature or
bar-downbeat detection, or definitive musical beat interpretation. Dense distorted
mixes may merge/mask transients. Review results on real recordings before using
them in a future Phase 5.2 mapping layer; that layer is not implemented here.

Primary library documentation:
[soundfile installation and format support](https://python-soundfile.readthedocs.io/en/latest/),
[librosa onset strength](https://librosa.org/doc/0.11.0/generated/librosa.onset.onset_strength.html),
[librosa tempo estimation and prior](https://librosa.org/doc/0.11.0/generated/librosa.feature.tempo.html).
