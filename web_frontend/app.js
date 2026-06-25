const DEFAULT_FEATURE_COUNT = 6;

const state = {
  authEnabled: true,
  authToken: "",
  appReady: false,
  jobsTimer: null,
  protectedObjectUrls: new Map(),
  options: { categories: [], models_by_category: {} },
  features: [],
  featuresRequestId: 0,
  selectedFeatures: [],
  videoTypes: ["问题解决/痛点挖掘型", "产品展示/功能介绍型", "开箱体验型", "场景化/生活方式型", "测评/对比型"],
  selectedVideoTypes: ["问题解决/痛点挖掘型"],
  activeJobId: "",
  renderedJobId: "",
  activeVariantIndex: 0,
  currentResultJob: null,
  videoJobs: [],
  storyboardVideoJobs: [],
  canvasJobs: [],
  canvasJobsSignature: "",
  canvasProvider: "",
  canvasModelId: "",
  canvasPollTimer: null,
  storyboardShots: [],
  canvasGenerating: new Set(),
  bulkCanvasGenerating: false,
  productImageAssets: [],
  selectedProductImageId: "",
  productImageAsset: null,
  competitorResearchTimer: null,
  lastDiscoveredAsins: [],
  lastDiscoveredVideoIds: [],
};

const $ = (id) => document.getElementById(id);

function authHeaders(headers = {}) {
  const nextHeaders = new Headers(headers);
  if (state.authToken) {
    nextHeaders.set("Authorization", `Bearer ${state.authToken}`);
  }
  return nextHeaders;
}

function formatErrorDetail(value, fallback = "请求失败") {
  if (value == null || value === "") return fallback;
  if (value instanceof Error) return formatErrorDetail(value.message, fallback);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    const lines = value.map((item) => formatErrorDetail(item, "")).filter(Boolean);
    return lines.length ? lines.join("；") : fallback;
  }
  if (typeof value === "object") {
    const location = Array.isArray(value.loc) ? value.loc.filter((item) => item !== "body").join(".") : "";
    const message = value.msg || value.message || value.detail || value.error;
    if (message) return `${location ? `${location}: ` : ""}${formatErrorDetail(message, fallback)}`;
    try {
      return JSON.stringify(value);
    } catch (_) {
      return fallback;
    }
  }
  return String(value);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: authHeaders(options.headers || {}),
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = formatErrorDetail(body.detail || body.message || body.error, message);
    } catch (_) {}
    if (response.status === 401) {
      clearAuth(message);
    }
    throw new Error(message);
  }
  return response.json();
}

async function fetchProtectedBlob(path) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: authHeaders(),
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = formatErrorDetail(body.detail || body.message || body.error, message);
    } catch (_) {}
    if (response.status === 401) {
      clearAuth(message);
    }
    throw new Error(message);
  }
  return {
    blob: await response.blob(),
    contentDisposition: response.headers.get("Content-Disposition") || "",
  };
}

function filenameFromDisposition(contentDisposition, fallback) {
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) return decodeURIComponent(utf8Match[1]);
  const plainMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  return plainMatch ? plainMatch[1] : fallback;
}

