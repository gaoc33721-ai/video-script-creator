const state = {
  options: { categories: [], models_by_category: {} },
  features: [],
  selectedFeatures: [],
  videoTypes: ["问题解决/痛点挖掘型", "产品展示/功能介绍型", "开箱体验型", "场景化/生活方式型", "测评/对比型"],
  selectedVideoTypes: ["问题解决/痛点挖掘型", "场景化/生活方式型"],
  activeJobId: "",
  renderedJobId: "",
  activeVariantIndex: 0,
  currentResultJob: null,
  videoJobs: [],
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

function setMessage(id, text, kind = "") {
  const node = $(id);
  node.textContent = text || "";
  node.className = `message ${kind}`.trim();
}

function optionHtml(value, label = value, selected = false) {
  return `<option value="${escapeAttr(value)}" ${selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
}

function checkItemHtml(value, selected) {
  return `
    <button class="check-item ${selected ? "selected" : ""}" type="button" data-value="${escapeAttr(value)}" aria-pressed="${selected}">
      <span class="check-sign" aria-hidden="true">✓</span>
      <span>${escapeHtml(value)}</span>
    </button>
  `;
}

async function loadSummary() {
  const summary = await api("/api/summary");
  $("metrics").innerHTML = [
    ["当前品类数", summary.category_count],
    ["当前型号数", summary.model_count],
    ["当前卖点行数", summary.row_count],
    ["缓存状态", summary.loaded ? "已加载" : "未加载"],
  ]
    .map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
}

async function loadOptions() {
  state.options = await api("/api/options");
  const category = $("categorySelect");
  category.innerHTML = state.options.categories.map((item) => optionHtml(item)).join("");
  updateModels();
}

function updateModels() {
  const category = $("categorySelect").value;
  const models = state.options.models_by_category[category] || [];
  $("modelSelect").innerHTML = models.map((item) => optionHtml(item, String(item).trim())).join("");
  loadFeatures();
  updateSelectionSummary();
}

async function loadFeatures() {
  const category = $("categorySelect").value;
  const model = $("modelSelect").value;
  if (!category || !model) {
    state.features = [];
    state.selectedFeatures = [];
    renderFeaturePicker();
    return;
  }
  const data = await api(`/api/features?category=${encodeURIComponent(category)}&model=${encodeURIComponent(model)}`);
  state.features = data.features;
  state.selectedFeatures = data.features.slice(0, 3);
  renderFeaturePicker();
  updateSelectionSummary();
}

function toggleValue(list, value) {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

function renderFeaturePicker() {
  const picker = $("featurePicker");
  if (!state.features.length) {
    picker.innerHTML = '<div class="check-empty">当前型号未匹配到卖点，请切换型号或更新卖点库</div>';
    return;
  }
  picker.innerHTML = state.features
    .map((item) => checkItemHtml(item, state.selectedFeatures.includes(item)))
    .join("");
}

function renderVideoTypePicker() {
  $("videoTypePicker").innerHTML = state.videoTypes
    .map((item) => checkItemHtml(item, state.selectedVideoTypes.includes(item)))
    .join("");
}

function formPayload(form) {
  const data = new FormData(form);
  return {
    platform: data.get("platform"),
    target_market: data.get("target_market"),
    variant_count: Number(data.get("variant_count") || 2),
    category: data.get("category"),
    model: data.get("model"),
    selected_features: state.selectedFeatures,
    video_usage: data.get("video_usage"),
    video_type: state.selectedVideoTypes,
    expected_duration: Number(data.get("expected_duration") || 30),
    project_type: data.get("project_type"),
    target_audience: data.get("target_audience") || "",
    pain_points: data.get("pain_points") || "",
    custom_requirements: data.get("custom_requirements") || "",
  };
}

async function submitGeneration(event) {
  event.preventDefault();
  hideResults();
  setMessage("formMessage", "任务提交中...");
  try {
    const result = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formPayload(event.currentTarget)),
    });
    state.activeJobId = result.job_id;
    state.renderedJobId = "";
    setMessage("formMessage", `已提交任务 ${result.job_id}，生成完成后会自动显示在下方。`, "ok");
    await loadJobs();
  } catch (error) {
    setMessage("formMessage", error.message, "error");
  }
}

function updateSelectionSummary() {
  const category = $("categorySelect").value || "未选择";
  const model = ($("modelSelect").value || "未选择").trim();
  const features = state.selectedFeatures;
  $("selectionSummary").innerHTML = `
    <div><span>品类</span><strong>${escapeHtml(category)}</strong></div>
    <div><span>型号</span><strong>${escapeHtml(model)}</strong></div>
    <div><span>已选卖点</span><strong>${features.length}</strong></div>
    <ul>${features.slice(0, 5).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>暂无卖点</li>"}</ul>
  `;
}

async function uploadFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  setMessage("uploadMessage", "正在上传并解析...");
  const body = new FormData();
  body.append("file", file);
  try {
    await api("/api/upload", { method: "POST", body });
    setMessage("uploadMessage", "卖点库已更新。", "ok");
    await loadSummary();
    await loadOptions();
  } catch (error) {
    setMessage("uploadMessage", error.message, "error");
  } finally {
    event.target.value = "";
  }
}

async function loadJobs() {
  const data = await api("/api/jobs");
  $("jobs").innerHTML =
    data.jobs.map(renderJob).join("") ||
    '<div class="empty-state"><strong>暂无任务</strong><span>提交脚本生成后，进度会显示在这里。</span></div>';
  revealCompletedResult(data.jobs);
}

function renderJob(job) {
  const variants =
    job.status === "succeeded"
      ? `<button class="load-result" type="button" data-job-id="${escapeAttr(job.id)}">查看脚本</button><a href="/api/jobs/${job.id}/download">下载 Excel</a>`
      : "";
  const error = job.error_message ? `<div class="message error">${escapeHtml(job.error_message)}</div>` : "";
  return `
    <article class="job">
      <div class="job-head">
        <span>${escapeHtml((job.request || {}).model || "未命名产品")}</span>
        <span>${escapeHtml(job.status)} ${Number(job.progress || 0)}%</span>
      </div>
      <div class="progress"><div style="width:${Number(job.progress || 0)}%"></div></div>
      <div class="message">${escapeHtml(job.current_step || "")}</div>
      ${error}
      ${variants}
    </article>
  `;
}

function revealCompletedResult(jobs) {
  const completed = (jobs || []).find((job) => {
    if (job.status !== "succeeded" || !(job.variants || []).length) return false;
    return state.activeJobId ? job.id === state.activeJobId : true;
  });
  if (!completed || completed.id === state.renderedJobId) return;
  renderResult(completed);
}

function hideResults() {
  $("resultSection").classList.add("hidden");
  $("resultTabs").innerHTML = "";
  $("resultBody").innerHTML = "";
  $("videoJobs").innerHTML = "";
  $("videoMessage").textContent = "";
  state.currentResultJob = null;
}

function renderResult(job, variantIndex = 0) {
  const variants = job.variants || [];
  if (!variants.length) return;
  state.renderedJobId = job.id;
  state.activeVariantIndex = Math.max(0, Math.min(variantIndex, variants.length - 1));
  $("resultSection").classList.remove("hidden");
  $("downloadResult").href = `/api/jobs/${job.id}/download`;
  $("resultTabs").innerHTML = variants
    .map((variant, index) => {
      const active = index === state.activeVariantIndex;
      const label = variant.label ? `｜${variant.label}` : "";
      return `<button class="result-tab ${active ? "active" : ""}" type="button" data-index="${index}">${escapeHtml(variant.name || `方案${index + 1}`)}${escapeHtml(label)}</button>`;
    })
    .join("");
  const current = variants[state.activeVariantIndex];
  $("resultBody").innerHTML = renderVariantContent(current);
  state.currentResultJob = job;
  renderVideoPanel(job);
  $("resultSection").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderVariantContent(variant) {
  const label = variant.label ? `<div class="result-label">方案定位：${escapeHtml(variant.label)}</div>` : "";
  return `${label}<div class="script-markdown">${markdownToHtml(variant.content || "")}</div>`;
}

function extractVideoPrompt(content) {
  const text = String(content || "");
  const patterns = [
    /整体AI视频生成Prompt\s*（English）\s*[:：]\s*([\s\S]*?)(?:\n\s*Negative Prompt|\n\s*Recommended Settings|$)/i,
    /整体AI视频生成Prompt\s*\(English\)\s*[:：]\s*([\s\S]*?)(?:\n\s*Negative Prompt|\n\s*Recommended Settings|$)/i,
    /Overall AI Video Generation Prompt\s*[:：]\s*([\s\S]*?)(?:\n\s*Negative Prompt|\n\s*Recommended Settings|$)/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match && match[1]) {
      return match[1].replace(/^\s*[-•]\s*/gm, "").replace(/\s+/g, " ").trim();
    }
  }
  return "";
}

function fallbackVideoPrompt(job) {
  const request = job.request || {};
  const features = (request.selected_features || []).filter(Boolean).join("; ") || "product benefits and lifestyle usage";
  return `Six-second premium e-commerce reference video for a Hisense product, model ${request.model || ""}. Show a realistic product-focused scene based on this script variant, highlighting: ${features}. Modern bright kitchen, cinematic soft daylight, smooth camera movement, realistic product proportions, no text overlay, no logo distortion, no extra brands.`;
}

async function renderVideoPanel(job) {
  const variants = job.variants || [];
  if (!variants.length) return;
  $("videoVariantSelect").innerHTML = variants
    .map((variant, index) => optionHtml(String(index), variant.name || `方案${index + 1}`, index === state.activeVariantIndex))
    .join("");
  updateVideoPrompt();
  await loadVideoJobs(job.id);
}

function updateVideoPrompt() {
  const job = state.currentResultJob;
  if (!job) return;
  const variants = job.variants || [];
  const selectedIndex = Number($("videoVariantSelect").value || state.activeVariantIndex || 0);
  const variant = variants[selectedIndex] || variants[0] || {};
  $("videoPrompt").value = extractVideoPrompt(variant.content || "") || fallbackVideoPrompt(job);
  $("videoCost").textContent = "本次会提交 1 条 6 秒 Nova Reel 任务，按 $0.08/秒估算约 $0.48，实际以 AWS 账单为准。";
}

async function loadVideoJobs(scriptJobId) {
  if (!scriptJobId) return;
  try {
    const data = await api(`/api/nova-reel/jobs?script_job_id=${encodeURIComponent(scriptJobId)}`);
    state.videoJobs = data.jobs || [];
    if (typeof data.estimated_usd_per_second === "number") {
      const seconds = 6;
      $("videoCost").textContent = `本次会提交 1 条 ${seconds} 秒 Nova Reel 任务，按 $${data.estimated_usd_per_second}/秒估算约 $${(seconds * data.estimated_usd_per_second).toFixed(2)}，实际以 AWS 账单为准。`;
    }
    renderVideoJobs(state.videoJobs);
  } catch (error) {
    setMessage("videoMessage", error.message, "error");
  }
}

function renderVideoJobs(jobs) {
  $("videoJobs").innerHTML =
    (jobs || []).map((job) => {
      const preview = job.preview_url
        ? `<video class="video-preview" controls src="${escapeAttr(job.preview_url)}"></video><a class="download-link" href="${escapeAttr(job.preview_url)}" target="_blank" rel="noreferrer">打开视频</a>`
        : "";
      const failure = job.failure_message ? `<div class="message error">${escapeHtml(job.failure_message)}</div>` : "";
      return `
        <article class="video-job">
          <div class="job-head">
            <span>${escapeHtml(job.variant_name || "视频任务")}</span>
            <span>${escapeHtml(job.status || "")}</span>
          </div>
          <div class="message">${escapeHtml(job.model || "")} · ${escapeHtml(job.model_id || "")} · ${escapeHtml(job.created_at || "")}</div>
          ${failure}
          ${preview}
        </article>
      `;
    }).join("") || '<div class="empty-state"><strong>暂无视频任务</strong><span>确认脚本后，可在这里提交 Nova Reel 参考片段。</span></div>';
}

async function submitVideoGeneration() {
  const job = state.currentResultJob;
  if (!job) return;
  setMessage("videoMessage", "正在提交 Nova Reel 视频任务...");
  try {
    await api("/api/nova-reel/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script_job_id: job.id,
        variant_index: Number($("videoVariantSelect").value || 0),
      }),
    });
    setMessage("videoMessage", "视频任务已提交，稍后点击刷新查看状态。", "ok");
    await loadVideoJobs(job.id);
  } catch (error) {
    setMessage("videoMessage", error.message, "error");
  }
}

async function refreshVideoGeneration() {
  const job = state.currentResultJob;
  if (!job) return;
  setMessage("videoMessage", "正在刷新视频生成状态...");
  try {
    const data = await api(`/api/nova-reel/refresh?script_job_id=${encodeURIComponent(job.id)}`, { method: "POST" });
    renderVideoJobs(data.jobs || []);
    setMessage("videoMessage", "视频状态已刷新。", "ok");
  } catch (error) {
    setMessage("videoMessage", error.message, "error");
  }
}

function markdownToHtml(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const html = [];
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (isMarkdownTableStart(lines, index)) {
      const tableLines = [];
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        tableLines.push(lines[index]);
        index++;
      }
      index--;
      html.push(renderMarkdownTable(tableLines));
    } else if (!line.trim()) {
      html.push("");
    } else if (/^#{1,4}\s+/.test(line)) {
      html.push(`<h3>${escapeHtml(line.replace(/^#{1,4}\s+/, ""))}</h3>`);
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(`<li>${escapeHtml(lines[index].replace(/^\s*[-*]\s+/, ""))}</li>`);
        index++;
      }
      index--;
      html.push(`<ul>${items.join("")}</ul>`);
    } else {
      html.push(`<p>${escapeHtml(line)}</p>`);
    }
  }
  return html.join("");
}

function isMarkdownTableStart(lines, index) {
  return (
    lines[index] &&
    lines[index].trim().startsWith("|") &&
    lines[index + 1] &&
    /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1])
  );
}

