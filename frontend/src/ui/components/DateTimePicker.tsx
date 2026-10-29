import {
  createEffect,
  createMemo,
  createSignal,
  createUniqueId,
  For,
  on,
  onCleanup,
  onMount,
  Show,
  splitProps,
  type JSX,
} from "solid-js";
import { Portal } from "solid-js/web";
import { callHandler, forwardRef } from "./helpers";
import {
  MONTHS,
  addDays,
  dateTimeParts,
  dateValue,
  formatDate,
  formatDateTime,
  monthDays,
  offsetFor,
  parseDate,
  parseDateTime,
  parseTime,
  popupPosition,
  todayValue,
} from "./date-time";

type Mode = "date" | "time" | "datetime";
export interface DateTimeInputProps extends Omit<
  JSX.InputHTMLAttributes<HTMLInputElement>,
  "value" | "onChange" | "onInput" | "type"
> {
  value?: string | null;
  onChange: (value: string) => void;
  onDraftChange?: (value: string) => void;
  pickerLabel?: string;
}
export const DatePicker = (props: DateTimeInputProps) => (
  <DateTimeInput {...props} mode="date" />
);
export const TimePicker = (props: DateTimeInputProps) => (
  <DateTimeInput {...props} mode="time" />
);
export const DateTimePicker = (props: DateTimeInputProps) => (
  <DateTimeInput {...props} mode="datetime" />
);

