"""Strict monophonic bass derived from an existing guitar MIDI, without new rhythms."""
from collections import defaultdict
from dataclasses import dataclass
import hashlib
from pathlib import Path

import mido

from midi_generator.core.midi_io import load_midi, save_midi
from midi_generator.core.models import GenerationRequest, GenerationResult


@dataclass(frozen=True)
class RootEvent:
    start_tick: int
    end_tick: int
    note: int
    velocity: int


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in {low}..{high}')


def _read_roots(source):
    try:
        midi = load_midi(source)
    except (OSError, ValueError, EOFError, KeyError, TypeError) as exc:
        raise ValueError(f'Could not read guitar MIDI: {exc}') from exc
    if (midi.type not in (0, 1) or midi.ticks_per_beat <= 0 or not midi.tracks
            or (midi.type == 0 and len(midi.tracks) != 1)):
        raise ValueError('Guitar MIDI requires format 0/1 and positive ticks_per_beat')
    events = defaultdict(list)
    tempos, meters, channels, ends = [], [], set(), []
    for track in midi.tracks:
        tick = 0
        for message in track:
            _integer(message.time, 0, 0x0FFFFFFF, 'MIDI delta time')
            tick += message.time
            if message.type == 'set_tempo':
                tempos.append((tick, message.tempo))
            elif message.type == 'time_signature':
                meters.append((tick, (message.numerator, message.denominator)))
            elif message.type in ('note_on', 'note_off'):
                channels.add(message.channel)
                events[tick].append(message)
            elif not message.is_meta and message.type != 'program_change':
                raise ValueError(f'Unsupported guitar performance message: {message.type}')
        ends.append(tick)
    if len(channels) != 1 or 9 in channels:
        raise ValueError('Expected one non-drum guitar note channel')
    if not tempos or not any(t == 0 for t, _ in tempos) or len({v for _, v in tempos}) != 1 or tempos[0][1] <= 0:
        raise ValueError('Guitar MIDI needs explicit constant tempo from tick 0')
    if not meters or not any(t == 0 for t, _ in meters) or {v for _, v in meters} != {(4, 4)}:
        raise ValueError('Guitar MIDI needs explicit constant 4/4 from tick 0')
    pending = {}
    notes = []
    for tick, group in sorted(events.items()):
        # Close every existing voice before any same-tick retrigger.
        for message in group:
            if message.type == 'note_off' or message.velocity == 0:
                key = (message.channel, message.note)
                if key not in pending:
                    raise ValueError('Unmatched guitar Note Off')
                start, velocity = pending.pop(key)
                if tick <= start:
                    raise ValueError('Guitar notes must have positive duration')
                notes.append((start, tick, message.note, velocity))
        for message in group:
            if message.type == 'note_on' and message.velocity > 0:
                key = (message.channel, message.note)
                if key in pending:
                    raise ValueError('Overlapping duplicate guitar note')
                pending[key] = (tick, message.velocity)
    if pending:
        raise ValueError('Missing guitar Note Off')
    groups = defaultdict(list)
    for note in notes:
        groups[note[0]].append(note)
    if not groups:
        raise ValueError('Guitar MIDI contains no notes')
    roots = []
    previous_end = 0
    for start, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda n: n[2])
        pitches = [n[2] for n in ordered]
        if len(pitches) not in (1, 2) or (len(pitches) == 2 and pitches[1] - pitches[0] != 7):
            raise ValueError('Only single notes or root+7 two-note power chords are supported')
        if len({(n[1], n[3]) for n in ordered}) != 1:
            raise ValueError('Chord tones must share Note Off tick and velocity')
        if start < previous_end:
            raise ValueError('Guitar attack groups must not overlap')
        root = ordered[0]
        roots.append(RootEvent(start, root[1], root[2], root[3]))
        previous_end = root[1]
    return midi, tuple(roots), max(ends), tempos[0][1]


