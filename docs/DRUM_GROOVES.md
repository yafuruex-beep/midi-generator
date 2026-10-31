# Drum groove analysis (Phase 4.1)

This read-only analyzer describes existing confirmed drum sources. Labels are
heuristic estimates for selection, not musical quality scores or verified genre
annotations. It never adds, removes, moves, quantizes, or swings notes.

## Use

```bash
midi-generator catalog analyze-grooves
midi-generator catalog analyze-grooves --config config/drum_groove.json
midi-generator search --instrument drums --time-signature 4/4 \
  --subdivision sixteenth --timing-feel straight --syncopation high
midi-generator search --instrument drums --subdivision sixteenth \
  --timing-feel straight --syncopation high --backbeat standard \
  --density medium --tightness 0.95
midi-generator generate --instrument drums --section intro --tempo 160 --bars 8 \
  --time-signature 4/4 --subdivision sixteenth --timing-feel straight \
  --syncopation high --seed 42 --output output/metalcore_intro_test.mid
```

`generate` still requires a section tag. The last command may produce an
approximate groove match; inspect `groove_selection.differences` before treating it
as a requested musical match. No section is inferred or assigned by groove analysis.
`--groove-analysis` selects an alternative report for search/generation.

```python
from midi_generator.api import (
    analyze_drum_groove, analyze_catalog_grooves, get_drum_groove_analysis,
    search_patterns, generate,
)

features = analyze_drum_groove("source.mid")
report = analyze_catalog_grooves()
entry = get_drum_groove_analysis("data/sources/example.mid")
ranked = search_patterns(instrument="drums", subdivision="sixteenth",
                         timing_feel="straight", syncopation="high")
result = generate({
    "instrument": "drums", "section": "intro", "tempo": 160, "bars": 8,
    "seed": 42, "groove": {
        "subdivision": "sixteenth", "timing_feel": "straight", "syncopation": "high",
    },
})
```

## Mapping and time basis

The configurable map starts with GM assumptions: kick 35/36, snare 38/40,
hi-hat 42/44/46, cymbals 49/51/52/53/55/57/59, toms 41/43/45/47/48/50.
Unassigned GM percussion and non-GM notes are explicitly listed and excluded from
role-based metrics. A custom map can include non-GM notes. No note numbers are
rewritten. A low `mapping_coverage` means a custom source map may be needed.
In particular, the meaning of source articulations numbered 54, 60, 64, 70, or 74
is not inferred from their repetition. Their actual roles require source mapping
documentation or user confirmation.

Times are measured in quarter-note beats (`tick / ticks_per_beat`). A sixteenth is
0.25 beats in this coordinate system. Duration/bar estimates reuse the existing
catalog analyzer and preserve tempo/meter events. Independent format-2 sequences
return a per-file error instead of mixing independent timelines. Backbeat labels
are intentionally unknown for inconsistent or non-4/4 meters.

## Metrics and default decisions

Thresholds and role-reference densities are in `config/drum_groove.json`; the
effective configuration is saved in the independent analysis report.

| Feature | Default rule and retained evidence |
|---|---|
| Subdivision | At least 8 distinct onsets. Sixteenth needs at least 4 distinct odd sixteenth cells and >=20% odd-cell usage, or >=4 odd kick events and >=35% odd kick usage. Eighth permits <=1 odd cell or <=5% odd usage when grid alignment is high. Remaining binary mixtures are mixed. Insufficient evidence is unknown. |
| Timing feel | Straight requires >=85% of mapped events within 0.04 quarter beats of a sixteenth grid. Swung requires >=4 consistent base/offbeat pairs, >=65% ratio consistency, >=50% coverage of occupied pulse cells, median offbeat ratio 0.58–0.75, deviation <=0.04, and an actual departure from the straight grid. Both eighth- and sixteenth-swing pulses are inspected. A local triplet fill is insufficient. |
| Tightness | Mean of `max(0, 1 - abs(beat - nearest_sixteenth) / 0.125)`. Range 0–1; no mapped notes gives null. Small performance offsets reduce the score slightly without automatically implying swing. |
| Syncopation | At least 4 grid-aligned kicks. Score = 0.40 × odd-sixteenth kick share + 0.20 × off-eighth kick share + 0.25 × velocity-weighted weak-position accent share + 0.15 × weak kicks followed by an empty stronger quarter beat. Low <0.25, medium <0.55, high >=0.55. Continuous sixteenths do not automatically score high. |
| Backbeat | In consistent 4/4, >=4 accented snare events (>=70% of maximum snare velocity), >=70% position fit, and repeated bar coverage. Beats 2/4 => standard; beat 3 => half_time; off-eighth snares => double_time. Other supported positions => other. Quarter-pulse interpretation is reported; musical half/double BPM interpretation remains ambiguous. |
| Double kick | Consecutive unique kick onsets separated by 0.25 beats +/-0.04, minimum run length 3. Save each run length, longest run, runs/bar, and fraction of kick onsets within runs. Light <25% coverage, medium >=25%, heavy >=60% and >=4 run notes/bar. No runs => none; no known kick => unknown. |
| Density | Save raw overall density plus mapped role densities per quarter/bar. Class index = 0.4 × min(kick_rate/2,1) + 0.2 × min(snare_rate/1,1) + 0.4 × min((hat+cymbal)_rate/3,1). Low <0.30, medium <0.70, high otherwise. Overall note count alone does not decide it. Missing roles have unknown role-density labels. |

The confidence values are evidence-support indicators: mapped-note coverage
multiplied by onset support (saturating at 16 distinct onsets), with zero for
unknown labels. They are not calibrated probabilities of musical correctness.
Single-file analysis is deterministic; batch report timestamps describe execution.

## Persistence and selection

`data/catalog/drum_groove_analysis.json` is independent of the source catalog.
Each result uses the stable catalog `source_id`, current `file_path`, content
SHA-256, feature values, evidence, confidence, and warnings. Read errors are isolated
per source. Analysis never changes tags, IDs, paths, source files, or the catalog.
Missing or changed source content makes a stored analysis missing/stale for search.

Existing supplied filters (including instrument, section, meter and required tags)
remain mandatory. Groove conditions are soft preferences. Existing base-score
components and groove components are normalized together to 0–100. Exact labels
receive 1; ordered levels receive `1 - level_distance / maximum_distance`;
mixed subdivision receives 0.5, and eighth-vs-sixteenth receives 0.25. Different
timing/backbeat labels receive 0. Missing features receive 0, with differences
recorded. Tightness is a soft minimum: `min(actual / requested, 1)`.

New default weights: subdivision 15, timing 20, syncopation 15, backbeat 10,
double kick 10, density 10, tightness 15. `search_patterns(score_weights=...)`
can override `groove_<field>` weights. Ties resolve by current source path.
Generation reuses the existing seeded, score-weighted selection, so a higher score
is preferred rather than guaranteed. It records requested and selected features,
reasons, differences, map warnings, and confidence. Generation with groove
preferences fails clearly if all section-eligible candidates lack usable analysis.
Existing generation without groove preferences retains its previous behavior.
