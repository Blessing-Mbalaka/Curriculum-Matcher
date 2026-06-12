import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

from django.conf import settings
from django.utils import timezone

from courses.models import Course, Module
from jobs.models import JobAdvert
from analysis.models import AnalysisRun, GapResult, SkillMatrix


KNOWLEDGE_CACHE_DIR = Path(settings.BASE_DIR) / "memory" / "md_cache"
KNOWLEDGE_INDEX_PATH = KNOWLEDGE_CACHE_DIR / "knowledge_index.md"
ANSWER_CACHE_DIR = KNOWLEDGE_CACHE_DIR / "answers"
RAG_SCOPES = {"all", "jobs", "courses"}
ANSWER_CACHE_VERSION = "v2"

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "based", "by", "for", "from",
    "have", "how", "i", "in", "is", "it", "of", "on", "or", "our", "show",
    "that", "the", "there", "this", "to", "us", "using", "what", "which",
    "with", "you",
}


def answer_schema():
    return {
        "answer": "",
        "confidence": "low",
        "citations": [],
        "chart": {
            "type": "bar",
            "title": "Retrieved evidence",
            "labels": [],
            "datasets": [],
        },
        "table": {
            "columns": [],
            "rows": [],
        },
        "limitations": [],
        "cache": {
            "hit": False,
            "scope": "all",
            "knowledge_index": str(KNOWLEDGE_INDEX_PATH),
            "answer_path": "",
        },
    }


def ask_rag_question(question, refresh=False, scope="all"):
    scope = normalise_scope(scope)
    question = _compact_text(question, 900)
    if not question:
        response = answer_schema()
        response["answer"] = "Ask a question about the selected stored knowledge scope."
        response["limitations"] = ["No question was provided."]
        response["cache"]["scope"] = scope
        return response

    ANSWER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    knowledge = load_or_build_knowledge_index(refresh=refresh)
    scoped_chunks = scoped_knowledge_chunks(knowledge["chunks"], scope)
    scoped_fingerprint = f"{ANSWER_CACHE_VERSION}:{knowledge['fingerprint']}:{scope}:{len(scoped_chunks)}"
    answer_path = ANSWER_CACHE_DIR / f"{scope}-{_cache_key(question, scoped_fingerprint)}.md"
    if answer_path.exists() and not refresh:
        cached = _read_cached_answer(answer_path)
        if cached:
            cached["cache"] = {
                "hit": True,
                "scope": scope,
                "knowledge_index": str(KNOWLEDGE_INDEX_PATH),
                "answer_path": str(answer_path),
            }
            return cached

    chunks = retrieve_chunks(question, scoped_chunks)
    response = _ask_tinyllama(question, chunks, scope=scope)
    if not response or _looks_like_schema_stub(response):
        response = _extractive_answer(question, chunks)

    response = _normalise_response(response, chunks)
    response["cache"] = {
        "hit": False,
        "scope": scope,
        "knowledge_index": str(KNOWLEDGE_INDEX_PATH),
        "answer_path": str(answer_path),
    }
    _write_cached_answer(answer_path, question, response)
    return response


def load_or_build_knowledge_index(refresh=False):
    KNOWLEDGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fingerprint = _source_fingerprint()
    if KNOWLEDGE_INDEX_PATH.exists() and not refresh:
        cached = _parse_knowledge_index(KNOWLEDGE_INDEX_PATH)
        if cached and cached["fingerprint"] == fingerprint:
            return cached

    chunks = _build_knowledge_chunks()
    text = _render_knowledge_index(fingerprint, chunks)
    KNOWLEDGE_INDEX_PATH.write_text(text, encoding="utf-8")
    return {"fingerprint": fingerprint, "chunks": chunks}


def normalise_scope(scope):
    value = str(scope or "all").strip().lower()
    return value if value in RAG_SCOPES else "all"


def scoped_knowledge_chunks(chunks, scope):
    scope = normalise_scope(scope)
    if scope == "all":
        return chunks
    allowed_sources = {"jobs": {"job"}, "courses": {"course"}}[scope]
    filtered = [chunk for chunk in chunks if chunk.get("source") in allowed_sources]
    if filtered:
        return filtered
    return [_chunk(
        f"empty-{scope}",
        f"Empty {scope.title()} Knowledge",
        f"No {scope} knowledge is currently available in the Markdown cache.",
        scope[:-1] if scope.endswith("s") else scope,
    )]


