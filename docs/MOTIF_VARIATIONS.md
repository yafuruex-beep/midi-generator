# Phase 9 Step 3 — 8小節モチーフの試聴候補

既存Step7-B/Step2を変更せず、同じChorus先頭8小節でA/B/Cを比較する。
選択した候補を楽曲全体へ適用する機能・既定設定変更は含まない。

## 共通処理

`develop_backing_motif(bars, mode="current")` はPython APIから利用できる
小さな決定論的後処理。入力は既存 `generate_backing_phrase()` またはStep2の
`section_motif()` から得た8個のローカル小節（BackingStepのタプル）。
既定currentは入力と同じイベントを返し、生成器の既定値は変えない。
試聴スクリプトも同じAPIを使用する。

- `current`：無変更。
- `repetition`：4/8小節で大きな休符に先行する長音があれば、最後の該当発音だけ
  1ステップへ短縮する（次の発音まで4ステップ以上ある発音）。
- `call_response`：3小節目で後半の強アクセント直前の弱い内部発音を1個除去、
  7小節目ではその直後の弱い内部発音を1個除去。8小節末の長音は既存の末尾休符が
  ある場合のみ1ステップへ短縮する。

強アクセントは小節最大velocityの90%以上。弱い発音はその閾値未満。
先頭・末尾発音や強アクセントは除去対象にしない。安全な候補がなければ無変更。
入力は正確に8小節、非空・順序付き・非重複のBackingStepであることを検証する。

発音の追加/移動、velocity変更、音高/voicing再割当てはしない。
ゲート短縮と弱い発音除去だけなので、元の主要休符を埋めない。
コード比率の再計算はせず、残る音の構成を保持して変更要因を絞る。

## 採用モチーフでの具体的変化

AはStep2の `after_chorus_8bars.mid` をバイトコピーする。
元の構造は4小節周期×2（基本/小変化/基本/締め）。1〜2＝5〜6が完全回帰。
Bは4・8小節のstep8を0.5→0.25拍にする。Cは3小節step6と7小節step10を除去し、
8小節step13を0.5→0.25拍にする。位置は小節先頭0の16分ステップ。

Bのオンセット反復率はAと同じ。休符前の長音を短音へ戻すことで音価込みの反復を
強める比較であり、全発音を増やして同じ形へ揃える方式ではない。
Cは5〜6で基本に戻り、3〜4と7〜8に応答と終止の違いを残す。

## 生成・検証

```sh
.venv/bin/python scripts/create_motif_variation_auditions.py \
  examples/requests/chorus_motif_variations.json \
  output/phase9_step3_motif_variations
```

新規ディレクトリが必要。`chorus_A_current.mid`、`chorus_B_repetition.mid`、
`chorus_C_call_response.mid` が3トラックの統合MIDI。各候補のguitar/bass単体と
共通 `drums_shared.mid` も保存する。Aのギター/ベースも元トラックとイベント完全一致。
ドラム全トラックは3候補で同一。B/Cベースは既存APIから−24半音で完全追従生成する。
105 BPM、4/4、8小節、ギターProgram31・ベースProgram34、ch1/2/10を統一する。

comparison_report.md と verification.json に各小節のオンセット、無音セル、音価、
実velocity、強アクセント、変更位置、コード比率、キック同期、ベース追従を保存する。
モチーフ反復は1〜2対3〜4/5〜6/7〜8のオンセット集合、さらに(onset, gate)集合の
Jaccardと完全回帰ペア数を使用。音高や和音の音数はリズム反復のカウントに含めない。
類似性の高低は音楽的な優劣を意味せず、B/Cを自動的に採用しない。

出力後の validation_summary.json / regression_tests.txt は再生成バイト一致・全テスト結果。
外部音源の発音可能音域や聴感上の奏法はMIDI検証の対象外。
