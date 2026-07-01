from django.contrib import admin
from django.urls import path
from courses.views import (
    CourseListView, CourseCreateView, CourseDetailView,
    CourseEditView, CourseDeleteView,
    CourseSkillAuditView,
    ModuleCreateView, ModuleEditView, ModuleDeleteView, ModuleSkillEnhanceView,
    ModuleSkillDeleteView,
)
from dashboard.views import (
    DashboardView, JobUploadView, JobListView, JobDeleteView,
    RunAnalysisView, AnalysisResultsView, ModelValidationView,
    StartContinuousJobsView, StartJobsOnlyView, StopTaskView, TaskListView,
    TechnicalReportExportView, TechnicalReportDownloadView, TechnicalReportVisualArchiveView,
    TechnicalReportVisualDownloadView, DataExportView, SkillEntityUpdateView,
    SkillEntityCreateView, SkillEntityDeleteView, SkillEntityBulkApproveView,
    SkillEntityCsvExportView, CourseSkillTrainingCsvExportView,
    CleanedSkillCsvDownloadView, JobCsvExportView, DataExportVisualCsvExportView,
    SkillVectorSpaceView, SkillVectorSpaceCsvExportView, HumanOversightView,
    SkillAliasCreateView, SkillAliasReviewView, ReviewedAliasRefreshView, RagChatbotView, RagChatbotApiView,
    DashboardVisualCsvExportView, AnalysisVisualCsvExportView, SummaryView,
    task_status_api, results_json, dashboard_metrics, similarity_network,
    skill_vector_space, data_export_forecast_api, course_skill_training_readiness,
)
from course_scraper.views import CourseScraperView, StartCourseScrapeView, scrape_status_api
from methodology.views import MethodologyView

urlpatterns = [
    path("admin/", admin.site.urls),

    # Dashboard
    path("", DashboardView.as_view(), name="home"),

    # Courses
    path("courses/", CourseListView.as_view(), name="course-list"),
    path("courses/new/", CourseCreateView.as_view(), name="course-create"),
    path("courses/<int:pk>/", CourseDetailView.as_view(), name="course-detail"),
    path("courses/<int:pk>/skills/audit/", CourseSkillAuditView.as_view(), name="course-skill-audit"),
    path("courses/<int:pk>/edit/", CourseEditView.as_view(), name="course-edit"),
    path("courses/<int:pk>/delete/", CourseDeleteView.as_view(), name="course-delete"),
    path("course-scraper/", CourseScraperView.as_view(), name="course-scraper"),
    path("course-scraper/start/", StartCourseScrapeView.as_view(), name="course-scraper-start"),

    # Modules
    path("courses/<int:course_pk>/modules/add/", ModuleCreateView.as_view(), name="module-create"),
    path("modules/<int:pk>/edit/", ModuleEditView.as_view(), name="module-edit"),
    path("modules/<int:pk>/skills/enhance/", ModuleSkillEnhanceView.as_view(), name="module-skill-enhance"),
    path("modules/<int:pk>/skills/delete/", ModuleSkillDeleteView.as_view(), name="module-skill-delete"),
    path("modules/<int:pk>/delete/", ModuleDeleteView.as_view(), name="module-delete"),

    # Jobs
    path("jobs/", JobListView.as_view(), name="job-list"),
    path("jobs/upload/", JobUploadView.as_view(), name="job-upload"),
    path("jobs/<int:pk>/delete/", JobDeleteView.as_view(), name="job-delete"),

    # Analysis
    path("analysis/run/", RunAnalysisView.as_view(), name="run-analysis"),
    path("analysis/live/start/", StartContinuousJobsView.as_view(), name="start-live-jobs"),
    path("jobs/fetch-only/start/", StartJobsOnlyView.as_view(), name="start-jobs-only"),
    path("tasks/<int:pk>/stop/", StopTaskView.as_view(), name="stop-task"),
    path("analysis/results/", AnalysisResultsView.as_view(), name="analysis-results"),
    path("analysis/validation/", ModelValidationView.as_view(), name="model-validation"),
    path("human-oversight/", HumanOversightView.as_view(), name="human-oversight"),
    path("reports/technical/", TechnicalReportExportView.as_view(), name="technical-report-export"),
    path("reports/technical/<int:pk>/download/", TechnicalReportDownloadView.as_view(), name="technical-report-download"),
    path("reports/technical/visuals/<int:run_id>/", TechnicalReportVisualArchiveView.as_view(), name="technical-report-visual-archive"),
    path("reports/technical/visuals/<int:run_id>/<str:image_name>.png", TechnicalReportVisualDownloadView.as_view(), name="technical-report-visual-download"),
    path("dashboard/visuals.csv", DashboardVisualCsvExportView.as_view(), name="dashboard-visual-export"),
    path("data-export/", DataExportView.as_view(), name="data-export"),
    path("data-export/vector-space/", SkillVectorSpaceView.as_view(), name="skill-vector-space"),
    path("data-export/skills.csv", SkillEntityCsvExportView.as_view(), name="skill-entity-export"),
    path("data-export/course-skill-training.csv", CourseSkillTrainingCsvExportView.as_view(), name="course-skill-training-export"),
    path("data-export/cleaned/<str:file_key>.csv", CleanedSkillCsvDownloadView.as_view(), name="cleaned-skill-csv-download"),
    path("data-export/jobs.csv", JobCsvExportView.as_view(), name="job-skill-export"),
    path("data-export/visuals.csv", DataExportVisualCsvExportView.as_view(), name="data-export-visual-export"),
    path("data-export/vector-space.csv", SkillVectorSpaceCsvExportView.as_view(), name="skill-vector-space-export"),
    path("analysis/results/visuals.csv", AnalysisVisualCsvExportView.as_view(), name="analysis-visual-export"),
    path("data-export/entity/create/", SkillEntityCreateView.as_view(), name="skill-entity-create"),
    path("data-export/entity/update/", SkillEntityUpdateView.as_view(), name="skill-entity-update"),
    path("data-export/entity/delete/", SkillEntityDeleteView.as_view(), name="skill-entity-delete"),
    path("data-export/entity/approve-candidates/", SkillEntityBulkApproveView.as_view(), name="skill-entity-bulk-approve"),
    path("data-export/alias/create/", SkillAliasCreateView.as_view(), name="skill-alias-create"),
    path("data-export/alias/review/", SkillAliasReviewView.as_view(), name="skill-alias-review"),
    path("data-export/alias/hourly-refresh/", ReviewedAliasRefreshView.as_view(), name="skill-alias-hourly-refresh"),
    path("rag-chatbot/", RagChatbotView.as_view(), name="rag-chatbot"),
    path("api/rag-chatbot/", RagChatbotApiView.as_view(), name="rag-chatbot-api"),
    path("methodology/", MethodologyView.as_view(), name="methodology"),
    path("summary/", SummaryView.as_view(), name="summary"),

    # Tasks
    path("tasks/", TaskListView.as_view(), name="task-list"),
    path("api/task/<int:pk>/", task_status_api, name="task-status-api"),
    path("api/results/", results_json, name="results-json"),
    path("api/dashboard/metrics/", dashboard_metrics, name="dashboard-metrics"),
    path("api/dashboard/network/", similarity_network, name="similarity-network"),
    path("api/data-export/forecast/", data_export_forecast_api, name="data-export-forecast-api"),
    path("api/data-export/vector-space/", skill_vector_space, name="skill-vector-space-api"),
    path("api/data-export/course-skill-training/readiness/", course_skill_training_readiness, name="course-skill-training-readiness"),
    path("api/course-scraper/status/", scrape_status_api, name="course-scraper-status"),
]
