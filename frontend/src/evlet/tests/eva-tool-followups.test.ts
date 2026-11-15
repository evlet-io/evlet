import { afterEach, expect, it, vi } from 'vitest';
import { adminRoutes, click, start, stopAll, typeInto, until, text } from './screens-helpers';
import { mount } from '../dom';
import { cell } from '../reactive';
import { EvaThread } from '../eva/chat';
import { follow } from '../eva/client';
const reply=(body:any,status=200)=>({body,status,etag:null});
const status:any={models:['Standard'],model:'Standard',choose_model:false,tasks:true,pending_approvals:0,allowance:{limit:'25.00',unit:'aed',period:'week',used:'0',resets:1,exhausted:false},files:{max_size:1048576,max_files:5,kinds:['.csv']}};
const conversation={id:7,title:'Audit chat',created_at:1,updated_at:1,archived:false,task_id:null,context:null};
const stored={conversation,messages:[],runs:[],files:[],usage:{tokens:0,cost:'0'}};
afterEach(()=>{stopAll();vi.restoreAllMocks()});

it('suppresses a polling response after the watcher is stopped', async () => {
  let finish!: (value: unknown) => void;
  const onEvent = vi.fn(), onEnd = vi.fn();
  const eva: any = { events: () => new Promise(resolve => { finish = resolve; }) };
  const stop = follow(eva, 4, onEvent, onEnd);
  stop();
  finish({ items: [{ id: 1, type: 'run.completed' }], status: 'completed', done: true });
  await Promise.resolve();
  expect(onEvent).not.toHaveBeenCalled();
  expect(onEnd).not.toHaveBeenCalled();
});

