import {
  createSignal,
  createEffect,
  on,
  onCleanup,
  onMount,
  For,
  Show,
} from "solid-js";
import {
  Button,
  Badge,
  PageHeader,
  Field,
  Modal,
  ActionMenu,
  Glyph,
} from "../ui";
import FieldInput from "./FieldInput";
import TransientForm, { formPayload } from './TransientForm';
import NativePanels from './NativePanels';
import { request, recordRequest } from "../api";
import { inputValue, visibleFields, recordLabel, formatValue } from "../schema";
import { useApp, messageFor } from "../runtime/Context";
import { NodeRenderer, makeRuntime } from "../runtime/ViewRenderer";
import { createState } from "../runtime/StateStore";
import { calculateDocument, documentRead, documentValue, behaviorValue, fieldFilters } from '../runtime/DocumentState';
import type { ModelSchema, RecordData, FieldSchema } from "../runtime/types";

export function buildRecordPayload(
  model: ModelSchema,
  models: ModelSchema[],
  values: Record<string, any>,
  dirty: Set<string>,
  existing: boolean,
  collections: Record<string, any[]>,
  childDirty: Set<string>,
) {
  const data: Record<string, any> = {},
    errors: Record<string, string> = {};
  for (const field of visibleFields(model)) {
    if (
      field.read_only ||
      (!dirty.has(field.name) && (existing || field.default_kind !== "none"))
    )
      continue;
    try {
      data[field.name] = inputValue(field, values[field.name]);
    } catch (e) {
      errors[field.name] = messageFor(e);
    }
  }
  const children: Record<string, any[]> = {};
  for (const child of model.children || []) {
    if (!childDirty.has(child.name)) continue;
    const childModel = models.find((m) => m.name === child.model)!;
    children[child.name] = (collections[child.name] || []).map((row) => {
      const item: Record<string, any> = row.id ? { id: row.id } : {};
      if (model.document_version && row._key) item._key = row._key;
      if (row.id && childModel.permissions?.update === false) return item;
      for (const field of visibleFields(childModel).filter(
        (f) => f.name !== child.link && !f.read_only,
      )) {
        try {
          if (row[field.name] != null || field.default_kind === "none")
            item[field.name] = inputValue(field, row[field.name]);
        } catch (e) {
          errors.children = messageFor(e);
        }
      }
      return item;
    });
  }
  if (Object.keys(children).length) data.children = children;
  return { data, errors };
}
export default function RecordForm(props: {
  model: ModelSchema;
  recordId: string;
  readOnly?: boolean;
}) {
  const app = useApp(),
    model = props.model,
    base = "/api/" + model.name,
    isNew = props.recordId === "new",
    recordId = Number(props.recordId);
  const view = app.ui.views.find(
    (v) => v.name === model.name + "." + (props.readOnly ? "detail" : "form"),
  )!;
  const state = createState(
    view.state,
    view.computed,
    app.applicationState || {
      user: app.user,
      tenant: app.tenant,
    },
  );
  const [record, setRecord] = createSignal<RecordData | null>(null),
    [etag, setEtag] = createSignal(""),
    [loading, setLoading] = createSignal(!isNew),
    [busy, setBusy] = createSignal(false),
    [errors, setErrors] = createSignal<Record<string, string>>({}),
    [error, setError] = createSignal(""),
    [dirty, setDirty] = createSignal<Set<string>>(new Set()),
    [childDirty, setChildDirty] = createSignal<Set<string>>(new Set()),
    [collections, setCollections] = createSignal<Record<string, any[]>>({}),
    [stale, setStale] = createSignal(false),
    [dialog, setDialog] = createSignal(""),
    [job, setJob] = createSignal<any>(null),
    [jobError, setJobError] = createSignal("");
  const [businessDialog, setBusinessDialog] = createSignal<any>(null),
    [dialogValues, setDialogValues] = createSignal<any>({}),
    [dialogReady, setDialogReady] = createSignal(false);
  const [previewPending, setPreviewPending] = createSignal(false);
  const [querySources, setQuerySources] = createSignal<any>({});
  const [argumentAction, setArgumentAction] = createSignal<any>(null), [argumentValues, setArgumentValues] = createSignal<any>({});
  let alive = true,
    sequence = 0,
    draftRevision = 0,
    previewTimer: ReturnType<typeof setTimeout> | undefined,
    jobTimer: ReturnType<typeof setTimeout> | undefined,
    form!: HTMLFormElement;
  const canEdit = () =>
    !props.readOnly &&
    model.permissions?.[isNew ? "create" : "update"] !== false;
  const isDirty = () => dirty().size > 0 || childDirty().size > 0;
  const change = (field: FieldSchema, value: any) => {
    state.write("record." + field.name, value);
    setDirty((old) => new Set([...old, field.name]));
    setErrors((old) => {
      const copy = { ...old };
      delete copy[field.name];
      return copy;
    });
    recalculate();
  };
  function condition(definition: ModelSchema, field: FieldSchema, kind: string, values: any, fallback: boolean, parent?: {model: ModelSchema; values: any}) {
    const expression = definition.behavior?.[field.name]?.[kind];
    if (!expression) return fallback;
    try { return !!behaviorValue(expression, documentRead(definition, values, collections(), app.models, parent)); }
    catch { return kind === 'read_only_if'; }
  }
  function recalculate(hydrating = false) {
    if (!model.document_version) return;
    setQuerySources({});
    for (const field of model.fields) if (model.behavior?.[field.name]?.query) state.write('record.' + field.name, null);
    setCollections(current => Object.fromEntries(Object.entries(current).map(([name, rows]) => {
      const table = model.children?.find(table => table.name === name);
      const definition = app.models.find(item => item.name === table?.model);
      return [name, rows.map(row => ({...row, _sources: {}, ...Object.fromEntries((definition?.fields || []).filter(field => definition?.behavior?.[field.name]?.query).map(field => [field.name, null]))}))];
    })));
    const result = calculateDocument(model, app.models, {...state.state.record, _sources: querySources()}, collections());
    for (const field of model.fields) if (model.behavior?.[field.name]?.compute && (!hydrating || field.stored === false)) state.write('record.' + field.name, result.values[field.name]);
    if (!hydrating) setCollections(result.children);
    setErrors(result.errors);
    const revision = ++draftRevision;
    clearTimeout(previewTimer);
    setPreviewPending(true);
    previewTimer = setTimeout(async () => {
      const payload = buildRecordPayload(model, app.models, state.state.record, dirty(), !isNew, collections(), childDirty());
      if (Object.keys(payload.errors).length) {setPreviewPending(false); return;}
      try {
        const response = await request('/api/_draft/' + model.name, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: isNew ? null : recordId, values: payload.data, revision})});
        if (!alive || revision !== draftRevision || response.revision !== revision) return;
        setQuerySources(response.values._sources || {});
        for (const field of model.fields) if ((!hydrating || field.stored === false) && ['query', 'compute', 'fetch'].some(kind => model.behavior?.[field.name]?.[kind]) || isNew && field.default_kind === 'callable' && !dirty().has(field.name)) state.write('record.' + field.name, response.values[field.name]);
        setCollections(current => Object.fromEntries(Object.entries(current).map(([name, rows]) => {
          const table = model.children?.find(table => table.name === name);
          const definition = app.models.find(item => item.name === table?.model);
          return [name, rows.map(row => {
            const resolved = response.values.children?.[name]?.find((candidate: any) => row.id ? candidate.id === row.id : candidate._key === row._key);
            if (!resolved) return row;
            const next = {...row, _sources: resolved._sources || {}};
            for (const field of definition?.fields || []) if ((!hydrating || field.stored === false) && ['query', 'compute', 'fetch'].some(kind => definition?.behavior?.[field.name]?.[kind])) next[field.name] = resolved[field.name];
            return next;
          })];
        })));
        setErrors(response.errors || {});
      } catch (error) { if (alive && revision === draftRevision) setError(messageFor(error)); }
      finally {if (alive && revision === draftRevision) setPreviewPending(false);}
    }, 200);
  }
  function initialize(data: RecordData | null, revision = "") {
    ++draftRevision;
    clearTimeout(previewTimer);
    setPreviewPending(false);
    setRecord(data);
    setEtag(revision);
    setDirty(new Set<string>());
    setChildDirty(new Set<string>());
    setStale(false);
    setErrors({});
    state.write("record.id", data?.id);
    for (const field of model.fields)
      state.write(
        "record." + field.name,
        data
          ? data[field.name]
          : field.default_kind === "static"
            ? field.default
            : field.type === "boolean" && field.required
              ? false
              : null,
      );
    setCollections(
      Object.fromEntries(
        (model.children || []).map((child) => [
          child.name,
          structuredClone(data?.children?.[child.name] || []).map((row: any) => ({...row, _key: String(row.id)})),
        ]),
      ),
    );
    if (model.document_version) recalculate(true);
  }
  async function load() {
    if (isNew) return;
    const current = ++sequence;
    setLoading(true);
    setError("");
    try {
      const snapshot = await request(base + "/" + recordId, {
        withMeta: true,
      } as any);
      if (alive && current === sequence)
        initialize(snapshot.data, snapshot.etag || "");
    } catch (e) {
      if (alive && current === sequence) setError(messageFor(e));
    } finally {
      if (alive && current === sequence) setLoading(false);
    }
  }
  async function reloadDraft() {
    if (isDirty() && !(await app.dialogs.confirm({
      title: "Reload this record?",
      message: "Your unsaved changes will be discarded and replaced with the latest saved record.",
      confirmLabel: "Discard and reload",
      cancelLabel: "Keep editing",
    }))) return;
    if (alive) await load();
  }
  async function save(e?: SubmitEvent) {
    e?.preventDefault();
    if (busy() || !canEdit() || loading()) return;
    const payload = buildRecordPayload(
      model,
      app.models,
      state.state.record,
      dirty(),
      !isNew,
      collections(),
      childDirty(),
    );
    setErrors(payload.errors);
    if (Object.keys(payload.errors).length) {
      void app.dialogs.error(Object.values(payload.errors).join("\n"), "Check your entries");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const saved = await request(base + (isNew ? "" : "/" + recordId), {
        method: isNew ? "POST" : "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...(!isNew && etag() ? { "If-Match": etag() } : {}),
        },
        body: JSON.stringify(payload.data),
        withMeta: true,
      } as any);
      if (!alive) return;
      initialize(saved.data, saved.etag || "");
      app.notify(isNew ? model.label + " created." : "Changes saved.");
      if (isNew)
        app.router.navigate(
          "/app/" + model.name + "/" + saved.data.id,
          true,
          true,
        );
    } catch (e: any) {
      if (alive) {
        void app.dialogs.error(messageFor(e), "Unable to save");
        setErrors(e.fields || {});
        setError(messageFor(e));
        if (e.status === 409) setStale(true);
      }
    } finally {
      if (alive) setBusy(false);
    }
  }
  const runtime = makeRuntime(view, {
    state,
    submit: () => form?.requestSubmit(),
    refresh: () => {
      if (isDirty()) setStale(true);
      else void load();
    },
    field: (node, p) => {
      const field = model.fields.find((f) => f.name === node.props.field);
      if (!field) return null;
      const required =
        condition(model, field, 'required_if', state.state.record, p.required ?? field.required) &&
        (field.default_kind === "none" || field.type === "boolean");
      return (
        <Show when={condition(model, field, 'visible_if', state.state.record, true)}>
        <Field
          class={
            field.type === "boolean" && field.required
              ? "ev-field-check"
              : undefined
          }
          label={p.label || field.label}
          required={required && !field.read_only && canEdit()}
          error={errors()[field.name]}
          help={[
            field.help_text,
            field.read_only ? "Read only." : "",
            field.default_kind === "callable" && isNew
              ? "Leave unchanged to use the server default."
              : "",
            ["currency", "decimal"].includes(field.type)
              ? `Up to ${field.scale} decimal places.`
              : "",
            field.type === "datetime"
              ? "Type a date and time. The saved timezone is kept."
              : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          {(control) => (
            <FieldInput
              field={field}
              prefix={p.prefix}
              suffix={p.suffix}
              id={control.id}
              required={required}
              disabled={!canEdit() || busy() || field.read_only || p.disabled || condition(model, field, 'read_only_if', state.state.record, false)}
              filters={fieldFilters(model, field, state.state.record, collections(), app.models)}
              invalid={control.invalid}
              describedBy={control.describedBy}
              value={state.state.record[field.name]}
              onChange={(value) => {
                change(field, value);
                void runtime.dispatch(
                  [
                    ...(node.events.on_input || []),
                    ...(node.events.on_change || []),
                  ],
                  node,
                  value,
                );
              }}
            />
          )}
        </Field>
        </Show>
      );
    },
  });
  function updateChild(name: string, index: number, field: string, value: any) {
    setCollections((old) => ({
      ...old,
      [name]: old[name].map((row, i) =>
        i === index ? { ...row, [field]: value } : row,
      ),
    }));
    setChildDirty((old) => new Set([...old, name]));
    recalculate();
  }
  function childFields(child: any) {
    return visibleFields(
      app.models.find((m) => m.name === child.model)!,
    ).filter((f) => f.name !== child.link);
  }
  function childPermission(
    child: any,
    operation: "create" | "update" | "delete",
  ) {
    return (
      app.models.find((m) => m.name === child.model)?.permissions?.[
        operation
      ] !== false
    );
  }
  async function action(name: string, argumentsValue?: Record<string, any>) {
    if (busy() || isDirty()) return;
    const definition = model.actions?.find(item => item.name === name);
    if (argumentsValue === undefined && definition?.arguments?.length) {
      setArgumentValues(Object.fromEntries(definition.arguments.map(item => [item.name, item.default ?? (item.type === 'bool' ? false : '')])));
      setArgumentAction(definition); return;
    }
    setBusy(true);
    setError("");
    try {
      if (model.actions?.find(item => item.name === name)?.dialog) {
        const loaded = await request('/api/_dialogs/' + model.name + '/' + recordId + '/' + name + '/load', {
          method: 'POST', headers: {'Content-Type': 'application/json', 'If-Match': etag()}, body: '{}'});
        if (alive) {
          setDialogValues(loaded.values);
          setBusinessDialog({...loaded, name, key: crypto.randomUUID(), etag: etag()});
        }
        return;
      }
      const result = await request(base + "/" + recordId + "/actions/" + name, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(etag() ? { "If-Match": etag() } : {}),
        },
        body: JSON.stringify(argumentsValue || {}),
      });
      if (!alive) return;
      setArgumentAction(null);
      await load();
      if (result.job) {
        setJob(result.job);
        setJobError("");
        clearTimeout(jobTimer);
        jobTimer = setTimeout(() => pollJob(result.job.id), 500);
      } else app.notify("Action completed.");
    } catch (e) {
      if (alive) {
        setError(messageFor(e));
        void app.dialogs.error(messageFor(e));
      }
    } finally {
      if (alive) setBusy(false);
    }
  }
  async function submitBusinessDialog() {
    const current = businessDialog();
    if (!current || !dialogReady() || busy()) return;
    setBusy(true);
    try {
      await request('/api/_dialogs/' + model.name + '/' + recordId + '/' + current.name + '/submit', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'If-Match': current.etag},
        body: JSON.stringify({key: current.key, values: formPayload(current.definition, dialogValues())})});
      if (!alive) return;
      setBusinessDialog(null);
      await load();
      app.notify('Action completed.');
    } catch (error) {if (alive) void app.dialogs.error(messageFor(error), 'Unable to complete action');}
    finally {if (alive) setBusy(false);}
  }
  async function pollJob(id: number) {
    if (!alive || job()?.id !== id) return;
    try {
      const status = await request("/api/_jobs/" + id);
      if (!alive || job()?.id !== id) return;
      setJob(status);
      setJobError("");
      if (["queued", "running"].includes(status.state))
        jobTimer = setTimeout(() => pollJob(id), 1000);
      else if (status.state === "succeeded") {
        if (isDirty()) setStale(true);
        else await load();
      }
    } catch (e: any) {
      if (!alive || job()?.id !== id) return;
      setJobError(messageFor(e));
      if (e.retryable) jobTimer = setTimeout(() => pollJob(id), 3000);
    }
  }
  async function remove() {
    if (busy()) return;
    setBusy(true);
    setError("");
    try {
      await request(base + "/" + recordId, {
        method: "DELETE",
        headers: etag() ? { "If-Match": etag() } : {},
      });
      if (!alive) return;
      setDirty(new Set<string>());
      setChildDirty(new Set<string>());
      app.notify(model.label + " deleted.");
      app.router.navigate("/app/" + model.name, true, true);
    } catch (e) {
      if (alive) {
        setDialog("");
        setError(messageFor(e));
        void app.dialogs.error(messageFor(e), "Unable to delete record");
      }
    } finally {
      if (alive) setBusy(false);
    }
  }
  onMount(() => {
    if (isNew) initialize(null);
    else void load();
    app.router.setGuard(() => isDirty() || busy());
  });
  createEffect(
    on(
      app.refresh,
      () => {
        if (isNew || busy()) return;
        if (isDirty()) setStale(true);
        else void load();
      },
      { defer: true },
    ),
  );
  onCleanup(() => {
    alive = false;
    sequence++;
    clearTimeout(jobTimer);
    clearTimeout(previewTimer);
    draftRevision++;
    app.router.setGuard();
  });
  return (
    <div class="form-view">
      <PageHeader
        title={
          isNew
            ? "New " + model.label.toLowerCase()
            : record()
              ? recordLabel(model, record()!)
              : model.label
        }
        subtitle={
          isNew
            ? "Add a record to " + model.plural.toLowerCase() + "."
            : model.label + " · #" + recordId
        }
        badge={
          <Badge tone={isDirty() ? "warning" : "success"}>
            {isDirty()
              ? "Unsaved"
              : props.readOnly
                ? "Read only"
                : isNew
                  ? "New"
                  : "Saved"}
          </Badge>
        }
        actions={
          canEdit()
            ? [
                {
                  id: "save",
                  label: busy()
                    ? "Saving…"
                    : isNew
                      ? "Create " + model.label.toLowerCase()
                      : "Save changes",
                  disabled: busy() || loading(),
                  onSelect: () => form?.requestSubmit(),
                },
              ]
            : []
        }
        trailing={
          <>
            <Button
              disabled={busy()}
              onClick={() => app.router.navigate("/app/" + model.name)}
            >
              Back to list
            </Button>
            <Show when={!isNew && record()}>
              <ActionMenu
                actions={[
                  ...(props.readOnly && model.permissions?.update !== false
                    ? [
                        {
                          id: "edit",
                          label: "Edit record",
                          onSelect: () =>
                            app.router.navigate(
                              "/app/" + model.name + "/" + recordId,
                            ),
                        },
                      ]
                    : []),
                  ...(model.actions || []).map((item) => ({
                    id: item.name,
                    label: item.label,
                    onSelect: () => void action(item.name),
                    disabled: busy() || isDirty(),
                  })),
                  ...(model.permissions?.delete !== false
                    ? [
                        {
                          id: "delete",
                          label: "Delete record",
                          destructive: true,
                          onSelect: () => setDialog("delete"),
                          disabled: busy(),
                        },
                      ]
                    : []),
                ]}
              />
            </Show>
          </>
        }
      />
      <div class="form-scroll">
        <Show when={error()}>
          <p role="alert" class="error-banner">
            {error()}{" "}
            <Show when={!isNew}>
              <Button
                onClick={reloadDraft}
              >
                Reload
              </Button>
            </Show>
          </p>
        </Show>
        <Show when={stale()}>
          <div class="stale-banner" role="status">
            The server has newer changes. Your draft is still here.{" "}
            <Button
              size="small"
              onClick={reloadDraft}
            >
              Reload record
            </Button>
          </div>
        </Show>
        <Show when={loading()}>
          <p role="status">Loading record…</p>
        </Show>
        <Show when={!loading() && (isNew || record())}>
          <div class="record-columns">
            <form ref={form} class="record-form" onSubmit={save}>
              <fieldset disabled={busy() || !canEdit()}>
                <NodeRenderer node={view.layout} runtime={runtime} />
                <For each={model.children || []}>
                  {(child) => (
                    <section class="child-collection">
                      <header>
                        <h2>{child.label}</h2>
                        <Badge>{collections()[child.name]?.length || 0}</Badge>
                      </header>
                      <For each={(collections()[child.name] || []).map(row => row._key || String(row.id))}>
                        {(key, index) => {const row = () => (collections()[child.name] || []).find(item => (item._key || String(item.id)) === key) || {}; return (
                          <fieldset class="child-row">
                            <legend>
                              {child.label} {index() + 1}
                            </legend>
                            <div class="child-fields">
                              <For each={childFields(child)}>
                                {(field) => (
                                  <Show when={condition(app.models.find(item => item.name === child.model)!, field, 'visible_if', row(), true, {model, values: state.state.record})}>
                                  <Field
                                    class={
                                      field.type === "boolean" && field.required
                                        ? "ev-field-check"
                                        : undefined
                                    }
                                    label={field.label}
                                    error={errors()[child.name + '.' + key + '.' + field.name]}
                                    required={
                                      condition(app.models.find(item => item.name === child.model)!, field, 'required_if', row(), field.required, {model, values: state.state.record}) && !field.read_only
                                    }
                                  >
                                    {(control) => (
                                      <FieldInput
                                        field={field}
                                        id={control.id}
                                        value={row()[field.name]}
                                        filters={fieldFilters(app.models.find(item => item.name === child.model)!, field, row(), collections(), app.models, {model, values: state.state.record})}
                                        disabled={
                                          field.read_only ||
                                          condition(app.models.find(item => item.name === child.model)!, field, 'read_only_if', row(), false, {model, values: state.state.record}) ||
                                          !childPermission(
                                            child,
                                            row().id ? "update" : "create",
                                          ) ||
                                          !canEdit() ||
                                          busy()
                                        }
                                        onChange={(value) =>
                                          updateChild(
                                            child.name,
                                            index(),
                                            field.name,
                                            value,
                                          )
                                        }
                                      />
                                    )}
                                  </Field>
                                  </Show>
                                )}
                              </For>
                            </div>
                            <Show when={model.document_version && canEdit() && childPermission(child, 'update')}>
                              <Button size="small" disabled={busy() || index() === 0} onClick={() => {
                                setCollections(current => {const rows = [...current[child.name]]; [rows[index()-1], rows[index()]] = [rows[index()], rows[index()-1]]; return {...current, [child.name]: rows};});
                                setChildDirty(current => new Set([...current, child.name])); recalculate();
                              }}>Move up</Button>
                            </Show>
                            <Show
                              when={
                                canEdit() &&
                                (!row().id || childPermission(child, "delete"))
                              }
                            >
                              <Button
                                size="small"
                                disabled={busy()}
                                onClick={() => {
                                  setCollections((old) => ({
                                    ...old,
                                    [child.name]: old[child.name].filter(
                                      (_, i) => i !== index(),
                                    ),
                                  }));
                                  setChildDirty(
                                    (old) => new Set([...old, child.name]),
                                  );
                                  recalculate();
                                }}
                              >
                                Remove row
                              </Button>
                            </Show>
                          </fieldset>
                        );}}
                      </For>
                      <Show
                        when={canEdit() && childPermission(child, "create")}
                      >
                        <Button
                          disabled={
                            busy() ||
                            (collections()[child.name]?.length || 0) >= 100
                          }
                          onClick={() => {
                            const row = Object.fromEntries(
                              childFields(child).map((field) => [
                                field.name,
                                field.default_kind === "static"
                                  ? field.default
                                  : null,
                              ]),
                            );
                            row._key = crypto.randomUUID();
                            const childModel = app.models.find(item => item.name === child.model)!;
                            for (const field of childFields(child)) {
                              const expression = childModel.behavior?.[field.name]?.default_ref;
                              if (expression) {
                                try { row[field.name] = documentValue(field, behaviorValue(expression, documentRead(childModel, row, collections(), app.models, {model, values: state.state.record}))); }
                                catch { row[field.name] = null; }
                              }
                            }
                            setCollections((old) => ({
                              ...old,
                              [child.name]: [...(old[child.name] || []), row],
                            }));
                            setChildDirty(
                              (old) => new Set([...old, child.name]),
                            );
                            recalculate();
                          }}
                        >
                          Add {child.label.toLowerCase()} row
                        </Button>
                      </Show>
                    </section>
                  )}
                </For>
                <Show when={errors().children}>
                  <p role="alert" class="error-banner">
                    {errors().children}
                  </p>
                </Show>
                <Show when={previewPending()}><p role="status">Updating derived values…</p></Show>
                <For each={Object.entries(errors()).filter(([path]) => path.includes('.'))}>{([path, message]) => <p role="alert">{path}: {message}</p>}</For>
                <Show when={canEdit()}>
                  <div class="form-actions">
                    <Button variant="primary" type="submit" disabled={busy()}>
                      {busy()
                        ? "Saving…"
                        : isNew
                          ? "Create " + model.label.toLowerCase()
                          : "Save changes"}
                    </Button>
                    <Button
                      onClick={() => app.router.navigate("/app/" + model.name)}
                    >
                      Cancel
                    </Button>
                  </div>
                </Show>
              </fieldset>
            </form>
            <aside class="record-aside">
              <NativePanels target={model.resource || model.name} record={state.state.record} />
              <h3>{model.label}</h3>
              <p>
                {isNew
                  ? "Fill in the details and save when you’re ready."
                  : "Saved in your workspace database."}
              </p>
              <Show when={!isNew}>
                <dl>
                  <div>
                    <dt>Record ID</dt>
                    <dd>#{recordId}</dd>
                  </div>
                  <div>
                    <dt>Access</dt>
                    <dd>{canEdit() ? "Can edit" : "Read only"}</dd>
                  </div>
                </dl>
              </Show>
              <Show when={isDirty()}>
                <p class="draft-note">You have unsaved changes.</p>
              </Show>
              <Show when={job()}>
                <p role="status">
                  Background task:{" "}
                  {job().state === "succeeded" ? "completed" : job().state}
                </p>
              </Show>
              <Show when={jobError()}>
                <p role="alert" class="error-banner">
                  {jobError()}
                </p>
              </Show>
              <Show when={!isNew && model.attachments && record()}>
                <Attachments
                  model={model}
                  recordId={recordId}
                  disabled={busy()}
                  revision={app.refresh()}
                />
              </Show>
            </aside>
          </div>
        </Show>
      </div>
      <Modal
        title={"Delete " + model.label.toLowerCase() + "?"}
        open={dialog() === "delete"}
        onClose={() => {
          if (!busy()) setDialog("");
        }}
        footer={
          <>
            <Button disabled={busy()} onClick={() => setDialog("")}>
              Cancel
            </Button>
            <Button variant="primary" disabled={busy()} onClick={remove}>
              Delete record
            </Button>
          </>
        }
      >
        <p>
          This removes this record and its child rows. This cannot be undone.
        </p>
      </Modal>
      <Show when={argumentAction()} keyed>{current => <Modal title={current.label} open onClose={() => {if (!busy()) setArgumentAction(null);}}
        footer={<><Button disabled={busy()} onClick={() => setArgumentAction(null)}>Cancel</Button><Button variant="primary" disabled={busy() || current.arguments.some((item: any) => item.required && argumentValues()[item.name] === '')} onClick={() => {
          const values = Object.fromEntries(current.arguments.map((item: any) => [item.name, ['int', 'float'].includes(item.type) ? Number(argumentValues()[item.name]) : argumentValues()[item.name]]));
          void action(current.name, values);
        }}>Run</Button></>}>
        <For each={current.arguments}>{(item: any) => <Field label={item.name.replaceAll('_', ' ')} required={item.required}>{control =>
          <input id={control.id} type={item.type === 'bool' ? 'checkbox' : ['int', 'float'].includes(item.type) ? 'number' : 'text'}
            step={item.type === 'int' ? 1 : 'any'} checked={argumentValues()[item.name] === true} value={argumentValues()[item.name]}
            onInput={event => setArgumentValues((values: any) => ({...values, [item.name]: item.type === 'bool' ? event.currentTarget.checked : event.currentTarget.value}))} />
        }</Field>}</For>
      </Modal>}</Show>
      <Show when={businessDialog()} keyed>{current => <Modal title={current.definition.label} open onClose={() => {if (!busy()) setBusinessDialog(null);}}
        footer={<><Button disabled={busy()} onClick={() => setBusinessDialog(null)}>Cancel</Button><Button variant="primary" disabled={busy() || !dialogReady()} onClick={submitBusinessDialog}>Submit</Button></>}>
        <NativePanels target={(model.resource || model.name) + '.' + current.name} record={record()} />
        <fieldset disabled={busy()}><TransientForm definition={current.definition} initial={current.values}
          evaluateUrl={'/api/_dialogs/' + model.name + '/' + recordId + '/' + current.name + '/evaluate'} headers={{'If-Match': current.etag}}
          onValues={(values, ready) => {setDialogValues(values); setDialogReady(ready);}} /></fieldset>
      </Modal>}</Show>
    </div>
  );
}
function Attachments(props: {
  model: ModelSchema;
  recordId: number;
  disabled: boolean;
  revision: number;
}) {
  const app = useApp();
  const [files, setFiles] = createSignal<any[]>([]),
    [error, setError] = createSignal(""),
    [busy, setBusy] = createSignal(false);
  let alive = true,
    seq = 0;
  const base = `/api/${props.model.name}/${props.recordId}/attachments`;
  async function load() {
    const current = ++seq;
    try {
      const result = await request(base);
      if (alive && seq === current) setFiles(result.items);
    } catch (e) {
      if (alive) setError(messageFor(e));
    }
  }
  createEffect(
    on(
      () => props.revision,
      () => {
        void load();
      },
    ),
  );
  onCleanup(() => {
    alive = false;
    seq++;
  });
  async function upload(event: Event & { currentTarget: HTMLInputElement }) {
    const input = event.currentTarget,
      file = input.files?.[0];
    if (!file || busy() || props.disabled) return;
    setError("");
    if (!file.size || file.size > 1024 * 1024) {
      setError("Use a nonempty file up to 1 MiB.");
      void app.dialogs.error("Use a nonempty file up to 1 MiB.", "Unable to attach file");
      input.value = "";
      return;
    }
    setBusy(true);
    seq++;
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let i = 0; i < bytes.length; i += 8192)
        binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
      if (!alive) return;
      const saved = await recordRequest(
        props.model.name,
        props.recordId,
        "attachments",
        { name: file.name, content: btoa(binary) },
      );
      if (alive) setFiles((old) => [...old, saved]);
    } catch (e) {
      if (alive) {
        setError(messageFor(e));
        void app.dialogs.error(messageFor(e), "Unable to update attachments");
      }
    } finally {
      if (alive) setBusy(false);
      input.value = "";
    }
  }
  async function remove(id: string) {
    if (busy() || props.disabled) return;
    setBusy(true);
    seq++;
    try {
      await request(base + "/" + id, { method: "DELETE" });
      if (alive) setFiles((old) => old.filter((f) => f.id !== id));
    } catch (e) {
      if (alive) {
        setError(messageFor(e));
        void app.dialogs.error(messageFor(e), "Unable to update attachments");
      }
    } finally {
      if (alive) setBusy(false);
    }
  }
  return (
    <section class="attachments">
      <h3>Attachments</h3>
      <p>Private files · up to 1 MiB each</p>
      <For each={files()}>
        {(file) => (
          <div class="attachment">
            <a href={base + "/" + file.id}>{file.name}</a>
            <small>{Math.ceil(file.size / 1024)} KB</small>
            <Show when={props.model.permissions?.update !== false}>
              <Button
                size="small"
                disabled={busy() || props.disabled}
                aria-label={"Remove " + file.name}
                onClick={() => remove(file.id)}
              >
                Remove
              </Button>
            </Show>
          </div>
        )}
      </For>
      <Show when={!files().length}>
        <p class="muted">No attachments yet.</p>
      </Show>
      <Show when={props.model.permissions?.update !== false}>
        <label class="file-upload">
          Attach file
          <input
            type="file"
            aria-label="Attach file"
            disabled={busy() || props.disabled || files().length >= 10}
            onChange={upload}
          />
        </label>
      </Show>
      <Show when={error()}>
        <p role="alert" class="ev-field-error">
          {error()}{" "}
          <button type="button" onClick={load}>
            Retry
          </button>
        </p>
      </Show>
    </section>
  );
}
