import { createMemo } from "solid-js";
import { createStore } from "solid-js/store";
export type StateScope = "component" | "view" | "record" | "application";
const valid = /^(component|view|record|application)\.([A-Za-z][A-Za-z0-9_]*)$/;
export function evaluate(value: any, read: (path: string) => any): any {
  if (Array.isArray(value)) return value.map((item) => evaluate(item, read));
  if (!value || typeof value !== "object") return value;
  if (!value.$expr)
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, evaluate(item, read)]),
    );
  if (value.$expr === "ref") return read(value.args[0]);
  const [a, b, c] = value.args.map((arg: any) => evaluate(arg, read));
  switch (value.$expr) {
    case "eq":
      return a === b;
    case "ne":
      return a !== b;
    case "gt":
      return a > b;
    case "gte":
      return a >= b;
    case "lt":
      return a < b;
    case "lte":
      return a <= b;
    case "and":
      return !!a && !!b;
    case "or":
      return !!a || !!b;
    case "not":
      return !a;
    case "choose":
      return a ? b : c;
    case "contains":
      return (typeof a === "string" || Array.isArray(a)) && a.includes(b);
    default:
      throw new Error("Unsupported expression: " + value.$expr);
  }
}
export function createState(
  initial: Record<string, any> = {},
  computed: Record<string, any> = {},
  application: Record<string, any> = {},
) {
  const [state, set] = createStore<Record<StateScope, Record<string, any>>>({
    component: {},
    view: structuredClone(initial),
    record: {},
    application,
  });
  const memos: Record<string, () => any> = {};
  const read = (path: string): any => {
    const match = valid.exec(path);
    if (!match || ["constructor", "prototype", "__proto__"].includes(match[2]))
      throw new Error("Invalid scoped state path");
    if (memos[path]) return memos[path]();
    return state[match[1] as StateScope][match[2]];
  };
  const building = new Set<string>();
  const build = (path: string): (() => any) => {
    if (memos[path]) return memos[path];
    if (building.has(path)) throw new Error("Circular computed state: " + path);
    building.add(path);
    memos[path] = createMemo(() =>
      evaluate(computed[path], (ref) =>
        computed[ref] ? build(ref)() : read(ref),
      ),
    );
    building.delete(path);
    return memos[path];
  };
  Object.keys(computed).forEach(build);
  return {
    state,
    read,
    evaluate: (value: any) => evaluate(value, read),
    write(path: string, value: any) {
      const match = valid.exec(path);
      if (
        !match ||
        computed[path] ||
        ["constructor", "prototype", "__proto__"].includes(match[2]) ||
        ["application.user", "application.tenant"].includes(path)
      )
        throw new Error("State is invalid or computed");
      set(match[1] as StateScope, match[2], () => value);
    },
    reset(path: string) {
      const match = valid.exec(path);
      if (
        !match ||
        ["constructor", "prototype", "__proto__"].includes(match[2]) ||
        ["application.user", "application.tenant"].includes(path)
      )
        throw new Error("Invalid state path");
      set(
        match[1] as StateScope,
        match[2],
        match[1] === "view" ? initial[match[2]] : undefined,
      );
    },
  };
}

export function componentState(parent: ReturnType<typeof createState>) {
  const [local, setLocal] = createStore<Record<string, any>>({});
  const key = (path: string) => {
    if (
      !valid.test(path) ||
      ["constructor", "prototype", "__proto__"].includes(path.slice(10))
    )
      throw new Error("Invalid component state path");
    return path.slice(10);
  };
  const read = (path: string): any =>
    path.startsWith("component.") ? local[key(path)] : parent.read(path);
  return {
    ...parent,
    read,
    evaluate: (value: any) => evaluate(value, read),
    write(path: string, value: any) {
      if (!path.startsWith("component.")) return parent.write(path, value);
      setLocal(key(path), () => value);
    },
    reset(path: string) {
      if (path.startsWith("component.")) setLocal(key(path), undefined);
      else parent.reset(path);
    },
  };
}
