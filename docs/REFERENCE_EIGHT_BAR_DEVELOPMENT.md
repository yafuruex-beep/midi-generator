# Phase 12 Step 2: explicit C/D eight-bar development

This independent module develops the **saved, generated** Step 1 motifs. It
never transcribes guitar, modifies Phase 10 defaults, or changes any Step 1 file.
A/B remain untouched.

```python
from midi_generator.modules.guitar.reference_development import (
    extend_reference_groove, develop_reference_eight_bars,
)
# Load the saved Step 1 groove_map.json and generation_settings.json.
groove8 = extend_reference_groove(first_map, original_audio_path)
midi, metadata = develop_reference_eight_bars(groove8, settings, pattern="c")
```

The first four map rows and original observations are retained exactly. The
existing audio analyzer processes only the additional four bars. Thresholds and
normalization are calculated per block; both method records are preserved, and
new observation IDs are namespaced. Previous bar-4 phrase-boundary information
is retained as a four-bar boundary, not misrepresented as the end of the song.
Tempo, origin and source hashes must match accepted input. No audio timing shift.

C uses Bm/G/A/Bm in two-bar blocks. It reuses the two-bar Step 1 motif, omits one
weak opening-bar reply to allow an actual two-beat power-chord hold, and mixes
half-, one- and two-beat durations. Single-note thirds/fifths provide diatonic
responses. D preserves the existing 32nd chug gates and pedal, with B5 / G/B /
A5/B(no third) / B5 return in successive blocks. Only restart/tail accents get
upper-voice colors. Two short C-natural tensions resolve back to B; one bar-7
restart is omitted to make a final break. Long notes are not imposed on chugs.
Register stays inside explicit MIDI 40..76; actual C/D ranges are in metadata.
Tuning and sound-library technique remain unknown; no Drop B or keyswitch claim.

```sh
.venv/bin/python scripts/create_eight_bar_reference_compositions.py \
  --renderer /path/to/existing/macOS/offline_renderer
```

The config is `config/reference_eight_bar_development.json`; output defaults to
`output/phase12_step2_eight_bar_composition`. Existing directories are refused.
The renderer is optional and uses the existing input.mid/output.wav/seconds
contract. MIDI has local beat zero mapped to the original section start. Note
Off precedes subsequent Note On, and last releases leave a short native-synth
tail inside the eight-bar duration. No new dependencies or source models.

No The Grey drum-separated audio exists in the inspected project inputs and
outputs. Deliverable `with_reference` files include all original instruments;
**drum-only sync evaluation remains unmet**. No `with_drums` files are created.
The script deliberately does not claim or auto-install drum separation. A
future drum stem would need trusted provenance and a measured or documented
source offset before use; that work is outside this audition.

The old separation diagnostic report records possible isolated runtimes and
model runners. Current runtime inspection found no torch/demucs/onnxruntime/mlx.
A separate installation/model/license/runtime decision is required before any
new separation run. No run speed, compatibility or quality is promised.

Metrics distinguish groups from notes, motif onset repetition from harmony,
MIDI duration from soundfont release, and nonzero late RMS from listening
quality. End after the two auditions and regression checks; await user review.
