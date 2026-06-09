#!/usr/bin/env python
"""
Fine-tune a BERT/RoBERTa/DistilBERT model for SKILL Named Entity Recognition.

Pipeline
--------
1. Bootstrap Django (optional) to pull seed examples from the module DB.
2. Optionally generate synthetic examples with Gemini via --synthetic.
3. Convert character-level spaCy annotations to BIO token labels.
4. Fine-tune with Hugging Face Trainer.
5. Save model + tokenizer to --output.

The saved model can then be loaded in SpacySkillExtractor as a new
high-quality backend tier, replacing the spaCy CNN NER for skill extraction.

Usage
-----
# Minimal — seed data from Django DB only:
    python train_bert_ner.py

# With synthetic data augmentation:
    python train_bert_ner.py --synthetic --per-skill 8

# Larger base model, more epochs:
    python train_bert_ner.py --base-model roberta-base --epochs 15 --synthetic

# From a pre-saved seed JSON (no Django DB needed):
    python train_bert_ner.py --seed-json data/seed_examples.json --no-django

# Save synthetic examples for inspection / reuse:
    python train_bert_ner.py --synthetic --save-synthetic data/synthetic_ner.json

Requirements
------------
    pip install transformers datasets seqeval accelerate
"""

import argparse
import json
import logging
import os
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HuggingFace imports (fail early with a clear message)
# ---------------------------------------------------------------------------

try:
    from transformers import (
        AutoModelForTokenClassification,
        AutoTokenizer,
        DataCollatorForTokenClassification,
        Trainer,
        TrainingArguments,
    )
    from datasets import Dataset
    import numpy as np
    import evaluate
except ImportError as exc:
    sys.exit(
        f"Missing dependency: {exc}\n"
        "Install with:  pip install transformers datasets seqeval accelerate"
    )


# ---------------------------------------------------------------------------
# BIO label schema
# ---------------------------------------------------------------------------

LABELS = ["O", "B-SKILL", "I-SKILL"]
LABEL2ID: Dict[str, int] = {label: i for i, label in enumerate(LABELS)}
ID2LABEL: Dict[int, str] = {i: label for label, i in LABEL2ID.items()}


# ---------------------------------------------------------------------------
# Annotation alignment
# ---------------------------------------------------------------------------

def align_labels(
    tokenizer,
    text: str,
    entities: List[Tuple[int, int, str]],
    max_length: int = 512,
) -> Dict:
    """
    Tokenize *text* and align character-level entity spans to BIO token labels.

    Special tokens receive label -100 (ignored by the loss).
    For multi-subword tokens, only the first subword gets the B-/I- label;
    subsequent subwords get -100.
    """
    encoding = tokenizer(
        text,
        truncation=True,
        max_length=max_length,
        return_offsets_mapping=True,
    )
    offsets = encoding.pop("offset_mapping")
    entities_sorted = sorted(entities, key=lambda e: e[0])

    labels: List[int] = []
    prev_entity_start: Optional[int] = None

    for tok_start, tok_end in offsets:
        # Special token (CLS / SEP / PAD) — ignore in loss
        if tok_start == tok_end:
            labels.append(-100)
            continue

        matched_label = "O"
        for ent_start, ent_end, ent_tag in entities_sorted:
            if tok_start >= ent_start and tok_end <= ent_end:
                if tok_start == ent_start:
                    matched_label = f"B-{ent_tag}"
                    prev_entity_start = ent_start
                elif prev_entity_start is not None and tok_start > prev_entity_start:
                    # Continuation subword of the same entity
                    matched_label = f"I-{ent_tag}"
                else:
                    # Continuation subword — ignore in loss so it doesn't confuse
                    matched_label = None
                break

        if matched_label is None:
            labels.append(-100)
        else:
            labels.append(LABEL2ID.get(matched_label, LABEL2ID["O"]))

    encoding["labels"] = labels
    return dict(encoding)


def examples_to_dataset(
    examples: List[Tuple[str, dict]],
    tokenizer,
    max_length: int = 512,
) -> Dataset:
    """Convert (text, annotations) pairs to a HuggingFace Dataset."""
    rows = []
    skipped = 0
    for text, annotations in examples:
        entities = [
            (int(s), int(e), tag)
            for s, e, tag in annotations.get("entities", [])
        ]
        if not text.strip():
            skipped += 1
            continue
        try:
            row = align_labels(tokenizer, text, entities, max_length=max_length)
            rows.append(row)
        except Exception as exc:
            logger.debug("Skipped example due to error: %s", exc)
            skipped += 1

    if skipped:
        logger.warning("Skipped %d examples during tokenization.", skipped)
    logger.info("Dataset size: %d examples.", len(rows))
    return Dataset.from_list(rows)


