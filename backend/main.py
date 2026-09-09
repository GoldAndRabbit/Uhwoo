"""FastAPI 服务：开局 / 停止 / SSE 事件流 / 历史对局。前端静态文件在 ../frontend。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .game import DATA_DIR, Game
from .llm import has_credentials, model_name
from .llm_api import load_config

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app = FastAPI(title="Multi-agent-werewolf")

_game: Game | None = None
_task: asyncio.Task | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/config")
async def config() -> dict[str, Any]:
    cfg = load_config()
    ok = has_credentials()
    return {
        "use_api": ok,
        "provider": cfg.provider,
        "model": f"{cfg.provider} / {cfg.model}" if ok
                 else "mock（未检测到 ALIYUN_BAILIAN_API_KEY，用本地启发式规则演示）",
    }


@app.post("/api/start")
async def start() -> dict[str, Any]:
    global _game, _task
    if _game and _game.status == "running":
        raise HTTPException(409, "已有对局在进行中")
    gid = time.strftime("%Y%m%d%H%M%S")
    _game = Game(gid)
    _task = asyncio.create_task(_game.run())
    return {"gid": gid, "state": _game.state()}


@app.post("/api/stop")
async def stop() -> dict[str, Any]:
    if not _game:
        raise HTTPException(404, "还没有对局")
    _game.stop()
    return {"ok": True, "state": _game.state()}


@app.get("/api/snapshot")
async def snapshot() -> dict[str, Any]:
    if not _game:
        return {"empty": True}
    return _game.to_json()


@app.get("/api/history")
async def history() -> list[dict[str, Any]]:
    DATA_DIR.mkdir(exist_ok=True)
    out = []
    for f in sorted(DATA_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        st = d.get("state", {})
        out.append({"gid": d.get("gid", f.stem), "winner": st.get("winner"),
                    "status": st.get("status"), "calls": st.get("calls", 0),
                    "elapsed": st.get("elapsed", 0)})
    return out[:30]


@app.get("/api/history/{gid}")
async def history_one(gid: str) -> dict[str, Any]:
    f = DATA_DIR / f"{gid}.json"
    if not f.exists():
        raise HTTPException(404, "没有这局记录")
    return json.loads(f.read_text(encoding="utf-8"))


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    async def gen():
        if not _game:
            yield "data: " + json.dumps({"type": "idle"}) + "\n\n"
            return
        game = _game
        # 先补发已经发生的一切，保证刷新页面不丢历史
        yield "data: " + json.dumps({"type": "snapshot", "data": game.to_json()},
                                    ensure_ascii=False) + "\n\n"
        q = game.subscribe()
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield "data: " + json.dumps(msg, ensure_ascii=False) + "\n\n"
                if msg.get("type") == "state" and msg["state"]["status"] in ("finished", "stopped"):
                    break
        finally:
            game.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
