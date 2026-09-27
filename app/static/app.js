// Persistent Goal-Agent Platform — Frontend Controller
// Handles API calls, live polling, task switching, and state rendering.

const API_BASE = window.location.origin;

let currentTaskId = null;
let pollTimer = null;
let cachedTasks = [];
let pendingApprovalActionId = null;

// DOM Elements
const serverStatusEl = document.getElementById("server-status");
const taskListEl = document.getElementById("task-list");
const btnNewGoal = document.getElementById("btn-new-goal");
const btnRefreshTasks = document.getElementById("btn-refresh-tasks");

const viewCreate = document.getElementById("view-create");
const viewTask = document.getElementById("view-task");
const goalForm = document.getElementById("goal-form");

// Task View Elements
const taskStatusPill = document.getElementById("task-status-pill");
const taskIdDisplay = document.getElementById("task-id-display");
const taskTimeDisplay = document.getElementById("task-time-display");
const taskObjectiveTitle = document.getElementById("task-objective-title");
const taskWorkspaceDisplay = document.getElementById("task-workspace-display");

const btnPauseTask = document.getElementById("btn-pause-task");
const btnResumeTask = document.getElementById("btn-resume-task");
const btnCancelTask = document.getElementById("btn-cancel-task");
const btnManualRefresh = document.getElementById("btn-manual-refresh");

const approvalBanner = document.getElementById("approval-banner");
const approvalDescription = document.getElementById("approval-description");
const btnApproveAction = document.getElementById("btn-approve-action");
const btnDenyAction = document.getElementById("btn-deny-action");

const blockedBanner = document.getElementById("blocked-banner");
const blockedReasonText = document.getElementById("blocked-reason-text");

// Metrics
const metricIterations = document.getElementById("metric-iterations");
const metricMaxIterations = document.getElementById("metric-max-iterations");
const progressIterations = document.getElementById("progress-iterations");

const metricMinutes = document.getElementById("metric-minutes");
const metricMaxMinutes = document.getElementById("metric-max-minutes");
const progressMinutes = document.getElementById("progress-minutes");

const metricCost = document.getElementById("metric-cost");
const metricMaxCost = document.getElementById("metric-max-cost");
const progressCost = document.getElementById("progress-cost");

// Details Tabs
const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanes = document.querySelectorAll(".tab-pane");
const criteriaList = document.getElementById("criteria-list");
const criteriaCount = document.getElementById("criteria-count");
const eventsTimeline = document.getElementById("events-timeline");
const eventsCount = document.getElementById("events-count");
const autoRefreshToggle = document.getElementById("auto-refresh-toggle");
const btnClearLogs = document.getElementById("btn-clear-logs");

const artifactsList = document.getElementById("artifacts-list");
const btnLoadReport = document.getElementById("btn-load-report");
const previewFilename = document.getElementById("preview-filename");
const artifactContent = document.getElementById("artifact-content");

// Preset Elements
const presetTitle = document.getElementById("preset-title");
const presetCostBadge = document.getElementById("preset-cost-badge");
const presetDescription = document.getElementById("preset-description");
const presetSetupHint = document.getElementById("preset-setup-hint");
const modelPresetInput = document.getElementById("model-preset");
const freeCloudOption = modelPresetInput.querySelector('option[value="free_cloud"]');
const maxCostInput = document.getElementById("max-cost");
let automaticPreset = null;
let previousPaidBudget = null;

// ─── Initialisation ─────────────────────────────────────────────────────────

async function init() {
  bindEvents();
  await Promise.all([
    checkHealthAndLoadTasks(),
    loadPresets()
  ]);
  setupPolling();
}