async function downloadJob(jobId) {
  if (!jobId) return;
  const path = `/api/jobs/${encodeURIComponent(jobId)}/download`;
  const { blob, contentDisposition } = await fetchProtectedBlob(path);
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filenameFromDisposition(contentDisposition, `video-script-${jobId}.xlsx`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function showAuth(message = "") {
  $("authScreen").classList.remove("hidden");
  $("appShell").classList.add("hidden");
  if (message) setMessage("authMessage", message, "error");
  setTimeout(() => $("authPassword").focus(), 0);
}

function showApp() {
  $("authScreen").classList.add("hidden");
  $("appShell").classList.remove("hidden");
}

function clearProtectedObjectUrls() {
  state.protectedObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  state.protectedObjectUrls.clear();
}

function clearAuth(message = "请重新输入访问密码。") {
  state.authToken = "";
  if (state.jobsTimer) {
    clearInterval(state.jobsTimer);
    state.jobsTimer = null;
  }
  state.appReady = false;
  clearProtectedObjectUrls();
  showAuth(message);
}

async function submitAuth(event) {
  event.preventDefault();
  const password = $("authPassword").value.trim();
  if (!password && state.authEnabled) {
    setMessage("authMessage", "请输入访问密码。", "error");
    return;
  }
  $("authSubmit").disabled = true;
  setMessage("authMessage", "正在校验...");
  try {
    const response = await fetch("/api/auth/login", {
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
    state.authToken = password;
    $("authPassword").value = "";
    setMessage("authMessage", "");
    showApp();
    await startApp();
  } catch (error) {
    setMessage("authMessage", error.message, "error");
  } finally {
    $("authSubmit").disabled = false;
  }
}

async function initializeAuth() {
  try {
    const response = await fetch("/api/auth/status", { credentials: "same-origin" });
    const status = response.ok ? await response.json() : { enabled: true };
    state.authEnabled = Boolean(status.enabled);
    if (!state.authEnabled) {
      showApp();
      await startApp();
      return;
    }
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
    showAuth();
  } catch (error) {
    clearAuth(error.message || "请重新输入访问密码。");
  }
}

function setMessage(id, text, kind = "") {
  const node = $(id);
  node.textContent = formatErrorDetail(text, "");
  node.className = `message ${kind}`.trim();
}

function optionHtml(value, label = value, selected = false) {
  return `<option value="${escapeAttr(value)}" ${selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
}

function normalizeSearchText(value) {
  return String(value || "").trim().toLowerCase();
}

function currentCategoryModels() {
  const category = $("categorySelect").value;
  return state.options.models_by_category[category] || [];
}

function renderModelOptions(preferredModel = "") {
  const select = $("modelSelect");
  const query = normalizeSearchText($("modelSearch").value);
  const models = currentCategoryModels();
  const filtered = query
    ? models.filter((item) => normalizeSearchText(item).includes(query))
    : models;
  if (!filtered.length) {
    select.innerHTML = '<option value="">未找到匹配型号</option>';
    select.value = "";
    return;
  }
  const selectedModel = filtered.some((item) => String(item) === String(preferredModel)) ? preferredModel : filtered[0];
  select.innerHTML = filtered
    .map((item) => optionHtml(item, String(item).trim(), String(item) === String(selectedModel)))
    .join("");
  select.value = selectedModel;
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
  if (!$("metrics")) return;
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
  $("modelSearch").value = "";
  renderModelOptions();
  loadFeatures();
  updateSelectionSummary();
}

function filterModels() {
  const currentModel = $("modelSelect").value;
  renderModelOptions(currentModel);
  loadFeatures();
  updateSelectionSummary();
}

async function loadFeatures() {
  const requestId = ++state.featuresRequestId;
  const category = $("categorySelect").value;
  const model = $("modelSelect").value;
  if (!category || !model) {
    state.features = [];
    state.selectedFeatures = [];
    renderFeaturePicker();
    return;
  }
  const data = await api(`/api/features?category=${encodeURIComponent(category)}&model=${encodeURIComponent(model)}`);
  if (requestId !== state.featuresRequestId) return;
  state.features = data.features;
  state.selectedFeatures = data.features.slice(0, DEFAULT_FEATURE_COUNT);
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
    platform: "TikTok / Reels / Shorts",
    target_market: "北美 (US/CA)",
    variant_count: Number(data.get("variant_count") || 2),
    category: data.get("category"),
    model: data.get("model"),
    selected_features: state.selectedFeatures,
    video_usage: "站外种草",
    video_type: state.selectedVideoTypes,
    expected_duration: Number(data.get("expected_duration") || 30),
    project_type: "常规上新",
    use_competitor_context: data.get("use_competitor_context") === "on",
    use_hotspot_context: data.get("use_hotspot_context") === "on",
    target_audience: "",
    pain_points: "",
    custom_requirements: data.get("custom_requirements") || "",
  };
}

function splitList(value) {
  return String(value || "")
    .split(/[\n,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function splitUrls(value) {
  return String(value || "")
    .split(/[\n,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function competitorCategoryValue() {
  return ($("competitorCategory")?.value || "").trim() || $("categorySelect")?.value || "";
}

function competitorPayload() {
  return {
    category: competitorCategoryValue(),
    target_market: $("competitorMarket")?.value || "北美 (US/CA)",
    amazon_domain: ($("competitorDomain")?.value || "").trim(),
    brands: splitList($("competitorBrands")?.value || ""),
    keywords: splitList($("competitorKeywords")?.value || ""),
    asins: splitList($("competitorAsins")?.value || ""),
  };
}

function competitorSourceValue() {
  return $("competitorSource")?.value || "";
}

function competitorPlatformForSource(source) {
  if (source === "rainforest") return "Amazon";
  if (source === "youtube") return "YouTube";
  if (source === "instagram") return "Instagram";
  if (source === "tiktok") return "TikTok";
  if (source === "pinterest") return "Pinterest";
  if (source === "facebook") return "Facebook";
  return "";
}

function renderCompetitorDiscovery(data) {
  const asins = data.asins || [];
  state.lastDiscoveredAsins = asins.map((item) => item.asin).filter(Boolean);
  if (!asins.length) {
    $("competitorDiscovery").innerHTML = '<div class="empty-state small"><strong>未发现 ASIN</strong><span>可换关键词或手动输入重点 ASIN。</span></div>';
    return;
  }
  $("competitorDiscovery").innerHTML = `
    <div class="discovery-head">
      <strong>发现 ${asins.length} 个 ASIN</strong>
      <span>${escapeHtml(data.amazon_domain || "")}</span>
    </div>
    <div class="asin-list">
      ${asins
        .map(
          (item) => `
            <button type="button" class="asin-chip" data-asin="${escapeAttr(item.asin)}">
              <strong>${escapeHtml(item.asin)}</strong>
              <span>${escapeHtml(item.brand || item.source_query || "")}</span>
            </button>
          `
        )
        .join("")}
    </div>
  `;
}

function renderYouTubeDiscovery(data) {
  const assets = data.assets || [];
  state.lastDiscoveredVideoIds = assets
    .map((asset) => asset.metadata?.youtube_video_id || asset.metadata?.platform_content_id)
    .filter(Boolean);
  if (!assets.length) {
    $("competitorDiscovery").innerHTML = '<div class="empty-state small"><strong>未发现 YouTube 视频</strong><span>可换关键词、品牌或直接粘贴社媒 URL。</span></div>';
    return;
  }
  $("competitorDiscovery").innerHTML = `
    <div class="discovery-head">
      <strong>发现 ${assets.length} 条 YouTube 候选素材</strong>
      <span>${escapeHtml((data.queries || []).map((item) => item.query).join(" / "))}</span>
    </div>
    <div class="asin-list">
      ${assets
        .map((asset) => {
          const videoId = asset.metadata?.youtube_video_id || asset.metadata?.platform_content_id || "";
          return `
            <button type="button" class="asin-chip video-chip" data-video-id="${escapeAttr(videoId)}">
              <strong>${escapeHtml(videoId || asset.id)}</strong>
              <span>${escapeHtml(asset.title || asset.channel || "")}</span>
            </button>
          `;
        })
        .join("")}
    </div>
  `;
}

async function importSocialUrls() {
  const urls = splitUrls($("competitorSocialUrls")?.value || "");
  if (!urls.length) {
    setMessage("competitorMessage", "请先粘贴至少一个社媒素材 URL。", "error");
    return;
  }
  setMessage("competitorMessage", "正在导入社媒 URL...");
  try {
    const payload = competitorPayload();
    const data = await api("/api/social-assets/import-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        urls,
        category: payload.category,
        target_market: payload.target_market,
        brands: payload.brands,
        fetch_oembed: true,
      }),
    });
    renderCompetitorAssets(data.assets || []);
    const errors = data.errors?.length ? `，失败 ${data.errors.length} 条` : "";
    setMessage("competitorMessage", `已导入/更新 ${data.upsert?.total || 0} 条社媒素材${errors}。`, "ok");
  } catch (error) {
    setMessage("competitorMessage", error.message, "error");
  }
}

async function discoverYouTube() {
  setMessage("competitorMessage", "正在通过 YouTube API 发现竞品视频...");
  try {
    const payload = competitorPayload();
    const data = await api("/api/competitor-sources/youtube/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: payload.category,
        target_market: payload.target_market,
        brands: payload.brands,
        keywords: payload.keywords,
      }),
    });
    renderYouTubeDiscovery(data);
    renderCompetitorAssets(data.assets || []);
    setMessage("competitorMessage", `已发现 ${data.assets?.length || 0} 条 YouTube 候选素材，可继续刷新入库。`, "ok");
  } catch (error) {
    setMessage("competitorMessage", error.message, "error");
  }
}

function youtubeInputsFromSocialUrls() {
  return splitUrls($("competitorSocialUrls")?.value || "").filter((url) => /youtu\.be|youtube\.com/i.test(url));
}

async function refreshYouTube() {
  setMessage("competitorMessage", "正在刷新 YouTube 素材入库...");
  try {
    const payload = competitorPayload();
    const videoIds = Array.from(new Set([...state.lastDiscoveredVideoIds, ...youtubeInputsFromSocialUrls()]));
    const data = await api("/api/competitor-assets/youtube/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: payload.category,
        target_market: payload.target_market,
        brands: payload.brands,
        keywords: payload.keywords,
        video_ids: videoIds,
        use_discovery: true,
      }),
    });
    renderCompetitorAssets(data.assets || []);
    const errors = data.errors?.length ? `，失败 ${data.errors.length} 条` : "";
    setMessage("competitorMessage", `已入库/更新 ${data.upsert?.total || 0} 条 YouTube 素材${errors}。`, "ok");
  } catch (error) {
    setMessage("competitorMessage", error.message, "error");
  }
}

async function refreshSocialThumbnails() {
  setMessage("competitorMessage", "正在刷新已入库素材缩略图...");
  try {
    const params = {
      q: ($("competitorAssetQuery")?.value || "").trim(),
      category: ($("competitorCategory")?.value || "").trim(),
      source: competitorSourceValue(),
      media_type: $("competitorMediaType")?.value || "",
      limit: 20,
    };
    const data = await api("/api/competitor-assets/social/thumbnails/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    if (data.assets?.length) {
      renderCompetitorAssets(data.assets);
    }
    const errors = data.errors?.length ? `，${data.errors.length} 条暂无法刷新` : "";
    setMessage("competitorMessage", `已刷新 ${data.refreshed_count || 0} 条缩略图${errors}。`, "ok");
  } catch (error) {
    setMessage("competitorMessage", error.message, "error");
  }
}

async function discoverRainforest() {
  setMessage("competitorMessage", "正在通过 Rainforest 搜索 Amazon ASIN...");
  try {
    const payload = competitorPayload();
    const data = await api("/api/competitor-sources/rainforest/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: payload.category,
        target_market: payload.target_market,
        amazon_domain: payload.amazon_domain,
        brands: payload.brands,
        keywords: payload.keywords,
      }),
    });
    renderCompetitorDiscovery(data);
    setMessage("competitorMessage", `已发现 ${data.asins?.length || 0} 个候选 ASIN，可继续刷新入库。`, "ok");
  } catch (error) {
    setMessage("competitorMessage", error.message, "error");
  }
}

async function refreshRainforest() {
  setMessage("competitorMessage", "正在刷新 Rainforest 素材入库...");
  try {
    const payload = competitorPayload();
    const mergedAsins = Array.from(new Set([...payload.asins, ...state.lastDiscoveredAsins]));
    const data = await api("/api/competitor-assets/rainforest/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...payload,
        asins: mergedAsins,
        use_discovery: true,
      }),
    });
    renderCompetitorAssets(data.assets || []);
    const errors = data.errors?.length ? `，失败 ${data.errors.length} 条` : "";
    setMessage("competitorMessage", `已入库/更新 ${data.upsert?.total || 0} 条商品素材${errors}。`, "ok");
  } catch (error) {
    setMessage("competitorMessage", error.message, "error");
  }
}

async function loadCompetitorAssets() {
  setMessage("competitorMessage", "正在加载已入库竞品素材...");
  try {
    const params = new URLSearchParams({
      limit: "8",
    });
    const category = ($("competitorCategory")?.value || "").trim();
    const source = competitorSourceValue();
    const q = ($("competitorAssetQuery")?.value || "").trim();
    const mediaType = $("competitorMediaType")?.value || "";
    if (category) params.set("category", category);
    if (source) params.set("source", source);
    if (q) params.set("q", q);
    if (mediaType) params.set("media_type", mediaType);
    const data = await api(`/api/competitor-assets/search?${params.toString()}`);
    renderCompetitorAssets(data.assets || []);
    setMessage("competitorMessage", `已加载 ${data.count || 0} 条素材。`, "ok");
  } catch (error) {
    setMessage("competitorMessage", error.message, "error");
  }
}

function renderCompetitorAssets(assets, targetId = "competitorAssets") {
  const target = $(targetId);
  if (!target) return;
  if (!assets.length) {
    target.innerHTML = '<div class="empty-state small"><strong>暂无素材</strong><span>可先发现 ASIN 或刷新入库。</span></div>';
    return;
  }
  target.innerHTML = assets.map(renderCompetitorAsset).join("");
}

function renderCompetitorAsset(asset) {
  const media = asset.media || [];
  const videos = media.filter((item) => item.media_type === "video");
  const firstImage = asset.image_url || media.find((item) => item.thumbnail_url || item.media_url)?.thumbnail_url || media.find((item) => item.media_type === "image")?.media_url || "";
  const tags = (asset.ai_tags || []).slice(0, 5).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
  const mediaLine = `${videos.length} 条视频 / ${media.length} 个媒体项`;
  const sourceLabel = asset.platform || asset.source_type || "素材";
  const assetKey = asset.asin ? `ASIN ${asset.asin}` : sourceLabel;
  const evidenceLabel = asset.platform ? `打开 ${asset.platform} 证据` : "打开素材证据";
  const contentId = asset.metadata?.platform_content_id || asset.metadata?.youtube_video_id || "";
  const publishedAt = asset.metadata?.published_at ? `发布 ${formatDateTime(asset.metadata.published_at)}` : "";
  const embedLink = asset.embed_url ? `<a href="${escapeAttr(asset.embed_url)}" target="_blank" rel="noreferrer">嵌入预览</a>` : "";
  return `
    <article class="competitor-asset">
      ${firstImage ? `<img src="${escapeAttr(firstImage)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : `<div class="asset-image-empty"><strong>${escapeHtml(sourceLabel)}</strong><span>无缩略图</span></div>`}
      <div>
        <div class="asset-meta">
          <span>${escapeHtml(asset.brand || "Unknown")}</span>
          <span>${escapeHtml(assetKey)}</span>
          ${contentId ? `<span>ID ${escapeHtml(contentId)}</span>` : ""}
          <span>分数 ${escapeHtml(asset.quality_score || 0)}</span>
          ${publishedAt ? `<span>${escapeHtml(publishedAt)}</span>` : ""}
        </div>
        <h4>${escapeHtml(asset.title || "Untitled")}</h4>
        <p>${escapeHtml(asset.ai_analysis || "")}</p>
        <div class="asset-tags">${tags}</div>
        <div class="asset-foot">
          <span>${escapeHtml(mediaLine)}</span>
          <span class="asset-links">
            ${embedLink}
            <a href="${escapeAttr(asset.source_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(evidenceLabel)}</a>
          </span>
        </div>
      </div>
    </article>
  `;
}

async function submitCompetitorResearch(event) {
  event.preventDefault();
  const question = ($("competitorQuestion")?.value || "").trim();
  if (!question) {
    setMessage("competitorMessage", "请先填写业务调研问题。", "error");
    return;
  }
  if (state.competitorResearchTimer) {
    clearTimeout(state.competitorResearchTimer);
    state.competitorResearchTimer = null;
  }
  setMessage("competitorMessage", "正在提交竞品调研报告任务...");
  try {
    const data = await api("/api/competitor-research/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        category: competitorCategoryValue(),
        target_market: $("competitorMarket")?.value || "",
        platform: competitorPlatformForSource(competitorSourceValue()),
        source: competitorSourceValue(),
        top_k: 8,
      }),
    });
    setMessage("competitorMessage", `调研任务 ${data.job_id} 已提交，正在生成报告...`, "ok");
    await pollCompetitorResearchJob(data.job_id, 0);
  } catch (error) {
    setMessage("competitorMessage", error.message, "error");
  }
}

async function pollCompetitorResearchJob(jobId, attempt) {
  const job = await api(`/api/competitor-research/jobs/${encodeURIComponent(jobId)}`);
  if (job.status === "succeeded") {
    renderCompetitorReport(job);
    setMessage("competitorMessage", "调研报告已生成。", "ok");
    return;
  }
  if (job.status === "failed") {
    setMessage("competitorMessage", job.error_message || "调研报告生成失败。", "error");
    return;
  }
  setMessage("competitorMessage", `${job.current_step || "正在生成"} ${Number(job.progress || 0)}%`);
  if (attempt < 90) {
    state.competitorResearchTimer = setTimeout(() => pollCompetitorResearchJob(jobId, attempt + 1), 2000);
  }
}

function renderCompetitorReport(job) {
  $("competitorReportSection").classList.remove("hidden");
  $("competitorReportMeta").textContent = `任务 ${job.id} · ${formatDateTime(job.completed_at || job.updated_at)} · 证据 ${job.evidence?.length || 0} 条`;
  $("competitorReportBody").innerHTML = markdownToHtml(job.report || "");
  renderCompetitorAssets(job.evidence || [], "competitorEvidence");
  $("competitorReportSection").scrollIntoView({ behavior: "smooth", block: "start" });
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
  if ($("competitorCategory") && category !== "未选择") {
    $("competitorCategory").placeholder = `默认：${category}`;
  }
  $("selectionSummary").innerHTML = `
    <div><span>品类</span><strong>${escapeHtml(category)}</strong></div>
    <div><span>型号</span><strong>${escapeHtml(model)}</strong></div>
    <div><span>已选卖点</span><strong>${features.length}</strong></div>
    <ul>${features.slice(0, DEFAULT_FEATURE_COUNT).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>暂无卖点</li>"}</ul>
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
    if ($("metrics")) await loadSummary();
    await loadOptions();
  } catch (error) {
    setMessage("uploadMessage", error.message, "error");
  } finally {
    event.target.value = "";
  }
}

async function loadJobs() {
  try {
    const data = await api("/api/jobs");
    const jobs = data.jobs || [];
    if (!jobs.length) {
      $("jobs").innerHTML = '<div class="empty-state compact-empty"><strong>暂无任务</strong><span>提交脚本后会显示在这里。</span></div>';
      return;
    }
    $("jobs").innerHTML = jobs.map(renderJob).join("");
    await revealCompletedResult(jobs);
  } catch (error) {
    $("jobs").innerHTML = `<div class="empty-state"><strong>任务加载失败</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function renderJob(job) {
  const request = job.request || {};
  const model = request.model || "未命名产品";
  const category = request.category || "";
  const title = category ? `${model} · ${category}` : model;
  const variants =
    job.status === "succeeded"
      ? `<button class="load-result" type="button" data-job-id="${escapeAttr(job.id)}">查看脚本</button>`
      : "";
  const error = job.error_message ? `<div class="message error">${escapeHtml(job.error_message)}</div>` : "";
  const step = job.status === "succeeded" ? "" : job.current_step || "";
  const stepHtml = step ? `<div class="message">${escapeHtml(step)}</div>` : "";
  const finishedAt =
    job.status === "succeeded" || job.status === "failed"
      ? `<div class="job-time">完成时间：${escapeHtml(formatDateTime(job.completed_at || job.updated_at))}</div>`
      : "";
  return `
    <article class="job">
      <div class="job-head">
        <span>${escapeHtml(title)}</span>
      </div>
      <div class="progress"><div style="width:${Number(job.progress || 0)}%"></div></div>
      ${stepHtml}
      ${finishedAt}
      ${error}
      ${variants}
    </article>
  `;
}

async function revealCompletedResult(jobs) {
  const completedSummary = (jobs || []).find((job) => {
    if (job.status !== "succeeded" || !(job.has_variants || job.variant_count || (job.variants || []).length)) return false;
    return state.activeJobId ? job.id === state.activeJobId : true;
  });
  if (!completedSummary || completedSummary.id === state.renderedJobId) return;
  const completed = (completedSummary.variants || []).length
    ? completedSummary
    : await api(`/api/jobs/${encodeURIComponent(completedSummary.id)}`);
  renderResult(completed);
}

function hideResults() {
  $("resultSection").classList.add("hidden");
  $("resultTabs").innerHTML = "";
  $("resultBody").innerHTML = "";
  if ($("videoJobs")) $("videoJobs").innerHTML = "";
  if ($("videoMessage")) $("videoMessage").textContent = "";
  if ($("storyboardVideoJobs")) $("storyboardVideoJobs").innerHTML = "";
  if ($("storyboardVideoMessage")) $("storyboardVideoMessage").textContent = "";
  if (state.canvasPollTimer) {
    clearTimeout(state.canvasPollTimer);
    state.canvasPollTimer = null;
  }
  state.productImageAssets = [];
  state.selectedProductImageId = "";
  state.productImageAsset = null;
  state.canvasJobsSignature = "";
  state.currentResultJob = null;
}

function renderResult(job, variantIndex = 0) {
  const variants = job.variants || [];
  if (!variants.length) return;
  state.renderedJobId = job.id;
  state.activeVariantIndex = Math.max(0, Math.min(variantIndex, variants.length - 1));
  $("resultSection").classList.remove("hidden");
  $("mediaPanel").classList.remove("hidden");
  $("downloadResult").href = "#";
  $("downloadResult").dataset.jobId = job.id;
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
  state.canvasJobs = [];
  state.canvasJobsSignature = "";
  state.canvasProvider = "";
  state.canvasModelId = "";
  state.storyboardShots = [];
  state.storyboardVideoJobs = [];
  rerenderStoryboardCards();
  loadCanvasJobs(job.id).catch((error) => {
    console.warn("Failed to load storyboard image jobs", error);
  });
  loadProductImages(job.id).catch((error) => {
    console.warn("Failed to load product images", error);
  });
  loadStoryboardVideoJobs(job.id).catch((error) => {
    console.warn("Failed to load storyboard video jobs", error);
  });
  renderMediaPanel();
  renderVideoPanel(job);
  $("resultSection").scrollIntoView({ behavior: "smooth", block: "start" });
}

function rerenderStoryboardCards() {
  const job = state.currentResultJob;
  const variants = (job && job.variants) || [];
  const current = variants[state.activeVariantIndex] || {};
  $("storyboardCards").innerHTML = renderStoryboardCards(current.content || "");
  state.canvasJobsSignature = canvasJobsRenderSignature();
  hydrateProtectedImages();
}

function renderVariantContent(variant) {
  const label = variant.label ? `<div class="result-label">方案定位：${escapeHtml(variant.label)}</div>` : "";
  return `${label}<div class="script-markdown">${markdownToHtml(stripOverallVideoPrompt(variant.content || ""))}</div>`;
}

function stripOverallVideoPrompt(content) {
  return String(content || "")
    .replace(
      /\n*\s*(?:整体AI视频生成Prompt|整体 AI 视频生成 Prompt|Overall AI Video Generation Prompt)\s*(?:（English）|\(English\))?\s*[:：][\s\S]*?(?=\n\s*(?:产品动图片段|分镜拍摄参考|$))/gi,
      ""
    )
    .trim();
}

function currentProductImageId() {
  return currentProductImageAsset()?.id || "";
}

function currentProductImageAsset() {
  const assets = state.productImageAssets || [];
  const selected = assets.find((item) => item.id === state.selectedProductImageId);
  return selected || assets[0] || null;
}

function renderMediaPanel() {
  const assets = state.productImageAssets || [];
  const asset = currentProductImageAsset();
  state.productImageAsset = asset;
  if (asset && state.selectedProductImageId !== asset.id) {
    state.selectedProductImageId = asset.id;
  }
  if ($("productImageStatus")) {
    $("productImageStatus").textContent = asset
      ? `${assets.length} 张 · 当前 ${asset.width || "-"}×${asset.height || "-"}`
      : "未绑定";
  }
  if ($("productImagePreviewWrap")) {
    $("productImagePreviewWrap").innerHTML = renderProductImageGallery(assets, asset);
    $("productImagePreviewWrap").classList.toggle("empty", !assets.length);
  }
  if ($("storyboardVideoCost")) {
    const seconds = estimateStoryboardVideoDuration();
    $("storyboardVideoCost").textContent = seconds
      ? `${seconds} 秒 · 约 $${(seconds * 0.08).toFixed(2)}`
      : "等待分镜";
  }
  renderStoryboardVideoJobs(state.storyboardVideoJobs || []);
  hydrateProtectedImages();
}

function renderProductImageGallery(assets, selectedAsset) {
  if (!assets.length) return "<span>暂无产品图</span>";
  return `
    <div class="product-image-gallery">
      ${assets
        .map((asset) => {
          const active = selectedAsset?.id === asset.id;
          return `
            <button class="product-image-thumb ${active ? "active" : ""}" type="button" data-product-image-id="${escapeAttr(asset.id)}" title="${escapeAttr(asset.filename || "产品图")}">
              ${asset.preview_url ? `<div class="storyboard-image-placeholder" data-protected-image="${escapeAttr(asset.preview_url)}">图片正在加载。</div>` : "<span>无预览</span>"}
              <span>${escapeHtml(asset.filename || "产品图")}</span>
            </button>
          `;
        })
        .join("")}
    </div>
  `;
}

function estimateStoryboardVideoDuration() {
  const total = (state.storyboardShots || []).reduce((sum, shot) => sum + durationToSeconds(shot.duration), 0);
  if (!total) return 0;
  return Math.max(12, Math.ceil(total / 6) * 6);
}

function durationToSeconds(value) {
  const match = String(value || "").match(/\d{1,3}/);
  return match ? Number(match[0]) : 0;
}

async function loadProductImages(scriptJobId) {
  if (!scriptJobId) return;
  const data = await api(
    `/api/product-images?script_job_id=${encodeURIComponent(scriptJobId)}&variant_index=${state.activeVariantIndex}`
  );
  state.productImageAssets = data.assets || [];
  if (!state.productImageAssets.some((asset) => asset.id === state.selectedProductImageId)) {
    state.selectedProductImageId = state.productImageAssets[0]?.id || "";
  }
  state.productImageAsset = currentProductImageAsset();
  renderMediaPanel();
}

async function uploadProductImage(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length || !state.currentResultJob) return;
  setMessage("productImageMessage", `正在上传 ${files.length} 张产品图...`);
  const uploaded = [];
  try {
    for (const [index, file] of files.entries()) {
      const body = new FormData();
      body.append("script_job_id", state.currentResultJob.id);
      body.append("variant_index", String(state.activeVariantIndex));
      body.append("file", file);
      const data = await api("/api/product-images", { method: "POST", body });
      if (data.asset) uploaded.push(data.asset);
      setMessage("productImageMessage", `正在上传产品图 ${index + 1}/${files.length}...`);
    }
    if (uploaded.length) {
      state.selectedProductImageId = uploaded[0].id;
      await loadProductImages(state.currentResultJob.id);
    }
    setMessage("productImageMessage", `已上传 ${uploaded.length} 张产品图，当前方案已绑定。`, "ok");
  } catch (error) {
    setMessage("productImageMessage", error.message, "error");
  } finally {
    event.target.value = "";
  }
}

function storyboardRowFromCells(row, index) {
  const isAgencyStoryboard = row.length >= 13;
  if (isAgencyStoryboard) {
    const shotLabel = row[0] || `镜头 ${index + 1}`;
    const segment = row[1] || shotLabel;
    const visualPrompt = row[2] || "";
    const movement = row[3] || "";
    const sceneNote = row[4] || "";
    const storyNote = row[5] || "";
    const sellingPoint = row[6] || "";
    const feature = row[7] || sellingPoint || "";
    const voiceover = row[8] || "";
    const subtitle = row[9] || "";
    const duration = row[12] || "";
    const method = [visualPrompt, sceneNote, storyNote].filter(Boolean).join("；");
    return {
      shotLabel,
      segment,
      feature,
      method,
      voiceover,
      subtitle,
      angle: "",
      movement,
      duration,
      sellingPoint,
    };
  }
  const isMarketingSchemaStoryboard = row.length === 7;
  if (isMarketingSchemaStoryboard) {
    const shotLabel = row[0] || `镜头 ${index + 1}`;
    const feature = row[1] || "";
    const method = row[2] || "";
    const voiceover = row[3] || "";
    const subtitle = row[4] || "";
    const movement = row[5] || "";
    const duration = row[6] || "";
    return {
      shotLabel,
      segment: shotLabel,
      feature,
      method,
      voiceover,
      subtitle,
      angle: movement,
      movement,
      duration,
      sellingPoint: feature,
    };
  }
  const hasLegacyEffectColumns = row.length >= 12;
  const segment = row[0] || `镜头 ${index + 1}`;
  const feature = row[1] || "";
  const method = row[2] || "";
  const voiceover = row[3] || "";
  const subtitle = row[4] || "";
  const angle = row[hasLegacyEffectColumns ? 6 : 5] || "";
  const movement = row[hasLegacyEffectColumns ? 7 : 6] || "";
  const duration = row[hasLegacyEffectColumns ? 11 : 9] || "";
  return {
    shotLabel: `镜头 ${String(index + 1).padStart(2, "0")}`,
    segment,
    feature,
    method,
    voiceover,
    subtitle,
    angle,
    movement,
    duration,
    sellingPoint: "",
  };
}

function renderStoryboardCards(content) {
  const rows = parseFirstMarkdownTable(content);
  const shotRows = rows.filter((row) => {
    const marker = [row[0], row[1]].filter(Boolean).join(" ");
    return marker && !marker.includes("总时长") && !marker.toLowerCase().includes("total");
  });
  state.storyboardShots = [];
  if (!shotRows.length) {
    return '<div class="empty-state"><strong>暂无产品动图片段</strong><span>脚本表格生成后，这里会自动拆出可生成视频片段的镜头卡片。</span></div>';
  }
  const request = (state.currentResultJob && state.currentResultJob.request) || {};
  const productCategory = request.category || ($("categorySelect") ? $("categorySelect").value : "");
  const productModel = request.model || ($("modelSelect") ? $("modelSelect").value : "");
  return shotRows
    .slice(0, 12)
    .map((row, index) => {
      const { shotLabel, segment, feature, method, voiceover, subtitle, angle, movement, duration, sellingPoint } =
        storyboardRowFromCells(row, index);
      const prompt = buildStoryboardImagePrompt({
        category: productCategory,
        model: productModel,
        segment,
        feature,
        method,
        angle,
        movement,
        subtitle,
      });
      state.storyboardShots.push({ segment, feature, method, angle, movement, subtitle, duration, prompt });
      const isGenerating = state.canvasGenerating.has(index) || hasActiveCanvasJobForShot(index);
      return `
        <article class="storyboard-card">
          <div class="storyboard-meta">
            <span>${escapeHtml(shotLabel)}</span>
            <strong>${escapeHtml(duration || "-")}</strong>
          </div>
          <h4>${escapeHtml(segment)}</h4>
          <p class="storyboard-feature">${escapeHtml(feature)}</p>
          <dl>
            <div><dt>画面表现</dt><dd>${escapeHtml(method || "按脚本场景执行")}</dd></div>
            <div><dt>拍摄方式</dt><dd>${escapeHtml([angle, movement].filter(Boolean).join(" / ") || "稳定镜头")}</dd></div>
            ${sellingPoint ? `<div><dt>功能卖点</dt><dd>${escapeHtml(sellingPoint)}</dd></div>` : ""}
            <div><dt>旁白/字幕</dt><dd>${escapeHtml([voiceover, subtitle].filter(Boolean).join(" / "))}</dd></div>
          </dl>
          <div class="storyboard-actions">
            <button class="storyboard-generate" type="button" data-shot-index="${index}" ${isGenerating ? "disabled" : ""}>
              ${isGenerating ? "正在生成片段参考图..." : currentProductImageId() ? "结合产品图生成片段参考图" : "生成片段参考图"}
            </button>
          </div>
          ${renderCanvasJobForShot(index)}
          <details>
            <summary>片段生成 Prompt</summary>
            <p>${escapeHtml(prompt)}</p>
          </details>
        </article>
      `;
    })
    .join("");
}

function renderProtectedImage(imageUrl) {
  if (isExternalImageUrl(imageUrl)) {
    return `<img class="storyboard-image" src="${escapeAttr(imageUrl)}" alt="Storyboard reference" loading="lazy" referrerpolicy="no-referrer" data-storyboard-preview="true" title="单击放大查看" />`;
  }
  const objectUrl = state.protectedObjectUrls.get(imageUrl);
  if (objectUrl) {
    return `<img class="storyboard-image" src="${escapeAttr(objectUrl)}" alt="Storyboard reference" loading="lazy" data-storyboard-preview="true" title="单击放大查看" />`;
  }
  return `<div class="storyboard-image-placeholder" data-protected-image="${escapeAttr(imageUrl)}">图片正在加载。</div>`;
}

function isExternalImageUrl(imageUrl) {
  try {
    const url = new URL(imageUrl, window.location.origin);
    return url.origin !== window.location.origin;
  } catch (_) {
    return false;
  }
}

async function hydrateProtectedImages() {
  const nodes = Array.from(document.querySelectorAll("[data-protected-image]"));
  await Promise.all(
    nodes.map(async (node) => {
      const imageUrl = node.dataset.protectedImage || "";
      if (!imageUrl) return;
      try {
        let objectUrl = state.protectedObjectUrls.get(imageUrl);
        if (!objectUrl) {
          const { blob } = await fetchProtectedBlob(imageUrl);
          objectUrl = URL.createObjectURL(blob);
          state.protectedObjectUrls.set(imageUrl, objectUrl);
        }
        const image = document.createElement("img");
        image.className = "storyboard-image";
        image.src = objectUrl;
        image.alt = "Storyboard reference";
        image.loading = "lazy";
        image.dataset.storyboardPreview = "true";
        image.title = "单击放大查看";
        node.replaceWith(image);
      } catch (error) {
        node.textContent = error.message || "图片加载失败。";
        node.classList.add("error");
      }
    })
  );
}

function openStoryboardImagePreview(image) {
  const modal = $("imagePreviewModal");
  const preview = $("imagePreviewImage");
  if (!modal || !preview || !image) return;
  const imageUrl = image.currentSrc || image.src;
  if (!imageUrl) return;
  preview.src = imageUrl;
  preview.alt = image.alt || "Storyboard reference";
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("preview-open");
}

function closeStoryboardImagePreview() {
  const modal = $("imagePreviewModal");
  const preview = $("imagePreviewImage");
  if (!modal || !preview) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  preview.removeAttribute("src");
  document.body.classList.remove("preview-open");
}

function storyboardImageProviderLabel(modelId = "") {
  const provider = String(state.canvasProvider || "").toLowerCase();
  const model = String(modelId || state.canvasModelId || "").toLowerCase();
  if (provider.includes("liblib") || model.includes("liblib")) return "LibLibAI";
  if (modelId) return modelId;
  if (state.canvasModelId) return state.canvasModelId;
  return "片段参考图服务";
}

function storyboardImageGeneratingMessage() {
  const provider = storyboardImageProviderLabel();
  if (provider === "LibLibAI") {
    return currentProductImageId()
      ? "LibLibAI 正在参考产品图生成片段参考图，通常需要几十秒。"
      : "LibLibAI 正在生成片段参考图，通常需要几十秒。";
  }
  return `${provider} 正在生成片段参考图，通常需要几十秒。`;
}

function storyboardImageModelLabel(job = {}) {
  return job.model_id || state.canvasModelId || storyboardImageProviderLabel();
}

function isActiveCanvasJob(job = {}) {
  return ["pending", "queued", "running", "inprogress", "submitted"].includes(String(job.status || "").toLowerCase());
}

function canvasJobForShot(shotIndex) {
  return (state.canvasJobs || []).find((item) => {
    return Number(item.shot_index) === Number(shotIndex) && Number(item.variant_index || 0) === Number(state.activeVariantIndex);
  });
}

function hasActiveCanvasJobForShot(shotIndex) {
  return isActiveCanvasJob(canvasJobForShot(shotIndex));
}

function storyboardSubmitPrompt(shot = {}) {
  return [
    "Create a 16:9 product motion segment reference image based on the uploaded product image and this script segment.",
    shot.segment ? `Shot: ${shot.segment}.` : "",
    shot.feature ? `Feature: ${shot.feature}.` : "",
    shot.method ? `Visual action: ${shot.method}.` : "",
    shot.angle ? `Camera angle: ${shot.angle}.` : "",
    shot.movement ? `Camera movement: ${shot.movement}.` : "",
    shot.subtitle ? `Voiceover/subtitle: ${shot.subtitle}.` : "",
    "Keep product identity from the reference image while generating a new scene, not a copied packshot.",
  ]
    .filter(Boolean)
    .join(" ")
    .slice(0, 1800);
}

function canvasJobsRenderSignature(jobs = state.canvasJobs, provider = state.canvasProvider, modelId = state.canvasModelId) {
  const relevantJobs = (jobs || [])
    .filter((item) => Number(item.variant_index || 0) === Number(state.activeVariantIndex))
    .map((item) => ({
      id: item.id || "",
      shot_index: Number(item.shot_index),
      status: item.status || "",
      attempt: Number(item.attempt || 0),
      preview_url: item.preview_url || "",
      image_uri: item.image_uri || "",
      failure_message: formatErrorDetail(item.failure_message, ""),
      updated_at: item.updated_at || "",
    }))
    .sort((a, b) => a.shot_index - b.shot_index || String(a.id).localeCompare(String(b.id)));
  return JSON.stringify({
    variant: Number(state.activeVariantIndex),
    provider: provider || "",
    modelId: modelId || "",
    jobs: relevantJobs,
  });
}

function renderCanvasJobForShot(shotIndex) {
  if (state.canvasGenerating.has(shotIndex)) {
    return `<div class="storyboard-image-slot loading">${escapeHtml(storyboardImageGeneratingMessage())}</div>`;
  }
  const job = canvasJobForShot(shotIndex);
  if (!job) {
    return '<div class="storyboard-image-slot empty">生成后会在这里显示 16:9 片段参考图。</div>';
  }
  if (isActiveCanvasJob(job)) {
    const attempt = Number(job.attempt || 0);
    const suffix = attempt > 1 ? `（第 ${attempt} 次尝试）` : "";
    return `<div class="storyboard-image-slot loading">${escapeHtml(`${storyboardImageGeneratingMessage()}${suffix}`)}</div>`;
  }
  if (job.status === "failed") {
    return `<div class="storyboard-image-slot error">${escapeHtml(formatErrorDetail(job.failure_message, "片段参考图生成失败"))}</div>`;
  }
  const imageUrl = job.preview_url || "";
  const image = imageUrl
    ? renderProtectedImage(imageUrl)
    : '<div class="storyboard-image-placeholder">图片已生成，预览链接暂不可用。</div>';
  return `
    <div class="storyboard-image-slot ready">
      ${image}
      <div class="storyboard-image-meta">
        <span>${escapeHtml(storyboardImageModelLabel(job))}</span>
        <span>${escapeHtml(formatDateTime(job.updated_at || job.created_at))}</span>
      </div>
    </div>
  `;
}

async function loadCanvasJobs(scriptJobId) {
  if (!scriptJobId) return;
  const data = await api(
    `/api/nova-canvas/jobs?script_job_id=${encodeURIComponent(scriptJobId)}&variant_index=${state.activeVariantIndex}`
  );
  const nextJobs = data.jobs || [];
  const nextProvider = data.provider || "";
  const nextModelId = data.model_id || "";
  const nextSignature = canvasJobsRenderSignature(nextJobs, nextProvider, nextModelId);
  const shouldRerender = nextSignature !== state.canvasJobsSignature;
  state.canvasJobs = nextJobs;
  state.canvasProvider = nextProvider;
  state.canvasModelId = nextModelId;
  if (shouldRerender) {
    rerenderStoryboardCards();
  } else {
    hydrateProtectedImages();
  }
  scheduleCanvasJobsPoll();
}

function scheduleCanvasJobsPoll() {
  if (state.canvasPollTimer) {
    clearTimeout(state.canvasPollTimer);
    state.canvasPollTimer = null;
  }
  const job = state.currentResultJob;
  const hasActiveJobs = (state.canvasJobs || []).some((item) => {
    return Number(item.variant_index || 0) === Number(state.activeVariantIndex) && isActiveCanvasJob(item);
  });
  if (!job || !hasActiveJobs) return;
  state.canvasPollTimer = setTimeout(() => {
    loadCanvasJobs(job.id).catch((error) => {
      console.warn("Failed to poll storyboard image jobs", error);
    });
  }, 3500);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForCanvasJob(shotIndex, timeoutMs = 420000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    await sleep(3500);
    if (!state.currentResultJob) return null;
    await loadCanvasJobs(state.currentResultJob.id);
    const job = canvasJobForShot(shotIndex);
    if (job && !isActiveCanvasJob(job)) return job;
  }
  throw new Error("参考图已提交后台生成，请稍后刷新状态。");
}

async function submitCanvasImage(shotIndex, options = {}) {
  const job = state.currentResultJob;
  const shot = state.storyboardShots[shotIndex];
  const waitForCompletion = options.waitForCompletion !== false;
  if (!job || !shot) return;
  state.canvasGenerating.add(shotIndex);
  rerenderStoryboardCards();
  try {
    await api("/api/nova-canvas/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script_job_id: job.id,
        variant_index: state.activeVariantIndex,
        shot_index: shotIndex,
        prompt: storyboardSubmitPrompt(shot),
        product_image_id: currentProductImageId(),
      }),
    });
    await loadCanvasJobs(job.id);
    if (waitForCompletion) {
      await waitForCanvasJob(shotIndex);
    }
  } catch (error) {
    if (String(error.message || "").includes("已提交后台")) {
      await loadCanvasJobs(job.id).catch(() => {});
      return;
    }
    const failureMessage = formatErrorDetail(error, "参考图生成失败");
    state.canvasJobs = [
      {
        id: `failed-${Date.now()}`,
        script_job_id: job.id,
        variant_index: state.activeVariantIndex,
        shot_index: shotIndex,
        status: "failed",
        failure_message: failureMessage,
        created_at: new Date().toISOString(),
      },
      ...(state.canvasJobs || []),
    ];
  } finally {
    state.canvasGenerating.delete(shotIndex);
    rerenderStoryboardCards();
  }
}

