from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views import View
from django.views.generic import ListView

from .models import Course, Module
from .forms import CourseForm, ModuleForm


class CourseListView(ListView):
    model = Course
    template_name = "courses/list.html"
    context_object_name = "courses"

    def get_queryset(self):
        return Course.objects.prefetch_related("modules").all()


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
        modules = course.modules.all()
        return render(request, "courses/detail.html", {
            "course": course,
            "modules": modules,
            "module_form": ModuleForm(),
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
        form = ModuleForm(request.POST)
        if form.is_valid():
            module = form.save(commit=False)
            module.course = course
            module.save()
            messages.success(request, f"Module '{module.name}' added.")
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
        form = ModuleForm(request.POST, instance=module)
        if form.is_valid():
            form.save()
            messages.success(request, "Module updated.")
            return redirect("course-detail", pk=module.course.pk)
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