def retrieve_chunks(question, chunks, limit=8):
    query_terms = Counter(_tokenize(question))
    if not query_terms:
        return chunks[:limit]
    scored = []
    for chunk in chunks:
        terms = Counter(_tokenize(chunk["text"]))
        overlap = sum(min(count, terms.get(term, 0)) for term, count in query_terms.items())
        title_bonus = sum(1 for term in query_terms if term in _tokenize(chunk["title"]))
        score = overlap + (title_bonus * 1.5)
        if score:
            scored.append((score, chunk))
    if not scored:
        return chunks[:limit]
    return [chunk for _score, chunk in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]


def _build_knowledge_chunks():
    chunks = []
    latest_run = AnalysisRun.objects.order_by("-created_at").first()
    if latest_run:
        results = GapResult.objects.filter(run=latest_run).select_related("course", "job")
        scores = [result.similarity_score for result in results[:2000]]
        if scores:
            chunks.append(_chunk(
                "analysis-summary",
                "Latest Analysis Summary",
                "\n".join([
                    f"Run: {latest_run.name}",
                    f"Status: {latest_run.status}",
                    f"Results: {len(scores)}",
                    f"Average alignment score: {round((sum(scores) / len(scores)) * 100, 1)}%",
                    f"Highest alignment score: {round(max(scores) * 100, 1)}%",
                    f"Lowest alignment score: {round(min(scores) * 100, 1)}%",
                ]),
                "comparison",
            ))
            for result in results.order_by("-similarity_score")[:80]:
                chunks.append(_chunk(
                    f"gap-{result.id}",
                    f"{result.course.code or result.course.name} to {result.job.title}",
                    "\n".join([
                        f"Course: {result.course.name}",
                        f"Job: {result.job.title}",
                        f"Company: {result.job.company or 'Unknown'}",
                        f"Alignment score: {result.similarity_percent}%",
                        f"Matched skills: {', '.join(result.matched_skills or []) or 'None recorded'}",
                        f"Missing skills: {', '.join(result.missing_skills or []) or 'None recorded'}",
                    ]),
                    "comparison",
                ))

    for source in ("jobs", "courses"):
        rows = SkillMatrix.objects.filter(source=source).order_by("-frequency")[:60]
        if rows:
            chunks.append(_chunk(
                f"skill-matrix-{source}",
                f"Top {source.title()} Skills",
                "\n".join(f"{row.skill}: {row.frequency}" for row in rows),
                "job" if source == "jobs" else "course",
            ))

    for course in Course.objects.prefetch_related("modules").order_by("code", "name")[:120]:
        module_names = [module.name for module in course.modules.all()[:20]]
        skills = sorted({skill for module in course.modules.all() for skill in (module.skills_extracted or [])})[:50]
        chunks.append(_chunk(
            f"course-{course.id}",
            f"Course {course.code}: {course.name}",
            "\n".join([
                f"Course: {course.name}",
                f"Code: {course.code}",
                f"University: {course.university_name or 'Unknown'}",
                f"Description: {_compact_text(course.description, 500) or 'No description recorded'}",
                f"Modules: {', '.join(module_names) or 'None recorded'}",
                f"Extracted skills: {', '.join(skills) or 'None recorded'}",
            ]),
            "course",
        ))

    for module in Module.objects.select_related("course").order_by("course__code", "order", "name")[:180]:
        chunks.append(_chunk(
            f"module-{module.id}",
            f"Module {module.course.code}: {module.name}",
            "\n".join([
                f"Course: {module.course.name}",
                f"Module: {module.name}",
                f"Content: {_compact_text(module.content, 900)}",
                f"Extracted skills: {', '.join(module.skills_extracted or []) or 'None recorded'}",
            ]),
            "course",
        ))

    for job in JobAdvert.objects.order_by("-created_at")[:220]:
        chunks.append(_chunk(
            f"job-{job.id}",
            f"Job {job.title}",
            "\n".join([
                f"Title: {job.title}",
                f"Company: {job.company or 'Unknown'}",
                f"Location: {job.location or 'Unknown'}",
                f"Category: {job.category or 'Unknown'}",
                f"Date posted: {job.date_posted.isoformat() if job.date_posted else 'Unknown'}",
                f"Description: {_compact_text(job.analysis_text(), 1000)}",
                f"Extracted skills: {', '.join(_job_skill_names(job)[:50]) or 'None recorded'}",
            ]),
            "job",
        ))

    if not chunks:
        chunks.append(_chunk(
            "empty-knowledge",
            "Empty Knowledge Base",
            "No courses, modules, jobs, skill matrices, or gap results are currently stored.",
            "all",
        ))
    return chunks


