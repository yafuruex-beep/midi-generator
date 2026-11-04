"""Small deterministic edits to an existing eight-bar BackingStep motif.

This opt-in postprocessor does not alter phrase generation or default settings.
It only removes weak interior attacks or shortens gates; no new onsets/pitches.
"""
from dataclasses import replace

from .rule_backing import BackingStep


def _weak_response(bar, *, after):
    """Remove one weak interior event beside a second-half major accent."""
    maximum = max(s.velocity for s in bar)
    anchors = [s for s in bar if s.step >= 8 and s.velocity >= .9 * maximum]
    if not anchors:
        return bar
    anchor = anchors[0]
    choices = [i for i, s in enumerate(bar) if 0 < i < len(bar) - 1
               and s.velocity < .9 * maximum
               and (s.step > anchor.step if after else s.step < anchor.step)]
    if not choices:
        return bar
    index = choices[0] if after else choices[-1]
    return bar[:index] + bar[index + 1:]


def develop_backing_motif(bars, *, mode='current'):
    """Return current/repetition/call_response candidates without mutating input.

    Input is exactly eight nonempty, ordered, nonoverlapping local-bar step
    tuples. It is normally produced by generate_backing_phrase(..., bars=8).
    Primary accents use >=90% of the local maximum velocity, not inferred drums.
    All retained events keep their pitches, voicing, onset and velocity.

    repetition: shorten one pre-pause long gate in bars 4/8 to one step.
    call_response: omit one weak pre-accent attack in bar 3 and one weak
    post-accent attack in bar 7; shorten bar 8's final long gate if a tail rest
    already exists. Missing safe candidates leave that bar unchanged.
    """
    if not isinstance(mode, str) or mode not in ('current', 'repetition', 'call_response'):
        raise ValueError('mode must be current, repetition or call_response')
    try:
        source = tuple(tuple(bar) for bar in bars)
    except TypeError as exc:
        raise ValueError('Expected eight nonempty BackingStep bars') from exc
    if len(source) != 8 or any(not bar for bar in source):
        raise ValueError('Expected exactly eight nonempty BackingStep bars')
    for bar in source:
        previous = 0
        for step in bar:
            if not isinstance(step, BackingStep):
                raise ValueError('Expected BackingStep events')
            if step.step < previous:
                raise ValueError('Motif bars must be ordered and nonoverlapping')
            previous = step.step + step.duration_steps
    if mode == 'current':
        return source
    result = list(source)
    if mode == 'repetition':
        for index in (3, 7):
            bar = result[index]
            choices = [i for i, s in enumerate(bar[:-1]) if s.duration_steps > 1
                       and bar[i + 1].step - s.step >= 4]
            if choices:
                i = choices[-1]
                result[index] = bar[:i] + (replace(bar[i], duration_steps=1),) + bar[i + 1:]
    else:
        result[2] = _weak_response(result[2], after=False)
        result[6] = _weak_response(result[6], after=True)
        bar = result[7]; last = bar[-1]
        if last.duration_steps > 1 and last.step + last.duration_steps < 16:
            result[7] = bar[:-1] + (replace(last, duration_steps=1),)
    return tuple(result)
