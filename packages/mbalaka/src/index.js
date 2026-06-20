import ForceGraph3D from "3d-force-graph";
import SpriteText from "three-spritetext";
import * as THREE from "three";

const DEFAULT_COLORS = {
  jobRoot: "#f58220",
  courseRoot: "#7bd88f",
  technical: "#2f80ed",
  soft: "#39a96b",
  business: "#ffb703",
  domain: "#9aa0a6",
  selected: "#ffffff",
  evidenceLink: "rgba(123,216,143,.32)",
  similarityLink: "rgba(245,130,32,.74)",
};

const DEFAULT_LABELS = {
  title: "Semantic Skill Vector Space",
  loading: "Loading root jobs, courses, skills, and cosine-similarity strands.",
  emptyTitle: "No vector-space data yet",
  emptyText: "Add or extract skills, then return to this view.",
  panelTitle: "Select a node or strand",
  panelText: "Root jobs and root courses are connected to extracted skills. Skill-to-skill strands show semantic association.",
};

export function createMbalakaVectorSpace(options = {}) {
  const instance = new MbalakaVectorSpace(options);
  instance.mount();
  return instance;
}

export class MbalakaVectorSpace {
  constructor(options = {}) {
    if (!options.container) {
      throw new Error("Mbalaka requires a container element or selector.");
    }
    this.container = resolveElement(options.container);
    this.options = {
      apiUrl: "",
      data: null,
      autoLoad: true,
      controls: true,
      legend: true,
      panel: true,
      className: "",
      labels: {},
      colors: {},
      graphOptions: {},
      fetchOptions: {},
      ...options,
    };
    this.labels = { ...DEFAULT_LABELS, ...this.options.labels };
    this.colors = { ...DEFAULT_COLORS, ...this.options.colors };
    this.graph = null;
    this.paused = false;
    this.selectedNodeId = null;
    this.selectedLinkKey = null;
    this.rawData = null;
    this.currentGraphData = { nodes: [], links: [] };
    this.controls = {};
  }

  mount() {
    this.container.innerHTML = "";
    this.container.classList.add("mbalaka-shell");
    if (this.options.className) {
      this.container.classList.add(...this.options.className.split(/\s+/).filter(Boolean));
    }
    this.container.style.setProperty("--mbalaka-accent", this.colors.jobRoot);
    this.container.appendChild(this.renderToolbar());
    this.container.appendChild(this.renderStage());
    this.bindEvents();
    if (this.options.data) {
      this.setData(this.options.data);
    } else if (this.options.autoLoad && this.options.apiUrl) {
      this.load(this.options.apiUrl);
    }
    return this;
  }

  renderToolbar() {
    const toolbar = el("div", "mbalaka-toolbar");
    toolbar.innerHTML = `
      <div>
        <div class="mbalaka-title">${escapeHtml(this.labels.title)}</div>
        <div class="mbalaka-subtitle" data-mbalaka-status>${escapeHtml(this.labels.loading)}</div>
      </div>
      <div class="mbalaka-actions" data-mbalaka-actions></div>
    `;
    this.statusEl = toolbar.querySelector("[data-mbalaka-status]");
    const actions = toolbar.querySelector("[data-mbalaka-actions]");
    if (this.options.controls) {
      actions.append(
        this.selectControl("source", [["", "All sources"], ["job-root", "Jobs"], ["course-root", "Courses"], ["skill", "Skills"]]),
        this.selectControl("skill", [["", "All skills"]]),
        this.selectControl("sector", [["", "All sectors"]]),
        this.selectControl("type", [["", "All skill types"]]),
        this.selectControl("job", [["", "All jobs"]]),
        this.selectControl("course", [["", "All courses"]]),
        this.selectControl("extractor", [["", "All extractors"]]),
        this.inputControl("search", "Find a skill, job, or course"),
        this.buttonControl("focus", "Focus"),
        this.buttonControl("clear", "Clear"),
        this.buttonControl("json", "Download JSON"),
        this.buttonControl("csv", "Download CSV"),
      );
    }
    this.nodeCountEl = el("span", "mbalaka-stat", "0 nodes");
    this.edgeCountEl = el("span", "mbalaka-stat", "0 strands");
    actions.append(
      this.nodeCountEl,
      this.edgeCountEl,
      this.buttonControl("fit", "Fit view"),
      this.buttonControl("pause", "Pause"),
    );
    return toolbar;
  }

