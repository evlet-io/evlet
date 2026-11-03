# Print output: design

Status: proposal. The owner chose the name Print Design on 03-11-2026; the other decisions in
section 9 are open. Nothing in the framework is built yet. The spike below was
run on 03-11-2026 against WeasyPrint 70.0 (Python 3.12, pango 1.56.3, Apple M3).

Evlet prints records to PDF through WeasyPrint. A **Print Design** describes how one model prints. People
build print designs from ordered blocks; developers can write the same design as an HTML template and CSS.
Both go through one renderer, and the preview is the real PDF from the server.

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
| 4 | Sign-off at the bottom of the last page | Works with the footnote area, tested at every length from 1 to 80 lines (1 to 3 pages): 80 of 80 correct. Details below. |
| 5 | Arabic and RTL | Works. Shaping is correct in Geeza Pro, Tahoma, Arial and Al Nile; RTL paragraphs and RTL tables lay out correctly. One bidi trap found and fixed (dates). |
| 6 | Speed | 1 page: about 190 ms. 9 pages: about 1.04 s. Template time is 2 ms or less; the rest is layout and PDF writing. |

### Sign-off: the four approaches

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

Decided by the owner: the concept is a **Print Design**. The base class is `PrintDesign`, and each
design is named after the document it prints with a `Print` suffix: `class TaxInvoicePrint(PrintDesign)`,
`class DeliveryNotePrint(PrintDesign)`. In prose it is "a print design"; the shorter "print" is used
where the context is clear (`prints=[...]`, the `prints` routes). "Print Format" (Frappe's term),
"Layout" (already used by Slips and layout metadata) and "Template" (the HTML file inside a code-mode
design) were avoided.

| Term | Meaning |
|---|---|
| Print design | How one model prints: blocks or a template, page size, margins, letterhead |
| Letterhead | The company block on page 1 (and a compact header on later pages) |
| Header / Footer | Running content on every page: document `name`, page X of Y, TRN |
| Body | The blocks between header and footer, in order |
| Lines | A child table printed as a table whose header repeats on each page |
| Totals | The closing figures; never start a page alone |
| Sign-off | Signature, stamp and "Received by" fixed to the bottom of the totals page |
| Print | Open the preview for a record |
| File a copy | Render now and keep the PDF as an attachment of the record |
| Print check | The list of layout warnings for one render |

Templates use `doc.name` as the document number and `doc.title` as its display text, so the
letterhead, running header and file name come from the standard identity, never from a
model-specific field.

## 3. Visual designer or HTML code

### Absolute-position canvas (as in Frappe's Print Designer)

Every element has x, y, width and height on a page-sized canvas. This is exact for fixed forms such
as a cheque or a pre-printed stationery overlay. It breaks when content varies:

- A box has a fixed height, so long text is either clipped or flows over the element below it.
- Positions are relative to the page, so nothing pushes anything else down. A table that grows
  must be special-cased, and everything below it has to be re-anchored by the designer tool.
- Page breaks are not part of the model: what moves to page 2, whether the header repeats, whether
  totals and the signature stay together all need extra rules on top of coordinates.
- The spike's (c1) result is this failure in small: an absolutely placed sign-off overlapped the
  totals as soon as the page filled up.

### Flow-based blocks

A print design is an ordered list of blocks. Each block has spacing, alignment, font size and weight,
column widths, a visibility condition and break options (keep with next, start on a new page). The
blocks compile to the HTML template and CSS that the spike proved. Because the result is normal
flow, every block pushes the next one down, tables break between rows with repeated headers, and
the paged-media rules do the page breaking. Accuracy comes from the preview being the real PDF,
not from coordinates.

What blocks cannot do: place text at an exact spot on pre-printed stationery. That is a niche case
and is covered by code mode (`position: absolute` inside a fixed-size block).

### Raw HTML and CSS

Full control and every paged-media feature. Needs a developer, and mistakes such as the CSS
escaping or the numeric wrap are easy to make. Good for apps and for unusual layouts.

### Recommendation

The expectation holds: **flow blocks for clients, an HTML and CSS code mode for developers, one
renderer, and a server-rendered PDF preview.** Blocks are stored as data and compile to a template;
"Convert to code" turns a block design into an editable template (one way, with a warning). Python
apps can define print designs either way.

## 4. Designer

The designer is an Evlet screen with three panes (see the mockup in the artifact):

- **Blocks** (left): the ordered list. Add, drag, duplicate, hide. Blocks: Letterhead, Heading,
  Fields (label and value grid), Text, Lines, Totals, Amount in words, Image, Spacer, Page break,
  Sign-off, and an HTML block for small custom pieces.
- **Properties** (middle): for the selected block. Lines blocks list the child table's fields with
  width (mm or %), alignment, wrap or no wrap, and label. Visibility uses Evlet's finite
  expressions (`status == 'issued'`), so the same rule engine checks it.
- **Preview** (right): the PDF from the server in the browser's own PDF viewer (no frontend
  dependency), for a sample record the designer picks. It renders again about half a second after
  the last change; one page takes about 0.2 s.

