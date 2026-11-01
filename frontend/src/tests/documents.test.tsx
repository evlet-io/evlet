import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@solidjs/testing-library';
import { evaluate } from '../runtime/StateStore';
import { DecimalValue } from '../runtime/DecimalValue';
import { calculateDocument } from '../runtime/DocumentState';
import ReportView from '../views/ReportView';
import { AppContext } from '../runtime/Context';
import vectors from './document-vectors.json';
import contract from './document-contract.json';
import RecordForm from '../views/RecordForm';
import uiFixture from './ui-fixture.json';
import { createRouter } from '../runtime/Router';
const api = vi.hoisted(() => ({request: vi.fn(), listRecords: vi.fn(), getRecord: vi.fn(), recordRequest: vi.fn()}));
vi.mock('../api', () => api);
afterEach(() => {cleanup(); vi.clearAllMocks();});

it('hydrates query fields without dirtying the record and retains row input identity', async () => {
  api.request.mockImplementation(async (url, options) => {
    if (url.startsWith('/api/_draft/')) {
      const input = JSON.parse(options.body);
      const values = {...contract.preview.values, id:1, children:{items:contract.preview.values.children.items.map((row,index) => ({...row,id:index+1,_key:String(index+1)}))}};
      return {...contract.preview, values, revision:input.revision};
    }
    return {data:contract.saved,etag:contract.etag};
  });
  const model = contract.models.find(model => model.name === 'order')!;
  render(() => <AppContext.Provider value={{...context, ui:{...uiFixture.ui,views:[contract.form]}, models:contract.models, user:{id:1,role:'admin',username:'owner'}, router:createRouter(async () => true), refresh:() => 0, capabilities:{}} as any}><RecordForm model={model as any} recordId="1"/></AppContext.Provider>);
  await screen.findByDisplayValue('Contract');
  await waitFor(() => expect(screen.getAllByLabelText('Stock')[0]).toHaveValue('5.0000'));
  expect(screen.queryByText('State is invalid or computed')).not.toBeInTheDocument();
  expect(screen.queryByText('Unsaved')).not.toBeInTheDocument();
  const input = screen.getAllByLabelText(/Qty/)[0];
  input.focus();
  fireEvent.input(input, {target:{value:'4'}});
  expect(screen.getAllByLabelText(/Qty/)[0]).toBe(input);
  expect(document.activeElement).toBe(input);
});

describe('shared exact document expressions', () => {
  it('consumes the authenticated Python document contract', () => {
    const model = contract.models.find(model => model.name === 'order')!;
    const result = calculateDocument(model as any, contract.models as any, contract.input, contract.input.children);
    expect(result.values.total).toBe(contract.preview.values.total);
    expect(result.children.items.map(row => row.amount)).toEqual(contract.preview.values.children.items.map(row => row.amount));
  });
  for (const [index, vector] of vectors.entries()) it('matches server vector ' + index, () => {
    const convert = (value: any): any => Array.isArray(value) ? value.map(convert) : value == null ? null : DecimalValue.from(value);
    const run = () => DecimalValue.from(evaluate(vector.expression, path => convert((vector.inputs as any)[path]))).fixed(vector.scale);
    if (vector.error) expect(run).toThrow();
    else expect(run()).toBe(vector.expected);
  });
  it('keeps lazy zero guards and row identities', () => {
    expect(evaluate({$expr: 'and', args: [{$expr: 'ref', args: ['qty']}, {$expr: 'div', args: [1,0]}]}, () => DecimalValue.from(0))).toBe(false);
    const row: any = {name: 'item', fields: [{name: 'qty', type: 'decimal'}, {name: 'rate', type: 'currency'}, {name: 'amount', type: 'currency', scale: 2}], behavior: {amount: {compute: vectors[0].expression}}};
    const parent: any = {name: 'order', fields: [{name: 'total', type: 'currency', scale: 2}], children: [{name: 'items', model: 'item'}], behavior: {total: {compute: vectors[1].expression}}, order: ['items.amount', 'total']};
    const result = calculateDocument(parent, [parent,row], {}, {items: [{_key: 'second', qty: '2', rate:'1.50'}, {_key: 'first', qty:'3', rate:'0.10'}]});
    expect(result.values.total).toBe('3.30');
    expect(result.children.items.map(row => row._key)).toEqual(['second','first']);
  });
});

const report: any = {name: 'example.Report', label: 'Example report', autorun: false, fields: [{name:'term', label:'Term', type:'text', default_kind:'static', default:'', required:false}], behavior:{term:{}}, children:[], columns:[{name:'name'}]};
const context: any = {models:[], nativePanels:[], dialogs:{error:vi.fn()}, notify:vi.fn()};
it('keeps manual filter evaluation separate from report execution', async () => {
  api.request.mockImplementation(async (url, options) => {
    const body = JSON.parse(options.body);
    if (url.endsWith('/evaluate')) return {values:{term:body.filters.term || '', children:{}},errors:{},states:{},revision:body.revision};
    return {rows:[{name:'Executed'}],total:1,offset:0,limit:50,filters:body.filters,revision:body.revision};
  });
  render(() => <AppContext.Provider value={context}><ReportView report={report}/></AppContext.Provider>);
  await waitFor(() => expect(screen.getByRole('button',{name:'Run'})).not.toBeDisabled());
  expect(api.request.mock.calls.every(([url]) => url.endsWith('/evaluate'))).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Run'}));
  await screen.findByText('Executed');
  fireEvent.input(screen.getByLabelText('Term'), {target:{value:'Changed'}});
  await screen.findByText('Inputs changed. Run again to update these results.');
  await waitFor(() => expect(screen.getByRole('button',{name:'Run'})).not.toBeDisabled());
  expect(api.request.mock.calls.filter(([url]) => !url.endsWith('/evaluate'))).toHaveLength(1);
});

it('autorun waits for valid evaluated inputs', async () => {
  api.request.mockImplementation(async (url, options) => {
    const body = JSON.parse(options.body);
    if (url.endsWith('/evaluate')) return {values:{term:body.filters.term || null,children:{}},errors:{},states:{},revision:body.revision};
    return {rows:[],total:0,offset:0,limit:50,filters:body.filters,revision:body.revision};
  });
  render(() => <AppContext.Provider value={context}><ReportView report={{...report, autorun:true, fields:[{...report.fields[0], required:true, default_kind:'none'}]}}/></AppContext.Provider>);
  await waitFor(() => expect(api.request).toHaveBeenCalled());
  expect(api.request.mock.calls.every(([url]) => url.endsWith('/evaluate'))).toBe(true);
  fireEvent.input(screen.getByLabelText(/^Term/), {target:{value:'Ready'}});
  await screen.findByText('No results.');
  expect(api.request.mock.calls.filter(([url]) => !url.endsWith('/evaluate'))).toHaveLength(1);
});
