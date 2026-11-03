# Phase 7.3 / Phase 7.4 Step 4 — deterministic rule backing (B)

This independent Python API never reads guitar source MIDI or a catalog and does
not change `generate()`, the material selection generator, sync scores, or
Reference planning. No new dependencies or CLI commands were added.

```python
from midi_generator.api import (
    extract_rhythm_blueprint, generate_backing_steps, render_backing_midi,
)
blueprint = extract_rhythm_blueprint("path/to/one_bar_drums.mid")
steps = generate_backing_steps(blueprint, note=52)
midi = render_backing_midi(
    steps, export_bpm=105, program=31, output_path="output/new_rule_guitar.mid",
)
```

The example pitch 52 (E3) and GM program 31 (Distortion Guitar; encoded as 30)
are separate settings. The program selects a sound on a compatible synth; the
MIDI does not contain audio. Omit `program` to leave sound selection to the host.
Existing destinations are refused, including the input drum path.

`RhythmBlueprint` is immutable and contains sorted zero-based `kick_steps`,
`snare_steps`, `accent_steps`, `strong_steps=(0,4,8,12)`, `steps_per_bar=16`,
`time_signature=(4,4)`, warnings, source SHA-256, BPM and ticks per beat.
Accents are kick/snare cells whose velocity reaches 70% of the maximum mapped
kick/snare velocity. These are deterministic observations and metric positions,
not inferred harmony or a musical-quality judgment. Source BPM is informational;
Export BPM changes output tempo only, never beat positions or source files.

The extractor reuses `read_timeline()` for absolute ticks across all tracks,
Note On/Off pairing and MIDI validation, `constant_context()` for meter/tempo,
and `load_sync_config()` for existing kick/snare maps and tolerances. Default maps
are kick 35/36, snare 38/40. Pass `sync_config_path` explicitly for custom maps;
no working-directory configuration is discovered implicitly. Channel 10 is
preferred; a single other channel is accepted with a warning, while ambiguous
multiple channels without 10 are rejected. Unmapped percussion is not guessed.
Every selected drum onset is checked against the straight sixteenth grid.
Mapping to the nearest cell within the existing default 0.04 quarter-beat tolerance
is reported in warnings. Larger deviations are rejected. Without `bar_index`, onsets at/outside bar end are rejected and total input
duration must equal four beats within the existing 0.04-beat tail tolerance.
Longer input is refused unless a bar is explicitly selected as described below. Missing MIDI
tempo/meter use MIDI defaults with explicit warnings. Variable/conflicting tempo,
non-4/4 or changing meter, format 2, SMPTE timing and unmatched notes are rejected.

`generate_backing_steps(blueprint, note=..., rules=BackingRules(...))` returns an
immutable tuple of `BackingStep(step, note, duration_steps, velocity, voicing="single")` values.
A step and one unit of duration are each a sixteenth (0.25 quarter beats).
Rules:

- Keep every kick cell, with velocity 108 by default.
- Fill gaps of at least four steps on the eighth grid, starting after the first
  kick and ending before step 16; supplements stay at least two steps from kicks.
- Suppress supplements one step before snares; kicks always take precedence.
- Base velocity 76, metric strong-beat velocity 96, observed accent velocity 100;
  use the highest applicable value.
- Default duration is half a step (0.125 quarter beats), capped before the next
  onset and bar end. Short notes express muted rhythm; no keyswitch is sent.

`BackingRules` exposes these velocities, short duration and pre-snare rest toggle.
There is no randomness or seed. Empty kick maps are rejected. No note is emitted
before the first kick. Dense kicks can occupy every grid cell: the generator does
not invent additional sixteenth attacks or remove kick attacks.

`render_backing_midi()` accepts the intermediate steps independently of the B
rules. It validates one fixed root pitch, sorted nonoverlapping chord events, exact tick
representability, MIDI channel (human 1..16 except drum channel 10), durations and
Export BPM, and returns `mido.MidiFile`. Output is one 4/4 bar with explicit tempo,
paired Note On/Off, and end-of-track at four beats. Default resolution is 480;
custom resolution must represent a sixteenth grid and each duration exactly.
Saving uses exclusive creation and never overwrites an existing file.

