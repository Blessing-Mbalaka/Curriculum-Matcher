import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from analysis.models import TaskRecord
from analysis.services import run_gap_analysis
from jobs.models import JobAdvert, job_fingerprint


PROVINCE_CITIES = [
    ("Johannesburg", "Gauteng"),
    ("Pretoria", "Gauteng"),
    ("Midrand", "Gauteng"),
    ("Sandton", "Gauteng"),
    ("Cape Town", "Western Cape"),
    ("Bellville", "Western Cape"),
    ("Stellenbosch", "Western Cape"),
    ("Durban", "KwaZulu-Natal"),
    ("Pinetown", "KwaZulu-Natal"),
    ("Umhlanga", "KwaZulu-Natal"),
    ("Gqeberha", "Eastern Cape"),
    ("East London", "Eastern Cape"),
    ("Bloemfontein", "Free State"),
    ("Polokwane", "Limpopo"),
    ("Nelspruit", "Mpumalanga"),
    ("Rustenburg", "North West"),
]

COMPANY_PREFIXES = [
    "Amajuba", "Blue Dune", "Cedar Bridge", "Delta Ridge", "Golden Kudu", "Harbour Stone",
    "Imbali", "Karoo Peak", "Lion Crest", "Metsi", "Northbank", "Ubuntu Edge",
    "Sisonke", "ThrivePoint", "Umoya", "Vela", "Zebra Rock", "Ivory Stream",
]

COMPANY_SUFFIXES = [
    "Advisory", "Analytics", "Capital", "Commercial", "Consumer Group", "Digital",
    "Finance", "Foods", "Holdings", "Logistics", "Manufacturing", "Partners",
    "Retail", "Services", "Solutions", "Supply Chain", "Technologies", "Ventures",
]

ROLE_TEMPLATES = [
    {
        "title": "Management Accountant",
        "category": "MBA Finance",
        "salary": (360000, 620000),
        "skills": ["management accounting", "budgeting", "forecasting", "excel", "financial reporting", "stakeholder management"],
        "summary": "Support monthly reporting, budgeting, variance analysis, and commercial decision support.",
        "position_info": "CA(SA) not required; strong commercial finance mindset preferred.",
        "description": (
            "{company} is hiring a Management Accountant in {city}. The role supports budgeting, forecasting, "
            "management accounting, monthly financial reporting, and stakeholder management for business unit leaders."
        ),
    },
    {
        "title": "Business Analyst",
        "category": "MBA Strategy",
        "salary": (320000, 560000),
        "skills": ["business analysis", "presentation", "stakeholder management", "problem solving", "excel", "project management"],
        "summary": "Translate operational and commercial requirements into actionable business insights.",
        "position_info": "Experience with cross-functional stakeholders and reporting is advantageous.",
        "description": (
            "{company} needs a Business Analyst in {city} to drive business analysis, presentation of insights, "
            "project management support, and stakeholder management across transformation initiatives."
        ),
    },
    {
        "title": "Finance Analyst",
        "category": "MBA Finance",
        "salary": (300000, 520000),
        "skills": ["financial modelling", "forecasting", "excel", "quantitative analysis", "presentation", "communication"],
        "summary": "Prepare financial models, commercial analysis, and executive reporting packs.",
        "position_info": "Advanced Excel and analytical storytelling are important for this role.",
        "description": (
            "{company} is recruiting a Finance Analyst in {city} to prepare financial modelling, forecasting, "
            "quantitative analysis, and executive presentation material for strategic planning."
        ),
    },
    {
        "title": "Operations Manager",
        "category": "MBA Operations",
        "salary": (420000, 760000),
        "skills": ["operations management", "leadership", "process improvement", "stakeholder management", "reporting", "change management"],
        "summary": "Lead service delivery performance, process improvement, and team execution.",
        "position_info": "Suitable for MBA candidates with multi-site operations exposure.",
        "description": (
            "{company} seeks an Operations Manager in {city} to lead operations management, leadership, "
            "process improvement, reporting, and change management in a growth-focused environment."
        ),
    },
    {
        "title": "HR Business Partner",
        "category": "MBA Human Resources",
        "salary": (420000, 700000),
        "skills": ["human resources", "performance management", "change management", "employment equity", "leadership", "negotiation"],
        "summary": "Partner with leaders on workforce planning, talent, and organisational change.",
        "position_info": "Knowledge of South African labour practices and coaching capability is useful.",
        "description": (
            "{company} is appointing an HR Business Partner in {city} to support human resources strategy, "
            "performance management, employment equity, leadership coaching, and negotiation planning."
        ),
    },
    {
        "title": "Marketing Manager",
        "category": "MBA Marketing",
        "salary": (380000, 650000),
        "skills": ["digital marketing", "brand management", "communication", "presentation", "crm", "customer insights"],
        "summary": "Own campaign planning, brand execution, and customer growth reporting.",
        "position_info": "MBA or postgraduate commercial qualification preferred.",
        "description": (
            "{company} is looking for a Marketing Manager in {city} to drive digital marketing, brand management, "
            "CRM planning, customer insights, and executive communication."
        ),
    },
    {
        "title": "Supply Chain Analyst",
        "category": "MBA Supply Chain",
        "salary": (300000, 540000),
        "skills": ["supply chain", "data analysis", "excel", "forecasting", "reporting", "problem solving"],
        "summary": "Improve demand planning, inventory visibility, and service-level reporting.",
        "position_info": "Exposure to planning, logistics, or procurement analytics is valuable.",
        "description": (
            "{company} requires a Supply Chain Analyst in {city} to support supply chain analysis, data analysis, "
            "forecasting, reporting, and problem solving for planning and logistics teams."
        ),
    },
    {
        "title": "Strategy Associate",
        "category": "MBA Strategy",
        "salary": (450000, 780000),
        "skills": ["strategy", "market analysis", "presentation", "stakeholder management", "financial modelling", "research"],
        "summary": "Support strategic planning, market reviews, and board-level insight preparation.",
        "position_info": "Strong commercial acumen and written communication are important.",
        "description": (
            "{company} is hiring a Strategy Associate in {city} to support strategy development, market analysis, "
            "financial modelling, research, and stakeholder management for executive projects."
        ),
    },
]


