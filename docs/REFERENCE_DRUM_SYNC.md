# Phase 12 Step 3: original support drums and fixed-guitar sync

No guitar event is changed. `compose_original_drums` accepts the saved 480 TPB,
eight-bar guitar and shared audio groove map. It composes original notes on GM
channel 10, then reuses the existing drum generator's `_select_drum_tracks` and
`_make_output_midi` for validated event/tempo/length assembly. It does **not**
claim catalog `api.generate()` has become a reference-audio transcription API.
The legacy catalog selector and Phase 10 defaults remain unchanged.

Mapping comes from `core.midi_io.GM_DRUM_MAP` and `config/drum_groove.json`:
36 kick, 38 snare, 42 closed hat, 46 open hat, 49 crash. All are auditioned in
the existing macOS DLS percussion bank before the full render. D kicks lock to
a selected subset of existing guitar attacks, ranked partly by observed mix
accent scores. Quarter hats maintain count across ordinary guitar rests;
beat-3 snare is an original half-time choice. Bar 7 deliberately drops the
pulse/snare during the extended break, returning at the actual guitar restart.
C uses eighth hats, backbeat2/4, and a restrained two-bar harmonic entry color.
No universal sixteenth kick fill or guitar note insertion.

`compare_reference_phases` uses **all raw** mix observations, not just previously
accepted on-grid candidates. It scans eighth/sixteenth phases independently per
band and per four-bar half, and compares four possible quarter-beat bar labels.
This is not isolated drum analysis. Subdivision agreement cannot uniquely label
bar one. No automatic change to the adopted C working grid or fixed guitar.

`analyze_eighth_positions` treats 1/3/5/7 as quarter beats0/1/2/3 initially,
recording attacks vs sustained notes vs composed rests and kick/pulse relations.
Even bars of the original D start a sixteenth late; this is a composed onset,
not automatically a renderer offset. User listening must confirm the intended
count interpretation. Rendered signal evidence is reported only at known MIDI
attacks; energy alone is not instrument identification or perceptual precision.

```sh
swiftc scripts/render_drum_guitar.swift -o /tmp/drum-guitar-render
.venv/bin/python scripts/create_reference_drum_sync.py \
  --renderer /tmp/drum-guitar-render
```

Default request: `config/reference_drum_sync.json`.
Default output: `output/phase12_step3_drum_guitar_sync`; existing output refused.
Without a renderer only MIDI and reports are emitted. Native renderer mode is
`guitar`, `drums`, or `band`; a band file contains the original guitar track first
and generated drums second. The original guitar track carries the single common
tempo map. Native render uses **one AVAudioEngine/AVAudioSequencer** and two
samplers (GM31 melodic + default percussion bank), output volume0.5, no timing
shift. Calibration probes establish sample-bank attack thresholds, not pure
hardware latency. No arbitrary ear-based alignment or new sound/model/library.

All six generated stems/bands share the same length, including a stated one-
second tail beyond the eight-bar music end. A common 100ms fade is restricted
to the end of that post-music tail, leaving every music-region sample timing intact. The original reference remains the
unaltered eight-bar excerpt. Common post gain prevents created audio clipping.
Independent native renders need not be byte-exact due to voice phase/state;
MIDI determinism and common-clock event identity are the sync guarantees.

Actual original drum alignment remains unverified without isolated stems.
Original-vs-generated musical quality awaits listening. Stop after C/D audition;
no guitar fixes, A/B work, full song, separation or transcription resumption.
