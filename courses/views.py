from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views import View
from django.views.generic import ListView

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
                    "source": entity.get("source", ""),
                    "confidence": entity.get("confidence", ""),
                    "skill_type": entity.get("skill_type", ""),
                    "tier": entity.get("tier", ""),
                    "label_status": entity.get("label_status", ""),
                    "text": entity.get("text", ""),
                })
        rows.sort(key=lambda item: (item["skill"], item["module"].name))
        return render(request, "courses/skill_audit.html", {
            "course": course,
            "rows": rows,
            "unique_skill_count": len(unique_skills),
            "module_skill_count": len(rows),
        })


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
