"""Config-driven A/B/C song audition through the public riff-style API."""
from collections import Counter
from copy import deepcopy
from itertools import pairwise
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import mido
from midi_generator.api import generate

sys.path.insert(0,str(Path(__file__).resolve().parent))
if not (Path(__file__).resolve().parent/'create_intro_cohesion_auditions.py').exists():
    sys.path.insert(0,str(Path.cwd()/'scripts'))
from create_intro_cohesion_auditions import _helper
pitch = _helper('create_pitch_variation_auditions')
transition = _helper('create_transition_auditions')
band_io = _helper('run_reference_band_demo')

BAR, TPB = 1920, 480
NAMES = ('A_direct','B_motif_unified','C_refined_transitions')


def notes_from_groups(groups):
    return [(g['start'],g['end'],g['root']+interval,g['velocity'],1)
            for g in groups for interval in g['intervals']]


def write_guitar(groups,path,bars,bpm):
    return band_io.write_part(path,'Arranged Guitar',notes_from_groups(groups),
                              bars*BAR,mido.bpm2tempo(bpm),program=31,channel=1)


def group_rows(path):
    return [{key:deepcopy(g[key]) for key in ('start','end','root','intervals','velocity','channel')}
            for g in pitch.groups(mido.MidiFile(path))[0]]


def finish(groups,bars):
    rows=deepcopy(groups)
    last=rows[-1]
    last.update(root=52,velocity=110,end=min(bars*BAR,last['start']+240))
    return rows


