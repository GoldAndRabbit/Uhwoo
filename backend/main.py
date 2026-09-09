"""FastAPI 服务：开局 / 停止 / SSE 事件流 / 历史对局。前端静态文件在 ../frontend。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import tts
from .game import GAMES_DIR, Game
from .model import Role
from .llm import has_credentials
from .llm_api import load_config

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app = FastAPI(title="Multi-agent-werewolf")


@app.middleware("http")
async def force_https(request: Request, call_next):
    """隧道进来的请求带 x-forwarded-proto。走 http 的话浏览器地址栏会挂「不安全」小三角，
    这里直接 301 到 https。本机 127.0.0.1 调试不受影响。"""
    if request.headers.get("x-forwarded-proto") == "http":
        return RedirectResponse(str(request.url).replace("http://", "https://", 1), status_code=301)
    return await call_next(request)

_game: Game | None = None
_task: asyncio.Task | None = None


@app.get("/")
async def index() -> Response:
    """首页不缓存，静态资源带上按 mtime 生成的版本号 —— 否则改了前端，
    Cloudflare 边缘和浏览器还在发旧的 app.js。"""
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for name in ("style.css", "app.js"):
        ver = int((FRONTEND / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={ver}")
    return Response(html, media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/config")
async def config() -> dict[str, Any]:
    cfg = load_config()
    ok = has_credentials()
    return {
        "use_api": ok,
        "provider": cfg.provider,
        "model": cfg.model,
        "models": list(cfg.models),
        "note": f"{cfg.provider}" if ok else "mock（未检测到 ALIYUN_BAILIAN_API_KEY）",
        "tts": tts.available(),
        "clone_default": tts.load_tts_config().clone_default_on,
    }


@app.post("/api/start")
async def start(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    global _game, _task
    if _game and _game.status == "running":
        raise HTTPException(409, "已有对局在进行中")
    cfg = load_config()
    model = body.get("model") or cfg.model
    if model not in cfg.models:
        raise HTTPException(400, f"未知模型 {model}")
    mode = body.get("mode") or "sim"
    if mode not in ("sim", "play"):
        raise HTTPException(400, f"未知模式 {mode}")
    gid = time.strftime("%Y%m%d%H%M%S")
    role = body.get("role") or None
    if role and role not in [r.value for r in Role]:
        raise HTTPException(400, f"未知身份 {role}")
    _game = Game(gid, model=model, tts=bool(body.get("tts")), mode=mode,
                 human_seat=body.get("seat"), human_role=role, clone=body.get("clone"))
    _task = asyncio.create_task(_game.run())
    return {"gid": gid, "state": _game.state()}


@app.post("/api/answer")
async def answer(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """单人模式下把你的选择/发言交上去。"""
    if not _game or _game.status != "running":
        raise HTTPException(409, "没有进行中的对局")
    if not _game.pending:
        raise HTTPException(409, "现在没轮到你")
    field = _game.pending["field"]
    if field == "target":
        try:
            payload: dict[str, Any] = {"target": int(body.get("target"))}
        except (TypeError, ValueError):
            raise HTTPException(400, "target 要是座位号") from None
        options = _game.pending.get("options") or []
        if options and payload["target"] not in options:
            raise HTTPException(400, f"只能选 {options}")
        if _game.pending["kind"] == "wolf":       # 只有狼队夜里商议要给队友一句话
            payload["reason"] = str(body.get("reason", "")).strip()[:200]
    else:
        text = str(body.get("speech", "")).strip()
        if not text:
            raise HTTPException(400, "发言不能为空")
        payload = {"speech": text[:500]}
    if not _game.answer(payload):
        raise HTTPException(409, "这一步已经过去了")
    return {"ok": True}


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
    return _game.to_json(filtered=True)


@app.get("/api/history")
async def history() -> list[dict[str, Any]]:
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(GAMES_DIR.glob("*.json"), reverse=True):
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
    f = GAMES_DIR / f"{gid}.json"
    if not f.exists():
        raise HTTPException(404, "没有这局记录")
    return json.loads(f.read_text(encoding="utf-8"))


@app.post("/api/tts")
async def speak(body: dict[str, Any] = Body(...)) -> Response:
    """把一句发言合成成 mp3。结果按文本落盘缓存，重复播放不再花钱。"""
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "缺少 text")
    if not tts.available():
        raise HTTPException(503, "TTS 未启用（缺 DASHSCOPE_API_KEY / ALIYUN_BAILIAN_API_KEY 或未装 dashscope）")
    seat = body.get("seat")
    try:
        clone = body.get("clone")
        audio = await tts.synthesize(text[:400], int(seat) if seat is not None else None,
                                     clone=None if clone is None else bool(clone))
    except Exception as exc:
        raise HTTPException(502, f"合成失败：{type(exc).__name__}: {exc}") from exc
    return Response(audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    async def gen():
        if not _game:
            yield "data: " + json.dumps({"type": "idle"}) + "\n\n"
            return
        game = _game
        # 先补发已经发生的一切，保证刷新页面不丢历史
        yield "data: " + json.dumps({"type": "snapshot", "data": game.to_json(filtered=True)},
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
