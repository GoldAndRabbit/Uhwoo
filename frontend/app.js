const $ = (id) => document.getElementById(id);
const ROLE_ORDER = { "狼人": 0, "预言家": 1, "守卫": 2, "平民": 3 };

const S = {
  state: null,
  events: [],
  calls: [],
  filter: null,        // null=全部, 数字=座位, "sys"=系统
  es: null,
  timer: null,
  readonly: false,
};

/* ---------------- 工具 ---------------- */
const kchars = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n));
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const roleOf = (seat) => S.state?.players?.find((p) => p.seat === seat)?.role || "";
const isPlay = () => S.state?.mode === "play";
const audienceLabel = (a) =>
  a === "all" ? null : "仅 " + a.map((s) => s + "号").join("/");

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
  $("tabBadge").textContent = st.calls;
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
  setRun(`回放 werewolf ${gid} · ${d.state.winner || "中断"} · ${d.state.calls} 次调用`, true);
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
      ? `<div class="empty">游玩模式下这里只显示你自己的回合，别人的上下文不给你看。</div>`
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

function rowHTML(ev) {
  const only = audienceLabel(ev.audience);
  const onlyTag = only ? `<span class="only">${only}</span>` : "";
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
    return `<div class="row"><span class="badge r-${role || "平民"}">${seat}号${role ? " " + role : ""}</span>
      <div class="bubble say" data-seat="${seat}" data-text="${esc(body)}"
           title="点一下重听">${esc(body)}</div></div>`;
  }
  if (ev.kind === "vote")
    return `<div class="row">${onlyTag}<div class="bubble plain vote">${esc(ev.text)}</div></div>`;
  if (ev.kind === "result")
    return `<div class="row"><div class="bubble result">${esc(ev.text)}</div></div>`;
  if (ev.kind === "night_action") {
    const role = ev.seat ? roleOf(ev.seat) : "";
    const badge = ev.seat
      ? `<span class="badge r-${role || "平民"}">${ev.seat}号${role ? " " + role : ""}</span>` : "";
    return `<div class="row">${badge}${onlyTag}
      <div class="bubble ${only ? "wolfnight" : ""}">${esc(ev.text)}</div></div>`;
  }
  return `<div class="row">${onlyTag}<div class="bubble plain">${esc(ev.text)}</div></div>`;
}

function setRun(text, done) {
  $("runbar").hidden = !text;            // 没在跑就整条不出现
  $("runText").textContent = text || "";
  $("runbar").classList.toggle("done", !!done);
}

function tickRun() {
  const st = S.state;
  if (!st) { setRun("", true); return; }
  if (st.status === "running") {
    setRun(`运行中… ${st.elapsed.toFixed(1)}s · ${st.calls} 次调用 · 第${st.round}${st.phase === "night" ? "夜" : "天"}`, false);
    st.elapsed += 0.1;
  } else if (st.status === "finished")
    setRun(`本局结束：${st.winner}胜利 · ${st.elapsed.toFixed(1)}s · ${st.calls} 次调用`, true);
  else if (st.status === "stopped")
    setRun(`已停止 · ${st.elapsed.toFixed(1)}s · ${st.calls} 次调用`, true);
}

/* ---------------- 事件流 ---------------- */
function renderAll() {
  renderPlayers();
  renderCalls();
  renderLog();
  const running = S.state?.status === "running" && !S.readonly;
  $("btnStart").disabled = running;
  $("btnStop").disabled = !running;
}

function connect() {
  if (S.es) S.es.close();
  S.es = new EventSource("/api/stream");
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
    if (msg.state) S.state = msg.state;
    if (msg.type === "event") {
      S.events.push(msg.data);
      if (msg.data.kind === "speech")
        speak(msg.data.seat, msg.data.text.replace(/^[^：]*：/, ""));
      renderLog();
      const box = $("log").parentElement;
      box.scrollTop = box.scrollHeight;
    } else if (msg.type === "call") {
      S.calls.push(msg.data);
      renderCalls();
    } else if (msg.type === "prompt") {
      renderAsk(msg.data);
    } else if (msg.type === "thinking") {
      setRun(`${msg.title} 思考中…`, false);
    }
    renderPlayers();
    if (["finished", "stopped"].includes(S.state?.status)) { loadHistory(); renderAll(); }
  };
  S.es.onerror = () => { /* 浏览器会自动重连 */ };
}

/* ---------------- 游玩模式：轮到你 ---------------- */
let askTimer = null;

function renderAsk(p) {
  const box = $("ask");
  if (!p) { box.hidden = true; clearInterval(askTimer); return; }
  box.hidden = false;
  $("askTitle").textContent = p.title + "　轮到你";
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
    $("askBody").innerHTML = q +
      `<div class="seats" id="askSeats">${seats}</div>
       <input type="text" id="askReason" placeholder="理由（可留空）">
       <button class="send" id="askSend" disabled>确定</button>`;
    let chosen = null;
    $("askSeats").querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => {
        chosen = Number(b.dataset.seat);
        $("askSeats").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
        b.classList.add("on");
        $("askSend").disabled = false;
      }));
    $("askSend").addEventListener("click", () =>
      submitAnswer({ target: chosen, reason: $("askReason").value }));
  }

  clearInterval(askTimer);
  askTimer = setInterval(() => {
    const left = Math.max(0, p.timeout - (Date.now() / 1000 - p.asked_at));
    $("askLeft").textContent = left > 0 ? `剩 ${Math.ceil(left)}s` : "已超时，交给模型代打";
    if (left <= 0) clearInterval(askTimer);
  }, 200);
  if (isMobile()) setTab("log");
}