async function generateAllStoryboards() {
  const job = state.currentResultJob;
  if (!job || state.bulkCanvasGenerating) return;
  const missing = (state.storyboardShots || [])
    .map((_, index) => index)
    .filter((index) => {
      return !(state.canvasJobs || []).some((item) => {
        return Number(item.shot_index) === index && Number(item.variant_index || 0) === Number(state.activeVariantIndex) && (item.status === "succeeded" || isActiveCanvasJob(item));
      });
    });
  if (!missing.length) {
    if ((state.canvasJobs || []).some((item) => Number(item.variant_index || 0) === Number(state.activeVariantIndex) && isActiveCanvasJob(item))) {
      setMessage("storyboardVideoMessage", "片段参考图正在后台生成，请稍后刷新状态。", "ok");
      return;
    }
    setMessage("storyboardVideoMessage", "当前方案的片段参考图已经生成。", "ok");
    return;
  }
  state.bulkCanvasGenerating = true;
  setMessage("storyboardVideoMessage", `正在生成 ${missing.length} 张片段参考图...`);
  try {
    for (const index of missing) {
      await submitCanvasImage(index, { waitForCompletion: false });
    }
    setMessage("storyboardVideoMessage", `已提交 ${missing.length} 张片段参考图到后台队列，将按顺序生成。`, "ok");
  } catch (error) {
    setMessage("storyboardVideoMessage", error.message, "error");
  } finally {
    state.bulkCanvasGenerating = false;
  }
}