function bindEvents() {
  btnNewGoal.addEventListener("click", showCreateView);
  modelPresetInput.addEventListener("change", updatePresetDisplay);
  btnRefreshTasks.addEventListener("click", loadTasks);
  goalForm.addEventListener("submit", handleCreateGoal);

  btnPauseTask.addEventListener("click", () => pauseTask(currentTaskId));
  btnResumeTask.addEventListener("click", () => resumeTask(currentTaskId));
  btnCancelTask.addEventListener("click", () => cancelTask(currentTaskId));
  btnManualRefresh.addEventListener("click", () => refreshCurrentTask());

  btnApproveAction.addEventListener("click", () => handleApproval(true));
  btnDenyAction.addEventListener("click", () => handleApproval(false));

  btnClearLogs.addEventListener("click", () => {
    eventsTimeline.innerHTML = '<div class="empty-state">Logs cleared.</div>';
  });

  btnLoadReport.addEventListener("click", loadOutcomeReport);

  // Tab switching
  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      tabButtons.forEach(b => b.classList.remove("active"));
      tabPanes.forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      const targetTab = document.getElementById(btn.dataset.tab);
      if (targetTab) targetTab.classList.add("active");
    });
  });
}

async function loadPresets() {
  try {
    const res = await fetch(`${API_BASE}/presets`);
    if (res.ok) {
      automaticPreset = await res.json();
      freeCloudOption.title = automaticPreset.free_cloud_available
        ? `Configured: ${automaticPreset.free_cloud_providers.join(", ")}`
        : "No Free Cloud keys configured. Starting will ask whether to switch to Free Local.";
      updatePresetDisplay();
    }
  } catch (err) {
    console.warn("Could not load presets:", err);
  }
}

function updatePresetDisplay() {
  if (!presetTitle) return;
  const useFreeLocal = modelPresetInput.value === "free_local";
  const useFreeCloud = modelPresetInput.value === "free_cloud";
  const setup = useFreeLocal ? {
    name: "Free local (Ollama)",
    cost_estimate: "$0.00 (Free)",
    description: "Runs locally with Ollama and does not use paid model providers.",
    setup_hint: "Requires Ollama running with qwen2.5-coder, llama3.1, and qwen2.5 available."
  } : useFreeCloud ? {
    name: "Free Cloud",
    cost_estimate: "$0.00 (Free Tiers)",
    description: "Uses allowlisted free-tier models from Groq and Gemini API, or OpenRouter's free-model router. Paid model routes are blocked.",
    setup_hint: `Configured: ${(automaticPreset?.free_cloud_providers || []).join(", ") || "none"}. If all providers are unavailable, you will be asked before switching to Free Local.`
  } : automaticPreset;
  if (!setup) return;
  presetTitle.textContent = setup.name;
  presetCostBadge.textContent = setup.cost_estimate;
  presetDescription.textContent = setup.description;
  presetSetupHint.textContent = setup.setup_hint;

  if (useFreeLocal || useFreeCloud) {
    if (!maxCostInput.disabled) previousPaidBudget = maxCostInput.value;
    maxCostInput.value = "0";
    maxCostInput.disabled = true;
  } else {
    const wasFree = maxCostInput.disabled;
    maxCostInput.disabled = false;
    if (wasFree && previousPaidBudget !== null) {
      maxCostInput.value = previousPaidBudget;
    } else if (setup.needs_setup) {
      maxCostInput.value = "0.00";
    }
  }
}

// ─── API Communications ─────────────────────────────────────────────────────

async function checkHealthAndLoadTasks() {
  try {
    const res = await fetch(`${API_BASE}/tasks`);
    if (res.ok) {
      setServerStatus(true);
      const tasks = await res.json();
      renderTaskList(tasks);
      if (tasks.length > 0 && !currentTaskId) {
        selectTask(tasks[0].id);
      }
    } else {
      setServerStatus(false);
    }
  } catch (err) {
    console.error("Connection error:", err);
    setServerStatus(false);
  }
}

function setServerStatus(online) {
  serverStatusEl.className = online ? "server-status online" : "server-status offline";
  serverStatusEl.querySelector(".status-label").textContent = online ? "API Connected" : "API Offline";
}

async function loadTasks() {
  try {
    const res = await fetch(`${API_BASE}/tasks`);
    if (!res.ok) return;
    const tasks = await res.json();
    cachedTasks = tasks;
    renderTaskList(tasks);
  } catch (err) {
    console.error("Failed to load tasks:", err);
  }
}

