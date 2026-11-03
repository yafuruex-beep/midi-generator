# Reference section analysis — Phase 7.0 Step 2

Implemented: a read-only Python API for bounded full-mix audio sections. Existing
`reference analyze`, its schema, and MIDI generation behavior remain unchanged.
No Pattern Map schema, save API, dedicated CLI, cache migration, or generation
integration is implemented in this step.

```python
from midi_generator.api import analyze_reference_section

result = analyze_reference_section(
    "data/references/example.wav",
    start_seconds=0,
    end_seconds=90,
    # Optional analysis hypotheses; these do not change the audio:
    # reference_tempo_bpm=120,  # Only if the Reference tempo is independently known
    time_signature=[4, 4],
    bar_start_seconds=0,  # absolute source time, inside the selected section
)
for bar in result["bars"]:
    print(bar["bar_index"], bar["onsets_per_second"],
          bar["rms"], bar["pattern_id"], bar["quiet_fraction"])
```

Install existing extras with `python -m pip install -e '.[audio,dev]'`.
No dependencies were added. Audio libraries remain lazily imported.

## What the measurements mean

- The existing loader, onset detector, tempo candidates, phase estimation,
  activity windows, and RMS functions are reused. Audio is not decoded twice.
- `tempo` retains the automatically estimated BPM and half/double alternatives.
  `reference_tempo` records the adopted Reference BPM and its estimated/manual origin.
  `effective_tempo` is retained as a compatibility alias. Export BPM is not accepted.
- `beats` are constant-tempo quarter-note grid positions inferred from onset
  phase, not independently tracked beats. Variable tempo is not modeled.
- Meter is unknown unless supplied. With no supplied meter, a provisional 4/4
  grouping is used and explicitly labeled `provisional_assumption`. It is not
  time-signature detection. The first inferred pulse is only an assumed downbeat;
  its confidence is null. User inputs are hypotheses, not verified annotations.
- Bar numbering is local to this section, not the original song. A clipped first
  or last bar is marked `partial` and excluded from repetition grouping. Start/end
  timestamps are absolute audio times. RMS and onset density changes are measured
  relative to the preceding analyzed bar.
- Quiet candidates use 20 ms RMS windows below 8% of the section maximum (absolute
  floor 0.0001), lasting at least 180 ms. Confidence is `1 - mean_rms/threshold`,
  a heuristic quietness score, not a calibrated probability. Fades, masking, noise,
  and stereo cancellation can mislead it. These are acoustic full-mix pauses,
  never guitar-specific rests.
- Repetition uses cosine similarity of two independently normalized 16-bin
  vectors: onset strength and RMS shape within each complete bar. A bar joins the
  most similar previously established representative at similarity >=0.92;
  otherwise it establishes a new stable, first-seen ID. Silent/onset-free bars
  receive no pattern ID. Similarity is a heuristic confidence, not proof of the
  same notes, riff, harmony, or multi-bar musical phrase. Amplitude normalization
  deliberately allows similar rhythms at different strengths to share an ID.
- Unsupported automatic tempo leaves beats/bars empty while retaining onset,
  activity, energy, and quiet-interval measurements. Manual tempo/origin can
  provide a hypothesis grid. Nonempty very short/silent inputs remain valid;
  invalid or out-of-range intervals are rejected.

## Resource and reproducibility limits

Sections are limited to 120 seconds, a preflight 128 MiB decoded float64 input
budget, and at most 512 bars. These are input/workload limits, not a guarantee of
128 MiB process RSS: NumPy/librosa and spectrograms allocate additional memory.
The existing loader still holds the selected interval in memory; streaming decode
is not implemented. Hashing streams the source file. Repetition uses fixed-size
bar vectors, not a frame-by-frame self-similarity matrix.

No analysis timestamp is included. Same content, path, interval, settings, and
installed numerical environment produce equal JSON-compatible values. Different
paths are recorded differently; cross-version floating-point identity is not
promised. Persistent versioned JSON, library-version records, schema and CLI are
reserved for Step 3. This API does not write caches, catalogs, audio or MIDI.

Tests synthesize repeated pulse patterns, an inserted silent bar, short silence,
partial bars, invalid settings, forced tempo failure and preflight resource limits.

## Step 3 addition

Versioned persistence, the `reference pattern-map` CLI and readback APIs are now
available as a separate layer. See [REFERENCE_PATTERN_MAP.md](REFERENCE_PATTERN_MAP.md).
The section analysis itself is unchanged.