# ---------------------------------------------------------------------------
# seqeval metric
# ---------------------------------------------------------------------------

def build_compute_metrics(id2label: Dict[int, str]):
    metric = evaluate.load("seqeval")

    def compute_metrics(eval_pred):
        logits, label_ids = eval_pred
        predictions = np.argmax(logits, axis=-1)

        true_labels = []
        pred_labels = []
        for pred_seq, label_seq in zip(predictions, label_ids):
            true_row, pred_row = [], []
            for p, l in zip(pred_seq, label_seq):
                if l == -100:
                    continue
                true_row.append(id2label[l])
                pred_row.append(id2label[p])
            true_labels.append(true_row)
            pred_labels.append(pred_row)

        results = metric.compute(predictions=pred_labels, references=true_labels)
        return {
            "precision": results["overall_precision"],
            "recall": results["overall_recall"],
            "f1": results["overall_f1"],
            "accuracy": results["overall_accuracy"],
        }

    return compute_metrics


# ---------------------------------------------------------------------------
# Django bootstrap helpers
# ---------------------------------------------------------------------------

def _bootstrap_django() -> bool:
    """Set up Django settings so we can call ORM models."""
    manage = Path(__file__).parent / "manage.py"
    if not manage.exists():
        return False
    if "DJANGO_SETTINGS_MODULE" not in os.environ:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        import django
        django.setup()
        return True
    except Exception as exc:
        logger.warning("Django bootstrap failed: %s", exc)
        return False


def _load_db_examples() -> List[Tuple[str, dict]]:
    """Pull seed examples from the module DB via collect_training_examples."""
    try:
        from analysis.course_skill_ner import collect_training_examples
        examples, skipped = collect_training_examples(reviewed_only=False)
        logger.info("Loaded %d seed examples from DB (%d skipped).", len(examples), skipped)
        return examples
    except Exception as exc:
        logger.warning("Could not load DB examples: %s", exc)
        return []


