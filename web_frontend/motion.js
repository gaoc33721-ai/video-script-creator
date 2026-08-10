const motionKnownAssets = new Set();
let motionUploadBatch = { status: "idle", files: [] };
const motionSubmittingAssets = new Set();
let motionSubmitBusy = false;
const motionRetryBusyJobs = new Set();
const motionSubmissionFallbackKeys = new Map();
const MOTION_SUBMISSION_TTL_MS = 30 * 60 * 1000;

function motionSubmissionSignature(assetIds) {
  return Array.from(new Set(assetIds.map(String))).sort().join(",");
}

function pendingMotionSubmissionKey(assetIds) {
  const signature = motionSubmissionSignature(assetIds);
  const storageKey = "image-motion-submit:" + signature;
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    if (saved?.key && Date.now() - Number(saved.createdAt || 0) < MOTION_SUBMISSION_TTL_MS) return saved.key;
    sessionStorage.removeItem(storageKey);
  } catch (_) {
    const saved = motionSubmissionFallbackKeys.get(signature);
    if (saved?.key && Date.now() - saved.createdAt < MOTION_SUBMISSION_TTL_MS) return saved.key;
  }
  const randomPart = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
  const key = "motion-" + Date.now() + "-" + randomPart;
  const pending = { key, createdAt: Date.now() };
  motionSubmissionFallbackKeys.set(signature, pending);
  try {
    sessionStorage.setItem(storageKey, JSON.stringify(pending));
  } catch (_) {}
  return key;
}

function clearPendingMotionSubmissionKey(assetIds) {
  const signature = motionSubmissionSignature(assetIds);
  motionSubmissionFallbackKeys.delete(signature);
  try {
    sessionStorage.removeItem("image-motion-submit:" + signature);
  } catch (_) {}
}

function motionUploadFileSummary(files) {
  const names = (files || []).map((item) => (typeof item === "string" ? item : item.name)).filter(Boolean);
  if (!names.length) return "";
  const visible = names.slice(0, 3).join("、");
  return names.length > 3 ? visible + " 等 " + names.length + " 张" : visible;
}

function updateMotionUploadArea() {
  const count = motionUploadBatch.files.length;
  const title = $("motionUploadTitle");
  const hint = $("motionUploadHint");
  if (!title || !hint) return;
  if (!count || motionUploadBatch.status === "idle") {
    title.textContent = "上传卖点图";
    hint.textContent = "同一批最多 10 张同型号图片，短边至少 720px";
    return;
  }
  const labels = {
    selected: "已选择 " + count + " 张卖点图",
    uploading: "正在上传并识别 " + count + " 张卖点图",
    success: "刚刚上传 " + count + " 张卖点图",
    error: count + " 张卖点图上传未完成",
  };
  title.textContent = labels[motionUploadBatch.status] || labels.selected;
  hint.textContent = motionUploadFileSummary(motionUploadBatch.files);
}

function motionUploadBatchHtml() {
  const count = motionUploadBatch.files.length;
  if (!count || motionUploadBatch.status === "idle") return "";
  const copy = {
    selected: ["已选择 " + count + " 张卖点图", "文件已选中，点击“识别图片并推荐动效”开始上传。"],
    uploading: ["正在处理本次 " + count + " 张卖点图", "图片正在上传并识别卖点、裁切区和保护区，请稍候。"],
    success: ["刚刚成功上传 " + count + " 张卖点图", "素材已接收并显示在下方，可继续确认推荐动效。"],
    error: ["本次 " + count + " 张卖点图上传未完成", "文件仍保留在选择框中，可检查提示后重试。"],
  }[motionUploadBatch.status];
  if (!copy) return "";
  return '<div class="empty-state motion-batch-status" data-status="' + escapeAttr(motionUploadBatch.status) + '">' +
    "<strong>" + escapeHtml(copy[0]) + "</strong>" +
    "<span>" + escapeHtml(copy[1]) + "</span>" +
    "<small>" + escapeHtml(motionUploadFileSummary(motionUploadBatch.files)) + "</small>" +
    "</div>";
}

function motionOptions(items, selected) {
  return items
    .map(([value, label]) => `<option value="${escapeAttr(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(label)}</option>`)
    .join("");
}

function setAppMode(mode) {
  state.activeMode = mode === "image_motion" ? "image_motion" : "script";
  document.querySelectorAll("[data-app-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.appMode === state.activeMode);
  });
  $("generateForm")?.classList.toggle("hidden", state.activeMode !== "script");
  $("scriptSelectionCard")?.classList.toggle("hidden", state.activeMode !== "script");
  $("motionWorkflow")?.classList.toggle("hidden", state.activeMode !== "image_motion");
  if (state.activeMode === "image_motion") {
    $("resultSection")?.classList.add("hidden");
    loadMotionWorkspace();
  } else if (state.currentResultJob) {
    $("resultSection")?.classList.remove("hidden");
  }
}

