# Rich text: design note

Status: design for three stacked PRs. PR 1 is the editor core and the `RichText` field, PR 2 inline
images, PR 3 mentions in the activity composer and the Mentions inbox.

The owner chose an editor Evlet builds itself. ProseMirror was rejected because it is a runtime
dependency, and Markdown with a preview was rejected because it is not WYSIWYG. The editor must be
reliable: the user's text is never lost, and what the server stores is always safe.

## Principles

1. **The server decides what is stored.** The browser sends HTML. The server parses it with a
   standard library allowlist parser (`html.parser`), keeps only allowlisted tags and attributes,
   checks every URL and writes canonical HTML. Nothing the client sends is trusted.
2. **The editor owns its document.** The browser never edits the DOM on its own outside IME
   composition. Every edit becomes an operation on Evlet's model, and the model renders the DOM.
3. **One small schema.** Paste, load, the server and print all reduce content to the same schema,
   so the editor never meets a node it cannot render.
4. **No markup from data.** The editor and the read-only view build DOM with `createElement` from
   the model. Stored HTML is parsed into the model first (with `DOMParser`, which never runs
   scripts or loads resources), never assigned to `innerHTML`.

## Document model

A document is a flat list of blocks. Lists are blocks with a list type and an indent, as in most
word processors. A flat list keeps positions simple (block index and offset), which makes selection
mapping, undo and the `beforeinput` handlers much easier to get right than a deep tree.

| Block | Fields | Stored as |
|---|---|---|
| paragraph | inline content | `<p>` |
| heading | `level` 2 or 3 | `<h2>`, `<h3>` |
| list item | `list`: bullet, ordered or check; `indent` 0 to 2; `checked` for check | `<ul>`, `<ol>`, `<ul data-type="checklist">` with `<li>` (`data-checked="true"` when ticked), nested for indents |
| quote | inline content | `<blockquote><p>` (neighbouring quote blocks share one `blockquote`) |
| code | plain text with newlines, no marks | `<pre><code>` |
| rule | none | `<hr>` |
| image (PR 2) | attachment id, alt, size, caption | `<figure class="rt-md"><img data-attachment="ID" alt="..."><figcaption>` |

Inline content is a list of pieces:

- text with marks: bold `<strong>`, italic `<em>`, underline `<u>`, strike `<s>`, inline code
  `<code>`, and a link `<a href>` (http, https and mailto only);
- a hard break `<br>`, one position wide;
- a mention (PR 3) `<span data-mention="ID">Name</span>`, one position wide.

Offsets count UTF-16 code units of text, and one for each break or mention. Neighbouring text
pieces with the same marks merge, so a document has one canonical form and two equal documents
serialise to the same HTML. Lists nest at most three levels (indent 0 to 2); deeper content is
lifted to the third level.

## Storage

- The field stores the sanitised HTML in its column and the plain-text extract in a second column,
  `<name>_text`, as `Reference` already stores two columns. Search, list filters and list cells use
  the text; record reads, print and exports carry the HTML (and exports the text as well).
- The plain text has a newline between blocks and for each `<br>`, a mention as `@Name`, and an
  image as its alt text. It is what search matches and what a list cell shows.
- `RichText(max_length=20000)` limits the plain text. The HTML is limited to ten times that (at
  least 4,096 characters), so heavy formatting cannot grow a row without bound.
- An empty document (no text, no rule, no image) is stored as `NULL`, so `required=True` works as
  it does for text.

## The sanitiser (`evlet/richtext.py`)

- `html.parser.HTMLParser` with `convert_charrefs=True` reads the input into a small tree.
- Allowlist: `p h2 h3 ul ol li blockquote pre code hr br strong em u s a` (PR 2 adds `figure img
  figcaption`, PR 3 adds `span[data-mention]`). Aliases from paste are mapped first: `b` to
  `strong`, `i` to `em`, `strike` and `del` to `s`, `h1` to `h2`, `h4` to `h6` to `h3`, `div` to
  `p`.
- The content of `script style iframe object embed svg math template noscript textarea select
  title head` and of comments and processing instructions is dropped whole. Any other unknown tag is
  unwrapped and its text kept.
