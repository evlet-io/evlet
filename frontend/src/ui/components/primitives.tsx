import {
  createEffect,
  createMemo,
  createSignal,
  createUniqueId,
  For,
  onCleanup,
  Show,
  splitProps,
  type JSX,
} from 'solid-js';
import { Portal } from 'solid-js/web';
import { cx, type Action, type ElementProps } from '../types';
import { useOverlayMount } from './context';
import { callHandler, contentSlot, forwardRef, mergeStyle } from './helpers';

export function Glyph(props: {
  name: 'close' | 'down' | 'menu' | 'check' | 'search' | 'arrow' | 'more';
  class?: string;
}) {
  const paths = {
    close: 'M6 6l12 12M6 18L18 6',
    down: 'M5 9l7 7 7-7',
    menu: 'M4 6h16M4 12h16M4 18h16',
    check: 'M5 12l4 4L19 6',
    search: 'M16 16l5 5',
    arrow: 'M4 12h16m-6-6 6 6-6 6',
    more: 'M5 12h.01M12 12h.01M19 12h.01',
  };
  return (
    <svg class={cx('ev-icon', props.class)} viewBox="0 0 24 24" aria-hidden="true">
      <Show when={props.name === 'search'}>
        <circle cx="10.5" cy="10.5" r="6.5" />
      </Show>
      <path d={paths[props.name]} />
    </svg>
  );
}
export interface ButtonProps extends JSX.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost';
  size?: 'normal' | 'small';
  icon?: JSX.Element;
}
export function Button(props: ButtonProps) {
  const [p, rest] = splitProps(props, [
    'class',
    'classList',
    'variant',
    'size',
    'icon',
    'children',
  ]);
  return (
    <button
      type="button"
      {...rest}
      class={cx(
        'ev-btn',
        'ev-' + (p.variant ?? 'secondary'),
        p.size === 'small' && 'ev-small',
        p.class,
      )}
      classList={p.classList}
    >
      {p.icon}
      {p.children}
    </button>
  );
}
export function IconButton(props: JSX.ButtonHTMLAttributes<HTMLButtonElement>) {
  const [p, rest] = splitProps(props, ['class', 'classList', 'children']);
  return (
    <button type="button" {...rest} class={cx('ev-icon-button', p.class)} classList={p.classList}>
      {p.children}
    </button>
  );
}
export interface BadgeProps extends ElementProps<HTMLSpanElement> {
  tone?: 'info' | 'success' | 'warning' | 'neutral';
}
export function Badge(props: BadgeProps) {
  const [p, rest] = splitProps(props, ['class', 'classList', 'tone', 'children']);
  return (
    <span
      {...rest}
      class={cx('ev-badge', 'ev-tone-' + (p.tone ?? 'neutral'), p.class)}
      classList={p.classList}
    >
      {p.children}
    </span>
  );
}
export interface AvatarProps extends ElementProps<HTMLSpanElement> {
  initials: string;
  background?: string;
  color?: string;
  label?: string;
}
export function Avatar(props: AvatarProps) {
  const [p, rest] = splitProps(props, [
    'class',
    'classList',
    'style',
    'initials',
    'background',
    'color',
    'label',
  ]);
  return (
    <span
      {...rest}
      class={cx('ev-avatar', p.class)}
      classList={p.classList}
      aria-label={p.label}
      style={mergeStyle({ '--ev-avbg': p.background, '--ev-avfg': p.color }, p.style)}
    >
      {p.initials}
    </span>
  );
}

