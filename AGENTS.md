# cite-audit

Local, single-user evidence-first BibTeX auditing workbench. Follow docs/implementation-plan.md. Do not mutate uploaded source files or silently conflate publication versions. Every factual suggestion needs evidence. Never commit secrets, uploads, or SQLite state. Live provider calls require explicit operator action; use mock mode for offline tests.

UI principle: “Avoid unnecessary secondary labels and descriptions. Let the layout do the explaining”.

Run `python -m unittest discover -s tests -v` before shipping. Use `python -m app` for the local application.
