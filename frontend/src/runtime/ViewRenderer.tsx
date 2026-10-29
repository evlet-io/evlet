import { SelectInput, InputGroup } from "../ui";
import {
  createSignal,
  createResource,
  For,
  Show,
  Switch,
  Match,
  lazy,
  type Component as SolidComponent,
  type JSX,
} from "solid-js";
import { Dynamic } from "solid-js/web";
import { createStore } from "solid-js/store";
import {
  Button,
  Badge,
  Avatar,
  ActionMenu,
  Modal,
  Field as LabelledField,
  CategoryTabs,
  PageHeader,
  DatePicker,
  TimePicker,
  DateTimePicker,
  Glyph,
} from "../ui";
import { request, listRecords, recordRequest } from "../api";
import { useApp, messageFor } from "./Context";
import { createState, componentState } from "./StateStore";
import type {
  NodeSchema,
  Operation,
  ViewSchema,
  ComponentContract,
} from "./types";
import { Icon, iconNames } from "../demos/icons";

export interface RenderContext {
  state: ReturnType<typeof createState>;
  refresh: () => number;
  dispatch: (
    ops: Operation[],
    node: NodeSchema,
    value?: any,
    eventState?: ReturnType<typeof createState>,
  ) => Promise<void>;
  open: Record<string, boolean>;
  field?: (node: NodeSchema, evaluated: Record<string, any>) => JSX.Element;
}
export interface RendererProps {
  node: NodeSchema;
  runtime: RenderContext;
  contract: ComponentContract;
}
const renderers = new Map<
  string,
  { version: number; renderer: SolidComponent<RendererProps> }
