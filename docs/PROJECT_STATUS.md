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
- [x] Implement drum-synchronized backing guitar generation; lead remains planned.
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


## Phase 6.2-A: Drum-synchronized backing guitar generation

Accepted drum generation, drum groove analysis, guitar riff analysis and reference
audio algorithms remain unchanged. No original MIDI, production catalog, manual
tag, source ID, or existing analysis report was modified.

- Added a backing-only generation engine using existing catalog mandatory filters,
  Phase 6.1 source hashes/features and the existing MIDI timeline/note pairing.
- Added configurable kick-first precision/recall, grid, syncopation, sounding-rest,
  snare-accent and repetition scores. Chords count as one rhythmic attack.
- Added phrase-length ranking, deterministic seeded selection within a top-score
  band, whole-source looping/trimming, boundary releases, preserved pitch/velocity/
  controls/track count, fixed target tempo/meter and guitar channel 1 by default.
- Added `--guitar-role`, `--sync-to-drums`, `--guitar-analysis` on generation and
  `--guitar-sync-config`, shared by CLI/API. Two request JSON aliases normalize
  into existing `options`; existing direct request fields are unchanged.
- Explicit backing/lead roles use tags or optional instrument/genre/role folders.
  Filenames are not a generation requirement. Unknown roles remain warned,
  unverified candidates; explicit lead/conflicting roles are excluded.
- Added provenance sidecars, strict finite JSON, no-overwrite export, generic
  request example, source-ZIP inclusion and terminal/API documentation.

Full verification: **168 passed in 5.72s** (125 existing + 43 guitar generation
cases). Baseline before changes: 125 passed in 4.24s. Synthetic tests cover TPB
normalization, non-4/4 meters, exact/dense rhythms, rests, chords, role separation,
phrase fit, note pairing/retriggers, controller order, configurable selection,
CLI/API equality, seeded stability and input/output protection.

Real validation used 39 user-directed `Riff`-named guitar sources and
`output/drums_intro_160bpm_8bars.mid`. Naming was only a user-specified validation
scope, not a program rule. Because no intro definitions existed, a separate
`data/cache/phase62_guitar_validation_catalog.json` holds the provisional intro
context; the production catalog retains all original tags. 37 candidates were
usable; two were excluded for insufficient straight-grid alignment.

Selected: `data/sources/guitar/LFUMMG_Riff_22_186_Wet_MIDI_G.mid`.
Output: `output/guitar_backing_intro.mid` and adjacent `.mid.json`.
Both drum and guitar are 160 BPM, 4/4, eight bars, 12 seconds. The guitar retains
all 76 source notes with identical onset/end ticks, pitches, velocities, channels
and track positions. Source length is exactly eight bars, so no phrase was cut.
CLI/API repeated generation produced identical MIDI bytes and synchronization
metrics. All 357 protected file hashes matched, including the 324 source MIDI,
existing generated drums, production catalogs/reports and frozen implementations.

| Actual metric | Value |
|---|---:|
| Overall sync score | 0.561416 |
| Kick coverage | 13/16 = 0.8125 |
| Guitar attack precision | 13/38 = 0.342105 |
| Kick alignment component | 0.547612 |
| Grid match | 0.740741 |
| Syncopation match | 0.454545 |
| Rest compatibility | 0.438596 |
| Snare accent relation | 0.805455 |
| Repetition match | 0.607143 |

The fit is moderate, not a promise of complete musical synchronization. The drum
contains one off-grid snare ornament retained at its actual time. The source has
possible articulation keys that were retained. Actual backing role remains
unverified. DAW audition was not performed. Phase 6.2-B may add active rhythmic
editing; lead, transpose, chord/tuning conversion and AI composition remain out
of scope.

Guide: `docs/GUITAR_BACKING.md`.
Full checks/file inventory: `data/reports/phase62a_verification_report.json`.


## Phase 6.1.1: Formal guitar role folders and safe reference updates

This source-management milestone follows acceptance of the Phase 6.2-A prototype.
The prototype remains present and unchanged; no generation/synchronization or
musical-analysis algorithms were added or altered in this milestone.

- Formal guitar roles come only from direct `data/sources/guitar/backing/` or
  `lead/` file placement. Genres/sections remain catalog tags. Filename, channel,
  free-tag and musical-feature guesses never establish the formal attribute.
- Old genre layouts and flat guitar files remain readable with an effective null
  role. Existing old empty directories are retained, but new scaffolding and
  source distributions create only backing/lead guitar role directories.
- Added strict `search --guitar-role backing|lead` and shared API filtering;
  omission retains previous retrieval/ranking. Optional role metadata extends
  catalog batch reports and the backward-compatible guitar JSON Schema.