For phase C, retain the Blueprint and renderer and add a separate probabilistic
step function. Probabilities, seeds, multiple candidates, bass, AI inference,
key/chord/tuning inference, swing and multi-bar generation are not implemented.


## Step 2 — select one bar from a longer drum MIDI

```python
blueprint = extract_rhythm_blueprint("path/to/multi_bar_drums.mid", bar_index=1)
steps = generate_backing_steps(blueprint, note=52)
render_backing_midi(steps, export_bpm=105, program=31,
                    output_path="output/new_guitar_bar2.mid")
```

`bar_index` is an optional, zero-based integer: 0 is the first bar, 1 the second.
Omission (or `None`) keeps the Step 1 strict one-bar contract. Explicit selection
uses integer tick bounds `[bar_index * 4 * tpb, (bar_index + 1) * 4 * tpb)`.
Negative, boolean, non-integer, absent and final partial bars are rejected. An
explicitly selected bar must have its full four-beat window present in the file;
the legacy short-tail tolerance applies only when `bar_index` is omitted.

Note On alone determines window membership. Releases can cross either edge
without importing a preceding attack or editing a release. Pairing and constant
tempo/4/4 context are validated across the full MIDI, so changes or malformed
Note On/Off outside the selected bar also reject the input. Channel selection is
the existing full-source channel policy. Only selected drum onsets are grid
checked; other channels are ignored when channel 10 is available, and unknown
percussion is grid checked but never classified as kick/snare. Empty kick windows
reject rather than guessing or inventing a rhythm.

Onsets are normalized by subtracting the exact window start before applying
the unchanged 0.04 quarter-beat grid tolerance. Accent velocity thresholds use
only mapped kick/snare attacks inside the selected bar. An onset just before
the end that would round to step 16 is rejected, never wrapped to step 0 or moved
to the next bar. Boundary-exact attacks belong to the next bar.

Blueprint provenance additionally records `source_bar_index`,
`source_bar_start_tick` and `source_bar_end_tick` (defaults `None` for manually
constructed blueprints). Existing fields and positional constructor arguments
are retained. Generated output is still exactly one local bar; no source-position
padding, placement, multi-bar guitar or file cropping is performed.

For future multi-bar guitar work: define placement/offset metadata, clip-boundary
Note Off and continuity policies, phrase-level rules, and deterministic assembly
tests before adding an arranger. C probabilistic generation remains unimplemented.


## Phase 7.4 Step 3 — selectable note duration

Use the existing optional `rules` argument; no new positional argument or CLI.
`duration_steps` is in sixteenths, each equal to 0.25 quarter-note beats:

```python
from midi_generator.api import BackingRules, generate_backing_steps
steps = generate_backing_steps(blueprint, note=52,
                               rules=BackingRules(duration_steps=2))
# .5 step = .125 beats (unchanged default), 1 step = .25 beats, 2 steps = .5 beats
```

The accepted finite range is now `0 < duration_steps <= 2` (previously <= 1).
Boolean, nonnumeric, nonfinite, zero, negative and out-of-range values reject.
The selected gate travels in `BackingStep.duration_steps` to the unchanged MIDI
renderer. It is capped at the next attack or bar end: a .5-beat request before a
next sixteenth, or on the final sixteenth, becomes .25 beats. At shared ticks,
Note Off precedes the next Note On. Defaults remain byte-identical. Attack
positions, supplemental/rest decisions, pitch and velocity rules are unchanged.
Longer gates intentionally reduce gate-level silence; this does not add or
remove any attack or introduce a new rest mask. Arbitrary durations must still
be exactly representable at the renderer's ticks_per_beat.

The core renderer still emits one local bar with a fixed root. No guitar-source
selection, chord progression, new accent/rest rules, probabilistic or AI generation is added.


## Phase 7.4 Step 4 — optional power chord voicing

```python
steps = generate_backing_steps(
    blueprint, note=52,
    rules=BackingRules(duration_steps=1, voicing="power8"),
)
render_backing_midi(steps, export_bpm=105, program=31,
                    output_path="output/new_power8.mid")
```

