(function () {
  const root = document.querySelector(".onboarding");
  const tutorialRoot = document.querySelector(".tutorial");
  const activeRoot = root || tutorialRoot;
  if (!activeRoot) {
    return;
  }

  const steps = Array.from(activeRoot.querySelectorAll(".onboarding-step"));
  const dots = Array.from(activeRoot.querySelectorAll("[data-step-target]"));
  const max = steps.length - 1;
  const maxAllowed = Number(activeRoot.dataset.maxStep || String(max));
  const skipped = new Set(
    String(activeRoot.dataset.skipSteps || "")
      .split(",")
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isFinite(value))
  );

  function normalize(index, direction) {
    let next = Math.max(0, Math.min(max, maxAllowed, index));
    const stepDirection = direction || 1;
    while (skipped.has(next) && next > 0 && next < maxAllowed) {
      next += stepDirection >= 0 ? 1 : -1;
    }
    if (skipped.has(next)) {
      next = stepDirection >= 0 ? Math.min(maxAllowed, next + 1) : Math.max(0, next - 1);
    }
    return Math.max(0, Math.min(max, maxAllowed, next));
  }

  function readStep() {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = Number(params.get("step"));
    const fromData = Number(activeRoot.dataset.startStep || "0");
    const value = Number.isFinite(fromQuery) ? fromQuery : fromData;
    return normalize(value, 1);
  }

  function show(index, push, direction) {
    const next = normalize(index, direction);
    activeRoot.dataset.currentStep = String(next);
    steps.forEach((step) => {
      const active = Number(step.dataset.step) === next;
      step.classList.toggle("active", active);
      step.setAttribute("aria-hidden", active ? "false" : "true");
    });
    dots.forEach((dot) => {
      const active = Number(dot.dataset.stepTarget) === next;
      dot.classList.toggle("active", active);
      dot.setAttribute("aria-current", active ? "step" : "false");
    });
    if (push) {
      const url = new URL(window.location.href);
      url.searchParams.set("step", String(next));
      window.history.replaceState({}, "", url);
    }
  }

  activeRoot.addEventListener("click", (event) => {
    const target = event.target.closest("[data-step-target], [data-next], [data-prev]");
    if (!target) {
      return;
    }
    if (target.disabled || target.classList.contains("locked")) {
      return;
    }
    const current = Number(activeRoot.dataset.currentStep || "0");
    if (target.hasAttribute("data-step-target")) {
      show(Number(target.dataset.stepTarget), true, Number(target.dataset.stepTarget) >= current ? 1 : -1);
      return;
    }
    if (target.hasAttribute("data-next")) {
      show(current + 1, true, 1);
      return;
    }
    if (target.hasAttribute("data-prev")) {
      show(current - 1, true, -1);
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.target.matches("input, select, textarea")) {
      return;
    }
    const current = Number(activeRoot.dataset.currentStep || "0");
    if (event.key === "ArrowRight") {
      show(current + 1, true, 1);
    }
    if (event.key === "ArrowLeft") {
      show(current - 1, true, -1);
    }
  });

  show(readStep(), false);
})();