  renderStage() {
    const stage = el("div", "mbalaka-stage");
    this.graphEl = el("div", "mbalaka-graph");
    this.emptyEl = el("div", "mbalaka-empty is-active");
    this.emptyEl.innerHTML = `<div><div style="font-weight:800;color:#fff;">${escapeHtml(this.labels.emptyTitle)}</div><div style="font-size:12px;margin-top:8px;">${escapeHtml(this.labels.emptyText)}</div></div>`;
    stage.append(this.graphEl, this.emptyEl);

    if (this.options.panel) {
      this.panelEl = el("div", "mbalaka-panel");
      this.panelEl.innerHTML = `<div class="mbalaka-panel-title">${escapeHtml(this.labels.panelTitle)}</div><div class="mbalaka-panel-meta">${escapeHtml(this.labels.panelText)}</div>`;
      stage.appendChild(this.panelEl);
    }
    if (this.options.legend) {
      stage.appendChild(this.renderLegend());
    }
    return stage;
  }

  renderLegend() {
    const legend = el("div", "mbalaka-legend");
    legend.innerHTML = `
      <div class="mbalaka-legend-title">Legend</div>
      <div class="mbalaka-legend-grid">
        ${legendSwatch(this.colors.jobRoot, "Job root")}
        ${legendSwatch(this.colors.courseRoot, "Course root")}
        ${legendSwatch(this.colors.technical, "Technical skill")}
        ${legendSwatch(this.colors.soft, "Soft skill")}
        ${legendSwatch(this.colors.business, "Business skill")}
        ${legendSwatch(this.colors.domain, "Other skill")}
        ${legendLine("rgba(123,216,143,.72)", "Evidence strand")}
        ${legendLine("rgba(245,130,32,.88)", "Similarity strand")}
      </div>
    `;
    return legend;
  }

  bindEvents() {
    this.controls.fit?.addEventListener("click", () => this.fit());
    this.controls.focus?.addEventListener("click", () => this.focusSearchResult());
    this.controls.clear?.addEventListener("click", () => this.clearFilters());
    this.controls.json?.addEventListener("click", () => this.downloadJson());
    this.controls.csv?.addEventListener("click", () => this.downloadCsv());
    this.controls.pause?.addEventListener("click", () => this.togglePause());
    this.controls.search?.addEventListener("keydown", event => {
      if (event.key === "Enter") this.focusSearchResult();
    });
    ["source", "skill", "sector", "type", "job", "course", "extractor"].forEach(key => {
      this.controls[key]?.addEventListener("change", () => this.applyFilters());
    });
    this.resizeHandler = () => this.resize();
    window.addEventListener("resize", this.resizeHandler);
  }

  async load(apiUrl = this.options.apiUrl) {
    this.setStatus(this.labels.loading);
    const response = await fetch(apiUrl, { headers: { Accept: "application/json" }, ...this.options.fetchOptions });
    if (!response.ok) {
      throw new Error(`Mbalaka could not load data: HTTP ${response.status}`);
    }
    const data = await response.json();
    this.setData(data);
    return data;
  }

  setData(data) {
    this.rawData = normalizePayload(data);
    this.populateFilters(this.rawData);
    this.renderGraph(this.filteredData(this.rawData));
    return this;
  }

  populateFilters(data) {
    const nodes = data.nodes || [];
    const skills = nodes.filter(node => node.group === "skill").sort(labelSort);
    const jobs = nodes.filter(node => node.group === "job-root").sort(labelSort);
    const courses = nodes.filter(node => node.group === "course-root").sort(labelSort);
    this.setSelectOptions("skill", skills.map(node => [node.id, node.full_label || node.label || node.id]));
    this.setSelectOptions("job", jobs.map(node => [node.id, node.full_label || node.label || node.id]));
    this.setSelectOptions("course", courses.map(node => [node.id, node.full_label || node.label || node.id]));
    this.setSelectOptions("sector", uniqueOptions(nodes.map(node => node.sector)));
    this.setSelectOptions("type", uniqueOptions(skills.map(node => node.skill_type)));
    this.setSelectOptions("extractor", uniqueOptions(skills.map(node => node.source)));
  }

  setSelectOptions(key, values) {
    const select = this.controls[key];
    if (!select) return;
    const current = select.value;
    const first = select.options[0]?.cloneNode(true);
    select.innerHTML = "";
    if (first) select.appendChild(first);
    values.forEach(value => {
      const [optionValue, label] = Array.isArray(value) ? value : [value, value];
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = label;
      select.appendChild(option);
    });
    select.value = values.some(value => (Array.isArray(value) ? value[0] : value) === current) ? current : "";
  }

