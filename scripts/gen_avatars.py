"""生成 12 张座位头像（方舟 Seedream），6 女 6 男，写实人像。

    .venv/bin/python -m scripts.gen_avatars            # 缺哪张补哪张
    .venv/bin/python -m scripts.gen_avatars f1 m3      # 只重画这几张（先删原图）

原图 2144×2144 落在 assets/avatars-original/（和角色图一样不进仓库），
frontend/img/avatars/ 下留 320px、**背景漂成纯白**的版本，前端用的是后者。
所以改了漂白参数不用重新花钱生图，直接重跑这个脚本就行（原图都在）。
seed 固定：同一个人重跑不会变脸，image_api 的磁盘缓存也能命中。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from backend.image_api import ROOT, generate_png, resize_png, whiten_bg

ORIGINAL = ROOT / "assets" / "avatars-original"
PUBLIC = ROOT / "frontend" / "img" / "avatars"
EDGE = 320

# 摄影参数统一，只换人 —— 12 张摆在一起才像同一组照片而不是网图拼盘。
# 表情是硬要求：一个个绷着脸拍证件照太没气氛，所有人都得在笑。
# （试过给几个人写「邪魅的笑 / 意味深长的坏笑」当狼的暗示，出来的脸是真难看，
#  模型一收到「邪」就往凶相上画，全部改回好看的笑。）
STYLE = (
    "写实人像摄影，容貌出众，正面头肩特写，肩部以上，人物居中，头顶留白，直视镜头，"
    "表情生动，绝不面无表情、不冷峻、不严肃，"
    "男性一律年轻、瘦削的脸型、下颌线清晰，不要宽脸方脸、不要中年人，"
    "柔和的影棚柔光，浅灰白色纯色背景，85mm 镜头浅景深，真实皮肤质感与毛孔细节，"
    "自然肤色，构图正方形，无文字无水印"
)

# (key, seed, 这个人长什么样)
PEOPLE: list[tuple[str, int, str]] = [
    ("f1", 2101, "非常漂亮的中国年轻女性，二十五岁，明星级的精致五官，齐肩黑色直发，通透的淡妆，"
                 "灿烂的笑容，眼睛弯成月牙，米白色针织衫"),
    ("f2", 2102, "非常漂亮的中国年轻女性，二十二岁，清纯甜美，高马尾，开心地笑着露出牙齿，"
                 "眼神明亮有神采，浅蓝色衬衫"),
    ("f3", 2703, "非常漂亮的中国年轻女性，二十八岁，明艳动人的长相，皮肤白皙紧致，"
                 "精致的齐耳短发，开朗的微笑，眼神温暖有神，黑色西装内搭白衬衫"),
    ("f4", 2104, "非常漂亮的中国年轻女性，二十七岁，温柔的鹅蛋脸，微卷长发披肩，"
                 "含笑的浅笑，眼神柔和，燕麦色高领毛衣"),
    ("f5", 2105, "非常漂亮的中国年轻女性，二十四岁，甜美可爱，空气刘海及腰长直发，"
                 "俏皮地眯眼笑，藕粉色卫衣"),
    ("f6", 2706, "非常漂亮的中国年轻女性，二十九岁，优雅知性的美人，皮肤白皙，"
                 "低盘发，得体大方的微笑，眼神明亮从容，深灰色西装"),
    ("m1", 2601, "非常帅气的欧美年轻男性，二十五岁，瘦削立体的脸型，下颌线清晰，高鼻梁，"
                 "浅棕色短发，蓝灰色眼睛，阳光爽朗的笑容，浅灰色圆领 T 恤"),
    ("m2", 2602, "非常帅气的欧美年轻男性，二十二岁，清瘦的鹅蛋脸，少年感，金色短发，"
                 "蓝眼睛，腼腆开心地笑，白色衬衫"),
    ("m3", 2603, "非常帅气的欧美年轻男性，二十八岁，瘦长的脸型，轮廓分明，深棕色微卷短发，"
                 "修剪干净的短胡茬，从容的微笑，藏青色西装白衬衫"),
    ("m4", 2604, "非常帅气的欧美年轻男性，二十六岁，窄脸高颧骨，模特般的骨相，黑色短发，"
                 "绿色眼睛，咧嘴自信地笑，黑色皮夹克"),
    ("m5", 2605, "非常帅气的欧美年轻男性，二十四岁，斯文清瘦的长脸，细金属边框眼镜，"
                 "浅金棕色头发，温和的笑容，浅蓝色牛津纺衬衫"),
    ("m6", 2606, "非常帅气的欧美年轻男性，二十七岁，瘦削的脸，深邃的五官，深色卷发，"
                 "轻松的微笑，眼神清朗，焦糖色针织衫"),
    # 韩式那一挂：和上面的欧美脸放在一起，一局里抽到谁都不至于撞脸
    ("m7", 2701, "非常帅气的韩式风格年轻男性，二十四岁，瘦削的小脸，下颌线清晰，冷白皮，"
                 "利落的黑色短发，阳光灿烂的笑容，白色 T 恤"),
    ("m8", 2702, "非常帅气的韩式风格年轻男性，二十二岁，清瘦的窄脸，韩国男团的少年感，"
                 "蓬松的栗色碎盖发型，开心地笑着，浅灰色卫衣"),
    ("m9", 2703, "非常帅气的韩式风格年轻男性，二十六岁，瘦长脸型，高鼻梁，桃花眼，"
                 "黑色中分短发，温柔的笑容，黑色针织衫"),
    ("m10", 2704, "非常帅气的韩式风格年轻男性，二十五岁，瘦脸，轮廓干净，微卷的深棕色短发，"
                  "笑起来有梨涡，浅蓝色衬衫"),
    ("m11", 2705, "非常帅气的韩式风格年轻男性，二十三岁，清瘦白净的脸，细框眼镜，"
                  "柔顺的黑色短发，腼腆阳光的笑，米色开衫"),
    ("m12", 2706, "非常帅气的韩式风格年轻男性，二十七岁，瘦削立体的脸，眉眼深邃，"
                  "黑色短发微微上梳，爽朗自信的笑容，深灰色西装"),
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
    small = resize_png(whiten_bg(png), EDGE)      # 统一漂成纯白底，排在一起才齐
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
