import { createRoot } from "solid-js";
import { render, screen, fireEvent, waitFor, within } from "@solidjs/testing-library";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createDialogs, DialogHost, Button, SelectInput, useValidationDialogs, type DialogController } from "../ui";
import { createRouter } from "../runtime/Router";

const options = { title: "Discard unsaved changes?", message: "Your changes have not been saved.", confirmLabel: "Discard changes", cancelLabel: "Keep editing" };
let dialogs: DialogController;
let router: ReturnType<typeof createRouter>;
let disposeRouter: () => void;
beforeEach(() => {
  history.replaceState({}, "", "/docs");
  vi.spyOn(window, "confirm").mockImplementation(() => { throw new Error("Browser confirm is forbidden"); });
  vi.spyOn(window, "alert").mockImplementation(() => { throw new Error("Browser alert is forbidden"); });
  vi.spyOn(window, "prompt").mockImplementation(() => { throw new Error("Browser prompt is forbidden"); });
});
afterEach(() => { disposeRouter?.(); vi.restoreAllMocks(); });
function mount() {
  return render(() => {
    dialogs = createDialogs();
    return <><Button onClick={() => void dialogs.confirm(options)}>Leave page</Button><DialogHost dialogs={dialogs} /></>;
  });
}
function mountRouter() {
  mount();
  createRoot((dispose) => {
    disposeRouter = dispose;
    router = createRouter(() => dialogs.confirm(options));
  });
}
it("confirms only with the primary action and restores trigger focus on cancellation", async () => {
  mount();
  const trigger = screen.getByRole("button", { name: "Leave page" });
  trigger.focus();
  const result = dialogs.confirm(options);
  const modal = await screen.findByRole("dialog", { name: options.title });
  expect(modal).toHaveAccessibleDescription(options.message);
  await waitFor(() => expect(within(modal).getByRole("button", { name: "Keep editing" })).toHaveFocus());
  const primary = screen.getByRole("button", {name: "Discard changes"});
  primary.focus();
  fireEvent.keyDown(primary, {key: "Tab"});
  expect(screen.getByRole("button", {name: "Close dialog"})).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, {key: "Tab", shiftKey: true});
  expect(primary).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
  expect(await result).toBe(false);
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  const accepted = dialogs.confirm(options);
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(await accepted).toBe(true);
});
it("treats Escape and close as cancellation and queues requests without overlapping", async () => {
  mount();
  const first = dialogs.confirm(options);
  const second = dialogs.error("<script>untrusted message</script>", "Unable to save");
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
  expect(await first).toBe(false);
  const error = screen.getByRole("alertdialog", { name: "Unable to save" });
  expect(error).toHaveTextContent("<script>untrusted message</script>");
  expect(error.querySelector("script")).toBeNull();
  fireEvent.click(within(error).getByRole("button", { name: "Close dialog" }));
  await second;
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});
it("settles active and queued confirmations as cancelled on teardown", async () => {
  const view = mount();
  const first = dialogs.confirm(options), second = dialogs.confirm(options);
  view.unmount();
  expect(await first).toBe(false);
  expect(await second).toBe(false);
  expect(await dialogs.confirm(options)).toBe(false);
});
it("blocks duplicate navigation while asking and leaves the original URL on cancellation", async () => {
  mountRouter();
  await router.navigate("/app/customer/new");
  router.setGuard(() => true);
  const leave = router.navigate("/docs");
  expect(await router.navigate("/demo/projects")).toBe(false);
  expect(location.pathname).toBe("/app/customer/new");
  fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
  expect(await leave).toBe(false);
  expect(router.location()).toBe("/app/customer/new");
  const next = router.navigate("/docs");
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(await next).toBe(true);
  expect(router.location()).toBe("/docs");
});
it("preserves Back and Forward entries when cancelling, then follows the approved traversal", async () => {
  mountRouter();
  await router.navigate("/demo/layouts");
  await router.navigate("/app/customer/new");
  router.setGuard(() => true);
  history.back();
  await screen.findByRole("dialog");
  expect(location.pathname).toBe("/app/customer/new");
  fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  history.back();
  await screen.findByRole("dialog");
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  await waitFor(() => expect(router.location()).toBe("/demo/layouts"));
  router.setGuard();
  history.forward();
  await waitFor(() => expect(router.location()).toBe("/app/customer/new"));
  history.back();
  await waitFor(() => expect(router.location()).toBe("/demo/layouts"));
  history.back();
  await waitFor(() => expect(router.location()).toBe("/docs"));
});
it("does not let an old confirmation override a replacement navigation", async () => {
  mountRouter();
  router.setGuard(() => true);
  const stale = router.navigate("/demo/projects");
  await router.navigate("/login", true, true);
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(await stale).toBe(false);
  expect(router.location()).toBe("/login");
});
it("keeps tab unload protection only while the form is dirty", () => {
  mountRouter();
  const clean = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
  router.setGuard(() => true);
  const dirty = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(dirty);
  expect(dirty.defaultPrevented).toBe(true);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("replaces browser validation bubbles with one error dialog and returns to the invalid field", async () => {
  render(() => {
    dialogs = createDialogs();
    useValidationDialogs(dialogs);
    return <><form><label>Name<input required /></label><label>Email<input type="email" required /></label></form><DialogHost dialogs={dialogs} /></>;
  });
  expect(document.querySelector("form")!.checkValidity()).toBe(false);
  const error = await screen.findByRole("alertdialog", { name: "Check your entries" });
  expect(error).toHaveTextContent("Name:");
  expect(error).toHaveTextContent("Email:");
  fireEvent.click(within(error).getByRole("button", { name: "OK" }));
  await waitFor(() => expect(screen.getByLabelText("Name")).toHaveFocus());
});
it("names invalid custom selects and focuses their visible trigger", async () => {
  render(() => {
    dialogs = createDialogs();
    useValidationDialogs(dialogs);
    return <><form><SelectInput required aria-label="Status" options={[{value: "active", label: "Active"}]} /></form><DialogHost dialogs={dialogs} /></>;
  });
  expect(document.querySelector("form")!.checkValidity()).toBe(false);
  const error = await screen.findByRole("alertdialog");
  expect(error).toHaveTextContent("Status:");
  fireEvent.click(within(error).getByRole("button", { name: "OK" }));
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Status" })).toHaveFocus());
});

it("keeps one prompt when Back is pressed repeatedly while awaiting a decision", async () => {
  mountRouter();
  await router.navigate("/demo/layouts");
  await router.navigate("/app/customer/new");
  router.setGuard(() => true);
  history.back();
  await screen.findByRole("dialog");
  history.back();
  await new Promise<void>((resolve) => window.addEventListener("popstate", () => resolve(), {once: true}));
  fireEvent.click(screen.getByRole("button", {name: "Discard changes"}));
  await waitFor(() => expect(router.location()).toBe("/demo/layouts"));
  expect(location.pathname).toBe("/demo/layouts");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
