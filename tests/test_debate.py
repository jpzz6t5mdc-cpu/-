"""モック使用のスモークテスト。AnthropicクライアントをMagicMockで差し替えて検証する。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ai_debate.agents import DEBATERS, label_for
from ai_debate.client import CallResult, Client
from ai_debate.debate import Turn, run_debate
from ai_debate.executor import (
    _safe_resolve,
    _tool_python_exec,
    _tool_read_file,
    _tool_write_file,
    make_session_dir,
)


def _fake_message(text: str, *, stop_reason: str = "end_turn", tool_uses: list | None = None):
    """Anthropic Message レスポンスのモック。"""
    blocks = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    if tool_uses:
        blocks.extend(tool_uses)
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def _make_mock_client(canned_responses: list[str]) -> Client:
    """カンペ通りに順番にCallResultを返すモッククライアント。"""
    client = Client.__new__(Client)
    client.model = "mock-model"
    client.max_tokens = 8000
    client._client = MagicMock()

    responses = list(canned_responses)

    def call(*, system, messages, max_tokens=None):
        if not responses:
            text = "デフォルト応答"
        else:
            text = responses.pop(0)
        return CallResult(text=text, raw=_fake_message(text))

    client.call = call  # type: ignore[method-assign]
    return client


def test_run_debate_basic_flow():
    """4人のペルソナ + Synthesizerが順に呼ばれることを確認。"""
    # Round 0で4発言 + Round 1で4発言 + Synthesizer = 9回
    canned = [
        "戦略家の初期見解",
        "行動経済学者の初期見解",
        "現場の初期見解",
        "悪魔の代弁者の初期見解",
        "戦略家の応酬",
        "行動経済学者の応酬",
        "現場の応酬",
        "悪魔の代弁者の応酬",
        "### 合意点\n- ok\n### 対立点\n- ok\n### 最終推奨\nok\n### 成果物仕様\nok",
    ]
    client = _make_mock_client(canned)

    result = run_debate("テストお題", rounds=1, client=client, ask_user=None)

    # 履歴: 4 (Round0) + 4 (Round1) + 1 (Synthesizer) = 9
    assert len(result.history) == 9
    roles = [t.role for t in result.history]
    expected = [
        "Strategist", "Behavioral", "Field", "DevilsAdvocate",  # Round 0
        "Strategist", "Behavioral", "Field", "DevilsAdvocate",  # Round 1
        "Synthesizer",
    ]
    assert roles == expected
    assert "最終推奨" in result.synthesis


def test_run_debate_with_user_intervention():
    """ask_userで入力されたコメントが履歴に含まれること。"""
    canned = ["s0", "b0", "f0", "d0", "synth"]
    client = _make_mock_client(canned)

    inputs = iter(["ターゲットを中小企業に絞ってほしい"])

    def ask_user(history):
        return next(inputs, None)

    result = run_debate("お題", rounds=0, client=client, ask_user=ask_user)

    user_turns = [t for t in result.history if t.role == "User"]
    assert len(user_turns) == 1
    assert "中小企業" in user_turns[0].content


def test_run_debate_no_intervention_when_callback_none():
    """ask_user=Noneのとき、Userロールは登場しない。"""
    canned = ["s0", "b0", "f0", "d0", "synth"]
    client = _make_mock_client(canned)
    result = run_debate("お題", rounds=0, client=client, ask_user=None)
    assert all(t.role != "User" for t in result.history)


def test_run_debate_zero_rounds():
    """rounds=0なら初動の4発言 + Synthesizerのみ。"""
    canned = ["s0", "b0", "f0", "d0", "synth"]
    client = _make_mock_client(canned)
    result = run_debate("お題", rounds=0, client=client, ask_user=None)
    assert len(result.history) == 5  # 4 + 1


def test_label_for_known_roles():
    assert "戦略家" in label_for("Strategist")
    assert "行動経済学者" in label_for("Behavioral")
    assert "現場" in label_for("Field")
    assert "悪魔" in label_for("DevilsAdvocate")
    assert "あなた" in label_for("User")


def test_safe_resolve_blocks_traversal(tmp_path: Path):
    """`..` は拒否されること。"""
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    with pytest.raises(ValueError):
        _safe_resolve(session_dir, "../etc/passwd")
    with pytest.raises(ValueError):
        _safe_resolve(session_dir, "../../foo")


def test_safe_resolve_blocks_absolute(tmp_path: Path):
    """絶対パスは拒否されること。"""
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    with pytest.raises(ValueError):
        _safe_resolve(session_dir, "/etc/passwd")


def test_safe_resolve_allows_subdir(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    resolved = _safe_resolve(session_dir, "src/main.py")
    assert resolved.is_relative_to(session_dir.resolve())


def test_tool_write_and_read_file(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    write_result = _tool_write_file(session_dir, "hello.txt", "hello, world")
    assert write_result["ok"] is True
    assert (session_dir / "hello.txt").read_text() == "hello, world"

    read_result = _tool_read_file(session_dir, "hello.txt")
    assert read_result["ok"] is True
    assert read_result["content"] == "hello, world"


def test_tool_write_creates_subdirs(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    result = _tool_write_file(session_dir, "src/lib/main.py", "print('hi')")
    assert result["ok"] is True
    assert (session_dir / "src" / "lib" / "main.py").exists()


def test_tool_write_rejects_traversal(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    result = _tool_write_file(session_dir, "../leak.txt", "x") if False else None
    # _tool_write_fileはValueErrorを投げる
    import pytest
    with pytest.raises(ValueError):
        _tool_write_file(session_dir, "../leak.txt", "x")


def test_tool_python_exec_runs_code(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    result = _tool_python_exec(session_dir, "print(2 + 3)")
    assert result["ok"] is True
    assert "5" in result["stdout"]


def test_tool_python_exec_reports_failure(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    result = _tool_python_exec(session_dir, "import sys; sys.exit(1)")
    assert result["ok"] is False
    assert result["returncode"] == 1


def test_make_session_dir_creates_directory(tmp_path: Path):
    output_root = tmp_path / "out"
    output_root.mkdir()
    session_dir = make_session_dir(output_root, "新規SaaSの差別化アイデア")
    assert session_dir.exists()
    assert session_dir.is_dir()
    assert session_dir.parent == output_root


def test_on_turn_callback_called():
    """on_turnが各発言で呼ばれること。"""
    canned = ["s0", "b0", "f0", "d0", "synth"]
    client = _make_mock_client(canned)
    captured: list[Turn] = []
    run_debate("お題", rounds=0, client=client, on_turn=captured.append, ask_user=None)
    assert len(captured) == 5
    assert captured[0].role == "Strategist"
    assert captured[-1].role == "Synthesizer"
