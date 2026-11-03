# Print output: design

Status: decided by the owner on 03-11-2026 (section 9); being built. The spike below was
run on 03-11-2026 against WeasyPrint 70.0 (Python 3.12, pango 1.56.3, Apple M3).

Evlet prints records and reports to PDF through WeasyPrint. A **Print Design** describes how one
model or report prints, as ordered blocks in five regions. Any single block can be switched to HTML;
there is no whole-document HTML mode. The preview is the real PDF from the server.

## 1. Spike results

The spike rendered a generic tax invoice (fictional company and customer, AED, 5% VAT, DD-MM-YYYY,
Arabic names) and then measured the layout boxes WeasyPrint produced, page by page, instead of
only looking at the pictures. Screenshots and the designer mockup are in the design artifact,
https://claude.ai/artifact/4TVw1AMx9aGpVhmaFj17uK. The spike sources and PDFs are kept outside the
repository with the session notes (`print-spike/`); `build.py` reruns everything in about 90 s.

| # | What was tested | Verdict |
|---|---|---|
| 1 | Overflow: 1-page invoice (8 lines), 9-page invoice (280 lines) | Works. Long descriptions wrap; a 70-character serial with no spaces breaks inside its cell. Nothing is clipped. |
| 2 | Page breaks | Works. `thead` repeats on every page. Rows never split (`break-inside: avoid`). Totals never start a page alone (`break-before: avoid` pulls the last row along). `break-before: page` forces the terms onto a new page. Orphans and widows of 3 hold in the terms. |
| 3 | Running header and footer | Works. Full letterhead on page 1 (`@page :first`), a compact header on later pages, footer with TRN and document number from `string-set`, and "Page X of Y" from `counter(page)` and `counter(pages)`. |
| 4 | Content snapped to the bottom of the last page (tested with a signing block) | Works with the footnote area, tested at every length from 1 to 80 lines (1 to 3 pages): 80 of 80 correct. Details below. |
| 5 | Arabic and RTL | Works. Shaping is correct in Geeza Pro, Tahoma, Arial and Al Nile; RTL paragraphs and RTL tables lay out correctly. One bidi trap found and fixed (dates). |
| 6 | Speed | 1 page: about 190 ms. 9 pages: about 1.04 s. Template time is 2 ms or less; the rest is layout and PDF writing. |

### The last-page area: the four approaches

CSS has no `@page :last`, so "only on the last page" has to come from the flow. Each approach was
rendered at many lengths and checked for: on the totals page, flush with the bottom of the content
area, in one piece, not over other content, and no space reserved on other pages.

| Approach | Result | Why |
|---|---|---|
| (a) Running element, `position: running(signoff)` with `element()` in `@bottom-center` | 0 of 20 | It does appear only from the page where it occurs, but a margin box needs a fixed `@page` margin, so the 44 mm band is reserved on every page. Margin boxes beside it get squeezed (the page number wrapped). A sign-off taller than the margin would be cut. |
| (b) Footnote area, `float: footnote` | **80 of 80** | The footnote area exists only on the page that holds the call, it sits at the bottom of the content area above the footer, and it is never split. |
| (c1) `position: absolute; bottom: 0` | 17 of 20 | Lands on the right page, but reserves nothing, so when the last page is full it is drawn over the totals. |
| (c2) Normal flow plus a measured spacer (two layout passes) | 80 of 80 | Render once, measure the free space above the sign-off on its page, insert a spacer of exactly that height, render again. Costs a second layout (9 pages: about 1.1 s). |
| (c3) Flex column, `margin-top: auto` | 0 of 20 | `height: 100%` is one page tall. On one page it does not reach the bottom; past one page, every row after page 1, the totals and the sign-off were **silently dropped** (15 of 20 cases). |

The footnote approach needs two details, both found in the spike:

- `footnote-policy: block` must be set **on the sign-off itself**, and its call must sit **inside a
  line** (the last paragraph of the closing block). Then, when the sign-off does not fit, that
  paragraph moves to the next page with it, and the totals follow because the closing block avoids
  breaks. Set on the parent block, or with the call between blocks, WeasyPrint 70 moved only the
  sign-off and left the totals behind (10 of 80 failed).
- The footnote box ignores `display: flex` on itself. Put the flex layout on an inner element.

```css
.signoff { float: footnote; footnote-display: block; footnote-policy: block; text-align: left; }
.signoff::footnote-call, .signoff::footnote-marker { content: none; }
@page { @footnote { margin-top: 6mm; } }
.closing { break-inside: avoid; break-before: avoid; }
```

```html
<section class="closing">
  <table class="totals">...</table>
  <div class="words">Amount due: AED 5,616.35<div class="signoff"><div class="signoff-body">...</div></div></div>
</section>
```

When a print design has a forced break after the totals (terms on the next page), the sign-off stays at the
bottom of the totals page, which is what a signed invoice needs. Designs that put content after the
totals can choose "last page of the document" instead; that uses the two-pass method (c2).

### Other findings

- **Escaping breaks inline CSS.** With autoescape on, quotes in a `<style>` block became `&#34;` and
  every font declaration was dropped without an error. The stylesheet must go to WeasyPrint as
  `stylesheets=[CSS(...)]`, never through the template.
