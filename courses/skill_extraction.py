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
