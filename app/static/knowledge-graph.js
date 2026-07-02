(function () {
  const root = document.querySelector("[data-knowledge-graph]");
  if (!root) return;

  const canvas = root.querySelector("[data-graph-canvas]");
  const statusEl = root.querySelector("[data-graph-status]");
  const nodeCountEl = root.querySelector("[data-graph-node-count]");
  const linkCountEl = root.querySelector("[data-graph-link-count]");
  const proposalCountEl = root.querySelector("[data-graph-proposal-count]");
  const syncButton = root.querySelector("[data-graph-sync]");
  const fitButton = root.querySelector("[data-graph-fit]");
  const inspector = {
    kicker: root.querySelector("[data-inspector-kicker]"),
    title: root.querySelector("[data-inspector-title]"),
    body: root.querySelector("[data-inspector-body]"),
    meta: root.querySelector("[data-inspector-meta]"),
    actions: root.querySelector("[data-inspector-actions]"),
  };
  const storageKey = "nocturne.knowledgeGraph.v1";
  const state = {
    graph: null,
    selected: null,
    hovered: null,
    data: { nodes: [], links: [], meta: {} },
  };

  function boot() {
    const cached = readCache();
    if (cached) renderData(cached, { fromCache: true });
    if (typeof ForceGraph !== "function") {
      setStatus("force-graph 로드 실패");
      return;
    }
    ensureGraph();
    loadGraph();
    syncButton.addEventListener("click", function () {
      syncGraph();
    });
    fitButton.addEventListener("click", function () {
      fitGraph();
    });
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
      });
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
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "그래프를 불러오지 못했습니다.");
      renderData(data);
      if (data.meta && data.meta.needsSync) syncGraph({ quiet: true });
    } catch (error) {
      setStatus(error.message || "그래프 오류");
    }
  }

  async function syncGraph(options) {
    const quiet = options && options.quiet;
    syncButton.disabled = true;
    if (!quiet) setStatus("Notion 동기화 중");
    try {
      const response = await fetch("/api/knowledge-graph/sync", { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "동기화 실패");
      renderData(data);
      setStatus(syncStatusText(data.meta));
    } catch (error) {
      setStatus(error.message || "동기화 실패");
    } finally {
      syncButton.disabled = false;
    }
  }

  async function approveProposal(node) {
    if (!node || !node.proposalId) return;
    setStatus("승인 반영 중");
    try {
      const response = await fetch(`/api/knowledge-graph/proposals/${node.proposalId}/approve`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "승인 실패");
      renderData(data);
      setStatus(data.message || "반영 완료");
      state.selected = null;
      renderInspector(null);
    } catch (error) {
      setStatus(error.message || "승인 실패");
    }
  }

  function renderData(data, options) {
    state.data = normalizeData(data);
    nodeCountEl.textContent = String(state.data.meta.nodeCount || state.data.nodes.length);
    linkCountEl.textContent = String(state.data.meta.linkCount || state.data.links.length);
    proposalCountEl.textContent = String(state.data.meta.proposalCount || 0);
    if (!options || !options.fromCache) writeCache(state.data);
    setStatus(options && options.fromCache ? "로컬 캐시" : syncStatusText(state.data.meta));
    if (state.graph) {
      state.graph.graphData({ nodes: state.data.nodes, links: state.data.links });
      requestAnimationFrame(fitGraph);
    }
    if (state.selected) {
      const selected = state.data.nodes.find(function (node) {
        return node.id === state.selected.id;
      });
      selectNode(selected || null, { keepView: true });
    } else {
      renderInspector(null);
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
      inspector.kicker.textContent = "선택";
      inspector.title.textContent = "지식 Graph";
      inspector.body.textContent = "캐시 대기";
      return;
    }
    if (node.kind === "proposal") {
      inspector.kicker.textContent = "Agent 제안";
      inspector.title.textContent = node.name || "제안";
      inspector.body.textContent = node.suggestedSentence || node.rationale || node.status || "대기";
      appendMeta("원본", node.sourceTitle || trim(node.sourcePageId || "", 18));
      appendMeta("상태", node.status || "-");
      appendMeta("방식", node.applyMode || "-");
      appendMeta("확신도", node.confidence ? `${Math.round(node.confidence * 100)}%` : "-");
      if (node.originalSentence) appendMeta("현재", trim(node.originalSentence, 120));
      if (node.rationale) appendMeta("근거", trim(node.rationale, 120));
      if (node.status !== "반영됨") {
        const button = document.createElement("button");
        button.className = "primary";
        button.type = "button";
        button.textContent = "승인";
        button.addEventListener("click", function () {
          approveProposal(node);
        });
        inspector.actions.appendChild(button);
      }
      if (node.notionProposalPageId) {
        inspector.actions.appendChild(notionLink(node.notionProposalPageId, "Notion"));
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

  function appendMeta(label, value) {
    const item = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = label;
    dd.textContent = value || "-";
    item.append(dt, dd);
    inspector.meta.appendChild(item);
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

  function fitGraph() {
    if (!state.graph || !state.data.nodes.length) return;
    state.graph.zoomToFit(420, 54);
  }

  function resizeGraph() {
    if (!state.graph || !canvas) return;
    const rect = canvas.getBoundingClientRect();
    state.graph.width(Math.max(280, rect.width));
    state.graph.height(Math.max(360, rect.height));
  }

  function syncStatusText(meta) {
    if (!meta) return "캐시";
    if (meta.syncStatus === "never") return "동기화 대기";
    if (meta.syncStatus === "partial_success") return "부분 동기화";
    if (meta.syncStatus === "failed") return "동기화 실패";
    if (meta.lastSyncedAt) return "동기화됨";
    return "캐시";
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

  function readCache() {
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!Array.isArray(data.nodes) || !Array.isArray(data.links)) return null;
      return data;
    } catch (error) {
      return null;
    }
  }

  function writeCache(data) {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(data));
    } catch (error) {
      // Browser storage is an optimization only.
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