Under the preview, the **print check** lists what the renderer found by measuring the layout:
sign-off moved to a new page, a row taller than a page, a numeric value wider than its column, a
field the template names that this role cannot read, missing fonts. Each warning selects the block
it belongs to. This is how the visual designer "stays accurate" without coordinates.

Code mode replaces the Blocks and Properties panes with two editors, template and CSS, over the
same preview and print check.

## 5. Templates

| Option | Loops and conditions | Safe for people who are not developers | Dependency |
|---|---|---|---|
| `string.Template` | no | yes | none |
| Own small template language | yes | yes, if built well | none, but Evlet must maintain a parser and its security |
| **Jinja2 sandboxed** (recommended) | yes | yes, with `SandboxedEnvironment` | `jinja2`, only in the print extra |

Templates edited in the browser are untrusted input, so the sandbox matters more than the standard
library preference. Jinja2's sandbox is mature; writing and securing our own parser is more risk.
It is only installed with WeasyPrint, so Evlet's core stays free of dependencies.

Rules:

- **Escaped by default.** Autoescape on; there is no `|safe` in the sandbox for stored designs.
- **CSS outside the template**, passed as a stylesheet (section 1).
- **Permissions.** The template context is built for the person printing, with the same field set
  as an HTTP read (`presented` fields), the same row scope and the same Link checks. A hidden or
  restricted field is not in `doc`; it prints empty, and the print check names it. Links resolve one
  level (`doc.customer.title`) only when the person can read the target.
- **No network or file access.** WeasyPrint's `url_fetcher` refuses everything except the design's
  own assets (letterhead images, bundled fonts). Otherwise `url(file:///...)` or an internal URL in
  CSS could read server files or call internal services.
- **Helpers**, all isolating direction so they read correctly inside Arabic text:
  `money(value, currency="AED")` gives `AED 5,616.35`; `date(value)` gives `03-11-2026`;
  `number(value, places)`; `words(amount, currency, lang="en"|"ar")` for the amount in words;
  `ar(text)` marks an Arabic run (`lang="ar" dir="rtl"`); `t("label")` uses the app's translations.
  Date and money formats come from app settings, so a non-UAE app can change them.

## 6. Packaging

```toml
[project.optional-dependencies]
print = ["weasyprint>=70,<71", "jinja2>=3.1,<4"]
```

`pip install evlet[print]` adds printing. WeasyPrint also needs pango 1.44 or later from the system:
`brew install pango` on macOS, `apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0` on Debian and Ubuntu.
The README says so, and start-up logs one line when the extra or pango is missing.

Without the extra:

- Print design definitions still import and are checked at start-up, so an app does not fail to start.
- The schema reports `print: false`; the browser hides Print and the designer shows how to install.
- The print routes answer `503 Printing is not installed on this server (pip install evlet[print])`.
- Tests that render skip with that reason; CI installs the extra and runs them.

The version is pinned to one major release because the print check reads WeasyPrint's layout boxes.

Fonts: ship Noto Sans and Noto Sans Arabic (SIL Open Font License, about 1 MB together) in
`evlet/resources/print/fonts`, used by the default designs, so a design prints the same on a Mac and
a Linux server. Apps can add their own fonts as design assets.

## 7. API and integration

### Python

