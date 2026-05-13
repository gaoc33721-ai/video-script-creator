const DATASETS = {
  specs: { label: "规格参数", accept: ".xlsx,.xls,.csv", hint: "型号、品类、容量、能效、噪音、尺寸等结构化参数" },
  marketing: { label: "营销素材/FAQ", accept: ".xlsx,.xls,.csv", hint: "卖点、长文案、导购话术、异议处理" },
  competitors: { label: "竞品资料/卖点", accept: ".xlsx,.xls,.csv", hint: "竞品品牌、品类、卖点、标题、描述、链接和参数" },
  documents: { label: "文档资料", accept: ".xlsx,.xls,.csv,.pdf", hint: "认证、说明书、培训材料或业务需求文档" },
};

const DATASET_ORDER = ["specs", "marketing", "competitors", "documents"];
const SOURCE_LABELS = {
  specs: "规格",
  marketing: "营销",
  competitors: "竞品",
  documents: "文档",
};

const state = {
  authEnabled: true,
  authToken: "",
  role: "",
  sessions: [],
  activeSessionId: "",
  selectedModel: sessionStorage.getItem("fridge_selected_model") || "",
  options: { models: [], model_cards: [], datasets: {} },
  pending: null,
  busy: false,
};

const $ = (id) => document.getElementById(id);

function setMessage(id, text, kind = "") {
  const node = $(id);
  if (!node) return;
  node.textContent = text || "";
  node.className = `message ${kind}`.trim();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

function authHeaders(headers = {}) {
  const next = new Headers(headers);
  if (state.authToken) {
    next.set("Authorization", `Bearer ${state.authToken}`);
  }
  return next;
}

async function fridgeApi(path, options = {}) {
  const headers = authHeaders(options.headers || {});
  const isForm = options.body instanceof FormData;
  if (options.body && !isForm && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) {
    let message = response.statusText || "请求失败";
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch (_) {}
    if (response.status === 401) {
      clearAuth(message);
    }
    throw new Error(message);
  }
  if (response.status === 204) return {};
  const contentType = response.headers.get("Content-Type") || "";
  return contentType.includes("application/json") ? response.json() : response.text();
}

function showLogin(message = "") {
  $("loginView").classList.remove("hidden");
  $("appView").classList.add("hidden");
  setMessage("loginMessage", message, message ? "error" : "");
  setTimeout(() => $("passwordInput").focus(), 0);
}

function showApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
}

function clearAuth(message = "请重新登录。") {
  state.authToken = "";
  state.role = "";
  state.sessions = [];
  state.activeSessionId = "";
  sessionStorage.removeItem("fridge_auth_token");
  showLogin(message);
}

async function submitLogin(event) {
  event.preventDefault();
  const password = $("passwordInput").value.trim();
  if (state.authEnabled && !password) {
    setMessage("loginMessage", "请输入访问密码。", "error");
    return;
  }
  $("loginButton").disabled = true;
  setMessage("loginMessage", "正在登录...");
  try {
    const response = await fetch("/api/fridge/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!response.ok) {
      let message = response.statusText;
      try {
        const body = await response.json();
        message = body.detail || message;
      } catch (_) {}
      throw new Error(message);
    }
    const data = await response.json();
    state.authToken = password;
    state.role = data.role || "user";
    sessionStorage.setItem("fridge_auth_token", password);
    $("passwordInput").value = "";
    showApp();
    await bootstrap();
  } catch (error) {
    setMessage("loginMessage", error.message, "error");
  } finally {
    $("loginButton").disabled = false;
  }
}

async function initialize() {
  try {
    const response = await fetch("/api/fridge/auth/status", { credentials: "same-origin" });
    const status = response.ok ? await response.json() : { enabled: true };
    state.authEnabled = Boolean(status.enabled);
    if (!state.authEnabled) {
      state.role = "admin";
      showApp();
      await bootstrap();
      return;
    }
    const cachedToken = sessionStorage.getItem("fridge_auth_token") || "";
    if (cachedToken) {
      state.authToken = cachedToken;
      showApp();
      try {
        await bootstrap();
        return;
      } catch (error) {
        clearAuth(error.message);
        return;
      }
    }
    showLogin();
  } catch (error) {
    showLogin(error.message || "无法连接服务。");
  }
}

async function bootstrap() {
  await Promise.all([loadOptions(), loadSessions()]);
  if (!state.sessions.length) {
    await createSession("新会话");
  } else if (!state.activeSessionId || !state.sessions.some((item) => item.id === state.activeSessionId)) {
    state.activeSessionId = sessionStorage.getItem("fridge_active_session") || state.sessions[0].id;
    if (!state.sessions.some((item) => item.id === state.activeSessionId)) {
      state.activeSessionId = state.sessions[0].id;
    }
  }
  renderAll();
}

