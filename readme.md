# Multi-agent-werewolf

6 人狼人杀的多 agent 模拟：**2 狼人 + 2 平民 + 1 预言家 + 1 守卫**。
每个玩家是一个独立的 agent，各有各的视野 —— 全局一条事件流，每条事件带 `audience`，
谁看得到、谁看不到由它决定。右侧是上帝视角（受限信息会打上「仅 X 号」标签），
中间是每个 agent 真正吃进去的上下文和它吐出来的 JSON。

```
┌── 左：开局 / 玩家 / 历史 ──┬── 中：每轮上下文与输出 ──┬── 右：上帝视角事件流 ──┐
│ 6/6 存活                   │ 规则与身份（常驻）        │ 第 1 夜 / 第 1 天       │
│ 1号 守卫  2 次决策 · 1.3k  │ 本轮新增（290 字）        │ 仅 3号/4号：狼队商议     │
│ 系统全局状态 11 次调用     │ 输出：thinking / target   │ 4号 狼人：发言…          │
└────────────────────────────┴───────────────────────────┴─────────────────────────┘
```

## 跑起来

```bash
./run.sh                      # 建 venv、装依赖、起服务 → http://127.0.0.1:8130
PORT=8131 ./run.sh            # 换端口
```

不开 UI，命令行跑一局：

```bash
.venv/bin/python -m backend.cli            # 真实模型
.venv/bin/python -m backend.cli --mock     # 本地启发式大脑，不烧 token
WEREWOLF_MOCK=1 ./run.sh                   # 整个服务强制 mock（调 UI 用）
```

## 模型

LLM transport 从 `pitchasso/llm_api` 移植过来：OpenAI 兼容的 `/v1/chat/completions`，
支持阿里云百炼（DashScope）和 SiliconFlow，带 transient 重试和 SSE 流式解码。

- 鉴权：环境变量 `ALIYUN_BAILIAN_API_KEY` / `SILICONFLOW_API_KEY`（也可以写进项目根的 `.env`）
- 选型：`config/llm_api.yaml` 的 `werewolf` 段（默认 `aliyun / qwen3.8-flash`，关思考）
- 没有 key 时自动退回本地启发式大脑，UI 仍然能完整跑完一局
- 单独测 transport：`.venv/bin/python -m backend.llm_api --no-stream "你是谁"`

每次调用都要求模型输出 JSON（`response_format: json_object`，上游不支持就退回纯提示词约束）；
拿不到可解析 JSON 时按规则兜底，不让一局崩掉，那张卡片会标 ⚠︎。

## 上下文是怎么给的

每个 agent 一条自己的消息历史：

- **system（常驻）**：规则 + 自己的身份 + 队友（狼）/ 技能说明，全局不变，中间栏折叠成「规则与身份（常驻，N 字）」
- **user（本轮新增）**：从上次轮到它之后、它**可见**的事件（按「历史发言」/「历史投票和当前状态」分组）+ 这一步的指令和 JSON 字段说明
- **assistant**：模型输出的 JSON 原文

所以狼队夜里的商议只进狼的上下文，预言家的查验结果只进预言家的上下文。
左栏每个玩家后面的 `2 次决策 · 1.3k` 就是它的决策次数和当前上下文字符数。

## 规则

- 夜晚：守卫守护（不能连守同一人、可以守自己）→ 狼队商议后刀人 → 预言家查验。守护成功则当晚不死。
- 白天：公布死讯 → 随机起始位依次发言 → 全体同时投票，得票最高者出局，平票无人出局，出局者不留遗言。
- 胜负：狼全出局 → 好人胜；狼数 ≥ 好人数 → 狼人胜；8 轮未分胜负按平局。
- 并发：守卫 / 狼队 / 预言家三条线在同一夜里并发跑（狼队内部串行，要看到彼此的提议）；
  投票也是并发的（本来就是同时投），投完再按座位顺序公布。一局约 30~50 次调用、两三分钟。

## 目录

```
backend/
  main.py      FastAPI：/api/start /api/stop /api/stream(SSE) /api/snapshot /api/history
  game.py      对局引擎：夜晚 → 白天 → 投票 → 胜负，逐条广播事件
  model.py     Role / Player / Event(audience) / LLMCall
  prompts.py   常驻的规则与身份 + 每一步的指令和 JSON schema
  llm.py       调用层：真实模型 + mock 启发式大脑 + JSON 提取
  llm_api.py   transport（移植自 pitchasso/llm_api）
  cli.py       命令行跑一局
frontend/      index.html / style.css / app.js（原生三栏 UI，SSE 增量渲染）
config/        llm_api.yaml
data/          每局存档 JSON，左栏「历史对话」可回放
```