def generate_follow_bass(request: GenerationRequest, *, output_path=None, project_root='.'):
    """GenerationRequest/Result interface shared by the Python API and CLI.

    options requires guitar_midi, transpose_semitones and program (human 1..128).
    Export keeps every root event's ticks and velocity; output channel is fixed 2.
    Source Program Changes are discarded. No waveform/audio range is inferred.
    """
    if request.instrument.lower() != 'bass':
        raise ValueError('Expected instrument="bass"')
    allowed = {'guitar_midi', 'transpose_semitones', 'program', 'midi_channel'}
    unknown = set(request.options) - allowed
    if unknown:
        raise ValueError(f'Unsupported bass options: {", ".join(sorted(unknown))}')
    missing = allowed - {'midi_channel'} - set(request.options)
    if missing:
        raise ValueError(f'Missing bass options: {", ".join(sorted(missing))}')
    if request.key is not None or request.chords or request.groove:
        raise ValueError('Bass follow mode does not support key/chord/groove generation')
    options = request.options
    if not isinstance(options['guitar_midi'], str) or not options['guitar_midi'].strip():
        raise ValueError('guitar_midi must be a nonempty path string')
    shift, program = options['transpose_semitones'], options['program']
    _integer(shift, -127, 127, 'transpose_semitones')
    _integer(program, 1, 128, 'program')
    if type(options.get('midi_channel', 2)) is not int or options.get('midi_channel', 2) != 2:
        raise ValueError('Bass follow output midi_channel must be 2')
    if output_path is None:
        raise ValueError('Bass follow requires an explicit output_path')
    root = Path(project_root).resolve()
    source = Path(options['guitar_midi'])
    source = source if source.is_absolute() else root / source
    destination = Path(output_path)
    destination = destination if destination.is_absolute() else root / destination
    if source.resolve() == destination.resolve():
        raise ValueError('Source and destination must be different paths')
    try:
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f'Could not read guitar MIDI: {exc}') from exc
    midi, events, end_tick, tempo = _read_roots(source)
    if tuple(request.time_signature) != (4, 4):
        raise ValueError('Bass follow requires 4/4 without meter conversion')
    if tempo != mido.bpm2tempo(request.tempo):
        raise ValueError('Request tempo must match guitar MIDI; no tempo conversion')
    if end_tick != request.bars * 4 * midi.ticks_per_beat:
        raise ValueError('Request bars must match exact guitar MIDI length; no looping/trimming')
    for event in events:
        _integer(event.note + shift, 0, 127, 'transposed MIDI note')
    bass = mido.MidiFile(type=0, ticks_per_beat=midi.ticks_per_beat)
    track = mido.MidiTrack([
        mido.MetaMessage('track_name', name='Bass Guitar Follow'),
        mido.MetaMessage('set_tempo', tempo=tempo),
        mido.MetaMessage('time_signature', numerator=4, denominator=4),
        mido.Message('program_change', channel=1, program=program - 1),
    ])
    previous = 0
    for event in events:
        track.append(mido.Message('note_on', note=event.note + shift, velocity=event.velocity,
                                  channel=1, time=event.start_tick - previous))
        track.append(mido.Message('note_off', note=event.note + shift, velocity=0,
                                  channel=1, time=event.end_tick - event.start_tick))
        previous = event.end_tick
    track.append(mido.MetaMessage('end_of_track', time=end_tick - previous))
    bass.tracks.append(track)
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise ValueError('Guitar MIDI changed during generation')
    saved = save_midi(bass, destination)
    metadata = {'mode': 'guitar_follow', 'source_midi': str(source), 'source_sha256': source_hash,
                'transpose_semitones': shift, 'program': program, 'midi_channel': 2,
                'attack_events': len(events), 'ticks_per_beat': midi.ticks_per_beat, 'end_tick': end_tick,
                'output_notes': sorted({e.note + shift for e in events}),
                'provenance': 'Derived from supplied guitar MIDI. Original usage/license notes remain with source.',
                'audio_playable_range_verified': False}
    return GenerationResult(output_files=[saved], metadata=metadata)
