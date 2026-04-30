const state = {
  options: { categories: [], models_by_category: {} },
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
  category.innerHTML = state.options.categories.map((item) => `<option>${escapeHtml(item)}</option>`).join("");
  updateModels();
}

function updateModels() {
  const category = $("categorySelect").value;
  const models = state.options.models_by_category[category] || [];
  $("modelSelect").innerHTML = models.map((item) => `<option>${escapeHtml(item)}</option>`).join("");
  loadFeatures();
}

async function loadFeatures() {
  const category = $("categorySelect").value;
  const model = $("modelSelect").value;
  if (!category || !model) {
    $("featureSelect").innerHTML = "";
    return;
  }
  const data = await api(`/api/features?category=${encodeURIComponent(category)}&model=${encodeURIComponent(model)}`);
  $("featureSelect").innerHTML = data.features
    .map((item, index) => `<option ${index < 3 ? "selected" : ""}>${escapeHtml(item)}</option>`)
    .join("");
}

function selectedValues(select) {
  return Array.from(select.selectedOptions).map((option) => option.value);
}

function formPayload(form) {
  const data = new FormData(form);
  return {
    platform: data.get("platform"),
    target_market: data.get("target_market"),
    variant_count: Number(data.get("variant_count") || 2),
    category: data.get("category"),
    model: data.get("model"),
    selected_features: selectedValues($("featureSelect")),
    video_usage: data.get("video_usage"),
    video_type: selectedValues(form.elements.video_type),
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
  $("jobs").innerHTML = data.jobs.map(renderJob).join("") || '<p class="message">暂无任务。</p>';
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

$("categorySelect").addEventListener("change", updateModels);
$("modelSelect").addEventListener("change", loadFeatures);
$("generateForm").addEventListener("submit", submitGeneration);
$("uploadInput").addEventListener("change", uploadFile);
$("refreshJobs").addEventListener("click", loadJobs);

Promise.all([loadSummary(), loadOptions(), loadJobs()]).catch((error) => {
  setMessage("formMessage", error.message, "error");
});
setInterval(loadJobs, 5000);
