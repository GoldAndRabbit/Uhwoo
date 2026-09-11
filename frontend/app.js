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
const nameOf = (seat) => S.state?.names?.[String(seat)] || `${seat}号`;
const isPlay = () => S.state?.mode === "play";
// 音色两档：快速 = qwen-audio-3.0-tts-flash（一把嗓子，约 0.8s/句），
// 真实 = cosyvoice-v3.5-flash 的克隆音色（男女跟着头像走，约 1.6s/句）
const realVoice = () => $("voiceMode").value === "real";

// 玩家头像：开局时后端从 12 张里随机抽 6 张发给 1–6 号（state.avatars）。
// 事件流、左栏玩家、顶栏共用这一个，号码压在头像下半部当角标 —— 谁在说话一眼认得出，
// 也省得每处都在旁边再挂一个「N号」的标签。
const seatFace = (seat) => S.state?.avatars?.[String(seat)] || null;

function face(seat, cls = "", label = "", eid = null) {
  const key = seatFace(seat);
  // 事件流里的头像按**事件**判断在不在说话：同一个人（尤其法官）在历史里出现很多次，
  // 按座位判断会让他所有的历史气泡一起呼吸。左栏/顶栏那种常驻位没有 eid，按座位判断
  const speaking = (eid === null ? TTS.speaking === seat : TTS.speakingId === eid)
    ? " speaking" : "";
  // 身份揭示了就让呼吸灯跟着身份的颜色走（模拟模式全程可见）
  const role = roleOf(seat);
  cls = cls + (role ? ` g-${role}` : "");
  const img = key
    ? `<img src="/static/img/avatars/${key}.png" alt="${seat}号">`
    : `<span class="face-blank"></span>`;   // 老存档没发过头像，留个占位别塌掉
  const tag = label || `${seat}<i>号</i>`;
  return `<span class="face ${cls}${speaking}" data-seat="${seat}">${img}
            <b class="face-no">${tag}</b></span>`;
}

// 法官（0 号）：也每局换一张脸，角标写「法官」而不是号码
const judgeFace = (cls = "", eid = null) => face(0, cls, "法官", eid);

// 朗读到谁，谁的头像就呼吸一下（左栏、事件流、顶栏是同一个 seat，一起亮）
function markSpeaking(seat, eid = null) {
  // 法官是 0 号，`seat || null` 会把它当成空值 —— 必须显式判 null
  TTS.speaking = seat === null || seat === undefined ? null : seat;
  TTS.speakingId = eid;
  document.querySelectorAll(".face").forEach((el) => {
    const row = el.closest(".row[data-eid]");
    el.classList.toggle("speaking", row
      ? TTS.speakingId !== null && String(TTS.speakingId) === row.dataset.eid
      : TTS.speaking !== null && String(TTS.speaking) === el.dataset.seat);
  });
}

