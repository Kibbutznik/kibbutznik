# Contributing to Kibbutznik

Thanks for thinking about it. Kibbutznik is a small project and friendly PRs move fast.

## Before you open a PR

1. **Run the tests.** `.venv/bin/pytest tests/ -x -q`. They should all pass on a clean checkout.
2. **Follow the design bias.** If your change is adding behavior a community could express as a rule (`Statement`) or a tunable (`Variable`), consider whether it belongs in the governance config rather than in Python.
3. **Keep the viewer honest.** Anything the agents do should be observable in the Big Brother viewer. If you add a new event, wire it into the live feed.
4. **No new external services without discussion.** We pay a hard cost for every new runtime dependency (Postgres, Ollama, FastAPI are already a lot). Open an issue first.

## How to run locally

See [README.md](./README.md#quick-start). The short version:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[agents]"
alembic upgrade head
uvicorn kbz.main:app --port 8000 &
python -m agents.run_with_viewer --backend ollama --model mistral-small:latest --rounds 0
```

## Filing issues

- **Bugs:** what you did, what you expected, what happened. Screenshots and a `simulation.log` excerpt help a lot.
- **Feature ideas:** a one-paragraph sketch is enough. Don't spend a weekend on a prototype before we've talked about the shape.
- **Security issues:** please **do not** open a public issue. Email `ops@kibbutznik.org` instead.

## Style

- **Python:** PEP-8-ish, black-compatible, type hints where they clarify. No mypy gate yet.
- **JavaScript (`app/app.js`):** plain ES2022 via Babel Standalone. No build step, no package.json. Keep it that way.
- **Commit messages:** one-line summary in imperative mood, then a paragraph or two explaining *why*. Past-tense OK in the body.

## Tests

- `tests/` is a pytest suite, async-aware via `pytest-asyncio`.
- Unit tests live next to the module they cover (`tests/test_<module>.py`).
- Integration tests talk to a throwaway Postgres DB; the harness spins one up per test run.
- If you add a new governance rule, add at least one test that exercises it end-to-end.

## License

By contributing you agree to license your contribution under [MIT](./LICENSE).

## Code of conduct

Be kind, be curious, assume good faith. If a community built on Kibbutznik wouldn't be proud of how you behave here, rethink it.
