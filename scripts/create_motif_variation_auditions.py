"""Create A/B/C from one adopted chorus, without applying them to a whole song."""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

import mido

from midi_generator.api import develop_backing_motif, generate, render_backing_midi

_spec = importlib.util.spec_from_file_location('groove_compare', Path(__file__).with_name('run_groove_consistency_demo.py'))
compare = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(compare)
demo = compare.demo
TPB, CELL = demo.TPB, compare.CELL


def motif_rows(bars, bpm):
    """Reuse the existing renderer for every chord's actual On/Off ticks."""
    rows = []
    for i, bar in enumerate(bars):
        midi = render_backing_midi(bar, export_bpm=bpm)
        tick = i * 4 * TPB; active = {}
        for msg in midi.tracks[0]:
            tick += msg.time
            if msg.type == 'note_on' and msg.velocity:
                assert msg.note not in active; active[msg.note] = (tick, msg.velocity)
            elif msg.type == 'note_off':
                start, velocity = active.pop(msg.note)
                rows.append((start, tick, msg.note, velocity, msg.channel + 1))
        assert not active and tick == (i + 1) * 4 * TPB
    return sorted(rows)


def rhythm_pairs(bars):
    """Onset+gate signatures; pitch/voicing are deliberately not counted twice."""
    signatures = []
    for start in (0, 2, 4, 6):
        signatures.append({(i * 16 + s, g) for i, bar in enumerate(bars[start:start + 2])
                           for s, g in zip(bar['onsets'], bar['gates_steps'])})
    scores = [compare.jaccard(signatures[0], s) for s in signatures[1:]]
    return {'onset_gate_jaccard_1_2_vs_3_4_5_6_7_8': scores,
            'mean_onset_gate_pair_similarity': sum(scores) / 3,
            'exact_return_pairs': sum(s == signatures[0] for s in signatures[1:]),
            'return_pairs_compared': 3}


def changes_between(before, after):
    changes = []
    for i, (a, b) in enumerate(zip(before, after)):
        old = {s.step: s for s in a}; new = {s.step: s for s in b}
        assert set(new) <= set(old)
        for cell, step in new.items():
            original = old[cell]
            assert (step.note, step.velocity, step.voicing) == (original.note, original.velocity, original.voicing)
            assert step.duration_steps <= original.duration_steps
            if step.duration_steps != original.duration_steps:
                changes.append({'bar': i + 1, 'step': cell, 'action': 'shorten',
                                'old_beats': original.duration_steps / 4, 'new_beats': step.duration_steps / 4})
        for cell in sorted(set(old) - set(new)):
            changes.append({'bar': i + 1, 'step': cell, 'action': 'remove_weak_attack',
                            'notes': list(old[cell].notes), 'velocity': old[cell].velocity})
    return changes


def strict_pairing(midi):
    for track in midi.tracks:
        active = set(); tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == 'note_on' and msg.velocity:
                key = (msg.channel, msg.note); assert key not in active; active.add(key)
            elif msg.type == 'note_off':
                key = (msg.channel, msg.note); assert key in active; active.remove(key)
        assert not active and tick == 32 * TPB


