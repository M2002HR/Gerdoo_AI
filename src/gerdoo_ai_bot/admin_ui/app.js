const PAGE_META = {
  dashboard: {
    title: "Project Dashboard",
    desc: "Live health, user growth, and service performance for Gerdoo bot",
  },
  logs: {
    title: "Event Logs",
    desc: "Filtered event stream with error visibility and debug details",
  },
  users: {
    title: "User Analytics",
    desc: "User activity, growth trend, and per-user consumption insights",
  },
  models: {
    title: "Model Analytics",
    desc: "Model usage distribution, quality, and model-by-user behavior",
  },
  features: {
    title: "Feature Analytics",
    desc: "Feature-level usage, latency, and error behavior over time",
  },
  reports: {
    title: "Reports",
    desc: "Custom timeseries and export for operational analysis",
  },
};

const MENU_ITEMS = [
  ["dashboard", "Dashboard"],
  ["logs", "Logs"],
  ["users", "Users"],
  ["models", "Models"],
  ["features", "Features"],
  ["reports", "Reports"],
];

const state = {
  token: "",
  headerName: "x-admin-token",
  currentPage: "dashboard",
  sinceMinutes: 60,
  dailyDays: 30,
  ws: null,
  wsPaused: false,
  reconnectTimer: null,
  reconnectAttempt: 0,
  charts: new Map(),
  layouts: {},
  live: {
    health: null,
    overview: null,
    logs: null,
  },
  data: {
    logs: null,
    users: null,
    usersDaily: null,
    models: null,
    features: null,
    reportsFeatureTs: null,
    reportsModelTs: null,
    userDetail: null,
  },
  filters: {
    logs: {
      level: "",
      eventType: "",
      userId: "",
      search: "",
      limit: 300,
    },
    users: {
      limit: 120,
      detailUserId: "",
    },
    reports: {
      userId: "",
      featureEventType: "",
      model: "",
      bucket: "hour",
    },
  },
};

const els = {
  app: document.getElementById("app"),
  loginView: document.getElementById("loginView"),
  loginForm: document.getElementById("loginForm"),
  adminUsername: document.getElementById("adminUsername"),
  adminPassword: document.getElementById("adminPassword"),
  loginError: document.getElementById("loginError"),
  menu: document.getElementById("menu"),
  logoutBtn: document.getElementById("logoutBtn"),
  pageTitle: document.getElementById("pageTitle"),
  pageDesc: document.getElementById("pageDesc"),
  sinceSelect: document.getElementById("sinceSelect"),
  sinceLabel: document.getElementById("sinceLabel"),
  refreshBtn: document.getElementById("refreshBtn"),
  pauseWsBtn: document.getElementById("pauseWsBtn"),
  wsStatus: document.getElementById("wsStatus"),
  lastTick: document.getElementById("lastTick"),
  toastRoot: document.getElementById("toastRoot"),
  modalOverlay: document.getElementById("modalOverlay"),
  modalTitle: document.getElementById("modalTitle"),
  modalBody: document.getElementById("modalBody"),
  modalCloseBtn: document.getElementById("modalCloseBtn"),
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtInt(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0";
  return Math.round(n).toLocaleString();
}

function fmtFloat(value, digits = 2) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0";
  return n.toFixed(digits);
}

function fmtPct(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0.00%";
  return `${(n * 100).toFixed(2)}%`;
}

function fmtMs(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0ms";
  return `${n.toFixed(2)}ms`;
}

function formatTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString();
}

function compactJson(value, maxLen = 220) {
  let text = "";
  try {
    text = JSON.stringify(value ?? {});
  } catch {
    text = String(value ?? "");
  }
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen)}...`;
}

function toQuery(params = {}) {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null) return;
    const text = String(v).trim();
    if (!text) return;
    q.set(k, text);
  });
  return q.toString();
}

function topN(items, n = 12) {
  return (items || []).slice(0, n);
}

function toast(message, isError = false) {
  const node = document.createElement("div");
  node.className = `toast${isError ? " err" : ""}`;
  node.textContent = String(message || "");
  els.toastRoot.appendChild(node);
  requestAnimationFrame(() => node.classList.add("show"));
  setTimeout(() => {
    node.classList.remove("show");
    setTimeout(() => node.remove(), 240);
  }, 3200);
}

function openModal(title, contentObj) {
  els.modalTitle.textContent = title;
  els.modalBody.innerHTML = `<pre>${esc(JSON.stringify(contentObj, null, 2))}</pre>`;
  els.modalOverlay.classList.remove("hidden");
  els.modalOverlay.setAttribute("aria-hidden", "false");
}

function closeModal() {
  els.modalOverlay.classList.add("hidden");
  els.modalOverlay.setAttribute("aria-hidden", "true");
}

function setWsStatus(kind, text) {
  const cls = kind === "ok" ? "ok" : kind === "err" ? "err" : "warn";
  els.wsStatus.className = `badge ${cls}`;
  els.wsStatus.textContent = text;
}

function apiHeaders() {
  return {
    [state.headerName]: state.token,
  };
}

function storeAuth() {
  localStorage.setItem("gerdoo_admin_token", state.token);
  localStorage.setItem("gerdoo_admin_header", state.headerName);
}

function loadAuth() {
  state.token = localStorage.getItem("gerdoo_admin_token") || "";
  state.headerName = localStorage.getItem("gerdoo_admin_header") || "x-admin-token";
}

function clearAuth() {
  localStorage.removeItem("gerdoo_admin_token");
  localStorage.removeItem("gerdoo_admin_header");
  state.token = "";
}

async function apiGet(path, params = {}) {
  const query = toQuery(params);
  const url = query ? `${path}?${query}` : path;
  const resp = await fetch(url, {
    method: "GET",
    headers: apiHeaders(),
  });

  const text = await resp.text();
  let parsed;
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    parsed = { raw: text };
  }

  if (!resp.ok) {
    const message = parsed && typeof parsed === "object" ? parsed.detail || parsed.error || JSON.stringify(parsed) : String(text || "Request failed");
    throw new Error(`${resp.status} ${resp.statusText}: ${message}`);
  }
  return parsed;
}

async function apiPost(path, payload = {}, headers = {}) {
  const resp = await fetch(path, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...headers,
    },
    body: JSON.stringify(payload || {}),
  });

  const text = await resp.text();
  let parsed;
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    parsed = { raw: text };
  }

  if (!resp.ok) {
    const message = parsed && typeof parsed === "object" ? parsed.detail || parsed.error || JSON.stringify(parsed) : String(text || "Request failed");
    throw new Error(`${resp.status} ${resp.statusText}: ${message}`);
  }
  return parsed;
}

function ensureChart(id) {
  const el = document.getElementById(id);
  if (!el || typeof echarts === "undefined") return null;

  const existing = state.charts.get(id);
  if (existing && existing.getDom() === el) return existing;

  if (existing) {
    try {
      existing.dispose();
    } catch {
      // no-op
    }
  }

  const next = echarts.init(el);
  state.charts.set(id, next);
  return next;
}

function setChartOption(id, option) {
  const chart = ensureChart(id);
  if (!chart) return;
  chart.setOption(option, { notMerge: true, lazyUpdate: true });
}

function resizeCharts() {
  for (const chart of state.charts.values()) {
    try {
      chart.resize();
    } catch {
      // no-op
    }
  }
}

function debounce(fn, waitMs = 220) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), waitMs);
  };
}

function dayRangeFromWindow() {
  const daysByWindow = Math.ceil(state.sinceMinutes / 1440);
  return Math.max(7, Math.min(120, Math.max(state.dailyDays, daysByWindow)));
}

async function refreshOverviewData() {
  const [health, overview, logs] = await Promise.all([
    apiGet("/health"),
    apiGet("/admin/analytics/overview", { since_minutes: state.sinceMinutes, days: dayRangeFromWindow() }),
    apiGet("/admin/logs/events", {
      since_minutes: state.sinceMinutes,
      limit: Math.min(150, state.filters.logs.limit),
    }),
  ]);
  state.live.health = health;
  state.live.overview = overview;
  state.live.logs = logs;
}

async function refreshLogsData() {
  state.data.logs = await apiGet("/admin/logs/events", {
    since_minutes: state.sinceMinutes,
    level: state.filters.logs.level,
    event_type: state.filters.logs.eventType,
    user_id: state.filters.logs.userId,
    search: state.filters.logs.search,
    limit: state.filters.logs.limit,
  });
}

async function refreshUsersData() {
  const [users, daily] = await Promise.all([
    apiGet("/admin/analytics/users", {
      since_minutes: state.sinceMinutes,
      limit: state.filters.users.limit,
    }),
    apiGet("/admin/analytics/timeseries/users", {
      days: dayRangeFromWindow(),
    }),
  ]);
  state.data.users = users;
  state.data.usersDaily = daily;
}

async function refreshUserDetail(userId) {
  const uid = String(userId || "").trim();
  if (!uid) {
    state.data.userDetail = null;
    return;
  }
  state.data.userDetail = await apiGet(`/admin/analytics/user/${encodeURIComponent(uid)}`, {
    since_minutes: Math.max(state.sinceMinutes, 1440),
  });
}

async function refreshModelsData() {
  state.data.models = await apiGet("/admin/analytics/models", {
    since_minutes: state.sinceMinutes,
  });
}

async function refreshFeaturesData() {
  state.data.features = await apiGet("/admin/analytics/features", {
    since_minutes: state.sinceMinutes,
  });
}

async function refreshReportsData() {
  const userId = state.filters.reports.userId;
  const bucket = state.filters.reports.bucket;
  const [featureTs, modelTs] = await Promise.all([
    apiGet("/admin/analytics/timeseries/features", {
      since_minutes: state.sinceMinutes,
      bucket,
      user_id: userId,
      event_type: state.filters.reports.featureEventType,
    }),
    apiGet("/admin/analytics/timeseries/models", {
      since_minutes: state.sinceMinutes,
      bucket,
      user_id: userId,
      model: state.filters.reports.model,
    }),
  ]);
  state.data.reportsFeatureTs = featureTs;
  state.data.reportsModelTs = modelTs;
}

async function refreshPageData(page) {
  if (page === "dashboard") {
    await refreshOverviewData();
    return;
  }
  if (page === "logs") {
    await refreshLogsData();
    return;
  }
  if (page === "users") {
    await refreshUsersData();
    if (state.filters.users.detailUserId) {
      await refreshUserDetail(state.filters.users.detailUserId);
    }
    return;
  }
  if (page === "models") {
    await refreshModelsData();
    return;
  }
  if (page === "features") {
    await refreshFeaturesData();
    return;
  }
  if (page === "reports") {
    await refreshReportsData();
  }
}

function wsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams({
    token: state.token,
    since_minutes: String(state.sinceMinutes),
    limit: String(state.filters.logs.limit),
  });
  return `${protocol}://${window.location.host}/ws/admin?${query.toString()}`;
}