function renderTaskList(tasks) {
  if (!tasks || tasks.length === 0) {
    taskListEl.innerHTML = '<div class="empty-state">No tasks created yet.</div>';
    return;
  }

  taskListEl.innerHTML = "";
  tasks.forEach(task => {
    const item = document.createElement("div");
    item.className = `task-item ${task.id === currentTaskId ? "active" : ""}`;
    item.dataset.id = task.id;

    const statusClass = (task.status || "pending").toLowerCase();
    const createdDate = task.created_at ? new Date(task.created_at).toLocaleTimeString() : "";

    item.innerHTML = `
      <div class="task-item-header">
        <span class="task-item-id">${task.id.slice(0, 14)}...</span>
        <span class="badge badge-status ${statusClass}">${task.status}</span>
      </div>
      <div class="task-item-goal" title="${escapeHtml(task.objective)}">${escapeHtml(task.objective)}</div>
      <div class="task-item-meta">
        <span>Iter: ${task.iteration || 0}</span>
        <span>${createdDate}</span>
      </div>
    `;

    item.addEventListener("click", () => selectTask(task.id));
    taskListEl.appendChild(item);
  });
}

function showCreateView() {
  currentTaskId = null;
  document.querySelectorAll(".task-item").forEach(i => i.classList.remove("active"));
  viewTask.classList.add("hidden");
  viewCreate.classList.remove("hidden");
}

async function selectTask(taskId) {
  currentTaskId = taskId;
  document.querySelectorAll(".task-item").forEach(i => {
    i.classList.toggle("active", i.dataset.id === taskId);
  });

  viewCreate.classList.add("hidden");
  viewTask.classList.remove("hidden");

  await refreshCurrentTask();
}

async function refreshCurrentTask() {
  if (!currentTaskId) return;

  try {
    const [detailRes, eventsRes, artifactsRes] = await Promise.all([
      fetch(`${API_BASE}/tasks/${currentTaskId}`),
      fetch(`${API_BASE}/tasks/${currentTaskId}/events`),
      fetch(`${API_BASE}/tasks/${currentTaskId}/artifacts`)
    ]);

    if (detailRes.ok) {
      const detail = await detailRes.json();
      renderTaskDetail(detail);
    }

    if (eventsRes.ok) {
      const events = await eventsRes.json();
      renderEvents(events);
    }

    if (artifactsRes.ok) {
      const artifacts = await artifactsRes.json();
      renderArtifacts(artifacts);
    }
  } catch (err) {
    console.error("Error refreshing task:", err);
  }
}

function renderTaskDetail(task) {
  taskStatusPill.textContent = task.status;
  taskStatusPill.className = `badge badge-status ${(task.status || "").toLowerCase()}`;
  taskIdDisplay.textContent = task.id;
  taskObjectiveTitle.textContent = task.objective;
  taskWorkspaceDisplay.textContent = `Workspace: ${task.workspace}`;
  taskTimeDisplay.textContent = task.created_at ? new Date(task.created_at).toLocaleString() : "";

  // Action buttons visibility
  const isRunning = ["created", "contract_review", "context_gathering", "planning", "executing", "observing", "verifying", "reviewing"].includes(task.status);
  const isPaused = task.status === "paused";
  btnPauseTask.classList.toggle("hidden", !isRunning);
  btnResumeTask.classList.toggle("hidden", !isPaused);
  btnCancelTask.disabled = ["success", "cancelled", "failed", "blocked", "budget_exhausted"].includes(task.status);

  // Blocked Banner
  if (["blocked", "budget_exhausted", "failed"].includes(task.status)) {
    blockedBanner.classList.remove("hidden");
    blockedReasonText.textContent = task.blocked_reason || "Task stopped due to safety limits or budget constraint.";
  } else {
    blockedBanner.classList.add("hidden");
  }

  // Check for pending approval
  if (task.status === "needs_approval") {
    approvalBanner.classList.remove("hidden");
  } else {
    approvalBanner.classList.add("hidden");
  }

  // Metrics
  const budget = task.budget || { max_iterations: 40, max_minutes: 120, max_cost_usd: 25 };
  const curIter = task.iteration || 0;
  const maxIter = budget.max_iterations || 40;
  const curCost = (task.total_cost_usd || 0).toFixed(2);
  const maxCost = (budget.max_cost_usd || 25).toFixed(2);

  metricIterations.textContent = curIter;
  metricMaxIterations.textContent = maxIter;
  progressIterations.style.width = `${Math.min(100, (curIter / maxIter) * 100)}%`;

  metricCost.textContent = curCost;
  metricMaxCost.textContent = maxCost;
  progressCost.style.width = `${Math.min(100, (parseFloat(curCost) / parseFloat(maxCost)) * 100)}%`;

  // Criteria
  const criteria = task.criteria_status || [];
  criteriaCount.textContent = criteria.length;
  renderCriteria(criteria);
}