function renderMarkdownTable(lines) {
  const rows = lines
    .filter((line, index) => index !== 1)
    .map((line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim()));
  if (!rows.length) return "";
  const [header, ...body] = rows;
  return `
    <div class="table-wrap">
      <table>
        <thead><tr>${header.map((cell) => `<th>${escapeHtml(cell)}</th>`).join("")}</tr></thead>
        <tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>
  `;
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

$("categorySelect").addEventListener("change", updateModels);
$("modelSelect").addEventListener("change", loadFeatures);
$("featurePicker").addEventListener("click", (event) => {
  const item = event.target.closest(".check-item");
  if (!item) return;
  state.selectedFeatures = toggleValue(state.selectedFeatures, item.dataset.value);
  renderFeaturePicker();
  updateSelectionSummary();
});
$("videoTypePicker").addEventListener("click", (event) => {
  const item = event.target.closest(".check-item");
  if (!item) return;
  state.selectedVideoTypes = toggleValue(state.selectedVideoTypes, item.dataset.value);
  renderVideoTypePicker();
});
$("generateForm").addEventListener("submit", submitGeneration);
$("uploadInput").addEventListener("change", uploadFile);
$("refreshJobs").addEventListener("click", loadJobs);
$("jobs").addEventListener("click", async (event) => {
  const button = event.target.closest(".load-result");
  if (!button) return;
  const job = await api(`/api/jobs/${encodeURIComponent(button.dataset.jobId)}`);
  state.activeJobId = job.id;
  renderResult(job);
});
$("resultTabs").addEventListener("click", async (event) => {
  const tab = event.target.closest(".result-tab");
  if (!tab || !state.renderedJobId) return;
  const job = await api(`/api/jobs/${encodeURIComponent(state.renderedJobId)}`);
  renderResult(job, Number(tab.dataset.index || 0));
});
$("videoVariantSelect").addEventListener("change", updateVideoPrompt);
$("submitVideo").addEventListener("click", submitVideoGeneration);
$("refreshVideo").addEventListener("click", refreshVideoGeneration);

renderVideoTypePicker();
Promise.all([loadSummary(), loadOptions(), loadJobs()]).catch((error) => {
  setMessage("formMessage", error.message, "error");
});
setInterval(loadJobs, 5000);
