"""房间：一个 5 位数字码对应一局。单人/模拟也是房间，只是没人用码进来。

- 房主建房拿到码，其他人输码加入，房主点开始
- 每个人有自己的 token，token → 座位，SSE 和作答都按这个座位裁视野
- 空座位交给模型；开局后不再接受加入
"""
from __future__ import annotations

import asyncio
import random
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .game import Game

MAX_HUMANS = 6
ROOM_TTL = 6 * 3600          # 空置 6 小时就回收


@dataclass
class Member:
    token: str
    name: str
    mid: str = field(default_factory=lambda: secrets.token_hex(3))
    host: bool = False
    seat: int | None = None
    joined_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        return {"id": self.mid, "name": self.name, "host": self.host, "seat": self.seat}


@dataclass
class Room:
    code: str
    mode: str = "multi"
    members: dict[str, Member] = field(default_factory=dict)
    game: Game | None = None
    task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)

    # ---------- 成员 ----------
    def join(self, name: str, host: bool = False, token: str = "") -> Member:
        if token and token in self.members:      # 重复点/刷新回来的，直接返回原成员
            return self.members[token]
        if self.game and self.game.status == "running":
            raise ValueError("这局已经开始了，等下一局吧")
        if len(self.members) >= MAX_HUMANS:
            raise ValueError(f"房间满了（最多 {MAX_HUMANS} 人）")
        m = Member(token=secrets.token_urlsafe(12), name=name.strip()[:12] or "玩家", host=host)
        self.members[m.token] = m
        self.touch()
        return m

    def leave(self, token: str) -> None:
        self.members.pop(token, None)
        self.touch()

    def kick(self, mid: str) -> bool:
        """房主踢人。只在开局前有效，开局后座位已经定了。"""
        if self.game and self.game.status == "running":
            raise ValueError("对局已经开始，踢不了了")
        for tok, m in list(self.members.items()):
            if m.mid == mid and not m.host:
                self.members.pop(tok)
                self.touch()
                return True
        return False

    def member(self, token: str) -> Member | None:
        return self.members.get(token)

    def seat_of(self, token: str) -> int | None:
        m = self.members.get(token)
        return m.seat if m else None

    def touch(self) -> None:
        self.touched_at = time.time()

    # ---------- 开局 ----------
    def start(self, settings: dict[str, Any]) -> Game:
        if self.game and self.game.status == "running":
            raise ValueError("这局还在进行中")
        people = list(self.members.values())
        gid = time.strftime("%Y%m%d%H%M%S")
        rng = random.Random()
        seats = list(range(1, 7))
        rng.shuffle(seats)
        for m, seat in zip(people, seats):
            m.seat = seat
        names = {m.seat: m.name for m in people if m.seat}
        self.game = Game(
            gid,
            model=settings.get("model"),
            tts=bool(settings.get("tts")),
            mode="multi",
            clone=settings.get("clone"),
            humans=len(people),
            names=names,
        )
        # Game 自己会随机分座位，这里覆盖成房间里已经分好的，保证 token→座位对得上
        self.game.humans = {m.seat for m in people if m.seat}
        self.touch()
        return self.game

    def public(self, token: str | None = None) -> dict[str, Any]:
        me = self.members.get(token or "")
        return {
            "code": self.code,
            "members": [m.public() for m in self.members.values()],
            "count": len(self.members),
            "max": MAX_HUMANS,
            "status": self.game.status if self.game else "lobby",
            "you": me.public() if me else None,
            "is_host": bool(me and me.host),
        }


_rooms: dict[str, Room] = {}


def _prune() -> None:
    now = time.time()
    for code in [c for c, r in _rooms.items()
                 if now - r.touched_at > ROOM_TTL
                 and not (r.game and r.game.status == "running")]:
        _rooms.pop(code, None)


def create(name: str) -> tuple[Room, Member]:
    _prune()
    for _ in range(50):
        code = f"{random.randint(0, 99999):05d}"
        if code not in _rooms:
            break
    else:
        raise ValueError("房间号分配不出来了，稍后再试")
    room = Room(code=code)
    _rooms[code] = room
    return room, room.join(name, host=True)


def get(code: str) -> Room | None:
    r = _rooms.get((code or "").strip())
    if r:
        r.touch()
    return r


def all_rooms() -> dict[str, Room]:
    return _rooms
