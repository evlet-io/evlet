// "Create new <Model>" from a Link dropdown: a small form for the target model in a dialog. It starts with
// the typed text in the title field and the values the Link's filters ask for (the customer of an invoice's
// project, say); Save creates the record through the usual create route, and the Link selects it.

import type { ApiClient, User } from '../api';
import { html } from '../dom';
import { RecordStore } from '../model/record';
import type { FieldSchema, ModelSchema, SchemaIndex } from '../model/schema';
import { BaseField } from '../ui/fields';
import { DialogFooter, openModal } from '../ui/layers';

/** At most this many fields show; the record page has the rest. */
const MAX_FIELDS = 8;

const editable = (field: FieldSchema) => !field.hidden && !field.read_only && field.stored !== false;

/** The fields the quick form shows, in the model's order: the title, required fields without a default, and the prefilled ones. */
export function quickCreateFields(model: ModelSchema, prefilled: string[]): string[] {
  return model.fields.filter(field => editable(field) && (field.name === model.title_field || prefilled.includes(field.name)
    || field.required && (field.default_kind ?? 'none') === 'none')).map(field => field.name).slice(0, MAX_FIELDS);
}

/** The values a new record starts with: the typed text in its title field, and the Link filter values for fields it has. */
export function quickCreateValues(model: ModelSchema, text: string, values: Record<string, unknown>): Record<string, unknown> {
  const fields = new Map(model.fields.map(field => [field.name, field]));
  const start: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(values)) if (fields.has(name) && editable(fields.get(name)!)) start[name] = value;
  const title = fields.get(model.title_field);
  if (text && title && editable(title) && ['text', 'email', 'phone', 'url'].includes(title.type)) start[title.name] = text;
  return start;
}

/** Open the quick form; resolves with the created record's id and title, or null when it is closed unsaved. */
export function quickCreate(options: { api: ApiClient; schema: SchemaIndex; user?: User | null }, model: string, text: string,
  values: Record<string, unknown>): Promise<{ id: string; title: string } | null> {
  const meta = options.schema.model(model);
  const start = quickCreateValues(meta, text, values);
  const store = new RecordStore({ api: options.api, schema: options.schema, model, user: options.user ?? null });
  void store.create(start);
  const names = quickCreateFields(meta, Object.keys(start));
  return new Promise(resolve => {
    let result: { id: string; title: string } | null = null;
    const save = async (close: () => void) => {
      if (store.saving.peek()) return;
      if (!(await store.save()) || !store.id) return;
      const id = String(store.id);
      const title = meta.title_field === 'id' ? `#${id}` : String(store.field(meta.title_field).value.peek() ?? `#${id}`);
      result = { id, title };
      close();
    };
    openModal({
      title: `New ${meta.label.toLowerCase()}`, icon: 'plus', tint: 'blue', narrow: true, className: 'quick-create',
      onClose: () => { store.dispose(); resolve(result); },
      body: () => html`<form class="form quick-create-form" on:submit=${(event: Event) => event.preventDefault()}>${names.map(name => BaseField({ field: store.field(name), full: true }))}</form>`,
      footer: close => DialogFooter({ note: () => store.formError.get() ?? '', cancel: close,
        submit: { label: () => store.saving.get() ? 'Saving' : `Create ${meta.label.toLowerCase()}`, onClick: () => void save(close) } }),
    });
  });
}
