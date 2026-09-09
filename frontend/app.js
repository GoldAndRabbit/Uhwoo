const $ = (id) => document.getElementById(id);
const ROLE_ORDER = { "狼人": 0, "预言家": 1, "守卫": 2, "平民": 3 };

const S = {
  state: null,
  events: [],
  calls: [],
  filter: null,        // null=全部, 数字=座位, "sys"=系统
  pending: null,       // 正在等你回答的那个问题
  es: null,
  timer: null,
  readonly: false,
};

/* ---------------- 工具 ---------------- */
const kchars = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n));
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const roleOf = (seat) => S.state?.players?.find((p) => p.seat === seat)?.role || "";
const isPlay = () => S.state?.mode === "play";

// 身份头像。单人模式下别人的 role 是 null，自然就不会有头像，逻辑不用另写
const ROLE_IMG = { "守卫": "guard", "平民": "villager", "狼人": "wolf", "预言家": "seer" };
const avatar = (role, cls = "ava") =>
  ROLE_IMG[role] ? `<img class="${cls}" src="/static/img/roles/${ROLE_IMG[role]}.png" alt="${role}">` : "";
const audienceLabel = (a) =>
  a === "all" ? null : "只有 " + a.map((s) => s + "号").join("、") + " 看得到";

/* ---------------- 左栏 ---------------- */
function renderPlayers() {
  const box = $("players");
  const st = S.state;
  if (!st) return;
  const rows = st.players.map((p) => {
    const active = S.filter === p.seat ? " active" : "";
    const dead = p.alive ? "" : " dead";
    const meta = `${p.alive ? "存活" : "第" + p.death_round + "轮出局"}<br>${p.decisions} 次决策 · ${kchars(p.ctx_chars)}`;
    const me = p.seat === S.state.human ? '<span class="rtag r-守卫">你</span>' : "";
    const tag = p.role ? `<span class="rtag r-${p.role}">${p.role}</span>`
                       : `<span class="rtag r-平民">?</span>`;
    return `<div class="prow${active}${dead}" data-seat="${p.seat}">
      ${avatar(p.role) || '<span class="ava blank"></span>'}
      <span class="pname">${p.seat}号</span>${tag}${me}
      <span class="pmeta">${meta}</span></div>`;
  });
  const sysActive = S.filter === "sys" ? " active" : "";
  box.innerHTML =
    `<div class="prow sys${sysActive}" data-seat="sys">
       <span class="pname">系统全局状态</span>
       <span class="pmeta">${st.sys_calls} 次调用</span></div>` + rows.join("");
  box.querySelectorAll(".prow").forEach((el) =>
    el.addEventListener("click", () => {
      const v = el.dataset.seat;
      const seat = v === "sys" ? "sys" : Number(v);
      S.filter = S.filter === seat ? null : seat;
      renderPlayers();
      renderCalls();
      if (isMobile() && S.filter !== null) setTab("ctx");
    })
  );
  const me = st.human ? st.players.find((p) => p.seat === st.human) : null;
  $("meCard").hidden = !me;
  if (me) {
    $("meCard").innerHTML =
      `你是 <b>${me.seat}号 · ${me.role || "?"}</b>　${me.alive ? "存活" : "已出局"}<br>
       只看得到公开发言、投票和你自己的信息。`;
  }
  const aliveN = st.players.filter((p) => p.alive).length;
  $("aliveCount").textContent = `${aliveN}/${st.players.length} 存活`;
  $("callCount").textContent = st.calls;
  $("navCalls").textContent = st.calls;
}

async function loadHistory() {
  const list = await (await fetch("/api/history")).json();
  $("histCount").textContent = list.length;
  $("history").innerHTML =
    list
      .map(
        (h) => `<div class="hrow" data-gid="${h.gid}"><span>werewolf ${h.gid.slice(0, 12)}</span>
        <span class="hwin">${h.winner ? h.winner.slice(0, 2) + "胜" : h.status === "stopped" ? "中断" : ""}</span></div>`
      )
      .join("") || `<div class="empty">还没有历史对局</div>`;
  $("history").querySelectorAll(".hrow").forEach((el) =>
    el.addEventListener("click", () => openHistory(el.dataset.gid))
  );
}

