import { createRoot, createSignal } from "solid-js";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@solidjs/testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { createDialogs, DialogHost } from "../ui";
import FieldInput from "../views/FieldInput";
import ModelList from "../views/ModelList";
import RecordForm, { buildRecordPayload } from "../views/RecordForm";
import { ViewRenderer } from "../runtime/ViewRenderer";
import { AppContext } from "../runtime/Context";
import { createState, evaluate, componentState } from "../runtime/StateStore";
import { createRouter, matchRoute } from "../runtime/Router";
import fixtures from "./ui-fixture.json";
import type { UIDocument, ModelSchema } from "../runtime/types";
const api = vi.hoisted(() => ({
  request: vi.fn(),
  authRequest: vi.fn(),
  getSchema: vi.fn(),
  setCsrf: vi.fn(),
  listRecords: vi.fn(),
  getRecord: vi.fn(),
  recordRequest: vi.fn(),
}));
vi.mock("../api", () => api);
vi.mock("../events", () => ({ subscribeEvents: () => () => {} }));
const ui = fixtures.ui as unknown as UIDocument,
  models = fixtures.models as ModelSchema[];
const user = { id: 1, username: "owner", role: "admin" };
const customer = models.find((m) => m.name === "customer")!;
function context(
  path: string,
  body: () => any,
  refresh: () => number = () => 0,
) {
  history.replaceState({}, "", path);
  return render(() => {
    const dialogs = createDialogs();
    const router = createRouter(() => dialogs.confirm({title: "Discard unsaved changes?", message: "Your changes have not been saved.", confirmLabel: "Discard changes"}));
    return (
      <AppContext.Provider
        value={{
          ui,
          models,
          user,
          tenant: "local",
          router,
          dialogs,
          notify: vi.fn(),
          refresh,
          capabilities: {},
        }}
      >
        <DialogHost dialogs={dialogs} />
        {body()}
      </AppContext.Provider>
    );
  });
}
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  history.replaceState({}, "", "/");
  api.listRecords.mockResolvedValue({ items: [], total: 0 });
  api.getSchema.mockResolvedValue({
    version: 1,
    ui_version: 1,
    models,
    capabilities: {},
  });
});
it("signs in and opens Docs, then exposes Demo Pages and real model navigation", async () => {
  api.request.mockImplementation(async (path: string) =>
    path === "/api/auth/session"
      ? { user: null, setup: false }
      : path === "/api/ui"
        ? ui
        : {},
  );
  api.authRequest.mockResolvedValue({ user, csrf: "test-csrf" });
  render(() => <App />);
  await screen.findByRole("heading", { name: "Welcome back" });
  fireEvent.input(screen.getByLabelText("Username"), {
    target: { value: "owner" },
  });
  fireEvent.input(screen.getByLabelText("Password"), {
    target: { value: "test-only-password" },
  });
  fireEvent.submit(screen.getByLabelText("Username").closest("form")!);
  await screen.findByRole("heading", { name: "Build with Evlet" });
  expect(location.pathname).toBe("/docs");
  expect(api.setCsrf).toHaveBeenCalledWith("test-csrf");
  expect(screen.getAllByText("Demo Pages").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Layouts & state" }));
  await screen.findByRole("heading", { name: "Layouts & state" });
  expect(location.pathname).toBe("/demo/layouts");
  window.dispatchEvent(new Event("evlet-session-ended"));
  await screen.findByRole("heading", { name: "Welcome back" });
  expect(api.setCsrf).toHaveBeenLastCalledWith("");
});
it("renders Python state, conditional fields, reset and dialog operations", async () => {
  const view = ui.views.find((v) => v.name === "layouts_demo")!;
  context("/demo/layouts", () => <ViewRenderer view={view} />);
  expect(screen.queryByLabelText(/Company name/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("combobox", { name: "Customer type" }));
  fireEvent.click(screen.getByRole("option", { name: "Company" }));
  fireEvent.input(screen.getByLabelText("Name"), {
    target: { value: "Aster" },
  });
  expect(screen.getByLabelText(/Company name/)).toBeRequired();
  expect(screen.getByRole("heading", { name: "Aster" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Preview details" }));
  expect(screen.getByRole("dialog")).toHaveAttribute("open");
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }),
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue(""));
  expect(screen.queryByLabelText(/Company name/)).not.toBeInTheDocument();
});
it("queries models on the server and preserves URL search, ordering and pagination", async () => {
  api.listRecords.mockResolvedValue({
    items: [{ id: 1, name: "Aster", email: "a@example.test", active: true }],
    total: 30,
  });
  context("/app/customer", () => <ModelList model={customer} />);
  await screen.findByText("Aster");
  fireEvent.input(screen.getByRole("searchbox", { name: "Search Customers" }), {
    target: { value: "Aster" },
  });
  fireEvent.submit(
    screen
      .getByRole("searchbox", { name: "Search Customers" })
      .closest("form")!,
  );
  await waitFor(() =>
    expect(api.listRecords).toHaveBeenLastCalledWith(
      "customer",
      25,
      0,
      {},
      "-id",
      "Aster",
    ),
  );
  expect(location.search).toContain("q=Aster");
  fireEvent.click(screen.getByRole("button", { name: "Sort by Name" }));
  await waitFor(() =>
    expect(api.listRecords).toHaveBeenLastCalledWith(
      "customer",
      25,
      0,
      {},
      "name",
      "Aster",
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(api.listRecords).toHaveBeenLastCalledWith(
      "customer",
      25,
      25,
      {},
      "name",
      "Aster",
    ),
  );
});
it("creates with server defaults omitted and keeps exact decimal strings", () => {
  const invoice = models.find((m) => m.name === "invoice")!;
  const result = buildRecordPayload(
    invoice,
    models,
    {
      customer: 1,
      reference: "INV-1",
      total: "9007199254740993.12",
      tax_rate: null,
      status: "draft",
    },
    new Set(["total"]),
    false,
    {},
    new Set(),
  );
  expect(result.data.total).toBe("9007199254740993.12");
  expect(result.data).not.toHaveProperty("status");
  expect(result.data).not.toHaveProperty("tax_rate");
  expect(result.errors).toEqual({});
});
it("preserves existing child rows when update is forbidden but deletion is allowed", () => {
  const project = structuredClone(models.find((m) => m.name === "project")!);
  const restricted = models.map((m) =>
    m.name === "projecttask"
      ? { ...m, permissions: { create: false, update: false, delete: true } }
      : m,
  );
  const result = buildRecordPayload(
    project,
    restricted,
    {},
    new Set(),
    true,
    { tasks: [{ id: 4, title: "Kept", done: true }] },
    new Set(["tasks"]),
  );
  expect(result.data.children).toEqual({ tasks: [{ id: 4 }] });
});
it("loads a generated form, sends the revision, and preserves the draft on conflict", async () => {
  api.request.mockImplementation(async (path: string, options: any) => {
    if (options?.method === "PATCH")
      throw Object.assign(new Error("This record changed."), {
        status: 409,
        fields: {},
      });
    return {
      data: { id: 1, name: "Original", email: null, active: true, notes: null },
      etag: '"revision-one"',
    };
  });
  context("/app/customer/1", () => (
    <RecordForm model={customer} recordId="1" />
  ));
  await screen.findByDisplayValue("Original");
  fireEvent.input(screen.getByLabelText(/Name/), {
    target: { value: "My draft" },
  });
  fireEvent.submit(screen.getByLabelText(/Name/).closest("form")!);
  const failure = await screen.findByRole("alertdialog", {name: "Unable to save"});
  expect(failure).toHaveTextContent("This record changed.");
  fireEvent.click(within(failure).getByRole("button", {name: "OK"}));
  expect(screen.getByLabelText(/Name/)).toHaveValue("My draft");
  const call = api.request.mock.calls.find(
    ([, options]) => options?.method === "PATCH",
  )!;
  expect(call[1].headers["If-Match"]).toBe('"revision-one"');
  expect(JSON.parse(call[1].body)).toEqual({ name: "My draft" });
  const requests = api.request.mock.calls.length;
  fireEvent.click(screen.getByRole("button", {name: "Reload record"}));
  const reload = await screen.findByRole("dialog", {name: "Reload this record?"});
  fireEvent.click(within(reload).getByRole("button", {name: "Keep editing"}));
  expect(screen.getByLabelText(/Name/)).toHaveValue("My draft");
  expect(api.request.mock.calls).toHaveLength(requests);
  fireEvent.click(screen.getByRole("button", {name: "Reload record"}));
  fireEvent.click(screen.getByRole("button", {name: "Discard and reload"}));
  await screen.findByDisplayValue("Original");
  expect(api.request.mock.calls).toHaveLength(requests + 1);
});
it("renders viewer fields read-only and hides mutations", async () => {
  api.request.mockResolvedValue({
    data: { id: 1, name: "Read only", active: true },
    etag: '"one"',
  });
  context("/app/customer/1", () => (
    <RecordForm
      model={{
        ...customer,
        permissions: { create: false, update: false, delete: false },
      }}
      recordId="1"
    />
  ));
  await screen.findByDisplayValue("Read only");
  expect(screen.getByLabelText("Name")).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "Save changes" }),
  ).not.toBeInTheDocument();
});
it("supports generated routes, custom parameters and rejects unsafe record IDs", () => {
  expect(matchRoute("/app/customer/new", ui, models).kind).toBe("form");
  expect(matchRoute("/app/customer/10/view", ui, models).kind).toBe("detail");
  expect(matchRoute("/app/customer/9999999999999999", ui, models).kind).toBe(
    "missing",
  );
  const extended = {
    ...ui,
    views: [{ ...ui.views[0], route: "/schedule/{id:int}" }],
  };
  expect(matchRoute("/schedule/12", extended, models).params).toEqual({
    id: "12",
  });
  expect(matchRoute("/schedule/no", extended, models).kind).toBe("missing");
});
it("rejects unknown expressions and cycles and recomputes dependent state", () => {
  createRoot((dispose) => {
    const state = createState(
      { name: "Aster" },
      {
        "view.ready": {
          $expr: "ne",
          args: [{ $expr: "ref", args: ["view.name"] }, ""],
        },
      },
    );
    expect(state.read("view.ready")).toBe(true);
    state.write("view.name", "");
    expect(state.read("view.ready")).toBe(false);
    expect(() => state.write("view.ready", true)).toThrow("computed");
    expect(() =>
      evaluate({ $expr: "eval", args: ["source"] }, () => null),
    ).toThrow("Unsupported");
    dispose();
  });
  expect(() =>
    createRoot((dispose) => {
      try {
        createState({}, { "view.a": { $expr: "ref", args: ["view.a"] } });
      } finally {
        dispose();
      }
    }),
  ).toThrow("Circular");
});
it("isolates component state and shares application state between views", () => {
  createRoot((dispose) => {
    const shared = { theme: "light" };
    const first = createState({}, {}, shared),
      second = createState({}, {}, shared);
    first.write("application.theme", "blue");
    expect(second.read("application.theme")).toBe("blue");
    const a = componentState(first),
      b = componentState(first);
    a.write("component.value", "A");
    b.write("component.value", "B");
    expect(a.read("component.value")).toBe("A");
    expect(b.read("component.value")).toBe("B");
    expect(() => first.write("application.user", { role: "admin" })).toThrow();
    dispose();
  });
});
it("validates an uploaded file and shows attachment failures without losing the form", async () => {
  const project = models.find((m) => m.name === "project")!;
  api.request.mockImplementation(async (path: string) =>
    path.endsWith("/attachments")
      ? { items: [{ id: "file-1", name: "brief.txt", size: 10 }] }
      : {
          data: {
            id: 1,
            name: "Project",
            status: "planned",
            budget: null,
            progress: 0,
            children: { tasks: [] },
          },
          etag: '"one"',
        },
  );
  context("/app/project/1", () => <RecordForm model={project} recordId="1" />);
  await screen.findByRole("link", { name: "brief.txt" });
  fireEvent.change(screen.getByLabelText("Attach file"), {
    target: { files: [new File([], "empty.txt")] },
  });
  const failure = await screen.findByRole("alertdialog", {name: "Unable to attach file"});
  expect(failure).toHaveTextContent("Use a nonempty file up to 1 MiB.");
  fireEvent.click(within(failure).getByRole("button", {name: "OK"}));
  expect(api.recordRequest).not.toHaveBeenCalled();
  expect(screen.getByDisplayValue("Project")).toBeInTheDocument();
});
it("dispatches registered record actions and reloads the server result", async () => {
  const project = models.find((m) => m.name === "project")!;
  api.request.mockImplementation(async (path: string, options: any) => {
    if (path.endsWith("/attachments")) return { items: [] };
    if (options?.method === "POST")
      return { id: 1, name: "Project", status: "active" };
    return {
      data: {
        id: 1,
        name: "Project",
        status: "planned",
        progress: 0,
        children: { tasks: [] },
      },
      etag: '"one"',
    };
  });
  context("/app/project/1", () => <RecordForm model={project} recordId="1" />);
  await screen.findByDisplayValue("Project");
  fireEvent.click(screen.getByRole("button", { name: "Actions" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Start project" }));
  await waitFor(() =>
    expect(api.request).toHaveBeenCalledWith(
      "/api/project/1/actions/start",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "If-Match": '"one"' }),
      }),
    ),
  );
});