def _ask_tinyllama(question, chunks, scope="all"):
    endpoint = getattr(settings, "TINYLLAMA_ENDPOINT", "http://127.0.0.1:11434/api/generate")
    model = getattr(settings, "TINYLLAMA_MODEL", "tinyllama")
    timeout = getattr(settings, "TINYLLAMA_TIMEOUT_SECONDS", 45)
    prompt = _tinyllama_prompt(question, chunks, scope=scope)
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "top_p": 0.8},
    }).encode("utf-8")
    req = urlrequest.Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (OSError, URLError, HTTPError, TimeoutError, json.JSONDecodeError):
        return None
    content = raw.get("response") if isinstance(raw, dict) else ""
    if not content:
        return None
    try:
        return json.loads(_extract_json_text(content))
    except json.JSONDecodeError:
        return None


def _tinyllama_prompt(question, chunks, scope="all"):
    evidence = "\n\n".join(
        f"[{chunk['id']}] {chunk['title']}\n{chunk['text']}"
        for chunk in chunks
    )
    return f"""
You are CurriculumMatch RAG. The active knowledge scope is "{scope}".
Answer only from the evidence below. If the evidence is insufficient, say so.
Write a useful answer, not just a label or one-line summary. Explain the finding, name the relevant jobs/courses/skills,
and include practical interpretation grounded in the evidence. Keep it concise enough to scan, but complete enough
for a curriculum or job-market analyst to act on.
Return valid JSON only using this schema:
{{
  "answer": "grounded answer with the main finding, supporting evidence, and interpretation",
  "confidence": "low|medium|high",
  "citations": [{{"id": "chunk id", "title": "chunk title", "quote": "short evidence phrase"}}],
  "chart": {{"type": "bar|doughnut|line", "title": "chart title", "labels": ["label"], "datasets": [{{"label": "Metric", "data": [1]}}]}},
  "table": {{"columns": ["Column"], "rows": [["Value"]]}},
  "limitations": ["what the evidence cannot prove"]
}}

Question: {question}

Evidence:
{evidence}
""".strip()


def _extractive_answer(question, chunks):
    highlights = _evidence_highlights(question, chunks)
    skill_sections = _skill_count_sections(chunks)
    citations = [
        {"id": chunk["id"], "title": chunk["title"], "quote": _compact_text(chunk["text"], 180)}
        for chunk in chunks[:5]
    ]
    labels = [chunk["title"][:36] for chunk in chunks[:6]]
    data = [max(1, len(_tokenize(chunk["text"]))) for chunk in chunks[:6]]
    answer_lines = [
        "Based on the retrieved knowledge, here is the grounded answer.",
        "",
        "Main findings:",
    ]
    if skill_sections:
        for section_title, rows in skill_sections:
            answer_lines.append(f"- {section_title}: " + ", ".join(f"{skill} ({count})" for skill, count in rows[:8]))
        if "Top Jobs Skills" in {title for title, _rows in skill_sections} and "Top Courses Skills" in {title for title, _rows in skill_sections}:
            answer_lines.append(
                "- Curriculum reading: compare the high-demand job skills against the course skills. Skills that appear strongly in jobs but weakly in courses are priority gap candidates."
            )
    elif highlights:
        answer_lines.extend(f"- {item}" for item in highlights[:6])
    else:
        answer_lines.append("- The retrieved records are relevant, but they do not contain a clear direct answer to the question.")
    answer_lines.extend([
        "",
        "Evidence trail:",
        "The citations and table below show exactly which cached records were used. The chart is generated from the retrieved evidence so the answer remains tied to stored knowledge.",
    ])
    return {
        "answer": "\n".join(answer_lines),
        "confidence": "low",
        "citations": citations,
        "chart": {
            "type": "bar",
            "title": "Retrieved evidence size",
            "labels": labels,
            "datasets": [{"label": "Evidence tokens", "data": data}],
        },
        "table": {
            "columns": ["Evidence", "Source"],
            "rows": [[item["quote"], item["title"]] for item in citations],
        },
        "limitations": ["This answer is limited to the retrieved cached knowledge and does not use external search."],
    }


