"""
spaCy-backed skill extraction.

The extractor uses spaCy PhraseMatcher for known skills and aliases, then adds
lightweight phrase mining from noun chunks when a full spaCy model is present.
The legacy regex keyword extractor can be enabled as an explicit fallback, but
is disabled by default because it is prone to broad false positives.
"""

import logging
from collections import Counter
from typing import Iterable, List, Tuple
import hashlib
from pathlib import Path

from django.conf import settings

from .nlp_pipeline import BUSINESS_SKILL_EXCLUDED_TERMS, SKILL_KEYWORDS, extract_skills

logger = logging.getLogger(__name__)


SKILL_ALIASES = {
    "artificial intelligence": ["ai", "generative ai", "gen ai"],
    "business intelligence": ["bi", "business intelligence", "dashboards", "dashboarding"],
    "communication": ["written communication", "verbal communication", "interpersonal communication"],
    "crm": ["customer relationship management"],
    "data analysis": ["data analyst", "analytical skills", "analysis"],
    "data analytics": ["analytics", "data insights", "insight generation"],
    "financial modelling": ["financial modeling", "financial models"],
    "google analytics": ["ga4", "google analytics 4"],
    "human resources": ["human resource management", "people management"],
    "machine learning": ["ml", "predictive modelling", "predictive modeling"],
    "microsoft office": ["ms office", "office suite"],
    "power bi": ["powerbi", "power-bi", "business intelligence dashboards"],
    "problem solving": ["troubleshooting", "analytical problem solving"],
    "project management": ["programme management", "program management"],
    "sql": ["t-sql", "sql server", "structured query language"],
    "stakeholder management": ["stakeholder engagement", "stakeholder relations"],
    "tableau": ["tableau dashboards"],
    "training and development": ["learning and development", "l&d"],
}

SKILL_HEAD_TERMS = {
    "accounting", "analysis", "analytics", "auditing", "budgeting", "communication",
    "compliance", "dashboard", "dashboards", "development", "forecasting", "leadership",
    "management", "marketing", "modelling", "modeling", "payroll", "programming",
    "recruitment", "reporting", "sales", "service", "statistics", "training",
}

TECHNICAL_CONTEXT_TERMS = {
    "api", "automation", "cloud", "code", "coding", "dashboard", "data",
    "database", "digital", "engineering", "model", "modelling", "modeling",
    "programming", "reporting", "software", "system", "technical", "technology",
}

SOFT_CONTEXT_TERMS = {
    "adaptability", "collaboration", "communication", "creative", "critical",
    "empathy", "leadership", "negotiation", "presentation", "problem",
    "stakeholder", "team", "teamwork", "time",
}

TECHNICAL_SKILL_TERMS = {
    "ai", "algorithm", "algorithms", "analytics", "api", "architecture", "automation",
    "azure", "aws", "bi", "c#", "c++", "cloud", "code", "coding", "css", "dashboard", "data",
    "database", "databases", "devops", "django", "engineering", "excel", "flask",
    "gcp", "html", "java", "javascript", "kubernetes", "machine", "model",
    "modelling", "modeling", "power", "programming", "python", "r", "reporting",
    "science", "security", "software", "sql", "systems", "tableau", "technical",
    "technology", "typescript",
}

SOFT_SKILL_TERMS = {
    "adaptability", "collaboration", "communication", "creative", "critical",
    "emotional", "empathy", "influence", "influencing", "interpersonal", "leadership",
    "mentorship", "negotiation", "presentation", "problem", "relationship",
    "stakeholder", "team", "teamwork", "time",
}

BUSINESS_SKILL_TERMS = {
    "accounting", "auditing", "budgeting", "change", "commercial", "compliance",
    "customer", "finance", "financial", "forecasting", "governance", "hr",
    "human", "ifrs", "labour", "management", "marketing", "operations", "payroll",
    "procurement", "recruitment", "risk", "sales", "service", "strategy", "tax",
    "training", "vendor",
}

TOOL_TIER_TERMS = {
    "aws", "azure", "bi", "c#", "c++", "css", "django", "excel", "gcp", "html",
    "java", "javascript", "kubernetes", "power", "python", "r", "salesforce",
    "sql", "tableau", "typescript",
}

METHOD_TIER_TERMS = {
    "agile", "analysis", "analytics", "auditing", "forecasting", "management",
    "modelling", "modeling", "reporting", "research", "strategy", "testing",
}


def token_set(*values):
    return set(" ".join(" ".join(str(value or "").lower().replace("-", " ").split()) for value in values).split())


