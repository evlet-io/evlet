import { For, Show, createResource, onCleanup } from 'solid-js';
import { Button } from '../ui';
import { request } from '../api';
import { useApp, messageFor } from '../runtime/Context';
import { NodeRenderer, makeRuntime } from '../runtime/ViewRenderer';
import { createState } from '../runtime/StateStore';

function NativePanel(props: {descriptor: any; record?: any; result?: any}) {
  const app = useApp();
  const state = createState({}, {}, app.applicationState || {});
  const runtime = makeRuntime({state: {}, computed: {}} as any, {state});
  let dispose: (() => void) | undefined, alive = true;
  const [panel] = createResource(async () => {
    const module = await import(/* @vite-ignore */ props.descriptor.url);
    if (!alive) return;
    if (module.extension.name !== props.descriptor.name || module.extension.target !== props.descriptor.target)
      throw new Error('Native extension does not match the active build');
    const rendered = module.extension.render({get record() {return props.record;}, get result() {return props.result;}, request, notify: app.notify, dialogs: app.dialogs});
    if (!rendered || typeof rendered.title !== 'string' || rendered.content != null && typeof rendered.content !== 'string' || !rendered.content && !rendered.layout)
      throw new Error('Native panels return a title and escaped content or a native component layout');
    for (const [name, value] of Object.entries(rendered.state || {})) state.write(name, value);
    if (rendered.dispose != null && typeof rendered.dispose !== 'function') throw new Error('Invalid panel cleanup');
    dispose = rendered.dispose;
    return rendered;
  });
  onCleanup(() => {alive = false; dispose?.();});
  return <section class="native-panel"><Show when={panel.loading}><p role="status">Loading panel…</p></Show>
    <Show when={panel.error}><p role="alert">{messageFor(panel.error)}</p></Show>
    <Show when={panel()}>{value => <><h3>{value().title}</h3><p>{value().content}</p>
      <Show when={value().layout}><NodeRenderer node={value().layout} runtime={runtime}/></Show>
      <For each={value().actions || []}>{action => <Button onClick={async () => {try {await action.onSelect();} catch (error) {void app.dialogs.error(messageFor(error));}}}>{action.label}</Button>}</For>
    </>}</Show>
  </section>;
}
export default function NativePanels(props: {target: string; record?: any; result?: any}) {
  const app = useApp();
  return <For each={(app.nativePanels || []).filter(panel => panel.target === props.target)}>{descriptor => <NativePanel descriptor={descriptor} record={props.record} result={props.result} />}</For>;
}
