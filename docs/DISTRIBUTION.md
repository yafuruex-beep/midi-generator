# ソースコードの再配布

利用者向けの全操作手順は[ターミナル操作リファレンス](CLI_REFERENCE_JA.md)を参照してください。

## ZIP作成

プロジェクトルートから実行します。標準ライブラリのみで動作します。

```bash
python3 scripts/package_release.py
```

`dist/midi-generator-0.1.0-source.zip`を作成します。既存ZIPは上書きしません。
作り直す場合は別名を指定してください。

```bash
python3 scripts/package_release.py --output dist/midi-generator-review.zip
```

コード、テスト、実行時設定、汎用使用例、Schema、ドキュメントを許可リストで収録します。
ローカルのMIDI・参考音源・カタログ・生成物・実行レポート・履歴・バックアップ・
仮想環境は収録しません。`release_manifest.json`には同梱ファイルのSHA-256を記録します。
同じ同梱内容なら同じZIPバイト列になります。過去の動作報告に記載された素材や
レポートは開発環境の記録であり、配布物には含まれません。

ソフトウェア自体の再配布条件を示す`LICENSE`は現時点では未設定です。
公開前に権利者として利用・改変・再配布条件を決めて追加してください。
素材を別途同梱する場合は、その素材の再配布許諾を個別に確認してください。

## 受け取った人のセットアップ

ZIPを展開し、`midi-generator/`へ移動します。Python 3.11以上が必要です。
以下はmacOS/Linuxの例です。Windowsでは仮想環境のPython/CLIが`Scripts/`内に作成されます。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[audio,dev]'
.venv/bin/python -c 'import midi_generator; print(midi_generator.__file__)'
.venv/bin/midi-generator --help
.venv/bin/python -m pytest
```

参考音源解析を使う場合は追加でインストールします。
全テスト（参考音源解析を含む）を動かす場合もaudio extraが必要です。

```bash
.venv/bin/python -m pip install -e '.[audio,dev]'
```

ドラム等は既存の楽器・ジャンル構造、ギターは`data/sources/guitar/backing/`または`lead/`直下へ格納します。配布物には素材がないため、
生成には利用者自身のMIDI登録とセクション等のタグ付けが必要です。
ギターは解析・検索とドラム同期型バッキング生成に対応しています。リード生成は未実装です。

```bash
.venv/bin/midi-generator catalog scan
.venv/bin/midi-generator source validate-channels
.venv/bin/midi-generator catalog analyze-grooves
.venv/bin/midi-generator catalog analyze-guitars
```

参考音源は`data/references/`、生成結果は`output/`へ保存します。
CLIはカレントディレクトリをプロジェクトルートとして使います。展開したルートから
実行するか、対応コマンドの`--project-root`等で指定してください。
`.venv/`はコピーせず、必ず展開先で作成してください。


ギターのジャンル・セクションはタグで管理します。配布時にはギターのジャンル階層を作成しません。
既存の素材を配置し直す場合、スキャンより先に[ギター参照更新](GUITAR_SOURCE_MIGRATION.md)を行ってください。