  selectedFilters() {
    return Object.fromEntries(["source", "skill", "sector", "type", "job", "course", "extractor"].map(key => [key, this.controls[key]?.value || ""]));
  }

  filteredData(data) {
    if (!data) return emptyPayload();
    const filters = this.selectedFilters();
    if (!Object.values(filters).some(Boolean)) return data;
    const nodeById = new Map(data.nodes.map(node => [node.id, node]));
    const visible = new Set();
    const matchingSkillIds = new Set();
    const matchingRootIds = new Set();

    data.nodes.forEach(node => {
      let matches = true;
      if (filters.source && node.group !== filters.source) matches = false;
      if (filters.skill && node.id !== filters.skill) matches = false;
      if (filters.sector && node.sector !== filters.sector) matches = false;
      if (filters.type && node.skill_type !== filters.type) matches = false;
      if (filters.extractor && node.source !== filters.extractor) matches = false;
      if (filters.job && node.id !== filters.job) matches = false;
      if (filters.course && node.id !== filters.course) matches = false;
      if (!matches) return;
      visible.add(node.id);
      if (node.group === "skill") matchingSkillIds.add(node.id);
      if (node.group === "job-root" || node.group === "course-root") matchingRootIds.add(node.id);
    });

    if (filters.skill) matchingSkillIds.add(filters.skill);
    if (filters.job) matchingRootIds.add(filters.job);
    if (filters.course) matchingRootIds.add(filters.course);

    data.edges.forEach(edge => {
      const source = endpointId(edge.source);
      const target = endpointId(edge.target);
      if (edge.group === "evidence") {
        const sourceNode = nodeById.get(source);
        const rootId = sourceNode?.group === "skill" ? target : source;
        const skillId = sourceNode?.group === "skill" ? source : target;
        if (matchingSkillIds.has(skillId) || matchingRootIds.has(rootId)) {
          visible.add(rootId);
          visible.add(skillId);
        }
      }
      if (edge.group === "similarity" && (matchingSkillIds.has(source) || matchingSkillIds.has(target))) {
        visible.add(source);
        visible.add(target);
      }
    });

    const nodes = data.nodes.filter(node => visible.has(node.id));
    const edges = data.edges.filter(edge => visible.has(endpointId(edge.source)) && visible.has(endpointId(edge.target)));
    return withCounts({ nodes, edges, has_visual_data: Boolean(nodes.length) });
  }

  applyFilters() {
    this.selectedNodeId = null;
    this.selectedLinkKey = null;
    this.renderGraph(this.filteredData(this.rawData));
  }

  clearFilters() {
    ["source", "skill", "sector", "type", "job", "course", "extractor"].forEach(key => {
      if (this.controls[key]) this.controls[key].value = "";
    });
    if (this.controls.search) this.controls.search.value = "";
    this.applyFilters();
  }

  renderGraph(data) {
    const hasData = data.has_visual_data && data.nodes.length;
    this.emptyEl.classList.toggle("is-active", !hasData);
    this.nodeCountEl.textContent = `${data.counts.nodes} nodes`;
    this.edgeCountEl.textContent = `${data.counts.edges} strands`;
    this.setStatus(hasData ? `${data.counts.roots} roots and ${data.counts.skills} skill nodes. Drag, scroll, and orbit freely.` : "No skill vector data for the current filters.");
    if (!hasData) {
      this.currentGraphData = { nodes: [], links: [] };
      this.graph?.graphData({ nodes: [], links: [] });
      return;
    }
    const graphData = {
      nodes: data.nodes.map(node => ({ ...node, name: node.label })),
      links: data.edges.map(edge => ({ ...edge })),
    };
    this.currentGraphData = graphData;
    if (!this.graph) {
      this.graph = ForceGraph3D()(this.graphEl)
        .backgroundColor("rgba(0,0,0,0)")
        .showNavInfo(false)
        .nodeLabel(node => this.nodeLabel(node))
        .linkLabel(link => this.linkLabel(link))
        .nodeColor(node => this.nodeColor(node))
        .nodeVal(node => node.group === "job-root" || node.group === "course-root" ? 9 : 4.5)
        .linkWidth(link => this.selectedLinkKey === linkKey(link) ? 4 : link.group === "similarity" ? Math.max(1, Number(link.value || 1)) : 1.6)
        .linkColor(link => this.linkColor(link))
        .linkOpacity(.54)
        .linkDirectionalParticles(link => link.group === "similarity" ? 2 : 1)
        .linkDirectionalParticleWidth(link => link.group === "similarity" ? 1.8 : 1)
        .onNodeClick(node => this.focusNode(node))
        .onLinkClick(link => this.focusLink(link))
        .nodeThreeObject(node => this.createNodeObject(node))
        .linkThreeObjectExtend(true)
        .linkThreeObject(link => this.createLinkLabel(link))
        .linkPositionUpdate((sprite, { start, end }) => positionLinkLabel(sprite, start, end));
      this.graph.d3Force("charge").strength(this.options.graphOptions.chargeStrength ?? -95);
      this.graph.d3Force("link").distance(link => link.group === "similarity" ? 58 : 42);
    }
    this.resize();
    this.graph.graphData(graphData);
    setTimeout(() => this.fit(900), 450);
  }

