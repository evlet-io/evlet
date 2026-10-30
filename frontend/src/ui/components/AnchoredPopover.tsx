import { createSignal, onMount, onCleanup, type JSX } from "solid-js";
import { popupPosition } from "./date-time";

export function AnchoredPopover(props: {
  anchor: HTMLElement;
  control: HTMLElement;
  id: string;
  label: string;
  width?: number;
  matchWidth?: boolean;
  role?: "dialog";
  class?: string;
  onClose: (focus?: boolean) => void;
  panelRef: (el: HTMLDivElement) => void;
  children: JSX.Element;
}) {
  let panel!: HTMLDivElement,
    frame = 0;
  const [position, setPosition] = createSignal({
    left: 0,
    top: 0,
    width: props.width || 290,
    maxHeight: 500,
    placement: "bottom",
  });
  const update = () => {
    const rect = props.control.getBoundingClientRect(),
      visual = window.visualViewport;
    const viewport = {
      width: visual?.width || window.innerWidth,
      height: visual?.height || window.innerHeight,
      left: visual?.offsetLeft || 0,
      top: visual?.offsetTop || 0,
    };
    if (
      rect.bottom < (viewport.top || 0) ||
      rect.top > viewport.height + (viewport.top || 0)
    ) {
      props.onClose();
      return;
    }
    for (
      let ancestor = props.anchor.parentElement;
      ancestor && ancestor !== document.body;
      ancestor = ancestor.parentElement
    ) {
      const style = getComputedStyle(ancestor);
      if (/auto|scroll|hidden|clip/.test(style.overflowY)) {
        const bounds = ancestor.getBoundingClientRect();
        if (rect.bottom <= bounds.top || rect.top >= bounds.bottom) {
          props.onClose();
          return;
        }
      }
    }
    setPosition(
      popupPosition(
        rect,
        {
          width: props.matchWidth
            ? Math.max(rect.width, props.width || 220)
            : props.width || 290,
          height: panel.scrollHeight + panel.offsetHeight - panel.clientHeight,
        },
        viewport,
      ),
    );
  };
  const schedule = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(update);
  };
  onMount(() => {
    panel.showPopover?.();
    update();
    const outside = (e: Event) => {
      if (
        !props.anchor.contains(e.target as Node) &&
        !panel.contains(e.target as Node)
      )
        props.onClose();
    };
    const key = (e: KeyboardEvent) => {
      const target =
        e.target instanceof Element ? e.target : document.activeElement;
      if (
        e.key === "Escape" &&
        (target?.closest(".ev-anchored-popover") === panel ||
          props.anchor.contains(target))
      ) {
        e.preventDefault();
        e.stopPropagation();
        props.onClose(true);
      }
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", outside, true);
    document.addEventListener("keydown", key, true);
    document.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    window.visualViewport?.addEventListener("resize", schedule);
    window.visualViewport?.addEventListener("scroll", schedule);
    const resize = new ResizeObserver(schedule);
    resize.observe(props.anchor);
    resize.observe(panel);
    onCleanup(() => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      panel.hidePopover?.();
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("focusin", outside, true);
      document.removeEventListener("keydown", key, true);
      document.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      window.visualViewport?.removeEventListener("resize", schedule);
      window.visualViewport?.removeEventListener("scroll", schedule);
    });
  });
  return (
    <div
      ref={(el) => {
        panel = el;
        props.panelRef(el);
      }}
      id={props.id}
      class={"ev-anchored-popover " + (props.class || "")}
      popover={
        typeof HTMLElement.prototype.showPopover === "function"
          ? "manual"
          : undefined
      }
      role={props.role}
      aria-label={props.label}
      data-placement={position().placement}
      style={{
        left: position().left + "px",
        top: position().top + "px",
        width: position().width + "px",
        "max-height": position().maxHeight + "px",
      }}
    >
      {props.children}
    </div>
  );
}
