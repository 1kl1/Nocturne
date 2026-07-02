(function () {
  const root = document.querySelector("[data-runs-live]");
  if (!root) return;

  const pollSeconds = Math.max(3, Number(root.dataset.pollSeconds || 10));
  const limit = Number(root.dataset.limit || 20);
  const refreshButton = root.querySelector("[data-runs-refresh]");
  const countdown = root.querySelector("[data-runs-countdown]");
  const runRows = root.querySelector("[data-run-rows]");
  const logRows = root.querySelector("[data-log-rows]");
  let remaining = pollSeconds;
  let refreshing = false;

  function boot() {
    updateCountdown();
    animateRows(runRows);
    animateRows(logRows);
    if (refreshButton) {
      refreshButton.addEventListener("click", function () {
        refresh({ manual: true });
      });
    }
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

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    setRefreshing(true);
    try {
      const url = new URL("/api/runs", window.location.origin);
      url.searchParams.set("limit", String(limit));
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "실행 로그를 새로고침하지 못했습니다.");
      replaceRows(runRows, data.runRows || "");
      replaceRows(logRows, data.logRows || "");
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

  function replaceRows(tbody, html) {
    if (!tbody || !html || tbody.innerHTML === html) return;
    tbody.innerHTML = html;
    animateRows(tbody);
  }

  function animateRows(tbody) {
    if (!tbody) return;
    Array.from(tbody.querySelectorAll("tr")).forEach(function (row, index) {
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

  function setRefreshing(value) {
    if (!refreshButton) return;
    refreshButton.classList.toggle("is-refreshing", value);
    refreshButton.disabled = value;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