def company_name(index: int) -> str:
    prefix = COMPANY_PREFIXES[index % len(COMPANY_PREFIXES)]
    suffix = COMPANY_SUFFIXES[(index // len(COMPANY_PREFIXES)) % len(COMPANY_SUFFIXES)]
    return f"{prefix} {suffix}"


def pick_date(rng: random.Random, start_date: date, end_date: date) -> date:
    span = (end_date - start_date).days
    return start_date + timedelta(days=rng.randint(0, span))


def build_skill_entities(skills):
    entities = []
    for skill in sorted({skill.strip().lower() for skill in skills if skill}):
        entities.append({
            "skill": skill,
            "id": f"skill-{skill.replace(' ', '-')}",
            "source": "seed-generator",
            "confidence": 0.88,
            "label": "SKILL",
            "label_status": "seeded",
        })
    return entities


class Command(BaseCommand):
    help = "Seed a large synthetic South African MBA-oriented jobs dataset for local dashboard testing."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=178000, help="Number of jobs to seed.")
        parser.add_argument("--batch-size", type=int, default=2000, help="Bulk insert batch size.")
        parser.add_argument("--seed", type=int, default=20240630, help="Random seed for repeatable output.")
        parser.add_argument(
            "--clear-existing",
            action="store_true",
            help="Delete previously seeded synthetic CSV jobs before inserting new ones.",
        )
        parser.add_argument(
            "--run-analysis",
            action="store_true",
            help="Run gap analysis after seeding so the dashboard sees a fresh AnalysisRun.",
        )
        parser.add_argument(
            "--run-name",
            default="Synthetic SA MBA Analysis",
            help="Analysis run name to use when --run-analysis is enabled.",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=None,
            help="Optional cap for the number of jobs scored during analysis.",
        )

    def handle(self, *args, **options):
        count = max(1, int(options["count"]))
        batch_size = max(100, int(options["batch_size"]))
        rng = random.Random(int(options["seed"]))
        start_date = date(2024, 1, 1)
        end_date = date(2025, 12, 31)
        task = None

        if options["run_analysis"]:
            task = TaskRecord.objects.create(
                task_id=f"seed-synthetic-sa-jobs:{timezone.now():%Y%m%d%H%M%S}",
                run_name=options["run_name"],
                status="STARTED",
                progress=1,
                notes="Preparing synthetic South African job seed...",
            )

        with transaction.atomic():
            if options["clear_existing"]:
                deleted, _ = JobAdvert.objects.filter(
                    source="csv",
                    external_id__startswith="synthetic-sa-mba-",
                ).delete()
                self.stdout.write(f"Cleared {deleted} previously seeded synthetic job rows.")
                if task:
                    TaskRecord.objects.filter(id=task.id).update(
                        progress=5,
                        notes=f"Cleared {deleted} previously seeded synthetic job rows.",
                        updated_at=timezone.now(),
                    )

            batch = []
            created = 0
            for index in range(count):
                template = ROLE_TEMPLATES[index % len(ROLE_TEMPLATES)]
                city, province = PROVINCE_CITIES[rng.randrange(len(PROVINCE_CITIES))]
                employer = company_name(index)
                posted = pick_date(rng, start_date, end_date)
                salary_min = rng.randrange(template["salary"][0], template["salary"][1], 5000)
                salary_max = salary_min + rng.randrange(40000, 180000, 5000)
                title = template["title"]
                description = template["description"].format(company=employer, city=city)
                skills = sorted(set(template["skills"]))
                skill_entities = build_skill_entities(skills)
                external_id = f"synthetic-sa-mba-{posted:%Y%m%d}-{index + 1:06d}"
                summary = template["summary"]
                position_info = template["position_info"]
                location = f"{city}, {province}"
                url = f"https://jobs.curriculummatch.local/postings/{external_id}"
                fingerprint = job_fingerprint(title, employer, location, description, employer, external_id)

                batch.append(
                    JobAdvert(
                        title=title,
                        company=employer,
                        recruiter=employer,
                        job_reference=external_id[-10:],
                        location=location,
                        category=template["category"],
                        contract_type="permanent",
                        contract_time="full_time",
                        summary=summary,
                        position_info=position_info,
                        raw_description=description,
                        description=description,
                        source="csv",
                        external_id=external_id,
                        fingerprint=fingerprint,
                        url=url,
                        salary_min=salary_min,
                        salary_max=salary_max,
                        skills_extracted=skills,
                        skill_entities=skill_entities,
                        source_payload={
                            "dataset_type": "synthetic",
                            "region": "south_africa",
                            "focus": "mba",
                            "seed": int(options["seed"]),
                        },
                        date_posted=posted,
                    )
                )

                if len(batch) >= batch_size:
                    JobAdvert.objects.bulk_create(batch, ignore_conflicts=True, batch_size=batch_size)
                    created += len(batch)
                    self.stdout.write(f"Inserted {created}/{count} jobs...")
                    if task:
                        progress = min(55, 5 + int(50 * created / max(1, count)))
                        TaskRecord.objects.filter(id=task.id).update(
                            progress=progress,
                            notes=f"Inserted {created}/{count} synthetic job rows...",
                            updated_at=timezone.now(),
                        )
                    batch = []

            if batch:
                JobAdvert.objects.bulk_create(batch, ignore_conflicts=True, batch_size=batch_size)
                created += len(batch)

        seed_message = f"Seeded {created} synthetic South African MBA-oriented CSV jobs dated across 2024-2025."
        self.stdout.write(self.style.SUCCESS(seed_message))

        if not task:
            return

        TaskRecord.objects.filter(id=task.id).update(
            progress=60,
            notes=seed_message + " Running analysis...",
            updated_at=timezone.now(),
        )
        self.stdout.write("Running gap analysis for dashboard data...")

        def report(percent, message):
            mapped = 60 + int(40 * max(0, min(100, percent)) / 100)
            TaskRecord.objects.filter(id=task.id).update(
                status="STARTED",
                progress=mapped,
                notes=message,
                updated_at=timezone.now(),
            )
            self.stdout.write(message)

        try:
            run = run_gap_analysis(
                run_name=options["run_name"],
                progress_callback=report,
                max_jobs=options["max_jobs"],
            )
            TaskRecord.objects.filter(id=task.id).update(
                status="SUCCESS",
                progress=100,
                notes=f"{seed_message} Analysis run #{run.id} completed with status {run.status}.",
                finished_at=timezone.now(),
                updated_at=timezone.now(),
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{seed_message} Analysis run #{run.id} completed with status {run.status}."
                )
            )
        except Exception as exc:
            TaskRecord.objects.filter(id=task.id).update(
                status="FAILURE",
                notes=str(exc),
                finished_at=timezone.now(),
                updated_at=timezone.now(),
            )
            raise
