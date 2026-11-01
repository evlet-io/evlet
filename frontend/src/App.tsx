import { createStore } from "solid-js/store";
import {
  createEffect,
  createResource,
  createSignal,
  ErrorBoundary,
  For,
  onCleanup,
  onMount,
  Show,
  Switch,
  Match,
  Suspense,
  lazy,
} from "solid-js";
import { request, authRequest, setCsrf, getSchema } from "./api";
import { validateSchema } from "./schema";
import { subscribeEvents } from "./events";
import {
  AppShell,
  Sidebar,
  BrandArea,
  WorkspaceSwitcher,
  NavigationGroup,
  SidebarFooter,
  TopBar,
  Breadcrumbs,
  Avatar,
  Badge,
  Button,
  IconButton,
  Glyph,
  Modal,
  Toast,
  createDialogs,
  DialogHost,
  useValidationDialogs,
  type DialogController,
  type AppShellProps,
} from "./ui";
import { Icons, Icon } from "./demos/icons";
import { createRouter, matchRoute } from "./runtime/Router";
import {
  AppContext,
  messageFor,
  type ApplicationContext,
} from "./runtime/Context";
import { ViewRenderer, registerLazyComponent } from "./runtime/ViewRenderer";
import type { User, UIDocument, ModelSchema } from "./runtime/types";
import Docs from "./views/Docs";
import ModelList from "./views/ModelList";
import RecordForm from "./views/RecordForm";
import ReportView from './views/ReportView';
const Accounts = lazy(() => import("./views/Accounts"));
registerLazyComponent("ProjectsDemo", async () => {
  const { default: Projects } = await import("./demos/ProjectsDemo");
  await import("./demos/demo.css");
  const Host = (props: AppShellProps) => (
    <>
      {props.children}
      {props.statusBar}
      {props.overlays}
    </>
  );
  return { default: () => <Projects shell={Host} /> };
});
export function Logo() {
  return (
    <span class="evlet-logo" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none">
        <path
          d="M6 6h12M6 12h9M6 18h12"
          stroke="currentColor"
          stroke-width="3"
          stroke-linecap="round"
        />
      </svg>
    </span>
  );
}
export default function App() {
  const dialogs = createDialogs();
  useValidationDialogs(dialogs);
  const router = createRouter(() => dialogs.confirm({
    title: "Discard unsaved changes?",
    message: "Your changes have not been saved. Leave this page and discard them?",
    confirmLabel: "Discard changes",
    cancelLabel: "Keep editing",
  }));
  const [user, setUser] = createSignal<User | null>(null),
    [setup, setSetup] = createSignal(false),
    [connecting, setConnecting] = createSignal(true),
    [busy, setBusy] = createSignal(false),
    [error, setError] = createSignal(""),
    [tenant, setTenant] = createSignal<any>(null),
    [application, setApplication] = createSignal<any>({});
  const [username, setUsername] = createSignal(""),
    [password, setPassword] = createSignal("");
  let alive = true;
  const expired = () => {
    dialogs.dismissAll();
    setUser(null);
    setCsrf("");
    setError("Your session ended. Sign in again.");
    router.setGuard();
  };
  async function session() {
    setConnecting(true);
    setError("");
    try {
      const data = await request("/api/auth/session");
      if (!alive) return;
      setCsrf(data.csrf || "");
      setUser(data.user);
      setSetup(!!data.setup);
      setTenant(data.tenant);
      setApplication(data.application || {});
      if (data.user && ["/", "/login"].includes(location.pathname))
        router.navigate("/docs", true, true);
    } catch (e) {
      if (alive) {
        setError(messageFor(e));
        void dialogs.error(messageFor(e), "Unable to connect");
      }
    } finally {
      if (alive) setConnecting(false);
    }
  }
  async function login(e: SubmitEvent) {
    e.preventDefault();
    if (busy()) return;
    setBusy(true);
    setError("");
    try {
      if (setup()) {
        await authRequest("setup", {
          username: username(),
          password: password(),
        });
        setSetup(false);
      }
      const data = await authRequest("login", {
        username: username(),
        password: password(),
      });
      if (!alive) return;
      setCsrf(data.csrf);
      setUser(data.user);
      setPassword("");
      router.navigate("/docs", true, true);
    } catch (e) {
      if (alive) {
        setError(messageFor(e));
        void dialogs.error(messageFor(e), "Unable to sign in");
      }
    } finally {
      if (alive) setBusy(false);
    }
  }
  async function logout() {
    if (!(await router.navigate("/docs"))) return;
    setBusy(true);
    try {
      await authRequest("logout", {});
      setUser(null);
      setCsrf("");
      router.navigate("/login", true, true);
      setError("");
    } catch (e) {
      setError(messageFor(e));
      void dialogs.error(messageFor(e), "Unable to sign out");
    } finally {
      setBusy(false);
    }
  }
  onMount(() => {
    window.addEventListener("evlet-session-ended", expired);
    void session();
  });
  onCleanup(() => {
    alive = false;
    window.removeEventListener("evlet-session-ended", expired);
    setCsrf("");
  });
  return (
    <>
      <Icons />
      <DialogHost dialogs={dialogs} />
      <Show
        when={!connecting()}
        fallback={
          <div class="connection-page" role="status">
            <Logo />
            <p>Connecting to Evlet…</p>
          </div>
        }
      >
        <Show
          when={user()}
          keyed
          fallback={
            <div class="login-page">
              <a class="login-brand" href="/login">
                <Logo />
                <strong>evlet</strong>
                <span>{application().playground ? "Playground" : application().name || "Your workspace"}</span>
              </a>
              <div class="login-card">
                <Badge tone="info">{tenant()?.label || "Your workspace"}</Badge>
                <h1>{setup() ? `Set up ${application().playground ? "your Playground" : application().name || "your workspace"}` : "Welcome back"}</h1>
                <p>
                  {setup()
                    ? "Create the first administrator account."
                    : "Sign in to your Evlet workspace."}
                </p>
                <form onSubmit={login}>
                  <label for="username">Username</label>
                  <input
                    id="username"
                    value={username()}
                    onInput={(e) => setUsername(e.currentTarget.value)}
                    autocomplete="username"
                    required
                    minLength={3}
                    maxLength={64}
                    disabled={busy()}
                  />
                  <label for="password">Password</label>
                  <input
                    id="password"
                    type="password"
                    value={password()}
                    onInput={(e) => setPassword(e.currentTarget.value)}
                    autocomplete={setup() ? "new-password" : "current-password"}
                    required
                    minLength={setup() ? 15 : undefined}
                    maxLength={128}
                    disabled={busy()}
                  />
                  <Show when={setup()}>
                    <small>Use at least 15 characters.</small>
                  </Show>
                  <Show when={error()}>
                    <p class="error-banner" role="alert">
                      {error()}
                    </p>
                  </Show>
                  <Button variant="primary" type="submit" disabled={busy()}>
                    {busy()
                      ? "Signing in…"
                      : setup()
                        ? "Create administrator"
                        : "Sign in"}
                    <Glyph name="arrow" />
                  </Button>
                </form>
                <Show when={error()}>
                  <button class="ui-text-link" onClick={session}>
                    Check connection
                  </button>
                </Show>
                <footer>Simple tools. Your application.</footer>
              </div>
              <p class="login-footnote">
                Evlet · Python applications, thoughtfully put together.
              </p>
            </div>
          }
        >
          {(account) => (
            <ErrorBoundary
              fallback={(failure, reset) => (
                <div class="connection-page">
                  <h1>Unable to open the workspace</h1>
                  <p role="alert">{messageFor(failure)}</p>
                  <Button onClick={reset}>Try again</Button>
                  <Button onClick={logout}>Sign out</Button>
                </div>
              )}
            >
              <Workspace
                user={account}
                tenant={tenant()}
                application={application()}
                router={router}
                dialogs={dialogs}
                onLogout={logout}
                accountError={error()}
                busy={busy()}
              />
            </ErrorBoundary>
          )}
        </Show>
      </Show>
    </>
  );
}
function Workspace(props: {
  dialogs: DialogController;
  user: User;
  tenant: any;
  application: any;
  router: ReturnType<typeof createRouter>;
  onLogout: () => void;
  accountError: string;
  busy: boolean;
}) {
  const [applicationState] = createStore({user: props.user, tenant: props.tenant?.name || "local"});
  const [data, { refetch }] = createResource(async () => {
    const [schema, ui] = await Promise.all([getSchema(), request("/api/ui")]);
    const models = validateSchema(schema) as ModelSchema[];
    if (ui.version !== 1) throw new Error("Unsupported UI schema version.");
    return {
      models,
      reports: schema.reports || [],
      nativePanels: schema.native_panels || [],
      ui: ui as UIDocument,
      capabilities: schema.capabilities || {},
    };
  });
  const [navOpen, setNavOpen] = createSignal(false),
    [searchOpen, setSearchOpen] = createSignal(false),
    [search, setSearch] = createSignal(""),
    [notice, setNotice] = createSignal(""),
    [revision, setRevision] = createSignal(0);
  let noticeTimer: ReturnType<typeof setTimeout> | undefined;
  const notify = (message: string) => {
    setNotice(message);
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => setNotice(""), 6500);
  };
  const navigate = async (path: string) => {
    if (await props.router.navigate(path)) {
      setNavOpen(false);
      setSearchOpen(false);
    }
  };
  createEffect(() => {
    if (!data()?.capabilities.events) return;
    const close = subscribeEvents(() => setRevision((v) => v + 1));
    onCleanup(close);
  });
  const key = (e: KeyboardEvent) => {
    if (
      (e.metaKey || e.ctrlKey) &&
      e.key === "k" &&
      !location.pathname.startsWith("/demo/projects")
    ) {
      e.preventDefault();
      setSearchOpen((v) => !v);
    }
  };
  onMount(() => window.addEventListener("keydown", key));
  onCleanup(() => {
    window.removeEventListener("keydown", key);
    clearTimeout(noticeTimer);
    props.router.setGuard();
  });
  const route = (): ReturnType<typeof matchRoute> =>
    data()
      ? matchRoute(
          props.router.location().split("?")[0],
          data()!.ui,
          data()!.models,
        )
      : { kind: "docs", params: {} };
  const currentTitle = () =>
    report()?.label ||
    route().model?.plural ||
    route().view?.title ||
    (route().kind === "accounts" ? "Accounts" : "Docs");
  const pages = () => data()?.ui.views.filter((v) => v.route) || [];
  const report = () => data()?.reports.find((report: any) => props.router.location().split('?')[0] === '/reports/' + report.name);
  const links = () => [
    { value: "/docs", label: "Overview" },
    { value: "/docs/getting-started", label: "Getting started" },
    { value: "/docs/components", label: "Components" },
    { value: "/docs/layout", label: "Layout & views" },
    { value: "/docs/state", label: "State & actions" },
    { value: "/docs/extensions", label: "Customization" },
    { value: "/docs/reference", label: "API reference" },
    { value: "/docs/troubleshooting", label: "Troubleshooting" },
  ];
  const navValue = () =>
    props.router
      .location()
      .split("?")[0]
      .replace(/(\/app\/[^/]+).*/, "$1");
  const context = (): ApplicationContext => ({
    ui: data()!.ui,
    models: data()!.models,
    user: props.user,
    tenant: props.application?.id || props.tenant?.name || data()!.ui.name,
    router: props.router,
    dialogs: props.dialogs,
    notify,
    refresh: revision,
    capabilities: data()!.capabilities,
    nativePanels: data()!.nativePanels,
    applicationState,
  });
  return (
    <Show
      when={data()}
      fallback={
        <div class="connection-page">
          <Show
            when={data.error}
            fallback={<p role="status">Opening your workspace…</p>}
          >
            <p role="alert">{messageFor(data.error)}</p>
            <Button onClick={() => refetch()}>Retry</Button>
            <Button onClick={props.onLogout}>Sign out</Button>
          </Show>
        </div>
      }
    >
      <AppContext.Provider value={context()}>
        <AppShell
          sidebarOpen={navOpen()}
          onSidebarClose={() => setNavOpen(false)}
          sidebar={
            <Sidebar
              brand={
                <BrandArea
                  logo={<Logo />}
                  name={
                    <>
                      <strong>evlet</strong>
                      <span class="brand-label">{props.application?.playground ? "Playground" : props.tenant?.label || props.application?.name || data()!.ui.name}</span>
                    </>
                  }
                />
              }
              workspaceSwitcher={
                <WorkspaceSwitcher
                  name={props.tenant?.label || data()!.ui.name}
                  description={props.application?.playground ? "Local playground" : "Your workspace"}
                  icon={<span class="workspace-letter">E</span>}
                  onClick={() => navigate("/docs")}
                />
              }
              navigation={
                <>
                  <NavigationGroup
                    title="Documentation"
                    value={navValue()}
                    onChange={navigate}
                    items={links().map((l, i) => ({
                      ...l,
                      icon: (
                        <Icon
                          name={i === 0 ? "grid" : i === 2 ? "layers" : "book"}
                        />
                      ),
                    }))}
                  />
                  <For each={[...new Set(pages().map((v) => v.section))]}>
                    {(section) => (
                      <NavigationGroup
                        title={section}
                        value={navValue()}
                        onChange={navigate}
                        items={pages()
                          .filter((v) => v.section === section)
                          .map((v) => ({
                            value: v.route!,
                            label: v.title,
                            icon: (
                              <Icon
                                name={
                                  v.layout.type === "ProjectsDemo"
                                    ? "grid"
                                    : "layers"
                                }
                              />
                            ),
                          }))}
                      />
                    )}
                  </For>
                  <NavigationGroup
                    title="Models"
                    value={navValue()}
                    onChange={navigate}
                    items={data()!
                      .models.filter((m) => !m.child_only)
                      .map((m) => ({
                        value: "/app/" + m.name,
                        label: m.plural,
                        icon: <Icon name="layers" />,
                      }))}
                  />
                  <Show when={props.user.role === "admin"}>
                    <NavigationGroup
                      title="Workspace"
                      value={navValue()}
                      onChange={navigate}
                      items={[
                        {
                          value: "/settings/accounts",
                          label: "Accounts",
                          icon: <Icon name="people" />,
                        },
                      ]}
                    />
                  </Show>
                  <Show when={data()!.reports.length}>
                    <NavigationGroup title="Reports" value={navValue()} onChange={navigate}
                      items={data()!.reports.map((report: any) => ({value: '/reports/' + report.name, label: report.label}))} />
                  </Show>
                </>
              }
              footer={
                <SidebarFooter>
                  <Avatar
                    initials={props.user.username.slice(0, 2).toUpperCase()}
                    label={props.user.username}
                  />
                  <span class="user-info">
                    <strong>{props.user.username}</strong>
                    <small>{props.user.role}</small>
                  </span>
                  <IconButton
                    title="Sign out"
                    aria-label="Sign out"
                    disabled={props.busy}
                    onClick={props.onLogout}
                  >
                    <Icon name="arrow" />
                  </IconButton>
                </SidebarFooter>
              }
            />
          }
          topBar={
            <TopBar
              leading={
                <>
                  <IconButton
                    class="mobile-navigation"
                    aria-label="Open navigation"
                    onClick={() => setNavOpen(true)}
                  >
                    <Glyph name="menu" />
                  </IconButton>
                  <Breadcrumbs
                    items={[
                      { label: "Workspace", onSelect: () => navigate("/docs") },
                      { label: currentTitle() },
                    ]}
                  />
                </>
              }
              trailing={
                <>
                  <button
                    class="global-search" aria-label="Find a page"
                    onClick={() => setSearchOpen(true)}
                  >
                    <Glyph name="search" />
                    <span>Find a page…</span>
                    <kbd>⌘ K</kbd>
                  </button>
                  <Badge tone="success">Local</Badge>
                </>
              }
            />
          }
          overlays={
            <>
              <Toast message={notice()} onDismiss={() => setNotice("")} />
              <Modal
                title="Go to a page"
                open={searchOpen()}
                onClose={() => setSearchOpen(false)}
              >
                <input
                  class="nav-search"
                  aria-label="Find a page"
                  placeholder="Search documentation, demos, models…"
                  value={search()}
                  onInput={(e) => setSearch(e.currentTarget.value)}
                />
                <div class="nav-results">
                  <For
                    each={[
                      ...links(),
                      ...pages().map((v) => ({
                        value: v.route!,
                        label: v.title,
                      })),
                      ...data()!
                        .models.filter((m) => !m.child_only)
                        .map((m) => ({
                          value: "/app/" + m.name,
                          label: m.plural,
                        })),
                    ].filter((item) =>
                      item.label.toLowerCase().includes(search().toLowerCase()),
                    )}
                  >
                    {(link) => (
                      <button onClick={() => navigate(link.value)}>
                        {link.label}
                        <Glyph name="arrow" />
                      </button>
                    )}
                  </For>
                </div>
              </Modal>
            </>
          }
        >
          <Show when={props.accountError}>
            <p class="error-banner" role="alert">
              {props.accountError}
            </p>
          </Show>
          <Suspense fallback={<p role="status">Loading page…</p>}>
            <Show when={props.router.location().split("?")[0]} keyed>
              {(path) => (
                <Switch>
                  <Match when={report()}><ReportView report={report()} /></Match>
                  <Match when={route().kind === "docs"}>
                    <Docs path={path} />
                  </Match>
                  <Match when={route().kind === "page"}>
                    <ViewRenderer
                      view={route().view!}
                      params={route().params}
                    />
                  </Match>
                  <Match when={route().kind === "list"}>
                    <ModelList model={route().model!} />
                  </Match>
                  <Match
                    when={route().kind === "form" || route().kind === "detail"}
                  >
                    <RecordForm
                      model={route().model!}
                      recordId={route().id!}
                      readOnly={route().kind === "detail"}
                    />
                  </Match>
                  <Match
                    when={
                      route().kind === "accounts" && props.user.role === "admin"
                    }
                  >
                    <Accounts />
                  </Match>
                  <Match when={true}>
                    <div class="page-scroll">
                      <h1>Page not found</h1>
                      <p>This page is unavailable or you do not have access.</p>
                      <Button onClick={() => navigate("/docs")}>
                        Open Docs
                      </Button>
                    </div>
                  </Match>
                </Switch>
              )}
            </Show>
          </Suspense>
        </AppShell>
      </AppContext.Provider>
    </Show>
  );
}
