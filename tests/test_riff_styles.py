from collections import defaultdict
from itertools import pairwise

import pytest

from midi_generator.api import generate
from midi_generator.modules.guitar.riff_styles import RIFF_STYLES
from midi_generator.modules.guitar.timeline import read_timeline
from test_guitar_analysis import write_midi
from test_guitar_generation import drum_notes, library, request  # noqa: F401


@pytest.fixture
def drums(tmp_path):
    return write_midi(tmp_path / "drums.mid", drum_notes(), end_beats=32)


def guitar(tmp_path, drums, style, seed=201):
    return generate({"instrument": "guitar", "section": "chorus", "tempo": 120, "bars": 8,
                     "key": "E minor", "seed": seed,
                     "options": {"riff_style": style, "sync_to_drums": str(drums)}},
                    project_root=tmp_path, output_path=tmp_path / f"{style}.mid")


def grouped(path):
    data = read_timeline(path, set())
    assert data["pairing"]["unmatched_note_offs"] == data["pairing"]["inferred_note_ends"] == 0
    attacks = defaultdict(list)
    for note in data["notes"]:
        attacks[note["start_tick"]].append(note)
    groups = []
    for tick, notes in sorted(attacks.items()):
        pitches = sorted(n["note"] for n in notes)
        ends = {n["end_tick"] for n in notes}
        velocities = {n["velocity"] for n in notes}
        assert len(ends) == len(velocities) == 1 and 1 <= len(pitches) <= 2
        if len(pitches) == 2:
            assert pitches[1] - pitches[0] == 7
        end, = ends
        assert tick < end <= (tick // 1920 + 1) * 1920
        groups.append((tick, end, min(pitches), len(pitches), velocities.pop()))
    assert all(a[1] <= b[0] for a, b in pairwise(groups))
    assert data["end_tick"] == 15360
    return groups


@pytest.mark.parametrize("style", RIFF_STYLES)
def test_riff_style_api_features_seed_and_bass(tmp_path, drums, style):
    first = guitar(tmp_path, drums, style).output_files[0]
    repeat = guitar(tmp_path, drums, style).output_files[0]
    other = guitar(tmp_path, drums, style, 202).output_files[0]
    assert first != repeat and first.read_bytes() == repeat.read_bytes()
    groups = grouped(first)
    assert [g[0] for g in groups] != [g[0] for g in grouped(other)]
    assert all(g[2] in (52,55) for g in groups)
    assert sum(g[3] == 2 for g in groups) > len(groups)/2
    a = [g for g in groups if g[0]<3840]
    b = [(t-7680,end-7680,root,count,vel) for t,end,root,count,vel in groups if 7680<=t<11520]
    assert a==b
    adjacent = [(a,b) for a,b in pairwise(groups) if b[0]-a[0]==60]
    if style in ("stutter_breakdown", "groove_burst"):
        assert adjacent and all(a[1]<b[0] and a[2]==b[2] for a,b in adjacent)
    else:
        assert not adjacent
    if style=="chord_driven":
        assert sum(end-start>=240 for start,end,*_ in groups) >= len(groups)*.75
    if style=="stop_and_go":
        assert all(max((b[0]-a[1] for a,b in pairwise(groups) if a[0]//1920==bar==b[0]//1920))>=480
                   for bar in range(8))
    if style=="tight_groove":
        assert sum(t%480!=0 for t,*_ in groups)>len(groups)/2
        assert {60,120} <= {end-start for start,end,*_ in groups}
    if style=="stutter_breakdown":
        for bar in range(8):
            local=[g for g in groups if g[0]//1920==bar]
            assert 2<=len(local)-2<=4 and local[-2][0]-local[-3][1]>=480 and local[-2][3]==2
    if style=="groove_burst":
        tight = grouped(guitar(tmp_path, drums, "tight_groove").output_files[0])
        assert {g[0] for g in tight} < {g[0] for g in groups}
    bass = generate(dict(instrument="bass",section="chorus",tempo=120,bars=8,
                         options=dict(guitar_midi=str(first),transpose_semitones=-24,program=34)),
                    project_root=tmp_path, output_path=tmp_path / "bass.mid").output_files[0]
    assert [(t,end,root-24,1,vel) for t,end,root,_,vel in groups] == grouped(bass)


@pytest.mark.parametrize("style", ["D1", None, 1, []])
def test_riff_style_invalid(style, tmp_path, drums):
    with pytest.raises(ValueError, match="Invalid riff_style"):
        guitar(tmp_path, drums, style)


def test_catalog_result_unchanged_by_opt_in_call(library):
    before = generate(request(), project_root=library)
    generate(request(riff_style="tight_groove"), project_root=library)
    after = generate(request(), project_root=library)
    assert before.output_files[0].read_bytes() == after.output_files[0].read_bytes()
    assert before.metadata["selected_source"] == after.metadata["selected_source"]
