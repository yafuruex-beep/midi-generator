"""Create only requested D head attacks; drums and all other guitar events fixed."""
import argparse,hashlib,json,subprocess
from pathlib import Path
import mido
from midi_generator.modules.guitar.attack_fix import add_bar_head_attacks
from midi_generator.modules.guitar.reference_composition import midi_bytes
from midi_generator.reference_analysis.audio_loader import audio_dependencies


def build(config,*,output,renderer=None):
    out=Path(output)
    if out.exists():raise FileExistsError('Refuse existing output')
    source=Path(config['guitar']);bandpath=Path(config['band']);guitar=mido.MidiFile(source);band=mido.MidiFile(bandpath)
    assert [m.dict() for m in band.tracks[0]]==[m.dict() for m in guitar.tracks[0]]
    fixed,added=add_bar_head_attacks(guitar,bars=config['bars'],note=config['note'],duration_beats=config['duration_beats'])
    assert midi_bytes(fixed)==midi_bytes(add_bar_head_attacks(guitar,bars=config['bars'],note=config['note'],duration_beats=config['duration_beats'])[0])
    old_drums=[m.dict() for m in band.tracks[1]];band.tracks[0]=mido.MidiTrack([m.copy() for m in fixed.tracks[0]])
    assert [m.dict() for m in band.tracks[1]]==old_drums
    out.mkdir(parents=True,exist_ok=False)
    for filename,mid in [('pattern_d_guitar_attack_fixed.mid',fixed),('pattern_d_drums_guitar_attack_fixed.mid',band)]:
        with (out/filename).open('xb') as f:f.write(midi_bytes(mid))
    tempo=next(m.tempo for m in guitar.tracks[0] if m.type=='set_tempo')
    for a in added:
        a.update(start_seconds=mido.tick2second(a['start_tick'],480,tempo),duration_seconds=mido.tick2second(a['end_tick']-a['start_tick'],480,tempo))
    details=dict(config=config,additions=added,source_guitar_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),source_band_sha256=hashlib.sha256(bandpath.read_bytes()).hexdigest(),drums_changed=False,existing_note_edits=0)
    audio_check={}
    if renderer:
        np,sf,_=audio_dependencies();old_dir=Path(config['step3_directory']);baseline,sr=sf.read(old_dir/'audio/pattern_d_drums_guitar_8bars.wav',dtype='float32',always_2d=True);frames=len(baseline);audio=out/'audio';audio.mkdir();fade=round(.1*sr);ramp=np.linspace(1,0,fade,dtype='float32')[:,None]
        for mode,midname,name in [('guitar','pattern_d_guitar_attack_fixed.mid','pattern_d_guitar_attack_fixed.wav'),('band','pattern_d_drums_guitar_attack_fixed.mid','pattern_d_drums_guitar_attack_fixed.wav')]:
            dest=audio/name;p=subprocess.run([str(renderer),str(out/midname),str(dest),str(frames/sr),mode],capture_output=True,text=True)
            if p.returncode:raise RuntimeError(p.stderr)
            y,rsr=sf.read(dest,dtype='float32',always_2d=True);assert rsr==sr and y.shape==baseline.shape and np.isfinite(y).all()
            y[-fade:]*=ramp;sf.write(dest,y,sr,subtype='FLOAT');assert abs(y).max()<1
            old,sro=sf.read(old_dir/f"audio/pattern_d_{'guitar' if mode=='guitar' else 'drums_guitar'}_8bars.wav",dtype='float32',always_2d=True);assert sro==sr
            sf.write(audio/f"pattern_d_{'guitar' if mode=='guitar' else 'drums_guitar'}_before.wav",old,sr,subtype='FLOAT')
            # Before/after stereo comparison at identical timing and gain: no normalization.
            compare=np.column_stack([old.mean(axis=1),y.mean(axis=1)]);sf.write(audio/f'pattern_d_{mode}_before_after.wav',compare,sr,subtype='FLOAT')
            audio_check[mode]=dict(frames=frames,sample_rate=sr,peak=float(abs(y).max()),music_end_seconds=15360/480*tempo/1e6,tail_seconds=1.,timing_shift_seconds=0,gain='same native mixer 0.5 as Step3; no new normalization',attacks=[])
            for a in added:
                for role,tick in [('new_head',a['start_tick']),('existing_025',a['existing_first_attack_tick'])]:
                    i=round(mido.tick2second(tick,480,tempo)*sr);clip=y[i:i+round(.04*sr)];reference=old[i:i+round(.04*sr)]
                    audio_check[mode]['attacks'].append(dict(bar=a['bar'],role=role,tick=tick,new_peak=float(abs(clip).max()),new_rms=float(np.sqrt(np.mean(clip**2))),old_peak=float(abs(reference).max()),old_rms=float(np.sqrt(np.mean(reference**2)))))
    (out/'attack_fix_details.json').write_text(json.dumps(details,ensure_ascii=False,indent=2));(out/'audio_validation.json').write_text(json.dumps(audio_check,ensure_ascii=False,indent=2))
    lines=['# D 第2・4・6小節の頭アタック補正','','既存ギターの絶対時刻・音高・音価・velocityは無変更。追加は3音のみ。Bペダル47は各小節の既存先頭和音47/54と一致し、G/B・A5/BのBペダルとも衝突しない。チューニングは推定していない。短音はMIDI音価による表現で、音源固有のパームミュート奏法ではない。','','|小節|追加tick|音高|音価拍|音価秒|velocity|元の次On tick|','|---|---:|---:|---:|---:|---:|---:|']
    for a in added:lines.append(f"|{a['bar']}|{a['start_tick']}|{a['note']}|{a['actual_duration_beats']}|{a['duration_seconds']:.9f}|{a['velocity']}|{a['existing_first_attack_tick']}|")
    lines+=['','新規音のOffと既存0.25拍位置のOnは同tickで、Offを先に配置。元の0.25拍・0.375拍・0.5拍の刻みは保持し、既存音の短縮も行っていない。第1/3/5/7/8小節、終盤ブレイク、ドラムトラックの全メッセージを維持。95 BPM、4/4仮説、B minor、15360 tickの8小節終端を維持。','','audio/*before_after.wavは左が修正前、右が修正後。同じ時刻・音量で比較し、新規アタックと0.25拍後の元の刻みの両方が聞こえるか確認する。ドラムはStep3のままなので新規小節頭は四分ハットと合い、キックは従来の0.25拍位置を維持する。原曲との完全同期を検証したものではない。','','WAVは8小節＋1秒余韻、最後100msは同じフェード。末尾余韻以外の時刻・ゲインは移動・変更しない。音楽的な連続性と改善度はユーザー試聴待ち。']
    (out/'attack_fix_report.md').write_text('\n'.join(lines)+'\n');(out/'validation_report.md').write_text('# 検証\n\n最終MIDI差分・レンダー・回帰結果を完了時に追記。\n')
    return details


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=Path('config/d_attack_fix.json'));p.add_argument('--output',default='output/phase12_step3_1_d_attack_fix');p.add_argument('--renderer',type=Path);a=p.parse_args();print(json.dumps(build(json.loads(a.config.read_text()),output=a.output,renderer=a.renderer)['additions'],ensure_ascii=False))
if __name__=='__main__':main()
