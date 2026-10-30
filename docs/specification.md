# EVLET
## Framework Vision, Architecture and Development Specification

**Version:** 0.1

**Date:** 28 October 2026

**Repository:** https://github.com/evlet-io/evlet

**Development environment:** Codex Cloud

**Project status:** Initial framework development

---

# 1. Project Vision

Evlet is a lightweight, AI-native Python application framework for building modern business applications.

It is intended to provide the capabilities of a mature business application framework without the installation complexity, heavy dependencies, excessive abstraction or development overhead associated with larger frameworks.

Evlet should make it possible to create sophisticated applications using remarkably small amounts of Python code.

The framework should automatically handle common application requirements such as database persistence, forms, lists, APIs, validation, permissions, search and user interfaces.

The developer should concentrate on business logic rather than repeatedly implementing application infrastructure.

**The central objective is to minimize the amount of code, configuration, context and infrastructure needed to build and operate a complete business application.**

Evlet should be designed from the beginning for both human developers and AI coding agents.

## Intended applications

Evlet will eventually serve as the common foundation for products such as:

- Accounting and financial management software.
- ERP applications.
- CRM systems.
- HR and payroll applications.
- Recruitment systems.
- Inventory and purchasing applications.
- Industry-specific business applications.
- Multitenant SaaS products.

Evlet may eventually be released as an open-source framework, while applications built using it may remain proprietary.

It must remain a general-purpose framework rather than becoming tightly coupled to any individual product.

---

# 2. Non-Negotiable Design Principles

## 2.1 Minimal dependencies

Avoid external packages unless they provide substantial value.

Prefer Python's standard library wherever practical.

Every dependency must justify its presence through functionality, reliability or significant development savings.

Avoid introducing infrastructure dependencies that make installation or deployment complicated.

Do not require:

- Redis.
- Celery.
- Docker.
- Kubernetes.
- A separate message broker.
- A mandatory external database server.
- A complex build or deployment stack.

Optional adapters may support these technologies later when genuinely useful.

## 2.2 Python is the source of truth

Application definitions should live in ordinary Python files.

Avoid maintaining duplicate definitions across Python, JSON, YAML, JavaScript and database metadata.

A developer should be able to understand an application's structure by reading its Python code.

Python model definitions should provide enough information for Evlet to derive database structures, APIs, forms, lists, validation and other standard functionality.

## 2.3 Convention over configuration

The framework should provide intelligent defaults.

A developer should not have to specify properties that can be inferred safely.

For example, a field's type should inform its default form control, validation, display formatting, search behavior and serialization.

Explicit overrides must always remain possible.

## 2.4 Code-first, not no-code

Evlet is not intended to be a visual no-code application builder.

It is a developer framework that makes writing business applications substantially easier.

Visual customization and configuration tools may eventually be added, but Python code remains authoritative.

## 2.5 AI-native by design

Evlet should be optimized for AI-assisted development, not merely have AI features added later.

An AI agent should be able to create an application using concise framework instructions and a small number of model definitions.

Avoid requiring AI agents to understand thousands of lines of framework internals to perform ordinary development tasks.

Keep the public API consistent, predictable and compact.

## 2.6 Modular architecture

The core must not depend unnecessarily on optional components.

Database adapters, UI renderers, hosting adapters, background jobs, authentication providers and AI connectors should be independently replaceable.

## 2.7 Simple installation

A fresh machine or cloud development environment should be able to install and run a basic Evlet application with minimal commands.

A small application should not require a database server, cache server, process supervisor or other services merely to start.

## 2.8 No unnecessary jargon

Framework APIs, documentation and generated business application interfaces should use clear, understandable terminology.

Avoid introducing abstractions solely for architectural elegance.

---

# 3. Initial Technology Decisions

| Layer | Initial choice | Direction |
|---|---|---|
| Programming language | Python 3.12+ | Core framework |
| Backend protocol | ASGI | Portable HTTP application interface |
| Default database | SQLite | Built-in, low overhead |
| Additional database | PostgreSQL | Future adapter |
| Optional database | MariaDB | Consider if useful |
| Frontend framework | Vue 3 | Initial renderer |
| UI component library | PrimeVue | Fully external for v1 |
| Frontend build | Vite | Standard Vue tooling |
| Backend tests | pytest | Automated verification |
| Frontend tests | Vitest | Component and logic tests |
| Version control | GitHub | Permanent source of truth |
| Development | Codex Cloud | Primary environment |
| Deployment | VPS and serverless adapters | Both supported |
| Default cache | In-process / none | No mandatory Redis |
| Realtime | SSE | WebSockets optional |
| Multitenancy | Hostname-based | Separate tenant databases by default |

