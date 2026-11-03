# Reference Pattern Map — Phase 7.1 Step 3

This layer adapts the existing `analyze_reference_section()` result. It adds no
DSP, MIDI selection, drum/guitar synchronization, separation, or model dependency.
It writes a new versioned JSON format; existing `reference analyze` semantics and
`data/cache/reference_analysis.json` remain unchanged.

## CLI

Run from the project root after installing the existing `.[audio]` extra:

```bash
.venv/bin/midi-generator reference pattern-map "data/references/The Queendom.wav" \
  --start 0 --end 90 --time-signature 4/4 --bar-start 0 \
  --section-name intro
```

Output defaults to `data/cache/reference_patterns/<content-hash>.json`. To choose a
new file, append `--output data/cache/reference_patterns/intro.json`. No overwrite
flag exists. An identical existing map is reused without changing its file/mtime;
a different or invalid existing file is never replaced. The filename
`reference_analysis.json` is reserved and refused. Parent directories are created
only when saving a valid map. Writes reuse the existing atomic exclusive JSON
persistence, including a check for concurrent creation.

Omit `--reference-tempo`, `--time-signature` and `--bar-start` to use Step 2's hypotheses.
Unspecified meter remains a provisional 4/4 grouping; it is not inferred meter.
Times and bar origin are absolute source-audio seconds, not offsets into the cut.
The 120-second / 128 MiB decoded-input limits from Step 2 still apply, and do not
represent a process-RSS guarantee. No extra audio buffers are created by mapping.

## Python API

```python
from midi_generator.api import (
    analyze_reference_section, build_reference_pattern_map,
    analyze_reference_pattern_map, save_reference_pattern_map,
    load_reference_pattern_map,
)

# Adapt a result already obtained in Step 2; no audio reanalysis here:
section = analyze_reference_section(
    "data/references/example.wav", start_seconds=0, end_seconds=16,
    reference_tempo_bpm=120, time_signature=[4, 4], bar_start_seconds=0,
)
pattern_map = build_reference_pattern_map(section, section_name="intro")
path = save_reference_pattern_map(pattern_map)
restored = load_reference_pattern_map(path)
assert restored == pattern_map

# Alternatively analyze + adapt in one call, still without writing a file:
pattern_map = analyze_reference_pattern_map(
    "data/references/example.wav", start_seconds=0, end_seconds=16,
    section_name="intro",
)
```

Readback reuses the result without DSP. The CLI and `analyze_reference_pattern_map`
do perform analysis before saving; cache hits do not skip that analysis. Analyzing
once then calling `build_reference_pattern_map` avoids duplicate audio work.

## Format and interpretation

Schema: `schemas/reference_pattern_map.schema.json`, JSON Schema 2020-12.
`schema_version` is `1.1`; `analyzer_version` is `7.1-bpm.1` and the underlying
section analyzer is recorded separately. `estimated_tempo` retains automatic
candidates even when `reference_tempo` records a manual Reference override.
`effective_tempo` remains a compatibility alias (source `user` corresponds to `manual`).

Top-level fields include reference/source identity, optional section name,
requested and sample-aligned ranges, tempo and meter hypotheses, beat positions,
bars, pattern sequence, energy/activity, onset density, silence candidates,
confidence, warnings, algorithm parameters and numerical package versions.

- `reference_id`: SHA-256 of the original full audio file.
- `map_id`: SHA-256 of canonical JSON content excluding `map_id`. Source path,
  source hash, interval, settings, label, results and recorded versions participate.
  Moving an audio file or changing its label/environment can yield a different ID.
- `activity_score`: bar RMS / highest bar RMS in this section; null if no positive
  bar RMS is available. This is relative signal energy, not musical quality.
- `onset_density`: onset count / analyzed bar duration, measured in events/second.
- `silence_candidates`: full-mix quietness candidates; per-bar intervals are
  clipped to their bar, while top-level intervals retain their full boundaries.
- `P001`, `P002`, etc.: acoustic-shape similarity groups inherited unchanged from
  Step 2, not confirmed guitar riffs or drum patterns. Partial/onset-free bars have
  null IDs. New representatives have null repetition confidence; matched bars have
  a cosine-similarity support score clamped to [0,1] for floating-point roundoff.
