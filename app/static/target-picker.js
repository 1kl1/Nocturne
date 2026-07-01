(function () {
  const pickers = Array.from(document.querySelectorAll("[data-target-picker]"));
  if (!pickers.length) {
    return;
  }

  const labels = {
    page: "페이지",
    database: "데이터베이스",
  };

  function field(form, name) {
    return form.querySelector(`[name="${name}"]`);
  }

  function formatEdited(value) {
    if (!value) {
      return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    return date.toLocaleDateString("ko-KR", { month: "short", day: "numeric" });
  }

  function setStatus(picker, message, tone) {
    const status = picker.querySelector("[data-target-status]");
    if (!status) {
      return;
    }
    status.textContent = message || "";
    status.dataset.tone = tone || "";
  }

  function clearSelection(picker) {
    const form = picker.closest("form");
    const selection = picker.querySelector("[data-target-selection]");
    if (!form || !selection) {
      return;
    }
    field(form, "notion_object_id").value = "";
    field(form, "notion_object_type").value = "";
    field(form, "title").value = "";
    field(form, "url").value = "";
    selection.hidden = true;
    selection.replaceChildren();
    picker.querySelectorAll(".target-result-card").forEach((card) => {
      card.classList.remove("selected");
      card.setAttribute("aria-pressed", "false");
    });
  }

  function selectItem(picker, item) {
    const form = picker.closest("form");
    const selection = picker.querySelector("[data-target-selection]");
    if (!form || !selection) {
      return;
    }
    field(form, "notion_object_id").value = item.object_id || "";
    field(form, "notion_object_type").value = item.object_type || "";
    field(form, "title").value = item.title || "";
    field(form, "url").value = item.url || "";

    const label = document.createElement("span");
    label.textContent = "선택됨";
    const title = document.createElement("strong");
    title.textContent = item.title || "Untitled";
    const meta = document.createElement("small");
    meta.textContent = labels[item.object_type] || item.object_type || "";
    selection.replaceChildren(label, title, meta);
    selection.hidden = false;

    picker.querySelectorAll(".target-result-card").forEach((card) => {
      const selected = card.dataset.objectId === item.object_id;
      card.classList.toggle("selected", selected);
      card.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    setStatus(picker, "", "");
  }

  function resultCard(picker, item) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "target-result-card";
    card.dataset.objectId = item.object_id || "";
    card.setAttribute("aria-pressed", "false");

    const title = document.createElement("strong");
    title.textContent = item.title || "Untitled";

    const meta = document.createElement("span");
    const edited = formatEdited(item.last_edited_time);
    meta.textContent = [labels[item.object_type] || item.object_type, edited].filter(Boolean).join(" · ");

    const id = document.createElement("small");
    id.textContent = item.object_id || "";

    card.replaceChildren(title, meta, id);
    card.addEventListener("click", () => selectItem(picker, item));
    return card;
  }

  function renderResults(picker, items) {
    const results = picker.querySelector("[data-target-results]");
    if (!results) {
      return;
    }
    results.replaceChildren();
    if (!items.length) {
      setStatus(picker, "선택 가능한 Notion 대상이 없습니다.", "muted");
      return;
    }
    const fragment = document.createDocumentFragment();
    items.forEach((item) => fragment.appendChild(resultCard(picker, item)));
    results.appendChild(fragment);
    setStatus(picker, `${items.length}개 대상을 불러왔습니다.`, "ok");
  }

  async function loadTargets(picker) {
    const query = picker.querySelector("[data-target-query]");
    const type = picker.querySelector("[data-target-type]");
    const button = picker.querySelector("[data-target-search]");
    const url = new URL("/api/notion/search", window.location.origin);
    url.searchParams.set("limit", "25");
    if (query && query.value.trim()) {
      url.searchParams.set("q", query.value.trim());
    }
    if (type && type.value) {
      url.searchParams.set("object_type", type.value);
    }

    clearSelection(picker);
    setStatus(picker, "Notion에서 불러오는 중입니다.", "loading");
    if (button) {
      button.disabled = true;
    }
    try {
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Notion 목록을 불러오지 못했습니다.");
      }
      renderResults(picker, Array.isArray(data.items) ? data.items : []);
    } catch (error) {
      renderResults(picker, []);
      setStatus(picker, error.message || "Notion 목록을 불러오지 못했습니다.", "error");
    } finally {
      if (button) {
        button.disabled = false;
      }
    }
  }

  pickers.forEach((picker) => {
    const form = picker.closest("form");
    const query = picker.querySelector("[data-target-query]");
    const type = picker.querySelector("[data-target-type]");
    const search = picker.querySelector("[data-target-search]");

    if (search) {
      search.addEventListener("click", () => loadTargets(picker));
    }
    if (query) {
      query.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          loadTargets(picker);
        }
      });
    }
    if (type) {
      type.addEventListener("change", () => {
        clearSelection(picker);
        loadTargets(picker);
      });
    }
    if (form) {
      form.addEventListener("submit", (event) => {
        if (!field(form, "notion_object_id").value) {
          event.preventDefault();
          setStatus(picker, "점검 대상을 선택해 주세요.", "error");
          picker.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    }

    loadTargets(picker);
  });
})();
