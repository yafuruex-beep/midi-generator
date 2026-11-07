"""Analyze repeated accent evidence before making localized groove auditions."""
from collections import Counter
from copy import deepcopy
from itertools import pairwise
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import mido
from midi_generator.api import generate

sys.path.insert(0,str(Path(__file__).resolve().parent))
if not Path(__file__).with_name('create_intro_cohesion_auditions.py').exists():
    sys.path.insert(0,str(Path.cwd()/'scripts'))
from create_intro_cohesion_auditions import _helper
pitch=_helper('create_pitch_variation_auditions')
transition=_helper('create_transition_auditions')
band_io=_helper('run_reference_band_demo')

SOURCE=Path('output/phase10_step4_song_arrangement/song_C_refined_transitions.mid')
SECTIONS=(('Intro',0,8),('Verse',8,24),('Chorus',24,32),('Breakdown',32,40),('Outro',40,48))
PATTERNS=((4,4),(3,3,2),(3,2,3),(2,3,3),(5,5,6))
BAR,END=1920,92160
NAMES=('A_original','B_straight_groove','C_controlled_syncopation')


def groups(path):
    return [{k:deepcopy(g[k]) for k in ('start','end','root','velocity','channel','intervals')}
            for g in pitch.groups(mido.MidiFile(path))[0]]


def cycle_hits(ticks,pattern,unit=120):
    tickset=set(ticks)
    return [t for t in ticks if all(t+sum(pattern[:i])*unit in tickset
                                  for i in range(1,len(pattern)+1))]


def rest_ranges(rows,start,end):
    notes=[g for g in rows if start<=g['start']<end]
    return {(a,b) for a,b in zip([start]+[g['end'] for g in notes],
                                [g['start'] for g in notes]+[end]) if a<b}


def burst_signature(rows):
    return [(a['start'],b['start'],a['root'],b['root']) for a,b in pairwise(rows)
            if b['start']-a['start']==60]


