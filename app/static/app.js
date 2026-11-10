/* 前端逻辑：会话管理 + 对话（SSE 流式）+ 上传解析 + 录入中断确认 + 报告生成 + 评论 */
const $ = (sel) => document.querySelector(sel);
const state = { sessionId: "", sessions: [] };

/* ---------------- 通用请求 ---------------- */
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = body.detail || body.message || msg;
      if (typeof msg === "object") msg = JSON.stringify(msg);
    } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

/* ---------------- 消息渲染 ---------------- */
function addMsg(role, text, meta = {}) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "我" : "AI";
  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (meta.mock) {
    const tag = document.createElement("span");
    tag.className = "tag mock";
    tag.textContent = "示例数据";
    bubble.appendChild(tag);
  }
  bubble.appendChild(document.createTextNode(text));

  if (meta.citations && meta.citations.length) {
    const cites = document.createElement("div");
    cites.className = "cites";
    cites.appendChild(document.createTextNode("依据："));
    meta.citations.slice(0, 4).forEach((c, i) => {
      const a = document.createElement(c.source_url ? "a" : "span");
      a.textContent = `${i + 1}. ${c.title || c.knowledge_id || "条款"}`;
      if (c.source_url) {
        a.href = c.source_url;
        a.target = "_blank";
        a.rel = "noreferrer";
      }
      cites.appendChild(a);
      cites.appendChild(document.createTextNode("  "));
    });
    bubble.appendChild(cites);
  }

  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  $("#chat").appendChild(wrap);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  return bubble;
}

function sysMsg(text) {
  addMsg("assistant", text);
}

/* ---------------- 会话 ---------------- */
async function loadSessions() {
  const data = await api("/api/chat/sessions?limit=30");
  state.sessions = data.items || [];
  const box = $("#session-list");
  box.innerHTML = "";
  state.sessions.forEach((s) => {
    const el = document.createElement("div");
    el.className = "session-item" + (s.session_id === state.sessionId ? " active" : "");
    el.innerHTML = `<div class="s-title">${escapeHtml(s.title || "新会话")}</div>
      <div class="s-meta"><span>${s.message_count} 条</span><span>${(s.updated_at || "").slice(5, 16)}</span></div>`;
    el.onclick = () => selectSession(s.session_id, s.title);
    box.appendChild(el);
  });
}

async function selectSession(id, title) {
  state.sessionId = id;
  $("#session-title").textContent = title || "会话";
  $("#session-id-pill").textContent = id;
  $("#chat").innerHTML = "";
  const data = await api(`/api/chat/sessions/${id}/messages`);
  if (!data.items.length) sysMsg("会话已创建，可以开始提问了。");
  data.items.forEach((m) => {
    let meta = {};
    try { meta = JSON.parse(m.meta || "{}"); } catch (_) {}
    addMsg(m.role, m.content, meta);
  });
  loadSessions();
}

async function newSession() {
  const data = await api("/api/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "新会话" }),
  });
  await selectSession(data.session_id, data.title);
  sysMsg("已创建新会话。可以直接提问，或使用上方功能：上传材料、录入对手方、生成画像报告。");
}

/* ---------------- 对话 ---------------- */
async function send() {
  if (!state.sessionId) await newSession();
  const text = $("#input").value.trim();
  if (!text) return;
  $("#input").value = "";
  addMsg("user", text);

  const streamOn = $("#stream-toggle").checked;
  const btn = $("#btn-send");
  btn.disabled = true;

  try {
    if (streamOn) {
      await sendStream(text);
    } else {
      const res = await api("/api/chat/messages", {
        method: "POST",
        body: JSON.stringify({ session_id: state.sessionId, content: text }),
      });
      addMsg("assistant", res.reply, { citations: res.citations });
    }
  } catch (err) {
    addMsg("assistant", `请求失败：${err.message}`);
  } finally {
    btn.disabled = false;
    loadSessions();
  }
}

