from copy import deepcopy
import mido
import pytest
from midi_generator.modules.drums.reference_drum_sync import compose_original_drums,analyze_eighth_positions,compare_reference_phases
from midi_generator.modules.guitar.reference_development import render_developed_groups
from midi_generator.modules.guitar.reference_composition import midi_bytes

MAP=dict(kick=36,snare=38,closed_hat=42,open_hat=46,crash=49)

def fixture(profile):
    rows=[];groups=[]
    for b in range(8):
        rows.append(dict(cells=[dict(accent_score=.6 if s in (0,7,12) else .1) for s in range(16)]))
        if profile=='d':
            cells=[0,60,120,180,840,1440] if b%2==0 else [120,180,240,1200,1440]
            if b==6:cells.remove(840)
            for t in cells:groups.append(dict(start=b*1920+t,end=b*1920+t+(45 if t<300 else 360 if t==1440 else 120),notes=[47,54] if t>=720 or t==0 else [47],velocity=112 if t==cells[0] or t>=720 else 89))
        else:
            for t,gate in [(0,960),(1200,240),(1680,240)]:groups.append(dict(start=b*1920+t,end=b*1920+t+gate,notes=[47,54],velocity=110))
    return render_developed_groups(groups,bpm=95),dict(section=dict(bars=8,bpm=95),bars=rows)

@pytest.mark.parametrize('p',['c','d'])
def test_drums_and_band_are_deterministic_and_guitar_unchanged(p):
    guitar,g=fixture(p);old=midi_bytes(guitar);before=deepcopy(g)
    drum,band,meta=compose_original_drums(guitar,g,profile=p,mapping=MAP,seed=42)
    assert old==midi_bytes(guitar) and before==g
    assert [m.dict() for m in band.tracks[0]]==[m.dict() for m in guitar.tracks[0]]
    assert midi_bytes(drum)==midi_bytes(compose_original_drums(guitar,g,profile=p,mapping=MAP,seed=42)[0])
    assert sum(m.type=='set_tempo' for t in band.tracks for m in t)==1
    active={};tick=0
    for msg in drum.tracks[0]:
        assert msg.time>=0;tick+=msg.time
        if msg.type=='note_on' and msg.velocity:assert msg.channel==9 and msg.note not in active;active[msg.note]=tick
        elif msg.type=='note_off':assert active.pop(msg.note)<tick<=15360
    assert not active and tick==15360


def test_d_kick_sync_half_time_and_special_break():
    guitar,g=fixture('d');_,_,meta=compose_original_drums(guitar,g,profile='d',mapping=MAP)
    onsets=set();tick=0
    for msg in guitar.tracks[0]:
        tick+=msg.time
        if msg.type=='note_on' and msg.velocity:onsets.add(tick)
    assert all(n['start'] in onsets for n in meta['notes'] if n['kind']=='kick')
    assert {n['start']//1920 for n in meta['notes'] if n['kind']=='snare'}=={0,1,2,3,4,5,7}
    assert all(n['start']%1920==960 for n in meta['notes'] if n['kind']=='snare')
    assert not any(6*1920+225<n['start']<6*1920+1440 for n in meta['notes'])
    assert max(sum(n['kind']=='kick' and n['bar']==b for n in meta['notes']) for b in range(1,9))<=3


def test_c_backbeat_and_hat_articulation():
    guitar,g=fixture('c');_,_,meta=compose_original_drums(guitar,g,profile='c',mapping=MAP)
    assert sum(n['kind']=='snare' for n in meta['notes'])==16
    assert all(n['start']%1920 in (480,1440) for n in meta['notes'] if n['kind']=='snare')
    assert sum(n['kind']=='crash' for n in meta['notes'])==2
    assert not any(a['start']==b['start'] and {a['kind'],b['kind']}=={'closed_hat','open_hat'} for a in meta['notes'] for b in meta['notes'])


def test_missing_odd_eighth_positions_are_real_composed_rests():
    guitar,g=fixture('d');_,_,meta=compose_original_drums(guitar,g,profile='d',mapping=MAP);rows=analyze_eighth_positions(guitar,meta,bpm=95)
    assert len(rows)==64
    odd=[r for r in rows if r['odd_position_focus']];assert len(odd)==32 and sum(r['guitar_attack'] for r in odd)==12
    for b in (2,4,6,8):
        r=next(r for r in rows if r['bar']==b and r['eighth_position']==1)
        assert not r['guitar_attack'] and r['next_onset_delay_beats']==.25 and 'closed_hat' in r['other_drum_notes']
    assert all(next(r for r in rows if r['bar']==b and r['eighth_position']==5)['guitar_ending_here'] for b in (1,3,5))
    assert all(not r['guitar_attack'] for r in odd if r['eighth_position'] in (3,5))


def test_phase_scan_uses_raw_off_grid_events_without_mutation():
    events=[dict(band=band,section_beat=i*.5+.04,strength=1.,composition_grid_eligible=False) for band in ('low','body','high') for i in range(64)]
    g=dict(observations=events);before=deepcopy(g);out=compare_reference_phases(g)
    assert g==before
    for r in out['phase_comparisons']:
        assert r['best']['phase_beats']==pytest.approx(.04,abs=.003)
        assert r['best']['mean_absolute_residual_beats']<r['at_C']['mean_absolute_residual_beats']
    assert len(out['coarse_bar_origin_candidates'])==4


def test_invalid_request_refused():
    guitar,g=fixture('d')
    with pytest.raises(ValueError):compose_original_drums(guitar,g,profile='unknown',mapping=MAP)
    with pytest.raises(ValueError):compose_original_drums(guitar,g,profile='d',mapping=MAP,seed=True)
    with pytest.raises(ValueError):compose_original_drums(guitar,g,profile='d',mapping=dict(MAP,kick=1))
    g['section']['bpm']=105
    with pytest.raises(ValueError,match='tempo'):compose_original_drums(guitar,g,profile='d',mapping=MAP)
