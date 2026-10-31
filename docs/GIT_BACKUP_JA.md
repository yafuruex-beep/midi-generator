# GitHubでバージョンを保存する

保存先：<https://github.com/yafuruex-beep/midi-generator>

このプロジェクトはGitで変更履歴を管理し、`git push`でGitHubへ保存します。
Codexを終了していても、ターミナルから同じ操作ができます。
**フォルダを編集しただけではGitHubには反映されません。**

## 変更した後の保存手順

プロジェクトルートから順番に実行します。

```bash
git status --short
git diff
git add -A
git diff --cached --stat
git diff --cached
```

保存する内容を確認したら、変更内容が分かるメッセージで記録して送信します。

```bash
git commit -m "ドラム生成条件の説明を更新"
git push origin main
```

`git commit`はこのPC内への履歴保存、`git push`がGitHubへの送信です。
`nothing to commit`は変更がない状態です。
変更履歴は次のコマンドで確認できます。

```bash
git log --oneline -10
```

`.gitignore`でMIDI素材、参考音源、カタログ、生成物、キャッシュ、バックアップ、
仮想環境やローカル認証情報を除外しています。これらは別途バックアップしてください。
除外された素材を無理に登録する`git add -f`は通常使いません。
コミット前には必ず保存対象を確認してください。

## 別のPCへ取得する

GitとPython 3.11以上を用意します。非公開リポジトリはGitHubの認証が必要です。

```bash
git clone https://github.com/yafuruex-beep/midi-generator.git
cd midi-generator
```

その後は[ターミナル操作リファレンス](CLI_REFERENCE_JA.md)の初回インストールを実行します。
MIDI素材・カタログ等は含まれていないため、必要なデータを別のバックアップからコピーします。
仮想環境はコピーせず、移動先で新しく作成してください。

## 複数のPCで作業する

作業開始前に、未コミットの変更がないか確認してから最新の履歴を取り込みます。

```bash
git status --short
git pull --ff-only origin main
```

`--ff-only`が失敗した場合は両方のPCで別々の変更が進んでいます。
作業内容を確認して統合する必要があります。`--force`で送信したり、
`reset --hard`で現在のファイルを捨てたりせず、まず両方の変更を確認してください。

## 過去のファイルを確認する

```bash
git log --oneline -10
git show COMMIT_ID:README.md
```

`COMMIT_ID`は履歴に表示された短い英数字へ置き換えます。
`git show`は内容を表示するだけで、現在の作業ファイルは変更しません。

## 認証・送信に失敗したとき

- `git remote -v`で保存先が正しいか確認します。
- GitHub側で利用中のアカウントに書き込み権限があるか確認します。
- Gitの認証画面・認証マネージャー、または設定済みのSSH認証を使います。
- パスワードやアクセストークンをコード・設定ファイル・チャットへ貼らないでください。

この手順は手動保存です。定期的な自動送信や、素材自体のオンラインバックアップは設定していません。