afterEach(() => vi.useRealTimers());

it("keeps the newer background job when an older poll completes late", async () => {
  const project = models.find((m) => m.name === "project")!;
  let actionCount = 0,
    completeOld!: (value: any) => void;
  api.request.mockImplementation(async (path: string, options: any) => {
    if (path.endsWith("/attachments")) return { items: [] };
    if (options?.method === "POST")
      return { job: { id: ++actionCount, state: "queued" } };
    if (path === "/api/_jobs/1")
      return new Promise((resolve) => {
        completeOld = resolve;
      });
    if (path === "/api/_jobs/2") return { id: 2, state: "running" };
    return {
      data: {
        id: 1,
        name: "Project",
        status: "active",
        progress: 0,
        children: { tasks: [] },
      },
      etag: '"one"',
    };
  });
  context("/app/project/1", () => <RecordForm model={project} recordId="1" />);
  await screen.findByDisplayValue("Project");
  vi.useFakeTimers();
  const run = async () => {
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Recalculate progress" }),
    );
    await vi.waitFor(() =>
      expect(screen.getByText("Background task: queued")).toBeInTheDocument(),
    );
  };
  await run();
  await vi.advanceTimersByTimeAsync(500);
  expect(completeOld).toBeTypeOf("function");
  await run();
  completeOld({ id: 1, state: "succeeded" });
  await vi.advanceTimersByTimeAsync(0);
  expect(screen.getByText("Background task: queued")).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(500);
  expect(screen.getByText("Background task: running")).toBeInTheDocument();
});