export interface ActionMenuProps extends ElementProps {
  actions: readonly Action[];
  label?: string;
  iconOnly?: boolean;
  menuClass?: string;
  menuStyle?: JSX.CSSProperties;
  primary?: boolean;
}
export function ActionMenu(props: ActionMenuProps) {
  const [p, rest] = splitProps(props, [
    'actions',
    'label',
    'iconOnly',
    'menuClass',
    'menuStyle',
    'primary',
    'class',
    'classList',
  ]);
  const actions = createMemo(() => p.actions.filter((a) => !a.hidden));
  const [open, setOpen] = createSignal(false),
    [position, setPosition] = createSignal<JSX.CSSProperties>({});
  const id = createUniqueId(),
    mount = useOverlayMount();
  let trigger!: HTMLButtonElement, menu: HTMLDivElement | undefined;
  const close = (restore = false) => {
    setOpen(false);
    if (restore) trigger.focus();
  };
  const items = () =>
    Array.from(menu?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);
  const locate = () => {
    const r = trigger.getBoundingClientRect(),
      height = menu?.offsetHeight ?? 150,
      width = Math.max(190, menu?.offsetWidth ?? 190);
    setPosition({
      position: 'fixed',
      left: Math.max(8, Math.min(r.right - width, innerWidth - width - 8)) + 'px',
      top:
        (r.bottom + height + 8 > innerHeight ? Math.max(8, r.top - height - 6) : r.bottom + 6) +
        'px',
    });
  };
  createEffect(() => {
    if (!open()) return;
    queueMicrotask(() => {
      if (open()) {
        locate();
        items()[0]?.focus();
      }
    });
    const outside = (e: PointerEvent) => {
      if (!trigger.contains(e.target as Node) && !menu?.contains(e.target as Node)) close();
    };
    const resize = () => locate();
    document.addEventListener('pointerdown', outside);
    window.addEventListener('resize', resize);
    onCleanup(() => {
      document.removeEventListener('pointerdown', outside);
      window.removeEventListener('resize', resize);
    });
  });
  createEffect(() => {
    if (!actions().length && open()) close(true);
  });
  const keys: JSX.EventHandler<HTMLDivElement, KeyboardEvent> = (e) => {
    const list = items();
    let index = list.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === 'Tab') {
      close(true);
    } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key) && list.length) {
      e.preventDefault();
      index =
        e.key === 'Home'
          ? 0
          : e.key === 'End'
            ? list.length - 1
            : (index + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length;
      list[index]?.focus();
    }
  };
  return (
    <Show when={actions().length}>
      <div {...rest} class={cx('ev-action-group', p.class)} classList={p.classList}>
        <button
          ref={trigger}
          type="button"
          class={
            p.iconOnly ? 'ev-icon-button' : cx('ev-btn', p.primary ? 'ev-primary' : 'ev-secondary')
          }
          aria-label={p.iconOnly ? (p.label ?? 'Actions') : undefined}
          aria-haspopup="menu"
          aria-expanded={open()}
          aria-controls={open() ? id : undefined}
          onClick={() => setOpen((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setOpen(true);
            }
          }}
        >
          {p.iconOnly ? (
            <Glyph name="more" />
          ) : (
            <>
              {p.label ?? 'Actions'}
              <Glyph name="down" />
            </>
          )}
        </button>
        <Show when={open()}>
          <Portal mount={mount?.()}>
            <div
              ref={menu}
              id={id}
              role="menu"
              aria-label={p.label ?? 'Actions'}
              class={cx('ev-action-menu', p.menuClass)}
              style={{ ...position(), ...p.menuStyle }}
              onKeyDown={keys}
            >
              <For each={actions()}>
                {(action) => (
                  <button
                    role="menuitem"
                    type="button"
                    disabled={action.disabled}
                    classList={{ 'ev-destructive': action.destructive }}
                    onClick={() => {
                      close(true);
                      action.onSelect();
                    }}
                  >
                    {action.icon}
                    <span>{action.label}</span>
                  </button>
                )}
              </For>
            </div>
          </Portal>
        </Show>
      </div>
    </Show>
  );
}
export interface PrimaryActionsProps extends ElementProps {
  actions?: readonly Action[];
  label?: string;
}
export function PrimaryActions(props: PrimaryActionsProps) {
  const [p, rest] = splitProps(props, ['actions', 'label', 'class', 'classList']);
  const visible = createMemo(() => p.actions?.filter((a) => !a.hidden) ?? []);
  return (
    <Show when={visible().length}>
      <div {...rest} class={cx('ev-primary-actions', p.class)} classList={p.classList}>
        <Show
          when={visible().length === 1}
          fallback={<ActionMenu primary actions={visible()} label={p.label} />}
        >
          <For each={visible()}>
            {(a) => (
              <Button
                variant="primary"
                icon={a.icon}
                disabled={a.disabled}
                onClick={() => a.onSelect()}
              >
                {a.label}
              </Button>
            )}
          </For>
        </Show>
      </div>
    </Show>
  );
}

export interface ModalProps extends Omit<
  JSX.IntrinsicElements['dialog'],
  'open' | 'title' | 'onClose' | 'onCancel'
