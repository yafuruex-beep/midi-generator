# Project Status

## Milestone 0: Initial scaffold
- [x] Standalone repository structure.
- [x] Python package metadata.
- [x] Shared request/result models.
- [x] Minimal MIDI read/write helpers.
- [x] CLI entry point.
- [x] Python API entry point.
- [x] Initial automated tests defined.
- [x] Install development dependencies and run the test suite.
- [x] Implement source catalog and batch MIDI analysis.
- [x] Implement catalog tagging and pattern search/ranking.
- [ ] Implement pattern segmentation and MIDI transformation.
- [x] Implement template-based drum generator.
- [ ] Implement guitar generator.
- [ ] Implement reference-audio feature analysis.
- [ ] Implement bass and synth generators.

## Notes
This is a starter scaffold, not a completed generative system. Planned features must not be described as implemented until code and tests exist.

Verification: installed the project and development dependencies in `.venv`, then ran `python -m pytest`; all 9 tests passed.

## Milestone 1: MIDI catalog
- [x] Recursive `.mid` / `.midi` scan with per-file error isolation.
- [x] Track, note, velocity, tempo, meter, duration, and bar metadata.
- [x] JSON catalog merge that preserves manual tags/provenance and marks removed files absent.
- [x] CLI and Python API use the same scanner implementation.
- [x] Catalog and regression tests; all 12 tests pass.

Initial repository scan: 225 MIDI files processed successfully, 0 failed. The generated catalog is `data/catalog/midi_catalog.json`.

## Milestone 2: Pattern classification and search
- [x] Add/update/remove/show taxonomy and custom tags with deduplication.
- [x] Preserve tag metadata across rescans and load legacy records without tags.
- [x] Search by taxonomy, tag, BPM, meter, bars, note count, and pitch range.
- [x] Rank matches deterministically and expose score breakdowns and configurable weights.
- [x] Add CLI and Python API operations using shared catalog logic.
- [x] Add tests for tagging, filtering, scoring, stable ordering, CLI, and rescans.
- [x] Run full test suite: 18 passed.

## Milestone 3: Template-based drum MIDI generation
- [x] JSON-compatible request validation and shared `GenerationRequest` use.
- [x] Catalog-backed drum pattern filtering, score ranking, and seeded candidate selection.
- [x] BPM/meter setup and complete-bar looping/trimming with paired Note Off handling.
- [x] Drums track/channel output, collision-safe filenames, and generation provenance sidecar.
- [x] CLI and Python API generation paths call the same core engine.
- [x] Drum generation/regression tests.
- [x] Full suite after Phase 3: 28 passed in 0.15s.

## Milestone 4: MIDI channel conversion and note analysis
- [x] Channel and note-number analysis added to the existing MIDI catalog analyzer/API.
- [x] GM Level 1 percussion names reported as candidates, with out-of-map notes listed separately.
- [x] Safe channel remapping with optional source channel, dry-run, and no-overwrite output.
- [x] `midi analyze` and `midi convert-channel` CLI commands plus Python API.
- [x] Tests preserve the 28 previous cases; full suite now has 33 passing tests.

## Milestone 5: Catalog drum-candidate analysis
- [x] Analyze catalog MIDI files at file and track level, including channels, notes/GM candidates, polyphony, onset intervals, tempo, meter, and estimated bars.
- [x] Explainable A–D candidate grading with reasons and confidence; channel 10 is supporting evidence only.
- [x] Save independent JSON output and optional human-readable report/CSV without modifying the source catalog or tags.
- [x] Add CLI and Python API on shared analysis logic.
- [x] Add synthetic MIDI tests and preserve the existing suite; full suite: 36 passed.
- [x] Run against the current catalog: 225 entries analyzed successfully, zero errors.

Current analysis output: `data/catalog/drum_analysis.json`. The current snapshot classifies 225 entries as A, 0 as B, 0 as C, and 0 as D. These are heuristic candidates only and do not add tags; manual review is still required.

## Milestone 6: Instrument-organized sources and channel standards
- [x] Add configurable standard channels for drums/guitar/bass/synth.
- [x] Add source listing, instrument-filtered inspection, channel validation, dry-run correction, and backup-first correction APIs/CLI.
- [x] Add safe source organization with verified backups, stable catalog `file_id`, preserved tags, and directory-derived catalog metadata.
- [x] Integrate directory-derived instrument into pattern search without converting it into a manual tag.
- [x] Add tests for folder classification, both channel directions, timing/note preservation, mixed/no-channel handling, backups, migration, stable IDs, and tag retention.
- [x] Full suite: 42 passed.

