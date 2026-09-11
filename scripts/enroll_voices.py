"""把 frontend/ref_audio/*.wav 注册成克隆音色，打印出可以直接贴进 config 的 voice id。

    .venv/bin/python -m scripts.enroll_voices                      # 全部
    .venv/bin/python -m scripts.enroll_voices wu_gang dee_hsu      # 只注册这几个

注册是**一次性**的：拿到 voice id 之后就和预置音色一样用，不必再传参考音频。
参考音频要能从公网取到（DashScope 去拉），所以走线上的 https://uhwoo.com/static/ref_audio/。
换模型就得重新注册一遍 —— voice id 是绑模型的（id 里就带着模型名）。

实测各档的延迟（同一句 23 字，首包 / 整句）：
    qwen-audio-3.0-tts-flash  预置单音色   353ms / 0.74s
    cosyvoice-v1              20 个预置    606ms / 1.17s
    cosyvoice-v3.5-flash      克隆         790ms / 1.65s
    cosyvoice-v2              克隆        1200ms / 2.40s
"""
from __future__ import annotations

import sys
import time

import dashscope
from dashscope.audio.tts_v2 import VoiceEnrollmentService

from backend.image_api import ROOT
from backend.tts import api_key

MODEL = "cosyvoice-v3.5-flash"
PREFIX = "uhwoo"
BASE_URL = "https://uhwoo.com/static/ref_audio"
REF_DIR = ROOT / "frontend" / "ref_audio"


def enroll(name: str) -> str:
    svc = VoiceEnrollmentService()
    vid = svc.create_voice(target_model=MODEL, prefix=PREFIX, url=f"{BASE_URL}/{name}.wav")
    for _ in range(30):
        status = svc.query_voice(voice_id=vid).get("status")
        if status == "OK":
            return vid
        if status == "UNDEPLOYED":
            raise RuntimeError(f"{name} 注册失败：{status}")
        time.sleep(5)
    raise RuntimeError(f"{name} 等了 150s 还没部署好")


def main() -> None:
    dashscope.api_key = api_key()
    dashscope.base_websocket_api_url = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"
    want = sys.argv[1:]
    names = want or sorted(p.stem for p in REF_DIR.glob("*.wav"))
    for name in names:
        t = time.perf_counter()
        try:
            print(f"{name:<16} {enroll(name)}   ({time.perf_counter() - t:.0f}s)", flush=True)
        except Exception as exc:
            print(f"{name:<16} ✗ {exc}", flush=True)


if __name__ == "__main__":
    main()
