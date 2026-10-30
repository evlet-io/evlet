import { children, type JSX } from 'solid-js';
export const contentSlot = (value: () => JSX.Element) => children(value);
export function forwardRef<T>(ref: T | ((element: T) => void) | undefined, element: T) {
  if (typeof ref === 'function') (ref as (element: T) => void)(element);
}
export function mergeStyle(
  defaults: JSX.CSSProperties,
  style: JSX.CSSProperties | string | undefined,
): JSX.CSSProperties | string {
  if (typeof style === 'string')
    return (
      Object.entries(defaults)
        .filter(([, value]) => value !== undefined)
        .map(([key, value]) => `${key}:${value}`)
        .join(';') +
      ';' +
      style
    );
  return { ...defaults, ...style };
}
export function callHandler<T extends HTMLElement, E extends Event>(
  handler: JSX.EventHandlerUnion<T, E> | undefined,
  event: E & { currentTarget: T; target: Element },
) {
  if (typeof handler === 'function') handler(event);
  else if (Array.isArray(handler)) handler[0](handler[1], event);
}

export function withoutRef<T extends { ref?: unknown }>(props: T | undefined): Omit<T, 'ref'> {
  const { ref, ...rest } = props ?? ({} as T);
  return rest;
}