function renderCriteria(criteria) {
  if (!criteria || criteria.length === 0) {
    criteriaList.innerHTML = '<div class="empty-state">No criteria defined yet.</div>';
    return;
  }

  criteriaList.innerHTML = "";
  criteria.forEach(c => {
    const item = document.createElement("div");
    item.className = "criteria-item";
    const state = (c.state || "pending").toLowerCase();
    const statusClass = state === "passed" ? "pass" : (state === "failed" ? "fail" : "pending");
    const statusText = state.toUpperCase();

    item.innerHTML = `
      <span class="criteria-badge ${statusClass}">${statusText}</span>
      <div class="criteria-body">
        <div class="criteria-text">${escapeHtml(c.description)}</div>
        ${c.command ? `<code class="criteria-command">${escapeHtml(c.command)}</code>` : ""}
        ${c.evidence ? `<div class="criteria-obs">Obs: ${escapeHtml(c.evidence.slice(0, 300))}</div>` : ""}
      </div>
    `;
    criteriaList.appendChild(item);
  });
}

function renderEvents(events) {
  eventsCount.textContent = events.length;
  if (!events || events.length === 0) {
    eventsTimeline.innerHTML = '<div class="empty-state">No events recorded.</div>';
    return;
  }

  if (taskStatusPill.textContent === "needs_approval") {
    const approvalEvent = [...events].reverse().find(e =>
      e.kind === "approval_requested" && e.payload && e.payload.approval_id
    );
    pendingApprovalActionId = approvalEvent ? approvalEvent.payload.approval_id : null;
    const switchingToLocal = approvalEvent?.payload?.tool === "switch_to_free_local";
    approvalDescription.textContent = switchingToLocal
      ? `${approvalEvent.payload.reason || "Free Cloud providers are unavailable."} Choose whether to continue with Ollama.`
      : approvalEvent
        ? `Approval required for ${approvalEvent.payload.tool || "action"}.`
        : "Pending approval details are unavailable.";
    btnApproveAction.textContent = switchingToLocal ? "Switch to Free Local" : "Approve & Resume";
    btnDenyAction.textContent = switchingToLocal ? "Stop Task" : "Deny Action";
  } else {
    pendingApprovalActionId = null;
    btnApproveAction.textContent = "Approve & Resume";
    btnDenyAction.textContent = "Deny Action";
  }

  eventsTimeline.innerHTML = "";
  events.forEach(e => {
    const row = document.createElement("div");
    const typeClass = (e.kind || "").toLowerCase().replace(/[^a-z0-9_]/g, "");
    row.className = `event-row ${typeClass}`;

    const time = e.created_at ? new Date(e.created_at).toLocaleTimeString() : "";
    let payloadStr = "";
    try {
      payloadStr = typeof e.payload === "string" ? e.payload : JSON.stringify(e.payload, null, 2);
    } catch {
      payloadStr = String(e.payload);
    }

    row.innerHTML = `
      <span class="event-time">${time}</span>
      <span class="event-type">${escapeHtml(e.kind)}</span>
      <span class="event-payload">${escapeHtml(payloadStr)}</span>
    `;
    eventsTimeline.appendChild(row);
  });
}

