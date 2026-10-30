import { Show, type JSX } from "solid-js";
export interface InputGroupProps {
  prefix?: string;
  suffix?: string;
  children: JSX.Element;
}
export function InputGroup(props: InputGroupProps) {
  return (
    <div
      role={props.prefix || props.suffix ? "group" : undefined}
      aria-label={
        [props.prefix, props.suffix].filter(Boolean).join(" ") || undefined
      }
      class={
        props.prefix || props.suffix ? "ev-input-group" : "ev-input-control"
      }
    >
      <Show when={props.prefix}>
        <span class="ev-input-addon" aria-hidden="true">
          {props.prefix}
        </span>
      </Show>
      {props.children}
      <Show when={props.suffix}>
        <span class="ev-input-addon" aria-hidden="true">
          {props.suffix}
        </span>
      </Show>
    </div>
  );
}
