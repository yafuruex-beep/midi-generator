# フォルダ構成と移行記録

```text
midi-generator/
├── README.md                 導入・操作方法
├── AGENTS.md                 開発ルール
├── pyproject.toml            パッケージ・依存関係・CLI設定
├── MANIFEST.in               ビルド補助の同梱設定
├── build_support/            editable install用バックエンド
├── src/midi_generator/       プログラム本体（唯一の正本）
│   ├── core/                 共通モデル・MIDI入出力
│   ├── modules/              楽器別生成・解析
│   ├── storage/              カタログ・タグ・検索・ソース管理
│   └── reference_analysis/   参考音源解析
├── config/                   実際に読み込まれる共通設定
├── examples/
│   ├── python_api_example.py Python API使用例
│   └── requests/             汎用生成リクエスト例
├── schemas/                  JSON Schema
├── tests/                    外部素材不要の自動テスト
├── scripts/                  再配布ZIPの作成
├── docs/                     設計・開発状況・各機能ガイド
├── data/                     ローカルの入力・管理データ
│   ├── sources/              MIDI素材（ギターはbacking/lead、他は既存構造）
│   ├── references/           参考音源と出典メタデータ
│   ├── catalog/              現行のカタログ・解析JSON
│   ├── cache/                再生成できる中間データ
│   ├── reports/              検証結果と過去の実行記録
│   ├── archive/              現行処理で使わない過去データ
│   └── backups/              復元用バックアップ
├── output/                   生成MIDI・生成条件・出典のサイドカー
└── dist/                     素材を含まない再配布ZIP（作成後）
```

`.venv/`はこのPCの実行環境です。第三者は展開先で新しく作成します。
`build/`、`*.egg-info/`、`__pycache__/`、`.pytest_cache/`は自動生成物です。
インストールやテストで再び現れる場合があり、ソースの正本ではありません。
`build_support/`は自動生成物ではなく、インストールに必要なコードです。

## 今回の整理

| 以前 | 現在 |
|---|---|
| `configs/examples/drums_breakdown.json` | `examples/requests/drums_breakdown.json` |
| `references/` | `data/references/` |
| ルートの`DESIGN.md` / `PROJECT_STATUS.md` | `docs/`内の同名ファイル |
| `output/`内の検証レポート | `data/reports/` |
| `data/catalog/drum_analysis 2.json`等 | `data/archive/catalog/` |
| 未使用の`config/defaults.json`・`module_registry.json` | `data/archive/scaffold/config/` |
| 未実装のギター生成リクエスト例 | `data/archive/scaffold/examples/` |
| 追加の`.venv-1/` | レイアウト整理バックアップ内の圧縮アーカイブ |

番号付きのドラム解析JSONは内容が異なるため、重複と決めつけず保持しました。
過去レポート本文には実行当時のパスやハッシュをそのまま残しています。
現在の場所は上表とバックアップ内`migration.json`の対応表で確認できます。
現行の生成リクエスト・出典サイドカー・音源解析キャッシュは音源の新パスに更新しました。

MIDI素材、生成MIDI、現行カタログの配置・内容、カタログID・手動タグ、
生成・分析アルゴリズムは変更していません。音源の内容も変更していません。

整理前のファイルは`data/backups/layout-refactor-20261031T061801353442Z/`に保存しました。
`workspace-before.tar.gz`は仮想環境・自動生成キャッシュを除く整理前の内容です。
`unused-venv-1.tar.gz`には追加仮想環境を保存しています。
復元時は一度別ディレクトリに展開して確認し、現行ファイルへ直接上書きしないでください。

## 再配布

プロジェクトフォルダを丸ごと圧縮せず、[再配布手順](DISTRIBUTION.md)のスクリプトを使用します。
再配布ZIPには素材、音源、現行カタログ、生成物、過去データ、バックアップ、
仮想環境を含めず、空のデータ格納先を用意します。


## ギターの正式構成（Phase 6.1.1）

ギターの正式な分類先は`data/sources/guitar/backing/`・`lead/`直下だけです。
ジャンルはタグで管理し、ギターのジャンル別フォルダは新設しません。
旧ジャンル階層や直下の素材は互換用に残し、正式ロールは未分類です。
以前のgenre/role階層の推奨はこのルールで置き換えます。
現在の99件は動かしていません。[参照更新の手順](GUITAR_SOURCE_MIGRATION.md)を参照してください。
