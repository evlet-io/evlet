import { SelectInput } from "../ui";
import {
  createSignal,
  createResource,
  onCleanup,
  Show,
  Switch,
  Match,
  For,
} from "solid-js";
import type { FieldSchema } from "../runtime/types";
import { useApp, messageFor } from "../runtime/Context";
import { getRecord, listRecords } from "../api";
import { recordLabel } from "../schema";
import { Button, DatePicker, DateTimePicker, InputGroup } from "../ui";
export interface FieldInputProps {
  field: FieldSchema;
  value: any;
  onChange: (value: any) => void;
  id: string;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
  required?: boolean;
  prefix?: string;
  suffix?: string;
}
export default function FieldInput(props: FieldInputProps) {
  const required = () => props.required ?? props.field.required;
  const common = () => ({
    id: props.id,
    disabled: props.disabled,
    required: required(),
    "aria-invalid": props.invalid,
    "aria-describedby": props.describedBy,
  });
  return (
    <Switch>
      <Match
        when={
          props.field.type === "integer" &&
          typeof props.value === "number" &&
          !Number.isSafeInteger(props.value)
        }
      >
        <p class="error-banner">
          This value exceeds the browser integer range. Use the Python API to
          read or change it exactly.
        </p>
      </Match>
      <Match when={props.field.type === "date"}>
        <DatePicker
          {...common()}
          value={props.value}
          onChange={props.onChange}
          onDraftChange={props.onChange}
        />
      </Match>
      <Match when={props.field.type === "datetime"}>
        <DateTimePicker
          {...common()}
          value={props.value}
          onChange={props.onChange}
          onDraftChange={props.onChange}
        />
      </Match>
      <Match when={props.field.type === "link"}>
        <LinkInput {...props} />
      </Match>
      <Match when={props.field.type === "boolean" && required()}>
        <input
          {...common()}
          required={false}
          type="checkbox"
          class="ev-check"
          checked={!!props.value}
          onChange={(e) => props.onChange(e.currentTarget.checked)}
        />
      </Match>
      <Match
        when={props.field.type === "select" || props.field.type === "boolean"}
      >
        <SelectInput
          {...common()}
          value={props.value == null ? "" : String(props.value)}
          onChange={(nextValue) =>
            props.onChange(
              nextValue === ""
                ? null
                : props.field.type === "boolean"
                  ? nextValue === "true"
                  : nextValue,
            )
          }
        >
          <option value="" selected={props.value == null || props.value === ""}>
            {required() ? "Choose a value" : "Not set"}
          </option>
          <Show
            when={props.field.type === "boolean"}
            fallback={
              <For each={props.field.choices}>
                {(choice) => (
                  <option
                    value={choice}
                    selected={String(props.value) === choice}
                  >
                    {choice}
                  </option>
                )}
              </For>
            }
          >
            <option value="true" selected={props.value === true}>
              Yes
            </option>
            <option value="false" selected={props.value === false}>
              No
            </option>
          </Show>
        </SelectInput>
      </Match>
      <Match when={props.field.type === "longtext"}>
        <textarea
          {...common()}
          value={props.value ?? ""}
          maxLength={props.field.max_length}
          placeholder={props.field.placeholder}
          rows={4}
          onInput={(e) => props.onChange(e.currentTarget.value)}
        />
      </Match>
      <Match when={true}>
        <InputGroup prefix={props.prefix} suffix={props.suffix}>
          <input
            {...common()}
            value={props.value == null ? "" : String(props.value)}
            type={
              ["date", "email", "url"].includes(props.field.type)
                ? props.field.type
                : "text"
            }
            inputMode={
              ["currency", "decimal"].includes(props.field.type)
                ? "decimal"
                : props.field.type === "integer"
                  ? "numeric"
                  : undefined
            }
            maxLength={props.field.max_length}
            placeholder={
              props.field.placeholder ||
              (props.field.type === "datetime"
                ? "2026-10-29T09:00:00+04:00"
                : undefined)
            }
            onInput={(e) => props.onChange(e.currentTarget.value)}
          />
        </InputGroup>
      </Match>
    </Switch>
  );
}
function LinkInput(props: FieldInputProps) {
  const app = useApp();
  const [query, setQuery] = createSignal(""),
    [offset, setOffset] = createSignal(0);
  const target = () => app.models.find((m) => m.name === props.field.target);
  const [page, { refetch }] = createResource(
    () => ({ target: props.field.target, q: query(), offset: offset() }),
    (args) => listRecords(args.target!, 20, args.offset, {}, "-id", args.q),
  );
  const [selected, { refetch: reloadSelected }] = createResource(
    () => props.value,
    (value) => getRecord(props.field.target!, Number(value)),
  );
  const options = () => {
    const items = [...(page.error ? [] : page()?.items || [])];
    const current = selected.error ? undefined : selected();
    if (current && !items.some((r) => r.id === current.id))
      items.unshift(current);
    return items;
  };
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  onCleanup(() => clearTimeout(searchTimer));
  const find = (text: string) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      setOffset(0);
      setQuery(text.trim());
    }, 200);
  };
  return (
    <div class="link-picker">
      <SelectInput
        onSearch={find}
        searchLabel={props.field.label}
        loading={page.loading}
        footer={
          <>
            <Show when={offset() > 0}>
              <Button
                size="small"
                onClick={() => setOffset((v) => Math.max(0, v - 20))}
              >
                Previous choices
              </Button>
            </Show>
            <Show when={offset() + 20 < (page.error ? 0 : page()?.total || 0)}>
              <Button size="small" onClick={() => setOffset((v) => v + 20)}>
                More choices
              </Button>
            </Show>
          </>
        }
        id={props.id}
        value={props.value ?? ""}
        required={props.required ?? props.field.required}
        disabled={props.disabled}
        aria-invalid={props.invalid}
        aria-describedby={props.describedBy}
        onChange={(nextValue) =>
          props.onChange(nextValue ? Number(nextValue) : null)
        }
      >
        <option value="" selected={props.value == null}>
          Choose {props.field.label.toLowerCase()}
        </option>
        <Show
          when={
            props.value != null && !options().some((r) => r.id === props.value)
          }
        >
          <option value={props.value} selected>
            #{props.value}
          </option>
        </Show>
        <For each={options()}>
          {(record) => (
            <option
              value={record.id}
              selected={record.id === Number(props.value)}
            >
              {target() ? recordLabel(target()!, record) : "#" + record.id}
            </option>
          )}
        </For>
      </SelectInput>
      <Show when={page.loading}>
        <small role="status">Loading choices…</small>
      </Show>
      <Show when={page.error || selected.error}>
        <small class="ev-field-error" role="alert">
          {messageFor(page.error || selected.error)}{" "}
          <button
            type="button"
            onClick={() => {
              void refetch();
              void reloadSelected();
            }}
          >
            Retry choices
          </button>
        </small>
      </Show>
    </div>
  );
}
