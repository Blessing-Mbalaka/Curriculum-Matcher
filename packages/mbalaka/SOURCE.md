# Source Snapshot

Mbalaka was extracted from:

```text
templates/dashboard/skill_vector_space.html
```

The reusable package keeps the same visual model:

- `job-root` and `course-root` nodes
- `skill` nodes
- `evidence` links between roots and skills
- `similarity` links between skills
- filters for source, skill, sector, skill type, job, course, and extractor
- JSON and CSV export helpers

The Django template remains the application-specific integration. The npm package is the framework-neutral copy intended for reuse.