async function openHistory(gid) {
  const d = await (await fetch("/api/history/" + gid)).json();
  if (S.es) { S.es.close(); S.es = null; }
  S.readonly = true;
  S.state = d.state; S.events = d.events; S.calls = d.calls; S.filter = null;
  renderAll();
}

/* ---------------- 中栏 ---------------- */
function renderCalls() {
  const box = $("calls");
  let list = S.calls;
  if (S.filter === "sys") list = list.filter((c) => c.seat === null);
  else if (typeof S.filter === "number") list = list.filter((c) => c.seat === S.filter);

  $("filterBar").hidden = S.filter === null;
  $("filterName").textContent = S.filter === "sys" ? "系统全局状态" : S.filter + "号";

  if (!list.length) {
    box.innerHTML = isPlay()
      ? `<div class="empty">单人模式下这里只显示你自己的回合，别人的上下文不给你看。</div>`
      : `<div class="empty">还没有调用。点「开一局」开始。</div>`;
    return;
  }
  box.innerHTML = list.slice(-60).map(cardHTML).join("");
}

function cardHTML(c) {
  const sys = c.seat === null;
  const p = c.parsed || {};
  let act = "";
  if (p.speech !== undefined) act = `<div class="act">${esc(p.speech)}</div>`;
  else if (p.target !== undefined)
    act = `<div class="act"><span class="pill">target ${esc(p.target)}号</span> ${esc(p.reason || "")}</div>`;
  else if (sys) act = `<div class="blk">${esc(c.output)}</div>`;
  const err = p._error || p._parse_error
    ? `<div class="act mock">⚠︎ ${esc(p._error || "模型没给出可解析 JSON")}（已按规则兜底）</div>` : "";
  const think = p.thinking ? `<div class="think">${esc(p.thinking)}</div>` : "";
  return `<div class="card">
    <div class="card-head"><b>${esc(c.title)}</b>
      <span class="ctx">${sys ? c.model : "ctx " + kchars(c.ctx_chars)}</span></div>
    <div class="card-body">
      <details><summary>${sys ? "上帝视角说明" : "规则与身份（常驻，" + c.system_prompt.length + " 字）"}</summary>
        <div class="blk">${esc(c.system_prompt)}</div></details>
      <details open><summary>本轮新增（${c.delta.length} 字）</summary>
        <div class="blk">${esc(c.delta)}</div></details>
      <div class="out"><span class="k">输出${c.latency_ms ? " · " + (c.latency_ms / 1000).toFixed(1) + "s" : ""}</span>
        ${think}${act}${err}</div>
    </div></div>`;
}

/* ---------------- 右栏 ---------------- */
function renderLog() {
  $("log").innerHTML = S.events.map(rowHTML).join("") ||
    `<div class="empty">点「开一局」，这里会逐条出现夜里的行动和白天的发言。</div>`;
}

const PLAY_ICON = '<svg viewBox="0 0 12 14" width="10" height="12"><path d="M1 1l10 6-10 6z" fill="currentColor"/></svg>';
const WAVE_ICON = '<i class="wave"><b></b><b></b><b></b></i>';

function playBtn(ev) {
  if (!TTS.ok || !speakable(ev)) return "";
  const on = TTS.playingId === ev.id;
  return `<button class="play${on ? " on" : ""}" data-eid="${ev.id}"
           title="${on ? "停止" : "播放这一句"}">${on ? WAVE_ICON : PLAY_ICON}</button>`;
}

