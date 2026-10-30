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
export function createRouter(confirmDiscard: () => Promise<boolean>) {
  const path = () => window.location.pathname + window.location.search;
  const [location, setLocation] = createSignal(path());
  const position = () => Number.isInteger(history.state?.evletIndex) ? history.state.evletIndex as number : undefined;
  let index = position() ?? 0;
  history.replaceState({ ...history.state, evletIndex: index }, "");
  let guard: (() => boolean) | undefined;
  let pending = false;
  let generation = 0;
  let disposed = false;
  let restore: (() => void) | undefined;
  let restored: Promise<void> | undefined;
  let replay = false;
  async function allowed() {
    if (!guard?.()) return true;
    const currentGuard = guard;
    const currentGeneration = generation;
    const answer = await confirmDiscard();
    return answer && !disposed && generation === currentGeneration && guard === currentGuard;
  }
  function restoreCurrent(targetIndex: number) {
    if (!restored) {
      restored = new Promise<void>((resolve) => {
        restore = resolve;
        history.go(index - targetIndex);
      });
    }
    return restored;
  }
  function commit() {
    index = position() ?? index;
    history.replaceState({ ...history.state, evletIndex: index }, "");
    setLocation(path());
  }
  const update = async () => {
    const targetIndex = position();
    if (restore) {
      if (targetIndex !== index && targetIndex !== undefined) {
        history.go(index - targetIndex);
        return;
      }
      const done = restore;
      restore = undefined;
      restored = undefined;
      done();
      return;
    }
    if (replay) {
      replay = false;
      pending = false;
      commit();
      return;
    }
    if (pending) {
      if (targetIndex !== undefined && targetIndex !== index) {
        void restoreCurrent(targetIndex);
      }
      return;
    }
    if (!guard?.()) { commit(); return; }
    pending = true;
    const target = path();
    if (targetIndex !== undefined && targetIndex !== index) {
      // Restore the current entry before asking, then replay the same traversal.
      await restoreCurrent(targetIndex);
      if (disposed) return;
      const accepted = await allowed();
      if (restored) await restored;
      if (accepted && !disposed) {
        replay = true;
        history.go(targetIndex - index);
      } else pending = false;
    } else {
      // An entry from outside the router has no position to traverse back to.
      history.replaceState({ ...history.state, evletIndex: index }, "", location());
      if (await allowed()) {
        history.pushState({ evletIndex: ++index }, "", target);
        setLocation(target);
      }
      pending = false;
    }
  };
  const before = (event: BeforeUnloadEvent) => {
    // Browsers only permit their own warning when closing or reloading a tab.
    if (guard?.()) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  window.addEventListener("popstate", update);
  window.addEventListener("beforeunload", before);
  onCleanup(() => {
    disposed = true;
    generation++;
    restore?.();
    window.removeEventListener("popstate", update);
    window.removeEventListener("beforeunload", before);
  });
  return {
    location,
    setGuard(value?: () => boolean) {
      guard = value;
      generation++;
    },
    async navigate(path: string, replace = false, bypass = false) {
      if (
        !path.startsWith("/") || path.startsWith("//") ||
        path.includes("\\") || /[\r\n\0]/.test(path)
      ) throw new Error("Use an application-relative URL");
      if (disposed || (!bypass && pending)) return false;
      if (bypass) generation++;
      pending = true;
      try {
        const accepted = bypass || await allowed();
        if (restored) await restored;
        if (!accepted || disposed) return false;
        if (!replace) index++;
        history[replace ? "replaceState" : "pushState"]({ ...history.state, evletIndex: index }, "", path);
        setLocation(path);
        return true;
      } finally {
        pending = false;
      }
    },
  };
}
