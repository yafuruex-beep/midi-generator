# ギターソースの正式分類と安全な参照更新（Phase 6.1.1）

この変更はフォルダ属性・検索・移行支援に限定します。
承認済みPhase 6.2-A生成プロトタイプは保持し、生成・同期・MIDI解析アルゴリズムは変更していません。
ツールはMIDIファイルを移動・削除・改名・校正しません。

## 正式な配置

```text
data/sources/
├── drums/
│   ├── metalcore/
│   ├── hardcore/
│   └── other/
├── guitar/
│   ├── backing/
│   │   └── 任意のファイル名.mid
│   └── lead/
│       └── 任意のファイル名.mid
├── bass/
└── synth/
```

ギターは**backing/lead直下へMIDIを配置**します。
`guitar/metalcore/`、`guitar/backing/metalcore/`などのジャンル階層は新設しません。
既存の空の旧フォルダは互換性のため残しています。
`guitar/unclassified/`は作らず、旧配置のファイルを勝手にどちらかへ振り分けません。
他楽器やプロジェクト全体の既存配置は変更しません。

| 情報 | 正となる情報 |
|---|---|
| instrument | 楽器フォルダ |
| guitar_role | `guitar/backing/`または`guitar/lead/`直下の配置 |
| ジャンル・セクション | 既存カタログの明示的なタグ |
| リフ・リズム特性 | Phase 6.1の解析 |
| 同一性 | 既存`file_id` / `source_id`と内容ハッシュ |

ファイル名、チャンネル、自由タグ`guitar_backing`、単音/コード比率、音域から
**正式なguitar_roleを自動確定しません**。Leadというファイル名でも旧配置なら役割は未分類です。
旧`guitar/<genre>/`、旧`guitar/<genre>/<role>/`、`guitar/`直下は読み込みを維持し、実効ロールは`null`です。
古いカタログに`guitar_role`がない場合も同じ意味です。新しいスキャンでは属性を保存します。
既存の自由タグは削除しませんが、正式ロール検索の条件には使いません。

## 現状とIDの意味

実データは99件すべてが`data/sources/guitar/`直下です。
旧metalcore/hardcore/otherフォルダにはMIDIがありません。正式分類は99件すべて未確定です。
分類候補の音楽的特徴があっても、今回は配置を変えていません。

既存IDは初回登録時の相対パスで初期化されます。
カタログは`file_id`、ギター解析は`source_id`という名前で、同じIDを参照しています。
移行でUUIDやハッシュIDへ置き換えず、**旧IDのまま現在パスだけを更新**します。

```text
file_id / source_id: data/sources/guitar/old_name.mid    ← 移動しても維持
現在パス:           data/sources/guitar/backing/new.mid ← 配置先へ更新
```

IDをそのままファイルパスとして開かず、カタログの`path`または検索結果の`file_path`を使ってください。
旧パスは`path_history`に保持します。既存タグ・出典・ユーザー属性も維持します。
旧genre_directoryは、必要に応じて`legacy_genre_directory`と`genre_directory_history`に保存します。
ジャンルの手動タグを上書きしたり、backing/leadをstyleへ追加したりしません。

## 手順：既存素材を分類するとき

### 1. 手動配置前にバックアップを取る

ファイル管理アプリ等で、次を新しいバックアップ先へコピーしてください。

- `data/sources/guitar/`全体
- `data/catalog/midi_catalog.json`
- `data/catalog/guitar_riff_analysis.json`

再配布用ZIPには素材・カタログが入らないため、このバックアップの代わりにはなりません。
今回の実装では実素材を移動せず、実環境のJSONにも更新を適用していません。

### 2. 移動前の参照状態を確認する

```bash
midi-generator source reconcile-guitars --dry-run
```

既存素材の登録・解析がまだなければ、**移動する前に**カタログ登録とギター解析を完了させてください。
旧ファイルも信頼できるハッシュもない状態から、同一性を推測して復元することはできません。
現在の99件は登録・解析済みです。

### 3. 自分でBacking/Leadを決めて配置する

内容や用途を確認し、ファイル管理アプリ等で必要なMIDIを`backing/`または`lead/`直下へ移します。
ファイル名の入力規則はありません。同名ファイルがある場合に上書きしないでください。
ツールは移動先もロールも自動決定しません。

元を残したままのコピーは別素材として扱い、移行と決めつけません。
別素材として追加する場合は、元レコードと無条件に統合せず新規登録します。

### 4. スキャンより先にDry Runする

```bash
midi-generator source reconcile-guitars --dry-run
```

表示項目：

| フィールド | 内容 |
|---|---|
| target_count | 照合したカタログ内のギター件数 |
| confirmed_count | IDと内容の対応を確認できた件数 |
| change_count | 参照・正式ロールの更新予定件数 |
| unchanged_count | 現在の参照を維持する件数 |
| unresolved_count | 解決が必要な参照・整合性の問題数。全体エラーも含む |
| unclassified_count | 更新前カタログのパスに正式ロールがない件数 |
| updates | 旧/新パス、旧/新ID、ハッシュ、タグ、旧ジャンル、更新フィールド |
| analysis_context_changes | プロジェクト移転等に伴う解析JSONのルート参照修正 |
| unresolved | 曖昧・欠落・不一致の理由と候補 |

Dry Runは元MIDI・カタログ・解析JSONを変更せず、バックアップやロックも作りません。
旧/新IDは同じ値です。正式ロールが未分類であることと、ID照合が曖昧であることは別です。