- Confidence values can be null. Numerical support is heuristic, not probability.
  Unknown downbeats, provisional meter and failed tempo evidence remain explicit.
  A map with no grid has empty bars/sequence and warnings, not invented bars.

No execution timestamp is included. Integer-equivalent floats are normalized so
CLI `0.0` and API `0` produce the same content identity. Stability is for the same
path/content/settings/numerical environment; cross-version numerical identity is
not promised. Source license remains unknown unless already supplied in Step 2.

For full structural validation (development extra):

```python
import json
from jsonschema import Draft202012Validator
from midi_generator.api import load_reference_pattern_map

result = load_reference_pattern_map("data/cache/reference_patterns/intro.json")
with open("schemas/reference_pattern_map.schema.json", encoding="utf-8") as stream:
    Draft202012Validator(json.load(stream)).validate(result)
```

The runtime has no JSON Schema dependency. `validate_reference_pattern_map` and
readback check finite JSON, supported versions, content identity and key interval /
sequence relationships. Full structural schema validation is the separate check
above. Saving does not modify source audio, source MIDI, catalogs or old caches.

Step 4 full regression/resource verification and Step 5 ZIP rebuild/environment
reconstruction are outside this change; neither has been executed for Step 3.

## Saved sample from the Step 3 check

`data/cache/reference_patterns/7f02c1ebe85738a1cd808d0077b034889f2b4020d2b110559ca2a52e05bcd116.json`
is a preserved legacy 1.0 sample for The Queendom 0–90 seconds, using a 105 BPM,
4/4 hypothesis with bar origin 0. It contains 40 bar intervals, with the final one
partial. This user-audio-derived cache is excluded from Git.

Selected fields from that legacy sample (excerpt, not a complete map). Its 105 BPM was a requested Export tempo mistakenly used for analysis; it is not confirmed Reference BPM:

```json
{
  "schema_version": "1.0",
  "section_name": "intro",
  "start_time": 0,
  "end_time": 90,
  "analyzer_version": "7.1-step3.1",
  "effective_tempo": {"bpm": 105, "source": "user", "confidence": null},
  "time_signature": {"value": [4, 4], "source": "user", "confidence": null}
}
```

## Step 4B-0: Reference BPM versus Export BPM

Default analysis estimates Reference BPM from audio. To override only when the
Reference tempo is independently known, use `--reference-tempo 120` or the API
keyword `reference_tempo_bpm=120`. No Export BPM setting exists in these APIs/maps.
If estimation fails, BPM stays null and no beat/bar grid is invented.

Old `--tempo 120` remains an analysis override, emits a stderr warning, and is not
reinterpreted as Export BPM. Old `tempo_bpm=120` emits `FutureWarning`. Both aliases
produce identical maps to the new spelling; warnings are not persisted merely
because a deprecated spelling was used. Simultaneous new/old overrides are errors.

Schema 1.1 includes the adopted Reference tempo:

```json
{"reference_tempo": {"bpm": 120, "source": "estimated", "confidence": null}}
```

For a manual Reference override, source is `manual`; user input does not imply
verified tempo or known confidence. `estimated_tempo` still contains the original
automatic candidates. Half/double-time candidates are exposed and warned about;
confidence in tempo interpretation is separate from confidence in the downbeat.

Legacy schema 1.0 remains available at
`schemas/reference_pattern_map_v1.schema.json`. The reader validates old maps and
warns that their analysis tempo is unverified, without adding fields, changing IDs
or writing the file. Reanalyze instead of relabeling a legacy 105-BPM grid as
correct Reference analysis. New version metadata participates in content identity,
so old and new maps have different keys. Saving over a different old map fails.
Previously obtained Step-2 dictionaries can still be adapted, but their old
analysis override is explicitly warned as unverified; this does not repair old
bar boundaries or establish Reference truth.

Output generation continues to use `generate --tempo 105` and
`GenerationRequest.tempo`. It is independent of this layer. No generation or
synchronization module is changed by Step 4B-0; Phase 7.2 remains unimplemented.