it("keeps unsaved input when live changes arrive", async () => {
  const [refresh, setRefresh] = createSignal(0);
  api.request.mockResolvedValue({
    data: { id: 1, name: "Original", active: true },
    etag: '"one"',
  });
  context(
    "/app/customer/1",
    () => <RecordForm model={customer} recordId="1" />,
    refresh,
  );
  await screen.findByDisplayValue("Original");
  fireEvent.input(screen.getByLabelText(/Name/), {
    target: { value: "Unsaved work" },
  });
  const calls = api.request.mock.calls.length;
  setRefresh(1);
  expect(screen.getByDisplayValue("Unsaved work")).toBeInTheDocument();
  expect(screen.getByText(/The server has newer changes/)).toBeInTheDocument();
  expect(api.request).toHaveBeenCalledTimes(calls);
});

it("shows saved select and link values after asynchronous choices arrive", async () => {
  const project = models.find((m) => m.name === "project")!;
  api.listRecords.mockResolvedValue({
    items: [{ id: 1, name: "Existing customer" }],
    total: 1,
  });
  api.getRecord.mockResolvedValue({ id: 1, name: "Existing customer" });
  context("/app/project/1", () => (
    <>
      <FieldInput
        id="saved-status"
        field={project.fields.find((f) => f.name === "status")!}
        value="planned"
        onChange={() => {}}
      />
      <FieldInput
        id="saved-customer"
        field={project.fields.find((f) => f.name === "customer")!}
        value={1}
        onChange={() => {}}
      />
    </>
  ));
  await screen.findByText("Existing customer");
  const selects = screen.getAllByRole("combobox");
  expect(selects[0]).toHaveTextContent("planned");
  expect(selects[1]).toHaveTextContent("Existing customer");
  fireEvent.click(selects[1]);
  expect(
    screen.getByRole("option", { name: "Existing customer" }),
  ).toHaveAttribute("aria-selected", "true");
});

