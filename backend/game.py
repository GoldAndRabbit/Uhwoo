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

GAMES_DIR = Path(__file__).resolve().parent.parent / "logs" / "games"   # 对局存档


class GameStopped(Exception):
    pass


class Game:
    ANSWER_TIMEOUT = 300          # 轮到你却一直不答，超时就交给模型代打，别把整局挂死

    def __init__(self, gid: str, seed: int | None = None, use_api: bool | None = None,
                 model: str | None = None, tts: bool = False, mode: str = "sim",
                 human_seat: int | None = None, human_role: str | None = None,
                 clone: bool | None = None, humans: int = 0,
                 names: dict[int, str] | None = None):
        self.gid = gid
        self.tts = bool(tts) and tts_mod.available()
        self.clone = tts_mod.load_tts_config().clone_default_on if clone is None else bool(clone)
        self.mode = mode if mode in ("sim", "play", "multi") else "sim"
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

        # 有人坐的座位，其余交给模型。视野严格按座位裁。
        # 单人模式可以指定身份（从该身份的座位里挑一个）；多人模式按人数随机分座位。
        self.humans: set[int] = set()
        if self.mode == "play":
            if human_seat in range(1, 7):
                self.humans = {human_seat}
            elif human_role:
                pool = [p.seat for p in self.players if p.role.value == human_role]
                self.humans = {self.rng.choice(pool) if pool else self.rng.randint(1, 6)}
            else:
                self.humans = {self.rng.randint(1, 6)}
        elif self.mode == "multi":
            seats = list(range(1, 7))
            self.rng.shuffle(seats)
            self.humans = set(seats[:max(1, min(6, humans))])
        self.names: dict[int, str] = dict(names or {})
        self.pending: dict[int, dict[str, Any]] = {}   # 座位 -> 正在等他回答的问题
        self._answers: dict[int, asyncio.Future] = {}

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

    def single_viewer(self) -> int | None:
        """旧的单人/模拟接口只有一个视角：单人模式就是那个座位，模拟模式是上帝视角。"""
        return next(iter(self.humans)) if len(self.humans) == 1 else None

    def subscribe(self, viewer: int | None = None) -> asyncio.Queue:
        """viewer=座位号表示这个连接只能看到那个座位的视野；None 是上帝视角。"""
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append((q, viewer))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs = [(x, v) for x, v in self._subs if x is not q]

    def stop(self) -> None:
        self._stop = True
        # 正等着你回答的话，这里必须把那个 future 叫醒 —— 否则引擎会一直挂在 await 上，
        # 要等 300s 超时才发现该停了（表现就是「停止按钮按不动」）。
        for fut in list(self._answers.values()):
            if not fut.done():
                fut.cancel()
        self._push({"type": "state"})

    def _check_stop(self) -> None:
        if self._stop:
            raise GameStopped()

    def _push(self, payload: dict[str, Any]) -> None:
        """按每个连接自己的视角裁剪后再投递 —— 多人局里每个人看到的东西都不一样。"""
        for q, viewer in list(self._subs):
            kind = payload.get("type")
            if kind == "event" and not self._visible_raw(payload["_ev"], viewer):
                out = {"type": "state"}
            elif kind == "call" and not self._call_visible_raw(payload["_call"], viewer):
                out = {"type": "state"}
            elif kind == "prompt" and payload.get("seat") != viewer:
                continue                       # 别人的回合不推给你
            else:
                out = {k: v for k, v in payload.items() if not k.startswith("_")}
            out = dict(out)
            out["state"] = self.state(viewer)
            q.put_nowait(out)

    def _emit(self, kind: str, text: str, audience: Any = "all", seat: int | None = None,
              stream: str = "state", **meta) -> Event:
        ev = Event(kind=kind, text=text, audience=audience, seat=seat,
                   round=self.round, phase=self.phase, meta={"stream": stream, **meta})
        self.events.append(ev)
        if self.tts:
            self._warm_tts(ev)
        self._push({"type": "event", "data": ev.to_json(), "_ev": ev})
        return ev

    JUDGE_VOICE = 0          # 法官（上帝）：只报天数、死讯和票型，用 0 号音色

    def _narration(self, ev: Event) -> tuple[int | None, str] | None:
        """这条事件要不要念、用谁的声音念 —— 和前端 narration() 保持一致。"""
        if ev.kind == "speech":
            return ev.seat, ev.text.split("：", 1)[-1]
        if ev.kind in ("result", "judge"):
            return self.JUDGE_VOICE, ev.text
        if ev.kind == "night_action" and self.humans:
            if ev.seat and "：" in ev.text:            # 狼队商议是角色在说
                return ev.seat, ev.text.split("：", 1)[-1]
            return self.JUDGE_VOICE, ev.text
        return None                                   # 分隔线、存活清单、逐条票型都不念

    def _warm_tts(self, ev: Event) -> None:
        """事件一落地就先去合成，等前端来取的时候基本已经在缓存里了。"""
        line = self._narration(ev)
        if not line:
            return
        seat, text = line

        async def go() -> None:
            try:
                await tts_mod.synthesize(text[:400], seat, clone=self.clone)
            except Exception:
                pass

        asyncio.get_running_loop().create_task(go())

    def _judge(self, text: str) -> Event:
        """法官（上帝）报幕。纯固定台词，不过模型，两种模式都有。"""
        return self._emit("judge", text)

    def _visible_raw(self, ev: Event, viewer: int | None) -> bool:
        """模拟模式（viewer=None）是上帝视角；有座位的人只看得到自己视野内的事件。"""
        return viewer is None or ev.visible_to(viewer)

    def _call_visible_raw(self, c: LLMCall, viewer: int | None) -> bool:
        """别人的上下文和系统结算（含全部身份）都不能推给坐在局里的人。"""
        return viewer is None or c.seat == viewer

    def _record(self, call: LLMCall) -> None:
        self.calls.append(call)
        self._push({"type": "call", "data": call.to_json(), "_call": call})

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

    def answer(self, seat: int, payload: dict[str, Any]) -> bool:
        """前端把某个座位的选择/发言送进来，唤醒正在等他的那一步。"""
        fut = self._answers.get(seat)
        if seat not in self.pending or not fut or fut.done():
            return False
        fut.set_result(payload)
        return True

    async def _ask_human(self, p: Player, kind: str, title: str, instruction: str,
                         options: list[int]) -> dict[str, Any] | None:
        """轮到你了：把问题推给前端，等你回答；超时返回 None 交给模型代打。"""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._answers[p.seat] = fut
        # 给人看的提示不用带 JSON 字段说明，那是给模型的
        human_text = instruction.split("输出 JSON")[0].strip()
        q = {"kind": kind, "seat": p.seat, "title": title,
             "instruction": human_text, "options": options,
             "field": "speech" if kind == "speech" else "target",
             "timeout": self.ANSWER_TIMEOUT, "asked_at": time.time()}
        self.pending[p.seat] = q
        self._push({"type": "prompt", "data": q, "seat": p.seat})
        try:
            return await asyncio.wait_for(fut, self.ANSWER_TIMEOUT)
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            return None                 # 被 stop() 取消，交给下面的 _check_stop 收尾
        finally:
            self.pending.pop(p.seat, None)
            self._answers.pop(p.seat, None)
            self._push({"type": "prompt", "data": None, "seat": p.seat})

    async def _ask(self, p: Player, kind: str, title: str, instruction: str,
                   schema: dict, **mock_extra) -> dict[str, Any]:
        self._check_stop()
        delta = self._delta_for(p, instruction)
        p.messages.append({"role": "user", "content": delta})
        ctx_chars = p.ctx_chars

        if p.seat in self.humans:
            pool = list((schema.get("properties", {}).get("target") or {}).get("enum") or [])
            ans = await self._ask_human(p, kind, title, instruction, pool)
            self._check_stop()          # 停止是在这里生效的
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
                       audience=[p.seat] if self.humans else "all")

        self._push({"type": "thinking", "seat": p.seat, "title": title})
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
    def state(self, viewer: int | None = None) -> dict[str, Any]:
        reveal = self.status in ("finished", "stopped")
        show_roles = reveal or not self.humans        # 有人坐在局里时，没结束前只能看到自己的身份
        return {
            "gid": self.gid,
            "status": self.status,
            "round": self.round,
            "phase": self.phase,
            "winner": self.winner,
            "seed": self.seed,
            "model": self.llm.model,
            "tts": self.tts,
            "clone": self.clone,
            "reveal": reveal,
            "alive": self.alive,
            "calls": len(self.calls),
            "llm_calls": sum(p.llm_calls for p in self.players),
            "sys_calls": sum(1 for c in self.calls if c.seat is None),
            "elapsed": (self.finished_at or time.time()) - self.started_at,
            "mode": self.mode,
            "human": viewer,
            "humans": sorted(self.humans),
            "names": {str(k): v for k, v in self.names.items()},
            "pending": self.pending.get(viewer) if viewer else None,
            "players": [p.public(reveal=show_roles or p.seat == viewer)
                        for p in self.players],
        }

    async def run(self) -> None:
        self.status = "running"
        self.phase = "开局"
        names = "、".join(p.name for p in self.players)
        self._emit("system", f"游戏开始，共 6 名玩家：{names}。配置：2 狼人、2 平民、1 预言家、1 守卫。")
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
            self._push({"type": "state"})
            self.save()

    # ---------- 夜晚 ----------
    async def _night(self) -> None:
        """守卫 / 狼队 / 预言家在同一个夜里同时行动，三条线并发跑（狼队内部仍按顺序商议）。"""
        self.phase = "night"
        self._emit("phase", f"—— 第 {self.round} 夜 ——")
        self._judge(f"天黑请闭眼。现在是第 {self.round} 夜。")
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
            self._emit("night_action",
                       f"（第 {self.round} 夜你查验了 {t} 号）结果：{t} 号是{result}。",
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
        dead = ("昨晚是平安夜。" if self.night_death is None
                else f"昨晚 {self.night_death} 号 倒牌出局，不留遗言。")
        # 法官一口气报完：天数 + 死讯 + 发言顺序，别拆成三条
        if self._winner():
            self._judge(f"天亮了。现在是第 {self.round} 天。{dead}")
            return

        alive = self.alive
        start = self.rng.choice(alive)
        i = alive.index(start)
        order = alive[i:] + alive[:i]
        self._judge(f"天亮了。现在是第 {self.round} 天。{dead}"
                    f"现在开始发言，从 {start} 号开始。")
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

        async def vote_one(seat: int) -> None:
            p = self.player(seat)
            instr, schema = prompts.vote_instruction(self.round, self.alive, seat)
            out = await self._ask(p, "vote", f"{p.name} 第{self.round}天投票", instr, schema)
            pool = [s for s in self.alive if s != seat]
            votes[seat] = self._coerce(out.get("target"), pool, self.rng)

        await self._gather(*(vote_one(s) for s in voters))   # 投票是同时的，所以并发
        for seat in voters:                                  # 投票不留理由，只报票型
            t = votes[seat]
            self.suspicion[t] = self.suspicion.get(t, 0.0) + 1.0
            self._emit("vote", f"{self.player(seat).name} → {t}号", seat=seat, target=t)

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
    def to_json(self, viewer: int | None = None) -> dict[str, Any]:
        """给前端用：坐在局里的人只拿得到自己视野内的事件和自己的上下文。
        存档始终存完整的上帝视角（viewer=None），复盘时才看得到狼队夜里聊了什么。"""
        events = self.events
        calls = self.calls
        if viewer is not None:
            events = [e for e in events if self._visible_raw(e, viewer)]
            calls = [c for c in calls if self._call_visible_raw(c, viewer)]
        return {
            "gid": self.gid,
            "state": self.state(viewer),
            "events": [e.to_json() for e in events],
            "calls": [c.to_json() for c in calls],
        }

    def save(self) -> None:
        GAMES_DIR.mkdir(parents=True, exist_ok=True)
        (GAMES_DIR / f"{self.gid}.json").write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