- **Numbers must never wrap.** `overflow-wrap: anywhere` on a narrow `#` column broke `100` into
  `10` and `0`. Numeric cells get `white-space: nowrap`; only text cells break long words. The
  designer must warn when a numeric column is too narrow for its widest value.
- **A row taller than a page** cannot avoid a break. WeasyPrint moved it to a fresh page (leaving
  page 1 mostly empty), split it, and did **not** repeat the table header on the pages it continued
  onto. Long text belongs in a Text block, not in a table cell; the print check flags such rows.
- **Arabic dates reverse.** In an Arabic sentence, `03-11-2026` displays as `2026-11-03`, because
  digits after Arabic letters become Arabic numbers and the hyphens between them stay neutral.
  `<bdi dir="ltr">03-11-2026</bdi>` fixes it. The date, money and number helpers always isolate.
- **Fonts differ by machine.** macOS has Geeza Pro and Helvetica Neue; a Linux server usually has
  neither, and fontconfig falls back silently. Arial's Arabic rendered noticeably smaller. Print designs
  should name bundled fonts loaded with `@font-face`, so output is the same everywhere.
- **Layout can be measured.** WeasyPrint's `document.pages[n]._page_box` gives every box's position.
  The spike used it to check the sign-off automatically. The same idea powers the print check in
  section 4. It is a private attribute, so the framework must pin the WeasyPrint version and test it.
- **Size.** PDFs were 35 KB (1 page) and 93 KB (9 pages) with fonts subset and embedded.

## 2. Naming

The owner chose **Print Design**. The base class is `PrintDesign`; each design is named after what it
prints with a `Print` suffix: `class TaxInvoicePrint(PrintDesign)`, `class SalesReportPrint(PrintDesign)`.
In prose it is "a print design"; the shorter "print" is used where the context is clear (`prints=[...]`,
the `prints` routes, the `print` permission). "Print Format" (Frappe's term), "Layout" (Slips and layout
metadata) and "Template" were avoided.

| Term | Meaning |
|---|---|
| Print design | How one model or report prints: regions of blocks, page size, margins, fonts |
| Region | One of five places on the page: header, main, footnote, footer, watermark |
| Block | One piece of content in a region: heading, text, fields, table, totals, image, spacer, page break |
| HTML block | Any block switched to HTML: its Jinja template replaces the generated markup |
| Footnote | Content snapped to the bottom of the last page (or of every page) |
| Watermark | Text, an image or HTML behind the content of every page, with a size and opacity |
| Page number | A format such as `Page {page} of {pages}`, placed in the header or footer |
| Print | Open the preview for a record or a report |
| File a copy | Render now and keep the PDF as an attachment of the record |

Templates use `doc.name` as the document number and `doc.title` as its display text.

## 3. Blocks, with HTML per block

The owner chose blocks only, with an HTML option on each block: "blocks with option for html in
individual block. not html as whole."