>();
export function registerComponent(
  name: string,
  renderer: SolidComponent<RendererProps>,
  version = 1,
) {
  if (renderers.has(name))
    throw new Error("Duplicate frontend renderer: " + name);
  if (version !== 1) throw new Error("Unsupported renderer version");
  renderers.set(name, { version, renderer });
}
export function registerLazyComponent(
  name: string,
  loader: () => Promise<{ default: SolidComponent<RendererProps> }>,
  version = 1,
) {
  registerComponent(name, lazy(loader), version);
}
export function NodeRenderer(props: {
  node: NodeSchema;
  runtime: RenderContext;
}) {
  const app = useApp();
  const contract = () =>
    app.ui.components.find((item) => item.name === props.node.type);
  const renderer = () => renderers.get(contract()?.renderer || props.node.type);
  const local = componentState(props.runtime.state);
  const runtime: RenderContext = {
    ...props.runtime,
    state: local,
    dispatch: (ops, node, value, eventState = local) =>
      props.runtime.dispatch(ops, node, value, eventState),
  };
  const p = () => local.evaluate(props.node.props);
  return (
    <Show when={p().visible !== false}>
      <div
        data-component={props.node.type}
        data-component-id={props.node.id}
        class={p().class}
        classList={{
          "ui-node": true,
          "ui-field-node": props.node.type === "Field",
        }}
        style={{
          ...p().style,
          ...(p().width
            ? { "grid-column": `span ${Math.min(12, Math.max(1, p().width))}` }
            : {}),
        }}
      >
        <Show
          when={contract() && renderer()?.version === props.node.version}
          fallback={
            <p role="alert">
              Renderer unavailable: {props.node.type} (version{" "}
              {props.node.version})
            </p>
          }
        >
          <Dynamic
            component={renderer()!.renderer}
            node={props.node}
            runtime={runtime}
            contract={contract()!}
          />
        </Show>
      </div>
    </Show>
  );
}
function Standard(props: RendererProps) {
  const p = () => props.runtime.state.evaluate(props.node.props);
  const value = (key = "value") =>
    props.node.bindings[key]
      ? props.runtime.state.read(props.node.bindings[key])
      : p()[key];
  const event = (name: string, data?: any) =>
    props.runtime.dispatch(props.node.events[name] || [], props.node, data);
  const change = (next: any, name = "on_change", key = "value") => {
    if (props.node.bindings[key])
      props.runtime.state.write(props.node.bindings[key], next);
    void event(name, next);
  };
  const slot = (name = "children") => (
    <For each={props.node.slots[name] || []}>
      {(node) => <NodeRenderer node={node} runtime={props.runtime} />}
    </For>
  );
  const options = () =>
    (p().options || p().items || []).map((item: any) =>
      typeof item === "string" ? { value: item, label: item } : item,
    );
  const type = props.node.type;
  const layouts = [
    "Row",
    "Column",
    "Grid",
    "Stack",
    "Container",
    "Section",
    "Card",
    "Split",
    "Panel",
    "FormLayout",
    "ButtonGroup",
  ];
  const controls = [
    "TextInput",
    "TextArea",
    "NumberInput",
    "CurrencyInput",
    "Checkbox",
    "Switch",
    "Select",
    "MultiSelect",
    "DatePicker",
    "TimePicker",
    "DateTimePicker",
    "SearchBox",
  ];
  return (
    <Switch>
      <Match when={layouts.includes(type)}>
        <section
          class={"ui-layout ui-" + type.toLowerCase()}
          style={{
            gap: `${p().gap ?? 16}px`,
            padding: p().padding !== undefined ? `${p().padding}px` : undefined,
            "--columns": p().columns || (type === "Split" ? 2 : 3),
            "align-items": p().align,
            "justify-content": p().justify,
            "flex-wrap": p().wrap ? "wrap" : undefined,
          }}
        >
          {slot("header")}
          <Show when={p().title}>
            <h2>{p().title}</h2>
          </Show>
          <Show when={p().description}>
            <p class="muted">{p().description}</p>
          </Show>
          {slot()}
          {slot("footer")}
        </section>
      </Match>
      <Match when={type === "Text"}>
        <Dynamic
          component={p().variant === "heading" ? "h2" : "p"}
          class={"ui-text " + (p().variant || "")}
        >
          {String(p().text ?? "")}
        </Dynamic>
      </Match>
      <Match when={type === "Heading"}>
        <Dynamic component={"h" + Math.min(6, Math.max(1, p().level || 2))}>
          {String(p().text ?? "")}
        </Dynamic>
      </Match>
      <Match when={type === "Badge"}>
        <Badge tone={p().tone}>{String(p().text ?? "")}</Badge>
      </Match>
      <Match when={type === "Icon"}>
        <Icon name={iconNames.includes(p().name) ? p().name : "grid"} />
      </Match>
      <Match when={type === "Image"}>
        <img src={p().src} alt={p().alt || ""} loading="lazy" />
      </Match>
      <Match when={type === "Avatar"}>
        <Avatar initials={p().initials || "?"} label={p().label} />
      </Match>
      <Match when={type === "Progress"}>
        <label class="ui-progress">
          {p().label}
          <progress value={p().value || 0} max={p().max || 100} />
        </label>
      </Match>
      <Match when={type === "Divider"}>
        <hr />
      </Match>
      <Match when={["Alert", "EmptyState", "Skeleton"].includes(type)}>
        <div
          class={"ui-" + type.toLowerCase()}
          role={type === "Alert" ? "status" : undefined}
          aria-busy={type === "Skeleton"}
        >
          <strong>{p().title}</strong>
          <p>{p().text}</p>
          {slot()}
        </div>
      </Match>
      <Match when={type === "Button"}>
        <Button
          variant={p().variant}
          disabled={p().disabled}
          onClick={() => event("on_click")}
          icon={p().icon ? <Icon name={p().icon} /> : undefined}
        >
          {p().label}
        </Button>
      </Match>
      <Match when={type === "DropdownButton"}>
        <ActionMenu
          label={p().label}
          actions={options().map((item: any) => ({
            id: item.value,
            label: item.label,
            disabled: p().disabled,
            onSelect: () => change(item.value, "on_select"),
          }))}
        />
      </Match>
      <Match when={type === "Field"}>
        {props.runtime.field?.(props.node, p()) ?? (
          <p class="ui-alert">Field requires a model form.</p>
        )}
      </Match>
      <Match when={controls.includes(type)}>
        <LabelledField
          class={
            type === "Checkbox" || type === "Switch"
              ? "ev-field-check"
              : undefined
          }
          label={p().label || p().aria_label || props.node.id}
          required={p().required}
          help={p().help}
          error={p().error}
        >
          {(control) => (
            <Switch>
              <Match
                when={["DatePicker", "TimePicker", "DateTimePicker"].includes(
                  type,
                )}
              >
                <Dynamic
                  component={
                    type === "DatePicker"
                      ? DatePicker
                      : type === "TimePicker"
                        ? TimePicker
                        : DateTimePicker
                  }
                  id={control.id}
                  value={value() ?? ""}
                  required={p().required}
                  disabled={p().disabled}
                  placeholder={p().placeholder}
                  aria-invalid={control.invalid}
                  aria-describedby={control.describedBy}
                  onChange={(next: string) => change(next, "on_change")}
                  onDraftChange={(next: string) => change(next, "on_input")}
                  onFocus={() => event("on_focus")}
                  onBlur={() => event("on_blur")}
                />
              </Match>
              <Match when={type === "TextArea"}>
                <textarea
                  id={control.id}
                  rows={p().rows || 3}
                  value={value() ?? ""}
                  required={p().required}
                  maxLength={p().max_length}
                  disabled={p().disabled}
                  placeholder={p().placeholder}
                  aria-invalid={control.invalid}
                  aria-describedby={control.describedBy}
                  onInput={(e) => change(e.currentTarget.value, "on_input")}
                  onChange={(e) => event("on_change", e.currentTarget.value)}
                  onFocus={() => event("on_focus")}
                  onBlur={() => event("on_blur")}
                />
              </Match>
              <Match when={type === "Select" || type === "MultiSelect"}>
                <SelectInput
                  id={control.id}
                  multiple={type === "MultiSelect"}
                  value={value() ?? ""}
                  disabled={p().disabled}
                  required={p().required}
                  aria-invalid={control.invalid}
                  aria-describedby={control.describedBy}
                  options={
                    type === "MultiSelect"
                      ? options()
                      : [{ value: "", label: "Choose a value" }, ...options()]
                  }
                  onChange={(next) => change(next)}
                  onMultipleChange={(next) => change(next)}
                  onFocus={() => event("on_focus")}
                  onBlur={() => event("on_blur")}
                />
              </Match>
              <Match when={type === "Checkbox" || type === "Switch"}>
                <input
                  id={control.id}
                  type="checkbox"
                  class="ev-check"
                  role={type === "Switch" ? "switch" : undefined}
                  checked={!!value()}
                  disabled={p().disabled}
                  aria-describedby={control.describedBy}
                  aria-invalid={control.invalid}
                  onChange={(e) => {
                    change(e.currentTarget.checked, "on_input");
                    event("on_change", e.currentTarget.checked);
                  }}
                  onFocus={() => event("on_focus")}
                  onBlur={() => event("on_blur")}
                />
              </Match>
              <Match when={true}>
                <InputGroup prefix={p().prefix} suffix={p().suffix}>
                  <input
                    id={control.id}
                    type={
                      type === "Checkbox" || type === "Switch"
                        ? "checkbox"
                        : type === "NumberInput"
                          ? "number"
                          : type === "SearchBox"
                            ? "search"
                            : "text"
                    }
                    role={type === "Switch" ? "switch" : undefined}
                    value={value() ?? ""}
                    checked={!!value()}
                    inputMode={type === "CurrencyInput" ? "decimal" : undefined}
                    min={p().min}
                    max={p().max}
                    step={p().step}
                    required={
                      p().required && !["Checkbox", "Switch"].includes(type)
                    }
                    disabled={p().disabled}
                    placeholder={p().placeholder}
                    maxLength={p().max_length}
                    aria-invalid={control.invalid}
                    aria-describedby={control.describedBy}
                    onInput={(e) =>
                      change(
                        ["Checkbox", "Switch"].includes(type)
                          ? e.currentTarget.checked
                          : type === "NumberInput"
                            ? e.currentTarget.value === ""
                              ? null
                              : Number(e.currentTarget.value)
                            : e.currentTarget.value,
                        "on_input",
                      )
                    }
                    onChange={(e) => {
                      if (["Checkbox", "Switch"].includes(type))
                        change(e.currentTarget.checked);
                      else event("on_change", value());
                    }}
                    onFocus={() => event("on_focus")}
                    onBlur={() => event("on_blur")}
                  />
                </InputGroup>
              </Match>
            </Switch>
          )}
        </LabelledField>
      </Match>
      <Match when={type === "Dialog" || type === "Drawer"}>
        <Modal
          class={type === "Drawer" ? "ui-drawer" : undefined}
          open={props.runtime.open[props.node.id] ?? !!value("open")}
          onClose={() => {
            change(false, "on_close", "open");
            void props.runtime.dispatch(
              [{ op: "close", target: props.node.id }],
              props.node,
            );
          }}
          title={p().title}
          footer={slot("footer")}
        >
          {slot()}
        </Modal>
      </Match>
      <Match when={type === "Tabs"}>
        <div>
          <CategoryTabs
            items={options()}
            value={value() || ""}
            onChange={(v) => change(v, "on_select")}
          />
          {slot()}
        </div>
      </Match>
      <Match when={type === "Table" || type === "RecordList"}>
        <SchemaTable
          node={props.node}
          runtime={props.runtime}
          contract={props.contract}
        />
      </Match>
      <Match when={type === "Pagination"}>
        <div class="pagination">
          <Button
            disabled={p().disabled || value("offset") <= 0}
            onClick={() =>
              change(
                Math.max(0, value("offset") - p().page_size),
                "on_change",
                "offset",
              )
            }
          >
            Previous
          </Button>
          <span>
            {Math.min(p().total, value("offset") + 1)}–
            {Math.min(p().total, value("offset") + p().page_size)} of{" "}
            {p().total}
          </span>
          <Button
            disabled={
              p().disabled || value("offset") + p().page_size >= p().total
            }
            onClick={() =>
              change(value("offset") + p().page_size, "on_change", "offset")
            }
          >
            Next
          </Button>
        </div>
      </Match>
    </Switch>
  );
}
function SchemaTable(props: RendererProps) {
  const p = () => props.runtime.state.evaluate(props.node.props);
  const [data] = createResource(
    () => ({ source: p().source, tick: props.runtime.refresh() }),
    async ({ source }) =>
      source
        ? listRecords(
            source.model,
            Math.min(100, source.limit || 25),
            source.offset || 0,
            source.filters || {},
            source.order_by || "-id",
            source.q || "",
          )
        : { items: p().items || [] },
  );
  return (
    <div class="ui-data-table">
      <Show when={data.error}>
        <p role="alert">{messageFor(data.error)}</p>
      </Show>
      <Show when={data.loading}>
        <p role="status">Loading records…</p>
      </Show>
      <table>
        <thead>
          <tr>
            <For each={p().columns || []}>
              {(column: any) => (
                <th>{typeof column === "string" ? column : column.label}</th>
              )}
            </For>
          </tr>
        </thead>
        <tbody>
          <For each={(!data.error && data()?.items) || []}>
            {(row: any) => (
              <tr>
                <For each={p().columns || []}>
                  {(column: any) => (
                    <td>
                      <Show
                        when={props.node.events.on_select?.length}
                        fallback={String(
                          row[
                            typeof column === "string" ? column : column.key
                          ] ?? "",
                        )}
                      >
                        <button
                          class="ui-text-link"
                          onClick={() =>
                            props.runtime.dispatch(
                              props.node.events.on_select,
                              props.node,
                              row,
                            )
                          }
                        >
                          {String(
                            row[
                              typeof column === "string" ? column : column.key
                            ] ?? "",
                          )}
                        </button>
                      </Show>
                    </td>
                  )}
                </For>
              </tr>
            )}
          </For>
        </tbody>
      </table>
      <Show when={!data.error && !data.loading && !data()?.items.length}>
        <p class="ev-empty">{p().empty_text || "No records yet."}</p>
      </Show>
    </div>
  );
}
for (const name of [
  "Row",
  "Column",
  "Grid",
  "Stack",
  "Container",
  "Section",
  "Card",
  "Split",
  "Panel",
  "FormLayout",
  "ButtonGroup",
  "Text",
  "Heading",
  "Badge",
  "Icon",
  "Image",
  "Avatar",
  "Progress",
  "Alert",
  "EmptyState",
  "Skeleton",
  "Divider",
  "TextInput",
  "TextArea",
  "NumberInput",
  "CurrencyInput",
  "Checkbox",
  "Switch",
  "Select",
  "MultiSelect",
  "DatePicker",
  "TimePicker",
  "DateTimePicker",
  "SearchBox",
  "Field",
  "Button",
  "DropdownButton",
  "Dialog",
  "Drawer",
  "Tabs",
  "Table",
  "RecordList",
  "Pagination",
])
  registerComponent(name, Standard);