function syncMotionCategoryOptions() {
  if (!$("motionCategorySelect")) return;
  const current = $("motionCategorySelect").value || $("categorySelect")?.value || state.options.categories[0] || "";
  $("motionCategorySelect").innerHTML = (state.options.categories || [])
    .map((item) => `<option value="${escapeAttr(item)}">${escapeHtml(item)}</option>`)
    .join("");
  if ((state.options.categories || []).includes(current)) $("motionCategorySelect").value = current;
  syncMotionModelOptions();
}

function syncMotionModelOptions() {
  const category = $("motionCategorySelect")?.value || "";
  const query = ($("motionModelSearch")?.value || "").trim().toLowerCase();
  const models = (state.options.models_by_category?.[category] || []).filter((item) => !query || String(item).toLowerCase().includes(query));
  const previous = $("motionModelSelect")?.value || $("modelSelect")?.value || "";
  $("motionModelSelect").innerHTML = models.map((item) => `<option value="${escapeAttr(item)}">${escapeHtml(item)}</option>`).join("");
  if (models.includes(previous)) $("motionModelSelect").value = previous;
}

function jobsForMotionAsset(assetId) {
  return state.imageMotionJobs
    .filter((item) => item.creative_asset_id === assetId)
    .sort((left, right) => Number(right.version || 0) - Number(left.version || 0));
}

function activeMotionJob(assetId) {
  const jobs = jobsForMotionAsset(assetId);
  const wanted = state.motionActiveVersions.get(assetId);
  return jobs.find((item) => item.id === wanted) || jobs.find((item) => item.status === "succeeded") || jobs[0] || null;
}

function motionGenerationLabel(job) {
  if (job?.generation_mode === "luma_ray2_flow") return "\u00b7 Ray2 \u6c14\u6d41\u751f\u6210";
  if (job?.generation_mode === "luma_ray2_component") return "\u00b7 Ray2 \u90e8\u4ef6\u751f\u6210";
  if (job?.generation_mode === "luma_ray2_steam") return "\u00b7 Ray2 \u4f53\u79ef\u84b8\u6c7d";
  if (job?.generation_mode === "luma_ray2_liquid") return "\u00b7 Ray2 \u6db2\u4f53\u751f\u6210";
  if (job?.generation_mode === "stable_flow_overlay") return "\u00b7 \u65e7\u7248\u626b\u5149\u5408\u6210";
  if (job?.generation_mode === "stable_template") return "\u00b7 \u65e7\u7248\u7ebf\u6761\u6a21\u677f";
  return "";
}

function motionFailureGuidance(job) {
  const raw = String(job?.qa_result?.message || job?.failure_message || job?.current_step || "").toLowerCase();
  if (raw.includes("\u72ec\u7acb") || raw.includes("\u76ee\u6807\u52a8\u6548") || raw.includes("global") || raw.includes("\u63a8\u955c") || raw.includes("\u6f02\u79fb")) {
    return "\u76ee\u6807\u52a8\u6548\u4e0d\u591f\u660e\u786e\uff0c\u6216\u88ab\u6574\u4f53\u63a8\u955c\u4ee3\u66ff\u3002\u5efa\u8bae\u9996\u9009\u201c\u5f3a\u5316\u76ee\u6807\u52a8\u6548\u91cd\u8bd5\u201d\uff0c\u4fdd\u6301\u5f53\u524d\u84b8\u6c7d\u3001\u6c14\u6d41\u6216\u90e8\u4ef6\u7c7b\u578b\u4e0d\u53d8\u3002";
  }
  if (raw.includes("\u4fdd\u771f") || raw.includes("\u7ed3\u6784") || raw.includes("\u76f8\u4f3c") || raw.includes("fidelity")) {
    return "\u4ea7\u54c1\u7ed3\u6784\u6216\u6784\u56fe\u504f\u79bb\u539f\u56fe\u3002\u5efa\u8bae\u4f7f\u7528\u201c\u4fdd\u7559\u6784\u56fe\u91cd\u8bd5\u201d\uff1b\u82e5\u4ecd\u6709\u6f02\u79fb\uff0c\u518d\u9009\u62e9\u201c\u964d\u4f4e\u5f3a\u5ea6\u201d\u3002";
  }
  if (raw.includes("\u670d\u52a1") || raw.includes("\u4f9b\u5e94\u5546") || raw.includes("ray 2") || raw.includes("provider")) {
    return "\u6a21\u578b\u670d\u52a1\u672a\u8fd4\u56de\u53ef\u7528\u7ed3\u679c\u3002\u5efa\u8bae\u4fdd\u6301\u5f53\u524d\u65b9\u6848\u91cd\u8bd5\uff0c\u65e0\u9700\u6539\u53d8\u4e1a\u52a1\u9700\u6c42\u3002";
  }
  return "\u672c\u7248\u672c\u672a\u901a\u8fc7\u8d28\u68c0\uff0c\u5df2\u7981\u6b62\u4e0b\u8f7d\u3002\u53ef\u4f18\u5148\u5f3a\u5316\u539f\u76ee\u6807\u52a8\u6548\uff0c\u6216\u6839\u636e\u6784\u56fe\u504f\u79fb\u60c5\u51b5\u964d\u4f4e\u5f3a\u5ea6\u3002";
}