- Added read-only `source reconcile-guitars --dry-run`, backed-up `--apply`,
  explicit ID/path `--mapping`, and verified `--recover`. No MIDI is moved.
- Relocation preserves original file_id/source_id, manual tags, provenance,
  unknown user attributes, legacy genre/history, and hash-verified riff features.
  Duplicate-content identities need explicit mapping; registered records are
  never merged, and any unresolved condition blocks the entire update.
- Apply uses verified before-images, atomic per-file replacement, complete-owner
  exclusive locks, a recovery journal, content checks and rollback. This is a
  recoverable multi-file update, not a single filesystem transaction. Recovery
  refuses later user edits, tampered backups and live/conflicting writers.
- Catalog scans preserve migration history and refuse detectable unreconciled
  moves or reused stable IDs. Generic source migration no longer moves guitars
  into genre folders; one old test expectation was changed to enforce this rule.

Baseline: **168 passed in 5.00s**. Final: **208 passed in 5.56s**, including 40 new
migration/classification cases and all drum, guitar-analysis, guitar-prototype and
reference-audio regressions. Tests used generated MIDI in temporary directories.
Failure injection covered rollback, failed rollback, abrupt interruption,
recovery retry, edited metadata, source changes, and ambiguous identical content.

Actual-source dry-run inspected 99 guitar records and confirmed all 99 existing
identity/content mappings: **0 changes, 0 integrity issues, 0 source ID changes**.
All 99 MIDI remain directly under `data/sources/guitar/`; legacy genre folders
contain no MIDI. Formal roles: backing 0, lead 0, unclassified 99. One identical-
content group contains two distinct sources; they were not merged.

Only the two empty formal role directories were added. Real MIDI moves: **0**;
real metadata apply: **not executed**. All 369 protected original hashes matched,
including source MIDI, original catalogs/reports, accepted generated MIDI and
frozen generation/analysis modules. Guitar-role searches correctly returned 0/0,
ordinary guitar search 99, drum search 225. Existing riff JSON still validates
without the optional new role property.

Guide: `docs/GUITAR_SOURCE_MIGRATION.md`.
Verification/file inventory: `data/reports/phase611_verification_report.json`.
Update backups are created only on apply under
`data/backups/guitar-reference-update-<UTC timestamp>/`; actual dry-run made none.

Next: users decide and place backing/lead sources, reconcile ID/path/hash
references, and verify style/section tags. Formal generation integration should
then restrict retrieval to verified backing-folder records and be auditioned;
the existing prototype's warned role heuristics are not the formal source label.
Historical generation sidecars and temporary validation catalogs are not rewritten.

## Phase 7.0 Step 2 — Reference section analysis

Added `analyze_reference_section()` to the public Python API. Reuses optional
reference DSP for bounded range analysis and adds provisional regular beat/bar
hypotheses, per-bar activity changes, full-mix quiet candidates and deterministic
bar-shape repetition IDs. Meter/downbeat ambiguity is explicit; no automatic
instrument-specific rest claim is made. Existing reference reports and MIDI
engines remain unchanged. No new dependencies.

Step 3 versioned persistence/schema/CLI and Step 5 Python 3.11 source-ZIP environment
reconstruction have not been implemented or verified in this step. Resource
preflight caps section duration at 120 seconds and raw decoded input at 128 MiB;
streaming decode is not implemented. See `docs/REFERENCE_SECTION.md`.

Verification: **225 passed in 5.44s** (208 existing + 17 new cases), including
existing MIDI generation and source-ZIP layout tests. Python 3.13.6 arm64.

## Phase 7.1 Step 3 — Reference Pattern Map persistence

Added a separate version-1.0 JSON adapter over Step 2, atomic exclusive saving,
content-addressed default paths under `data/cache/reference_patterns/`, validated
readback and `reference pattern-map`. Integer-equivalent CLI/API inputs normalize
to the same identity. Different existing files and the legacy cache destination
are never overwritten. No new dependencies or DSP/generation changes.

Schema: `schemas/reference_pattern_map.schema.json`.
Guide/API examples: `docs/REFERENCE_PATTERN_MAP.md`.

Step-3-only verification: **7 passed in 2.15s**, using generated WAVs. Tested
schema validity, Step 2 adaptation, readback, stable repeated inputs, cache reuse,
no overwrite, silent/short/invalid audio and old-cache protection. The earlier
integer-versus-float cache-key mismatch was corrected and the tests rerun.

Read-only actual-reference check: The Queendom 0–90 seconds, 105 BPM / 4/4
hypothesis / origin 0 produced 40 bar intervals (including the final partial bar)
and 3 full-mix quiet candidates. The saved map validates and reuses without a
write. Original audio and legacy reference-cache hashes matched. These results
are acoustic hypotheses, not instrument transcription or validated song sections.