Initial source migration: 225 MIDI files were safely backed up and moved to `data/sources/unclassified/` because the existing catalog had no manual instrument/style tags. The separate backup contains the original files and catalog, and the originals' hashes were verified.

User-confirmed classification and calibration: the user confirmed all 225 files are drums and should use channel 10. All files were moved to `data/sources/drums/other/` (genre left as `other` because no narrower genre was confirmed), then calibrated in place after a second byte-for-byte backup. 50,209 channel-bearing messages were changed; non-channel MIDI event data, timing, track structure, notes, velocities, tempo, and meter were verified unchanged. Catalog rescan: 225 succeeded, 0 failed; all 225 now have `channel_status: valid`, actual and expected channel 10. No manual tags were added.

Genre organization: after the user selected an automatic rule, the 225 files were moved into `drums/hardcore/` (90 files) and `drums/metalcore/` (135 files) using BPM >=160 => Hardcore and BPM <160 => Metalcore. The rule, tempo evidence, automatic origin, and low-confidence caveat are stored in each catalog record's `classification_metadata`; no manual genre tags were added. MIDI hashes were verified unchanged during folder moves. A backup of the `drums/other` state and catalog is in `data/backups/source-migration-20261031T013544612753Z/`.

Latest full test suite: 44 passed.

## Phase 4: First real-source drum generation verification

The initial generation failure was caused by missing section annotations: all 225
catalog paths were valid and all instruments were drums, but no source had an
`intro` section tag. Search counts were 225 for drums, 195 for drums in 4/4, and
zero for drums + intro + 4/4.

After structural review, only `RoseNuMetal-S10-G07.mid` received a provisional
`intro` tag with confidence 0.5 for this initial test. Its source is approximately
158 BPM, 4/4, eight bars, with a sparse repeated pulse and a denser final bar.
DAW audition remains pending. The original catalog is backed up at
`data/backups/phase4-initial-generation-20261031T040402987547Z/midi_catalog.json`.
The intro search now returns one candidate. No generator code or package environment
was changed for this verification.

CLI output: `output/drums_intro_160bpm_8bars.mid`, with adjacent `.mid.json`
generation provenance. Python inspection confirmed 160 BPM, 4/4, eight bars
(15,360 ticks at 480 ticks/beat), 12 seconds, a Drums track on channel 10, and
60 Note On / 60 Note Off events with no unmatched or hanging notes. Source note
numbers, velocities, and absolute tick positions were preserved. Python API
regeneration with the same request and seed 42 produced identical MIDI bytes.
All 225 source-file SHA-256 hashes were unchanged, and the other 224 catalog
records were unchanged. Full pytest: 49 passed in 0.32s.

Detailed verification: `data/reports/phase4_initial_generation_report.json`.
Remaining work: user DAW playback and musical review; annotate other sections
incrementally after review. The intro label is a provisional test annotation.

## Phase 4.1: Drum groove analysis and preferred source selection

- Implemented subdivision, timing feel, kick syncopation, snare backbeat,
  double-kick runs, role-based density, and sixteenth-grid tightness metrics.
- Added configurable GM role mapping/thresholds, evidence and confidence, an
  independent `data/catalog/drum_groove_analysis.json`, CLI/API access, and
  soft groove preference scoring in existing search/generation.
- Preserved mandatory section, instrument, meter, and required-tag filtering;
  generated metadata includes selection reasons and requested/actual differences.
- Full pytest: **70 passed in 0.58s** (49 existing + 21 groove cases).
- Real-source analysis: 225 succeeded, 0 failed. Catalog bytes, manual tags,
  source IDs/paths, and all 225 source MIDI hashes remained unchanged.

| Metric | Current distribution |
|---|---|
| Subdivision | eighth 46; mixed 67; sixteenth 112; unknown 0 |
| Timing feel | straight 224; swung 0; unknown 1 |
| Syncopation | low 13; medium 174; high 38; unknown 0 |
| Backbeat | standard 23; half_time 52; double_time 1; other 119; unknown 30 |
| Double kick | none 153; light 39; medium 20; heavy 13; unknown 0 |
| Density | low 45; medium 180; high 0; unknown 0 |