These are initial architectural choices. Additional dependencies should not be introduced without a clear reason.

---

# 4. Python Model System

The model system is the heart of Evlet.

A developer should be able to declare an application model using concise Python syntax.

Illustrative syntax:

```python
from evlet import Model, Text, Email, Currency, Link

class Customer(Model):
    name = Text(required=True)
    email = Email()
    credit_limit = Currency(default=0)

class Invoice(Model):
    customer = Link(Customer, required=True)
    posting_date = Date(default="today")
    total = Currency(readonly=True)
```

This is an example of the intended developer experience, not a finalized API.

The exact syntax must be designed carefully.

## Expected automatic capabilities

From a model definition, Evlet should eventually derive:

1. Database schema.
2. Schema migrations.
3. Create, read, update and delete operations.
4. API endpoints.
5. Validation.
6. Form layouts.
7. List views.
8. Filtering and sorting.
9. Search.
10. Relationships.
11. Field-level permissions.
12. Model-level permissions.
13. Serialization.
14. Standard UI actions.
15. Metadata for AI agents.

Developers should only need to override behavior when the defaults are insufficient.

## Model requirements

Support, progressively:

- Text and long text.
- Integer, decimal and currency.
- Date and datetime.
- Boolean.
- Select and enumerated values.
- Email, URL and phone.
- Relationships and references.
- Child tables and collections.
- File attachments.
- Computed and read-only fields.
- Defaults and validation.
- Unique and indexed fields.
- Optional custom field types.

Do not implement all field types in the first milestone.

Begin with the smallest useful subset and expand through tests.

---

# 5. Smart Defaults Engine

A major Evlet differentiator is the automatic generation of useful application behavior.

The framework should not require separate hand-written definitions for every form, list, API and search interface.

## Forms

Field definitions should automatically determine appropriate input components.

Examples:

- Text maps to a text input.
- Date maps to a date picker.
- Boolean maps to a checkbox or switch.
- Link maps to a searchable relationship selector.
- Currency maps to a properly formatted numeric input.
- Long text maps to a text area.

Fields should automatically respect required, read-only, hidden, default and validation properties.

The generated form should be usable without additional configuration.

## Lists

Every model should have a sensible default list view.

The framework should choose appropriate columns, provide sorting, filtering, pagination and search, and allow explicit customization.

Large datasets must use server-side querying rather than loading entire tables into the browser.

## APIs

Standard model operations should be available through predictable APIs.

Custom Python methods should be exposed deliberately, not automatically expose arbitrary internal functions.

Validation, permissions and tenant isolation must apply consistently across API access and application UI access.

## Search

Evlet should provide a standard search mechanism.

Basic search should work automatically for ordinary models.

More advanced indexing and full-text search can be introduced through optional capabilities.

## Permissions

Permissions must be enforced in the backend.

UI visibility is not a security boundary.

The permission system should support sensible defaults and explicit role, model, record and field restrictions.

## Overrides

Generated behavior should be customizable at several levels:

- Model definition.
- Field properties.
- View configuration.
- Custom Python methods.
- Renderer customization.
- Application theme.

Overrides should not require modifying Evlet's core source.

---

# 6. Compiler and Metadata Architecture

Evlet should use a compilation or definition-processing layer between Python application models and the frontend.

Conceptual flow:

**Python definitions → Model registry → Validated application metadata → UI schema → Vue/PrimeVue renderer**

The metadata layer should provide a compact, consistent representation of application behavior.

The frontend should not need to inspect arbitrary Python code.

The compiler should handle:

- Model registration.
- Field metadata.
- Relationship metadata.
- Validation rules.
- View definitions.
- API definitions.
- Permission metadata.
- UI schema generation.

The architecture must distinguish static definitions from runtime values.

Avoid unnecessary compilation steps or complex build infrastructure.

