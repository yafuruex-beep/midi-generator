"""Opt-in, source-preserving 16-bar Intro/Chorus listening experiment."""
from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import mido


def _helper(name):
    path = Path(__file__).with_name(name + '.py')
    if not path.exists():
        path = Path.cwd() / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pitch = _helper('create_pitch_variation_auditions')
intro = _helper('create_intro_cohesion_auditions')
gate = _helper('create_duration_variation_auditions')


def absolute(track):
    tick = 0
    result = []
    for message in track:
        if type(message.time) is not int or message.time < 0:
            raise ValueError('Nonnegative integer delta ticks required')
        tick += message.time
        result.append((tick, message.copy(time=0)))
    return result


def is_release(message):
    return message.type == 'note_off' or (message.type == 'note_on' and message.velocity == 0)


def schedule(events, end):
    """Serialize absolute events, placing releases before retriggers at a tick."""
    result = mido.MidiTrack()
    previous = 0
    events = [(t, m) for t, m in events if m.type != 'end_of_track']
    for _, (tick, message) in sorted(enumerate(events), key=lambda item: (
            item[1][0], 0 if is_release(item[1][1]) else 1, item[0])):
        if tick < 0 or tick > end:
            raise ValueError('Event outside sequence')
        result.append(message.copy(time=tick - previous))
        previous = tick
    result.append(mido.MetaMessage('end_of_track', time=end - previous))
    return result


def part(midi, index):
    result = mido.MidiFile(type=0, ticks_per_beat=midi.ticks_per_beat)
    result.tracks.append(deepcopy(midi.tracks[index]))
    return result


def musical(track, shift=0):
    """All non-meta events, including PC/CC, independent of serialization order."""
    return Counter((tick + shift, json.dumps(message.dict(), sort_keys=True))
                   for tick, message in absolute(track) if not message.is_meta)


def validate_notes(track, end):
    pending = {}
    for tick, message in absolute(schedule(absolute(track), end)):
        if message.type not in {'note_on', 'note_off'}:
            continue
        key = message.channel, message.note
        if is_release(message):
            if key not in pending or tick <= pending[key]:
                raise ValueError('Orphan release or nonpositive duration')
            del pending[key]
        else:
            if key in pending:
                raise ValueError('Overlapping duplicate note')
            pending[key] = tick
    if pending:
        raise ValueError('Missing Note Off')


def validate_source(midi):
    if midi.type != 1 or midi.ticks_per_beat != 480 or len(midi.tracks) != 3:
        raise ValueError('Expected source type 1, TPB 480 and guitar/bass/drum tracks')
    for index, track in enumerate(midi.tracks):
        events = absolute(track)
        if events[-1][0] != 15360:
            raise ValueError('Expected eight complete bars')
        if [(t, m.tempo) for t, m in events if m.type == 'set_tempo'] != [(0, mido.bpm2tempo(105))]:
            raise ValueError('Expected one constant 105 BPM event per source track')
        if [(t, m.numerator, m.denominator) for t, m in events if m.type == 'time_signature'] != [(0, 4, 4)]:
            raise ValueError('Expected constant 4/4')
        if {m.channel for _, m in events if hasattr(m, 'channel')} != [{0}, {1}, {9}][index]:
            raise ValueError('Unexpected source channel layout')
        if any(m.type in {'sysex','control_change','pitchwheel','aftertouch','polytouch'} for _, m in events):
            raise ValueError('Stateful controller input requires an explicit boundary policy')
        programs = [(t, m.program) for t, m in events if m.type == 'program_change']
        if programs != [[(0,30)], [(0,33)], []][index]:
            raise ValueError('Unexpected Program Change state')
        validate_notes(track, 15360)
    gs, _ = pitch.groups(part(midi, 0))
    bs, _ = pitch.groups(part(midi, 1))
    if len(gs) != len(bs) or any((g['start'],g['root']-24,g['velocity']) != (b['start'],b['root'],b['velocity']) for g,b in zip(gs,bs)):
        raise ValueError('Bass must follow guitar onsets, pitches and velocity')
    validate_pitch(gs)