function rowHTML(ev) {
  const rid = ` data-eid="${ev.id}"`;
  const only = audienceLabel(ev.audience);
  const onlyTag = only
    ? `<span class="only" title="这条事件只进这些人的上下文，别人拿不到">${only}</span>` : "";
  if (ev.kind === "phase") {
    const day = ev.text.includes("天");
    return `<div class="phase"><h2>${esc(ev.text.replace(/—/g, "").trim())}</h2>
      <span class="note">${day ? "白天：发言 → 投票" : "夜晚：守卫 → 狼人 → 预言家"}</span></div>`;
  }
  if (ev.kind === "system" && ev.text.startsWith("存活玩家"))
    return `<div class="info">${esc(ev.text)}</div>`;
  if (ev.kind === "speech") {
    const seat = ev.seat, role = roleOf(seat);
    const body = ev.text.replace(/^[^：]*：/, "");
    return `<div class="row"${rid}><div class="who">${avatar(role)}
        <span class="badge r-${role || "平民"}">${seat}号${role ? " " + role : ""}</span></div>
      <div class="bubble say">${esc(body)}</div>${playBtn(ev)}</div>`;
  }
  if (ev.kind === "vote")
    return `<div class="row"><div class="bubble plain vote">${esc(ev.text)}</div>${onlyTag}</div>`;
  if (ev.kind === "judge")
    return `<div class="row"${rid}><span class="badge judge">法官</span>
      <div class="bubble judgeline">${esc(ev.text)}</div>${playBtn(ev)}</div>`;
  if (ev.kind === "result")
    return `<div class="row"${rid}><span class="badge judge">法官</span>
      <div class="bubble result">${esc(ev.text)}</div>${playBtn(ev)}</div>`;
  if (ev.kind === "night_action") {
    const role = ev.seat ? roleOf(ev.seat) : "";
    const badge = ev.seat
      ? `<div class="who">${avatar(role)}
           <span class="badge r-${role || "平民"}">${ev.seat}号${role ? " " + role : ""}</span></div>` : "";
    return `<div class="row"${rid}>${badge}
      <div class="bubble ${only ? "wolfnight" : ""}">${esc(ev.text)}</div>${onlyTag}${playBtn(ev)}</div>`;
  }
  return `<div class="row"><div class="bubble plain">${esc(ev.text)}</div>${onlyTag}</div>`;
}



/* ---------------- 事件流 ---------------- */
function renderAll() {
  renderMeBar();
  renderPlayers();
  renderCalls();
  renderLog();
  const running = S.state?.status === "running" && !S.readonly;
  $("btnStart").disabled = running;
  $("btnStop").disabled = !running;
}

function connect() {
  if (S.es) S.es.close();
  S.es = new EventSource(api.stream());
  S.es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "idle") return;
    if (msg.type === "snapshot") {
      S.state = msg.data.state; S.events = msg.data.events; S.calls = msg.data.calls;
      if (TTS.ok && S.state.status === "running") {
        TTS.on = !!S.state.tts && $("ttsOn").checked;
      }
      renderAsk(S.state.pending);        // 刷新页面时如果正轮到你，把问题接回来
      renderAll(); return;
    }
    if (msg.state && msg.type !== "event") { S.state = msg.state; renderMeBar(); }
    if (msg.type === "event") {
      present({ type: "event", ev: msg.data, state: msg.state });
    } else if (msg.type === "call") {
      S.calls.push(msg.data);
      renderCalls();
    } else if (msg.type === "prompt") {
      if (msg.data) present({ type: "prompt", p: msg.data });
      else renderAsk(null);
    }
    renderPlayers();
    if (["finished", "stopped"].includes(S.state?.status)) { loadHistory(); renderAll(); }
  };
  S.es.onerror = () => { /* 浏览器会自动重连 */ };
}

/* ---------------- 多人房间 ---------------- */
// 进了房间之后，所有请求都走 /api/room/<code>/*，并带上自己的 token；
// 服务端按 token 对应的座位裁视野，所以每个人看到的事件流是不一样的
const R = { code: null, token: null, host: false, poll: null };

const api = {
  stream: () => (R.code ? `/api/room/${R.code}/stream?token=${R.token}` : "/api/stream"),
  answer: () => (R.code ? `/api/room/${R.code}/answer` : "/api/answer"),
  stop: () => (R.code ? `/api/room/${R.code}/stop` : "/api/stop"),
};

const post = async (url, body) => {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || r.status);
  return d;
};

function saveRoom() {
  try { localStorage.setItem("ww_room", JSON.stringify({ code: R.code, token: R.token })); }
  catch (_) {}
}

function renderRoom(info) {
  R.host = !!info.is_host;
  $("roomBox").hidden = false;
  $("roomCode").textContent = info.code;
  $("roomHint").textContent = `把房间号或这个链接发给朋友：${location.origin}/#join-${info.code}`;
  $("roomMembers").innerHTML = info.members.map((m) => {
    const me = info.you && m.name === info.you.name && m.seat === info.you.seat;
    return `<span class="${m.host ? "host" : ""}${me ? " me" : ""}">${esc(m.name)}${
      m.seat ? " · " + m.seat + "号" : ""}</span>`;
  }).join("") + `<span>${info.count}/${info.max} 人 · 空位交给模型</span>`;
  $("btnRoomStart").hidden = !info.is_host || info.status === "running";
  $("btnRoomStart").textContent = info.status === "running" ? "进行中" : "开始（房主）";
}

