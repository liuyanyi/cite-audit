# cite-audit

Local, single-user evidence-first BibTeX auditing workbench. Follow docs/implementation-plan.md. Do not mutate uploaded source files or silently conflate publication versions. Every factual suggestion needs evidence. Never commit secrets, uploads, or SQLite state. Live provider calls require explicit operator action; use mock mode for offline tests.

UI principle: “Avoid unnecessary secondary labels and descriptions. Let the layout do the explaining”.

Run `python -m unittest discover -s tests -v` before shipping. Use `python -m app` for the local application.

Frontend source of truth is React + TypeScript in `frontend/src/`, built with Vite. Do not hand-edit generated `app/static/` assets; run `npm ci`, `npm run typecheck`, `npm test`, and `npm run build` in `frontend/` after source changes. Keep the lockfile and generated assets in sync. The installed Python application serves these assets locally and does not require Node at runtime.
