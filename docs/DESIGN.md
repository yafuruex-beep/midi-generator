# Design

## Goal
Build an independent, reusable MIDI generator callable through both CLI and Python API. Song-specific settings stay outside the core package.

## Architecture
- `core/`: shared models, MIDI I/O, validation, and future pattern infrastructure.
- `modules/`: instrument-specific generators, added incrementally.
- `reference_analysis/`: optional read-only audio loading, onset/tempo/rhythm/energy analysis, and independent JSON reporting.
- `storage/`: MIDI source catalog, manual tag management, and pattern search/ranking.
- `data/sources/`: guitar uses direct backing/lead roles; other instruments keep existing genre layouts; `unclassified/` holds unverified material. Directory-derived instrument data is kept distinct from manual tags.
- CLI and Python API call the same core implementation.

## Initial implementation
- Shared request/result models.
- Minimal Standard MIDI File load/save and inspection support using `mido`.
- Recursive MIDI source scanning, per-file metadata extraction, and a merge-safe JSON catalog at `data/catalog/midi_catalog.json`.
- Manual taxonomy/free-form tags with source/confidence metadata, persisted separately from analysis.
- Catalog search with strict supplied filters, preferred/required tags, deterministic ranking, and per-condition score breakdown.
- Template-based drum MIDI generation using catalog search results, reproducible seeded selection, tempo/meter metadata, and bar-aligned loop/trim processing.
- MIDI channel usage and GM drum-note candidate analysis; explicit channel remapping to a separate output file.
- Read-only drum groove analysis in `modules/drums/groove.py`, independent report storage in `storage/drum_grooves.py`, and soft groove preferences shared by search and generation.
- Optional reference audio feature reports, separate from accepted MIDI analysis/search/generation and manual annotations.
- Catalog-wide drum-candidate analysis with file/track MIDI features, A–D heuristic grades, reasons/confidence, and independent JSON/CSV reports. Candidate analysis never changes catalog tags.
- CLI commands for MIDI inspection, round-trip copying, catalog scans, tag/search operations, and drum generation.
- Python API for shared request/result models, MIDI loading/saving, inspection, catalog/tag/search, and drum generation.
- Export refuses to replace an existing destination unless overwrite is explicitly requested.
- Initial automated tests.

The catalog stores paths relative to the project root when possible, preserves removed entries with `present: false`, and retains manual `tags` and `provenance` on rescans. Analysis errors are stored separately from successful metadata. Duration and bar counts are estimates based on MIDI tick positions, tempo events, and time-signature events; missing tempo and meter use MIDI's 120 BPM and 4/4 defaults with warnings. Format 2 MIDI sequences are treated independently for duration and bar estimates.

Tag data uses separate taxonomy arrays (`instrument`, `section`, `style`) and extensible `custom` labels. `tag_metadata` records the origin and optional confidence per label so later automatic classifications can coexist with manual tags. Search treats taxonomy, BPM, meter, bars, note-count, and pitch-range criteria as filters. `tags` is an OR preference that requires at least one match and scores the number matched; `required_tags` is an AND filter. Search weights are overridable through the Python API, and ties resolve by catalog path. Scores describe query fit, not musical quality.

The initial drum generator requires a manually tagged drum catalog entry, then applies the shared ranking, target-tempo/bar preferences, and a seeded weighted choice. It preserves source note numbers and tick positions, rewrites the tempo/meter metadata, maps the selected drum events to MIDI channel 10 by default, and writes an adjacent JSON provenance record. It selects drums only from channel 10, explicit drum/percussion track names, or a sole note-bearing track whose catalog entry is tagged as drums. It does not infer drum roles from pitches. Mixed or unrecognized multi-track files fail clearly. Source/target meter conversion is not performed.

Channel conversion operates on a loaded copy and changes only channel-bearing MIDI messages. It preserves note values, velocities, delta times, track names, tempo/meter events, and all non-channel events. GM Level 1 percussion mappings are reported as candidates only; channel conversion never adds drum tags automatically.

Catalog drum-candidate analysis reads existing catalog entries and source MIDI files, retaining per-file errors and per-track features such as channels, note counts, GM mapping candidates, note overlap/polyphony, and onset spacing. It writes a separate report rather than modifying `midi_catalog.json`. A–D grades are explainable heuristic candidates, not verified instrument classifications; no grade automatically adds an instrument tag. Channel 10 is never used as a negative signal and can only support a candidate when other evidence exists.

The source manager reads instrument from the directory path and genre from the immediate genre directory. `config/instrument_channels.json` contains the system's channel defaults. Catalog records store stable `file_id` separately from the current `path`, plus directory classification, actual/expected channels, and a `valid` / `needs_correction` / `mixed` / `unknown` status. Migration uses existing manual instrument/style tags by default; an explicit user-verified classification can organize unclassified sources. It verifies a byte-for-byte backup of all source MIDI and the catalog before moving anything. Optional per-series genre assignments are stored in `classification_metadata` with source, method, parameters, evidence, and confidence separate from manual tags. The current 225-file set uses a provisional tempo threshold (below 160 BPM = metalcore, at/above 160 BPM = hardcore); this is a low-confidence organizational heuristic, not definitive genre analysis. In-place channel correction is an explicit apply operation, makes a byte-for-byte source backup first, skips mixed/unknown sources, and updates the catalog while preserving user tags. Dry-run is read-only. Copy-output mode leaves the catalog and sources untouched.

