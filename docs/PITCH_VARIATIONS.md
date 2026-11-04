# Phase 9 Step 6：リズム固定の音程展開実験

Step 3-BのChorus先頭8小節を固定し、ルート音程だけを動かす任意の比較。
既存生成器、Phase 8.1 A2、既定値、Step 3-B原本は変更しない。

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/create_pitch_variation_auditions.py \
  examples/requests/chorus_pitch_variations.json output/phase9_step6_pitch_variations
```

出力先は未使用の新規ディレクトリが必要。requestに正規ソースパスとSHA-256、
全60グループのルート列、明示的な音階・範囲を持たせ、変更をレビュー可能にする。
Eマイナーのルート52/54/55/57/59は今回ユーザーが指定した比較条件であり、
Referenceのキーやチューニングを推定したものではない。
ギター範囲52..66、ベース28..35は明示的な比較制限。音源固有の発音可能域は未確認。

- A: Step 3-Bのバンド・ギター・ベースをバイト一致で保持。
- B: 60グループ中4グループのルートを変更。基準52中心、反復箇所と終端だけ動かす。
- C: 48グループを変更。3–4/7–8小節に別の応答を置き、1–2=5–6を保持。
- ルート移動の最大幅はB=3半音、C=4半音。ランダムな変更はない。
- F#ルート54は単音グループだけに配置する。固定root+7のまま二音化するとC#が
  入るため、和音構成を変更することなく、全構成音をE自然短音階内に維持する。

各グループを構成するNote On/Offのnote値だけを同量移動する。
絶対tick、音価、休符、velocity、発音グループ数、内部音程、チャンネル、
Program Change、marker等の補助メッセージは維持する。
既存のgenerate(instrument='bass')を使い、ルート−24半音、同じOn/Offとvelocityで
ベース追従を生成する。最終ベースは基準のtrack名・marker等も維持し、A2の発音内容だけを反映する。A2のロジックは変更しない。

主ファイルchorus_A_baseline.mid / chorus_B_limited_pitch.mid /
chorus_C_expanded_pitch.midが、ギター・ベース・ドラムのバンド統合MIDI。
各パート単体も保存し、drums_shared.midと候補別drums_A/B/C.midは原本とバイト一致。
一時的なAPIベース出力はgeneration_work内に保存し、既存ファイルを上書きしない。

同じ入力から再実行して全MIDIのSHA-256一致を確認する。
候補を自動採用せず、音源を固定したDAW試聴で比較する。
Loud Foundryの奏法キーや具体的な音程配置は模倣しない。