async function submitAnswer(payload) {
  const btn = $("askSend");
  if (btn) btn.disabled = true;
  const r = await fetch("/api/answer", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    alert((await r.json()).detail);
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

/* ---------------- 朗读 ---------------- */
const TTS = { on: false, ok: false, q: [], playing: false, audio: new Audio(), urls: new Map(),
              unlocked: false, blocked: false };
TTS.audio.preload = "auto";
TTS.audio.playsInline = true;

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
document.addEventListener("pointerdown", () => { unlockAudio(); if (TTS.blocked) pump(); },
                          { capture: true });

async function audioURL(seat, text) {
  const key = seat + "|" + text;
  if (TTS.urls.has(key)) return TTS.urls.get(key);
  const p = fetch("/api/tts", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seat, text }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    return URL.createObjectURL(await r.blob());
  });
  TTS.urls.set(key, p);
  return p;
}

function speak(seat, text) {
  if (!TTS.on || !TTS.ok || !text) return;
  TTS.q.push({ seat, text });
  pump();
}

async function pump() {
  if (TTS.playing || !TTS.q.length) return;
  TTS.playing = true;
  const { seat, text } = TTS.q.shift();
  if (TTS.q.length) audioURL(TTS.q[0].seat, TTS.q[0].text).catch(() => {});  // 预取下一句
  try {
    TTS.audio.src = await audioURL(seat, text);
    await TTS.audio.play();
    TTS.blocked = false;
    await new Promise((res) => { TTS.audio.onended = res; TTS.audio.onerror = res; });
  } catch (e) {
    if (e && e.name === "NotAllowedError") {       // 被自动播放策略拦了，等下一次点击再续
      TTS.blocked = true;
      TTS.q.unshift({ seat, text });
      TTS.playing = false;
      return;
    }
  }
  TTS.playing = false;
  pump();
}

function stopSpeaking() {
  TTS.q.length = 0;
  TTS.audio.pause();
  TTS.audio.currentTime = 0;
  TTS.playing = false;
}

/* ---------------- 交互 ---------------- */
$("btnStart").addEventListener("click", async () => {
  unlockAudio();                                   // 必须在 await 之前，手势才有效
  $("btnStart").disabled = true;
  S.readonly = false;
  stopSpeaking();
  TTS.on = TTS.ok && $("ttsOn").checked;          // 以点开一局这一刻的勾选为准
  const r = await fetch("/api/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: $("modelSel").value, tts: TTS.on, mode: $("modeSel").value }),
  });
  if (!r.ok) { alert((await r.json()).detail); $("btnStart").disabled = false; return; }
  const d = await r.json();
  S.state = d.state; S.events = []; S.calls = []; S.filter = null;
  renderAsk(null);
  renderAll();
  connect();
  if (isMobile()) setTab("log");            // 手机上开局后直接看事件流
});

$("btnStop").addEventListener("click", async () => {
  $("btnStop").disabled = true;
  renderAsk(null);
  stopSpeaking();
  await fetch("/api/stop", { method: "POST" });
});

$("ttsOn").addEventListener("change", (e) => {
  if (e.target.checked) unlockAudio();
  TTS.on = TTS.ok && e.target.checked;
  try { localStorage.setItem("ww_tts", TTS.on ? "1" : "0"); } catch (_) {}
  if (!TTS.on) stopSpeaking();                    // 中途取消勾选就立刻闭麦
});

// 点发言气泡可以重听这一句
$("log").addEventListener("click", (e) => {
  const b = e.target.closest(".bubble.say");
  if (!b || !TTS.ok) return;
  unlockAudio();
  stopSpeaking();
  TTS.on = true;
  $("ttsOn").checked = true;
  speak(Number(b.dataset.seat), b.dataset.text);
});

$("btnNew").addEventListener("click", () => $("btnStart").click());
$("filterClear").addEventListener("click", () => { S.filter = null; renderPlayers(); renderCalls(); });

(async function init() {
  const cfg = await (await fetch("/api/config")).json();
  $("modelSel").innerHTML = cfg.models
    .map((m) => `<option value="${m}"${m === cfg.model ? " selected" : ""}>${m}</option>`).join("");
  $("modelNote").textContent = cfg.note;
  TTS.ok = !!cfg.tts;
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
    if (snap.state.status === "running") $("modeSel").value = snap.state.mode;
    renderAll();
    if (snap.state.status === "running") connect();
  } else {
    renderLog(); renderCalls();
  }
  await loadHistory();
  const hash = location.hash.replace("#", "");
  setTab(["side", "ctx", "log"].includes(hash) ? hash
         : S.state && S.state.status === "running" ? "log" : "side");
  S.timer = setInterval(tickRun, 100);
})();