export function makeRuntime(
  view: ViewSchema,
  options: {
    field?: RenderContext["field"];
    submit?: () => void;
    refresh?: () => void;
    state?: ReturnType<typeof createState>;
  } = {},
) {
  const app = useApp();
  const state =
    options.state ||
    createState(
      view.state,
      view.computed,
      app.applicationState || {
        user: app.user,
        tenant: app.tenant,
      },
    );
  const [open, setOpen] = createStore<Record<string, boolean>>({});
  const [tick, setTick] = createSignal(0);
  const runtime: RenderContext = {
    state,
    open,
    field: options.field,
    refresh: () => tick() + app.refresh(),
    async dispatch(ops, node, value, eventState = state) {
      eventState.write("component.value", value);
      try {
        for (const raw of ops) {
          const op = eventState.evaluate(raw);
          switch (op.op) {
            case "navigate":
              if (!(await app.router.navigate(op.path))) return;
              break;
            case "back":
              history.back();
              break;
            case "set":
              eventState.write(op.path, op.value);
              break;
            case "reset":
              eventState.reset(op.path);
              break;
            case "open":
            case "close": {
              setOpen(op.target, op.op === "open");
              const find = (n: NodeSchema): NodeSchema | undefined =>
                n.id === op.target
                  ? n
                  : Object.values(n.slots).flat().map(find).find(Boolean);
              const target = find(view.layout);
              if (target?.bindings.open)
                state.write(target.bindings.open, op.op === "open");
              if (op.op === "open" && target?.events.on_open)
                await runtime.dispatch(target.events.on_open, target);
              break;
            }
            case "notify":
              app.notify(String(op.message));
              break;
            case "refresh":
              setTick((v) => v + 1);
              options.refresh?.();
              break;
            case "submit":
              if (!options.submit)
                throw new Error("There is no form to submit in this view.");
              options.submit();
              break;
            case "execute": {
              const [modelPart, actionPart] = String(op.action).split(".");
              const model = op.model || (actionPart ? modelPart : view.model),
                action = actionPart || modelPart;
              const id = op.record_id || state.read("record.id");
              if (!model || !Number.isSafeInteger(id))
                throw new Error("Choose a record before running this action.");
              await recordRequest(model, id, "actions/" + action, {});
              setTick((v) => v + 1);
              options.refresh?.();
              app.notify("Action completed.");
              break;
            }
            default:
              throw new Error("Unsupported client operation: " + op.op);
          }
        }
      } catch (error) {
        void app.dialogs.error(messageFor(error));
      }
    },
  };
  return runtime;
}
export function ViewRenderer(props: {
  view: ViewSchema;
  params?: Record<string, string>;
}) {
  const runtime = makeRuntime(props.view);
  for (const [key, value] of Object.entries(props.params || {}))
    runtime.state.write("view." + key, value);
  return (
    <div class="custom-view">
      <Show when={props.view.layout.type !== "ProjectsDemo"}>
        <PageHeader title={props.view.title} subtitle={props.view.subtitle} />
      </Show>
      <div
        classList={{
          "page-scroll": props.view.layout.type !== "ProjectsDemo",
          "demo-host": props.view.layout.type === "ProjectsDemo",
        }}
      >
        <NodeRenderer node={props.view.layout} runtime={runtime} />
      </div>
    </div>
  );
}
