import {
  children,
  createEffect,
  createMemo,
  createSignal,
  createUniqueId,
  For,
  Show,
  splitProps,
  type JSX,
} from "solid-js";
import { Portal } from "solid-js/web";
import { AnchoredPopover } from "./AnchoredPopover";
import { callHandler, forwardRef } from "./helpers";

export interface SelectOption {
  value: string | number;
  label: string;
  disabled?: boolean;
}
export interface SelectInputProps extends Omit<
  JSX.ButtonHTMLAttributes<HTMLButtonElement>,
  "value" | "onChange" | "children" | "type"
> {
  value?: string | number | readonly string[] | null;
  options?: readonly SelectOption[];
  children?: JSX.Element;
  placeholder?: string;
  required?: boolean;
  multiple?: boolean;
  onChange?: (value: string) => void;
  onMultipleChange?: (value: string[]) => void;
  onSearch?: (query: string) => void;
  searchLabel?: string;
  loading?: boolean;
  footer?: JSX.Element;
}
export function SelectInput(props: SelectInputProps) {
  const [p, rest] = splitProps(props, [
    "value",
    "options",
    "children",
    "placeholder",
    "required",
    "multiple",
    "onChange",
    "onMultipleChange",
    "onSearch",
    "searchLabel",
    "loading",
    "footer",
    "class",
    "id",
    "ref",
    "name",
    "onKeyDown",
  ]);
  const uid = createUniqueId(),
    listId = uid + "-options";
  let trigger!: HTMLButtonElement,
    anchor!: HTMLDivElement,
    search!: HTMLInputElement,
    panel!: HTMLDivElement;
  const [open, setOpen] = createSignal(false),
    [query, setQuery] = createSignal(""),
    [active, setActive] = createSignal(-1);
  const optionChildren = children(() => p.children);
  const options = createMemo<SelectOption[]>(() =>
    p.options
      ? [...p.options]
      : optionChildren
          .toArray()
          .flatMap((node) =>
            node instanceof HTMLOptionElement
              ? [
                  {
                    value: node.value,
                    label: node.textContent || node.value,
                    disabled: node.disabled,
                  },
                ]
              : [],
          ),
  );
  const values = () =>
    Array.isArray(p.value)
      ? p.value.map(String)
      : p.value == null
        ? []
        : [String(p.value)];
  const selected = (option: SelectOption) =>
    values().includes(String(option.value));
  const text = () =>
    options()
      .filter(selected)
      .map((o) => o.label)
      .join(", ") ||
    values().filter(Boolean).join(", ") ||
    p.placeholder ||
    "Choose a value";
  const filtered = createMemo(() =>
    p.onSearch
      ? options()
      : options().filter((o) =>
          o.label
            .toLocaleLowerCase()
            .includes(query().trim().toLocaleLowerCase()),
        ),
  );
  const label = () =>
    p.searchLabel ||
    rest["aria-label"] ||
    trigger?.labels?.[0]?.textContent?.trim() ||
    "options";
  createEffect(() => {
    filtered();
    setActive(-1);
  });
  createEffect(() => {
    if (rest.disabled) setOpen(false);
  });
  function close(focus = false) {
    setOpen(false);
    if (focus) trigger.focus();
  }
  function show(initial = "") {
    if (rest.disabled) return;
    setQuery(initial);
    p.onSearch?.(initial);
    setOpen(true);
    queueMicrotask(() => search?.focus());
  }
  function choose(option: SelectOption) {
    if (option.disabled || rest.disabled) return;
    const value = String(option.value);
    if (p.multiple) {
      p.onMultipleChange?.(
        selected(option)
          ? values().filter((v) => v !== value)
          : [...values(), value],
      );
      search.focus();
    } else {
      p.onChange?.(value);
      close(true);
    }
  }
  function move(direction: number) {
    const rows = filtered();
    let next = active();
    for (let i = 0; i < rows.length; i++) {
      next =
        next < 0
          ? direction > 0
            ? 0
            : rows.length - 1
          : (next + direction + rows.length) % rows.length;
      if (!rows[next].disabled) {
        setActive(next);
        panel
          .querySelector(`[data-index="${next}"]`)
          ?.scrollIntoView({ block: "nearest" });
        return;
      }
    }
  }
  return (
    <div class="ev-select" ref={anchor}>
      <button
        {...rest}
        ref={(el) => {
          trigger = el;
          forwardRef(p.ref, el);
        }}
        id={p.id || uid}
        type="button"
        class={"ev-select-trigger " + (p.class || "")}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open()}
        aria-controls={open() ? listId : undefined}
        aria-required={p.required}
        onClick={() => (open() ? close(true) : show())}
        onKeyDown={(e) => {
          callHandler(p.onKeyDown, e);
          if (e.defaultPrevented) return;
          if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
            e.preventDefault();
            show();
          } else if (
            e.key.length === 1 &&
            !e.ctrlKey &&
            !e.metaKey &&
            !e.altKey
          ) {
            e.preventDefault();
            show(e.key);
          }
        }}
      >
        <span class="ev-select-value">{text()}</span>
        <svg
          class="ev-select-chevron"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
          aria-hidden="true"
        >
          <path d="m6 8 4 4 4-4" />
        </svg>
      </button>
      <input
        class="ev-select-validation"
        tabindex={-1}
        aria-hidden="true"
        value={values().filter(Boolean).join(",")}
        required={p.required}
        disabled={rest.disabled}
        onInvalid={(e) => {
          e.preventDefault();
          trigger.focus();
        }}
      />
      <Show when={p.name}>
        <For each={values()}>
          {(value) => (
            <input
              type="hidden"
              name={p.name}
              value={value}
              disabled={rest.disabled}
            />
          )}
        </For>
      </Show>
      <Show when={open()}>
        <Portal
          mount={
            trigger.closest(".ev-anchored-popover, dialog") || document.body
          }
        >
          <AnchoredPopover
            anchor={anchor}
            control={trigger}
            id={uid + "-popup"}
            label={String(label())}
            matchWidth
            width={220}
            class="ev-select-popup"
            panelRef={(el) => (panel = el)}
            onClose={close}
          >
            <input
              ref={search}
              class="ev-select-search"
              type="search"
              role="combobox"
              aria-label={"Search " + label()}
              placeholder="Search…"
              autocomplete="off"
              aria-expanded="true"
              aria-controls={listId}
              aria-activedescendant={
                active() >= 0 ? `${listId}-${active()}` : undefined
              }
              value={query()}
              onInput={(e) => {
                setQuery(e.currentTarget.value);
                p.onSearch?.(query());
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                  e.preventDefault();
                  move(e.key === "ArrowDown" ? 1 : -1);
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  const item =
                    filtered()[active()] || filtered().find((o) => !o.disabled);
                  if (item) choose(item);
                } else if (e.key === "Tab") {
                  close(true);
                }
              }}
            />
            <div
              class="ev-select-options"
              id={listId}
              role="listbox"
              aria-label={String(label())}
              aria-multiselectable={p.multiple || undefined}
              aria-busy={p.loading}
            >
              <For each={filtered()}>
                {(option, index) => (
                  <button
                    type="button"
                    role="option"
                    tabindex={-1}
                    id={`${listId}-${index()}`}
                    data-index={index()}
                    aria-selected={selected(option)}
                    disabled={option.disabled}
                    classList={{ "is-active": active() === index() }}
                    onPointerMove={() => setActive(index())}
                    onClick={() => choose(option)}
                  >
                    <span>{option.label}</span>
                    <Show when={selected(option)}>
                      <span class="ev-option-check" aria-hidden="true">
                        ✓
                      </span>
                    </Show>
                  </button>
                )}
              </For>
              <Show when={!filtered().length}>
                <p class="ev-select-empty">
                  {p.loading ? "Loading…" : "No matches"}
                </p>
              </Show>
            </div>
            <Show when={p.footer}>
              <div class="ev-select-footer">{p.footer}</div>
            </Show>
          </AnchoredPopover>
        </Portal>
      </Show>
    </div>
  );
}
