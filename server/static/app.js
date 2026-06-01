const state = {
  posts: [],
  selectedPost: null,
  selectedDetail: null,
  query: "",
  account: "",
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
  elements.refreshButton.addEventListener("click", () => loadPosts());
  elements.searchInput.addEventListener("input", debounce(handleSearch, 260));
  elements.accountFilter.addEventListener("change", () => {
    state.account = elements.accountFilter.value;
    loadPosts();
  });
  elements.deepButton.addEventListener("click", runDeepResearch);
  elements.askButton.addEventListener("click", askFollowUp);
  loadPosts();
});

async function loadPosts() {
  setSync("syncing");
  const params = new URLSearchParams({ limit: "120" });
  if (state.account) params.set("username", state.account);
  if (state.query) params.set("q", state.query);

  try {
    const payload = await api(`/api/posts?${params.toString()}`);
    state.posts = payload.posts || [];
    renderAccountFilter();
    renderPostList();
    renderStats();
    setSync("ready");
    if (!state.selectedPost && state.posts.length > 0) {
      selectPost(state.posts[0].id);
    }
  } catch (error) {
    setSync("error");
    elements.postList.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function handleSearch() {
  state.query = elements.searchInput.value.trim();
  loadPosts();
}

function renderAccountFilter() {
  const current = elements.accountFilter.value;
  const accounts = Array.from(new Set(state.posts.map((post) => post.username))).sort();
  elements.accountFilter.innerHTML = `<option value="">All accounts</option>`;
  for (const account of accounts) {
    const option = document.createElement("option");
    option.value = account;
    option.textContent = `@${account}`;
    elements.accountFilter.append(option);
  }
  elements.accountFilter.value = accounts.includes(current) ? current : state.account;
}

function renderStats() {
  elements.postCount.textContent = String(state.posts.length);
  const active = state.posts.filter((post) => post.research?.status === "active").length;
  elements.activeResearchCount.textContent = String(active);
}

function renderPostList() {
  if (state.posts.length === 0) {
    elements.postList.innerHTML = `<div class="empty-state">No updates in this view.</div>`;
    return;
  }
  elements.postList.innerHTML = "";
  for (const post of state.posts) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `post-item ${state.selectedPost?.id === post.id ? "active" : ""}`;
    item.addEventListener("click", () => selectPost(post.id));
    item.innerHTML = `
      <div class="post-topline">
        <span>@${escapeHtml(post.username)} · ${relativeTime(post.posted_at)}</span>
        <span>#${post.id}</span>
      </div>
      <p class="post-preview">${escapeHtml(post.preview || "（无正文）")}</p>
      <div class="post-metrics">
        ${metricChip(`${post.link_count} links`)}
        ${metricChip(`${post.media_count} media`)}
        ${metricChip(`${post.reference_count} refs`)}
        ${post.research ? metricChip("research", "hot") : ""}
      </div>
    `;
    elements.postList.append(item);
  }
}

async function selectPost(postId) {
  const summary = state.posts.find((post) => post.id === postId);
  state.selectedPost = summary || null;
  renderPostList();
  setAgentStatus("loading");
  try {
    const detail = await api(`/api/posts/${postId}`);
    state.selectedDetail = detail;
    state.selectedPost = detail.post;
    renderDetail(detail);
    setAgentStatus("ready");
  } catch (error) {
    setAgentStatus("error");
    elements.postDetail.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function renderDetail(detail) {
  const post = detail.post;
  const latestResearch = detail.research_sessions?.[0];
  elements.detailEyebrow.textContent = `@${post.username} · ${relativeTime(post.posted_at)} · #${post.id}`;
  elements.detailTitle.textContent = compact(post.preview || post.content || "Untitled", 120);
  elements.detailMeta.textContent = [
    `${post.link_count} links`,
    `${post.media_count} media`,
    `${post.reference_count} refs`,
  ].join(" / ");

  if (post.tweet_url) {
    elements.originalLink.href = post.tweet_url;
    elements.originalLink.classList.remove("hidden");
  } else {
    elements.originalLink.classList.add("hidden");
  }

  elements.postDetail.classList.remove("empty");
  elements.postDetail.innerHTML = `
    <div class="detail-chips">
      ${post.labels.map((label) => metricChip(label, "hot")).join("")}
      ${latestResearch ? metricChip(`research ${latestResearch.status}`, "hot") : ""}
    </div>
    <div class="post-body">${escapeHtml(post.content || "（无正文）")}</div>
    ${post.translated_content ? resourceCard("Translation", escapeHtml(post.translated_content)) : ""}
    ${post.summary ? resourceCard("Summary", escapeHtml(post.summary)) : ""}
    ${renderLinks(post.links)}
    ${renderMedia(post.media)}
    ${renderReferences(post.referenced_tweets)}
  `;

  if (latestResearch?.answer) {
    elements.answerOutput.textContent = latestResearch.answer;
    elements.answerSession.textContent = `#${latestResearch.id}`;
  } else {
    elements.answerOutput.textContent = "No research yet.";
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
  if (!state.selectedPost || state.busy) return;
  setBusy(true, "researching");
  try {
    const payload = await api(`/api/posts/${state.selectedPost.id}/deep`, {
      method: "POST",
      body: JSON.stringify({ focus: elements.focusInput.value.trim() }),
    });
    elements.answerOutput.textContent = payload.answer;
    elements.answerSession.textContent = `#${payload.session_id}`;
    await selectPost(state.selectedPost.id);
  } catch (error) {
    elements.answerOutput.textContent = error.message;
  } finally {
    setBusy(false, "ready");
  }
}

async function askFollowUp() {
  if (!state.selectedPost || state.busy) return;
  const question = elements.questionInput.value.trim();
  if (!question) {
    elements.answerOutput.textContent = "Question is required.";
    return;
  }
  setBusy(true, "asking");
  try {
    const payload = await api(`/api/posts/${state.selectedPost.id}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    elements.answerOutput.textContent = payload.answer;
    elements.questionInput.value = "";
    await selectPost(state.selectedPost.id);
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