def accent_analysis(rows,drums):
    result=[]
    for name,first,last in SECTIONS:
        start,end=first*BAR,last*BAR
        local=[g for g in rows if start<=g['start']<end]
        strong=[g for g in local if g['velocity']>=100]
        kicks=[n for n in drums if start<=n[0]<end and n[2] in (35,36)]
        snare=[n for n in drums if start<=n[0]<end and n[2] in (38,40)]
        patterns={}
        for instrument,ticks in [('guitar',[g['start'] for g in strong]),
                                 ('kick',[n[0] for n in kicks if n[3]>=100]),
                                 ('snare',[n[0] for n in snare if n[3]>=100])]:
            # Drum candidates are admitted only within .04 beats of the grid.
            grid=sorted({round(t/120)*120 for t in ticks if abs(round(t/120)*120-t)<=19})
            patterns[instrument]={str(p):dict(ticks=cycle_hits(grid,p),
                crosses_bar=[t for t in cycle_hits(grid,p) if t//BAR!=(t+sum(p)*120)//BAR])
                                  for p in PATTERNS}
        masks=Counter(tuple(g['start']%BAR/120 for g in strong if g['start']//BAR==bar)
                      for bar in range(first,last))
        profile=Counter(g['start']%BAR/120 for g in strong)
        sustained=[g['start'] for g in local if g['end']-g['start']>=240]
        rests=rest_ranges(rows,start,end)
        after_rest=[g['start'] for g in local if any(b==g['start'] and b-a>=240 for a,b in rests)]
        repeated={}
        for mask,count in masks.items():
            repeated[str(mask)]=dict(count=count,
                 bars=[bar+1 for bar in range(first,last) if tuple(
                     g['start']%BAR/120 for g in strong if g['start']//BAR==bar)==mask])
        motifs=[]
        origin={g['start']-start:g for g in local if g['start']<start+2*BAR}
        for bar in range(first+2,last,2):
            target={g['start']-bar*BAR:g for g in local if bar*BAR<=g['start']<(bar+2)*BAR}
            union=origin.keys()|target.keys()
            common=origin.keys()&target.keys()
            motifs.append(dict(bars=[bar+1,min(bar+2,last)],rhythm=[len(common),len(union)],
                       accents=[sum((origin[t]['velocity']>=100)==(target[t]['velocity']>=100)
                                    for t in common),len(common)]))
        result.append(dict(section=name,groups=len(local),strong=len(strong),
             quarter_onsets=sum(g['start']%480==0 for g in local),
             metrical_strong_onsets=sum(g['start']%BAR in (0,960) for g in local),
             offbeat_onsets=sum(g['start']%480!=0 for g in local),
             downbeat_accents=sum(g['start']%BAR==0 for g in strong),
             kick_matches=sum(any(abs(g['start']-n[0])<=19 for n in kicks) for g in strong),
             accent_distribution=dict(sorted(profile.items())),repeated_masks=repeated,
             sustained_onsets=sustained,rest_emphasis_onsets=after_rest,
             rests=sorted(rests),pattern_candidates=patterns,motifs=motifs))
    return result


def is_kick(tick,drums):
    return any(n[2] in (35,36) and abs(n[0]-tick)<=19 for n in drums)


def valid_move(rows,index,target):
    g=rows[index]
    end=target+g['end']-g['start']
    lower=rows[index-1]['end'] if index else 0
    upper=rows[index+1]['start'] if index+1<len(rows) else END
    return lower<=target<end<=upper and target//BAR==g['start']//BAR and end<=(target//BAR+1)*BAR


def revise(original,drums,controlled, *, skip_unmatched=False):
    rows=deepcopy(original)
    percussion=deepcopy(drums)
    # Measured 5+5+6 masks. Protect section-edge bars and alter all three
    # guitar/kick roles together so the primary pairing remains intact.
    chosen_verse=(11,17,19) if controlled else (9,11,13,17,19,21)
    changes=[]
    for bar in chosen_verse:
        indices=[i for i,g in enumerate(rows) if g['start']//BAR==bar and g['velocity']>=100]
        positions=[rows[i]['start']%BAR//120 for i in indices]
        expected=[1,6,12] if bar in (9,17) else [1,6,11]
        if positions!=expected and skip_unmatched:
            continue
        if positions!=expected:
            raise ValueError(f'Expected measured Verse accent mask at bar {bar+1}: {positions}')
        for index,step in zip(indices,(0,4,12)):
            g=rows[index]
            old=g['start']
            target=bar*BAR+step*120
            if not valid_move(rows,index,target):
                raise ValueError('Cannot move measured Verse accent without overlap')
            delta=target-old
            if delta==0:
                continue
            g['start']+=delta
            g['end']+=delta
            target_has_kick=is_kick(target,percussion)
            for j,n in enumerate(percussion):
                if not target_has_kick and n[2] in (35,36) and abs(n[0]-old)<=19:
                    a,b,note,velocity,channel=n
                    percussion[j]=(a+delta,b+delta,note,velocity,channel)
            changes.append(dict(bar=bar+1,old_tick=old,new_tick=target,reason='Measured 5+5+6 mask'))
    # Repeated Intro/Outro offbeat strong masks. Only move an offbeat
    # accent when its kick relationship and neighboring gates are preserved.
    for index,g in enumerate(rows):
        bar=g['start']//BAR
        section=next(name for name,a,b in SECTIONS if a<=bar<b)
        if section not in ('Intro','Outro') or bar in (0,7,40,47):
            continue
        if controlled and bar%4!=3:
            continue
        if g['velocity']<100 or g['start']%480==0 or is_kick(g['start'],percussion):
            continue
        # Protect all burst participants.
        if any(abs(g['start']-other['start'])==60 for other in rows):
            continue
        old=g['start']
        candidates=sorted({old//480*480,(old//480+1)*480},key=lambda t:(abs(t-old),t))
        for target in candidates:
            if valid_move(rows,index,target) and not is_kick(target,percussion):
                delta=target-old
                g['start']+=delta
                g['end']+=delta
                changes.append(dict(bar=bar+1,old_tick=old,new_tick=target,
                                    reason='Repeated offbeat velocity accent; existing kick relation preserved'))
                break
    if not skip_unmatched:
        assert changes
    assert [(g['root'],g['intervals'],g['velocity'],g['end']-g['start']) for g in rows]==[
        (g['root'],g['intervals'],g['velocity'],g['end']-g['start']) for g in original]
    assert burst_signature(rows)==burst_signature(original)
    assert [n for n in percussion if n[2] in (38,40)]==[n for n in drums if n[2] in (38,40)]
    assert all(not is_kick(g['start'],drums) or is_kick(new['start'],percussion)
               for g,new in zip(original,rows) if g['velocity']>=100)
    return rows,percussion,changes


def assemble(rows,drums,original,work,name):
    markers=[(tick,msg.text) for tick,msg in transition.absolute(original.tracks[0]) if msg.type=='marker']
    guitar_path=work/f'{name}_guitar.mid'
    notes=[(g['start'],g['end'],g['root']+interval,g['velocity'],1)
           for g in rows for interval in g['intervals']]
    guitar=band_io.write_part(guitar_path,'Guitar',notes,END,480000,markers,program=31,channel=1)
    bass_path=generate(dict(instrument='bass',section='song',tempo=125,bars=48,
             options=dict(guitar_midi=str(guitar_path),transpose_semitones=-24,program=34,midi_channel=2)),
             output_path=work/f'{name}_bass.mid').output_files[0]
    bass=mido.MidiFile(bass_path)
    drum=band_io.write_part(work/f'{name}_drums.mid','Drums',drums,END,480000,markers)
    band=mido.MidiFile(type=1,ticks_per_beat=480)
    band.tracks.extend([guitar.tracks[0],bass.tracks[0],drum.tracks[0]])
    return band


def build(output):
    out=Path(output)
    if out.exists():
        raise FileExistsError(out)
    original=mido.MidiFile(SOURCE)
    data,drums=band_io.read_notes(SOURCE)
    # Use only the drum channel from the verified three-track band.
    drums=[n for n in drums if n[4]==10]
    rows=[{k:deepcopy(g[k]) for k in ('start','end','root','velocity','channel','intervals')}
          for g in pitch.groups(transition.part(original,0))[0]]
    assert len(original.tracks)==3 and original.ticks_per_beat==480 and data['end_tick']==END
    for track in original.tracks:
        events=transition.absolute(track)
        assert [(t,m.tempo) for t,m in events if m.type=='set_tempo']==[(0,480000)]
        assert [(t,m.numerator,m.denominator) for t,m in events if m.type=='time_signature']==[(0,4,4)]
    analysis=accent_analysis(rows,drums)
    assert all(not a['pattern_candidates']['guitar'][str(pattern)]['ticks']
               for a in analysis for pattern in ((3,3,2),(3,2,3),(2,3,3)))
    revisions=[(rows,drums,[]),revise(rows,drums,False),revise(rows,drums,True)]
    bands=[original]
    with TemporaryDirectory() as directory:
        work=Path(directory)
        bands.extend(assemble(g,d,original,work,label) for label,(g,d,_) in zip(NAMES[1:],revisions[1:]))
    stats=[]
    for label,band,(g,d,changes) in zip(NAMES,bands,revisions):
        for track in band.tracks:
            transition.validate_notes(track,END)
            events=transition.absolute(track)
            assert events[-1][0]==END
            assert [(t,m.tempo) for t,m in events if m.type=='set_tempo']==[(0,480000)]
            assert [(t,m.numerator,m.denominator) for t,m in events if m.type=='time_signature']==[(0,4,4)]
        markers=lambda track:[(t,m.text) for t,m in transition.absolute(track) if m.type=='marker']
        assert markers(band.tracks[0])==markers(original.tracks[0])
        gs,bs=[pitch.groups(transition.part(band,i))[0] for i in (0,1)]
        assert len(gs)==len(bs)
        assert all((a['start'],a['end'],a['root']-24,a['velocity'])==
                   (b['start'],b['end'],b['root'],b['velocity']) for a,b in zip(gs,bs))
        # Preserve the entire Chorus, Breakdown, ending, and section-edge bars.
        protected=set(range(24,40))|{7,23,40,47}
        for a,b in zip(rows,g):
            if a['start']//BAR in protected:
                assert a==b
        moved=sum(a['start']!=b['start'] for a,b in zip(rows,g))
        rest_delta=len(rest_ranges(rows,0,END)^rest_ranges(g,0,END))
        stats.append(dict(candidate=label,moved_attacks=moved,rest_interval_difference=rest_delta,
                          duration_changes=0,velocity_changes=0,changes=changes,
                          sections=accent_analysis(g,d),
                          event_diff=transition.event_difference(original,band)))
    before_count=len(analysis[1]['pattern_candidates']['guitar']['(5, 5, 6)']['ticks'])
    assert before_count==2
    assert len(stats[1]['sections'][1]['pattern_candidates']['guitar']['(5, 5, 6)']['ticks'])==0
    assert len(stats[2]['sections'][1]['pattern_candidates']['guitar']['(5, 5, 6)']['ticks'])==1
    out.mkdir()
    for label,band in zip(NAMES,bands):
        if label==NAMES[0]:
            (out/f'song_{label}.mid').write_bytes(SOURCE.read_bytes())
        else:
            pitch.save_new(band,out/f'song_{label}.mid')
    band_io.dump(out/'verification.json',dict(source_sha256=pitch.sha(SOURCE),analysis=analysis,comparisons=stats))
    write_reports(out,analysis,stats)
    return bands


def write_reports(out,analysis,stats):
    lines=['# アクセント周期の解析','',
           '原本の実拍子は全48小節で4/4、125 BPM。以下はMIDIに記録された打点と表現の分析。'
           'velocity 100以上を強打と定義。音源の奏法や聴覚上のアクセントは未確認。'
           'キック35/36、スネア38/40。ドラムは±19 tick以内だけ16分グリッドへ解析上対応付けし、原本は保持。','',
           '## 確認結果と推定','',
           'ギター強打の3+3+2、3+2+3、2+3+3は、連続する境界点まで含めた検出では0回。'
           'これらを原因として除去する処理は追加していない。',
           'Verseに1・6・11という16分位置の強打マスクが反復。別途、小節をまたぐ5+5+6は9小節目の第12ステップから10小節目、17小節目の第12ステップから18小節目にかけて各1回検出（ステップは0始まり）。'
           'この配置自体は複数小節で反復するが、同じ周期が途切れず続くポリメーターとは断定できない。'
           'IntroとOutroでは不均等な強打マスクが対応する小節で再登場。'
           'これらの配置が複雑な拍節感の原因となる可能性はあるが、聴感上の因果関係は未確認。','',
           '|区間|発音|1・3拍発音|四分拍頭発音|拍頭外発音|強打|小節頭強打|強打→キック|',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for a,(_,first,last) in zip(analysis,SECTIONS):
        n=a['groups']
        lines.append(f'|{a["section"]}|{n}|{a["metrical_strong_onsets"]}/{n}|{a["quarter_onsets"]}/{n}|'
                     f'{a["offbeat_onsets"]}/{n}|{a["strong"]}|{a["downbeat_accents"]}/{last-first}|'
                     f'{a["kick_matches"]}/{a["strong"]}|')
    for a in analysis:
        lines+=['',f'## {a["section"]}','',
                '強打の16分位置分布 '+str(a['accent_distribution']),
                '強打マスクの反復（0始まり16分位置） '+str(a['repeated_masks']),
                '長音（0.5拍以上）による強調候補の曲頭拍 '+str([t/480 for t in a['sustained_onsets']]),
                '0.5拍以上の休符直後の強調候補の曲頭拍 '+str([t/480 for t in a['rest_emphasis_onsets']]),
                '休符区間（曲頭拍） '+str([(x/480,y/480) for x,y in a['rests']]),
                '2小節モチーフ反復 '+str(a['motifs'])]
        for instrument,candidates in a['pattern_candidates'].items():
            for pattern,found in candidates.items():
                lines.append(f'- {instrument} {pattern}（単位16分） {len(found["ticks"])}回。開始小節 '
                             f'{[t//BAR+1 for t in found["ticks"]]}。小節線横断開始 '
                             f'{[t//BAR+1 for t in found["crosses_bar"]]}。')
    lines+=['','強打閾値に依存する探索のため、不検出は他の奏法・音色アクセントの不存在を意味しない。'
            '定型周期の検出は複数の強打境界を必要とする。単独のIOIだけから周期を推定していない。'
            'ドラム周期のグリッド一致は解析候補として中程度、MIDIギターの強打配置と同一マスク反復は記録上高い確度。'
            '音価と休符の強調は別の候補として併記し、聴覚アクセントとの一致は推定。']
    (out/'accent_analysis.md').write_text('\n'.join(lines)+'\n')
    lines=['# グルーヴ比較','',
           'AはStep 4-Cとバイト一致。Bは確認されたVerseの5+5+6マスクとその関連する5ステップ配置を6小節で整え、'
           'Intro/Outroの反復する拍頭外強打のうち、安全に動かせる箇所を拍頭へ寄せる。'
           'Cは各セクション冒頭1〜2小節とIntro/Outroを保持し、Verseの12・18・20小節へ調整を限定する。','',
           '全候補で根音、和音構成、velocity、各音価、発音数を保持。'
           'Verseは移動先に既存キックがある場合それを利用し、ない場合に対応キックを同じtick差だけ移動。スネアは全イベント一致。'
           'Chorus・Breakdown・区間境界の両側の音とOutro終止は保護。','',
           '|候補|発音移動数|休符区間の対称差|音価変更|velocity変更|',
           '|---|---:|---:|---:|---:|']
    for s in stats:
        lines.append(f'|{s["candidate"]}|{s["moved_attacks"]}|{s["rest_interval_difference"]}|0|0|')
    lines+=['','休符配置の差は変更前後で片側だけに存在する区間数。ギター移動数とは異なる。'
            'Verseの小節線をまたぐ5+5+6検出数はAが2回、Bが0回、Cが1回。'
            'Bはこの2回と関連マスクを整え、Cは冒頭モチーフの1回を残す。','']
    for s in stats:
        lines += [f'## {s["candidate"]}','',
                  '|小節|旧16分位置|新16分位置|理由|','|---|---:|---:|---|']
        for c in s['changes']:
            lines.append(f'|{c["bar"]}|{c["old_tick"]%BAR/120:g}|{c["new_tick"]%BAR/120:g}|{c["reason"]}|')
        lines+=['','|区間|強打→キック|小節頭強打|','|---|---:|---:|']
        for a,(_,first,last) in zip(s['sections'],SECTIONS):
            lines.append(f'|{a["section"]}|{a["kick_matches"]}/{a["strong"]}|{a["downbeat_accents"]}/{last-first}|')
    lines+=['','各楽器のNote On/Off差分はverification.jsonに全件保存。'
            'ギターとベースは移動後も発音・音価・停止・velocityと−24半音追従が一致。'
            '32分連打のオンセットと同一根音、Chorusの長音、Step 4の接続イベント、Outro末尾は一致。'
            'Bは広い範囲を整え、Cは同じ調整を選択したフレーズ終端に限定して個性を残す。'
            '数値だけで改善を判定せず、試聴による採用を待つ。','']
    (out/'comparison_report.md').write_text('\n'.join(lines))
    (out/'README.md').write_text('# グルーヴ試聴\n\n'+ '\n'.join(
        f'- [{name}](song_{name}.mid)' for name in NAMES)+
        '\n\n[解析](accent_analysis.md) · [比較](comparison_report.md) · [イベント差分](verification.json)\n')


if __name__=='__main__':
    build(sys.argv[1])
