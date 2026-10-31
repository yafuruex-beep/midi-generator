# MIDI Generator

Independent, reusable MIDI pattern generation framework for metalcore, hardcore, post-hardcore, and related genres.

## Current status

The project includes MIDI inspection/cataloging, manual pattern tags, catalog search/ranking, template-based drum generation, drum-synchronized backing guitar generation, and optional read-only reference audio rhythm analysis. Pattern segmentation, advanced composition, and reference-driven MIDI generation remain planned.

## Terminal-only usage

Codex/ChatGPT is not required. See the [Japanese CLI reference](docs/CLI_REFERENCE_JA.md)
for installation, everyday workflows, generation examples, and troubleshooting.

GitHub history and terminal backup steps: [Japanese Git guide](docs/GIT_BACKUP_JA.md).

## Project layout and distribution

Start with [the directory guide](docs/PROJECT_LAYOUT.md),
[design](docs/DESIGN.md), and [development status](docs/PROJECT_STATUS.md).
Runtime settings live in `config/`, reusable requests in `examples/requests/`,
reference audio in `data/references/`, verification reports in `data/reports/`,
and generated MIDI in `output/`. Source code has a single home in `src/`.

To create a source-only ZIP without your MIDI packs, reference audio, catalogs,
or virtual environments:

```bash
python3 scripts/package_release.py
```

See [distribution and recipient setup](docs/DISTRIBUTION.md).

## Setup

Requires Python 3.11 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[audio,dev]"
python -c "import midi_generator; print(midi_generator.__file__)"
midi-generator --help
python -m pytest
```

Use the same activated virtual environment for installation and CLI execution.
Editable installs use a small PEP 660 proxy package to load `src/midi_generator`
directly. Changes to Python source take effect without reinstalling, and
`midi_generator.__file__` points to the source initializer. Ordinary installs still
use the standard setuptools wheel build. No `PYTHONPATH` setting is required.

This avoids a recurring macOS/Python 3.13 failure: setuptools' editable `.pth`
file repeatedly acquired the `hidden` flag, causing Python to skip it on startup.
Reinstallation alone only cleared the flag temporarily. The process assigning
the flag has not been identified; the proxy removes dependence on `.pth` loading
and works even if its own Python file is marked hidden. If the repository is moved,
reinstall from its new location. `build_support/editable_backend.py` implements the
editable hook, and `MANIFEST.in` includes this backend in source distributions.

## Current scope

The package provides shared request/result models, MIDI inspection and load/save helpers, catalog management, pattern tagging/search, template-based drum generation, and drum-synchronized backing guitar generation. General MIDI export helpers refuse to replace existing destinations unless overwrite is explicitly requested; generation instead chooses a free numbered filename.

```bash
midi-generator inspect input.mid
midi-generator round-trip input.mid output/copy.mid
```

The Python API is available from `midi_generator.api`:

```python
from midi_generator.api import GenerationRequest, inspect, load_midi, save_midi

request = GenerationRequest(instrument="drums", section="breakdown", tempo=105, bars=4)
metadata = inspect("input.mid")
midi = load_midi("input.mid")
save_midi(midi, "output/copy.mid")
```

## MIDI catalog scan

Scan `data/sources/` recursively and update `data/catalog/midi_catalog.json`:

```bash
midi-generator catalog scan
midi-generator catalog scan --source-dir /path/to/midi-pack --output data/catalog/pack.json
```

The Python API calls the same scanner:

```python
from midi_generator.api import scan_midi_catalog

