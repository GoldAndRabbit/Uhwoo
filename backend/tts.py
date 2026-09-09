"""语音合成：阿里云百炼 qwen-audio-3.0-tts-flash，把白天的发言念出来。

- 鉴权：DASHSCOPE_API_KEY，没有就退回 ALIYUN_BAILIAN_API_KEY（同一把百炼 key）
- 配置：config/llm_api.yaml 的 tts 段（model / voice / 每个座位的音高语速）
- 合成结果按 sha1(model|voice|seat|text) 落盘 data/tts/，重复播放不再花钱
- SDK 是同步的，统一用 asyncio.to_thread 包一层，别卡住事件循环
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .llm_api import CONFIG_PATH, ROOT, load_dotenv

logger = logging.getLogger(__name__)
CACHE_DIR = ROOT / "data" / "tts"

# 只有 longanhuan_v3.6 一个音色可用，所以用音高/语速把 6 个座位区分开
DEFAULT_SEAT_STYLES: dict[int, dict[str, float]] = {
    0: {"pitch_rate": 0.90, "speech_rate": 0.92},      # 0 号 = 法官（上帝）：低沉、慢一点
    1: {"pitch_rate": 1.00, "speech_rate": 1.05},
    2: {"pitch_rate": 0.88, "speech_rate": 1.00},
    3: {"pitch_rate": 1.12, "speech_rate": 1.10},
    4: {"pitch_rate": 0.94, "speech_rate": 1.15},
    5: {"pitch_rate": 1.06, "speech_rate": 0.98},
    6: {"pitch_rate": 0.82, "speech_rate": 1.08},
}


@dataclass(frozen=True)
class TtsConfig:
    enabled: bool = True
    model: str = "qwen-audio-3.0-tts-flash"
    voice: str = "longanhuan_v3.6"
    websocket_url: str | None = None       # 需要指定业务空间/地域时填
    seats: dict[int, dict[str, float]] = field(default_factory=lambda: DEFAULT_SEAT_STYLES)


@lru_cache(maxsize=1)
def load_tts_config() -> TtsConfig:
    load_dotenv()
    raw: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            import yaml

            raw = (yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}).get("tts") or {}
        except Exception as exc:
            logger.warning("读取 tts 配置失败：%s", exc)
    seats = dict(DEFAULT_SEAT_STYLES)
    for k, v in (raw.get("seats") or {}).items():
        if isinstance(v, dict):
            seats[int(k)] = {"pitch_rate": float(v.get("pitch_rate", 1.0)),
                             "speech_rate": float(v.get("speech_rate", 1.0))}
    return TtsConfig(
        enabled=bool(raw.get("enabled", True)),
        model=raw.get("model") or "qwen-audio-3.0-tts-flash",
        voice=raw.get("voice") or "longanhuan_v3.6",
        websocket_url=raw.get("websocket_url"),
        seats=seats,
    )


def api_key() -> str:
    load_dotenv()
    return (os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("ALIYUN_BAILIAN_API_KEY") or "").strip()


def available() -> bool:
    if not (load_tts_config().enabled and api_key()):
        return False
    try:
        import dashscope  # noqa: F401
    except ImportError:
        return False
    return True


def _cache_path(cfg: TtsConfig, seat: int | None, text: str) -> Path:
    key = f"{cfg.model}|{cfg.voice}|{seat}|{text}".encode("utf-8")
    return CACHE_DIR / f"{hashlib.sha1(key).hexdigest()}.mp3"


def _synth_blocking(cfg: TtsConfig, seat: int | None, text: str) -> bytes:
    import dashscope
    from dashscope.audio.tts_v2 import SpeechSynthesizer

    dashscope.api_key = api_key()
    if cfg.websocket_url:
        dashscope.base_websocket_api_url = cfg.websocket_url
    style = cfg.seats.get(seat or 0, {})
    syn = SpeechSynthesizer(model=cfg.model, voice=cfg.voice,
                            pitch_rate=style.get("pitch_rate", 1.0),
                            speech_rate=style.get("speech_rate", 1.0))
    audio = syn.call(text)
    if not audio:
        raise RuntimeError("TTS 返回空音频")
    logger.info("TTS %s 字，首包 %.0fms，request_id=%s",
                len(text), syn.get_first_package_delay() or 0, syn.get_last_request_id())
    return bytes(audio)


_locks: dict[str, asyncio.Lock] = {}


async def synthesize(text: str, seat: int | None = None) -> bytes:
    """合成一句话，命中磁盘缓存就直接返回。同一句并发请求只合成一次。"""
    cfg = load_tts_config()
    text = text.strip()
    if not text:
        raise ValueError("空文本")
    path = _cache_path(cfg, seat, text)
    if path.exists():
        return path.read_bytes()
    lock = _locks.setdefault(path.name, asyncio.Lock())
    async with lock:
        if path.exists():
            return path.read_bytes()
        audio = await asyncio.to_thread(_synth_blocking, cfg, seat, text)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        return audio
