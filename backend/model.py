"""数据模型：角色、玩家、事件。"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class Role(str, Enum):
    WOLF = "狼人"
    VILLAGER = "平民"
    SEER = "预言家"
    GUARD = "守卫"

    @property
    def camp(self) -> str:
        return "狼人阵营" if self is Role.WOLF else "好人阵营"


# 座位头像：frontend/img/avatars/<key>.png，6 女 6 男，由 scripts/gen_avatars.py 生成。
# 一局只用得上 6 张，开局时随机抽（见 Game.avatars）。
AVATARS: list[str] = [f"f{i}" for i in range(1, 7)] + [f"m{i}" for i in range(1, 7)]

# 6 人局：2 狼 2 民 1 预言家 1 守卫
SETUP: list[Role] = [Role.WOLF, Role.WOLF, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.GUARD]


@dataclass
class Player:
    seat: int
    role: Role
    alive: bool = True
    death_round: int | None = None
    death_cause: str | None = None
    # agent 私有上下文
    system_prompt: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    decisions: int = 0
    llm_calls: int = 0
    cursor: int = 0          # 已经进过自己上下文的事件游标

    @property
    def name(self) -> str:
        return f"{self.seat}号"

    @property
    def ctx_chars(self) -> int:
        n = len(self.system_prompt)
        for m in self.messages:
            n += len(m["content"]) if isinstance(m["content"], str) else 0
        return n

    def public(self, reveal: bool) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "name": self.name,
            "role": self.role.value if reveal else None,
            "camp": self.role.camp if reveal else None,
            "alive": self.alive,
            "decisions": self.decisions,
            "ctx_chars": self.ctx_chars,
            "death_round": self.death_round,
            "death_cause": self.death_cause,
        }


Audience = Literal["all"] | list[int]

_eid = itertools.count(1)


@dataclass
class Event:
    """一条游戏事件。audience 决定哪些 agent 能在自己的上下文里看到它。

    右侧“上帝视角”总是全部展示，受限事件会打上「仅 X 号」的标签。
    """

    kind: str            # phase / night_action / speech / vote / death / result / system
    text: str            # 进入 agent 上下文的文本
    audience: Any = "all"
    seat: int | None = None       # 事件的发起者座位
    round: int = 0
    phase: str = ""      # night / day
    meta: dict[str, Any] = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_eid))
    ts: float = field(default_factory=time.time)

    def visible_to(self, seat: int) -> bool:
        return self.audience == "all" or seat in self.audience

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "audience": self.audience,
            "seat": self.seat,
            "round": self.round,
            "phase": self.phase,
            "meta": self.meta,
            "ts": self.ts,
        }


@dataclass
class LLMCall:
    """一次模型调用的完整记录，用于中间栏「每轮上下文与输出」。"""

    seat: int | None          # None = 系统全局状态
    title: str                # 例如 "1号 第1夜"
    system_prompt: str
    delta: str                # 本轮新增
    output: str               # 模型原始输出
    parsed: dict[str, Any]
    ctx_chars: int
    latency_ms: int
    model: str
    round: int
    phase: str
    id: int = field(default_factory=lambda: next(_eid))
    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seat": self.seat,
            "title": self.title,
            "system_prompt": self.system_prompt,
            "delta": self.delta,
            "output": self.output,
            "parsed": self.parsed,
            "ctx_chars": self.ctx_chars,
            "latency_ms": self.latency_ms,
            "model": self.model,
            "round": self.round,
            "phase": self.phase,
            "ts": self.ts,
        }