async function sendStream(text) {
  const resp = await fetch("/api/chat/messages/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, content: text }),
  });
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

  const bubble = addMsg("assistant", "");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let full = "";
  let cites = [];

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop();
    for (const chunk of chunks) {
      const evMatch = chunk.match(/^event:\s*(\w+)/m);
      const dataMatch = chunk.match(/^data:\s*(.*)$/m);
      if (!evMatch || !dataMatch) continue;
      let payload = {};
      try { payload = JSON.parse(dataMatch[1]); } catch (_) { continue; }
      if (evMatch[1] === "delta") {
        full += payload.text || "";
        bubble.textContent = full;
        $("#chat").scrollTop = $("#chat").scrollHeight;
      } else if (evMatch[1] === "done") {
        cites = payload.citations || [];
      }
    }
  }
  if (cites.length) {
    bubble.appendChild(document.createTextNode(""));
    addMsg("assistant", "", { citations: cites });
    // 引用气泡内容重复，这里直接删掉占位，仅保留引用块
    const last = [...document.querySelectorAll(".msg.assistant")].pop();
    last.querySelector(".bubble").firstChild.remove();
    last.querySelector(".bubble").textContent = "";
    last.querySelector(".bubble").appendChild(document.createTextNode("（引用见上一条回复）"));
  }
}

/* ---------------- 工具函数 ---------------- */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let modalSeq = 0;

function openModal(title, bodyHtml, onOk, okText = "确认") {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = bodyHtml;
  $("#modal-mask").hidden = false;
  $("#modal-ok").textContent = okText;
  const token = ++modalSeq;  // 如果 onOk 内部又开了新弹窗，外层就不能把它关掉
  $("#modal-ok").onclick = async () => {
    try {
      await onOk();
      if (token === modalSeq) closeModal();
    } catch (err) {
      alert(err.message);
    }
  };
}
function closeModal() {
  $("#modal-mask").hidden = true;
  modalSeq++;  // 关闭后，任何在途的旧回调不再影响新弹窗
}

/* ---------------- 上传解析 ---------------- */
function pickFile() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".pdf,.png,.jpg,.jpeg,.webp,.bmp";
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("session_id", state.sessionId);
    $("#panel-title").textContent = "文档解析";
    $("#panel-body").innerHTML = `<p class="hint">正在解析 ${escapeHtml(file.name)}…</p>`;
    try {
      const res = await fetch("/api/documents/parse", { method: "POST", body: fd }).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
      $("#panel-body").innerHTML = `
        <div class="card">
          <h4>${escapeHtml(res.filename)}</h4>
          <div class="kv"><span>页数</span><b>${res.pages}</b></div>
          <div class="kv"><span>表格数</span><b>${res.tables}</b></div>
          <div class="kv"><span>解析方式</span><b>${res.parser}</b></div>
          <div class="kv"><span>文本量</span><b>${res.text_chars} 字符</b></div>
          <div class="kv"><span>缓存命中</span><b>${res.cached ? "是" : "否"}</b></div>
        </div>
        <pre class="md">${escapeHtml(res.markdown || "（无文本）").slice(0, 3000)}</pre>`;
      addMsg("assistant", `已解析《${res.filename}》：${res.pages} 页、${res.tables} 个表格，解析方式 ${res.parser}。可用于生成报告时引用。`);
    } catch (err) {
      $("#panel-body").innerHTML = `<p class="hint">解析失败：${escapeHtml(err.message)}</p>`;
    }
  };
  input.click();
}

/* ---------------- 对话录入对手方（中断确认） ---------------- */
function startIntake() {
  openModal("录入交易对手方", `
    <p class="hint">用一段话描述对手方，系统抽取字段后请你确认。</p>
    <div class="field"><label>对手方描述</label>
      <textarea id="intake-text" rows="5" placeholder="例如：中国银行，统一社会信用代码 911000001000013428，主体评级 AAA，注册资本 2943 亿元，成立于 1983 年，法定代表人某某"></textarea>
    </div>`, async () => {
    const text = $("#intake-text").value.trim();
    if (!text) throw new Error("请填写描述");
    const res = await api("/api/counterparty/intake", {
      method: "POST",
      body: JSON.stringify({ session_id: state.sessionId, text }),
    });
    showIntakeConfirm(res);
  }, "抽取信息");
}