function clearReconnect() {
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
}

function scheduleReconnect() {
  clearReconnect();
  const wait = Math.min(15000, 1200 * (state.reconnectAttempt + 1));
  state.reconnectTimer = setTimeout(() => connectWs(), wait);
}

function sendWs(obj) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  state.ws.send(JSON.stringify(obj));
}

function applyWsFilters() {
  sendWs({
    type: "filters",
    since_minutes: state.sinceMinutes,
    limit: state.filters.logs.limit,
    level: state.filters.logs.level,
    event_type: state.filters.logs.eventType,
    search: state.filters.logs.search,
    user_id: state.filters.logs.userId,
  });
}

function connectWs() {
  if (state.ws) {
    try {
      state.ws.close();
    } catch {
      // no-op
    }
  }

  clearReconnect();
  setWsStatus("warn", "connecting");
  const socket = new WebSocket(wsUrl());
  state.ws = socket;

  socket.addEventListener("open", () => {
    state.reconnectAttempt = 0;
    setWsStatus("ok", "live");
    applyWsFilters();
  });

  socket.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data || "{}");
    } catch {
      return;
    }

    if (payload?.type === "error") {
      toast(payload.error || "WebSocket error", true);
      return;
    }

    if (payload?.type === "hello" || payload?.type === "tick") {
      if (state.wsPaused) return;
      handleWsSnapshot(payload);
    }
  });

  socket.addEventListener("close", () => {
    setWsStatus("warn", "reconnecting");
    state.reconnectAttempt += 1;
    scheduleReconnect();
  });

  socket.addEventListener("error", () => {
    setWsStatus("err", "error");
  });
}

function handleWsSnapshot(payload) {
  els.lastTick.textContent = formatTime(payload.server_time || "");

  if (payload.project_overview) state.live.overview = payload.project_overview;
  if (payload.events) {
    state.live.logs = payload.events;
    if (state.currentPage === "logs") {
      state.data.logs = payload.events;
    }
  }

  if (state.currentPage === "dashboard") {
    renderDashboard();
  }
  if (state.currentPage === "logs") {
    renderLogs();
  }
}

function buildMenu() {
  els.menu.innerHTML = MENU_ITEMS.map(([id, label]) => `<button data-page="${id}">${esc(label)}</button>`).join("");
  els.menu.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await setPage(btn.getAttribute("data-page") || "dashboard");
    });
  });
}

function setMenuActive(page) {
  els.menu.querySelectorAll("button").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-page") === page);
  });
}

async function setPage(page) {
  if (!PAGE_META[page]) return;
  state.currentPage = page;
  setMenuActive(page);

  Object.keys(PAGE_META).forEach((name) => {
    const el = document.getElementById(`page-${name}`);
    if (!el) return;
    el.classList.toggle("hidden", name !== page);
  });

  const meta = PAGE_META[page];
  els.pageTitle.textContent = meta.title;
  els.pageDesc.textContent = meta.desc;

  try {
    await refreshPageData(page);
    renderCurrentPage();
  } catch (err) {
    toast(String(err.message || err), true);
  }
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = String(value ?? "");
}

