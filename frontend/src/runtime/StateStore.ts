import { createMemo } from "solid-js";
import { createStore } from "solid-js/store";
import { DecimalValue, decimalOperation } from './DecimalValue';
export type StateScope = "component" | "view" | "record" | "application";
const valid = /^(component|view|record|application)\.([A-Za-z][A-Za-z0-9_]*)$/;
const truthy = (value: any) => value instanceof DecimalValue ? value.coefficient !== 0n : !!value;
export function evaluate(value: any, read: (path: string) => any): any {
  if (Array.isArray(value)) return value.map((item) => evaluate(item, read));
  if (!value || typeof value !== "object") return value;
  if (!value.$expr)
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, evaluate(item, read)]),
    );
  if (value.$expr === "ref") return read(value.args[0]);
  if (value.$expr === 'choose') return evaluate(value.args[truthy(evaluate(value.args[0], read)) ? 1 : 2], read);
  if (value.$expr === 'and') return truthy(evaluate(value.args[0], read)) && truthy(evaluate(value.args[1], read));
  if (value.$expr === 'or') return truthy(evaluate(value.args[0], read)) || truthy(evaluate(value.args[1], read));
  const [a, b, c] = value.args.map((arg: any) => evaluate(arg, read));
  if (['add', 'sub', 'mul', 'div', 'neg'].includes(value.$expr)) return decimalOperation(value.$expr, a, b);
  if (['sum', 'min', 'max', 'count'].includes(value.$expr)) {
    if (!Array.isArray(a)) throw new Error('Aggregates require a table field');
    if (value.$expr === 'count') return a.length;
    if (value.$expr === 'sum') return a.reduce((sum, item) => decimalOperation('add', sum, item), DecimalValue.from(0));
    return a.length ? a.map(DecimalValue.from).reduce((x, y) => (x.compare(y) < 0) === (value.$expr === 'min') ? x : y) : null;
  }
  if ((a instanceof DecimalValue || b instanceof DecimalValue) && a != null && b != null) {
    const comparison = DecimalValue.from(a).compare(b);
    return ({eq: comparison === 0, ne: comparison !== 0, lt: comparison < 0, lte: comparison <= 0, gt: comparison > 0, gte: comparison >= 0} as any)[value.$expr];
  }
  // Match the server: ordering against a missing value is false, not a comparison with 0.
  if (['lt', 'lte', 'gt', 'gte'].includes(value.$expr) && (a == null || b == null)) return false;
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
      return !truthy(a);
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