function motionResultHtml(asset) {
  const versions = jobsForMotionAsset(asset.id);
  const job = activeMotionJob(asset.id);
  if (!versions.length) {
    return `<div class="motion-result"><p>\u786e\u8ba4\u52a8\u6548\u540e\u5373\u53ef\u751f\u6210\uff0c\u6b63\u5e38\u7d20\u6750\u65e0\u9700 Prompt\u3002</p></div>`;
  }
  const versionButtons = versions
    .map(
      (item) =>
        `<button type="button" class="motion-version ${job?.id === item.id ? "active" : ""}" data-motion-version="${escapeAttr(item.id)}" data-asset-id="${escapeAttr(asset.id)}">\u7248\u672c ${Number(item.version || 1)} \u00b7 ${escapeHtml(item.status)} ${escapeHtml(motionGenerationLabel(item))}</button>`
    )
    .join("");
  const status = job?.current_step || job?.status || "";
  const qa = job?.qa_result?.message || job?.fallback_reason || job?.failure_message || "";
  const failedRecovery = job?.status === "failed"
    ? '<div class="motion-recovery">' +
      '<strong>\u672c\u7248\u672c\u672a\u901a\u8fc7\u8d28\u68c0\uff0c\u4e0d\u4f1a\u4f5c\u4e3a\u6b63\u5f0f\u7ed3\u679c\u4e0b\u8f7d\u3002</strong>' +
      '<span>' + escapeHtml(motionFailureGuidance(job)) + '</span>' +
      '<div class="motion-result-actions">' +
        '<button type="button" data-motion-retry="strengthen_effect" data-job-id="' + escapeAttr(job.id) + '">\u5f3a\u5316\u76ee\u6807\u52a8\u6548\u91cd\u8bd5</button>' +
        '<button type="button" class="secondary" data-motion-retry="preserve_composition" data-job-id="' + escapeAttr(job.id) + '">\u4fdd\u7559\u6784\u56fe\u91cd\u8bd5</button>' +
        '<button type="button" class="secondary" data-motion-retry="lower_intensity" data-job-id="' + escapeAttr(job.id) + '">\u964d\u4f4e\u5f3a\u5ea6</button>' +
        '<button type="button" class="secondary" data-motion-retry="alternate_effect" data-job-id="' + escapeAttr(job.id) + '">\u6362\u4e00\u79cd\u52a8\u6548</button>' +
      '</div>' +
    '</div>'
    : "";
  const video =
    job?.status === "succeeded" && job?.preview_url
      ? `
        <video src="${escapeAttr(job.preview_url)}" controls autoplay muted loop playsinline></video>
        <label class="motion-job-select">
          <input type="checkbox" data-motion-export-job="${escapeAttr(job.id)}" ${state.motionSelectedJobs.has(job.id) ? "checked" : ""} />
          \u9009\u4e2d\u6b64\u7248\u672c\u7528\u4e8e ZIP \u5bfc\u51fa
        </label>
        <div class="motion-result-actions">
          <a class="download-link" href="${escapeAttr(job.download_url)}">\u4e0b\u8f7d\u89c6\u9891</a>
          <a class="download-link" href="${escapeAttr(job.muted_download_url)}">\u9759\u97f3\u4e0b\u8f7d</a>
          <button type="button" class="secondary" data-motion-retry="preserve_composition" data-job-id="${escapeAttr(job.id)}">\u4fdd\u7559\u6784\u56fe\u91cd\u8bd5</button>
          <button type="button" class="secondary" data-motion-retry="lower_intensity" data-job-id="${escapeAttr(job.id)}">\u964d\u4f4e\u5f3a\u5ea6</button>
          <button type="button" class="secondary" data-motion-retry="alternate_effect" data-job-id="${escapeAttr(job.id)}">\u6362\u4e00\u79cd\u52a8\u6548</button>
        </div>`
      : failedRecovery || '<div class="message">' + escapeHtml(status) + "</div>";
  return `
    <div class="motion-result">
      <div class="motion-version-list">${versionButtons}</div>
      ${video}
      ${qa ? `<p class="message">${escapeHtml(qa)}</p>` : ""}
    </div>`;
}

