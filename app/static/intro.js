(function () {
  const panels = Array.from(document.querySelectorAll(".intro-panel"));
  if (!panels.length) {
    return;
  }

  panels.forEach((panel) => panel.classList.add("intro-reveal"));

  if (!("IntersectionObserver" in window)) {
    panels.forEach((panel) => panel.classList.add("in-view"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("in-view");
        }
      });
    },
    { rootMargin: "0px 0px -12% 0px", threshold: 0.24 }
  );

  panels.forEach((panel) => observer.observe(panel));

  window.addEventListener(
    "scroll",
    () => {
      const y = window.scrollY || 0;
      document.documentElement.style.setProperty("--intro-scroll", String(Math.min(1, y / 900)));
    },
    { passive: true }
  );
})();