The first implementation can be deliberately small.

The exact metadata schema is still to be designed and should be versioned once established.

---

# 7. Frontend Architecture

## Decision: Vue 3 + PrimeVue

The first Evlet frontend will use Vue 3 and PrimeVue.

The user interface should rely on a complete external component library rather than developing an entire component system from scratch.

PrimeVue was selected as a strong candidate because of its breadth of business application components, particularly data tables, forms, dialogs, menus and other enterprise controls.

## Full customization

Evlet applications should support:

- Custom themes.
- Design tokens.
- CSS variables.
- Typography.
- Spacing.
- Colors.
- Component styling.
- Responsive layouts.
- Custom layouts.
- Application-level branding.

The objective is to deliver distinctive application interfaces, not generic PrimeVue demos.

## Renderer independence

**Application definitions must never depend directly on PrimeVue-specific APIs.**

Evlet should expose a neutral UI schema.

A Vue/PrimeVue renderer will translate that schema into components.

This allows Evlet to replace PrimeVue with an internally developed component library in the future without requiring applications to be rewritten.

The framework should eventually support different renderer implementations if necessary.

## Initial UI components

The first frontend should focus on:

- Application shell.
- Navigation.
- Forms.
- Lists and tables.
- Detail views.
- Dialogs.
- Notifications.
- Search.
- Loading and error states.

Avoid building a large design system in the initial milestone.

## UX philosophy

Evlet should enable interfaces that are:

- Minimal.
- Modern.
- Fast.
- Easy to navigate.
- Consistent.
- Highly customizable.
- Suitable for data-heavy business workflows.

Avoid excessive menus, unnecessary screens and cluttered dashboards.

---

# 8. Database Architecture

## SQLite first

SQLite is the default database because it provides:

- Very low installation overhead.
- No separate database server.
- Small deployment footprint.
- Reliable transactions.
- Excellent performance for many single-node applications.
- Straightforward per-tenant database isolation.

Enable appropriate SQLite features such as WAL mode where suitable.

Use short transactions and carefully designed indexes.

## Database abstraction

Evlet must not embed SQLite-specific assumptions throughout its model or application layers.

Introduce a small database abstraction that can eventually support PostgreSQL.

Do not build a large generic ORM unnecessarily.

The abstraction should be as simple as possible while supporting the framework's actual needs.

## PostgreSQL

PostgreSQL should become the preferred option when workloads require:

- Multiple application servers.
- Greater concurrent write capacity.
- Complex analytical queries.
- Large, heavily used ERP systems.
- More demanding transaction processing.

## MariaDB

MariaDB support may be considered later, especially for compatibility or migration use cases.

It is not required for the initial framework.

## SQLite limitations

SQLite supports multiple concurrent readers but serializes writes.

This does not automatically make it unsuitable for business applications.

Proper transaction design, indexing and workload characteristics matter.

However, Evlet must not claim that SQLite scales indefinitely.

The database adapter must make it possible to move demanding applications to PostgreSQL.

---

# 9. Scalability Targets

Evlet should be designed to support a wide range of application sizes.

Illustrative targets include:

- Small businesses with 5–50 employees.
- Medium businesses with 50–500 employees.
- Larger businesses with thousands of employees.
- Large ERP datasets containing millions of accounting or inventory records.
- SaaS platforms with approximately 1,000 tenant sites.
- Individual organizations with up to 10,000 employees, when backed by suitable infrastructure and database architecture.

These are design goals, not validated performance guarantees.

## Representative benchmark scenario

Use a realistic ERP-like benchmark with:

- 50 concurrent users.
- 10–20 invoices created per minute.
- Approximately 2 million general ledger entries.
- Approximately 500,000 stock ledger entries.
- Concurrent read and write operations.
- Financial reporting queries.
- Stock balance queries.
- Trial balance queries.

Benchmark SQLite and PostgreSQL using equivalent schemas and workloads when the PostgreSQL adapter is available.

## Important performance metrics

Measure:

- Request latency.
- Queries per second.
- Write throughput.
- Transaction duration.
- Concurrent request behavior.
- Memory usage.
- CPU usage.
- Database storage size.
- Installation footprint.
- Frontend bundle size.