The MIDI generators, sync modules, existing reference analyzer, dependency list
and release script were not changed in Step 3. Full regression/resource testing
(Step 4), source-ZIP reconstruction (Step 5), and calibrated/variable-tempo or
meter/downbeat inference remain pending. No full-suite or release test was run
for this step, as requested.

## Phase 7.1 Step 4B-0 — Reference versus Export BPM

Implemented `reference_tempo_bpm` / `reference pattern-map --reference-tempo`.
Legacy `tempo_bpm` / `--tempo` retain their analysis-override meaning and emit
warnings; simultaneous new/old overrides are errors. Generation request tempo,
`generate --tempo`, generators and synchronization modules are unchanged. No
Export BPM is accepted by Pattern Map analysis. Failed estimation stays unknown.

New maps use schema **1.1**, analyzer **7.1-bpm.1**, and record
`reference_tempo.{bpm,source,confidence}` with estimated/manual origin. The old
`effective_tempo` remains a compatibility alias; original automatic candidates
remain separate. Half/double ambiguity and the distinction between BPM support
and downbeat support are warned about. Feature vectors and repetition thresholds
are unchanged.

Schema 1.0 is preserved at `schemas/reference_pattern_map_v1.schema.json`.
Old maps load unchanged with a warning; their analysis overrides are not confirmed
Reference tempos. There is no automatic rewrite, relabeling, or cache migration.
Versioned content identity prevents old/new key collisions. Existing save guards
prevent replacement of old caches. No dependencies added.

Verification: **240 passed, 10 expected legacy-argument FutureWarnings in 6.15s**.
This includes 8 added BPM responsibility/compatibility cases, existing Reference
regressions, and existing MIDI generation/synchronization tests. The focused
Reference run passed 32 tests in 3.73s.

The Queendom 0–90 seconds was analyzed with automatic Reference tempo and manual
4/4 / bar-origin-0 hypotheses: estimated BPM 117.34894413336433 (confidence null),
45 bar intervals. This is not confirmed original meter/tempo. New map:
`data/cache/reference_patterns/67152a4d07753ae0cfec69772d14f376d650532c064121b91de7aa101cc6e03e.json`.
Schema validation and readback passed. The old 105-BPM map remains unchanged and
loads with the legacy warning. All 498 pre-existing files under source/reference/
catalog/cache data roots retained their hashes during this check.

Remaining: verify Reference pulse/downbeat by listening/annotations, assess
half/double candidates and repetition accuracy under the correct grid, resource
and Python 3.11 distribution checks. Phase 7.2 was not started.

## Phase 7.2 Step 2 — Generation plan JSON only

Added an independent `planning` package with create/validate/save/load APIs and
`reference plan --config ... --output ...`. Plans record Reference Map identity,
Reference BPM provenance, distinct Export BPM, selected complete bars, explicit
repetition blocks, quarter-note export positions, informational Reference features,
reuse keys and execution prerequisites. They do not call any generator.

Explicit reuse requires an earlier, nonoverlapping original block with the same
length, meter, generation conditions and known Pattern ID sequence. Repeated IDs
alone never group blocks. Partial bars are excluded; out-of-range references,
overlaps, duplicate reuse destinations, chains and meter conversion are rejected.
Unknown/unconfirmed BPM or origin prevents eligibility. Legacy maps require
reanalysis. Null confidence never supplies confirmation. Eligibility means plan
prerequisites only; execution is unimplemented for every plan.

Guide: `docs/REFERENCE_GENERATION_PLAN.md`.
Schema: `schemas/reference_generation_plan.schema.json`.

Local example: `output/reference_plans/not_my_time_chorus.input.json` and
`output/reference_plans/not_my_time_chorus.json`. Selection 16–37 includes the
intervening bars; section `chorus` is an illustrative generation condition for
that whole range, not inferred real-song section annotation. The plan contains
20 blocks / 22 bars / 88 beats. Bars 16–17 are a new planned pair at beat 0; bars
36–37 reuse it at beat 80; both are 8 beats and P005→P013. Export BPM is 105;
Reference BPM remains estimated 106.00556691091187. User-observed approximate
106 BPM following is acknowledged, but origin 0 is not confirmed: eligibility is
false with `bar_start_unconfirmed`. No MIDI was created by planning or example
execution. API/CLI results match and validate against the schema.

