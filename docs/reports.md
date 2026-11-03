# Reports

A report is a Python class that turns filters into typed rows. The browser shows it at
`/reports/<name>`: a filter bar, the rows, totals, an optional chart, Export (CSV) and Print.
The same report runs for Eva and MCP clients through the `run_report` tool.

```python
from evlet import Chart, Column, Date, Link, Report, Select, Sum

class InvoiceRegister(Report):
    title = 'Invoice register'
    roles = ('admin', 'editor', 'viewer')
    group = 'Sales'                        # the sidebar heading
    model = Invoice                        # its list page offers the report
    sortable = ('posting_date', 'total')   # columns people may sort by
    chart = Chart('bar', x='customer', y=('total',))

    customer = Link(Customer)              # filters are ordinary fields
    status = Select(['draft', 'sent'])
    from_date = Date(label='Posted from')

    columns = (Column('id', type='link', link='invoice', label='Invoice'),
               Column('customer', type='link', link='customer'),
               Column('posting_date', type='date', label='Posted'),
               Column('total', type='currency', total=True, unit='AED', source='invoice.total'))

    def run(self):
        filters = {'status': self.status} if self.status else {}
        page = self.context.list(Invoice, filters=filters, order_by=self.context.sort or ('-posting_date',),
                                 limit=self.context.limit, offset=self.context.offset)
        [everything] = self.context.aggregate(Invoice, measures={'total': Sum('total')}, filters=filters)
        return {'rows': [...], 'total': page['total'], 'totals': {'total': everything['total']}}

app = Application(database, auth=auth, reports=(InvoiceRegister,))
```

The Playground has three: **Invoice register** (a list with sorting and totals), **Invoices by
customer** (the register grouped by customer, with subtotals) and **Sales by customer** (one row
per customer, with a chart, run as it opens). See
`examples/playground/reports.py`.

## What a report declares

| Attribute | Default | Meaning |
| --- | --- | --- |
| `title` | the class name | The heading, the sidebar entry and the export file name |
| `roles` | `()` | Who may run it: `admin`, `editor`, `viewer`. `permissions` is an older alias |
| `autorun` | `False` | Run as soon as the filters are complete, and again after each change |
| `columns` | required | `Column` entries, in order |
| `group` | `''` | The sidebar heading it is listed under |
| `model` | `None` | A model whose list page shows a **Reports** menu with this report |
| `sortable` | `()` | Column names people may sort by |
| `chart` | `None` | A `Chart` drawn above the rows |
| `export` | `True` | Offer Export (CSV) |
| `group_by` | `None` | A column whose rows stay together, with a heading and a subtotal for each group |

These names (and `group_by`) cannot also be filter fields. Filters are the report's own fields: any field type,
with defaults, `required`, `Ref` filters on Links and `visible_if`, `required_if`, `read_only_if`
and `compute` rules. The server settles them as you type; editing a filter never runs a report
that is not `autorun`.

`Column(name, type='text', label=None, link=None, total=False, unit=None, source=None)`:

| Option | Meaning |
| --- | --- |
| `type` | `text`, `integer`, `decimal`, `currency`, `date`, `boolean` or `link`. Every cell is cleaned through it |
| `link` | For a `link` column, the model its values open (`'invoice'`). The cell shows the record's title |
| `total` | Total this number column. Needs `unit`, the one currency or unit all its values share |
| `unit` | A currency code (`'AED'`) prints as money; any other text follows the number |
| `source` | `'model.field'`: the column, its values and its total are left out for anyone who cannot read that field |

`Chart(type, x, y, label=None)`: `type` is `bar` or `line`, `x` a column for the labels and `y`
one to four number columns. It draws the rows on the current page.

## Running

`run()` reads `self.context.limit`, `self.context.offset` and `self.context.sort`, and returns
`{'rows': [...], 'total': n}` with at most `limit` rows. `context.sort` is `None` or a one-item
tuple such as `('-total',)`, ready for `context.list(order_by=...)`; it only ever names a column in
`sortable`. Rows hold declared column names only.

Totals: return `'totals': {column: value}` for every row, usually from `context.aggregate`, and
the response says `{"scope": "all"}`. Without it the server adds up the page (`{"scope": "page"}`),
and the view labels the row **Total, this page** when there is more than one page.

Groups: with `group_by`, `context.sort` always starts with the group column, then the person's
sort, for example `('customer', '-total')`, so each group's rows arrive together. Return
`'groups': [{'value': ..., 'count': n, 'totals': {column: value}}]` for every group, usually from
`context.aggregate(..., group_by=(...))`. The response keeps the groups on the current page
(`{"scope": "all", "items": [...]}`). Without them the server counts and totals the page's rows
(`"scope": "page"`). A person who cannot read the group column sees the rows ungrouped. The CSV
export stays one line per row.

Read through `self.context` (`list`, `aggregate`, `get`): it applies the person's role, row scope
and field rules. A value a report computes itself is the report author's responsibility; declare
`source` on its column when it comes from one field.

## Routes

| Route | Does |
| --- | --- |
| `GET /api/_reports` | The reports this person can run: filters, columns, `group`, `model`, `sortable`, `chart`, `export` (also in `/api/schema` as `reports`) |
| `POST /api/_reports/<name>/evaluate` | `{filters, revision}`: settles the filters without running. Returns `values`, `errors`, `states` and `rules` |
| `POST /api/_reports/<name>` | `{filters, limit (1 to 100), offset, sort}`: one page. Returns `rows`, `total`, `totals`, `groups` (with `group_by`), `filters`, `sort` |
| `POST /api/_reports/<name>/export.csv` | `{filters, sort}`: every row as CSV, up to 5,000 |
| `POST /api/_reports/<name>/prints/<design>` | `{filters}`: the report's PDF in a print design ([printing.md](printing.md), when installed) |

Every route checks the report's roles. `sort` must name a sortable column the person can read
(otherwise 422). The CSV is UTF-8 with a byte order mark, so spreadsheets read Arabic and other
scripts. Text that a spreadsheet would read as a formula (`=`, `+`, `-`, `@`) starts with an
apostrophe. When more than 5,000 rows match, a last line says how many were left out. It is sent
as an attachment with `nosniff` and no caching.

## In the browser

- **Reports** in the sidebar lists each report under its `group`; `/reports` shows them all as
  cards, and the command palette finds them. A model's list shows a **Reports** menu for reports
  with `model` set.
- The filter bar shows the report's fields. **Run** runs it; an `autorun` report runs as you
  change a filter. After a run, chips show the filters it ran with, and a note says when the
  filters changed since.
- The table pins the first column, sorts on the server by the sortable columns, and keeps 50 rows a
  page. **Columns** hides columns and the chart; drag a header to move a column. Both are kept per
  person on the device.
- The address holds the filters a report ran with, its sort and page:
  `/reports/InvoiceRegister?status=sent&sort=-total&page=2`. Opening it runs the report again, as
  the person opening it, so they see only their own rows. **Copy link** copies it.
- With `group_by`, each group gets a heading (its value and row count) and a subtotal row.
- **Save view** keeps the filters a report ran with and its sort under a name. Saved views show as
  chips above the filters; choosing one runs it again. They are kept per person on the device,
  up to 20 per report.
- **Export** downloads the CSV, with the filters the report ran with and its sort.
- **Print** shows when the person may print the report with a print design and the server has the
  print extra. The preview is the PDF from the filters the report ran with, never unsent edits.

## Eva and MCP

`list_reports` lists the reports a person can run, with their filters and columns. `run_report`
runs one page and adds `view`, the address that opens the same report in the browser
([tools.md](tools.md)).