function ensureDashboardLayout() {
  if (state.layouts.dashboard) return;
  const root = document.getElementById("page-dashboard");
  root.innerHTML = `
    <div class="grid cols-4">
      <div class="card"><h3>Total Users</h3><div id="kpiUsersTotal" class="stat">0</div><div class="sub">all registered users</div></div>
      <div class="card"><h3>Active Users</h3><div id="kpiUsersActive" class="stat">0</div><div class="sub">active in current window</div></div>
      <div class="card"><h3>AI Requests</h3><div id="kpiReqWindow" class="stat">0</div><div class="sub">requests in selected window</div></div>
      <div class="card"><h3>Success Rate</h3><div id="kpiReqRate" class="stat">0%</div><div class="sub">successful AI requests</div></div>
      <div class="card"><h3>Failed Events</h3><div id="kpiFailedEvents" class="stat">0</div><div class="sub">event failures in window</div></div>
      <div class="card"><h3>New Users Today</h3><div id="kpiUsersNew" class="stat">0</div><div class="sub">added today</div></div>
      <div class="card"><h3>Avg Latency</h3><div id="kpiLatencyAvg" class="stat">0ms</div><div class="sub">events average latency</div></div>
      <div class="card"><h3>P95 Latency</h3><div id="kpiLatencyP95" class="stat">0ms</div><div class="sub">events p95 latency</div></div>
    </div>

    <div class="grid cols-2" style="margin-top:10px">
      <div class="chart-card"><h3>Daily Trend (Users / Requests / Failures)</h3><div id="chartDailyTrend" class="chart"></div></div>
      <div class="chart-card"><h3>24h Throughput</h3><div id="chartThroughput24h" class="chart"></div></div>
    </div>

    <div class="grid cols-2" style="margin-top:10px">
      <div class="chart-card"><h3>Top Feature Events</h3><div id="chartFeatureUsage" class="chart small"></div></div>
      <div class="chart-card"><h3>Chat Model Share</h3><div id="chartModelShare" class="chart small"></div></div>
    </div>

    <div class="card" style="margin-top:10px">
      <h3 style="margin:0 0 8px">Top Users (Window)</h3>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>User</th><th>AI</th><th>Images</th><th>Voice</th><th>Events</th><th>Failures</th><th>Feedback</th><th>Last Seen</th></tr>
          </thead>
          <tbody id="dashUsersBody"></tbody>
        </table>
      </div>
    </div>

    <div class="card" style="margin-top:10px">
      <h3 style="margin:0 0 8px">Recent Events</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Seq</th><th>Level</th><th>Type</th><th>Message</th><th>Details</th></tr></thead>
          <tbody id="dashEventsBody"></tbody>
        </table>
      </div>
    </div>
  `;
  state.layouts.dashboard = true;
}

function renderDashboard() {
  ensureDashboardLayout();

  const packet = state.live.overview || {};
  const overview = packet.overview || {};
  const users = overview.users || {};
  const req = overview.requests || {};
  const events = overview.events || {};
  const latency = overview.latency || {};

  setText("kpiUsersTotal", fmtInt(users.total));
  setText("kpiUsersActive", fmtInt(users.active_window));
  setText("kpiReqWindow", fmtInt(req.window_total));
  setText("kpiReqRate", fmtPct(req.window_success_rate));
  setText("kpiFailedEvents", fmtInt(events.window_failed));
  setText("kpiUsersNew", fmtInt(users.new_today));
  setText("kpiLatencyAvg", fmtMs(latency.window_avg_ms));
  setText("kpiLatencyP95", fmtMs(latency.window_p95_ms));

  const daily = packet.daily || [];
  const dLabels = daily.map((x) => x.day);
  setChartOption("chartDailyTrend", {
    tooltip: { trigger: "axis" },
    legend: { bottom: 0 },
    grid: { left: 50, right: 20, top: 14, bottom: 44 },
    xAxis: { type: "category", data: dLabels },
    yAxis: { type: "value" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16 }],
    series: [
      {
        name: "requests",
        type: "line",
        smooth: true,
        data: daily.map((x) => Number(x.ai_requests || 0)),
        itemStyle: { color: "#5f6b80" },
      },
      {
        name: "active users",
        type: "line",
        smooth: true,
        data: daily.map((x) => Number(x.active_users || 0)),
        itemStyle: { color: "#95a029" },
      },
      {
        name: "failed events",
        type: "line",
        smooth: true,
        data: daily.map((x) => Number(x.events_failed || 0)),
        itemStyle: { color: "#b54646" },
      },
    ],
  });

  const throughput = overview.throughput_24h || [];
  setChartOption("chartThroughput24h", {
    tooltip: { trigger: "axis" },
    grid: { left: 50, right: 20, top: 14, bottom: 32 },
    xAxis: { type: "category", data: throughput.map((x) => x.hour) },
    yAxis: { type: "value" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16 }],
    series: [
      {
        type: "bar",
        data: throughput.map((x) => Number(x.count || 0)),
        itemStyle: { color: "#6e7c92", borderRadius: [6, 6, 0, 0] },
      },
    ],
  });

  const featureUsage = overview.feature_usage || [];
  const featureAgg = {};
  featureUsage.forEach((row) => {
    const key = String(row.event_type || "unknown");
    featureAgg[key] = (featureAgg[key] || 0) + Number(row.count || 0);
  });
  const featureRows = Object.entries(featureAgg)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12);
  setChartOption("chartFeatureUsage", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 18, top: 14, bottom: 32 },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: featureRows.map((x) => x[0]) },
    series: [
      {
        type: "bar",
        data: featureRows.map((x) => Number(x[1])),
        itemStyle: { color: "#6d7b91", borderRadius: [0, 6, 6, 0] },
      },
    ],
  });

  const modelUsage = packet.model_usage?.chat || [];
  const modelTop = topN(modelUsage, 10);
  setChartOption("chartModelShare", {
    tooltip: { trigger: "item" },
    legend: { bottom: 0 },
    series: [
      {
        type: "pie",
        radius: ["38%", "72%"],
        center: ["50%", "45%"],
        data: modelTop.map((x) => ({ value: Number(x.count || 0), name: String(x.model || "unknown") })),
      },
    ],
  });

  const dashUsersBody = document.getElementById("dashUsersBody");
  const topUsers = packet.top_users || [];
  dashUsersBody.innerHTML = topUsers
    .map((u) => `
      <tr>
        <td>${esc(u.display_name || u.user_id)} <span class="muted">@${esc(u.username || "-")}</span></td>
        <td>${fmtInt(u.ai_requests)}</td>
        <td>${fmtInt(u.images_generated)}</td>
        <td>${fmtInt(u.voice_transcriptions)}</td>
        <td>${fmtInt(u.events_total)}</td>
        <td>${fmtInt(u.failed_events)}</td>
        <td>${fmtInt(u.feedback_count)}</td>
        <td>${esc(formatTime(u.last_seen_at))}</td>
      </tr>
    `)
    .join("");
  if (!topUsers.length) {
    dashUsersBody.innerHTML = '<tr><td colspan="8" class="muted">No user activity in this window</td></tr>';
  }

  const eventRows = (state.live.logs?.items || []).slice(0, 20);
  const eventsBody = document.getElementById("dashEventsBody");
  eventsBody.innerHTML = eventRows
    .map((ev) => {
      const level = String(ev.level || "INFO").toUpperCase();
      const cls = level === "ERROR" ? "err" : level === "WARNING" ? "warn" : "ok";
      return `
        <tr>
          <td>${esc(formatTime(ev.ts))}</td>
          <td>${esc(ev.seq)}</td>
          <td><span class="badge ${cls}">${esc(level)}</span></td>
          <td>${esc(ev.event_type || "")}</td>
          <td>${esc(ev.message || "")}</td>
          <td><button class="btn btn-ghost" data-dash-seq="${esc(ev.seq)}">open</button></td>
        </tr>
      `;
    })
    .join("");
  if (!eventRows.length) {
    eventsBody.innerHTML = '<tr><td colspan="6" class="muted">No events available</td></tr>';
  }
  eventsBody.querySelectorAll("button[data-dash-seq]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const seq = Number(btn.getAttribute("data-dash-seq") || 0);
      const item = eventRows.find((x) => Number(x.seq || 0) === seq);
      if (item) openModal(`Event #${seq}`, item);
    });
  });
}