  focusNode(node) {
    if (!node || !this.graph) return;
    this.selectedNodeId = node.id;
    this.selectedLinkKey = null;
    const distance = node.group === "skill" ? 82 : 112;
    const distRatio = 1 + distance / Math.hypot(node.x || 1, node.y || 1, node.z || 1);
    this.graph.cameraPosition(
      { x: (node.x || 1) * distRatio, y: (node.y || 1) * distRatio, z: (node.z || 1) * distRatio },
      node,
      900,
    );
    this.refreshGraphStyles();
    if (this.panelEl) {
      this.panelEl.innerHTML = `
        <div class="mbalaka-panel-title">${node.group === "skill" ? "Skill node" : node.group === "job-root" ? "Root job" : "Root course"}</div>
        <div class="mbalaka-panel-meta"><strong>${escapeHtml(node.full_label || node.label)}</strong><br>
          ${node.group === "course-root" && node.course_code ? `Course code: ${escapeHtml(node.course_code)}<br>` : ""}
          ${node.group === "course-root" && node.course_name ? `Course name: ${escapeHtml(node.course_name)}<br>` : ""}
          ${node.skill_type ? `Type: ${escapeHtml(node.skill_type)}<br>` : ""}
          ${node.sector ? `Sector: ${escapeHtml(node.sector)}<br>` : ""}
          ${node.group === "course-root" && node.parent_label ? `School: ${escapeHtml(node.parent_label)}<br>` : ""}
          ${node.source ? `Extractor: ${escapeHtml(node.source)}<br>` : ""}
          ${node.confidence ? `Confidence: ${Math.round(Number(node.confidence) * 100)}%` : "Linked evidence is shown as strands."}
          ${node.description ? `<br><br>${escapeHtml(node.description.slice(0, 220))}` : ""}
        </div>`;
    }
  }

  focusLink(link) {
    this.selectedLinkKey = linkKey(link);
    this.selectedNodeId = null;
    this.refreshGraphStyles();
    if (this.panelEl) {
      this.panelEl.innerHTML = `
        <div class="mbalaka-panel-title">${link.group === "similarity" ? "Cosine strand" : "Evidence strand"}</div>
        <div class="mbalaka-panel-meta"><strong>${escapeHtml(this.linkLabel(link))}</strong><br>${link.group === "similarity" ? "This connects skills by semantic association." : "This connects a root job or course to an extracted skill."}</div>`;
    }
  }

  focusSearchResult() {
    const term = this.controls.search?.value.trim().toLowerCase();
    if (!term) return null;
    const match = this.currentGraphData.nodes.find(node =>
      [node.label, node.full_label, node.skill, node.sector, node.parent_label, node.description]
        .filter(Boolean)
        .some(value => String(value).toLowerCase().includes(term)),
    );
    if (match) this.focusNode(match);
    return match || null;
  }

