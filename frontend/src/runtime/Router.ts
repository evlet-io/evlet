import { createSignal, onCleanup } from "solid-js";
import type { UIDocument, ModelSchema } from "./types";
export function matchRoute(
  path: string,
  ui: UIDocument,
  models: ModelSchema[],
) {
  for (const view of ui.views.filter((view) => view.route)) {
    const keys: string[] = [];
    const pattern = view
      .route!.split("/")
      .map((part) => {
        const parameter = /^\{([A-Za-z][A-Za-z0-9_]*)(:int)?\}$/.exec(part);
        if (parameter) {
          keys.push(parameter[1]);
          return parameter[2] ? "([1-9][0-9]{0,15})" : "([A-Za-z0-9_-]+)";
        }
        return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      })
      .join("/");
    const match = new RegExp("^" + pattern + "$").exec(path);
    if (match)
      return {
        kind: "page",
        view,
        params: Object.fromEntries(keys.map((key, i) => [key, match[i + 1]])),
      };
  }
  const match =
    /^\/app\/([a-z][a-z0-9_]*)(?:\/(new|[1-9][0-9]{0,15})(\/view)?)?$/.exec(
      path,
    );
  if (match) {
    const model = models.find(
      (item) => item.name === match[1] && !item.child_only,
    );
    if (
      model &&
      (!match[2] ||
        match[2] === "new" ||
        Number.isSafeInteger(Number(match[2])))
    )
      return {
        kind: match[2] ? (match[3] ? "detail" : "form") : "list",
        model,
        id: match[2],
        params: {},
      };
  }
  return {
    kind:
      path === "/docs" || path.startsWith("/docs/")
        ? "docs"
        : path === "/settings/accounts"
          ? "accounts"
          : "missing",
    params: {},
  };
}
export function createRouter() {
  const [location, setLocation] = createSignal(
    window.location.pathname + window.location.search,
  );
  let guard: (() => boolean) | undefined;
  let previous = location();
  const allowed = () =>
    !guard || !guard() || window.confirm("Discard your unsaved changes?");
  const update = () => {
    if (!allowed()) {
      history.pushState({}, "", previous);
      return;
    }
    previous = window.location.pathname + window.location.search;
    setLocation(previous);
  };
  const before = (event: BeforeUnloadEvent) => {
    if (guard?.()) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  window.addEventListener("popstate", update);
  window.addEventListener("beforeunload", before);
  onCleanup(() => {
    window.removeEventListener("popstate", update);
    window.removeEventListener("beforeunload", before);
  });
  return {
    location,
    setGuard(value?: () => boolean) {
      guard = value;
    },
    navigate(path: string, replace = false, bypass = false) {
      if (
        !path.startsWith("/") ||
        path.startsWith("//") ||
        path.includes("\\") ||
        /[\r\n\0]/.test(path)
      )
        throw new Error("Use an application-relative URL");
      if (!bypass && !allowed()) return false;
      history[replace ? "replaceState" : "pushState"]({}, "", path);
      previous = path;
      setLocation(path);
      return true;
    },
  };
}
