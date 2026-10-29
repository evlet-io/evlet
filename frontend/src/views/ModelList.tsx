import {
  createSignal,
  createMemo,
  createResource,
  createEffect,
  For,
  Show,
} from "solid-js";
import {
  ExplorerView,
  ViewBar,
  CategoryTabs,
  ViewSwitcher,
  ListToolbar,
  RecordList,
  RecordPreview,
  ListFooter,
  Button,
  IconButton,
  Glyph,
  Badge,
  Modal,
  Field,
  type Column,
} from "../ui";
import { listRecords, getRecord, request } from "../api";
import {
  fieldFor,
  formatValue,
  inputValue,
  recordLabel,
  visibleFields,
} from "../schema";
import { useApp, messageFor } from "../runtime/Context";
import type { ModelSchema, RecordData, FieldSchema } from "../runtime/types";
import FieldInput from "./FieldInput";
import { Icon } from "../demos/icons";
type Query = {
  q: string;
  order: string;
  offset: number;
  limit: number;
  filters: Record<string, any>;
};
export default function ModelList(props: { model: ModelSchema }) {
  const app = useApp();
  const model = props.model;
  const base = "/app/" + model.name;
  const viewOptions = () =>
    app.ui.views.find((v) => v.name === model.name + ".list")?.options || {};
  const defaultPageSize = Math.min(
    100,
    Math.max(1, viewOptions().page_size || 25),
  );
  const prefKey = `evlet:list:${app.tenant}:${app.user.id}:${model.name}`;
  let prefs: any = {};
  try {
    prefs = JSON.parse(localStorage.getItem(prefKey) || "{}");
  } catch {}
  const fields = () => [
    {
      name: "id",
      label: "ID",
      type: "integer",
      required: true,
      default_kind: "none",
    } as FieldSchema,
    ...visibleFields(model),
  ];
  const query = createMemo<Query>(() => {
    const p = new URLSearchParams(app.router.location().split("?")[1]);
    let filters = {};
    try {
      filters = JSON.parse(p.get("filters") || "{}");
      if (!filters || Array.isArray(filters) || typeof filters !== "object")
        filters = {};
    } catch {}
    return {
      q: p.get("q") || "",
      order: p.get("order_by") || "-id",
      offset: Math.max(0, Number(p.get("offset")) || 0),
      limit:
        Number.isInteger(Number(p.get("limit"))) &&
        Number(p.get("limit")) > 0 &&
        Number(p.get("limit")) <= 100
          ? Number(p.get("limit"))
          : defaultPageSize,
      filters,
    };
  });
  const [search, setSearch] = createSignal(query().q),
    [columns, setColumns] = createSignal<string[]>(
      Array.isArray(prefs.columns)
        ? prefs.columns.filter((name: string) =>
            fields().some((f) => f.name === name),
          )
        : viewOptions().columns || model.list_fields,
    ),
    [saved, setSaved] = createSignal<any[]>(
      Array.isArray(prefs.saved) ? prefs.saved : [],
    ),
    [selection, setSelection] = createSignal<Set<string | number>>(new Set()),
    [selected, setSelected] = createSignal<number>(),
    [mode, setMode] = createSignal("list"),
    [modal, setModal] = createSignal(""),
    [error, setError] = createSignal(""),
    [busy, setBusy] = createSignal(false),
    [filterRows, setFilterRows] = createSignal<any[]>([]),
    [savedName, setSavedName] = createSignal(""),
    [categoryField, setCategoryField] = createSignal(
      model.fields.find((f) => f.type === "select" && !f.hidden)?.name || "",
    );
  const apply = (patch: Partial<Query>) => {
    const q = { ...query(), ...patch };
    const p = new URLSearchParams();
    if (q.q) p.set("q", q.q);
    if (q.order !== "-id") p.set("order_by", q.order);
    if (q.offset) p.set("offset", String(q.offset));
    if (q.limit !== 25) p.set("limit", String(q.limit));
    if (Object.keys(q.filters).length)
      p.set("filters", JSON.stringify(q.filters));
    app.router.navigate(base + (p.size ? "?" + p : ""), false);
    setSelection(new Set<string | number>());
    setSelected(undefined);
  };
  createEffect(() => setSearch(query().q));
  createEffect(() => {
    try {
      localStorage.setItem(
        prefKey,
        JSON.stringify({ columns: columns(), saved: saved() }),
      );
    } catch {}
  });
  const [page, { refetch }] = createResource(
    () => ({ query: query(), revision: app.refresh() }),
    ({ query: q }) =>
      listRecords(model.name, q.limit, q.offset, q.filters, q.order, q.q),
  );
  const [preview] = createResource(
    () => (selected() ? { id: selected()!, revision: app.refresh() } : false),
    (args) => getRecord(model.name, args.id),
  );
  const selectable = () =>
    model.fields.filter((f) => f.type === "select" && !f.hidden);
  const category = () => model.fields.find((f) => f.name === categoryField());
  const activeCategory = () =>
    query().filters[categoryField() + "__eq"] ?? "__all__";
  const categories = () => [
    { value: "__all__", label: "All " + model.plural.toLowerCase() },
    ...(category()?.choices || []).map((value) => ({
      value,
      label: value.charAt(0).toUpperCase() + value.slice(1),
    })),
  ];
  const setCategory = (value: string) => {
    const filters = { ...query().filters };
    delete filters[categoryField() + "__eq"];
    if (value !== "__all__") filters[categoryField() + "__eq"] = value;
    apply({ filters, offset: 0 });
  };
  const visibleColumns = () => {
    const all = columns().filter((name) =>
      fields().some((f) => f.name === name),
    );
    return all.length ? all : [model.title_field || "id"];
  };
  const tableColumns = (): Column<RecordData>[] =>
    visibleColumns().map((name, i) => ({
      key: name,
      header: (
        <button
          class="column-sort"
          disabled={viewOptions().sortable === false}
          aria-label={"Sort by " + fieldFor(model, name)?.label}
          onClick={() =>
            apply({
              order: query().order === name ? "-" + name : name,
              offset: 0,
            })
          }
        >
          {fieldFor(model, name)?.label}
          {query().order.replace("-", "") === name ? (
            <span aria-hidden="true">
              {query().order.startsWith("-") ? " ↓" : " ↑"}
            </span>
          ) : null}
        </button>
      ),
      cell: (record) =>
        name === model.title_field ||
        (i === 0 && !visibleColumns().includes(model.title_field)) ? (
          <button class="record-title" onClick={() => setSelected(record.id)}>
            {formatValue(fieldFor(model, name), record[name])}
          </button>
        ) : (
          <Value field={fieldFor(model, name)} value={record[name]} />
        ),
    }));
  const openFilters = () => {
    setFilterRows(
      Object.entries(query().filters).map(([key, value]) => {
        const [name, operator = "eq"] = key.split("__");
        return { key: crypto.randomUUID(), name, operator, value };
      }),
    );
    setError("");
    setModal("filters");
  };
  const applyFilters = () => {
    try {
      const filters: Record<string, any> = {};
      for (const row of filterRows()) {
        const key = row.name + "__" + row.operator;
        if (key in filters)
          throw new Error("Use each field and comparison once.");
        filters[key] =
          row.operator === "isnull"
            ? !!row.value
            : inputValue(fieldFor(model, row.name), row.value);
      }
      apply({ filters, offset: 0 });
      setModal("");
    } catch (e) {
      setError(messageFor(e));
    }
  };
  async function bulkDelete() {
    setBusy(true);
    setError("");
    let removed = 0;
    const failures: string[] = [];
    for (const id of selection()) {
      try {
        const current = await request(`/api/${model.name}/${id}`, {
          withMeta: true,
        } as any);
        await request(`/api/${model.name}/${id}`, {
          method: "DELETE",
          headers: current.etag ? { "If-Match": current.etag } : {},
        });
        removed++;
      } catch (e) {
        failures.push("#" + id + ": " + messageFor(e));
      }
    }
    setSelection(new Set<string | number>());
    setSelected(undefined);
    setBusy(false);
    setModal("");
    await refetch();
    app.notify(`${removed} record${removed === 1 ? "" : "s"} deleted.`);
    if (failures.length) setError(failures.join(" "));
  }
  return (
    <>
      <ExplorerView
        class="model-explorer"
        title={model.plural}
        subtitle={"Browse and manage " + model.plural.toLowerCase() + "."}
        badge={<Badge>{page()?.total ?? "…"}</Badge>}
        collapsible={false}
        actions={
          model.permissions?.create !== false
            ? [
                {
                  id: "new",
                  label: "New " + model.label.toLowerCase(),
                  icon: <Icon name="plus" />,
                  onSelect: () => app.router.navigate(base + "/new"),
                },
              ]
            : []
        }
        viewBar={
          <ViewBar
            categories={
              <CategoryTabs
                items={categories()}
                value={activeCategory()}
                onChange={setCategory}
              />
            }
            viewSwitcher={
              <>
                <Show when={selectable().length > 1}>
                  <select
                    class="category-field"
                    aria-label="Categorize by"
                    value={categoryField()}
                    onChange={(e) => {
                      setCategoryField(e.currentTarget.value);
                      apply({ filters: {}, offset: 0 });
                    }}
                  >
                    <For each={selectable()}>
                      {(f) => (
                        <option
                          value={f.name}
                          selected={categoryField() === f.name}
                        >
                          {f.label}
                        </option>
                      )}
                    </For>
                  </select>
                </Show>
                <ViewSwitcher
                  value={mode()}
                  onChange={setMode}
                  items={[
                    {
                      value: "list",
                      label: "List",
                      icon: <Icon name="list" />,
                    },
                    {
                      value: "gallery",
                      label: "Gallery",
                      icon: <Icon name="grid" />,
                    },
                  ]}
                />
              </>
            }
          />
        }
        toolbar={
          <ListToolbar
            leading={
              <>
                <form
                  class="list-search"
                  onSubmit={(e) => {
                    e.preventDefault();
                    apply({ q: search().trim(), offset: 0 });
                  }}
                >
                  <Glyph name="search" />
                  <input
                    type="search"
                    aria-label={"Search " + model.plural}
                    value={search()}
                    maxLength={200}
                    onInput={(e) => setSearch(e.currentTarget.value)}
                    placeholder={"Search " + model.plural.toLowerCase() + "…"}
                  />
                  <button type="submit">Search</button>
                </form>
                <Button
                  size="small"
                  icon={<Icon name="filter" />}
                  hidden={viewOptions().filterable === false}
                  onClick={openFilters}
                >
                  Filters {Object.keys(query().filters).length || ""}
                </Button>
              </>
            }
            trailing={
              <>
                <select
                  aria-label="Saved filters"
                  value=""
                  onChange={(e) => {
                    const item = saved().find(
                      (s) => s.name === e.currentTarget.value,
                    );
                    if (item) apply({ ...item.query, offset: 0 });
                  }}
                >
                  <option value="">Saved views</option>
                  <For each={saved()}>
                    {(item) => <option value={item.name}>{item.name}</option>}
                  </For>
                </select>
                <IconButton
                  aria-label="Column settings"
                  title="Column settings"
                  onClick={() => setModal("columns")}
                >
                  <Icon name="layers" />
                </IconButton>
                <IconButton
                  aria-label="Refresh records"
                  onClick={() => refetch()}
                >
                  <Icon name="activity" />
                </IconButton>
              </>
            }
          />
        }
        onPreviewClose={() => setSelected(undefined)}
        previewKey={selected()}
        preview={
          <Show when={selected()}>
            <Show when={preview.error}>
              <div class="ui-alert" role="alert">
                {messageFor(preview.error)}
                <Button onClick={() => setSelected(undefined)}>
                  Close preview
                </Button>
              </div>
            </Show>
            <Show when={preview.loading}>
              <p role="status">Loading record…</p>
            </Show>
            <Show when={!preview.error && preview()}>
              {(record) => (
                <RecordPreview
                  title={recordLabel(model, record())}
                  recordId={model.label + " #" + record().id}
                  icon={<Icon name="layers" />}
                  badge={<Badge tone="info">{model.label}</Badge>}
                  onClose={() => setSelected(undefined)}
                  onEdit={
                    model.permissions?.update !== false
                      ? () => app.router.navigate(base + "/" + record().id)
                      : undefined
                  }
                  metadata={visibleFields(model)
                    .slice(0, 5)
                    .map((f) => ({
                      id: f.name,
                      label: f.label,
                      value: formatValue(f, record()[f.name]),
                    }))}
                  tabs={[
                    {
                      value: "details",
                      label: "Details",
                      content: () => (
                        <dl class="record-details">
                          <For each={visibleFields(model)}>
                            {(f) => (
                              <div>
                                <dt>{f.label}</dt>
                                <dd>
                                  <Value field={f} value={record()[f.name]} />
                                </dd>
                              </div>
                            )}
                          </For>
                        </dl>
                      ),
                    },
                    {
                      value: "related",
                      label: "Related",
                      count: model.children?.length,
                      content: () => (
                        <For each={model.children || []}>
                          {(child) => (
                            <div class="preview-children">
                              <h3>{child.label}</h3>
                              <p>
                                {record().children?.[child.name]?.length || 0}{" "}
                                rows
                              </p>
                            </div>
                          )}
                        </For>
                      ),
                    },
                  ]}
                  footer={
                    <Button
                      variant="primary"
                      onClick={() =>
                        app.router.navigate(
                          base +
                            "/" +
                            record().id +
                            (model.permissions?.update === false
                              ? "/view"
                              : ""),
                        )
                      }
                    >
                      Open record
                      <Glyph name="arrow" />
                    </Button>
                  }
                />
              )}
            </Show>
          </Show>
        }
      >
        <Show when={error()}>
          <p role="alert" class="error-banner">
            {error()}
          </p>
        </Show>
        <Show when={page.error}>
          <p role="alert" class="error-banner">
            {messageFor(page.error)}{" "}
            <Button onClick={() => refetch()}>Retry</Button>
          </p>
        </Show>
        <Show when={page.loading}>
          <p role="status" class="list-loading">
            Loading records…
          </p>
        </Show>
        <Show when={!page.error && page()}>
          {(data) => (
            <>
              <Show
                when={mode() === "list"}
                fallback={
                  <div class="record-gallery">
                    <For each={data().items}>
                      {(record) => (
                        <button
                          class="gallery-card"
                          onClick={() => setSelected(record.id)}
                        >
                          <Icon name="layers" />
                          <strong>{recordLabel(model, record)}</strong>
                          <small>#{record.id}</small>
                          <For each={visibleColumns().slice(1, 4)}>
                            {(name) => (
                              <span>
                                {fieldFor(model, name)?.label}:{" "}
                                {formatValue(
                                  fieldFor(model, name),
                                  record[name],
                                )}
                              </span>
                            )}
                          </For>
                        </button>
                      )}
                    </For>
                    <Show when={!data().items.length}>
                      <p class="ev-empty">No records match this view.</p>
                    </Show>
                  </div>
                }
              >
                <RecordList
                  records={data().items}
                  columns={tableColumns()}
                  getKey={(r) => r.id}
                  getLabel={(r) => recordLabel(model, r)}
                  selectedKey={selected()}
                  selection={selection()}
                  onSelectionChange={setSelection}
                  selectionActions={
                    <Show when={model.permissions?.delete !== false}>
                      <Button size="small" onClick={() => setModal("delete")}>
                        Delete selected
                      </Button>
                    </Show>
                  }
                  rowActions={(record) => (
                    <IconButton
                      aria-label={"Open " + recordLabel(model, record)}
                      onClick={() =>
                        app.router.navigate(
                          base +
                            "/" +
                            record.id +
                            (model.permissions?.update === false
                              ? "/view"
                              : ""),
                        )
                      }
                    >
                      <Glyph name="arrow" />
                    </IconButton>
                  )}
                  empty={
                    <div class="ev-empty">
                      <Icon name="layers" />
                      <h3>No records yet</h3>
                      <p>Create a record or adjust your filters.</p>
                      <Show when={model.permissions?.create !== false}>
                        <Button
                          onClick={() => app.router.navigate(base + "/new")}
                        >
                          New {model.label.toLowerCase()}
                        </Button>
                      </Show>
                    </div>
                  }
                />
              </Show>
              <ListFooter
                leading={
                  <span>
                    {data().total ? query().offset + 1 : 0}–
                    {Math.min(
                      query().offset + data().items.length,
                      data().total,
                    )}{" "}
                    of {data().total}
                  </span>
                }
                trailing={
                  <div class="pagination">
                    <select
                      aria-label="Records per page"
                      value={query().limit}
                      onChange={(e) =>
                        apply({
                          limit: Number(e.currentTarget.value),
                          offset: 0,
                        })
                      }
                    >
                      <For
                        each={[
                          ...new Set([10, 25, 50, 100, defaultPageSize]),
                        ].sort((a, b) => a - b)}
                      >
                        {(count) => (
                          <option
                            value={count}
                            selected={query().limit === count}
                          >
                            {count} per page
                          </option>
                        )}
                      </For>
                    </select>
                    <Button
                      size="small"
                      disabled={query().offset === 0 || page.loading}
                      onClick={() =>
                        apply({
                          offset: Math.max(0, query().offset - query().limit),
                        })
                      }
                    >
                      Previous
                    </Button>
                    <Button
                      size="small"
                      disabled={
                        query().offset + query().limit >= data().total ||
                        page.loading
                      }
                      onClick={() =>
                        apply({ offset: query().offset + query().limit })
                      }
                    >
                      Next
                    </Button>
                  </div>
                }
              />
            </>
          )}
        </Show>
      </ExplorerView>
      <Modal
        title="Filter records"
        open={modal() === "filters"}
        onClose={() => setModal("")}
        footer={
          <>
            <Button
              onClick={() => {
                apply({ filters: {}, q: "", offset: 0 });
                setModal("");
              }}
            >
              Clear all
            </Button>
            <Button variant="primary" onClick={applyFilters}>
              Apply filters
            </Button>
          </>
        }
      >
        <For each={filterRows()}>
          {(row) => (
            <div class="filter-row">
              <select
                aria-label="Filter field"
                value={row.name}
                onChange={(e) =>
                  setFilterRows((rows) =>
                    rows.map((r) =>
                      r.key === row.key
                        ? {
                            ...r,
                            name: e.currentTarget.value,
                            operator: "eq",
                            value: null,
                          }
                        : r,
                    ),
                  )
                }
              >
                <For each={fields()}>
                  {(field) => (
                    <option
                      value={field.name}
                      selected={row.name === field.name}
                    >
                      {field.label}
                    </option>
                  )}
                </For>
              </select>
              <select
                aria-label="Comparison"
                value={row.operator}
                onChange={(e) => {
                  const value = e.currentTarget.value;
                  setFilterRows((rows) =>
                    rows.map((r) =>
                      r.key === row.key
                        ? {
                            ...r,
                            operator: value,
                            value: value === "isnull" ? true : null,
                          }
                        : r,
                    ),
                  );
                }}
              >
                <For
                  each={[
                    "eq",
                    "ne",
                    "isnull",
                    ...(["text", "longtext", "email", "url"].includes(
                      fieldFor(model, row.name)?.type,
                    )
                      ? ["icontains", "contains", "startswith"]
                      : [
                            "integer",
                            "currency",
                            "decimal",
                            "date",
                            "datetime",
                          ].includes(fieldFor(model, row.name)?.type)
                        ? ["gt", "gte", "lt", "lte"]
                        : []),
                  ]}
                >
                  {(op) => (
                    <option value={op} selected={row.operator === op}>
                      {
                        (
                          {
                            eq: "Equals",
                            ne: "Does not equal",
                            isnull: "Is empty",
                            icontains: "Contains",
                            contains: "Contains (case sensitive)",
                            startswith: "Starts with",
                            gt: "Greater than",
                            gte: "At least",
                            lt: "Less than",
                            lte: "At most",
                          } as any
                        )[op]
                      }
                    </option>
                  )}
                </For>
              </select>
              <Field label="Value">
                {(control) => (
                  <FieldInput
                    field={
                      row.operator === "isnull"
                        ? {
                            name: "value",
                            label: "Value",
                            type: "boolean",
                            required: true,
                            default_kind: "none",
                          }
                        : { ...fieldFor(model, row.name), required: false }
                    }
                    id={control.id}
                    value={row.value}
                    onChange={(value) =>
                      setFilterRows((rows) =>
                        rows.map((r) =>
                          r.key === row.key ? { ...r, value } : r,
                        ),
                      )
                    }
                  />
                )}
              </Field>
              <IconButton
                aria-label="Remove filter"
                onClick={() =>
                  setFilterRows((rows) => rows.filter((r) => r.key !== row.key))
                }
              >
                <Glyph name="close" />
              </IconButton>
            </div>
          )}
        </For>
        <Button
          disabled={filterRows().length >= 20}
          onClick={() =>
            setFilterRows((rows) => [
              ...rows,
              {
                key: crypto.randomUUID(),
                name: fields()[0].name,
                operator: "eq",
                value: null,
              },
            ])
          }
        >
          Add filter
        </Button>
        <Show when={error()}>
          <p class="error-banner" role="alert">
            {error()}
          </p>
        </Show>
        <hr />
        <label class="inline-label">
          Save current view
          <input
            placeholder="View name"
            maxLength={60}
            value={savedName()}
            onInput={(e) => setSavedName(e.currentTarget.value)}
          />
          <Button
            disabled={!savedName().trim()}
            onClick={() => {
              setSaved((items) =>
                [
                  ...items.filter((s) => s.name !== savedName().trim()),
                  { name: savedName().trim(), query: query() },
                ].slice(-20),
              );
              setSavedName("");
              app.notify("Current view saved.");
            }}
          >
            Save
          </Button>
        </label>
        <For each={saved()}>
          {(item) => (
            <div class="saved-filter">
              <span>{item.name}</span>
              <button
                onClick={() =>
                  setSaved((items) => items.filter((s) => s.name !== item.name))
                }
              >
                Remove
              </button>
            </div>
          )}
        </For>
      </Modal>
      <Modal
        title="Visible columns"
        open={modal() === "columns"}
        onClose={() => setModal("")}
      >
        <For each={fields()}>
          {(field) => (
            <label class="column-choice">
              <input
                type="checkbox"
                checked={columns().includes(field.name)}
                onChange={(e) =>
                  setColumns((cols) =>
                    e.currentTarget.checked
                      ? [...cols, field.name]
                      : cols.filter((c) => c !== field.name),
                  )
                }
              />
              {field.label}
              <Show when={columns().includes(field.name)}>
                <Button
                  size="small"
                  aria-label={"Move " + field.label + " left"}
                  disabled={columns().indexOf(field.name) <= 0}
                  onClick={() => {
                    const next = [...columns()],
                      i = next.indexOf(field.name);
                    [next[i - 1], next[i]] = [next[i], next[i - 1]];
                    setColumns(next);
                  }}
                >
                  ↑
                </Button>
              </Show>
            </label>
          )}
        </For>
      </Modal>
      <Modal
        title={"Delete " + selection().size + " records?"}
        open={modal() === "delete"}
        onClose={() => {
          if (!busy()) setModal("");
        }}
        footer={
          <>
            <Button disabled={busy()} onClick={() => setModal("")}>
              Cancel
            </Button>
            <Button variant="primary" disabled={busy()} onClick={bulkDelete}>
              {busy() ? "Deleting…" : "Delete records"}
            </Button>
          </>
        }
      >
        <p>
          This removes the selected records and their child rows. The server
          checks each record’s permissions.
        </p>
      </Modal>
    </>
  );
}
export function Value(props: { field: FieldSchema; value: any }) {
  return (
    <Show
      when={props.field.type === "select" || props.field.type === "boolean"}
      fallback={<span>{formatValue(props.field, props.value)}</span>}
    >
      <Badge
        tone={
          props.value === true ||
          ["active", "done", "sent"].includes(props.value)
            ? "success"
            : props.value === false
              ? "neutral"
              : "info"
        }
      >
        {formatValue(props.field, props.value)}
      </Badge>
    </Show>
  );
}
