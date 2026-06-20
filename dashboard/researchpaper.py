from collections import Counter
from io import BytesIO

from django.utils import timezone

from analysis.models import GapResult, SkillMatrix
from courses.models import Course, Module
from jobs.models import JobAdvert


REPORT_FILENAME = "curriculummatch-research-paper.docx"


def docx_add_field(paragraph, instruction, placeholder="Right-click and update field in Word."):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    run._r.append(begin)

    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    run._r.append(instr)

    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    run._r.append(separate)
    paragraph.add_run(placeholder)

    end_run = paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    end_run._r.append(end)


def docx_shade(cell, fill):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    tc_pr.append(shading)


def docx_set_cell_text(cell, text, bold=False):
    cell.text = ""
    run = cell.paragraphs[0].add_run(str(text))
    run.bold = bold


def add_simple_table(document, headers, rows):
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for index, header in enumerate(headers):
        docx_set_cell_text(table.rows[0].cells[index], header, True)
        docx_shade(table.rows[0].cells[index], "F2EEE8")
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = str(value)
    return table


def add_key_value_table(document, rows):
    return add_simple_table(document, ["Metric", "Value"], rows)


def plotly_to_image(fig, width=1050, height=560):
    if fig is None:
        return None
    try:
        return BytesIO(fig.to_image(format="png", width=width, height=height, scale=2))
    except Exception:
        return None


def plotly_go():
    try:
        import plotly.graph_objects as go
        return go
    except Exception:
        return None


def add_figure(document, title, caption, fig, fallback_headers=None, fallback_rows=None, width=6.4):
    from docx.shared import Inches

    document.add_heading(title, level=2)
    image = plotly_to_image(fig)
    if image:
        document.add_picture(image, width=Inches(width))
        document.add_paragraph(caption)
        return True
    document.add_paragraph(f"{caption} Image rendering was unavailable, so the source table is shown below.")
    if fallback_headers and fallback_rows:
        add_simple_table(document, fallback_headers, fallback_rows)
    return False


def create_bar_figure(title, labels, values, x_title="", y_title="Count", color="#f58220"):
    go = plotly_go()
    if go is None:
        return None

    fig = go.Figure(go.Bar(
        x=labels,
        y=values,
        marker={"color": color},
        text=values,
        textposition="outside",
    ))
    fig.update_layout(
        title={"text": title, "x": 0.02, "xanchor": "left"},
        margin={"l": 60, "r": 30, "t": 70, "b": 130},
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis={"title": x_title, "tickangle": -35},
        yaxis={"title": y_title, "gridcolor": "#f2eee8"},
        showlegend=False,
    )
    return fig


def score_distribution_figure(results):
    rows = score_distribution_rows(results)
    return create_bar_figure("Course-to-Job Score Distribution", [row[0] for row in rows], [row[1] for row in rows], "Score band", "Comparisons", "#2f80ed")


def score_distribution_rows(results):
    labels = ["0-20", "20-40", "40-60", "60-80", "80-100"]
    counts = dict.fromkeys(labels, 0)
    for result in results:
        score = result.similarity_percent
        if score < 20:
            counts["0-20"] += 1
        elif score < 40:
            counts["20-40"] += 1
        elif score < 60:
            counts["40-60"] += 1
        elif score < 80:
            counts["60-80"] += 1
        else:
            counts["80-100"] += 1
    return [(label, counts[label]) for label in labels]


def top_skill_rows(matrix_rows, limit=15):
    return [(row.skill, row.frequency) for row in matrix_rows[:limit]]


def skill_gap_rows(visual_data, limit=15):
    rows = visual_data.get("skill_suggestion_matrix", [])[:limit]
    return [
        (
            row["skill"],
            row["demand_count"],
            row["matched_count"],
            row["missing_count"],
            f'{row["gap_percent"]}%',
        )
        for row in rows
    ]