移動直後に先に`catalog scan`すると、新素材として別IDを付ける恐れがあります。
移動の可能性をハッシュで検知した場合はスキャンを停止します。
**必ず参照更新を先に行ってからスキャン**してください。

### 5. 曖昧な場合は対応表を自分で確認する

同じ内容のファイルが複数ある場合、ファイル名の類似でペアを決めません。
現在のデータにも、同一内容の2件の組があります。
別レコードのタグ・出典を誤って引き継がないよう、内容が同じ別IDがあれば自動照合を止めます。

例えば次のJSONを`output/guitar_move_mapping.json`へ保存します。
キーは表示された**既存source_id**、値は自分で確認した新しい相対パスです。

```json
{
  "data/sources/guitar/old_name.mid": "data/sources/guitar/backing/new_name.mid"
}
```

```bash
midi-generator source reconcile-guitars --dry-run --mapping output/guitar_move_mapping.json
```

対応表を指定しても、ハッシュ不一致、他IDが使っている移動先、同じ移動先への複数IDの割当ては拒否します。
単にファイル名が一致しているだけでは安全な移行先とはしません。
すでに別IDで登録された移動先を、自動で吸収・削除することもありません。

### 6. 予定を確認したらJSON参照を更新する

```bash
midi-generator source reconcile-guitars --apply
```

対応表を使った場合は適用時にも指定します。

```bash
midi-generator source reconcile-guitars --apply --mapping output/guitar_move_mapping.json
```

更新するのはカタログとギター解析JSONの参照メタデータだけです。
内容ハッシュが一致する解析結果は再解析せず、特徴量・解析日時・解析バージョンを維持します。
一件でも曖昧な参照や不整合があれば、全体を停止し、部分的な確定は行いません。
予定がなければ何も書き換えず、バックアップも作りません。

バックアップ先：

```text
data/backups/guitar-reference-update-<UTC timestamp>/
├── 0.before.json   元カタログの完全なバイト列
├── 1.before.json   元ギター解析JSONの完全なバイト列
└── manifest.json   対象パス、更新前後ハッシュ、処理状態
```

更新内容を再検証し、各JSONを一時ファイルから原子的に置き換えます。
2ファイルを一度に置き換えるOSの単一トランザクションではありません。
バックアップ・ジャーナル・排他ロック・失敗時の巻き戻しを組み合わせています。
通常の失敗では元JSONへ戻し、復元に失敗した場合や異常終了時はロックを残して読書きを止めます。
更新中は他のカタログ編集・スキャンを同時実行しないでください。

### 7. 更新後に確認する

```bash
midi-generator search --instrument guitar --guitar-role backing
midi-generator search --instrument guitar --guitar-role lead
midi-generator catalog scan
```

移動したレコードは旧IDのまま維持されます。
ロール未分類の素材はロール指定検索へ混入しません。
`--guitar-role`を省略した検索は、従来どおり旧配置も含めます。

配置変更と内容編集を同時に行わないでください。先に元の内容のまま参照更新を確定し、
その後に編集してスキャン・ギター解析をやり直します。すでに移動と編集を同時に行った場合は、
バックアップと現状を確認して対応を決めてください。ハッシュ不一致を無視して旧解析を使いません。

## 失敗・異常終了からの復旧

まず更新していたプロセスが終了していることを確認します。
実行結果やロックファイルに表示されたバックアップ先を使います。
既定のカタログ配置なら、ロックに記録された場所は次で確認できます。

```bash
python -m json.tool data/catalog/.guitar-reference-update.lock
```

表示された`backup_directory`を復旧コマンドへ指定してください。

```bash
midi-generator source reconcile-guitars --recover data/backups/guitar-reference-update-EXAMPLE
```

`EXAMPLE`は実際のバックアップディレクトリ名へ置き換えます。
バックアップのハッシュと対象JSONの状態を確認して、更新前のJSONを復元します。
更新後にユーザーが編集した別内容のJSONや、動作中の別トランザクションは上書きしません。
バックアップを改変した場合も復元を拒否します。ロックだけを手動で削除して続行しないでください。

**MIDIの場所は戻しません。** JSONを適用直前へ戻すため、手動で移動したMIDIは新しい場所に残ります。
復旧後に再度Dry Runを行い、参照を安全に確定してください。
過去の生成サイドカーや検証用カタログは実行当時の記録なので、自動書き換えの対象外です。

## Python API

```python
from midi_generator.api import (
    reconcile_guitar_sources, search_patterns,
)

plan = reconcile_guitar_sources()
print(plan["updates"], plan["unresolved"])

backing = search_patterns(instrument="guitar", guitar_role="backing")
```

予定を確認した後の適用は`reconcile_guitar_sources(apply=True)`です。
対応表は`mapping={旧ID: 新相対パス}`で渡せます。
`project_root`、`catalog_path`、`analysis_path`、`backup_directory`も指定可能です。
復旧は`recover_guitar_references(バックアップ先, project_root=...)`でCLIと同じ処理を使います。

## 既存生成プロトタイプとの関係

正式なソース管理・検索と、Phase 6.2-Aプロトタイプ内部の推定ロールは別です。
今回は承認済みプロトタイプを削除・改変せず、同期評価・生成も追加実装していません。
以前のgenre/role階層や自由タグを、正式なguitar_roleとして昇格させることはありません。

次の正式な生成運用へ進む条件は、ユーザーがbacking素材を配置し、style/sectionを確認し、
IDと解析ハッシュの照合を完了することです。
そのうえで、生成側でも正式ロール検索に限定する接続と試聴確認を後続作業として行います。
過去のRiff名による検証集合は今回の正式分類には利用していません。