def run(request_path, output):
    request = json.loads(Path(request_path).read_text())
    source = Path(request['source_chorus_midi'])
    assert demo.sha(source) == request['source_sha256']
    source_midi = demo.load_midi(source)
    assert source_midi.type == 1 and len(source_midi.tracks) == 3 and source_midi.ticks_per_beat == TPB
    source_config = json.loads(Path(request['motif_request']).read_text())
    section = next(s for s in source_config['sections'] if s['id'] == request['section_id'])
    original = demo.section_motif(source_config, section)
    bpm = source_config['export_bpm']; tempo = mido.bpm2tempo(bpm); stop = 32 * TPB
    _, source_rows = demo.read_notes(source)
    original_rows = motif_rows(original, bpm)
    assert original_rows == sorted(n for n in source_rows if n[4] == 1)
    assert sorted((s, e, p - 24, v, 2) for s, e, p, v, _ in original_rows if p == source_config['guitar']['root']) == sorted(n for n in source_rows if n[4] == 2)
    # The accepted excerpt has exactly these shared performance conditions.
    for i, program in ((0, source_config['guitar']['program']), (1, source_config['bass']['program'])):
        pc = [m for m in source_midi.tracks[i] if m.type == 'program_change']
        assert len(pc) == 1 and pc[0].program == program - 1 and pc[0].channel == i
    markers = []; tick = 0
    for msg in source_midi.tracks[0]:
        tick += msg.time
        if msg.type == 'marker':markers.append((tick, msg.text))
    protected = {str(p): demo.sha(p) for base in (Path('data'), Path('output'), Path('src'))
                 for p in base.rglob('*') if p.is_file() and p.suffix.lower() in ('.mid', '.midi', '.json', '.wav', '.mp3', '.flac', '.py')}
    out = Path(output); out.mkdir(parents=True, exist_ok=False)
    work = out / 'generation_work'; work.mkdir()
    shared = mido.MidiFile(type=0, ticks_per_beat=TPB)
    shared.tracks.append(mido.MidiTrack(m.copy() for m in source_midi.tracks[2]))
    demo.save_midi(shared, out / 'drums_shared.mid')
    reference_roots = [n for n in original_rows if n[2] == source_config['guitar']['root']]
    reference = compare.local_bars(reference_roots, 0, stop)
    report = {'source': str(source), 'source_sha256': request['source_sha256'],
              'source_context': 'Same first eight chorus bars as Phase9 Step2; no opening silence',
              'settings': {'bpm': bpm, 'meter': [4, 4], 'bars': 8, 'guitar_program': source_config['guitar']['program'],
                           'bass_program': source_config['bass']['program'], 'bass_transpose': -24},
              'definitions': {'onset_similarity': 'Jaccard of sixteenth-grid attack sets; chord tones count as one attack',
                  'rhythm_pair_similarity': 'Jaccard of (local onset, gate) tuples for 1–2 vs 3–4/5–6/7–8; velocities listed separately',
                  'major_rest': 'Reference quiet runs >=2 consecutive sixteenth cells; retention checks those cells remain quiet',
                  'major_accent': 'Velocity >=90% of the local maximum; independent from drum classification',
                  'kick_match': 'Existing tolerance 0.04 quarter beats; drum original microtiming retained'},
              'candidates': {}, 'files': {}, 'automatic_quality_ranking': None}
    names = {'A': 'current', 'B': 'repetition', 'C': 'call_response'}
    assert request['candidates'] == names
    for label, mode in request['candidates'].items():
        bars = develop_backing_motif(original, mode=mode)
        assert bars == develop_backing_motif(original, mode=mode)
        edits = changes_between(original, bars)
        rows = motif_rows(bars, bpm)
        guitar_path = out / f'guitar_{label}_{mode}.mid'
        guitar = demo.write_part(guitar_path, 'Guitar', rows, stop, tempo, markers,
                                  program=source_config['guitar']['program'], channel=1)
        _, checked_guitar_rows = demo.read_notes(guitar_path); assert sorted(checked_guitar_rows) == rows
        bass_request = {'instrument': 'bass', 'section': 'chorus', 'tempo': bpm, 'bars': 8,
            'options': {**source_config['bass'], 'transpose_semitones': -24, 'guitar_midi': str(guitar_path.resolve())}}
        generated_bass = work / f'bass_{label}_generated.mid'
        bass_result = generate(bass_request, output_path=generated_bass)
        _, bass_rows = demo.read_notes(generated_bass)
        roots = [n for n in rows if n[2] == source_config['guitar']['root']]
        assert bass_rows == [(s, e, p - 24, v, 2) for s, e, p, v, _ in roots]
        bass_path = out / f'bass_{label}_{mode}.mid'
        bass = demo.write_part(bass_path, 'Bass', bass_rows, stop, tempo, markers,
                                program=source_config['bass']['program'], channel=2)
        band = mido.MidiFile(type=1, ticks_per_beat=TPB)
        band.tracks.extend([guitar.tracks[0], bass.tracks[0], mido.MidiTrack(m.copy() for m in source_midi.tracks[2])])
        band_path = out / f'chorus_{label}_{mode}.mid'
        if label == 'A':
            assert band.tracks == source_midi.tracks
            shutil.copyfile(source, band_path)
        else:
            demo.save_midi(band, band_path)
        loaded = demo.load_midi(band_path)
        assert loaded.tracks[2] == source_midi.tracks[2]
        assert loaded.tracks[:2] == [guitar.tracks[0], bass.tracks[0]]
        strict_pairing(loaded)
        data, band_rows = demo.read_notes(band_path); assert data['end_tick'] == stop
        assert len(set(s for s, *_ in bass_rows)) == len(bass_rows)
        metrics = compare.measure_section(roots, source_rows, reference,
            {'id': label, 'start_beat': 0, 'end_beat': 32}, 0)
        assert metrics['major_rest_retention'] == 1
        assert all(set(a['major_accents']) == set(b['major_accents']) for a, b in zip(reference, metrics['bars']))
        assert all(not current or prior for a, b in zip(reference, metrics['bars']) for prior, current in zip(a['active'], b['active']))
        kicks = [n for n in source_rows if n[4] == 10 and n[2] in (35, 36)]
        matches = sum(any(abs(s - k[0]) <= .04 * TPB for s, *_ in roots) for k in kicks)
        chord_count = sum(s.voicing == 'power5' for bar in bars for s in bar)
        report['candidates'][label] = {'mode': mode, 'event_count': len(roots), 'power5_events': chord_count,
            'power5_ratio': chord_count / len(roots), 'changes': edits, 'rhythm': metrics,
            'repetition': rhythm_pairs(metrics['bars']), 'kick_matches': matches, 'kick_events': len(kicks),
            'gate_distribution_beats': dict(Counter((e - s) / TPB for s, e, *_ in roots)),
            'bass_follow_exact': True, 'bass_metadata': bass_result.metadata,
            'drum_track_all_messages_identical': True, 'on_off_and_boundaries_verified': True}
        for path in (guitar_path, bass_path, band_path):report['files'][path.name] = demo.sha(path)
    assert all(demo.sha(p) == digest for p, digest in protected.items())
    report['validation'] = {'existing_files_hashes_unchanged': len(protected), 'A_band_byte_identical_to_Step2': demo.sha(out / 'chorus_A_current.mid') == request['source_sha256'],
                            'all_modes_deterministic': True, 'defaults_and_whole_song_unchanged': True}
    demo.dump(out / 'verification.json', report); demo.dump(out / 'request.json', request)
    write_report(out, report)
    return report


