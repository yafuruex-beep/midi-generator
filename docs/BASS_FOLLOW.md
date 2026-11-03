# Phase 8.1 Step 2 — ギター完全追従の単音ベース

`modules/bass/follow.py` は、`GenerationRequest` → `GenerationResult` の共通
インターフェースを実装する。`api.generate()` の bass 分岐と既存CLIの
`generate --config` が同じコアを呼ぶ。カタログやベース素材は不要。
独立したベースリズム、コード進行、音源の自動選択は実装しない。

```sh
.venv/bin/python -m midi_generator.cli generate \
  --config examples/requests/bass_follow.json \
  --output output/new_bass_follow.mid
```

```python
from midi_generator.api import generate
result = generate({
    "instrument": "bass", "section": "validation", "tempo": 105, "bars": 8,
    "time_signature": [4, 4],
    "options": {
        "guitar_midi": "output/phase74_step7_groove_105bpm_8bars/guitar_B_contrast_105bpm_8bars.mid",
        "transpose_semitones": -24,
        "program": 34,
        "midi_channel": 2,
    },
}, output_path="output/new_bass_E1.mid")
```

## 設定

- `guitar_midi`：必須。単一の非ドラム・ノートチャンネルを持つギターMIDI。
- `transpose_semitones`：必須の整数 −127..127。変換後もMIDI 0..127を検証。
- `program`：必須、人間表記1..128。比較用は34（GM Electric Bass finger、内部33）。
- `midi_channel`：省略時2。最小実装では2以外を拒否。Mido内部では1。
- `output_path`：必須。相対入力・出力パスは `project_root` 基準。

`options`／`generation_options` の既存マージ規則をそのまま使用する。
bass固有設定以外の options は拒否し、`key`／`chords`／非空 `groove` も未対応として拒否。
既存drums/guitarのリクエスト・分岐・既定値は変更しない。汎用のsection/style/tags/seedは
この追従処理のリズムや音高を変えない。

## 入力の厳密な検証と出力

既存 `load_midi()` で読み、絶対tickで全トラックをまとめる。開始tickの完全一致で
グループ化し、単音またはルート＋7半音の2音だけを許可する。和音内の終了tick・
発音ベロシティも一致が必要。最低音をルートとして各グループから1ノートを出力。
転回形・任意の外部和音のルート推定は行わない。

- MIDI format 0/1、正のTPB、明示的かつ一定のテンポ・4/4がtick 0から必要。
- リクエストのBPM・小節数は入力と完全一致。ループ、切り詰め、量子化はしない。
- Note Off不足・余分なOff・重複同音・重なる発音グループ・ゼロ音価は拒否。
- 3音以上、完全5度以外、異なる終了tick/ベロシティ、複数ノートチャンネルも拒否。
- ノートとProgram Change以外の演奏メッセージ（CC、Pitch Bend、SysEx等）は未対応として拒否。
  サステインや移調を黙って無視しない。入力Program Changeは読み捨てる。
- 正しいNote On velocity=0はNote Offとして処理する。同tickでは全OffをOnより先に扱う。

元の開始・終了tick、休符、Note Onベロシティ、TPB、テンポと終了位置を維持する。
ベースは新しいトラック名と明示Program Changeを持ち、Note Off velocityは0。
音価は再計算・短縮しない。入力が非重複であることを事前に検証する。

保存は既存 `save_midi()` の既定上書き拒否を利用し、入力と同じパスも拒否する。
入力SHA-256を読込前と保存前に確認。`GenerationResult.metadata` に入力パス・ハッシュ、
移調量、Program、発音数、音域を返す。APIはMIDIのみ保存し、JSONサイドカーは自動作成しない。
比較用スクリプトはそのメタデータを verification.json に保存する。

## 音高と再生音源

ルート52から−12で40（E2）、−24で28（E1）。これは中央C=60=C4の音名表記。
譜面のオクターブ移調や5弦半音下げの自動補正は追加しない。
MIDI番号の正しさと、使用音源の実際の再生音域・キースイッチ対応は別問題。
この段階では音源音域未確認を metadata に記録する。Program指定はGM対応再生環境向けで、
外部音源のパッチや発音可能範囲を保証しない。

## 採用Step 7-Bの比較出力

```sh
.venv/bin/python scripts/create_bass_follow_auditions.py \
  examples/requests/bass_follow.json \
  output/phase74_step7_groove_105bpm_8bars/drums_guitar_B_contrast_105bpm_8bars.mid \
  output/phase81_step2_bass_follow_105bpm_8bars
```

既存の出力ディレクトリは拒否。A1は−12、A2は−24。各版にベース単体と
ドラム＋ギター＋ベースを保存する。元のドラム＋ギター全トラックは変更せずコピーし、
再読込して全メッセージ一致を検証する。設定は各版の request JSON にも保存する。

全版105 BPM・4/4・8小節・480 TPB、ベース60発音、0.25拍54発音・0.5拍6発音、
終了15360 tick。発音位置・終了・休符・ベロシティはギターのルートと完全一致。
既存素材・試聴MIDIのハッシュ、Note On/Off、単音性、小節境界も検証する。
新しいベースリズム、コード進行、ヒューマナイズ、ベース素材選択は追加していない。