function showIntakeConfirm(res) {
  const fields = res.extracted || {};
  const labels = {
    full_name: "全称", short_name: "简称", credit_code: "统一社会信用代码", inst_type: "机构类型",
    reg_capital: "注册资本", found_date: "成立日期", legal_person: "法定代表人",
    issuer_rating: "主体评级", issuer_outlook: "评级展望", agency: "评级机构",
  };
  const rows = Object.keys(labels).map((k) => `
    <div class="field"><label>${labels[k]}</label>
      <input id="f-${k}" value="${escapeHtml(fields[k] || "")}" /></div>`).join("");
  const missing = (res.missing || []).slice(0, 6).join("、");

  openModal("请确认交易对手方信息", `
    <p class="hint">置信度 ${(res.confidence * 100).toFixed(0)}%｜缺失字段：${escapeHtml(missing || "无")}。可直接修改后保存。</p>
    ${rows}`, async () => {
    const patched = {};
    Object.keys(labels).forEach((k) => { patched[k] = $(`#f-${k}`).value.trim(); });
    const out = await api("/api/counterparty/intake/confirm", {
      method: "POST",
      body: JSON.stringify({ thread_id: res.thread_id, action: "confirm", patched_fields: patched }),
    });
    addMsg("assistant", `已保存对手方「${out.record?.full_name || patched.full_name}」，ID：${out.cp_id}。`);
  }, "确认入库");
}

/* ---------------- 生成报告 ---------------- */
function generateReport() {
  openModal("生成对手方画像报告", `
    <div class="field"><label>对手方名称</label>
      <input id="rp-name" value="中国银行" /></div>
    <div class="field"><label>最大校验重试次数</label>
      <input id="rp-retry" type="number" value="2" min="0" max="3" /></div>
    <p class="hint">流程：MCP 取数 → 读缓存 → 生成 → 校验（有问题回炉重生成）。</p>`, async () => {
    const name = $("#rp-name").value.trim();
    const maxRetry = parseInt($("#rp-retry").value || "2", 10);
    if (!name) throw new Error("请填写对手方名称");

    $("#panel-title").textContent = "生成中…";
    $("#panel-body").innerHTML = `<p class="hint">正在运行四 Agent 协作流程，请稍候（约 30–90 秒）。</p>`;
    const res = await api("/api/reports/generate", {
      method: "POST",
      body: JSON.stringify({ counterparty: name, session_id: state.sessionId, max_retry: maxRetry }),
    });
    renderReport(res);
    addMsg("assistant", `已生成《${name}》对手方画像报告（状态：${res.status}，校验轮次 ${res.retries}）。`);
  }, "开始生成");
}

function renderReport(res) {
  $("#panel-title").textContent = "对手方画像报告";
  const scoreRows = Object.entries(res.scores || {}).map(([k, v]) =>
    `<div class="kv"><span>${escapeHtml(k)}</span><b>${Number(v).toFixed(0)}</b></div>`).join("");
  const issueRows = (res.issues || []).map((i) =>
    `<div class="issue ${i.level}">[${escapeHtml(i.level)}] ${escapeHtml(i.field)}：${escapeHtml(i.problem)}<br/>
     <span class="hint">建议：${escapeHtml(i.fix)}</span></div>`).join("");
  const traceRows = (res.trace || []).map((t) =>
    `<div class="kv"><span>${escapeHtml(t.node)}</span><b>${escapeHtml(String(t.elapsed_ms ?? ""))}</b></div>`).join("");

  $("#panel-body").innerHTML = `
    <div class="card">
      <h4>${escapeHtml(res.counterparty)}</h4>
      <div class="kv"><span>状态</span><b>${escapeHtml(res.status)}</b></div>
      <div class="kv"><span>数据来源</span><b>${res.data_source === "mock" ? "示例数据" : "MCP"}</b></div>
      <div class="kv"><span>校验轮次</span><b>${res.retries}</b></div>
      ${res.pdf_url ? `<a class="btn block primary" style="margin-top:8px;text-align:center;text-decoration:none" href="${res.pdf_url}" target="_blank">下载 PDF 报告</a>` : ""}
    </div>
    <div class="card"><h4>五维评分</h4>${scoreRows}</div>
    ${issueRows ? `<div class="card"><h4>校验意见</h4>${issueRows}</div>` : ""}
    <div class="card"><h4>执行轨迹</h4>${traceRows}</div>
    <div class="card"><h4>报告正文</h4><pre class="md">${escapeHtml(res.markdown || "")}</pre></div>`;
}

