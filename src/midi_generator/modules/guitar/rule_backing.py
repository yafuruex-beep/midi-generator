"""Phase 7.3 B: one-bar drum blueprint, deterministic steps, independent renderer.

Positions and durations use sixteenth-note steps (one step = .25 quarter beats).
No guitar source, catalog, random state or Reference analysis is consulted.
"""
from dataclasses import dataclass, replace
import hashlib
import math
from pathlib import Path

import mido

from midi_generator.modules.guitar.timeline import read_timeline
from midi_generator.modules.guitar.sync import constant_context, load_sync_config


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")


def _finite(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low < value <= high:
        raise ValueError(f"{label} must be finite and in ({low}, {high}]")


def _cells(values, label):
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a sorted unique tuple of steps")
    for value in values:
        _integer(value, 0, 15, label)
    if tuple(sorted(set(values))) != values:
        raise ValueError(f"{label} must be sorted and unique")


_VOICING_INTERVALS = {'single': (0,), 'power5': (0, 7), 'power8': (0, 7, 12)}


def _voicing_intervals(voicing):
    if not isinstance(voicing, str) or voicing not in _VOICING_INTERVALS:
        raise ValueError('voicing must be single, power5 or power8')
    return _VOICING_INTERVALS[voicing]


def _voiced_notes(note, voicing):
    _integer(note, 0, 127, 'note')
    notes = tuple(note + interval for interval in _voicing_intervals(voicing))
    if notes[-1] > 127:
        raise ValueError(f'voicing {voicing} with root {note} exceeds MIDI note range 0..127')
    return notes


@dataclass(frozen=True)
class RhythmBlueprint:
    kick_steps: tuple[int, ...]
    snare_steps: tuple[int, ...]
    accent_steps: tuple[int, ...]
    strong_steps: tuple[int, ...] = (0, 4, 8, 12)
    steps_per_bar: int = 16
    time_signature: tuple[int, int] = (4, 4)
    warnings: tuple[str, ...] = ()
    source_sha256: str | None = None
    source_bpm: float | None = None
    source_ticks_per_beat: int | None = None
    source_bar_index: int | None = None
    source_bar_start_tick: int | None = None
    source_bar_end_tick: int | None = None

    def __post_init__(self):
        if type(self.steps_per_bar) is not int or self.steps_per_bar != 16 or self.time_signature != (4, 4):
            raise ValueError("Only one 16-step 4/4 bar is supported")
        for label in ('kick_steps', 'snare_steps', 'accent_steps', 'strong_steps'):
            _cells(getattr(self, label), label)
        if not self.kick_steps:
            raise ValueError("Blueprint requires mapped kick steps")


@dataclass(frozen=True)
class BackingRules:
    """Fixed backing rules; duration_steps is in sixteenths (0.25 beats each)."""
    base_velocity: int = 76
    kick_velocity: int = 108
    strong_velocity: int = 96
    accent_velocity: int = 100
    duration_steps: float = .5
    rest_before_snare: bool = True
    voicing: str = "single"
    power_chord_ratio: float | None = None
    rhythm_mode: str = "repeat"

    def __post_init__(self):
        for label in ('base_velocity', 'kick_velocity', 'strong_velocity', 'accent_velocity'):
            _integer(getattr(self, label), 1, 127, label)
        if min(self.kick_velocity, self.strong_velocity, self.accent_velocity) < self.base_velocity:
            raise ValueError("Accent velocities must be at least base_velocity")
        _finite(self.duration_steps, 0, 2, 'duration_steps')
        if self.voicing != "mixed":
            _voicing_intervals(self.voicing)
        if self.power_chord_ratio is not None:
            if (isinstance(self.power_chord_ratio, bool)
                    or not isinstance(self.power_chord_ratio, (int, float))
                    or self.power_chord_ratio not in (.5, .75, 1)):
                raise ValueError('power_chord_ratio must be 0.5, 0.75 or 1.0')
            if self.voicing != 'mixed':
                raise ValueError('power_chord_ratio requires voicing="mixed"')
        if not isinstance(self.rhythm_mode, str) or self.rhythm_mode not in ('repeat', 'phrase4'):
            raise ValueError('rhythm_mode must be repeat or phrase4')
        if type(self.rest_before_snare) is not bool:
            raise ValueError("rest_before_snare must be boolean")


@dataclass(frozen=True)
class BackingStep:
    step: int
    note: int
    duration_steps: float
    velocity: int
    voicing: str = "single"

    @property
    def notes(self):
        """Validated chord pitches, ordered root first."""
        return _voiced_notes(self.note, self.voicing)

    def __post_init__(self):
        _integer(self.step, 0, 15, 'step')
        _voiced_notes(self.note, self.voicing)
        _integer(self.velocity, 1, 127, 'velocity')
        _finite(self.duration_steps, 0, 16 - self.step, 'duration_steps')


def extract_rhythm_blueprint(drum_midi, *, bar_index=None, sync_config_path=None):
    """Read one bar without altering input MIDI.

    Omit bar_index for the original strict one-bar input contract. Explicit
    zero-based bar_index selects a complete [start_tick, end_tick) window from
    a longer input. Note On determines membership, independent of Note Off.
    Tempo/meter and note pairing are validated over the entire source.

    Reuse the existing drum mapping, channel policy and .04-beat default tolerance.
    Check every selected drum onset, including non-kick/snare percussion. Events
    within tolerance map to the nearest cell with a warning; larger errors reject.
    """
    if bar_index is not None and (type(bar_index) is not int or bar_index < 0):
        raise ValueError('bar_index must be a nonnegative integer (zero-based)')
    config = load_sync_config(sync_config_path)
    path = Path(drum_midi)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        data = read_timeline(path, set())
    except Exception as exc:
        raise ValueError(f"Could not read drum MIDI: {exc}") from exc
    constant_context(data, (4, 4))
    if data['pairing']['unmatched_note_offs'] or data['pairing']['inferred_note_ends']:
        raise ValueError("Drum MIDI has unmatched Note On/Off events")
    tpb = data['midi'].ticks_per_beat
    selected_index = 0 if bar_index is None else bar_index
    start_tick = selected_index * 4 * tpb
    end_tick = start_tick + 4 * tpb
    if bar_index is None:
        if abs(data['end_tick'] / tpb - 4) > config['tail_tolerance_beats']:
            raise ValueError("Drum MIDI must contain exactly one complete bar; cropping/looping is unsupported")
    elif start_tick >= data['end_tick']:
        raise ValueError(f'bar_index {bar_index} does not exist in the source MIDI')
    elif end_tick > data['end_tick']:
        raise ValueError(f'bar_index {bar_index} is a final partial bar; a complete four-beat window is required')
    channels = {n['channel'] for n in data['notes']}
    if not channels:
        raise ValueError("Drum MIDI contains no notes")
    warnings = list(data['warnings'])
    if 10 in channels:
        notes = [n for n in data['notes'] if n['channel'] == 10]
        if len(channels) > 1:
            warnings.append('Only channel 10 notes were used from the multi-channel drum reference.')
    elif len(channels) == 1:
        notes = data['notes']
        warnings.append('Single nonstandard drum channel: existing configured drum mapping used without inference.')
    else:
        raise ValueError('Ambiguous multi-channel drum reference without channel 10')
    if bar_index is not None:
        notes = [n for n in notes if start_tick <= n['start_tick'] < end_tick]
    kick, snare, accents = set(), set(), set()
    shifted = 0
    mapped = []
    for note in notes:
        local_tick = note['start_tick'] - start_tick
        beat = local_tick / tpb
        cell = round(beat * 4)
        error = abs(local_tick - cell * tpb / 4) / tpb
        if not 0 <= cell < 16 or not 0 <= beat < 4:
            raise ValueError('Drum onset lies outside the single bar')
        if error > config['onset_tolerance_beats']:
            raise ValueError('Drum onset is off the straight sixteenth grid; swing/quantization is unsupported')
        shifted += error > 0
        if note['note'] in config['kick_notes']:
            kick.add(cell); mapped.append((cell, note['velocity']))
        elif note['note'] in config['snare_notes']:
            snare.add(cell); mapped.append((cell, note['velocity']))
    if not kick:
        raise ValueError('No mapped kick notes; configure kick_notes instead of guessing')
    max_velocity = max(v for _, v in mapped)
    accents.update(s for s, v in mapped if v >= .7 * max_velocity)
    if shifted:
        warnings.append(f'{shifted} drum onset(s) mapped to nearest sixteenth within {config["onset_tolerance_beats"]} quarter-beat tolerance.')
    if hashlib.sha256(path.read_bytes()).hexdigest() != source_hash:
        raise ValueError('Drum MIDI changed during extraction')
    tempos = data['context']['tempo_events']
    tempo = tempos[0]['microseconds_per_beat'] if tempos and tempos[0]['tick'] == 0 else 500000
    return RhythmBlueprint(tuple(sorted(kick)), tuple(sorted(snare)), tuple(sorted(accents)),
                           warnings=tuple(warnings), source_sha256=source_hash,
                           source_bpm=mido.tempo2bpm(tempo), source_ticks_per_beat=tpb,
                           source_bar_index=selected_index, source_bar_start_tick=start_tick,
                           source_bar_end_tick=end_tick)


def _mixed_chord_index(positions, phrase_bar_index):
    """Listening hypothesis: one sparse chord accent per bar, four-bar cycle.

    Structural gaps (> eighth-note onset spacing) get a modest preference, not
    a mandate. Gate-level silence is ignored so changing duration cannot change
    the placement. Phrase start/end get a preference on cycle bars 0/3.
    """
    phase = phrase_bar_index % 4
    if len(positions) == 1:
        return 0 if phase == 0 else None
    target = (0, 8, 4, 15)[phase]
    def rank(index):
        cell = positions[index]
        gap_bonus = 3 if index > 0 and cell - positions[index - 1] > 2 else 0
        boundary_bonus = 4 if (phase == 0 and index == 0) or (phase == 3 and index == len(positions) - 1) else 0
        return (abs(cell - target) - gap_bonus - boundary_bonus, index)
    return min(range(len(positions)), key=rank)


def _ratio_chord_indices(positions, phrase_bar_index, ratio):
    """Round the event count half up per bar, then distribute chord indices.

    Keep the original mixed accent as the anchor, spacing other chords evenly
    by event index. Timing/gates do not participate in this ratio calculation.
    """
    count = len(positions)
    chord_count = math.floor(count * ratio + .5)
    if not chord_count:
        return set()
    anchor = _mixed_chord_index(positions, phrase_bar_index)
    if anchor is None:
        anchor = 0
    return {(anchor + index * count // chord_count) % count for index in range(chord_count)}


def _phrase_positions(positions, kicks, rests, phrase_bar_index):
    """Map new positions to original events, retaining their velocity identity.

    A / A variation / A / ending. Never remove/move kicks or add fills after
    editing. If no safe supplemental event exists, retain the input groove.
    """
    origins = {cell: cell for cell in positions}
    phase = phrase_bar_index % 4
    if phase in (0, 2):
        return origins
    # Only move even-grid supplements; no adjacent sixteenth retriggers added.
    candidates = sorted((s for s in positions if s not in kicks and s % 2 == 0
                         and s >= (8 if phase == 1 else 12)), reverse=True)
    for donor in candidates:
        target = donor + 1 if phase == 1 else donor - 1
        if not 0 <= target < 16 or target in rests:
            continue
        removed = [] if phase == 1 else sorted(
            (s for s in positions if 8 < s < donor and s not in kicks))[-2:]
        remaining = positions - {donor} - set(removed)
        if any(abs(target - s) < 2 for s in remaining):
            continue
        for cell in (*removed, donor):
            del origins[cell]
        origins[target] = donor
        break
    return origins


def _refine_phrase(bars, blueprint, duration_mode, long_note_ratio, drum_mode):
    """Opt-in gate contrast and ending-only backbeat space; keep event identity."""
    result = [list(bar) for bar in bars]
    if duration_mode == 'contrast':
        # Allocate within each four-bar block so exact repetitions stay exact.
        for offset in range(0, len(result), 4):
            block = result[offset:offset + 4]
            event_count = sum(map(len, block))
            budget = min(math.floor(event_count * long_note_ratio + .5), max(0, event_count - 1))
            candidates = []
            for local_bar, bar in enumerate(block):
                for index, step in enumerate(bar):
                    stop = bar[index + 1].step if index + 1 < len(bar) else 16
                    # Leave >= one silent sixteenth before the next attack/bar.
                    # Continuous eighth chugs and the final sixteenth stay short.
                    if step.duration_steps == 1 and stop - step.step >= 3:
                        candidates.append((index != len(bar) - 1, -step.velocity,
                                           offset + local_bar, index))
            for _, _, bar_index, index in sorted(candidates)[:budget]:
                step = result[bar_index][index]
                stop = result[bar_index][index + 1].step if index + 1 < len(result[bar_index]) else 16
                result[bar_index][index] = replace(step, duration_steps=min(2, stop - step.step - 1))
    if drum_mode == 'ending_space':
        for bar_index, bar in enumerate(result):
            if bar_index % 4 != 3:
                continue
            # At most one non-kick supplement just before a sounding backbeat.
            # Keep kicks, simultaneous snare attacks, and the final pickup.
            candidates = [i for i, step in enumerate(bar[:-1])
                          if step.step not in blueprint.kick_steps
                          and step.step + 2 in blueprint.snare_steps
                          and bar[i + 1].step == step.step + 2
                          and step.step + step.duration_steps < bar[i + 1].step]
            if candidates:
                del bar[candidates[-1]]
    return tuple(tuple(bar) for bar in result)


def generate_backing_phrase(blueprint, *, note, rules=None, bars=8,
                            duration_mode="fixed", long_note_ratio=.15,
                            drum_mode="preserve"):
    """Return local bars; optional contrast/ending space never changes defaults.

    Gate contrast needs phrase4 and a one-step (0.25 beat) base gate. The ratio
    is an experimental target, limited by available rest-preserving candidates.
    Ending space removes at most one non-kick pre-snare event per four bars,
    retaining the exact voicing, velocity and gate of every surviving event.
    """
    if type(bars) is not int or bars < 1:
        raise ValueError('bars must be a positive integer')
    if not isinstance(duration_mode, str) or duration_mode not in ('fixed', 'contrast'):
        raise ValueError('duration_mode must be fixed or contrast')
    if not isinstance(drum_mode, str) or drum_mode not in ('preserve', 'ending_space'):
        raise ValueError('drum_mode must be preserve or ending_space')
    if (isinstance(long_note_ratio, bool) or not isinstance(long_note_ratio, (int, float))
            or not math.isfinite(long_note_ratio) or not 0 <= long_note_ratio <= 1):
        raise ValueError('long_note_ratio must be finite and in 0..1')
    settings = BackingRules() if rules is None else rules
    if not isinstance(settings, BackingRules):
        raise ValueError('Expected BackingRules')
    if (duration_mode != 'fixed' or drum_mode != 'preserve') and settings.rhythm_mode != 'phrase4':
        raise ValueError('Phrase refinement requires rhythm_mode="phrase4"')
    if duration_mode == 'contrast' and settings.duration_steps != 1:
        raise ValueError('contrast requires duration_steps=1 (0.25 beats)')
    result = tuple(generate_backing_steps(blueprint, note=note, rules=settings,
                                         phrase_bar_index=bar) for bar in range(bars))
    if duration_mode == 'fixed' and drum_mode == 'preserve':
        return result
    return _refine_phrase(result, blueprint, duration_mode, long_note_ratio, drum_mode)


def generate_backing_steps(blueprint, *, note, rules=None, phrase_bar_index=0):
    """Keep kicks; fill wide gaps on the eighth grid, avoiding adjacent kicks.

    Fill only after the first kick, through the bar end. A gap must span at least
    four sixteenths. Supplements stay >= two steps from any kick. The cell before
    a snare is silent unless occupied by a kick. Strong beats are metric positions,
    accents are mapped kick/snare velocities >= 70% of their maximum, not inferred
    harmony or musical quality.

    Select duration via rules=BackingRules(duration_steps=...): .5, 1, 2
    steps mean .125, .25, .5 quarter beats. The default remains .5 step.
    Requested duration is capped at the next onset and bar end; only Note Off
    changes, not onset, pitch, velocity or supplement selection.
    """
    if not isinstance(blueprint, RhythmBlueprint):
        raise ValueError('Expected RhythmBlueprint')
    _integer(note, 0, 127, 'note')
    rules = BackingRules() if rules is None else rules
    if not isinstance(rules, BackingRules):
        raise ValueError('Expected BackingRules')
    if type(phrase_bar_index) is not int or phrase_bar_index < 0:
        raise ValueError('phrase_bar_index must be a nonnegative integer')
    _voiced_notes(note, 'power5' if rules.voicing == 'mixed' else rules.voicing)
    kicks = set(blueprint.kick_steps)
    positions = set(kicks)
    boundaries = (*blueprint.kick_steps, 16)
    rests = {s - 1 for s in blueprint.snare_steps if s > 0} if rules.rest_before_snare else set()
    for left, right in zip(boundaries, boundaries[1:]):
        if right - left < 4:
            continue
        for cell in range(left + 1, right):
            if cell % 2 == 0 and cell not in rests and min(abs(cell - k) for k in kicks) >= 2:
                positions.add(cell)
    origins = (_phrase_positions(positions, kicks, rests, phrase_bar_index)
               if rules.rhythm_mode == 'phrase4' else {s: s for s in positions})
    ordered = sorted(origins)
    if rules.voicing == 'mixed':
        if rules.power_chord_ratio is None:
            chord_indices = {_mixed_chord_index(ordered, phrase_bar_index)}
        else:
            chord_indices = _ratio_chord_indices(ordered, phrase_bar_index, rules.power_chord_ratio)
    else:
        chord_indices = set()
    result = []
    for i, cell in enumerate(ordered):
        source_cell = origins[cell]
        velocity = rules.base_velocity
        if source_cell in blueprint.strong_steps:
            velocity = max(velocity, rules.strong_velocity)
        if source_cell in blueprint.accent_steps:
            velocity = max(velocity, rules.accent_velocity)
        if source_cell in kicks:
            velocity = max(velocity, rules.kick_velocity)
        stop = ordered[i + 1] if i + 1 < len(ordered) else 16
        voicing = ('power5' if i in chord_indices else 'single') if rules.voicing == 'mixed' else rules.voicing
        result.append(BackingStep(cell, note, min(rules.duration_steps, stop - cell), velocity, voicing))
    return tuple(result)


def render_backing_midi(steps, *, export_bpm, ticks_per_beat=480, midi_channel=1, program=None, output_path=None):
    """Render intermediate steps to a new MIDI; refuse overlap or overwriting.

    Channel is human-numbered 1..16 (10 is reserved for drums). The renderer
    accepts ordered nonoverlapping BackingStep chord events, independent of rules.
    Optional program is human-numbered 1..128; 31 selects GM Distortion Guitar.
    Returns mido.MidiFile whether or not a new output path was supplied.
    """
    _finite(export_bpm, 0, 60_000_000, 'export_bpm')
    tempo = mido.bpm2tempo(export_bpm)
    if not 1 <= tempo <= 0xFFFFFF:
        raise ValueError('Export BPM cannot be represented by a MIDI tempo event')
    _integer(ticks_per_beat, 1, 32767, 'ticks_per_beat')
    if ticks_per_beat % 4:
        raise ValueError('ticks_per_beat must represent an exact sixteenth grid')
    _integer(midi_channel, 1, 16, 'midi_channel')
    if midi_channel == 10:
        raise ValueError('Channel 10 is reserved for drums')
    steps = tuple(steps)
    if not steps or any(not isinstance(s, BackingStep) for s in steps):
        raise ValueError('Expected a nonempty sequence of BackingStep')
    if len({s.note for s in steps}) != 1:
        raise ValueError('Step 1 supports one fixed MIDI pitch only')
    midi = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack([
        mido.MetaMessage('track_name', name='Rule Backing Guitar'),
        mido.MetaMessage('set_tempo', tempo=tempo),
        mido.MetaMessage('time_signature', numerator=4, denominator=4),
    ])
    if program is not None:
        _integer(program, 1, 128, 'program')
        track.append(mido.Message('program_change', program=program - 1, channel=midi_channel - 1))
    previous = 0
    for step in steps:
        start = step.step * ticks_per_beat // 4
        end = start + step.duration_steps * ticks_per_beat / 4
        if not float(end).is_integer() or end <= start:
            raise ValueError('Duration cannot be represented exactly at ticks_per_beat')
        end = int(end)
        if start < previous:
            raise ValueError('Steps must be ordered, monophonic and nonoverlapping')
        for index, note in enumerate(step.notes):
            track.append(mido.Message('note_on', note=note, velocity=step.velocity,
                                      channel=midi_channel - 1, time=start - previous if index == 0 else 0))
        for index, note in enumerate(step.notes):
            track.append(mido.Message('note_off', note=note, velocity=0,
                                      channel=midi_channel - 1, time=end - start if index == 0 else 0))
        previous = end
    track.append(mido.MetaMessage('end_of_track', time=4 * ticks_per_beat - previous))
    midi.tracks.append(track)
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            midi.save(file=stream)
    return midi