function ensureLogsLayout() {
  if (state.layouts.logs) return;
  const root = document.getElementById("page-logs");
  root.innerHTML = `
    <div class="card">
      <div class="filters">
        <label>Level
          <select id="logsLevel">
            <option value="">All</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
          </select>
        </label>
        <label>Event Type
          <input id="logsEventType" type="text" placeholder="audio.transcription" />
        </label>
        <label>User ID
          <input id="logsUserId" type="text" placeholder="user id" />
        </label>
        <label>Search
          <input id="logsSearch" type="text" placeholder="message / error / details" />
        </label>
        <label>Max Rows
          <select id="logsLimit">
            <option value="100">100</option>
            <option value="300" selected>300</option>
            <option value="700">700</option>
            <option value="1200">1200</option>
          </select>
        </label>
      </div>
      <div class="row" style="margin-top:10px">
        <button id="logsApplyBtn" class="btn">Apply Filters</button>
        <button id="logsClearBtn" class="btn btn-ghost">Clear</button>
      </div>
    </div>

    <div class="grid cols-2" style="margin-top:10px">
      <div class="chart-card"><h3>Top Event Types</h3><div id="chartLogsTypes" class="chart small"></div></div>
      <div class="chart-card"><h3>Errors By Event Type</h3><div id="chartLogsErrors" class="chart small"></div></div>
    </div>

    <div class="card" style="margin-top:10px">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th><th>Seq</th><th>Level</th><th>Type</th><th>Request ID</th><th>Message</th><th>Data</th><th></th>
            </tr>
          </thead>
          <tbody id="logsBody"></tbody>
        </table>
      </div>
    </div>
  `;

  document.getElementById("logsApplyBtn").addEventListener("click", async () => {
    state.filters.logs.level = String(document.getElementById("logsLevel").value || "").trim().toUpperCase();
    state.filters.logs.eventType = String(document.getElementById("logsEventType").value || "").trim();
    state.filters.logs.userId = String(document.getElementById("logsUserId").value || "").trim();
    state.filters.logs.search = String(document.getElementById("logsSearch").value || "").trim();
    state.filters.logs.limit = Number(document.getElementById("logsLimit").value || "300");
    applyWsFilters();
    await refreshLogsData();
    renderLogs();
  });

  document.getElementById("logsClearBtn").addEventListener("click", async () => {
    state.filters.logs = { level: "", eventType: "", userId: "", search: "", limit: 300 };
    document.getElementById("logsLevel").value = "";
    document.getElementById("logsEventType").value = "";
    document.getElementById("logsUserId").value = "";
    document.getElementById("logsSearch").value = "";
    document.getElementById("logsLimit").value = "300";
    applyWsFilters();
    await refreshLogsData();
    renderLogs();
  });

  state.layouts.logs = true;
}

function renderLogs() {
  ensureLogsLayout();

  document.getElementById("logsLevel").value = state.filters.logs.level;
  document.getElementById("logsEventType").value = state.filters.logs.eventType;
  document.getElementById("logsUserId").value = state.filters.logs.userId;
  document.getElementById("logsSearch").value = state.filters.logs.search;
  document.getElementById("logsLimit").value = String(state.filters.logs.limit);

  const rows = state.data.logs?.items || state.live.logs?.items || [];
  const body = document.getElementById("logsBody");
  body.innerHTML = rows
    .map((ev) => {
      const level = String(ev.level || "INFO").toUpperCase();
      const cls = level === "ERROR" ? "err" : level === "WARNING" ? "warn" : "ok";
      return `
        <tr>
          <td>${esc(formatTime(ev.ts))}</td>
          <td>${esc(ev.seq)}</td>
          <td><span class="badge ${cls}">${esc(level)}</span></td>
          <td>${esc(ev.event_type || "")}</td>
          <td><span class="code">${esc(ev.request_id || "-")}</span></td>
          <td>${esc(ev.message || "")}</td>
          <td><span class="code">${esc(compactJson(ev.data || {}))}</span></td>
          <td><button class="btn btn-ghost" data-log-seq="${esc(ev.seq)}">open</button></td>
        </tr>
      `;
    })
    .join("");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="8" class="muted">No logs found for current filters</td></tr>';
  }

  body.querySelectorAll("button[data-log-seq]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const seq = Number(btn.getAttribute("data-log-seq") || 0);
      const item = rows.find((x) => Number(x.seq || 0) === seq);
      if (item) openModal(`Event #${seq}`, item);
    });
  });

  const typeMap = {};
  const errorByType = {};
  rows.forEach((ev) => {
    const type = String(ev.event_type || "unknown");
    typeMap[type] = (typeMap[type] || 0) + 1;
    if (String(ev.level || "").toUpperCase() === "ERROR") {
      errorByType[type] = (errorByType[type] || 0) + 1;
    }
  });

  const topTypes = Object.entries(typeMap).sort((a, b) => b[1] - a[1]).slice(0, 12);
  const topErrors = Object.entries(errorByType).sort((a, b) => b[1] - a[1]).slice(0, 12);

  setChartOption("chartLogsTypes", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 18, top: 14, bottom: 30 },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: topTypes.map((x) => x[0]) },
    series: [{ type: "bar", data: topTypes.map((x) => Number(x[1])), itemStyle: { color: "#6e7c92", borderRadius: [0, 6, 6, 0] } }],
  });

  setChartOption("chartLogsErrors", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 18, top: 14, bottom: 30 },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: topErrors.map((x) => x[0]) },
    series: [{ type: "bar", data: topErrors.map((x) => Number(x[1])), itemStyle: { color: "#b54646", borderRadius: [0, 6, 6, 0] } }],
  });
}

