# Mbalaka

Mbalaka is a reusable 3D semantic skill vector-space visualization. It renders job roots, course roots, extracted skill nodes, evidence strands, and similarity strands from a JSON payload.

It was extracted from the CurriculumMatch skill vector-space dashboard and packaged as the npm module `mbalaka`.

## Install

```bash
npm install mbalaka three
```

Mbalaka depends on `3d-force-graph` and `three-spritetext`, and uses `three` as a peer dependency.

## Quick Start

```js
import { createMbalakaVectorSpace } from "mbalaka";
import "mbalaka/styles.css";

createMbalakaVectorSpace({
  container: "#skill-vector",
  apiUrl: "/api/data-export/vector-space/",
});
```

```html
<div id="skill-vector" style="height: 100vh;"></div>
```

## Static Data

You can pass data directly instead of using `apiUrl`.

```js
import { createMbalakaVectorSpace } from "mbalaka";
import "mbalaka/styles.css";

createMbalakaVectorSpace({
  container: document.getElementById("skill-vector"),
  data: {
    has_visual_data: true,
    nodes: [
      { id: "job-1", label: "Strategy Manager", group: "job-root", sector: "Strategy" },
      { id: "course-1", label: "MBA Strategy", group: "course-root", course_code: "MBA101" },
      { id: "skill-strategy", label: "strategy", full_label: "strategy", group: "skill", skill_type: "business" }
    ],
    edges: [
      { source: "job-1", target: "skill-strategy", group: "evidence", value: 1 },
      { source: "course-1", target: "skill-strategy", group: "evidence", value: 1 }
    ]
  }
});
```

## Payload Shape

Mbalaka accepts either `edges` or `links`. Nodes and edges are copied before rendering.

### Node Fields

Required:

```js
{
  id: "skill-data-analysis",
  label: "data analysis",
  group: "skill" // "skill", "job-root", or "course-root"
}
```

Common optional fields:

```js
{
  full_label: "Data Analysis",
  skill: "data analysis",
  skill_type: "technical", // "technical", "soft", "business", "domain"
  sector: "Technology and Data",
  source: "bert-ner",
  confidence: 0.91,
  course_code: "MBA101",
  course_name: "MBA Strategy",
  parent_label: "Business School",
  description: "Context shown in node panel"
}
```

### Edge Fields

```js
{
  source: "job-1",
  target: "skill-data-analysis",
  group: "evidence", // "evidence" or "similarity"
  value: 1,
  label: "87% cosine",
  similarity: 87,
  title: "Cosine similarity: 87%"
}
```

## Configuration

```js
const viewer = createMbalakaVectorSpace({
  container: "#skill-vector",
  apiUrl: "/api/data-export/vector-space/",
  autoLoad: true,
  controls: true,
  legend: true,
  panel: true,
  className: "my-vector-view",
  fetchOptions: {
    credentials: "same-origin"
  },
  labels: {
    title: "My Skill Graph",
    loading: "Loading graph...",
    emptyTitle: "No graph data",
    emptyText: "Try another filter."
  },
  colors: {
    jobRoot: "#f58220",
    courseRoot: "#7bd88f",
    technical: "#2f80ed",
    soft: "#39a96b",
    business: "#ffb703",
    domain: "#9aa0a6",
    selected: "#ffffff",
    evidenceLink: "rgba(123,216,143,.32)",
    similarityLink: "rgba(245,130,32,.74)"
  },
  graphOptions: {
    chargeStrength: -95
  }
});
```

## Instance API

```js
viewer.load("/api/data-export/vector-space/");
viewer.setData(payload);
viewer.applyFilters();
viewer.clearFilters();
viewer.fit();
viewer.togglePause();
viewer.exportData();
viewer.exportCsv();
viewer.downloadJson();
viewer.downloadCsv();
viewer.destroy();
```

## Django Integration

The original CurriculumMatch endpoint already returns the correct shape:

```python
path("api/data-export/vector-space/", skill_vector_space, name="skill-vector-space-api")
```

Use it from a Django template:

```html
<div id="skill-vector" style="height: calc(100vh - 60px);"></div>
<script type="module">
  import { createMbalakaVectorSpace } from "/static/vendor/mbalaka/index.js";
  import "/static/vendor/mbalaka/styles.css";

  createMbalakaVectorSpace({
    container: "#skill-vector",
    apiUrl: "{% url 'skill-vector-space-api' %}"
  });
</script>
```

## Development

```bash
npm run build
npm pack
```

The package is intentionally light: the build step copies `src` into `dist` so the published artifact stays easy to inspect.
