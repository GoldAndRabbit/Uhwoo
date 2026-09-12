"""提示词：常驻的「规则与身份」+ 每轮的「本轮新增」。

中间栏展示的就是这两块：system 段是常驻（可命中 prompt cache），
user 段是本轮新增的可见信息 + 当前指令。
"""
from __future__ import annotations

from typing import Any

from .model import DEFAULT_SETUP, SETUPS, Player, Role

def rules_text(setup: dict[str, Any]) -> str:
    """规则正文。人数、配置、夜里有哪些神职，都跟着局面走。"""
    roles = setup["roles"]
    n = len(roles)
    has = {r.value for r in roles}
    night = ["狼人商议并刀人"]
    if "守卫" in has:
        night.insert(0, "守卫守护一人")
    if "女巫" in has:
        night.append("女巫决定救人或用毒")
    if "预言家" in has:
        night.append("预言家查验一人")
    skills = []
    if "预言家" in has:
        skills.append("- 预言家每晚查验一人，只知道「好人 / 狼人」。")
    if "守卫" in has:
        skills.append("- 守卫不能连续两晚守护同一个人，可以守自己；守护成功则该玩家当晚不死。")
    if "女巫" in has:
        skills.append("- 女巫有一瓶解药一瓶毒药，全局各用一次，同一晚不能既救又毒；"
                      "她知道今晚谁被刀，但不能自救。")
    if "猎人" in has:
        skills.append("- 猎人出局时可以开枪带走一名存活玩家；被女巫毒死时开不了枪。")
    return f"""你正在参加一场 {n} 人狼人杀游戏，你要像真人玩家一样推理、伪装和说服别人。

【配置】{n} 名玩家：{setup["desc"]}。
【流程】每晚：{" → ".join(night)}；白天：公布死讯 → 依次发言 → 全体投票放逐得票最高者。
【规则要点】
{chr(10).join(skills)}
- 平票时，平票的人各做一次补充发言，其余人在他们之间重投一轮；还平票就直接天黑。
- 出局者不留遗言。
- 你只能看到自己视角内的信息：狼人夜里的商议只有狼看得到，预言家的查验结果只有自己看得到。
【胜负 · 屠边】狼人**不需要杀光所有人**：只要所有平民出局（屠民），或者所有神职出局（屠神），
狼人阵营就赢；放逐所有狼人，好人阵营赢。所以好人要护住弱的那一边，狼人要盯着人少的那一边打。
【发言纪律】不要复述规则，不要说“作为一个 AI”，就用狼人杀玩家的口吻，简短、有信息量、带明确站边或倾向。
局势再差也要争取到最后一票：不许弃疗、不许劝别人投自己、不许说“你们赢了”这种放弃的话。"""


def build_system(player: Player, teammates: list[int], persona: str = "",
                 setup: dict[str, Any] | None = None) -> str:
    lines = [
        rules_text(setup or SETUPS[DEFAULT_SETUP]),
        "",
        f"【你的身份】你是 {player.name}，你的身份是【{player.role.value}】，属于{player.role.camp}。",
    ]
    if player.role is Role.WOLF:
        mate = "、".join(f"{s}号" for s in teammates) or "无"
        lines.append(f"【狼队友】{mate}。你们夜里的商议只有狼队看得到，白天要伪装成好人。")
        lines.append(
            "【铁律】任何情况下都不能承认自己是狼人，也不能说出谁是你的队友、队友死没死。"
            "被预言家查杀了就反咬他是悍跳狼、挑他验人逻辑的毛病，或者退一步说自己是被冤枉的好人；"
            "哪怕只剩你一只狼、局势已经必输，也要装到最后一票 —— "
            "好人里总有人会怀疑预言家，你的机会就在那里。")
    elif player.role is Role.SEER:
        lines.append("【你的能力】每晚查验一名玩家的身份。你是好人阵营的核心信息来源，注意跳身份的时机。")
    elif player.role is Role.GUARD:
        lines.append("【你的能力】每晚守护一名玩家（不能连续两晚守同一人，可以守自己）。")
    elif player.role is Role.HUNTER:
        lines.append("【你的能力】你出局时可以开枪带走一名存活玩家（被女巫毒死则开不了枪）。"
                     "活着的时候你就是把枪，狼人不敢轻易刀你 —— 但也别太早暴露。")
    elif player.role is Role.WITCH:
        lines.append("【你的能力】一瓶解药一瓶毒药，全局各用一次，同一晚只能用一瓶，而且不能自救。"
                     "每晚你会知道今晚谁被刀。药是好人阵营最硬的底牌，用早了浪费，用晚了来不及。")
    else:
        lines.append("【你的能力】没有技能，靠发言和逻辑找出狼人。")
    if persona:
        lines += ["", persona]
    return "\n".join(lines)