> {
  open: boolean;
  onClose: () => void;
  title: JSX.Element;
  description?: JSX.Element;
  footer?: JSX.Element;
  initialFocus?: () => HTMLElement | undefined;
}
export function Modal(props: ModalProps) {
  const [p, rest] = splitProps(props, [
    'open',
    'onClose',
    'title',
    'description',
    'footer',
    'initialFocus',
    'children',
    'class',
    'classList',
    'ref',
    'onClick',
  ]);
  const descriptionContent = contentSlot(() => p.description);
  const footerContent = contentSlot(() => p.footer);
  const id = createUniqueId();
  let dialog!: HTMLDialogElement;
  createEffect(() => {
    if (p.open) {
      if (!dialog.open) {
        dialog.showModal();
        queueMicrotask(() => p.initialFocus?.()?.focus());
      }
    } else if (dialog.open) dialog.close();
  });
  onCleanup(() => {
    if (dialog?.open) dialog.close();
  });
  return (
    <dialog
      {...rest}
      ref={(el) => {
        dialog = el;
        forwardRef(p.ref, el);
      }}
      class={cx('ev-dialog', p.class)}
      classList={p.classList}
      aria-labelledby={id}
      onCancel={(e) => {
        e.preventDefault();
        p.onClose();
      }}
      onClose={() => {
        if (p.open) p.onClose();
      }}
      onClick={(e) => {
        callHandler(p.onClick, e);
        if (e.defaultPrevented || e.target !== dialog) return;
        const r = dialog.getBoundingClientRect();
        if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)
          p.onClose();
      }}
    >
      <div class="ev-dialog-head">
        <div>
          <h2 id={id}>{p.title}</h2>
          <Show when={descriptionContent()}>
            <p>{descriptionContent()}</p>
          </Show>
        </div>
        <IconButton aria-label="Close dialog" onClick={p.onClose}>
          <Glyph name="close" />
        </IconButton>
      </div>
      <div class="ev-dialog-body">{p.children}</div>
      <Show when={footerContent()}>
        <div class="ev-dialog-footer">{footerContent()}</div>
      </Show>
    </dialog>
  );
}
export interface FieldControl {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
}
export interface FieldProps extends Omit<ElementProps, 'children'> {
  label: JSX.Element;
  controlId?: string;
  required?: boolean;
  help?: JSX.Element;
  error?: JSX.Element;
  children: (control: FieldControl) => JSX.Element;
}
export function Field(props: FieldProps) {
  const [p, rest] = splitProps(props, [
    'label',
    'controlId',
    'required',
    'help',
    'error',
    'children',
    'class',
    'classList',
  ]);
  const errorContent = contentSlot(() => p.error);
  const helpContent = contentSlot(() => p.help);
  const uid = createUniqueId();
  const id = () => p.controlId ?? uid;
  return (
    <div {...rest} class={cx('ev-field', p.class)} classList={p.classList}>
      <label for={id()}>
        {p.label}
        <Show when={p.required}>
          <span class="ev-required"> *</span>
        </Show>
      </label>
      {p.children({
        get id() {
          return id();
        },
        get describedBy() {
          return errorContent() ? id() + '-error' : helpContent() ? id() + '-help' : undefined;
        },
        get invalid() {
          return Boolean(errorContent());
        },
      })}
      <Show when={helpContent() && !errorContent()}>
        <small class="ev-hint" id={id() + '-help'}>
          {helpContent()}
        </small>
      </Show>
      <Show when={errorContent()}>
        <small class="ev-field-error" id={id() + '-error'} role="alert">
          {errorContent()}
        </small>
      </Show>
    </div>
  );
}
export interface ToastProps extends ElementProps {
  message?: JSX.Element;
  action?: { label: string; onSelect: () => void };
  onDismiss: () => void;
}
export function Toast(props: ToastProps) {
  const [p, rest] = splitProps(props, ['message', 'action', 'onDismiss', 'class', 'classList']);
  const messageContent = contentSlot(() => p.message);
  return (
    <Show when={messageContent()}>
      <div {...rest} class={cx('ev-toast', p.class)} classList={p.classList} role="status">
        <Glyph name="check" />
        <span>{messageContent()}</span>
        <Show when={p.action}>
          {(action) => (
            <button type="button" onClick={() => action().onSelect()}>
              {action().label}
            </button>
          )}
        </Show>
        <IconButton aria-label="Dismiss notification" onClick={p.onDismiss}>
          <Glyph name="close" />
        </IconButton>
      </div>
    </Show>
  );
}
