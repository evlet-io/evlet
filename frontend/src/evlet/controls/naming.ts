// Naming: the series list is a draft behind the change bar; counters are instance data and save at
// once with a reason. Conditions and formats are checked by the server, through Try it, so the page
// and the create path share one parser.

import { html, when } from '../dom';
import { ValidationError } from '../api';
import { cell, derive, effect, onCleanup } from '../reactive';
import { Button, Icon, IconButton } from '../ui/base';
import { DialogFooter, openDrawer, openModal, toast } from '../ui/layers';
import { Callout, Head, stamp, type ControlsContext } from './app';
import type { Counter, ModelInfo, SeriesValue, TryResult } from './client';
import { idOf } from './drafts';
import { effective } from './models';
import { field, message, Select } from './people';

const TOKENS: Array<[string, string]> = [['{YYYY}', 'Year, 2026'], ['{YY}', 'Year, 26'], ['{MM}', 'Month, 11'], ['{DD}', 'Day, 01'],
  ['{####}', 'Counter, 4 digits'], ['{#####}', 'Counter, 5 digits']];

export function NamingPage(ctx: ControlsContext): HTMLElement {
  const models = () => ctx.state.get().models.filter(model => model.naming);
  const asked = ctx.router.route.peek()?.query.model;
  const selected = cell(models().some(model => model.name === asked) ? asked! : models()[0]?.name ?? '', { name: 'naming.model' });
  const pick = (name: string) => { selected.set(name); void ctx.router.go('naming', {}, { model: name }, { replace: true }); };
  return html`<div>
    ${Head('Naming series', 'How record names are made, like INV-2026-0143. Series are checked from the top; the first one whose condition matches names the record.')}
    ${() => models().length ? html`<div class="toolbar"><div class="seg" role="tablist">${models().map(model => html`<button type="button" role="tab"
      class:on=${() => selected.get() === model.name} on:click=${() => pick(model.name)}>${model.name}<span class="muted">.${model.naming!.field}</span></button>`)}</div></div>`
      : html`<section class="ctl-card"><div class="ctl-empty">No model has naming series. Add <code>Meta.naming = Naming(...)</code> to a model in its app.</div></section>`}
    ${() => { const model = models().find(item => item.name === selected.get()); return model ? Series(ctx, model) : null; }}
  </div>` as HTMLElement;
}