Do not publish estimated performance numbers as measured results.

For accounting workloads, design proper indexes and consider summary or balance tables where justified.

---

# 10. Multitenancy

Hostname-based multitenancy is a central requirement.

Examples:

- `company1.example.com`
- `company2.example.com`
- `customer-owned-domain.com`

## Preferred initial model

Use a central tenant registry.

Resolve the tenant from a verified incoming hostname.

Associate each tenant with its database and configuration.

Prefer separate SQLite database files per tenant for the initial single-node architecture.

## Security requirements

- Never trust an unverified Host header.
- Prevent cross-tenant data access.
- Resolve tenant context before database access.
- Ensure background jobs preserve tenant identity.
- Keep tenant-specific files and configuration isolated.
- Prevent accidental cross-tenant queries.

## Future scalability

Support the possibility of:

- Multiple tenants per server.
- Multiple application servers.
- Tenant-specific PostgreSQL databases.
- Tenant migration between servers.
- Centralized management of large tenant fleets.

Do not implement complex distributed orchestration in milestone one.

---

# 11. Deployment Architecture

Evlet must support traditional servers and serverless environments.

## Traditional VPS deployment

A small VPS should be able to run Evlet with minimal infrastructure.

Preferred default direction:

- Python application.
- ASGI server.
- SQLite.
- Caddy.
- systemd.

Caddy is attractive because of automatic HTTPS certificate management.

Nginx and Apache should remain possible alternatives.

Avoid requiring Docker.

## DNS and certificates

Support:

- Subdomain-based tenants.
- Custom domains.
- Domain verification.
- Automatic HTTPS where supported.
- ACME-compatible certificate issuance.
- Certificate renewal.

Custom domain and certificate lifecycle management can be developed after the core is stable.

## Serverless deployment

Design portable adapters for environments such as:

- AWS Lambda.
- Google Cloud Run.
- Cloudflare-related infrastructure where compatible.
- Other Python-capable serverless environments.

Cloudflare Workers cannot be assumed to behave like an ordinary persistent Python server.

Runtime and database adapters must account for platform differences.

Avoid assuming a writable local filesystem or persistent process in serverless deployments.

## Storage

Use an abstract file-storage interface.

Initial local filesystem storage should eventually support object-storage adapters such as S3-compatible services and Cloudflare R2.

---

# 12. Background Jobs and Realtime

## No mandatory Redis

Redis must not be a required Evlet dependency.

The framework should work without a separate cache or queue service.

## Background jobs

For single-node deployments, explore a durable SQLite-backed job mechanism.

Requirements should eventually include:

- Scheduled jobs.
- Retries.
- Failure tracking.
- Tenant context.
- Recovery after process restart.
- Safe job claiming.
- Idempotency guidance.

Serverless environments should be able to use platform-native queue and scheduling adapters.

Do not build a sophisticated distributed queue in the initial milestone.

## Realtime

Preferred approach:

1. Server-Sent Events for ordinary one-way updates.
2. WebSockets when genuine bidirectional communication is required.
3. Polling fallback for environments that do not support persistent connections.

The event abstraction should remain independent of the underlying transport.

Avoid requiring a permanent socket connection for routine application functionality.

---

# 13. AI-Native Development

AI friendliness is a foundational requirement.

Evlet should minimize the tokens and context an AI coding agent needs to understand or modify an application.

## AI-oriented design principles

- Small, consistent public APIs.
- Predictable file organization.
- Concise model definitions.
- Automatic generation of repetitive functionality.
- Discoverable model metadata.
- Compact structured schemas.
- Clear error messages.
- Minimal configuration.
- Short, task-oriented documentation.

## AI integration

The framework should eventually expose an AI-friendly interface for:

- Discovering available models.
- Reading model schemas.
- Discovering permitted operations.
- Creating records.
- Querying records.
- Executing authorized application actions.
- Generating application definitions.
- Understanding validation failures.

Consider MCP and other LLM connector interfaces, but do not make a specific AI provider mandatory.

All AI actions must obey ordinary application permissions and tenant boundaries.

## AI-generated applications

An AI agent should eventually be able to create a functional application from a compact specification.

For example, defining a Customer model should automatically provide the database table, API, form, list and standard interactions.

