"""議論オーケストレーター。

4人のペルソナ（戦略家・行動経済学者・現場・悪魔の代弁者）に発言させ、
ラウンド間でユーザーの介入を受け付け、最後にモデレーターがまとめる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ai_debate.agents import DEBATERS, SYNTHESIZER, Agent, label_for
from ai_debate.client import Client


@dataclass
class Turn:
    role: str  # "Strategist" / "Behavioral" / "Field" / "DevilsAdvocate" / "Synthesizer" / "User"
    content: str


@dataclass
class DebateResult:
    topic: str
    history: list[Turn]
    synthesis: str

    @property
    def final_text(self) -> str:
        return self.synthesis


OnTurn = Callable[[Turn], None]
AskUser = Callable[[list[Turn]], Optional[str]]


def _format_history_for_prompt(topic: str, history: list[Turn]) -> str:
    """これまでの議論履歴を user メッセージとして渡すための文字列化。"""
    lines = [f"# お題\n{topic}\n"]
    if history:
        lines.append("# これまでの議論\n")
        for turn in history:
            lines.append(f"## [{label_for(turn.role)}]")
            lines.append(turn.content)
            lines.append("")
    return "\n".join(lines)


def _ask_persona(
    client: Client,
    agent: Agent,
    topic: str,
    history: list[Turn],
    instruction: str,
) -> str:
    """1人のペルソナに発言させる。"""
    user_content = _format_history_for_prompt(topic, history) + "\n\n# 指示\n" + instruction
    result = client.call(
        system=agent.system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return result.text.strip()


def _maybe_intervene(
    history: list[Turn],
    ask_user: AskUser | None,
    on_turn: OnTurn | None,
) -> None:
    """ユーザー介入ポイント。ask_userコールバックを呼び、入力があれば履歴に追加。"""
    if ask_user is None:
        return
    user_input = ask_user(history)
    if user_input and user_input.strip():
        turn = Turn(role="User", content=user_input.strip())
        history.append(turn)
        if on_turn is not None:
            on_turn(turn)


def run_debate(
    topic: str,
    *,
    rounds: int = 2,
    client: Client | None = None,
    on_turn: OnTurn | None = None,
    ask_user: AskUser | None = None,
) -> DebateResult:
    """議論を回す。

    Args:
        topic: お題。
        rounds: 応酬ラウンド数（Round 0以降の追加ラウンド数）。0なら初動のみ。
        client: Anthropicクライアント。Noneなら新規作成。
        on_turn: 各発言が出たときに呼ばれるコールバック。
        ask_user: ラウンド間で呼ばれる介入コールバック。Noneなら介入なし（自動進行）。

    Returns:
        DebateResult: 議論履歴 + モデレーターによる統合結果。
    """
    if client is None:
        client = Client()

    history: list[Turn] = []

    # Round 0: 初動 - 4人が順に初期見解を出す
    for agent in DEBATERS:
        instruction = (
            "上記のお題に対して、あなたのペルソナとして初期見解を述べてください。"
            "他の3人もこの後同じお題について発言します。"
        )
        text = _ask_persona(client, agent, topic, history, instruction)
        turn = Turn(role=agent.role, content=text)
        history.append(turn)
        if on_turn is not None:
            on_turn(turn)

    # 介入ポイント
    _maybe_intervene(history, ask_user, on_turn)

    # Round 1+: 応酬
    for round_idx in range(rounds):
        for agent in DEBATERS:
            instruction = (
                f"上記の議論（Round {round_idx + 1}）を踏まえ、他のペルソナの発言に"
                "反応・反論・補強しながら、あなたの見解を更新・深化させてください。"
                "単なる繰り返しは避け、議論を前に進めること。"
                "ユーザー（人間モデレーター）の発言があれば、それを最優先で考慮すること。"
            )
            text = _ask_persona(client, agent, topic, history, instruction)
            turn = Turn(role=agent.role, content=text)
            history.append(turn)
            if on_turn is not None:
                on_turn(turn)

        # 各ラウンド後にも介入ポイント
        _maybe_intervene(history, ask_user, on_turn)

    # Final: モデレーターが統合
    synth_instruction = (
        "上記の議論全体を踏まえ、あなたの定型フォーマット（合意点 / 対立点 / "
        "最終推奨 / 成果物仕様）でまとめてください。"
    )
    user_content = _format_history_for_prompt(topic, history) + "\n\n# 指示\n" + synth_instruction
    result = client.call(
        system=SYNTHESIZER.system_prompt,
        messages=[{"role": "user", "content": user_content}],
        max_tokens=4000,
    )
    synthesis = result.text.strip()
    synth_turn = Turn(role="Synthesizer", content=synthesis)
    history.append(synth_turn)
    if on_turn is not None:
        on_turn(synth_turn)

    return DebateResult(topic=topic, history=history, synthesis=synthesis)