async function pollRoom() {
  if (!R.code) return;
  try {
    const info = await (await fetch(`/api/room/${R.code}?token=${R.token}`)).json();
    if (info.detail) { leaveRoom(); return; }
    renderRoom(info);
    if (info.status === "running" && !S.es) connect();   // 房主开局了，跟着进去
  } catch (_) {}
}

function leaveRoom() {
  if (R.code) post(`/api/room/${R.code}/leave`, { token: R.token }).catch(() => {});
  R.code = R.token = null; R.host = false;
  clearInterval(R.poll); R.poll = null;
  try { localStorage.removeItem("ww_room"); } catch (_) {}
  $("roomBox").hidden = true;
  if (S.es) { S.es.close(); S.es = null; }
}

function enterRoom(d) {
  R.code = d.code; R.token = d.token;
  saveRoom();
  renderRoom(d.room);
  clearInterval(R.poll);
  R.poll = setInterval(pollRoom, 2000);
}

/* ---------------- 事件流顶栏：身份 + 停止/重开 ---------------- */
function renderMeBar() {
  const st = S.state;
  const bar = $("meBar");
  if (!st) { bar.hidden = true; return; }
  bar.hidden = false;
  const running = st.status === "running" && !S.readonly;
  const me = st.human ? st.players.find((p) => p.seat === st.human) : null;
  const status = st.status === "running" ? `第${st.round}${st.phase === "night" ? "夜" : "天"}`
               : st.winner ? `${st.winner}胜` : "已停止";
  $("meBarText").innerHTML = me
    ? `${avatar(me.role, "ava sm")}你是 <b>${me.seat}号</b> <span class="role r-${me.role}">${me.role}</span>
       ${me.alive ? "" : "（已出局）"} · ${status}`
    : `模拟模式 · 上帝视角 · ${status}`;
  $("barStop").disabled = !running;
  $("barNew").disabled = running;
}

/* ---------------- 单人模式：轮到你 ---------------- */
let askTimer = null;

function renderAsk(p) {
  const box = $("ask");
  S.pending = p || null;
  if (!p) { box.hidden = true; clearInterval(askTimer); return; }
  box.hidden = false;
  const meRole = roleOf(p.seat);
  $("askTitle").textContent = `${p.title}　轮到你${meRole ? "（你是 " + meRole + "）" : ""}`;
  const q = `<div class="ask-q">${esc(p.instruction)}</div>`;

  if (p.field === "speech") {
    $("askBody").innerHTML = q +
      `<textarea id="askText" placeholder="说点什么，所有人都看得到…"></textarea>
       <button class="send" id="askSend">发言</button>`;
    $("askSend").addEventListener("click", () =>
      submitAnswer({ speech: $("askText").value }));
    $("askText").focus();
  } else {
    const seats = (p.options || []).map((s) =>
      `<button data-seat="${s}">${s}号</button>`).join("");
    // 只有狼队夜里商议要给队友一句理由；投票和守护/查验都是闷着来的，不填理由
    const reason = p.kind === "wolf"
      ? `<input type="text" id="askReason" placeholder="给狼队友的一句话（可留空）">` : "";
    $("askBody").innerHTML = q +
      `<div class="seats" id="askSeats">${seats}</div>${reason}
       <button class="send" id="askSend" disabled>${p.kind === "vote" ? "投票" : "确定"}</button>`;
    let chosen = null;
    $("askSeats").querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => {
        chosen = Number(b.dataset.seat);
        $("askSeats").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
        b.classList.add("on");
        $("askSend").disabled = false;
      }));
    $("askSend").addEventListener("click", () =>
      submitAnswer({ target: chosen, reason: $("askReason")?.value || "" }));
  }

  clearInterval(askTimer);
  askTimer = setInterval(() => {
    const left = Math.max(0, p.timeout - (Date.now() / 1000 - p.asked_at));
    $("askLeft").textContent = left > 0 ? `剩 ${Math.ceil(left)}s` : "已超时，交给模型代打";
    if (left <= 0) clearInterval(askTimer);
  }, 200);
  if (isMobile()) setTab("log");
  setTimeout(() => box.scrollIntoView({ block: "end", behavior: "smooth" }), 60);
}