function renderMotionAssets() {
  if (!$("motionAssets")) return;
  const batchStatus = motionUploadBatchHtml();
  if (!state.creativeAssets.length && !batchStatus) {
    $("motionAssets").innerHTML = '<div class="empty-state"><strong>\u5c1a\u672a\u4e0a\u4f20\u5356\u70b9\u56fe</strong><span>\u5148\u786e\u8ba4\u54c1\u7c7b\u548c\u578b\u53f7\uff0c\u518d\u4e0a\u4f20\u540c\u578b\u53f7\u7d20\u6750\u3002</span></div>';
    return;
  }
  $("motionAssets").innerHTML = batchStatus + state.creativeAssets
    .map((asset) => {
      const analysis = asset.analysis || {};
      const plan = asset.motion_plan || {};
      if (!motionKnownAssets.has(asset.id)) {
        motionKnownAssets.add(asset.id);
        if (analysis.ready) state.motionSelectedAssets.add(asset.id);
      }
      const blockers = analysis.blocking_issues || [];
      const warnings = analysis.warnings || [];
      const isSubmitting = motionSubmittingAssets.has(asset.id);
      const enhancement = asset.resolution_enhancement || analysis.resolution_enhancement || {};
      const resolutionText = enhancement.applied
        ? "\u539f\u56fe " + Number(enhancement.source_width || asset.source_width || 0) + "\u00d7" + Number(enhancement.source_height || asset.source_height || 0) +
          " \u2192 \u5df2\u81ea\u52a8\u589e\u5f3a\u81f3 " + Number(enhancement.output_width || asset.working_width || 0) + "\u00d7" + Number(enhancement.output_height || asset.working_height || 0)
        : Number(asset.source_width || 0) + " \u00d7 " + Number(asset.source_height || 0);
      return `
        <article class="motion-asset" data-motion-asset-id="${escapeAttr(asset.id)}">
          <div class="motion-asset-head">
            <label>
              <input type="checkbox" data-motion-select-asset="${escapeAttr(asset.id)}" ${state.motionSelectedAssets.has(asset.id) ? "checked" : ""} ${analysis.ready ? "" : "disabled"} />
              <strong>${escapeHtml(asset.filename || asset.feature || asset.id)}</strong>
            </label>
            <span class="qa-badge">${analysis.ready ? "\u53ef\u751f\u6210" : "\u9700\u8c03\u6574"}</span>
          </div>
          <div class="motion-asset-body">
            <div>
              <img class="motion-original" src="${escapeAttr(asset.preview_url)}" alt="${escapeAttr(asset.filename || "\u5356\u70b9\u56fe")}" />
              <p class="message motion-resolution ${enhancement.applied ? "enhanced" : ""}">${escapeHtml(resolutionText)} \u00b7 ${escapeHtml(analysis.detected_category || asset.category || "")}</p>
            </div>
            <div class="motion-analysis">
              <p><strong>\u5356\u70b9\uff1a</strong>${escapeHtml(analysis.selling_point_summary || asset.feature || "\u5df2\u6839\u636e\u56fe\u7247\u8bc6\u522b")}</p>
              <p><strong>\u63a8\u8350\u88c1\u5207\uff1a</strong>${escapeHtml(JSON.stringify(analysis.visual_crop || {}))}</p>
              ${blockers.length ? `<p class="motion-blockers">${blockers.map(escapeHtml).join("\uff1b")}</p>` : ""}
              ${warnings.length ? `<p class="message">${warnings.map(escapeHtml).join("\uff1b")}</p>` : ""}
              <div class="motion-controls">
                <label>\u52a8\u54ea\u91cc
                  <select data-motion-field="focus">
                    ${motionOptions([["product", "\u4ea7\u54c1"], ["effect", "\u529f\u80fd\u6548\u679c"], ["background", "\u80cc\u666f"]], plan.focus || "effect")}
                  </select>
                </label>
                <label>\u600e\u4e48\u52a8
                  <select data-motion-field="preset">
                    ${motionOptions([["flow", "\u6c14\u6d41/\u70ed\u6d41"], ["liquid", "\u6db2\u4f53/\u6c34\u6d41"], ["steam", "\u84b8\u6c7d"], ["glow", "\u53d1\u5149"], ["component", "\u90e8\u4ef6\u8fd0\u52a8"], ["camera", "\u955c\u5934\u63a8\u8fdb"]], plan.preset || "camera")}
                  </select>
                </label>
                <label>\u52a8\u6548\u5f3a\u5ea6
                  <select data-motion-field="intensity">
                    ${motionOptions([["subtle", "\u8f7b\u5fae"], ["standard", "\u6807\u51c6"], ["strong", "\u5f3a\u70c8"]], plan.intensity || "standard")}
                  </select>
                </label>
              </div>
              ${analysis.ready ? `<div class="motion-generate-action">
                <button type="button" data-motion-generate-asset="${escapeAttr(asset.id)}" ${motionSubmitBusy ? "disabled" : ""} ${isSubmitting ? 'aria-busy="true"' : ""}>${isSubmitting ? "\u6b63\u5728\u63d0\u4ea4\u2026" : "\u751f\u6210\u52a8\u6001\u77ed\u89c6\u9891"}</button>
                <span>${isSubmitting ? "\u4efb\u52a1\u5df2\u53d7\u7406\uff0c\u6b63\u5728\u6392\u961f\uff0c\u8bf7\u52ff\u91cd\u590d\u70b9\u51fb\u3002" : "\u9ed8\u8ba4 5 \u79d2\u30011080p\u3001\u5355\u955c\u5934\uff1b\u751f\u6210\u540e\u53ef\u9884\u89c8\u548c\u4e0b\u8f7d\u3002"}</span>
              </div>` : ""}
              ${motionResultHtml(asset)}
            </div>
          </div>
        </article>`;
    })
    .join("");
}