function renderArtifacts(artifacts) {
  if (!artifacts || artifacts.length === 0) {
    artifactsList.innerHTML = '<li class="empty-state">No artifacts available yet.</li>';
    return;
  }

  artifactsList.innerHTML = "";
  artifacts.forEach(a => {
    const li = document.createElement("li");
    li.textContent = `${a.kind.toUpperCase()}: ${a.name}`;
    li.addEventListener("click", () => {
      document.querySelectorAll(".artifacts-tree li").forEach(el => el.classList.remove("active"));
      li.classList.add("active");
      previewFilename.textContent = `${a.name} (${a.kind})`;
      artifactContent.textContent = "Artifact recorded in database.";
    });
    artifactsList.appendChild(li);
  });
}

async function loadOutcomeReport() {
  if (!currentTaskId) return;
  try {
    const res = await fetch(`${API_BASE}/tasks/${currentTaskId}/report`);
    if (res.ok) {
      const report = await res.json();
      previewFilename.textContent = `Report for ${currentTaskId}`;
      artifactContent.textContent = JSON.stringify(report, null, 2);
    }
  } catch (err) {
    console.error("Failed to load report:", err);
  }
}

// ─── Actions ────────────────────────────────────────────────────────────────

async function handleCreateGoal(e) {
  e.preventDefault();
  const objective = document.getElementById("goal-objective").value.trim();
  const workspace = document.getElementById("goal-workspace").value.trim();
  const maxIterations = parseInt(document.getElementById("max-iterations").value, 10);
  const maxMinutes = parseInt(document.getElementById("max-minutes").value, 10);
  const maxCost = parseFloat(document.getElementById("max-cost").value);

  if (!objective || !workspace) {
    alert("Please fill in both objective and workspace.");
    return;
  }

  const requestBody = {
    goal: objective,
    workspace,
    model_preset: modelPresetInput.value,
    budget: {
      max_iterations: maxIterations,
      max_minutes: maxMinutes,
      max_cost_usd: maxCost
    }
  };

  try {
    let res = await fetch(`${API_BASE}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody)
    });

    let err = res.ok ? null : await res.json();
    if (err?.detail?.code === "free_cloud_unavailable") {
      const switchToLocal = confirm(
        `${err.detail.message}\n\nSwitch to Free Local (Ollama)?`
      );
      if (!switchToLocal) return;

      modelPresetInput.value = "free_local";
      updatePresetDisplay();
      requestBody.model_preset = "free_local";
      requestBody.budget.max_cost_usd = 0;
      res = await fetch(`${API_BASE}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody)
      });
      err = res.ok ? null : await res.json();
    }

    if (!res.ok) {
      alert(`Failed to create task: ${err?.detail?.message || err?.detail || "Server error"}`);
      return;
    }

    const task = await res.json();
    goalForm.reset();
    updatePresetDisplay();
    await loadTasks();
    selectTask(task.id);
  } catch (err) {
    alert(`Request error: ${err.message}`);
  }
}

async function pauseTask(taskId) {
  if (!taskId) return;
  await fetch(`${API_BASE}/tasks/${taskId}/pause`, { method: "POST" });
  refreshCurrentTask();
  loadTasks();
}

async function resumeTask(taskId) {
  if (!taskId) return;
  await fetch(`${API_BASE}/tasks/${taskId}/resume`, { method: "POST" });
  refreshCurrentTask();
  loadTasks();
}

async function cancelTask(taskId) {
  if (!taskId) return;
  if (!confirm("Are you sure you want to cancel this task?")) return;
  await fetch(`${API_BASE}/tasks/${taskId}/cancel`, { method: "POST" });
  refreshCurrentTask();
  loadTasks();
}

async function handleApproval(approved) {
  if (!currentTaskId) return;
  if (!pendingApprovalActionId) {
    alert("No pending approval request is available.");
    return;
  }
  const response = await fetch(`${API_BASE}/tasks/${currentTaskId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action_id: pendingApprovalActionId,
      approved: approved,
      reason: approved ? "Approved via Web UI" : "Denied via Web UI"
    })
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    alert(error.detail || "Could not resolve the approval request.");
    return;
  }
  pendingApprovalActionId = null;
  approvalBanner.classList.add("hidden");
  refreshCurrentTask();
  loadTasks();
}

function setupPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (autoRefreshToggle && autoRefreshToggle.checked && currentTaskId) {
      refreshCurrentTask();
      loadTasks();
    }
  }, 3000);
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Start
init();
