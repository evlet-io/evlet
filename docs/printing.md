# Print designs

A print design turns a record or a report into a PDF. It is ordered blocks in five regions,
rendered by WeasyPrint. Text in any block is a Jinja template, filled in a sandbox with every
value escaped, and any single block can be written as HTML instead.

Printing is an optional extra, so Evlet's core keeps no dependencies:

```bash
python -m pip install "evlet[print]"
```

WeasyPrint also needs pango 1.44 or later from the system: `brew install pango` on macOS,
`apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0` on Debian and Ubuntu. Without
the extra, designs still load and are checked, the schema reports `printing.available: false`,
and the print routes answer 503 `Printing is not installed on this server (pip install evlet[print])`.

## Define a design

```python
from evlet import Application, PrintDesign
from evlet.printing import Column, Fields, Footnote, Heading, PageNumber, Table, Text, Totals, Watermark

class TaxInvoicePrint(PrintDesign):
    model = SalesInvoice
    title = "Tax invoice"
    default = True
    currency = "AED"
    header = (Text("Sample Trading LLC · {{ doc.name }}", weight="bold"),)
    main = (
        Heading("Tax Invoice"),
        Fields(("name", "Invoice no."), "posting_date", "customer", columns=2),
        Table("lines", columns=(Column("item", width="55%"), Column("qty"), Column("rate"), Column("amount"))),
        Totals(("net_total", "Total excluding VAT"), ("vat_total", "VAT 5%"), ("grand_total", "Total")),
        Text(html="<p>Amount due: <b>{{ money(doc.grand_total) }}</b></p>"),
    )
    footnote = Footnote(Text("Received by ____________   Date ________"))
    footer = (PageNumber("Page {page} of {pages}", align="right"),)
    watermark = Watermark(text="DRAFT", size=55, opacity=0.08, show_if="doc.status == 'draft'")

app = Application(database, auth=auth, prints=[TaxInvoicePrint])
```

A design sets `model` (document data) or `report` (report data), never both. Designs are checked
when the application starts: tables, columns and fields must exist, a child table prints with its
document, and each model or report has at most one `default` design. The design's identity in
routes is its class name.

| Option | Default | Meaning |
| --- | --- | --- |
| `model` / `report` | | What the design prints |
| `title` | the class name | Shown in the Print menu; a Jinja template for the PDF title |
| `default` | `False` | The design the Print button opens first |
| `roles` | `("admin",)` | Report designs only: who may print it, among the report's roles |
| `size` | `"A4"` | `A3`, `A4`, `A5`, `Letter`, `Legal`, or a `("210mm", "297mm")` pair |
| `orientation` | `"portrait"` | Or `"landscape"` |
| `margins` | `("30mm", "14mm", "20mm", "14mm")` | Top, right, bottom, left. The header and footer sit in the top and bottom margins |
| `first_margin_top` | none | A different top margin on page 1 |
| `font_size` | `"9pt"` | The base size; blocks set their own with `size` |
| `currency` | none | The code `money()` uses when a value has none of its own |
| `date_format` | `"%d-%m-%Y"` | How dates print |
| `assets` | `{}` | Images by name (`{"logo.png": b"..."}`) for `Image` blocks and image watermarks |

## Regions

| Region | Attribute | Where |
| --- | --- | --- |
| Header | `header`, and `header_first` for page 1 only | Top margin of every page |
| Main | `main` | The page body, in order |
| Footnote | `footnote = Footnote(*blocks, place=...)` | Bottom of the content area |
| Footer | `footer` | Bottom margin of every page |
| Watermark | `watermark = Watermark(...)` | Behind the content of every page |

**Footnote.** `place="last_page"` (the default) snaps the blocks to the bottom of the page where the
main flow ends, whatever the content length. Space is taken only on that page. When the blocks do
not fit under the last content, they move to a new page whole, and the last block (or the last table
row) goes with them, so totals never print apart from them. `place="every_page", height="12mm"`
prints them above the footer on every page and reserves that height there. CSS has no
`@page :last`; the last-page footnote uses WeasyPrint's footnote area, measured in the tests at many
content lengths.