`BackingRules.voicing` accepts `single` (default, root only), `power5`
(root + 7 semitones) and `power8` (root + 7 + 12 semitones). Root 52 emits
52; 52/59; or 52/59/64 respectively. `BackingStep.note` remains the root;
its optional final `voicing` field defaults to `single` so existing four-argument
construction works. `BackingStep.notes` exposes the validated chord tuple.
Direct renderer callers can construct these steps without the rule generator.

Unknown voicings and chord tones beyond MIDI 0..127 raise `ValueError` before
saving. There is no clipping, octave folding, transposition, or pitch randomness.
Maximum roots are 127 (`single`), 120 (`power5`), and 115 (`power8`).
All chord tones share attack tick, release tick and attack velocity. Every release
at a shared tick precedes every next attack. Existing rhythmic selection, rests,
accents, syncopation, next-onset/bar-end duration caps and default MIDI bytes are
preserved. Note Off velocity remains zero, as before. No new CLI command is
introduced; existing CLI/API core behavior is untouched.

### Audition files

`output/archive/phase7/phase74_step4_power_chords_105bpm_8bars/` contains two comparison groups:
0.25 beats and 0.5 beats. Within each group A is single, B is power5, C is power8.
Each has guitar-only and drum-plus-guitar files; all use the identical existing
105 BPM eight-bar drums and root 52. `verification.json` records pitches,
onsets, gates, velocities, pairing, file hashes, and unchanged existing MIDI hashes.
The selected duration still shortens where required by the original rules.

Reproduce into a **new** directory (existing destinations are refused):

```sh
.venv/bin/python scripts/create_power_chord_auditions.py \
  output/archive/phase7/phase74_step3_duration_105bpm_8bars/drums_8bars.mid \
  output/archive/phase7/phase74_step4_power_chords_105bpm_8bars
```

The script assembles eight independently rendered bars for audition only; it does
not introduce a multi-bar arranger or change drum/bass generation. No external
guitar MIDI is used. Existing drum provenance/usage notes remain in its source
directory, linked by path and SHA-256 in the verification report.

## Step 4 extension — mixed single/power5 (initial listening hypothesis)

```python
steps = generate_backing_steps(
    blueprint, note=52, rules=BackingRules(voicing="mixed", duration_steps=1),
    phrase_bar_index=bar_index,  # explicit zero-based arrangement bar, default 0
)
```

`single` remains the default; `power5` and `power8` remain available. `mixed`
is a generator policy, resolved into concrete `single`/`power5` steps before
rendering. It is not accepted as an unresolved `BackingStep.voicing`.
`phrase_bar_index` must be a nonnegative integer; it is explicit and independent
of a blueprint's source bar index. Callers assembling repeated bars must advance
it. The renderer and rhythm selection are unchanged.

This is an **initial listening hypothesis**, not a common rule established from
five favorite examples. No taste inference or verified annotation is claimed.
For bars with at least two attacks, one attack becomes power5 and all others
remain single. A four-bar cycle has target sixteenth cells 0, 8, 4, 15.
Candidates are ranked by distance to that target, minus a preference of 3 cells
for an onset after a structural gap (>2 sixteenth cells between attacks), and
minus 4 for the first attack on cycle bar 0 or the last attack on cycle bar 3.
Ties choose the earlier attack. This makes gap resumes candidates without
requiring them to be chords. Eighth-note runs receive sparse chord accents even
without a structural rest; all remaining attacks stay single.

Silence caused only by a short gate is ignored when ranking: changing duration
cannot change the voicing placement. No cross-bar gap inference is performed;
phrase boundary preferences are explicit. With only one attack in a bar, it is
power5 only on cycle bar 0 and single on bars 1–3; mixing inside one event is
impossible. Different cycles can coincide for sparse/irregular input; the
regular eight-bar audition explicitly verifies variation. Mixed roots must be
0..120 on every cycle bar, even when that bar resolves to single only.

The following remain identical to single: onset set, rests, syncopation, root,
duration caps, attack velocity and zero Note Off velocity. Chord tones share
On/Off ticks. No rhythm events are added, removed or shifted.

### Mixed audition

```sh
.venv/bin/python scripts/create_mixed_backing_auditions.py \
  output/archive/phase7/phase74_step3_duration_105bpm_8bars/drums_8bars.mid \
  output/archive/phase7/phase74_step4_mixed_105bpm_8bars
```