async function refreshWorkspace() {
  setMessage("chatMessage", "正在刷新...");
  try {
    await bootstrap();
    setMessage("chatMessage", "已刷新。", "ok");
  } catch (error) {
    setMessage("chatMessage", error.message, "error");
  }
}

async function loadOptions() {
  const data = await fridgeApi("/api/fridge/options");
  state.options = data;
  state.role = data.role || state.role || "user";
}

async function loadSessions() {
  const data = await fridgeApi("/api/fridge/sessions");
  state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
}

async function createSession(title = "新会话") {
  const session = await fridgeApi("/api/fridge/sessions", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  upsertSession(session);
  state.activeSessionId = session.id;
  sessionStorage.setItem("fridge_active_session", session.id);
  renderAll();
  return session;
}

function upsertSession(session) {
  const index = state.sessions.findIndex((item) => item.id === session.id);
  if (index >= 0) {
    state.sessions[index] = session;
  } else {
    state.sessions.unshift(session);
  }
  state.sessions.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
}

function activeSession() {
  return state.sessions.find((item) => item.id === state.activeSessionId) || null;
}

function friendlyErrorMessage(error) {
  const message = String(error?.message || "请求失败");
  if (/failed to fetch|networkerror|load failed|network request failed/i.test(message)) {
    return "网络连接中断。请稍后重试，或点击刷新同步最新会话。";
  }
  return message;
}

async function reconcileSentQuestion(sessionId, question) {
  try {
    await loadSessions();
    const session = state.sessions.find((item) => item.id === sessionId);
    if (!session) return false;
    state.activeSessionId = session.id;
    sessionStorage.setItem("fridge_active_session", session.id);
    const messages = Array.isArray(session.messages) ? session.messages : [];
    let userIndex = -1;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const item = messages[index];
      if (item.role === "user" && String(item.content || "").trim() === question) {
        userIndex = index;
        break;
      }
    }
    const hasAnswer = userIndex >= 0 && messages.slice(userIndex + 1).some((item) => item.role === "assistant" && item.content);
    if (!hasAnswer) return false;
    setMessage("chatMessage", "网络回包中断，已从服务端同步刚生成的回答。", "ok");
    return true;
  } catch (_) {
    return false;
  }
}

function renderAll() {
  renderRole();
  renderSummary();
  renderModelSelect();
  renderModelCard();
  renderDatasetStatus();
  renderUploadRows();
  renderQuickQuestions();
  renderSessions();
  renderMessages();
}

function renderRole() {
  const badge = $("roleBadge");
  const role = state.role || "user";
  badge.textContent = role === "admin" ? "管理员" : "成员";
  badge.className = `role-badge ${role === "admin" ? "admin" : "user"}`;
  $("adminPanel").classList.toggle("hidden", role !== "admin");
}

function renderSummary() {
  const datasets = state.options.datasets || {};
  const loaded = DATASET_ORDER.filter((name) => datasets[name]?.loaded).length;
  $("summaryPills").innerHTML = [
    ["型号", state.options.model_count || 0],
    ["系列", state.options.series_count || 0],
    ["知识库", `${loaded}/${DATASET_ORDER.length}`],
  ]
    .map(([label, value]) => `<span class="pill">${escapeHtml(label)} ${escapeHtml(value)}</span>`)
    .join("");
}

function renderModelSelect() {
  const models = Array.isArray(state.options.models) ? state.options.models : [];
  if (state.selectedModel && !models.includes(state.selectedModel)) {
    state.selectedModel = "";
  }
  $("modelSelect").innerHTML = [
    '<option value="">不限定型号</option>',
    ...models.map((model) => `<option value="${escapeAttr(model)}">${escapeHtml(model)}</option>`),
  ].join("");
  $("modelSelect").value = state.selectedModel;
}

function selectedModelCard() {
  if (!state.selectedModel) return null;
  const cards = Array.isArray(state.options.model_cards) ? state.options.model_cards : [];
  return cards.find((card) => card.model === state.selectedModel) || { model: state.selectedModel };
}