summary = scan_midi_catalog("data/sources", "data/catalog/midi_catalog.json")
print(summary["scanned"], summary["succeeded"], summary["failed"])
```

Each catalog record keeps `analysis` and `error` separate, with editable `tags` and `provenance` fields. Removed files remain in the catalog with `present: false`, preserving annotations. A rescan updates analysis while retaining these user-managed fields.

## MIDI channel and note analysis

Analyze the channel usage and note numbers in a MIDI file:

```bash
midi-generator midi analyze input.mid
```

The report includes note counts, minimum/maximum note number, channel usage, GM Level 1 percussion mapping candidates, and note numbers outside the GM percussion map. A GM match is only a candidate and does not classify the file as drums.

Convert all channel-bearing messages to channel 10 (Mido channel 9) and write a separate file:

```bash
midi-generator midi convert-channel input.mid --to 10 --output output/converted.mid
midi-generator midi convert-channel input.mid --from 1 --to 10 --output output/converted.mid
midi-generator midi convert-channel input.mid --to 10 --dry-run
```

The conversion preserves notes, velocity, message timing, track names, tempo, meter, meta events, and SysEx. Existing destinations are never overwritten. Conversion does not add an `instrument=drums` catalog tag; after reviewing the result, use the existing catalog scan and tag commands if you want to register and classify it.

```python
from midi_generator.api import analyze_midi, convert_midi_channel

report = analyze_midi("input.mid")
conversion = convert_midi_channel(
    "input.mid", "output/converted.mid", target_channel=10,
)
```

## Drum candidate analysis for the MIDI catalog

Analyze every file currently listed in the catalog and write a separate report. The catalog and its manual tags are read-only during this operation:

```bash
midi-generator catalog analyze-drums
midi-generator catalog analyze-drums --file-id data/sources/example.mid
midi-generator catalog analyze-drums --report --csv data/catalog/drum_analysis.csv
```

The default JSON output is `data/catalog/drum_analysis.json`. Grades A–D are heuristic candidate groupings based on multiple signals (note distribution, GM percussion candidates, track names, rhythm/polyphony, and channel); they are not verified instrument labels. In particular, MIDI channel 10 is only supporting evidence, and no drum tags are added. Review candidates before tagging them.

The Python API uses the same analyzer and can also render the human-readable report or export CSV:

```python
from midi_generator.api import analyze_catalog_drums, render_drum_analysis_report

report = analyze_catalog_drums()
print(report["classification_counts"])
print(render_drum_analysis_report(report))
```

## Instrument-organized MIDI sources

Drums and other existing instruments keep their genre layouts. Guitar uses only
`data/sources/guitar/backing/` and `data/sources/guitar/lead/` directly; guitar
genres and sections are catalog tags. Legacy guitar paths remain readable with
`guitar_role: null`. See [safe guitar placement and reconciliation](docs/GUITAR_SOURCE_MIGRATION.md).
Files without a verified instrument retain the existing global `data/sources/unclassified/` compatibility path. Instrument is derived from this path and is kept separate from manual tags. Standard MIDI channels are configurable in `config/instrument_channels.json` (drums 10, guitar 1, bass 2, synth 3).

```bash
midi-generator source list
midi-generator source inspect --instrument drums
midi-generator source validate-channels
midi-generator source correct-channels --dry-run
midi-generator source correct-channels --apply
midi-generator source correct-channels --apply --copy-output
```

Channel correction skips mixed and unknown sources. Applying in place first creates a byte-for-byte copy in `data/backups/channel-correction-<timestamp>/`; corrected MIDI keeps note numbers, velocities, event timing, tracks, tempo, and meter. The CLI defaults to preview and requires `--apply` to write. `--copy-output` writes to `output/corrected_sources/` instead of editing source files. To back up and organize existing flat source files, run `midi-generator source migrate`; manual instrument/style tags drive folder assignment, while unclassified files remain unclassified. A user-verified classification can be supplied explicitly with `--instrument`. Genre can be assigned by S-series, for example `midi-generator source migrate --instrument drums --series-genres 'S01-S02:hardcore,S03-S05:metalcore'`. Automatic assignments are recorded separately from manual tags, with their rule and evidence in `classification_metadata`. Migration records each former path as a stable catalog `file_id` and updates the current `path`.

The current source set was divided by a project-specific provisional rule: tempo below 160 BPM goes to `metalcore`, and tempo at or above 160 BPM goes to `hardcore`. This is a coarse organizational heuristic, not a verified genre classifier. The catalog marks these genre values as automatic and low confidence; change the folders after manual review if needed.

Python API:

```python
from midi_generator.api import list_sources, validate_source_channels, correct_source_channels

