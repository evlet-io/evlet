import { createSignal } from "solid-js";
import { fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { expect, it, vi } from "vitest";
import { SelectInput, DatePicker, Modal, InputGroup, ListToolbar } from "../ui";
const choices = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Beta", disabled: true },
  { value: "c", label: "Charlie" },
];
it("searches options, skips disabled choices, and restores trigger focus", async () => {
  const changed = vi.fn();
  render(() => (
    <SelectInput
      aria-label="Team"
      value="a"
      options={choices}
      onChange={changed}
    />
  ));
  const trigger = screen.getByRole("combobox", { name: "Team" });
  fireEvent.click(trigger);
  const search = screen.getByRole("combobox", { name: "Search Team" });
  await waitFor(() => expect(search).toHaveFocus());
  fireEvent.keyDown(search, { key: "ArrowUp" });
  fireEvent.keyDown(search, { key: "Enter" });
  expect(changed).toHaveBeenCalledWith("c");
  expect(trigger).toHaveFocus();
  fireEvent.click(trigger);
  fireEvent.input(screen.getByRole("combobox", { name: "Search Team" }), {
    target: { value: "ALP" },
  });
  expect(screen.getAllByRole("option")).toHaveLength(1);
  fireEvent.click(screen.getByRole("option", { name: /Alpha/ }));
  expect(changed).toHaveBeenLastCalledWith("a");
  expect(document.querySelector("select")).toBeNull();
});
it("supports multiple selection, empty search results, and external resets", () => {
  let reset!: () => void;
  render(() => {
    const [value, setValue] = createSignal<string[]>(["a"]);
    reset = () => setValue([]);
    return (
      <SelectInput
        aria-label="Tags"
        multiple
        value={value()}
        options={choices}
        onMultipleChange={setValue}
      />
    );
  });
  fireEvent.click(screen.getByRole("combobox", { name: "Tags" }));
  fireEvent.click(screen.getByRole("option", { name: /Charlie/ }));
  expect(screen.getByRole("combobox", { name: "Tags" })).toHaveTextContent(
    "Alpha, Charlie",
  );
  reset();
  expect(screen.getByRole("option", { name: "Alpha" })).toHaveAttribute(
    "aria-selected",
    "false",
  );
  fireEvent.input(screen.getByRole("combobox", { name: "Search Tags" }), {
    target: { value: "missing" },
  });
  expect(screen.getByText("No matches")).toBeInTheDocument();
});
it("validates required selections and keeps disabled controls closed", () => {
  render(() => (
    <form>
      <SelectInput
        aria-label="Required choice"
        required
        value=""
        options={choices}
      />
      <SelectInput aria-label="Locked" disabled options={choices} />
    </form>
  ));
  expect(document.querySelector("form")!.checkValidity()).toBe(false);
  expect(
    screen.getByRole("combobox", { name: "Required choice" }),
  ).toHaveFocus();
  fireEvent.click(screen.getByRole("combobox", { name: "Locked" }));
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});
it("keeps a nested month dropdown inside its calendar and modal", async () => {
  render(() => (
    <Modal open title="Schedule" onClose={() => {}}>
      <DatePicker aria-label="Date" value="2026-12-10" onChange={() => {}} />
    </Modal>
  ));
  fireEvent.click(screen.getByRole("button", { name: "Choose date" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Month" }));
  const search = screen.getByRole("combobox", { name: "Search Month" });
  await waitFor(() => expect(search).toHaveFocus());
  fireEvent.input(search, { target: { value: "Feb" } });
  fireEvent.click(screen.getByRole("option", { name: "Feb" }));
  expect(screen.getByRole("grid", { name: "Feb 2026" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("combobox", { name: "Month" }));
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Search Month" }), {
    key: "Escape",
  });
  expect(
    screen.getByRole("dialog", { name: "Choose date" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Schedule" })).toHaveAttribute(
    "open",
  );
});
it("preserves exact prefixed values and places selection in the existing toolbar", () => {
  const changed = vi.fn();
  let select!: (value: boolean) => void;
  render(() => {
    const [selected, setSelected] = createSignal(false);
    select = setSelected;
    return (
      <>
        <InputGroup prefix="AED">
          <input
            aria-label="Amount"
            value="123456789.01"
            onInput={(e) => changed(e.currentTarget.value)}
          />
        </InputGroup>
        <ListToolbar
          leading={<span>Search records</span>}
          selection={selected() ? <span role="status">2 selected</span> : null}
        />
      </>
    );
  });
  fireEvent.input(screen.getByRole("textbox", { name: "Amount" }), {
    target: { value: "123456789.02" },
  });
  expect(changed).toHaveBeenCalledWith("123456789.02");
  const toolbar = document.querySelector(".ev-toolbar");
  select(true);
  expect(screen.getByRole("status").closest(".ev-toolbar")).toBe(toolbar);
  expect(screen.queryByText("Search records")).not.toBeInTheDocument();
  select(false);
  expect(screen.getByText("Search records")).toBeInTheDocument();
});