function renderModelCard() {
  const card = selectedModelCard();
  if (!card) {
    $("modelCard").innerHTML = `
      <div class="fact">
        <span>当前范围</span>
        <strong>不限定型号</strong>
      </div>
      <p class="message">选择具体型号后，问答会优先召回该型号的规格、卖点和竞品素材。</p>
    `;
    return;
  }
  const facts = Array.isArray(card.fields) && card.fields.length
    ? card.fields
    : [
        { label: "型号", value: card.model },
        { label: "品牌", value: card.brand },
        { label: "产品类型", value: card.product_type },
        { label: "系列", value: card.series },
        { label: "市场", value: card.market },
        { label: "洗涤容量", value: card.washing_capacity_kg },
        { label: "烘干容量", value: card.drying_capacity_kg },
        { label: "能效", value: card.energy_rating || card.energy_rating_wash || card.energy_rating_dry },
        { label: "水效/冷凝效率", value: card.water_rating },
        { label: "噪音", value: card.noise_db },
      ].filter((item) => item.value);
  $("modelCard").innerHTML = `
    <div class="model-facts">
      ${facts.map((item) => `<div class="fact"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>`).join("")}
    </div>
  `;
}

function renderDatasetStatus() {
  const datasets = state.options.datasets || {};
  $("datasetStatus").innerHTML = DATASET_ORDER.map((name) => {
    const info = datasets[name] || {};
    const meta = info.meta || {};
    const loaded = Boolean(info.loaded);
    const rowText = loaded ? `${info.row_count || 0} 行` : "未入库";
    const fileText = meta.file_name ? ` · ${meta.file_name}` : "";
    return `
      <div class="dataset-row ${loaded ? "loaded" : "empty"}">
        <span>${escapeHtml(DATASETS[name].label)}</span>
        <strong>${escapeHtml(rowText)}</strong>
        <span>${escapeHtml(formatDateTime(meta.updated_at) || "")}${escapeHtml(fileText)}</span>
      </div>
    `;
  }).join("");
}

function renderUploadRows() {
  if (state.role !== "admin") return;
  $("uploadRows").innerHTML = DATASET_ORDER.map((name) => {
    const item = DATASETS[name];
    return `
      <div class="upload-row">
        <div class="upload-copy">
          <strong>${escapeHtml(item.label)}</strong>
          <span>${escapeHtml(item.hint)}</span>
        </div>
        <label class="file-button">
          上传
          <input type="file" data-dataset="${escapeAttr(name)}" accept="${escapeAttr(item.accept)}" />
        </label>
      </div>
    `;
  }).join("");
}

function quickQuestionTemplates() {
  const prefix = state.selectedModel ? `${state.selectedModel} ` : "";
  return [
    `${prefix}核心卖点和适用场景怎么概括？`,
    `${prefix}和竞品卖点相比有哪些可主推优势？`,
    `${prefix}给我一段适合渠道销售的 30 秒话术。`,
    `${prefix}有哪些参数或认证不能过度承诺？`,
    `${prefix}整理一版短视频脚本卖点提纲。`,
  ];
}

function renderQuickQuestions() {
  $("quickQuestions").innerHTML = quickQuestionTemplates()
    .map((question) => `<button type="button" class="quick-button" data-question="${escapeAttr(question)}"${state.busy ? " disabled" : ""}>${escapeHtml(question)}</button>`)
    .join("");
}

function renderSessions() {
  const query = $("sessionSearch").value.trim().toLowerCase();
  const sessions = state.sessions.filter((session) => {
    if (!query) return true;
    const messages = (session.messages || []).map((item) => item.content || "").join(" ");
    return `${session.title || ""} ${messages}`.toLowerCase().includes(query);
  });
  $("sessionList").innerHTML = sessions.map((session) => {
    const active = session.id === state.activeSessionId;
    const count = Array.isArray(session.messages) ? session.messages.length : 0;
    return `
      <article class="session-item ${active ? "active" : ""}" data-session-id="${escapeAttr(session.id)}">
        <div class="session-row">
          <span class="session-title">${escapeHtml(session.title || "新会话")}</span>
          <span class="session-time">${escapeHtml(formatDateTime(session.updated_at))}</span>
        </div>
        <div class="session-row">
          <span class="session-time">${count} 条消息</span>
          <span class="session-actions">
            <button type="button" class="session-action" data-action="favorite" data-session-id="${escapeAttr(session.id)}">${session.favorite ? "已收藏" : "收藏"}</button>
            <button type="button" class="session-action danger" data-action="delete" data-session-id="${escapeAttr(session.id)}">删除</button>
          </span>
        </div>
      </article>
    `;
  }).join("") || '<p class="message">没有匹配的历史会话。</p>';
}

