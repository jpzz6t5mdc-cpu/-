# ai-debate

4人のAIペルソナがお題について議論し、人間の介入を受け付けつつ、合意した結論を **実際の成果物（コード／Markdownプラン／設計書等）** にまで持っていくCLIツール。

## ペルソナ構成

「理論↔現場 / 推進↔批判」の2軸で議論役4人を配置:

| 役割 | アイコン | 強み |
|---|---|---|
| 戦略家 (Strategist) | 🎯 | 構造化・長期視点・MECE・フレームワーク |
| 行動経済学者 (Behavioral) | 🧠 | エビデンス・心理メカニズム・バイアス・ナッジ |
| 現場の叩き上げ (Field) | 🔥 | 肌感覚・顧客の生の声・実装可能性 |
| 悪魔の代弁者 (DevilsAdvocate) | ⚔️ | リスク発見・前提疑い・失敗シナリオ |

加えて取りまとめ役が2人:

| 役割 | アイコン | 仕事 |
|---|---|---|
| モデレーター (Synthesizer) | 📋 | 合意点 / 対立点 / 最終推奨 / 成果物仕様 にまとめる |
| 実装者 (Implementer) | 🛠 | tool use で実際にファイルを書き出す |

## フロー

```
Round 0 (初動): 戦略家 → 行動経済学者 → 現場 → 悪魔の代弁者
   ↓ ユーザー介入ポイント
Round 1+ (応酬): 同じ4人が他者に名指しで反応・反論
   ↓ ユーザー介入ポイント（ラウンドごと）
モデレーター: 合意点 / 対立点 / 最終推奨 / 成果物仕様
   ↓
Implementer: ./output/<timestamp>-<topic>/ にファイル生成
```

## セットアップ

```bash
# 仮想環境を作って依存をインストール
python -m venv .venv
source .venv/bin/activate
pip install -e .

# APIキーを設定
cp .env.example .env
# .env を編集して ANTHROPIC_API_KEY を入れる
```

## 使い方

```bash
# 既定: 議論 → ユーザー介入 → 成果物生成 まで一気通貫
python -m ai_debate "新規SaaSの差別化アイデア"
# → ./output/20260504-XXXX-新規saasの差別化アイデア/ にMarkdownプラン等が生成される

# AIに任せて自動進行（介入なし）
python -m ai_debate --no-interactive "Pythonでfizzbuzz"

# 議論だけで止めて成果物は作らない
python -m ai_debate --no-build "コーヒーと紅茶どちらが良いか"

# 議論を深くする
python -m ai_debate --rounds 3 "リモートワークの生産性を上げる施策"

# モデルを変える（既定: claude-opus-4-7）
python -m ai_debate --model claude-sonnet-4-6 "..."
```

### インタラクティブセッションのイメージ

```
お題: 新規SaaSの差別化アイデア

=== 🎯 戦略家 ===
（5年スパンの構造化された提案...）

=== 🧠 行動経済学者 ===
（エビデンス込みの心理アプローチ...）

=== 🔥 現場の叩き上げ ===
（顧客の生の声ベースの提案...）

=== ⚔️ 悪魔の代弁者 ===
（前提を疑う / 失敗シナリオ...）

[あなたの番] コメントがあれば入力（空Enterでスキップ／`:::`で複数行モード）>
> ターゲットを中小製造業に絞ってほしい

（応酬ラウンドが続く...）

=== 📋 モデレーター ===
合意点 / 対立点 / 最終推奨 / 成果物仕様

=== 🛠 Implementer ===
[tool] ✅ write_file plan.md
[tool] ✅ write_file sample.py
[tool] ✅ python_exec (returncode=0)
[tool] ✅ write_file index.md
✅ 成果物を作成しました...
```

## オプション

| オプション | 説明 | 既定 |
|---|---|---|
| `--rounds N` | 応酬ラウンド数 | 2 |
| `--no-build` | Implementerフェーズをスキップ | off |
| `--no-interactive` | ユーザー介入なし（自動進行） | off |
| `--model MODEL` | Claudeモデル | `claude-opus-4-7` |
| `--output-dir DIR` | 成果物出力先 | `./output` |
| `--no-color` | ANSIカラー出力を無効化 | 自動判定 |

## 出力構成

```
output/20260504-153022-新規saasの差別化アイデア/
├── _debate_log/
│   ├── debate.md      # 全議論ログ（人間可読）
│   └── debate.json    # 全議論ログ（機械可読）
├── plan.md            # 成果物（モデレーターの仕様に従う）
├── sample.py
└── index.md           # Implementerが書く目次
```

## セキュリティ注意

- Implementerは `./output/<session_id>/` 配下にしかファイルを書けない（パストラバーサル拒否）
- `python_exec` は30秒タイムアウトで `subprocess.run` 実行
- とはいえローカル実行なので、**信頼するお題のみで使うこと**
- 機密情報をお題に書かない

## 開発

```bash
# テスト
pip install pytest
pytest

# テストはすべてモックなのでAPIキー不要
```

## ライブラリとして使う

```python
from ai_debate import run_debate, run_implementer
from ai_debate.executor import make_session_dir
from pathlib import Path

result = run_debate("お題", rounds=2)
print(result.synthesis)

session_dir = make_session_dir(Path("./output"), "お題")
impl_result = run_implementer(result, session_dir)
print(f"Generated: {impl_result.files_written}")
```

## ライセンス

MIT
