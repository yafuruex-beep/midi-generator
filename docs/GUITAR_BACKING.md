# ドラム同期型バッキングギター生成（Phase 6.2-A）

生成済みドラムとリズムの合う既存ギターリフを選び、指定BPM・小節数で出力します。
**ノート位置の積極的な編集、移調、コード進行への追従は行いません。**
同じ素材群・設定・リクエスト・seedなら、同じ素材とMIDIバイト列を再現します。
リード生成は未実装です。

## 準備

1. ドラムMIDIを生成し、テンポ・拍子・小節数を確認する。
2. 新規ギター素材を`data/sources/guitar/backing/`直下へ置き、カタログを更新する。
   既存素材の移動時は、先に[参照更新](GUITAR_SOURCE_MIGRATION.md)を完了してからスキャンする。
3. 内容を確認してセクション・styleを付ける。ロールが確認済みなら自由タグも付ける。
4. Phase 6.1の解析を実行する。

```bash
midi-generator catalog scan
midi-generator tag add "example.mid" --section intro --style metalcore
midi-generator catalog analyze-guitars
```

セクションタグの自動推定や追加はしません。素材が`intro`条件を満たさない場合はエラーです。
検索・候補選択で別セクションや別ジャンルを勝手に使うことはありません。
BPMはギター素材と異なっていて構いませんが、参照ドラムと生成指定は一致が必要です。

## CLI

```bash
midi-generator generate --instrument guitar --guitar-role backing --section intro --tempo 160 --bars 8 --time-signature 4/4 --style metalcore --sync-to-drums output/drums_intro_160bpm_8bars.mid --seed 42 --output output/guitar_backing_intro.mid
```

主な指定：

| オプション | 意味 |
|---|---|
| `--guitar-role backing` | バッキング生成を明示。`lead`は未実装エラー |
| `--sync-to-drums` | 存在するドラムMIDI。省略不可 |
| `--midi-channel` | ギター出力チャンネル1〜16。省略時は1 |
| `--catalog` | 使用するカタログ。既定は`data/catalog/midi_catalog.json` |
| `--guitar-analysis` | Phase 6.1の解析JSON。既定は指定カタログと同じフォルダの`guitar_riff_analysis.json` |
| `--guitar-sync-config` | 同期の閾値・重み。既定は`config/guitar_sync.json` |
| `--output` | MIDI出力先。既存MIDIやサイドカーがあれば連番で保存 |

通常のドラム生成は従来どおりです。ドラム生成で`--guitar-role`や`--sync-to-drums`を指定すると、
用途が異なることを示すエラーになります。

JSONの例は`examples/requests/guitar_backing_intro.json`です。

```bash
midi-generator generate --config examples/requests/guitar_backing_intro.json --output output/my_backing.mid
```

## Python API

```python
from midi_generator.api import generate

result = generate({
    "instrument": "guitar",
    "guitar_role": "backing",
    "section": "intro",
    "tempo": 160,
    "bars": 8,
    "time_signature": [4, 4],
    "style": ["metalcore"],
    "sync_to_drums": "output/drums_intro_160bpm_8bars.mid",
    "seed": 42,
}, output_path="output/my_backing.mid")
print(result.output_files)
print(result.metadata["rhythm_sync"])
```

拍子・styleは既存リクエスト形式の配列を使います。文字列の`"4/4"`・`"metalcore"`ではありません。
新しい2項目は`GenerationRequest.from_dict`で既存の`options`に格納されます。
`GenerationRequest`を直接構築するときは、`options`内に`guitar_role`と`sync_to_drums`を指定します。
APIの追加キーワードは`guitar_analysis_path`、`guitar_sync_config_path`です。

## Backing / Leadの正式管理（Phase 6.1.1）

ファイル名の入力規則はありません。正式な配置は次の2つだけです。

```text
data/sources/guitar/
├── backing/
│   └── 任意のファイル名.mid
└── lead/
    └── 任意のファイル名.mid
```

ジャンル・セクションはタグで管理します。旧genre/role階層の推奨は廃止しました。
旧配置は読み込みを維持しますが、正式な`guitar_role`は`null`です。
自由タグやリフ解析から正式な役割へ自動昇格させません。

正式ロール検索は`search --instrument guitar --guitar-role backing`です。
`generate --guitar-role backing`は既存プロトタイプの生成対象指定であり、
このプロトタイプの既存の推定ロール処理は今回変更していません。
プロトタイプ内部のヒントと正式なカタログ属性を混同しないでください。
正式ロール検索への生成側の限定接続は、分類・参照更新が済んだ後の後続確認事項です。
詳細は[ギターソース移行ガイド](GUITAR_SOURCE_MIGRATION.md)を参照してください。

## 同期スコア

MIDIのtickを四分音符単位に正規化します。4/4の1小節は16ステップ、3/4や6/8は12ステップです。
わずかにずれたコード構成音は1アタックとして集約し、3音コードを3アタックとは数えません。
出力MIDIには各構成音の元のtickをそのまま残します。

