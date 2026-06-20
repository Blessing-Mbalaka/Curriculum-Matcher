import hashlib

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views import View
from django.views.generic import ListView

from analysis.spacyskillextraction import classify_skill_text
from .models import Course, Module
from .forms import CourseForm, ModuleForm
from .file_parsing import parse_uploaded_files
from .skill_extraction import (
    ensure_module_skills,
    enhance_module_skills,
    extract_module_skills,
    parse_skill_enhancements,
    remove_module_skill,
)


class CourseListView(ListView):
    model = Course
    template_name = "courses/list.html"
    context_object_name = "courses"

    def get_queryset(self):
        queryset = Course.objects.prefetch_related("modules").all()
        university = self.request.GET.get("university", "").strip()
        country = self.request.GET.get("country", "").strip()
        if university:
            queryset = queryset.filter(university_name=university)
        if country:
            queryset = queryset.filter(country=country)
        courses = list(queryset.distinct())
        for course in courses:
            for module in course.modules.all():
                ensure_module_skills(module)
        return courses

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["selected_university"] = self.request.GET.get("university", "").strip()
        ctx["selected_country"] = self.request.GET.get("country", "").strip()
        ctx["universities"] = (
            Course.objects
            .exclude(university_name="")
            .order_by("university_name")
            .values_list("university_name", flat=True)
            .distinct()
        )
        ctx["countries"] = (
            Course.objects
            .exclude(country="")
            .order_by("country")
            .values_list("country", flat=True)
            .distinct()
        )
        return ctx


class CourseCreateView(View):
    def get(self, request):
        return render(request, "courses/form.html", {
            "form": CourseForm(),
            "title": "New Course",
            "action": "Create Course",
        })

    def post(self, request):
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save()
            messages.success(request, f"Course '{course.name}' created.")
            return redirect("course-detail", pk=course.pk)
        return render(request, "courses/form.html", {
            "form": form, "title": "New Course", "action": "Create Course",
        })


