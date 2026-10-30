import { createSignal } from "solid-js";
import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it, vi } from "vitest";
import { DatePicker, TimePicker, DateTimePicker, Modal } from "../ui";
import {
  parseDate,
  parseTime,
  parseDateTime,
  formatDate,
  formatDateTime,
  monthDays,
  popupPosition,
} from "../ui/components/date-time";

describe("editable date and time parsing", () => {
  it.each([
    ["101226", "2026-12-10"],
    ["10022026", "2026-02-10"],
    ["1/2/26", "2026-02-01"],
    ["10 Dec 2026", "2026-12-10"],
    ["10 december 26", "2026-12-10"],
    ["2026-12-10", "2026-12-10"],
    ["290224", "2024-02-29"],
    ["", ""],
    ["310226", null],
    ["290223", null],
    ["001226", null],
    ["2026-13-01", null],
    ["10 decc 2026", null],
    ["2026-12-10extra", null],
    ["1", null],
  ])("parses date %s without rolling invalid days", (raw, expected) =>
    expect(parseDate(raw!)).toBe(expected),
  );
  it.each([
    ["6.30pm", "18:30"],
    ["630pm", "18:30"],
    ["6 pm", "18:00"],
    ["18:30", "18:30"],
    ["0630", "06:30"],
    ["12am", "00:00"],
    ["12 pm", "12:00"],
    ["23:59:59.123456", "23:59:59.123456"],
    ["", ""],
    ["24:00", null],
    ["6.60pm", null],
    ["0pm", null],
    ["18pm", null],
    ["6:30:60", null],
    ["6x30", null],
  ])("normalizes time %s", (raw, expected) =>
    expect(parseTime(raw!)).toBe(expected),
  );
  it("formats dates and retains explicit offsets and subsecond precision", () => {
    expect(formatDate("2026-12-10")).toBe("10 Dec 2026");
    expect(parseDateTime("101226 6.30pm", "+04:00")).toBe(
      "2026-12-10T18:30+04:00",
    );
    expect(parseDateTime("2026-12-10T18:30", "+05:30")).toBe(
      "2026-12-10T18:30+05:30",
    );
    expect(parseDateTime("10 Dec 2026, 18:30 +05:30")).toBe(
      "2026-12-10T18:30+05:30",
    );
    expect(parseDateTime("2026-12-10T18:30:22.123456Z")).toBe(
      "2026-12-10T18:30:22.123456Z",
    );
    const stored = "2026-12-10T18:30:22.123456-03:30";
    expect(parseDateTime(formatDateTime(stored))).toBe(stored);
    expect(parseDateTime("31 Feb 2026, 18:30")).toBeNull();
    expect(parseDateTime("2026-12-10T18:30+25:00")).toBeNull();
    expect(monthDays("2026-12-10").flat()).toContain("2026-12-31");
  });
});