Search for sixteenth + straight + high syncopation found 37 exact feature matches
among 225 candidates, or 34 within 4/4. Adding standard backbeat, medium density,
and a soft tightness minimum of 0.95 found 10 exact feature matches. Example:
`RoseNuMetal-S04-G01.mid` (tightness 0.959587). These labels are inferred features,
not verification of the source's musical genre.

No target-feature source currently has an intro tag. The only intro-tagged source
is eighth / straight / medium syncopation, so generation of the requested
sixteenth / straight / high-syncopation intro was skipped without assigning tags.
An integration smoke test using that existing intro's actual groove values
generated `output/drums_intro_groove_validation.mid`: 160 BPM, 4/4, eight bars,
12 seconds, channel 10, 60 paired notes. Same-seed CLI/API regeneration produced
identical MIDI bytes, including source notes excluded from analysis metrics.

Remaining work: confirm source-specific articulation mappings (current mean
mapped-note coverage is about 71%), review section annotations for the strong
target candidates, and audition in a DAW. No quantization, swing, note insertion,
or rearrangement was implemented. Detailed results and ranked candidate examples
are saved in `data/reports/phase41_verification_report.json`; algorithm details are in
`docs/DRUM_GROOVES.md`.

## Phase 5.1: Independent reference audio rhythm reports

Phases 1–4.1 are **Accepted / Baseline Frozen**. Their generation/groove/search
logic, request model, existing catalogs and 225 MIDI sources were unchanged.

- Implemented read-only WAV/MP3/FLAC decoding, sample-aligned range validation,
  mono analysis, tempo and half/double candidates, onsets, activity, accents,
  RMS/energy curves, and estimated beat/eighth/sixteenth grid alignment.
- Added optional `audio` dependencies (librosa, NumPy, soundfile) and `jsonschema`
  as a development dependency. Audio imports are lazy; MIDI-only CLI help and
  actual template generation pass when audio imports are blocked.
- Added `reference analyze`, Python analysis/save functions, strict UTF-8 JSON,
  atomic exclusive saving with explicit overwrite, and a JSON Schema 2020-12.
- Full pytest: **94 passed in 3.47s** (70 accepted tests plus 24 reference cases).
  WAV/MP3/FLAC, range errors, silence, short signals, strict finite JSON, schema,
  source protection, output protection, and optional-dependency isolation passed.

No real user reference audio was present. A clearly labeled, deterministic
32-second stereo synthetic signal was created at
`data/references/synthetic_160bpm_phase51.wav`, with a companion source manifest.
CLI analysis of seconds 12–28 saved `data/cache/reference_analysis.json`:

| Measurement/estimate | Verified value |
|---|---|
| Known synthetic pulse | 160 BPM |
| Estimated BPM | 159.998009 |
| Half/double candidates | 79.999005 / 319.996018 BPM |
| Onsets | 96 detected / 96 synthesized within the range |
| Onset density | 6.0 per second |
| Eighth/sixteenth grid-cell activity | 1.0 / 0.564706 |
| Strong onsets / offbeat accent ratio | 24 / 0.125 |
| Mean frame RMS / segment RMS | 0.026393 / 0.037662 |
| Sixteenth grid alignment | 0.935814 |

Candidate confidence stays null; relative scores are measured autocorrelation
support. Warnings explain instrument ambiguity, tempo/phase ambiguity, and segment
boundary limitations. The primary estimator includes a documented 120 BPM tempo
prior; alternative periodicities are retained. Schema validation succeeded, and
repeat API analysis matched CLI features except for the timestamp.

Final verification confirmed 231 frozen file hashes, including all 225 MIDI
sources, both existing catalogs, and the generation/groove/search/model files.
The input audio hash also remained unchanged. No reference-driven MIDI generation,
automatic query integration, or channel correction was executed.

Verification: `data/reports/phase51_verification_report.json`.
Guide/API/MP3 installation details: `docs/REFERENCE_AUDIO.md`.
Remaining work: evaluate real mixes and confirm tempo interpretation, then design
an explicitly authorized Phase 5.2 feature-to-query mapping. No such mapping is
part of Phase 5.1.

## Phase 6.1: Metalcore guitar registration and read-only riff analysis

Phases 1–5.1 remain Accepted / Baseline Frozen. No drum generator, drum groove,
reference-audio implementation, core request, or source MIDI was changed.

