const state = {
  events: [],
  selectedEvent: null,
  selectedDetail: null,
  query: "",
  source: "",
  busy: false,
};

const elements = {
  searchInput: document.querySelector("#searchInput"),
  accountFilter: document.querySelector("#accountFilter"),
  refreshButton: document.querySelector("#refreshButton"),
  postList: document.querySelector("#postList"),
  postCount: document.querySelector("#postCount"),
  activeResearchCount: document.querySelector("#activeResearchCount"),
  syncState: document.querySelector("#syncState"),
  detailEyebrow: document.querySelector("#detailEyebrow"),
  detailTitle: document.querySelector("#detailTitle"),
  detailMeta: document.querySelector("#detailMeta"),
  originalLink: document.querySelector("#originalLink"),
  postDetail: document.querySelector("#postDetail"),
  focusInput: document.querySelector("#focusInput"),
  questionInput: document.querySelector("#questionInput"),
  deepButton: document.querySelector("#deepButton"),
  askButton: document.querySelector("#askButton"),
  agentStatus: document.querySelector("#agentStatus"),
  answerSession: document.querySelector("#answerSession"),
  answerOutput: document.querySelector("#answerOutput"),
};

window.addEventListener("DOMContentLoaded", () => {
  elements.refreshButton.addEventListener("click", () => loadEvents());
  elements.searchInput.addEventListener("input", debounce(handleSearch, 260));
  elements.accountFilter.addEventListener("change", () => {
    state.source = elements.accountFilter.value;
    loadEvents();
  });
  elements.deepButton.addEventListener("click", runDeepResearch);
  elements.askButton.addEventListener("click", askFollowUp);
  loadEvents();
});