function renderMessages() {
  const session = activeSession();
  const messages = session ? [...(session.messages || [])] : [];
  if (state.pending) {
    messages.push({ id: "pending-user", role: "user", content: state.pending.text, created_at: state.pending.created_at });
    messages.push({ id: "pending-assistant", role: "assistant", content: "", created_at: state.pending.created_at, pending: true });
  }
  $("emptyChat").classList.toggle("hidden", messages.length > 0);
  $("messageList").innerHTML = messages.map(renderMessage).join("");
  $("messageList").scrollTop = $("messageList").scrollHeight;
}

function renderMessage(message) {
  const role = message.role === "user" ? "user" : "assistant";
  const body = message.pending
    ? '<div class="typing" aria-label="正在生成"><span></span><span></span><span></span></div>'
    : role === "assistant"
      ? markdownToHtml(message.content || "")
      : `<p>${escapeHtml(message.content || "")}</p>`;
  const sources = role === "assistant" && message.sources ? renderSources(message.sources) : "";
  const feedback = role === "assistant" && !message.pending ? renderFeedback(message.id) : "";
  return `
    <article class="chat-message ${role}">
      <div class="message-meta">
        <span>${role === "user" ? "你" : "助手"}</span>
        <span>${escapeHtml(formatDateTime(message.created_at))}</span>
      </div>
      <div class="message-bubble">
        <div class="message-body">${body}</div>
      </div>
      ${sources}
      ${feedback}
    </article>
  `;
}

function renderSources(sources) {
  return `
    <div class="source-chips">
      ${Object.entries(SOURCE_LABELS)
        .map(([key, label]) => {
          const count = Number(sources[key] || 0);
          return `<span class="source-chip ${count ? "hit" : ""}">${escapeHtml(label)} ${count}</span>`;
        })
        .join("")}
    </div>
  `;
}

function renderFeedback(messageId) {
  return `
    <div class="feedback-actions">
      <button type="button" class="feedback-button" data-feedback="up" data-message-id="${escapeAttr(messageId)}">有帮助</button>
      <button type="button" class="feedback-button" data-feedback="down" data-message-id="${escapeAttr(messageId)}">需改进</button>
    </div>
  `;
}