function ensureUsersLayout() {
  if (state.layouts.users) return;
  const root = document.getElementById("page-users");
  root.innerHTML = `
    <div class="card">
      <div class="filters">
        <label>User Rows Limit
          <select id="usersLimit">
            <option value="50">50</option>
            <option value="120" selected>120</option>
            <option value="250">250</option>
            <option value="500">500</option>
          </select>
        </label>
        <label>Inspect User ID
          <input id="usersDetailId" type="text" placeholder="user id" />
        </label>
        <label>Actions
          <button id="usersInspectBtn" class="btn">Load User Detail</button>
        </label>
        <label>Reset Detail
          <button id="usersClearDetailBtn" class="btn btn-ghost">Clear Detail</button>
        </label>
      </div>
      <div class="row" style="margin-top:10px">
        <button id="usersApplyBtn" class="btn">Refresh Users</button>
      </div>
    </div>

    <div class="grid cols-4" style="margin-top:10px">
      <div class="card"><h3>Total Users</h3><div id="usersKpiTotal" class="stat">0</div></div>
      <div class="card"><h3>Active in Window</h3><div id="usersKpiActive" class="stat">0</div></div>
      <div class="card"><h3>New Today</h3><div id="usersKpiNew" class="stat">0</div></div>
      <div class="card"><h3>Top Users Rows</h3><div id="usersKpiRows" class="stat">0</div></div>
    </div>

    <div class="grid cols-2" style="margin-top:10px">
      <div class="chart-card"><h3>User Growth & Activity</h3><div id="chartUsersDaily" class="chart small"></div></div>
      <div class="chart-card"><h3>Top Users Consumption</h3><div id="chartUsersConsumption" class="chart small"></div></div>
    </div>

    <div class="card" style="margin-top:10px">
      <h3 style="margin:0 0 8px">Top Users</h3>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>User</th><th>AI</th><th>Images</th><th>Voice</th><th>Events</th><th>Failed</th><th>Feedback</th><th>Last Seen</th><th></th></tr>
          </thead>
          <tbody id="usersTableBody"></tbody>
        </table>
      </div>
    </div>

    <div class="card" style="margin-top:10px">
      <h3 style="margin:0 0 8px">Selected User Detail</h3>
      <div class="grid cols-2">
        <div class="chart-card"><h3>User Model Usage</h3><div id="chartUserModels" class="chart small"></div></div>
        <div class="chart-card"><h3>User Feature Trend</h3><div id="chartUserFeatures" class="chart small"></div></div>
      </div>
      <div class="table-wrap" style="margin-top:10px">
        <table>
          <thead><tr><th>Time</th><th>Level</th><th>Type</th><th>Message</th><th>Status</th><th>Latency</th></tr></thead>
          <tbody id="userDetailEventsBody"></tbody>
        </table>
      </div>
    </div>
  `;

  document.getElementById("usersApplyBtn").addEventListener("click", async () => {
    state.filters.users.limit = Number(document.getElementById("usersLimit").value || "120");
    await refreshUsersData();
    renderUsers();
  });

  document.getElementById("usersInspectBtn").addEventListener("click", async () => {
    const uid = String(document.getElementById("usersDetailId").value || "").trim();
    state.filters.users.detailUserId = uid;
    if (!uid) {
      toast("Enter a user id first", true);
      return;
    }
    try {
      await refreshUserDetail(uid);
      renderUsers();
    } catch (err) {
      toast(String(err.message || err), true);
    }
  });

  document.getElementById("usersClearDetailBtn").addEventListener("click", () => {
    state.filters.users.detailUserId = "";
    document.getElementById("usersDetailId").value = "";
    state.data.userDetail = null;
    renderUsers();
  });

  state.layouts.users = true;
}

function renderUsers() {
  ensureUsersLayout();

  document.getElementById("usersLimit").value = String(state.filters.users.limit);
  document.getElementById("usersDetailId").value = state.filters.users.detailUserId;

  const packet = state.live.overview || {};
  const overview = packet.overview || {};
  const topUsers = state.data.users?.top_users || packet.top_users || [];
  const daily = state.data.usersDaily?.items || packet.daily || [];

  setText("usersKpiTotal", fmtInt(overview.users?.total));
  setText("usersKpiActive", fmtInt(overview.users?.active_window));
  setText("usersKpiNew", fmtInt(overview.users?.new_today));
  setText("usersKpiRows", fmtInt(topUsers.length));

  setChartOption("chartUsersDaily", {
    tooltip: { trigger: "axis" },
    legend: { bottom: 0 },
    grid: { left: 50, right: 20, top: 14, bottom: 42 },
    xAxis: { type: "category", data: daily.map((x) => x.day) },
    yAxis: { type: "value" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16 }],
    series: [
      { name: "new users", type: "line", smooth: true, data: daily.map((x) => Number(x.new_users || 0)), itemStyle: { color: "#7a879c" } },
      { name: "active users", type: "line", smooth: true, data: daily.map((x) => Number(x.active_users || 0)), itemStyle: { color: "#95a029" } },
      { name: "requests", type: "line", smooth: true, data: daily.map((x) => Number(x.ai_requests || 0)), itemStyle: { color: "#5f6b80" } },
    ],
  });

  const top = topN(topUsers, 12);
  setChartOption("chartUsersConsumption", {
    tooltip: { trigger: "axis" },
    legend: { bottom: 0 },
    grid: { left: 56, right: 16, top: 14, bottom: 46 },
    xAxis: { type: "category", data: top.map((u) => (u.display_name || u.user_id || "").slice(0, 14)) },
    yAxis: { type: "value" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16 }],
    series: [
      { name: "ai", type: "bar", stack: "cons", data: top.map((u) => Number(u.ai_requests || 0)), itemStyle: { color: "#5f6b80" } },
      { name: "image", type: "bar", stack: "cons", data: top.map((u) => Number(u.images_generated || 0)), itemStyle: { color: "#7f8ca1" } },
      { name: "voice", type: "bar", stack: "cons", data: top.map((u) => Number(u.voice_transcriptions || 0)), itemStyle: { color: "#a0adbf" } },
    ],
  });

  const tableBody = document.getElementById("usersTableBody");
  tableBody.innerHTML = topUsers
    .map((u) => `
      <tr>
        <td>${esc(u.display_name || u.user_id)} <span class="muted">@${esc(u.username || "-")}</span><div class="code" style="margin-top:4px">${esc(u.user_id)}</div></td>
        <td>${fmtInt(u.ai_requests)}</td>
        <td>${fmtInt(u.images_generated)}</td>
        <td>${fmtInt(u.voice_transcriptions)}</td>
        <td>${fmtInt(u.events_total)}</td>
        <td>${fmtInt(u.failed_events)}</td>
        <td>${fmtInt(u.feedback_count)}</td>
        <td>${esc(formatTime(u.last_seen_at))}</td>
        <td><button class="btn btn-ghost" data-inspect-user="${esc(u.user_id)}">inspect</button></td>
      </tr>
    `)
    .join("");
  if (!topUsers.length) {
    tableBody.innerHTML = '<tr><td colspan="9" class="muted">No users in selected window</td></tr>';
  }

  tableBody.querySelectorAll("button[data-inspect-user]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const uid = String(btn.getAttribute("data-inspect-user") || "").trim();
      state.filters.users.detailUserId = uid;
      document.getElementById("usersDetailId").value = uid;
      await refreshUserDetail(uid);
      renderUsers();
    });
  });

  const detail = state.data.userDetail;
  const detailUsage = detail?.model_usage || [];
  const detailEvents = detail?.events || [];
  const detailFeatureTs = detail?.feature_timeseries || [];

  setChartOption("chartUserModels", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 16, top: 14, bottom: 28 },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: detailUsage.map((x) => x.model || "") },
    series: [{ type: "bar", data: detailUsage.map((x) => Number(x.count || 0)), itemStyle: { color: "#6e7c92", borderRadius: [0, 6, 6, 0] } }],
  });

  const featureBucketMap = {};
  detailFeatureTs.forEach((row) => {
    const key = String(row.bucket || "");
    featureBucketMap[key] = (featureBucketMap[key] || 0) + Number(row.count || 0);
  });
  const fKeys = Object.keys(featureBucketMap).sort();
  setChartOption("chartUserFeatures", {
    tooltip: { trigger: "axis" },
    grid: { left: 50, right: 16, top: 14, bottom: 30 },
    xAxis: { type: "category", data: fKeys },
    yAxis: { type: "value" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16 }],
    series: [{ type: "line", smooth: true, data: fKeys.map((k) => Number(featureBucketMap[k] || 0)), itemStyle: { color: "#5f6b80" } }],
  });

  const detailBody = document.getElementById("userDetailEventsBody");
  detailBody.innerHTML = detailEvents
    .slice(0, 150)
    .map((ev) => {
      const level = String(ev.status || "").toLowerCase() === "ok" ? "INFO" : "ERROR";
      const cls = level === "ERROR" ? "err" : "ok";
      return `
        <tr>
          <td>${esc(formatTime(ev.created_at))}</td>
          <td><span class="badge ${cls}">${esc(level)}</span></td>
          <td>${esc(ev.event_type || "")}</td>
          <td>${esc((ev.details && ev.details.summary) || "")}</td>
          <td>${esc(ev.status || "")}</td>
          <td>${fmtMs(ev.latency_ms)}</td>
        </tr>
      `;
    })
    .join("");
  if (!detailEvents.length) {
    detailBody.innerHTML = '<tr><td colspan="6" class="muted">No selected user details</td></tr>';
  }
}

