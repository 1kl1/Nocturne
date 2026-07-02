(function () {
  const root = document.querySelector("[data-runs-live]");
  if (!root) return;

  const pollSeconds = Math.max(3, Number(root.dataset.pollSeconds || 10));
  let currentLimit = Number(root.dataset.limit || 20);
  const refreshButton = root.querySelector("[data-runs-refresh]");
  const countdown = root.querySelector("[data-runs-countdown]");
  const limitSelect = root.querySelector("[data-runs-limit]");
  const panelCounts = Array.from(root.querySelectorAll("[data-runs-panel-count]"));
  const manualReturnInput = root.querySelector('form[action="/runs/manual"] input[name="return_to"]');
  const runRows = root.querySelector("[data-run-rows]");
  const logRows = root.querySelector("[data-log-rows]");
  const errorDialog = root.querySelector("[data-run-error-dialog]");
  const errorTitle = root.querySelector("[data-run-error-title]");
  const errorBody = root.querySelector("[data-run-error-body]");
  const errorClose = root.querySelector("[data-run-error-close]");
  let remaining = pollSeconds;
  let refreshing = false;

  function boot() {
    updateCountdown();
    updateLimitLabels();
    if (refreshButton) {
      refreshButton.addEventListener("click", function () {
        refresh({ manual: true });
      });
    }
    if (limitSelect) {
      limitSelect.addEventListener("change", function () {
        const nextLimit = Number(limitSelect.value || currentLimit);
        if (!Number.isFinite(nextLimit) || nextLimit === currentLimit) return;
        currentLimit = nextLimit;
        root.dataset.limit = String(currentLimit);
        updateLimitLabels();
        refresh({ updateUrl: true });
      });
    }
    if (errorClose) {
      errorClose.addEventListener("click", closeErrorDialog);
    }
    if (errorDialog) {
      errorDialog.addEventListener("click", function (event) {
        if (event.target === errorDialog) closeErrorDialog();
      });
    }
    root.addEventListener("click", handleErrorClick);
    window.setInterval(tick, 1000);
  }

  function tick() {
    remaining -= 1;
    if (remaining <= 0) {
      remaining = 0;
      updateCountdown();
      refresh();
      return;
    }
    updateCountdown();
  }

  async function refresh(options) {
    if (refreshing) return;
    refreshing = true;
    setRefreshing(true);
    try {
      const url = new URL("/api/runs", window.location.origin);
      url.searchParams.set("limit", String(currentLimit));
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "실행 로그를 새로고침하지 못했습니다.");
      if (data.limit) {
        currentLimit = Number(data.limit);
        root.dataset.limit = String(currentLimit);
      }
      replaceRows(runRows, data.runRows || "", "data-run-key");
      replaceRows(logRows, data.logRows || "", "data-log-key");
      updateLimitLabels();
      if (options && options.updateUrl) updateUrlLimit();
      root.dataset.liveStatus = "ok";
      if (refreshButton) refreshButton.title = "실행 로그 새로고침";
    } catch (error) {
      root.dataset.liveStatus = "error";
      if (refreshButton) refreshButton.title = error.message || "실행 로그 새로고침 실패";
    } finally {
      refreshing = false;
      setRefreshing(false);
      remaining = pollSeconds;
      updateCountdown();
    }
  }

  function replaceRows(tbody, html, keyAttribute) {
    if (!tbody || !html || tbody.innerHTML === html) return;
    const previousKeys = new Set(
      Array.from(tbody.querySelectorAll(`[${keyAttribute}]`))
        .map(function (row) {
          return row.getAttribute(keyAttribute);
        })
        .filter(Boolean)
    );
    tbody.innerHTML = html;
    animateNewRows(tbody, keyAttribute, previousKeys);
  }

  function animateNewRows(tbody, keyAttribute, previousKeys) {
    if (!tbody) return;
    Array.from(tbody.querySelectorAll(`[${keyAttribute}]`)).forEach(function (row, index) {
      const key = row.getAttribute(keyAttribute);
      if (!key || previousKeys.has(key)) return;
      row.classList.remove("stagger-row");
      row.style.setProperty("--stagger", String(Math.min(index, 14)));
      window.requestAnimationFrame(function () {
        row.classList.add("stagger-row");
      });
    });
  }

  function updateCountdown() {
    if (countdown) countdown.textContent = String(Math.max(0, remaining));
  }

  function updateLimitLabels() {
    if (limitSelect && limitSelect.value !== String(currentLimit)) {
      limitSelect.value = String(currentLimit);
    }
    panelCounts.forEach(function (count) {
      count.textContent = `최근 ${currentLimit}개`;
    });
    if (manualReturnInput) {
      manualReturnInput.value = `/runs?limit=${currentLimit}`;
    }
  }

  function updateUrlLimit() {
    const url = new URL(window.location.href);
    url.searchParams.set("limit", String(currentLimit));
    window.history.replaceState({}, "", url);
  }

  function setRefreshing(value) {
    if (!refreshButton) return;
    refreshButton.classList.toggle("is-refreshing", value);
    refreshButton.disabled = value;
  }

  async function handleErrorClick(event) {
    const runButton = event.target.closest("[data-run-error-run-id]");
    if (runButton && root.contains(runButton)) {
      await openRunErrorDialog(runButton.dataset.runErrorRunId);
      return;
    }
    const logButton = event.target.closest("[data-error-detail]");
    if (logButton && root.contains(logButton)) {
      openErrorDialog(logButton.dataset.errorTitle || "오류 상세", [
        ["이벤트", logButton.dataset.errorEvent || "-"],
        ["요약", logButton.dataset.errorDetail || "-"],
        ["원본", logButton.dataset.errorPayload || "-"],
      ]);
    }
  }

  async function openRunErrorDialog(runId) {
    if (!runId) return;
    openErrorDialog("오류 불러오는 중", [["Run", runId], ["상태", "관련 오류를 확인하고 있습니다."]]);
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/errors`, { headers: { Accept: "application/json" } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "오류 상세를 불러오지 못했습니다.");
      const rows = [["Run", data.runId || runId], ["상태", data.status || "-"]];
      if (data.errorMessage) rows.push(["실행 오류", data.errorMessage]);
      if (Array.isArray(data.items) && data.items.length) {
        data.items.forEach(function (item) {
          rows.push([item.label || item.event || "오류", item.summary || item.payload || "-"]);
        });
      } else {
        rows.push(["상세", "관련 오류 로그가 없습니다."]);
      }
      openErrorDialog("점검 오류 상세", rows);
    } catch (error) {
      openErrorDialog("점검 오류 상세", [["Run", runId], ["오류", error.message || "오류 상세를 불러오지 못했습니다."]]);
    }
  }

  function openErrorDialog(title, rows) {
    if (!errorDialog || !errorTitle || !errorBody) return;
    errorTitle.textContent = title || "오류 상세";
    errorBody.replaceChildren();
    const list = document.createElement("dl");
    rows.forEach(function (row) {
      const item = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = row[0] || "-";
      dd.textContent = row[1] || "-";
      item.append(dt, dd);
      list.appendChild(item);
    });
    errorBody.appendChild(list);
    if (errorDialog.open) {
      return;
    }
    if (typeof errorDialog.showModal === "function") {
      errorDialog.showModal();
    } else {
      errorDialog.setAttribute("open", "");
    }
  }

  function closeErrorDialog() {
    if (!errorDialog) return;
    if (typeof errorDialog.close === "function") {
      errorDialog.close();
    } else {
      errorDialog.removeAttribute("open");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