it("retains an unavailable linked record and shows a recoverable error", async () => {
  const field = models
    .find((m) => m.name === "project")!
    .fields.find((f) => f.name === "customer")!;
  api.listRecords.mockRejectedValue(
    new Error("Choices temporarily unavailable"),
  );
  api.getRecord.mockRejectedValue(new Error("Linked record unavailable"));
  context("/app/project/1", () => (
    <FieldInput
      id="restricted-customer"
      field={field}
      value={7}
      onChange={() => {}}
    />
  ));
  await screen.findByText(/Choices temporarily unavailable/);
  expect(screen.getByRole("combobox")).toHaveTextContent("#7");
  fireEvent.click(screen.getByRole("combobox"));
  expect(screen.getByRole("option", { name: "#7" })).toBeInTheDocument();
});

it("searches linked records on the server inside the dropdown", async () => {
  const field = models
    .find((m) => m.name === "project")!
    .fields.find((f) => f.name === "customer")!;
  api.listRecords.mockImplementation(
    async (_model, _limit, _offset, _filters, _order, q) => ({
      items:
        q === "beta"
          ? [{ id: 2, name: "Beta customer" }]
          : [{ id: 1, name: "Alpha customer" }],
      total: 1,
    }),
  );
  const changed = vi.fn();
  context("/app/project/new", () => (
    <FieldInput
      id="customer-search"
      field={field}
      value={null}
      onChange={changed}
    />
  ));
  fireEvent.click(screen.getByRole("combobox"));
  await screen.findByRole("option", { name: "Alpha customer" });
  fireEvent.input(screen.getByRole("combobox", { name: "Search Customer" }), {
    target: { value: "beta" },
  });
  await screen.findByRole("option", { name: "Beta customer" });
  expect(api.listRecords).toHaveBeenLastCalledWith(
    "customer",
    20,
    0,
    {},
    "-id",
    "beta",
  );
  expect(
    screen.queryByRole("option", { name: "Alpha customer" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("option", { name: "Beta customer" }));
  expect(changed).toHaveBeenCalledWith(2);
});

it("does not sign out while unsaved navigation is awaiting a decision", async () => {
  history.replaceState({}, "", "/app/customer/new");
  api.request.mockImplementation(async (path: string) => path === "/api/auth/session" ? { user, csrf: "test-csrf" } : path === "/api/ui" ? ui : {});
  render(() => <App />);
  await screen.findByRole("heading", {name: "New customer"});
  fireEvent.input(screen.getByLabelText(/Name/), {target: {value: "Keep this draft"}});
  fireEvent.click(screen.getByRole("button", {name: "Sign out"}));
  await screen.findByRole("dialog", {name: "Discard unsaved changes?"});
  expect(api.authRequest).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", {name: "Keep editing"}));
  expect(screen.getByLabelText(/Name/)).toHaveValue("Keep this draft");
  expect(api.authRequest).not.toHaveBeenCalled();
});