function ensureModelsLayout() {
  if (state.layouts.models) return;
  const root = document.getElementById("page-models");
  root.innerHTML = `
    <div class="grid cols-2">
      <div class="chart-card"><h3>Chat Model Usage</h3><div id="chartModelsChat" class="chart small"></div></div>
      <div class="chart-card"><h3>Chat Model Success Rate</h3><div id="chartModelsSuccess" class="chart small"></div></div>
    </div>

    <div class="grid cols-2" style="margin-top:10px">
      <div class="chart-card"><h3>Image Generation Models</h3><div id="chartModelsImage" class="chart small"></div></div>
      <div class="chart-card"><h3>Voice Modes</h3><div id="chartModelsVoice" class="chart small"></div></div>
    </div>

    <div class="chart-card" style="margin-top:10px"><h3>Model Requests Over Time</h3><div id="chartModelsTimeseries" class="chart"></div></div>

    <div class="card" style="margin-top:10px">
      <h3 style="margin:0 0 8px">Per-User Model Usage (Top)</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>User</th><th>Model</th><th>Count</th><th>Success</th><th>Failed</th><th>Rate</th></tr></thead>
          <tbody id="modelsUserUsageBody"></tbody>
        </table>
      </div>
    </div>
  `;

  state.layouts.models = true;
}

function renderModels() {
  ensureModelsLayout();
  const packet = state.data.models || state.live.overview || {};
  const modelUsage = packet.models || packet.model_usage || {};

  const chat = modelUsage.chat || [];
  const image = modelUsage.image_generation || [];
  const voice = modelUsage.voice || [];
  const modelTs = packet.model_timeseries || [];
  const userModelUsage = state.data.users?.user_model_usage || state.live.overview?.user_model_usage || [];

  const chatTop = topN(chat, 12);
  setChartOption("chartModelsChat", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 16, top: 14, bottom: 30 },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: chatTop.map((r) => r.model || "") },
    series: [{ type: "bar", data: chatTop.map((r) => Number(r.count || 0)), itemStyle: { color: "#6e7c92", borderRadius: [0, 6, 6, 0] } }],
  });

  setChartOption("chartModelsSuccess", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 16, top: 14, bottom: 30 },
    xAxis: { type: "value", max: 100 },
    yAxis: { type: "category", inverse: true, data: chatTop.map((r) => r.model || "") },
    series: [
      {
        type: "bar",
        data: chatTop.map((r) => {
          const count = Number(r.count || 0);
          const ok = Number(r.ok_count || 0);
          return count > 0 ? (ok / count) * 100 : 0;
        }),
        itemStyle: { color: "#2f8a62", borderRadius: [0, 6, 6, 0] },
      },
    ],
  });

  const imageTop = topN(image, 12);
  setChartOption("chartModelsImage", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 16, top: 14, bottom: 30 },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: imageTop.map((r) => `${r.provider}/${r.model}`) },
    series: [{ type: "bar", data: imageTop.map((r) => Number(r.count || 0)), itemStyle: { color: "#7f8ca1", borderRadius: [0, 6, 6, 0] } }],
  });

  const voiceTop = topN(voice, 12);
  setChartOption("chartModelsVoice", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 16, top: 14, bottom: 30 },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: voiceTop.map((r) => r.mode || "") },
    series: [{ type: "bar", data: voiceTop.map((r) => Number(r.count || 0)), itemStyle: { color: "#98a3b3", borderRadius: [0, 6, 6, 0] } }],
  });

  const buckets = Array.from(new Set(modelTs.map((x) => x.bucket))).sort();
  const topModels = Array.from(
    modelTs.reduce((acc, row) => {
      const k = String(row.model || "unknown");
      acc.set(k, (acc.get(k) || 0) + Number(row.count || 0));
      return acc;
    }, new Map())
  )
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map((x) => x[0]);

  const series = topModels.map((model, idx) => ({
    name: model,
    type: "line",
    smooth: true,
    data: buckets.map((bucket) => {
      const row = modelTs.find((x) => x.bucket === bucket && x.model === model);
      return Number(row?.count || 0);
    }),
    lineStyle: { width: 2 },
    itemStyle: { color: ["#5f6b80", "#7a879c", "#919db0", "#6f7d93", "#8290a5", "#a2adbe"][idx % 6] },
  }));

  setChartOption("chartModelsTimeseries", {
    tooltip: { trigger: "axis" },
    legend: { bottom: 0 },
    grid: { left: 50, right: 20, top: 14, bottom: 44 },
    xAxis: { type: "category", data: buckets },
    yAxis: { type: "value" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16 }],
    series,
  });

  const userBody = document.getElementById("modelsUserUsageBody");
  userBody.innerHTML = topN(userModelUsage, 250)
    .map((row) => {
      const total = Number(row.count || 0);
      const ok = Number(row.ok_count || 0);
      return `
        <tr>
          <td>${esc(row.display_name || row.user_id)} <span class="muted">@${esc(row.username || "-")}</span><div class="code" style="margin-top:4px">${esc(row.user_id || "")}</div></td>
          <td><span class="code">${esc(row.model || "")}</span></td>
          <td>${fmtInt(total)}</td>
          <td>${fmtInt(ok)}</td>
          <td>${fmtInt(row.failed_count)}</td>
          <td>${total ? fmtPct(ok / total) : "0%"}</td>
        </tr>
      `;
    })
    .join("");
  if (!userModelUsage.length) {
    userBody.innerHTML = '<tr><td colspan="6" class="muted">No user/model rows yet</td></tr>';
  }
}

function ensureFeaturesLayout() {
  if (state.layouts.features) return;
  const root = document.getElementById("page-features");
  root.innerHTML = `
    <div class="grid cols-2">
      <div class="chart-card"><h3>Feature Usage Volume</h3><div id="chartFeaturesUsage" class="chart small"></div></div>
      <div class="chart-card"><h3>Feature Failure Rate</h3><div id="chartFeaturesFailure" class="chart small"></div></div>
    </div>

    <div class="grid cols-2" style="margin-top:10px">
      <div class="chart-card"><h3>Feature Latency (avg)</h3><div id="chartFeaturesLatency" class="chart small"></div></div>
      <div class="chart-card"><h3>Content Type Distribution</h3><div id="chartFeaturesContent" class="chart small"></div></div>
    </div>

    <div class="card" style="margin-top:10px">
      <h3 style="margin:0 0 8px">Top Error Codes</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Error Code</th><th>Count</th></tr></thead>
          <tbody id="featuresErrorsBody"></tbody>
        </table>
      </div>
    </div>

    <div class="card" style="margin-top:10px">
      <h3 style="margin:0 0 8px">Feature Rows</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Event Type</th><th>Status</th><th>Count</th><th>Avg Latency</th></tr></thead>
          <tbody id="featuresRowsBody"></tbody>
        </table>
      </div>
    </div>
  `;
  state.layouts.features = true;
}

