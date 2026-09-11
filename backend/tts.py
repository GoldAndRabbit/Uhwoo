"""语音合成：阿里云百炼 qwen-audio-3.0-tts-flash，把白天的发言念出来。

- 鉴权：DASHSCOPE_API_KEY，没有就退回 ALIYUN_BAILIAN_API_KEY（同一把百炼 key）
- 配置：config/llm_api.yaml 的 tts 段（model / voice / 每个座位的音高语速）
- 念之前先把阿拉伯数字改写成中文数字：「第2夜」交给模型会念成「第两夜」
- 合成结果按 sha1(model|voice|seat|text) 落盘 data/tts/，重复播放不再花钱
- SDK 是同步的，统一用 asyncio.to_thread 包一层，别卡住事件循环
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
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


# 数字读法：模型把「第2夜」念成「第两夜」、「2号」念成「两号」，都不对。
# 送进去之前自己把阿拉伯数字换成中文，一律用「二」不用「两」。
_CN_DIGIT = "零一二三四五六七八九"


def _cn_number(n: int) -> str:
    """0–99 的常规读法：10→十、11→十一、20→二十、25→二十五。"""
    if n < 10:
        return _CN_DIGIT[n]
    if n < 20:
        return "十" + (_CN_DIGIT[n % 10] if n % 10 else "")
    return _CN_DIGIT[n // 10] + "十" + (_CN_DIGIT[n % 10] if n % 10 else "")


def normalize_digits(text: str) -> str:
    """把文本里的阿拉伯数字换成中文读法。

    1–2 位按数值读（2→二、15→十五），3 位以上逐位念（房间号 40317 → 四零三一七，
    读成「四万零三百一十七」就没人听得懂了）。
    """
    out: list[str] = []
    for chunk in re.split(r"(\d+)", text):
        if not chunk.isdigit():
            out.append(chunk)
        elif len(chunk) <= 2 and not chunk.startswith("0"):
            out.append(_cn_number(int(chunk)))
        else:
            out.append("".join(_CN_DIGIT[int(c)] for c in chunk))
    return "".join(out)


@dataclass(frozen=True)
class TtsConfig:
    enabled: bool = True
    model: str = "qwen-audio-3.0-tts-flash"
    voice: str = "longanhuan_v3.6"
    websocket_url: str | None = None       # 需要指定业务空间/地域时填
    seats: dict[int, dict[str, float]] = field(default_factory=lambda: DEFAULT_SEAT_STYLES)
    # 音色克隆：一人一把真嗓子，慢一档但辨识度高
    clone_default_on: bool = True
    clone_model: str = "cosyvoice-v2"
    clone_voices: dict[int, str] = field(default_factory=dict)

    def pick(self, seat: int | None, clone: bool) -> tuple[str, str, dict[str, float]]:
        """选模型和音色：克隆音色自带音高语速，不再叠加 seats 里的微调。"""
        vid = self.clone_voices.get(seat if seat is not None else 0)
        if clone and vid:
            return self.clone_model, vid, {}
        return self.model, self.voice, self.seats.get(seat or 0, {})


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
    clone = raw.get("clone") or {}
    voices = {int(k): str(v) for k, v in (clone.get("voices") or {}).items()}
    return TtsConfig(
        enabled=bool(raw.get("enabled", True)),
        model=raw.get("model") or "qwen-audio-3.0-tts-flash",
        voice=raw.get("voice") or "longanhuan_v3.6",
        websocket_url=raw.get("websocket_url"),
        seats=seats,
        clone_default_on=bool(clone.get("default_on", True)) and bool(voices),
        clone_model=clone.get("model") or "cosyvoice-v2",
        clone_voices=voices,
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


def _cache_path(model: str, voice: str, seat: int | None, text: str) -> Path:
    key = f"{model}|{voice}|{seat}|{text}".encode("utf-8")
    return CACHE_DIR / f"{hashlib.sha1(key).hexdigest()}.mp3"


def _synth_blocking(model: str, voice: str, style: dict[str, float], text: str) -> bytes:
    import dashscope
    from dashscope.audio.tts_v2 import SpeechSynthesizer

    cfg = load_tts_config()
    dashscope.api_key = api_key()
    if cfg.websocket_url:
        dashscope.base_websocket_api_url = cfg.websocket_url
    last: Exception | None = None
    for attempt in range(3):                 # websocket 偶发 5s 内连不上，重试一次基本就好
        try:
            syn = SpeechSynthesizer(model=model, voice=voice,
                                    pitch_rate=style.get("pitch_rate", 1.0),
                                    speech_rate=style.get("speech_rate", 1.0))
            audio = syn.call(text)
            if not audio:
                raise RuntimeError("TTS 返回空音频")
            logger.info("TTS %s/%s %s 字，首包 %.0fms", model, voice, len(text),
                        syn.get_first_package_delay() or 0)
            return bytes(audio)
        except Exception as exc:
            last = exc
            time.sleep(0.8 * (attempt + 1))
    raise last if last else RuntimeError("TTS 失败")


_locks: dict[str, asyncio.Lock] = {}


async def synthesize(text: str, seat: int | None = None, clone: bool | None = None) -> bytes:
    """合成一句话，命中磁盘缓存就直接返回。同一句并发请求只合成一次。"""
    cfg = load_tts_config()
    text = normalize_digits(text.strip())
    if not text:
        raise ValueError("空文本")
    if clone is None:
        clone = cfg.clone_default_on
    model, voice, style = cfg.pick(seat, clone)
    path = _cache_path(model, voice, seat, text)
    if path.exists():
        return path.read_bytes()
    lock = _locks.setdefault(path.name, asyncio.Lock())
    async with lock:
        if path.exists():
            return path.read_bytes()
        audio = await asyncio.to_thread(_synth_blocking, model, voice, style, text)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        return audio