Use a new destination; existing directories are refused. At 105 BPM / eight bars,
A is single, B all power5, C mixed. Each has 0.25- and 0.5-beat requested gates,
guitar-only and drum-plus-guitar MIDI. All variants share the same existing drums.
`placement.md` lists every onset and the mixed chord positions;
`verification.json` contains checks, hashes and source provenance. Other onset
positions are single. The previous power8 comparison and all existing outputs
are preserved. This script uses the same Python core; it adds no generator CLI
command and changes no drum, bass or material-selection implementation.

## Phase 7.4 Step 5 — power chord event ratios

```python
steps = generate_backing_steps(
    blueprint, note=52,
    rules=BackingRules(voicing="mixed", power_chord_ratio=.75, duration_steps=1),
    phrase_bar_index=bar_index,
)
```

`power_chord_ratio` is optional and defaults to `None`, retaining the Step 4
mixed policy exactly. Existing single/power5/power8 behavior and default MIDI
bytes are unchanged. Explicit ratios require `voicing="mixed"`; supported values
are `.5`, `.75`, and `1.0`. Other values, booleans, and nonfinite values reject.
The output contains only root or root+7, with the same root range 0..120 as mixed.

The denominator is the **actual generated attack-event count**, not the number
of MIDI notes, grid cells or kick cells. Per bar, chord count is the nearest
integer to `event_count * ratio`, rounding half up. When the event count is not
divisible by the ratio denominator, exact percentages are impossible; rounding
is local to each bar (there is no cross-bar quota/carry). Inspect actual counts
for short/irregular bars. A single-event bar at 50% rounds to one chord event.

Placement keeps the existing mixed chord candidate as an anchor (fallback to
index zero if absent). For N attacks and K chords, select event indices
`(anchor + floor(j*N/K)) % N` for `j=0..K-1`. This spaces chords by actual
attack index and retains deterministic four-bar anchor variation without
altering any rhythm event. At 100%, every event is power5 and MIDI bytes match
ordinary power5 for the same settings. The chord count ignores gate duration.
This step changes only chord membership; groove improvement is deferred.

### Step 5 audition

```sh
.venv/bin/python scripts/create_ratio_backing_auditions.py \
  output/archive/phase7/phase74_step3_duration_105bpm_8bars/drums_8bars.mid \
  output/archive/phase7/phase74_step5_ratios_105bpm_8bars
```

The destination must be new. Output is 105 BPM / eight bars / 0.25-beat gates,
root 52 (power5 = 52/59), with identical existing drums and rhythmic events:

| Version | Chord events | Single events | Actual chord ratio |
|---|---:|---:|---:|
| A | 64 | 0 | 100% |
| B | 48 | 16 | 75% |
| C | 32 | 32 | 50% |

Each version has guitar-only and drum-plus-guitar MIDI. `verification.json`
records measured ratios grouped from rendered notes, per-bar placement, pitch,
onset/gate/velocity/pairing checks, provenance and hashes. Existing MIDI assets
are hash-checked and never overwritten. The script calls the same Python core;
no drum/bass generator, material-selection path, or existing audition tool changes.

## Phase 7.4 Step 6 — explicit four-bar rhythm phrases

```python
from midi_generator.api import BackingRules, generate_backing_phrase, render_backing_midi
bars = generate_backing_phrase(
    blueprint, note=52, bars=8,
    rules=BackingRules(rhythm_mode="phrase4", voicing="mixed",
                       power_chord_ratio=.75, duration_steps=1),
)
# Each item is a tuple of local-bar BackingStep values for the existing renderer.
for bar in bars:
    clip = render_backing_midi(bar, export_bpm=105, program=31)
```

`BackingRules.rhythm_mode` accepts `repeat` (unchanged default) or `phrase4`.
The new `generate_backing_phrase()` explicitly repeats **one base blueprint**;
`bars` is a positive integer, default eight. It returns local-bar step tuples,
not a new MIDI type. Bar zero-based indices are passed to the existing step
function for rhythmic role and mixed voicing placement. For direct per-bar use,
`generate_backing_steps(..., phrase_bar_index=i)` supports the same mode; use the
same base blueprint to reproduce the phrase. Source drum bar selection remains
explicit and separate. No drum events are generated/edited by these functions.

