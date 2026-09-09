"""命令行跑一局，不开 UI：python -m backend.cli [--mock] [--seed N]"""
from __future__ import annotations

import argparse
import asyncio
import time

from .game import Game


async def _run(args: argparse.Namespace) -> None:
    g = Game(time.strftime("%Y%m%d%H%M%S"), seed=args.seed,
             use_api=False if args.mock else None, model=args.model)
    print(f"[model] {g.llm.model}  [seed] {g.seed}")
    t0 = time.time()
    await g.run()
    for e in g.events:
        tag = "" if e.audience == "all" else f"[仅{'/'.join(str(s) + '号' for s in e.audience)}] "
        print(f"{tag}{e.text}")
    print(f"\n[结果] {g.winner}胜 · {len(g.calls)} 次调用 · {time.time() - t0:.1f}s · 存档 data/{g.gid}.json")


def main() -> None:
    ap = argparse.ArgumentParser(prog="backend.cli")
    ap.add_argument("--mock", action="store_true", help="不调模型，用本地启发式大脑")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model", default=None, help="覆盖默认模型，如 qwen3.8-flash")
    asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    main()
