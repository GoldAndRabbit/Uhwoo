"""狼人杀对局引擎：夜晚行动 → 白天发言 → 投票，逐条广播事件。"""
from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any, Callable

from . import prompts
from . import tts as tts_mod
from .llm import LLMClient
from .model import SETUP, Event, LLMCall, Player, Role

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class GameStopped(Exception):
    pass


class Game:
    ANSWER_TIMEOUT = 300          # 轮到你却一直不答，超时就交给模型代打，别把整局挂死

    def __init__(self, gid: str, seed: int | None = None, use_api: bool | None = None,
                 model: str | None = None, tts: bool = False, mode: str = "sim",
                 human_seat: int | None = None):
        self.gid = gid
        self.tts = bool(tts) and tts_mod.available()
        self.mode = mode if mode in ("sim", "play") else "sim"
        self.seed = seed if seed is not None else random.randrange(10**6)
        self.rng = random.Random(self.seed)
        self.llm = LLMClient(use_api=use_api, seed=self.seed, model=model)

        roles = SETUP[:]
        self.rng.shuffle(roles)
        self.players = [Player(seat=i + 1, role=roles[i]) for i in range(6)]
        wolves = [p.seat for p in self.players if p.role is Role.WOLF]
        for p in self.players:
            p.system_prompt = prompts.build_system(p, [s for s in wolves if s != p.seat])
            p.cursor = 0

        self.events: list[Event] = []
        self.calls: list[LLMCall] = []
        self.round = 0
        self.phase = "准备中"
        self.status = "idle"          # idle / running / finished / stopped
        self.winner: str | None = None
        self.started_at = time.time()
        self.finished_at: float | None = None

        # 引擎内部状态
        self.last_guard: int | None = None
        self.night_death: int | None = None
        self.seer_checks: dict[int, str] = {}
        self.claimed_seer: int | None = None
        self.suspicion: dict[int, float] = {p.seat: 0.0 for p in self.players}

        # 游玩模式：随机坐一个座位，其余 5 个交给模型。视野严格按这个座位裁。
        self.human: int | None = None
        if self.mode == "play":
            self.human = human_seat if human_seat in range(1, 7) else self.rng.randint(1, 6)
        self.pending: dict[str, Any] | None = None     # 当前等你回答的问题
        self._answer: asyncio.Future | None = None

        self._subs: list[asyncio.Queue] = []
        self._stop = False

    # ---------- 基础工具 ----------
    @property
    def wolves(self) -> list[int]:
        return [p.seat for p in self.players if p.role is Role.WOLF]

    @property
    def alive(self) -> list[int]:
        return [p.seat for p in self.players if p.alive]

    def player(self, seat: int) -> Player:
        return self.players[seat - 1]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)

    def stop(self) -> None:
        self._stop = True

    def _check_stop(self) -> None:
        if self._stop:
            raise GameStopped()

    def _push(self, payload: dict[str, Any]) -> None:
        for q in list(self._subs):
            q.put_nowait(payload)

    def _emit(self, kind: str, text: str, audience: Any = "all", seat: int | None = None,
              stream: str = "state", **meta) -> Event:
        ev = Event(kind=kind, text=text, audience=audience, seat=seat,
                   round=self.round, phase=self.phase, meta={"stream": stream, **meta})
        self.events.append(ev)
        if self.tts and kind == "speech":
            self._warm_tts(ev)
        if self._visible(ev):
            self._push({"type": "event", "data": ev.to_json(), "state": self.state()})
        else:
            self._push({"type": "state", "state": self.state()})
        return ev

    def _warm_tts(self, ev: Event) -> None:
        """发言一出来就先去合成，等前端来取的时候基本已经在缓存里了。"""
        text = ev.text.split("：", 1)[-1]

        async def go() -> None:
            try:
                await tts_mod.synthesize(text[:400], ev.seat)
            except Exception:
                pass

        asyncio.get_running_loop().create_task(go())

    def _visible(self, ev: Event) -> bool:
        """模拟模式是上帝视角，全看得到；游玩模式只推你这个座位看得到的。"""
        return self.human is None or ev.visible_to(self.human)

    def _call_visible(self, c: LLMCall) -> bool:
        """游玩模式下别人的上下文和系统结算（含全部身份）都不能推给你。"""
        return self.human is None or c.seat == self.human

    def _record(self, call: LLMCall) -> None:
        self.calls.append(call)
        if self._call_visible(call):
            self._push({"type": "call", "data": call.to_json(), "state": self.state()})
        else:
            self._push({"type": "state", "state": self.state()})

    # ---------- 上下文 ----------
    def _delta_for(self, p: Player, instruction: str) -> str:
        """本轮新增：该玩家可见、且还没进过它上下文的事件 + 当前指令。"""
        speeches, states = [], []
        i = p.cursor
        while i < len(self.events):
            ev = self.events[i]
            if ev.visible_to(p.seat):
                (speeches if ev.meta.get("stream") == "speech" else states).append(ev.text)
            i += 1
        p.cursor = len(self.events)

        parts = []
        if speeches:
            parts.append("【历史发言】\n" + "\n".join(speeches))
        if states:
            parts.append("【历史投票和当前状态】\n" + "\n".join(states))
        parts.append("【现在轮到你】\n" + instruction)
        return "\n\n".join(parts)

    def _mock_ctx(self, p: Player, **extra) -> dict[str, Any]:
        return {
            "me": p.seat,
            "role": p.role.value,
            "alive": self.alive,
            "wolves": self.wolves if p.role is Role.WOLF else [],
            "checks": dict(self.seer_checks) if p.role is Role.SEER else {},
            "claimed_seer": self.claimed_seer,
            "suspicion": {s: v + self.rng.random() * 0.3 for s, v in self.suspicion.items()},
            "last_guard": self.last_guard,
            "threats": [s for s, v in sorted(self.suspicion.items(), key=lambda kv: -kv[1])[:2]],
            **extra,
        }

    def answer(self, payload: dict[str, Any]) -> bool:
        """前端把你的选择/发言送进来，唤醒正在等你的那一步。"""
        if not self.pending or not self._answer or self._answer.done():
            return False
        self._answer.set_result(payload)
        return True

    async def _ask_human(self, p: Player, kind: str, title: str, instruction: str,
                         options: list[int]) -> dict[str, Any] | None:
        """轮到你了：把问题推给前端，等你回答；超时返回 None 交给模型代打。"""
        loop = asyncio.get_running_loop()
        self._answer = loop.create_future()
        # 给人看的提示不用带 JSON 字段说明，那是给模型的
        human_text = instruction.split("输出 JSON")[0].strip()
        self.pending = {"kind": kind, "seat": p.seat, "title": title,
                        "instruction": human_text, "options": options,
                        "field": "speech" if kind == "speech" else "target",
                        "timeout": self.ANSWER_TIMEOUT, "asked_at": time.time()}
        self._push({"type": "prompt", "data": self.pending, "state": self.state()})
        try:
            return await asyncio.wait_for(self._answer, self.ANSWER_TIMEOUT)
        except asyncio.TimeoutError:
            return None
        finally:
            self.pending = None
            self._answer = None
            self._push({"type": "prompt", "data": None, "state": self.state()})

    async def _ask(self, p: Player, kind: str, title: str, instruction: str,
                   schema: dict, **mock_extra) -> dict[str, Any]:
        self._check_stop()
        delta = self._delta_for(p, instruction)
        p.messages.append({"role": "user", "content": delta})
        ctx_chars = p.ctx_chars

        if p.seat == self.human:
            pool = list((schema.get("properties", {}).get("target") or {}).get("enum") or [])
            ans = await self._ask_human(p, kind, title, instruction, pool)
            self._check_stop()
            if ans is not None:
                parsed = {"thinking": "（你自己的判断）", **ans}
                raw = json.dumps(parsed, ensure_ascii=False, indent=2)
                p.messages.append({"role": "assistant", "content": raw})
                p.decisions += 1
                self._record(LLMCall(seat=p.seat, title=title, system_prompt=p.system_prompt,
                                     delta=delta, output=raw, parsed=parsed, ctx_chars=ctx_chars,
                                     latency_ms=0, model="你", round=self.round, phase=self.phase))
                return parsed
            self._emit("system", f"{p.name} 超时未操作，本回合交给模型代打。",
                       audience=[p.seat] if self.human else "all")

        self._push({"type": "thinking", "seat": p.seat, "title": title, "state": self.state()})
        raw, parsed, ms, model = await self.llm.complete(
            p.system_prompt, p.messages, schema, kind, self._mock_ctx(p, **mock_extra)
        )
        p.messages.append({"role": "assistant", "content": raw})
        p.decisions += 1
        p.llm_calls += 1
        self._record(LLMCall(seat=p.seat, title=title, system_prompt=p.system_prompt, delta=delta,
                             output=raw, parsed=parsed, ctx_chars=ctx_chars, latency_ms=ms,
                             model=model, round=self.round, phase=self.phase))
        return parsed

    def _sys_call(self, title: str, delta: str, result: dict[str, Any]) -> None:
        """系统全局状态：不过模型，按规则结算的一步，也记进中间栏。"""
        self._record(LLMCall(seat=None, title=title, system_prompt="上帝视角：按规则结算，不调用模型。",
                             delta=delta, output=json.dumps(result, ensure_ascii=False, indent=2),
                             parsed=result, ctx_chars=0, latency_ms=0, model="rule-engine",
                             round=self.round, phase=self.phase))

    @staticmethod
    def _coerce(value: Any, pool: list[int], rng: random.Random) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = -1
        return v if v in pool else rng.choice(pool)

    # ---------- 对局主流程 ----------
    def state(self) -> dict[str, Any]:
        reveal = self.status in ("finished", "stopped")
        show_roles = reveal or self.human is None      # 游玩模式没结束前只能看到自己的身份
        return {
            "gid": self.gid,
            "status": self.status,
            "round": self.round,
            "phase": self.phase,
            "winner": self.winner,
            "seed": self.seed,
            "model": self.llm.model,
            "tts": self.tts,
            "reveal": reveal,
            "alive": self.alive,
            "calls": len(self.calls),
            "llm_calls": sum(p.llm_calls for p in self.players),
            "sys_calls": sum(1 for c in self.calls if c.seat is None),
            "elapsed": (self.finished_at or time.time()) - self.started_at,
            "mode": self.mode,
            "human": self.human,
            "pending": self.pending,
            "players": [p.public(reveal=show_roles or p.seat == self.human)
                        for p in self.players],
        }

    async def run(self) -> None:
        self.status = "running"
        self.phase = "开局"
        names = "、".join(p.name for p in self.players)
        self._emit("system", f"游戏开始，共 6 名玩家：{names}。配置：2 狼人、2 平民、1 预言家、1 守卫。")
        self._sys_call("系统全局状态 开局", "发牌与阵营划分",
                       {"座位": {p.name: p.role.value for p in self.players},
                        "狼队": [f"{s}号" for s in self.wolves], "seed": self.seed})
        try:
            while self.round < 8:
                self.round += 1
                await self._night()
                await self._day()
                if self._settle():
                    break
            else:                                  # 8 轮还没分出胜负（极少见）
                self.winner = "平局"
                self._emit("result", "打满 8 轮仍未分出胜负，本局按平局处理。")
            self.status = "finished"
        except GameStopped:
            self.status = "stopped"
            self._emit("system", "对局被手动停止。")
        finally:
            self.finished_at = time.time()
            self._push({"type": "state", "state": self.state()})
            self.save()

    # ---------- 夜晚 ----------
    async def _night(self) -> None:
        """守卫 / 狼队 / 预言家在同一个夜里同时行动，三条线并发跑（狼队内部仍按顺序商议）。"""
        self.phase = "night"
        self._emit("phase", f"—— 第 {self.round} 夜 ——")
        guarded: int | None = None
        target: int | None = None

        async def guard_step() -> None:
            nonlocal guarded
            guard = next((p for p in self.players if p.role is Role.GUARD and p.alive), None)
            if not guard:
                return
            instr, schema = prompts.guard_instruction(self.round, self.alive, self.last_guard)
            out = await self._ask(guard, "guard", f"{guard.name} 第{self.round}夜", instr, schema)
            pool = [s for s in self.alive if s != self.last_guard]
            guarded = self._coerce(out.get("target"), pool, self.rng)
            self._emit("night_action", f"（第 {self.round} 夜你守护了 {guarded} 号）",
                       audience=[guard.seat], seat=guard.seat)

        async def wolf_step() -> None:
            nonlocal target
            wolves = [p for p in self.players if p.role is Role.WOLF and p.alive]
            if not wolves:
                return
            wolf_seats = [p.seat for p in wolves]
            proposals: dict[int, int] = {}
            for w in wolves:                      # 狼队要看到彼此的提议，所以这里保持串行
                instr, schema = prompts.wolf_instruction(
                    self.round, self.alive, [s for s in wolf_seats if s != w.seat],
                    wolves=wolf_seats)
                out = await self._ask(w, "wolf", f"{w.name} 第{self.round}夜", instr, schema)
                pool = [s for s in self.alive if s not in wolf_seats] or self.alive
                t = self._coerce(out.get("target"), pool, self.rng)
                proposals[w.seat] = t
                self._emit("night_action",
                           f"{w.name}（狼）：提议刀 {t} 号。{str(out.get('reason', '')).strip()}",
                           audience=wolf_seats, seat=w.seat)
            counts: dict[int, int] = {}
            for t in proposals.values():
                counts[t] = counts.get(t, 0) + 1
            target = max(counts, key=lambda t: (counts[t], -t))
            detail = "，".join(f"{s}: {t}" for s, t in proposals.items())
            self._emit("night_action", f"今晚刀口定为 {target} 号（各自报的：{{{detail}}}）。",
                       audience=wolf_seats)

        async def seer_step() -> None:
            seer = next((p for p in self.players if p.role is Role.SEER and p.alive), None)
            if not seer:
                return
            instr, schema = prompts.seer_instruction(self.round, self.alive,
                                                     sorted(self.seer_checks), me=seer.seat)
            out = await self._ask(seer, "seer", f"{seer.name} 第{self.round}夜", instr, schema)
            pool = [s for s in self.alive if s != seer.seat and s not in self.seer_checks] \
                or [s for s in self.alive if s != seer.seat]
            t = self._coerce(out.get("target"), pool, self.rng)
            result = "狼人" if self.player(t).role is Role.WOLF else "好人"
            self.seer_checks[t] = result
            self._emit("night_action", f"（第 {self.round} 夜你查验了 {t} 号）",
                       audience=[seer.seat], seat=seer.seat)
            self._emit("night_action", f"【查验结果】{t} 号的身份是：{result}。",
                       audience=[seer.seat], seat=seer.seat)

        await self._gather(guard_step(), wolf_step(), seer_step())
        self.last_guard = guarded

        # 结算：被守的人当晚不死
        died = None if (target is None or target == guarded) else target
        self._sys_call(f"系统全局状态 第{self.round}夜",
                       f"守卫守护={guarded}，狼队刀口={target}",
                       {"守护": guarded, "刀口": target,
                        "结果": "平安夜（守护成功）" if died is None else f"{died} 号出局"})
        if died is not None:
            p = self.player(died)
            p.alive, p.death_round, p.death_cause = False, self.round, "夜间被刀"
        self.night_death = died

    @staticmethod
    async def _gather(*coros) -> None:
        """并发跑若干步；任一步抛错（比如被手动停止）就在全部收尾后抛出。"""
        results = await asyncio.gather(*coros, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                raise r

    # ---------- 白天 ----------
    async def _day(self) -> None:
        self.phase = "day"
        self._emit("phase", f"—— 第 {self.round} 天 ——")
        if self.night_death is None:
            self._emit("result", "昨晚是平安夜。")
        else:
            self._emit("result", f"昨晚 {self.night_death} 号 倒牌出局，不留遗言。")
        if self._winner():
            return

        alive = self.alive
        start = self.rng.choice(alive)
        i = alive.index(start)
        order = alive[i:] + alive[:i]
        self._emit("system", f"存活玩家：{alive}，发言顺序：{order}。", order=order)

        for seat in order:
            p = self.player(seat)
            if not p.alive:
                continue
            instr, schema = prompts.speech_instruction(self.round, self.alive, order)
            out = await self._ask(p, "speech", f"{p.name} 第{self.round}天", instr, schema)
            speech = str(out.get("speech", "")).strip() or "过。"
            self._emit("speech", f"{p.name}：{speech}", seat=seat, stream="speech")
            if p.role is Role.SEER and self.claimed_seer is None and "预言家" in speech:
                self.claimed_seer = seat
            elif self.claimed_seer is None and ("我跳预言家" in speech or "我是预言家" in speech):
                self.claimed_seer = seat
            for other in self.alive:
                if other != seat and (f"{other} 号" in speech or f"{other}号" in speech):
                    self.suspicion[other] = self.suspicion.get(other, 0.0) + 0.3

        # 投票
        votes: dict[int, int] = {}
        voters = [s for s in order if self.player(s).alive]
        reasons: dict[int, str] = {}

        async def vote_one(seat: int) -> None:
            p = self.player(seat)
            instr, schema = prompts.vote_instruction(self.round, self.alive, seat)
            out = await self._ask(p, "vote", f"{p.name} 第{self.round}天投票", instr, schema)
            pool = [s for s in self.alive if s != seat]
            votes[seat] = self._coerce(out.get("target"), pool, self.rng)
            reasons[seat] = str(out.get("reason", "")).strip()

        await self._gather(*(vote_one(s) for s in voters))   # 投票是同时的，所以并发
        for seat in voters:
            t = votes[seat]
            self.suspicion[t] = self.suspicion.get(t, 0.0) + 1.0
            self._emit("vote", f"{self.player(seat).name} → {t}号：{reasons[seat]}",
                       seat=seat, target=t)

        tally: dict[int, int] = {}
        for t in votes.values():
            tally[t] = tally.get(t, 0) + 1
        top = max(tally.values()) if tally else 0
        tied = [s for s, n in tally.items() if n == top]
        detail = "，".join(f"{s}号 {n}票" for s, n in sorted(tally.items(), key=lambda kv: -kv[1]))
        if len(tied) == 1:
            out_seat = tied[0]
            p = self.player(out_seat)
            p.alive, p.death_round, p.death_cause = False, self.round, "被投票放逐"
            self._emit("result", f"投票结果：{detail}。{out_seat} 号被放逐出局。", tally=tally)
        else:
            out_seat = None
            self._emit("result", f"投票结果：{detail}。平票，本轮无人出局。", tally=tally)
        self._sys_call(f"系统全局状态 第{self.round}天投票", f"票型：{votes}",
                       {"票型": {f"{k}号": f"{v}号" for k, v in votes.items()},
                        "计票": {f"{k}号": v for k, v in tally.items()},
                        "出局": f"{out_seat}号" if out_seat else "平票，无人出局"})

    # ---------- 胜负 ----------
    def _winner(self) -> str | None:
        wolves = [p for p in self.players if p.role is Role.WOLF and p.alive]
        goods = [p for p in self.players if p.role is not Role.WOLF and p.alive]
        if not wolves:
            return "好人阵营"
        if len(wolves) >= len(goods):
            return "狼人阵营"
        return None

    def _settle(self) -> bool:
        w = self._winner()
        if not w:
            return False
        self.winner = w
        roles = "、".join(f"{p.name}{p.role.value}" for p in self.players)
        self._emit("result", f"游戏结束，{w}胜利。身份公布：{roles}。", winner=w)
        self._sys_call("系统全局状态 结算", "胜负判定",
                       {"胜方": w, "身份": {p.name: p.role.value for p in self.players}})
        return True

    # ---------- 存档 ----------
    def to_json(self, filtered: bool = False) -> dict[str, Any]:
        """filtered=True 给前端用：游玩模式下裁掉你看不到的事件和别人的上下文。
        存档始终存完整的上帝视角，复盘时才看得到狼队夜里聊了什么。"""
        events = self.events
        calls = self.calls
        if filtered and self.human is not None:
            events = [e for e in events if self._visible(e)]
            calls = [c for c in calls if self._call_visible(c)]
        return {
            "gid": self.gid,
            "state": self.state(),
            "events": [e.to_json() for e in events],
            "calls": [c.to_json() for c in calls],
        }

    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        (DATA_DIR / f"{self.gid}.json").write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
