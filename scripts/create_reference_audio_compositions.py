"""4-bar reference-audio composition audition. Run from the project root."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import time

from midi_generator.reference_analysis.groove_map import analyze_audio_groove
from midi_generator.modules.guitar.reference_composition import compose_reference_guitar, midi_bytes

NAMES={'a':'kick_lock','b':'syncopated','c':'melodic','d':'breakdown'}


def metrics(meta,groove):
    groups=meta['groups'];length=groove['section']['bars']*1920
    kick_slots={b*1920+s*120 for b,row in enumerate(groove['bars']) for s in row['kick_candidate_steps']}
    snare_slots={b*1920+s*120 for b,row in enumerate(groove['bars']) for s in row['snare_candidate_steps']}
    onsets={g['start'] for g in groups}
    durations={}
    for g in groups:
        key=str((g['end']-g['start'])/480);durations[key]=durations.get(key,0)+1
    gaps=[groups[0]['start']]+[b['start']-a['end'] for a,b in zip(groups,groups[1:])]+[length-groups[-1]['end']]
    repetition=[]
    for b in range(groove['section']['bars']-2):
        a={g['start']%1920 for g in groups if g['start']//1920==b};c={g['start']%1920 for g in groups if g['start']//1920==b+2}
        repetition.append(len(a&c)/len(a|c) if a|c else 0)
    return dict(group_count=len(groups),power_chord_count=sum(len(g['notes'])>1 for g in groups),
        power_chord_ratio=sum(len(g['notes'])>1 for g in groups)/len(groups),duration_distribution_beats=durations,
        rest_ratio=sum(gaps)/length,longest_gap_beats=max(gaps)/480,offbeat_group_fraction=sum(g['start']%480!=0 for g in groups)/len(groups),
        kick_proxy_coincidences=len(onsets&kick_slots),kick_proxy_total=len(kick_slots),snare_proxy_coincidences=len(onsets&snare_slots),
        kick_proxy_onset_fraction=len(onsets&kick_slots)/len(groups),audio_anchor_groups=meta['audio_anchor_group_count'],
        motif_onset_jaccard_bars_1_3_and_2_4=repetition,
        thirtysecond_adjacent_pairs=sum(b['start']-a['start']==60 for a,b in zip(groups,groups[1:])),
        pitch_range=meta['pitch_range'])


def build(input_path,output_path,*,reference_context,seed=42,renderer=None):
    started=time.monotonic();output=Path(output_path)
    if output.exists():raise FileExistsError(f'Refuse existing output directory: {output}')
    # Three bounded windows in the previously reviewed late region; no guitar stem consumed.
    if reference_context['key']!='B minor':raise ValueError('Composition prototype currently supports B minor only')
    bpm=reference_context['bpm'];origin=reference_context['bar_origin_seconds']
    candidates=[analyze_audio_groove(input_path,start_seconds=origin+b*240/bpm,bpm=bpm,bar_origin_seconds=origin,bars=reference_context['bars']) for b in reference_context['candidate_bar_indices']]
    for candidate in candidates:
        candidate['section']['bpm_status']='user_verified'
        candidate['section']['grid_status']='user_adopted'
    chosen=max(range(len(candidates)),key=lambda i:candidates[i]['summary']['grid_eligible_fraction']);groove=candidates[chosen]
    output.mkdir(parents=True,exist_ok=False)
    def write(name,data):
        with (output/name).open('x') as f:json.dump(data,f,indent=2,ensure_ascii=False,allow_nan=False)
    write('groove_map.json',groove)
    write('reference_audio_analysis.json',dict(candidates=[{'section':m['section'],'summary':m['summary'],'bar_similarity':m['bar_similarity']} for m in candidates],selected_index=chosen,selection_criterion='Highest near-grid band-attack fraction; not a drum-accuracy or listening-quality score.'))
    write('input_analysis.json',dict(source=groove['source'],drum_midi_required=False,drum_stem_used=False,
        input_mode='original full mix',pitch='B minor user verified, not inferred from audio',frozen_guitar_transcription_used=False,
        warnings=['No The Grey drum stem found in project inputs. Mixes include original guitar/bass/synth/vocal.']))
    allmeta={};allmetrics={};digest=hashlib.sha256(json.dumps(groove,sort_keys=True).encode()).hexdigest()
    for pattern,name in NAMES.items():
        midi,meta=compose_reference_guitar(groove,pattern=pattern,seed=seed);data=midi_bytes(midi)
        assert data==midi_bytes(compose_reference_guitar(groove,pattern=pattern,seed=seed)[0])
        with (output/f'pattern_{pattern}_{name}.mid').open('xb') as f:f.write(data)
        # These files are guitar-only, not fabricated drum+guitar MIDIs.
        meta['groove_map_sha256']=digest;meta['midi_sha256']=hashlib.sha256(data).hexdigest()
        allmeta[pattern]=meta;allmetrics[pattern]=metrics(meta,groove)
    # Counterfactual: remove audio observations while retaining the confirmed metric grid.
    # Composer must refuse rather than report a generic riff as reference-guided success.
    blank=deepcopy(groove)
    for b in blank['bars']:
        b['major_accent_steps']=[];b['kick_candidate_steps']=[]
        for c in b['cells']:c['accent_score']=0;c['observation_ids']=[]
    try:compose_reference_guitar(blank,pattern='a',seed=seed)
    except ValueError:empty_map_rejected=True
    else:raise AssertionError('No-audio map unexpectedly accepted')
    # Causal test: change the actual observed anchor positions, not merely the source name.
    shifted=deepcopy(groove)
    for b in shifted['bars']:
        old=deepcopy(b['cells'])
        for step in range(16):b['cells'][step]=dict(old[(step-2)%16],step=step,beat=step/4)
        b['major_accent_steps']=sorted((s+2)%16 for s in b['major_accent_steps']);b['kick_candidate_steps']=sorted((s+2)%16 for s in b['kick_candidate_steps'])
    causal={p:[g['start'] for g in compose_reference_guitar(shifted,pattern=p,seed=seed)[1]['groups']]!=[g['start'] for g in meta['groups']] for p,meta in allmeta.items()}
    assert all(causal.values())
    write('generation_settings.json',dict(seed=seed,bpm=bpm,key='B minor',reference_context=reference_context,groove_map_sha256=digest,patterns=allmeta,
        declared_tuning='unknown; no Drop B assumption',composition_bounds=[40,76],
        rule_provenance={'existing':['docs/RULE_BACKING.md','src/midi_generator/modules/guitar/riff_styles.py','docs/INTRO_CHORUS_TRANSITION.md'],
        'new_hypotheses':['Audio-accent anchored adaptation','B-minor harmony choices from user request','MIDI register 43..55 with optional fifths, not a validated instrument preset']},
        renderer=str(renderer) if renderer else None))
    write('composition_metrics.json',allmetrics)
    audio_status='not requested: no renderer argument';audio=output/'audio'
    from midi_generator.reference_analysis.audio_loader import audio_dependencies
    np,sf,_=audio_dependencies();source=groove['source'];s=groove['section'];sr=source['sample_rate']
    with sf.SoundFile(input_path) as f:
        first=round(s['start_seconds']*sr);last=round(s['end_seconds']*sr);f.seek(first);reference=f.read(last-first,dtype='float32',always_2d=True)
    audio.mkdir();sf.write(audio/'reference_excerpt.wav',reference,sr,subtype='FLOAT')
    if renderer:
        if not Path(renderer).is_file():raise FileNotFoundError('Renderer unavailable; no fabricated audio')
        renders={}
        for p,n in NAMES.items():
            dest=audio/f'pattern_{p}_guitar.wav';subprocess.run([str(renderer),str(output/f'pattern_{p}_{n}.mid'),str(dest),str(len(reference)/sr)],check=True,capture_output=True)
            y,rsr=sf.read(dest,always_2d=True,dtype='float32')
            if rsr!=sr or y.shape!=reference.shape or not np.isfinite(y).all() or abs(y).max()<1e-7:raise ValueError('Bad or silent renderer output')
            renders[p]=y
        rms=max(float(np.sqrt(np.mean(y*y))) for y in renders.values());ref_rms=float(np.sqrt(np.mean(reference*reference)))
        guitar_gain=min(6.,.75*ref_rms/max(rms,1e-10));source_gain=.6
        peak=max(float(abs(source_gain*reference+guitar_gain*y).max()) for y in renders.values());scale=min(1.,.95/max(peak,1e-10))
        for p,y in renders.items():sf.write(audio/f'pattern_{p}_with_reference.wav',scale*(source_gain*reference+guitar_gain*y),sr,subtype='FLOAT')
        audio_status=dict(engine='Existing macOS AVAudioUnitSampler/system DLS',reference_gain=source_gain*scale,guitar_gain=guitar_gain*scale,source='full mix, NOT isolated drums',timing_shift_seconds=0,reference_frames=len(reference),reference_first_frame=first,reference_last_frame=last)
    validation=dict(deterministic_midi=True,empty_audio_map_rejected=empty_map_rejected,changed_audio_anchors_change_rhythm=causal,
        unique_rhythm_structures=len({tuple(g['start'] for g in m['groups']) for m in allmeta.values()}),renderer=audio_status,
        input_hash_unchanged=hashlib.sha256(Path(input_path).read_bytes()).hexdigest()==source['sha256'],
        elapsed_build_seconds=time.monotonic()-started,tests='Run separately and recorded after regression checks')
    assert validation['unique_rhythm_structures']==4 and validation['input_hash_unchanged']
    write('validation.json',validation)
    (output/'section_selection.md').write_text(f"# 区間選択\n\n原音{s['start_seconds']:.9f}〜{s['end_seconds']:.9f}秒、{s['bars']}小節（4/4仮説）、{bpm} BPM、C起点{origin:.9f}秒。3候補中、帯域アタックのグリッド近接率が最大の区間。終端の減衰を含む後半側より比較的安定した前半4小節を採用。音楽的な聴取確認ではなく数値による選択。\n\n{groove['summary']['observation_count']}件の帯域検出は楽器別ノート数ではない。キック／スネア／フィル／ハーフタイムは未確認。原曲の区間にローカル0拍を対応させ、原音を移動・伸縮していない。\n")
    lines=['# 4パターン比較','', '原曲MP3の帯域別アタック候補を共通入力にしたオリジナル作曲。原曲ギター・コード進行の採譜ではない。B minorと95 BPMはユーザー指定。', '', '|案|設計|発音群|パワーコード比|音価（拍：件数）|休符率|音声アンカー群|キック様一致群|', '|---|---|---:|---:|---|---:|---:|---:|']
    descriptions={'a':'Bペダル／短音・選択した低域アクセント','b':'Bm–C–Bm／裏拍と停止・半音の緊張','c':'Bm–G–A–Bm／持続和音＋単音応答','d':'Bペダル＋短いC／連打・停止・再開'}
    for p,m in allmetrics.items():lines.append(f"|{p.upper()}|{descriptions[p]}|{m['group_count']}|{m['power_chord_ratio']:.1%}|{m['duration_distribution_beats']}|{m['rest_ratio']:.1%}|{m['audio_anchor_groups']}|{m['kick_proxy_coincidences']}|")
    lines+=['','コード名は和声設計のラベル。実際の和音は単音／root+7で3度を含まず、長短三和音を完全に鳴らしたものではない。CはB natural minor外の意図的なbII。ギター43〜57のMIDIレジスターで、Drop Bを仮定しない。正式なチューニング・音源固有の演奏可能域は未確認。', '', '## 観測・推定・作曲の区別','', 'observedは帯域の音声特徴。kick_like等の役割はinferred、楽器信頼度はlow。原音ギター・ベース・シンセが混入し得る。低信頼の楽器判定に依存せず、実際に検出した共通アクセント位置を選択し、残りを既存スタイルのモチーフで補完した。各発音群の由来と観測IDはgeneration_settings.jsonに記録。', '', '静的に確認したのは4種類のオンセット構造、B minor中心の設計、音価・停止の配置、再現性。音楽的自然さは未確認。Aのキック一致率だけを正解指標にしない。Dのハーフタイムは作曲上の意図であり原曲のハーフタイム認定ではない。', '', '## 試聴','', 'まずaudio/pattern_*_guitar.wavを単独比較し、次にpattern_*_with_reference.wavで原曲との衝突・拍の噛み合いを確認する。後者は原曲ギター・ベース等を含む参考ミックスで、原曲ドラムだけとの比較ではない。B/Cの和声は原曲進行と異なる可能性があり、ミックスでの不協和と生成ギター単体の品質を分けて評価する。GM音源の短音はパームミュートの再現保証ではなく、奏法キーは一切追加していない。','', '既存資料は固定スタイル、休符、パワーコード、モチーフ、音価コントラストの根拠。今回の音声アンカーの選択・B minor和声・音域は明示的な新規作曲仮説であり、資料から学習した最適解ではない。']
    (output/'pattern_comparison.md').write_text('\n'.join(lines)+'\n')
    (output/'validation_report.md').write_text('# 検証\n\nvalidation.json参照。4パターン同一groove_map、固定seed、MIDI再生成バイト一致、4つの異なるオンセット構造を確認。音声アンカーを移動すると全4案のオンセットが変化し、音声情報が空のマップは拒否する。一般的な95 BPMリフだけをThe Grey由来と扱わない。楽器別の正解性は未検証。回帰結果と既存保護は完了時に追記。\n')
    return validation


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=Path('config/reference_audio_composition.json'));p.add_argument('--input');p.add_argument('--output',default='output/phase12_step1_reference_audio_composition');p.add_argument('--seed',type=int);p.add_argument('--renderer',type=Path)
    args=p.parse_args();config=json.loads(args.config.read_text());print(json.dumps(build(args.input or config['input'],args.output,reference_context=config,seed=config['seed'] if args.seed is None else args.seed,renderer=args.renderer),ensure_ascii=False))
if __name__=='__main__':main()