function Series(ctx: ControlsContext, model: ModelInfo): HTMLElement {
  const id = idOf('naming', model.name);
  const list = () => ctx.drafts.value<SeriesValue[]>(id);
  const set = (next: SeriesValue[]) => ctx.drafts.set(id, next);
  const values = cell<Record<string, string>>({}, { name: 'naming.try' });
  const result = cell<TryResult | null>(null), counters = cell<Counter[]>([]);
  let alive = true, timer: ReturnType<typeof setTimeout> | undefined;
  onCleanup(() => { alive = false; clearTimeout(timer); });
  const check = async () => {
    try {
      const body = { values: values.peek(), ...(ctx.drafts.dirty(id) || ctx.drafts.overridden(id) ? { series: list() } : {}) };
      const next = await ctx.api.tryName(model.name, body);
      if (alive) result.set(next);
    } catch (failure) { if (alive) toast(message(failure), { icon: 'info' }); }
  };
  const later = () => { clearTimeout(timer); timer = setTimeout(() => void check(), 200); };
  effect(() => { list(); values.get(); later(); });
  const loadCounters = async () => {
    try { const page = await ctx.api.counters(model.name); if (alive) counters.set(page.items); } catch (failure) { if (alive) toast(message(failure), { icon: 'info' }); }
  };
  void loadCounters();
  const problem = (index: number) => () => {
    const found = result.get()?.checks[index];
    return found?.when ?? found?.format ?? null;
  };
  const move = (index: number, step: number) => { const next = [...list()]; [next[index], next[index + step]] = [next[index + step], next[index]]; set(next); };
  const row = (series: SeriesValue, index: number, all: SeriesValue[]) => {
    const last = index === all.length - 1;
    return html`<div class="ctl-ser" class:hit=${() => result.get()?.match === series.id}><span class="ctl-ser-n">${index + 1}</span>
      <div class="grow"><div class="ctl-line"><strong>${series.label}</strong><code>${series.format}</code>
        ${() => result.get()?.match === series.id ? html`<span class="ctl-badge green">Matches the test record</span>` : null}</div>
        <div class="ctl-when">${() => last ? html`<span class="muted">${all.length > 1 ? 'Everything else' : 'Every record'}</span>`
          : problem(index)() ? html`<span class="ctl-err">${Icon('info', { size: 'xs' })} ${problem(index)()}</span>`
          : html`<span class="muted">when</span> <code>${series.when}</code>`}</div>
        <div class="muted ctl-small">Next: <span class="mono">${() => result.get()?.checks[index]?.next ?? '…'}</span></div></div>
      <div class="ctl-line">${IconButton({ icon: 'arrow-up', tip: 'Check earlier', size: 'small', disabled: index === 0 || last, onClick: () => move(index, -1) })}
        ${IconButton({ icon: 'arrow-down', tip: 'Check later', size: 'small', disabled: index >= all.length - 2, onClick: () => move(index, 1) })}
        ${Button({ label: 'Edit', size: 'small', onClick: () => editSeries(ctx, model, index, values) })}</div></div>`;
  };
  const inputs = () => model.fields_detail.filter(info => info.name !== model.naming!.field && info.stored && info.type !== 'link' && info.type !== 'longtext' && info.type !== 'richtext').slice(0, 6);
  const input = (name: string) => {
    const value = effective(ctx, model.name, name);
    const choices = value.options ? [...(value.options ?? []).filter(option => !value.options_hidden.includes(option)), ...value.options_add] : null;
    const change = (next: string) => values.set({ ...values.peek(), [name]: next });
    return field(name, `ctl-try-${name}`, choices ? Select({ id: `ctl-try-${name}`, value: () => values.get()[name] ?? '', options: () => [['', 'Empty'], ...choices.map(option => [option, option] as [string, string])], onChange: change })
      : html`<input class="input" id=${`ctl-try-${name}`} placeholder=${name.includes('date') ? 'YYYY-MM-DD' : ''} prop:value=${() => values.get()[name] ?? ''}
          on:input=${(event: Event) => change((event.target as HTMLInputElement).value)}>`, () => null, () => result.get()?.errors?.[name] ?? null);
  };
  return html`<div>
    <div class="toolbar"><span class="grow"></span>${() => ctx.drafts.overridden(id)
      ? [html`<span class="ctl-tag">Changed here</span>`, Button({ label: 'Reset to models.py', icon: 'rotate-ccw', size: 'small', variant: 'ghost', onClick: () => ctx.drafts.reset(id) })]
      : html`<span class="ctl-src s-file"><i></i>From models.py</span>`}
      ${Button({ label: 'Add series', icon: 'plus', size: 'small', variant: 'primary', onClick: () => editSeries(ctx, model, -1, values) })}</div>
    ${when(() => ctx.drafts.errors.get().has(id), () => Callout('err', 'info', () => ctx.drafts.errors.get().get(id)))}
    <div class="ctl-grid main">
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>${`Series for ${model.name}`}</h2>
        <p>Names go into <code>${`${model.name}.${model.naming!.field}`}</code> when a record is created${model.naming!.date_field ? `, dated by ${model.naming!.date_field}` : ''}</p></div></div>
        ${() => list().map(row)}${when(() => result.get()?.error, () => html`<p class="ctl-err ctl-pad">${() => result.get()?.error}</p>`)}</section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Try it</h2><p>Change the values to see which series is used</p></div></div>
        <div class="ctl-pad ctl-form">${inputs().map(info => input(info.name))}
          <div class="ctl-tryres"><span class="muted ctl-small">This record would be named</span><strong class="mono">${() => result.get()?.name ?? '…'}</strong>
            <span class="muted ctl-small">${() => result.get()?.label ? `by ${result.get()!.label}` : ''}</span></div>
          <p class="muted ctl-small">Trying a name never changes a counter.</p></div></section></div>
    <section class="ctl-card"><div class="ctl-card-head"><div><h2>Counters</h2><p>One counter per prefix. A new year or month starts a new counter at 1.</p></div></div>
      <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Prefix</th><th class="r">Current</th><th>Next name</th><th>Last change</th><th></th></tr></thead>
        <tbody>${() => counters.get().length ? counters.get().map(counter => html`<tr><td class="mono"><strong>${counter.prefix || '(none)'}</strong>
          ${counter.series ? null : html` <span class="ctl-badge">No series uses it</span>`}</td><td class="r num"><strong>${counter.value.toLocaleString('en')}</strong></td>
          <td class="mono">${counter.next}</td>
          <td>${counter.how === 'set' ? html`<span class="ctl-badge orange">Set by hand</span> ${counter.note ?? ''}` : counter.last_name ? html`<span class="mono">${counter.last_name}</span> issued` : 'Counter set'}
            ${counter.changed_by ? html` by <strong>${counter.changed_by}</strong>` : null}<span class="ctl-sub num">${stamp(counter.changed_at)}</span></td>
          <td class="r">${counter.series ? Button({ label: 'Set counter', size: 'small', onClick: () => setCounter(ctx, model, counter, loadCounters) }) : null}</td></tr>`)
          : html`<tr><td colspan="5"><div class="ctl-empty">No names issued yet.</div></td></tr>`}</tbody></table>
        <div class="ctl-tfoot"><span>Counters change when a record is created, inside the same transaction, so two people never get the same name.</span></div></div></section>
  </div>` as HTMLElement;
}

