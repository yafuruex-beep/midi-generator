# Phase 9 Step 11: optional Intro / Chorus audition

This source-hash-locked experiment concatenates the chosen eight-bar Intro and Chorus without changing generator defaults. A preserves all source channel events. B changes Chorus guitar pitches and follows them in bass, retaining the original independent bass gates. C makes only the documented local boundary articulation, accent and crash-candidate edits.

From the project root:

```sh
.venv/bin/python scripts/create_transition_auditions.py \
  examples/requests/intro_chorus_transition.json \
  output/phase9_step11_intro_chorus_transition
```

The destination must not exist. To reproduce, choose a different new directory. Twelve MIDI files, source hashes, exact B/C channel-event differences, metrics and listening notes are saved. Stateful controllers, unexpected programs/channels, nonconstant tempo/meter, invalid notes, source hash mismatches and incomplete motifs are rejected. Motif correspondence uses local tick positions, not event indices.

105 BPM / E minor / roots 52,54,55,57,59 / guitar 52–66 / bass 28–35 / maximum adjacent root jump 4 semitones are inherited audition constraints, not verified sound-library playability. Note49 is a GM-map crash candidate; the actual drum instrument mapping requires listening. No articulation keys, dependencies, catalog changes or automatic adoption.
