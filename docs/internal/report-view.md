# Report view

Status: plan only, waiting for owner approval (03-11-2026). No framework code yet.

This plans a browser view for Python reports in Evlet's own UI framework, and how it prints
through Print Design. Everything here is framework-level and generic.

## 1. What exists

### The Python structure

`evlet/forms.py` defines reports. A report is a transient model: its fields are the filters,
`columns` declares the result, and `run()` returns one page.

```python
class SalesRegister(Report):                     # examples/amendment/reports/orders.py
    title = 'Sales Register'
    roles = ('admin', 'editor')                  # 'permissions' is an alias
    autorun = False                              # True: run as soon as the filters are valid
    company = Link(Company, required=True)       # filters are ordinary fields
    warehouse = Link(Warehouse, filters={'company': Ref('company')})
    columns = (Column('id', type='integer'), Column('name'), Column('total', type='currency'))

    def run(self):
        filters = {'company': self.company}
        if self.warehouse: filters['warehouse'] = self.warehouse
        page = self.context.list(SalesOrder, filters=filters, limit=self.context.limit, offset=self.context.offset)
        return {**page, 'rows': [...]}
```

What a report declares:

| Part | Shape |
| --- | --- |
| Filters | Any model fields (Link, Select, Date, ...), with defaults, `required`, `Ref` filters, computed and conditional rules. Evaluated by the same transient engine as dialogs and slips |
| `columns` | `Column(name, type, label, link, total, unit)`. Types: `text`, `integer`, `decimal`, `currency`, `date`, `boolean`, `link`. `total=True` only on numbers and needs a `unit` |
| `roles` | Subset of `admin`, `editor`, `viewer` |
| `autorun` | Boolean, required |
| `run()` | Returns `{'rows': [...], 'total': int}`. Rows may hold only declared columns, at most `limit` of them |

Routes (`Forms.dispatch`):

| Route | Does |
| --- | --- |
| `GET /api/_reports` | The catalog for this person: `name`, `label`, `fields` (filter schema), `behavior`, `sources`, `autorun`, `columns` |
| `POST /api/_reports/{name}/evaluate` | `{filters, revision}`: settles the filter form without running. Returns `values`, `errors`, `states` |
| `POST /api/_reports/{name}` | `{filters, revision, limit (1..100, default 50), offset}`: runs. Returns `rows`, `total`, `filters` (executed values), `limit`, `offset`, and `totals: {scope: 'page', values}` when a column has `total` |

The server cleans every cell through its column type. Read-only context. Unknown body keys
give 422; a role outside `roles` gives 403.

What it returns, then: typed rows, a row count, page totals and the executed filter values.
There is **no** sorting argument, no grouping or subtotals, no full-result totals, no chart,
no export, no sidebar placement and no per-column field permission.

Permissions today: report `roles`, then whatever `run()` reads. `context.list` applies the
model `read` permission, row scope and field permissions (unreadable fields are dropped from
the row). `context.aggregate` does the same for measures. A column the report computes
itself is not tied to any field rule.

Where reports are used:

- `examples/amendment/reports/orders.py`: `SalesRegister` and `LiveSalesRegister` (autorun).
- `tests/test_forms_services.py`: evaluate never runs, page bounds, roles and alias. Also
  `test_tools.py`, `test_tool_matrix.py`, `test_connector_hardening.py`, `test_routing.py`, `test_postgres.py`.
- Tool Engine: `list_reports` and `run_report` (`evlet/tools.py`), so Eva and MCP can already
  run reports, one page at a time. A read-only key may run them through the tool route.
- The Playground has **no** report on `main`. The Print Design branch adds
  `InvoicesByStatus` in `examples/playground/prints.py`.
- Docs: `docs/tools.md`, `docs/connector.md` (tools), `docs/ui.md` and `docs/routing.md`
  (the notice and the locked `/reports` prefix). `docs/business-capabilities.md`,
  `docs/finite-calculations.md`, `docs/meaning.md` and `docs/architecture.md` have no report
  section; they only use the word in passing.

The measures API (`Meta.measures`, `GET /api/{model}/aggregate`, `GET /api/{model}/{id}/measures`,
the `aggregate_records` tool) is the model-level stand-in the list page, boards, the readiness
report and the Eva plan used ("Reports: the existing report runtime (`list_reports`,
`run_report`) and the measures API (`aggregate_records`)", `docs/internal/eva-plan.md` item 7).

### Why the browser shows a notice