def classify_skill_text(skill, mention_text="", context="", pattern="", source=""):
    tokens = token_set(skill, mention_text, context)
    phrase = " ".join(str(skill or "").lower().replace("-", " ").split())
    scores = {
        "technical": len(tokens & TECHNICAL_SKILL_TERMS),
        "soft": len(tokens & SOFT_SKILL_TERMS),
        "business": len(tokens & BUSINESS_SKILL_TERMS),
        "domain": 0,
    }
    if "problem solving" in phrase or "critical thinking" in phrase:
        scores["soft"] += 3
    if "project management" in phrase or "stakeholder management" in phrase or "change management" in phrase:
        scores["business"] += 2
        scores["soft"] += 1
    if "data" in tokens or "software" in tokens or "technical" in tokens:
        scores["technical"] += 2
    if "leadership" in tokens or "communication" in tokens or "teamwork" in tokens:
        scores["soft"] += 3
    if "management" in tokens and scores["technical"] == 0:
        scores["business"] += 1
    skill_type = max(scores.items(), key=lambda item: (item[1], item[0] != "domain"))[0]
    if scores[skill_type] == 0:
        skill_type = "domain"

    tier = "capability"
    if tokens & TOOL_TIER_TERMS or any(char in phrase for char in ["#", "+"]):
        tier = "tool"
    elif tokens & METHOD_TIER_TERMS:
        tier = "method"
    elif skill_type == "soft":
        tier = "transferable"
    elif source == "noun_chunk":
        tier = "candidate"
    elif len(tokens) >= 4:
        tier = "specialized"

    return {
        "skill_type": skill_type,
        "tier": tier,
        "scores": scores,
    }