async function loadMotionWorkspace() {
  if (!$("motionCategorySelect")) return;
  const category = $("motionCategorySelect").value || "";
  const model = $("motionModelSelect").value || "";
  try {
    const query = new URLSearchParams();
    if (category) query.set("category", category);
    if (model) query.set("model", model);
    const [assetData, jobData] = await Promise.all([
      api(`/api/creative-assets?${query.toString()}`),
      api("/api/image-motion/jobs"),
    ]);
    state.creativeAssets = assetData.assets || [];
    state.imageMotionJobs = jobData.jobs || [];
    const batchPlan = state.creativeAssets[0]?.motion_plan;
    if (batchPlan && $("motionTextPolicy")) $("motionTextPolicy").value = batchPlan.text_policy || "visual_only";
    if (batchPlan && $("motionAspectRatio")) $("motionAspectRatio").value = batchPlan.aspect_ratio || "source";
    renderMotionAssets();
  } catch (error) {
    setMessage("motionMessage", error.message, "error");
  }
}

async function uploadMotionAssets() {
  const files = Array.from($("motionImageInput")?.files || []);
  const category = $("motionCategorySelect")?.value || "";
  const model = $("motionModelSelect")?.value || "";
  if (!category || !model) {
    setMessage("motionMessage", "\u8bf7\u5148\u786e\u8ba4\u4ea7\u54c1\u54c1\u7c7b\u548c\u578b\u53f7\u3002", "error");
    return;
  }
  if (!files.length || files.length > 10) {
    setMessage("motionMessage", "\u8bf7\u9009\u62e9 1 \u81f3 10 \u5f20\u540c\u578b\u53f7\u5356\u70b9\u56fe\u3002", "error");
    return;
  }
  const body = new FormData();
  body.append("category", category);
  body.append("model", model);
  files.forEach((file) => body.append("files", file));
  $("uploadMotionAssets").disabled = true;
  motionUploadBatch = { status: "uploading", files: files.map((file) => file.name) };
  updateMotionUploadArea();
  renderMotionAssets();
  setMessage("motionMessage", "\u6b63\u5728\u8bc6\u522b\u5356\u70b9\u3001\u88c1\u5207\u533a\u548c\u4fdd\u62a4\u533a...");
  try {
    const data = await api("/api/creative-assets", { method: "POST", body });
    const uploadedAssets = data.assets || [];
    uploadedAssets.forEach((asset) => state.motionSelectedAssets.add(asset.id));
    const uploadedIds = new Set(uploadedAssets.map((asset) => asset.id));
    state.creativeAssets = [...uploadedAssets, ...state.creativeAssets.filter((asset) => !uploadedIds.has(asset.id))];
    motionUploadBatch = { status: "success", files: files.map((file) => file.name) };
    $("motionImageInput").value = "";
    updateMotionUploadArea();
    renderMotionAssets();
    setMessage("motionMessage", `\u5df2\u8bc6\u522b ${(data.assets || []).length} \u5f20\u7d20\u6750\uff0c\u8bf7\u786e\u8ba4\u63a8\u8350\u52a8\u6548\u3002`, "ok");
    await loadMotionWorkspace();
  } catch (error) {
    motionUploadBatch = { status: "error", files: files.map((file) => file.name) };
    updateMotionUploadArea();
    renderMotionAssets();
    setMessage("motionMessage", error.message, "error");
  } finally {
    $("uploadMotionAssets").disabled = false;
  }
}

function motionPlanPayload(card) {
  return {
    text_policy: $("motionTextPolicy")?.value || "visual_only",
    aspect_ratio: $("motionAspectRatio")?.value || "source",
    focus: card.querySelector('[data-motion-field="focus"]')?.value || "effect",
    preset: card.querySelector('[data-motion-field="preset"]')?.value || "camera",
    intensity: card.querySelector('[data-motion-field="intensity"]')?.value || "standard",
    direction: "",
    custom_instruction: "",
  };
}