sources = list_sources()
validation = validate_source_channels()
preview = correct_source_channels()  # no writes
# Apply only after reviewing the preview:
# result = correct_source_channels(apply=True)
```

## Tag and search patterns

Use the catalog path as the stable file ID (a unique basename also works):

```bash
midi-generator tag add data/sources/example.mid \
  --instrument drums --section breakdown --style metalcore \
  --tags half_time,double_kick
midi-generator tag show data/sources/example.mid
midi-generator tag update data/sources/example.mid --section verse --tags chug
midi-generator tag remove data/sources/example.mid --tags half_time

midi-generator search --instrument drums --section breakdown \
  --bpm-min 90 --bpm-max 110 --tags half_time
```

Taxonomy fields accept the documented instrument, section, and style values. Free-form tags are extensible. `--tags` requires at least one listed tag and scores candidates by the fraction that match; use `--required-tags` to require every listed tag. Specified taxonomy, BPM, meter, bars, note count, and pitch range conditions are filters. `--pitch-min` and `--pitch-max` match overlapping MIDI note ranges.

The same operations are available in the Python API:

```python
from midi_generator.api import add_tags, search_patterns

add_tags(
    "data/sources/example.mid",
    instrument="drums",
    section="breakdown",
    style="metalcore",
    tags=["half_time", "double_kick"],
)
matches = search_patterns(
    instrument="drums",
    section="breakdown",
    bpm_min=90,
    bpm_max=110,
    tags="half_time",
)
```

Tag records are separate from MIDI analysis and include source metadata such as `manual` and optional confidence. Search scores are 0–100 match-fit rankings, with per-condition contributions in `score_breakdown`; they do not assess musical quality. Weights use defaults in the storage layer and can be overridden through the Python API.

## Drum MIDI generation

Phase 4.1 adds read-only drum groove analysis and soft source-selection preferences:

```bash
midi-generator catalog analyze-grooves
midi-generator search --instrument drums --subdivision sixteenth \
  --timing-feel straight --syncopation high
```

The same flags work with `generate`; JSON/API requests use a `groove` object.
Results include match explanations and differences. Section tags remain required
for generation. See [groove metrics, thresholds, mapping, and API examples](docs/DRUM_GROOVES.md).

Generation requires a confirmed drum catalog record (instrument directory or manual tag) matching the requested section, style, tags, and meter. The engine never substitutes an unclassified file or infers drums from note pitches. The current catalog has one provisional `intro` source from the Phase 4 verification; other section tags require review before use.

```bash
midi-generator tag add data/sources/known_drums.mid \
  --instrument drums --section breakdown --style metalcore,hardcore \
  --tags half_time,double_kick
midi-generator generate --config examples/requests/drums_breakdown.json
midi-generator generate --config examples/requests/drums_breakdown.json \
  --output output/test_drums.mid
midi-generator generate --instrument drums --section breakdown --tempo 100 --bars 8 \
  --time-signature 4/4 --style metalcore,hardcore --tags half_time,double_kick --seed 42
```

Generation uses the same search/ranking implementation as the catalog API, chooses among matches with the request seed, sets the target tempo and meter, and loops or trims by whole bars. The output uses a `Drums` track and MIDI channel 10 by default; `options.midi_channel` or `--midi-channel` changes the one-based channel. MIDI and a `.mid.json` provenance sidecar are written without replacing existing outputs.

```python
from midi_generator.api import generate

result = generate({
    "instrument": "drums",
    "section": "breakdown",
    "tempo": 100,
    "bars": 8,
    "time_signature": [4, 4],
    "style": ["metalcore", "hardcore"],
    "tags": ["half_time", "double_kick"],
    "seed": 42,
})
print(result.output_files)
```

`GenerationRequest` is the shared generation input model. Pattern segmentation, advanced drum composition, lead/bass/synth generation, and reference-driven MIDI generation remain planned.

## Reference audio analysis (Phase 5.1)

Install optional audio libraries; the base MIDI CLI works without these extras:

```bash
python -m pip install -e '.[audio,dev]'
midi-generator reference analyze data/references/example.wav \
  --start 12 --end 28 --output data/cache/reference_analysis.json