function DateTimeInput(props: DateTimeInputProps & { mode: Mode }) {
  const [p, rest] = splitProps(props, [
    "mode",
    "value",
    "onChange",
    "onDraftChange",
    "pickerLabel",
    "ref",
    "onFocus",
    "onBlur",
    "onKeyDown",
    "class",
    "id",
    "placeholder",
    "aria-describedby",
    "aria-invalid",
  ]);
  const uid = createUniqueId(),
    inputId = () => p.id || uid,
    popupId = uid + "-picker",
    errorId = uid + "-error";
  let input!: HTMLInputElement,
    anchor!: HTMLDivElement,
    panel: HTMLDivElement | undefined,
    editing = false;
  let lastOffset: string | undefined;
  const [draft, setDraft] = createSignal(""),
    [error, setError] = createSignal(""),
    [open, setOpen] = createSignal(false);
  const [day, setDay] = createSignal(todayValue()),
    [month, setMonth] = createSignal(todayValue()),
    [time, setTime] = createSignal("09:00"),
    [timeError, setTimeError] = createSignal("");
  const label = () =>
    p.pickerLabel ||
    (p.mode === "time"
      ? "Choose time"
      : p.mode === "datetime"
        ? "Choose date and time"
        : "Choose date");
  const format = (value: string) =>
    p.mode === "date"
      ? formatDate(value)
      : p.mode === "datetime"
        ? formatDateTime(value)
        : value;
  createEffect(
    on(
      () => p.value,
      (raw) => {
        const value = raw || "";
        const parsed = p.mode === "datetime" ? dateTimeParts(value) : null;
        if (parsed) lastOffset = parsed.offset;
        else if (!value) lastOffset = undefined;
        if (!editing || value !== draft()) {
          editing = false;
          setDraft(format(value));
          setError("");
          input?.setCustomValidity("");
        }
      },
    ),
  );
  createEffect(() => {
    if (rest.disabled || rest.readOnly) setOpen(false);
  });
  const normalize = (value: string) =>
    p.mode === "date"
      ? parseDate(value)
      : p.mode === "time"
        ? parseTime(value)
        : parseDateTime(value, lastOffset);
  function emit(value: string) {
    editing = false;
    setError("");
    input.setCustomValidity("");
    setDraft(format(value));
    p.onChange(value);
  }
  function commit() {
    if (!editing) return true;
    const value = normalize(draft());
    if (value === null) {
      const message =
        p.mode === "date"
          ? "Use a valid date, such as 101226 or 10 Dec 2026."
          : p.mode === "time"
            ? "Use a valid time, such as 6.30pm or 18:30."
            : "Use a date and time, such as 10 Dec 2026, 18:30.";
      setError(message);
      input.setCustomValidity(message);
      return false;
    }
    emit(value);
    return true;
  }
  function close(focus = false) {
    setOpen(false);
    if (focus) input.focus();
  }
  function show(focus = false) {
    if (rest.disabled || rest.readOnly) return;
    const normalized = normalize(draft()) || "";
    const parts = p.mode === "datetime" ? dateTimeParts(normalized) : null;
    const selected =
      (p.mode === "date" ? normalized : parts?.date) || todayValue();
    setDay(selected);
    setMonth(selected);
    setTime(p.mode === "time" ? normalized || "09:00" : parts?.time || "09:00");
    setTimeError("");
    setOpen(true);
    requestAnimationFrame(() => {
      const current = panel?.querySelector<HTMLButtonElement>(
        '[data-current="true"]',
      );
      if (p.mode === "time") current?.scrollIntoView({ block: "nearest" });
      if (focus)
        (
          current || panel?.querySelector<HTMLButtonElement>('[role="option"]')
        )?.focus();
    });
  }
  function selectDate(value: string) {
    setDay(value);
    setMonth(value);
    if (p.mode === "date") {
      emit(value);
      close(true);
    }
  }
  function applyDateTime() {
    const parsed = parseTime(time());
    if (!parsed) {
      setTimeError("Enter a valid time.");
      return;
    }
    emit(`${day()}T${parsed}${lastOffset || offsetFor(day(), parsed)}`);
    close(true);
  }
  function shiftMonth(amount: number) {
    const [year, m, d] = month().split("-").map(Number);
    const target = new Date(Date.UTC(year, m - 1 + amount, 1));
    const days = new Date(
      Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0),
    ).getUTCDate();
    const value = dateValue(
      target.getUTCFullYear(),
      target.getUTCMonth() + 1,
      Math.min(d, days),
    );
    if (value) {
      setMonth(value);
      setDay(value);
    }
  }
  function calendarKey(event: KeyboardEvent, value: string) {
    const offsets: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -7,
      ArrowDown: 7,
    };
    let next: string | undefined;
    if (event.key in offsets) next = addDays(value, offsets[event.key]);
    else if (event.key === "Home")
      next = addDays(
        value,
        -((new Date(value + "T12:00:00Z").getUTCDay() + 6) % 7),
      );
    else if (event.key === "End")
      next = addDays(
        value,
        6 - ((new Date(value + "T12:00:00Z").getUTCDay() + 6) % 7),
      );
    else if (event.key === "PageUp" || event.key === "PageDown") {
      shiftMonth(event.key === "PageUp" ? -1 : 1);
      next = day();
    }
    if (next) {
      event.preventDefault();
      setDay(next);
      setMonth(next);
      queueMicrotask(() =>
        panel
          ?.querySelector<HTMLButtonElement>(`[data-date="${next}"]`)
          ?.focus(),
      );
    }
  }
  const weeks = createMemo(() => monthDays(month()));
  const timeOptions = Array.from(
    { length: 48 },
    (_, i) =>
      `${String(Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}`,
  );
  return (
    <div class="ev-date-control" ref={anchor}>
      <input
        {...rest}
        id={inputId()}
        ref={(element) => {
          input = element;
          forwardRef(p.ref, element);
        }}
        class={p.class}
        type="text"
        autocomplete="off"
        value={draft()}
        placeholder={
          p.placeholder ||
          (p.mode === "date"
            ? "DD MMM YYYY"
            : p.mode === "time"
              ? "HH:mm"
              : "DD MMM YYYY, HH:mm")
        }
        aria-invalid={!!error() || p["aria-invalid"]}
        aria-describedby={
          [p["aria-describedby"], error() && errorId]
            .filter(Boolean)
            .join(" ") || undefined
        }
        aria-haspopup="dialog"
        aria-expanded={open()}
        aria-controls={open() ? popupId : undefined}
        onClick={() => show()}
        onFocus={(e) => {
          const h = p.onFocus;
          if (typeof h === "function") h(e);
          else if (Array.isArray(h)) h[0](h[1], e);
        }}
        onInput={(e) => {
          editing = true;
          setDraft(e.currentTarget.value);
          setError("");
          input.setCustomValidity("");
          p.onDraftChange?.(e.currentTarget.value);
        }}
        onBlur={(e) => {
          commit();
          const h = p.onBlur;
          if (typeof h === "function") h(e);
          else if (Array.isArray(h)) h[0](h[1], e);
        }}
        onKeyDown={(e) => {
          callHandler(p.onKeyDown, e);
          if (e.defaultPrevented) return;
          if (e.key === "Escape" && open()) {
            e.preventDefault();
            e.stopPropagation();
            close();
          } else if (e.key === "ArrowDown" && e.altKey) {
            e.preventDefault();
            show(true);
          } else if (e.key === "Enter" && editing) {
            e.preventDefault();
            if (commit()) close();
          }
        }}
      />
      <button
        type="button"
        class="ev-date-toggle"
        aria-label={label()}
        aria-expanded={open()}
        aria-controls={open() ? popupId : undefined}
        disabled={rest.disabled || rest.readOnly}
        onClick={() => (open() ? close(true) : show(true))}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.6"
          aria-hidden="true"
        >
          <Show
            when={p.mode !== "time"}
            fallback={
              <>
                <circle cx="12" cy="12" r="8" />
                <path d="M12 7v5l3 2" />
              </>
            }
          >
            <rect x="4" y="5" width="16" height="16" rx="3" />
            <path d="M8 3v4m8-4v4M4 10h16m-11 4h3m3 0h3m-9 3h3" />
          </Show>
        </svg>
      </button>
      <Show when={error()}>
        <small id={errorId} class="ev-field-error" role="alert">
          {error()}
        </small>
      </Show>
      <Show when={open()}>
        <Portal mount={input.closest("dialog") || document.body}>
          <PickerPopup
            anchor={anchor}
            control={input}
            id={popupId}
            label={label()}
            timeOnly={p.mode === "time"}
            onClose={close}
            panelRef={(el) => (panel = el)}
          >
            <Show
              when={p.mode !== "time"}
              fallback={
                <>
                  <div class="ev-picker-heading">
                    <strong>Select time</strong>
                    <span>24-hour clock</span>
                  </div>
                  <div
                    class="ev-time-options"
                    role="listbox"
                    aria-label="Time options"
                  >
                    <For each={timeOptions}>
                      {(option, index) => (
                        <button
                          type="button"
                          role="option"
                          aria-selected={time().slice(0, 5) === option}
                          data-current={time().slice(0, 5) === option}
                          onClick={() => {
                            emit(option);
                            close(true);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                              e.preventDefault();
                              const buttons =
                                e.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>(
                                  "button",
                                );
                              buttons[
                                (index() + (e.key === "ArrowDown" ? 1 : 47)) %
                                  48
                              ].focus();
                            }
                          }}
                        >
                          {option}
                        </button>
                      )}
                    </For>
                  </div>
                </>
              }
            >
              <div class="ev-calendar-heading">
                <button
                  type="button"
                  aria-label="Previous month"
                  onClick={() => shiftMonth(-1)}
                >
                  ‹
                </button>
                <select
                  aria-label="Month"
                  value={+month().slice(5, 7)}
                  onChange={(e) => {
                    const [y, , d] = month().split("-").map(Number),
                      m = +e.currentTarget.value;
                    const value = dateValue(y, m, d) || dateValue(y, m, 1)!;
                    setMonth(value);
                    setDay(value);
                  }}
                >
                  <For each={MONTHS}>
                    {(name, i) => (
                      <option
                        value={i() + 1}
                        selected={+month().slice(5, 7) === i() + 1}
                      >
                        {name}
                      </option>
                    )}
                  </For>
                </select>
                <input
                  aria-label="Year"
                  inputmode="numeric"
                  maxlength={4}
                  value={month().slice(0, 4)}
                  onChange={(e) => {
                    const [, m, d] = month().split("-").map(Number),
                      y = Number(e.currentTarget.value);
                    const value = dateValue(y, m, d) || dateValue(y, m, 1);
                    if (value) {
                      setMonth(value);
                      setDay(value);
                    } else e.currentTarget.value = month().slice(0, 4);
                  }}
                />
                <button
                  type="button"
                  aria-label="Next month"
                  onClick={() => shiftMonth(1)}
                >
                  ›
                </button>
              </div>
              <div
                class="ev-calendar-grid"
                role="grid"
                aria-label={
                  MONTHS[+month().slice(5, 7) - 1] + " " + month().slice(0, 4)
                }
              >
                <div role="row" class="ev-calendar-week">
                  <For each={["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]}>
                    {(name) => <span role="columnheader">{name}</span>}
                  </For>
                </div>
                <For each={weeks()}>
                  {(week) => (
                    <div role="row" class="ev-calendar-week">
                      <For each={week}>
                        {(value) => (
                          <button
                            type="button"
                            role="gridcell"
                            aria-label={formatDate(value)}
                            aria-selected={value === day()}
                            aria-current={
                              value === todayValue() ? "date" : undefined
                            }
                            data-date={value}
                            data-current={value === day()}
                            tabIndex={value === day() ? 0 : -1}
                            classList={{
                              "is-outside":
                                value.slice(0, 7) !== month().slice(0, 7),
                              "is-selected": value === day(),
                              "is-today": value === todayValue(),
                            }}
                            onClick={() => selectDate(value)}
                            onKeyDown={(e) => calendarKey(e, value)}
                          >
                            {Number(value.slice(-2))}
                          </button>
                        )}
                      </For>
                    </div>
                  )}
                </For>
              </div>
              <Show when={p.mode === "datetime"}>
                <div class="ev-picker-time-row">
                  <label>
                    Time
                    <input
                      aria-label="Time"
                      value={time()}
                      onInput={(e) => {
                        setTime(e.currentTarget.value);
                        setTimeError("");
                      }}
                      onBlur={() => {
                        const parsed = parseTime(time());
                        if (parsed) setTime(parsed);
                        else setTimeError("Enter a valid time.");
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          applyDateTime();
                        }
                      }}
                    />
                  </label>
                  <span class="ev-picker-zone">
                    {lastOffset ||
                      offsetFor(day(), parseTime(time()) || "09:00")}
                  </span>
                  <button
                    type="button"
                    class="ev-picker-apply"
                    onClick={applyDateTime}
                  >
                    Apply
                  </button>
                </div>
                <Show when={timeError()}>
                  <small role="alert" class="ev-field-error">
                    {timeError()}
                  </small>
                </Show>
              </Show>
            </Show>
            <div class="ev-picker-footer">
              <button
                type="button"
                onClick={() => {
                  if (p.mode === "time") {
                    const now = new Date();
                    emit(
                      `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`,
                    );
                    close(true);
                  } else selectDate(todayValue());
                }}
              >
                {p.mode === "time" ? "Now" : "Today"}
              </button>
              <span>{p.mode === "time" ? "Type 6.30pm" : "Type 101226"}</span>
              <button
                type="button"
                onClick={() => {
                  emit("");
                  close(true);
                }}
              >
                Clear
              </button>
            </div>
          </PickerPopup>
        </Portal>
      </Show>
    </div>
  );
}

