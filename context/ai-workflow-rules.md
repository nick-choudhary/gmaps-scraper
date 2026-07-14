# AI Workflow Rules

## Approach

Build incrementally using context-driven development. The six context files define what to build, how to build it, and the current state. Always implement against these specs — do not infer or invent behavior from scratch. When Google changes their API (field indices shift), update the constants in `parser.py` and the verified date, then re-run tests.

## Scoping Rules

- Work on one feature or fix at a time
- Prefer small, verifiable increments over large speculative changes
- Do not combine parsing changes with transport changes in the same commit
- A single feature unit = one thing a user can verify (e.g., "add pagination to grid search", not "improve grid search")

## When to Split Work

Split an implementation step if it combines:

- Parsing changes AND transport changes (different system boundaries)
- CLI changes AND parser changes (different interfaces)
- Multiple unrelated features (e.g., email extraction + review pagination)
- A change that touches more than 3 files in different directories

If a change cannot be verified end-to-end quickly (live search + `pytest`), the scope is too broad — split it.

## Handling Missing Requirements

- Do not invent product behavior not defined in the context files
- If a Google Maps field index changes, verify with the inspection script in AGENTS.md before updating
- If a requirement is ambiguous, resolve it in the relevant context file before implementing
- If a requirement is missing, add it as an open question in `progress-tracker.md`

## Protected Files

Do not modify the following unless explicitly instructed:

- `BASE_HEADERS` in transport.py (causes silent failures if wrong — see invariants)
- `parse_search_response()` control flow in parser.py (core extraction path)
- `pyproject.toml` build config (already working with hatchling)
- `.github/workflows/ci.yml` matrix (already covers all supported platforms)

## Keeping Docs in Sync

Update the relevant context file whenever implementation changes:

- Field indices changed → update `architecture.md` invariants and `AGENTS.md` field table
- New output field added → update `interface-context.md` output format section
- New anti-detection pattern → update `architecture.md` and `code-standards.md`
- Feature completed or started → update `progress-tracker.md`

## Before Moving to the Next Unit

1. `pytest tests/ -v` passes with no failures
2. `gmaps search "test" --lat 30.27 --lng -97.74 -n 1` returns live results
3. All Python files compile (`python -c "import ast; ..."`)
4. `progress-tracker.md` reflects the completed work
5. If architecture changed, `architecture.md` is updated

## When Google Changes the API

This WILL happen. Google Maps' internal `pb=` format changes periodically.

1. Run the inspection script (see AGENTS.md "When Google Changes the API")
2. Compare field indices against working sample at `scripts/raw_pb_response.txt`
3. Update constants in `parser.py`
4. Update mock data in `tests/test_parser.py` to match new indices
5. Run `pytest tests/ -v` to verify
6. Update the "last verified" date in the constant comment
7. Update `progress-tracker.md` session notes