Final verification: **262 passed, 10 expected legacy-argument warnings in 5.92s**,
including 22 new plan cases and the existing generation/synchronization regressions
on temporary test fixtures. All 500 original source/reference/catalog/cache files
and 17 existing output MIDI retained their hashes in the example check. AST
comparison confirms `api.generate()` is unchanged. Instrument modules, sync,
Reference classifier/features/0.92 threshold and dependencies are unchanged.

Next step requires a reviewed generation plan, confirmed Reference bar origin,
source-library/section/phrase-fit validation and a separately authorized executor.
No generator connection, assembly, automatic fill/rest/dynamics or later Phase 7.2
step was implemented here.


## Phase 7.3 Step 1 — Rhythm Blueprint and rule backing (B)

Added an independent one-bar, 4/4, sixteenth-grid pipeline: drum MIDI to
immutable RhythmBlueprint, deterministic single-pitch BackingStep sequence, then
MIDI rendering with separate Export BPM. No guitar sources or catalogs are used.
Existing generation, material selection, sync scores and Reference analysis are
unchanged. See `docs/RULE_BACKING.md` for API, rules and rejection policies.

Next C: probabilistic/weighted step selection from the same Blueprint, reusing
the renderer; seeds, multiple candidates and bass are not implemented here.
Long-term D: investigate learned drum/guitar/bass correspondence and AI inference;
no training, inference implementation or dependencies are added in this step.
Only B one-bar generation is implemented; multi-bar/swing, key/chord/tuning
inference and instrument-specific keyswitches remain unsupported.


## Phase 7.3 Step 2 — read-only selection of one drum bar

`extract_rhythm_blueprint(..., bar_index=1)` selects the second complete 4/4
bar from a longer MIDI using half-open absolute-tick bounds and local-zero
normalization. Omitted/None bar_index retains Step 1 strict input length behavior.
Negative/non-integer/absent/partial bars reject. Full-source constant tempo/meter
and Note On/Off pairing are validated; crossing releases remain untouched.
The existing mapping, 0.04-beat onset tolerance, rule generator and renderer are
preserved. Blueprint provenance adds source bar index and tick bounds.
See `docs/RULE_BACKING.md` and `tests/test_rule_backing_bars.py`.

Output remains one local guitar bar. Multi-bar placement/assembly, cross-clip
continuity, phrase planning and C probabilistic generation remain future work.
No source editing, guitar material selection, AI, bass or dependencies added.

Step 2 verification: **325 passed** (300 existing + 25 new), with 10 expected
legacy-argument warnings. Existing data/output files: 1,487 hash matches; no
changes. Existing unrelated src/tests/config files are hash-identical; AST checks
confirm the step generator and MIDI renderer are unchanged.

Real source: `data/sources/drums/hardcore/RoseNuMetal-S01-G01.mid`, 4/4, constant
200 BPM, 16 bars, tpb=480. Selected second bar has kick cells [2,10,12], snare
cells [0,6,8,14]. Seven selected drum onsets map within the unchanged 0.04-beat
tolerance, with a warning. New local one-bar E3/GM program 31/Export BPM 105 output:
`output/phase73_step2/guitar_bar2_105bpm_E3.mid`, with same-name JSON report
containing source/output hashes, Blueprint, steps, timing errors and checks.
All three kick cells are covered; raw kick timing errors are within tolerance.
Original source and prior outputs remain untouched. Audio listening validation
and multi-bar placement/continuity are not claimed. Step 2 stops here.


## Phase 7.4 Step 3 — selectable guitar note duration

Expanded existing `BackingRules.duration_steps` from maximum 1 to 2 sixteenth
steps, enabling .125/.25/.5-quarter-beat gates through the existing optional
`generate_backing_steps(..., rules=...)` API. Default .5 step (.125 beats) is
unchanged. Next-attack/bar-end capping and renderer are retained. No onset,
supplement/rest, pitch, velocity, drum, material selection or Reference rule
changes. Added duration/regression tests; see `docs/RULE_BACKING.md`.

Step 3 verification: **345 passed** (325 existing + 20 added), 10 expected
legacy-argument warnings. Pre-change default-output golden hash and previous
accepted sample MIDI both match byte-for-byte. A/B/C have identical eight
attacks/pitches/velocities/counts; actual gates .125/.25/.5 quarter beats.
No shortening in the sample; retrigger/final-sixteenth tests shorten .5 to .25
beats and verify Note Off precedes Note On at shared ticks. Extractor, step
generator and renderer executable bodies are AST-identical (docstring excluded);
only BackingRules gate validation maximum changed. Existing data/output files
(1507) retained all hashes. New sample directory:
`output/phase74_step3_duration_105bpm` (three guitars, three drum/guitar pairs, shared
drum, verification JSON and guide). No audio-listening validation claimed.
Step 3 stops here.
