"""CLIエントリポイント。議論→介入→成果物生成 までを束ねる。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from ai_debate.agents import label_for
from ai_debate.client import Client, DEFAULT_MODEL
from ai_debate.debate import DebateResult, Turn, run_debate
from ai_debate.executor import ImplementerResult, make_session_dir, run_implementer

# ANSIカラー（簡易版）
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"
    RED = "\033[31m"


ROLE_COLORS = {
    "Strategist": C.CYAN,
    "Behavioral": C.MAGENTA,
    "Field": C.YELLOW,
    "DevilsAdvocate": C.RED,
    "Synthesizer": C.GREEN,
    "User": C.BLUE,
    "Implementer": C.GREEN,
}


def _print_turn(turn: Turn, *, no_color: bool = False) -> None:
    label = label_for(turn.role)
    if no_color:
        print(f"\n=== {label} ===")
        print(turn.content)
    else:
        color = ROLE_COLORS.get(turn.role, "")
        print(f"\n{color}{C.BOLD}=== {label} ==={C.RESET}")
        print(turn.content)


def _read_multiline_input(prompt: str) -> str:
    """1行入力。`:::`で終わる場合は複数行モードに入る。"""
    print(prompt, end="", flush=True)
    try:
        first = input()
    except EOFError:
        return ""
    if first.strip() == ":::":
        # 複数行モード
        print(f"{C.DIM}（複数行入力モード。`:::`単独行で終了）{C.RESET}")
        lines: list[str] = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == ":::":
                break
            lines.append(line)
        return "\n".join(lines)
    return first


def _make_ask_user(no_color: bool) -> Any:
    """ユーザー介入用のask_userコールバックを作る。"""
    def ask_user(history: list[Turn]) -> Optional[str]:
        prompt_text = (
            f"\n{C.BOLD}[あなたの番] コメントがあれば入力（空Enterでスキップ／"
            f"`:::`で複数行モード）>{C.RESET} "
        )
        if no_color:
            prompt_text = "\n[あなたの番] コメントがあれば入力（空Enterでスキップ／`:::`で複数行モード）> "
        text = _read_multiline_input(prompt_text)
        return text or None

    return ask_user


def _save_debate_log(session_dir: Path, debate_result: DebateResult) -> None:
    """議論ログをJSON＋Markdownで保存する。"""
    log_dir = session_dir / "_debate_log"
    log_dir.mkdir(exist_ok=True)

    # JSON（機械可読）
    json_data = {
        "topic": debate_result.topic,
        "history": [{"role": t.role, "content": t.content} for t in debate_result.history],
        "synthesis": debate_result.synthesis,
    }
    (log_dir / "debate.json").write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Markdown（人間可読）
    md_lines = [f"# 議論ログ: {debate_result.topic}", ""]
    for turn in debate_result.history:
        md_lines.append(f"## {label_for(turn.role)}")
        md_lines.append("")
        md_lines.append(turn.content)
        md_lines.append("")
    (log_dir / "debate.md").write_text("\n".join(md_lines), encoding="utf-8")


def _print_tool_call(name: str, tool_input: dict[str, Any], result: dict[str, Any], *, no_color: bool) -> None:
    prefix = "[tool]" if no_color else f"{C.DIM}[tool]{C.RESET}"
    if name == "write_file":
        path = tool_input.get("path", "?")
        ok = "✅" if result.get("ok") else "❌"
        print(f"{prefix} {ok} write_file {path}")
    elif name == "read_file":
        path = tool_input.get("path", "?")
        ok = "✅" if result.get("ok") else "❌"
        print(f"{prefix} {ok} read_file {path}")
    elif name == "python_exec":
        ok = "✅" if result.get("ok") else "❌"
        rc = result.get("returncode", "?")
        print(f"{prefix} {ok} python_exec (returncode={rc})")
        stdout = result.get("stdout", "").strip()
        stderr = result.get("stderr", "").strip()
        if stdout:
            print(f"{prefix}   stdout: {stdout[:200]}")
        if stderr:
            print(f"{prefix}   stderr: {stderr[:200]}")
        if "error" in result:
            print(f"{prefix}   error: {result['error']}")
    else:
        print(f"{prefix} {name} {tool_input}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ai_debate",
        description="4人のAIペルソナが議論し、結論を成果物として `./output/` に書き出す。",
    )
    parser.add_argument(
        "topic",
        nargs="?",
        help="お題。省略時は標準入力から1行読む。",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=2,
        help="応酬ラウンド数（既定: 2）。0なら初動のみ。",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Implementerフェーズをスキップし、議論だけで止める。",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="ユーザー介入を無効化（自動進行）。",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"使用するClaudeモデル（既定: {DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="成果物の出力先ルート（既定: ./output）",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="ANSIカラー出力を無効化。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    topic = args.topic
    if topic is None:
        print("お題を入力してください: ", end="", flush=True)
        try:
            topic = input().strip()
        except EOFError:
            topic = ""
    if not topic:
        print("エラー: お題が空です。", file=sys.stderr)
        return 1

    try:
        client = Client(model=args.model)
    except RuntimeError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2

    no_color = args.no_color or not sys.stdout.isatty()

    print(f"\n{C.BOLD}お題: {topic}{C.RESET}" if not no_color else f"\nお題: {topic}")
    print(f"{C.DIM}モデル: {args.model} / 応酬ラウンド: {args.rounds}{C.RESET}\n" if not no_color else "")

    on_turn = lambda t: _print_turn(t, no_color=no_color)
    ask_user = None if args.no_interactive else _make_ask_user(no_color)

    try:
        debate_result = run_debate(
            topic,
            rounds=args.rounds,
            client=client,
            on_turn=on_turn,
            ask_user=ask_user,
        )
    except KeyboardInterrupt:
        print("\n中断しました。", file=sys.stderr)
        return 130

    if args.no_build:
        print(f"\n{C.DIM}--no-build 指定のため、Implementerをスキップします。{C.RESET}" if not no_color else "")
        return 0

    # Implementerフェーズ
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    session_dir = make_session_dir(output_root, topic)

    # 議論ログを保存
    _save_debate_log(session_dir, debate_result)

    print(f"\n{C.GREEN}{C.BOLD}=== 🛠 Implementer ==={C.RESET}" if not no_color else "\n=== 🛠 Implementer ===")
    print(f"{C.DIM}出力先: {session_dir}{C.RESET}\n" if not no_color else f"出力先: {session_dir}\n")

    on_tool = lambda name, inp, res: _print_tool_call(name, inp, res, no_color=no_color)
    on_text = lambda t: print(t)

    try:
        impl_result = run_implementer(
            debate_result,
            session_dir,
            client=client,
            on_tool_call=on_tool,
            on_text=on_text,
        )
    except KeyboardInterrupt:
        print("\nImplementerを中断しました。", file=sys.stderr)
        return 130

    print(f"\n{C.BOLD}成果物:{C.RESET} {session_dir}" if not no_color else f"\n成果物: {session_dir}")
    if impl_result.files_written:
        for f in impl_result.files_written:
            print(f"  - {f}")
    print(f"  - _debate_log/debate.md")
    print(f"  - _debate_log/debate.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