```

WAV, MP3, and FLAC use soundfile/libsndfile decoding. Current macOS soundfile wheels
bundle libsndfile; MP3 support is decoder-build dependent. Check with
`python -c "import soundfile as sf; print(sf.__libsndfile_version__); print(sf.available_formats().get('MP3'))"`.
If MP3 is unsupported, use a current MP3-capable libsndfile/soundfile build or convert
a separate copy with `ffmpeg -i input.mp3 converted.wav`. See the
[audio guide](docs/REFERENCE_AUDIO.md) for installation steps, metrics, schema, and limitations.

The API separates analysis from JSON saving:

```python
from midi_generator.reference_analysis import analyze_reference_audio, save_reference_analysis

result = analyze_reference_audio("data/references/example.wav", start_seconds=12, end_seconds=28)
save_reference_analysis(result, "data/cache/reference_analysis.json")
```

Outputs include tempo/half/double candidates with measured relative scores,
onsets, activity, accents, RMS, and estimated grid alignment. Confidence is null
where no calibrated estimate is available. `--overwrite` is required to replace a
report; `--verbose` prints the full JSON. Invalid/out-of-bounds ranges are rejected.
The input audio, MIDI sources, MIDI catalog, and frozen generation engine are
unchanged. Phase 5.1 does not convert audio to MIDI or automatically use these
features for pattern search/selection. JSON Schema: `schemas/reference_analysis.schema.json`.

## Guitar riff analysis (Phase 6.1)

Read-only guitar pitch, interval polyphony, rhythm, duration/rest, chord-structure,
and bar-repetition analysis is available through CLI and API. Phase 6.2-A adds
drum-synchronized backing generation; lead generation remains planned. Reports are independent of the main catalog and manual tags.

```bash
midi-generator catalog analyze-guitars
midi-generator search --instrument guitar --subdivision sixteenth
midi-generator search --instrument guitar --riff-characteristic tremolo_like
```

See [guitar analysis/API, thresholds and limitations](docs/GUITAR_RIFFS.md).
The current 99 guitar assets contain possible articulation/control keys; these
are retained and flagged, so pitch/chord classifications require mapping review.


## Backing guitar generation (Phase 6.2-A)

Select an existing section/style-tagged guitar riff by its rhythm fit to an existing
drum MIDI. Pitch, velocity, chord structure and onset ticks stay intact; complete
source phrases are looped or trimmed. No lead generation or active rhythm editing.

```bash
midi-generator generate --config examples/requests/guitar_backing_intro.json --output output/my_backing.mid
```

The referenced drum must exist and match the requested BPM/meter. Sources need
section/style annotations and fresh Phase 6.1 analysis. Formal source roles use only the direct `guitar/backing/` and `guitar/lead/`
folders. The accepted generation prototype still has its earlier warned role
heuristics; these do not establish the formal catalog attribute. See
[Phase 6.1.1 source management](docs/GUITAR_SOURCE_MIGRATION.md) before relocating assets. Filenames have no required naming convention. See the
[backing guide and scoring/API details](docs/GUITAR_BACKING.md).


## Formal guitar roles and manual relocation (Phase 6.1.1)

```bash
midi-generator search --instrument guitar --guitar-role backing
midi-generator search --instrument guitar --guitar-role lead
midi-generator source reconcile-guitars --dry-run
```

Role searches exclude unclassified legacy sources and ignore filename, musical
feature and free-tag guesses. After manual placement, reconcile references before
rescanning. Stable IDs, tags and hash-verified riff features are preserved;
ambiguous matches block every update. Explicit apply backs up both JSON files and
uses a recoverable journal/lock transaction. MIDI is never moved by this tool.
CLI/API/recovery: [guitar source migration guide](docs/GUITAR_SOURCE_MIGRATION.md).
