"""Opt-in generated backing riffs on a 32nd-note onset grid."""
from dataclasses import dataclass, replace
from random import Random

import mido


RIFF_STYLES = (
    "chord_driven", "stop_and_go", "tight_groove", "stutter_breakdown", "groove_burst",
)
_TICKS_PER_BEAT = 480
_CELL = 60
_BAR_CELLS = 32
# Each row is a one-bar rhythm cell. The four roles are stated twice per phrase.
_CHORD_CELLS = ((0, 12, 20, 28), (0, 10, 16, 24), (0, 8, 18, 26), (0, 12, 24))
_TIGHT_CELLS = ((0, 4, 6, 12, 14, 20, 24, 28), (0, 6, 8, 12, 18, 20, 26, 30),
                (0, 4, 10, 12, 18, 22, 24, 28), (0, 4, 6, 14, 16, 24))


@dataclass(frozen=True)
class _Attack:
    cell: int
    gate: int
    velocity: int
    root: int = 52
    chord: bool = True


def _chord(role, variant, accent):
    cells = list(_CHORD_CELLS[role])
    cells[1] += 2 * (variant - 1)
    return [_Attack(cell, 480 if cell % 8 == 0 else (270, 300, 330)[variant],
                    110 if i == accent else 94,
                    55 if role in (1, 2) and i in (1, 2) else 52,
                    chord=i != len(cells)-1 or role == 3)
            for i, cell in enumerate(cells)]


def _stop(role, variant, accent, kicks):
    early = [cell for cell in kicks if cell <= 12]
    late = [cell for cell in kicks if 22 <= cell <= 30]
    # Choose among the nearby kick anchors and keep the center of the bar empty.
    early_pool = early or [0, 4, 8]
    late_pool = late or [24, 26, 28]
    first = early_pool[variant % len(early_pool)]
    remaining = [cell for cell in early_pool if cell != first]
    extra = remaining[accent % len(remaining):][:1] if remaining else []
    restart = late_pool[(variant + accent) % len(late_pool)]
    cells = sorted([first, *extra, restart])
    return [_Attack(cell, (90, 120, 150)[variant] if cell < restart else (240, 300, 360)[variant],
                    118 if i == 0 or cell == restart else 88,
                    55 if role == 2 and cell == restart else 52)
            for i, cell in enumerate(cells)]


def _tight(role, variant, accent, ending):
    cells = list(_TIGHT_CELLS[role])
    cells[3] += 2 * (variant - 1)
    cells = sorted(set(cells))
    result = []
    for i, cell in enumerate(cells):
        strong = i == accent or i == 0 or (i + variant) % 4 == 0
        last = i == len(cells)-1
        gate = (180, 240, 240)[variant] if i == 0 or last else (60 if (i+variant)%3==0 else 120)
        if ending and last:
            gate = 240
        result.append(_Attack(cell, gate, 108 if strong else 82,
                              55 if role in (1, 2) and i >= 5 else 52,
                              chord=strong or i % 3 != 1))
    return result


def _stutter(role, variant, accent):
    first = (0, 2, 0, 2)[role]
    count = 2 + (variant + role) % 3
    restart = 12 + 2 * variant
    tail = 24 + 2 * (accent % 2)
    return ([_Attack(first+i, 45 if i<count-1 else (45, 50, 55)[variant],
                     110 if i==0 else 89, chord=i==0)
             for i in range(count)]
            + [_Attack(restart, (240, 270, 300)[variant], 118),
               _Attack(tail, 270, 104 if accent == 1 else 113, 55 if role == 2 else 52)])


def _burst(attacks, variation):
    # Subdivide a sustained group in place. Keep the next existing onset and
    # leave at least a full sixteenth between the last added and next attack.
    eligible = []
    for i, attack in enumerate(attacks):
        stop = attacks[i+1].cell if i+1 < len(attacks) else _BAR_CELLS
        span = min(attack.gate, (stop-attack.cell)*_CELL)
        count = min(4, span//_CELL, stop-attack.cell-1)
        if count >= 2 and span >= 180:
            eligible.append((i, count, span))
    if not eligible:
        return attacks
    index, maximum, span = eligible[variation % len(eligible)]
    count = min(maximum, 2 + variation % 3)
    attack = attacks[index]
    subdivided = [replace(attack, cell=attack.cell+i, gate=45, velocity=92, chord=False)
                  for i in range(count)]
    subdivided[0] = replace(attack, gate=45)
    subdivided[-1] = replace(subdivided[-1], gate=span-(count-1)*_CELL)
    return attacks[:index]+subdivided+attacks[index+1:]


def render_riff_style(style, *, tempo, bars, channel, seed, drum):
    """Render an E-minor backing phrase; kick positions are from drum sync analysis."""
    rng = Random(0 if seed is None else seed)
    variations = [(rng.randrange(3), rng.randrange(1, 3)) for _ in range(4)]
    burst_role = rng.randrange(2)
    burst_variation = rng.randrange(6)
    tempo_event = mido.bpm2tempo(tempo)
    if not 1 <= tempo_event <= 0xFFFFFF:
        raise ValueError("Requested tempo cannot be represented by a Standard MIDI tempo event")
    track = mido.MidiTrack([
        mido.MetaMessage("track_name", name=f"Backing Guitar ({style})"),
        mido.MetaMessage("set_tempo", tempo=tempo_event),
        mido.MetaMessage("time_signature", numerator=4, denominator=4),
        mido.Message("program_change", channel=channel-1, program=30),
    ])
    previous = 0
    for bar in range(bars):
        role = bar % 4
        variant, accent = variations[role]
        ending = bar % 8 == 7 or bar == bars-1
        if ending and role == 3:
            variant = (variant+1) % 3
        if style == "chord_driven":
            attacks = _chord(role, variant, accent)
        elif style == "stop_and_go":
            kicks = sorted({step*2 for step in drum["bar_patterns"][bar]["kick_steps"]})
            attacks = _stop(role, variant, accent, kicks)
        elif style == "stutter_breakdown":
            attacks = _stutter(role, variant, accent)
        else:
            attacks = _tight(role, variant, accent, ending)
            if style == "groove_burst" and (role == burst_role or role == 2 or bars == 1):
                attacks = _burst(attacks, burst_variation+role)
        for i, attack in enumerate(attacks):
            stop = attacks[i+1].cell if i+1<len(attacks) else _BAR_CELLS
            start = (bar*_BAR_CELLS+attack.cell)*_CELL
            end = start + min(attack.gate, (stop-attack.cell)*_CELL)
            notes = (attack.root, attack.root+7) if attack.chord else (attack.root,)
            for note in notes:
                track.append(mido.Message("note_on", channel=channel-1, note=note,
                                          velocity=attack.velocity, time=start-previous))
                previous = start
            for note in notes:
                track.append(mido.Message("note_off", channel=channel-1, note=note,
                                          velocity=0, time=end-previous))
                previous = end
    track.append(mido.MetaMessage("end_of_track", time=bars*4*_TICKS_PER_BEAT-previous))
    midi = mido.MidiFile(type=0, ticks_per_beat=_TICKS_PER_BEAT)
    midi.tracks.append(track)
    return midi