async function loadEvents() {
  setSync("syncing");
  const params = new URLSearchParams({ limit: "120" });
  if (state.source) params.set("source_type", state.source);
  if (state.query) params.set("q", state.query);

  try {
    const payload = await api(`/api/events?${params.toString()}`);
    state.events = payload.events || [];
    renderSourceFilter();
    renderEventList();
    renderStats();
    setSync("ready");
    if (!state.selectedEvent && state.events.length > 0) {
      selectEvent(state.events[0].source_type, state.events[0].source_id);
    }
  } catch (error) {
    setSync("error");
    elements.postList.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function handleSearch() {
  state.query = elements.searchInput.value.trim();
  loadEvents();
}

function renderSourceFilter() {
  const current = elements.accountFilter.value;
  const sources = Array.from(new Set(state.events.map((event) => event.source_type))).sort();
  elements.accountFilter.innerHTML = `<option value="">All sources</option>`;
  for (const source of sources) {
    const option = document.createElement("option");
    option.value = source;
    option.textContent = sourceLabel(source);
    elements.accountFilter.append(option);
  }
  elements.accountFilter.value = sources.includes(current) ? current : state.source;
}

function renderStats() {
  elements.postCount.textContent = String(state.events.length);
  const active = state.events.filter((event) => event.has_research).length;
  elements.activeResearchCount.textContent = String(active);
}

function renderEventList() {
  if (state.events.length === 0) {
    elements.postList.innerHTML = `<div class="empty-state">No events in this view.</div>`;
    return;
  }
  elements.postList.innerHTML = "";
  for (const event of state.events) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `post-item ${state.selectedEvent?.id === event.id ? "active" : ""}`;
    item.addEventListener("click", () => selectEvent(event.source_type, event.source_id));
    item.innerHTML = `
      <div class="post-topline">
        <span>${escapeHtml(sourceLabel(event.source_type))} · ${relativeTime(event.occurred_at || event.created_at)}</span>
        <span>${escapeHtml(event.id)}</span>
      </div>
      <p class="post-preview">${escapeHtml(event.summary || event.title || "（无正文）")}</p>
      <div class="post-metrics">
        ${metricChip(event.priority || "info", priorityClass(event.priority))}
        ${event.sentiment && event.sentiment !== "unknown" ? metricChip(event.sentiment) : ""}
        ${event.tickers?.map((ticker) => metricChip(ticker, "hot")).join("") || ""}
        ${event.has_research ? metricChip("research", "hot") : ""}
        ${event.delivery_status && event.delivery_status !== "none" ? metricChip(event.delivery_status) : ""}
      </div>
    `;
    elements.postList.append(item);
  }
}

async function selectEvent(sourceType, sourceId) {
  const summary = state.events.find(
    (event) => event.source_type === sourceType && String(event.source_id) === String(sourceId),
  );
  state.selectedEvent = summary || null;
  renderEventList();
  setAgentStatus("loading");
  try {
    const detail = await api(`/api/events/${sourceType}/${sourceId}`);
    state.selectedDetail = detail;
    state.selectedEvent = detail.event;
    renderEventDetail(detail);
    setAgentStatus("ready");
  } catch (error) {
    setAgentStatus("error");
    elements.postDetail.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function renderEventDetail(detail) {
  const event = detail.event;
  const record = detail.record || {};
  elements.detailEyebrow.textContent = `${sourceLabel(event.source_type)} · ${relativeTime(event.occurred_at || event.created_at)} · ${event.id}`;
  elements.detailTitle.textContent = compact(event.title || event.summary || "Untitled", 120);
  elements.detailMeta.textContent = [
    event.priority || "info",
    event.delivery_status || "none",
    event.thread_id ? `thread #${event.thread_id}` : "",
  ]
    .filter(Boolean)
    .join(" / ");

  if (event.url) {
    elements.originalLink.href = event.url;
    elements.originalLink.classList.remove("hidden");
  } else {
    elements.originalLink.classList.add("hidden");
  }

  elements.postDetail.classList.remove("empty");
  elements.postDetail.innerHTML =
    event.source_type === "twitter"
      ? renderTwitterRecord(event, record)
      : renderGenericRecord(event, record);

  elements.deepButton.disabled = event.source_type !== "twitter";
  elements.askButton.disabled = event.source_type !== "twitter";
  if (event.has_research || event.thread_id) {
    loadEventResearch(event);
  } else {
    elements.answerOutput.textContent = "No research yet.";
    elements.answerSession.textContent = "-";
  }
}

function renderTwitterRecord(event, post) {
  return `
    <div class="detail-chips">
      ${metricChip(event.priority || "info", priorityClass(event.priority))}
      ${event.tickers?.map((ticker) => metricChip(ticker, "hot")).join("") || ""}
      ${event.has_research ? metricChip("research", "hot") : ""}
    </div>
    <div class="post-body">${escapeHtml(post.content || "（无正文）")}</div>
    ${post.translated_content ? resourceCard("Translation", escapeHtml(post.translated_content)) : ""}
    ${post.summary ? resourceCard("Summary", escapeHtml(post.summary)) : ""}
    ${renderLinks(post.links)}
    ${renderMedia(post.media)}
    ${renderReferences(post.referenced_tweets)}
  `;
}

function renderGenericRecord(event, record) {
  return `
    <div class="detail-chips">
      ${metricChip(sourceLabel(event.source_type), "hot")}
      ${metricChip(event.priority || "info", priorityClass(event.priority))}
      ${event.tickers?.map((ticker) => metricChip(ticker, "hot")).join("") || ""}
      ${event.delivery_status && event.delivery_status !== "none" ? metricChip(event.delivery_status) : ""}
    </div>
    <div class="post-body">${escapeHtml(event.summary || event.body || "（无正文）")}</div>
    ${event.body ? resourceCard("Detail", escapeHtml(event.body)) : ""}
    ${resourceCard("Record", `<pre>${escapeHtml(JSON.stringify(record, null, 2))}</pre>`)}
  `;
}

async function loadEventResearch(event) {
  try {
    if (event.thread_id) {
      const payload = await api(`/api/threads/${event.thread_id}`);
      const research = payload.research;
      elements.answerOutput.textContent = research?.answer || "No research answer yet.";
      elements.answerSession.textContent = research?.id ? `#${research.id}` : "-";
      return;
    }
    elements.answerOutput.textContent = "No research yet.";
    elements.answerSession.textContent = "-";
  } catch (error) {
    elements.answerOutput.textContent = error.message;
    elements.answerSession.textContent = "-";
  }
}

function renderLinks(links = []) {
  if (!links.length) return "";
  return resourceCard(
    "Links",
    `<div class="resource-list">${links
      .map((link) => `<a href="${escapeAttribute(link)}" target="_blank" rel="noreferrer">${escapeHtml(link)}</a>`)
      .join("")}</div>`,
  );
}

function renderMedia(media = []) {
  if (!media.length) return "";
  return resourceCard(
    "Media",
    `<div class="resource-list">${media
      .map((item) => `<a href="${escapeAttribute(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.type || "media")}: ${escapeHtml(item.url || "")}</a>`)
      .join("")}</div>`,
  );
}

function renderReferences(references = []) {
  if (!references.length) return "";
  return resourceCard(
    "Referenced Updates",
    references
      .map((item) => {
        const url = item.url
          ? `<a href="${escapeAttribute(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.url)}</a>`
          : "";
        const text = item.text ? `<p>${escapeHtml(item.text)}</p>` : "";
        return `<div class="reference-block"><strong>${escapeHtml(item.type || "reference")}</strong>${url}${text}</div>`;
      })
      .join(""),
  );
}