async function submitAnswer(payload) {
  const btn = $("askSend");
  if (btn) btn.disabled = true;
  try {
    await post(api.answer(), { ...payload, token: R.token });
  } catch (e) {
    alert(e.message);
    if (btn) btn.disabled = false;
    return;
  }
  renderAsk(null);
}

/* ---------------- 移动端页签 ---------------- */
const isMobile = () => window.matchMedia("(max-width: 860px)").matches;

function setTab(name) {
  document.body.dataset.tab = name;
  if (isMobile()) history.replaceState(null, "", "#" + name);   // 刷新后停在同一页签
  document.querySelectorAll(".mobnav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  const col = { side: ".sidebar", ctx: ".center", log: ".right" }[name];
  const el = document.querySelector(col);
  if (el) el.scrollTop = name === "log" ? el.scrollHeight : 0;
}

document.querySelectorAll(".mobnav button")
  .forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

/* ---------------- 朗读 + 演出队列 ---------------- */
// 开了朗读之后，事件不再一到就渲染 —— 渲染和播报走同一条流水线：
// 念完这一条才放出下一条，包括「轮到你」的作答面板。不然语音还没读完
// 下面的发言就全刷出来了，等于剧透。
const TTS = { on: false, ok: false, audio: new Audio(), urls: new Map(),
              unlocked: false, blocked: false };
TTS.audio.preload = "auto";
TTS.audio.playsInline = true;

const JUDGE = 0;                       // 法官（上帝）用 0 号音色，只报天数、死讯和票型

// iOS / Safari 只认「用户手势里同步调用的 play()」。我们的音频要等 fetch 回来才播，
// 那会儿手势早过期了，所以点按钮的当下先拿一段无声 wav 把这个 audio 元素解锁，
// 之后再换 src 播真正的语音就不会被拦。必须同步调用，前面不能有 await。
const SILENT_WAV = "data:audio/wav;base64,UklGRrQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YZABAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICA";

function unlockAudio() {
  if (TTS.unlocked || !TTS.ok) return;
  try {
    TTS.audio.src = SILENT_WAV;
    const p = TTS.audio.play();
    if (p && p.then) {
      p.then(() => { TTS.audio.pause(); TTS.unlocked = true; TTS.blocked = false; })
       .catch(() => { TTS.blocked = true; });
    } else {
      TTS.unlocked = true;
    }
  } catch (e) { TTS.blocked = true; }
}

// 万一解锁没赶上（比如刷新页面接上正在跑的一局），下一次点页面任意处补一次
document.addEventListener("pointerdown", () => { unlockAudio(); if (TTS.blocked) drain(); },
                          { capture: true });

async function audioURL(seat, text) {
  const key = seat + "|" + text + "|" + ($("cloneOn").checked ? "c" : "p");
  if (TTS.urls.has(key)) return TTS.urls.get(key);
  const p = fetch("/api/tts", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seat, text, clone: $("cloneOn").checked }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    return URL.createObjectURL(await r.blob());
  });
  TTS.urls.set(key, p);
  return p;
}

