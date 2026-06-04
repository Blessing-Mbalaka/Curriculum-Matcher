from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views import View
from django.views.generic import ListView

from .models import Course, Module
from .forms import CourseForm, ModuleForm
from .file_parsing import parse_uploaded_files
from .skill_extraction import ensure_module_skills, extract_module_skills


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
                module.content = merge_module_content(
                    form.cleaned_data.get("content"),
                    parse_uploaded_files(form.cleaned_data.get("content_files") or []),
                )
                module.save()
                extract_module_skills(module)
                module.save(update_fields=["skill_entities", "skills_extracted"])
                messages.success(request, f"Module '{module.name}' added.")
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
                module.content = merge_module_content(
                    form.cleaned_data.get("content"),
                    parse_uploaded_files(form.cleaned_data.get("content_files") or []),
                )
                module.save()
                extract_module_skills(module)
                module.save(update_fields=["skill_entities", "skills_extracted"])
                messages.success(request, "Module updated.")
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


def merge_module_content(pasted_content, parsed_file_content):
    parts = [
        (pasted_content or "").strip(),
        (parsed_file_content or "").strip(),
    ]
    return "\n\n".join(part for part in parts if part)