function parseFirstMarkdownTable(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const tableLines = [];
  let started = false;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!started && isMarkdownTableStart(lines, index)) {
      started = true;
    }
    if (started && trimmed.startsWith("|")) {
      tableLines.push(line);
      continue;
    }
    if (started) break;
  }
  if (tableLines.length < 3) return [];
  return tableLines
    .slice(2)
    .map((line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim()));
}

function buildStoryboardImagePrompt({ category, model, segment, feature, method, angle, movement, subtitle }) {
  const categoryHint = storyboardCategoryHint(category, feature, method, subtitle);
  return [
    "Premium 16:9 photorealistic e-commerce storyboard reference image for a Hisense product video.",
    "Follow the storyboard exactly; do not invent another product category, room, or action.",
    "Single-product rule: show exactly one physical Hisense appliance unit; no duplicate product, no second unit, no side-by-side appliances, no showroom lineup, and no background appliance of the same category.",
    "Brand text rule: any readable logo or brand text must be exactly 'Hisense' with complete, sharp Latin letters; no misspelled, partial, garbled, or fake brand text.",
    "Action/result priority: if the shot mentions hands, taking items out, clean clothes, fluffy laundry, food results, racks, steam, or completion results, those objects/actions must be the dominant visual subject; the appliance may be secondary or partial.",
    "Never output an appliance-only front product shot unless the shot specifically asks for exterior, control-panel, display, button, or product-detail close-up.",
    "Reference image rule: use the uploaded product image only for product identity, color, finish, door outline, handle, buttons, display/control-panel layout, logo position, and proportions.",
    "Scene rewrite rule: create a new storyboard scene from the prompt; do not copy the uploaded image's room, closet, cabinet layout, lighting, crop, or camera angle.",
    category ? `Product category from brief: ${category}.` : "",
    categoryHint ? `Product category in English: ${categoryHint.subject}.` : "",
    categoryHint ? `Required setting: ${categoryHint.setting}.` : "",
    categoryHint ? `Must include: ${categoryHint.must}.` : "",
    categoryHint ? `Avoid: ${categoryHint.negative}.` : "",
    model ? `Product model from brief: Hisense ${model}.` : "",
    `Shot: ${segment}.`,
    feature ? `Product benefit: ${feature}.` : "",
    method ? `Visual action: ${method}.` : "",
    angle ? `Camera angle: ${angle}.` : "",
    movement ? `Camera movement: ${movement}.` : "",
    subtitle ? `Keep the product message aligned with: ${subtitle}.` : "",
    "The selected product must be the main subject with realistic product proportions, straight handles, sharp buttons, plausible panel geometry, soft commercial lighting, no competitor brands, no distorted logo, no misspelled Hisense logo, no text overlay unless required by the script.",
  ]
    .filter(Boolean)
    .join(" ");
}