# ---------- 人设：让 6 个人听起来不像同一个模型 ----------
# 每局开场随机给每个座位摇一套（见 Game.personas）。分开几个维度是故意的：
# 只给「性格」两个字，模型会齐刷刷地变成同一种「活泼」；拆成说话习惯、推理强度、
# 情绪、策略偏好之后，组合出来的人才有区别。
SPEECH_TICS = [
    "说话前爱先「嗯……」一下，想到哪说到哪，句子偏短",
    "爱用「我直说了」「听我说完」开头，语气冲，喜欢下结论",
    "爱把话说一半又绕回来，常用「不过」「话说回来」自我修正",
    "喜欢反问：「你为什么不敢跳？」「那你倒是解释一下？」",
    "爱打比方，把局势比成打牌、打球、上班开会",
    "口头禅是「我跟你讲」「说白了」，喜欢总结成一两句",
    "说话客气但绵里藏针，常用「我尊重你」「可能是我想多了」",
    "爱自嘲：「我这脑子」「我可能又想歪了」，但结论其实很硬",
    "喜欢点名喊人：「三号，我问你」，一句话里反复提座位号",
    "语速快，爱连着说两三件事，中间用「再一个」「还有」串起来",
]
REASONING = [
    "直觉派：不摆完整推理链，一上来就报感觉，但敢押",
    "逻辑派：一条一条对时间线和票型，说话像在念账本",
    "半吊子逻辑派：喜欢讲推理但经常绕晕自己，最后还是凭印象",
    "复读机式：抓住某一个细节反复咀嚼，不肯换话题",
    "全局派：喜欢算人数、算刀口、算平安夜说明什么",
]
MOODS = [
    "急躁，容易被顶两句就上头",
    "稳，谁都不得罪，但关键时刻一票定生死",
    "多疑，谁跳身份都先怀疑三分",
    "戏精，喜欢拱火看别人吵",
    "谨慎，话留半句，先看别人怎么说",
    "护短，认定谁是好人就一路帮他说话",
]
TACTICS = [
    "偏好冲锋：第一个站边，带头指认",
    "偏好保守：前期只报感觉，中后期才押",
    "爱跟票：更相信人多的一边",
    "爱唱反调：人多的一边先怀疑一下",
    "喜欢留后手：先不表态，等预言家出来再站队",
    "喜欢逼身份：不停要求别人跳，用别人的反应找狼",
]


def persona_text(tic: str, reasoning: str, mood: str, tactic: str) -> str:
    return (
        "【你这个人】下面是你的说话习惯和脾气，发言时自然带出来就行，"
        "**不要**直接描述自己的性格，也不要每句话都硬套口头禅：\n"
        f"- 说话习惯：{tic}\n"
        f"- 推理方式：{reasoning}\n"
        f"- 脾气：{mood}\n"
        f"- 打法：{tactic}\n"
        "说人话：允许有语气词、口误、半截话，允许不那么严谨 —— "
        "宁可像个真人在牌桌上随口说的，也别像一段结构工整的分析报告。"
    )

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
    pool = [s for s in alive if s != last] or alive
    text = (
        f"现在是第 {rnd} 夜，请选择今晚要守护的玩家。存活玩家：{alive}。{ban}\n"
        "你需要确认你的判断和发言策略，帮助你的阵营获取更多的信息以走向胜利。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "target": 要守护的座位号（整数）\n}'
    )
    return text, _schema({"thinking": THINKING, "target": {"type": "integer", "enum": pool}})


def wolf_instruction(rnd: int, alive: list[int], mates: list[int],
                     wolves: list[int] | None = None) -> tuple[str, dict]:
    pool = [s for s in alive if s not in (wolves or [])] or alive
    text = (
        f"现在是第 {rnd} 夜，狼队商议今晚刀谁。存活玩家：{alive}，你的狼队友：{mates}。\n"
        "说出你想刀的人和理由，队友能看到你的发言。优先考虑：预言家在谁身上、谁的发言最有威胁、白天的局势怎么带。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "target": 你提议今晚刀的座位号（整数）,\n'
        '  "reason": 给队友看的一句话理由\n}'
    )
    return text, _schema(
        {"thinking": THINKING, "target": {"type": "integer", "enum": pool}, "reason": {"type": "string"}}
    )


def seer_instruction(rnd: int, alive: list[int], checked: list[int],
                     me: int | None = None) -> tuple[str, dict]:
    done = f"\n你已经查验过：{checked}，不要重复查验。" if checked else ""
    pool = [s for s in alive if s not in checked and s != me] \
        or [s for s in alive if s != me] or alive
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