// 自动朗读时，文字跟着音频逐字浮出来 —— 按 currentTime/duration 推进，
// 所以念多快字就出多快，不是固定速度的打字机。每个字是一个 span，
// 靠 opacity + 微微上移的过渡柔和地淡进来。
function startReveal(ev) {
  const el = document.querySelector(`.row[data-eid="${ev.id}"] .bubble`);
  if (!el) return () => {};
  const full = el.textContent;

  // 按小段切（遇到标点断一下，否则每 4 个字一段）。一个字一个字地蹦太碎，
  // 成段淡入更接近正常说话的节奏
  const chunks = [];
  let buf = "";
  for (const ch of full) {
    buf += ch;
    if (buf.length >= 4 || "。！？，、；：…—".includes(ch)) { chunks.push(buf); buf = ""; }
  }
  if (buf) chunks.push(buf);

  el.textContent = "";
  const spans = chunks.map((t) => {
    const sp = document.createElement("span");
    sp.className = "ck";                 // 默认 display:none，所以气泡是跟着长高的
    sp.textContent = t;
    el.appendChild(sp);
    return sp;
  });
  el.classList.add("revealing");

  const box = $("log").parentElement;
  // 只有当你本来就贴着底部时才自动跟随；往上翻了就别再把人拽回去
  const nearBottom = () => box.scrollHeight - box.scrollTop - box.clientHeight < 90;
  let shown = 0, stopped = false;

  const show = (n) => {
    if (n <= shown) return;
    const follow = nearBottom();
    for (; shown < n && shown < spans.length; shown++) spans[shown].classList.add("on");
    if (follow) box.scrollTop = box.scrollHeight;
  };

  const tick = () => {
    if (stopped) return;
    const a = TTS.audio, d = a.duration;
    if (d && isFinite(d) && d > 0)
      show(Math.ceil(spans.length * Math.min(1, a.currentTime / d)));
    requestAnimationFrame(tick);
  };

  return {
    // 必须等 play() 真的开始了才推进：在那之前 audio 还停在上一句播完的位置
    // （currentTime≈duration），比例算出来是 100%，整段会立刻吐光
    start: () => requestAnimationFrame(tick),
    finish: () => {                    // 播完/被打断都要把整句补齐，别卡在半截
      stopped = true;
      show(spans.length);
      el.classList.remove("revealing");
    },
  };
}

function markPlaying(eid) {
  TTS.playingId = eid;
  document.querySelectorAll(".play").forEach((b) => {
    const on = String(eid) === b.dataset.eid;
    b.classList.toggle("on", on);
    b.innerHTML = on ? WAVE_ICON : PLAY_ICON;
    b.title = on ? "停止" : "播放这一句";
  });
}

async function playLine(seat, text, eid = null, onStart = null) {
  markPlaying(eid);
  try {
    TTS.audio.src = await audioURL(seat, text);
    await TTS.audio.play();
    TTS.blocked = false;
    if (onStart) onStart();            // 这一刻起 currentTime 才是新音频的时间轴
    // pause() 不会触发 ended，所以把 resolve 留一份出去，停止时能直接叫醒这个 await
    await new Promise((res) => { TTS.finish = res; TTS.audio.onended = res; TTS.audio.onerror = res; });
  } catch (e) {
    if (e && e.name === "NotAllowedError") TTS.blocked = true;   // 自动播放被拦，等下一次点击
  } finally {
    TTS.finish = null;
    markPlaying(null);
  }
}

// 这一条能不能念、用谁的声音念（手动点播放用这个，范围宽一些）
function speakable(ev) {
  // 只有两种声音：角色（发言、狼队夜里的商议）和法官（报幕、死讯、票型、你自己的私密信息）。
  // 第N夜/第N天这种分隔线、存活玩家清单、逐条票型都不念 —— 法官那句已经把信息说全了。
  if (ev.kind === "speech")
    return { seat: ev.seat, text: ev.text.replace(/^[^：]*：/, "") };
  if (ev.kind === "result" || ev.kind === "judge")
    return { seat: JUDGE, text: ev.text };
  if (ev.kind === "night_action")
    return ev.seat && ev.text.includes("：")         // 狼队商议是角色在说，用他自己的声音
      ? { seat: ev.seat, text: ev.text.replace(/^[^：]*：/, "") }
      : { seat: JUDGE, text: ev.text };
  return null;
}

// 自动朗读只念叙事主线：发言 + 法官报幕。夜里的私密行动只在单人模式下念给你自己听
function narration(ev) {
  if (ev.kind === "night_action" && !isPlay()) return null;
  return speakable(ev);
}

/* 演出队列：{type:"event"|"prompt", ...} */
const Q = { items: [], busy: false };

function present(item) {
  if (!TTS.on || !TTS.ok) { apply(item); return; }   // 没开朗读就照旧即时渲染
  Q.items.push(item);
  drain();
}

function apply(item) {
  if (item.type === "event") {
    if (S.events.some((e) => e.id === item.ev.id)) return;   // 同一条只渲染一次
    if (item.state) { S.state = item.state; renderMeBar(); renderPlayers(); }
    const box = $("log").parentElement;
    const follow = box.scrollHeight - box.scrollTop - box.clientHeight < 90;
    S.events.push(item.ev);
    renderLog();
    if (follow) box.scrollTop = box.scrollHeight;   // 你翻上去看历史时不打断
  } else if (item.type === "prompt") {
    renderAsk(item.p);
  }
}