async function sendQuestion(text) {
  const question = String(text || "").trim();
  if (!question || state.busy) return;
  state.busy = true;
  const requestId = (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`).replace(/[^A-Za-z0-9_.-]/g, "");
  $("sendButton").disabled = true;
  renderQuickQuestions();
  let session = activeSession();
  if (!session) {
    try {
      session = await createSession("新会话");
    } catch (error) {
      state.busy = false;
      $("sendButton").disabled = false;
      renderQuickQuestions();
      setMessage("chatMessage", friendlyErrorMessage(error), "error");
      return;
    }
  }
  state.pending = { text: question, created_at: new Date().toISOString() };
  setMessage("chatMessage", "");
  renderMessages();
  let rendered = false;
  try {
    const data = await fridgeApi(`/api/fridge/sessions/${encodeURIComponent(session.id)}/messages`, {
      method: "POST",
      body: JSON.stringify({ message: question, model: state.selectedModel || "", request_id: requestId }),
    });
    upsertSession(data.session);
    state.activeSessionId = data.session.id;
    sessionStorage.setItem("fridge_active_session", data.session.id);
    $("messageInput").value = "";
    state.pending = null;
    renderAll();
    rendered = true;
  } catch (error) {
    state.pending = null;
    if (await reconcileSentQuestion(session.id, question)) {
      $("messageInput").value = "";
      renderAll();
      rendered = true;
    } else {
      setMessage("chatMessage", friendlyErrorMessage(error), "error");
    }
  } finally {
    state.pending = null;
    state.busy = false;
    $("sendButton").disabled = false;
    renderQuickQuestions();
    if (!rendered) {
      renderMessages();
    }
  }
}

async function submitComposer(event) {
  event.preventDefault();
  await sendQuestion($("messageInput").value);
}

async function patchSession(sessionId, payload) {
  const session = await fridgeApi(`/api/fridge/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  upsertSession(session);
  renderSessions();
}

async function deleteSession(sessionId) {
  if (!window.confirm("删除该会话？")) return;
  await fridgeApi(`/api/fridge/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  state.sessions = state.sessions.filter((item) => item.id !== sessionId);
  if (state.activeSessionId === sessionId) {
    state.activeSessionId = state.sessions[0]?.id || "";
    if (!state.activeSessionId) {
      await createSession("新会话");
      return;
    }
  }
  renderAll();
}

async function uploadDataset(dataset, file) {
  if (!dataset || !file) return;
  const form = new FormData();
  form.append("file", file);
  setMessage("uploadMessage", `正在上传 ${file.name}...`);
  try {
    const data = await fridgeApi(`/api/fridge/upload/${encodeURIComponent(dataset)}`, {
      method: "POST",
      body: form,
    });
    setMessage("uploadMessage", `${DATASETS[dataset].label}已更新：${data.meta?.row_count || 0} 行。`, "ok");
    await loadOptions();
    renderSummary();
    renderModelSelect();
    renderModelCard();
    renderDatasetStatus();
    renderQuickQuestions();
  } catch (error) {
    setMessage("uploadMessage", error.message, "error");
  }
}

async function submitFeedback(score, messageId) {
  const session = activeSession();
  if (!session || !messageId) return;
  setMessage("feedbackMessage", "正在提交反馈...");
  try {
    await fridgeApi("/api/fridge/feedback", {
      method: "POST",
      body: JSON.stringify({
        session_id: session.id,
        message_id: messageId,
        score,
        issue_type: $("feedbackIssue").value || "",
        note: $("feedbackNote").value.trim(),
      }),
    });
    $("feedbackNote").value = "";
    setMessage("feedbackMessage", "反馈已记录。", "ok");
  } catch (error) {
    setMessage("feedbackMessage", error.message, "error");
  }
}

function markdownToHtml(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const html = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (isTableStart(lines, index)) {
      const tableLines = [];
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        tableLines.push(lines[index]);
        index += 1;
      }
      index -= 1;
      html.push(renderTable(tableLines));
    } else if (/^#{1,4}\s+/.test(line)) {
      html.push(`<h3>${inlineMarkdown(line.replace(/^#{1,4}\s+/, ""))}</h3>`);
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(`<li>${inlineMarkdown(lines[index].replace(/^\s*[-*]\s+/, ""))}</li>`);
        index += 1;
      }
      index -= 1;
      html.push(`<ul>${items.join("")}</ul>`);
    } else if (line.trim()) {
      html.push(`<p>${inlineMarkdown(line)}</p>`);
    }
  }
  return html.join("") || "<p>暂无内容。</p>";
}

function inlineMarkdown(text) {
  return escapeHtml(text).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function isTableStart(lines, index) {
  return (
    lines[index] &&
    lines[index].trim().startsWith("|") &&
    lines[index + 1] &&
    /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1])
  );
}

function renderTable(lines) {
  const rows = lines
    .filter((_, index) => index !== 1)
    .map((line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim()));
  if (!rows.length) return "";
  const [head, ...body] = rows;
  return `
    <div class="table-wrap">
      <table>
        <thead><tr>${head.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead>
        <tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>
  `;
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function bindEvents() {
  $("loginForm").addEventListener("submit", submitLogin);
  $("newSessionButton").addEventListener("click", () => createSession("新会话"));
  $("reloadButton").addEventListener("click", refreshWorkspace);
  $("sessionSearch").addEventListener("input", renderSessions);
  $("composerForm").addEventListener("submit", submitComposer);
  $("modelSelect").addEventListener("change", (event) => {
    state.selectedModel = event.target.value;
    sessionStorage.setItem("fridge_selected_model", state.selectedModel);
    renderModelCard();
    renderQuickQuestions();
  });
  $("quickQuestions").addEventListener("click", (event) => {
    const button = event.target.closest("[data-question]");
    if (!button || button.disabled || state.busy) return;
    event.preventDefault();
    sendQuestion(button.dataset.question || "");
  });
  $("sessionList").addEventListener("click", async (event) => {
    const action = event.target.closest("[data-action]");
    if (action) {
      const sessionId = action.dataset.sessionId;
      if (action.dataset.action === "favorite") {
        const session = state.sessions.find((item) => item.id === sessionId);
        await patchSession(sessionId, { favorite: !session?.favorite });
      } else if (action.dataset.action === "delete") {
        await deleteSession(sessionId);
      }
      return;
    }
    const item = event.target.closest("[data-session-id]");
    if (!item) return;
    state.activeSessionId = item.dataset.sessionId;
    sessionStorage.setItem("fridge_active_session", state.activeSessionId);
    renderSessions();
    renderMessages();
  });
  $("uploadRows").addEventListener("change", (event) => {
    const input = event.target.closest("input[type='file'][data-dataset]");
    if (!input || !input.files?.[0]) return;
    uploadDataset(input.dataset.dataset, input.files[0]);
    input.value = "";
  });
  $("messageList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-feedback]");
    if (!button) return;
    submitFeedback(button.dataset.feedback, button.dataset.messageId);
  });
}

bindEvents();
initialize();