const audienceLabel = (a) => {
  if (a === "all") return null;
  const who = a.map((s) => `${s}号`);
  // 两个人用「和」，三个以上前面顿号、最后一个还是「和」：2号、4号和5号
  const list = who.length > 1 ? who.slice(0, -1).join("、") + "和" + who[who.length - 1] : who[0];
  return `可见范围：${list}`;
};

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
      ${face(p.seat, "md")}
      <span class="pname">${nameOf(p.seat)}</span>${tag}${me}
      <span class="pmeta">${meta}</span></div>`;
  });
  const sysActive = S.filter === "sys" ? " active" : "";
  // 法官排在 6 个玩家后面：他不是玩家，但这一局的脸也该有个地方挂着
  const judgeRow = seatFace(0)
    ? `<div class="prow judge">${judgeFace("md")}
         <span class="pname">法官</span>
         <span class="pmeta">主持本局</span></div>` : "";
  box.innerHTML =
    `<div class="prow sys${sysActive}" data-seat="sys">
       <span class="pname">系统全局状态</span>
       <span class="pmeta">${st.sys_calls} 次调用</span></div>` + rows.join("") + judgeRow;
  box.querySelectorAll(".prow[data-seat]").forEach((el) =>
    el.addEventListener("click", () => {
      const v = el.dataset.seat;
      const seat = v === "sys" ? "sys" : Number(v);
      S.filter = S.filter === seat ? null : seat;
      renderPlayers();
      renderCalls();
      if (isMobile() && S.filter !== null) setTab("ctx");
    })
  );
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
// 「存活玩家 / 发言顺序」永远跟在法官那句后面，是它的注脚而不是一条独立事件，
// 所以渲染时直接并进法官的气泡里（括号），少占一行
const isRoster = (ev) => ev && ev.kind === "system" && ev.text.startsWith("存活玩家");

function renderLog() {
  // 按天/夜分段：每一段整个包进一个底色块里（夜里冷灰、白天米黄），
  // 翻长事件流时一眼看得出自己在哪一段，而不是只有一根分隔条
  const out = [];
  let seg = null;
  const flush = () => {
    if (seg) out.push(`<section class="seg ${seg.cls}">${seg.rows.join("")}</section>`);
    seg = null;
  };
  for (let i = 0; i < S.events.length; i++) {
    const ev = S.events[i];
    if (isRoster(ev) && (seg ? seg.rows.length : out.length)) continue;   // 已并进上一条
    const next = S.events[i + 1];
    const html = rowHTML(ev, isRoster(next) ? next.text : "");
    if (ev.kind === "phase") {
      flush();
      seg = { cls: ev.text.includes("天") ? "day" : "night", rows: [html] };
    } else if (seg) {
      seg.rows.push(html);
    } else {
      out.push(html);                    // 开局那条在第一段之前
    }
  }
  flush();
  $("log").innerHTML = out.join("") ||
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

function rowHTML(ev, extra = "") {
  const rid = ` data-eid="${ev.id}"`;
  const note = extra ? `<span class="aside">（${esc(extra.replace(/。$/, ""))}）</span>` : "";
  const only = audienceLabel(ev.audience);
  // 可见范围不单独占一个胶囊，跟在正文后面当个括号 —— 它只是句注脚，不该抢位置
  const onlyTag = only
    ? `<span class="aside" title="这条事件只进这些人的上下文，别人拿不到">（${only}）</span>` : "";
  if (ev.kind === "phase") {
    const day = ev.text.includes("天");
    return `<div class="phase${day ? " day" : ""}"><h2>${esc(ev.text.replace(/—/g, "").trim())}</h2>
      <span class="note">${day ? "白天：发言 → 投票" : "夜晚：守卫 → 狼人 → 预言家"}</span></div>`;
  }
  if (ev.kind === "system" && ev.text.startsWith("存活玩家"))
    return `<div class="info">${esc(ev.text)}</div>`;
  if (ev.kind === "system" && ev.text.startsWith("游戏开始")) {
    // 开局先把 6 个人摆出来。座位清单本来就写在这条事件的文本里（agent 的上下文要用），
    // 但 UI 上有头像就够了，把那半句摘掉，只留配置
    // 法官站最左边，用一条竖线和 1–6 号隔开 —— 他不是这一局的玩家
    // 带上 ev.id：开局这排是「历史」，不该跟着谁在说话一起呼吸
    const roster = (seatFace(0) ? judgeFace("", ev.id) + `<span class="roster-split"></span>` : "")
      + [1, 2, 3, 4, 5, 6].map((seat) => face(seat, "", "", ev.id)).join("");
    const note = ev.text.replace(/共 ?6 ?名玩家：[^。]*。/, "");
    // 胜负条件写在开局这条底下：狼人是「人数追平就赢」，不是「杀光」
    const win = "狼人阵营胜利条件：活着的狼人数量追平好人（2 狼对 2 好人即可），"
              + "不必杀光；好人阵营：把 2 只狼全部票出局或杀掉。";
    return `<div class="row"${rid}><div class="bubble plain roster-box">
      <div class="roster">${roster}</div>
      <div class="roster-note">${esc(note)}<br>${esc(win)}</div></div></div>`;
  }
  if (ev.kind === "speech") {
    const seat = ev.seat, role = roleOf(seat);
    const body = ev.text.replace(/^[^：]*：/, "");
    return `<div class="row"${rid}><div class="who">${face(seat, "", "", ev.id)}
        ${role ? `<span class="badge r-${role}">${role}</span>` : ""}</div>
      <div class="bubble say${role ? " r-" + role : ""}">${esc(body)}</div>${playBtn(ev)}</div>`;
  }
  if (ev.kind === "vote")
    return `<div class="row"><div class="bubble plain vote">${esc(ev.text)}${onlyTag}</div></div>`;
  if (ev.kind === "judge" || ev.kind === "result")
    return `<div class="row"${rid}><div class="who">${judgeFace("", ev.id)}</div>
      <div class="bubble ${ev.kind === "judge" ? "judgeline" : "result"}">${esc(ev.text)}${note}</div>
      ${playBtn(ev)}</div>`;
  if (ev.kind === "night_action") {
    const role = ev.seat ? roleOf(ev.seat) : "";
    const badge = ev.seat
      ? `<div class="who">${face(ev.seat, "", "", ev.id)}
           ${role ? `<span class="badge r-${role}">${role}</span>` : ""}</div>`
      : `<div class="who">${judgeFace("", ev.id)}</div>`;
    return `<div class="row"${rid}>${badge}
      <div class="bubble ${role ? "r-" + role : only ? "wolfnight" : ""}">${esc(ev.text)}${onlyTag}</div>
      ${playBtn(ev)}</div>`;
  }
  return `<div class="row"><div class="bubble plain">${esc(ev.text)}${onlyTag}</div></div>`;
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
      // SSE 断线重连时服务端会重发完整快照。如果这会儿正在按语音节奏演出，
      // 直接整份替换会把还没念到的发言一次性铺出来 —— 只把新事件排进队列。
      const busy = TTS.on && TTS.ok && (Q.busy || Q.items.length);
      S.state = msg.data.state;
      if (TTS.ok && S.state.status === "running") {
        TTS.on = !!S.state.tts;
      }
      if (busy) {
        const known = new Set(S.events.map((e) => e.id));
        Q.items.forEach((i) => { if (i.type === "event") known.add(i.ev.id); });
        msg.data.events.filter((x) => !known.has(x.id))       // e 是外层的 MessageEvent，别遮蔽
          .forEach((ev) => present({ type: "event", ev, state: msg.data.state }));
        S.calls = msg.data.calls;
        renderCalls(); renderPlayers(); renderMeBar();
        return;
      }
      S.events = msg.data.events; S.calls = msg.data.calls;
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
  setJoinUI(false);                 // 已经在房里了，就别再让人点创建/加入
  $("roomCode").textContent = info.code;
  $("roomHint").textContent = `把房间号或这个链接发给朋友：${location.origin}/#join-${info.code}`;
  $("roomMembers").innerHTML = info.members.map((m) => {
    const me = info.you && m.id === info.you.id;
    const kick = info.is_host && !m.host && info.status !== "running"
      ? `<i class="kick" data-id="${m.id}" title="移出房间">×</i>` : "";
    return `<span class="${m.host ? "host" : ""}${me ? " me" : ""}">${esc(m.name)}${
      m.seat ? " · " + m.seat + "号" : ""}${kick}</span>`;
  }).join("") + `<span>${info.count}/${info.max} 人 · 空位交给模型</span>`;
  $("roomMembers").querySelectorAll(".kick").forEach((el) =>
    el.addEventListener("click", async () => {
      try { renderRoom((await post(`/api/room/${R.code}/kick`,
                                   { token: R.token, id: el.dataset.id })).room); }
      catch (e) { alert(e.message); }
    }));
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