  createNodeObject(node) {
    const group = new THREE.Group();
    const isRoot = node.group === "job-root" || node.group === "course-root";
    const active = node.id === this.selectedNodeId;
    const sphere = new THREE.Mesh(
      new THREE.SphereGeometry(active ? 6.8 : isRoot ? 5.8 : 3.7, 24, 24),
      new THREE.MeshLambertMaterial({ color: this.nodeColor(node) }),
    );
    group.add(sphere);
    if (active) {
      group.add(new THREE.Mesh(
        new THREE.SphereGeometry(isRoot ? 8.2 : 5.4, 24, 24),
        new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, transparent: true, opacity: .46 }),
      ));
    }
    const label = new SpriteText(node.label);
    label.color = "#ffffff";
    label.backgroundColor = active ? "rgba(245,130,32,.92)" : isRoot ? "rgba(245,130,32,.78)" : "rgba(0,0,0,.54)";
    label.borderColor = active ? "#ffffff" : isRoot ? "#ffffff" : this.nodeColor(node);
    label.borderWidth = .35;
    label.padding = 3;
    label.textHeight = active ? 4.6 : isRoot ? 4.3 : 3.3;
    label.position.y = isRoot ? 10 : 7;
    group.add(label);
    return group;
  }

  createLinkLabel(link) {
    const text = link.group === "similarity" ? link.label || `${Math.round(link.similarity || 0)}% cosine` : "";
    if (!text) return null;
    const sprite = new SpriteText(text);
    sprite.color = "#ffffff";
    sprite.backgroundColor = "rgba(245,130,32,.70)";
    sprite.padding = 2;
    sprite.textHeight = 2.6;
    return sprite;
  }

  nodeColor(node) {
    if (node.id === this.selectedNodeId) return this.colors.selected;
    if (node.group === "job-root") return this.colors.jobRoot;
    if (node.group === "course-root") return this.colors.courseRoot;
    if (node.skill_type === "technical") return this.colors.technical;
    if (node.skill_type === "soft") return this.colors.soft;
    if (node.skill_type === "business") return this.colors.business;
    return this.colors.domain;
  }

  linkColor(link) {
    const source = endpointId(link.source);
    const target = endpointId(link.target);
    const active = this.selectedLinkKey === linkKey(link) || this.selectedNodeId === source || this.selectedNodeId === target;
    if (active) return this.colors.selected;
    return link.group === "similarity" ? this.colors.similarityLink : this.colors.evidenceLink;
  }

  nodeLabel(node) {
    const description = node.description ? `\n${node.description.slice(0, 160)}` : "";
    if (node.group === "job-root") return `Root job: ${node.full_label || node.label}\nSector: ${node.sector || "Unclassified"}${description}`;
    if (node.group === "course-root") {
      const courseName = node.course_name || node.full_label || node.label;
      const courseCode = node.course_code ? `${node.course_code}: ` : "";
      const school = node.parent_label ? `\nSchool: ${node.parent_label}` : "";
      return `Course root: ${courseCode}${courseName}${school}\nSource: Course/module skill evidence${description}`;
    }
    return `Skill: ${node.full_label || node.label}\nType: ${node.skill_type || "domain"}\nSector: ${node.sector || "Unclassified"}`;
  }

  linkLabel(link) {
    if (link.group === "similarity") return link.title || `Cosine similarity: ${Math.round(link.similarity || 0)}%`;
    return link.title || "Root evidence link";
  }

  refreshGraphStyles() {
    this.graph?.nodeColor(node => this.nodeColor(node));
    this.graph?.nodeThreeObject(node => this.createNodeObject(node));
    this.graph?.linkColor(link => this.linkColor(link));
    this.graph?.linkWidth(link => this.selectedLinkKey === linkKey(link) ? 4 : link.group === "similarity" ? Math.max(1, Number(link.value || 1)) : 1.6);
  }

  resize() {
    if (!this.graph) return;
    this.graph.width(this.graphEl.clientWidth || this.container.clientWidth || window.innerWidth);
    this.graph.height(this.graphEl.clientHeight || this.container.clientHeight || window.innerHeight);
  }

  fit(duration = 600, padding = 90) {
    this.graph?.zoomToFit(duration, padding);
    return this;
  }

  togglePause() {
    this.paused = !this.paused;
    if (this.paused) this.graph?.pauseAnimation?.();
    if (!this.paused) this.graph?.resumeAnimation?.();
    if (this.controls.pause) this.controls.pause.textContent = this.paused ? "Resume" : "Pause";
    return this.paused;
  }

  exportData() {
    return {
      exported_at: new Date().toISOString(),
      filters: this.selectedFilters(),
      counts: {
        nodes: this.currentGraphData.nodes.length,
        links: this.currentGraphData.links.length,
      },
      nodes: this.currentGraphData.nodes.map(node => ({ ...node })),
      links: this.currentGraphData.links.map(link => ({ ...link, source: endpointId(link.source), target: endpointId(link.target) })),
    };
  }

  exportCsv() {
    return graphToCsv(this.exportData());
  }

  downloadJson(filename = `skill-vector-space-${timestamp()}.json`) {
    downloadBlob(filename, JSON.stringify(this.exportData(), null, 2), "application/json");
  }

  downloadCsv(filename = `skill-vector-space-${timestamp()}.csv`) {
    downloadBlob(filename, this.exportCsv(), "text/csv;charset=utf-8");
  }

  setStatus(text) {
    if (this.statusEl) this.statusEl.textContent = text;
  }

  destroy() {
    window.removeEventListener("resize", this.resizeHandler);
    this.graph?._destructor?.();
    this.container.innerHTML = "";
  }

  selectControl(key, options) {
    const select = el("select", "mbalaka-select");
    select.dataset.mbalakaControl = key;
    options.forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    });
    this.controls[key] = select;
    return select;
  }

  inputControl(key, placeholder) {
    const input = el("input", "mbalaka-input");
    input.type = "search";
    input.placeholder = placeholder;
    this.controls[key] = input;
    return input;
  }

  buttonControl(key, label) {
    const button = el("button", "mbalaka-button", label);
    button.type = "button";
    this.controls[key] = button;
    return button;
  }
}

