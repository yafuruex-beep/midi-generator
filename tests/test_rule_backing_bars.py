"""Read-only bar selection, half-open membership and Step 1 compatibility."""
import hashlib

import mido
import pytest

from midi_generator.api import extract_rhythm_blueprint, generate_backing_steps, render_backing_midi
from midi_generator.modules.guitar.timeline import read_timeline
from test_guitar_analysis import write_midi


def four_bars(tmp_path, tpb=480, **kwargs):
    # Distinct rhythms; crossing releases must not import the preceding onset.
    notes = [(0, 36, .125, 100, 0, 9), (1, 38, .125, 100, 1, 9),
             (3.75, 35, .5, 80, 0, 9),
             (4, 36, .125, 120, 0, 9), (4.75, 35, .125, 60, 1, 9),
             (5, 40, .125, 100, 1, 9), (6.5, 36, .125, 100, 0, 9),
             (7.75, 38, .5, 100, 1, 9),
             (8, 36, .125, 100, 0, 9), (12, 36, .125, 100, 0, 9)]
    return write_midi(tmp_path / 'four.mid', notes, tpb=tpb, end_beats=16, tracks=2, **kwargs)


@pytest.mark.parametrize('tpb', [96, 480, 960, 100])
def test_second_bar_normalization_multitrack_boundaries_and_accents(tmp_path, tpb):
    path = four_bars(tmp_path, tpb)
    before = path.read_bytes()
    first = extract_rhythm_blueprint(path, bar_index=0)
    second = extract_rhythm_blueprint(path, bar_index=1)
    assert first.kick_steps == (0, 15)
    assert first.snare_steps == (4,)
    assert second.kick_steps == (0, 3, 10)
    assert second.snare_steps == (4, 15)
    assert second.accent_steps == (0, 4, 10, 15)  # 60-velocity kick below 70%
    assert second.source_bar_index == 1
    assert second.source_bar_start_tick == 4 * tpb
    assert second.source_bar_end_tick == 8 * tpb
    assert second.source_ticks_per_beat == tpb
    assert second.source_sha256 == hashlib.sha256(before).hexdigest()
    assert extract_rhythm_blueprint(path, bar_index=2).kick_steps == (0,)
    assert extract_rhythm_blueprint(path, bar_index=3).kick_steps == (0,)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='one complete bar'):
        extract_rhythm_blueprint(path)  # Omission still refuses longer inputs.


def test_second_bar_to_one_bar_guitar(tmp_path):
    path = four_bars(tmp_path)
    bp = extract_rhythm_blueprint(path, bar_index=1)
    steps = generate_backing_steps(bp, note=52)
    output = tmp_path / 'guitar.mid'
    render_backing_midi(steps, export_bpm=105, program=31, output_path=output)
    data = read_timeline(output, set())
    starts = {n['start_tick'] / 480 for n in data['notes']}
    assert {s / 4 for s in bp.kick_steps} <= starts
    assert min(starts) == 0
    assert {s.step for s in steps} - set(bp.kick_steps)
    assert data['context']['tempo_events'][0]['microseconds_per_beat'] == mido.bpm2tempo(105)
    assert data['end_tick'] == 1920
    assert all(0 <= n['start_tick'] < n['end_tick'] <= 1920 for n in data['notes'])
    assert not data['pairing']['unmatched_note_offs']
    assert not data['pairing']['inferred_note_ends']
    for a, b in zip(data['notes'], data['notes'][1:]):
        assert a['end_tick'] <= b['start_tick']
    assert bp.source_bpm == 120
    with pytest.raises(FileExistsError):
        render_backing_midi(steps, export_bpm=105, output_path=path)


@pytest.mark.parametrize('index', [-1, 1.0, True, False, '1', [], 4, 500])
def test_invalid_or_absent_bar(tmp_path, index):
    with pytest.raises(ValueError, match='bar_index'):
        extract_rhythm_blueprint(four_bars(tmp_path), bar_index=index)


@pytest.mark.parametrize('length', [6, 7.98])
def test_final_partial_bar_rejected_but_earlier_full_bar_usable(tmp_path, length):
    notes = [(0, 36, .125, 100, 0, 9), (4, 36, .125, 100, 0, 9)]
    path = write_midi(tmp_path / 'partial.mid', notes, end_beats=length)
    assert extract_rhythm_blueprint(path, bar_index=0).kick_steps == (0,)
    with pytest.raises(ValueError, match='partial bar'):
        extract_rhythm_blueprint(path, bar_index=1)
    with pytest.raises(ValueError, match='does not exist'):
        extract_rhythm_blueprint(path, bar_index=2)