async function runDeepResearch() {
  if (!state.selectedEvent || state.selectedEvent.source_type !== "twitter" || state.busy) return;
  setBusy(true, "researching");
  try {
    const payload = await api(`/api/posts/${state.selectedEvent.source_id}/deep`, {
      method: "POST",
      body: JSON.stringify({ focus: elements.focusInput.value.trim() }),
    });
    elements.answerOutput.textContent = payload.answer;
    elements.answerSession.textContent = `#${payload.session_id}`;
    await selectEvent(state.selectedEvent.source_type, state.selectedEvent.source_id);
  } catch (error) {
    elements.answerOutput.textContent = error.message;
  } finally {
    setBusy(false, "ready");
  }
}

async function askFollowUp() {
  if (!state.selectedEvent || state.selectedEvent.source_type !== "twitter" || state.busy) return;
  const question = elements.questionInput.value.trim();
  if (!question) {
    elements.answerOutput.textContent = "Question is required.";
    return;
  }
  setBusy(true, "asking");
  try {
    const payload = await api(`/api/posts/${state.selectedEvent.source_id}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    elements.answerOutput.textContent = payload.answer;
    elements.questionInput.value = "";
    await selectEvent(state.selectedEvent.source_type, state.selectedEvent.source_id);
  } catch (error) {
    elements.answerOutput.textContent = error.message;
  } finally {
    setBusy(false, "ready");
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return payload;
}

function setBusy(value, label) {
  state.busy = value;
  elements.deepButton.disabled = value;
  elements.askButton.disabled = value;
  setAgentStatus(label);
}

function setAgentStatus(value) {
  elements.agentStatus.textContent = value;
}

function setSync(value) {
  elements.syncState.textContent = value;
}

function metricChip(text, modifier = "") {
  return `<span class="chip ${modifier}">${escapeHtml(text)}</span>`;
}

function sourceLabel(sourceType) {
  return {
    twitter: "Twitter/X",
    regulatory: "SEC/FDA",
    market_mover: "Market mover",
    stock_watch: "Stock watch",
    price: "Price alert",
    volume: "Volume alert",
    news: "News alert",
  }[sourceType] || sourceType || "Event";
}

function priorityClass(priority) {
  return priority === "critical" || priority === "warning" ? "hot" : "";
}

function resourceCard(title, body) {
  return `<section class="resource-card"><h3>${escapeHtml(title)}</h3>${body}</section>`;
}

function relativeTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  const diff = Date.now() - date.getTime();
  const minutes = Math.max(0, Math.floor(diff / 60000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function compact(value, limit) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 1).trim()}...`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

function debounce(fn, wait) {
  let timeout;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => fn(...args), wait);
  };
}