function editSeries(ctx: ControlsContext, model: ModelInfo, index: number, values: { peek(): Record<string, string> }) {
  const id = idOf('naming', model.name);
  const all = ctx.drafts.value<SeriesValue[]>(id);
  const fresh = index < 0, last = !fresh && index === all.length - 1;
  const draft = cell<SeriesValue>(fresh ? { id: `s${Date.now().toString(36)}`, label: '', format: '', when: '' } : { ...all[index] });
  const checked = cell<{ format?: string; when?: string; next?: string; matches?: boolean } | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined, open = true;
  const fields = model.fields_detail.map(info => info.name);
  const verify = async () => {
    const series = draft.peek();
    const probe: SeriesValue[] = last ? [series] : [series, { id: 'probe_last', label: 'Last', format: 'P-{###}', when: '' }];
    try {
      const reply = await ctx.api.tryName(model.name, { values: values.peek(), series: probe });
      if (open) checked.set({ ...reply.checks[0], matches: reply.match === series.id });
    } catch (failure) { if (open) checked.set({ format: message(failure) }); }
  };
  const stop = effect(() => { draft.get(); clearTimeout(timer); timer = setTimeout(() => void verify(), 200); });
  const update = (key: keyof SeriesValue) => (event: Event) => draft.set({ ...draft.peek(), [key]: (event.target as HTMLInputElement).value });
  const valid = derive(() => {
    const value = draft.get(), result = checked.get();
    return Boolean(value.label.trim() && value.format.trim() && result && !result.format && !result.when && (last || value.when.trim()));
  });
  const apply = (close: () => void) => {
    const value = { ...draft.peek(), label: draft.peek().label.trim(), when: last ? '' : draft.peek().when.trim() };
    const next = [...all];
    if (fresh) next.splice(next.length - 1, 0, value); else next[index] = value;
    ctx.drafts.set(id, next);
    close();
    toast(fresh ? 'Series added. Save changes to start using it.' : 'Series updated');
  };
  const insert = (token: string) => draft.set({ ...draft.peek(), format: draft.peek().format + token });
  openDrawer({ title: fresh ? 'New series' : all[index].label, icon: 'sliders-horizontal', tint: 'blue', className: 'ctl-drawer',
    onClose: () => { open = false; clearTimeout(timer); stop(); },
    body: () => html`<div class="ctl-form">
      <p class="muted ctl-small">${`${model.name} · ${last ? 'used when no other series matches' : fresh ? 'checked before the last series' : `checked in position ${index + 1}`}`}</p>
      ${field('Name', 'ctl-s-label', html`<input class="input" id="ctl-s-label" placeholder="Zero-rated invoices" prop:value=${() => draft.get().label} on:input=${update('label')}>`)}
      ${field('Format', 'ctl-s-format', html`<div><input class="input mono" id="ctl-s-format" spellcheck="false" prop:value=${() => draft.get().format} on:input=${update('format')}>
        <div class="ctl-chips ctl-tokens">${TOKENS.map(([token, title]) => html`<button type="button" class="ctl-chip" title=${title} on:click=${() => insert(token)}>${token}</button>`)}</div></div>`,
        () => checked.get()?.next ? `Next name: ${checked.get()!.next}. Field values work too, like {${fields.find(name => name.startsWith('x_')) ?? 'status'}}.` : null,
        () => checked.get()?.format ?? null)}
      ${last ? Callout('info', 'info', "The last series has no condition. It names every record the others don't match, so a record always gets a name.")
        : field('Condition', 'ctl-s-when', html`<textarea class="input mono ctl-ta" id="ctl-s-when" rows="2" spellcheck="false" placeholder='tax_rate == 0 and status != "draft"'
          prop:value=${() => draft.get().when} on:input=${update('when')}></textarea>`,
          () => !draft.get().when.trim() ? null : checked.get() && !checked.get()!.when ? `Valid. With the test record it ${checked.get()!.matches ? 'matches' : 'does not match'}.` : null,
          () => checked.get()?.when ?? (draft.get().when.trim() ? null : 'Add a condition, or this series catches everything below it.'))}
      ${last ? null : html`<section class="ctl-section"><h3>How to write a condition</h3><div class="table-wrap"><table class="dt ctl-table"><tbody>
        <tr><td class="mono">status == "sent"</td><td class="muted">equals</td></tr><tr><td class="mono">total &gt;= 100000</td><td class="muted">compare numbers with &gt; &gt;= &lt; &lt;=</td></tr>
        <tr><td class="mono">status in ("sent", "paid")</td><td class="muted">one of several values</td></tr><tr><td class="mono">x_branch != empty</td><td class="muted">has a value</td></tr>
        <tr><td class="mono">a == 1 and (b == 2 or not c == 3)</td><td class="muted">combine with and, or, not, ( )</td></tr></tbody></table></div>
        <p class="muted ctl-small">${`Only fields of ${model.name} can be used. No functions, no code. Fields: `}${fields.map((name, at) => [at ? ', ' : '', html`<button type="button" class="ctl-linkbtn mono"
          on:click=${() => draft.set({ ...draft.peek(), when: (draft.peek().when.trim() ? draft.peek().when.trim() + ' and ' : '') + name + ' == ' })}>${name}</button>`])}</p></section>`}
    </div>`,
    footer: close => html`${!fresh && !last ? Button({ label: 'Remove', icon: 'trash-2', variant: 'danger', onClick: () => {
        ctx.drafts.set(id, all.filter((_, at) => at !== index)); close(); } }) : null}<span class="grow"></span>
      ${Button({ label: 'Cancel', variant: 'ghost', onClick: close })}${Button({ label: fresh ? 'Add series' : 'Apply', variant: 'primary', disabled: () => !valid.get(), onClick: () => apply(close) })}`,
  });
}

