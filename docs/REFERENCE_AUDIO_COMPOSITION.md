# Reference audio composition prototype (Phase 12 Step 1)

Song-specific tempo, C-grid phase, source path and candidate bars live in
`config/reference_audio_composition.json`, not in the audio analysis core.

This opt-in prototype reads **mix audio**, not guitar or drum transcription.
Phase 10's public `generate()` still accepts its original E-minor riff-style
contract. Phase 11 guitar transcription remains frozen. No default is changed.

```python
from midi_generator.reference_analysis.groove_map import analyze_audio_groove
from midi_generator.modules.guitar.reference_composition import compose_reference_guitar

groove = analyze_audio_groove(
    "data/references/09 The Grey.mp3",
    start_seconds=125.07663157894736, bars=4, bpm=95,
    bar_origin_seconds=1.287157894736842,
)
midi, settings = compose_reference_guitar(groove, pattern="a", seed=42)
# Caller saves to a NEW path; no implicit file output from either function.
```

`analyze_audio_groove` reuses the existing bounded audio loader and optional
installed audio stack. It returns raw spectral/energy observations (`observed`),
role guesses (`inferred`, low instrument confidence), raw/grid times, confidence
and provenance, per-bar accent vectors, rest/fill candidates and similarity.
BPM/grid are caller-supplied; the audition script marks the actual user-approved
95/C context as verified. No low-frequency peak is a verified kick. Unknown
half-time and original section labels remain unknown. The .08-beat mapping limit
is a new **audio abstraction** parameter, separate from the existing .04-beat
MIDI Blueprint tolerance. Neither source audio nor that existing tolerance changes.

`compose_reference_guitar` is a small B-minor-only 4/8-bar prototype. It calls the
existing Phase 10 `render_riff_style` core for motif proposals, then selects
observed accent anchors and makes original harmonic/phrase edits. It does not
feed fake drum MIDI to `api.generate()` or change its harmony support. Empty
accent maps are refused. Saved per-group provenance distinguishes audio-selected
anchors from compositional supplements. An anchor-shift test checks causal
rhythmic dependence on the input map.

Patterns: A selected low-band/major accents and B pedal; B offbeat answers and
B–C–B tension; C repeated rhythmic motif with B–G–A–B roots and longer notes;
D contextual 32nd bursts, stops, restarts and a brief C tension. These are user-
specified/new composition hypotheses, not inferred original harmony or facts
learned from a theory database. Existing rule sources are `docs/RULE_BACKING.md`
and `modules/guitar/riff_styles.py`; no separate formal composition database
was found. Chord labels describe harmonic intent. Actual voicings are single
notes or root+7, with no major/minor third. B natural minor is the tonal center;
C natural is a deliberate borrowed bII in B/D. No articulation keys.

Pitch register is an explicit prototype choice within MIDI 40..76, not a change
to Phase 10 defaults, not a Drop B assumption or sound-library playability
certification. The supplied 4-bar audition actually spans MIDI 43..57, with B
pedal at 47. Tuning is unknown.

```sh
.venv/bin/python scripts/create_reference_audio_compositions.py \
  --output output/phase12_step1_reference_audio_composition \
  --renderer /path/to/existing/macOS/offline_renderer
```

The optional renderer executable contract is `renderer input.mid output.wav
seconds`. The run used the already available macOS AVAudioUnitSampler/system
DLS renderer, not a downloaded synth. Without `--renderer`, the script still
writes guitar MIDIs and the original excerpt, and reports no guitar rendering.
An existing output directory is refused. The script uses 3 bounded late-section
candidates, selects the highest near-grid band-attack fraction, and saves the
selection criterion. It generates only four guitar MIDIs, not a full song.

No The Grey drum-separated audio was found for the delivered run. Files named
`*_with_reference.wav` are the **original full mix plus generated guitar**,
not isolated original drums. Original guitar/bass/synth can clash with new
harmony. Compare solo guitar first; then evaluate timing against the reference.
The same source samples and mix gains are used for all four. Soundfont attack,
release and lack of true palm-mute articulation limit evaluation. Static
statistics never establish musical quality; await listening and stop.