async function drain() {
  if (Q.busy) return;
  Q.busy = true;
  while (Q.items.length) {
    const it = Q.items.shift();
    apply(it);
    const line = it.type === "event" ? narration(it.ev) : null;
    if (line && line.text) {
      const next = Q.items.find((x) => x.type === "event" && narration(x.ev));
      if (next) audioURL(narration(next.ev).seat, narration(next.ev).text).catch(() => {});
      const reveal = startReveal(it.ev);      // 先把文字藏起来，等真的开播再逐段放
      await playLine(line.seat, line.text, it.ev.id, reveal.start);
      reveal.finish();
      // 被浏览器的自动播放策略拦了：文字继续往下走，别把已经渲染过的这条塞回队首
      // （塞回去会在续播时二次渲染，就是「同一句出现两遍」的来源）
    } else {
      await new Promise((r) => setTimeout(r, 220));      // 不念的条目也留一点节奏
    }
    if (!TTS.on) { Q.items.forEach(apply); Q.items.length = 0; }   // 中途关掉朗读就全放出来
  }
  Q.busy = false;
}

function stopSpeaking() {
  Q.items.forEach(apply);              // 别把没渲染的事件吞掉
  Q.items.length = 0;
  TTS.audio.pause();
  TTS.audio.currentTime = 0;
  if (TTS.finish) TTS.finish();        // 叫醒正卡在「等播完」的那个 await，否则 drain 会一直挂着
  Q.busy = false;
}

/* ---------------- 交互 ---------------- */
// 左栏的「开一局/停止」和事件流顶栏的「重开/停止」是同一套逻辑
async function startGame() {
  unlockAudio();                                   // 必须在 await 之前，手势才有效
  $("btnStart").disabled = true;
  $("barNew").disabled = true;
  S.readonly = false;
  stopSpeaking();
  TTS.on = TTS.ok && $("ttsOn").checked;          // 以点开一局这一刻的勾选为准
  const r = await fetch("/api/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: $("modelSel").value, tts: TTS.on, mode: $("modeSel").value,
      clone: $("cloneOn").checked,
      role: $("modeSel").value === "play" ? $("roleSel").value : "",
    }),
  });
  if (!r.ok) {
    alert((await r.json()).detail);
    $("btnStart").disabled = false;
    renderMeBar();
    return;
  }
  const d = await r.json();
  S.state = d.state; S.events = []; S.calls = []; S.filter = null;
  renderAsk(null);
  renderAll();
  connect();
  if (isMobile()) setTab("log");            // 手机上开局后直接看事件流
}

async function stopGame() {
  $("btnStop").disabled = true;
  $("barStop").disabled = true;
  renderAsk(null);
  // 停止要两三秒才真正生效，这期间在途的发言还会推过来 —— 先把朗读关掉，
  // 否则它们照样排进播放队列，人已经点了停止还在念
  TTS.on = false;
  stopSpeaking();
  await post(api.stop(), { token: R.token }).catch(() => {});
}

function syncModeUI() {
  const m = $("modeSel").value;
  $("roleField").hidden = m !== "play";           // 身份只在单人模式下用得上
  $("lobby").hidden = m !== "multi";              // 房间面板只在多人模式出现
  $("btnStart").hidden = m === "multi";           // 多人局由房主在房间里开
  $("btnStop").hidden = m === "multi";
  if (m !== "multi" && R.code) leaveRoom();
}
$("modeSel").addEventListener("change", syncModeUI);
syncModeUI();

$("btnCreate").addEventListener("click", async () => {
  unlockAudio();
  try { enterRoom(await post("/api/room", { name: $("nickName").value })); }
  catch (e) { alert(e.message); }
});

$("btnJoin").addEventListener("click", async () => {
  unlockAudio();
  const code = $("joinCode").value.trim();
  if (!/^\d{5}$/.test(code)) { alert("房间号是 5 位数字"); return; }
  try { enterRoom(await post(`/api/room/${code}/join`, { name: $("nickName").value })); }
  catch (e) { alert(e.message); }
});

$("btnLeave").addEventListener("click", leaveRoom);