Absolute-position canvases (as in Frappe's Print Designer) place every element at fixed coordinates.
Long text then clips or overlaps, nothing pushes the next element down, and page breaks need extra
rules; the spike's (c1) result shows the failure. Flow blocks avoid all of that: each block pushes the
next one down, tables break between rows with repeated headers, and the paged-media rules do the page
breaking. A block switched to HTML keeps its place in the flow and its spacing, alignment, visibility
and break options; only its inner markup comes from the person's Jinja template. That covers the
unusual piece without giving up the flow for the whole document.

## 4. Regions

| Region | Where | Notes |
|---|---|---|
| Header | Top margin of every page | `header_first` can differ on page 1 (a letterhead), with its own top margin |
| Main | The page body | The flow of blocks |
| Footnote | Bottom of the content area | `place="last_page"` (default) or `"every_page"` |
| Footer | Bottom margin of every page | Usually the page number and a reference |
| Watermark | Behind the content of every page | Text, image or HTML; `size` (percent of the page width), `opacity`, `rotate`, `show_if` |

**Footnote, last page.** The footnote's content uses the spike's technique: `float: footnote` with
`footnote-policy: block`, called from a line at the end of the main flow. Space is taken only on the page
where the main flow ends; if it does not fit, it moves whole to the next page and takes the last main block
(or the last table row) with it. **Footnote, every page.** The content sits above the footer in the bottom
margin on every page; `height` reserves its space there.

**Page numbers** use `{page}` and `{pages}`: `"Page {page} of {pages}"`, `"{page}/{pages}"`,
`"Page {page}"`. The owner's `{0}` and `{1}` are accepted as the same placeholders. The format compiles to
spans whose content is `counter(page)` and `counter(pages)`, so the literal text is escaped HTML and never
enters a CSS string.

## 5. Data and templates

A design maps **document data** (`model = SalesInvoice`) or **report data** (`report = SalesReport`).

- Document: `doc` holds the record's readable fields as Python values (Decimal, date), each Link as
  the linked record's readable fields with `title`, and each child table as a list of rows. `doc.name`
  and `doc.title` are always present.
- Report: `report.title`, `report.filters` (the applied filter values, with labels), `report.rows`,
  `report.columns` and `report.totals`. Rows come from the report's own `run()`, page by page, up to a
  bound.

Filling uses **Jinja2 in its sandbox** with autoescape on, installed only with the print extra. Text in
any block may use `{{ ... }}`; HTML blocks are Jinja templates. Templates never see a field the person
cannot read: the context is built from the same permission-filtered detail as an HTTP read, with the
same row scope and Link checks. The stylesheet is passed to WeasyPrint directly, never through the
template. WeasyPrint's URL fetcher refuses everything except the design's own assets and the bundled
fonts, so a template cannot read files or call internal addresses.

Helpers, all isolating direction so they read correctly inside Arabic text: `money(value, currency)`,
`date(value)`, `number(value, places)`, `ar(text)`.

## 6. Packaging

```toml
[project.optional-dependencies]
print = ["weasyprint>=70,<71", "jinja2>=3.1,<4"]
```

`pip install evlet[print]` adds printing. WeasyPrint also needs pango 1.44 or later from the system:
`brew install pango` on macOS, `apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0` on
Debian and Ubuntu. Without the extra, designs still load and are checked, the schema reports that
printing is off, the browser hides Print, and the print routes answer 503 with the install command.

Noto Sans and Noto Sans Arabic (SIL Open Font License) ship in `evlet/resources/print/fonts`, so a design
prints the same on every server.

## 7. Permissions

Printing is its own operation, `print`, checked on the server on every print route. Admin may print
any model it can read. Another role prints a model when its Rule names `print`:
`Rule(("read", "print"))`. Read access is still required, and only readable fields reach templates.
Report designs are printed by the report's roles that the design's `roles` also lists.

## 8. API

```python
from evlet import PrintDesign, Heading, Text, Fields, Table, Column, Totals, Image, Spacer, PageBreak, PageNumber, Footnote, Watermark

class TaxInvoicePrint(PrintDesign):
    model = SalesInvoice
    title = "Tax invoice"
    default = True
    size = "A4"
    margins = ("30mm", "14mm", "20mm", "14mm")        # top, right, bottom, left
    header = (Text("{{ doc.company.title }}", weight="bold"),)
    main = (
        Heading("Tax Invoice"),
        Fields(("name", "Invoice no."), "posting_date", "customer", columns=2),
        Table("items", columns=(Column("item", width="45%"), Column("qty"), Column("rate"), Column("amount"))),
        Totals("net_total", "vat_total", "grand_total"),
    )
    footnote = Footnote(Text("Received by"), place="last_page")
    footer = (PageNumber("Page {page} of {pages}", align="right"),)
    watermark = Watermark(text="DRAFT", show_if="doc.status == 'draft'")

class SalesReportPrint(PrintDesign):
    report = SalesReport
    roles = ("admin", "editor")
    main = (Heading("{{ report.title }}"), Fields("from_date", "to_date"), Table("rows"))

app = Application(database, auth=auth, prints=[TaxInvoicePrint, SalesReportPrint])
```

Every block takes `html=` (a Jinja template that replaces its markup), `show_if=` (a Jinja expression),
`align`, `size`, `weight`, `space_before`, `space_after` and `break_before="page"`.

| Route | Does | Checks |
|---|---|---|
| `GET /api/_prints` | Designs this person may print, by model and report | |
| `GET /api/{model}/{id}/prints/{design}.pdf` | The PDF inline; `?download=1` names it `{name}.pdf` | read and `print` on the record |
| `POST /api/{model}/{id}/prints/{design}/file` | Keep a copy as an attachment | also update, attachments enabled |
| `POST /api/_reports/{report}/prints/{design}` | The PDF of a report with the given filters | report roles and design roles |

From trusted code: `context.print(TaxInvoicePrint, record_id)` returns PDF bytes.

## 9. Owner decisions (03-11-2026)

1. Name: Print Design (`PrintDesign`).
2. Designer: blocks only, any block can be switched to HTML; no whole-document HTML mode.
3. Data: document data or report data (filters and rows), filled with Jinja.
4. Regions: header, footer, footnote, main and watermark; the watermark is an image or HTML (or text),
   previewed and sized by the person.
5. Page numbers: a format with placeholders, `{page}` and `{pages}` (`{0}` and `{1}` also accepted).
6. Last-page area: generic content snapped to the bottom of the last page (the footnote region), with
   "every page" as an option.
7. Templates: Jinja2 sandboxed inside `evlet[print]`; the core stays free of dependencies.
8. Fonts: Noto Sans and Noto Sans Arabic bundled.
9. Print rights: a separate `print` operation, checked on every print route, on top of read access.

## 10. Build order

1. Renderer, `PrintDesign`, blocks and regions, the `print` operation, routes, the extra and fonts,
   tests, docs and a Playground invoice and report.
2. Print button and preview drawer on record pages; Print on report pages.
3. Stored designs and the block designer screen, with HTML per block and the watermark preview.

Not in scope: bulk printing many records into one PDF, barcodes, PDF/A, digital signatures, receipt
printers, and letterhead records per company (a later step).