async function saveMotionPlan(card) {
  const assetId = card?.dataset.motionAssetId || "";
  if (!assetId) return;
  const data = await api(`/api/creative-assets/${encodeURIComponent(assetId)}/motion-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(motionPlanPayload(card)),
  });
  const asset = state.creativeAssets.find((item) => item.id === assetId);
  if (asset) asset.motion_plan = data.motion_plan;
}

async function saveGlobalMotionPlans() {
  const cards = Array.from(document.querySelectorAll("[data-motion-asset-id]"));
  await Promise.all(cards.map((card) => saveMotionPlan(card)));
  setMessage("motionMessage", "\u6587\u5b57\u7b56\u7565\u548c\u6210\u7247\u6bd4\u4f8b\u5df2\u66f4\u65b0\u3002", "ok");
}

async function submitSelectedMotion(assetIds = null) {
  const selected = Array.from(new Set(assetIds || Array.from(state.motionSelectedAssets)));
  if (!selected.length) {
    setMessage("motionMessage", "\u8bf7\u81f3\u5c11\u52fe\u9009\u4e00\u5f20\u53ef\u751f\u6210\u7d20\u6750\u3002", "error");
    return;
  }
  if (motionSubmitBusy) {
    setMessage("motionMessage", "\u4efb\u52a1\u5df2\u5728\u63d0\u4ea4\uff0c\u8bf7\u52ff\u91cd\u590d\u70b9\u51fb\u3002");
    return;
  }
  const idempotencyKey = pendingMotionSubmissionKey(selected);
  setMotionSubmitState(selected, true);
  setMessage("motionMessage", `\u5df2\u53d7\u7406 ${selected.length} \u6761\u52a8\u6548\u4efb\u52a1\uff0c\u6b63\u5728\u5b89\u5168\u63d0\u4ea4\uff0c\u8bf7\u52ff\u91cd\u590d\u70b9\u51fb\u2026`);
  await new Promise((resolve) => requestAnimationFrame(resolve));
  const submitController = new AbortController();
  let submitTimedOut = false;
  const submitTimeout = setTimeout(() => {
    submitTimedOut = true;
    submitController.abort();
  }, 20000);
  try {
    const data = await api("/api/image-motion/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ creative_asset_ids: selected, idempotency_key: idempotencyKey }),
      signal: submitController.signal,
    });
    const returnedJobs = data.jobs || [];
    clearPendingMotionSubmissionKey(selected);
    const returnedIds = new Set(returnedJobs.map((job) => job.id));
    state.imageMotionJobs = [...returnedJobs, ...state.imageMotionJobs.filter((job) => !returnedIds.has(job.id))];
    returnedJobs.forEach((job) => {
      if (job.creative_asset_id) state.motionActiveVersions.set(job.creative_asset_id, job.id);
    });
    renderMotionAssets();
    const reusedCount = returnedJobs.filter((job) => job.idempotent_reuse).length;
    setMessage(
      "motionMessage",
      reusedCount
        ? "\u5df2\u6062\u590d " + reusedCount + " \u6761\u4e0a\u6b21\u63d0\u4ea4\u7684\u4efb\u52a1\uff0c\u672a\u91cd\u590d\u521b\u5efa\u6216\u6263\u8d39\u3002"
        : "\u5df2\u521b\u5efa " + returnedJobs.length + " \u6761\u65b0\u4efb\u52a1\uff0c\u53ef\u5728\u5f53\u524d\u7d20\u6750\u548c\u4efb\u52a1\u4e2d\u5fc3\u67e5\u770b\u8fdb\u5ea6\u3002",
      "ok"
    );
    await loadMotionWorkspace();
    await loadJobs();
  } catch (error) {
    setMessage(
      "motionMessage",
      submitTimedOut || error.name === "AbortError"
        ? "\u63d0\u4ea4\u54cd\u5e94\u8d85\u65f6\uff0c\u5df2\u81ea\u52a8\u89e3\u9664\u6309\u94ae\u9501\u5b9a\u3002\u8bf7\u5237\u65b0\u4efb\u52a1\u4e2d\u5fc3\u540e\u91cd\u8bd5\uff0c\u76f8\u540c\u4efb\u52a1\u4e0d\u4f1a\u91cd\u590d\u521b\u5efa\u3002"
        : error.message,
      "error"
    );
  } finally {
    clearTimeout(submitTimeout);
    setMotionSubmitState(selected, false);
  }
}

function setMotionSubmitState(assetIds, busy) {
  motionSubmitBusy = busy;
  motionSubmittingAssets.clear();
  if (busy) assetIds.forEach((assetId) => motionSubmittingAssets.add(String(assetId)));

  const batchButton = $("generateReadyMotion");
  if (batchButton) {
    batchButton.disabled = busy;
    batchButton.toggleAttribute("aria-busy", busy);
    batchButton.textContent = busy ? `\u6b63\u5728\u63d0\u4ea4 ${assetIds.length} \u6761\u4efb\u52a1\u2026` : "\u6279\u91cf\u751f\u6210\u5df2\u52fe\u9009\u7d20\u6750";
  }
  document.querySelectorAll("[data-motion-generate-asset]").forEach((button) => {
    const isTarget = motionSubmittingAssets.has(button.dataset.motionGenerateAsset || "");
    button.disabled = busy;
    button.toggleAttribute("aria-busy", busy && isTarget);
    button.textContent = busy
      ? isTarget
        ? "\u6b63\u5728\u63d0\u4ea4\u2026"
        : "\u7b49\u5f85\u5f53\u524d\u63d0\u4ea4\u2026"
      : "\u751f\u6210\u52a8\u6001\u77ed\u89c6\u9891";
    const hint = button.nextElementSibling;
    if (hint) {
      hint.textContent = busy
        ? isTarget
          ? "\u4efb\u52a1\u5df2\u53d7\u7406\uff0c\u6b63\u5728\u6392\u961f\uff0c\u8bf7\u52ff\u91cd\u590d\u70b9\u51fb\u3002"
          : "\u5df2\u6709\u5176\u4ed6\u7d20\u6750\u6b63\u5728\u63d0\u4ea4\uff0c\u5b8c\u6210\u6216\u8d85\u65f6\u540e\u5c06\u81ea\u52a8\u6062\u590d\u3002"
        : "\u9ed8\u8ba4 5 \u79d2\u30011080p\u3001\u5355\u955c\u5934\uff1b\u751f\u6210\u540e\u53ef\u9884\u89c8\u548c\u4e0b\u8f7d\u3002";
    }
  });
}

async function refreshMotionJobs(silent = true) {
  if (state.motionRefreshBusy) return;
  state.motionRefreshBusy = true;
  try {
    const data = await api("/api/image-motion/jobs/refresh", { method: "POST" });
    state.imageMotionJobs = data.jobs || [];
    await loadMotionWorkspace();
  } catch (error) {
    if (!silent) setMessage("motionMessage", error.message, "error");
  } finally {
    state.motionRefreshBusy = false;
  }
}

async function regenerateMotionJob(jobId, action) {
  if (motionRetryBusyJobs.has(jobId)) {
    setMessage("motionMessage", "\u8be5\u7248\u672c\u6b63\u5728\u521b\u5efa\u91cd\u8bd5\u4efb\u52a1\uff0c\u8bf7\u52ff\u91cd\u590d\u70b9\u51fb\u3002");
    return;
  }
  motionRetryBusyJobs.add(jobId);
  const buttons = Array.from(document.querySelectorAll('[data-motion-retry][data-job-id="' + jobId + '"]'));
  buttons.forEach((button) => {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  });
  setMessage("motionMessage", "\u6b63\u5728\u521b\u5efa\u65b0\u7248\u672c\uff0c\u8bf7\u52ff\u91cd\u590d\u70b9\u51fb...");
  try {
    const data = await api("/api/image-motion/jobs/" + encodeURIComponent(jobId) + "/regenerate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    const newJob = data.job;
    state.motionActiveVersions.set(newJob.creative_asset_id, newJob.id);
    state.imageMotionJobs = [newJob, ...state.imageMotionJobs.filter((item) => item.id !== newJob.id)];
    renderMotionAssets();
    setMessage("motionMessage", "\u5df2\u521b\u5efa\u7248\u672c " + newJob.version + "\uff0c\u6b63\u5728\u751f\u6210\u5e76\u6267\u884c\u8d28\u68c0\u3002", "ok");
    await loadMotionWorkspace();
    await loadJobs();
  } catch (error) {
    setMessage("motionMessage", error.message, "error");
  } finally {
    motionRetryBusyJobs.delete(jobId);
    buttons.forEach((button) => {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
  }
}
async function exportMotionZip() {
  const jobIds = Array.from(state.motionSelectedJobs);
  if (!jobIds.length) {
    setMessage("motionMessage", "\u8bf7\u5148\u52fe\u9009\u81f3\u5c11\u4e00\u4e2a\u5df2\u901a\u8fc7\u8d28\u68c0\u7684\u89c6\u9891\u7248\u672c\u3002", "error");
    return;
  }
  try {
    const response = await fetch("/api/image-motion/exports", {
      method: "POST",
      credentials: "same-origin",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ job_ids: jobIds, muted: Boolean($("motionExportMuted")?.checked) }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(formatErrorDetail(body.detail, response.statusText));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "selling-point-image-motion.zip";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    setMessage("motionMessage", error.message, "error");
  }
}

async function showMotionTask(jobId) {
  try {
    const job = await api(`/api/image-motion/jobs/${encodeURIComponent(jobId)}`);
    setAppMode("image_motion");
    if ((state.options.categories || []).includes(job.category)) {
      $("motionCategorySelect").value = job.category;
      syncMotionModelOptions();
    }
    if (Array.from($("motionModelSelect")?.options || []).some((option) => option.value === job.model)) {
      $("motionModelSelect").value = job.model;
    }
    state.motionActiveVersions.set(job.creative_asset_id, job.id);
    await loadMotionWorkspace();
    const activeCard = document.querySelector('[data-motion-asset-id="' + job.creative_asset_id + '"]');
    (activeCard || $("motionWorkflow"))?.scrollIntoView({ behavior: "smooth", block: "start" });
    if (job.status === "failed") {
      setMessage("motionMessage", motionFailureGuidance(job), "error");
    }
  } catch (error) {
    setMessage("motionMessage", error.message, "error");
  }
}

async function initializeMotionWorkflow() {
  syncMotionCategoryOptions();
  await loadMotionWorkspace();
}

document.querySelectorAll("[data-app-mode]").forEach((button) => {
  button.addEventListener("click", () => setAppMode(button.dataset.appMode));
});
$("motionCategorySelect")?.addEventListener("change", () => {
  motionUploadBatch = { status: "idle", files: [] };
  updateMotionUploadArea();
  $("motionImageInput").value = "";
  renderMotionAssets();
  $("motionModelSearch").value = "";
  syncMotionModelOptions();
  loadMotionWorkspace();
});
$("motionModelSearch")?.addEventListener("input", syncMotionModelOptions);
$("motionModelSelect")?.addEventListener("change", () => {
  motionUploadBatch = { status: "idle", files: [] };
  updateMotionUploadArea();
  $("motionImageInput").value = "";
  renderMotionAssets();
  loadMotionWorkspace();
});
$("motionImageInput")?.addEventListener("change", () => {
  const files = Array.from($("motionImageInput").files || []);
  const count = files.length;
  motionUploadBatch = count ? { status: "selected", files: files.map((file) => file.name) } : { status: "idle", files: [] };
  updateMotionUploadArea();
  renderMotionAssets();
  setMessage("motionMessage", count ? `\u5df2\u9009\u62e9 ${count} \u5f20\u56fe\u7247\u3002` : "");
});
$("uploadMotionAssets")?.addEventListener("click", uploadMotionAssets);
$("generateReadyMotion")?.addEventListener("click", () => submitSelectedMotion());
$("exportMotionZip")?.addEventListener("click", exportMotionZip);
$("motionTextPolicy")?.addEventListener("change", saveGlobalMotionPlans);
$("motionAspectRatio")?.addEventListener("change", saveGlobalMotionPlans);
$("taskTypeFilter")?.addEventListener("change", loadJobs);
$("motionAssets")?.addEventListener("change", async (event) => {
  const assetCheckbox = event.target.closest("[data-motion-select-asset]");
  if (assetCheckbox) {
    if (assetCheckbox.checked) state.motionSelectedAssets.add(assetCheckbox.dataset.motionSelectAsset);
    else state.motionSelectedAssets.delete(assetCheckbox.dataset.motionSelectAsset);
    return;
  }
  const jobCheckbox = event.target.closest("[data-motion-export-job]");
  if (jobCheckbox) {
    if (jobCheckbox.checked) state.motionSelectedJobs.add(jobCheckbox.dataset.motionExportJob);
    else state.motionSelectedJobs.delete(jobCheckbox.dataset.motionExportJob);
    return;
  }
  const field = event.target.closest("[data-motion-field]");
  if (field) {
    try {
      await saveMotionPlan(field.closest("[data-motion-asset-id]"));
      setMessage("motionMessage", "\u52a8\u6548\u8ba1\u5212\u5df2\u4fdd\u5b58\u3002", "ok");
    } catch (error) {
      setMessage("motionMessage", error.message, "error");
    }
  }
});
$("motionAssets")?.addEventListener("click", async (event) => {
  const generate = event.target.closest("[data-motion-generate-asset]");
  if (generate) {
    await submitSelectedMotion([generate.dataset.motionGenerateAsset]);
    return;
  }
  const version = event.target.closest("[data-motion-version]");
  if (version) {
    state.motionActiveVersions.set(version.dataset.assetId, version.dataset.motionVersion);
    renderMotionAssets();
    return;
  }
  const retry = event.target.closest("[data-motion-retry]");
  if (retry) regenerateMotionJob(retry.dataset.jobId, retry.dataset.motionRetry);
});
$("jobs")?.addEventListener("click", (event) => {
  const button = event.target.closest(".load-motion-result");
  if (button) showMotionTask(button.dataset.motionJobId);
});

setTimeout(() => {
  if (state.appReady && !state.creativeAssets.length) initializeMotionWorkflow();
}, 0);
