"""Implementer（実装者）の実行環境。

Claude tool use ループで自前ツール（write_file / read_file / python_exec）を回し、
モデレーターの結論を実際の成果物（ファイル）に変換する。
出力先は `./output/<session_id>/` 配下に閉じ込める。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ai_debate.agents import IMPLEMENTER
from ai_debate.client import Client
from ai_debate.debate import DebateResult, Turn

MAX_ITERATIONS = 15
PYTHON_EXEC_TIMEOUT = 30  # seconds


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "write_file",
        "description": (
            "成果物のファイルを作成する。pathは出力ディレクトリ配下の相対パス（例: 'plan.md', 'src/main.py'）。"
            "親ディレクトリは自動で作られる。絶対パスや'..'を含むパスは拒否される。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "出力ディレクトリからの相対パス",
                },
                "content": {
                    "type": "string",
                    "description": "ファイルの中身（テキスト）",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "自分が書いたファイルを読み返す。pathはwrite_fileと同じ相対パス。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "python_exec",
        "description": (
            "短時間で終わるPythonコードを実行する。出力ディレクトリをカレントディレクトリとして実行されるので、"
            "write_fileで作成したファイルを相対パスで参照できる。タイムアウトは30秒。"
            "外部ネットワーク呼び出しは避けること。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "実行するPythonコード",
                },
            },
            "required": ["code"],
        },
    },
]


@dataclass
class ImplementerResult:
    output_dir: Path
    files_written: list[str] = field(default_factory=list)
    final_message: str = ""
    iterations: int = 0


def _slugify(text: str, max_len: int = 30) -> str:
    """お題から安全なディレクトリ名を作る。"""
    text = text.strip().lower()
    # 日本語を含むので、英数字・ハイフン・アンダースコア以外を全部潰す
    text = re.sub(r"[^\w\-]+", "-", text, flags=re.UNICODE)
    text = text.strip("-_")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-_")
    return text or "topic"


def make_session_dir(output_root: Path, topic: str) -> Path:
    """`./output/YYYYMMDD-HHMMSS-<slug>/` を作成して返す。"""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _slugify(topic)
    session_dir = output_root / f"{timestamp}-{slug}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _safe_resolve(session_dir: Path, rel_path: str) -> Path:
    """rel_pathを session_dir 配下に解決する。範囲外なら例外。"""
    if not rel_path:
        raise ValueError("空のパスは許可されていません")
    p = Path(rel_path)
    if p.is_absolute():
        raise ValueError(f"絶対パスは許可されていません: {rel_path}")
    resolved = (session_dir / p).resolve()
    base = session_dir.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as e:
        raise ValueError(f"出力ディレクトリの範囲外です: {rel_path}") from e
    return resolved


def _tool_write_file(session_dir: Path, path: str, content: str) -> dict[str, Any]:
    full_path = _safe_resolve(session_dir, path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    rel = full_path.relative_to(session_dir.resolve())
    return {"ok": True, "path": str(rel), "bytes": len(content.encode("utf-8"))}


def _tool_read_file(session_dir: Path, path: str) -> dict[str, Any]:
    full_path = _safe_resolve(session_dir, path)
    if not full_path.exists():
        return {"ok": False, "error": f"ファイルが存在しません: {path}"}
    return {"ok": True, "content": full_path.read_text(encoding="utf-8")}


def _tool_python_exec(session_dir: Path, code: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(session_dir),
            capture_output=True,
            text=True,
            timeout=PYTHON_EXEC_TIMEOUT,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],  # 念のため上限
            "stderr": proc.stderr[-4000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"タイムアウト（{PYTHON_EXEC_TIMEOUT}秒）",
        }
    except Exception as e:
        return {"ok": False, "error": f"実行エラー: {type(e).__name__}: {e}"}


def _dispatch_tool(session_dir: Path, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "write_file":
            return _tool_write_file(session_dir, tool_input["path"], tool_input["content"])
        if name == "read_file":
            return _tool_read_file(session_dir, tool_input["path"])
        if name == "python_exec":
            return _tool_python_exec(session_dir, tool_input["code"])
        return {"ok": False, "error": f"未知のツール: {name}"}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


def _build_initial_prompt(topic: str, history: list[Turn], synthesis: str, session_dir: Path) -> str:
    """Implementerに渡す最初のメッセージ。"""
    return f"""# お題
{topic}

# モデレーターによる議論サマリと成果物仕様
{synthesis}

# あなたの作業ディレクトリ
{session_dir}
（write_file等のpathはここからの相対パス）

# 指示
上記の「成果物仕様」に従って、実際にファイルを作成してください。
作業手順:
1. 必要なファイルを write_file で作成
2. コードを生成した場合は python_exec で軽く動作確認
3. 最後に index.md を作り、何を作ったか・どう使うかをまとめる
4. 完了したらツールを呼ばずにテキストで完了報告

ではお願いします。"""


ToolEvent = Callable[[str, dict[str, Any], dict[str, Any]], None]


def run_implementer(
    debate_result: DebateResult,
    session_dir: Path,
    *,
    client: Client | None = None,
    on_tool_call: ToolEvent | None = None,
    on_text: Callable[[str], None] | None = None,
    max_iterations: int = MAX_ITERATIONS,
) -> ImplementerResult:
    """Implementerをtool useループで動かし、成果物を生成する。"""
    if client is None:
        client = Client()

    initial_prompt = _build_initial_prompt(
        debate_result.topic, debate_result.history, debate_result.synthesis, session_dir
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": initial_prompt}]

    files_written: list[str] = []
    final_text = ""

    for iteration in range(max_iterations):
        response = client.call_with_tools(
            system=IMPLEMENTER.system_prompt,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            max_tokens=8000,
        )

        # アシスタントのレスポンスを履歴に追加
        messages.append({"role": "assistant", "content": response.content})

        # text blocksを抽出して通知
        for block in response.content:
            if block.type == "text" and block.text.strip():
                if on_text is not None:
                    on_text(block.text)
                final_text = block.text  # 最後のテキストが最終報告

        # tool_use がなければ終了
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "end_turn" or not tool_use_blocks:
            return ImplementerResult(
                output_dir=session_dir,
                files_written=files_written,
                final_message=final_text,
                iterations=iteration + 1,
            )

        # ツールを実行して結果を返す
        tool_results: list[dict[str, Any]] = []
        for tu in tool_use_blocks:
            result = _dispatch_tool(session_dir, tu.name, dict(tu.input))
            if on_tool_call is not None:
                on_tool_call(tu.name, dict(tu.input), result)
            if tu.name == "write_file" and result.get("ok"):
                files_written.append(result["path"])
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    # max_iterations 到達
    return ImplementerResult(
        output_dir=session_dir,
        files_written=files_written,
        final_message=final_text or f"⚠️ 最大反復数（{max_iterations}）に到達しました。",
        iterations=max_iterations,
    )