function storyboardCategoryHint(category, feature, method, subtitle) {
  const raw = [category, feature, method, subtitle].filter(Boolean).join(" ").toLowerCase();
  if (/(空气炸锅|air\s*fry|air\s*fryer|fryer|frying|little oil|frozen food|6\.3\s*l?)/i.test(raw)) {
    return {
      subject: "a countertop air fryer with visible basket or drawer and control buttons",
      setting: "modern kitchen countertop or breakfast prep counter, never a living room",
      must: "the air fryer as the main foreground subject, food placed into or removed from the basket, and button/control interaction when mentioned",
      negative: "television, TV screen, living room, entertainment console, sofa, media wall, unrelated appliance",
    };
  }
  if (/(微波|microwave|reheat|defrost|popcorn)/i.test(raw)) {
    return {
      subject: "a countertop microwave oven with visible door, cavity, plate, and control panel",
      setting: "modern kitchen countertop, never a living room",
      must: "the microwave as the main subject, with food container, steam, plate, door, or control panel visible according to the shot",
      negative: "television, TV screen, living room, sofa, unrelated appliance",
    };
  }
  if (/(烤箱|oven|bake|roast|pizza)/i.test(raw)) {
    return {
      subject: "a kitchen oven with visible door, tray, cavity, and control area",
      setting: "modern kitchen or kitchen countertop, never a living room",
      must: "the oven as the main subject, with tray, food, oven door, interior light, or baked result visible according to the shot",
      negative: "television, TV screen, living room, sofa, unrelated appliance",
    };
  }
  if (/(冰箱|refrigerator|fridge|freezer|freshness|fresh food)/i.test(raw)) {
    return {
      subject: "a refrigerator with visible doors, shelves, drawers, and stored food",
      setting: "modern kitchen, never a living room TV wall",
      must: "the refrigerator as the main subject, with shelves, drawers, food containers, produce, or storage result cues visible",
      negative: "television, TV screen, entertainment console, sofa, unrelated appliance",
    };
  }
  if (/(\u6d17\u8863|\u70d8\u5e72|\u6d17\u70d8|laundry|washer|dryer|washing\s*machine|15\s*washing\s*programs|clothes|towel|\u6eda\u7b52|\u8863\u7269|\u6bdb\u5dfe|\u886c\u886b)/i.test(raw)) {
    return {
      subject: "one front-loading washing machine or washer-dryer combo with a visible door and control panel",
      setting: "modern laundry room or utility room, never a kitchen showroom",
      must: "exactly one laundry appliance unit as the only product, with clothes, towels, laundry basket, detergent, or control-panel interaction according to the shot",
      negative: "multiple washing machines, second washer, second dryer, washer and dryer pair, side-by-side appliances, appliance lineup, showroom display, duplicate product, kitchen, TV screen, unrelated appliance",
    };
  }
  if (/(洗碗|dishwasher|dishes|tableware|餐具)/i.test(raw)) {
    return {
      subject: "a dishwasher with visible racks, dishes, door, and control panel",
      setting: "modern kitchen beside cabinets or a sink, never a living room",
      must: "the dishwasher as the main subject, with open racks, dishes, cutlery, clean results, or control panel visible",
      negative: "television, TV screen, living room, sofa, laundry appliances as main subject, unrelated appliance",
    };
  }
  return null;
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
  if (!$("videoVariantSelect")) return; // Video section hidden
  const variants = job.variants || [];
  if (!variants.length) return;
  $("videoVariantSelect").innerHTML = variants
    .map((variant, index) => optionHtml(String(index), variant.name || `方案${index + 1}`, index === state.activeVariantIndex))
    .join("");
  updateVideoPrompt();
  await loadVideoJobs(job.id);
}

