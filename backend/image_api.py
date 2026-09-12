"""生图：火山引擎方舟 Seedream（transport 移植自 Rinarino/util/image_api）。

    POST {base_url}/images/generations
    请求: model / prompt / size / response_format(url|b64_json) / watermark / seed
         / image(参考图 url 或 data URI，4.0+ 支持，同一角色的多张图靠它保持一致)
    响应: {"data":[{"url": ...}], "usage":{...}}  —— url 是临时地址，拿到就下载。

- 鉴权：ARK_API_KEY（兼容 ARK_VOLCENGINE_API_KEY / VOLCENGINE_API_KEY 两个别名）
- 配置：config/llm_api.yaml 的 ark 段（base_url / image_model / 各 kind 的出图尺寸）
- 给了 seed 的请求按 sha1(model|size|seed|prompt|refs|cutout) 落盘 data/images/，
  重画同一张不再花钱；没给 seed 就是「要一张新的」，不走缓存

公开接口:
  - available() / has_credentials() -> bool
  - generate_png(prompt, *, kind, seed, ref_images, cutout) -> bytes
  - cutout(png) -> bytes         去背：从四边 flood fill 掉近白底，转 RGBA（要 Pillow）
  - to_data_uri(png) -> str      把本地参考图喂回 image 参数

CLI: .venv/bin/python -m backend.image_api --prompt "一只狼" --kind role -o wolf.png
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import logging
import os
import sys
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx

from .llm_api import CONFIG_PATH, ROOT, TRANSIENT_EXC, load_dotenv

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)
CACHE_DIR = ROOT / "data" / "images"

Kind = Literal["role", "bg", "sprite"]

# 同一把 key 在不同仓里叫过不同名字，都认
ENV_KEY_ALIASES = ("ARK_API_KEY", "ARK_VOLCENGINE_API_KEY", "VOLCENGINE_API_KEY")

# 方舟的硬限制：3,686,400 ≤ 面积 ≤ 4,624,220，超了直接 400。
# 每一档都是该比例在这个区间里能取到的最大值 —— 像素是白给的。
DEFAULT_SIZES: dict[str, str] = {
    "role": "2144x2144",      # 1:1    身份牌头像，和 assets/roles-original 一致
    "bg": "2864x1608",        # 16:9   背景板
    "sprite": "1608x2856",    # 9:16   竖版立绘
}


class SensitiveContentError(RuntimeError):
    """提示词被内容安全拦下。

    实测这个判定是**会误判的**：同一段提示词拦一次、再发一次就过了。所以它按
    可重试处理（和网络抖动同一条路径），只是重试完还不过的时候要单独报出来——
    那就真是措辞问题，再重试多少次都一样。
    """


@dataclass(frozen=True)
class ArkConfig:
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    image_model: str = "doubao-seedream-5-0-pro-260628"
    models: tuple[str, ...] = ()
    watermark: bool = False
    timeout: float = 180.0
    sizes: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SIZES))

    def size_for(self, kind: str) -> str:
        return self.sizes.get(kind) or DEFAULT_SIZES["role"]


@lru_cache(maxsize=1)
def load_ark_config() -> ArkConfig:
    load_dotenv()
    raw: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            import yaml

            raw = (yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}).get("ark") or {}
        except Exception as exc:              # 配置坏了不阻塞启动，退回默认
            logger.warning("读取 ark 配置失败：%s", exc)
    sizes = {**DEFAULT_SIZES, **{str(k): str(v) for k, v in (raw.get("sizes") or {}).items()}}
    model = raw.get("image_model") or ArkConfig.image_model
    models = tuple(str(m) for m in (raw.get("models") or ()))
    if model not in models:
        models = (model,) + models
    return ArkConfig(
        base_url=str(raw.get("base_url") or ArkConfig.base_url).rstrip("/"),
        image_model=model,
        models=models,
        watermark=bool(raw.get("watermark", False)),
        timeout=float(raw.get("timeout", 180)),
        sizes=sizes,
    )


def api_key() -> str:
    load_dotenv()
    for name in ENV_KEY_ALIASES:
        if value := os.environ.get(name, "").strip():
            return value
    return ""


def has_credentials() -> bool:
    return bool(api_key())


def available() -> bool:
    return has_credentials()


def credential_hint() -> str:
    return f"{' / '.join(ENV_KEY_ALIASES)}（model={load_ark_config().image_model}）"


def to_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _cache_path(model: str, size: str, seed: int, prompt: str,
                refs: list[str], cut: bool) -> Path:
    key = f"{model}|{size}|{seed}|{cut}|{prompt}|{'|'.join(refs)}".encode("utf-8")
    return CACHE_DIR / f"{hashlib.sha1(key).hexdigest()}.png"


_locks: dict[str, asyncio.Lock] = {}


async def generate_png(
    prompt: str,
    *,
    kind: Kind = "role",
    seed: int | None = None,
    ref_images: list[str] | None = None,
    model: str | None = None,
    size: str | None = None,
    cutout: bool = False,
    max_attempts: int = 4,
) -> bytes:
    """出一张图，返回 PNG bytes。ref_images 传 http(s) url 或 data URI。

    给了 seed 就走磁盘缓存（同一把 seed + 同一段词本来就该出同一张），并发请求
    只画一次；没给 seed 表示「随便来一张新的」，每次都真发请求。
    """
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("空提示词")
    cfg = load_ark_config()
    model = model or cfg.image_model
    size = size or cfg.size_for(kind)
    refs = list(ref_images or [])

    if seed is None:
        return await _generate_once(prompt, model=model, size=size, seed=None, refs=refs,
                                    cutout=cutout, max_attempts=max_attempts)
    path = _cache_path(model, size, seed, prompt, refs, cutout)
    if path.exists():
        return path.read_bytes()
    lock = _locks.setdefault(path.name, asyncio.Lock())
    async with lock:
        if path.exists():
            return path.read_bytes()
        png = await _generate_once(prompt, model=model, size=size, seed=seed, refs=refs,
                                   cutout=cutout, max_attempts=max_attempts)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return png


async def _generate_once(
    prompt: str, *, model: str, size: str, seed: int | None,
    refs: list[str], cutout: bool, max_attempts: int,
) -> bytes:
    cfg = load_ark_config()
    key = api_key()
    if not key:
        raise RuntimeError(f"{ENV_KEY_ALIASES[0]} is not set")
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "url",
        "watermark": cfg.watermark,
    }
    if seed is not None:
        payload["seed"] = int(seed) % 2_147_483_647
    if refs:
        # 单图用 str、多图用 list，方舟两种都收
        payload["image"] = refs[0] if len(refs) == 1 else list(refs)

    url = f"{cfg.base_url}/images/generations"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""
    for attempt in range(max(1, max_attempts)):
        try:
            png = await _post_once(url, headers, payload, cfg.timeout)
            return await asyncio.to_thread(cut_out, png) if cutout else png
        except (*TRANSIENT_EXC, SensitiveContentError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt >= max_attempts - 1:
                raise
            wait = (attempt + 1) * 2.0
            logger.warning("生图失败（%s），%.1fs 后第 %s 次重试", last, wait, attempt + 2)
            await asyncio.sleep(wait)
    raise RuntimeError(f"Seedream 重试耗尽: {last}")


async def _post_once(url: str, headers: dict[str, str], payload: dict[str, Any],
                     timeout: float) -> bytes:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            body = resp.text
            # 内容安全拦截是**改 prompt 才能解决**的，重试多少次都一样，
            # 单独认出来并说清楚，免得在重试循环里白白耗掉几分钟。
            if "SensitiveContent" in body:
                raise SensitiveContentError(f"内容安全拦截：{body[:200]}")
            raise RuntimeError(f"Seedream HTTP {resp.status_code}: {body[:300]}")
        items = resp.json().get("data") or []
        if not items:
            raise RuntimeError(f"Seedream 响应无图: {resp.text[:300]}")
        first = items[0]
        if b64 := first.get("b64_json"):
            return base64.b64decode(b64)
        got = await client.get(first["url"])
        got.raise_for_status()
        return got.content


# ---------------- 去背（要 Pillow，只有 cutout=True 时才 import） ----------------

def _bg_walk(im: Image.Image, tolerance: int, halo: int) -> Image.Image:
    """从四边往里走，吃掉背景，返回硬蒙版（255 前景 / 0 背景）。

    两档标准，都只从画面边缘连通地走，所以角色身上的白衣服（不挨着边）动不了：
      1. 近白：随便走多远——模型给的底色本来就是纯白
      2. 浅色低饱和：只准再走 halo 像素——模型很爱在白底上刷一圈米色的
         柔光 / 投影，那圈东西不近白，老标准吃不掉，抠完就挂着一条浅色轮廓边。
         限步数是因为角色可能穿白鞋：不限的话会从鞋边一路啃进鞋里。
    """
    from PIL import Image as _Image

    w, h = im.size
    px = im.load()
    assert px is not None

    white = 255 - tolerance

    def near_white(x: int, y: int) -> bool:
        r, g, b, _ = px[x, y]
        return r >= white and g >= white and b >= white

    def pale(x: int, y: int) -> bool:
        r, g, b, _ = px[x, y]
        lo, hi = min(r, g, b), max(r, g, b)
        return lo >= 168 and hi - lo <= 42

    # budget: 还能在「浅色低饱和」里走几步；近白像素随时把它充满
    budget = bytearray(w * h)
    seen = bytearray(w * h)
    queue: deque[tuple[int, int, int]] = deque()

    def seed(x: int, y: int) -> None:
        i = y * w + x
        if seen[i]:
            return
        if near_white(x, y):
            seen[i] = 1
            budget[i] = halo
            queue.append((x, y, halo))

    for x in range(w):
        seed(x, 0)
        seed(x, h - 1)
    for y in range(h):
        seed(0, y)
        seed(w - 1, y)

    mask = _Image.new("L", (w, h), 255)
    mpx = mask.load()
    assert mpx is not None
    while queue:
        x, y, left = queue.popleft()
        mpx[x, y] = 0
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            i = ny * w + nx
            if near_white(nx, ny):
                nleft = halo
            elif halo and pale(nx, ny) and left > 0:
                nleft = left - 1
            else:
                continue
            # 走过一次还不够：从别的方向来可能剩的步数更多，够就再走一遍
            if seen[i] and budget[i] >= nleft:
                continue
            seen[i] = 1
            budget[i] = nleft
            queue.append((nx, ny, nleft))
    return mask


def _fill_white_holes(im: Image.Image, mask: Image.Image, *, max_area: float = 0.0025) -> None:
    """把**发丝之间围出来的白洞**也抠掉（原地改 mask）。

    从四边走进不去的白：一缕一缕的碎发之间夹着的那些白块。不抠掉的话贴到深色
    背景上，角色头上就顶着一团一团的白斑——比边缘毛刺显眼得多。
    但「围起来的白」也可能是奶白衬衫，所以卡两道：
      1. 只认**很白**（比外面那档严得多），米白奶白都不算
      2. 只认**小块**（默认画面的 0.25%），衣服那种大片白一律留着
    """
    w, h = im.size
    px = im.load()
    mpx = mask.load()
    assert px is not None and mpx is not None
    limit = int(w * h * max_area)

    def very_white(x: int, y: int) -> bool:
        r, g, b = px[x, y][:3]
        return min(r, g, b) >= 242 and max(r, g, b) - min(r, g, b) <= 10

    seen = bytearray(w * h)
    for y0 in range(h):
        row = y0 * w
        for x0 in range(w):
            if seen[row + x0] or mpx[x0, y0] == 0 or not very_white(x0, y0):
                continue
            seen[row + x0] = 1
            blob = [(x0, y0)]
            queue: deque[tuple[int, int]] = deque(blob)
            while queue:
                x, y = queue.popleft()
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if not (0 <= nx < w and 0 <= ny < h):
                        continue
                    i = ny * w + nx
                    if seen[i] or mpx[nx, ny] == 0 or not very_white(nx, ny):
                        continue
                    seen[i] = 1
                    queue.append((nx, ny))
                    blob.append((nx, ny))
            # 整块走完再判：中途退出的话这块剩下的部分下一轮会被当成新的一块，
            # 一件白衬衫就会被一口一口啃掉
            if len(blob) <= limit:
                for x, y in blob:
                    mpx[x, y] = 0


def _soft_edge(im: Image.Image, hard: Image.Image, *, band: int = 5) -> Image.Image:
    """把硬边界内侧一条带子里的**残留白边**改成半透明。

    发丝、睫毛这种细节在白底上是半透明地过渡过去的，一刀切的蒙版会把过渡段
    整片留成不透明的白，贴到深色背景上就是一圈毛边。所以在边界带里按「有多白」
    重新定 alpha：越接近纯白越透明。只在带子里做，角色内部的浅色衣服不受影响。
    """
    from PIL import ImageChops, ImageFilter

    r, g, b = im.convert("RGB").split()
    lo = ImageChops.darker(ImageChops.darker(r, g), b)
    # lo >= 250 → 全透明；lo <= 198 → 全不透明；中间线性过渡
    soft = lo.point(lambda v: 255 if v <= 198 else (0 if v >= 250 else (250 - v) * 255 // 52))
    inner = hard.filter(ImageFilter.MinFilter(2 * band + 1))
    edge = ImageChops.subtract(hard, inner)
    # 带子外面不设限（取 255），带子里面取 soft
    relaxed = ImageChops.lighter(soft, ImageChops.invert(edge))
    return ImageChops.darker(hard, relaxed)


def cut_out(png: bytes, *, tolerance: int = 26, feather: bool = True, halo: int = 12) -> bytes:
    """去背：从四边 flood fill 掉与画面边缘连通的背景，再修一遍边。要提示词里写明白底。

    只做**边缘连通域**是关键：角色身上的白衬衫不挨着边，不会被一起抠掉。
    （Rinarino 那边试过改成「和邻居比色差」的区域生长，结果它会顺着柔和的轮廓
    边缘一路啃进角色内部——前景占比从 43% 涨到 93%。）
    """
    from PIL import Image as _Image
    from PIL import ImageFilter

    im = _Image.open(io.BytesIO(png)).convert("RGBA")
    hard = _bg_walk(im, tolerance, halo)
    _fill_white_holes(im, hard)
    mask = _soft_edge(im, hard)
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(0.8))
    im.putalpha(mask)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def recut(png: bytes, **kw: Any) -> bytes:
    """对**已经抠过**的图重抠一遍：先把透明的地方填回纯白，再走一次 cut_out。

    改了去背算法之后不用重新花钱生图——原图的背景本来就是白的，
    填回白色就等价于拿到了模型当初给的那张。
    """
    from PIL import Image as _Image

    im = _Image.open(io.BytesIO(png)).convert("RGBA")
    flat = _Image.new("RGBA", im.size, (255, 255, 255, 255))
    flat.alpha_composite(im)
    buf = io.BytesIO()
    flat.convert("RGB").save(buf, "PNG")
    return cut_out(buf.getvalue(), **kw)


def foreground_ratio(png: bytes) -> float:
    """不透明像素占比。立绘正常在 0.25–0.35 左右；去背失败或模型画糊了会明显偏高。"""
    from PIL import Image as _Image

    im = _Image.open(io.BytesIO(png)).convert("RGBA").resize((96, 96))
    px = [im.getpixel((x, y)) for y in range(96) for x in range(96)]
    return sum(1 for v in px if v[3] > 96) / len(px)


def whiten_bg(png: bytes, *, tol: int = 30, feather: float = 1.2) -> bytes:
    """把影棚灰底漂成纯白。

    只从**上边和左右边**往里走，不从底边起步 —— 头肩像的衣服是连着底边的，
    从那儿进去会把浅色衬衫一路啃掉。判定同时卡两条：和边缘基准色的差值在 tol 以内，
    而且本身是低饱和的（人脸、衣服的彩色都过不了这一关）。
    """
    from PIL import Image as _Image
    from PIL import ImageFilter

    im = _Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    px = im.load()
    assert px is not None
    edge = ([px[x, 0] for x in range(0, w, 3)]
            + [px[0, y] for y in range(0, h, 3)]
            + [px[w - 1, y] for y in range(0, h, 3)])
    base = tuple(sorted(c[i] for c in edge)[len(edge) // 2] for i in range(3))

    def near(c: tuple[int, int, int]) -> bool:
        return all(abs(c[i] - base[i]) <= tol for i in range(3)) and max(c) - min(c) <= 26

    seen = bytearray(w * h)
    queue: deque[tuple[int, int]] = deque()
    for x in range(w):
        if near(px[x, 0]):
            seen[x] = 1
            queue.append((x, 0))
    for y in range(h):
        for x in (0, w - 1):
            if not seen[y * w + x] and near(px[x, y]):
                seen[y * w + x] = 1
                queue.append((x, y))

    mask = _Image.new("L", (w, h), 0)
    mp = mask.load()
    assert mp is not None
    while queue:
        x, y = queue.popleft()
        mp[x, y] = 255
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and near(px[nx, ny]):
                seen[ny * w + nx] = 1
                queue.append((nx, ny))

    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
    out = _Image.composite(_Image.new("RGB", (w, h), (255, 255, 255)), im, mask)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def resize_png(png: bytes, edge: int) -> bytes:
    """等比缩到长边 edge 像素（前端用的 320px 版就是这么来的）。"""
    from PIL import Image as _Image

    im = _Image.open(io.BytesIO(png))
    im.thumbnail((edge, edge), _Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _main() -> None:
    cfg = load_ark_config()
    ap = argparse.ArgumentParser(prog="backend.image_api")
    ap.add_argument("--prompt")
    ap.add_argument("--kind", default="role", choices=sorted(DEFAULT_SIZES))
    ap.add_argument("-m", "--model", default=cfg.image_model)
    ap.add_argument("--size", default=None, help=f"默认按 kind 取：{cfg.sizes}")
    ap.add_argument("--seed", type=int, default=None, help="给了就走磁盘缓存")
    ap.add_argument("--ref", action="append", default=[], help="参考图：http url 或本地路径")
    ap.add_argument("--cutout", action="store_true", help="去背成透明 PNG")
    ap.add_argument("--edge", type=int, default=0, help="再等比缩到长边这么多像素")
    # 改了去背算法之后就地重抠，不用重新花钱生图（原图背景本来就是白的）
    ap.add_argument("--recut", nargs="+", default=[], metavar="PNG", help="就地重抠这些图")
    ap.add_argument("-o", "--out", default=str(ROOT / "out.png"))
    args = ap.parse_args(sys.argv[1:])

    if args.recut:
        for raw in args.recut:
            p = Path(raw)
            before = foreground_ratio(p.read_bytes())
            png = recut(p.read_bytes())
            p.write_bytes(png)
            print(f"✓ {p.name}  前景 {before:.2f} → {foreground_ratio(png):.2f}")
        return
    if not args.prompt:
        ap.error("要么给 --prompt 生图，要么给 --recut 重抠")

    refs = [r if r.startswith("http") else to_data_uri(Path(r).read_bytes()) for r in args.ref]
    png = asyncio.run(generate_png(
        args.prompt, kind=args.kind, seed=args.seed, ref_images=refs or None,
        model=args.model, size=args.size, cutout=args.cutout,
    ))
    if args.edge:
        png = resize_png(png, args.edge)
    Path(args.out).write_bytes(png)
    print(f"✓ {args.out} ({len(png) // 1024}KB)")


if __name__ == "__main__":
    try:
        _main()
    except (RuntimeError, ValueError, httpx.HTTPError) as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e