def validate_pitch(groups):
    roots = [g['root'] for g in groups]
    for g in groups:
        if g['root'] not in {52,54,55,57,59} or any(not 52 <= n['pitch'] <= 66 or n['pitch'] % 12 not in {0,2,4,6,7,9,11} for n in g['members']):
            raise ValueError('Pitch outside inherited E minor/range constraints')
    if any(abs(a-b)>4 for a,b in zip(roots,roots[1:])):
        raise ValueError('Adjacent root jump exceeds four semitones')


def concatenate(first, second):
    validate_source(first)
    validate_source(second)
    result = mido.MidiFile(type=1, ticks_per_beat=480)
    for a, b in zip(first.tracks, second.tracks):
        # Preserve every channel event (including the same PC at the boundary).
        # Suppress duplicate global tempo/meter; remove source-only SMPTE offset
        # and the drum track's C key marker rather than mislabel the E-minor song.
        remove = {'end_of_track','smpte_offset','key_signature'}
        events = [(t,m) for t,m in absolute(a) if m.type not in remove]
        events += [(t+15360,m) for t,m in absolute(b) if m.type not in remove | {'set_tempo','time_signature','track_name'}]
        result.tracks.append(schedule(events,30720))
        assert musical(result.tracks[-1]) == musical(a) + musical(b,15360)
    return result


def retune_bass(bass, guitar):
    """Pitch-only A2 following, retaining the inherited independent bass gates."""
    gs,_ = pitch.groups(guitar)
    bs,_ = pitch.groups(bass)
    if len(gs) != len(bs) or [g['start'] for g in gs] != [b['start'] for b in bs]:
        raise ValueError('Bass/guitar positions differ')
    result = deepcopy(bass)
    for g,b in zip(gs,bs):
        if b['intervals'] != (0,):
            raise ValueError('Bass must be monophonic')
        for ti,mi in (b['members'][0]['on'],b['members'][0]['off']):
            result.tracks[ti][mi] = result.tracks[ti][mi].copy(note=g['root']-24)
    assert pitch.rhythm_signature(result) == pitch.rhythm_signature(bass)
    return result


def unified_chorus(source, rows):
    guitar = part(source,0)
    gs,_ = pitch.groups(guitar)
    positions = intro.position_map(gs,0,8,480)
    expected = {(bar*1920 + step*120): root for bar,row in enumerate(rows) for step,root in row}
    if set(expected) != set(positions):
        raise ValueError('Explicit motif must cover the exact rhythmic positions')
    roots = [expected[g['start']] for g in gs]
    result = deepcopy(source)
    new = pitch.transpose_groups(guitar, roots, allowed_roots=[52,54,55,57,59],
         guitar_range=[52,66],bass_range=[28,35],max_jump=4)
    validate_pitch(pitch.groups(new)[0])
    result.tracks[0] = new.tracks[0]
    result.tracks[1] = retune_bass(part(source,1),new).tracks[0]
    assert intro.motif_comparison(gs,roots)['root_match_rate_over_position_union'] == 1
    assert source.tracks[2] == result.tracks[2]
    return result


def refine_transition(band):
    result = deepcopy(band)
    # Last Intro attack stays at beat 31.25; release at 31.5, leaving .5 beats.
    for index in (0,1):
        midi = part(result,index)
        gs,_ = pitch.groups(midi)
        durations = [(g['end']-g['start'])/480 for g in gs]
        last = max(i for i,g in enumerate(gs) if g['start'] < 15360)
        if gs[last]['start'] != 15000 or gs[last]['end'] != 15240:
            raise ValueError('Unexpected Intro final articulation')
        durations[last] = .25
        midi,_ = gate.change_gates(midi,durations)
        # First Chorus root/chord accent, pitch and gate unchanged.
        events = [(t,m.copy(velocity=116) if t==15360 and m.type=='note_on' and m.velocity>0 else m)
                  for t,m in absolute(midi.tracks[0])]
        result.tracks[index] = schedule(events,30720)
    # Keep the original last-bar snare fill. Add one GM-map crash candidate only.
    events = absolute(result.tracks[2])
    if any(m.type=='note_on' and m.velocity>0 and m.channel==9 and m.note==49 and 15360<=t<15480 for t,m in events):
        raise ValueError('Crash already present; do not double the hit')
    events += [(15360,mido.Message('note_on',channel=9,note=49,velocity=116)),
               (15480,mido.Message('note_off',channel=9,note=49,velocity=0))]
    result.tracks[2] = schedule(events,30720)
    return result


