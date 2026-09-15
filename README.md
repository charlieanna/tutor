# Tutor

Deterministic adaptive drill engine. **Packs are not in this repo.**

## Layout

- `engine/` — policy (selection, update, diagnosis, validation)
- `app/` — CLI, sim runner, verify harness, web UI
- `grader/` — env-gated rubric grader
- `tests/seed/` — synthetic pack + harness fixtures so `make test` runs on a public clone

Authored packs (system design, DSA, Go) live in a **private** sibling repo named `tutor-content`.

## Run with private data

```bash
# next to this checkout
git clone git@github.com:charlieanna/tutor-content.git ../tutor-content
make test
make verify
python3 app/cli.py drill 3
```

The engine looks for packs in this order: `TUTOR_PACKS`, `../tutor-content/packs`, `./content/packs`, then `tests/seed/packs`.

```bash
export TUTOR_PACKS=/path/to/tutor-content/packs
export TUTOR_VERIFY=/path/to/tutor-content/verify
```

Git history of this public repo still contains earlier pack files. Do not treat old commits as private.