```python
from evlet import PrintDesign, Blocks, Letterhead, Heading, Fields, Lines, Column, Totals, Words, SignOff, Signature, Stamp, ReceivedBy

class TaxInvoicePrint(PrintDesign):
    model = SalesInvoice
    title = "Tax invoice"
    default = True                    # one default design per model
    size = "A4"                       # A4, A5, Letter, Legal, or ("210mm", "297mm")
    margins = ("24mm", "14mm", "18mm", "14mm")
    letterhead_by = "company"         # pick the Letterhead whose match is this Link value
    roles = ("admin", "editor", "viewer")
    drafts = "watermark"              # unconfirmed records print with DRAFT; or "refuse"

    blocks = Blocks(
        Letterhead(),
        Heading("Tax Invoice", ar="فاتورة ضريبية"),
        Fields(("name", "Invoice no."), "posting_date", "due_date", "customer", columns=2),
        Lines("items", columns=(
            Column("item", width="45%"), Column("qty", align="right"),
            Column("rate", align="right"), Column("vat", label="VAT 5%"), Column("amount"),
        )),
        Totals("net_total", "vat_total", "grand_total"),
        Words("grand_total"),
        SignOff(Signature("Prepared by"), Stamp("Authorised signatory and stamp"), ReceivedBy()),
    )

class DeliveryNotePrint(PrintDesign):   # code mode in an app
    model = SalesInvoice
    title = "Delivery note"
    template = "prints/delivery_note.html"
    stylesheet = "prints/delivery_note.css"

app = Application(database, auth=auth, prints=[TaxInvoicePrint, DeliveryNotePrint])
```

A print design sets `blocks` or `template`, not both. `SignOff(place="totals")` is the default (footnote
area); `place="end"` keeps it at the bottom of the document's last page with the two-pass method;
`place="every_page"` uses a page margin.

From trusted code: `context.print(TaxInvoicePrint, record_id)` returns PDF bytes, and
`context.file_print(TaxInvoicePrint, record_id)` stores it as an attachment.

### Routes

| Route | Does | Checks |
|---|---|---|
| `GET /api/{model}/{id}/prints` | Print designs this person can use for this record, default first | read on the record (row scope), design roles |
| `GET /api/{model}/{id}/prints/{design}.pdf` | The PDF, inline for preview; `?download=1` for an attachment named `{name}.pdf`; `?letterhead=` to choose | as above; fields filtered for this person |
| `POST /api/{model}/{id}/prints/{design}/file` | Render and keep as an attachment | update on the record, attachments enabled, `If-Match` |
| `POST /api/_prints/preview` | Designer preview of an unsaved design against a sample record | designer role, read on the sample |
| `GET /api/_prints/{design}/check?record=` | The print check as JSON | as preview |

PDF responses use `application/pdf`, `nosniff` and a restrictive content security policy, like
attachment downloads. Each render writes a journal entry (who printed what, and which design), so a
reprint of a tax invoice can be traced. Documents over a set page count render as a background job
with progress, through the existing jobs module.

### In the browser

- A **Print** button on the record page: the default design opens in a preview drawer; a menu lists
  the other designs. Ctrl+P on a record page opens the same preview.
- The drawer has: design, letterhead, Download, File a copy, and the print check for people who may
  edit the design.
- Settings gain **Print designs** (list, designer, default per model) and **Letterheads**.

### Stored print designs and letterheads

Print designs made in the browser are stored as data (blocks or template, CSS, options) next to the
app's other customizations, with history. A Python design can be copied into a stored design to
change it; the Python one stays as the fallback. **Letterhead** is a framework model: title, logo
image, address lines in English and Arabic, compact header, footer text, and an optional match on a
Link value. `letterhead_by = "company"` picks the letterhead whose match equals the record's
company, else the default letterhead. Evlet has no Company model of its own, so this stays generic.

## 8. Scope

Generic only. The framework provides PrintDesign, the blocks, Letterhead, the renderer, routes, the
designer and the print check. Tax invoices, payslips, delivery notes and every other document are
print designs that apps define. Not in this design: bulk printing many records into one PDF, barcodes and
QR codes, PDF/A, digital signatures, and receipt printers with continuous paper.

## 9. Decisions for the owner

1. Name: decided, **Print Design** (`PrintDesign`).
2. Designer: flow blocks plus code mode, server PDF preview (recommended), as above.
3. Templates: Jinja2 sandboxed inside `evlet[print]` (recommended), or an own template language.
4. Sign-off default: bottom of the totals page through the footnote area (recommended), with
   "last page" and "every page" as options.
5. Bundle Noto Sans and Noto Sans Arabic in the package (about 1 MB), or leave fonts to apps.
6. Print rights: printing follows read access (recommended), or a separate `print` operation.

## 10. Build order after approval

1. Renderer and code mode: `PrintDesign` with `template`, helpers, sandbox, URL fetcher, routes, Print
   button and preview drawer, `evlet[print]`, tests on real SQLite records.
2. Blocks compiled to templates, and the Sign-off block.
3. Print check from measured layout.
4. Stored print designs, Letterhead records, the designer screen.
5. Filing copies, journal entries, background rendering for long documents.
