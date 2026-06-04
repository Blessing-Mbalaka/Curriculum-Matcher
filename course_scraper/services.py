import json
import logging
import re
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from time import sleep
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from courses.models import Course, Module

from .models import ScrapedCourseCandidate, ScrapeRun
from .school_urls import configured_schools

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36 CurriculumMatchCourseScraper/1.0"
PROGRAMME_TERMS = ("mba", "master", "business-administration", "programme", "programmes", "curriculum", "module")
MODULE_HINTS = ("module", "course", "curriculum", "core", "elective", "learning outcome", "credits", "nqf")
SITEMAP_NAMES = ("sitemap.xml", "sitemap_index.xml")
SITEMAP_RELEVANCE_TERMS = (
    "mba",
    "master",
    "business-administration",
    "programme",
    "programmes",
    "program",
    "programs",
    "course",
    "courses",
    "curriculum",
    "module",
)


@dataclass
class PageText:
    url: str
    title: str = ""
    headings: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    text: str = ""


@dataclass
class CrawlResult:
    pages: list[PageText] = field(default_factory=list)
    blocked_urls: list[str] = field(default_factory=list)
    skipped_urls: list[str] = field(default_factory=list)


class TextHTMLParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.skip_depth = 0
        self.current_tag = ""
        self.current_href = ""
        self.title_parts = []
        self.heading_parts = []
        self.text_parts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.current_tag = tag
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag == "a":
            attrs_dict = dict(attrs)
            self.current_href = attrs_dict.get("href", "")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if tag == "a":
            self.current_href = ""
        self.current_tag = ""

    def handle_data(self, data):
        if self.skip_depth:
            return
        clean = normalize_space(data)
        if not clean:
            return
        if self.current_tag == "title":
            self.title_parts.append(clean)
        if self.current_tag in {"h1", "h2", "h3", "h4"}:
            self.heading_parts.append(clean)
        if self.current_tag == "a" and self.current_href:
            absolute = urljoin(self.base_url, self.current_href)
            self.links.append(absolute)
        self.text_parts.append(clean)

    def page(self):
        return PageText(
            url=self.base_url,
            title=normalize_space(" ".join(self.title_parts)),
            headings=dedupe(self.heading_parts),
            links=dedupe(self.links),
            text=normalize_space("\n".join(self.text_parts)),
        )


def start_scrape_thread(run_id, school_indexes=None):
    thread = threading.Thread(target=run_course_scrape, args=(run_id, school_indexes), daemon=True)
    thread.start()


def run_course_scrape(run_id, school_indexes=None):
    run = ScrapeRun.objects.get(pk=run_id)
    run.status = "running"
    run.save(update_fields=["status"])
    schools = configured_schools()
    if school_indexes:
        schools = [schools[index] for index in school_indexes if 0 <= index < len(schools)]

    try:
        pages_seen = 0
        crawl_notes = []
        for school in schools:
            run.school_name = school["name"] if len(schools) == 1 else "South African business schools"
            run.save(update_fields=["school_name"])
            for seed_url in school.get("urls", []):
                run.seed_url = seed_url
                run.notes = f"Crawling {school['name']} from {seed_url}"
                run.save(update_fields=["seed_url", "notes"])
                crawl_result = crawl_programme_pages_with_diagnostics(
                    seed_url,
                    max_pages=getattr(settings, "COURSE_SCRAPER_MAX_PAGES", 8),
                )
                pages = crawl_result.pages
                pages_seen += len(pages)
                for page in pages:
                    course_candidate = extract_course_candidate(school, page)
                    if not course_candidate:
                        continue
                    verified_candidate = verify_with_gemini(course_candidate, page.text)
                    created_course, module_count = import_candidate(run, verified_candidate)
                    run.courses_created += 1 if created_course else 0
                    run.modules_created += module_count
                    run.save(update_fields=["courses_created", "modules_created"])
                if not pages:
                    note = no_pages_note(school["name"], seed_url, crawl_result)
                    crawl_notes.append(note)
                    run.notes = note
                    run.save(update_fields=["notes"])
                sleep(float(getattr(settings, "COURSE_SCRAPER_REQUEST_DELAY", 0.6)))
        run.status = "done"
        run.pages_seen = pages_seen
        run.finished_at = timezone.now()
        run.notes = completed_note(pages_seen, crawl_notes)
        run.save(update_fields=["status", "pages_seen", "finished_at", "notes"])
    except Exception as exc:
        logger.exception("Course scrape failed")
        run.status = "error"
        run.finished_at = timezone.now()
        run.notes = str(exc)
        run.save(update_fields=["status", "finished_at", "notes"])


