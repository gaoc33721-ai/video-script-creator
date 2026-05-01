const state = {
  options: { categories: [], models_by_category: {} },
  features: [],
  selectedFeatures: [],
  videoTypes: ["问题解决/痛点挖掘型", "产品展示/功能介绍型", "开箱体验型", "场景化/生活方式型", "测评/对比型"],
  selectedVideoTypes: ["问题解决/痛点挖掘型", "场景化/生活方式型"],
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
  setMessage("formMessage", "任务提交中...");
  try {
    const result = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formPayload(event.currentTarget)),
    });
    setMessage("formMessage", `已提交任务 ${result.job_id}，可在任务中心查看。`, "ok");
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
}

function renderJob(job) {
  const variants =
    job.status === "succeeded"
      ? `<a href="/api/jobs/${job.id}/download">下载 Excel</a>${(job.variants || [])
          .map((item) => `<details><summary>${escapeHtml(item.name)}</summary><div class="variant">${escapeHtml(item.content)}</div></details>`)
          .join("")}`
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

renderVideoTypePicker();
Promise.all([loadSummary(), loadOptions(), loadJobs()]).catch((error) => {
  setMessage("formMessage", error.message, "error");
});
setInterval(loadJobs, 5000);