def _looks_like_schema_stub(response):
    if not isinstance(response, dict):
        return True
    answer = str(response.get("answer") or "").strip().lower()
    stub_phrases = {
        "grounded answer with the main finding, supporting evidence, and interpretation",
        "short grounded answer",
        "answer",
        "main finding",
    }
    if answer in stub_phrases:
        return True
    if "supporting evidence" in answer and "interpretation" in answer and len(answer) < 120:
        return True
    citations = response.get("citations") or []
    if len(answer) < 80 and not citations:
        return True
    return False


def _normalise_response(response, chunks):
    schema = answer_schema()
    if isinstance(response, dict):
        schema.update({key: response.get(key, schema[key]) for key in schema if key in response})
    schema["answer"] = _compact_text(schema.get("answer"), 6000) or "No grounded answer could be produced from the retrieved knowledge."
    schema["confidence"] = schema.get("confidence") if schema.get("confidence") in {"low", "medium", "high"} else "low"
    valid_ids = {chunk["id"]: chunk for chunk in chunks}
    citations = []
    for citation in schema.get("citations") or []:
        if not isinstance(citation, dict) or citation.get("id") not in valid_ids:
            continue
        source = valid_ids[citation["id"]]
        citations.append({
            "id": source["id"],
            "title": citation.get("title") or source["title"],
            "quote": _compact_text(citation.get("quote") or source["text"], 220),
        })
    if not citations:
        citations = [
            {"id": chunk["id"], "title": chunk["title"], "quote": _compact_text(chunk["text"], 180)}
            for chunk in chunks[:4]
        ]
    schema["citations"] = citations[:6]
    schema["chart"] = _normalise_chart(schema.get("chart"), chunks)
    schema["table"] = _normalise_table(schema.get("table"), citations)
    schema["limitations"] = [
        _compact_text(item, 220) for item in (schema.get("limitations") or [])
        if _compact_text(item, 220)
    ][:5]
    return schema


def _normalise_chart(chart, chunks):
    if not isinstance(chart, dict):
        chart = {}
    labels = chart.get("labels") if isinstance(chart.get("labels"), list) else []
    datasets = chart.get("datasets") if isinstance(chart.get("datasets"), list) else []
    clean_datasets = []
    for dataset in datasets[:3]:
        if not isinstance(dataset, dict):
            continue
        values = []
        for value in dataset.get("data", [])[:12]:
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                values.append(0)
        clean_datasets.append({"label": str(dataset.get("label") or "Value")[:80], "data": values})
    if not labels or not clean_datasets:
        labels = [chunk["title"][:36] for chunk in chunks[:6]]
        clean_datasets = [{"label": "Evidence tokens", "data": [len(_tokenize(chunk["text"])) for chunk in chunks[:6]]}]
    return {
        "type": chart.get("type") if chart.get("type") in {"bar", "doughnut", "line"} else "bar",
        "title": str(chart.get("title") or "Knowledge evidence")[:120],
        "labels": [str(label)[:80] for label in labels[:12]],
        "datasets": clean_datasets,
    }


def _normalise_table(table, citations):
    if not isinstance(table, dict):
        table = {}
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    clean_rows = []
    for row in rows[:12]:
        if isinstance(row, list):
            clean_rows.append([_compact_text(cell, 180) for cell in row[:6]])
    if not columns or not clean_rows:
        columns = ["Source", "Evidence"]
        clean_rows = [[item["title"], item["quote"]] for item in citations[:6]]
    return {
        "columns": [str(column)[:80] for column in columns[:6]],
        "rows": clean_rows,
    }


def _evidence_highlights(question, chunks):
    query_terms = set(_tokenize(question))
    highlights = []
    for chunk in chunks:
        lines = [
            line.strip(" -")
            for line in re.split(r"[\n.;]", chunk["text"])
            if line.strip()
        ]
        scored = []
        for line in lines:
            terms = set(_tokenize(line))
            score = len(query_terms & terms)
            if score:
                scored.append((score, line))
        if scored:
            best = sorted(scored, key=lambda item: item[0], reverse=True)[0][1]
        else:
            best = lines[0] if lines else chunk["text"]
        highlights.append(f"{chunk['title']}: {_compact_text(best, 260)}")
    return highlights