This is a deterministic listening hypothesis informed by the requested reference
features (repetition, stop/go, sparse development and space), not a transcription
or a claim that a genre's groove has been established. Gates stay fixed in this
comparison; long/short note contrasts and timing humanization are not added.

The four roles are A / small variation / A / ending, repeated exactly in bars
5–8. The optional different eighth-bar ending is not used in this version.
The original B rule computes A, then a bounded edit changes only supplements:

- Cycle bars 0 and 2 keep A unchanged.
- Cycle bar 1 shifts the latest safe even-cell supplement at/after cell 8 one
  sixteenth later. It moves at most one event.
- Cycle bar 3 chooses the latest safe even-cell supplement at/after cell 12,
  removes up to two preceding supplements strictly after cell 8, and moves that
  last event one sixteenth earlier. The resulting space is not filled again.

Kicks are never removed or moved. A moved event must be inside cells 0..15,
outside the existing pre-snare rest mask, and at least two cells from remaining
onsets. Candidates are tried latest first. If no safe candidate exists, the bar
keeps A: dense all-kick or very sparse input is not forced into artificial
variation. The maximum edit is two event removals plus one moved event. The
base groove's existing supplement logic is reused; no extra fill runs after
the edit, and attack counts are not equalized. No cross-bar note is introduced.

Surviving events keep their velocity. Moved events keep their **source event's**
velocity instead of receiving a new strength/accent at their new cell. Root and
requested gate are unchanged; existing next-onset/bar-end caps still apply.
Mixed allocation runs after the rhythm edit, using the existing per-bar event
count and half-up rounding. Its sparse/ratio defaults remain unchanged. This
means a six-event ending gets five chords at 75%, not an enforced equal count.

### Step 6 comparison artifacts

```sh
.venv/bin/python scripts/create_phrase_backing_auditions.py \
  output/archive/phase7/phase74_step3_duration_105bpm_8bars/drums_8bars.mid \
  output/archive/phase7/phase74_step6_phrase_105bpm_8bars
```

Existing destinations are refused. A is the original one-bar rhythm repeated;
B is phrase4. Both are 105 BPM, 4/4, eight bars, mixed/75%, root 52/fifth 59,
and 0.25-beat gates. Guitar-only and drum-plus-guitar files are saved separately.
The original drum tracks (all messages, not only note positions) are copied and
verified exactly on reloading. Original sources and output MIDI hashes are
checked. No external guitar MIDI is used; drum provenance is linked by source
path/hash. `comparison.md` lists each bar's positions/removals/additions;
`verification.json` records measured ratios, per-bar positions/chords, MIDI
pairing, gates, velocities and hashes.

For the supplied base groove, A has 64 attacks / 48 chords (75%); B has 60
attacks / 46 chords (76.6667%). B bars 2/6 move cell 14 to 15. Bars 4/8 remove
cells 10/12 and move cell 14 to 13. All other positions remain. Cells are
zero-based sixteenths; divide by four for quarter-note beats from the bar start.

## Phase 7.4 Step 7 — opt-in gate contrast and ending backbeat space

The existing phrase API adds three optional keyword settings. Defaults retain
all Step 6 MIDI bytes; the one-bar step generator and renderer are unchanged.

```python
settings = BackingRules(rhythm_mode="phrase4", voicing="mixed",
                        power_chord_ratio=.75, duration_steps=1)
A = generate_backing_phrase(blueprint, note=52, rules=settings)
B = generate_backing_phrase(blueprint, note=52, rules=settings,
                            duration_mode="contrast")
C = generate_backing_phrase(blueprint, note=52, rules=settings,
                            duration_mode="contrast", drum_mode="ending_space")
```

- `duration_mode="fixed"` (default) or `"contrast"`.
- `long_note_ratio=.15`: experimental target fraction of actual attack events,
  finite 0..1, booleans rejected. It is not an inferred optimal ratio.
- `drum_mode="preserve"` (default) or `"ending_space"`.

