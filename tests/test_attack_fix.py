from collections import Counter
import pytest
from midi_generator.modules.guitar.attack_fix import add_bar_head_attacks
from midi_generator.modules.guitar.reference_composition import midi_bytes
from midi_generator.modules.guitar.reference_development import render_developed_groups


def source():
    rows=[]
    for b in range(8):
        for tick in ([0,60,120] if b%2==0 else [120,180,240]):
            rows.append(dict(start=b*1920+tick,end=b*1920+tick+45,notes=[47,54] if tick in (0,120) else [47],velocity=104 if tick==120 else 90))
        rows.append(dict(start=b*1920+1440,end=b*1920+1800,notes=[47,54],velocity=110))
    return render_developed_groups(rows,bpm=95)


def absolute(mid):
    tick=0;rows=[]
    for m in mid.tracks[0]:
        tick+=m.time;rows.append((tick,m.copy(time=0)))
    return rows


def signature(rows):return Counter((t,repr(m)) for t,m in rows)


def test_only_three_on_off_pairs_added_and_all_original_events_unchanged():
    old=source();new,adds=add_bar_head_attacks(old,bars=[2,4,6]);a=signature(absolute(old));b=signature(absolute(new));assert not (a-b)
    extras=b-a;assert sum(extras.values())==6
    assert [(n['start_tick'],n['end_tick'],n['note'],n['velocity']) for n in adds]==[(1920,2040,47,104),(5760,5880,47,104),(9600,9720,47,104)]
    assert all(not n['capped_to_existing_attack'] for n in adds)
    for bar in [1,3,5,7,8]:
        lo=(bar-1)*1920;hi=bar*1920
        assert signature([(t,m) for t,m in absolute(old) if lo<=t<hi])==signature([(t,m) for t,m in absolute(new) if lo<=t<hi])


def test_shared_tick_off_precedes_existing_on_and_no_overlap():
    new,adds=add_bar_head_attacks(source(),bars=[2,4,6]);rows=absolute(new)
    for n in adds:
        events=[m for t,m in rows if t==n['end_tick'] and m.type in ('note_on','note_off') and m.note==47]
        assert [m.type for m in events]==['note_off','note_on']
    active={}
    for tick,msg in rows:
        if msg.type=='note_on' and msg.velocity:assert (msg.channel,msg.note) not in active;active[msg.channel,msg.note]=tick
        elif msg.type=='note_off':assert active.pop((msg.channel,msg.note))<tick
    assert not active and rows[-1][0]==15360


def test_deterministic_source_immutable_and_existing_onsets_preserved():
    old=source();before=midi_bytes(old);first,_=add_bar_head_attacks(old,bars=[2,4,6]);second,_=add_bar_head_attacks(old,bars=[2,4,6])
    assert midi_bytes(first)==midi_bytes(second) and midi_bytes(old)==before
    for bar in [2,4,6]:
        tick=(bar-1)*1920+120
        assert [(m.note,m.velocity) for t,m in absolute(first) if t==tick and m.type=='note_on']==[(m.note,m.velocity) for t,m in absolute(old) if t==tick and m.type=='note_on']


def test_requested_gate_caps_at_existing_first_attack_not_existing_note():
    old=source();new,adds=add_bar_head_attacks(old,bars=[2],duration_beats=.5)
    assert adds[0]['actual_duration_beats']==.25 and adds[0]['capped_to_existing_attack']
    assert not (signature(absolute(old))-signature(absolute(new)))


def test_bad_or_duplicate_edits_rejected():
    old=source()
    for bars in ([1],[2,2],[0],[9],[True],[]):
        with pytest.raises(ValueError):add_bar_head_attacks(old,bars=bars)
    for duration in (0,-.25,float('nan'),True):
        with pytest.raises(ValueError):add_bar_head_attacks(old,bars=[2],duration_beats=duration)
    with pytest.raises(ValueError):add_bar_head_attacks(old,bars=[2],note=48)
