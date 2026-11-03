# Reference generation plan — Phase 7.2 Step 2

This is a planning layer only. It reads an existing Pattern Map and writes a new
JSON plan. It imports no MIDI generator and does not generate clips, assemble MIDI,
change source assets, classify repeats, or process dynamics, rests or fills.
No new dependency or audio decoding is needed.

## Python API

```python
from midi_generator.api import (
    create_reference_generation_plan,
    save_reference_generation_plan,
    load_reference_generation_plan,
)

plan = create_reference_generation_plan(
    "data/cache/reference_patterns/ac0f75442e0de0e9f7914fddc173bb5ebe59bee6779f8a7ed7774fd8a1e043de.json",
    export_bpm=105,
    bar_range=[16, 37],
    section="chorus",
    time_signature=[4, 4],
    repeat_blocks=[{"source": [16, 17], "reuse": [36, 37]}],
    reference_bpm_confirmed=True,
    bar_start_confirmed=False,
)
save_reference_generation_plan(plan, "output/reference_plans/not_my_time_chorus.json")
restored = load_reference_generation_plan("output/reference_plans/not_my_time_chorus.json")
assert restored == plan
```

`bar_range` is inclusive and includes the intervening bars: this example covers
22 bars, not only the two chorus excerpts. `section` is a caller-specified
**generation search condition applied to the entire selection**, not an inferred
claim that every selected Reference bar is a chorus. It must be reviewed before
future execution. Local bar numbers refer to this exact Pattern Map.

Only explicit source/reuse ranges form multi-bar blocks. Other retained complete
bars become independent one-bar planned blocks, even if their Pattern IDs match.
Independent one-bar generation can cut guitar phrases; there is no claim that
such a plan will pass future source selection or phrase-fit validation.

The example has 20 blocks / 88 quarter-note beats. Bars 16–17 start at export beat
0 and bars 36–37 start at beat 80; both have length 8 beats and sequence
`P005 → P013`. The latter has `action="reuse_pair"`, references the former block
and shares its reuse key. Both instruments are intended to reuse the same future
finished pair of clips. This step does not create that pair.

## CLI

Save API keyword arguments as a JSON object (including `pattern_map_path`), then:

```bash
.venv/bin/midi-generator reference plan \
  --config output/reference_plans/not_my_time_chorus.input.json \
  --output output/reference_plans/not_my_time_chorus.json
```

Config paths are relative to the current directory, matching existing CLI usage.
New or nested output directories are supported. Existing destinations are never
overwritten, including identical plans. The input Map cannot be the output path.
No runtime audio libraries are imported just to make/read a plan.

## Validation and execution gates

- Export BPM must be finite and positive. It never overwrites Reference BPM.
- Reference tempo, source and confidence are copied separately from the Map.
  Version-1.0 maps remain readable but carry `legacy_unverified` tempo provenance
  and are blocked until Reference reanalysis. Nothing relabels old 105 BPM as true.
- Numerator/denominator must match the Map. Meter conversion is unsupported.
- Requested ranges must exist in the Map. An unknown-BPM map with no bars cannot
  supply a requested bar range: creation rejects it rather than inventing bars.
- Partial bars are excluded by default; a selection containing only partial bars
  is rejected. Interior partial bars cannot be silently removed. Export origin is
  the first retained complete bar, with no Reference leading-silence padding.
- Explicit reuse must point to an earlier, nonoverlapping, complete source range
  inside the selection, with equal length and the same non-null Pattern sequence.
  Duplicate/overlapping destinations and reuse chains are rejected. The same
  original source may be explicitly reused multiple times.
- Generation conditions are shared globally in this minimal version. Per-block
  overrides are not accepted. Reuse keys include Map ID, original block range,
  Export BPM, meter and section; matching IDs alone never create reuse.
- Confirmations are booleans acknowledging this exact Map's Reference BPM and
  origin. Null confidence is not a confirmation. BPM and origin confirmations
  remain separate. Unknown BPM/origin, missing confirmations and legacy maps
  prevent eligibility.
- `execution.eligible` means only those plan prerequisites are satisfied.
  `execution.implemented` is **always false**. No executor exists; eligibility does
  not validate source-library availability, key/harmony, guitar phrase length or
  musical suitability. There is no override that runs MIDI generation here.

Export positions and durations are quarter-note beats, independent of Export BPM.
Seconds can later be calculated as `beats * 60 / export_bpm`. Density, activity,
confidence and per-bar quiet candidates remain informational; no note-density,
velocity, expression or silence manipulation occurs.

## Format and verification

Schema: `schemas/reference_generation_plan.schema.json` (JSON Schema 2020-12).
Plan version 1.0; planner 7.2-plan.1. No timestamps are included; identical inputs
produce identical plan IDs. The ID covers all content except itself. Readback
validates identity, beat layout, coverage, reuse conditions and execution gates.
For complete structural validation, use the existing development dependency:

```python
import json
from jsonschema import Draft202012Validator
with open("schemas/reference_generation_plan.schema.json", encoding="utf-8") as f:
    Draft202012Validator(json.load(f)).validate(plan)
```

Tests use synthetic JSON Maps, exercise API/CLI roundtrip, explicit-only reuse,
Export BPM independence, unknown/unconfirmed gates, range/partial/meter checks,
schema validation, overwrite protection and tamper detection. Existing MIDI
regression tests run only against temporary test fixtures. Real source assets and
accepted generation outputs are not processed by this step.