Groove metrics, default thresholds, GM mapping assumptions, and score contributions
are documented in `docs/DRUM_GROOVES.md`. The core request carries a generic
`groove` object; drum-specific labels and interpretation stay in the instrument
module. Independent reports preserve stable source IDs, source hashes, evidence,
warnings, and confidence. Groove preferences add ranking components to existing
mandatory filters. Section annotation remains mandatory for drum generation;
analysis never assigns section tags or modifies timing. Missing/stale groove
evidence is reported instead of silently treated as a known match.

## Planned, not implemented
- Pattern segmentation and MIDI transformation.
- Lead guitar generation, active guitar rhythm editing, and advanced drum composition.
- Reference-audio-driven pattern retrieval/generation (Phase 5.2), variable-tempo tracking, and downbeat/section interpretation.
- Bass and synth generation.
- Cross-instrument coordination.

## Reference audio policy
Reference audio provides abstract musical guidance (tempo, section, rhythmic density, dynamics, stylistic tags), not a mechanism for copying recordings. Keep source metadata, user annotations, estimated features, and confidence values distinct. Manual section annotation is the initial preferred workflow.

Phase 5.1 implements independent WAV/MP3/FLAC reference reports. Optional NumPy,
soundfile, and librosa imports occur only on an audio analysis call, allowing the
MIDI-only CLI/API to operate without the audio extra. Reading, analysis, saving,
and CLI presentation are separate. JSON saving is UTF-8, finite, atomic, and
exclusive unless overwrite is explicit. Reports preserve source hash, range,
decoder metadata, algorithm/library versions, measured energy, estimated events,
tempo candidates, and tempo-dependent grid uncertainty. Confidence is null rather
than an invented probability. No automatic link into MIDI search or generation is
implemented. See `docs/REFERENCE_AUDIO.md` and `schemas/reference_analysis.schema.json`.

## Data and rights
Preserve original source files. Record provenance and license/usage notes before cataloging or redistributing assets. Possession of a file does not by itself establish permission to redistribute or train on it.

## Reproducibility
Record generation settings, random seed where applicable, input/source identifiers, and generator version. The drum engine writes request, source catalog ID, selection score, processing steps, output path, seed, and generation time to a JSON sidecar.

## Guitar analysis (Phase 6.1)

`modules/guitar/timeline.py` reads and pairs notes across tracks without editing
MIDI, reusing existing MIDI I/O and catalog context extraction. `analysis.py`
computes interval polyphony, pitch, rhythm, density, durations/rests, chord
structures, repetition and explainable multi-label riff estimates. Thresholds and
explicit note exclusions live in `config/guitar_analysis.json`. Unknown instrument
articulation maps are never guessed or silently excluded.

`storage/guitar_riffs.py` isolates batch errors and stores an independent,
versioned, schema-defined report with source IDs and hashes. Minimal search
dispatch routes guitar feature filters to this report; existing drum scoring and
generation are unchanged. CLI/API call the same implementation. Phase 6.2-A backing generation is separate from the read-only analyzer;
lead generation, tuning inference and automatic technique assignment are not implemented.
See `docs/GUITAR_RIFFS.md` for metric definitions and uncertainty.


## Drum-synchronized backing guitar (Phase 6.2-A)

`modules/guitar/sync.py` normalizes existing MIDI timelines to quarter beats and
sixteenth cells, groups chord attacks, and computes configurable overlap, accent,
rest and repetition scores. `backing.py` reuses catalog mandatory filters and
Phase 6.1 reports, separates explicit roles from uncertain feature evidence,
ranks candidates and performs a seeded choice within a configurable top band.
Formal guitar source directories are now instrument/role only; genre remains a tag. The prototype
heuristics are separate from formal role metadata.
Filenames are not a generation rule. No inferred labels are written to manual tags.

`render.py` retains source pitches, velocities, onset offsets, tracks and controls,
with whole-phrase looping/cropping, boundary releases, fixed tempo/meter and guitar
channel 1 by default. Export reserves new MIDI/JSON files exclusively. Core request
fields are unchanged: two new JSON aliases normalize into existing `options`.
The API dispatch and CLI flags share this one implementation. Drum generation,
guitar analysis and reference-audio algorithms stay frozen.


## Safe guitar source references (Phase 6.1.1)

Only direct `guitar/backing/*.mid` and `guitar/lead/*.mid` placement establishes
the formal role. Legacy guitar paths remain readable with null roles. Tags,
filenames and musical features never establish this attribute. Existing guitar
prototype algorithms remain unchanged in this source-management milestone.

`storage/guitar_migration.py` builds a read-only one-to-one reconciliation plan
from stable catalog IDs, current paths and saved MIDI hashes. Hash-duplicate
identities require explicit mapping; registered records are never merged. Apply
is all-or-nothing when identity problems exist, preserving tags, provenance,
unknown user fields and cached feature values. Legacy genre and path history
remain separate from current directory metadata.

Both JSON before-images are verified and backed up before replacement. Staged,
fsynced files, per-directory locks, a journal, optimistic hash checks and rollback
provide recoverable updates, not a single multi-file filesystem transaction.
Incomplete updates block normal catalog access until verified recovery. Recovery
refuses changed metadata, tampered backups and live/conflicting locks. MIDI is
never moved. Scans preserve stable IDs/history and refuse detectable unreconciled
moves or ID collisions. Generic source migration no longer creates guitar genre
folders or moves guitar-tagged flat assets. See `docs/GUITAR_SOURCE_MIGRATION.md`.