**Watermark.** One of `text=`, `image=` (an asset name or a `data:` URI) or `html=`. `size` is the
width in percent of the page (5 to 100), `opacity` 0.01 to 1, `rotate` -90 to 90 degrees, and
`show_if` an optional condition.

## Blocks

| Block | Prints |
| --- | --- |
| `Heading(text)` | A heading |
| `Text(text)` | A paragraph; line breaks are kept |
| `Fields(*names, columns=2)` | Label and value pairs. A name may follow a Link: `"customer.email"`. On a report design, the applied filters |
| `Table(source, columns=())` | A child table's rows (`"lines"`), or `"rows"` on a report design. The header repeats on every page and a row never splits. Without columns, every readable field |
| `Column(name, label=None, width=None, align=None, wrap=None)` | A table column. Numbers align right and never wrap |
| `Totals(*names)` | Right-aligned figures; the last is emphasized; never starts a page alone. On a report design, report column totals |
| `Image(src, width=None)` | A design asset or a `data:` URI |
| `Spacer(height="6mm")`, `PageBreak()` | Space, or a new page |
| `PageNumber(format)` | The page number, in the header or footer |

Names take `"field"` or `("field", "Label")`. Every block also takes:

| Option | Meaning |
| --- | --- |
| `html` | A Jinja template that replaces the block's markup. The block keeps its place, spacing and conditions |
| `show_if` | A Jinja expression; the block prints only when it is true |
| `align` | `left`, `center`, `right` or `justify` |
| `size`, `weight` | `"11pt"`, and `normal` or `bold` |
| `space_before`, `space_after` | Lengths such as `"4mm"` |
| `break_before` | `"page"` starts the block on a new page |

**Page numbers** use `{page}` and `{pages}`: `"Page {page} of {pages}"`, `"{page} of {pages}"`,
`"{page}/{pages}"`, `"Page {page}"`. `{0}` and `{1}` are the same placeholders. The rest of the
format prints as written.

## Data and templates

On a document design the template sees `doc`: the record's readable fields as Python values
(dates, decimals), each Link as the linked record's readable fields with its `title`, and each child
table as a list of rows. A `RichText` field prints formatted (headings, lists with checkboxes,
quotes, code, links) in `Fields` and in a template's `{{ doc.brief }}`: the stored HTML is sanitised
again as it prints and marked safe, so a template never turns other record text into markup.
Its images are the record's own attachments, embedded in the PDF; an image of another record never is.
`doc.name` and `doc.title` are always there: when the role cannot read
them, each is the record's id. On a report design it sees
`report`: `title`, `filters` (the applied values by name), `rows`, `columns`, `totals` and `total`.
Rows come from the report's own `run()`, page by page, up to 5000; a note says when more were left
out.

Helpers: `money(value, currency=None)` gives `AED 5,616.35`, `date(value)` gives `03-11-2026`,
`number(value, places=None)` and `ar(text)` (marks Arabic text right to left). They isolate text
direction, so dates and amounts read correctly inside Arabic. Noto Sans and Noto Sans Arabic are
bundled and embedded in every PDF.

What the server enforces:

- **Escaping.** Autoescape is on and the `safe` filter is removed: record data never becomes markup.
- **Fields.** The context is built for the person printing, from the same permission-filtered detail as
  an HTTP read, with the same row scope and Link checks. A field they cannot read, and a `hidden`
  field, is not in `doc`; it prints empty.
- **No network or files.** WeasyPrint may load only the bundled fonts, the design's `assets` and
  `data:` URIs. Every other URL, including `file:` and internal addresses, is refused.
- **Sandbox.** Templates run in Jinja's immutable sandbox.

## Permission