$("btnRoomStart").addEventListener("click", async () => {
  unlockAudio();
  TTS.on = TTS.ok && $("ttsOn").checked;
  try {
    await post(`/api/room/${R.code}/start`, {
      token: R.token, model: $("modelSel").value, tts: TTS.on, clone: $("cloneOn").checked,
    });
    S.events = []; S.calls = []; S.filter = null;
    renderAsk(null);
    connect();
    if (isMobile()) setTab("log");
  } catch (e) { alert(e.message); }
});

$("btnStart").addEventListener("click", startGame);
$("btnStop").addEventListener("click", stopGame);
$("barNew").addEventListener("click", startGame);
$("barStop").addEventListener("click", stopGame);

$("cloneOn").addEventListener("change", (e) => {
  try { localStorage.setItem("ww_clone", e.target.checked ? "1" : "0"); } catch (_) {}
  TTS.urls.clear();                    // 换了音色，之前那些 blob 不能再用
  stopSpeaking();
});

$("ttsOn").addEventListener("change", (e) => {
  if (e.target.checked) unlockAudio();
  TTS.on = TTS.ok && e.target.checked;
  try { localStorage.setItem("ww_tts", TTS.on ? "1" : "0"); } catch (_) {}
  if (!TTS.on) stopSpeaking();                    // 中途取消勾选就立刻闭麦
});

// 每条气泡后面的播放按钮：正在响就停，否则单独播这一句
$("log").addEventListener("click", (e) => {
  const btn = e.target.closest(".play");
  if (!btn || !TTS.ok) return;
  unlockAudio();
  const eid = Number(btn.dataset.eid);
  if (TTS.playingId === eid) { stopSpeaking(); markPlaying(null); return; }
  stopSpeaking();
  const ev = S.events.find((x) => x.id === eid);
  const line = ev && speakable(ev);
  if (line) playLine(line.seat, line.text, eid);
});

$("btnNew").addEventListener("click", startGame);
$("filterClear").addEventListener("click", () => { S.filter = null; renderPlayers(); renderCalls(); });

(async function init() {
  const cfg = await (await fetch("/api/config")).json();
  $("modelSel").innerHTML = cfg.models
    .map((m) => `<option value="${m}"${m === cfg.model ? " selected" : ""}>${m}</option>`).join("");
  TTS.ok = !!cfg.tts;
  $("cloneOn").checked = cfg.clone_default !== false;
  try {
    const c = localStorage.getItem("ww_clone");
    if (c !== null) $("cloneOn").checked = c === "1";
  } catch (_) {}
  if (!TTS.ok) {
    $("ttsOn").disabled = true;
    $("ttsOn").parentElement.title = "未配置 DASHSCOPE_API_KEY / ALIYUN_BAILIAN_API_KEY";
  } else {
    let want = false;
    try { want = localStorage.getItem("ww_tts") === "1"; } catch (_) {}
    $("ttsOn").checked = want;
    TTS.on = want;
  }
  const snap = await (await fetch("/api/snapshot")).json();
  if (!snap.empty) {
    S.state = snap.state; S.events = snap.events; S.calls = snap.calls;
    renderAsk(snap.state.pending);        // 刚进页面就正轮到你的话，直接把问题摆出来
    if (snap.state.status === "running") { $("modeSel").value = snap.state.mode; syncModeUI(); }
    renderAll();
    if (snap.state.status === "running") connect();
  } else {
    renderLog(); renderCalls();
  }
  await loadHistory();
  const invite = (location.hash.match(/^#join-(\d{5})$/) || [])[1];
  if (invite) {                                   // 朋友点邀请链接进来的
    $("modeSel").value = "multi";
    $("joinCode").value = invite;
    history.replaceState(null, "", location.pathname);
  }
  try {
    const saved = invite ? null : JSON.parse(localStorage.getItem("ww_room") || "null");
    if (saved && saved.code) {
      const info = await (await fetch(`/api/room/${saved.code}?token=${saved.token}`)).json();
      if (info.code && info.you) {                 // 房间还在，接着玩
        $("modeSel").value = "multi";
        R.code = saved.code; R.token = saved.token;
        renderRoom(info);
        R.poll = setInterval(pollRoom, 2000);
        if (info.status === "running") connect();
      } else { localStorage.removeItem("ww_room"); }
    }
  } catch (_) {}
  syncModeUI();

  const hash = location.hash.replace("#", "");
  setTab(["side", "ctx", "log"].includes(hash) ? hash
         : S.state && S.state.status === "running" ? "log" : "side");
})();