class CourseDetailView(View):
    def get(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        modules = list(course.modules.all())
        for module in modules:
            ensure_module_skills(module)
        return render(request, "courses/detail.html", {
            "course": course,
            "modules": modules,
            "module_form": ModuleForm(course=course),
        })


class CourseSkillAuditView(View):
    def get(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        modules = list(course.modules.all())
        rows = []
        unique_skills = set()
        for module in modules:
            ensure_module_skills(module)
            entities_by_skill = {
                str(entity.get("skill") or "").strip().lower(): entity
                for entity in (module.skill_entities or [])
                if entity.get("skill")
            }
            for skill in module.skills_extracted or []:
                key = str(skill or "").strip().lower()
                if not key:
                    continue
                entity = entities_by_skill.get(key, {})
                unique_skills.add(key)
                rows.append({
                    "module": module,
                    "skill": skill,
                    "entity_id": entity.get("id", ""),
                    "chunk_id": entity.get("chunk_id", ""),
                    "label": entity.get("label", "SKILL"),
                    "source": entity.get("source", ""),
                    "confidence": entity.get("confidence", ""),
                    "skill_type": entity.get("skill_type", ""),
                    "tier": entity.get("tier", ""),
                    "label_status": entity.get("label_status", ""),
                    "text": entity.get("text", ""),
                    "context": skill_evidence_context(module.content, entity, skill),
                })
        rows.sort(key=lambda item: (item["skill"], item["module"].name))
        return render(request, "courses/skill_audit.html", {
            "course": course,
            "rows": rows,
            "unique_skill_count": len(unique_skills),
            "module_skill_count": len(rows),
        })

    def post(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        module = get_object_or_404(Module, pk=request.POST.get("module_id"), course=course)
        original_skill = normalize_audit_skill(request.POST.get("original_skill", ""))
        reviewed_skill = normalize_audit_skill(request.POST.get("skill", ""))
        skill_type = request.POST.get("skill_type", "").strip() or "domain"
        tier = request.POST.get("tier", "").strip() or "reviewed"
        label = request.POST.get("label", "").strip() or "SKILL"

        if not original_skill or not reviewed_skill:
            messages.error(request, "Could not update the skill row because the skill value was blank.")
            return redirect("course-skill-audit", pk=course.pk)

        entities = list(module.skill_entities or [])
        entity = find_module_skill_entity(
            entities,
            original_skill=original_skill,
            entity_id=request.POST.get("entity_id", ""),
            chunk_id=request.POST.get("chunk_id", ""),
        )
        if entity is None:
            entity = build_reviewed_skill_entity(module, reviewed_skill)
            entities.append(entity)

        entity.update({
            "skill": reviewed_skill,
            "label": label,
            "tier": tier,
            "skill_type": skill_type,
            "label_status": "reviewed",
        })

        next_skills = [
            normalize_audit_skill(skill)
            for skill in (module.skills_extracted or [])
            if normalize_audit_skill(skill) != original_skill
        ]
        next_skills.append(reviewed_skill)
        module.skill_entities = sorted(
            [entity for entity in entities if isinstance(entity, dict) and entity.get("skill")],
            key=lambda item: normalize_audit_skill(item.get("skill")),
        )
        module.skills_extracted = sorted({skill for skill in next_skills if skill})
        module.save(update_fields=["skill_entities", "skills_extracted"])
        messages.success(request, f"Reviewed '{reviewed_skill}' as {skill_type}.")
        return redirect("course-skill-audit", pk=course.pk)


class CourseEditView(View):
    def get(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        return render(request, "courses/form.html", {
            "form": CourseForm(instance=course),
            "title": f"Edit {course.name}",
            "action": "Save Changes",
            "course": course,
        })

    def post(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            form.save()
            messages.success(request, "Course updated.")
            return redirect("course-detail", pk=pk)
        return render(request, "courses/form.html", {
            "form": form, "title": f"Edit {course.name}", "action": "Save Changes",
        })


class CourseDeleteView(View):
    def post(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        name = course.name
        course.delete()
        messages.success(request, f"Course '{name}' deleted.")
        return redirect("course-list")


# ---- Modules ----

class ModuleCreateView(View):
    def post(self, request, course_pk):
        course = get_object_or_404(Course, pk=course_pk)
        form = ModuleForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                module = form.save(commit=False)
                module.course = course
                module.university_name = module.university_name or course.university_name
                module.country = module.country or course.country
                parsed_upload = parse_uploaded_files(form.cleaned_data.get("content_files") or [])
                module.content = merge_module_content(
                    form.cleaned_data.get("content"),
                    parsed_upload.text,
                )
                if not module.content.strip():
                    raise ValidationError("No usable text was found in the uploaded files.")
                module.save()
                extract_module_skills(module)
                module.save(update_fields=["skill_entities", "skills_extracted"])
                messages.success(request, f"Module '{module.name}' added.")
                add_ignored_file_message(request, parsed_upload)
            except ValidationError as exc:
                form.add_error("content_files", exc)
                messages.error(request, "Please fix the errors below.")
                return render(request, "courses/detail.html", {
                    "course": course,
                    "modules": course.modules.all(),
                    "module_form": form,
                })
        else:
            messages.error(request, "Please fix the errors below.")
        return redirect("course-detail", pk=course_pk)


class ModuleEditView(View):
    def get(self, request, pk):
        module = get_object_or_404(Module, pk=pk)
        return render(request, "courses/module_form.html", {
            "form": ModuleForm(instance=module),
            "module": module,
        })

    def post(self, request, pk):
        module = get_object_or_404(Module, pk=pk)
        form = ModuleForm(request.POST, request.FILES, instance=module)
        if form.is_valid():
            try:
                module = form.save(commit=False)
                parsed_upload = parse_uploaded_files(form.cleaned_data.get("content_files") or [])
                module.content = merge_module_content(
                    form.cleaned_data.get("content"),
                    parsed_upload.text,
                )
                if not module.content.strip():
                    raise ValidationError("No usable text was found in the uploaded files.")
                module.save()
                extract_module_skills(module)
                module.save(update_fields=["skill_entities", "skills_extracted"])
                messages.success(request, "Module updated.")
                add_ignored_file_message(request, parsed_upload)
                return redirect("course-detail", pk=module.course.pk)
            except ValidationError as exc:
                form.add_error("content_files", exc)
                messages.error(request, "Please fix the errors below.")
        return render(request, "courses/module_form.html", {
            "form": form, "module": module,
        })


class ModuleDeleteView(View):
    def post(self, request, pk):
        module = get_object_or_404(Module, pk=pk)
        course_pk = module.course.pk
        module.delete()
        messages.success(request, "Module deleted.")
        return redirect("course-detail", pk=course_pk)


class ModuleSkillEnhanceView(View):
    def post(self, request, pk):
        module = get_object_or_404(Module, pk=pk)
        skill_input = request.POST.get("skills")
        if not parse_skill_enhancements(skill_input):
            messages.info(request, "Add at least one skill to enhance the extracted list.")
            return redirect("course-detail", pk=module.course.pk)
        added = enhance_module_skills(module, skill_input)
        if added:
            messages.success(request, f"Added {len(added)} enhanced skill{'s' if len(added) != 1 else ''} to '{module.name}'.")
        else:
            messages.info(request, "No new skills were added; existing skills were marked as reviewed.")
        return redirect("course-detail", pk=module.course.pk)


class ModuleSkillDeleteView(View):
    def post(self, request, pk):
        module = get_object_or_404(Module, pk=pk)
        skill = request.POST.get("skill", "")
        next_url = request.POST.get("next") or "detail"
        changed = remove_module_skill(module, skill)
        if changed:
            messages.success(request, f"Removed '{skill}' from '{module.name}'.")
        else:
            messages.info(request, f"Skill '{skill}' was not found on '{module.name}'.")
        if next_url == "audit":
            return redirect("course-skill-audit", pk=module.course.pk)
        return redirect("course-detail", pk=module.course.pk)


def merge_module_content(pasted_content, parsed_file_content):
    parts = [
        (pasted_content or "").strip(),
        (parsed_file_content or "").strip(),
    ]
    return "\n\n".join(part for part in parts if part)


def normalize_audit_skill(value):
    return " ".join(str(value or "").lower().replace("-", " ").split())


def skill_evidence_context(content, entity, skill, max_chars=900):
    text = str(content or "")
    if not text.strip():
        return ""
    start = entity.get("start") if isinstance(entity, dict) else None
    end = entity.get("end") if isinstance(entity, dict) else None
    try:
        start = int(start)
        end = int(end)
    except (TypeError, ValueError):
        start = end = None
    if start is None or end is None or not (0 <= start < end <= len(text)):
        mention = str((entity or {}).get("text") or skill or "").strip()
        if mention:
            index = text.lower().find(mention.lower())
            if index >= 0:
                start, end = index, index + len(mention)
    if start is None or end is None:
        return compact_context(text, max_chars=max_chars)

    paragraph_start = text.rfind("\n\n", 0, start)
    paragraph_start = 0 if paragraph_start < 0 else paragraph_start + 2
    paragraph_end = text.find("\n\n", end)
    paragraph_end = len(text) if paragraph_end < 0 else paragraph_end
    paragraph = text[paragraph_start:paragraph_end].strip()
    if len(paragraph) <= max_chars:
        return paragraph

    window_start = max(0, start - max_chars // 2)
    window_end = min(len(text), end + max_chars // 2)
    snippet = text[window_start:window_end].strip()
    prefix = "... " if window_start > 0 else ""
    suffix = " ..." if window_end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def compact_context(text, max_chars=900):
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars - 4].rstrip()} ..."


def find_module_skill_entity(entities, original_skill, entity_id="", chunk_id=""):
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if chunk_id and entity.get("chunk_id") == chunk_id:
            return entity
        if entity_id and entity.get("id") == entity_id:
            return entity
        if normalize_audit_skill(entity.get("skill") or entity.get("text")) == original_skill:
            return entity
    return None


def build_reviewed_skill_entity(module, skill):
    classification = classify_skill_text(skill, skill, source="audit_review")
    return {
        "id": audit_skill_entity_id(skill),
        "chunk_id": audit_skill_chunk_id(f"module-{module.pk or 'new'}", skill, None, None),
        "skill": skill,
        "label": "SKILL",
        "tier": classification["tier"],
        "skill_type": classification["skill_type"],
        "classification_scores": classification["scores"],
        "pattern": "manual",
        "pos_signature": "",
        "text": skill,
        "start": None,
        "end": None,
        "source": "audit_review",
        "confidence": 1.0,
        "mentions": [{"text": skill, "start": None, "end": None}],
        "mention_count": 1,
        "label_status": "reviewed",
    }


def audit_skill_entity_id(skill):
    canonical = normalize_audit_skill(skill)
    slug = "".join(ch if ch.isalnum() else "-" for ch in canonical).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    normalized_slug_source = "-".join(canonical.split())
    if slug and slug != normalized_slug_source:
        return f"skill-{slug[:63]}-{digest}"
    if slug:
        return f"skill-{slug[:72]}"
    return f"skill-{digest}"


def audit_skill_chunk_id(document_id, canonical, start, end):
    raw = f"{document_id}|{canonical}|{start}|{end}"
    return "chunk-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def add_ignored_file_message(request, parsed_upload):
    if not parsed_upload.ignored_count:
        return
    names = ", ".join(parsed_upload.ignored_files[:5])
    more = parsed_upload.ignored_count - 5
    suffix = f", and {more} more" if more > 0 else ""
    messages.warning(
        request,
        f"Ignored {parsed_upload.ignored_count} protected or unreadable file"
        f"{'s' if parsed_upload.ignored_count != 1 else ''}: {names}{suffix}.",
    )