1. `dee691b` (01-11-2026) "Render reactive documents, dialogs and reports in the native UI"
   added `frontend/src/views/ReportView.tsx` in the SolidJS app: the filter form, Run, a list of
   rows, page totals as plain text, Previous and Next.
2. Phase 15 `ui-final/05-remove-solid` (`cf0ccda`, `3664b6b` "Serve the Evlet UI framework as the
   production frontend") removed the Solid app. `docs/internal/handoff-plan.md` records: "`/settings/accounts` and
   `/reports/<name>` show a notice", and "Kept and documented as not rendered: ... reports and form dialogs".
3. So the notice is a gap left by the rewrite, not a decision against reports. The notice text
   in `frontend/src/evlet/app/app.ts` says "Model lists, boards and measures cover the data in
   this interface."

### Earlier discussions found

- `docs/internal/amendment-v2-release-notes.md`: "Manual and autorun reports use the same
  transient field engine. Manual filter editing does not execute a report. Results and pagination
  retain the executed filters; optional totals explicitly cover the current page." And:
  "Report totals are page totals with declared units. Exports, pivots and charts are deferred."
  Phase P7 row: "Report definitions/routes and `ReportView.tsx`".
- `docs/internal/handoff-routing-controls-settings.md` and `docs/routing.md`: `/report`,
  `/dashboard`, `/search`, `/print` are "Reserved. 404 until built", owner "Framework (planned)".
  `/reports` is locked to the `evlet.reports` frontend.
- `docs/ui-design/README.md` and `reference/src/5-views.js` (the approved CRM look): a Reports
  page of chart panels (bar chart of a measure by period, a leaderboard, a stage funnel) with a
  range segment (30 days, 6 months, Year), a `BarChart` component ("Props: `series` (measure
  grouped by period). Appears in: Reports"), Reports tiles in quick access and `G R` to go there.
  This is a dashboard of measures, not a tabular report runner.
- `docs/internal/eva-plan.md` item 7 (quoted above).
- `docs/internal/model-engine-implementation-status.md`: "`Report.roles` is the documented name
  (`permissions` stays as an alias)".
- Print Design (`agent/print-designer`, `docs/printing.md`): a design sets `report = ...`; the
  template sees `report.title`, `filters`, `rows`, `columns`, `totals`, `total`; rows come from
  `run()` page by page up to 5000; route `POST /api/_reports/{report}/prints/{design}` with
  `{"filters": {...}}`; each report catalog entry gets `prints`.
- Shared session notes: nothing about a report view.

No earlier report view plan or mockup was found beyond these.

## 2. Plan

### Placement and navigation

- `/reports` lists the reports this person can run (cards with title, group and filter count).
  `/reports/<name>` opens one. Both already resolve to `evlet.reports`; only `/reports/<name>`
  is served today, so the index route is added.
- Sidebar: a **Reports** group under Documents and Records, listing reports by `Report.group`
  (new, optional text such as "Sales"). Hidden when the catalog is empty.
- A report may set `Report.model = SalesOrder` (new, optional). Its list page then shows a
  Reports menu with that report, and the report rows link back.
- The command palette lists reports. Recents include reports.

### Filter bar

Report filters are parameters (a typed form with defaults, required fields, `Ref` filters and
rules), not operator conditions, so the PR 82 builder (`filterBuilder`, `FilterPanel`) is not the
input. Reuse instead:

- The slip and transient form renderer for the fields, with `/evaluate` on every change, as today.
- `FilterChips` styling to show the **executed** filters above the table once a run finishes.
- The PR 82 date range presets (`timespans`) for Date filter pairs named `from_date`/`to_date`
  or declared as a range (see gaps).
- "Inputs changed. Run again" status when the edited filters differ from the executed ones.
  Autorun reports run on every valid change, debounced.

### Results table

Built on `DataTable` (`ui/table.ts`), which already has widths, order, hidden columns,
resizing and virtual rows.

| Feature | v1 | Backend |
| --- | --- | --- |
| Column types | Numbers right aligned, tabular figures; currency with its unit; dates DD-MM-YYYY; boolean as a check; `link` opens `/app/<model>/<id>` | `Column.link` must hold a model name; `link` cleaning uses `Integer` and must follow the record identity change |
| Frozen first column | Yes, sticky on horizontal scroll | None |
| Column chooser | Show, hide, reorder, width, per person through `prefs` | None |
| Sorting | Only on columns the report lists in `sortable` | New `sort` body key, passed to `run()` as `self.context.order_by` |
| Totals | A pinned footer row | `run()` may return `totals` for the whole result (`scope: 'all'`), usually from `context.aggregate`; without it the footer says "This page" |
| Group subtotals | v2 | `Report.group_by = 'column'`; `run()` returns rows ordered by it and `groups: [{value, count, totals}]` for the whole result |
| Paging | 50 rows a page, Previous and Next, page size up to 100 | None. No streaming in the view; export and print walk the pages |
| Empty and error states | Yes | None |

### Chart (optional)

`Report.chart = Chart('bar', x='month', y=('total',))` (new, `line` and `bar` only). Drawn
above the table from the rows of the current run, using the approved `BarChart` look.
A chart over the whole result needs `run()` to return `series`; v1 charts the page and says so.
Charts stay off unless the report declares one.

### Saved views and URL state

- The URL holds everything: `/reports/<name>?company=AE01&from_date=01-10-2026&to_date=31-10-2026&sort=-total&page=2`.
  Filters use their field names; Link filters use the record id. Opening the link fills the form,
  evaluates it, and runs if it is valid (autorun or a `run=1` flag), so a shared link shows the
  same view to anyone with the role, filtered by their own row scope.
- Column layout is per person in `prefs`, as on lists.
- Named presets (v2): per-person saved filter sets in `prefs`, shown as tabs like a list's saved
  views. Shared presets wait for a settings store decision.

### Export

`POST /api/_reports/{name}/export.csv` with `{filters, sort}`. Same role check, same `run()`,
page by page up to 5000 rows (the Print Design bound), UTF-8 with BOM, column labels as headers,
cells that start with `=`, `+`, `-` or `@` prefixed with `'`. `Content-Disposition: attachment`,
`nosniff`, no caching. A note row says when rows were left out. A report can turn export off
(`Report.export = False`). Controls can take `export` away from a role like `print`.

### Print

- A **Print** button appears when the catalog entry has `prints` and `printing.available` is true.
- It posts the **executed** filters (not unsaved edits) to
  `POST /api/_reports/{report}/prints/{design}` and shows the PDF in the same drawer the record
  print uses, with a design menu, Download, and a page count note.
- Coordinate with Print Design: the route should also take `sort` once sorting exists, and
  should accept the full-result `totals` from `run()` so print and view agree.

### Permissions

- Who runs: `roles`, as now, checked on every route (catalog, evaluate, run, export, print).
- Rows: whatever `run()` reads through `context.list` and `context.aggregate`, which apply row
  scope and field permissions.
- Fields: new optional `Column(source='SalesOrder.total')`. The server drops such a column, its
  values and its totals for a person who cannot read that field. Columns without a source are the
  report author's responsibility (trusted Python), as today.
- Links: a `link` cell opens only if the person can read that record; the record route already checks.

### Eva and MCP

Already there: `list_reports` and `run_report`. Add to the result a `view` path
(`/reports/<name>?...`) so Eva can answer "open it here". No export or print tool in v1.

## 3. Backend gaps, in order

1. Catalog: `group`, `model`, `sortable`, `chart`, `export`, `prints` (from Print Design).
2. `sort` argument, checked against `sortable`.
3. Full-result `totals` from `run()` (`scope: 'all'`), validated against total columns.
4. CSV export route.
5. `Column.source` field rule.
6. `link` columns: a model name, and text ids after the record identity change.
7. v2: `group_by` with group totals, chart `series`, presets.

## 4. Should a "list" print source exist?

Recommendation: **yes, after the report view**, as a built-in generic design rather than a
per-model class. "Print" on a model list prints the applied list filters and the visible columns
through `Policy.query_fields` and the same 5000-row bound, with the model's title and the filter
chips as the header. People expect to print a filtered list; requiring a Report class for each
model would push them to write boilerplate. A model may still declare its own list design later.

## 5. Phases

1. Report view: index, sidebar group, filters, table, paging, page totals, URL state, Print. Playground gets a report.
2. Sorting, full totals, CSV export, `Column.source`, column chooser.
3. Chart, group subtotals, presets, list print source.

## 6. Decisions for the owner

1. Placement: a Reports sidebar group by `Report.group` (recommended), or reports only under their model.
2. Totals: let `run()` return whole-result totals and show "This page" otherwise (recommended).
3. Sorting: server-side, opt in per column (recommended), or none in v1.
4. Export bound: 5000 rows like print (recommended) or higher with streaming.
5. List print source: build it in phase 3 (recommended) or not at all.
6. Charts: declared in Python, page data in v1 (recommended) or postponed.