function updateVideoPrompt() {
  if (!$("videoVariantSelect") || !$("videoPrompt")) return; // Video section hidden
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

async function loadStoryboardVideoJobs(scriptJobId) {
  if (!scriptJobId) return;
  try {
    const data = await api(
      `/api/storyboard-video/jobs?script_job_id=${encodeURIComponent(scriptJobId)}&variant_index=${state.activeVariantIndex}`
    );
    state.storyboardVideoJobs = data.jobs || [];
    if (typeof data.estimated_usd_per_second === "number" && $("storyboardVideoCost")) {
      const seconds = estimateStoryboardVideoDuration();
      $("storyboardVideoCost").textContent = seconds
        ? `${seconds} 秒 · 约 $${(seconds * data.estimated_usd_per_second).toFixed(2)}`
        : "等待片段";
    }
    renderStoryboardVideoJobs(state.storyboardVideoJobs);
  } catch (error) {
    setMessage("storyboardVideoMessage", error.message, "error");
  }
}

function renderStoryboardVideoJobs(jobs) {
  const target = $("storyboardVideoJobs");
  if (!target) return;
  target.innerHTML =
    (jobs || []).map((job) => {
      const preview = job.preview_url
        ? `<video class="video-preview" controls src="${escapeAttr(job.preview_url)}"></video><a class="download-link" href="${escapeAttr(job.preview_url)}" target="_blank" rel="noreferrer">打开视频</a>`
        : "";
      const failure = job.failure_message ? `<div class="message error">${escapeHtml(job.failure_message)}</div>` : "";
      const summary = `${escapeHtml(job.model_id || "Nova Reel")} · ${escapeHtml(job.duration_seconds || "-")} 秒 · ${escapeHtml(job.shot_count || "-")} 段 · 智能转场`;
      return `
        <article class="video-job">
          <div class="job-head">
            <span>${escapeHtml(job.variant_name || "智能转场成片")}</span>
            <span>${escapeHtml(job.status || "")}</span>
          </div>
          <div class="message">${summary}</div>
          ${failure}
          ${preview}
        </article>
      `;
    }).join("") || '<div class="empty-state"><strong>暂无成片任务</strong><span>上传产品图并确认片段后，可提交智能转场合成整段视频。</span></div>';
}

async function submitStoryboardVideoGeneration() {
  const job = state.currentResultJob;
  if (!job) return;
  setMessage("storyboardVideoMessage", "正在提交片段串联与智能转场成片任务...");
  try {
    await api("/api/storyboard-video/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script_job_id: job.id,
        variant_index: state.activeVariantIndex,
        product_image_id: currentProductImageId(),
      }),
    });
    setMessage("storyboardVideoMessage", "智能转场成片任务已提交，稍后点击刷新查看状态。", "ok");
    await loadStoryboardVideoJobs(job.id);
  } catch (error) {
    setMessage("storyboardVideoMessage", error.message, "error");
  }
}

