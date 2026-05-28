from django.contrib import admin
from django.urls import path
from courses.views import (
    CourseListView, CourseCreateView, CourseDetailView,
    CourseEditView, CourseDeleteView,
    ModuleCreateView, ModuleEditView, ModuleDeleteView,
)
from dashboard.views import (
    DashboardView, JobUploadView, JobListView, JobDeleteView,
    RunAnalysisView, AnalysisResultsView,
    TaskListView, task_status_api, results_json,
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # Dashboard
    path("", DashboardView.as_view(), name="home"),

    # Courses
    path("courses/", CourseListView.as_view(), name="course-list"),
    path("courses/new/", CourseCreateView.as_view(), name="course-create"),
    path("courses/<int:pk>/", CourseDetailView.as_view(), name="course-detail"),
    path("courses/<int:pk>/edit/", CourseEditView.as_view(), name="course-edit"),
    path("courses/<int:pk>/delete/", CourseDeleteView.as_view(), name="course-delete"),

    # Modules
    path("courses/<int:course_pk>/modules/add/", ModuleCreateView.as_view(), name="module-create"),
    path("modules/<int:pk>/edit/", ModuleEditView.as_view(), name="module-edit"),
    path("modules/<int:pk>/delete/", ModuleDeleteView.as_view(), name="module-delete"),

    # Jobs
    path("jobs/", JobListView.as_view(), name="job-list"),
    path("jobs/upload/", JobUploadView.as_view(), name="job-upload"),
    path("jobs/<int:pk>/delete/", JobDeleteView.as_view(), name="job-delete"),

    # Analysis
    path("analysis/run/", RunAnalysisView.as_view(), name="run-analysis"),
    path("analysis/results/", AnalysisResultsView.as_view(), name="analysis-results"),

    # Tasks
    path("tasks/", TaskListView.as_view(), name="task-list"),
    path("api/task/<int:pk>/", task_status_api, name="task-status-api"),
    path("api/results/", results_json, name="results-json"),
]