Printing is its own operation, `print`. Admin may print every model it can read. Another role
prints a model when its Rule names `print`, and still needs read access:

```python
policy = Policy(registry, rules={("editor", SalesInvoice): Rule(("read", "create", "update", "print"))})
```

Controls can take `print` away from a role like any other operation. Report designs are printed by
the roles in the design's `roles` that the report also allows.

## Routes

| Route | Does |
| --- | --- |
| `GET /api/_prints` | `{available, models, reports}`: the designs this person may print |
| `GET /api/{model}/{id}/prints/{design}.pdf` | The PDF inline; `?download=1` as an attachment named after `doc.name` |
| `POST /api/{model}/{id}/prints/{design}/file` | Render and keep it as an attachment of the record (update access, attachments enabled). 201 with the attachment |
| `POST /api/_reports/{report}/prints/{design}` | `{"filters": {...}}`: the report's PDF |

PDF responses use `application/pdf` and `nosniff`. An inline PDF carries
`Content-Security-Policy: default-src 'none'; frame-ancestors 'self'`, so only the application's own pages can
frame it (browsers do not show a sandboxed PDF); a download keeps `default-src 'none'; sandbox`.
The schema lists each model's designs the person may print under `prints` (left out when there are none),
and each report entry the same way. From trusted code, `context.print(TaxInvoicePrint, record_id)` returns the PDF bytes
with that context's user and permissions.

The Playground prints its invoices (`InvoicePrint`, with a DRAFT watermark on drafts) and the
**Invoices by status** report (`InvoicesByStatusPrint`); see `examples/playground/prints.py`.

## In the browser

A record view shows **Print** when the schema lists designs for the model (`prints`) and
`printing.available` is true. It opens a drawer with the PDF from
`GET /api/{model}/{id}/prints/{design}.pdf` in the browser's own PDF viewer, so the preview is
exactly what prints. With more than one design the drawer offers a choice, the default first.
Download saves the same PDF; File a copy appears when the model keeps attachments and the role may
update the record, and the Files block then lists the new PDF.

## The designer

Controls has a **Print designs** page (Build, Print designs) for roles that open Controls. It lists the
app's designs and the stored ones. An app's design is read only there; **Copy** makes a stored design
from it. A design has a name (letters and digits, starting with a capital), a title, what it prints
(a model or a report), the paper, its margins, and the five regions. In each region you add, move and
remove blocks and set their options; **Write this block as HTML** replaces one block's markup with a
Jinja template. The Watermark region takes text, an image or HTML with its size, opacity and angle.
The preview is the PDF the server renders for the unsaved design, half a second after each change,
against the first record of the model.

Stored designs are kept in `evlet_print_designs` as JSON, and every save and removal is written to
the audit log (area Print designs). Saving checks the design exactly as the application checks a
Python design, and refuses the name of an app's design. Images are kept in the design as
`data:image/...` URIs, at most 256 KiB in all. A stored design that no longer fits the app (a field was
removed) is left out of the Print menu and logged. Stored designs take the same `print` permission.

| Route | Does |
| --- | --- |
| `GET /api/_controls/prints` | Every design with its definition, and the models, tables, fields and reports to design with |
| `PUT /api/_controls/prints/{name}` | `{"definition": {...}}`: save a stored design |
| `DELETE /api/_controls/prints/{name}` | Remove a stored design |
| `POST /api/_controls/prints/preview` | `{"definition", "record"}` or `{"definition", "filters"}`: render without saving; answers `{url}` |
| `GET /api/_controls/prints/preview/{token}.pdf` | That PDF, for the same person, for ten minutes |

These routes take the usual Controls checks; saving and removing ask for a recent two-step code,
previewing does not.

## Not yet

Reports have no screen in this browser interface, so report designs print through their route.
Bulk printing many records into one PDF, barcodes, PDF/A, digital signatures and letterheads stored
per company are not built.
