"""Fixed-guitar/original-drum audition; original mix phase is a separate hypothesis."""
import argparse
from collections import Counter
import hashlib,json
from pathlib import Path
import subprocess
import time
import mido

from midi_generator.core.midi_io import load_midi
from midi_generator.modules.guitar.reference_composition import midi_bytes
from midi_generator.modules.drums.reference_drum_sync import compare_reference_phases,compose_original_drums,analyze_eighth_positions
from midi_generator.reference_analysis.audio_loader import audio_dependencies


def build(config,*,output,renderer=None):
    started=time.monotonic();prior=Path(config['step2_directory']);out=Path(output)
    if out.exists():raise FileExistsError('Refuse existing output directory')
    groove=json.loads((prior/'groove_map_8bars.json').read_text());source=Path(config['input'])
    if hashlib.sha256(source.read_bytes()).hexdigest()!=groove['source']['sha256']:raise ValueError('Original MP3 hash differs')
    if config['bpm']!=groove['section']['bpm']:raise ValueError('Tempo context differs')
    out.mkdir(parents=True,exist_ok=False)
    def write(name,data):
        with (out/name).open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
    phases=compare_reference_phases(groove);write('grid_alignment_analysis.json',phases)
    allsettings={};guitars={};source_hashes={};drum_mids={}
    # D first, so the priority audition is produced first.
    for p,name in [('d','breakdown'),('c','melodic')]:
        path=prior/f'pattern_{p}_{name}_8bars.mid';guitar=load_midi(path);guitars[p]=guitar;source_hashes[p]=hashlib.sha256(path.read_bytes()).hexdigest()
        drums,band,meta=compose_original_drums(guitar,groove,profile=p,mapping=config['mapping'],seed=config['seed']);drum_mids[p]=drums;allsettings[p]=meta
        assert midi_bytes(drums)==midi_bytes(compose_original_drums(guitar,groove,profile=p,mapping=config['mapping'],seed=config['seed'])[0])
        assert [m.dict() for m in band.tracks[0]]==[m.dict() for m in guitar.tracks[0]]
        for filename,mid in [(f'pattern_{p}_drums_8bars.mid',drums),(f'pattern_{p}_drums_guitar_8bars.mid',band)]:
            with (out/filename).open('xb') as f:f.write(midi_bytes(mid))
    missing=analyze_eighth_positions(guitars['d'],allsettings['d'],bpm=config['bpm'])
    np,sf,_=audio_dependencies();sr=groove['source']['sample_rate'];s=groove['section'];start=round(s['start_seconds']*sr);end=round(s['end_seconds']*sr)
    with sf.SoundFile(source) as f:f.seek(start);reference=f.read(end-start,always_2d=True,dtype='float32')
    original_step2,sr2=sf.read(prior/'audio/reference_excerpt_8bars.wav',always_2d=True,dtype='float32');assert sr2==sr and np.array_equal(reference,original_step2)
    audio=out/'audio';audio.mkdir();sf.write(audio/'reference_excerpt_8bars.wav',reference,sr,subtype='FLOAT')
    musical_end=mido.bpm2tempo(config['bpm'])/1e6*32;duration=len(reference)/sr+config['tail_seconds'];render_info={'status':'unavailable','music_end_seconds':musical_end,'reference_duration_seconds':len(reference)/sr,'tail_seconds':config['tail_seconds'],'arbitrary_timing_shift_seconds':0,'original_drum_sync':'unverified_no_isolated_original_drums'}
    if renderer:
        if not Path(renderer).is_file():raise FileNotFoundError('Existing/native renderer not built')
        rendered={}
        for p in ['d','c']:
            for mode in ['drums','guitar','band']:
                midpath=out/f'pattern_{p}_drums_8bars.mid' if mode=='drums' else prior/f"pattern_{p}_{'melodic' if p=='c' else 'breakdown'}_8bars.mid" if mode=='guitar' else out/f'pattern_{p}_drums_guitar_8bars.mid'
                filename=f'pattern_{p}_drums_guitar_8bars.wav' if mode=='band' else f'pattern_{p}_{mode}_8bars.wav';dest=audio/filename
                proc=subprocess.run([str(renderer),str(midpath),str(dest),str(duration),mode],capture_output=True,text=True)
                if proc.returncode:raise RuntimeError(proc.stderr)
                y,rsr=sf.read(dest,dtype='float32',always_2d=True)
                if rsr!=sr or len(y)!=round(duration*sr) or not np.isfinite(y).all() or abs(y).max()<1e-6:raise ValueError('Invalid or silent rendered sound')
                rendered[(p,mode)]=y
        fade_samples=round(config.get('tail_fade_seconds',.1)*sr)
        if not 0<=fade_samples<=round(config['tail_seconds']*sr):raise ValueError('Fade must fit only inside post-music tail')
        if fade_samples:
            ramp=np.linspace(1.,0.,fade_samples,dtype='float32')[:,None]
            for y in rendered.values():y[-fade_samples:]*=ramp
        scale=min(1.,.95/max(float(abs(y).max()) for y in rendered.values()))
        for (p,mode),y in rendered.items():
            name=f'pattern_{p}_drums_guitar_8bars.wav' if mode=='band' else f'pattern_{p}_{mode}_8bars.wav';sf.write(audio/name,y*scale,sr,subtype='FLOAT')
        sound={f'{p}_{mode}':dict(peak=float(abs(y*scale).max()),rms=float(np.sqrt(np.mean((y*scale)**2))),frames=len(y),sample_rate=sr,last_20ms_rms=float(np.sqrt(np.mean((y[-882:]*scale)**2)))) for (p,mode),y in rendered.items()}
        render_info.update(status='rendered',engine='AVAudioEngine + AVAudioSequencer, two AVAudioUnitSamplers, existing system DLS',sampler='GM31 melodic guitar; GM standard percussion kit bank',common_mixer_volume=.5,common_post_gain=scale,tail_fade_seconds=config.get('tail_fade_seconds',.1),stems=sound,frames=round(duration*sr),sample_rate=sr)
        # Audio confirmation of MIDI attacks, not instrument identification from original audio.
        dy=rendered[('d','guitar')]*scale
        for row in missing:
            if row['guitar_attack']:
                i=round(row['seconds']*sr);clip=dy[i:min(len(dy),i+round(.035*sr))];pre=dy[max(0,i-round(.02*sr)):i]
                pre_rms=float(np.sqrt(np.mean(pre**2))) if len(pre) else 0.;post_rms=float(np.sqrt(np.mean(clip**2)))
                row['rendered_guitar_attack_evidence']=dict(peak_first_35ms=float(abs(clip).max()),rms_first_35ms=post_rms,pre_20ms_rms=pre_rms,post_to_pre_db=float(20*np.log10(max(post_rms,1e-12)/max(pre_rms,1e-12))),signal_present=bool(abs(clip).max()>1e-5),meaning='Known MIDI onset has rendered signal; threshold window alone is not a precise attack-time estimate.')
        # Compare band to sum of independently rendered same-clock stems; don't demand
        # byte equality from a synthesizer with voice state/phase.
        for p in ['d','c']:
            a=rendered[(p,'band')];b=rendered[(p,'guitar')]+rendered[(p,'drums')]
            render_info.setdefault('band_vs_sum_of_stems',{})[p]=dict(normalized_correlation=float(np.clip(np.sum(a.astype('float64')*b)/max(float(np.linalg.norm(a.astype('float64'))*np.linalg.norm(b.astype('float64'))),1e-12),-1,1)),rms_difference=float(np.sqrt(np.mean((a-b)**2))),not_a_requirement_for_bit_exact_native_synth=True)
    write('render_validation.json',render_info);write('pattern_d_missing_attack_analysis.json',missing)
    write('drum_generation_settings.json',dict(config=config,profiles=allsettings,guitar_source_sha256=source_hashes,
        source_audio=groove['source'],music_end_seconds=musical_end,classification='Original composed support drums, not The Grey drum transcription',guitar_modifications=0))
    glines=['# 原曲グリッドの再検証','','C起点と原曲ドラムの実小節頭は同じ意味ではない。既存groove_mapの全raw観測（グリッド採用外も含む）を使い、前後4小節別・帯域別に位相を比較した。元MP3ハッシュと抜粋サンプルを再確認。','', '|区間|帯域|分割|Cでの平均絶対残差（拍）|最良の微小位相（拍）|最良残差（拍）|','|---|---|---|---:|---:|---:|']
    for r in phases['phase_comparisons']:
        if r['best']:glines.append(f"|{r['bars']}|{r['band']}|{'8分' if r['spacing_beats']==.5 else '16分'}|{r['at_C']['mean_absolute_residual_beats']:.4f}|{r['best']['phase_beats']:+.4f}|{r['best']['mean_absolute_residual_beats']:.4f}|")
    glines+=['','微小位相は分割幅内の周期的な最良値で、小節頭の確定ではない。4つの四分音符単位の小節頭候補とlow/high帯域のdownbeat・2/4拍・half-time3拍の支持率はgrid_alignment_analysis.jsonに保存。高域はスネアだけでなくギター・シンバル等を含み、2/4拍やhalf-timeの原曲スネア配置を断定できない。低域もキック確定ではない。前後区間・帯域で最適位相が異なること自体をテンポ変化やドラムのズレと断定しない。','', 'Cはユーザー採用の作業基準として維持し、解析値に合わせて既存ギターや原音を移動していない。生成バンドの内部同期は共通MIDIテンポ・同一シーケンサーで検証するが、原曲ドラムと完全一致するという意味ではない。']
    (out/'grid_alignment_report.md').write_text('\n'.join(glines)+'\n')
    table=['# Dの1・3・5・7位置の解析','','ここでは8分グリッドの奇数位置＝四分音符の0/1/2/3拍として扱う。ユーザーの指摘と同じ位置かは試聴確認待ち。全8分位置64件をJSONへ、注目32件を以下へ保存。','', '|小節|8分位置|ギターAttack / 持続|元のOn秒・音価拍・velocity|Kick|他ドラム|解釈|','|---|---:|---|---|---|---|---|']
    for row in missing:
        if not row['odd_position_focus']:continue
        ending='; '.join(f"終了 {g['start']/480*60/config['bpm']:.4f}s / {(g['end']-g['start'])/480:.5f} / {g['velocity']}" for g in row['guitar_ending_here'])
        details='; '.join(f"{g['start']/480*60/config['bpm']:.4f}s / {(g['end']-g['start'])/480:.5f} / {g['velocity']}" for g in row['guitar_groups']) or ending or 'なし'
        table.append(f"|{row['bar']}|{row['eighth_position']}|{row['guitar_attack']} / {row['guitar_sustain']}|{details}|{row['kick']}|{','.join(row['other_drum_notes'])}|{row['rest_interpretation']}|")
    focused=[r for r in missing if r['odd_position_focus']];count=sum(r['guitar_attack'] for r in focused)
    table+=['',f'注目32位置中、ギターの新規アタックは{count}、新規アタックなしは{32-count}。後者をMIDIの破損やレンダー欠落と扱わない。', '', '1・3・5小節の位置5では、1.75拍に始まったギターがちょうど2拍で終了し、位置5に新しいアタックはない。これは新規ノートの欠落ではなく、前の刻みのNote Offである。', '', 'Dは元のスタイルで偶数小節の先頭が120 tick＝0.25拍＝約157.895 ms遅れた位置に置かれている。1/3/5/7がすべて四分音符刻みになる設計ではない。通常の休符ではドラムの四分ハットとhalf-timeスネアを残して拍を示し、7小節目の拡大ブレイクではドラムも停止する。ギターへは一切ノートを追加・移動していない。', '', '修正候補（今回は未実施）：四分拍すべてにギターを期待する場合は、意図的な休符・16分後ろの開始を再評価する必要がある。ただしドラム追加で欠落感が解消するならギター修正は不要。微小位相の再推定やWAVの先頭遅延と、元の作曲上の休符は分けて判断する。']
    (out/'pattern_d_missing_attack_analysis.md').write_text('\n'.join(table)+'\n')
    (out/'README.md').write_text('''# Phase 12 Step 3 試聴

最優先：audio/pattern_d_drums_guitar_8bars.wav。
Dドラム・Dギター単体と聞き比べ、四分ハット／half-timeスネアがあると欠落感が変わるか確認する。
次にaudio/pattern_c_drums_guitar_8bars.wav。Cの2小節コード変化と持続をバックビートが支えるか確認する。
ギターMIDIはStep 2から無変更。生成ドラムはオリジナル作曲で、原曲の採譜・分離ではない。
原音はaudio/reference_excerpt_8bars.wav。原曲ドラムの完全一致は未検証。
楽曲の終端32拍と末尾余韻の長さはrender_validation.json参照。
レンダラーなしで実行した場合、WAVは存在するものだけを成果物として扱う。
ここで停止し、ユーザー試聴を待つ。
''')
    validation=dict(deterministic_drums=True,guitar_track_messages_preserved=True,guitar_source_hash_unchanged=all(hashlib.sha256((prior/f"pattern_{p}_{'melodic' if p=='c' else 'breakdown'}_8bars.mid").read_bytes()).hexdigest()==h for p,h in source_hashes.items()),d_focus_positions_with_attack=count,d_focus_positions_without_attack=32-count,original_drum_alignment_verified=False,elapsed_build_seconds=time.monotonic()-started)
    write('validation.json',validation);(out/'validation_report.md').write_text('# 検証\n\nvalidation.jsonとrender_validation.json参照。回帰、ドラム音源プローブ、原曲グリッド結果との区別を完了時に追記。\n')
    return validation


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=Path('config/reference_drum_sync.json'));p.add_argument('--output',default='output/phase12_step3_drum_guitar_sync');p.add_argument('--renderer',type=Path);a=p.parse_args();print(json.dumps(build(json.loads(a.config.read_text()),output=a.output,renderer=a.renderer),ensure_ascii=False))
if __name__=='__main__':main()
