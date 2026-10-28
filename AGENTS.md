# Working on Evlet

Read `docs/specification.md` and `docs/architecture.md` before changing the public API.
The current scope is Milestone 1: an executable, small framework foundation.

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
- This milestone has no authentication, permissions, or tenant isolation. Bind
  development servers to loopback. Do not describe it as production ready.
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
