"""语音合成：阿里云百炼 qwen-audio-3.0-tts-flash，把白天的发言念出来。

- 鉴权：DASHSCOPE_API_KEY，没有就退回 ALIYUN_BAILIAN_API_KEY（同一把百炼 key）
- 配置：config/llm_api.yaml 的 tts 段（model / voice / 按性别分的音色池）
- 谁用哪把嗓子跟着头像的性别走：Game 开局时给每个座位发一个槽位（f0–f2 / m0–m2），
  朗读时原样传回来，同性别的三个人各拿一把，不重样（见 pick）
- 念之前先把阿拉伯数字改写成中文数字：「第2夜」交给模型会念成「第两夜」
- 再过一道 humanize：加停顿、偶尔来个语气词，别一句话从头平到尾像播报
- 合成结果按 sha1(model|voice|seat|text) 落盘 data/tts/，重复播放不再花钱
- SDK 是同步的，统一用 asyncio.to_thread 包一层，别卡住事件循环
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .llm_api import CONFIG_PATH, ROOT, load_dotenv

logger = logging.getLogger(__name__)
CACHE_DIR = ROOT / "data" / "tts"



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


# 口语化：一句话从头平到尾就是「AI 味」的一半来源。这里在送去合成之前加两样东西：
#   1. 停顿 —— 用 SSML 的 <break>，实测这档模型认（回听过，标签不会被念出来），
#      而且它不产生任何文字，所以前端逐字浮出的字幕跟原文还是一一对应的
#   2. 语气词 —— 少量、只放句首，让开口没那么齐整；法官不加，报幕要稳
# 随机数按文本内容播种：同一句每次加工出来都一样，免得缓存里存的和下次听到的对不上。
FILLERS = ("嗯，", "那个，", "诶，", "我说啊，", "这么说吧，")


def _pause(ms: int) -> str:
    return f'<break time="{ms}ms"/>'


def humanize(text: str, *, judge: bool = False) -> str:
    """给一句话加上停顿和语气词，返回 SSML。"""
    rng = random.Random(hashlib.sha1(text.encode("utf-8")).hexdigest())
    out: list[str] = []

    if not judge and len(text) > 12 and rng.random() < 0.35:
        out.append(rng.choice(FILLERS))

    # 按标点切句，逐段加停顿：逗号一档短的，句末一档长的
    parts = re.findall(r"[^，。！？；：、]+[，。！？；：、]?", text)
    for i, part in enumerate(parts):
        out.append(part)
        if i == len(parts) - 1:
            break
        tail = part[-1:]
        if tail in "。！？":
            out.append(_pause(rng.randint(260, 420)))
        elif tail in "，；：":
            if rng.random() < 0.55:
                out.append(_pause(rng.randint(140, 260)))
        elif len(part) >= 18:                 # 一长串没标点的，中间也得换口气
            out.append(_pause(rng.randint(120, 200)))
    return "<speak>" + "".join(out) + "</speak>"


@dataclass(frozen=True)
class TtsConfig:
    enabled: bool = True
    model: str = "qwen-audio-3.0-tts-flash"
    voice: str = "longanhuan_v3.6"
    websocket_url: str | None = None       # 需要指定业务空间/地域时填
    # 音色克隆：一人一把真嗓子，慢一档但辨识度高
    clone_default_on: bool = False
    clone_model: str = "cosyvoice-v2"
    clone_judge: str = ""
    clone_pool: dict[str, list[str]] = field(default_factory=dict)

    def pick(self, voice_key: str | None, clone: bool) -> tuple[str, str]:
        """选模型和音色。

        voice_key 是 Game 开局时发下来的槽位：法官没有槽位（None），玩家是 f0–f2 / m0–m2
        —— 性别跟着头像走，序号保证同性别的三个人不撞嗓子。只有克隆那档分得开：
        预置那档整个模型就一把嗓子（pitch/speech 参数它也不认，试过了）。
        """
        g, idx = "", 0
        key = (voice_key or "").strip()
        if len(key) >= 2 and key[0] in "fm" and key[1:].isdigit():
            g, idx = key[0], int(key[1:])
        if clone:
            pool = (self.clone_pool.get(g) or []) if g else ([self.clone_judge] if self.clone_judge else [])
            if pool:
                return self.clone_model, pool[idx % len(pool)]
        return self.model, self.voice


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

    clone = raw.get("clone") or {}
    pool = {g: [str(v) for v in (clone.get(g) or [])] for g in ("f", "m")}
    return TtsConfig(
        enabled=bool(raw.get("enabled", True)),
        model=raw.get("model") or "qwen-audio-3.0-tts-flash",
        voice=raw.get("voice") or "longanhuan_v3.6",
        websocket_url=raw.get("websocket_url"),
        clone_default_on=bool(clone.get("default_on", False)) and bool(pool["f"] or pool["m"]),
        clone_model=clone.get("model") or "cosyvoice-v2",
        clone_judge=str(clone.get("judge") or ""),
        clone_pool=pool,
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


def _cache_path(model: str, voice: str, text: str) -> Path:
    key = f"{model}|{voice}|{text}".encode("utf-8")
    return CACHE_DIR / f"{hashlib.sha1(key).hexdigest()}.mp3"


def _synth_blocking(model: str, voice: str, text: str) -> bytes:
    import dashscope
    from dashscope.audio.tts_v2 import SpeechSynthesizer

    cfg = load_tts_config()
    dashscope.api_key = api_key()
    if cfg.websocket_url:
        dashscope.base_websocket_api_url = cfg.websocket_url
    last: Exception | None = None
    for attempt in range(3):                 # websocket 偶发 5s 内连不上，重试一次基本就好
        try:
            syn = SpeechSynthesizer(model=model, voice=voice)
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


async def synthesize(text: str, voice_key: str | None = None,
                     clone: bool | None = None) -> bytes:
    """合成一句话，命中磁盘缓存就直接返回。同一句并发请求只合成一次。

    voice_key 是座位的音色槽位（f0–f2 / m0–m2，法官不传）；缺省就用法官那把。
    """
    cfg = load_tts_config()
    text = normalize_digits(text.strip())
    if not text:
        raise ValueError("空文本")
    if clone is None:
        clone = cfg.clone_default_on
    model, voice = cfg.pick(voice_key, clone)
    path = _cache_path(model, voice, text)
    if path.exists():
        return path.read_bytes()
    lock = _locks.setdefault(path.name, asyncio.Lock())
    async with lock:
        if path.exists():
            return path.read_bytes()
        spoken = humanize(text, judge=not voice_key)
        audio = await asyncio.to_thread(_synth_blocking, model, voice, spoken)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        return audio