function renderFeatures() {
  ensureFeaturesLayout();
  const packet = state.data.features || state.live.overview || {};
  const featureRows = packet.feature_usage || packet.overview?.feature_usage || [];
  const contentRows = packet.content_usage || packet.overview?.content_usage || [];
  const errorRows = packet.errors || packet.overview?.errors || [];

  const eventAgg = {};
  const failAgg = {};
  const latencyAgg = {};
  featureRows.forEach((row) => {
    const eventType = String(row.event_type || "unknown");
    const count = Number(row.count || 0);
    const status = String(row.status || "").toLowerCase();

    eventAgg[eventType] = (eventAgg[eventType] || 0) + count;
    if (status !== "ok") {
      failAgg[eventType] = (failAgg[eventType] || 0) + count;
    }

    const box = latencyAgg[eventType] || { totalLatency: 0, count: 0 };
    box.totalLatency += Number(row.avg_latency_ms || 0) * count;
    box.count += count;
    latencyAgg[eventType] = box;
  });

  const sortedEvents = Object.entries(eventAgg).sort((a, b) => b[1] - a[1]).slice(0, 14);
  setChartOption("chartFeaturesUsage", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 16, top: 14, bottom: 30 },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: sortedEvents.map((x) => x[0]) },
    series: [{ type: "bar", data: sortedEvents.map((x) => Number(x[1])), itemStyle: { color: "#6f7d93", borderRadius: [0, 6, 6, 0] } }],
  });

  setChartOption("chartFeaturesFailure", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 16, top: 14, bottom: 30 },
    xAxis: { type: "value", max: 100 },
    yAxis: { type: "category", inverse: true, data: sortedEvents.map((x) => x[0]) },
    series: [{
      type: "bar",
      data: sortedEvents.map((x) => {
        const all = Number(x[1] || 0);
        const fail = Number(failAgg[x[0]] || 0);
        return all > 0 ? (fail / all) * 100 : 0;
      }),
      itemStyle: { color: "#b54646", borderRadius: [0, 6, 6, 0] },
    }],
  });

  setChartOption("chartFeaturesLatency", {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 16, top: 14, bottom: 30 },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: sortedEvents.map((x) => x[0]) },
    series: [{
      type: "bar",
      data: sortedEvents.map((x) => {
        const row = latencyAgg[x[0]] || { totalLatency: 0, count: 0 };
        return row.count > 0 ? row.totalLatency / row.count : 0;
      }),
      itemStyle: { color: "#7f8ca1", borderRadius: [0, 6, 6, 0] },
    }],
  });

  setChartOption("chartFeaturesContent", {
    tooltip: { trigger: "item" },
    legend: { bottom: 0 },
    series: [
      {
        type: "pie",
        radius: ["38%", "72%"],
        center: ["50%", "45%"],
        data: contentRows.map((x) => ({ name: x.content_type || "unknown", value: Number(x.count || 0) })),
      },
    ],
  });

  const errBody = document.getElementById("featuresErrorsBody");
  errBody.innerHTML = errorRows
    .map((row) => `<tr><td>${esc(row.error_code || "unknown")}</td><td>${fmtInt(row.count)}</td></tr>`)
    .join("");
  if (!errorRows.length) {
    errBody.innerHTML = '<tr><td colspan="2" class="muted">No errors in selected window</td></tr>';
  }

  const rowsBody = document.getElementById("featuresRowsBody");
  rowsBody.innerHTML = featureRows
    .map((row) => `
      <tr>
        <td>${esc(row.event_type || "")}</td>
        <td>${esc(row.status || "")}</td>
        <td>${fmtInt(row.count)}</td>
        <td>${fmtMs(row.avg_latency_ms)}</td>
      </tr>
    `)
    .join("");
  if (!featureRows.length) {
    rowsBody.innerHTML = '<tr><td colspan="4" class="muted">No feature rows available</td></tr>';
  }
}

function ensureReportsLayout() {
  if (state.layouts.reports) return;
  const root = document.getElementById("page-reports");
  root.innerHTML = `
    <div class="card">
      <div class="filters">
        <label>User ID
          <input id="reportsUserId" type="text" placeholder="optional user filter" />
        </label>
        <label>Feature Type
          <input id="reportsFeatureType" type="text" placeholder="optional event_type" />
        </label>
        <label>Model
          <input id="reportsModel" type="text" placeholder="optional model" />
        </label>
        <label>Bucket
          <select id="reportsBucket">
            <option value="hour" selected>hour</option>
            <option value="day">day</option>
          </select>
        </label>
        <label>Actions
          <button id="reportsApplyBtn" class="btn">Build Report</button>
        </label>
      </div>
      <div class="row" style="margin-top:10px">
        <button id="reportsExportBtn" class="btn btn-ghost">Export JSON</button>
      </div>
    </div>

    <div class="grid cols-2" style="margin-top:10px">
      <div class="chart-card"><h3>Feature Requests Over Time</h3><div id="chartReportsFeatures" class="chart"></div></div>
      <div class="chart-card"><h3>Model Requests Over Time</h3><div id="chartReportsModels" class="chart"></div></div>
    </div>

    <div class="card" style="margin-top:10px">
      <h3 style="margin:0 0 8px">Feature Timeseries Rows</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Bucket</th><th>Event Type</th><th>Count</th><th>Success</th><th>Failed</th><th>Avg Latency</th></tr></thead>
          <tbody id="reportsFeatureBody"></tbody>
        </table>
      </div>
    </div>

    <div class="card" style="margin-top:10px">
      <h3 style="margin:0 0 8px">Model Timeseries Rows</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Bucket</th><th>Model</th><th>Count</th><th>Success</th><th>Failed</th></tr></thead>
          <tbody id="reportsModelBody"></tbody>
        </table>
      </div>
    </div>
  `;

  document.getElementById("reportsApplyBtn").addEventListener("click", async () => {
    state.filters.reports.userId = String(document.getElementById("reportsUserId").value || "").trim();
    state.filters.reports.featureEventType = String(document.getElementById("reportsFeatureType").value || "").trim();
    state.filters.reports.model = String(document.getElementById("reportsModel").value || "").trim();
    state.filters.reports.bucket = String(document.getElementById("reportsBucket").value || "hour");

    await refreshReportsData();
    renderReports();
  });

  document.getElementById("reportsExportBtn").addEventListener("click", async () => {
    try {
      const payload = await apiGet("/admin/api/export", { since_minutes: state.sinceMinutes });
      openModal("Export JSON", payload);
    } catch (err) {
      toast(String(err.message || err), true);
    }
  });

  state.layouts.reports = true;
}