- Found 99 existing guitar MIDI files directly under `data/sources/guitar/`.
  All are type 1, two tracks, channel 1; no moves, renames, or channel changes.
- Registered 99 guitar entries using the existing scanner and a merge that kept
  all 225 previous records exactly unchanged. Catalog total: 324. Metalcore style
  is a user-specified manual default with metadata, not automated genre inference.
- Backed up the original catalog at
  `data/backups/phase61-registration-20261031T055656344383Z/midi_catalog.json`.
- Added interval note pairing/polyphony, pitch/classes, rhythm/syncopation/density,
  duration/rest, chord intervals, exact/similar bar repetition, multi-label riff
  estimates and evidence. Thresholds are configurable; confidence stays null.
- Added independent finite JSON, schema, batch error isolation, stable IDs/hashes,
  CLI `catalog analyze-guitars`, guitar search filters and shared public APIs.
- Full pytest: **123 passed in 3.31s** (94 baseline + 29 guitar cases).
  JSON Schema validation, repeat-analysis equality, single/API vs batch/CLI equality,
  real feature searches and 335 unchanged hashes passed, including all 324 source
  MIDIs and frozen implementation files. All original source paths are intact.

Actual analysis: **99 succeeded, 0 failed**. Eighth 59, sixteenth 24, mixed 16.
Syncopation low 68, medium 31, high 0. Raw interval polyphony reports chord_based
99, monophonic/mostly_monophonic 0. This is **not a verified musical chord count**:
all 99 files contain recurring low notes that may be articulation/control keys.
They remain included until the source instrument map is confirmed. Overall MIDI
range is 7–81, including these possible controls.

Riff label counts (overlapping labels): chug 0, syncopated_riff 0, gallop 0,
stop_and_go 11, power_chord_riff 2, single_note_riff 0, open_chord_riff 11,
tremolo_like 15, mixed 1, unknown 61. Feature searches return sixteenth 24,
high syncopation 0, chug 0, and sixteenth + medium syncopation + tremolo_like 15.
No thresholds were adjusted to force matches. Example candidates include
`LFUMMG_Riff_08_180_Wet_MIDI_F#.mid` (stop_and_go + power_chord_riff), and
`LFUMMG_Lead_01_120_Wet_MIDI_B.mid` (sixteenth + tremolo_like); all require review.

Report/schema: `data/catalog/guitar_riff_analysis.json`,
`schemas/guitar_riff_analysis.schema.json`.
Definitions/API/configuration: `docs/GUITAR_RIFFS.md`.
Full verification and changed-file inventory: `data/reports/phase61_verification_report.json`.
Next: confirm source-specific articulation maps and annotate musical note roles
before Phase 6.2 guitar generation; tuning and technique inference remain out of scope.

## Distribution layout refactor

- Consolidated reusable requests under `examples/requests/` and kept only active
  system settings under `config/`; retired scaffold settings/examples are archived.
- Moved design/status documentation into `docs/`, reference audio into
  `data/references/`, and verification records into `data/reports/`.
- Archived distinct older numbered analysis reports without deleting their data.
  Preserved historical report contents and recorded old/new paths in the migration
  manifest. Updated live reference paths in request/provenance/cache JSON.
- Verified backup before moving files; archived the unused second virtual
  environment and removed generated build/cache copies. `.venv/` remains active.
- Added deterministic, no-overwrite source ZIP packaging with an explicit file
  selection policy. It includes no local MIDI, audio, catalog, report or environment.
- Full local pytest: **125 passed in 3.46s** (123 existing + 2 release checks).
  Editable reinstall, CLI help, and validation of all 324 sources succeeded.
- Verified 1,295 unchanged file hashes, including all source and generated MIDI,
  the main catalog and program implementation. Original drum output was reproduced
  byte-for-byte through CLI and API. The moved MP3's first 14 seconds decoded and
  analyzed successfully. Guitar batch analysis succeeded for 99 sources; sixteenth
  search still returned 24 matches.

Guide: `docs/PROJECT_LAYOUT.md`; recipient setup: `docs/DISTRIBUTION.md`.
Verification: `data/reports/layout_refactor_verification.json`.
Backup: `data/backups/layout-refactor-20261031T061801353442Z/`.
The project code license is not yet specified; an owner-selected license should
be added before public distribution. No musical algorithms or CLI behavior changed.