def skill_gap_figure(visual_data):
    go = plotly_go()
    if go is None:
        return None

    rows = visual_data.get("skill_suggestion_matrix", [])[:15]
    labels = [row["skill"] for row in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Covered", x=labels, y=[row["matched_count"] for row in rows], marker={"color": "#236b35"}))
    fig.add_trace(go.Bar(name="Missing", x=labels, y=[row["missing_count"] for row in rows], marker={"color": "#f58220"}))
    fig.update_layout(
        title={"text": "Skill Coverage and Gap Evidence", "x": 0.02, "xanchor": "left"},
        barmode="group",
        margin={"l": 60, "r": 30, "t": 70, "b": 140},
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis={"tickangle": -35},
        yaxis={"title": "Evidence count", "gridcolor": "#f2eee8"},
        legend={"orientation": "h", "y": 1.08},
    )
    return fig


def scatter_figure(visual_data):
    go = plotly_go()
    if go is None:
        return None

    points = visual_data.get("scatter_points", [])
    fig = go.Figure(go.Scatter(
        x=[point["x"] for point in points],
        y=[point["y"] for point in points],
        mode="markers+text",
        text=[point["label"] for point in points],
        textposition="top center",
        marker={
            "size": [max(10, min(38, point["score"] / 2)) for point in points],
            "color": [point["score"] for point in points],
            "colorscale": [[0, "#f1e7dd"], [0.5, "#f58220"], [1, "#236b35"]],
            "showscale": True,
            "colorbar": {"title": "Avg score"},
        },
    ))
    fig.update_layout(
        title={"text": "Matched vs Missing Skill Evidence", "x": 0.02, "xanchor": "left"},
        margin={"l": 70, "r": 40, "t": 70, "b": 70},
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis={"title": "Matched evidence", "gridcolor": "#f2eee8"},
        yaxis={"title": "Missing evidence", "gridcolor": "#f2eee8"},
    )
    return fig


def heatmap_figure(visual_data):
    go = plotly_go()
    if go is None:
        return None

    rows = visual_data.get("plotly_heatmap_rows", [])[:180]
    courses = []
    jobs = []
    for row in rows:
        if row["course"] not in courses:
            courses.append(row["course"])
        if row["job"] not in jobs:
            jobs.append(row["job"])
    courses = courses[:18]
    jobs = jobs[:12]
    score_map = {(row["course"], row["job"]): row["score"] for row in rows}
    z = [[score_map.get((course, job), None) for job in jobs] for course in courses]
    fig = go.Figure(go.Heatmap(
        z=z,
        x=jobs,
        y=courses,
        colorscale=[[0, "#f1f3f5"], [0.35, "#ffe1c2"], [0.7, "#f58220"], [1, "#236b35"]],
        colorbar={"title": "Score %"},
        zmin=0,
        zmax=100,
    ))
    fig.update_layout(
        title={"text": "Course-to-Job Gap Heatmap", "x": 0.02, "xanchor": "left"},
        margin={"l": 140, "r": 30, "t": 70, "b": 150},
        paper_bgcolor="white",
        xaxis={"tickangle": -40},
    )
    return fig


def school_summary_figure(visual_data):
    go = plotly_go()
    if go is None:
        return None

    rows = visual_data.get("school_summaries", [])
    labels = [row["school"] for row in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Average score", x=labels, y=[row["avg_score"] for row in rows], marker={"color": "#2f80ed"}))
    fig.add_trace(go.Bar(name="Missing evidence", x=labels, y=[row["missing_total"] for row in rows], marker={"color": "#f58220"}, yaxis="y2"))
    fig.update_layout(
        title={"text": "School Alignment Summary", "x": 0.02, "xanchor": "left"},
        margin={"l": 70, "r": 70, "t": 80, "b": 130},
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis={"tickangle": -30},
        yaxis={"title": "Average score %", "gridcolor": "#f2eee8", "range": [0, 100]},
        yaxis2={"title": "Missing evidence", "overlaying": "y", "side": "right"},
        legend={"orientation": "h", "y": 1.08},
    )
    return fig


def oversight_counts():
    counts = Counter()
    for model in (JobAdvert, Module):
        for entities in model.objects.values_list("skill_entities", flat=True):
            for entity in entities or []:
                if isinstance(entity, dict):
                    counts[entity.get("label_status") or "machine"] += 1
    return counts


def build_research_paper_docx(run, visual_data):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    document = Document()
    section = document.sections[0]
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("Page ")
    docx_add_field(footer, "PAGE", "1")

    results = list(GapResult.objects.filter(run=run).select_related("course", "job")) if run else []
    job_skill_rows = list(SkillMatrix.objects.filter(run=run, source="jobs").order_by("-frequency", "skill")[:25]) if run else []
    course_skill_rows = list(SkillMatrix.objects.filter(run=run, source="courses").order_by("-frequency", "skill")[:25]) if run else []
    schools = list(Course.objects.exclude(university_name="").values_list("university_name", flat=True).distinct())
    if Course.objects.filter(university_name="").exists():
        schools.append("Unassigned school")

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title.add_run("CurriculumMatch Research Paper Export")
    title_run.bold = True
    title_run.font.size = Pt(20)
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run("Tables, graphs, visualisations, methodology notes, and human review evidence").italic = True
    document.add_paragraph(f"Generated: {timezone.localtime(timezone.now()).strftime('%d %B %Y %H:%M')}")
    document.add_paragraph(f"Analysis run: {run.name if run else 'No completed analysis run available'}")
    document.add_page_break()

    document.add_heading("Table of Contents", level=1)
    docx_add_field(document.add_paragraph(), 'TOC \\o "1-3" \\h \\z \\u')
    document.add_paragraph("In Microsoft Word, right-click the table above and choose Update Field to refresh page numbers.")
    document.add_page_break()

    avg_score = round(sum(result.similarity_score for result in results) * 100 / len(results), 1) if results else 0
    counts = oversight_counts()
    document.add_heading("1. Executive Summary", level=1)
    document.add_paragraph(
        "This enhanced export packages the system evidence into a research-paper style document. It includes the same dashboard "
        "source data as tables, plus server-side Plotly image exports for the main charts and visualisations."
    )
    add_key_value_table(document, [
        ("Schools", len(schools)),
        ("Courses", Course.objects.count()),
        ("Modules", Module.objects.count()),
        ("Job adverts", JobAdvert.objects.count()),
        ("Course-job comparisons", len(results)),
        ("Average final score", f"{avg_score}%"),
        ("Reviewed skill entities", counts.get("reviewed", 0)),
        ("Candidate skill entities", counts.get("candidate", 0)),
    ])

    document.add_heading("2. Methodology", level=1)
    document.add_paragraph(
        "A skill is treated as a named capability that can be evidenced in curriculum text or job adverts. Technical skills are "
        "tools, programming languages, platforms, methods, or measurable specialist techniques. Soft skills are human workplace "
        "capabilities such as communication, leadership, collaboration, and problem solving. Business or domain skills describe "
        "work-area knowledge such as accounting, compliance, marketing, risk, education, or healthcare."
    )
    document.add_paragraph(
        "The system combines rule-assisted extraction, dynamic learned skills, human review, and semantic comparison. A candidate "
        "skill can be suggested by the model, but it only becomes trusted learning data after a human approves or edits it."
    )
    document.add_paragraph("Final score: 75% semantic similarity plus 25% explicit skill coverage.")

    document.add_heading("3. Visual Evidence", level=1)
    if results:
        add_figure(
            document,
            "Figure 1. Score Distribution",
            "This chart shows how course-to-job comparisons are distributed across score bands.",
            score_distribution_figure(results),
            ["Score band", "Comparisons"],
            score_distribution_rows(results),
        )
        add_figure(
            document,
            "Figure 2. Course-to-Job Heatmap",
            "This heatmap is the report image equivalent of the dashboard matrix visualisation.",
            heatmap_figure(visual_data),
        )
        add_figure(
            document,
            "Figure 3. Matched vs Missing Skill Evidence",
            "Each point represents a course, with position showing matched and missing skill evidence.",
            scatter_figure(visual_data),
        )
        add_figure(
            document,
            "Figure 4. School Summary",
            "This chart compares school-level average alignment with missing-skill evidence.",
            school_summary_figure(visual_data),
        )
        add_figure(
            document,
            "Figure 5. Skill Gap Matrix",
            "This chart compares covered and missing evidence for high-demand skills.",
            skill_gap_figure(visual_data),
            ["Skill", "Demand", "Covered", "Missing", "Gap %"],
            skill_gap_rows(visual_data),
        )
    else:
        document.add_paragraph("No completed course-to-job analysis results are available for chart export yet.")

    if job_skill_rows:
        rows = top_skill_rows(job_skill_rows)
        labels, values = zip(*rows)
        add_figure(
            document,
            "Figure 6. Top Job Skills",
            "Top skills extracted from job-market evidence.",
            create_bar_figure("Top Job Skills", list(labels), list(values), "Skill", "Frequency", "#0b0b0b"),
            ["Skill", "Frequency"],
            rows,
        )
    if course_skill_rows:
        rows = top_skill_rows(course_skill_rows)
        labels, values = zip(*rows)
        add_figure(
            document,
            "Figure 7. Top Course Skills",
            "Top skills extracted from course and module evidence.",
            create_bar_figure("Top Course Skills", list(labels), list(values), "Skill", "Frequency", "#236b35"),
            ["Skill", "Frequency"],
            rows,
        )

    document.add_heading("4. Tables", level=1)
    document.add_heading("School Summary Table", level=2)
    add_simple_table(
        document,
        ["School", "Average score", "Matched evidence", "Missing evidence", "Courses", "Risk"],
        [
            [
                row["school"],
                f'{row["avg_score"]}%',
                row["matched_total"],
                row["missing_total"],
                row["course_count"],
                "Yes" if row["mismatch"] else "No",
            ]
            for row in visual_data.get("school_summaries", [])
        ] or [["No analysis data", "", "", "", "", ""]],
    )

    document.add_heading("Skill Gap Matrix Table", level=2)
    add_simple_table(
        document,
        ["Skill", "Demand evidence", "Covered", "Missing", "Gap %"],
        skill_gap_rows(visual_data, limit=30) or [["No matrix data", "", "", "", ""]],
    )

    document.add_heading("Top Course-to-Job Results", level=2)
    top_results = sorted(results, key=lambda item: item.similarity_score, reverse=True)[:25]
    add_simple_table(
        document,
        ["Course", "Job advert", "Company", "Score", "Matched skills", "Missing skills"],
        [
            [
                result.course.name[:60],
                result.job.title[:60],
                result.job.company or "",
                f"{result.similarity_percent}%",
                ", ".join((result.matched_skills or [])[:6]),
                ", ".join((result.missing_skills or [])[:6]),
            ]
            for result in top_results
        ] or [["No analysis data", "", "", "", "", ""]],
    )

    document.add_heading("5. Human Oversight and Learning", level=1)
    document.add_paragraph(
        "The human oversight workflow keeps model suggestions auditable. Reviewers can approve, edit, reject, or delete candidate "
        "skills. Approved rows update the trusted skill lists and can be included in the next NER training run."
    )
    add_key_value_table(document, [
        ("Candidate entities waiting", counts.get("candidate", 0)),
        ("Reviewed entities", counts.get("reviewed", 0)),
        ("Excluded entities", counts.get("exclude", 0)),
        ("Machine/legacy entities", counts.get("machine", 0) + counts.get("legacy", 0)),
    ])

    document.add_heading("6. Limitations", level=1)
    document.add_paragraph(
        "The charts are evidence summaries, not automatic curriculum decisions. Scores can be affected by short module descriptions, "
        "thin job adverts, noisy text, duplicate wording, sector bias, or terminology differences. Human review remains necessary "
        "before changing curriculum content or retraining the model."
    )

    output = BytesIO()
    document.save(output)
    output.seek(0)
    return output
