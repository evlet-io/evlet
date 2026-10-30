import { createSignal, createUniqueId, onCleanup, onMount, Show } from "solid-js";
import { Portal } from "solid-js/web";
import { Button, Modal } from "./primitives";

export interface ConfirmationOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
}
interface DialogRequest extends ConfirmationOptions {
  kind: "confirm" | "error";
  resolve: (accepted: boolean) => void;
  returnFocus: HTMLElement | null;
}

export function createDialogs() {
  const [current, setCurrent] = createSignal<DialogRequest>();
  const queue: DialogRequest[] = [];
  let disposed = false;
  function enqueue(options: ConfirmationOptions, kind: DialogRequest["kind"]) {
    if (disposed) return Promise.resolve(false);
    return new Promise<boolean>((resolve) => {
      const request = {
        ...options, kind, resolve,
        returnFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null,
      };
      if (current()) queue.push(request);
      else setCurrent(request);
    });
  }
  function finish(accepted = false) {
    const request = current();
    if (!request) return;
    setCurrent(undefined);
    if (request.returnFocus?.isConnected) request.returnFocus.focus();
    const next = queue.shift();
    if (next) setCurrent(next);
    request.resolve(accepted);
  }
  function dismissAll() {
    const pending = [...(current() ? [current()!] : []), ...queue];
    queue.length = 0;
    setCurrent(undefined);
    for (const request of pending) request.resolve(false);
  }
  onCleanup(() => {
    disposed = true;
    dismissAll();
  });
  return {
    current, finish, dismissAll,
    confirm: (options: ConfirmationOptions) => enqueue(options, "confirm"),
    error: (message: string, title = "Unable to complete the action") =>
      enqueue({ title, message, confirmLabel: "OK" }, "error").then(() => {}),
  };
}
export type DialogController = ReturnType<typeof createDialogs>;

export function DialogHost(props: { dialogs: DialogController }) {
  return (
    <Portal>
      <Show when={props.dialogs.current()} keyed>
        {(request) => {
          const descriptionId = createUniqueId();
          let primary: HTMLButtonElement | undefined;
          let cancel: HTMLButtonElement | undefined;
          return (
            <Modal
              open
              class="ev-message-dialog"
              role={request.kind === "error" ? "alertdialog" : "dialog"}
              aria-describedby={descriptionId}
              title={request.title}
              onClose={() => props.dialogs.finish(false)}
              initialFocus={() => cancel || primary}
              onKeyDown={(event) => {
                if (event.key !== "Tab" || event.defaultPrevented) return;
                const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
                const first = buttons[0], last = buttons[buttons.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                  event.preventDefault();
                  last?.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                  event.preventDefault();
                  first?.focus();
                }
              }}
              footer={<>
                <Show when={request.kind === "confirm"}>
                  <Button ref={cancel} onClick={() => props.dialogs.finish(false)}>
                    {request.cancelLabel || "Cancel"}
                  </Button>
                </Show>
                <Button ref={primary} variant="primary" onClick={() => props.dialogs.finish(true)}>
                  {request.confirmLabel || "Continue"}
                </Button>
              </>}
            >
              <p id={descriptionId} class="ev-message-copy">{request.message}</p>
            </Modal>
          );
        }}
      </Show>
    </Portal>
  );
}

// Keep constraint validation while replacing browser validation bubbles.
export function useValidationDialogs(dialogs: DialogController) {
  let alive = true;
  let scheduled = false;
  const invalid = new Set<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>();
  const handleInvalid = (event: Event) => {
    const field = event.target;
    if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) return;
    event.preventDefault();
    invalid.add(field);
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(async () => {
      scheduled = false;
      if (!alive) return;
      const fields = [...invalid];
      invalid.clear();
      const messages = fields.map((input) => {
        const control = input.closest(".ev-select")?.querySelector<HTMLButtonElement>('[role="combobox"]') || input;
        const labelledBy = control.getAttribute("aria-labelledby")?.split(/\s+/).map((id) => document.getElementById(id)?.textContent?.trim()).join(" ");
        const label = control.getAttribute("aria-label") || labelledBy || [...(control.labels || [])].map((label) => label.textContent?.trim()).join(" ") || input.name;
        return (label ? label + ": " : "") + input.validationMessage;
      });
      await dialogs.error(messages.join("\n"), "Check your entries");
      const first = fields[0];
      const control = first.closest(".ev-select")?.querySelector<HTMLElement>('[role="combobox"]') || first;
      if (alive && control.isConnected) control.focus();
    });
  };
  onMount(() => document.addEventListener("invalid", handleInvalid, true));
  onCleanup(() => {
    alive = false;
    invalid.clear();
    document.removeEventListener("invalid", handleInvalid, true);
  });
}