- Attributes: only `a[href]`, `ul[data-type=checklist]` and `li[data-checked]`. Event handlers,
  `style`, `class` (until PR 2's size classes), `id` and every other attribute are dropped.
- URLs: control characters, tabs and newlines are removed and the scheme is read as a browser does,
  so `java\tscript:` and `&#106;avascript:` are refused. Only `http:`, `https:` and `mailto:` remain;
  a link with any other URL keeps its text and loses the link.
- The tree is then normalised: loose inline content at the top becomes a paragraph, a list item
  outside a list gets a list, block tags inside inline tags are lifted, and empty marks are dropped.
  The output is canonical, so `sanitise(sanitise(x)) == sanitise(x)`, and the editor's own output
  comes back from the server unchanged.

## Editing engine (`frontend/src/evlet/richtext/`)

| Module | What it does |
|---|---|
| `model.ts` | The document types, normalisation, positions, plain text |
| `ops.ts` | Pure transforms: insert, delete, split, join, set block, toggle mark, link, indent |
| `html.ts` | Stored or pasted HTML to the model (cleaned to the schema), the model to HTML |
| `render.ts` | The model to DOM, and DOM positions to model positions and back |
| `history.ts` | Undo and redo |
| `editor.ts` | The `RichTextEditor` component: events, toolbars, announcements |
| `richtext.css` | Editor and read-only styles |

- The surface is a `contenteditable` element with `role="textbox"` and `aria-multiline="true"`,
  labelled by the field label.
- `beforeinput` is handled for every input type the editor supports (`insertText`,
  `insertReplacementText`, `insertParagraph`, `insertLineBreak`, `insertFromPaste`,
  `insertFromDrop`, `insertFromYank`, `insertLink`, every `delete*` type, `formatBold`,
  `formatItalic`, `formatUnderline`, `formatStrikeThrough`, `historyUndo`, `historyRedo`). The
  editor cancels the event, applies the operation to its model and renders. Unknown types are
  cancelled so the DOM never drifts.
- **Composition and phone keyboards.** During IME composition (`compositionstart` to
  `compositionend`) the browser edits the text node itself; the editor does not render. At
  `compositionend`, and after any `input` event whose `beforeinput` could not be cancelled
  (Android keyboards send non-cancelable `insertCompositionText` and replacement events), the editor
  reads the text of the changed block back from the DOM, finds the changed range by comparing it with
  the model, applies that as one replacement that keeps the marks around it, and renders. Autocorrect
  and spellcheck replacements come as `insertReplacementText` with a target range, or through this
  same reconcile path.
- **Selection.** After each render the model selection is mapped back to the DOM. Each rendered
  text node and atom carries its block index and offset, so mapping does not depend on the browser
  keeping nodes.
- **History.** Each change records the document and selection before it. Typing in one block
  within one second is one step; a new word after a space, a change of block, a format change,
  paste, or moving the caret starts a new step. Up to 200 steps are kept.
- **Keys.** Ctrl or Cmd with B, I, U and K (link); Shift+Ctrl+7 numbered list and Shift+Ctrl+8
  bullet list; Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y. Tab and Shift+Tab indent and outdent inside lists
  only; elsewhere Tab leaves the editor. Input rules at the start of a paragraph: `# ` and `## `
  heading 2, `### ` heading 3, `- ` and `* ` bullet list, `1. ` numbered list, `> ` quote, `[] ` and
  `[ ] ` checklist, three backticks code block, `---` rule; text between backticks becomes inline
  code. Undo right after an input rule restores the typed characters.
- **Paste.** HTML is parsed with `DOMParser` and reduced to the schema: Word (`mso-` styles,
  `MsoListParagraph` paragraphs with list markers), Google Docs (`b id="docs-internal-guid"`
  wrappers and bold or italic carried in `style`), Outlook and web pages. Styles, classes, scripts,
  iframes and comments never survive. Plain text keeps paragraphs (blank lines) and lines (`<br>`);
  bare URLs become links.
- **Toolbars.** A compact toolbar with a roving tabindex (one Tab stop, arrows move), a floating
  selection toolbar on desktop, and on phones (coarse pointer, narrow width) the toolbar sits
  above the keyboard using `visualViewport`.
- **Announcements.** A polite live region says "Bold on", "Heading 2", "Bulleted list" and so on.

## The field

- `RichText(max_length=20000, **field options)` in `evlet/fields.py`, schema type `richtext`. It
  inherits `LongText`, so places that leave long text out (default list columns, default search
  fields, naming inputs, measures) leave rich text out too.
- List filters offer `contains`, `not contains` and `is empty` on the text column.
- At rest a record shows the formatted text (read only when locked); a list cell shows the
  plain text. Print Design renders the sanitised HTML (sanitised again when printed), and in a
  template `doc.<field>` is that safe markup. Search, Eva and MCP read the HTML through the same
  record routes; the report CSV export writes the plain text.

## PR 2: inline images

- Image blocks reference the record's private attachments: `<img data-attachment="ID" alt="">`.
  Never an external or `data:` URL; the sanitiser drops `src` and keeps only a well formed id.
- The browser loads images through the existing permission-checked download route, so whoever
  cannot read the record cannot load the image.
- PNG, JPEG, GIF and WebP only; SVG refused (by sniffing the bytes on the server too). Before
  upload the client downscales to 2,000 px on the long side and re-encodes (WebP, or JPEG when the
  image has no transparency), with a placeholder that shows progress.
- An unsaved record's images go to a per-user holding area (an attachment row with no record) and
  are linked to the record in its create transaction. Holding rows older than 24 hours are purged
  by a framework schedule. Restoring unsaved edits (the `unsaved-changes` store) keeps the image
  references; a reference to a purged image renders as "Image no longer available".
- Saving a record whose rich text no longer references an image deletes the attachment, in the
  same transaction, unless another rich text field on the record still references it.
- Copying an image to another record re-uploads it; a cross-record reference is refused by the
  server.
- Limits: image size and images per record come from instance settings, separate from the
  general attachment caps: 2 MiB and 50 images by default, at most 8 MiB and 200 (Controls settings
  `files.image_max_kb` and `files.images_per_record`).
- Sizes small, medium and full width are stored as one class from an allowlist (`rt-sm`, `rt-md`,
  `rt-full`); alt text and an optional caption.
- Print embeds the image bytes; email templates attach them inline (`cid:`).

### As built

- `evlet/images.py` holds the server side: `upload`, `settle` (called by `Session.create` and
  `Session.update`, so trusted Python writes link and delete images too), `purge`, `embed` (print)
  and `for_email`. The person a request writes for comes from a context variable the HTTP layer sets;
  trusted Python may link any held image.
- The upload routes take larger bodies than other writes: the image limit as base64.
- The unsaved-changes store (`docs/internal/unsaved-changes.md`) keeps rich text values as they are,
  so a restored draft keeps its image references. A new record's draft can live for up to seven
  days in the browser while held images go after one: a draft restored after a day shows those
  images as no longer available, and saving it asks to remove them and add them again.

## PR 3: mentions

- The activity composer (notes and messages, `docs/journal.md`) uses the editor.
- `@` opens a picker of active users (search by name and username, keyboard and touch).
- A mention is `<span data-mention="USER_ID">Name</span>` and shows the user's current name when
  displayed.
- While composing, a mention of a user who cannot read the record shows a warning ("Priya can't
  open this record"). That user is told only that they were mentioned, with no content, title or
  link they could not read.
- A framework table `evlet_mentions` (own rows only) is the Mentions inbox, with an unread count in
  the shell, kept live by the existing realtime channel. Email goes through the mail connector when
  a mailbox is configured and the user's notification preferences allow it. The table is generic
  so the later notification feed (readiness gap B6) can grow from it.

### As built

- The sanitiser keeps mentions only when asked (`sanitise(html, mentions=True)`, the journal); in a
  `RichText` field a mention is plain text, so fields and the journal share one schema otherwise.
- The inbox is `evlet_inbox` (`kind`, `actor`, `model`, `record_id`, `entry_id`, `at`, `read_at`,
  `email`, `hidden`), generic so readiness gap B6's notification feed can add kinds to it.
- The browser had no realtime subscription yet; the bell opens one (`EventSource` on the event
  stream) and checks the count every minute when the stream is unavailable.

## Tests

- Python: the sanitiser fuzz-style suite (scripts, event handlers, `javascript:` and `data:` URLs,
  entity tricks, SVG, CSS, nesting, idempotence), the field (columns, text extract, limits,
  required), search and filters on the text, print, and the API.
- Frontend: model operations, every `beforeinput` type, composition and reconcile, undo grouping,
  input rules, paste fixtures (Word, Google Docs, Outlook, web, plain text), HTML round trip,
  toolbar keyboard, and phone width.
