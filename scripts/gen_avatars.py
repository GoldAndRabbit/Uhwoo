"""生成 12 张座位头像（方舟 Seedream），6 女 6 男，写实人像。

    .venv/bin/python -m scripts.gen_avatars            # 缺哪张补哪张
    .venv/bin/python -m scripts.gen_avatars f1 m3      # 只重画这几张（先删原图）

原图 2144×2144 落在 assets/avatars-original/（和角色图一样不进仓库），
frontend/img/avatars/ 下留 320px 的版本，前端用的是后者。
seed 固定：同一个人重跑不会变脸，image_api 的磁盘缓存也能命中。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from backend.image_api import ROOT, generate_png, resize_png

ORIGINAL = ROOT / "assets" / "avatars-original"
PUBLIC = ROOT / "frontend" / "img" / "avatars"
EDGE = 320

# 摄影参数统一，只换人 —— 12 张摆在一起才像同一组照片而不是网图拼盘
STYLE = (
    "写实人像摄影，正面头肩特写，肩部以上，人物居中，头顶留白，直视镜头，"
    "柔和的影棚柔光，浅灰白色纯色背景，85mm 镜头浅景深，真实皮肤质感与毛孔细节，"
    "自然肤色，构图正方形，无文字无水印"
)

# (key, seed, 这个人长什么样)
PEOPLE: list[tuple[str, int, str]] = [
    ("f1", 1101, "中国年轻女性，二十五岁，齐肩黑色直发，干净通透的淡妆，嘴角微微上扬，米白色针织衫"),
    ("f2", 1102, "中国年轻女性，二十二岁，高马尾，圆润清秀的脸，眼神明亮带笑意，浅蓝色衬衫"),
    ("f3", 1103, "中国女性，三十岁，齐耳短发，冷静克制的表情，轮廓分明，黑色西装外套内搭白衬衫"),
    ("f4", 1104, "中国年轻女性，二十七岁，微卷长发披肩，柔和的鹅蛋脸，安静的浅笑，燕麦色高领毛衣"),
    ("f5", 1105, "中国年轻女性，二十四岁，空气刘海及腰长直发，圆眼睛，俏皮的神情，藕粉色卫衣"),
    ("f6", 1106, "中国女性，三十二岁，低盘发，干练利落，眉眼沉稳，深灰色西装"),
    ("m1", 1201, "中国年轻男性，二十六岁，利落短发，干净的下颌线，温和的微笑，浅灰色圆领 T 恤"),
    ("m2", 1202, "中国年轻男性，二十三岁，蓬松碎盖发型，清秀白净，略带腼腆，白色衬衫"),
    ("m3", 1203, "中国男性，三十五岁，背头，浅浅的胡茬，沉稳锐利的眼神，藏青色西装白衬衫"),
    ("m4", 1204, "中国年轻男性，二十八岁，寸头，轮廓硬朗，表情松弛自信，黑色夹克"),
    ("m5", 1205, "中国年轻男性，二十五岁，细边框眼镜，斯文书卷气，浅笑，浅蓝色牛津纺衬衫"),
    ("m6", 1206, "中国男性，三十岁，中分微卷短发，深邃的眼睛，不苟言笑，焦糖色针织衫"),
]


async def one(key: str, seed: int, who: str) -> str:
    raw = ORIGINAL / f"{key}.png"
    if raw.exists():
        png = raw.read_bytes()
        note = "已有"
    else:
        png = await generate_png(f"{who}。{STYLE}", kind="role", seed=seed)
        ORIGINAL.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(png)
        note = f"新出 {len(png) // 1024}KB"
    small = resize_png(png, EDGE)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / f"{key}.png").write_bytes(small)
    return f"✓ {key}  {note} → {EDGE}px {len(small) // 1024}KB"


async def main() -> None:
    want = set(sys.argv[1:])
    todo = [p for p in PEOPLE if not want or p[0] in want]
    if not todo:
        raise SystemExit(f"没有这几个：{' '.join(sorted(want))}；可选 {' '.join(k for k, _, _ in PEOPLE)}")
    for line in await asyncio.gather(*(one(*p) for p in todo)):
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
