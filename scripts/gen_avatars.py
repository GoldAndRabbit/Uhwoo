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

# 摄影参数统一，只换人 —— 12 张摆在一起才像同一组照片而不是网图拼盘。
# 表情是硬要求：这是一桌准备互相骗的人，一个个绷着脸拍证件照就没气氛了，
# 所以每个人要么笑得好看、要么笑得让人心里发毛，不许面无表情。
STYLE = (
    "写实人像摄影，容貌出众，正面头肩特写，肩部以上，人物居中，头顶留白，直视镜头，"
    "表情生动，绝不面无表情、不冷峻、不严肃，"
    "柔和的影棚柔光，浅灰白色纯色背景，85mm 镜头浅景深，真实皮肤质感与毛孔细节，"
    "自然肤色，构图正方形，无文字无水印"
)

# (key, seed, 这个人长什么样)
PEOPLE: list[tuple[str, int, str]] = [
    ("f1", 2101, "非常漂亮的中国年轻女性，二十五岁，明星级的精致五官，齐肩黑色直发，通透的淡妆，"
                 "灿烂的笑容，眼睛弯成月牙，米白色针织衫"),
    ("f2", 2102, "非常漂亮的中国年轻女性，二十二岁，清纯甜美，高马尾，开心地笑着露出牙齿，"
                 "眼神明亮有神采，浅蓝色衬衫"),
    ("f3", 2103, "非常漂亮的中国女性，三十岁，明艳大气的长相，齐耳短发，嘴角挑起的狡黠坏笑，"
                 "眼神里带着一点算计和挑衅，黑色西装内搭白衬衫"),
    ("f4", 2104, "非常漂亮的中国年轻女性，二十七岁，温柔的鹅蛋脸，微卷长发披肩，"
                 "含笑的浅笑，眼神柔和，燕麦色高领毛衣"),
    ("f5", 2105, "非常漂亮的中国年轻女性，二十四岁，甜美可爱，空气刘海及腰长直发，"
                 "俏皮地眯眼笑，藕粉色卫衣"),
    ("f6", 2106, "非常漂亮的中国女性，三十二岁，成熟妩媚的长相，低盘发，"
                 "似笑非笑的邪魅笑容，单边嘴角上扬，深灰色西装"),
    ("m1", 2201, "非常帅气的中国年轻男性，二十六岁，硬朗清爽的五官，利落短发，"
                 "阳光爽朗的笑容，浅灰色圆领 T 恤"),
    ("m2", 2202, "非常帅气的中国年轻男性，二十三岁，清秀白净的少年感，蓬松碎盖发型，"
                 "腼腆又开心地笑，白色衬衫"),
    ("m3", 2203, "非常帅气的中国男性，三十五岁，成熟有魅力，背头，浅浅的胡茬，"
                 "意味深长的坏笑，眼神里藏着东西，藏青色西装白衬衫"),
    ("m4", 2204, "非常帅气的中国年轻男性，二十八岁，轮廓分明的硬汉长相，寸头，"
                 "咧嘴自信地笑，黑色夹克"),
    ("m5", 2205, "非常帅气的中国年轻男性，二十五岁，斯文清俊，细边框眼镜，"
                 "温和的笑容，眼睛带笑，浅蓝色牛津纺衬衫"),
    ("m6", 2206, "非常帅气的中国男性，三十岁，深邃立体的五官，中分微卷短发，"
                 "嘴角勾起的邪气笑容，眼神玩味，焦糖色针织衫"),
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
