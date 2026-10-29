import { SelectInput } from "../ui";
import { createResource, createSignal, For, Show } from "solid-js";
import { PageHeader, Button, Field, Badge, Modal } from "../ui";
import { request, authRequest } from "../api";
import { useApp, messageFor } from "../runtime/Context";
export default function Accounts() {
  const app = useApp();
  const [offset, setOffset] = createSignal(0),
    [busy, setBusy] = createSignal(false),
    [error, setError] = createSignal(""),
    [open, setOpen] = createSignal(false),
    [username, setUsername] = createSignal(""),
    [password, setPassword] = createSignal(""),
    [role, setRole] = createSignal("viewer");
  const [page, { refetch }] = createResource(offset, (offset) =>
    request("/api/auth/users?offset=" + offset),
  );
  async function create(e: SubmitEvent) {
    e.preventDefault();
    if (busy()) return;
    setBusy(true);
    setError("");
    try {
      await authRequest("users", {
        username: username(),
        password: password(),
        role: role(),
      });
      setPassword("");
      setUsername("");
      setOpen(false);
      app.notify("Account created.");
      await refetch();
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }
  async function update(id: number, values: Record<string, any>) {
    if (busy()) return;
    setBusy(true);
    setError("");
    try {
      await authRequest("users/" + id, values, "PATCH");
      await refetch();
      app.notify("Account updated.");
    } catch (e) {
      setError(messageFor(e));
      void refetch();
    } finally {
      setBusy(false);
    }
  }
  return (
    <div class="accounts-view">
      <PageHeader
        title="Accounts"
        subtitle="Manage access to this workspace."
        actions={[
          { id: "new", label: "Add account", onSelect: () => setOpen(true) },
        ]}
      />
      <div class="page-scroll">
        <p class="muted">
          Account changes end that user’s active sessions. Keep one active
          administrator.
        </p>
        <Show when={error() || page.error}>
          <p role="alert" class="error-banner">
            {error() || messageFor(page.error)}
          </p>
        </Show>
        <Show when={page.loading}>
          <p role="status">Loading accounts…</p>
        </Show>
        <Show when={!page.error && page()}>
          {(data) => (
            <>
              <div class="ui-data-table">
                <table>
                  <thead>
                    <tr>
                      <th>Username</th>
                      <th>Role</th>
                      <th>Status</th>
                      <th>Access</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For each={data().items}>
                      {(user) => (
                        <tr>
                          <td>
                            <strong>{user.username}</strong>{" "}
                            {user.id === app.user.id ? (
                              <Badge>You</Badge>
                            ) : null}
                          </td>
                          <td>
                            <SelectInput
                              aria-label={"Role for " + user.username}
                              value={user.role}
                              disabled={busy()}
                              onChange={(nextValue) =>
                                update(user.id, { role: nextValue })
                              }
                            >
                              <option>admin</option>
                              <option>editor</option>
                              <option>viewer</option>
                            </SelectInput>
                          </td>
                          <td>
                            <Badge tone={user.active ? "success" : "neutral"}>
                              {user.active ? "Active" : "Disabled"}
                            </Badge>
                          </td>
                          <td>
                            <Button
                              disabled={busy()}
                              onClick={() =>
                                update(user.id, { active: !user.active })
                              }
                            >
                              {user.active ? "Disable" : "Enable"}
                            </Button>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </div>
              <div class="pagination">
                <span>{data().total} accounts</span>
                <Button
                  disabled={busy() || offset() === 0}
                  onClick={() => setOffset((v) => Math.max(0, v - 100))}
                >
                  Previous accounts
                </Button>
                <Button
                  disabled={busy() || offset() + 100 >= data().total}
                  onClick={() => setOffset((v) => v + 100)}
                >
                  Next accounts
                </Button>
              </div>
            </>
          )}
        </Show>
      </div>
      <Modal
        title="Add account"
        open={open()}
        onClose={() => {
          if (!busy()) setOpen(false);
        }}
      >
        <form class="account-form" onSubmit={create}>
          <Field label="Username" required>
            {(c) => (
              <input
                id={c.id}
                autocomplete="off"
                required
                minLength={3}
                maxLength={64}
                value={username()}
                onInput={(e) => setUsername(e.currentTarget.value)}
              />
            )}
          </Field>
          <Field
            label="Initial password"
            required
            help="Use at least 15 characters."
          >
            {(c) => (
              <input
                id={c.id}
                type="password"
                autocomplete="new-password"
                required
                minLength={15}
                maxLength={128}
                value={password()}
                onInput={(e) => setPassword(e.currentTarget.value)}
              />
            )}
          </Field>
          <Field label="Role">
            {(c) => (
              <SelectInput
                id={c.id}
                value={role()}
                onChange={(nextValue) => setRole(nextValue)}
              >
                <option>viewer</option>
                <option>editor</option>
                <option>admin</option>
              </SelectInput>
            )}
          </Field>
          <Show when={error()}>
            <p class="error-banner" role="alert">
              {error()}
            </p>
          </Show>
          <Button variant="primary" disabled={busy()} type="submit">
            Create account
          </Button>
        </form>
      </Modal>
    </div>
  );
}