export function normalizePayload(data = {}) {
  const nodes = Array.isArray(data.nodes) ? data.nodes : [];
  const edges = Array.isArray(data.edges) ? data.edges : Array.isArray(data.links) ? data.links : [];
  return withCounts({
    ...data,
    nodes: nodes.map(node => ({ ...node })),
    edges: edges.map(edge => ({ ...edge })),
    has_visual_data: data.has_visual_data ?? Boolean(nodes.length),
  });
}

export function graphToCsv(graph) {
  const rows = [[
    "record_type", "id", "source", "target", "label", "full_label", "group",
    "value", "similarity_percent", "skill", "skill_type", "sector",
    "source_type", "course_code", "course_name", "parent_label", "description",
  ]];
  graph.nodes.forEach(node => {
    rows.push([
      "node", node.id, "", "", node.label, node.full_label || "", node.group,
      "", "", node.skill || "", node.skill_type || "", node.sector || "",
      node.source_type || "", node.course_code || "", node.course_name || "",
      node.parent_label || "", node.description || "",
    ]);
  });
  graph.links.forEach(link => {
    rows.push([
      "link", "", endpointId(link.source), endpointId(link.target), link.label || "", link.title || "",
      link.group || "", link.value || "", link.similarity || "", "", "", "",
      "", "", "", "", "",
    ]);
  });
  return rows.map(row => row.map(csvValue).join(",")).join("\n");
}

function emptyPayload() {
  return withCounts({ has_visual_data: false, nodes: [], edges: [] });
}

function withCounts(data) {
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  return {
    ...data,
    counts: {
      nodes: nodes.length,
      edges: edges.length,
      roots: nodes.filter(node => node.group !== "skill").length,
      skills: nodes.filter(node => node.group === "skill").length,
      ...(data.counts || {}),
    },
  };
}

function resolveElement(value) {
  if (typeof value === "string") {
    const element = document.querySelector(value);
    if (!element) throw new Error(`Mbalaka container not found: ${value}`);
    return element;
  }
  return value;
}

function endpointId(endpoint) {
  return typeof endpoint === "object" && endpoint !== null ? endpoint.id : endpoint;
}

function linkKey(link) {
  return `${endpointId(link.source)}->${endpointId(link.target)}:${link.group || ""}`;
}

function positionLinkLabel(sprite, start, end) {
  if (!sprite) return;
  sprite.position.x = start.x + (end.x - start.x) * .5;
  sprite.position.y = start.y + (end.y - start.y) * .5;
  sprite.position.z = start.z + (end.z - start.z) * .5;
}

function uniqueOptions(values) {
  return [...new Set(values.filter(Boolean))].sort();
}

function labelSort(a, b) {
  return (a.full_label || a.label || "").localeCompare(b.full_label || b.label || "");
}

function el(tag, className, text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function legendSwatch(color, label) {
  return `<div class="mbalaka-legend-item"><span class="mbalaka-legend-swatch" style="background:${color};"></span>${escapeHtml(label)}</div>`;
}

function legendLine(color, label) {
  return `<div class="mbalaka-legend-item"><span class="mbalaka-legend-line" style="background:${color};"></span>${escapeHtml(label)}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function csvValue(value) {
  const text = value === null || value === undefined ? "" : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
}

function downloadBlob(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
