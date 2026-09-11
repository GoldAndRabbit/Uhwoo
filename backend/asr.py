"""语音输入：阿里云百炼 qwen-audio-3.0-asr-flash-streaming，把你说的话转成文字。

- 鉴权：和 TTS 同一把 key（DASHSCOPE_API_KEY，没有就退回 ALIYUN_BAILIAN_API_KEY）
- 配置：config/llm_api.yaml 的 asr 段（model / sample_rate / 可选 websocket_url）
- SDK 的 Recognition.call() 只吃**文件路径**，所以前端传上来的音频先落到临时文件；
  它又是同步阻塞的，和 tts 一样用 asyncio.to_thread 包一层
- 前端录的是 16k 单声道 wav（浏览器原生 MediaRecorder 给的是 webm/opus，
  各家格式不一，不如自己在 AudioContext 里降采样编 wav，服务端就只认一种格式）
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .llm_api import CONFIG_PATH, load_dotenv
from .tts import api_key

logger = logging.getLogger(__name__)

FORMATS = {"wav", "mp3", "pcm", "opus", "speex", "aac", "amr"}


@dataclass(frozen=True)
class AsrConfig:
    enabled: bool = True
    model: str = "qwen-audio-3.0-asr-flash-streaming"
    sample_rate: int = 16000
    websocket_url: str | None = None      # 需要指定业务空间/地域时填


@lru_cache(maxsize=1)
def load_asr_config() -> AsrConfig:
    load_dotenv()
    raw: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            import yaml

            raw = (yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}).get("asr") or {}
        except Exception as exc:
            logger.warning("读取 asr 配置失败：%s", exc)
    return AsrConfig(
        enabled=bool(raw.get("enabled", True)),
        model=raw.get("model") or AsrConfig.model,
        sample_rate=int(raw.get("sample_rate", 16000)),
        websocket_url=raw.get("websocket_url"),
    )


def available() -> bool:
    if not (load_asr_config().enabled and api_key()):
        return False
    try:
        import dashscope  # noqa: F401
    except ImportError:
        return False
    return True


def _recognize_blocking(path: str, fmt: str, sample_rate: int) -> str:
    from http import HTTPStatus

    import dashscope
    from dashscope.audio.asr import Recognition

    cfg = load_asr_config()
    dashscope.api_key = api_key()
    if cfg.websocket_url:
        dashscope.base_websocket_api_url = cfg.websocket_url
    # 采样率必须和音频真实的对得上，对不上服务端直接判错（不会自己重采样）
    rec = Recognition(model=cfg.model, format=fmt, sample_rate=sample_rate, callback=None)
    result = rec.call(path)
    if result.status_code != HTTPStatus.OK:
        raise RuntimeError(f"ASR {result.status_code}: {result.message}")
    logger.info("ASR %s 首包 %sms 末包 %sms", cfg.model,
                rec.get_first_package_delay(), rec.get_last_package_delay())
    return _text_of(result.get_sentence())


def _text_of(sentence: Any) -> str:
    """句子结构在不同模型/版本间不一样：可能是 dict、也可能是一串 dict。"""
    if sentence is None:
        return ""
    if isinstance(sentence, dict):
        return str(sentence.get("text") or "").strip()
    if isinstance(sentence, (list, tuple)):
        return "".join(_text_of(s) for s in sentence).strip()
    return str(sentence).strip()


async def transcribe(audio: bytes, fmt: str = "wav", sample_rate: int | None = None) -> str:
    """认一段音频，返回识别出的文字（识别不出内容就是空串）。

    sample_rate 要和音频本身一致，默认取配置里的 16000 —— 前端就是按这个录的。
    """
    if not audio:
        raise ValueError("空音频")
    fmt = fmt.lower().lstrip(".")
    if fmt not in FORMATS:
        raise ValueError(f"不支持的音频格式 {fmt}（支持 {'/'.join(sorted(FORMATS))}）")
    tmp = Path(tempfile.mkdtemp(prefix="uhwoo-asr-")) / f"clip.{fmt}"
    try:
        tmp.write_bytes(audio)
        rate = int(sample_rate or load_asr_config().sample_rate)
        return await asyncio.to_thread(_recognize_blocking, str(tmp), fmt, rate)
    finally:
        tmp.unlink(missing_ok=True)
        tmp.parent.rmdir()


async def _main() -> None:
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("用法: python -m backend.asr <音频文件> [采样率]")
    p = Path(sys.argv[1])
    rate = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print(repr(await transcribe(p.read_bytes(), p.suffix, rate)))


if __name__ == "__main__":
    asyncio.run(_main())