function renderReports() {
  ensureReportsLayout();

  document.getElementById("reportsUserId").value = state.filters.reports.userId;
  document.getElementById("reportsFeatureType").value = state.filters.reports.featureEventType;
  document.getElementById("reportsModel").value = state.filters.reports.model;
  document.getElementById("reportsBucket").value = state.filters.reports.bucket;

  const fRows = state.data.reportsFeatureTs?.items || [];
  const mRows = state.data.reportsModelTs?.items || [];

  const fBuckets = Array.from(new Set(fRows.map((x) => x.bucket))).sort();
  const topFeatures = Array.from(
    fRows.reduce((acc, row) => {
      const key = String(row.event_type || "unknown");
      acc.set(key, (acc.get(key) || 0) + Number(row.count || 0));
      return acc;
    }, new Map())
  )
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map((x) => x[0]);

  const fSeries = topFeatures.map((eventType, idx) => ({
    name: eventType,
    type: "line",
    smooth: true,
    data: fBuckets.map((bucket) => {
      const row = fRows.find((x) => x.bucket === bucket && x.event_type === eventType);
      return Number(row?.count || 0);
    }),
    itemStyle: { color: ["#5f6b80", "#7a879c", "#8f9bb0", "#6e7c92", "#a2adbe", "#b54646"][idx % 6] },
  }));

  setChartOption("chartReportsFeatures", {
    tooltip: { trigger: "axis" },
    legend: { bottom: 0 },
    grid: { left: 50, right: 20, top: 14, bottom: 44 },
    xAxis: { type: "category", data: fBuckets },
    yAxis: { type: "value" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16 }],
    series: fSeries,
  });

  const mBuckets = Array.from(new Set(mRows.map((x) => x.bucket))).sort();
  const topModels = Array.from(
    mRows.reduce((acc, row) => {
      const key = String(row.model || "unknown");
      acc.set(key, (acc.get(key) || 0) + Number(row.count || 0));
      return acc;
    }, new Map())
  )
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map((x) => x[0]);

  const mSeries = topModels.map((model, idx) => ({
    name: model,
    type: "line",
    smooth: true,
    data: mBuckets.map((bucket) => {
      const row = mRows.find((x) => x.bucket === bucket && x.model === model);
      return Number(row?.count || 0);
    }),
    itemStyle: { color: ["#6e7c92", "#8794a8", "#a2adbe", "#5f6b80", "#7a879c", "#b54646"][idx % 6] },
  }));

  setChartOption("chartReportsModels", {
    tooltip: { trigger: "axis" },
    legend: { bottom: 0 },
    grid: { left: 50, right: 20, top: 14, bottom: 44 },
    xAxis: { type: "category", data: mBuckets },
    yAxis: { type: "value" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16 }],
    series: mSeries,
  });

  const featureBody = document.getElementById("reportsFeatureBody");
  featureBody.innerHTML = fRows
    .map((row) => `
      <tr>
        <td>${esc(row.bucket || "")}</td>
        <td>${esc(row.event_type || "")}</td>
        <td>${fmtInt(row.count)}</td>
        <td>${fmtInt(row.ok_count)}</td>
        <td>${fmtInt(row.failed_count)}</td>
        <td>${fmtMs(row.avg_latency_ms)}</td>
      </tr>
    `)
    .join("");
  if (!fRows.length) {
    featureBody.innerHTML = '<tr><td colspan="6" class="muted">No feature timeseries rows</td></tr>';
  }

  const modelBody = document.getElementById("reportsModelBody");
  modelBody.innerHTML = mRows
    .map((row) => `
      <tr>
        <td>${esc(row.bucket || "")}</td>
        <td>${esc(row.model || "")}</td>
        <td>${fmtInt(row.count)}</td>
        <td>${fmtInt(row.ok_count)}</td>
        <td>${fmtInt(row.failed_count)}</td>
      </tr>
    `)
    .join("");
  if (!mRows.length) {
    modelBody.innerHTML = '<tr><td colspan="5" class="muted">No model timeseries rows</td></tr>';
  }
}

function renderCurrentPage() {
  if (state.currentPage === "dashboard") renderDashboard();
  if (state.currentPage === "logs") renderLogs();
  if (state.currentPage === "users") renderUsers();
  if (state.currentPage === "models") renderModels();
  if (state.currentPage === "features") renderFeatures();
  if (state.currentPage === "reports") renderReports();
}

function showApp() {
  els.loginView.classList.add("hidden");
  els.app.classList.remove("hidden");
}

function showLogin() {
  els.app.classList.add("hidden");
  els.loginView.classList.remove("hidden");
}

function disconnectWs() {
  clearReconnect();
  if (state.ws) {
    try {
      state.ws.close();
    } catch {
      // no-op
    }
    state.ws = null;
  }
}

async function enterPanel() {
  await refreshOverviewData();
  await refreshUsersData();
  await refreshModelsData();
  await refreshFeaturesData();
  await refreshReportsData();

  showApp();
  await setPage(state.currentPage);
  connectWs();
  toast("Admin panel connected");
}

async function attemptLogin() {
  els.loginError.textContent = "";
  const username = String(els.adminUsername.value || "").trim();
  const password = String(els.adminPassword.value || "").trim();

  if (!username || !password) {
    els.loginError.textContent = "Username and password are required";
    return;
  }

  try {
    const login = await apiPost("/admin/api/login", { username, password });
    state.token = String(login.token || "").trim();
    state.headerName = String(login.header_name || "x-admin-token").trim() || "x-admin-token";
    if (!state.token) {
      throw new Error("Login succeeded but no token was returned");
    }
    storeAuth();
    await enterPanel();
  } catch (err) {
    showLogin();
    els.loginError.textContent = String(err.message || err);
    setWsStatus("err", "auth failed");
  }
}

async function manualRefresh() {
  try {
    await refreshPageData(state.currentPage);
    if (state.currentPage !== "dashboard") {
      await refreshOverviewData();
    }
    renderCurrentPage();
    toast("Refreshed");
  } catch (err) {
    toast(String(err.message || err), true);
  }
}

function bindGlobalEvents() {
  els.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await attemptLogin();
  });

  els.logoutBtn.addEventListener("click", () => {
    disconnectWs();
    clearAuth();
    if (els.adminPassword) els.adminPassword.value = "";
    showLogin();
    setWsStatus("warn", "disconnected");
    toast("Logged out");
  });

  els.refreshBtn.addEventListener("click", async () => {
    await manualRefresh();
  });

  els.pauseWsBtn.addEventListener("click", () => {
    state.wsPaused = !state.wsPaused;
    els.pauseWsBtn.textContent = state.wsPaused ? "Resume Live" : "Pause Live";
    if (!state.wsPaused) {
      renderCurrentPage();
      applyWsFilters();
    }
  });

  els.sinceSelect.addEventListener("change", async () => {
    state.sinceMinutes = Number(els.sinceSelect.value || "60");
    els.sinceLabel.textContent = `${state.sinceMinutes}m`;
    applyWsFilters();
    await manualRefresh();
  });

  els.modalCloseBtn.addEventListener("click", closeModal);
  els.modalOverlay.addEventListener("click", (event) => {
    if (event.target === els.modalOverlay) closeModal();
  });

  window.addEventListener("resize", debounce(resizeCharts, 200));
}

async function bootstrap() {
  buildMenu();
  bindGlobalEvents();
  loadAuth();

  els.sinceSelect.value = String(state.sinceMinutes);
  els.sinceLabel.textContent = `${state.sinceMinutes}m`;
  els.lastTick.textContent = "-";
  setWsStatus("warn", "disconnected");

  if (!state.token) {
    showLogin();
    return;
  }

  try {
    await enterPanel();
  } catch (err) {
    showLogin();
    els.loginError.textContent = String(err.message || err);
  }
}

bootstrap();