The AI should only need to write code for behavior that cannot be inferred.

---

# 14. Testing Philosophy

Evlet is a framework, so testing must cover both the framework internals and real applications built using its public interfaces.

Testing is mandatory from the beginning.

## Testing layers

### Core unit tests

Test:

- Model registration.
- Field definitions.
- Defaults.
- Validation.
- Serialization.
- Relationships.
- Error handling.

### Database integration tests

Test:

- Table creation.
- Record persistence.
- Transactions.
- Rollbacks.
- Constraints.
- Schema changes.
- SQLite concurrency.
- Data integrity.

### API tests

Test:

- Standard CRUD operations.
- Validation failures.
- Filtering and pagination.
- Authentication.
- Authorization.
- Tenant isolation.

### Frontend tests

Test:

- Metadata-driven form rendering.
- Field-to-component mapping.
- Validation feedback.
- List rendering.
- User interactions.
- Loading and error states.

### Application integration tests

Build a small real application using Evlet's public APIs.

Use this to confirm that the framework is genuinely usable.

---

# 15. Evlet Playground

Create a permanent example application inside the repository.

Suggested initial business workflow:

**Customer → Invoice → Payment**

It should gradually demonstrate:

- Model definitions.
- Relationships.
- Generated forms.
- Generated lists.
- APIs.
- Database persistence.
- Transactions.
- Validation.
- Permissions.
- Basic business logic.

The Playground must not bypass framework APIs or use privileged internal shortcuts.

It should function as a realistic consumer of the framework.

Do not build a complete ERP for the initial Playground.

---

# 16. Performance Testing

Maintain reproducible benchmark datasets.

Suggested dataset levels:

| Level | Approximate GL entries | Purpose |
|---|---:|---|
| Tiny | 100 | Quick automated tests |
| Small | 10,000 | Functional integration |
| Medium | 100,000 | Performance regression |
| Large | 2,000,000 | ERP-scale stress testing |

The large dataset should not be generated on every commit.

Performance tests should cover:

- Record creation.
- Bulk insertion.
- Indexed searches.
- List pagination.
- Concurrent reads.
- Concurrent writes.
- Transaction posting.
- Trial balance queries.
- Stock balance queries.
- Database size.
- RAM and CPU consumption.

All benchmark results should include hardware, database configuration, dataset characteristics and test methodology.

---

# 17. Development Workflow

## Primary environment

Use Codex Cloud.

Local development should remain supported but must not be required.

## Source control

Repository:

https://github.com/evlet-io/evlet

Work on feature branches.

Use pull requests for review.

Do not push experimental changes directly into the main branch.

## Continuous integration

Use GitHub Actions to run:

- Python unit tests.
- Python integration tests.
- Frontend tests.
- Frontend production build.
- Basic package installation checks.

Add appropriate test matrices as the framework evolves.

## Agent instructions

Maintain an `AGENTS.md` file containing:

- Project philosophy.
- Architecture.
- Naming conventions.
- Dependency rules.
- Testing requirements.
- Repository structure.
- Coding conventions.
- Definition of done.

Codex should read these instructions before making changes.

## Definition of done

A framework feature is complete only when:

1. The implementation works.
2. Automated tests cover its expected behavior.
3. The Playground demonstrates its public API where applicable.
4. Documentation is updated.
5. Existing tests still pass.
6. No unnecessary dependencies were introduced.

---

# 18. Suggested Repository Structure

```text
evlet/
├── evlet/
│   ├── core/
│   ├── models/
│   ├── database/
│   ├── schema/
│   ├── api/
│   ├── permissions/
│   ├── web/
│   └── adapters/
├── frontend/
│   ├── src/
│   │   ├── renderer/
│   │   ├── components/
│   │   ├── layouts/
│   │   └── themes/
│   └── package.json
├── examples/
│   └── playground/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── performance/
├── .github/
│   └── workflows/
├── pyproject.toml
├── AGENTS.md
└── README.md
```

This structure is illustrative.

Do not create empty modules merely to match the directory tree.

Only introduce directories when they have actual responsibilities.

---

# 19. Development Roadmap

## Milestone 1: Executable foundation

Goal: A minimal, working framework that can be installed and tested in Codex Cloud.

Deliverables:

- Installable Python package.
- Minimal ASGI application.
- Health endpoint.
- SQLite connection handling.
- Basic model and field definitions.
- Model registration.
- Simple record persistence.
- Vue 3 frontend.
- PrimeVue integration.
- Basic frontend-to-backend connectivity.
- Small Playground example.
- pytest.
- GitHub Actions.
- README.
- AGENTS.md.

Do not implement the entire smart-default engine yet.

The milestone is successful when a fresh cloud environment can install Evlet, run tests and start the example application.

## Milestone 2: Model engine

Develop:

- Complete basic field types.
- Validation.
- Relationships.
- Query interface.
- Transactions.
- Model registry.
- Schema creation.
- Initial migration system.

## Milestone 3: Automatic application generation

Develop:

- Standard APIs.
- Neutral UI metadata schema.
- Automatic forms.
- Automatic lists.
- Filtering.
- Sorting.
- Pagination.
- Search.
- PrimeVue renderer.

## Milestone 4: Business application capabilities

Develop:

- Authentication.
- Roles.
- Permissions.
- Custom actions.
- Child tables.
- Attachments.
- Business transactions.
- More advanced layouts.

## Milestone 5: Multitenancy and deployment

Develop:

- Tenant registry.
- Hostname resolution.
- Tenant-specific databases.
- Custom domain verification.
- Server deployment.
- HTTPS automation.
- File storage abstraction.

## Milestone 6: Jobs, realtime and serverless

Develop:

- Background jobs.
- Scheduling.
- Event interface.
- SSE.
- Optional WebSockets.
- Serverless adapters.
- Object storage adapters.

## Milestone 7: AI-native interfaces

Develop:

- Compact model discovery.
- AI-readable metadata.
- Authorized action interfaces.
- MCP integration.
- AI development tooling.
- Documentation optimized for coding agents.

## Milestone 8: Performance and hardening

Develop:

- Large dataset benchmarks.
- Concurrency testing.
- Database adapter comparisons.
- PostgreSQL support.
- Tenant security tests.
- Performance optimizations.
- Production deployment documentation.

Milestones may overlap where necessary, but foundational correctness takes priority.

---

# 20. What Codex Must Not Do

Do not:

- Turn Evlet into a conventional heavyweight web framework.
- Add Redis as a required dependency.
- Introduce Celery or distributed infrastructure prematurely.
- Require Docker for development.
- Use an existing heavyweight ORM without architectural justification.
- Couple Python application definitions directly to PrimeVue.
- Build an entire ERP during initial framework development.
- Implement every planned feature in milestone one.
- Create unnecessary abstraction layers.
- Add dependencies merely for convenience.
- Use generated mock functionality as a substitute for real framework behavior.
- Claim performance or scalability without testing.
- Make irreversible architectural decisions without documenting their tradeoffs.
- Optimize for a single business application at the expense of framework generality.

---

# 21. Immediate Codex Instructions

The repository has been created privately at:

https://github.com/evlet-io/evlet

The immediate task is **Milestone 1 only**.

Before implementation:

1. Inspect the repository.
2. Review this specification.
3. Identify the smallest coherent architecture.
4. Document any significant architectural decisions.
5. Create a feature branch.

Then implement a minimal, executable foundation.

Use real SQLite persistence, not simulated storage.

Use real Vue 3 and PrimeVue integration, not a static HTML mockup.

Ensure the Playground exercises actual framework functionality.

Run all available tests and the frontend production build.

Fix failures before presenting the work.

Prepare a pull request with:

- Summary of implementation.
- Architecture overview.
- Dependencies introduced and reasons.
- Test results.
- How to run the Playground.
- Known limitations.
- Recommended next milestone.

Do not merge automatically.

---

# 22. Long-Term Success Criteria

Evlet should eventually enable a developer or AI coding agent to define a useful business application with a small amount of Python.

The framework should automatically provide most routine application infrastructure.

A small application should run comfortably on modest infrastructure.

Larger applications should be able to move to more capable database and hosting configurations without rewriting business logic.

The frontend should remain customizable and replaceable.

The framework should be easy to understand, install, extend, test and maintain.

**Evlet's success is not measured by how many features its core contains. It is measured by how much useful application functionality it can deliver with how little complexity.**
