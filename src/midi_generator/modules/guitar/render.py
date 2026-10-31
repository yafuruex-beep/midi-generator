"""Phrase-preserving guitar looping/trimming without pitch or onset editing."""
from collections import defaultdict, deque
import math

import mido


def source_length_bars(data, signature, tolerance):
    tpb = data["midi"].ticks_per_beat
    per_bar = signature[0] * 4 / signature[1]
    beats = data["end_tick"] / tpb
    if beats <= 0 or not data["notes"]:
        raise ValueError("Guitar source has no playable notes/duration")
    nearest = round(beats / per_bar)
    if nearest > 0 and abs(beats - nearest * per_bar) <= tolerance and not any(n["start_tick"] / tpb >= nearest * per_bar for n in data["notes"]):
        return nearest
    return max(1, math.ceil(beats / per_bar))


def phrase_fit(source_bars, target_bars, period=None):
    if source_bars == target_bars:
        return 1.0, "exact_length", False
    if source_bars < target_bars and target_bars % source_bars == 0:
        return .9, "whole_source_repetition", False
    if period and target_bars % period == 0 and source_bars % period == 0:
        return .7, "analyzed_period_boundary", False
    return .25, "partial_phrase_boundary", True


def render_backing(data, request, source_bars, channel):
    source = data["midi"]
    per_bar = source.ticks_per_beat * request.time_signature[0] * 4 / request.time_signature[1]
    if not per_bar.is_integer():
        raise ValueError("Requested meter cannot be represented at source ticks_per_beat")
    per_bar = int(per_bar)
    loop_ticks, total_ticks = source_bars * per_bar, request.bars * per_bar
    tempo = mido.bpm2tempo(request.tempo)
    if not 1 <= tempo <= 0xFFFFFF:
        raise ValueError("Target tempo cannot be represented by a MIDI tempo event")
    if len(data["channels"]) > 1:
        raise ValueError("Multi-channel guitar source cannot be collapsed safely; prepare a verified single-channel part")
    if data["pairing"]["unmatched_note_offs"] or data["pairing"]["inferred_note_ends"]:
        raise ValueError("Guitar source has unmatched Note On/Off events; repair the source separately before generation")
    auxiliary = []
    attacks = defaultdict(deque)
    releases = defaultdict(deque)
    note_tracks = set()
    for index, track in enumerate(source.tracks):
        tick = 0
        for order, message in enumerate(track):
            tick += message.time
            if message.type == "note_on" and message.velocity > 0:
                note_tracks.add(index)
                attacks[(tick, message.channel + 1, message.note, index)].append((order, message))
            elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
                releases[(tick, message.channel + 1, message.note)].append((index, order, message))
            elif message.type not in {"end_of_track", "track_name", "set_tempo", "time_signature"}:
                auxiliary.append((tick, index, order, message))
    intervals = []
    for note in data["notes"]:
        attack_order, attack = attacks[(note["start_tick"], note["channel"], note["note"], note["track_index"])].popleft()
        release_track, release_order, release = releases[(note["end_tick"], note["channel"], note["note"])].popleft()
        intervals.append((note, attack_order, attack, release_track, release_order, release))
    events = [[] for _ in source.tracks]
    serial = closed = 0
    for repetition in range(math.ceil(total_ticks / loop_ticks)):
        offset = repetition * loop_ticks
        stop = min(loop_ticks, total_ticks - offset)
        for tick, index, order, message in auxiliary:
            if tick >= stop:
                continue
            copy = message.copy(channel=channel - 1) if hasattr(message, "channel") else message.copy()
            events[index].append((offset + tick, (repetition, order), serial, copy)); serial += 1
        for note, attack_order, attack, release_track, release_order, release in intervals:
            start = note["start_tick"]
            if start >= stop:
                continue
            end = min(note["end_tick"], stop)
            events[note["track_index"]].append((offset + start, (repetition, attack_order), serial,
                                               attack.copy(channel=channel - 1)))
            serial += 1
            release_priority = max(release_order, attack_order + .5) if end == start and release_track == note["track_index"] else release_order
            events[release_track].append((offset + end, (repetition, release_priority), serial, release.copy(channel=channel - 1)))
            serial += 1
            if end < note["end_tick"]:
                closed += 1
    generated = mido.MidiFile(type=0 if len(source.tracks) == 1 else 1, ticks_per_beat=source.ticks_per_beat)
    for index, track_events in enumerate(events):
        name = "Guitar Backing" if index in note_tracks else source.tracks[index].name
        track = mido.MidiTrack()
        if name:
            track.append(mido.MetaMessage("track_name", name=name))
        if index == 0:
            track.extend([mido.MetaMessage("set_tempo", tempo=tempo),
                          mido.MetaMessage("time_signature", numerator=request.time_signature[0], denominator=request.time_signature[1])])
        previous = 0
        for tick, _, _, message in sorted(track_events, key=lambda e: (e[0], e[1], e[2])):
            track.append(message.copy(time=tick - previous)); previous = tick
        track.append(mido.MetaMessage("end_of_track", time=total_ticks - previous))
        generated.tracks.append(track)
    operations = {"source_bars": source_bars, "target_bars": request.bars,
                  "loop_count": math.ceil(request.bars / source_bars),
                  "source_ticks_per_beat": source.ticks_per_beat, "target_end_tick": total_ticks,
                  "output_midi_channel": channel, "tempo_set_bpm": request.tempo,
                  "closed_boundary_notes": closed, "onset_positions_edited": False,
                  "pitches_transposed": False, "source_track_structure_preserved": True,
                  "original_track_names": [t.name for t in source.tracks]}
    warnings = [f"{closed} sustained note(s) were closed at loop/output boundaries."] if closed else []
    return generated, operations, warnings
