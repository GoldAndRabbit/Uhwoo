"""补身份图：frontend/img/roles/<role>.png，风格对齐已有的那几张。

    .venv/bin/python -m scripts.gen_roles hunter witch

已有的四张（狼人/预言家/守卫/平民）是以前用别的工具出的西幻插画，所以这里把其中一张
当参考图喂回去（image_api 的 ref_images），新出的猎人和女巫才不会和它们不是一路货。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from backend.image_api import ROOT, generate_png, resize_png, to_data_uri

PUBLIC = ROOT / "frontend" / "img" / "roles"
ORIGINAL = ROOT / "assets" / "roles-original"
REF = PUBLIC / "seer.png"          # 拿预言家那张定风格
EDGE = 320

STYLE = ("西幻角色立绘，半身，人物居中，柔和的逆光，背景是虚化的雾气和光斑，"
         "厚涂质感，细节丰富，正方形构图，无文字无水印")

ROLES: dict[str, tuple[int, str]] = {
    "hunter": (3101, "狼人杀里的猎人：中年猎手，皮甲与棕色斗篷，手持一把装饰华丽的长猎枪，"
                     "肩上停着一只鹰，眼神锐利沉稳"),
    "witch": (3102, "狼人杀里的女巫：紫袍女法师，手捧两只发光的药瓶（一红一绿），"
                    "腰间挂着草药和小瓶子，神情神秘而温柔"),
}


async def one(key: str, seed: int, who: str) -> str:
    ref = [to_data_uri(REF.read_bytes())] if REF.exists() else None
    png = await generate_png(f"{who}。{STYLE}", kind="role", seed=seed, ref_images=ref)
    ORIGINAL.mkdir(parents=True, exist_ok=True)
    (ORIGINAL / f"{key}.png").write_bytes(png)
    small = resize_png(png, EDGE)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / f"{key}.png").write_bytes(small)
    return f"✓ {key}  {len(png) // 1024}KB → {EDGE}px {len(small) // 1024}KB"


async def main() -> None:
    want = sys.argv[1:] or list(ROLES)
    todo = [(k, *ROLES[k]) for k in want if k in ROLES]
    if not todo:
        raise SystemExit(f"可选：{' '.join(ROLES)}")
    for line in await asyncio.gather(*(one(*t) for t in todo)):
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