class SpacySkillExtractor:
    def __init__(self):
        self.model_name = self._spacy_model_name()
        self.nlp = None
        self.matcher = None
        self.alias_lookup = {}
        self.phrase_lookup = {}
        self.patterns_by_label = {}
        self.backend = "regex"
        self._load_spacy()
        self._bert_ner = None
        self._bert_backend = None
        self._bert_min_confidence = float(getattr(settings, "BERT_SKILL_NER_MIN_CONFIDENCE", 0.65))
        self._regex_fallback_enabled = bool(getattr(settings, "SKILL_REGEX_FALLBACK_ENABLED", False))
        self._noun_chunk_mining_enabled = bool(getattr(settings, "SKILL_NOUN_CHUNK_MINING_ENABLED", False))
        self._load_bert_ner()

    def _spacy_model_name(self):
        custom_path = getattr(settings, "COURSE_SKILL_NER_MODEL_PATH", "")
        if custom_path and Path(custom_path).exists():
            return str(custom_path)
        return getattr(settings, "SPACY_MODEL_NAME", "en_core_web_sm")

    def _load_bert_ner(self) -> None:
        if not getattr(settings, "BERT_SKILL_NER_ENABLED", True):
            return
        bert_path = Path(getattr(settings, "BERT_SKILL_NER_MODEL_PATH", "models/bert_skill_ner"))
        if not bert_path.is_absolute():
            bert_path = Path(settings.BASE_DIR) / bert_path
        if not (bert_path / "skill_ner_meta.json").exists():
            return
        try:
            from transformers import pipeline as hf_pipeline
        except ImportError:
            logger.info('transformers not installed; BERT NER backend unavailable.')
            return
        try:
            self._bert_ner = hf_pipeline(
                "ner",
                model=str(bert_path),
                tokenizer=str(bert_path),
                aggregation_strategy="simple",
                device=-1,
            )
            self._bert_backend = str(bert_path)
            self.backend = f"{self.backend}+bert-ner"
            logger.info("BERT NER backend loaded from %s", bert_path)
        except Exception as exc:
            logger.warning("Could not load BERT NER model: %s", exc)
            self._bert_ner = None

    def _load_spacy(self) -> None:
        try:
            import spacy
            from spacy.matcher import PhraseMatcher
        except ImportError:
            logger.info("spaCy is not installed; using regex skill extraction fallback.")
            return

        try:
            self.nlp = spacy.load(self.model_name)
            self.backend = self.model_name
        except (OSError, MemoryError):
            logger.warning("spaCy model %s is unavailable; using blank English pipeline.", self.model_name)
            self.nlp = spacy.blank("en")
            self.backend = "spacy.blank.en"

        if "sentencizer" not in self.nlp.pipe_names and "parser" not in self.nlp.pipe_names:
            self.nlp.add_pipe("sentencizer")

        self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        patterns_by_label = {}
        for skill in SKILL_KEYWORDS:
            canonical = self._canonical(skill)
            if canonical in BUSINESS_SKILL_EXCLUDED_TERMS:
                continue
            patterns_by_label.setdefault(canonical, set()).add(skill)
        for canonical, aliases in SKILL_ALIASES.items():
            normalized = self._canonical(canonical)
            patterns_by_label.setdefault(normalized, set()).add(canonical)
            patterns_by_label[normalized].update(aliases)
        self.patterns_by_label = patterns_by_label

        for canonical, phrases in patterns_by_label.items():
            label = self._label(canonical)
            self.alias_lookup[label] = canonical
            clean_phrases = [phrase for phrase in phrases if phrase]
            for phrase in clean_phrases:
                self.phrase_lookup[self._canonical(phrase)] = canonical
            self.matcher.add(label, [self.nlp.make_doc(phrase) for phrase in clean_phrases])

        ruler_config = {"phrase_matcher_attr": "LOWER", "overwrite_ents": False}
        if "skill_entity_ruler" not in self.nlp.pipe_names:
            before = "ner" if "ner" in self.nlp.pipe_names else None
            ruler = self.nlp.add_pipe("entity_ruler", name="skill_entity_ruler", before=before, config=ruler_config)
            patterns = [
                {"label": "SKILL", "pattern": phrase, "id": canonical}
                for canonical, phrases in patterns_by_label.items()
                for phrase in phrases
                if phrase
            ]
            ruler.add_patterns(patterns)

    def _canonical(self, value: str) -> str:
        return " ".join(value.lower().replace("-", " ").split())

    def _label(self, value: str) -> str:
        return "SKILL_" + "".join(ch if ch.isalnum() else "_" for ch in value.upper())

    def _entity_id(self, canonical: str) -> str:
        slug = "".join(ch if ch.isalnum() else "-" for ch in canonical.lower()).strip("-")
        slug = "-".join(part for part in slug.split("-") if part)
        digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
        normalized_slug_source = "-".join(canonical.lower().split())
        if slug and slug != normalized_slug_source:
            return f"skill-{slug[:63]}-{digest}"
        if slug:
            return f"skill-{slug[:72]}"
        return f"skill-{digest}"

    def _chunk_id(self, document_id: str, canonical: str, start, end) -> str:
        raw = f"{document_id}|{canonical}|{start}|{end}"
        return "chunk-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _pos_signature(self, span) -> str:
        if not span:
            return ""
        tags = [token.pos_ or token.tag_ or "X" for token in span if not token.is_space]
        return " ".join(tags)

    def _phrase_pattern(self, span_or_text) -> str:
        if hasattr(span_or_text, "__iter__") and not isinstance(span_or_text, str):
            parts = []
            for token in span_or_text:
                if token.is_space:
                    continue
                if token.pos_:
                    parts.append(token.pos_)
                elif token.is_alpha:
                    parts.append("WORD")
                else:
                    parts.append("TOKEN")
            return " ".join(parts)
        tokens = str(span_or_text or "").split()
        return " ".join("WORD" if token.isalpha() else "TOKEN" for token in tokens)

    def _skill_type(self, canonical: str, mention_text: str) -> str:
        return classify_skill_text(canonical, mention_text)["skill_type"]

    def _skill_tier(self, canonical: str, mention_text: str, source: str) -> str:
        return classify_skill_text(canonical, mention_text, source=source)["tier"]

    def _context_for_span(self, span) -> str:
        if not span:
            return ""
        sent = getattr(span, "sent", None)
        if sent:
            return sent.text[:500]
        return ""

    def extract(self, text: str) -> List[str]:
        return sorted({entity["skill"] for entity in self.extract_entities(text)})

    def extract_entities(self, text: str, document_id: str = "") -> List[dict]:
        document_id = document_id or hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]
        if not text:
            return []
        if not self.nlp or not self.matcher:
            if not self._regex_fallback_enabled:
                return []
            return [
                {
                    "id": self._entity_id(self._canonical(skill)),
                    "chunk_id": self._chunk_id(document_id, self._canonical(skill), None, None),
                    "skill": skill,
                    "label": "SKILL",
                    "tier": self._skill_tier(skill, skill, "regex"),
                    "skill_type": self._skill_type(skill, skill),
                    "classification_scores": classify_skill_text(skill, skill)["scores"],
                    "pattern": "regex",
                    "pos_signature": "",
                    "text": skill,
                    "start": None,
                    "end": None,
                    "source": "regex",
                    "confidence": 0.72,
                    "mentions": [{"text": skill, "start": None, "end": None}],
                    "mention_count": 1,
                }
                for skill in extract_skills(text)
            ]

        doc = self.nlp(text)
        entities = {}

        def add_entity(skill, mention_text, start, end, source, confidence, span=None):
            canonical = self._canonical(skill)
            if not canonical:
                return
            if canonical in BUSINESS_SKILL_EXCLUDED_TERMS:
                return
            item = entities.setdefault(canonical, {
                "id": self._entity_id(canonical),
                "chunk_id": self._chunk_id(document_id, canonical, start, end),
                "skill": skill,
                "label": "SKILL",
                "tier": self._skill_tier(canonical, mention_text, source),
                "skill_type": self._skill_type(canonical, f"{mention_text} {self._context_for_span(span)}"),
                "classification_scores": classify_skill_text(canonical, mention_text, self._context_for_span(span), source=source)["scores"],
                "pattern": self._phrase_pattern(span or mention_text),
                "pos_signature": self._pos_signature(span),
                "text": mention_text,
                "start": start,
                "end": end,
                "source": source,
                "confidence": confidence,
                "mentions": [],
                "mention_count": 0,
            })
            item["mention_count"] += 1
            if len(item["mentions"]) < 5:
                item["mentions"].append({"text": mention_text, "start": start, "end": end})

        for ent in doc.ents:
            if ent.label_ == "SKILL":
                canonical = self._canonical(ent.ent_id_ or ent.text)
                add_entity(canonical, ent.text, ent.start_char, ent.end_char, "ner", 0.96, ent)


        if self._bert_ner:
            try:
                for bert_ent in self._bert_ner(text):
                    if self._is_skill_bert_entity(bert_ent):
                        score = float(bert_ent.get("score", 0.9))
                        if score < self._bert_min_confidence:
                            continue
                        mention = self._bert_word(bert_ent)
                        canonical = self._canonical(mention)
                        if not canonical:
                            continue
                        add_entity(canonical, mention, bert_ent.get("start"), bert_ent.get("end"), "bert-ner", score)
            except Exception as exc:
                logger.debug("BERT NER pass failed: %s", exc)

        for match_id, start, end in self.matcher(doc):
            canonical = self.alias_lookup[self.nlp.vocab.strings[match_id]]
            span = doc[start:end]
            add_entity(canonical, span.text, span.start_char, span.end_char, "phrase_matcher", 0.92, span)

        if self._regex_fallback_enabled:
            for skill in extract_skills(text):
                add_entity(skill, skill, None, None, "regex", 0.74)

        if self._noun_chunk_mining_enabled and doc.has_annotation("DEP"):
            for skill in self._noun_chunk_skills(doc):
                add_entity(skill, skill, None, None, "noun_chunk", 0.66)

        return sorted(entities.values(), key=lambda item: (item["skill"], item["id"]))

    def _noun_chunk_skills(self, doc) -> set:
        mined = set()
        for chunk in doc.noun_chunks:
            phrase = self._canonical(chunk.text)
            if not 2 <= len(phrase.split()) <= 4:
                continue
            canonical = self._known_skill_for_phrase(phrase)
            if canonical:
                mined.add(canonical)
                continue
            if any(term in phrase.split() for term in SKILL_HEAD_TERMS):
                mined.add(phrase)
        return mined

    def _known_skill_for_phrase(self, phrase: str):
        if phrase in self.phrase_lookup:
            return self.phrase_lookup[phrase]
        phrase_tokens = set(phrase.split())
        for known_phrase, canonical in sorted(self.phrase_lookup.items(), key=lambda item: len(item[0]), reverse=True):
            known_tokens = set(known_phrase.split())
            if known_tokens and known_tokens.issubset(phrase_tokens):
                return canonical
        return None

    def _is_skill_bert_entity(self, entity):
        label = str(entity.get("entity_group") or entity.get("entity") or "").upper()
        return label in {"SKILL", "B-SKILL", "I-SKILL"}

    def _bert_word(self, entity):
        return str(entity.get("word") or entity.get("text") or "").replace("##", "").strip()

    def build_skill_matrix(self, texts: Iterable[str]) -> List[Tuple[str, int]]:
        skills = []
        for text in texts:
            skills.extend(self.extract(text))
        return Counter(skills).most_common()
