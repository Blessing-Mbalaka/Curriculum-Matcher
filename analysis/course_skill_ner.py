import hashlib
import json
import random
import re
from pathlib import Path

from django.conf import settings

from courses.models import Module
from jobs.models import JobAdvert


def normalize_skill(value):
    return " ".join(str(value or "").lower().replace("-", " ").split())


def find_skill_offsets(text, skill):
    clean_skill = " ".join(str(skill or "").strip().split())
    if not text or not clean_skill:
        return None
    pattern = r"(?<!\w)" + re.escape(clean_skill).replace(r"\ ", r"\s+") + r"(?!\w)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.start(), match.end()


def collect_training_examples(reviewed_only=False):
    return collect_skill_ner_training_examples(reviewed_only=reviewed_only, include_jobs=False)


def collect_skill_ner_training_examples(reviewed_only=False, include_jobs=True):
    examples = []
    skipped = 0
    modules = Module.objects.select_related("course").order_by("course__code", "order", "name", "id")
    for module in modules:
        added, missed = collect_examples_from_document(
            module.content or "",
            module.skill_entities or [],
            module.skills_extracted or [],
            reviewed_only=reviewed_only,
        )
        examples.extend(added)
        skipped += missed
    if include_jobs:
        jobs = JobAdvert.objects.order_by("title", "id")
        for job in jobs:
            added, missed = collect_examples_from_document(
                job.analysis_text(),
                job.skill_entities or [],
                job.skills_extracted or [],
                reviewed_only=reviewed_only,
            )
            examples.extend(added)
            skipped += missed
    return examples, skipped


def collect_examples_from_document(text, raw_entities, fallback_skills, reviewed_only=False):
    examples = []
    skipped = 0
    if text:
        entities = []
        seen_spans = set()
        raw_entities = list(raw_entities or [])
        if not raw_entities:
            raw_entities = [
                {"skill": skill, "label": "SKILL", "label_status": "legacy"}
                for skill in (fallback_skills or [])
            ]
        candidate_entities = []
        for entity in raw_entities:
            if not isinstance(entity, dict):
                continue
            if entity.get("label_status") == "candidate":
                continue
            if reviewed_only and entity.get("label_status") != "reviewed":
                continue
            if (entity.get("label") or "SKILL") != "SKILL":
                continue
            skill = normalize_skill(entity.get("skill") or entity.get("text"))
            if not skill:
                continue
            start = entity.get("start")
            end = entity.get("end")
            if start is None or end is None:
                offsets = find_skill_offsets(text, skill)
                if not offsets:
                    skipped += 1
                    continue
                start, end = offsets
            try:
                start = int(start)
                end = int(end)
            except (TypeError, ValueError):
                skipped += 1
                continue
            if not (0 <= start < end <= len(text)):
                skipped += 1
                continue
            candidate_entities.append((start, end, "SKILL"))
        for start, end, label in sorted(candidate_entities, key=lambda item: (item[0], item[1] - item[0])):
            if any(start < seen_end and end > seen_start for seen_start, seen_end in seen_spans):
                skipped += 1
                continue
            seen_spans.add((start, end))
            entities.append((start, end, label))
        if entities:
            examples.append((text, {"entities": sorted(entities)}))
    return examples, skipped


def model_output_path(output=None):
    return Path(output or getattr(settings, "COURSE_SKILL_NER_MODEL_PATH", "models/course_skill_ner"))


def training_fingerprint():
    payload = []
    modules = Module.objects.order_by("id").values(
        "id", "content", "skills_extracted", "skill_entities",
    )
    for module in modules:
        payload.append({
            "id": module["id"],
            "content": module["content"] or "",
            "skills_extracted": module["skills_extracted"] or [],
            "skill_entities": module["skill_entities"] or [],
        })
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def model_needs_training(output=None):
    path = model_output_path(output)
    meta_path = path / "meta.json"
    fingerprint_path = path / "training-fingerprint.txt"
    if not meta_path.exists():
        return True
    if not fingerprint_path.exists():
        return True
    return fingerprint_path.read_text(encoding="utf-8").strip() != training_fingerprint()


def train_course_skill_ner(
    base_model=None,
    output=None,
    epochs=30,
    dropout=0.2,
    dev_ratio=0.2,
    seed=42,
    min_examples=5,
    reviewed_only=False,
    progress_callback=None,
):
    import spacy
    from spacy.training import Example
    from spacy.util import compounding, minibatch

    def report(message):
        if progress_callback:
            progress_callback(message)

    examples, skipped = collect_training_examples(reviewed_only=reviewed_only)
    if len(examples) < min_examples:
        return {
            "trained": False,
            "reason": f"Only {len(examples)} usable labelled modules found; need at least {min_examples}.",
            "examples": len(examples),
            "skipped": skipped,
        }

    random.seed(seed)
    random.shuffle(examples)
    dev_size = max(1, int(len(examples) * dev_ratio)) if len(examples) > 1 else 0
    dev_examples = examples[:dev_size]
    train_examples = examples[dev_size:] or examples

    model_name = base_model or getattr(settings, "SPACY_MODEL_NAME", "en_core_web_sm")
    nlp = spacy.load(model_name)

    if "ner" not in nlp.pipe_names:
        ner = nlp.add_pipe("ner")
    else:
        ner = nlp.get_pipe("ner")
    ner.add_label("SKILL")

    pipe_exceptions = {"ner", "tok2vec", "transformer"}
    disabled = [pipe for pipe in nlp.pipe_names if pipe not in pipe_exceptions]
    optimizer = nlp.resume_training()

    def make_examples(raw_examples):
        return [Example.from_dict(nlp.make_doc(text), annotations) for text, annotations in raw_examples]

    train_batch = make_examples(train_examples)
    losses = {}
    with nlp.disable_pipes(*disabled):
        for epoch in range(epochs):
            random.shuffle(train_batch)
            losses = {}
            batches = minibatch(train_batch, size=compounding(2.0, 16.0, 1.4))
            for batch in batches:
                nlp.update(batch, drop=dropout, sgd=optimizer, losses=losses)
            report(f"Course skill NER epoch {epoch + 1}/{epochs}: {losses}")

    output_path = model_output_path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nlp.to_disk(output_path)
    (output_path / "training-fingerprint.txt").write_text(training_fingerprint(), encoding="utf-8")
    return {
        "trained": True,
        "output": str(output_path),
        "train_examples": len(train_examples),
        "dev_examples": len(dev_examples),
        "skipped": skipped,
        "losses": losses,
    }


def ensure_course_skill_ner_model(progress_callback=None, force=False):
    if not getattr(settings, "AUTO_TRAIN_COURSE_SKILL_NER", True):
        return {"trained": False, "reason": "Automatic course skill NER training is disabled."}
    if not force and not model_needs_training():
        return {"trained": False, "reason": "Existing course skill NER model is up to date."}
    try:
        return train_course_skill_ner(
            epochs=getattr(settings, "COURSE_SKILL_NER_AUTO_EPOCHS", 8),
            min_examples=getattr(settings, "COURSE_SKILL_NER_MIN_EXAMPLES", 5),
            progress_callback=progress_callback,
        )
    except Exception as exc:
        return {
            "trained": False,
            "reason": f"Automatic course skill NER training failed; continuing with available extractor. {exc}",
        }
