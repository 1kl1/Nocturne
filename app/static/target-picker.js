(function () {
  const pickers = Array.from(document.querySelectorAll("[data-target-picker]"));
  if (!pickers.length) {
    return;
  }

  const labels = {
    page: "페이지",
    database: "데이터베이스",
    workspace: "워크스페이스",
  };

  function field(form, name) {
    return (form && form.querySelector(`[name="${name}"]`)) || { value: "" };
  }

  function keyFor(item) {
    return `${item.object_type || "item"}:${item.object_id || ""}`;
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

  function stateFor(picker) {
    if (!picker._targetState) {
      picker._targetState = {
        roots: [],
        children: new Map(),
        expanded: new Set(["workspace"]),
        loaded: new Set(["workspace"]),
        loading: new Set(),
        items: new Map(),
        excluded: new Map(),
      };
    }
    return picker._targetState;
  }

  function selectedId(picker) {
    const form = picker.closest("form");
    return form ? field(form, "notion_object_id").value : "";
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
    picker.querySelectorAll(".target-tree-row").forEach((row) => {
      row.classList.remove("selected");
      row.setAttribute("aria-selected", "false");
    });
  }

  function selectItem(picker, item) {
    const form = picker.closest("form");
    const selection = picker.querySelector("[data-target-selection]");
    if (!form || !selection) {
      return;
    }
    const state = stateFor(picker);
    if (item.object_id && state.excluded.has(item.object_id)) {
      state.excluded.delete(item.object_id);
      syncExcludedField(picker);
    }
    field(form, "notion_object_id").value = item.object_id || "";
    field(form, "notion_object_type").value = item.object_type || "";
    field(form, "title").value = item.title || "";
    field(form, "url").value = item.url || "";

    const label = document.createElement("span");
    label.textContent = "선택됨";
    const title = document.createElement("strong");
    title.textContent = item.title || "Untitled";
    title.title = item.title || "Untitled";
    const meta = document.createElement("small");
    meta.textContent = labels[item.object_type] || item.object_type || "";
    selection.replaceChildren(label, title, meta);
    selection.hidden = false;

    picker.querySelectorAll(".target-tree-row").forEach((row) => {
      const selected = row.dataset.objectId === item.object_id;
      row.classList.toggle("selected", selected);
      row.setAttribute("aria-selected", selected ? "true" : "false");
    });
    renderTree(picker);
    setStatus(picker, "", "");
  }

  function hasChildren(item) {
    return item.object_type === "database" || item.has_children === true || item.has_children === "true";
  }

  function itemMeta(item) {
    const edited = formatEdited(item.last_edited_time);
    const parent = item.parent_type === "workspace" ? "워크스페이스" : item.parent_title || "";
    return [labels[item.object_type] || item.object_type, parent, edited].filter(Boolean).join(" · ");
  }

  function loadingNode(message, level) {
    const node = document.createElement("div");
    node.className = "target-loading";
    node.style.setProperty("--depth", String(level || 0));

    const spinner = document.createElement("span");
    spinner.className = "target-spinner";
    spinner.setAttribute("aria-hidden", "true");

    const text = document.createElement("span");
    text.textContent = message;
    node.replaceChildren(spinner, text);
    return node;
  }

  function workspaceNode(count) {
    const node = document.createElement("div");
    node.className = "target-tree-group";
    node.style.setProperty("--depth", "0");

    const title = document.createElement("strong");
    title.textContent = "워크스페이스";

    const meta = document.createElement("span");
    meta.textContent = `${count}개 항목`;
    node.replaceChildren(title, meta);
    return node;
  }

  function treeRow(picker, item, level) {
    const state = stateFor(picker);
    const row = document.createElement("div");
    const key = keyFor(item);
    row.className = "target-tree-row";
    row.dataset.objectId = item.object_id || "";
    row.style.setProperty("--depth", String(level));
    row.setAttribute("role", "treeitem");
    row.setAttribute("aria-selected", selectedId(picker) === item.object_id ? "true" : "false");
    row.classList.toggle("selected", selectedId(picker) === item.object_id);
    row.classList.toggle("excluded", state.excluded.has(item.object_id || ""));

    const expander = document.createElement("button");
    expander.type = "button";
    expander.className = "target-expander";
    expander.disabled = !hasChildren(item);
    expander.setAttribute("aria-label", `${item.title || "Untitled"} 하위 항목`);
    expander.setAttribute("aria-expanded", state.expanded.has(key) ? "true" : "false");
    expander.textContent = state.loading.has(key) ? "" : state.expanded.has(key) ? "⌄" : "›";
    if (state.loading.has(key)) {
      const spinner = document.createElement("span");
      spinner.className = "target-spinner tiny";
      spinner.setAttribute("aria-hidden", "true");
      expander.appendChild(spinner);
    }
    expander.addEventListener("click", () => toggleChildren(picker, item));

    const select = document.createElement("button");
    select.type = "button";
    select.className = "target-select";
    select.title = item.title || "Untitled";

    const title = document.createElement("strong");
    title.textContent = item.title || "Untitled";

    const meta = document.createElement("small");
    meta.textContent = itemMeta(item);
    select.replaceChildren(title, meta);
    select.addEventListener("click", () => selectItem(picker, item));

    const type = document.createElement("span");
    type.className = "target-type";
    type.textContent = labels[item.object_type] || item.object_type || "";

    const exclude = document.createElement("button");
    const excluded = state.excluded.has(item.object_id || "");
    exclude.type = "button";
    exclude.className = "target-exclude";
    exclude.disabled = item.object_type !== "page";
    exclude.title = excluded ? "제외 해제" : "제외";
    exclude.setAttribute("aria-label", `${item.title || "Untitled"} ${excluded ? "제외 해제" : "제외"}`);
    exclude.innerHTML = excluded
      ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 10.6A2 2 0 0 0 12 14a2 2 0 0 0 1.4-.6"/><path d="M9.9 4.2A9.8 9.8 0 0 1 12 4c5 0 8.5 4.5 9.7 6.3a3 3 0 0 1 0 3.4 18.5 18.5 0 0 1-2 2.5"/><path d="M6.1 6.1a18.7 18.7 0 0 0-3.8 4.2 3 3 0 0 0 0 3.4C3.5 15.5 7 20 12 20a9.7 9.7 0 0 0 4-.8"/></svg>';
    exclude.addEventListener("click", () => toggleExcluded(picker, item));

    row.replaceChildren(expander, select, type, exclude);
    return row;
  }

  function toggleExcluded(picker, item) {
    if (item.object_type !== "page") {
      setStatus(picker, "페이지 항목만 제외할 수 있습니다.", "error");
      return;
    }
    const state = stateFor(picker);
    const id = item.object_id || "";
    if (!id) {
      return;
    }
    const form = picker.closest("form");
    if (form && field(form, "notion_object_id").value === id) {
      setStatus(picker, "점검 대상으로 선택한 페이지는 제외할 수 없습니다.", "error");
      return;
    }
    if (state.excluded.has(id)) {
      state.excluded.delete(id);
      setStatus(picker, "제외 목록에서 제거했습니다.", "ok");
    } else {
      state.excluded.set(id, item);
      setStatus(picker, "제외 페이지를 추가했습니다.", "ok");
    }
    syncExcludedField(picker);
    renderTree(picker);
  }

  function syncExcludedField(picker) {
    const form = picker.closest("form");
    const state = stateFor(picker);
    if (form) {
      field(form, "excluded_page_ids").value = Array.from(state.excluded.keys()).join(",");
    }
    renderExclusions(picker);
  }

  function renderExclusions(picker) {
    const state = stateFor(picker);
    const wrapper = picker.querySelector("[data-target-exclusions]");
    const chips = picker.querySelector("[data-target-exclusion-chips]");
    const count = picker.querySelector("[data-target-exclusion-count]");
    if (!wrapper || !chips) {
      return;
    }
    const excluded = Array.from(state.excluded.values());
    wrapper.hidden = !excluded.length;
    if (count) {
      count.textContent = `${excluded.length}개`;
    }
    chips.replaceChildren(
      ...excluded.map((item) => {
        const chip = document.createElement("span");
        chip.className = "target-exclusion-chip";

        const title = document.createElement("span");
        title.textContent = item.title || "Untitled";
        title.title = item.title || "Untitled";

        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `${item.title || "Untitled"} 제외 해제`);
        remove.addEventListener("click", () => toggleExcluded(picker, item));

        chip.replaceChildren(title, remove);
        return chip;
      })
    );
  }

  function appendItems(fragment, picker, items, level) {
    const state = stateFor(picker);
    items.forEach((item) => {
      const key = keyFor(item);
      fragment.appendChild(treeRow(picker, item, level));
      if (state.loading.has(key)) {
        fragment.appendChild(loadingNode("하위 항목을 불러오는 중입니다.", level + 1));
      }
      if (state.expanded.has(key) && state.children.has(key)) {
        appendItems(fragment, picker, state.children.get(key), level + 1);
      }
    });
  }

  function renderTree(picker) {
    const results = picker.querySelector("[data-target-results]");
    const state = stateFor(picker);
    if (!results) {
      return;
    }
    results.replaceChildren();
    const fragment = document.createDocumentFragment();
    fragment.appendChild(workspaceNode(state.roots.length));
    if (state.loading.has("workspace")) {
      fragment.appendChild(loadingNode("워크스페이스 항목을 불러오는 중입니다.", 1));
    } else if (!state.roots.length) {
      const empty = document.createElement("div");
      empty.className = "target-empty";
      empty.textContent = "선택 가능한 Notion 대상이 없습니다.";
      fragment.appendChild(empty);
    } else {
      appendItems(fragment, picker, state.roots, 1);
    }
    results.appendChild(fragment);
  }

  function rememberItems(picker, items) {
    const state = stateFor(picker);
    items.forEach((item) => {
      state.items.set(keyFor(item), item);
    });
  }

  async function fetchItems(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || "Notion 목록을 불러오지 못했습니다.");
    }
    return Array.isArray(data.items) ? data.items : [];
  }

  async function toggleChildren(picker, item) {
    if (!hasChildren(item)) {
      return;
    }
    const state = stateFor(picker);
    const key = keyFor(item);
    if (state.loading.has(key)) {
      return;
    }
    if (state.loaded.has(key)) {
      if (state.expanded.has(key)) {
        state.expanded.delete(key);
      } else {
        state.expanded.add(key);
      }
      renderTree(picker);
      return;
    }

    const type = picker.querySelector("[data-target-type]");
    const url = new URL("/api/notion/children", window.location.origin);
    url.searchParams.set("limit", "50");
    url.searchParams.set("parent_id", item.object_id || "");
    url.searchParams.set("parent_type", item.object_type || "");
    if (type && type.value) {
      url.searchParams.set("object_type", type.value);
    }

    state.loading.add(key);
    state.expanded.add(key);
    renderTree(picker);
    try {
      const items = await fetchItems(url);
      state.children.set(key, items);
      state.loaded.add(key);
      rememberItems(picker, items);
      if (!items.length) {
        item.has_children = false;
        setStatus(picker, "하위 항목이 없습니다.", "muted");
      } else {
        setStatus(picker, `${items.length}개 하위 항목을 추가했습니다.`, "ok");
      }
    } catch (error) {
      state.expanded.delete(key);
      setStatus(picker, error.message || "하위 항목을 불러오지 못했습니다.", "error");
    } finally {
      state.loading.delete(key);
      renderTree(picker);
    }
  }

  async function loadTargets(picker) {
    const state = stateFor(picker);
    const query = picker.querySelector("[data-target-query]");
    const type = picker.querySelector("[data-target-type]");
    const button = picker.querySelector("[data-target-search]");
    const url = new URL("/api/notion/search", window.location.origin);
    url.searchParams.set("limit", "50");
    if (query && query.value.trim()) {
      url.searchParams.set("q", query.value.trim());
    }
    if (type && type.value) {
      url.searchParams.set("object_type", type.value);
    }

    clearSelection(picker);
    state.roots = [];
    state.children.clear();
    state.expanded = new Set(["workspace"]);
    state.loaded = new Set(["workspace"]);
    state.loading = new Set(["workspace"]);
    state.items.clear();
    renderTree(picker);
    setStatus(picker, query && query.value.trim() ? "검색 결과를 불러오는 중입니다." : "워크스페이스 항목을 불러오는 중입니다.", "loading");
    if (button) {
      button.disabled = true;
    }
    try {
      const items = await fetchItems(url);
      state.roots = items;
      rememberItems(picker, items);
      setStatus(picker, `${items.length}개 항목을 불러왔습니다.`, "ok");
    } catch (error) {
      state.roots = [];
      setStatus(picker, error.message || "Notion 목록을 불러오지 못했습니다.", "error");
    } finally {
      state.loading.delete("workspace");
      if (button) {
        button.disabled = false;
      }
      renderTree(picker);
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