def test_one_bar_compatibility_and_legacy_tail_tolerance(tmp_path):
    path = write_midi(tmp_path / 'one.mid', [(0, 36, .125, 100, 0, 9)], end_beats=4)
    assert extract_rhythm_blueprint(path) == extract_rhythm_blueprint(path, bar_index=0)
    path = write_midi(tmp_path / 'short_tail.mid', [(0, 36, .125, 100, 0, 9)], end_beats=3.98)
    assert extract_rhythm_blueprint(path).kick_steps == (0,)
    with pytest.raises(ValueError, match='partial bar'):
        extract_rhythm_blueprint(path, bar_index=0)


@pytest.mark.parametrize('offset,allowed', [(0.04, True), (.041, False)])
def test_local_grid_tolerance(tmp_path, offset, allowed):
    path = write_midi(tmp_path / 'grid.mid', [(4 + offset, 36, .125, 100, 0, 9)],
                      tpb=1000, end_beats=8)
    if allowed:
        bp = extract_rhythm_blueprint(path, bar_index=1)
        assert bp.kick_steps == (0,)
        assert any('0.04 quarter-beat tolerance' in w for w in bp.warnings)
    else:
        with pytest.raises(ValueError, match='off the straight'):
            extract_rhythm_blueprint(path, bar_index=1)


@pytest.mark.parametrize('kwargs', [
    {'tempos': [(0, 500000), (5, 600000)]},
    {'meters': [(0, 4, 4), (5, 3, 4)]},
    {'tempos': [(0, 500000), (1, 600000)]},
    {'meters': [(0, 4, 4), (9, 3, 4)]},
])
def test_context_changes_anywhere_in_source_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError, match='unsupported'):
        extract_rhythm_blueprint(four_bars(tmp_path, **kwargs), bar_index=1)


def test_non_drums_unmapped_notes_and_outside_grid(tmp_path):
    notes = [(0.1, 36, .125, 100, 0, 9),  # Outside selected bar: not quantized/used.
             (4, 36, .125, 100, 0, 9),
             (4.1, 60, .125, 100, 1, 0),  # Non-drum channel ignored.
             (5, 60, .125, 100, 0, 9)]  # Unknown map not guessed as snare/kick.
    path = write_midi(tmp_path / 'mixed.mid', notes, end_beats=8, tracks=2)
    bp = extract_rhythm_blueprint(path, bar_index=1)
    assert bp.kick_steps == (0,)
    assert bp.snare_steps == ()
    assert any('Only channel 10' in w for w in bp.warnings)
    # Even unmapped percussion inside the selected bar must obey grid policy.
    notes[-1] = (5.1, 60, .125, 100, 0, 9)
    path = write_midi(path, notes, end_beats=8, tracks=2)
    with pytest.raises(ValueError, match='off the straight'):
        extract_rhythm_blueprint(path, bar_index=1)


def test_near_end_onset_does_not_wrap_or_steal_next_bar(tmp_path):
    path = write_midi(tmp_path / 'edge.mid', [(7.98, 36, .01, 100, 0, 9),
                                            (8, 36, .125, 100, 0, 9)], end_beats=12)
    with pytest.raises(ValueError, match='outside the single bar'):
        extract_rhythm_blueprint(path, bar_index=1)
    assert extract_rhythm_blueprint(path, bar_index=2).kick_steps == (0,)


def test_empty_mapped_window_and_broken_pairing_rejected(tmp_path):
    path = write_midi(tmp_path / 'empty.mid', [(0, 36, .125, 100, 0, 9)], end_beats=8)
    with pytest.raises(ValueError, match='No mapped kick'):
        extract_rhythm_blueprint(path, bar_index=1)
    midi = mido.MidiFile(path)
    midi.tracks[-1].insert(0, mido.Message('note_off', note=38, channel=9))
    midi.save(path)
    with pytest.raises(ValueError, match='unmatched'):
        extract_rhythm_blueprint(path, bar_index=1)