Refinement requires an explicit `rhythm_mode="phrase4"`; contrast also requires
`duration_steps=1` (0.25 beats). It operates only in `generate_backing_phrase()`,
not in direct one-bar `generate_backing_steps()` calls. No new voicing, random
pitch/velocity, timing jitter, probability or dependency is introduced.

### Gate policy and its limits

Process each four-bar block independently so repeated phrases stay exact.
Candidates are one-step gates with at least three steps to the next attack or
bar end. They may become two-step gates (0.5 beats), **retaining at least one
silent sixteenth** before the next attack/end. Thus continuous eighth chugs,
final-sixteenth notes, and existing one-cell articulation pauses stay short.
A long note consumes one cell of a larger pause, but never fills that pause
completely; longer structural pauses and ending stops remain present.

The target count is `floor(block_event_count * long_note_ratio + .5)`, limited
to available candidates and capped below the total event count. Rank candidates
by bar-final first, then higher existing velocity, then earlier bar/event.
No velocity is changed. If the requested fraction cannot be reached without
filling rests, fewer notes are extended; sparse input may have rounding error
outside the initial 10–20% listening range. Ratios are measured, not guaranteed.
The supplied 30-event phrase has only three safe candidates: 10% are extended,
below the .15 target. They are cycle bar 1 step 12 and cycle bar 3 steps 8/13.
All others remain 0.25 beats. Gates never cross an attack or a bar boundary.

### Ending-space policy

After gate contrast, only cycle bar 3 may lose an event (at most one per four
bars). Choose the latest non-kick event two steps before a snare when the next
guitar event coincides with that snare and the preceding note already releases
before it. Preserve kick events, simultaneous snare attacks and the last pickup.
With no qualifying event, keep the bar unchanged. This is a small listening
hypothesis about backbeat space; it does not force a change to already aligned
kicks. No event moves and no new onsets are added by this policy.

Do not recompute voicing, velocity or gates after deletion: every retained C
event is identical to B. This isolates the test and permits a small measured
chord-ratio deviation. On the supplied drums, every bar's only mapped kick is
step 0, already matched exactly. The mapped snare is step 8 (actual first-bar
tick 958, while guitar step 8 is tick 960 at 480 TPB); reports distinguish raw
kick tick matches from tolerance-mapped snare/space observations. Drum tracks,
including their off-grid ticks and all metadata, remain unchanged.

### Step 7 auditions and report

```sh
.venv/bin/python scripts/create_groove_backing_auditions.py \
  output/archive/phase7/phase74_step3_duration_105bpm_8bars/drums_8bars.mid \
  output/phase74_step7_groove_105bpm_8bars
```

The new output directory is required. Optional `--baseline-directory` identifies
the Step 6 comparison folder; A must match its phrase4 guitar and paired MIDI
byte for byte. All variants are 105 BPM, 4/4, eight bars, mixed/75%, root 52/fifth
59. Each includes guitar-only and drum-plus-guitar files.

| Variant | Attacks | Power5 | Actual power5 % | 0.5-beat events | Long % | Exact kick matches |
|---|---:|---:|---:|---:|---:|---:|
| A: unchanged Step 6 | 60 | 46 | 76.67 | 0 | 0 | 8/8 |
| B: gate contrast | 60 | 46 | 76.67 | 6 | 10.00 | 8/8 |
| C: ending space | 58 | 44 | 75.86 | 6 | 10.34 | 8/8 |

B extends bar 2/6 step 12 and bar 4/8 steps 8/13. C removes bar 4/8 step 6,
widening mapped pre-snare silence from one to three sixteenths (0.25 to 0.75
beats). It preserves the final offbeat chord and all kicks. Positions are
zero-based sixteenths. No increase in exact kick matches is claimed; listening
is needed to assess whether the added space improves the groove.

`comparison.md` and `verification.json` record all gate/onset changes and their
reasons, per-bar sync/space data, measured ratios, pairing/boundary checks,
provenance, source/output hashes and A's Step 6 byte identity. `final_report.md`,
`validation.json` and `pytest.txt` additionally preserve the implementation's
full test/compatibility results. Existing MIDI assets are hash-checked and never
overwritten. No drum/bass generation or original guitar material is edited.
