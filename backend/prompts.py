"""提示词：常驻的「规则与身份」+ 每轮的「本轮新增」。

中间栏展示的就是这两块：system 段是常驻（可命中 prompt cache），
user 段是本轮新增的可见信息 + 当前指令。
"""
from __future__ import annotations

from typing import Any

from .model import Player, Role

RULES = """你正在参加一场 6 人狼人杀游戏，你要像真人玩家一样推理、伪装和说服别人。

【配置】6 名玩家：2 狼人、2 平民、1 预言家、1 守卫。
【流程】每晚：守卫守护一人 → 狼人商议并刀人 → 预言家查验一人；白天：公布死讯 → 依次发言 → 全体投票放逐得票最高者。
【规则要点】
- 守卫不能连续两晚守护同一个人，可以守自己；守护成功则该玩家当晚不死。
- 预言家每晚查验一人，只知道「好人 / 狼人」。
- 平票则本轮无人出局。出局者不留遗言。
- 你只能看到自己视角内的信息：狼人夜里的商议只有狼看得到，预言家的查验结果只有自己看得到。
【胜负】杀光所有好人，狼人胜；放逐所有狼人，好人胜。
【发言纪律】不要复述规则，不要说“作为一个 AI”，就用狼人杀玩家的口吻，简短、有信息量、带明确站边或倾向。"""


def build_system(player: Player, teammates: list[int]) -> str:
    lines = [
        RULES,
        "",
        f"【你的身份】你是 {player.name}，你的身份是【{player.role.value}】，属于{player.role.camp}。",
    ]
    if player.role is Role.WOLF:
        mate = "、".join(f"{s}号" for s in teammates) or "无"
        lines.append(f"【狼队友】{mate}。你们夜里的商议只有狼队看得到，白天要伪装成好人。")
    elif player.role is Role.SEER:
        lines.append("【你的能力】每晚查验一名玩家的身份。你是好人阵营的核心信息来源，注意跳身份的时机。")
    elif player.role is Role.GUARD:
        lines.append("【你的能力】每晚守护一名玩家（不能连续两晚守同一人，可以守自己）。")
    else:
        lines.append("【你的能力】没有技能，靠发言和逻辑找出狼人。")
    return "\n".join(lines)


# ---------- 各类决策的指令 + JSON schema ----------

def _schema(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


THINKING = {"type": "string", "description": "你的推理（不会被别人看到）"}


def guard_instruction(rnd: int, alive: list[int], last: int | None) -> tuple[str, dict]:
    ban = f"\n上一晚你守护了 {last} 号，今晚不能再守 {last} 号。" if last else ""
    text = (
        f"现在是第 {rnd} 夜，请选择今晚要守护的玩家。存活玩家：{alive}。{ban}\n"
        "你需要确认你的判断和发言策略，帮助你的阵营获取更多的信息以走向胜利。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "target": 要守护的座位号（整数）\n}'
    )
    return text, _schema({"thinking": THINKING, "target": {"type": "integer", "enum": alive}})


def wolf_instruction(rnd: int, alive: list[int], mates: list[int]) -> tuple[str, dict]:
    text = (
        f"现在是第 {rnd} 夜，狼队商议今晚刀谁。存活玩家：{alive}，你的狼队友：{mates}。\n"
        "说出你想刀的人和理由，队友能看到你的发言。优先考虑：预言家在谁身上、谁的发言最有威胁、白天的局势怎么带。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "target": 你提议今晚刀的座位号（整数）,\n'
        '  "reason": 给队友看的一句话理由\n}'
    )
    return text, _schema(
        {"thinking": THINKING, "target": {"type": "integer", "enum": alive}, "reason": {"type": "string"}}
    )


def seer_instruction(rnd: int, alive: list[int], checked: list[int]) -> tuple[str, dict]:
    done = f"\n你已经查验过：{checked}，不要重复查验。" if checked else ""
    pool = [s for s in alive if s not in checked] or alive
    text = (
        f"现在是第 {rnd} 夜，请选择今晚要查验的玩家。存活玩家：{alive}。{done}\n"
        "选择信息量最大的人查验。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "target": 要查验的座位号（整数）\n}'
    )
    return text, _schema({"thinking": THINKING, "target": {"type": "integer", "enum": pool}})


def speech_instruction(rnd: int, alive: list[int], order: list[int]) -> tuple[str, dict]:
    text = (
        f"轮到你发言。说出你的判断：你觉得谁是狼、为什么，或者你要不要跳身份。\n"
        f"当前是第 {rnd} 天，存活玩家：{alive}，发言顺序：{order}。\n"
        "发言控制在 120 字以内，要有明确的倾向和票型指引。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "speech": 你公开说出来的话（所有人都能看到）\n}'
    )
    return text, _schema({"thinking": THINKING, "speech": {"type": "string"}})


def vote_instruction(rnd: int, alive: list[int], me: int) -> tuple[str, dict]:
    pool = [s for s in alive if s != me]
    text = (
        f"发言结束，现在投票放逐一名玩家。可投对象：{pool}（不能投自己）。\n"
        "结合今天所有发言给出你的票。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "target": 你要投的座位号（整数）,\n'
        '  "reason": 一句话投票理由（公开）\n}'
    )
    return text, _schema(
        {"thinking": THINKING, "target": {"type": "integer", "enum": pool}, "reason": {"type": "string"}}
    )