def crawl_programme_pages(seed_url, max_pages=8):
    return crawl_programme_pages_with_diagnostics(seed_url, max_pages=max_pages).pages


def crawl_programme_pages_with_diagnostics(seed_url, max_pages=8):
    visited = set()
    result = CrawlResult()
    domain = urlparse(seed_url).netloc
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    sitemap_urls = discover_sitemap_page_urls(seed_url, session, max_urls=max_pages * 8)
    seed_clean_url = seed_url.split("#", 1)[0]
    queue = dedupe([seed_url, *sitemap_urls])

    while queue and len(result.pages) < max_pages:
        url = queue.pop(0)
        clean_url = url.split("#", 1)[0]
        if clean_url in visited:
            continue
        visited.add(clean_url)
        try:
            resp = session.get(clean_url, timeout=18)
            resp.raise_for_status()
        except requests.RequestException as exc:
            if is_blocked_response(exc):
                result.blocked_urls.append(clean_url)
            else:
                result.skipped_urls.append(clean_url)
            logger.info("Skipping %s: %s", clean_url, exc)
            continue
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            result.skipped_urls.append(clean_url)
            continue
        page = parse_html(clean_url, resp.text)
        if clean_url == seed_clean_url or is_relevant_page(page):
            result.pages.append(page)
        for link in page.links:
            parsed = urlparse(link)
            if parsed.netloc != domain:
                continue
            if link.split("#", 1)[0] in visited:
                continue
            if is_relevant_url(link):
                queue.append(link)
    return result


def is_blocked_response(exc):
    response = getattr(exc, "response", None)
    return response is not None and response.status_code in {401, 403, 429}


def no_pages_note(school_name, seed_url, crawl_result):
    if crawl_result.blocked_urls:
        sample = ", ".join(crawl_result.blocked_urls[:3])
        return (
            f"No accessible programme pages found for {school_name}. "
            f"The site blocked crawler access to {len(crawl_result.blocked_urls)} URL(s), including {sample}."
        )
    if crawl_result.skipped_urls:
        return (
            f"No accessible programme pages found for {school_name} at {seed_url}. "
            f"Skipped {len(crawl_result.skipped_urls)} URL(s) because they failed or were not HTML."
        )
    return (
        f"No accessible programme pages found for {school_name} at {seed_url}. "
        "The sitemap may not expose programme/course pages, or the seed URL may need tuning."
    )


def completed_note(pages_seen, crawl_notes):
    note = f"Completed. Crawled {pages_seen} pages."
    if pages_seen == 0 and crawl_notes:
        return f"{note} {crawl_notes[-1]}"
    return note


def discover_sitemap_page_urls(seed_url, session, max_urls=64):
    parsed_seed = urlparse(seed_url)
    domain = parsed_seed.netloc
    sitemap_urls = sitemap_locations(seed_url, session)
    seen_sitemaps = set()
    page_urls = []

    for sitemap_url in sitemap_urls:
        if len(page_urls) >= max_urls:
            break
        page_urls.extend(
            sitemap_page_urls(
                sitemap_url=sitemap_url,
                session=session,
                domain=domain,
                seen_sitemaps=seen_sitemaps,
                max_urls=max_urls - len(page_urls),
            )
        )
    return dedupe(page_urls)[:max_urls]


def sitemap_locations(seed_url, session):
    origin = site_origin(seed_url)
    locations = [urljoin(origin, name) for name in SITEMAP_NAMES]
    try:
        resp = session.get(urljoin(origin, "robots.txt"), timeout=10)
        if resp.ok:
            for line in resp.text.splitlines():
                key, sep, value = line.partition(":")
                if sep and key.strip().lower() == "sitemap":
                    locations.append(value.strip())
    except requests.RequestException as exc:
        logger.info("Could not read robots.txt for %s: %s", origin, exc)
    return dedupe(locations)


def sitemap_page_urls(sitemap_url, session, domain, seen_sitemaps, max_urls):
    if not sitemap_url or sitemap_url in seen_sitemaps or max_urls <= 0:
        return []
    seen_sitemaps.add(sitemap_url)
    try:
        resp = session.get(sitemap_url, timeout=18)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.info("Skipping sitemap %s: %s", sitemap_url, exc)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        logger.info("Could not parse sitemap %s: %s", sitemap_url, exc)
        return []

    output = []
    for loc in sitemap_locs(root):
        if len(output) >= max_urls:
            break
        parsed = urlparse(loc)
        if parsed.netloc != domain:
            continue
        if looks_like_sitemap_url(loc):
            output.extend(sitemap_page_urls(loc, session, domain, seen_sitemaps, max_urls - len(output)))
            continue
        if is_relevant_url(loc):
            output.append(loc)
    return output