def _load_json_examples(path: str) -> List[Tuple[str, dict]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    examples = [(item["text"], {"entities": [tuple(e) for e in item["entities"]]}) for item in raw]
    logger.info("Loaded %d examples from %s.", len(examples), path)
    return examples


# ---------------------------------------------------------------------------
# Training entry point
# ---------------------------------------------------------------------------

def train(
    base_model: str = "distilbert-base-uncased",
    output: str = "models/bert_skill_ner",
    epochs: int = 10,
    batch_size: int = 16,
    learning_rate: float = 2e-5,
    warmup_ratio: float = 0.1,
    dev_ratio: float = 0.15,
    max_length: int = 512,
    seed: int = 42,
    # Data sources
    seed_examples: Optional[List[Tuple[str, dict]]] = None,
    synthetic: bool = False,
    per_skill: int = 6,
    skills: Optional[List[str]] = None,
    save_synthetic: Optional[str] = None,
) -> None:
    random.seed(seed)
    np.random.seed(seed)

    # ---- collect examples ----
    all_examples: List[Tuple[str, dict]] = list(seed_examples or [])
    logger.info("Seed examples: %d", len(all_examples))

    if synthetic:
        logger.info("Generating synthetic examples with Gemini...")
        try:
            from analysis.synthetic_data_gen import generate_synthetic_examples, save_examples
            syn = generate_synthetic_examples(
                skills=skills,
                per_skill=per_skill,
                progress_callback=logger.info,
            )
            logger.info("Generated %d synthetic examples.", len(syn))
            if save_synthetic:
                save_examples(syn, save_synthetic)
                logger.info("Synthetic examples saved to %s", save_synthetic)
            all_examples.extend(syn)
        except Exception as exc:
            logger.warning("Synthetic generation failed: %s", exc)

    if not all_examples:
        sys.exit(
            "No training examples found.\n"
            "Options:\n"
            "  1. Run with Django DB available (it reads from module skill entities).\n"
            "  2. Pass --seed-json path/to/examples.json\n"
            "  3. Add --synthetic to generate examples with Gemini."
        )

    logger.info("Total examples before dedup: %d", len(all_examples))

    # Deduplicate by text
    seen_texts = set()
    deduped = []
    for text, ann in all_examples:
        if text not in seen_texts:
            seen_texts.add(text)
            deduped.append((text, ann))
    logger.info("After dedup: %d examples.", len(deduped))

    # Train / dev split
    random.shuffle(deduped)
    dev_size = max(1, int(len(deduped) * dev_ratio)) if len(deduped) > 1 else 0
    dev_data = deduped[:dev_size]
    train_data = deduped[dev_size:]
    logger.info("Train: %d  Dev: %d", len(train_data), len(dev_data))

    if len(train_data) < 5:
        sys.exit(
            f"Only {len(train_data)} training examples — too few to fine-tune.\n"
            "Add more reviewed module skill entities or use --synthetic."
        )

    # ---- tokenizer + model ----
    logger.info("Loading tokenizer and model: %s", base_model)
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    model = AutoModelForTokenClassification.from_pretrained(
        base_model,
        num_labels=len(LABELS),
        id2label=ID2LABEL,
        label2id=LABEL2ID,
        ignore_mismatched_sizes=True,
    )

    # ---- datasets ----
    train_dataset = examples_to_dataset(train_data, tokenizer, max_length)
    dev_dataset = examples_to_dataset(dev_data, tokenizer, max_length) if dev_data else None

    # ---- training arguments ----
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(output_path),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        learning_rate=learning_rate,
        warmup_ratio=warmup_ratio,
        weight_decay=0.01,
        eval_strategy="epoch" if dev_dataset else "no",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=bool(dev_dataset),
        metric_for_best_model="f1" if dev_dataset else None,
        seed=seed,
        logging_steps=20,
        report_to="none",
    )

    collator = DataCollatorForTokenClassification(tokenizer)

    trainer_kwargs = {
        "model": model,
        "args": training_args,
        "train_dataset": train_dataset,
        "eval_dataset": dev_dataset,
        "data_collator": collator,
        "compute_metrics": build_compute_metrics(ID2LABEL) if dev_dataset else None,
    }
    trainer_init_params = Trainer.__init__.__code__.co_varnames
    if "processing_class" in trainer_init_params:
        trainer_kwargs["processing_class"] = tokenizer
    else:
        trainer_kwargs["tokenizer"] = tokenizer

    trainer = Trainer(**trainer_kwargs)

    # ---- train ----
    logger.info("Starting training...")
    trainer.train()

    # ---- save ----
    logger.info("Saving model to %s", output_path)
    trainer.save_model(str(output_path))
    tokenizer.save_pretrained(str(output_path))

    # Write a metadata file so SpacySkillExtractor knows what's inside
    meta = {
        "base_model": base_model,
        "labels": LABELS,
        "label2id": LABEL2ID,
        "id2label": {str(k): v for k, v in ID2LABEL.items()},
        "train_examples": len(train_data),
        "dev_examples": len(dev_data),
        "epochs": epochs,
    }
    (output_path / "skill_ner_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    logger.info("Training complete. Model saved to: %s", output_path)

    # ---- final eval ----
    if dev_dataset:
        logger.info("Running final evaluation on dev set...")
        metrics = trainer.evaluate()
        logger.info("Dev metrics: %s", metrics)
        print("\nFinal dev metrics:")
        for k, v in metrics.items():
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fine-tune BERT/RoBERTa for SKILL NER.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--base-model",
        default="distilbert-base-uncased",
        help="HuggingFace model ID to fine-tune from.",
    )
    parser.add_argument(
        "--output",
        default="models/bert_skill_ner",
        help="Directory to save the trained model and tokenizer.",
    )
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5, help="Learning rate.")
    parser.add_argument("--dev-ratio", type=float, default=0.15)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)

    # Data source flags
    parser.add_argument(
        "--seed-json",
        default=None,
        help="Path to a JSON file of seed examples (from save_examples). "
             "If omitted, tries to pull from Django DB.",
    )
    parser.add_argument(
        "--no-django",
        action="store_true",
        help="Skip Django DB bootstrapping entirely.",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Generate additional synthetic examples with Gemini.",
    )
    parser.add_argument(
        "--per-skill",
        type=int,
        default=6,
        help="Sentences to request per skill from Gemini.",
    )
    parser.add_argument(
        "--save-synthetic",
        default=None,
        help="If set, save synthetic examples to this JSON path for inspection / reuse.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # ---- load seed examples ----
    seed_examples: List[Tuple[str, dict]] = []

    if args.seed_json:
        seed_examples = _load_json_examples(args.seed_json)
    elif not args.no_django:
        if _bootstrap_django():
            seed_examples = _load_db_examples()
        else:
            logger.warning(
                "Django bootstrap failed. Use --seed-json or --no-django. "
                "Continuing with synthetic-only data if --synthetic is set."
            )

    train(
        base_model=args.base_model,
        output=args.output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        dev_ratio=args.dev_ratio,
        max_length=args.max_length,
        seed=args.seed,
        seed_examples=seed_examples,
        synthetic=args.synthetic,
        per_skill=args.per_skill,
        save_synthetic=args.save_synthetic,
    )