def vote_instruction(rnd: int, alive: list[int], me: int,
                     pool: list[int] | None = None) -> tuple[str, dict]:
    pool = pool if pool is not None else [s for s in alive if s != me]
    text = (
        f"发言结束，现在投票放逐一名玩家。可投对象：{pool}（不能投自己）。\n"
        "结合今天所有发言给出你的票。投票是无声的，不用给理由。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "target": 你要投的座位号（整数）\n}'
    )
    return text, _schema({"thinking": THINKING, "target": {"type": "integer", "enum": pool}})


MVP_SYSTEM = (
    "你是这局狼人杀的法官（上帝视角）。一局刚打完，你要评出本局 MVP。\n"
    "评判只看**这一局里的决策质量**，不看阵营、不看输赢：\n"
    "  - 神职：查验/守护是否踩在关键位置，信息有没有被用出来\n"
    "  - 好人：站边和票型对不对，有没有带对节奏\n"
    "  - 狼人：刀口选得准不准，悍跳/倒钩/带队有没有骗到人\n"
    "输的一方也可以拿 MVP —— 打得好但输了是常有的事。"
)


def mvp_instruction(winner: str, roles: dict[int, str], track: str) -> tuple[str, dict]:
    seats = sorted(roles)
    text = (
        f"本局结束，{winner}胜利。每个人的身份："
        + "、".join(f"{s}号{roles[s]}" for s in seats) + "。\n\n"
        "完整轨迹如下（上帝视角，含夜里的私密行动和票型）：\n"
        f"{track}\n\n"
        "从 1–6 号里选一个本局 MVP，理由控制在 40 字以内，要具体到他做过的某个决策，"
        "不要写「发挥稳定」这种空话。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "seat": MVP 的座位号（整数）,\n'
        '  "reason": 40 字以内的理由\n}'
    )
    return text, _schema({"seat": {"type": "integer", "enum": seats},
                          "reason": {"type": "string"}})


def pk_speech_instruction(rnd: int, tied: list[int], me: int) -> tuple[str, dict]:
    others = "、".join(f"{s} 号" for s in tied if s != me)
    text = (
        f"第一轮投票你和 {others} 平票，你们要各做一次补充发言，"
        "之后由**其余玩家**在你们之间重新投一次 —— 被投中的人直接出局。\n"
        f"这是你最后的机会：正面回应别人对你的怀疑，给出能把票拉回来的理由，"
        f"或者把矛头指回 {others}。控制在 80 字以内，别复述前面说过的话。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "speech": 你公开说出来的话（所有人都能看到）\n}'
    )
    return text, _schema({"thinking": THINKING, "speech": {"type": "string"}})


def pk_vote_instruction(rnd: int, tied: list[int], me: int) -> tuple[str, dict]:
    text = (
        f"{'、'.join(f'{s} 号' for s in tied)} 平票，两边都做过补充发言了。"
        f"现在只在他们之间重新投一票，可投对象：{tied}。\n"
        "这一票定生死：再平票就没人出局，直接天黑。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "target": 你要投的座位号（整数）\n}'
    )
    return text, _schema({"thinking": THINKING, "target": {"type": "integer", "enum": tied}})


def witch_instruction(rnd: int, alive: list[int], killed: int | None, me: int,
                      has_cure: bool, has_poison: bool) -> tuple[str, dict]:
    who = f"{killed} 号" if killed else "没有人"
    acts = []
    if has_cure and killed and killed != me:
        acts.append('"save"（用解药救他）')
    if has_poison:
        acts.append('"poison"（用毒药毒死一人，同时填 target）')
    acts.append('"pass"（今晚不用药）')
    text = (
        f"第 {rnd} 夜。今晚被狼人刀的是：{who}。\n"
        f"你手上：解药{'还在' if has_cure else '已用完'}，毒药{'还在' if has_poison else '已用完'}。\n"
        "同一晚只能用一瓶药，而且不能用解药救自己。存活玩家：" + f"{alive}。\n"
        "药是好人阵营最硬的底牌：用早了浪费，用晚了来不及。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        f'  "action": {" / ".join(acts)},\n'
        '  "target": 用毒时填座位号，其它情况填 0\n}'
    )
    return text, _schema({
        "thinking": THINKING,
        "action": {"type": "string", "enum": ["save", "poison", "pass"]},
        "target": {"type": "integer"},
    })


def hunter_instruction(rnd: int, alive: list[int], me: int) -> tuple[str, dict]:
    pool = [s for s in alive if s != me]
    text = (
        f"你出局了。猎人的枪可以带走一名存活玩家，也可以选择不开。\n"
        f"可选目标：{pool}。开枪就把你最确定的那匹狼带走；没把握也可以空枪（target 填 0）。\n\n"
        "输出 JSON，字段如下：\n{\n"
        '  "thinking": 你的推理（不会被别人看到）,\n'
        '  "target": 要带走的座位号，不开枪填 0\n}'
    )
    return text, _schema({"thinking": THINKING, "target": {"type": "integer"}})
