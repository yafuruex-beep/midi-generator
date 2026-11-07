"""Develop approved Phase 12 C/D only, retaining Step 1 assets verbatim."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time

from midi_generator.modules.guitar.reference_development import extend_reference_groove,develop_reference_eight_bars
from midi_generator.modules.guitar.reference_composition import midi_bytes
from midi_generator.reference_analysis.audio_loader import audio_dependencies
from create_reference_audio_compositions import metrics

NAMES={'c':'melodic','d':'breakdown'}


def build(config, *, output, renderer=None):
    started=time.monotonic();out=Path(output)
    if out.exists():raise FileExistsError('Refuse an existing output directory')
    source=Path(config['input']);prior=Path(config['step1_directory']);first=json.loads((prior/'groove_map.json').read_text());settings=json.loads((prior/'generation_settings.json').read_text())
    if config['seed']!=settings['seed']:raise ValueError('Seed must identify the saved approved Step 1 motif')
    if config.get('drum_source') is not None:raise ValueError('New drum source requires separate provenance/offset validation; not silently consumed')
    if config['key']!='B minor' or config['bpm']!=first['section']['bpm'] or config['bar_origin_seconds']!=first['section']['bar_origin_seconds']:raise ValueError('Configuration differs from accepted context')
    groove=extend_reference_groove(first,source);s=groove['section'];out.mkdir(parents=True,exist_ok=False)
    def write(name,value):
        with (out/name).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
    write('groove_map_8bars.json',groove);meta={};comparison={}
    for p,name in NAMES.items():
        mid,m=develop_reference_eight_bars(groove,settings,pattern=p);data=midi_bytes(mid)
        assert data==midi_bytes(develop_reference_eight_bars(groove,settings,pattern=p)[0])
        with (out/f'pattern_{p}_{name}_8bars.mid').open('xb') as f:f.write(data)
        m['midi_sha256']=hashlib.sha256(data).hexdigest();meta[p]=m
        write(f'pattern_{p}_harmony.json',dict(harmony_blocks=m['harmony_blocks'],key=m['key'],non_diatonic=m['non_diatonic'],groups=m['groups']))
        adapter=dict(groups=m['groups'],audio_anchor_group_count=sum(bool(g['audio_observation_ids']) for g in m['groups']),pitch_range=m['note_range'])
        before=metrics(settings['patterns'][p],first);after=metrics(adapter,groove)
        # G/B and A5/B are multi-note colors, not plain root+fifth power chords.
        after['multi_note_group_count']=after['power_chord_count']
        strict=sum(tuple(n-min(g['notes']) for n in sorted(g['notes'])) in ((0,7),(0,7,12)) for g in m['groups'])
        after['power_chord_count']=strict;after['power_chord_ratio']=strict/after['group_count']
        after['voicing_count_definition']='Power count is strict root+7(/+12); inversion/pedal colors counted separately as multi-note groups.'
        before['note_count']=sum(len(g['notes']) for g in settings['patterns'][p]['groups'])
        after['note_count']=sum(len(g['notes']) for g in m['groups'])
        before['groups_per_bar']=before['group_count']/4;after['groups_per_bar']=after['group_count']/8
        before['duration_statistics_unit']=after['duration_statistics_unit']='attack_group'
        before['mean_duration_beats']=sum((g['end']-g['start'])/480 for g in settings['patterns'][p]['groups'])/before['group_count']
        after['mean_duration_beats']=sum((g['end']-g['start'])/480 for g in m['groups'])/after['group_count']
        comparison[p]=dict(step1=before,step2=after)
    write('generation_settings.json',dict(config=config,patterns=meta,step1_source_hashes={n:hashlib.sha256((prior/n).read_bytes()).hexdigest() for n in ['groove_map.json','generation_settings.json','pattern_c_melodic.mid','pattern_d_breakdown.mid']},source_hash=groove['source']['sha256']))
    write('comparison_metrics.json',comparison)
    np,sf,_=audio_dependencies();sr=groove['source']['sample_rate'];start=round(s['start_seconds']*sr);end=round(s['end_seconds']*sr)
    with sf.SoundFile(source) as f:f.seek(start);original=f.read(end-start,dtype='float32',always_2d=True)
    audio=out/'audio';audio.mkdir();sf.write(audio/'reference_excerpt_8bars.wav',original,sr,subtype='FLOAT')
    audio_info=dict(source_mode='original full mix; isolated drums unavailable',source_start_seconds=s['start_seconds'],source_end_seconds=s['end_seconds'],first_source_frame=start,last_source_frame=end,frames=len(original),sample_rate=sr,midi_local_start_beat=0,time_shift_seconds=0,drum_offset_seconds=None,drum_only_sync_evaluation='not_achieved',rendering='not_available_without_existing_renderer')
    sustain=[]
    if renderer:
        if not Path(renderer).is_file():raise FileNotFoundError('Existing renderer not found')
        renders={}
        for p,name in NAMES.items():
            dest=audio/f'pattern_{p}_guitar_8bars.wav';subprocess.run([str(renderer),str(out/f'pattern_{p}_{name}_8bars.mid'),str(dest),str(len(original)/sr)],check=True,capture_output=True)
            y,rsr=sf.read(dest,dtype='float32',always_2d=True)
            if rsr!=sr or y.shape!=original.shape or not np.isfinite(y).all() or abs(y).max()<1e-7:raise ValueError('Bad renderer output')
            renders[p]=y
        rg=min(6.,.75*float(np.sqrt(np.mean(original**2)))/max(float(np.sqrt(np.mean(y*y))) for y in renders.values()));sg=.6
        peak=max(float(abs(sg*original+rg*y).max()) for y in renders.values());scale=min(1.,.95/max(peak,1e-10))
        for p,y in renders.items():sf.write(audio/f'pattern_{p}_with_reference_8bars.wav',scale*(sg*original+rg*y),sr,subtype='FLOAT')
        audio_info.update(rendering='macOS AVAudioUnitSampler/system DLS GM Program31',reference_gain=sg*scale,guitar_gain=rg*scale,
            first_rendered_sample_over_1e_5_seconds={p:float(np.where(np.max(abs(y),axis=1)>1e-5)[0][0]/sr) for p,y in renders.items()},
            native_sound_attack_or_release_not_note_boundaries=True)
        for g in meta['c']['groups']:
            duration=(g['end']-g['start'])/480*60/config['bpm']
            if duration<1.0:continue
            sec=g['start']/480*60/config['bpm'];y=renders['c']
            early=y[round((sec+.05)*sr):round((sec+.15)*sr)];late=y[round((sec+duration*.75)*sr):round((sec+duration*.9)*sr)]
            er=float(np.sqrt(np.mean(early**2)));lr=float(np.sqrt(np.mean(late**2)))
            sustain.append(dict(group_start_tick=g['start'],midi_duration_seconds=duration,early_rms=er,late_rms=lr,late_to_early=lr/max(er,1e-10),nonzero_late_signal=lr>1e-5))
    write('audio_validation.json',dict(audio=audio_info,long_note_render_measurements=sustain,warning='Nonzero late RMS is a signal observation, not listening-quality verification.'))
    write('drum_source_status.json',dict(isolated_drums_found=False,scope='Project inputs and previous outputs inspected before run; no The Grey drum-separated audio identified',new_dependencies_added=False,installed_separation_runtime={n:importlib.util.find_spec(n) is not None for n in ['torch','demucs','onnxruntime','mlx']},reference_mix_is_not_drums=True))
    lines=['# Step 1 → Step 2 比較','','95 BPM・B minor・Cグリッド・4/4仮説。原音125.076632〜145.287158秒、8小節。前半4小節の解析セルと観測を完全再利用し、後半だけ同じ方法で解析。各4小節のMAD/最大値は独立に校正しており、強度をそのまま楽器音量として比較しない。','', '|案|発音群 旧→新|平均音価（拍）旧→新|休符率 旧→新|長音|','|---|---:|---:|---:|---|']
    for p,c in comparison.items():
        a,b=c['step1'],c['step2'];lines.append(f"|{p.upper()}|{a['group_count']}→{b['group_count']}|{a['mean_duration_beats']:.4f}→{b['mean_duration_beats']:.4f}|{a['rest_ratio']:.1%}→{b['rest_ratio']:.1%}|{b['duration_distribution_beats']}|")
    lines+=['','CはBm(1–2)→G(3–4)→A(5–6)→Bm(7–8)。冒頭和音を2拍へ伸ばすため弱い応答を1件だけ省き、2小節動機を繰り返す。その他は0.5／1拍の応答・持続を使い、全部を長音化しない。2小節の境界tickは0／3840／7680／11520。長短コード名は和声の意図で、主な和音はroot+7、単音旋律に3度・5度を配置。','', 'DはB5ペダル→G/B→A5/B（3度なし）→B5回帰。Bの短い連打・停止・再開を維持し、上声の変更は主に長い再開アクセントに限定。5〜6小節と7〜8小節のCは各0.25拍以下の一時的緊張。7小節目では再開アクセントを1件省き、最後の再開へ向けたブレイクを作る。DをCの長音型へ変更していない。','', '実際の音域：CはMIDI '+str(meta['c']['note_range'])+'、Dは'+str(meta['d']['note_range'])+'。ギターチューニング・音源固有の奏法は未確定。Drop Bと断定せず、既存Phase 10の音域・既定値は変更しない。','', '## 同期と制約','','ドラム分離音源は未確認／未発見。with_referenceは原曲全体（原曲ギター・ベース等も含む）との参考ミックスであり、with_drumsファイルは作成していない。ドラム単体との同期評価は未達。音源の時刻・フレーム数、ローカルMIDI開始、レンダーの先頭応答、ミックス長をaudio_validation.jsonに記録。恣意的なMIDI移動や原音のタイミング変更はない。','', '分離済みドラムを使うには、原曲と同じ時間軸のstem、または信頼できる開始オフセット情報が必要。新規分離を実施する場合は、既存調査output/phase11_step2b3_the_grey/source_separation_recommendation.mdにある隔離環境・分離ランナー・モデル重みの導入判断が先になる。現環境ではtorch/demucs/onnxruntime/mlxが未導入。実行速度・分離品質・互換性は未測定で、今回インストールやモデル実験は行わない。','', '静的指標やレンダー末尾RMSだけで自然さ・重さが改善したとは断定しない。まず単体でCの持続と2小節進行、Dの停止・再開・上声変化を確認し、その後原曲ミックスで拍感と和声の衝突を確認する。原曲の既存和声と新しいコード進行が衝突する可能性がある。']
    (out/'pattern_comparison.md').write_text('\n'.join(lines)+'\n')
    (out/'README.md').write_text('''# Phase 12 Step 2 試聴案内

- C: pattern_c_melodic_8bars.mid / audio/pattern_c_guitar_8bars.wav
- D: pattern_d_breakdown_8bars.mid / audio/pattern_d_guitar_8bars.wav
- 原音: audio/reference_excerpt_8bars.wav
- 参考ミックス: audio/pattern_c_with_reference_8bars.wav / pattern_d_with_reference_8bars.wav（レンダー利用時のみ）

**ドラム単体比較は未達。原曲全体とのミックスをドラム単体とは扱わない。**

125.076632〜145.287158秒、95 BPM・B minor・Cグリッド・8小節（4/4仮説）。MIDIのローカル0拍が原音の開始時刻に対応。
Cの長い音と2小節コード進行、Dの短い連打と停止を単体で比較してから、参考ミックスを聴く。音源固有のアタック／リリースをNote On/Offと混同しない。
Step 1、A/B、Phase 10、凍結済みギター採譜は保護。今回で停止し、試聴を待つ。
''')
    validation=dict(first_four_bars_reused_exactly=groove['extension_validation'],deterministic_midi=True,source_hash_unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==groove['source']['sha256'],drum_only_comparison_achieved=False,elapsed_build_seconds=time.monotonic()-started)
    write('validation.json',validation);(out/'validation_report.md').write_text('# 検証\n\nvalidation.json、comparison_metrics.json、audio_validation.json参照。回帰テスト・ハッシュ確認は最終保存時に追記。ドラム単体との同期評価は未達として区別する。\n')
    return validation


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=Path('config/reference_eight_bar_development.json'));p.add_argument('--output',default='output/phase12_step2_eight_bar_composition');p.add_argument('--renderer',type=Path)
    a=p.parse_args();print(json.dumps(build(json.loads(a.config.read_text()),output=a.output,renderer=a.renderer),ensure_ascii=False))
if __name__=='__main__':main()
