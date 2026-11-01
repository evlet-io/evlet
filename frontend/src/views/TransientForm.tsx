import { For, Show, createSignal, onCleanup, onMount } from 'solid-js';
import { Field, Button } from '../ui';
import FieldInput from './FieldInput';
import { request } from '../api';
import { inputValue } from '../schema';
import { useApp, messageFor } from '../runtime/Context';
import { documentRead, documentValue, behaviorValue, fieldFilters, calculateDocument } from '../runtime/DocumentState';
import type { ModelSchema } from '../runtime/types';

export type FormDefinition = {name: string; label: string; fields: any[]; behavior: any; children: {name: string; label: string; allow_add?: boolean; definition: FormDefinition}[]};
export function formSchema(definition: FormDefinition): ModelSchema {
  return {...definition, title_field: 'id', plural: definition.label, list_fields: [], search_fields: [], document_version: 1,
    children: definition.children.map(table => ({name: table.name, label: table.label, model: table.definition.name, link: ''}))};
}
export function formPayload(definition: FormDefinition, values: any) {
  const payload: any = {};
  for (const field of definition.fields) {
    if (field.read_only || field.hidden) continue;
    if (values[field.name] == null && field.default_kind !== 'none') continue;
    payload[field.name] = inputValue(field, values[field.name]);
  }
  if (definition.children.length) payload.children = Object.fromEntries(definition.children.map(table => [table.name,
    (values.children?.[table.name] || []).map((row: any) => ({_key: row._key, ...formPayload(table.definition, row)}))]));
  return payload;
}
export default function TransientForm(props: {definition: FormDefinition; initial?: any; evaluateUrl: string; headers?: Record<string, string>; report?: boolean;
  onValues: (values: any, ready: boolean) => void}) {
  const app = useApp();
  const [values, setValues] = createSignal<any>(props.initial || {}), [errors, setErrors] = createSignal<Record<string,string>>({}), [pending, setPending] = createSignal(false), [states, setStates] = createSignal<Record<string,string>>({});
  let sequence = 0, alive = true, timer: ReturnType<typeof setTimeout> | undefined;
  const model = formSchema(props.definition);
  const models = [model, ...props.definition.children.map(table => formSchema(table.definition)), ...app.models];
  const ready = () => !pending() && !Object.keys(errors()).length && !Object.values(states()).includes('required-input') && props.definition.fields.every(field => !field.required || values()[field.name] != null && values()[field.name] !== '');
  function schedule() {
    const revision = ++sequence;
    clearTimeout(timer);
    setPending(true);
    props.onValues(values(), false);
    timer = setTimeout(async () => {
      try {
        const payload = formPayload(props.definition, values());
        const result = await request(props.evaluateUrl, {method: 'POST', headers: {'Content-Type': 'application/json', ...props.headers},
          body: JSON.stringify({[props.report ? 'filters' : 'values']: payload, revision})});
        if (!alive || revision !== sequence || revision !== result.revision) return;
        setValues(result.values);
        setErrors(result.errors || {});
        setStates(result.states || {});
        setPending(false);
        props.onValues(result.values, ready());
      } catch (error) {
        if (!alive || revision !== sequence) return;
        setErrors({_form: messageFor(error)}); setPending(false); props.onValues(values(), false);
      }
    }, 180);
  }
  function change(name: string, value: any) {
    const result = calculateDocument(model, models, {...values(), [name]: value}, values().children || {});
    setValues({...result.values, children: result.children});
    setErrors(result.errors);
    schedule();
  }
  function rowsChanged(name: string, rows: any[]) {
    const result = calculateDocument(model, models, values(), {...(values().children || {}), [name]: rows});
    setValues({...result.values, children: result.children}); setErrors(result.errors); schedule();
  }
  function condition(definition: FormDefinition, row: any, field: any, kind: string, fallback: boolean, parent?: any) {
    const expression = definition.behavior?.[field.name]?.[kind];
    if (!expression) return fallback;
    try { return !!behaviorValue(expression, documentRead(formSchema(definition), row, values().children || {}, models, parent)); }
    catch { return kind === 'read_only_if'; }
  }
  const controls = (definition: FormDefinition, row: () => any, changed: (name: string, value: any) => void, parent?: any) => <For each={definition.fields.filter(field => !field.hidden)}>{field =>
    <Show when={condition(definition, row(), field, 'visible_if', true, parent)}>
      <Field label={field.label} error={errors()[field.name]} required={condition(definition, row(), field, 'required_if', field.required, parent)}>
        {control => <FieldInput field={field} id={control.id} value={row()[field.name]}
          required={condition(definition, row(), field, 'required_if', field.required, parent)}
          disabled={field.read_only || condition(definition, row(), field, 'read_only_if', false, parent)}
          filters={fieldFilters(formSchema(definition), field, row(), values().children || {}, models, parent)}
          onChange={value => changed(field.name, value)} />}
      </Field>
    </Show>
  }</For>;
  onMount(schedule);
  onCleanup(() => {alive = false; sequence++; clearTimeout(timer);});
  return <div class="transient-fields">
    {controls(props.definition, values, change)}
    <For each={props.definition.children}>{table => <section class="child-collection"><h3>{table.label}</h3>
      <For each={(values().children?.[table.name] || []).map((row: any) => row._key)}>{(key, index) => <fieldset class="child-row"><legend>{table.label} {index() + 1}</legend>
        {controls(table.definition, () => values().children[table.name][index()], (name, value) => {
          rowsChanged(table.name, values().children[table.name].map((item: any, position: number) => position === index() ? {...item, [name]: value} : item));
        }, {model, get values() {return values();}})}
        <Button onClick={() => rowsChanged(table.name, values().children[table.name].filter((item: any) => item._key !== key))}>Remove row</Button>
      </fieldset>}</For>
      <Show when={table.allow_add !== false}><Button disabled={(values().children?.[table.name]?.length || 0) >= 100} onClick={() => {
        const row: any = {_key: crypto.randomUUID()};
        for (const field of table.definition.fields) if (field.default_kind === 'static') row[field.name] = field.default;
        for (const field of table.definition.fields) if (table.definition.behavior?.[field.name]?.default_ref) {
          try {row[field.name] = documentValue(field, behaviorValue(table.definition.behavior[field.name].default_ref, documentRead(formSchema(table.definition), row, {}, models, {model, values: values()})));} catch {}
        }
        rowsChanged(table.name, [...(values().children?.[table.name] || []), row]);
      }}>Add row</Button></Show>
    </section>}</For>
    <Show when={pending()}><p role="status">Updating fields…</p></Show>
    <For each={Object.entries(errors())}>{([name, error]) => <p role="alert">{name}: {error}</p>}</For>
  </div>;
}
