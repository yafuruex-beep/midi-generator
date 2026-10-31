# Guitar riff analysis (Phase 6.1)

Phase 6.1 registers and analyzes guitar sources. Backing generation is now provided
separately by [Phase 6.2-A](GUITAR_BACKING.md); this analyzer remains read-only.
Instrument comes from the source directory, not channel or pitch. Formal guitar
roles now come only from direct `guitar/backing/` or `guitar/lead/` placement;
legacy paths have a null role. See [safe reference updates](GUITAR_SOURCE_MIGRATION.md). Sources currently
live directly in `data/sources/guitar/`; moving or renaming them is unnecessary.
Metalcore style is a user-provided default, recorded as manual metadata, not an
inference from filenames or MIDI. Unknown provenance/license details remain unknown.

## CLI and Python API

```bash
midi-generator catalog analyze-guitars
midi-generator catalog analyze-guitars --config config/guitar_analysis.json
midi-generator search --instrument guitar --subdivision sixteenth
midi-generator search --instrument guitar --syncopation high
midi-generator search --instrument guitar --riff-characteristic chug
midi-generator search --instrument guitar --riff-characteristic tremolo_like \
  --riff-characteristic stop_and_go
```

Repeated `--riff-characteristic` arguments (or comma-separated values) require ALL
requested labels. Guitar subdivision, syncopation, density and minimum tightness
are exact filters, unlike the accepted drum soft-preference behavior. Existing
catalog scores and deterministic path tie-breaking rank the surviving results.
Unsupported drum-only features produce an error for guitar queries. Missing,
stale (source SHA-256 mismatch), or failed reports cannot satisfy feature filters.
Search does not analyze or modify files implicitly.

```python
from midi_generator.api import (
    analyze_guitar_midi, analyze_catalog_guitars,
    get_guitar_riff_analysis, search_guitar_riffs,
)

single = analyze_guitar_midi("data/sources/guitar/example.mid")
report = analyze_catalog_guitars()
source = get_guitar_riff_analysis(report["sources"][0]["source_id"])
matches = search_guitar_riffs(subdivision="sixteenth",
                             riff_characteristics=["tremolo_like"])
```

Single-file analysis is deterministic and returns data without saving it. Batch
analysis records a UTC timestamp and writes an independent, regenerable report at
`data/catalog/guitar_riff_analysis.json`; it does not update the main catalog.
`catalog_path`, `output_path`, `project_root`, and `config_path` customize batch
operation. Search accepts `guitar_analysis_path` / CLI `--guitar-analysis`.
`schemas/guitar_riff_analysis.schema.json` defines version 1.0. JSON serialization
rejects NaN/Infinity. Each failure is isolated in a source record with status/error.

## Timing and note policies

Type 0/1 SMF events are merged by absolute tick across tracks. Note On velocity 0
is a release; repeated same-channel/same-pitch voices pair FIFO. At equal ticks,
existing voices release before new attacks, with remaining releases matched to
zero-length new voices. Unmatched releases are reported. Missing releases end at
the file's final tick and are explicitly marked estimated. Type 2 asynchronous
sequences and SMPTE timing are unsupported and reported as errors.

Tempo changes drive piecewise tick-to-seconds conversion. Quarter beats are the
common unit independent of ticks-per-beat; meter changes define bar segments and
partial bars. A note-free export tail of at most 0.04 quarter beats is merged with
the preceding bar for repetition only. No MIDI events are rewritten.

Pitch statistics are event-weighted, using MIDI numbers 0–127 and all 12 pitch
classes. Duration statistics include seconds and quarter beats. Rest intervals
are the complement of the union of sounding note intervals, including edge silence.
Density reports both raw notes and grouped attacks, per second/quarter/bar and
per-bar evolution; density classification uses grouped attacks per quarter to
avoid classifying chords as faster playing.

Polyphony is an interval sweep, not just simultaneous Note Ons. Raw duration-weighted
one/two/three-plus ratios use sounding time as denominator. Separate effective
ratios suppress short unsupported legato overlaps; true shared chord-attack
intervals remain. Chord groups use an anchored onset tolerance (not a chaining
window), distinct pitches, and minimum shared duration. Fifth/octave sets, conventional
triads including inversions, and extended third-containing sets are heuristic
structures, not verified chord roots/keys or tuning. Arpeggios outside the onset
window are not collapsed into chords.

Subdivision requires repeated grid evidence: at least eight attacks; sixteenth
requires at least four odd-sixteenth positions and a 20% share. A single displaced
attack cannot establish sixteenth. Syncopation combines odd-sixteenth positions,
weak eighth attacks, velocity-weighted weak accents, attacks after rests, and
notes held across an unarticulated stronger pulse. Density is not part of that
score. Meter-local pulses handle simple meters and compound eighth meters (e.g.
6/8 dotted quarters). Denominators other than 4/8 have unknown syncopation.
Repetition reports exact pitch/rhythm and rhythm-only bar matches separately from
similar matches within the configured timing tolerance; silent-only pairs do not
establish a period. Cross-meter repetition is unknown.

## Configuration and uncertainty

`config/guitar_analysis.json` contains documented-by-name thresholds for onset
and legato windows, subdivision, syncopation, density, durations, chords, repetition,
and each riff label. Missing keys use defaults; unknown/nonfinite/invalid settings
raise errors. Riff labels have supporting metrics and interpretation; confidence
is null because these heuristics have no calibrated probability. Multiple labels
are retained, with `mixed` added when appropriate. `unknown` means no requested
label met its thresholds. These are MIDI observations, never claims of palm muting,
open strings, picking direction, fret position, tuning, or audio sustain.

**Current asset caveat:** all 99 guitar files contain recurring very low notes
coincident with higher pitches. They may be articulation/control keys, but their
roles have not been confirmed. All notes remain included by default, so reported
pitch, polyphony, chord structures, and riff labels may include these controls.
In particular, the raw `chord_based` count is not proof that every riff is a
musical chord part. `possible_control_notes` lists evidence without assigning roles.
Only after verifying the source instrument map should a separate configuration
set `excluded_notes`; original MIDI and the main catalog remain unchanged.
Source-specific articulation maps are future work; a global exclusion is not
safe across unrelated packs.

## Registration and verification

The Phase 6.1 registration merged 99 guitar records into the existing 225-record
catalog, retaining all original records unchanged. The original catalog backup is
under `data/backups/phase61-registration-20261031T055656344383Z/`.
All 324 source file paths and bytes were preserved. Registration added no section
or performance annotations. Analysis never adds inferred labels to manual tags.
Actual measurements/search examples and compatibility checks are recorded in
`data/reports/phase61_verification_report.json`; tests generate their own MIDI fixtures.


Phase 6.1.1 adds optional `guitar_role` source metadata to catalog batch reports
and the existing JSON Schema without changing MIDI feature calculations.
Relocation updates only paths/role/context after hash verification; source IDs,
feature values, analysis timestamps and analysis version remain intact.
Single-file feature analysis remains independent of formal catalog classification.
