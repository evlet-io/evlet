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
import { request, recordRequest } from "../api";
import { inputValue, visibleFields, recordLabel, formatValue } from "../schema";
import { useApp, messageFor } from "../runtime/Context";
import { NodeRenderer, makeRuntime } from "../runtime/ViewRenderer";
import { createState } from "../runtime/StateStore";
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
  let alive = true,
    sequence = 0,
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
  };
  function initialize(data: RecordData | null, revision = "") {
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
          structuredClone(data?.children?.[child.name] || []),
        ]),
      ),
    );
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
    if (Object.keys(payload.errors).length) return;
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
        (p.required ?? field.required) &&
        (field.default_kind === "none" || field.type === "boolean");
      return (
        <Field
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
              ? "Include the timezone, for example +04:00."
              : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          {(control) => (
            <FieldInput
              field={field}
              id={control.id}
              required={required}
              disabled={!canEdit() || busy() || field.read_only || p.disabled}
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
  async function action(name: string) {
    if (busy() || isDirty()) return;
    setBusy(true);
    setError("");
    try {
      const result = await request(base + "/" + recordId + "/actions/" + name, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(etag() ? { "If-Match": etag() } : {}),
        },
        body: "{}",
      });
      if (!alive) return;
      await load();
      if (result.job) {
        setJob(result.job);
        setJobError("");
        clearTimeout(jobTimer);
        jobTimer = setTimeout(() => pollJob(result.job.id), 500);
      } else app.notify("Action completed.");
    } catch (e) {
      if (alive) setError(messageFor(e));
    } finally {
      if (alive) setBusy(false);
    }
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
                onClick={() => {
                  if (
                    !isDirty() ||
                    window.confirm("Discard your draft and reload?")
                  )
                    void load();
                }}
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
              onClick={() => {
                if (
                  window.confirm(
                    "Discard your draft and reload the current record?",
                  )
                )
                  void load();
              }}
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
                      <For each={collections()[child.name] || []}>
                        {(row, index) => (
                          <fieldset class="child-row">
                            <legend>
                              {child.label} {index() + 1}
                            </legend>
                            <div class="child-fields">
                              <For each={childFields(child)}>
                                {(field) => (
                                  <Field
                                    label={field.label}
                                    required={
                                      field.required && !field.read_only
                                    }
                                  >
                                    {(control) => (
                                      <FieldInput
                                        field={field}
                                        id={control.id}
                                        value={row[field.name]}
                                        disabled={
                                          field.read_only ||
                                          !childPermission(
                                            child,
                                            row.id ? "update" : "create",
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
                                )}
                              </For>
                            </div>
                            <Show
                              when={
                                canEdit() &&
                                (!row.id || childPermission(child, "delete"))
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
                                }}
                              >
                                Remove row
                              </Button>
                            </Show>
                          </fieldset>
                        )}
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
                            setCollections((old) => ({
                              ...old,
                              [child.name]: [...(old[child.name] || []), row],
                            }));
                            setChildDirty(
                              (old) => new Set([...old, child.name]),
                            );
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
    </div>
  );
}
function Attachments(props: {
  model: ModelSchema;
  recordId: number;
  disabled: boolean;
  revision: number;
}) {
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
      if (alive) setError(messageFor(e));
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
      if (alive) setError(messageFor(e));
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
