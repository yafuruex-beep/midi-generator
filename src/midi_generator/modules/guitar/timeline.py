"""Guitar MIDI timelines: global pairing, tempo conversion and meter-aware bars."""
from bisect import bisect_right
from collections import Counter, defaultdict, deque

import mido

from midi_generator.storage.catalog import analyze_midi


class TempoTimeline:
    def __init__(self, ticks_per_beat, events):
        self.tpb = ticks_per_beat
        self.ticks, self.times, self.tempos = [0], [0.0], [500000]
        for event in events:
            tick, tempo = event["tick"], event["microseconds_per_beat"]
            if tick == self.ticks[-1]:
                self.tempos[-1] = tempo
            else:
                seconds = self.seconds(tick)
                self.ticks.append(tick)
                self.times.append(seconds)
                self.tempos.append(tempo)

    def seconds(self, tick):
        index = max(0, bisect_right(self.ticks, tick) - 1)
        return self.times[index] + mido.tick2second(tick - self.ticks[index], self.tpb, self.tempos[index])


def read_timeline(path, excluded_notes):
    midi = mido.MidiFile(path)
    if midi.type == 2:
        raise ValueError("Independent MIDI format 2 sequences are not merged for guitar analysis")
    if midi.ticks_per_beat <= 0:
        raise ValueError("Guitar analysis requires positive ticks_per_beat")
    context = analyze_midi(path)
    events = defaultdict(list)
    tracks, ends, all_channels = [], [], set()
    for index, track in enumerate(midi.tracks):
        tick, count, channels = 0, 0, set()
        for order, message in enumerate(track):
            tick += message.time
            if hasattr(message, "channel"):
                channels.add(message.channel + 1)
                all_channels.add(message.channel + 1)
            if message.type in {"note_on", "note_off"}:
                events[tick].append((index, order, message))
                if message.type == "note_on" and message.velocity > 0:
                    count += 1
        ends.append(tick)
        tracks.append({"index": index, "name": track.name or None, "note_on_count": count,
                       "channels": sorted(channels), "end_tick": tick})
    end_tick = max(ends, default=0)
    pending, notes = defaultdict(deque), []
    orphan_offs = positive_ons = inferred = 0

    def close(attack, release_tick, inferred_end=False):
        start, message, track_index = attack
        notes.append({"start_tick": start, "end_tick": max(start, release_tick),
                      "note": message.note, "velocity": message.velocity,
                      "channel": message.channel + 1, "track_index": track_index,
                      "end_inferred": inferred_end})

    for tick, group in sorted(events.items()):
        releases = defaultdict(deque)
        attacks = []
        for track_index, order, message in sorted(group, key=lambda item: (item[0], item[1])):
            key = (message.channel, message.note)
            if message.type == "note_on" and message.velocity > 0:
                positive_ons += 1
                attacks.append((tick, message, track_index))
            elif pending[key]:
                close(pending[key].popleft(), tick)
            else:
                releases[key].append(message)
        # Existing voices are released before retriggers. Unmatched releases at the
        # same tick can still pair with a genuine zero-length attack.
        for attack in attacks:
            message = attack[1]
            key = (message.channel, message.note)
            if releases[key]:
                releases[key].popleft()
                close(attack, tick)
            else:
                pending[key].append(attack)
        orphan_offs += sum(len(values) for values in releases.values())
    for values in pending.values():
        for attack in values:
            close(attack, end_tick, True)
            inferred += 1
    notes.sort(key=lambda note: (note["start_tick"], note["note"], note["track_index"]))
    ignored = Counter(note["note"] for note in notes if note["note"] in excluded_notes)
    analyzed = [note for note in notes if note["note"] not in excluded_notes]
    warnings = list(context["warnings"])
    if orphan_offs:
        warnings.append(f"{orphan_offs} unmatched Note Off events were recorded and ignored.")
    if inferred:
        warnings.append(f"{inferred} missing Note Off endpoints were estimated at the file end for analysis only.")
    if len(all_channels) > 1:
        warnings.append("Multiple MIDI channels are preserved and analyzed as supplied guitar parts; instrument is determined by directory, not channel.")
    if ignored:
        warnings.append("Configured excluded notes were omitted from feature analysis, without editing the MIDI.")
    tempos_at_ticks = defaultdict(set)
    for event in context["tempo_events"]:
        tempos_at_ticks[event["tick"]].add(event["microseconds_per_beat"])
    if any(len(values) > 1 for values in tempos_at_ticks.values()):
        warnings.append("Conflicting tempo events at one tick: existing catalog track-order precedence was used.")
    timeline = TempoTimeline(midi.ticks_per_beat, context["tempo_events"])
    return {"midi": midi, "context": context, "notes": analyzed, "timeline": timeline,
            "end_tick": end_tick, "tracks": tracks, "channels": sorted(all_channels), "warnings": warnings,
            "pairing": {"positive_note_ons": positive_ons, "paired_note_count": len(notes),
                        "unmatched_note_offs": orphan_offs, "inferred_note_ends": inferred,
                        "excluded_note_counts": {str(note): count for note, count in sorted(ignored.items())},
                        "policy": "Global FIFO per channel/note; existing releases precede attacks at the same tick."}}


def bar_segments(data, tail_tolerance_beats):
    tpb, end = data["midi"].ticks_per_beat, data["end_tick"]
    changes = {0: (4, 4)}
    for event in data["context"]["time_signature_events"]:
        changes[event["tick"]] = (event["numerator"], event["denominator"])
    changes = sorted(changes.items())
    bars = []
    for index, (tick, signature) in enumerate(changes):
        stop = min(end, changes[index + 1][0]) if index + 1 < len(changes) else end
        length = tpb * signature[0] * 4 / signature[1]
        while tick < stop:
            right = min(tick + length, stop)
            bars.append({"index": len(bars) + 1, "start_tick": tick, "end_tick": right,
                         "time_signature": list(signature), "quarter_beats": (right - tick) / tpb,
                         "complete": abs(right - tick - length) <= tail_tolerance_beats * tpb})
            tick = right
    if len(bars) > 1 and bars[-1]["quarter_beats"] <= tail_tolerance_beats:
        tail = bars[-1]
        if not any(tail["start_tick"] <= note["start_tick"] < tail["end_tick"] for note in data["notes"]):
            bars[-2]["end_tick"] = tail["end_tick"]
            bars[-2]["quarter_beats"] += tail["quarter_beats"]
            bars.pop()
            data["warnings"].append("A note-free sub-tolerance export tail was merged into the last bar for structural analysis.")
    return bars
