# Multi-agent-werewolf

线上：**https://uhwoo.com**

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

## 两种模式

左栏第一个下拉框切换：

- **模拟模式**（默认）—— 6 个 agent 自己打，你是上帝视角：狼队夜里的商议、预言家的查验、
  每个 agent 吃进去的上下文和吐出来的 JSON，全都看得到。
- **单人模式** —— 随机给你一个座位，其余 5 个交给模型。视野严格按这个座位裁：
  只有公开发言、投票、死讯，加上你自己的身份和技能信息（你是狼就看得到狼队商议，
  是预言家就看得到自己的查验结果）。中栏不再显示别人的上下文，左栏别人的身份是 `?`。
  轮到你时右栏底部弹出作答面板：发言是输入框，守护/查验/刀人/投票是座位按钮，
  按钮只列引擎认可的合法目标（不能连守同一人、狼不能刀队友、投票不能投自己）。
  300 秒不操作就交给模型代打，不会把整局挂死。

裁剪是在服务端做的（`Game._visible` / `_call_visible` / `to_json(filtered=True)`），
不是前端藏起来 —— 看不到的信息根本不会推到浏览器。存档仍然存完整的上帝视角，复盘时才看得到全部。

## 规则

- 夜晚：守卫守护（不能连守同一人、可以守自己）→ 狼队商议后刀人 → 预言家查验。守护成功则当晚不死。
- 白天：公布死讯 → 随机起始位依次发言 → 全体同时投票，得票最高者出局，平票无人出局，出局者不留遗言。
- 胜负：狼全出局 → 好人胜；狼数 ≥ 好人数 → 狼人胜；8 轮未分胜负按平局。
- 并发：守卫 / 狼队 / 预言家三条线在同一夜里并发跑（狼队内部串行，要看到彼此的提议）；
  投票也是并发的（本来就是同时投），投完再按座位顺序公布。一局约 30~50 次调用、两三分钟。

## 上线

走 cloudflared tunnel：公网流量经 Cloudflare 回落到本机的 uvicorn，不需要公网 IP、不用开端口。
（同一个账号下的 days4fun 走的是 Cloudflare Pages —— 那套只能跑边缘 JS、请求级生命周期，
而这里一局是个跑 2~4 分钟的常驻 asyncio 任务 + 一条 SSE 长连接 + 内存里的 6 份 agent 上下文，
搬不过去，所以选隧道。）

```bash
./deploy/run_server.sh     # 起 uvicorn，只监听 127.0.0.1:8130
./deploy/run_cf.sh         # 起隧道，自动 quic/http2 探测
./deploy/check_cf.sh       # 看本地 + 隧道 + 线上三段通不通
./deploy/kill_cf.sh        # 只停自己这条隧道
```

首次要在 Cloudflare Zero Trust 建一条名为 `uhwoo` 的隧道，加 Published application
`uhwoo.com → http://localhost:8130`（DNS 会自动配好），然后把 token 写进 `.env`：

```
CLOUDFLARED_TUNNEL_UHWOO_TOKEN=eyJ...
```

变量名带 `UHWOO` 是刻意的：本机还跑着别的项目的隧道，用通用名会撞。同理，
`kill_cf.sh` 只按自己的 pid 文件停，不去 `pkill -f 'cloudflared tunnel'`，
否则会把别人的隧道一起杀掉。

`cloudflared tunnel login` 那条路在国内网络下走不通（回调 login.cloudflareaccess.org 秒 EOF），
所以用 token 模式。另外本机 DNS 被污染（`uhwoo.com` 会解析到 198.18.x.x），
自测时用 `curl --resolve uhwoo.com:443:<真实边缘IP>` 绕开，否则每个请求白等 5s。

## 目录

```
backend/
  main.py      FastAPI：/api/start /api/stop /api/answer /api/stream(SSE) /api/snapshot /api/history
  game.py      对局引擎：夜晚 → 白天 → 投票 → 胜负，逐条广播事件；单人模式在这里等你回答
  model.py     Role / Player / Event(audience) / LLMCall
  prompts.py   常驻的规则与身份 + 每一步的指令和 JSON schema
  llm.py       调用层：真实模型 + mock 启发式大脑 + JSON 提取
  llm_api.py   transport（移植自 pitchasso/llm_api）
  cli.py       命令行跑一局
  tts.py       语音合成（qwen-audio-3.0-tts-flash），发言落地即预热、按文本落盘缓存
deploy/        run_server.sh / run_cf.sh / kill_cf.sh / check_cf.sh / setup_tunnel.sh
frontend/      index.html / style.css / app.js（原生三栏 UI，SSE 增量渲染）
config/        llm_api.yaml
logs/games/    每局存档 JSON（完整上帝视角），左栏「历史对话」可回放
data/tts/      语音合成缓存（按文本 sha1）
```
