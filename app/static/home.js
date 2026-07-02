(function () {
  const dialog = document.querySelector("[data-proposal-dialog]");
  if (!dialog) return;

  const titleEl = dialog.querySelector("[data-proposal-dialog-title]");
  const metaEl = dialog.querySelector("[data-proposal-dialog-meta]");
  const renderEl = dialog.querySelector("[data-proposal-dialog-render]");
  const diffEl = dialog.querySelector("[data-proposal-dialog-diff]");
  const messageEl = dialog.querySelector("[data-proposal-dialog-message]");
  const approveButton = dialog.querySelector("[data-proposal-approve]");
  const rejectButton = dialog.querySelector("[data-proposal-reject]");
  const closeButton = dialog.querySelector("[data-proposal-close]");
  let activeProposalId = null;

  document.addEventListener("click", function (event) {
    const row = event.target.closest("[data-proposal-id]");
    if (row) {
      event.preventDefault();
      openProposal(row.dataset.proposalId);
    }
  });

  if (closeButton) closeButton.addEventListener("click", closeDialog);
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) closeDialog();
  });
  if (approveButton) {
    approveButton.addEventListener("click", function () {
      decide("approve");
    });
  }
  if (rejectButton) {
    rejectButton.addEventListener("click", function () {
      decide("reject");
    });
  }

  async function openProposal(proposalId) {
    if (!proposalId) return;
    activeProposalId = proposalId;
    setLoading();
    showDialog();
    try {
      const response = await fetch(`/api/proposals/${encodeURIComponent(proposalId)}`, { headers: { Accept: "application/json" } });
      if (redirectIfUnauthorized(response)) return;
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "제안을 불러오지 못했습니다.");
      renderProposal(data);
    } catch (error) {
      setMessage(error.message || "제안을 불러오지 못했습니다.", true);
    }
  }

  async function decide(action) {
    if (!activeProposalId) return;
    setButtonsDisabled(true);
    setMessage(action === "approve" ? "승인 처리 중입니다." : "거부 처리 중입니다.");
    try {
      const response = await fetch(`/api/proposals/${encodeURIComponent(activeProposalId)}/${action}`, { method: "POST" });
      if (redirectIfUnauthorized(response)) return;
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || data.message || "처리하지 못했습니다.");
      renderProposal(data);
      updateRowStatus(activeProposalId, data.status || (action === "reject" ? "거절" : "승인"));
      setMessage(data.message || "처리했습니다.");
    } catch (error) {
      setMessage(error.message || "처리하지 못했습니다.", true);
    } finally {
      setButtonsDisabled(false);
    }
  }

  function renderProposal(data) {
    titleEl.textContent = data.title || "제안 상세";
    renderMeta(data);
    renderDiff(data);
    renderMarkdown(data.markdown || "", renderEl);
    setMessage("");
    const canDecide = Boolean(data.canDecide);
    if (approveButton) approveButton.hidden = !canDecide;
    if (rejectButton) rejectButton.hidden = !canDecide;
  }

  function renderMeta(data) {
    metaEl.replaceChildren();
    [
      ["유형", data.issueLabel || data.issueType || "-"],
      ["상태", data.status || "-"],
      ["확신도", data.confidence ? `${Math.round(Number(data.confidence) * 100)}%` : "-"],
    ].forEach(function (item) {
      const pill = document.createElement("span");
      pill.textContent = `${item[0]} ${item[1]}`;
      metaEl.appendChild(pill);
    });
  }

  function renderDiff(data) {
    diffEl.replaceChildren();
    const original = data.originalSentence || "";
    const suggested = data.suggestedSentence || "";
    if (!original && !suggested) {
      diffEl.hidden = true;
      return;
    }
    diffEl.hidden = false;
    const heading = document.createElement("h3");
    heading.textContent = data.applyMode === "append" ? "추가 제안" : "수정 diff";
    diffEl.appendChild(heading);
    if (original) diffEl.appendChild(diffLine("-", original));
    if (suggested) diffEl.appendChild(diffLine("+", suggested));
  }

  function diffLine(prefix, text) {
    const row = document.createElement("div");
    row.className = `diff-line ${prefix === "+" ? "add" : "remove"}`;
    const mark = document.createElement("span");
    mark.textContent = prefix;
    const body = document.createElement("p");
    body.textContent = text;
    row.append(mark, body);
    return row;
  }

  function renderMarkdown(markdown, target) {
    target.replaceChildren();
    const lines = String(markdown || "").split(/\r?\n/);
    let list = null;
    lines.forEach(function (line) {
      const trimmed = line.trim();
      if (!trimmed) {
        list = null;
        return;
      }
      if (trimmed.startsWith("# ")) {
        list = null;
        const h = document.createElement("h1");
        h.textContent = trimmed.slice(2);
        target.appendChild(h);
      } else if (trimmed.startsWith("## ")) {
        list = null;
        const h = document.createElement("h2");
        h.textContent = trimmed.slice(3);
        target.appendChild(h);
      } else if (trimmed.startsWith("> ")) {
        list = null;
        const quote = document.createElement("blockquote");
        quote.textContent = trimmed.slice(2);
        target.appendChild(quote);
      } else if (trimmed.startsWith("- ")) {
        if (!list) {
          list = document.createElement("ul");
          target.appendChild(list);
        }
        const item = document.createElement("li");
        item.textContent = trimmed.slice(2);
        list.appendChild(item);
      } else {
        list = null;
        const p = document.createElement("p");
        p.textContent = trimmed;
        target.appendChild(p);
      }
    });
  }

  function setLoading() {
    titleEl.textContent = "제안 불러오는 중";
    metaEl.replaceChildren();
    diffEl.replaceChildren();
    renderEl.replaceChildren();
    setMessage("잠시만 기다려 주세요.");
    setButtonsDisabled(true);
  }

  function setButtonsDisabled(value) {
    if (approveButton) approveButton.disabled = value;
    if (rejectButton) rejectButton.disabled = value;
  }

  function setMessage(message, isError) {
    if (!messageEl) return;
    messageEl.textContent = message || "";
    messageEl.dataset.tone = isError ? "error" : "";
  }

  function updateRowStatus(proposalId, status) {
    document.querySelectorAll(`[data-proposal-id="${CSS.escape(String(proposalId))}"] .run-item-status`).forEach(function (node) {
      node.textContent = status || "-";
    });
  }

  function showDialog() {
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }

  function closeDialog() {
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
  }

  function redirectIfUnauthorized(response) {
    if (response.status !== 401) return false;
    window.location.assign("/");
    return true;
  }
})();
