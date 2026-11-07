# Reference MIDI analysis

Select guitar and drum tracks explicitly using zero-based indices. Track roles
are supplied by the transcriber. All selected notes, including low pitches, are retained.

~~~sh
.venv/bin/midi-generator reference analyze-midi transcription.mid \
  --guitar-tracks 1 2 --drum-tracks 3 \
  --sections sections.json \
  --output output/phase11_step1_reference_analysis
~~~

The destination must be new. The CLI saves analysis.json and report.md.
Repeated analysis of the same input and settings is deterministic.

~~~python
from midi_generator.api import analyze_reference_midi, save_reference_midi_analysis
result = analyze_reference_midi("transcription.mid", guitar_tracks=[1, 2], drum_tracks=[3])
save_reference_midi_analysis(result, "output/new_reference_report")
~~~

Section definitions use one-based inclusive bar numbers. Gaps are allowed.
Overlapping, reversed and out-of-range definitions are rejected.

~~~json
{
  "metadata": {
    "title": {"status": "user_verified", "value": "User supplied title",
              "evidence": "Entered by the transcriber"}
  },
  "sections": [
    {"label": "Intro", "start_bar": 1, "end_bar": 4,
     "status": "user_verified", "evidence": "Checked by the user"}
  ]
}
~~~

User verification applies only to the supplied annotation. Without definitions,
explicit MIDI marker texts are observed labels. Without markers, labels stay empty.
Conflicting marker labels at one tick are retained together.

JSON separates song_metadata, sections, rhythm_features, pitch_features, motifs
and analysis_metadata. Claims have status and value. Inferred claims also have
method, thresholds and evidence. Recorded tempo/meter events are observed.
Missing tick-zero context uses explicitly identified MIDI defaults.

Meter changes create new bar segments. Mid-bar changes and final partial bars
remain explicit. All beat values use quarter notes. Track delta times are
converted to absolute ticks using the existing shared MIDI timeline parser.

Exact onsets, gates and rest unions are observed. Lowest-pitch root interpretation,
power-chord identity, accent candidates, drum mapping and motif identity are inferred.
Kick/snare numbers and .04-beat tolerance reuse the existing sync config loader.

Motifs compare complete single bars and separate onset/gate signatures, note
sequences and velocity. Exact, transposed, rhythm-only and pitch-sequence matches
are distinct candidates. Similar onset sets use Jaccard >= .8. Comparison is
limited to the first 128 complete bars with a warning. Multi-bar phrase identity
is outside this first version.

Accent patterns require all velocity-accent boundaries including closure.
Velocity >= 100 defines this layer. Pattern intervals use sixteenths. Candidate
periods do not alter actual meter. Missing detections are threshold-dependent.

Format-2/SMPTE MIDI, ambiguous same-channel/pitch overlaps, broken note pairing,
zero gates and conflicting simultaneous tempo/meter events are rejected.
Instrument articulation, harmonic roots, audio transcription and musical quality
require additional source information or user confirmation.
