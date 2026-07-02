(function () {
  const root = document.querySelector("[data-knowledge-graph]");
  if (!root) return;

  const canvas = root.querySelector("[data-graph-canvas]");
  const statusEl = root.querySelector("[data-graph-status]");
  const emptyStateEl = root.querySelector("[data-graph-empty-state]");
  const emptyTitleEl = root.querySelector("[data-graph-empty-title]");
  const emptyBodyEl = root.querySelector("[data-graph-empty-body]");
  const nodeCountEl = root.querySelector("[data-graph-node-count]");
  const linkCountEl = root.querySelector("[data-graph-link-count]");
  const proposalCountEl = root.querySelector("[data-graph-proposal-count]");
  const inspector = {
    kicker: root.querySelector("[data-inspector-kicker]"),
    title: root.querySelector("[data-inspector-title]"),
    body: root.querySelector("[data-inspector-body]"),
    meta: root.querySelector("[data-inspector-meta]"),
    actions: root.querySelector("[data-inspector-actions]"),
  };
  const state = {
    graph: null,
    selected: null,
    hovered: null,
    syncing: false,
    fitTimers: [],
    fitOnEngineStop: false,
    autoSyncAttempted: false,
    data: { nodes: [], links: [], meta: {} },
  };

  function boot() {
    if (typeof ForceGraph !== "function") {
      setStatus("force-graph 로드 실패");
      return;
    }
    ensureGraph();
    loadGraph();
    window.addEventListener("resize", resizeGraph);
    if ("ResizeObserver" in window) {
      new ResizeObserver(resizeGraph).observe(canvas);
    }
  }

  function ensureGraph() {
    if (state.graph) return state.graph;
    state.graph = ForceGraph()(canvas)
      .backgroundColor("#101216")
      .nodeId("id")
      .nodeVal(function (node) {
        return node.val || 3;
      })
      .nodeColor(function (node) {
        return node.color || "#b8c7d9";
      })
      .nodeLabel(function (node) {
        return node.name || "";
      })
      .nodeCanvasObjectMode(function () {
        return "replace";
      })
      .nodeCanvasObject(paintNode)
      .nodePointerAreaPaint(paintPointerArea)
      .linkColor(function (link) {
        return link.color || "rgba(160, 174, 192, 0.35)";
      })
      .linkWidth(function (link) {
        return link.proposal ? 1.7 : 0.8;
      })
      .linkLineDash(function (link) {
        return link.proposal ? [3, 5] : null;
      })
      .linkDirectionalParticles(function (link) {
        return link.proposal ? 2 : 0;
      })
      .linkDirectionalParticleWidth(2.3)
      .linkDirectionalParticleSpeed(0.006)
      .onNodeHover(function (node) {
        state.hovered = node || null;
        canvas.style.cursor = node ? "pointer" : "";
      })
      .onNodeClick(function (node) {
        selectNode(node);
      })
      .onEngineStop(function () {
        if (!state.fitOnEngineStop || state.selected) return;
        state.fitOnEngineStop = false;
        fitGraph({ duration: 240, padding: 76 });
      });
    if (emptyStateEl && !emptyStateEl.isConnected) {
      canvas.appendChild(emptyStateEl);
    }
    state.graph.d3Force("charge").strength(-72);
    state.graph.d3Force("link").distance(function (link) {
      return link.proposal ? 84 : 48;
    });
    resizeGraph();
    return state.graph;
  }

  function paintNode(node, ctx, globalScale) {
    const selected = state.selected && state.selected.id === node.id;
    const hovered = state.hovered && state.hovered.id === node.id;
    const isProposal = node.kind === "proposal";
    const radius = Math.max(3, Math.sqrt(node.val || 3) * (isProposal ? 2.2 : 1.85));
    const color = selected ? "#c8ff49" : node.color || "#b8c7d9";

    ctx.save();
    ctx.globalAlpha = isProposal ? 0.98 : 0.92;
    if (selected || hovered || isProposal) {
      ctx.beginPath();
      ctx.fillStyle = isProposal ? "rgba(240, 106, 79, 0.18)" : "rgba(200, 255, 73, 0.13)";
      ctx.arc(node.x, node.y, radius + (selected ? 9 : 6), 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.fillStyle = color;
    ctx.strokeStyle = isProposal ? "#ffe1d9" : "rgba(255,255,255,0.55)";
    ctx.lineWidth = selected ? 1.6 : 0.8;
    if (isProposal) {
      ctx.translate(node.x, node.y);
      ctx.rotate(Math.PI / 4);
      ctx.beginPath();
      ctx.rect(-radius, -radius, radius * 2, radius * 2);
      ctx.fill();
      ctx.stroke();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    } else {
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }

    if (selected || hovered || globalScale > 1.35 || (isProposal && globalScale > 0.95)) {
      drawLabel(node, ctx, globalScale, radius);
    }
    ctx.restore();
  }

  function paintPointerArea(node, color, ctx) {
    const radius = Math.max(8, Math.sqrt(node.val || 3) * 4);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawLabel(node, ctx, globalScale, radius) {
    const label = trim(node.name || "", node.kind === "proposal" ? 52 : 34);
    if (!label) return;
    const fontSize = Math.max(8, 11 / globalScale);
    const padding = 3 / globalScale;
    ctx.font = `${fontSize}px Inter, ui-sans-serif, system-ui, sans-serif`;
    const textWidth = ctx.measureText(label).width;
    const x = node.x + radius + 5 / globalScale;
    const y = node.y + fontSize / 3;
    ctx.fillStyle = "rgba(16, 18, 22, 0.74)";
    ctx.fillRect(x - padding, y - fontSize, textWidth + padding * 2, fontSize + padding * 2);
    ctx.fillStyle = node.kind === "proposal" ? "#ffd2c7" : "#e6edf7";
    ctx.fillText(label, x, y);
  }

  async function loadGraph() {
    try {
      const response = await fetch("/api/knowledge-graph");
      if (redirectIfUnauthorized(response)) return;
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "그래프를 불러오지 못했습니다.");
      renderData(data);
      if (shouldAutoSync(state.data.meta)) syncGraph({ quiet: true });
    } catch (error) {
      setStatus(error.message || "그래프 오류");
    }
  }

  async function syncGraph(options) {
    if (state.syncing) return;
    const quiet = options && options.quiet;
    state.syncing = true;
    const buildingGraph = isGraphUnbuilt();
    if (buildingGraph) {
      setStatus("Graph 만드는 중");
      setEmptyState(true, "Graph 만드는 중", "Notion 구조를 읽고 있습니다.");
    } else if (!quiet) {
      setStatus("Notion 동기화 중");
    }
    try {
      const response = await fetch("/api/knowledge-graph/sync", { method: "POST" });
      if (redirectIfUnauthorized(response)) return;
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "동기화 실패");
      renderData(data, { fromSync: true });
      setStatus(syncStatusText(data.meta));
    } catch (error) {
      setStatus(error.message || "동기화 실패");
    } finally {
      state.syncing = false;
      updateEmptyState();
    }
  }

  async function approveProposal(node) {
    if (!node || !node.proposalId) return;
    setStatus("승인 반영 중");
    try {
      const response = await fetch(`/api/knowledge-graph/proposals/${node.proposalId}/approve`, { method: "POST" });
      if (redirectIfUnauthorized(response)) return;
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "승인 실패");
      renderData(data);
      setStatus(data.message || "반영 완료");
      state.selected = null;
      renderDefaultInspector();
    } catch (error) {
      setStatus(error.message || "승인 실패");
    }
  }

  async function rejectProposal(node) {
    if (!node || !node.proposalId) return;
    setStatus("거부 처리 중");
    try {
      const response = await fetch(`/api/proposals/${node.proposalId}/reject`, { method: "POST" });
      if (redirectIfUnauthorized(response)) return;
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.message || "거부 실패");
      await loadGraph();
      state.selected = null;
      renderDefaultInspector();
      setStatus(data.message || "거부 완료");
    } catch (error) {
      setStatus(error.message || "거부 실패");
    }
  }

  function renderData(data, options) {
    state.data = normalizeData(data);
    nodeCountEl.textContent = String(state.data.meta.nodeCount || state.data.nodes.length);
    linkCountEl.textContent = String(state.data.meta.linkCount || state.data.links.length);
    proposalCountEl.textContent = String(state.data.meta.proposalCount || 0);
    setStatus(syncStatusText(state.data.meta));
    updateEmptyState();
    if (state.graph) {
      resizeGraph();
      state.graph.graphData({ nodes: state.data.nodes, links: state.data.links });
      scheduleFitGraph();
    }
    if (state.selected) {
      const selected = state.data.nodes.find(function (node) {
        return node.id === state.selected.id;
      });
      selectNode(selected || null, { keepView: true });
    } else {
      renderDefaultInspector();
    }
  }

  function normalizeData(data) {
    const nodes = Array.isArray(data.nodes) ? data.nodes : [];
    const links = Array.isArray(data.links) ? data.links : [];
    const meta = data.meta || {};
    return { nodes, links, meta };
  }

  function selectNode(node, options) {
    state.selected = node || null;
    renderInspector(node || null);
    if (!node || !state.graph || (options && options.keepView)) return;
    state.graph.centerAt(node.x || 0, node.y || 0, 420);
    state.graph.zoom(2.1, 420);
  }

  function renderInspector(node) {
    clearElement(inspector.meta);
    clearElement(inspector.actions);
    if (!node) {
      renderDefaultInspector();
      return;
    }
    if (node.kind === "proposal") {
      inspector.kicker.textContent = "Agent 제안";
      inspector.title.textContent = node.name || "제안";
      inspector.body.textContent = node.rationale || node.suggestedSentence || "제안 내용을 확인해 주세요.";
      appendMeta("확신도", node.confidence ? `${Math.round(node.confidence * 100)}%` : "-");
      appendProposalDiff(node);
      if (node.rationale) appendTextMeta("근거", node.rationale);
      if (Array.isArray(node.sourceUrls) && node.sourceUrls.length) appendTextMeta("출처", node.sourceUrls.join("\n"));
      if (node.status !== "반영됨" && node.status !== "거절") {
        const button = document.createElement("button");
        button.className = "primary";
        button.type = "button";
        button.textContent = "승인";
        button.addEventListener("click", function () {
          approveProposal(node);
        });
        inspector.actions.appendChild(button);
        const rejectButton = document.createElement("button");
        rejectButton.type = "button";
        rejectButton.textContent = "거부";
        rejectButton.addEventListener("click", function () {
          rejectProposal(node);
        });
        inspector.actions.appendChild(rejectButton);
      }
      return;
    }
    inspector.kicker.textContent = node.type === "database" ? "데이터베이스" : "사용자 지식";
    inspector.title.textContent = node.name || "Untitled";
    inspector.body.textContent = node.type === "database" ? "Database" : "Page";
    appendMeta("ID", trim(node.objectId || node.id || "", 28));
    if (node.lastEditedTime) appendMeta("수정", node.lastEditedTime);
    if (node.parentType) appendMeta("상위", node.parentType);
    if (node.url) {
      const link = document.createElement("a");
      link.className = "button";
      link.href = node.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "Notion";
      inspector.actions.appendChild(link);
    }
  }

  function renderDefaultInspector() {
    clearElement(inspector.meta);
    clearElement(inspector.actions);
    const meta = state.data.meta || {};
    const workspaceName = meta.workspaceName || meta.workspaceId || "";
    if (workspaceName) {
      inspector.kicker.textContent = "워크스페이스";
      inspector.title.textContent = workspaceName;
      inspector.body.textContent = "이 Notion workspace를 기준으로 Graph를 구성합니다.";
      if (meta.workspaceId) appendMeta("Workspace ID", trim(meta.workspaceId, 32));
      if (meta.knowledgeNodeCount) appendMeta("지식 노드", `${meta.knowledgeNodeCount}개`);
      return;
    }
    inspector.kicker.textContent = "워크스페이스";
    inspector.title.textContent = "Notion workspace";
    inspector.body.textContent = "Notion을 연결하고 점검 대상을 추가하면 Graph를 구성합니다.";
  }

  function appendMeta(label, value) {
    const item = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = label;
    dd.textContent = value || "-";
    item.append(dt, dd);
    inspector.meta.appendChild(item);
  }

  function appendTextMeta(label, value) {
    const text = String(value || "").trim();
    if (!text) return;
    appendMeta(label, text);
  }

  function appendProposalDiff(node) {
    const original = node.originalSentence || "";
    const suggested = node.suggestedSentence || "";
    if (!original && !suggested) return;
    const item = document.createElement("div");
    item.className = "graph-proposal-diff";
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = node.applyMode === "append" ? "추가 제안" : "수정 diff";
    if (original) dd.appendChild(diffLine("-", original));
    if (suggested) dd.appendChild(diffLine("+", suggested));
    item.append(dt, dd);
    inspector.meta.appendChild(item);
  }

  function diffLine(prefix, value) {
    const row = document.createElement("div");
    row.className = `diff-line ${prefix === "+" ? "add" : "remove"}`;
    const mark = document.createElement("span");
    const body = document.createElement("p");
    mark.textContent = prefix;
    body.textContent = value;
    row.append(mark, body);
    return row;
  }

  function notionLink(pageId, label) {
    const link = document.createElement("a");
    link.className = "button";
    link.href = `https://www.notion.so/${String(pageId).replace(/-/g, "")}`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = label;
    return link;
  }

  function scheduleFitGraph() {
    clearFitTimers();
    if (!state.graph || !state.data.nodes.length) return;
    state.fitOnEngineStop = true;
    fitGraph({ duration: 0, padding: 84 });
    [120, 360, 820, 1400].forEach(function (delay) {
      state.fitTimers.push(
        window.setTimeout(function () {
          if (!state.selected) fitGraph({ duration: delay < 400 ? 0 : 260, padding: 76 });
        }, delay)
      );
    });
  }

  function clearFitTimers() {
    state.fitTimers.forEach(function (timer) {
      window.clearTimeout(timer);
    });
    state.fitTimers = [];
  }

  function fitGraph(options) {
    if (!state.graph || !state.data.nodes.length) return;
    const duration = options && Number.isFinite(options.duration) ? options.duration : 240;
    const padding = options && Number.isFinite(options.padding) ? options.padding : 76;
    state.graph.zoomToFit(duration, padding);
  }

  function resizeGraph() {
    if (!state.graph || !canvas) return;
    const rect = canvas.getBoundingClientRect();
    state.graph.width(Math.max(280, rect.width));
    state.graph.height(Math.max(360, rect.height));
    if (state.data.nodes.length && !state.selected) {
      window.requestAnimationFrame(function () {
        fitGraph({ duration: 0, padding: 84 });
      });
    }
  }

  function shouldAutoSync(meta) {
    if (state.autoSyncAttempted || !meta || !meta.hasTargets) return false;
    const shouldSync = meta.needsSync || meta.syncStatus === "never" || meta.syncStatus === "failed";
    state.autoSyncAttempted = shouldSync;
    return shouldSync;
  }

  function isGraphUnbuilt() {
    return Boolean(state.data.meta && state.data.meta.hasTargets && state.data.meta.needsSync && !state.data.nodes.length);
  }

  function updateEmptyState() {
    if (state.syncing && isGraphUnbuilt()) {
      setStatus("Graph 만드는 중");
      setEmptyState(true, "Graph 만드는 중", "Notion 구조를 읽고 있습니다.");
      return;
    }
    if (isGraphUnbuilt()) {
      setEmptyState(true, "Graph 생성 대기", "곧 Notion 구조를 읽어옵니다.");
      return;
    }
    setEmptyState(false);
  }

  function setEmptyState(visible, title, body) {
    if (!emptyStateEl) return;
    emptyStateEl.hidden = !visible;
    if (emptyTitleEl && title) emptyTitleEl.textContent = title;
    if (emptyBodyEl && body) emptyBodyEl.textContent = body;
  }

  function syncStatusText(meta) {
    if (!meta) return "Graph 준비";
    if (meta.syncStatus === "never") return "동기화 대기";
    if (meta.syncStatus === "partial_success") return "부분 동기화";
    if (meta.syncStatus === "failed") return "동기화 실패";
    if (meta.lastSyncedAt) return "동기화됨";
    return "Graph 준비";
  }

  function setStatus(message) {
    statusEl.textContent = message;
  }

  function trim(value, limit) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
  }

  function clearElement(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function redirectIfUnauthorized(response) {
    if (response.status !== 401) return false;
    window.location.assign("/");
    return true;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