function PickerPopup(props: {
  anchor: HTMLElement;
  control: HTMLInputElement;
  id: string;
  label: string;
  timeOnly: boolean;
  onClose: (focus?: boolean) => void;
  panelRef: (el: HTMLDivElement) => void;
  children: JSX.Element;
}) {
  let panel!: HTMLDivElement,
    frame = 0;
  const [position, setPosition] = createSignal({
    left: 0,
    top: 0,
    width: props.timeOnly ? 252 : 290,
    maxHeight: 500,
    placement: "bottom",
  });
  const update = () => {
    const rect = props.control.getBoundingClientRect(),
      visual = window.visualViewport;
    const viewport = {
      width: visual?.width || window.innerWidth,
      height: visual?.height || window.innerHeight,
      left: visual?.offsetLeft || 0,
      top: visual?.offsetTop || 0,
    };
    if (
      rect.bottom < (viewport.top || 0) ||
      rect.top > viewport.height + (viewport.top || 0)
    ) {
      props.onClose();
      return;
    }
    for (
      let ancestor = props.anchor.parentElement;
      ancestor && ancestor !== document.body;
      ancestor = ancestor.parentElement
    ) {
      const style = getComputedStyle(ancestor);
      if (/auto|scroll|hidden|clip/.test(style.overflowY)) {
        const bounds = ancestor.getBoundingClientRect();
        if (rect.bottom <= bounds.top || rect.top >= bounds.bottom) {
          props.onClose();
          return;
        }
      }
    }
    setPosition(
      popupPosition(
        rect,
        {
          width: props.timeOnly ? 252 : 290,
          height: panel.scrollHeight + panel.offsetHeight - panel.clientHeight,
        },
        viewport,
      ),
    );
  };
  const schedule = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(update);
  };
  onMount(() => {
    panel.showPopover?.();
    update();
    const outside = (e: Event) => {
      if (
        !props.anchor.contains(e.target as Node) &&
        !panel.contains(e.target as Node)
      )
        props.onClose();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        props.onClose(true);
      }
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", outside, true);
    document.addEventListener("keydown", key, true);
    document.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    window.visualViewport?.addEventListener("resize", schedule);
    window.visualViewport?.addEventListener("scroll", schedule);
    const resize = new ResizeObserver(schedule);
    resize.observe(props.anchor);
    resize.observe(panel);
    onCleanup(() => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      panel.hidePopover?.();
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("focusin", outside, true);
      document.removeEventListener("keydown", key, true);
      document.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      window.visualViewport?.removeEventListener("resize", schedule);
      window.visualViewport?.removeEventListener("scroll", schedule);
    });
  });
  return (
    <div
      ref={(el) => {
        panel = el;
        props.panelRef(el);
      }}
      id={props.id}
      class="ev-picker-popover"
      popover={
        typeof HTMLElement.prototype.showPopover === "function"
          ? "manual"
          : undefined
      }
      role="dialog"
      aria-label={props.label}
      data-placement={position().placement}
      style={{
        left: position().left + "px",
        top: position().top + "px",
        width: position().width + "px",
        "max-height": position().maxHeight + "px",
      }}
    >
      {props.children}
    </div>
  );
}
