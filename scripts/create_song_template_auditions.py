from pathlib import Path
import json,sys
import mido
sys.path.insert(0,str(Path(__file__).resolve().parent))
from create_song_templates import build,pitch,transition,band_io
TEMPLATES=('standard','double_chorus','breakdown_focus')
def run(output):
    out=Path(output)
    if out.exists():
        raise FileExistsError(out)
    out.mkdir()
    table=[]
    details=[]
    base=Path('output/phase10_step5_accent_grouping/song_C_controlled_syncopation.mid')
    for letter,template in zip('ABC',TEMPLATES):
        for ending in ('original','stop_final_hit'):
            config=dict(bpm=125,key='E',scale='minor',seed=42,
                        song_template=template,ending_style=ending)
            name=f'song_{letter}_{template}_{ending}'
            band=build(config,out/f'{name}_work')
            pitch.save_new(band,out/f'{name}.mid')
            m=json.loads((out/f'{name}_work'/'metadata.json').read_text())
            (out/f'{name}_work'/'song.mid').unlink()
            if template=='standard' and ending=='original':
                assert pitch.midi_bytes(band)==base.read_bytes(),'Standard differs from accepted Step 5-C'
            if template=='standard' and ending=='stop_final_hit':
                expected=Path('output/phase10_step6_outro_endings/song_C_stop_final_hit.mid')
                assert pitch.midi_bytes(band)==expected.read_bytes(),'Standard ending differs from Step 6-C'
            table.append(f'|[{name}]({name}.mid)|{m["total_bars"]}|{m["duration_seconds"]:.2f}秒|{ending}|')
            for s in m['sections']:
                details.append(f'|{letter}/{ending}|{s["id"]}|{s["start_bar"]}–{s["end_bar"]}|{s["style"]}|{s["groups"]}|')
            details.append('')
    report=['# 構成テンプレート比較','',
            '125 BPM、E minor、4/4、設定seed 42。Standardは採用基準を再生成し、'
            'originalはStep 5-C／Step 6-A、stop_final_hitはStep 6-Cとバイト一致。'
            '基準MIDIを生成入力に使わず、比較の読み取りにだけ用いる。','',
            '|試聴|総小節|MIDI演奏時間|終止|','|---|---:|---:|---|',*table,'',
            '種の分離は基本seed+359をドラム、同値から+0〜4を基本セクションのギターに使用。'
            'seed42で採用時の401〜405を再現する。追加Verse 2は+364、追加stop_and_go Outroは+365。'
            '同じChorusは同じ生成済み素材を共有し、接続処理だけ区間の役割に応じて変える。','',
            '|構成/終止|区間|小節|スタイル|ギター発音数|','|---|---|---|---|---:|',*details,
            '## モチーフと接続','',
            'Standardは採用済みのモチーフ継承、Controlled Syncopation、接続を再生成。'
            'Double Chorusは同じChorusを2回提示し、間にIntro由来のtight_grooveを配置。'
            'Breakdown FocusはIntro由来groove_burstにBreakdown 1の強い再開位置を対応づけ、'
            '小休止とコード強打を共有する。終端はstop_and_goのモチーフ再解釈。'
            '追加セクションへのアクセント最適化は行わず、既存の規則を使う。','',
            'stop_final_hitは曲の総tickから最終小節を求める。全楽器が最終小節の1〜2拍目（0始まり）で停止し、'
            '2拍目にE5（MIDI52/59）、ベース28、キック36を同時発音する。'
            'ギター・ベースは1.5拍保持し、48小節または56小節末まで0.5拍を残す。'
            'originalは生成したモチーフの終端を保ち、E根音で締める。','',
            '## 制約','',
            '- 現段階の楽曲構成APIはscripts/create_song_arrangement.pyの設定入口。公開の各楽器APIを内部で利用する。',
            '- 125 BPM・E minor・4/4に対応。素材はfoundation_configで明示し、一時カタログで役割指定。',
            '- 音源の余韻、追加セクションの自然さ、同じChorusの再登場の聴感はユーザー試聴で判断する。','']
    (out/'comparison_report.md').write_text('\n'.join(report)+'\n')
    (out/'README.md').write_text('# 構成テンプレート試聴\n\n'+'\n'.join(
        f'- [{p.stem}]({p.name})' for p in sorted(out.glob('song_*.mid')))+
        '\n\n[比較レポート](comparison_report.md)\n')
if __name__=='__main__':
    run(sys.argv[1])