def write_report(out, report):
    lines = ['# Phase 9 Step 3：8小節モチーフ候補比較', '',
        '同じChorus先頭8小節。AはStep2の統合MIDIのバイトコピー。B/Cは試聴候補で、楽曲全体には適用していない。',
        'ドラム全メッセージ、105 BPM、4/4、8小節、プログラム31/34、チャンネル1/2/10を統一。',
        '', '## 現状分析・候補の設計', '',
        'Step2のsection_motif()はgenerate_backing_phrase()から8小節を一度だけ作り、motif_slice()がその位相を保持する。',
        'Aの実体は4小節A/A小変化/A/締めを2回繰り返す構造。1〜2＝5〜6、3〜4＝7〜8で、1・3・5・7小節のオンセットも一致。',
        '変化は2/6小節末の14→15、4/8小節の大休符と裏拍13、そして限定的な0.5拍音価。終端は最後の音を閉じて8小節末まで休符。',
        'Step2の既存investigation.md/comparison_report.mdを再利用。Step7-Bブループリントを固定しており、この比較でもドラムのキックに合わせて作り直さない。',
        '', '- B：1〜2小節の構造を維持し、4/8小節の大きな休符前の長音だけ0.5→0.25拍にする。オンセット反復率はAと同じで、音価パターンの反復を強める候補。',
        '- C：3小節の弱い刻み6、7小節の弱い刻み10を1つずつ除去し応答を作る。5〜6は基本に戻り、8小節末13を0.25拍にして終止の間を広げる。',
        '- 主要アクセントのオンセット/ベロシティ、元の主要休符、ルート・ボイシングを保持。イベントの追加・移動、ランダム性はない。',
        '', '## 発音・反復・同期', '',
        '| 候補 | 発音 | コード比率 | 音価分布（拍:個数） | 音価込みペア類似性の平均 | 完全回帰ペア | キック同期 |',
        '|---|---:|---:|---|---:|---:|---:|']
    for label, c in report['candidates'].items():
        r = c['repetition']
        lines.append(f"| {label} | {c['event_count']} | {c['power5_ratio']*100:.2f}% | {c['gate_distribution_beats']} | {r['mean_onset_gate_pair_similarity']:.3f} | {r['exact_return_pairs']}/3 | {c['kick_matches']}/{c['kick_events']} |")
    lines += ['', 'ペア類似性は1〜2対3〜4、5〜6、7〜8の(local onset, gate)集合のJaccard平均。完全回帰はその集合が完全一致する後続ペア数。',
        'Bはオンセット自体を増やしたり均一化したりせず、終端の音価だけ基本の短音へ寄せる。Cは応答のため類似性が下がる。数値の高低で優劣を判定しない。',
        '', '## 変更位置（16分ステップ、先頭0）', '', '| 候補 | 小節 | 位置 | 変更 |', '|---|---:|---:|---|']
    for label, c in report['candidates'].items():
        for edit in c['changes']:
            text = '弱い発音を除去' if edit['action'] == 'remove_weak_attack' else f"{edit['old_beats']}→{edit['new_beats']}拍"
            lines.append(f"| {label} | {edit['bar']} | {edit['step']} | {text} |")
    for label, c in report['candidates'].items():
        lines += ['', f'## {label}：小節別データ', '',
            '| 小節 | オンセット | 無音セル | 音価（16分ステップ） | velocity | 強アクセント |', '|---|---|---|---|---|---|']
        for i, bar in enumerate(c['rhythm']['bars']):
            quiet = [j for j, active in enumerate(bar['active']) if not active]
            lines.append(f"| {i+1} | {bar['onsets']} | {quiet} | {bar['gates_steps']} | {bar['velocities']} | {bar['major_accents']} |")
        lines += ['', f"オンセットのペア類似性：{c['rhythm']['pair_similarities']}",
                  f"オンセット＋音価のペア類似性：{c['repetition']['onset_gate_jaccard_1_2_vs_3_4_5_6_7_8']}"]
    lines += ['', '## 検証と注意', '',
        '- 全候補で主要休符維持率100%。新たに無音になるセルはあるが、元の休符は埋めていない。',
        '- 全候補で元の強アクセント位置とvelocityを保持。反復の強さ・応答感の優劣は試聴で判断する。',
        '- ベースは既存generate(instrument=bass)で−24半音。各ギタールートの開始/終了/velocityと全件一致。',
        '- ドラムは元のStep2トラックを全メッセージそのままコピー。Cで除去した弱い刻みの一部がキックと一致していたため、全キック同期数はわずかに減る。主要拍頭は保持。',
        '- Note On/Off、同音連打、和音構成音の同時On/Off、8小節末を機械検証。Program/tempoをAの条件に統一。',
        '- 決定論的再生成と既存テストの最終結果はvalidation_summary.json/regression_tests.txtを参照。',
        '- Aを基準に保ち、B/Cの自動採用・品質ランキング・楽曲全体への既定反映は行わない。',
        '', 'chorus_*.mid が統合MIDI。guitar_*.mid/bass_*.midは単体。drums_shared.midは全候補共通。']
    (out / 'comparison_report.md').write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = run(args.request, args.output)
    print(json.dumps({k: {'events': v['event_count'], 'changes': v['changes']} for k, v in report['candidates'].items()}, indent=2))
