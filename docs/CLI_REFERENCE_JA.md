# MIDI Generator ターミナル操作リファレンス

このプログラムは、**CodexやChatGPTがなくても、Pythonとターミナルだけで動作します**。
自然文で依頼する代わりに、以下のコマンドで設定・実行します。

対象バージョン：0.1.0。コマンドはプロジェクトルートから実行してください。
`example.mid`などの例示名は、自分のファイル名に置き換えます。

## 目次

1. [できること・できないこと](#1-できることできないこと)
2. [初回インストール](#2-初回インストール)
3. [2回目以降の起動](#3-2回目以降の起動)
4. [ファイルの保存場所](#4-ファイルの保存場所)
5. [最初のドラムMIDIを生成する](#5-最初のドラムmidiを生成する)
6. [JSONで生成条件を保存する](#6-jsonで生成条件を保存する)
7. [タグを管理する](#7-タグを管理する)
8. [素材を検索する](#8-素材を検索する)
9. [グルーヴを指定する](#9-グルーヴを指定する)
10. [ギター素材を分析・検索する](#10-ギター素材を分析検索する)
11. [参考音源を解析する](#11-参考音源を解析する)
12. [チャンネルを確認・変換する](#12-チャンネルを確認変換する)
13. [コマンド早見表](#13-コマンド早見表)
14. [エラーへの対処](#14-エラーへの対処)
15. [テスト・バックアップ・再配布](#15-テストバックアップ再配布)

## 1. できること・できないこと

| 機能 | 現在の対応 |
|---|---|
| MIDI素材の一括登録・解析 | 対応 |
| 手動タグ・条件検索・ランキング | 対応 |
| 既存ドラム素材を選び、テンポ・小節数を調整して生成 | 対応 |
| ドラムのグルーヴ解析 | 対応 |
| ギターの音域・リズム・リフ特性解析と検索 | 対応 |
| WAV・MP3・FLACの参考音源解析 | audio追加依存関係の導入で対応 |
| 参考音源を渡すだけで、その音源に合わせたMIDIを自動生成 | 未対応。解析結果を見て、人が生成条件を決める |
| ギターバッキングのMIDI生成 | 生成済みドラムに同期する既存リフの選択方式に対応 |
| ギター追従の単音ベース生成 | 対応（[設定・制約](BASS_FOLLOW.md)） |
| リードギター・シンセのMIDI生成 | 未対応 |
| AIによる自動作曲・自動採譜 | 未対応 |

生成には利用者自身のMIDI素材が必要です。配布ZIPにはサンプルパックや参考音源を含めていません。
Python本体と依存関係の初回インストールには、通常インターネット接続が必要です。
インストール後の素材解析・検索・生成は、ローカルのファイルで実行します。

## 2. 初回インストール

### macOS / Linux

Python **3.11以上**を用意し、ZIPを展開します。
ターミナルを開き、展開した`midi-generator`フォルダへ移動してください。
以下のパスは自分の保存場所に置き換えます。

```bash
cd "/path/to/midi-generator"
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
midi-generator --help
```

### Windows（PowerShell）

```powershell
cd "C:\path\to\midi-generator"
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
midi-generator --help
```

`py -3 --version`も3.11以上であることを確認します。
PowerShellで仮想環境の有効化が制限される場合は、設定を変更せず直接実行できます。

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\midi-generator.exe --help
```

以降の例の`midi-generator`も、この実行ファイルのパスに置き換えられます。

### 参考音源解析・自動テストも使う場合

仮想環境を有効化した状態で、追加依存関係をインストールします。

```bash
python -m pip install -e '.[audio,dev]'
```

MIDIの登録・検索・生成だけなら、基本インストールの`pip install -e .`で利用できます。
すべての自動テストを動かす場合は`audio,dev`を導入してください。

## 3. 2回目以降の起動

毎回インストールし直す必要はありません。ターミナルを開き、プロジェクトへ移動して仮想環境を有効化します。

macOS / Linux：

```bash
cd "/path/to/midi-generator"
source .venv/bin/activate
midi-generator --help
```

Windows PowerShell：

```powershell
cd "C:\path\to\midi-generator"
.\.venv\Scripts\Activate.ps1
midi-generator --help
```

終了時は`deactivate`を実行するか、ターミナルを閉じます。

このガイドのコマンドは**すべてプロジェクトルートで実行する前提**です。
そこには`pyproject.toml`、`src/`、`config/`があります。
別の場所から実行すると、意図したカタログが見つからない場合があります。

## 4. ファイルの保存場所

```text
midi-generator/
├── config/                    チャンネル規則・解析の設定
├── examples/requests/         汎用的な生成リクエスト例
├── data/
│   ├── sources/               自分で用意したMIDI素材
│   │   ├── drums/metalcore/
│   │   ├── drums/hardcore/
│   │   ├── guitar/backing/
│   │   ├── guitar/lead/
│   │   ├── bass/other/
│   │   ├── synth/other/
│   │   └── unclassified/
│   ├── references/            参考音源
│   ├── catalog/               カタログ・楽器別解析JSON
│   ├── cache/                 音源解析などの中間JSON
│   ├── reports/               保存した確認結果
│   └── backups/               バックアップ
└── output/                    生成MIDIと生成条件・出典JSON
```

素材はファイル管理アプリでコピーして構いません。
楽器は`drums`、`guitar`、`bass`、`synth`などの**フォルダ名**で判定します。
チャンネル番号から楽器を推測する方式ではありません。
ギターはbacking/lead直下だけを正式な役割分類に使います。ギターのジャンル別階層は新設しません。
ジャンル・セクションはタグで管理します。旧配置のギターは未分類のまま読み込めます。
他楽器でも、検索・生成で使う`style`はタグとして確認・登録してください。

## 5. 最初のドラムMIDIを生成する

まず、**内容を確認済みの4/4ドラム素材**を1件用意してください。
以下では、`data/sources/drums/metalcore/example.mid`にコピーしたものとします。

### 手順1：素材を登録する

```bash
midi-generator catalog scan
```

`data/sources/`以下の`.mid`と`.midi`を再帰的に読み込み、
`data/catalog/midi_catalog.json`を作成・更新します。元MIDIは変更しません。
エラーがあるファイルは記録し、他のファイルの処理を続けます。

### 手順2：チャンネルと内容を確認する

```bash
midi-generator source validate-channels
midi-generator midi analyze "data/sources/drums/metalcore/example.mid"
```

ドラムの標準チャンネルは10です。チャンネル1でも、フォルダが`drums`ならドラム素材として扱います。
チャンネル変換が必要な場合の手順は[後述](#12-チャンネルを確認変換する)しています。

### 手順3：セクション・スタイルを付ける

内容を聴いて、イントロに使う素材だと確認できた場合の例です。

```bash
midi-generator tag add "data/sources/drums/metalcore/example.mid" --section intro --style metalcore
midi-generator tag show "data/sources/drums/metalcore/example.mid"
```

`intro`などのセクションは、自動解析だけでは付与されません。
既存素材の内容に合ったタグを付けてください。

### 手順4：検索して、候補があることを確認する

```bash
midi-generator search --instrument drums --section intro --style metalcore --time-signature 4/4
```

結果が`[]`なら該当素材がありません。先に登録先・タグ・拍子を確認します。

### 手順5：120 BPM・8小節を生成する

```bash
midi-generator generate --instrument drums --section intro --tempo 120 --bars 8 --time-signature 4/4 --style metalcore --seed 42 --output output/drums_intro_120bpm_8bars.mid
```

出力の`output_files`に実際の保存先が表示されます。同名ファイルがある場合は連番になります。
`output/`にはMIDIと、通常その隣に`.mid.json`の生成記録が作成されます。

- 元の素材は変更しません。
- 短い素材は小節単位でループし、長い素材は指定長でトリミングします。
- ノート番号を維持し、出力のドラムチャンネルは標準で10になります。
- 出力テンポを120 BPMにする操作であり、元素材を120 BPMに限定して探す操作ではありません。
- 同じ素材・カタログ・解析設定・入力条件・seedなら、選択結果を再現できます。
- MIDIの音色は含まれません。DAWではドラム音源に読み込んで確認してください。

## 6. JSONで生成条件を保存する

テキストエディタで、次の内容を`output/my_intro.request.json`として保存します。
ファイル名は任意です。JSONでは半角の`"`を使い、コメントや末尾の余分なカンマを入れないでください。

```json
{
  "instrument": "drums",
  "section": "intro",
  "tempo": 120,
  "bars": 8,
  "time_signature": [4, 4],
  "style": ["metalcore"],
  "seed": 42
}
```

実行します。

```bash
midi-generator generate --config output/my_intro.request.json --output output/my_intro.mid
```

JSONの一部だけをCLI引数で変更することもできます。指定したCLI引数が優先されます。

```bash
midi-generator generate --config output/my_intro.request.json --tempo 140 --bars 4 --output output/my_intro_140.mid
```

必須項目は`instrument`、`section`、`tempo`、`bars`です。拍子の省略時は4/4になります。
`style`や`tags`を追加すると候補が絞られ、条件不一致で生成できなくなる場合があります。
同梱の`examples/requests/drums_breakdown.json`にも利用例がありますが、
その条件の素材とタグが登録されていなければ生成できません。

## 7. タグを管理する

`<file_id>`には検索結果の`file_id`を指定します。
現在のファイルパスでも指定でき、ファイル名だけの場合はカタログ内で一意である必要があります。
移行済み素材では`file_id`と現在のファイルパスが異なる場合があります。

追加：

```bash
midi-generator tag add "example.mid" --section breakdown --style metalcore --tags half_time,double_kick
```

表示：

```bash
midi-generator tag show "example.mid"
```

変更：

```bash
midi-generator tag update "example.mid" --section verse --tags chug
```

`update`は指定した分類の値を置き換えます。上の例では既存の自由タグ一覧が`chug`に置き換わります。
既存の自由タグを残して追加したい場合は`add`を使います。指定しなかった分類は維持されます。

一部削除：

```bash
midi-generator tag remove "example.mid" --tags half_time
```

主な分類値：

| 分類 | 指定できる値 |
|---|---|
| instrument | `drums`, `guitar`, `bass`, `synth`, `other` |
| section | `intro`, `verse`, `pre_chorus`, `chorus`, `breakdown`, `bridge`, `outro`, `fill`, `transition`, `unknown` |
| style | `metalcore`, `hardcore`, `post_hardcore`, `deathcore`, `alternative_metal`, `electronic`, `other` |
| 自由タグ | `half_time`等。ユーザー独自の値を追加可能 |

タグの重複は除かれ、再スキャンしても手動タグは維持されます。
タグを消しても、フォルダ由来の楽器分類を解除する操作にはなりません。

## 8. 素材を検索する

基本検索：

```bash
midi-generator search --instrument drums --section breakdown
```

BPM・拍子・小節数を絞る：

```bash
midi-generator search --instrument drums --bpm-min 90 --bpm-max 130 --time-signature 4/4 --bars-min 2 --bars-max 8
```

自由タグを検索する：

```bash
midi-generator search --instrument drums --tags half_time,double_kick
midi-generator search --instrument drums --required-tags half_time,double_kick
```

- 検索の`--tags`：いずれか1つ以上の一致が必要。一致率もスコアに反映します。
- `--required-tags`：指定したすべてのタグが必要です。
- **生成の`--tags`はすべて必須**です。検索の`--tags`とは扱いが異なります。
- スコアは条件への適合度です。音楽的な良し悪しを表す値ではありません。
- 同点時もファイルパスで順位が安定します。

追加の絞り込みには`--notes-min`、`--notes-max`、`--pitch-min`、`--pitch-max`があります。
音域はMIDI番号0〜127で指定し、素材の音域との重なりで検索します。

検索結果をJSONとして保存するには、シェルのリダイレクトを使用できます。
以下の`>`は既存の保存先を上書きするので、新しいファイル名を選んでください。

```bash
midi-generator search --instrument drums > data/reports/drum_search.json
```

## 9. グルーヴを指定する

### 先に素材のグルーヴを解析する

```bash
midi-generator catalog analyze-grooves
```

`data/catalog/drum_groove_analysis.json`を作成・更新します。
元MIDIや手動タグは変更しません。

### 16分主体の素材を優先して探す

```bash
midi-generator search --instrument drums --subdivision sixteenth --timing-feel straight --syncopation medium
```

### 120 BPM・8小節の生成でグルーヴを指定する

```bash
midi-generator generate --instrument drums --section intro --tempo 120 --bars 8 --time-signature 4/4 --subdivision sixteenth --timing-feel straight --syncopation medium --seed 42 --output output/intro_16th.mid
```

**ドラムのグルーヴ条件は優先条件です。完全一致しない候補が返る場合があります。**
生成記録の`groove_selection`と`differences`を確認してください。
指定したリズムへノートを新たに並べ替える機能ではありません。
楽器・セクション・拍子・必須タグ等は引き続き必須条件です。

| 引数 | 値 |
|---|---|
| `--subdivision` | `eighth`, `sixteenth`, `mixed`, `unknown` |
| `--timing-feel` | `straight`, `swung`, `unknown` |
| `--syncopation` | `low`, `medium`, `high`, `unknown` |
| `--backbeat` | `standard`, `half_time`, `double_time`, `other`, `unknown` |
| `--double-kick` | `none`, `light`, `medium`, `heavy`, `unknown` |
| `--density` | `low`, `medium`, `high`, `unknown` |
| `--tightness` | 0〜1の目安。ドラムでは優先する最小値 |

解析後に素材を変更・チャンネル校正した場合は、スキャンと解析をやり直してください。

## 10. ギター素材を分析・検索する

新規素材は自分で役割を決めて`data/sources/guitar/backing/`または`lead/`直下へ格納します。
既存素材の移動時は、**先に[参照更新](GUITAR_SOURCE_MIGRATION.md)を完了してから**スキャンしてください。
以下は新規素材を登録・解析する操作です。

```bash
midi-generator catalog scan
midi-generator catalog analyze-guitars
midi-generator search --instrument guitar --subdivision sixteenth
midi-generator search --instrument guitar --syncopation high
midi-generator search --instrument guitar --riff-characteristic chug
```

複数のリフ特性をすべて満たすものを検索する例：

```bash
midi-generator search --instrument guitar --riff-characteristic tremolo_like --riff-characteristic stop_and_go
```

リフ特性は`chug`、`syncopated_riff`、`gallop`、`stop_and_go`、`power_chord_riff`、
`single_note_riff`、`open_chord_riff`、`tremolo_like`、`mixed`、`unknown`です。

ギターではリフ特性・subdivision・syncopation・densityが**絞り込み条件**になります。
ドラムの優先条件とは異なり、一致しなければ結果から除外します。
ギターの`--tightness`は最小値の絞り込みです。
結果が0件でもエラーではなく、現在の素材に条件一致がないことを示します。

解析JSONは`data/catalog/guitar_riff_analysis.json`です。
低音の奏法切替キーが演奏ノートに混在する素材では、音域・和音・同時発音の結果に影響します。
警告を確認し、推定ラベルを確定した奏法・チューニングとして扱わないでください。
解析の詳細は[ギター解析ガイド](GUITAR_RIFFS.md)を参照してください。

### 正式な役割で検索・参照更新する

```bash
midi-generator search --instrument guitar --guitar-role backing
midi-generator search --instrument guitar --guitar-role lead
midi-generator source reconcile-guitars --dry-run
```

ロール検索はそのフォルダ直下の素材だけを返し、旧配置や自由タグだけの素材を含めません。
手動で移した後はDry Runを確認してから`source reconcile-guitars --apply`でJSON参照を更新します。
曖昧な対応は停止し、元MIDIを自動移動しません。[移行・バックアップ・復旧手順](GUITAR_SOURCE_MIGRATION.md)を参照してください。

### ドラム同期型バッキングを生成する

Phase 6.2-Aではバッキング生成に対応しています。リードは未対応です。
正式なギターロールはbacking/lead直下の配置から取得し、セクション・styleはタグで管理します。
自由タグやファイル名から正式ロールを確定しません。以下は既存生成プロトタイプの呼び出しです。
プロトタイプ内部の推定・タグヒントは正式なカタログ分類とは別です。

```bash
midi-generator generate --instrument guitar --guitar-role backing --section intro --tempo 160 --bars 8 --time-signature 4/4 --style metalcore --sync-to-drums output/archive/early_validation/drums_intro_160bpm_8bars.mid --seed 42 --output output/archive/early_validation/guitar_backing_intro.mid
```

参照ドラムのBPM・拍子は生成指定と一致が必要です。
キックとの一致、休符、反復、フレーズ長で候補を選び、元の音程・コード構成・アタック位置を保持します。
完全一致しないリフも選ばれるため、隣の`.mid.json`に記録された同期スコアと警告を確認してください。
詳しい準備・API・重み設定は[バッキング生成ガイド](GUITAR_BACKING.md)を参照してください。

## 11. 参考音源を解析する

音源を`data/references/`へ保存します。以下は、`reference.mp3`の先頭14秒を解析する例です。

```bash
midi-generator reference analyze "data/references/reference.mp3" --start 0 --end 14 --output data/cache/reference_intro.json
```

`--start`と`--end`は**秒数**です。1分30秒は`90`と指定します。
`--end`は開始からの長さではなく、音源全体での終了位置です。
音源の長さを超える範囲はエラーになります。省略時は全体が対象です。

推定テンポ、発音のタイミング、密度、エネルギー等をJSONへ保存します。
すべての楽器を含む音源の特徴なので、ドラムのノートや正確な拍頭を抽出した結果ではありません。
既存JSONの上書きには、明示的に`--overwrite`が必要です。

参考音源を使う現在の流れは次のとおりです。

1. 音源の区間を解析する。
2. 出力JSONと試聴をもとに、テンポ・セクション・グルーヴ条件を人が決める。
3. ドラム素材を検索して、通常の`generate`を実行する。

「参考音源から自動生成」や「参考音源のドラムをそのまま採譜」は未対応です。
MP3を読み込めない場合の詳細は[参考音源解析ガイド](REFERENCE_AUDIO.md)を参照してください。

## 12. チャンネルを確認・変換する

表示・CLI入力は1〜16です。ドラムは10、ギターは1、ベースは2、シンセは3が標準設定です。
`config/instrument_channels.json`で変更できます。

全素材を検証：

```bash
midi-generator source validate-channels
```

校正対象のプレビュー（ファイル変更なし）：

```bash
midi-generator source correct-channels --dry-run
```

単一ファイルを別名でチャンネル10へ変換：

```bash
midi-generator midi convert-channel "data/sources/drums/metalcore/example.mid" --to 10 --dry-run
midi-generator midi convert-channel "data/sources/drums/metalcore/example.mid" --from 1 --to 10 --output output/example_ch10.mid
```

`--from`を省略すると、すべてのチャンネル付きメッセージが対象です。
チャンネル変換はノート番号・ベロシティ・タイミングを維持し、既存の出力先は上書きしません。
変換だけで`instrument=drums`の手動タグが付くことはありません。
コピーを素材として登録する場合は、内容確認後に適切な`data/sources/`内へコピーしてスキャンします。

以下は**実際に校正する操作**です。プレビューを確認した場合だけ使用してください。

元ソースを保護し、校正済みコピーを`output/corrected_sources/`へ作る：

```bash
midi-generator source correct-channels --apply --copy-output
```

標準設定で元ソースをバックアップ後に校正する：

```bash
midi-generator source correct-channels --apply
```

後者は元ファイルを変更します。`data/backups/channel-correction-.../`にバックアップが作成されます。
複数チャンネルの素材は`mixed`として自動校正をスキップします。
元ソースを校正した後は、必要な解析レポートを再作成してください。

## 13. コマンド早見表

| やりたいこと | コマンド |
|---|---|
| 全体のヘルプ | `midi-generator --help` |
| 生成の引数を調べる | `midi-generator generate --help` |
| 検索の引数を調べる | `midi-generator search --help` |
| MIDIの基本情報を見る | `midi-generator inspect input.mid` |
| ノート番号・チャンネル・GM候補を見る | `midi-generator midi analyze input.mid` |
| 別名で読み書きする | `midi-generator round-trip input.mid output/copy.mid` |
| ソース一覧 | `midi-generator source list` |
| ドラムソースの詳細 | `midi-generator source inspect --instrument drums` |
| カタログ更新 | `midi-generator catalog scan` |
| ドラム候補A〜Dのレポート | `midi-generator catalog analyze-drums --report` |
| ドラム候補CSVを保存 | `midi-generator catalog analyze-drums --csv data/reports/drum_candidates.csv` |
| ドラムグルーヴ解析 | `midi-generator catalog analyze-grooves` |
| ギターリフ解析 | `midi-generator catalog analyze-guitars` |
| ギター参照更新のプレビュー | `midi-generator source reconcile-guitars --dry-run` |
| 正式Backingの検索 | `midi-generator search --instrument guitar --guitar-role backing` |

`analyze-drums`は候補判定であり、手動の楽器タグを自動付与しません。
`source migrate`はギター以外の素材を移動する管理用操作です。ギターは手動配置後に`source reconcile-guitars`を使います。すでに楽器別に格納した通常運用では不要です。

## 14. エラーへの対処

### `command not found: midi-generator`

プロジェクトルートへ移動し、仮想環境を有効化します。
それでも見つからない場合は、macOS/Linuxなら直接実行してください。

```bash
.venv/bin/midi-generator --help
```

### `ModuleNotFoundError: No module named 'midi_generator'`

同じ仮想環境のPythonを使って、プロジェクトルートから再インストールします。
macOS/Linuxの例です。

```bash
.venv/bin/python -m pip install -e .
.venv/bin/python -c "import midi_generator; print(midi_generator.__file__)"
.venv/bin/midi-generator --help
```

`src/midi_generator/`が存在するか確認してください。
プロジェクトを移動した場合や別PCへ渡した場合は、コピーした仮想環境を使わず、
移動先で`.venv`を作り直してインストールします。

### `No catalog MIDI pattern matches ...`

素材不足または必須条件の不一致です。

1. `catalog scan`を実行したか確認する。
2. `tag show`で楽器・セクション・スタイル・自由タグを確認する。
3. `search --instrument drums --section intro`等の少ない条件から検索する。
4. 素材の拍子が生成指定と一致しているか確認する。
5. 不要な必須タグ・スタイル指定を外す。事実と異なるタグを付けて回避しない。

該当素材がない場合、架空の素材で代用することはありません。

### `No usable groove analysis ...` / `stale` / 解析結果が見つからない

ドラムなら`catalog analyze-grooves`、ギターなら`catalog analyze-guitars`を実行します。
素材変更後は`catalog scan`も実行してください。
解析レポートを独自の場所へ保存した場合は、検索・生成時にも対応するパスを指定します。

```bash
midi-generator search --instrument drums --subdivision sixteenth --groove-analysis data/catalog/drum_groove_analysis.json
midi-generator search --instrument guitar --subdivision sixteenth --guitar-analysis data/catalog/guitar_riff_analysis.json
```

### `FileNotFoundError` / ファイルが見つからない

現在位置とファイル名を確認します。空白や`#`を含むパスは`"..."`で囲んでください。
旧配置の`references/`は現在`data/references/`です。
旧配置の`configs/examples/`は現在`examples/requests/`です。

### JSONが読み込めない

半角の二重引用符、カンマ、括弧を確認します。次のコマンドで構文を確認できます。

```bash
python -m json.tool output/my_intro.request.json
```

### 参考音源解析のライブラリがない

仮想環境内で追加インストールします。

```bash
python -m pip install -e '.[audio]'
```

### 生成したMIDIから音が出ない・音色が違う

MIDIは音声ファイルではありません。DAWにドラム音源を用意し、生成MIDIを読み込みます。
チャンネル10、ドラム音源のノートマッピング、ルーティングを確認してください。
プログラムは元素材のノート番号を維持するため、異なる音源間のマッピング互換性は保証しません。

## 15. テスト・バックアップ・再配布

全テスト：

```bash
python -m pip install -e '.[audio,dev]'
python -m pytest
```

タグ・出典・登録状態は`data/catalog/`にあります。バックアップでは元MIDIだけでなく、
カタログ、利用した`config/`、生成リクエスト、生成MIDIの`.mid.json`も保存してください。
再スキャン時、消えた素材のエントリは`present: false`で保持されます。
不要な素材情報を消すためにカタログJSON全体を削除すると、手動タグ等も失われます。

再配布ZIPの作成：

```bash
python scripts/package_release.py --output dist/midi-generator-with-cli-reference.zip
```

このガイドも同梱されます。すでに同名ZIPがある場合は別名を指定してください。
配布物に素材・音源・個人カタログ・仮想環境は入りません。
利用条件と配布範囲は[再配布ガイド](DISTRIBUTION.md)、
全体の配置は[フォルダ構成ガイド](PROJECT_LAYOUT.md)で確認できます。

### Reference Pattern Map（Phase 7.1 Step 3）

指定区間の小節候補・活動量・反復・休止候補を独立JSONへ保存します。

```bash
.venv/bin/midi-generator reference pattern-map "data/references/The Queendom.wav" \
  --start 0 --end 90 --time-signature 4/4 --bar-start 0 \
  --section-name intro
```

保存先は既定で `data/cache/reference_patterns/<内容ハッシュ>.json`。
`--output` で別のJSONパスを指定できます。同じ内容は再利用し、異なる既存ファイルは上書きしません。
BPM・拍子・小節起点は解析上の仮説です。未指定の拍子は暫定4/4で、確定判定ではありません。
従来の `reference analyze` とそのキャッシュは変更しません。
詳しくは [Reference Pattern Map](REFERENCE_PATTERN_MAP.md) を参照してください。

Referenceの実テンポを確認して手動指定する場合だけ、解析コマンドへ
`--reference-tempo 120` を追加してください。省略時は音源から自動推定し、
推定不能なら不明のまま保持します。希望するMIDI出力BPMは渡しません。
旧 `reference pattern-map --tempo 120` は警告付きの互換指定です。
新旧の同時指定はエラーです。生成用の `generate --tempo 105` は従来どおりです。

### Reference生成計画（Phase 7.2 Step 2・MIDIは生成しない）

```bash
.venv/bin/midi-generator reference plan \
  --config output/reference_plans/not_my_time_chorus.input.json \
  --output output/reference_plans/not_my_time_chorus.json
```

設定JSONには `pattern_map_path`、`export_bpm`、`bar_range`、`section`、
`time_signature`、`repeat_blocks`、`reference_bpm_confirmed`、`bar_start_confirmed`
を指定します。小節範囲は両端を含み、中間小節も対象です。
`repeat_blocks` は `[{"source": [16, 17], "reuse": [36, 37]}]` の形式です。
小節起点などが未確認なら実行条件未充足として保存します。
条件が揃っても実行機能はまだありません。既存ファイルは上書きしません。
[仕様・Python API例](REFERENCE_GENERATION_PLAN.md)を参照してください。
