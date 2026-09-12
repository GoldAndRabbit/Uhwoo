"""模型调用层：走 llm_api（OpenAI 兼容，阿里云百炼 / SiliconFlow）+ 本地 mock。

没配 key 时自动退回 mock 启发式大脑，UI 依然能完整跑完一局。
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from typing import Any

from . import llm_api

MAX_MOCK_DELAY = 0.85

# WEREWOLF_MOCK=1 强制走本地启发式大脑（演示 / 调 UI 时不烧 token）
FORCE_MOCK = os.environ.get("WEREWOLF_MOCK", "").lower() in ("1", "true", "yes")


def has_credentials() -> bool:
    return not FORCE_MOCK and llm_api.has_credentials()


def model_name() -> str:
    return llm_api.load_config().model


def _extract_json(text: str) -> dict[str, Any] | None:
    """模型可能带 ```json 围栏或前后废话，取第一个完整对象。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    return None


class LLMClient:
    """kind: guard / wolf / seer / speech / vote。mock 模式下用启发式规则造出合理的中文输出。"""

    def __init__(self, use_api: bool | None = None, seed: int | None = None,
                 model: str | None = None):
        self.use_api = has_credentials() if use_api is None else use_api
        cfg = llm_api.load_config()
        self.model_id = model or cfg.model
        self.model = f"{cfg.provider}/{self.model_id}" if self.use_api else "mock"
        self.rng = random.Random(seed)
        self._json_mode = True

    # ---------- 对外接口 ----------
    async def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        kind: str,
        mock_ctx: dict[str, Any],
    ) -> tuple[str, dict[str, Any], int, str]:
        t0 = time.time()
        if self.use_api:
            try:
                raw = await self._call_api(system, messages)
                parsed = _extract_json(raw)
                if parsed is None:                       # 拿不到 JSON 就按 mock 兜底，不让一局崩掉
                    parsed = self._mock(kind, mock_ctx)
                    parsed["_parse_error"] = raw[:200]
            except Exception as exc:
                raw = ""
                parsed = self._mock(kind, mock_ctx)
                parsed["_error"] = f"{type(exc).__name__}: {exc}"[:300]
            if not raw:
                raw = json.dumps(parsed, ensure_ascii=False, indent=2)
        else:
            await asyncio.sleep(0.35 + self.rng.random() * MAX_MOCK_DELAY)   # 模拟思考耗时
            parsed = self._mock(kind, mock_ctx)
            raw = json.dumps(parsed, ensure_ascii=False, indent=2)
        return raw, parsed, int((time.time() - t0) * 1000), self.model

    async def _call_api(self, system: str, messages: list[dict[str, Any]]) -> str:
        payload = [{"role": "system", "content": system}] + messages
        extra = {"response_format": {"type": "json_object"}} if self._json_mode else None
        try:
            return await llm_api.chat_complete(payload, model=self.model_id, extra_payload=extra)
        except RuntimeError as exc:
            if self._json_mode and "response_format" in str(exc):
                self._json_mode = False                   # 上游不支持就退回纯提示词约束
                return await llm_api.chat_complete(payload, model=self.model_id)
            raise

    # ---------- mock 大脑 ----------
    def _mock(self, kind: str, c: dict[str, Any]) -> dict[str, Any]:
        me: int = c["me"]
        alive: list[int] = c["alive"]
        others = [s for s in alive if s != me]
        sus: dict[int, float] = c.get("suspicion", {})           # 座位 -> 可疑度
        claimed_seer: int | None = c.get("claimed_seer")
        wolves: list[int] = c.get("wolves", [])
        checks: dict[int, str] = c.get("checks", {})             # 预言家的查验结果

        def pick(pool: list[int], score) -> int:
            if not pool:
                pool = others or alive
            return max(pool, key=lambda s: (score(s), self.rng.random()))

        if kind == "guard":
            pool = [s for s in alive if s != c.get("last_guard")]
            t = pick(pool, lambda s: (3.0 if s == claimed_seer else 0.0) + (1.2 if s == me else 0.0)
                     - sus.get(s, 0.0) * 0.5 + self.rng.random())
            return {"thinking": f"预言家可能在 {claimed_seer or '未知'}，今晚优先保关键位；不能连守，所以排除上一晚的目标。",
                    "target": t}

        if kind == "wolf":
            pool = [s for s in alive if s not in wolves] or others
            t = pick(pool, lambda s: (4.0 if s == claimed_seer else 0.0)
                     + (2.0 if s in c.get("threats", []) else 0.0) + self.rng.random())
            why = "他跳了预言家，先手撕掉他的信息" if t == claimed_seer else "他的发言最有威胁，逻辑带节奏"
            return {"thinking": f"好人里 {t} 号最有威胁，刀掉他白天我们更好带。",
                    "target": t, "reason": f"刀 {t} 号，{why}。"}

        if kind == "seer":
            pool = [s for s in alive if s not in checks and s != me] or others
            t = pick(pool, lambda s: sus.get(s, 0.0) + self.rng.random())
            return {"thinking": f"{t} 号今天的发言最模糊，先验他把水位定下来。", "target": t}

        if kind == "speech":
            role = c["role"]
            wolf_in_view = [s for s, r in checks.items() if r == "狼人" and s in alive]
            if role == "预言家":
                if wolf_in_view:
                    txt = (f"我跳预言家，昨晚验 {wolf_in_view[0]} 号是狼人，今天必须出 {wolf_in_view[0]} 号。"
                           f"守卫今晚可以考虑守我，我明天再报验人。")
                else:
                    good = [s for s, r in checks.items() if r == "好人"]
                    g = good[0] if good else others[0]
                    t = pick(others, lambda s: sus.get(s, 0.0) + self.rng.random())
                    txt = f"我跳预言家，昨晚验 {g} 号是好人，可以放心跟我的票。今天我倾向出 {t} 号。"
            elif role == "狼人":
                t = pick([s for s in others if s not in wolves] or others,
                         lambda s: (2.0 if s == claimed_seer else 0.0) + self.rng.random())
                txt = (f"首置位没太多信息，我先不跳身份，偏闭眼人。后面重点听 {t} 号会不会借平安夜强抬自己，"
                       f"有对跳我再盘验人和发言，先不站边。")
            else:
                t = pick(others, lambda s: sus.get(s, 0.0) + self.rng.random())
                txt = (f"我不跳身份。目前 {claimed_seer or '还没有人'} 跳预言家，"
                       f"我今天倾向出 {t} 号，他的发言站边太模糊，重点听他怎么回应。")
            return {"thinking": f"我是{role}，这轮先把水位说清楚，给出明确票型。", "speech": txt}

        if kind == "vote":
            if c["role"] == "狼人":
                pool = [s for s in others if s not in wolves] or others
                t = pick(pool, lambda s: (3.0 if s == claimed_seer else 0.0) + self.rng.random())
            else:
                t = pick(others, lambda s: sus.get(s, 0.0) + self.rng.random())
            return {"thinking": f"综合今天的发言，{t} 号最像狼。", "target": t}

        if kind == "witch":
            # 没有 key 时的兜底：有解药就救（除非刀的是自己），否则留着毒药
            killed = c.get("killed")
            if c.get("has_cure") and killed and killed != me:
                return {"thinking": "先把人救下来，毒药留着。", "action": "save", "target": 0}
            if c.get("has_poison") and sus:
                t = pick(others, lambda s: sus.get(s, 0.0) + self.rng.random())
                return {"thinking": f"{t} 号最可疑，毒他。", "action": "poison", "target": t}
            return {"thinking": "今晚先不用药。", "action": "pass", "target": 0}

        if kind == "hunter":
            t = pick(others, lambda s: sus.get(s, 0.0) + self.rng.random()) if others else 0
            return {"thinking": f"带走 {t} 号。", "target": t}

        if kind == "mvp":
            # 没有 key 时的兜底：挑赢家阵营里还活着的人，理由按身份套一句
            pool = c.get("candidates") or alive or [me]
            t = pool[0] if len(pool) == 1 else self.rng.choice(pool)
            return {"seat": t, "reason": f"{t} 号这局的关键决策踩中了节奏。"}

        return {"thinking": "", "target": others[0] if others else me}
