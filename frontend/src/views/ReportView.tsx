import { For, Show, createSignal, onCleanup } from 'solid-js';
import { Button, PageHeader, RecordList } from '../ui';
import { request } from '../api';
import { messageFor } from '../runtime/Context';
import TransientForm, { formPayload, type FormDefinition } from './TransientForm';
import NativePanels from './NativePanels';

export default function ReportView(props: {report: FormDefinition & {autorun: boolean; columns: any[]}}) {
  const [filters, setFilters] = createSignal<any>({}), [ready, setReady] = createSignal(false), [busy, setBusy] = createSignal(false),
    [result, setResult] = createSignal<any>(null), [error, setError] = createSignal(''), [changed, setChanged] = createSignal(false);
  let sequence = 0, alive = true, effective = '', executed = '';
  const url = '/api/_reports/' + props.report.name;
  async function run(offset = 0, snapshot?: any) {
    if (!snapshot && !ready()) return;
    const current = ++sequence;
    const input = snapshot || formPayload(props.report, filters());
    setBusy(true); setError('');
    try {
      const response = await request(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({filters: input, offset, limit: 50, revision: current})});
      if (!alive || current !== sequence || response.revision !== current) return;
      executed = JSON.stringify(input);
      setResult({...response, input});
      setChanged(executed !== effective);
    } catch (error) { if (alive && current === sequence) setError(messageFor(error)); }
    finally {if (alive && current === sequence) setBusy(false);}
  }
  onCleanup(() => {alive = false; sequence++;});
  return <div class="page-scroll"><PageHeader title={props.report.label} />
    <NativePanels target={props.report.name} result={result()} />
    <section class="report-filters record-form"><TransientForm definition={props.report} evaluateUrl={url + '/evaluate'} report onValues={(values, valid) => {
      setFilters(values); setReady(valid);
      let next = '';
      try { next = JSON.stringify(formPayload(props.report, values)); } catch { return; }
      if (next !== effective) {effective = next; sequence++; setBusy(false); setChanged(!!result() && next !== executed);}
      if (props.report.autorun && valid && next !== executed) void run();
    }} />
    <Button variant="primary" disabled={!ready() || busy()} onClick={() => void run()}>{busy() ? 'Running…' : 'Run'}</Button></section>
    <Show when={changed()}><p role="status">Inputs changed. Run again to update these results.</p></Show>
    <Show when={error()}><p role="alert">{error()}</p></Show>
    <Show when={result()}><RecordList records={result().rows} getKey={row => result().rows.indexOf(row)} getLabel={row => String((row as any).id || 'Report row')}
      columns={props.report.columns.map(column => ({key:column.name,header:column.label || column.name,cell:(row:any) => String(row[column.name] ?? ''),align:['decimal','currency','integer'].includes(column.type) ? 'right' as const : 'left' as const}))}
      empty={<p>No results.</p>} />
      <p>{result().total} matching rows</p>
      <Show when={result().totals}><p>Current page totals: <For each={Object.entries(result().totals.values)}>{([name, value]) => <span>{name}: {String(value)} </span>}</For></p></Show>
      <Button disabled={busy() || !result().offset} onClick={() => void run(Math.max(0, result().offset - 50), result().input)}>Previous</Button>
      <Button disabled={busy() || result().offset + 50 >= result().total} onClick={() => void run(result().offset + 50, result().input)}>Next</Button>
    </Show>
  </div>;
}