async function refreshStoryboardVideoGeneration() {
  const job = state.currentResultJob;
  if (!job) return;
  setMessage("storyboardVideoMessage", "正在刷新智能转场成片状态...");
  try {
    const data = await api(
      `/api/storyboard-video/refresh?script_job_id=${encodeURIComponent(job.id)}&variant_index=${state.activeVariantIndex}`,
      { method: "POST" }
    );
    state.storyboardVideoJobs = data.jobs || [];
    renderStoryboardVideoJobs(state.storyboardVideoJobs);
    setMessage("storyboardVideoMessage", "视频状态已刷新。", "ok");
  } catch (error) {
    setMessage("storyboardVideoMessage", error.message, "error");
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

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

async function startApp() {
  if (state.appReady) return;
  state.appReady = true;
  renderVideoTypePicker();
  try {
    await Promise.all([loadOptions(), loadJobs()]);
    if (!state.jobsTimer) {
      state.jobsTimer = setInterval(loadJobs, 5000);
    }
  } catch (error) {
    state.appReady = false;
    setMessage("formMessage", error.message, "error");
  }
}

$("authForm").addEventListener("submit", submitAuth);
$("categorySelect").addEventListener("change", updateModels);
$("modelSearch").addEventListener("input", filterModels);
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
  state.selectedVideoTypes = [item.dataset.value];
  renderVideoTypePicker();
});
$("generateForm").addEventListener("submit", submitGeneration);
if ($("uploadInput")) $("uploadInput").addEventListener("change", uploadFile);
$("refreshJobs").addEventListener("click", loadJobs);
$("jobs").addEventListener("click", async (event) => {
  const button = event.target.closest(".load-result");
  if (!button) return;
  const job = await api(`/api/jobs/${encodeURIComponent(button.dataset.jobId)}`);
  state.activeJobId = job.id;
  renderResult(job);
});
$("downloadResult").addEventListener("click", async (event) => {
  event.preventDefault();
  try {
    await downloadJob($("downloadResult").dataset.jobId);
  } catch (error) {
    setMessage("formMessage", error.message, "error");
  }
});
$("toggleMediaPanel").addEventListener("click", () => {
  $("mediaPanel").classList.remove("hidden");
  renderMediaPanel();
  $("mediaPanel").scrollIntoView({ behavior: "smooth", block: "center" });
});
$("productImageInput").addEventListener("change", uploadProductImage);
$("productImagePreviewWrap").addEventListener("click", (event) => {
  const image = event.target.closest("[data-storyboard-preview]");
  if (image) {
    const button = image.closest(".product-image-thumb");
    if (button) {
      state.selectedProductImageId = button.dataset.productImageId || "";
      state.productImageAsset = currentProductImageAsset();
      renderMediaPanel();
      rerenderStoryboardCards();
    }
    openStoryboardImagePreview(image);
    return;
  }
  const button = event.target.closest(".product-image-thumb");
  if (!button) return;
  state.selectedProductImageId = button.dataset.productImageId || "";
  state.productImageAsset = currentProductImageAsset();
  renderMediaPanel();
  rerenderStoryboardCards();
});
$("generateAllStoryboards").addEventListener("click", generateAllStoryboards);
$("submitStoryboardVideo").addEventListener("click", submitStoryboardVideoGeneration);
$("refreshStoryboardVideo").addEventListener("click", refreshStoryboardVideoGeneration);
$("resultTabs").addEventListener("click", async (event) => {
  const tab = event.target.closest(".result-tab");
  if (!tab || !state.renderedJobId) return;
  const job = await api(`/api/jobs/${encodeURIComponent(state.renderedJobId)}`);
  renderResult(job, Number(tab.dataset.index || 0));
});
$("storyboardCards").addEventListener("click", (event) => {
  const image = event.target.closest("[data-storyboard-preview]");
  if (image) {
    openStoryboardImagePreview(image);
    return;
  }
  const button = event.target.closest(".storyboard-generate");
  if (!button) return;
  submitCanvasImage(Number(button.dataset.shotIndex || 0));
});
if ($("imagePreviewModal")) {
  $("imagePreviewModal").addEventListener("click", (event) => {
    if (event.target.matches("[data-preview-close]")) {
      closeStoryboardImagePreview();
    }
  });
}
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeStoryboardImagePreview();
  }
});
if ($("discoverRainforest")) $("discoverRainforest").addEventListener("click", discoverRainforest);
if ($("refreshRainforest")) $("refreshRainforest").addEventListener("click", refreshRainforest);
if ($("loadCompetitorAssets")) $("loadCompetitorAssets").addEventListener("click", loadCompetitorAssets);
if ($("importSocialUrls")) $("importSocialUrls").addEventListener("click", importSocialUrls);
if ($("discoverYouTube")) $("discoverYouTube").addEventListener("click", discoverYouTube);
if ($("refreshYouTube")) $("refreshYouTube").addEventListener("click", refreshYouTube);
if ($("refreshSocialThumbnails")) $("refreshSocialThumbnails").addEventListener("click", refreshSocialThumbnails);
if ($("competitorResearchForm")) $("competitorResearchForm").addEventListener("submit", submitCompetitorResearch);
if ($("competitorDiscovery")) {
  $("competitorDiscovery").addEventListener("click", (event) => {
    const chip = event.target.closest(".asin-chip");
    if (!chip) return;
    if (chip.dataset.videoId) {
      if (!state.lastDiscoveredVideoIds.includes(chip.dataset.videoId)) {
        state.lastDiscoveredVideoIds.push(chip.dataset.videoId);
      }
      return;
    }
    const current = splitList($("competitorAsins")?.value || "");
    if (!current.includes(chip.dataset.asin)) {
      $("competitorAsins").value = [...current, chip.dataset.asin].join(", ");
    }
  });
}
// Nova Reel video section hidden - guard against missing elements
if ($("videoVariantSelect")) $("videoVariantSelect").addEventListener("change", updateVideoPrompt);
if ($("submitVideo")) $("submitVideo").addEventListener("click", submitVideoGeneration);
if ($("refreshVideo")) $("refreshVideo").addEventListener("click", refreshVideoGeneration);

initializeAuth();
