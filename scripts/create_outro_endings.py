"""Localized Outro ending comparison. Run from the repository root."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys

import mido

sys.path.insert(0,str(Path(__file__).resolve().parent))
if not Path(__file__).with_name('create_intro_cohesion_auditions.py').exists():
    sys.path.insert(0,str(Path.cwd()/'scripts'))
from create_intro_cohesion_auditions import _helper

pitch=_helper('create_pitch_variation_auditions')
transition=_helper('create_transition_auditions')
band_io=_helper('run_reference_band_demo')
SOURCE=Path('output/phase10_step5_accent_grouping/song_C_controlled_syncopation.mid')
BAR,TPB,END=1920,480,92160
START=47*BAR
PROTECTED=44*BAR
NAMES=('A_original','B_sustained_ending','C_stop_final_hit')


def replace_tail(track,start,notes, *, end_tick=END):
    """Keep auxiliary messages and all preceding note events, including releases
    exactly at the cut. Reject sounding notes across the cut."""
    events=transition.absolute(track)
    active=set()
    prefix=[]
    for tick,msg in events:
        if tick<start:
            prefix.append((tick,msg))
            if msg.type in {'note_on','note_off'}:
                key=(msg.channel,msg.note)
                if transition.is_release(msg):
                    active.discard(key)
                else:
                    active.add(key)
        elif msg.is_meta or msg.type not in {'note_on','note_off'}:
            prefix.append((tick,msg))
        elif tick==start and transition.is_release(msg) and (msg.channel,msg.note) in active:
            prefix.append((tick,msg))
            active.remove((msg.channel,msg.note))
    if active:
        raise ValueError('Tail replacement would cut a note spanning the boundary')
    for a,b,note,velocity,channel in notes:
        if not start<=a<b<=end_tick:
            raise ValueError('Ending note outside the allowed tail')
        prefix.append((a,mido.Message('note_on',note=note,velocity=velocity,channel=channel)))
        prefix.append((b,mido.Message('note_off',note=note,velocity=0,channel=channel)))
    return transition.schedule(prefix,end_tick)


def make_variant(original,notes,stop_final, *, end_tick=END):
    result=deepcopy(original)
    start=end_tick-BAR
    if stop_final:
        silence_start=start+480
        hit=start+960
        release=start+1680
    else:
        silence_start=start
        hit=start
        release=start+1440
    for i,channel in enumerate((0,1,9)):
        ending=[]
        if stop_final:
            for a,b,n,v,ch in notes:
                if ch==channel+1 and start<=a<silence_start:
                    ending.append((a,min(b,silence_start),n,v,channel))
        if i==0:
            ending += [(hit,release,52,112,0),(hit,release,59,112,0)]
        elif i==1:
            ending.append((hit,release,28,112,1))
        else:
            # Existing mapping defines MIDI36 as kick. Avoid unverified cymbals.
            ending.append((hit,hit+60,36,110,9))
        result.tracks[i]=replace_tail(original.tracks[i],start,ending,end_tick=end_tick)
    return result


def prefix_signature(track,stop):
    return [(t,msg.dict()) for t,msg in transition.absolute(track) if t<stop]


def note_rows(band):
    groups,end=pitch.groups(transition.part(band,0))
    bass,_=pitch.groups(transition.part(band,1))
    assert end==END and len(groups)==len(bass)
    assert all((g['start'],g['end'],g['root']-24,g['velocity'])==
               (b['start'],b['end'],b['root'],b['velocity']) for g,b in zip(groups,bass))
    transition.validate_pitch(groups)
    return groups


def build(output):
    out=Path(output)
    if out.exists():
        raise FileExistsError(out)
    original=mido.MidiFile(SOURCE)
    data,notes=band_io.read_notes(SOURCE)
    assert len(original.tracks)==3 and original.ticks_per_beat==480 and data['end_tick']==END
    candidates=[original,make_variant(original,notes,False),make_variant(original,notes,True)]
    reports=[]
    for index,(name,band) in enumerate(zip(NAMES,candidates)):
        for old,track in zip(original.tracks,band.tracks):
            events=transition.absolute(track)
            assert events[-1][0]==END
            assert prefix_signature(old,START)==prefix_signature(track,START)
            assert prefix_signature(old,PROTECTED)==prefix_signature(track,PROTECTED)
            assert [(t,m.tempo) for t,m in events if m.type=='set_tempo']==[(0,480000)]
            assert [(t,m.numerator,m.denominator) for t,m in events if m.type=='time_signature']==[(0,4,4)]
            assert [(t,m) for t,m in transition.absolute(old) if m.is_meta]==[
                (t,m) for t,m in events if m.is_meta]
            transition.validate_notes(track,END)
        groups=note_rows(band)
        last=groups[-1]
        if index:
            assert (last['root'],last['intervals'])==(52,(0,7))
        finish={}
        for instrument,track in zip(('guitar','bass','drums'),band.tracks):
            note_events=[(tick,msg) for tick,msg in transition.absolute(track) if msg.type in {'note_on','note_off'}]
            attacks=[tick for tick,msg in note_events if msg.type=='note_on' and msg.velocity>0]
            release=max(t for t,msg in note_events if transition.is_release(msg))
            finish[instrument]=dict(last_onset=max(attacks),last_release=release,
                                    trailing_rest_beats=(END-release)/TPB)
        if index==2:
            for track in band.tracks:
                assert not any(START+480<=t<START+960 and m.type=='note_on' and m.velocity>0
                               for t,m in transition.absolute(track))
            assert all(finish[k]['last_onset']==START+960 for k in finish)
        diff=transition.event_difference(original,band)
        assert all(r['tick']>=START for changes in diff.values() for rows in changes.values() for r in rows)
        reports.append(dict(candidate=name,final_pitch=last['root'],final_intervals=list(last['intervals']),
                            final_gate_beats=(last['end']-last['start'])/TPB,finish=finish,
                            guitar_last4=[dict(bar=g['start']//BAR+1,beat=g['start']%BAR/TPB,
                                                root=g['root'],intervals=list(g['intervals']),
                                                gate=(g['end']-g['start'])/TPB)
                                          for g in groups if g['start']>=PROTECTED],
                            event_diff=diff))
    out.mkdir()
    for name,band in zip(NAMES,candidates):
        if name==NAMES[0]:
            (out/f'song_{name}.mid').write_bytes(SOURCE.read_bytes())
        else:
            pitch.save_new(band,out/f'song_{name}.mid')
    band_io.dump(out/'verification.json',dict(source_sha256=pitch.sha(SOURCE),comparisons=reports))
    lines=['# Phase 10 Step 6 Outro終止比較','',
           '125 BPM、E minor、4/4、48小節。変更は48小節だけ。1〜47小節の全イベントを保持し、'
           '45〜47小節で既存のIntro由来モチーフをそのまま再提示する。'
           '各トラックのテンポ・拍子・マーカー・Program Change等の補助イベントも原本通り。','',
           '|候補|最終発音（小節内の拍は0始まり）|最終ギター|音価|最終ギター休符|MIDI終端|',
           '|---|---|---|---:|---:|---|']
    for r in reports:
        end=r['finish']['guitar']
        onset=end['last_onset']
        notes=[r['final_pitch']+i for i in r['final_intervals']]
        lines.append(f'|{r["candidate"]}|48小節 {onset%BAR/TPB:g}拍|{notes}|{r["final_gate_beats"]:g}拍|'
                     f'{end["trailing_rest_beats"]:g}拍|48小節末、92160 tick|')
    lines+=['','Aの最後はMIDI52の単音。E5パワーコードはB/CでMIDI52＋59（E3＋B3）として明示し、ベース28が追従する。'
            'Bは48小節頭に最終コードを置き3拍保持、ドラムは同時キック1打で停止。'
            'Cは48小節の先頭1拍を保ち、1〜2拍の1拍を全楽器のMIDI無音とし、2拍目で最終ヒット。'
            '1.5拍のギター・ベース保持後、0.5拍を残す。velocity112は既存の最大118以内。','',
            '## 最終4小節のギター発音','',
            '|候補|小節|小節内拍（0始まり）|音程|音価|','|---|---:|---:|---|---:|']
    for r in reports:
        for g in r['guitar_last4']:
            notes=[g['root']+i for i in g['intervals']]
            lines.append(f'|{r["candidate"]}|{g["bar"]}|{g["beat"]:g}|{notes}|{g["gate"]:g}|')
    lines+=['','## 楽器ごとの終止と差分','',
            '|候補|楽器|最終Note Off tick|末尾休符（拍）|削除イベント|追加イベント|',
            '|---|---|---:|---:|---:|---:|']
    for r in reports:
        for instrument,finish in r['finish'].items():
            diff=r['event_diff'][instrument]
            lines.append(f'|{r["candidate"]}|{instrument}|{finish["last_release"]}|{finish["trailing_rest_beats"]:g}|'
                         f'{len(diff["removed"])}|{len(diff["added"])}|')
    lines+=['','Note On/Offの差分は削除・追加として集計し、全件をverification.jsonに保存。'
            'Aは原本とバイト一致。B/Cは1〜44小節に加え45〜47小節も一致。'
            '原本のChorus・Breakdown・曲中接続を保持。各候補で全ノートが92160 tick以内に終了する。','',
            '## 試聴での確認事項','',
            '- Bの3拍保持とCの1拍停止が意図した終止として聞こえるか。',
            '- MIDI上の全体停止後も、音源のリリースやシンバル残響が続く可能性がある。',
            '- 終止の自然さと余韻は音源での試聴判断を待つ。','']
    (out/'comparison_report.md').write_text('\n'.join(lines)+'\n')
    (out/'README.md').write_text('# Outro終止の試聴\n\n'+ '\n'.join(
        f'- [{name}](song_{name}.mid)' for name in NAMES)+
        '\n\n[比較レポート](comparison_report.md) · [イベント差分](verification.json)\n')
    return candidates


if __name__=='__main__':
    build(sys.argv[1])