キックは初期設定35/36、スネアは38/40です。独自マッピングは`config/guitar_sync.json`で指定します。
参照ドラムにチャンネル10があればそこだけを使い、単一の別チャンネルなら警告付きで受け入れます。
チャンネル10のない複数チャンネル参照は曖昧なのでエラーです。

| 指標 | 初期重み | 計算・意味 |
|---|---:|---|
| kick_alignment | 0.50 | 0.8×一致F1＋0.2×ベロシティ重み付きキックカバー率 |
| grid_match | 0.12 | 16分グリッド適合率と、キック/ギターのグリッド一致F1の平均 |
| syncopation_match | 0.12 | 弱拍上のキック/ギター位置の一致F1。両方に弱拍がなければ1 |
| rest_compatibility | 0.14 | キックのないセルとギターの発音区間がないセルのJaccard一致度 |
| snare_accent_relation | 0.05 | スネアとの同時アタックに加点。非一致は中立値0.5、スネア自体がなければ対象外 |
| repetition_match | 0.07 | 小節ペアが反復する/しないという構造の一致率 |

一致判定の許容差は初期設定0.04四分音符です。
F1はキック側カバー率とギター側一致率の調和平均なので、余分なアタックを増やしても高くなりません。
休符はノートの発音区間とセルの重なりから計算し、短い音も見落としません。
スネア位置でギターを鳴らすことや、すべてのキックに合わせることは必須ではありません。

適用可能な指標だけで重みを正規化し、同期スコアを0〜1で算出します。
その後、同期0.70、フレーズ長0.15、バッキング適合性0.10、リフ特性0.05で選択スコアを算出します。
どちらの重みも設定可能です。
フレーズ長は、同じ長さ、ソース全体の整数回反復、解析で確認できた周期の境界、その他の順に評価します。
最高スコアとの差が初期設定0.02以内の候補からseed付きで選択します。
同点の並びはパスで安定させ、カタログ配列の順序に依存させません。

## 時間軸と生成の制約

- ドラムのBPMは指定BPMと一致が必要。MIDIの整数テンポへの丸め差1マイクロ秒まで許容します。
- ドラム・ギターとも拍子変換と可変テンポは初期実装では非対応です。
- テンポ/拍子がないMIDIは標準120 BPM/4/4とし、警告を残します。
- ドラムは指定長以上が必要。長い場合は先頭の指定範囲のみ比較し、短いドラムを勝手にループしません。
- MIDI tick 0を小節1の開始として扱います。実際の音楽的な拍頭・アウフタクトは推定しません。
- キック・スネアはそれぞれ85%以上が16分グリッド許容内であることが必要です。
  少数のオフグリッド装飾音は元の時刻で残し、`step: null`と警告を記録します。
- イベントの`bar`/`position_beats`は実際の時刻、`grid_bar`/`step`は許容内での最寄りグリッドです。
  小節線の直前に発音した早めのノートでは、この2つの小節番号が異なることがあります。
- ギターも85%以上のアタックがグリッド許容内にある候補を使います。量子化はしません。
- ソース全体を小節単位でループ/トリミングします。途中の切断が避けられなければ警告し、境界でNote Offを処理します。
- 音程、アタック位置、ベロシティ、コード構成音、トラック数、補助メッセージを保持します。
  テンポ/拍子を設定し、ノートトラック名は`Guitar Backing`、出力チャンネルは標準1です。
- マルチチャンネルのギターや未対応Note On/Offを含む素材は安全に混合せず除外します。
  同じチャンネルの複数トラックには対応します。
- Phase 6.1で除外指定した制御キーも、生成MIDIから削除しません。音源固有のキーは元のままです。
- `key`/`chords`を指定したギター生成は非対応エラーです。奏法・チューニングは推測しません。

## 出力と来歴

MIDIの隣に`.mid.json`を保存します。
選択ソースのID/パス/ハッシュ/出典、ドラムのパス/ハッシュ/解析、全入力条件、同期スコアと内訳、
比較候補、除外理由、ロール根拠、処理内容、seed、設定、解析/アルゴリズム版、日時、警告を記録します。
元のドラム・ギター・カタログ・Phase 6.1レポートは変更しません。

スコアは音楽的な完成度を保証しません。ソースに適切な配置がなければ、選択だけで完全同期はできません。
ノート移動・追加・削除による改善はPhase 6.2-Bの対象です。実際の音源での試聴が必要です。

## 今回の実素材確認

ユーザー指定により、ファイル名に`Riff`を含む39素材のみを**検証時の候補集合**にしました。
これは一般機能の入力規則ではありません。イントロの確定した定義はないため、
検証用カタログだけに`intro`文脈を設定し、元のカタログとタグは変更していません。
ロールは未確認として扱い、生成時の警告を残しています。

```bash
midi-generator generate --config examples/requests/guitar_backing_intro.json --catalog data/cache/phase62_guitar_validation_catalog.json --guitar-analysis data/catalog/guitar_riff_analysis.json --output output/guitar_backing_intro.mid
```

この検証用カタログ・素材・ドラムは再配布ZIPに含まれません。
確認結果は`data/reports/phase62a_verification_report.json`に保存します。