it("normalizes typing on Enter and blur and keeps invalid input visible", () => {
  const changed = vi.fn();
  render(() => (
    <DatePicker aria-label="Due date" value="2026-11-01" onChange={changed} />
  ));
  const input = screen.getByRole("textbox", {
    name: "Due date",
  }) as HTMLInputElement;
  expect(input).toHaveValue("01 Nov 2026");
  fireEvent.input(input, { target: { value: "101226" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(input).toHaveValue("10 Dec 2026");
  expect(changed).toHaveBeenLastCalledWith("2026-12-10");
  fireEvent.input(input, { target: { value: "310226" } });
  fireEvent.blur(input);
  expect(input).toHaveValue("310226");
  expect(input.checkValidity()).toBe(false);
  expect(changed).toHaveBeenCalledTimes(1);
  fireEvent.input(input, { target: { value: "290228" } });
  fireEvent.blur(input);
  expect(input).toHaveValue("29 Feb 2028");
  expect(input.checkValidity()).toBe(true);
});
it("normalizes time without changing the field while typing", () => {
  const changed = vi.fn(),
    draft = vi.fn();
  render(() => (
    <TimePicker
      aria-label="Start time"
      value="09:00"
      onChange={changed}
      onDraftChange={draft}
    />
  ));
  const input = screen.getByRole("textbox", { name: "Start time" });
  fireEvent.input(input, { target: { value: "6.30pm" } });
  expect(input).toHaveValue("6.30pm");
  expect(draft).toHaveBeenLastCalledWith("6.30pm");
  fireEvent.blur(input);
  expect(input).toHaveValue("18:30");
  expect(changed).toHaveBeenLastCalledWith("18:30");
});
it("accepts external resets even while a draft is being edited", () => {
  let reset!: () => void;
  render(() => {
    const [value, setValue] = createSignal("2026-12-10");
    reset = () => setValue("");
    return (
      <DatePicker
        aria-label="Date"
        value={value()}
        onChange={setValue}
        onDraftChange={setValue}
      />
    );
  });
  const input = screen.getByRole("textbox", { name: "Date" });
  fireEvent.input(input, { target: { value: "bad draft" } });
  reset();
  expect(input).toHaveValue("");
  expect(input).not.toHaveAttribute("aria-invalid", "true");
});
it("selects calendar dates, closes on Escape, and returns focus", async () => {
  const changed = vi.fn();
  render(() => (
    <DatePicker aria-label="Date" value="2026-12-10" onChange={changed} />
  ));
  fireEvent.click(screen.getByRole("button", { name: "Choose date" }));
  expect(
    screen.getByRole("dialog", { name: "Choose date" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("gridcell", { name: "12 Dec 2026" }));
  expect(changed).toHaveBeenLastCalledWith("2026-12-12");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Date" })).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Date" }), {
    key: "ArrowDown",
    altKey: true,
  });
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("keeps date/time popovers inside a modal and preserves the stored timezone", () => {
  const changed = vi.fn();
  render(() => (
    <Modal title="Edit" open onClose={() => {}}>
      <DateTimePicker
        aria-label="Meeting"
        value="2026-12-10T09:00:00+04:00"
        onChange={changed}
      />
    </Modal>
  ));
  fireEvent.click(screen.getByRole("button", { name: "Choose date and time" }));
  const popup = screen.getByRole("dialog", { name: "Choose date and time" });
  expect(popup.closest("dialog")).toBe(
    screen.getByRole("dialog", { name: "Edit" }),
  );
  fireEvent.click(screen.getByRole("gridcell", { name: "11 Dec 2026" }));
  fireEvent.input(screen.getByRole("textbox", { name: /^Time$/ }), {
    target: { value: "6.30pm" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(changed).toHaveBeenLastCalledWith("2026-12-11T18:30+04:00");
});
it("does not open a disabled or read-only date field", () => {
  render(() => (
    <>
      <DatePicker
        aria-label="Locked"
        readOnly
        value="2026-12-10"
        onChange={() => {}}
      />
      <TimePicker
        aria-label="Disabled"
        disabled
        value="18:30"
        onChange={() => {}}
      />
    </>
  ));
  fireEvent.click(screen.getByRole("textbox", { name: "Locked" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Choose date" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Choose time" })).toBeDisabled();
});
it("anchors below the input, flips above, and clamps to narrow viewports", () => {
  expect(
    popupPosition(
      { left: 200, top: 80, bottom: 116, width: 320 },
      { width: 290, height: 300 },
      { width: 1000, height: 800 },
    ),
  ).toMatchObject({ left: 200, top: 122, width: 290, placement: "bottom" });
  expect(
    popupPosition(
      { left: 800, top: 680, bottom: 716, width: 180 },
      { width: 290, height: 300 },
      { width: 1000, height: 800 },
    ),
  ).toMatchObject({ left: 702, top: 374, placement: "top" });
  const narrow = popupPosition(
    { left: 250, top: 400, bottom: 436, width: 120 },
    { width: 290, height: 330 },
    { width: 320, height: 700 },
  );
  expect(narrow.left).toBe(22);
  expect(narrow.width).toBe(290);
  expect(narrow.top).toBe(64);
  const keyboard = popupPosition(
    { left: 16, top: 140, bottom: 176, width: 288 },
    { width: 290, height: 330 },
    { width: 320, height: 300 },
  );
  expect(keyboard.maxHeight).toBeLessThan(150);
});
