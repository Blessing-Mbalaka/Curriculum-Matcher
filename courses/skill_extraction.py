def extract_module_skills(module):
    from analysis.spacyskillextraction import SpacySkillExtractor

    extractor = SpacySkillExtractor()
    skill_entities = extractor.extract_entities(module.content, document_id=f"module-{module.pk or 'new'}")
    module.skill_entities = skill_entities
    module.skills_extracted = sorted({entity["skill"] for entity in skill_entities})
    return module


def ensure_module_skills(module):
    if module.content and not module.skill_entities and not module.skills_extracted:
        extract_module_skills(module)
        module.save(update_fields=["skill_entities", "skills_extracted"])
    return module


def normalize_skill_name(value):
    return " ".join(str(value or "").lower().replace("-", " ").split())


def parse_skill_enhancements(raw_value):
    parts = []
    for line in str(raw_value or "").replace(";", ",").splitlines():
        parts.extend(line.split(","))
    skills = []
    seen = set()
    for part in parts:
        skill = normalize_skill_name(part)
        if skill and skill not in seen:
            seen.add(skill)
            skills.append(skill)
    return skills


def enhance_module_skills(module, raw_skills):
    from analysis.spacyskillextraction import SpacySkillExtractor, classify_skill_text

    new_skills = parse_skill_enhancements(raw_skills)
    if not new_skills:
        return []

    extractor = SpacySkillExtractor()
    entities_by_skill = {
        normalize_skill_name(entity.get("skill")): dict(entity)
        for entity in (module.skill_entities or [])
        if entity.get("skill")
    }

    added = []
    for skill in new_skills:
        existing = entities_by_skill.get(skill)
        if existing:
            existing["label_status"] = "reviewed"
            entities_by_skill[skill] = existing
            continue
        classification = classify_skill_text(skill, skill, source="manual_enhancement")
        entities_by_skill[skill] = {
            "id": extractor._entity_id(skill),
            "chunk_id": extractor._chunk_id(f"module-{module.pk or 'new'}", skill, None, None),
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
            "source": "manual_enhancement",
            "confidence": 1.0,
            "mentions": [{"text": skill, "start": None, "end": None}],
            "mention_count": 1,
            "label_status": "reviewed",
        }
        added.append(skill)

    module.skill_entities = sorted(entities_by_skill.values(), key=lambda item: item["skill"])
    module.skills_extracted = sorted(entities_by_skill)
    module.save(update_fields=["skill_entities", "skills_extracted"])
    return added


def remove_module_skill(module, skill_name):
    skill = normalize_skill_name(skill_name)
    if not skill:
        return False

    original_skills = module.skills_extracted or []
    original_entities = module.skill_entities or []
    next_skills = [
        item
        for item in original_skills
        if normalize_skill_name(item) != skill
    ]
    next_entities = [
        entity
        for entity in original_entities
        if normalize_skill_name(entity.get("skill") or entity.get("text")) != skill
    ]
    changed = next_skills != original_skills or next_entities != original_entities
    if changed:
        module.skills_extracted = sorted({normalize_skill_name(item) for item in next_skills if item})
        module.skill_entities = sorted(next_entities, key=lambda item: normalize_skill_name(item.get("skill")))
        module.save(update_fields=["skills_extracted", "skill_entities"])
    return changed