def develop_verse(groups):
    rows=deepcopy(groups)
    # A shared eight-bar response development, including an audible held
    # restart, prevents a 16-bar section from being a copied eight-bar loop.
    target=next(g for g in rows if g['start']//BAR==12 and g['velocity']>=100)
    index=rows.index(target)
    stop=rows[index+1]['start'] if index+1<len(rows) else 16*BAR
    target.update(root=55,velocity=114,end=min(target['start']+360,stop))
    return rows


def unify(groups,donor,section,bars):
    if section=='intro':
        return deepcopy(groups)
    donor_by_position={g['start']:g for g in donor if g['start']<2*BAR}
    positions=sorted(donor_by_position)
    rows=deepcopy(groups)
    for g in rows:
        local=g['start']%(2*BAR)
        # Only preceding donor roles in the same two-bar motif are used.
        nearest=max((t for t in positions if t<=local),default=positions[0])
        source=donor_by_position[nearest]
        g['root']=source['root']
        if source['velocity']>=100:
            g['velocity']=max(g['velocity'],108)
        if section=='chorus':
            g['root']=55 if g['start']//BAR%4 in (1,2) else 52
    # Sustain each stutter burst on one root. Interior notes keep their
    # quieter dynamics even when the donor role is a strong attack.
    if section=='breakdown':
        for before,after in zip(rows,rows[1:]):
            if after['start']-before['start']==60:
                after['root']=before['root']
                after['velocity']=92
    if section=='outro':
        # Restate the actual Intro rhythm. Burst variants replace the final
        # sustained cell of each four-bar phrase, rather than arbitrary inserts.
        rows=deepcopy(donor)
        for bar in (3,7):
            candidates=[g for g in rows if g['start']//BAR==bar]
            last=candidates[-1]
            index=rows.index(last)
            span=last['end']-last['start']
            count=min(4,span//60)
            if count>=2:
                burst=[]
                for i in range(count):
                    g=deepcopy(last)
                    g.update(start=last['start']+60*i,
                             end=last['start']+60*i+45 if i<count-1 else last['end'],
                             intervals=last['intervals'] if i==0 else (0,),
                             velocity=last['velocity'] if i==0 else 92)
                    burst.append(g)
                rows[index:index+1]=burst
        rows=finish(rows,bars)
    return rows


def transition_edits(sections,drums,specs):
    guitar=deepcopy(sections)
    percussion=deepcopy(drums)
    # Different boundary roles: breath, entry accent, held-to-stutter cutoff,
    # and unison pickup. All edits stay within the adjacent edge bars.
    last=guitar[0][-1]
    last['end']=max(last['start']+45,min(last['end']-60,8*BAR-120))
    first=guitar[2][0]
    first['velocity']=118
    # A two-hit snare fill before Chorus. Existing snare slots are checked.
    boundary_stop=specs[1]['bars']*BAR
    for tick in (boundary_stop-240,boundary_stop-120):
        if not any(n[0]<=tick<n[1] and n[2]==38 for n in percussion[1]):
            percussion[1].append((tick,tick+60,38,103 if tick%240==0 else 114,10))
    held=guitar[2][-1]
    held['end']=max(held['start']+45,min(held['end'],8*BAR-180))
    next_first=guitar[3][0]
    next_first['velocity']=116
    last=guitar[3][-1]
    stop=8*BAR
    last['end']=min(last['end'],stop-240)
    # A single muted pickup, shared by guitar and regenerated bass.
    assert last['end']<=stop-120
    guitar[3].append(dict(start=stop-120,end=stop-60,root=52,
                          intervals=(0,),velocity=110,channel=0))
    return guitar,percussion


def event_diff(before,after):
    return transition.event_difference(before,after)


def motif_metrics(groups,donor):
    a={g['start']:g for g in donor if g['start']<2*BAR}
    b={g['start']:g for g in groups if g['start']<2*BAR}
    common=a.keys()&b.keys()
    union=a.keys()|b.keys()
    def rests(rows):
        return {(x,y) for x,y in zip([0]+[g['end'] for g in rows],
                                    [g['start'] for g in rows]+[2*BAR]) if x<y}
    da=[a[t] for t in sorted(a)]
    db=[b[t] for t in sorted(b)]
    ra,rb=rests(da),rests(db)
    return dict(
        rhythm_common=len(common),rhythm_union=len(union),
        pitch_common=sum(a[t]['root']==b[t]['root'] for t in common),
        accent_common=sum((a[t]['velocity']>=100)==(b[t]['velocity']>=100) for t in common),
        rest_common=len(ra&rb),rest_union=len(ra|rb))


def metrics(groups,drum_notes,donor):
    lengths=Counter((g['end']-g['start'])/TPB for g in groups)
    end=round(max(g['start'] for g in groups)//BAR+1)*BAR
    gaps=[groups[0]['start'],end-groups[-1]['end']]
    gaps += [b['start']-a['end'] for a,b in pairwise(groups)]
    pairs=[(a,b) for a,b in pairwise(groups) if b['start']-a['start']==60]
    chord=sum(len(g['intervals'])==2 for g in groups)
    kicks=[n[0] for n in drum_notes if n[2] in (35,36)]
    accents=[g for g in groups if g['velocity']>=100]
    matching=sum(any(abs(g['start']-t)<=19 for t in kicks) for g in accents)
    return dict(groups=len(groups),power_chords=chord,ratio=chord/len(groups),
                gates=dict(sorted(lengths.items())),rest_ratio=sum(gaps)/end,
                longest_rest=max(gaps)/TPB,burst_intervals=len(pairs),
                average_gate=sum(k*v for k,v in lengths.items())/len(groups),
                kick_accent_matches=matching,accents=len(accents),
                motif=motif_metrics(groups,donor))


def build(config,output):
    if 'song_template' in config:
        return _helper('create_song_templates').build(config,output)
    specs=config['sections']
    assert sum(s['bars'] for s in specs)==48
    assert [s['style'] for s in specs]==['tight_groove','stop_and_go','chord_driven',
                                        'stutter_breakdown','groove_burst']
    out=Path(output)
    if out.exists():
        raise FileExistsError(out)
    master=json.loads(Path(config['catalog']).read_text())
    records={r['path']:r for r in master['files']}
    originals=[]
    drums=[]
    provenance=[]
    paths=[]
    offset=0
    markers=[]
    with TemporaryDirectory() as tmp:
        work=Path(tmp)
        for index,spec in enumerate(specs):
            ident=spec['id']
            source=spec['drum_source']
            if pitch.sha(source)!=spec['drum_sha256']:
                raise ValueError(f'Drum source changed: {source}')
            job=work/f'{ident}_catalog.json'
            band_io.make_job_catalog(master,records[source],job,ident)
            request=dict(instrument='drums',section=ident,tempo=config['bpm'],bars=spec['bars'],
                         seed=spec['drum_seed'],time_signature=[4,4])
            dpath=generate(request,output_path=work/f'{ident}_drums.mid',catalog_path=job).output_files[0]
            guitar=generate(dict(instrument='guitar',section=ident,tempo=config['bpm'],bars=spec['bars'],
                                  seed=spec['guitar_seed'],key='E minor',riff_style=spec['style'],
                                  sync_to_drums=str(dpath)),output_path=work/f'{ident}_guitar.mid')
            rows=group_rows(guitar.output_files[0])
            if ident=='verse':
                rows=develop_verse(rows)
            if ident=='outro':
                rows=finish(rows,spec['bars'])
            originals.append(rows)
            data,notes=band_io.read_notes(dpath)
            assert data['end_tick']==spec['bars']*BAR
            transition.validate_notes(data['midi'].tracks[0],spec['bars']*BAR)
            drums.append(notes)
            paths.append((offset,offset+spec['bars']*BAR))
            markers.append((offset,ident))
            provenance.append(dict(**spec,start_bar=offset//BAR+1,end_bar=(offset+spec['bars']*BAR)//BAR))
            offset+=spec['bars']*BAR
        donor=originals[0]
        unified=[unify(rows,donor,s['id'],s['bars']) for rows,s in zip(originals,specs)]
        refined,refined_drums=transition_edits(unified,drums,specs)
        plans=[(originals,drums),(unified,drums),(refined,refined_drums)]
        results=[]
        sections_data={}
        validation={}
        for label,(parts,percussion) in zip(NAMES,plans):
            guitar_notes=[]
            bass_notes=[]
            drum_notes=[]
            stats=[]
            local_bands=[]
            for index,(rows,notes,spec,(start,stop)) in enumerate(zip(parts,percussion,specs,paths)):
                guitar_path=work/f'{label}_{spec["id"]}_guitar.mid'
                guitar=write_guitar(rows,guitar_path,spec['bars'],config['bpm'])
                checked=pitch.groups(guitar)[0]
                transition.validate_pitch(checked)
                bass_path=generate(dict(instrument='bass',section=spec['id'],tempo=config['bpm'],bars=spec['bars'],
                     options=dict(guitar_midi=str(guitar_path),transpose_semitones=-24,program=34,midi_channel=2)),
                     output_path=work/f'{label}_{spec["id"]}_bass.mid').output_files[0]
                bass_data,bass_local=band_io.read_notes(bass_path)
                bass_groups=pitch.groups(bass_data['midi'])[0]
                assert len(checked)==len(bass_groups)
                assert all((g['start'],g['end'],g['root']-24,g['velocity'])==
                           (b['start'],b['end'],b['root'],b['velocity']) for g,b in zip(checked,bass_groups))
                guitar_notes += [(a+start,b+start,n,v,ch) for a,b,n,v,ch in notes_from_groups(rows)]
                bass_notes += [(a+start,b+start,n,v,ch) for a,b,n,v,ch in bass_local]
                drum_notes += [(a+start,b+start,n,v,ch) for a,b,n,v,ch in notes]
                stats.append(metrics(rows,notes,donor))
                drum_local=band_io.write_part(work/f'{label}_{spec["id"]}_drums.mid','Drums',notes,
                                              spec['bars']*BAR,mido.bpm2tempo(config['bpm']))
                local=mido.MidiFile(type=1,ticks_per_beat=TPB)
                local.tracks.extend([guitar.tracks[0],bass_data['midi'].tracks[0],drum_local.tracks[0]])
                local_bands.append(local)
            all_parts=[]
            for name,notes,program,channel in [('Guitar',guitar_notes,31,1),('Bass',bass_notes,34,2),
                                                ('Drums',drum_notes,None,None)]:
                part=band_io.write_part(work/f'{label}_{name}.mid',name,notes,offset,
                                       mido.bpm2tempo(config['bpm']),markers,program=program,channel=channel)
                transition.validate_notes(part.tracks[0],offset)
                all_parts.append(part)
            band=mido.MidiFile(type=1,ticks_per_beat=TPB)
            band.tracks.extend([p.tracks[0] for p in all_parts])
            for track in band.tracks:
                events=transition.absolute(track)
                assert events[-1][0]==48*BAR
                assert [(t,m.tempo) for t,m in events if m.type=='set_tempo']==[(0,480000)]
                assert [(t,m.numerator,m.denominator) for t,m in events if m.type=='time_signature']==[(0,4,4)]
                assert [(t,m.text) for t,m in events if m.type=='marker']==markers
            results.append(band)
            sections_data[label]=local_bands
            validation[label]=stats
        assert results[0].tracks[2]==results[1].tracks[2]
        diff=event_diff(results[1],results[2])
        boundaries=[stop for start,stop in paths[:-1]]
        assert all(any(bound-BAR<=r['tick']<bound+BAR for bound in boundaries)
                   for changes in diff.values() for side in changes.values() for r in side)
        assert any(changes['added'] or changes['removed'] for changes in diff.values())
        for bound in boundaries:
            assert any(bound-BAR<=r['tick']<bound+BAR for changes in diff.values()
                       for side in changes.values() for r in side)
        assert results[0].tracks[0] != results[1].tracks[0]
        for parts,_ in plans:
            for section_index in (3,4):
                runs=[]
                run=[]
                for g in parts[section_index]:
                    if run and g['start']-run[-1]['start']!=60:
                        if len(run)>1:
                            runs.append(run)
                        run=[]
                    run.append(g)
                if len(run)>1:
                    runs.append(run)
                assert runs
                for run in runs:
                    assert 2<=len(run)<=4 and len({g['root'] for g in run})==1
                    assert all(a['end']<b['start'] for a,b in pairwise(run))
        for parts,_ in plans:
            verse=parts[1]
            a=[(g['start'],g['end'],g['root'],g['velocity']) for g in verse if g['start']<8*BAR]
            b=[(g['start']-8*BAR,g['end']-8*BAR,g['root'],g['velocity']) for g in verse if g['start']>=8*BAR]
            assert a!=b
        for label,stats in validation.items():
            assert stats[2]['average_gate']>stats[1]['average_gate']
            assert stats[3]['burst_intervals']>0 and stats[3]['longest_rest']>=1
        out.mkdir()
        for label,band in zip(NAMES,results):
            pitch.save_new(band,out/f'song_{label}.mid')
            for spec,local in zip(specs,sections_data[label]):
                pitch.save_new(local,out/f'{spec["id"]}_{label}.mid')
        band_io.dump(out/'request.json',config)
        band_io.dump(out/'verification.json',dict(sections=provenance,metrics=validation,
                      B_to_C_event_diff=diff,source_hashes={s['drum_source']:s['drum_sha256'] for s in specs}))
        write_report(out,provenance,validation,diff)
    return results


def write_report(out,sections,validation,diff):
    lines=['# Phase 10 Step 4 48小節アレンジ比較','',
           '125 BPM、E minor、4/4。ギターとベースは全発音・停止・velocityで一致し、ベースは根音−24半音のみ。'
           '公開スタイルAPIとドラム・ベースAPIを使用。ドラムの区間タグは一時カタログのアレンジ役割指定。','',
           '|区間|小節|スタイル|ギターseed|ドラムseed|','|---|---|---|---:|---:|']
    for s in sections:
        lines.append(f'|{s["id"]}|{s["start_bar"]}–{s["end_bar"]}|{s["style"]}|{s["guitar_seed"]}|{s["drum_seed"]}|')
    lines+=['','基本seed 401にセクション番号を加算してギターseedを分離。ドラムseedは全区間401。'
            'A/B/Cは生成済みの同じ元リフを使用し、比較ごとの再抽選は行わない。','',
            'Aはスタイル素材を配置し、Verse後半の一音に根音・音価・アクセントの展開、Outro末尾にEの終止を追加。'
            'BはIntro冒頭2小節の先行する打点の音程と強打役割を他区間に対応付ける。'
            'リズムが違う区間へイベント番号で転写せず、同一2小節内の拍位置を使用。'
            'ChorusはE5/G5のコード配置を保持し、Introの強打役割を反映。E5/G5は根音と5度のMIDI表現。'
            'OutroはIntroの打点を再提示し、4・8小節の終端持続を32分連打に変奏する。','',
            '|候補|区間|発音|パワーコード|音価（拍: 件数）|休符率|最長休符（拍）|32分間隔数|平均音価|強打→キック|',
            '|---|---|---:|---:|---|---:|---:|---:|---:|---:|']
    for label,stats in validation.items():
        for section,m in zip(sections,stats):
            lines.append(f'|{label[0]}|{section["id"]}|{m["groups"]}|{m["ratio"]:.1%}|{m["gates"]}|'
                         f'{m["rest_ratio"]:.1%}|{m["longest_rest"]:g}|{m["burst_intervals"]}|'
                         f'{m["average_gate"]:.3f}|{m["kick_accent_matches"]}/{m["accents"]}|')
    lines+=['','## Intro冒頭モチーフとの関連','',
            '各区間の冒頭2小節をIntro冒頭2小節と比較。リズム・休符区間・共通位置の根音・強打役割を分ける。'
            '異なるスタイルでは一致の低さがそのまま品質の低さを示すわけではない。','',
            '|候補|区間|リズム一致/和集合|休符一致/和集合|共通位置の根音一致|共通位置の強打役割一致|',
            '|---|---|---:|---:|---:|---:|']
    for label,stats in validation.items():
        for section,m in zip(sections,stats):
            d=m['motif']
            lines.append(f'|{label[0]}|{section["id"]}|{d["rhythm_common"]}/{d["rhythm_union"]}|'
                         f'{d["rest_common"]}/{d["rest_union"]}|{d["pitch_common"]}/{d["rhythm_common"]}|'
                         f'{d["accent_common"]}/{d["rhythm_common"]}|')
    lines+=['','## B→C 接続差','',
            '- 8→9小節。Intro最後の音を境界0.25拍前までに切り、Verseへの呼吸を作る。',
            '- 24→25小節。Verse末尾に既存スネアと重ならない最大2打を足し、Chorus先頭を強調。',
            '- 32→33小節。Chorus末尾を早く離し、Breakdownの連打先頭を強調。',
            '- 40→41小節。Breakdown末尾を切り、境界0.25拍前に単音の先行アタックを追加。',
            '各調整はギターと再生成ベースで同期。無条件のクラッシュ追加は行わない。'
            '変更イベントの全件はverification.jsonのB_to_C_event_diffを参照。'
            'その他の音楽イベントの一致を絶対tickで検証。','',
            '## 残る確認事項','',
            '- モチーフの拍位置写像は実験側の明示的アレンジで、正式な自動作曲APIへの追加は今回の対象外。',
            '- ChorusのE5/G5は2種類の根音。和声展開や開放感は音源での試聴判断が必要。',
            '- 共通音域はギター52–66、ベース28–35の実験範囲。実音源固有の演奏可能性は未検証。','']
    (out/'comparison_report.md').write_text('\n'.join(lines)+'\n')
    (out/'README.md').write_text('# 48小節アレンジ試聴\n\n'+ '\n'.join(
        f'- [{label}](song_{label}.mid)' for label in NAMES)+
        '\n\n[比較レポート](comparison_report.md)と[検証・イベント差分](verification.json)。'
        '区間単体MIDIはintro / verse / chorus / breakdown / outroの接頭辞。\n')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config')
    parser.add_argument('output')
    args=parser.parse_args()
    build(json.loads(Path(args.config).read_text()),args.output)