def event_difference(before, after):
    result = {}
    for name,a,b in zip(['guitar','bass','drums'],before.tracks,after.tracks):
        ca,cb = musical(a),musical(b)
        result[name] = {'removed':[{'tick':t,'message':json.loads(m)} for (t,m),n in sorted((ca-cb).items()) for _ in range(n)],
                        'added':[{'tick':t,'message':json.loads(m)} for (t,m),n in sorted((cb-ca).items()) for _ in range(n)]}
    return result


def metrics(guitar):
    gs,_ = pitch.groups(guitar)
    result = {}
    for name,first in [('Intro',0),('Chorus',8)]:
        subset = [g for g in gs if first*1920<=g['start']<(first+8)*1920]
        local = deepcopy(subset)
        for g in local:
            g['start']-=first*1920;g['end']-=first*1920
        roots = [g['root'] for g in local]
        triples = Counter(tuple(roots[i:i+3]) for i in range(len(roots)-2))
        result[name] = {'groups':len(local),'notes':sum(len(g['members']) for g in local),
            'single':sum(len(g['members'])==1 for g in local),'power_chord':sum(len(g['members'])==2 for g in local),
            'motion_count':sum(a!=b for a,b in zip(roots,roots[1:])),
            'motion_comparisons':len(roots)-1,'motion_rate':sum(a!=b for a,b in zip(roots,roots[1:]))/(len(roots)-1),
            'motif':intro.motif_comparison(local,roots),
            'duration_groups':dict(Counter(str((g['end']-g['start'])/480) for g in local)),
            'duration_notes':dict(Counter(str((g['end']-g['start'])/480) for g in local for _ in g['members'])),
            'distinct_pitch_triples':len(triples),'repeated_triple_occurrences':sum(n-1 for n in triples.values()),
            'roots_by_bar':[[g['root'] for g in local if g['start']//1920==b] for b in range(8)]}
    before = max(g['end'] for g in gs if g['start']<15360)
    after = min(g['start'] for g in gs if g['start']>=15360)
    result['boundary_guitar_gap_beats'] = (after-before)/480
    return result


def run(request_path, output, *, project_root='.'):
    root = Path(project_root).resolve()
    request = json.loads(Path(request_path).read_text())
    sources = {}
    for key,record in request['sources'].items():
        path = root/record['path']
        if pitch.sha(path) != record['sha256']:
            raise ValueError('Source hash mismatch: '+key)
        sources[key] = mido.MidiFile(path)
        validate_source(sources[key])
    assert len(pitch.groups(part(sources['intro'],0))[0])==52
    assert len(pitch.groups(part(sources['chorus'],0))[0])==60
    a = concatenate(sources['intro'],sources['chorus'])
    b = concatenate(sources['intro'],unified_chorus(sources['chorus'],request['chorus_motif_by_bar']))
    c = refine_transition(b)
    differences = event_difference(b,c)
    for instrument,changes in differences.items():
        assert all(13440<=r['tick']<17280 for rows in changes.values() for r in rows),instrument
    assert musical(a.tracks[2]) == musical(b.tracks[2])
    for ta,tb in zip(a.tracks,b.tracks):
        assert Counter({k:v for k,v in musical(ta).items() if k[0]<15360}) == Counter({k:v for k,v in musical(tb).items() if k[0]<15360})
    report = {'sources':request['sources'],'candidates':{},'B_to_C_event_diff':differences}
    out = Path(output)
    out.mkdir(parents=True,exist_ok=False)
    stems = {'A':'direct_transition','B':'unified_chorus','C':'refined_transition'}
    for label,band in [('A',a),('B',b),('C',c)]:
        guitar,bass = part(band,0),part(band,1)
        gs,_ = pitch.groups(guitar);bs,_ = pitch.groups(bass)
        validate_pitch(gs)
        assert len(gs)==len(bs)==112
        assert all((g['start'],g['root']-24,g['velocity']) == (z['start'],z['root'],z['velocity']) for g,z in zip(gs,bs))
        assert all((g['intervals'] in {(0,),(0,7)}) for g in gs)
        for t in band.tracks:
            validate_notes(t,30720)
            assert absolute(t)[-1][0]==30720
            assert [(tick,m.tempo) for tick,m in absolute(t) if m.type=='set_tempo']==[(0,571429)]
            assert [(tick,m.numerator,m.denominator) for tick,m in absolute(t) if m.type=='time_signature']==[(0,4,4)]
        for name,midi in [('song',band),('guitar',guitar),('bass',bass),('drums',part(band,2))]:
            pitch.save_new(midi,out/f'{name}_{label}_{stems[label]}.mid')
        row = metrics(guitar)
        row['bass_pitch_onset_velocity_follow_rate']=1
        row['inherited_guitar_bass_gate_mismatches']=sum(g['end']!=z['end'] for g,z in zip(gs,bs))
        row['boundary_drum_attacks']=[{'tick':t,'note':m.note,'velocity':m.velocity} for t,m in absolute(band.tracks[2]) if 13440<=t<17280 and m.type=='note_on' and m.velocity>0]
        report['candidates'][label]=row
    assert pitch.rhythm_signature(part(a,0))==pitch.rhythm_signature(part(b,0))
    for name in ['Intro','Chorus']:
        assert report['candidates']['B'][name]['roots_by_bar']==report['candidates']['C'][name]['roots_by_bar']
    report['checks']={'A_all_source_channel_events_preserved':True,'A_B_Intro_identical':True,
      'A_B_guitar_pitch_only':True,'B_C_outside_boundary_identical':True,'B_C_root_motifs_identical':True,
      'bass_root_minus24_onset_velocity_100percent':True,'all_notes_paired_no_duplicate_overlap':True,
      'constant_105bpm_4_4_16bars':True,'no_new_dependency_or_default_change':True}
    report['hashes']={p.name:pitch.sha(p) for p in sorted(out.glob('*.mid'))}
    (out/'verification.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    (out/'request.json').write_text(json.dumps(request,indent=2,ensure_ascii=False)+'\n')
    lines=['# Intro → Chorus 16小節 比較レポート','',
      '105 BPM（571429 μs/拍へのMIDI整数丸め）・4/4・TPB 480・30720 tick＝64拍。A/B/Cは試聴用で自動採用しない。','',
      '|候補|Intro打点/コード|Chorus打点/コード|Chorus音程移動率|1–2↔5–6音程一致|境界ギター無音|',
      '|---|---:|---:|---:|---:|---:|']
    for label,row in report['candidates'].items():
        i,h=row['Intro'],row['Chorus']
        lines.append(f"|{label}|{i['groups']}/{i['power_chord']}|{h['groups']}/{h['power_chord']}|{h['motion_count']}/{h['motion_comparisons']} ({h['motion_rate']:.1%})|{h['motif']['root_match_rate_over_position_union']:.1%}|{row['boundary_guitar_gap_beats']}拍|")
    lines += ['', '## セクション別の反復・移動','', '|候補/セクション|音程移動率|1–2↔5–6ルート一致|3音セル種類/再登場数|', '|---|---:|---:|---:|']
    for label,row in report['candidates'].items():
        for section in ['Intro','Chorus']:
            r=row[section]
            lines.append(f"|{label} {section}|{r['motion_rate']:.1%}|{r['motif']['root_match_rate_over_position_union']:.1%}|{r['distinct_pitch_triples']}/{r['repeated_triple_occurrences']}|")
    lines += ['', '## 比較設計','',
      'Aは原本両セクションのNote On/Off、Program Changeを絶対tickで完全保存。BはIntroを保存し、Chorusのルートと追従ベース音程だけ変更。原本Chorusも既に1–2小節と5–6小節は一致している。BはE–G–A–Gの短い核を繰り返し、応答と終止を関連付ける。反復一致率だけの改善とは主張しない。音程列は下表とverification.jsonに明記。',
      'CはBの音程列を全て維持。Intro最終グループ（beat 31.25）のギター2音とベース1音を0.5→0.25拍へ短縮。Chorus先頭グループのギター2音・ベース1音のvelocityを108→116へ変更。Chorus開始tick15360にch10 note49 velocity116を追加し、15480で解放。既存Intro末尾のスネアフィルは保持し、追加フィルを重ねない。',
      'note49は既存GM_DRUM_MAPのCrash Cymbal 1候補。原本にも49は存在するが、音源固有の割り当ては未確認。音源でクラッシュになるかはDAWで確認する。奏法キーは追加しない。',
      '', '## 接続とメタデータ','',
      '各パートの先頭に既存テンポ/拍子を1件ずつ保持。後半の重複テンポ/拍子とトラック名は削除。曲のテンポ/拍子は3トラック間で同一。元ドラムのC調ラベルとSMPTE開始時刻は曲の調性/位置と混同しないよう除外。Intro/Chorusマーカー、チャンネル0/1/9、Program Change 30/33（表示31/34）を保存し、境界の同値Program Changeも保持する。CC/ピッチベンド/SysEx等は原本に存在しない。未知の状態制御を持つ入力は拒否する。',
      '全Note Offを次の同tick Note Onより先に配置。原本に境界を越える音はなく、Aの音楽イベント修正は不要。30720tickで全トラック終了する。',
      '原本Chorusではギターとベースの音価が36グループ異なる（Step 9でベースを固定した仕様）。A/B/Cともこの差を保持する。ベースの追従100%はオンセット・ルート−24・velocityについての数値であり、音価一致を意味しない。CのIntro末尾短縮だけ両楽器で同時に行う。',
      '', '## 音程モチーフ（Chorus、発音順）','', '|小節|A|B/C|','|---|---|---|']
    for bar,(old,new) in enumerate(zip(report['candidates']['A']['Chorus']['roots_by_bar'],report['candidates']['B']['Chorus']['roots_by_bar']),1):
        lines.append(f'|{bar}|{old}|{new}|')
    lines += ['', '## 音価分布（発音グループ／ノートを区別）','']
    for label,row in report['candidates'].items():
        for section in ['Intro','Chorus']:
            r=row[section]
            lines.append(f"- {label} {section}: グループ {r['duration_groups']}、ノート {r['duration_notes']}。単音{r['single']}・コード{r['power_chord']}。")
    lines += ['', '## B→C 全イベント差分','', '各行は絶対tickとtime=0に正規化したMIDIイベント。削除/追加対で変更を表す。', '```json',json.dumps(differences,indent=2,ensure_ascii=False),'```',
      '', '境界ドラム打点、各候補の全音程列・集計・SHA256はverification.json参照。数値は好みの判定ではない。Eマイナーの全構成音、ルート52/54/55/57/59・ギター52–66・ベース28–35・隣接跳躍4半音以内を検証。音源固有の演奏可能域は未検証。',
      '', '## 試聴の焦点','',
      'A→BではChorusの短い音程セルと応答のつながり、B→Cでは境界0.5拍のギター/ベースの間とChorus先頭アクセントを比較する。ギター/ベースの間にも既存ドラムフィルが鳴るため、バンド全体の無音とは異なる。既定動作・素材・既存出力は変更しない。']
    (out/'comparison_report.md').write_text('\n'.join(lines)+'\n')
    (out/'README.md').write_text('# Phase 9 Step 11 試聴\n\nA: 既存Intro/Chorusの接続。B: Chorusの音程モチーフ統一。C: B＋局所的な境界調整。\n\n'+ '\n'.join(f'- [{label} バンド](song_{label}_{stem}.mid) / [ギター](guitar_{label}_{stem}.mid) / [ベース](bass_{label}_{stem}.mid) / [ドラム](drums_{label}_{stem}.mid)' for label,stem in stems.items())+'\n\n[比較レポート](comparison_report.md)、[イベント検証・ハッシュ](verification.json)。生成器の既定値は変更せず、試聴による判断を待つ。\n')
    return report


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request');parser.add_argument('output');parser.add_argument('--project-root',default='.')
    args=parser.parse_args()
    run(args.request,args.output,project_root=args.project_root)