def _skill_count_sections(chunks):
    sections = []
    for chunk in chunks:
        if not chunk["title"].lower().startswith("top "):
            continue
        counter = Counter()
        for skill, count in re.findall(r"([A-Za-z][A-Za-z0-9+#/&(). -]{0,80}?):\s*(\d+)", chunk["text"]):
            clean_skill = re.sub(r"^\d+\s+", "", _compact_text(skill, 80)).strip()
            if clean_skill:
                counter[clean_skill] = max(counter[clean_skill], int(count))
        if counter:
            sections.append((chunk["title"], counter.most_common(12)))
    return sections


def _render_knowledge_index(fingerprint, chunks):
    lines = [
        "# CurriculumMatch RAG Knowledge Index",
        "",
        f"Fingerprint: {fingerprint}",
        f"Generated at: {timezone.now().isoformat()}",
        f"Chunks: {len(chunks)}",
        "",
    ]
    for chunk in chunks:
        lines.extend([
            f"## {chunk['id']}",
            f"Title: {chunk['title']}",
            f"Source: {chunk.get('source', 'all')}",
            "",
            chunk["text"],
            "",
        ])
    return "\n".join(lines).strip() + "\n"


def _parse_knowledge_index(path):
    text = path.read_text(encoding="utf-8")
    fingerprint_match = re.search(r"^Fingerprint:\s*(.+)$", text, flags=re.MULTILINE)
    if not fingerprint_match:
        return None
    chunks = []
    for match in re.finditer(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE):
        start = match.end()
        next_match = re.search(r"^##\s+", text[start:], flags=re.MULTILINE)
        end = start + next_match.start() if next_match else len(text)
        body = text[start:end].strip()
        title_match = re.search(r"^Title:\s*(.+)$", body, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else match.group(1).strip()
        source_match = re.search(r"^Source:\s*(.+)$", body, flags=re.MULTILINE)
        source = source_match.group(1).strip() if source_match else _infer_chunk_source(match.group(1).strip())
        body_text = re.sub(r"^Title:\s*.+\n?", "", body, count=1, flags=re.MULTILINE).strip()
        body_text = re.sub(r"^Source:\s*.+\n?", "", body_text, count=1, flags=re.MULTILINE).strip()
        chunks.append(_chunk(match.group(1).strip(), title, body_text, source))
    return {"fingerprint": fingerprint_match.group(1).strip(), "chunks": chunks}


def _write_cached_answer(path, question, response):
    path.write_text(
        "\n".join([
            "# Cached RAG Answer",
            "",
            f"Question: {question}",
            "",
            "```json",
            json.dumps(response, indent=2),
            "```",
            "",
        ]),
        encoding="utf-8",
    )


def _read_cached_answer(path):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _source_fingerprint():
    parts = []
    for model in (Course, Module, JobAdvert, AnalysisRun, GapResult, SkillMatrix):
        latest = model.objects.order_by("-id").values_list("id", flat=True).first() or 0
        parts.append(f"{model.__name__}:{model.objects.count()}:{latest}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _cache_key(question, fingerprint):
    normalized = re.sub(r"\s+", " ", question.lower()).strip()
    return hashlib.sha256(f"{fingerprint}|{normalized}".encode("utf-8")).hexdigest()[:24]


def _chunk(chunk_id, title, text, source="all"):
    return {
        "id": str(chunk_id),
        "title": _compact_text(title, 140),
        "text": _compact_text(text, 1800),
        "source": source,
    }


def _infer_chunk_source(chunk_id):
    if chunk_id.startswith("job-") or chunk_id == "skill-matrix-jobs":
        return "job"
    if chunk_id.startswith("course-") or chunk_id.startswith("module-") or chunk_id == "skill-matrix-courses":
        return "course"
    if chunk_id.startswith("gap-") or chunk_id == "analysis-summary":
        return "comparison"
    return "all"


def _job_skill_names(job):
    names = []
    seen = set()
    for raw in job.skill_entities or job.skills_extracted or []:
        name = raw.get("skill") or raw.get("text") if isinstance(raw, dict) else str(raw or "")
        name = _compact_text(name, 80)
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def _extract_json_text(value):
    text = value.strip()
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        return text[first:last + 1]
    return text


def _tokenize(value):
    return [
        token for token in re.findall(r"[a-z0-9+#]+", str(value or "").lower())
        if len(token) > 1 and token not in STOP_WORDS
    ]


def _compact_text(value, max_chars):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].strip() + "..."