def sitemap_locs(root):
    locs = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() == "loc" and node.text:
            locs.append(node.text.strip())
    return locs


def looks_like_sitemap_url(url):
    lower = url.lower()
    return "sitemap" in lower and (lower.endswith(".xml") or ".xml?" in lower)


def site_origin(url):
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def parse_html(url, html):
    parser = TextHTMLParser(url)
    parser.feed(html)
    return parser.page()


def extract_course_candidate(school, page):
    matched_alias = match_school_alias(page.text, school)
    if not matched_alias:
        return None
    if not looks_like_mba_page(page):
        return None

    course_name = infer_course_name(page, school)
    modules = extract_modules(page.text)
    if not modules:
        modules = [{"name": "Programme Overview", "content": trimmed(page.text, 4500)}]

    extractor = skill_extractor()
    skills = extractor.extract(" ".join([module["content"] for module in modules]))
    return {
        "school_name": school["name"],
        "matched_alias": matched_alias,
        "source_url": page.url,
        "course_name": course_name,
        "course_code": make_course_code(school["name"], course_name),
        "confidence": score_candidate(page, modules),
        "modules": modules,
        "skills": skills,
        "verified_by_gemini": False,
        "notes": "Heuristic extraction",
    }


def verify_with_gemini(candidate, source_text):
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        return candidate

    model = getattr(settings, "GEMINI_MODEL", "gemini-1.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    prompt = (
        "Verify this extracted South African business school MBA/course structure. "
        "Return only JSON with keys course_name, modules, confidence, notes. "
        "Each module must have name and content. Prefer real module names from the source text.\n\n"
        f"Candidate JSON:\n{json.dumps(candidate, ensure_ascii=True)[:8000]}\n\n"
        f"Source text:\n{source_text[:12000]}"
    )
    try:
        resp = requests.post(
            url,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        verified = extract_json_object(text)
    except Exception as exc:
        candidate["notes"] = f"Gemini verification skipped after error: {exc}"
        return candidate

    if isinstance(verified, dict):
        candidate["course_name"] = verified.get("course_name") or candidate["course_name"]
        candidate["modules"] = clean_modules(verified.get("modules")) or candidate["modules"]
        candidate["confidence"] = float(verified.get("confidence") or candidate["confidence"])
        candidate["notes"] = verified.get("notes") or "Verified by Gemini"
        candidate["verified_by_gemini"] = True
    return candidate


def import_candidate(run, candidate):
    code = candidate["course_code"]
    course, created = Course.objects.get_or_create(
        code=code,
        defaults={
            "name": candidate["course_name"],
            "university_name": candidate["school_name"],
            "country": "South Africa",
            "description": f"Imported from {candidate['school_name']} ({candidate['source_url']})",
        },
    )
    changed_fields = []
    if not course.university_name:
        course.university_name = candidate["school_name"]
        changed_fields.append("university_name")
    if not course.country:
        course.country = "South Africa"
        changed_fields.append("country")
    if not created and candidate["source_url"] not in course.description:
        course.description = f"{course.description}\nImported source: {candidate['source_url']}".strip()
        changed_fields.append("description")
    if changed_fields:
        course.save(update_fields=changed_fields)

    module_count = 0
    extractor = skill_extractor()
    for index, module in enumerate(candidate["modules"], start=1):
        name = module["name"][:255]
        content = module["content"].strip()
        if not content:
            continue
        try:
            skill_entities = extractor.extract_entities(content, document_id=f"module-{course.code}-{index}")
            obj, module_created = Module.objects.get_or_create(
                course=course,
                name=name,
                defaults={
                    "content": content,
                    "order": index,
                    "university_name": candidate["school_name"],
                    "country": "South Africa",
                    "skills_extracted": sorted({entity["skill"] for entity in skill_entities}),
                    "skill_entities": skill_entities,
                },
            )
        except IntegrityError:
            continue
        if not module_created and len(content) > len(obj.content):
            skill_entities = extractor.extract_entities(content, document_id=f"module-{obj.id}")
            obj.content = content
            obj.skills_extracted = sorted({entity["skill"] for entity in skill_entities})
            obj.skill_entities = skill_entities
            obj.save(update_fields=["content", "skills_extracted", "skill_entities"])
        module_count += 1 if module_created else 0

    ScrapedCourseCandidate.objects.create(
        run=run,
        school_name=candidate["school_name"],
        matched_alias=candidate["matched_alias"],
        source_url=candidate["source_url"],
        course_name=candidate["course_name"],
        course_code=code,
        confidence=candidate["confidence"],
        verified_by_gemini=candidate["verified_by_gemini"],
        imported_course_id=course.id,
        extracted_modules=candidate["modules"],
        extracted_skills=candidate["skills"],
        notes=candidate["notes"],
    )
    return created, module_count


def extract_modules(text):
    lines = [normalize_space(line) for line in re.split(r"[\n\r]+| \| ", text)]
    lines = [line for line in lines if line]
    modules = []
    seen = set()
    for index, line in enumerate(lines):
        if not is_module_line(line):
            continue
        name = clean_module_name(line)
        if not name or name.lower() in seen:
            continue
        content = collect_module_context(lines, index)
        modules.append({"name": name[:255], "content": content})
        seen.add(name.lower())
    return modules[:40]


def skill_extractor():
    from analysis.spacyskillextraction import SpacySkillExtractor

    return SpacySkillExtractor()


def is_module_line(line):
    lower = line.lower()
    if len(line) < 4 or len(line) > 160:
        return False
    if re.search(r"\b[A-Z]{2,}\d{2,}[A-Z]?\b", line):
        return True
    if any(term in lower for term in MODULE_HINTS) and not lower.startswith(("click", "apply", "download")):
        return True
    title_like = sum(1 for word in line.split() if word[:1].isupper())
    return 2 <= len(line.split()) <= 9 and title_like >= 2 and any(word in lower for word in ("management", "finance", "strategy", "leadership", "analytics", "marketing", "operations"))


def clean_module_name(line):
    line = re.sub(r"\s+", " ", line).strip(" -:|")
    line = re.sub(r"^(module|core|elective|course)\s*[:\-]?\s*", "", line, flags=re.I)
    return line


def collect_module_context(lines, start):
    chunk = [lines[start]]
    for line in lines[start + 1:start + 5]:
        if is_module_line(line):
            break
        if 20 <= len(line) <= 700:
            chunk.append(line)
    return "\n".join(chunk)


def match_school_alias(text, school):
    haystack = normalize_space(text).lower()
    names = [school["name"], *school.get("aliases", [])]
    for name in names:
        if normalize_space(name).lower() in haystack:
            return name
    return ""


def looks_like_mba_page(page):
    text = f"{page.title} {' '.join(page.headings)} {page.text}".lower()
    return "mba" in text or "master of business administration" in text


def infer_course_name(page, school):
    for value in [page.title, *page.headings]:
        if "mba" in value.lower() or "master of business administration" in value.lower():
            return normalize_space(value)[:255]
    return f"{school['name']} MBA"


def score_candidate(page, modules):
    score = 0.35
    text = page.text.lower()
    if "mba" in text:
        score += 0.2
    if "curriculum" in text or "module" in text:
        score += 0.2
    score += min(0.25, len(modules) * 0.025)
    return round(min(score, 0.98), 2)


def make_course_code(school_name, course_name):
    base = "".join(word[0] for word in re.findall(r"[A-Za-z]+", school_name)[:5]).upper()
    suffix = "MBA" if "mba" in course_name.lower() or "business administration" in course_name.lower() else "COURSE"
    return f"{base}-{suffix}"[:50]


def is_relevant_page(page):
    text = f"{page.url} {page.title} {' '.join(page.headings)} {page.text}".lower()
    return any(term in text for term in PROGRAMME_TERMS)


def is_relevant_url(url):
    return any(term in url.lower() for term in SITEMAP_RELEVANCE_TERMS)


def clean_modules(value):
    if not isinstance(value, list):
        return []
    modules = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = normalize_space(str(item.get("name") or ""))
        content = normalize_space(str(item.get("content") or ""))
        if name and content:
            modules.append({"name": name[:255], "content": content})
    return modules


def extract_json_object(text):
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if not match:
        return None
    return json.loads(match.group(0))


def normalize_space(value):
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def trimmed(value, limit):
    value = normalize_space(value)
    return value[:limit]


def dedupe(values: Iterable[str]):
    seen = set()
    output = []
    for value in values:
        clean = normalize_space(value)
        key = clean.lower()
        if clean and key not in seen:
            output.append(clean)
            seen.add(key)
    return output