/* ---------------- 生成评论 ---------------- */
function generateComment() {
  openModal("生成对手方评论", `
    <div class="field"><label>对手方名称（可留空，仅在对话中评论）</label>
      <input id="cm-name" value="中国银行" /></div>
    <div class="field"><label>风格（写入长期记忆，默认 30 天有效）</label>
      <input id="cm-style" value="稳健审慎、结论先行" /></div>
    <div class="field"><label>角色视角</label>
      <input id="cm-role" value="资金交易员" /></div>
    <div class="field"><label>补充要求</label>
      <textarea id="cm-extra" rows="2" placeholder="例如：重点提示同业存单到期压力"></textarea></div>`, async () => {
    const name = $("#cm-name").value.trim();
    let cpId = "";
    if (name) {
      const list = await api(`/api/counterparty?keyword=${encodeURIComponent(name)}`);
      cpId = (list.items && list.items[0] && list.items[0].cp_id) || "";
    }
    const res = await api("/api/counterparty/comment", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        cp_id: cpId,
        style: $("#cm-style").value.trim(),
        role: $("#cm-role").value.trim(),
        extra: $("#cm-extra").value.trim(),
      }),
    });
    addMsg("assistant", res.comment, { mock: res.degraded });
    $("#panel-title").textContent = "对手方评论";
    $("#panel-body").innerHTML = `
      <div class="card"><h4>评论</h4><div>${escapeHtml(res.comment)}</div></div>
      <div class="card"><h4>记忆使用情况</h4>
        <div class="kv"><span>短期轮次</span><b>${res.memory_used.short_turns}</b></div>
        <div class="kv"><span>长期记忆条数</span><b>${(res.memory_used.long_terms || []).length}</b></div>
        <div class="kv"><span>风格</span><b>${escapeHtml(res.style_used)}</b></div>
        <div class="kv"><span>角色</span><b>${escapeHtml(res.role_used)}</b></div>
      </div>`;
  }, "生成评论");
}

/* ---------------- 健康检查 ---------------- */
async function checkHealth() {
  try {
    const h = await api("/health");
    const c = h.components;
    const degraded = [c.llm.degraded, c.chroma.degraded, c.redis.degraded, c.oracle.degraded, c.mcp.token_placeholder]
      .filter(Boolean).length;
    const dot = $("#dot-health");
    dot.className = `dot ${degraded > 0 ? "warn" : "ok"}`;
    $("#health-text").textContent = `依赖正常${degraded ? `（${degraded} 项降级）` : ""}｜知识库 ${h.knowledge.chunks} chunks`;
  } catch (err) {
    $("#dot-health").className = "dot";
    $("#health-text").textContent = "服务未连接";
  }
}

/* ---------------- 绑定 ---------------- */
$("#btn-new-session").onclick = () => newSession().catch((e) => alert(e.message));
$("#btn-send").onclick = send;
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("#btn-refresh").onclick = () => { checkHealth(); loadSessions(); };
$("#modal-cancel").onclick = closeModal;
$("#modal-mask").onclick = (e) => { if (e.target.id === "modal-mask") closeModal(); };

document.querySelectorAll(".chip").forEach((chip) => {
  chip.onclick = () => {
    const action = chip.dataset.action;
    if (action === "qa") { $("#input").focus(); $("#input").value = "什么是货银对付（DVP）？"; }
    if (action === "upload") pickFile();
    if (action === "intake") startIntake();
    if (action === "report") generateReport();
    if (action === "comment") generateComment();
  };
});

(async function init() {
  checkHealth();
  await loadSessions().catch(() => {});
  if (state.sessions.length) {
    const first = state.sessions[0];
    await selectSession(first.session_id, first.title);
  } else {
    await newSession();
  }
})();