function setCounter(ctx: ControlsContext, model: ModelInfo, counter: Counter, refresh: () => Promise<void>) {
  const value = cell(String(counter.value)), note = cell(''), error = cell<Record<string, string>>({}), busy = cell(false);
  const pad = counter.next.slice(counter.prefix.length).match(/^\d+/)?.[0].length ?? 4;
  const number = () => /^\d+$/.test(value.get().trim()) ? Number(value.get()) : NaN;
  const lower = () => counter.issued !== null && number() < counter.issued;
  const submit = async (close: () => void) => {
    busy.set(true);
    try {
      await ctx.api.setCounter(model.name, { prefix: counter.prefix, value: number(), note: note.peek().trim() });
      close();
      await refresh();
      toast(`Counter set. Next name is ${counter.prefix}${String(number() + 1).padStart(pad, '0')}`);
    } catch (failure) {
      error.set(failure instanceof ValidationError ? failure.fields : { _form: message(failure) });
    } finally { busy.set(false); }
  };
  openModal({ title: `Set counter for ${counter.prefix || '(no prefix)'}`, icon: 'hash', tint: 'blue', narrow: true,
    body: () => html`<div class="ctl-form">
      <dl class="ctl-kv"><dt>Current</dt><dd class="num">${counter.value}</dd><dt>Last name issued</dt><dd class="mono">${counter.last_name ?? 'None yet'}</dd>
        <dt>Last change</dt><dd>${[counter.changed_by, stamp(counter.changed_at)].filter(Boolean).join(' · ')}</dd></dl>
      ${field('New value', 'ctl-c-value', html`<input class="input num" id="ctl-c-value" type="number" min="0" prop:value=${value}
        on:input=${(event: Event) => { value.set((event.target as HTMLInputElement).value); error.set({}); }}>`,
        () => Number.isNaN(number()) ? 'Enter a whole number.' : lower() ? null : `The next record gets ${counter.prefix}${String(number() + 1).padStart(pad, '0')}.`,
        () => lower() ? `Lower than ${counter.last_name}, which already exists. The next names would clash with existing records.` : error.get().value ?? null)}
      ${field('Reason', 'ctl-c-note', html`<input class="input" id="ctl-c-note" placeholder="Continues from the old system" prop:value=${note}
        on:input=${(event: Event) => { note.set((event.target as HTMLInputElement).value); error.set({}); }}>`, () => 'Saved in the audit log with your name.', () => error.get().note ?? null)}
      ${when(() => error.get()._form || error.get().prefix, () => Callout('err', 'info', () => error.get()._form ?? error.get().prefix))}</div>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: () => busy.get() ? 'Saving…' : 'Set counter',
      disabled: () => busy.get() || lower() || Number.isNaN(number()) || !note.get().trim(), onClick: () => void submit(close) } }),
  });
}
