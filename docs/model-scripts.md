# Model scripts

A model script adds browser behaviour that a model cannot declare: a tooltip worked out from the
record, a value coloured by its meaning, a custom input in a table cell, or a block the app draws
itself. Declare what you can first (formulas, field rules, `help_text`, layouts); a script is the
escape hatch for the rest.

The wiring lives in the Python model, and the behaviour lives in one JavaScript file. The script
never attaches itself to anything: the model names which method runs where.

```python
from evlet import Child, Decimal, Js, Main, Model, Section, Table, Text


class Line(Child):
    qty = Decimal(scale=2, control='qty_input', look=Js('qty_look'))


class Quotation(Model):
    customer = Text(on_change='customer_changed', tooltip=Js('customer_tip'))
    lines = Table(Line)

    class Meta:
        script = 'quotation.js'          # next to this models.py
        on_open = 'opened'
        layout = (Main(('customer',)), Main('lines'), Section('Delivery map', custom='map'))
```

```js
// quotation.js
export default class Quotation extends evlet.Model {
  opened() { if (this.is_new) this.ui.toast('Pick the customer first.'); }
  customer_changed(field) { this.field('notes').set(`Quoted for ${field.value}`); }
}
```

## The script file

- `Meta.script` names a `.js` file relative to the folder of the module that declares the model. It
  may go up with `..` (`"../trade.js"`) as long as the file stays inside the app package. A missing
  file, another extension, a path that leaves the app package (a link out of it included) or a
  hidden folder fails at start-up, naming the model and the path. Documents and prompts take a
  script ([In a prompt](#in-a-prompt)), and `Extend` scripts follow the same rule: a Table row's hooks call
  its document's (or prompt's) script, and ledger models have none.
- It is plain JavaScript, as an ES module, served as it is: no TypeScript, no bundler and no Node,
  on the server or in any `evlet` command. Its default export is a class that extends
  `evlet.Model`. `evlet` is one frozen global, set before any script loads.
- It may import any other `.js` file of its app by relative path, across folders: the script of
  `purchase/purchase_order.py` can share `trade.js` at the app's root with `import { rate } from
  '../trade.js'`, so one file serves every model that needs it. It cannot import Evlet's bundled files,
  files outside the app or files in hidden folders.
- The browser loads it from `/assets/scripts/<hash>/<path>` only when a record of the model
  opens; `<path>` is the file's path in the app package (`purchase/purchase_order.js`). The hash covers
  every `.js` file of the app, so a changed file gets a new address. The app package is the outermost
  package of the declaring module (the app's source folder); a module in no package uses its own folder.
  The page's Content Security Policy is unchanged (`script-src 'self'`).
- The server never runs it and never depends on it. The API, MCP, Eva, imports and jobs work the
  same without it, and every check stays on the server.

## Hooks

Each hook names a method of the script's class. A method the script lacks gives one console
warning, and the screen keeps working. A hook on a field of a Table row gets the row as its
second argument.

### on_change

`Text(on_change='customer_changed')` runs after the value changes, for any reason: typing, a
formula, a cleared Link or another hook.

```js
customer_changed(field, row) { field.warn(field.value ? '' : 'Pick a customer'); }
```

### tooltip

`tooltip=Js('customer_tip')` returns text, or `{title, lines}`. `tooltip='text'` stays as it was.

```js
customer_tip(field) { return { title: 'Credit', lines: [`Limit ${this.field('credit_limit').value}`] }; }
```

### look

`look='strong'` (or `'muted'`, `'success'`, `'warning'`, `'danger'`) is fixed. `look=Js('qty_look')`
returns `{tone}` or `null`.

```js
qty_look(field, row) { return Number(field.value) > 100 ? { tone: 'warning' } : null; }
```

Tooltip and look methods run as reactive derives: they run again when a value they read
changes.

### control

`control='qty_input'` draws the field's input, in forms and in table cells. It gets an element to
draw in, inside a shadow root (its CSS stays inside; the theme variables still reach it), and may
return a cleanup function.

In a table cell (on a document or a prompt), keys typed while the focus is inside the control
belong to it: the grid keeps only Tab and Shift+Tab, which move to the next or previous cell. A
click inside it stays there, and moving to the cell (Tab, Add row, Enter) focuses the control's
first input, select, textarea or button. A script needs no `stopPropagation`.

```js
qty_input(el, field, row) {
  const input = document.createElement('input');
  input.value = field.value ?? '';
  input.disabled = !field.editable;
  input.addEventListener('change', () => field.set(input.value));
  el.append(input);
  return () => input.remove();
}
```

### custom blocks

`Main(custom='summary')` and `Section('Title', custom='map')` are blocks the script draws: a
`Main` in the main zone, a `Section` as a row of "more information". They draw when shown; they
may return a cleanup function.

```js
map(el) { el.textContent = `Deliver to ${this.field('city').value}`; }
```

### on_open

`Meta.on_open = 'opened'` runs once when a record opens, new or saved.

```js
opened() { if (this.is_new) this.ui.toast('Pick the customer first.'); }
```

## The browser API

This is the whole public surface ([api-stability.md](api-stability.md)); a script never gets
the record store.

- `this.model`, `this.id`, `this.is_new`, `this.state`
- `this.field(name)`: `{ name, label, value, editable, set(value), warn(text) }`
- `this.table(name)`: `{ rows, add(values), remove(row) }`; a row is `{ index, field(name) }`
- `this.call(action, values)`, `this.move(key, remark)`, `this.save()`, `this.reload()`
- `this.api.get(model, id)`, `this.api.list(model, query)`, `this.api.call(endpoint, params)`
- `this.ui.toast(text)`, `this.ui.confirm(text)`, `this.ui.open(model, id)`

Hook arguments: `on_change(field, row)`, `tooltip(field, row)`, `look(field, row)`,
`control(el, field, row)` and `custom(el)`.

## Rules

- `set()` means "as if the person typed it": it works only while the field's `editable` is true
  and goes through the same write path. On a calculated, derived, read-only or locked field it
  throws an error saying why.
- A script cannot replace or intercept a save, an action, a move or any server call; `call`,
  `move`, `save` and the `api` methods go through the same routes and checks as the screen.
- No loops: a hook is not called again for a change it made, directly or through other hooks,
  and hooks nest at most 8 deep (one console warning).
- Late results: after the record closes, reloads or saves, the views handed out before go
  inert and ignore calls. Ask `this.field(...)` again after an `await`.
- A hook that throws, or whose promise rejects, is logged with the model and the method, and the
  form keeps working.

The Playground's Employee shows each hook (`examples/playground/employee.js`).

## In a prompt

A [prompt](prompts.md) takes `Meta.script` and `Meta.on_open` with the same file rules, and its
fields and the fields of its Table rows take `on_change=`, `tooltip=Js(...)`, `look=Js(...)` and
`control=`. A hook on a prompt without `Meta.script` fails when the class is defined, naming the
field. The file may be one a model also names: a quantity typed as "10-6" (ten boxes and six
pieces, saved in the base unit) is drawn by the same control on the document's lines and on the
prompt that confirms them.

```python
class QuotationLine(Child):
    qty = Decimal(scale=3, control='qty_input')


class Quotation(Model):
    lines = Table(QuotationLine)

    class Meta:
        script = 'quotation.js'


class ConfirmQuotation(Prompt):
    class Line(Child):
        confirm_qty = Decimal(scale=3, control='qty_input')

    lines = Table(Line)

    class Meta:
        script = 'quotation.js'          # the same file: each gets its own instance
```

```js
// quotation.js: one control for both
qty_input(el, field) {
  const input = document.createElement('input');
  const per = 12;                                       // pieces in a box
  const shown = value => value === null ? '' : `${Math.trunc(value / per)}-${value % per}`;
  input.value = shown(field.value === null ? null : Number(field.value));
  input.disabled = !field.editable;
  input.addEventListener('change', () => {
    const [boxes, pieces = '0'] = input.value.split('-');
    field.set(String(Number(boxes) * per + Number(pieces)));
  });
  el.append(input);
}
```

In a prompt, `field()`, `table()`, rows, `set()`, `warn()`, `api` and `ui` work as on a record.
`this.model` is the prompt's form name, `this.id` is `null`, `this.is_new` is `true` and
`this.state` is `null`. `call()`, `move()`, `save()` and `reload()` reject: a prompt has no saved
record, and a script cannot submit, cancel or intercept it. `on_open` runs once the values from
`load()` are in, and closing the modal makes the script's views inert. Custom blocks
(`Main(custom=)`, `Section(custom=)`) are not supported in a prompt. The server still checks every
value on submit; it never runs the script.

## Extending a model from another app

An app can add checks and browser behaviour to another app's model without changing it:

```python
from evlet import Extend, ValidationError, validate


class AcmeQuotation(Extend):
    @validate
    def po_needed(self):
        if self.total > 10000 and not self.customer_po:
            raise ValidationError({'customer_po': 'Orders over 10,000 need a PO number'})

    class Meta:
        model = "quotation"                 # or "sales.Quotation"
        script = "acme_quotation.js"        # beside this module
        on_change = {"customer": "check_blacklist", "lines.qty": "qty_changed"}
        on_open = "opened"
```

- Register it with `AppDefinition(extensions=(AcmeQuotation,))`. A flat app's `Extend` classes are
  found like its models.
- It is additive only: extra `@validate` checks, `on_change` hooks on existing fields (`"field"`,
  or `"table.field"` for a Table's rows), and its own script with `on_open`. Its class body holds
  `@validate` methods and `Meta`, nothing else: it cannot add, remove or change fields, formulas,
  states, actions or the model's own hooks.
- Order: the model first, then extensions in app install order, for `@validate` checks, `on_open`
  and every `on_change`.
- Its script is its own class on the same record, with the same browser API. It cannot replace the
  model's script, and the rule against loops spans both: no script runs again for a change it made.
- A model, field or script that does not exist fails at start-up, naming the extension.
