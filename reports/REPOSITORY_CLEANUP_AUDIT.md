# Repository Cleanup Audit

Review date: 2026-11-09.

## Inventory result

- Maintained runtime code is under `controller/`; migrations are in
  `migrations/`; test code is in `tests/`; the only maintained script is
  `scripts/measure_load.py` (plus package initialization). The temporary
  microbenchmark helper used during optimization is absent.
- Benchmark reports, raw controller logs, samples, public grader reports and
  crash-recovery evidence are retained under `reports/`. They are useful
  submission evidence and were not deleted, rewritten or regenerated during
  this cleanup audit.
- No local `.db`, SQLite, private-key, or credential file is tracked. The
  public scenario files and `sim/` are assignment inputs; neither was edited.
- `.venv`, `runs/`, Python bytecode, pytest cache, and Ruff cache are local or
  generated/ignored. They were left untouched. In particular, no `runs/`
  contents were inspected because simulator run directories are outside the
  Controller's decision boundary and may contain scenario internals.
- `.env.example` contains only clearly labeled placeholders and is explicitly
  allowed by `.gitignore`; `.env` remains ignored. Compose binds its local
  PostgreSQL port to loopback and obtains credentials from environment
  interpolation.
- `requirements.txt` includes runtime/test dependencies for both Controller
  and bundled simulator commands. No dependency was removed: FastAPI/Uvicorn
  support the simulator server, PyYAML supports scenario loading, and NumPy is
  used by simulator sampling/scenario generation. Ruff is a separate development check and is
  documented as such. Dependency versions are lower-bounded rather than
  lock-file pinned; that reproducibility limitation is retained explicitly.
- No `PROJECT.md` or repository URL is present. `README.md` is the supplied
  assignment brief; `docs/ARCHITECTURE.md` records the actual architecture.

## Cleanup decision

No safe deletion was necessary. Ignored caches, run directories, virtualenv,
and all existing evidence were preserved. The stale runtime description in
README and the Chunk 9/10 status conflict in `PLAN.md` were corrected in the
documentation tasks. Remaining final submission materials are tracked in
`reports/SUBMISSION_CHECKLIST.md`.