function setJoinUI(on) {
  $("nickName").hidden = !on;
  document.querySelectorAll(".lobby .row2").forEach((el) => { el.hidden = !on; });
}

function leaveRoom() {
  if (R.code) post(`/api/room/${R.code}/leave`, { token: R.token }).catch(() => {});
  R.code = R.token = null; R.host = false;
  clearInterval(R.poll); R.poll = null;
  try { localStorage.removeItem("ww_room"); } catch (_) {}
  $("roomBox").hidden = true;
  setJoinUI(true);
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
  $("meBarText").innerHTML = (me
    ? `${face(me.seat, "sm")}你是 <b>${me.seat}号</b> <span class="role r-${me.role}">${me.role}</span>
       ${me.alive ? "" : "（已出局）"} · ${status}`
    : `模拟模式 · 上帝视角 · ${status}`);
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
    const mic = REC.ok
      ? `<button class="mic" id="askMic"><span class="dot"></span><span id="askMicTxt">🎤 语音输入</span></button>`
      : "";
    $("askBody").innerHTML = q +
      `<textarea id="askText" placeholder="说点什么，或者点右边的麦克风说给它听…"></textarea>
       <div class="ask-actions">${mic}<button class="send" id="askSend">发言</button></div>`;
    $("askSend").addEventListener("click", () =>
      submitAnswer({ speech: $("askText").value }));
    if (REC.ok) $("askMic").addEventListener("click", micToggle);
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

/* ---------------- 语音输入 ---------------- */
// 浏览器自带的 MediaRecorder 各家格式不一样（Chrome 是 webm/opus、Safari 是 mp4），
// 上游 ASR 只认几种固定格式，所以自己在 AudioContext 里拿原始 PCM，
// 降到 16k 单声道再编成 wav —— 服务端只需要认一种格式。
const REC = { ok: false, on: false, busy: false, ctx: null, stream: null, node: null,
              chunks: [], rate: 0, target: null };
const REC_RATE = 16000;

function downsample(chunks, from, to) {
  const flat = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of chunks) { flat.set(c, at); at += c.length; }
  if (from <= to) return flat;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(flat.length / ratio));
  for (let i = 0; i < out.length; i++) {
    // 取这一段的平均值而不是直接抽点：直接抽点会把高频折回来，听着发毛刺
    const a = Math.floor(i * ratio), b = Math.min(flat.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = a; j < b; j++) sum += flat[j];
    out[i] = b > a ? sum / (b - a) : 0;
  }
  return out;
}

function toWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buf);
  const str = (off, t) => { for (let i = 0; i < t.length; i++) view.setUint8(off + i, t.charCodeAt(i)); };
  str(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVEfmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  str(36, "data"); view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

async function recStart() {
  // channelCount 写成 ideal：写死 1 的话，不支持单声道的设备会直接 OverconstrainedError。
  // 反正下面只取 channel 0，多声道也无所谓
  const want = { channelCount: { ideal: 1 }, echoCancellation: true, noiseSuppression: true };
  try {
    REC.stream = await navigator.mediaDevices.getUserMedia({ audio: want });
  } catch (e) {
    if (e && (e.name === "OverconstrainedError" || e.name === "NotFoundError")) {
      REC.stream = await navigator.mediaDevices.getUserMedia({ audio: true });   // 退回最宽松的
    } else {
      throw e;
    }
  }
  const Ctx = window.AudioContext || window.webkitAudioContext;
  REC.ctx = new Ctx();
  if (REC.ctx.state === "suspended") await REC.ctx.resume();
  const src = REC.ctx.createMediaStreamSource(REC.stream);
  const node = REC.ctx.createScriptProcessor(4096, 1, 1);
  REC.chunks = [];
  REC.rate = REC.ctx.sampleRate;
  node.onaudioprocess = (e) => REC.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(node);
  node.connect(REC.ctx.destination);      // Safari 上不接到 destination 就不会回调
  REC.node = node;
  REC.on = true;
}

function recStop() {
  REC.on = false;
  try { REC.node.disconnect(); } catch (_) {}
  try { REC.stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
  try { REC.ctx.close(); } catch (_) {}
  const wav = toWav(downsample(REC.chunks, REC.rate, REC_RATE), REC_RATE);
  REC.chunks = [];
  return wav;
}

function micLabel(txt, cls) {
  const btn = $("askMic");
  if (!btn) return;
  btn.className = "mic" + (cls ? " " + cls : "");
  $("askMicTxt").textContent = txt;
}

// 出错时说人话：这几种原因的处理方式完全不一样，笼统报个英文没人知道该干嘛
async function micError(e) {
  const name = (e && e.name) || "";
  if (name === "NotAllowedError")
    return "浏览器没给录音权限：地址栏左边的图标里把麦克风改成「允许」，再试一次。";
  if (name === "NotReadableError")
    return "麦克风被别的程序占着（会议、录音软件之类），先把它关掉。";
  if (name === "NotFoundError") {
    let mics = -1;
    try {
      mics = (await navigator.mediaDevices.enumerateDevices())
        .filter((d) => d.kind === "audioinput").length;
    } catch (_) {}
    return mics === 0
      ? "这台机器上没有麦克风（Mac mini 这类台式机不带内置麦克风），接一个带麦的耳机/USB 麦克风再试。"
      : "系统没交出麦克风：macOS 下去「系统设置 → 隐私与安全性 → 麦克风」里把浏览器打开。";
  }
  return "拿不到麦克风：" + (e && e.message ? e.message : e);
}

// 点一下开始录，再点一下结束并识别 —— 比「按住说话」稳：移动端按住时
// 手指一滑出按钮就收不到 pointerup，录音会一直挂着
async function micToggle() {
  const box = $("askText");
  if (REC.busy) return;
  if (!REC.on) {
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
      // http 下除了 localhost 一律没有 mediaDevices，这时候按钮点了得说清楚为什么
      alert("这个页面拿不到麦克风：浏览器只在 https 或 localhost 下开放录音。");
      return;
    }
    try {
      await recStart();
      micLabel("正在听，点一下结束", "rec");
    } catch (e) {
      alert(await micError(e));
    }
    return;
  }
  const wav = recStop();
  REC.busy = true;
  micLabel("识别中…", "busy");
  try {
    const r = await fetch(`/api/asr?format=wav&rate=${REC_RATE}`, {
      method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav,
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    const text = (d.text || "").trim();
    if (!text) { micLabel("没听清，再说一次", ""); return; }
    // 接在已有文字后面：可以先打一半再补一段语音
    box.value = box.value.trim() ? `${box.value.trim()}${text}` : text;
    box.focus();
    micLabel("🎤 语音输入", "");
  } catch (e) {
    micLabel("🎤 语音输入", "");
    alert("识别失败：" + (e && e.message ? e.message : e));
  } finally {
    REC.busy = false;
  }
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
              unlocked: false, unlocking: false, blocked: false, speaking: null, speakingId: null };
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
    // unlocking 这个标记是关键：解锁用的静音 play() 是异步的，它 resolve 的时候
    // 第一条语音往往已经换好 src 开播了 —— 那时候再 pause() 就把法官第一句按停了
    // （反过来，静音的 play() 被新 src 打断而 reject，也会被误判成「浏览器拦了」）。
    // 所以回调里先看这面旗子还在不在：playLine 一接管就把它放倒。
    TTS.unlocking = true;
    TTS.audio.src = SILENT_WAV;
    const p = TTS.audio.play();
    if (p && p.then) {
      p.then(() => {
        TTS.unlocked = true;
        TTS.blocked = false;
        if (TTS.unlocking) TTS.audio.pause();       // 还没人接管才需要收尾
      }).catch(() => {
        if (TTS.unlocking) TTS.blocked = true;
      }).finally(() => { TTS.unlocking = false; });
    } else {
      TTS.unlocked = true;
      TTS.unlocking = false;
    }
  } catch (e) {
    TTS.unlocking = false;
    TTS.blocked = true;
  }
}

// 万一解锁没赶上（比如刷新页面接上正在跑的一局），下一次点页面任意处补一次
document.addEventListener("pointerdown", () => { unlockAudio(); if (TTS.blocked) drain(); },
                          { capture: true });

async function audioURL(seat, text) {
  // 音色槽位由后端开局时按头像性别发好（f0–f2 / m0–m2），这里原样带回去；法官没有槽位
  const voice = seat ? S.state?.voices?.[String(seat)] || "" : "";
  const key = voice + "|" + seat + "|" + text + "|" + (realVoice() ? "c" : "p");
  if (TTS.urls.has(key)) return TTS.urls.get(key);
  const p = fetch("/api/tts", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seat, voice, text, clone: realVoice() }),
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
  // 末尾的注脚（可见范围）不参与逐字浮出：先摘下来，正文切完再挂回去
  const aside = el.querySelector(".aside");
  if (aside) aside.remove();
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
  if (aside) el.appendChild(aside);
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
    const src = await audioURL(seat, text);
    TTS.unlocking = false;              // 从这里开始这个 audio 归我，解锁的收尾别再动它
    TTS.audio.src = src;
    await TTS.audio.play();
    TTS.blocked = false;
    markSpeaking(seat, eid);           // 等 play() 真的开始了再亮，免得被自动播放策略拦下还挂在那
    if (onStart) onStart();            // 这一刻起 currentTime 才是新音频的时间轴
    // pause() 不会触发 ended，所以把 resolve 留一份出去，停止时能直接叫醒这个 await
    await new Promise((res) => { TTS.finish = res; TTS.audio.onended = res; TTS.audio.onerror = res; });
  } catch (e) {
    if (e && e.name === "NotAllowedError") TTS.blocked = true;   // 自动播放被拦，等下一次点击
  } finally {
    TTS.finish = null;
    markPlaying(null);
    markSpeaking(null);
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
  markSpeaking(null);
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
  TTS.on = TTS.ok;
  const r = await fetch("/api/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: $("modelSel").value, tts: TTS.on, mode: $("modeSel").value,
      clone: realVoice(),
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

// 昵称：进多人模式时向服务端要一个「中文名 + 6 位数字」填进去，省得每个人现起名。
// 只覆盖没动过的那一栏 —— 自己改过名字就一直用自己的。
let nickAuto = "";

async function fillNick(force = false) {
  const box = $("nickName");
  if (!force && box.value && box.value !== nickAuto) return;
  try {
    const d = await (await fetch("/api/name")).json();
    box.value = nickAuto = d.name;
  } catch (_) {}
}

$("nickName").addEventListener("input", () => { nickAuto = ""; });

function syncModeUI() {
  const m = $("modeSel").value;
  $("roleField").hidden = m !== "play";           // 身份只在单人模式下用得上
  $("lobby").hidden = m !== "multi";              // 房间面板只在多人模式出现
  $("btnStart").hidden = m === "multi";           // 多人局由房主在房间里开
  $("btnStop").hidden = m === "multi";
  if (m !== "multi" && R.code) leaveRoom();
  if (m === "multi" && !R.code) fillNick();
}
$("modeSel").addEventListener("change", syncModeUI);
syncModeUI();

$("btnCreate").addEventListener("click", async () => {
  if (R.code) return;                            // 已经在房间里了
  unlockAudio();
  try { enterRoom(await post("/api/room", { name: $("nickName").value })); }
  catch (e) { alert(e.message); }
});

$("btnJoin").addEventListener("click", async () => {
  if (R.code) return;
  unlockAudio();
  const code = $("joinCode").value.trim();
  if (!/^\d{5}$/.test(code)) { alert("房间号是 5 位数字"); return; }
  try { enterRoom(await post(`/api/room/${code}/join`, { name: $("nickName").value })); }
  catch (e) { alert(e.message); }
});

$("btnLeave").addEventListener("click", leaveRoom);

$("btnRoomStart").addEventListener("click", async () => {
  unlockAudio();
  TTS.on = TTS.ok;
  try {
    await post(`/api/room/${R.code}/start`, {
      token: R.token, model: $("modelSel").value, tts: TTS.on, clone: realVoice(),
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

$("voiceMode").addEventListener("change", (e) => {
  try { localStorage.setItem("ww_voice", e.target.value); } catch (_) {}
  TTS.urls.clear();                    // 换了音色，之前那些 blob 不能再用
  stopSpeaking();
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
  REC.ok = !!cfg.asr;
  $("voiceMode").value = cfg.clone_default ? "real" : "fast";
  try {
    const v = localStorage.getItem("ww_voice");
    if (v === "fast" || v === "real") $("voiceMode").value = v;
  } catch (_) {}
  // 朗读没有开关了：后端配了 TTS 就一直念。真正的播放许可靠 unlockAudio()
  // 在「开一局 / 加入房间」那次点击里拿到（浏览器只认用户手势里同步调用的 play）
  TTS.on = TTS.ok;
  if (!TTS.ok) $("voiceField").title = "未配置 DASHSCOPE_API_KEY / ALIYUN_BAILIAN_API_KEY";
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
