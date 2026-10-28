# Working on Evlet

Read `docs/specification.md` and `docs/architecture.md` before changing the public API.
The current scope is Milestone 4: local accounts, permissions, actions, child tables,
private attachments, transactions, and grouped layouts. Milestones 1 through 3 are
merged. Keep tenant routing, hosting, and Milestone 5 work separate.
Read `docs/business-capabilities.md` for the public APIs and permission boundaries.

- Use Python 3.12+ and Node 24. Python definitions are authoritative; frontend
  components consume a neutral schema and never appear in Python model definitions.
- Prefer the standard library. Justify every dependency. No mandatory Redis,
  Docker, external database, ORM, or background worker.
- Keep public APIs explicit, small, and readable. Use snake_case in Python,
  PascalCase for model and Vue component names. Do not create unused modules.
- `evlet/` contains the framework; `examples/playground/` is a consumer using only
  public exports; `frontend/` is the Vue/PrimeVue Playground; `tests/` covers the
  core, SQLite integration, and ASGI interface.
- Parameterize SQL values and validate SQL identifiers. Validate on the backend.
  Preserve transaction boundaries, exact currency values, and foreign keys.
- Keep the Playground authenticated and development servers on loopback. Backend
  permission checks cover all HTTP surfaces. Python sessions and action handlers
  are trusted code. Do not describe the application as production ready.
- Add meaningful tests for features and regressions. Run `.venv/bin/python -m
  pytest`, then `npm test` and `npm run build` in `frontend/`. Verify package build
  and installation when changing packaging. Never substitute mocks for SQLite.
- Update documentation and demonstrate applicable public APIs in the Playground.
  A feature is done only when its tests and all existing tests pass.
- Work on feature branches and prepare PRs; never merge automatically or push
  experimental implementation into `main`.
- Cloud tasks are already isolated. Use the existing checkout; create no Git
  worktree unless explicitly requested by the user.

See the README for complete installation, startup, and smoke-check commands.
