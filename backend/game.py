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
from .model import DEFAULT_SETUP, FACES_F, FACES_M, SETUPS, Event, LLMCall, Player, Role

GAMES_DIR = Path(__file__).resolve().parent.parent / "logs" / "games"   # 对局存档


class GameStopped(Exception):
    pass


class Game:
    ANSWER_TIMEOUT = 300          # 轮到你却一直不答，超时就交给模型代打，别把整局挂死

    def __init__(self, gid: str, seed: int | None = None, use_api: bool | None = None,
                 model: str | None = None, tts: bool = False, mode: str = "sim",
                 human_seat: int | None = None, human_role: str | None = None,
                 clone: bool | None = None, humans: int = 0,
                 names: dict[int, str] | None = None, setup: str | None = None):
        self.gid = gid
        self.setup_key = setup if setup in SETUPS else DEFAULT_SETUP
        self.setup = SETUPS[self.setup_key]
        self.size = len(self.setup["roles"])
        self.tts = bool(tts) and tts_mod.available()
        self.clone = tts_mod.load_tts_config().clone_default_on if clone is None else bool(clone)
        self.mode = mode if mode in ("sim", "play", "multi") else "sim"
        self.seed = seed if seed is not None else random.randrange(10**6)
        self.rng = random.Random(self.seed)
        self.llm = LLMClient(use_api=use_api, seed=self.seed, model=model)

        roles = list(self.setup["roles"])
        self.rng.shuffle(roles)
        self.players = [Player(seat=i + 1, role=roles[i]) for i in range(self.size)]
        wolves = [p.seat for p in self.players if p.role is Role.WOLF]
        # 每个人摇一套说话习惯/推理方式/脾气/打法，塞进常驻的 system 段。
        # 六个人共用一个模型，不给人设的话发言会齐刷刷地一个味道
        self.personas: dict[int, dict[str, str]] = {}
        for p in self.players:
            persona = {
                "说话习惯": self.rng.choice(prompts.SPEECH_TICS),
                "推理方式": self.rng.choice(prompts.REASONING),
                "脾气": self.rng.choice(prompts.MOODS),
                "打法": self.rng.choice(prompts.TACTICS),
            }
            self.personas[p.seat] = persona
            p.system_prompt = prompts.build_system(
                p, [s for s in wolves if s != p.seat],
                prompts.persona_text(*persona.values()), self.setup)
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
        self.night_deaths: list[int] = []        # 一晚可能死两个（狼刀 + 女巫毒）
        self.witch_cure = True                   # 解药
        self.witch_poison = True                 # 毒药
        self.seer_checks: dict[int, str] = {}
        self.claimed_seer: int | None = None
        self.suspicion: dict[int, float] = {p.seat: 0.0 for p in self.players}

        # 有人坐的座位，其余交给模型。视野严格按座位裁。
        # 单人模式可以指定身份（从该身份的座位里挑一个）；多人模式按人数随机分座位。
        self.humans: set[int] = set()
        if self.mode == "play":
            if human_seat in range(1, self.size + 1):
                self.humans = {human_seat}
            elif human_role:
                pool = [p.seat for p in self.players if p.role.value == human_role]
                self.humans = {self.rng.choice(pool) if pool else self.rng.randint(1, self.size)}
            else:
                self.humans = {self.rng.randint(1, self.size)}
        elif self.mode == "multi":
            seats = list(range(1, self.size + 1))
            self.rng.shuffle(seats)
            self.humans = set(seats[:max(1, min(self.size, humans))])
        self.names: dict[int, str] = dict(names or {})
        # 每局随机 3 女 3 男，打乱之后发给 1–6 号：同一局里不重样，换一局就换一拨人。
        # 跟着 self.rng 走，所以同一个 seed 复盘出来的还是同一批脸。
        half = self.size // 2
        women = self.rng.sample(FACES_F, min(half, len(FACES_F)))
        men = self.rng.sample(FACES_M, self.size - len(women) + 1)   # 多抽一张给法官
        faces = women + men[:self.size - len(women)]
        self.rng.shuffle(faces)
        self.avatars: dict[int, str] = dict(zip(range(1, self.size + 1), faces))
        # 0 号是法官：也每局换一张脸，从男池里挑没被玩家占用的那张
        # （法官那把嗓子是男声，脸得对得上）
        self.avatars[0] = men[self.size - len(women)]
        # 朗读用哪把嗓子：性别跟着头像走，同性别的三个人各拿一把，不重样。
        # 编码成 "f0"/"m2" 这样的槽位，前端朗读时原样传回来（见 tts.pick）
        # 只发给 1–6 号；法官走自己那把嗓子（见 tts.pick），不占槽位，
        # 否则它会拿到 m3，回头 % 池长度又绕回 m0，和某个玩家撞车
        slots: dict[str, int] = {"f": 0, "m": 0}
        self.voices: dict[int, str] = {}
        for seat in range(1, self.size + 1):
            g = self.avatars[seat][0]
            self.voices[seat] = f"{g}{slots[g]}"
            slots[g] += 1
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
                await tts_mod.synthesize(text[:400], self.voices.get(seat or 0),
                                         clone=self.clone)
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
            "size": self.size,
            "setup": self.setup_key,
            "setup_name": self.setup["name"],
            "setup_desc": self.setup["desc"],
            "n_wolves": sum(1 for r in self.setup["roles"] if r is Role.WOLF),
            "human": viewer,
            "humans": sorted(self.humans),
            "names": {str(k): v for k, v in self.names.items()},
            "avatars": {str(k): v for k, v in self.avatars.items()},
            "voices": {str(k): v for k, v in self.voices.items()},
            "personas": {str(k): v for k, v in self.personas.items()},
            "pending": self.pending.get(viewer) if viewer else None,
            "players": [p.public(reveal=show_roles or p.seat == viewer)
                        for p in self.players],
        }

    async def run(self) -> None:
        self.status = "running"
        self.phase = "开局"
        names = "、".join(p.name for p in self.players)
        self._emit("system", f"游戏开始，共 {self.size} 名玩家：{names}。"
                             f"配置：{self.setup['desc']}。胜负算屠边。")
        try:
            while self.round < 8:
                self.round += 1
                await self._night()
                await self._day()
                if self._settle():
                    await self._pick_mvp()
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
            # 各自报了谁，上面每条商议里都说过了，这里再列一遍字典既啰嗦、念出来也难听
            self._emit("night_action", f"今晚刀口定为 {target} 号。", audience=wolf_seats)

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

        # 女巫要知道今晚谁被刀，所以她只能等狼队定完刀口再动 —— 这一步不能并发
        saved = False
        poisoned: int | None = None
        witch = next((p for p in self.players if p.role is Role.WITCH and p.alive), None)
        if witch and (self.witch_cure or self.witch_poison):
            killed = None if (target is None or target == guarded) else target
            instr, schema = prompts.witch_instruction(
                self.round, self.alive, killed, witch.seat, self.witch_cure, self.witch_poison)
            out = await self._ask(witch, "witch", f"{witch.name} 第{self.round}夜", instr, schema,
                                  killed=killed, has_cure=self.witch_cure,
                                  has_poison=self.witch_poison)
            act = str(out.get("action", "pass"))
            if act == "save" and self.witch_cure and killed and killed != witch.seat:
                saved, self.witch_cure = True, False
                self._emit("night_action", f"（第 {self.round} 夜你用掉了解药，救下 {killed} 号）",
                           audience=[witch.seat], seat=witch.seat)
            elif act == "poison" and self.witch_poison:
                pool = [s for s in self.alive if s != witch.seat]
                poisoned = self._coerce(out.get("target"), pool, self.rng)
                self.witch_poison = False
                self._emit("night_action", f"（第 {self.round} 夜你用掉了毒药，毒杀 {poisoned} 号）",
                           audience=[witch.seat], seat=witch.seat)

        # 结算：被守的人当晚不死，被救的也不死；被毒的另算一个
        died = None if (target is None or target == guarded or saved) else target
        deaths: list[int] = []
        for seat, cause in ((died, "夜间被刀"), (poisoned, "被女巫毒杀")):
            if seat is None or seat in deaths:      # 毒的正好是刀的那个，别记两次
                continue
            p = self.player(seat)
            p.alive, p.death_round, p.death_cause = False, self.round, cause
            deaths.append(seat)
        self._sys_call(f"系统全局状态 第{self.round}夜",
                       f"守护={guarded}，刀口={target}，解药={saved}，毒药={poisoned}",
                       {"守护": guarded, "刀口": target, "解药": saved, "毒药": poisoned,
                        "结果": "平安夜" if not deaths else "、".join(f"{s} 号出局" for s in deaths)})
        self.night_death = died
        self.night_deaths = deaths

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
        if not self.night_deaths:
            dead = "昨晚是平安夜。"
        else:
            who = "、".join(f"{s} 号" for s in self.night_deaths)
            dead = f"昨晚 {who} 倒牌出局，不留遗言。"
        if self.night_deaths:
            await self._hunter_shot(self.night_deaths)
        # 法官一口气报完：天数 + 死讯 + 发言顺序，别拆成三条
        if self._winner():
            self._judge(f"天亮了。现在是第 {self.round} 天。{dead}")
            return

        alive = self.alive
        start = self.rng.choice(alive)
        i = alive.index(start)
        order = alive[i:] + alive[:i]
        # 顺序念全了才知道自己什么时候说话，只说「从 X 号开始」不够。
        # 后面那串只报数字：「六、一、二、四、五」比「六号、一号、二号…」顺耳得多
        seq = "、".join(str(s) for s in order)
        self._judge(f"天亮了。现在是第 {self.round} 天。{dead}"
                    f"现在开始发言，从 {start} 号开始，发言顺序依次是 {seq}。")
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
        votes, tally, tied = await self._vote_round(
            [s for s in order if self.player(s).alive],
            lambda seat: [x for x in self.alive if x != seat], "投票")
        detail = self._tally_text(tally)
        out_seat: int | None = None

        if len(tied) == 1:
            out_seat = tied[0]
            await self._banish(out_seat, f"投票结果：{detail}。")
        else:
            # 平票不是直接过夜：平票的人各再说一轮，其余人在他们之间重投一次。
            # 还平票才天黑 —— 不然一局里最有信息量的对峙就这么被跳过去了。
            names = "、".join(f"{s} 号" for s in tied)
            self._emit("result", f"投票结果：{detail}。{names}平票。",
                       tally=tally, tied=tied)
            self._judge(f"{names}平票，各做一次补充发言，然后由其余玩家在他们之间重新投票。")
            for seat in tied:
                p = self.player(seat)
                instr, schema = prompts.pk_speech_instruction(self.round, tied, seat)
                out = await self._ask(p, "speech", f"{p.name} 第{self.round}天 补充发言", instr, schema)
                speech = str(out.get("speech", "")).strip() or "我没什么好补充的。"
                self._emit("speech", f"{p.name}：{speech}", seat=seat, stream="speech")

            pk_voters = [s for s in self.alive if s not in tied]
            if not pk_voters:
                self._emit("result", "没有其他玩家可以投票，本轮无人出局。", tally=tally)
            else:
                votes2, tally2, tied2 = await self._vote_round(
                    pk_voters, lambda seat: tied, "补充投票", pk=tied)
                detail2 = self._tally_text(tally2)
                if len(tied2) == 1:
                    out_seat = tied2[0]
                    await self._banish(out_seat, f"补充投票：{detail2}。")
                else:
                    self._emit("result", f"补充投票：{detail2}。依然平票，本轮无人出局。",
                               tally=tally2)
                votes = {**votes, **votes2}
                tally = tally2

        self._sys_call(f"系统全局状态 第{self.round}天投票", f"票型：{votes}",
                       {"票型": {f"{k}号": f"{v}号" for k, v in votes.items()},
                        "计票": {f"{k}号": v for k, v in tally.items()},
                        "出局": f"{out_seat}号" if out_seat else "平票，无人出局"})

    async def _hunter_shot(self, deaths: list[int]) -> None:
        """猎人出局就能开枪带走一个人 —— 被女巫毒死除外（毒死的猎人开不了枪）。"""
        for seat in list(deaths):
            p = self.player(seat)
            if p.role is not Role.HUNTER or p.death_cause == "被女巫毒杀":
                continue
            self._judge(f"{seat} 号是猎人，出局时可以开枪。")
            pool = [s for s in self.alive if s != seat]
            if not pool:
                continue
            instr, schema = prompts.hunter_instruction(self.round, self.alive, seat)
            out = await self._ask(p, "hunter", f"{p.name} 开枪", instr, schema)
            try:
                shot = int(out.get("target") or 0)
            except (TypeError, ValueError):
                shot = 0
            if shot not in pool:
                self._emit("result", f"{seat} 号没有开枪。", hunter=seat)
                continue
            t = self.player(shot)
            t.alive, t.death_round, t.death_cause = False, self.round, "被猎人带走"
            self._emit("result", f"{seat} 号开枪带走了 {shot} 号。", hunter=seat, shot=shot)
            await self._hunter_shot([shot])          # 枪口下还可能是另一个猎人

    async def _vote_round(self, voters: list[int], pool_for, tag: str,
                          pk: list[int] | None = None) -> tuple[dict[int, int],
                                                                dict[int, int], list[int]]:
        """跑一轮投票：并发问所有人 → 逐条播票型 → 返回 (票, 计票, 并列最高的人)。"""
        votes: dict[int, int] = {}

        async def one(seat: int) -> None:
            p = self.player(seat)
            pool = pool_for(seat)
            if pk:
                instr, schema = prompts.pk_vote_instruction(self.round, pk, seat)
            else:
                instr, schema = prompts.vote_instruction(self.round, self.alive, seat, pool)
            out = await self._ask(p, "vote", f"{p.name} 第{self.round}天{tag}", instr, schema)
            votes[seat] = self._coerce(out.get("target"), pool, self.rng)

        await self._gather(*(one(s) for s in voters))   # 投票是同时的，所以并发
        for seat in voters:                             # 投票不留理由，只报票型
            t = votes[seat]
            self.suspicion[t] = self.suspicion.get(t, 0.0) + 1.0
            self._emit("vote", f"{self.player(seat).name} → {t}号", seat=seat, target=t)

        tally: dict[int, int] = {}
        for t in votes.values():
            tally[t] = tally.get(t, 0) + 1
        top = max(tally.values()) if tally else 0
        return votes, tally, [s for s, n in tally.items() if n == top]

    @staticmethod
    def _tally_text(tally: dict[int, int]) -> str:
        return "，".join(f"{s}号 {n}票" for s, n in sorted(tally.items(), key=lambda kv: -kv[1]))

    async def _banish(self, seat: int, prefix: str) -> None:
        p = self.player(seat)
        p.alive, p.death_round, p.death_cause = False, self.round, "被投票放逐"
        self._emit("result", f"{prefix}{seat} 号被放逐出局。", tally={}, out=seat)
        await self._hunter_shot([seat])

    # ---------- 胜负 ----------
    def _winner(self) -> str | None:
        """屠边：民或神被杀光一边，狼就赢，不必杀光所有人。"""
        wolves = [p for p in self.players if p.role is Role.WOLF and p.alive]
        if not wolves:
            return "好人阵营"
        villagers = [p for p in self.players
                     if p.alive and p.role is not Role.WOLF and not p.role.is_god]
        gods = [p for p in self.players if p.alive and p.role.is_god]
        if not villagers or not gods:
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

    async def _pick_mvp(self) -> None:
        """法官复盘整局，评一个 MVP 出来。评的是决策质量，输的一方也可以拿。

        走一次独立的模型调用（不挂在任何玩家的上下文上，所以喂的是上帝视角的全量轨迹），
        记成一条 seat=None 的调用，中间栏在「系统全局状态」里看得到。
        """
        if not self.winner:
            return
        roles = {p.seat: p.role.value for p in self.players}
        track = "\n".join(
            f"[第{e.round}{'夜' if e.phase == 'night' else '天'}] {e.text}"
            for e in self.events if e.kind in ("night_action", "speech", "vote", "result")
        )[-6000:]                     # 只留末尾，长局也不至于把上下文撑爆
        instr, schema = prompts.mvp_instruction(self.winner, roles, track)
        t0 = time.time()
        raw, parsed, ms, model = await self.llm.complete(
            prompts.MVP_SYSTEM, [{"role": "user", "content": instr}], schema, "mvp",
            {"me": 0, "alive": self.alive, "candidates": sorted(roles)},
        )
        seat = self._coerce(parsed.get("seat"), sorted(roles), self.rng)
        reason = str(parsed.get("reason", "")).strip() or "这局的关键决策踩中了节奏。"
        self._emit("result", f"本局 MVP：{seat} 号（{roles[seat]}）。{reason}", mvp=seat)
        self._record(LLMCall(seat=None, title="系统全局状态 评选 MVP",
                             system_prompt=prompts.MVP_SYSTEM, delta=instr, output=raw,
                             parsed=parsed, ctx_chars=len(instr), latency_ms=ms or
                             int((time.time() - t0) * 1000), model=model,
                             round=self.round, phase=self.phase))

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
