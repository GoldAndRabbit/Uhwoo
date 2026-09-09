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
    return `<div class="prow${active}${dead}" data-seat="${p.seat}">
      <span class="pname">${p.seat}号</span>
      <span class="rtag r-${p.role}">${p.role}</span>
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
    })
  );
  const aliveN = st.players.filter((p) => p.alive).length;
  $("aliveCount").textContent = `${aliveN}/${st.players.length} 存活`;
  $("callCount").textContent = st.calls;
  $("tabBadge").textContent = st.calls;
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
    box.innerHTML = `<div class="empty">还没有调用。点「开一局」开始。</div>`;
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
    `<div class="empty">点左边「开一局」，这里会逐条出现夜里的行动和白天的发言。</div>`;
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
    return `<div class="row"><span class="badge r-${role}">${seat}号 ${role}</span>
      <div class="bubble">${esc(body)}</div></div>`;
  }
  if (ev.kind === "vote")
    return `<div class="row">${onlyTag}<div class="bubble plain vote">${esc(ev.text)}</div></div>`;
  if (ev.kind === "result")
    return `<div class="row"><div class="bubble result">${esc(ev.text)}</div></div>`;
  if (ev.kind === "night_action") {
    const role = ev.seat ? roleOf(ev.seat) : "";
    const badge = ev.seat ? `<span class="badge r-${role}">${ev.seat}号 ${role}</span>` : "";
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
      renderAll(); return;
    }
    if (msg.state) S.state = msg.state;
    if (msg.type === "event") {
      S.events.push(msg.data);
      renderLog();
      $("log").parentElement.scrollTop = $("log").parentElement.scrollHeight;
    } else if (msg.type === "call") {
      S.calls.push(msg.data);
      renderCalls();
    } else if (msg.type === "thinking") {
      setRun(`${msg.title} 思考中…`, false);
    }
    renderPlayers();
    if (["finished", "stopped"].includes(S.state?.status)) { loadHistory(); renderAll(); }
  };
  S.es.onerror = () => { /* 浏览器会自动重连 */ };
}

/* ---------------- 交互 ---------------- */
$("btnStart").addEventListener("click", async () => {
  $("btnStart").disabled = true;
  S.readonly = false;
  const r = await fetch("/api/start", { method: "POST" });
  if (!r.ok) { alert((await r.json()).detail); $("btnStart").disabled = false; return; }
  const d = await r.json();
  S.state = d.state; S.events = []; S.calls = []; S.filter = null;
  renderAll();
  connect();
});

$("btnStop").addEventListener("click", async () => {
  $("btnStop").disabled = true;
  await fetch("/api/stop", { method: "POST" });
});

$("btnNew").addEventListener("click", () => $("btnStart").click());
$("filterClear").addEventListener("click", () => { S.filter = null; renderPlayers(); renderCalls(); });

(async function init() {
  const cfg = await (await fetch("/api/config")).json();
  $("modelNote").textContent = (cfg.use_api ? "模型：" : "") + cfg.model;
  const snap = await (await fetch("/api/snapshot")).json();
  if (!snap.empty) {
    S.state = snap.state; S.events = snap.events; S.calls = snap.calls;
    renderAll();
    if (snap.state.status === "running") connect();
  } else {
    renderLog(); renderCalls();
  }
  await loadHistory();
  S.timer = setInterval(tickRun, 100);
})();