it('keeps files attached after an earlier message was submitted', async () => {
  let finish!: (value: unknown) => void;
  let nextFile = 0;
  const eva: any = {
    open: async () => stored, status: async () => status,
    send: () => new Promise(resolve => { finish = resolve; }),
    upload: async (name: string) => ({ id: ++nextFile, name, size: 4, kind: 'upload' }),
    events: () => new Promise(() => {}),
  };
  const ctx: any = { crumbs: cell([]), router: { href: () => '/eva' }, api: {} };
  const host = document.createElement('div'); document.body.append(host);
  const unmount = mount(host, () => EvaThread({ ctx, eva, status: cell(status), id: 7 }));
  try {
    await until(() => expect(host.querySelector('.eva-none')).toBeNull());
    const input = host.querySelector('input[type=file]') as HTMLInputElement;
    const attach = (name: string) => {
      Object.defineProperty(input, 'files', { configurable: true, value: [new File(['data'], name, { type: 'text/csv' })] });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    };
    attach('sent.csv');
    await until(() => expect(host.querySelectorAll('.eva-chip')).toHaveLength(1));
    typeInto(host.querySelector('textarea') as HTMLTextAreaElement, 'First message');
    click(host.querySelector('[aria-label="Send"]'));
    await until(() => expect(finish).toBeTypeOf('function'));
    attach('new-draft.csv');
    await until(() => expect(host.querySelectorAll('.eva-chip')).toHaveLength(2));
    finish({ run: { id: 4, status: 'running' }, message: { id: 9, run_id: 4, role: 'user', content: 'First message', created_at: 1, files: [] } });
    await until(() => expect(host.querySelectorAll('.eva-chip')).toHaveLength(1));
    expect(text(host.querySelector('.eva-chip'))).toContain('new-draft.csv');
  } finally { unmount(); host.remove(); }
});
it('preserves newer text written while an Eva send is pending',async()=>{
 let finish:any;
 const {host}=start([
  ['GET',/\/api\/_eva$/,reply(status)],
  ['GET',/\/api\/_eva\/conversations(\?.*)?$/,reply({items:[conversation],archived:0,next:null})],
  ['GET',/\/api\/_eva\/conversations\/7$/,reply(stored)],
  ['POST',/\/api\/_eva\/conversations\/7\/messages$/,()=>new Promise(resolve=>finish=resolve)],
  ['GET',/\/api\/_eva\/runs\/4\/events/,reply({items:[],status:'running',done:false})],
  ...adminRoutes()
 ],'/eva/7');
 await until(()=>expect(host.querySelector('.eva-compose textarea')).not.toBeNull());
 const area=host.querySelector('.eva-compose textarea') as HTMLTextAreaElement;
 typeInto(area,'First sent message');click(host.querySelector('.eva-send'));
 await until(()=>expect(finish).toBeTypeOf('function'));
 expect(area.disabled).toBe(false);
 typeInto(area,'A second draft composed during the request');
 finish(reply({run:{id:4,status:'running'},message:{id:9,run_id:4,role:'user',content:'First sent message',created_at:1,files:[]}},202));
 await until(()=>expect(host.querySelector('[aria-label="Stop"]')).not.toBeNull());
 expect(area.value).toBe('A second draft composed during the request');
});
it('loads the advertised next page of Eva conversations',async()=>{
 const conversations=Array.from({length:50},(_,n)=>({...conversation,id:n+1,title:`Chat ${n+1}`}));
 const {host,sent}=start([
 ['GET',/\/api\/_eva$/,reply(status)],
 ['GET',/\/api\/_eva\/conversations(\?.*)?$/,({url}:any)=>url.includes('before=')?reply({items:[{...conversation,id:51,title:'Chat 51'}],archived:0,next:null}):reply({items:conversations,archived:0,next:'1:1'})],...adminRoutes()
 ],'/eva');
 await until(()=>expect(host.querySelectorAll('.eva-conv')).toHaveLength(50));
 click([...host.querySelectorAll('button')].find(x=>x.textContent?.trim()==='Load more chats')!);
 await until(()=>expect(host.querySelectorAll('.eva-conv')).toHaveLength(51));
 expect(sent.filter(x=>x.url.includes('before='))).toHaveLength(1);
});
it('does not start polling after an Eva thread has been unmounted',async()=>{
 let finish:any;const events=vi.fn(async()=>({items:[],done:true,status:'completed'}));
 let opens=0;
 const eva:any={open:()=>++opens===1?new Promise(resolve=>finish=resolve):Promise.resolve(stored),events,status:async()=>status};
 const ctx:any={crumbs:cell([]),router:{href:()=>'/eva'},api:{}};
 const host=document.createElement('div');document.body.append(host);
 const unmount=mount(host,()=>EvaThread({ctx,eva,status:cell(status),id:7}));
 unmount();host.remove();
 finish({...stored,runs:[{id:4,status:'running',activities:[]}]});
 await Promise.resolve(); await Promise.resolve(); expect(events).not.toHaveBeenCalled();
});
it('preserves edits typed in a record note while the previous save is pending',async()=>{
 const { NoteComposer }=await import('../app/composer');
 let finish:any; const sent:string[]=[];
 const host=document.createElement('div');document.body.append(host);
 const unmount=mount(host,()=>NoteComposer({api:{addRichNote:(_m:any,_i:any,note:string)=>{sent.push(note);return new Promise(resolve=>finish=resolve)}} as any,model:'customer',id:'1',onAdded:()=>{},onError:()=>{}}));
 const area=host.querySelector('[contenteditable]') as HTMLElement;
 area.focus();
 const type=(value:string)=>area.dispatchEvent(new InputEvent('beforeinput',{inputType:'insertText',data:value,bubbles:true,cancelable:true}));
 type('Original note');click(host.querySelector('.cf button'));
 await until(()=>expect(finish).toBeTypeOf('function'));
 expect(area.getAttribute('contenteditable')).toBe('true');
 type(' + newer words');expect(area.textContent).toContain('newer words');
 finish({});
 await Promise.resolve(); await Promise.resolve(); expect(area.textContent).toContain('newer words');
 expect(sent).toEqual(['<p>Original note</p>']);
 unmount();host.remove();
});
it('does not start a notification stream after its bell has been unmounted',async()=>{
 const { InboxButton }=await import('../app/inbox');
 let finish:any;const streams:any[]=[];
 class FakeEventSource {static CLOSED=2;closed=false;constructor(public url:string){streams.push(this)}addEventListener(){}close(){this.closed=true}}
 vi.stubGlobal('EventSource',FakeEventSource);
 try {
 const ctx:any={api:{inboxCount:async()=>0,request:()=>new Promise(resolve=>finish=resolve),url:(url:string)=>url}};
 const host=document.createElement('div');document.body.append(host);
 const unmount=mount(host,()=>InboxButton(ctx));unmount();host.remove();
 finish({data:{cursor:'audit'}});
 await Promise.resolve(); await Promise.resolve(); expect(streams).toHaveLength(0);
 }finally{vi.unstubAllGlobals()}
});
it('preserves completed chat nodes and expanded details while another turn streams',async()=>{
 const sources:any[]=[];
 class FakeEventSource {handlers=new Map<string,Function>();constructor(){sources.push(this)}addEventListener(type:string,fn:Function){this.handlers.set(type,fn)}close(){}}
 vi.stubGlobal('EventSource',FakeEventSource);
 try {
 const completed={id:3,status:'completed',error:null,model:'Standard',origin:'eva',started_at:1,finished_at:2,activities:[{call_id:'old',tool:'list_records',label:'Read invoices',status:'done',summary:'2 records',inputs:{model:'invoice'},records:[]}]};
 const running={id:4,status:'running',error:null,model:'Standard',origin:'eva',started_at:3,finished_at:null,activities:[]};
 const data={...stored,runs:[completed,running],messages:[{id:1,run_id:3,role:'user',content:'Earlier request',created_at:1,files:[]},{id:2,run_id:3,role:'assistant',content:'Earlier answer',created_at:2,files:[]}]};
 const eva:any={open:async()=>data,api:{url:(x:string)=>x},status:async()=>status};
 const ctx:any={crumbs:cell([]),router:{href:()=>'/eva'},api:{}};
 const host=document.createElement('div');document.body.append(host);
 const unmount=mount(host,()=>EvaThread({ctx,eva,status:cell(status),id:7}));
 await until(()=>expect(host.querySelector('details.eva-act')).not.toBeNull());
 const before=host.querySelector('details.eva-act') as HTMLDetailsElement;before.open=true;
 sources[0].handlers.get('message.delta')({data:JSON.stringify({id:1,type:'message.delta',text:'A'})});
 const after=host.querySelector('details.eva-act') as HTMLDetailsElement;
 expect(after).toBe(before);expect(after.open).toBe(true);
 unmount();host.remove();
 }finally{vi.unstubAllGlobals()}
});
it('renders a newly sent message and streaming answer immediately',async()=>{
 const sources:any[]=[];
 class FakeEventSource {handlers=new Map<string,Function>();constructor(){sources.push(this)}addEventListener(type:string,fn:Function){this.handlers.set(type,fn)}close(){}}
 vi.stubGlobal('EventSource',FakeEventSource);
 try {
 const message={id:9,run_id:4,role:'user',content:'New request in existing chat',created_at:1,files:[]};
 const eva:any={open:async()=>stored,send:async()=>({run:{id:4,status:'running'},message}),api:{url:(x:string)=>x},status:async()=>status};
 const ctx:any={crumbs:cell([]),router:{href:()=>'/eva'},api:{}};
 const host=document.createElement('div');document.body.append(host);
 const unmount=mount(host,()=>EvaThread({ctx,eva,status:cell(status),id:7}));
 await until(()=>expect(host.querySelector('.eva-none')).toBeNull());
 typeInto(host.querySelector('textarea') as HTMLTextAreaElement,message.content);click(host.querySelector('[aria-label="Send"]'));
 await until(()=>expect(sources).toHaveLength(1));
 sources[0].handlers.get('message.delta')({data:JSON.stringify({id:1,type:'message.delta',text:'Streamed answer'})});
 expect(text(host.querySelector('.eva-msgs'))).toContain(message.content);
 expect(text(host.querySelector('.eva-msgs'))).toContain('Streamed answer');
 expect(host.querySelector('[aria-label="Stop"]')).not.toBeNull();
 unmount();host.remove();
 }finally{vi.unstubAllGlobals()}
});
