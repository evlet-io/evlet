
/* ---------- doc model: extra data ---------- */
const INDUSTRIES = ['Trading', 'Manufacturing', 'Logistics', 'Fit-out', 'Healthcare', 'Signage', 'FMCG', 'Courier', 'Construction', 'Auto parts', 'Hospitality', 'Retail', 'Aviation', 'Marine', 'Services'];
const EMIRATES = ['Dubai', 'Abu Dhabi', 'Sharjah', 'Ajman', 'Ras Al Khaimah', 'Fujairah', 'Umm Al Quwain', 'Jebel Ali', 'Oman'];
const EMPS = ['1-10', '11-50', '51-200', '201-500', '500+'];
const TIMELINES = ['This month', 'This quarter', 'Next quarter', 'Later'];
const PAY_TERMS = ['100% advance', '50% advance, 50% on go-live', '40/40/20 milestones', '30 days credit', '60 days credit'];
const CATALOG = [
  { n: 'ERPNext implementation', u: 'Entity', r: 42000, d: 'Setup, configuration and go-live support' },
  { n: 'Data migration', u: 'Nos', r: 18000, d: 'Masters, opening balances and open documents' },
  { n: 'User training', u: 'Day', r: 2500, d: 'Up to 8 users per batch' },
  { n: 'Custom report', u: 'Nos', r: 3000, d: 'Built to the agreed layout' },
  { n: 'Print format', u: 'Nos', r: 1500, d: 'Branded, bilingual where needed' },
  { n: 'Annual maintenance (AMC)', u: 'Year', r: 24000, d: 'Support, updates and minor changes' },
  { n: 'Cloud hosting', u: 'Year', r: 7200, d: 'Managed server with daily backups' },
  { n: 'E-invoicing integration', u: 'Nos', r: 12000, d: 'PINT-AE through an accredited provider' },
  { n: 'Module customization', u: 'Hour', r: 250, d: 'Scripts, workflows and form changes' },
];
function itemRow(item, desc, qty, uom, rate, disc = 0) { return { id: uid('r'), item, desc, qty, uom, rate, disc, vatp: 5 }; }
const ITEMS = {
  d1: [itemRow('ERPNext implementation', 'Al Sahra Trading LLC, Al Sahra FZE, Al Sahra Oman SPC', 3, 'Entity', 42000), itemRow('Data migration', 'Tally to ERPNext for all three entities', 1, 'Nos', 18000), itemRow('User training', 'Finance, stores and sales teams', 6, 'Day', 2500), itemRow('Custom report', 'Consolidated P&L and landed cost', 2, 'Nos', 3000), itemRow('Annual maintenance (AMC)', 'First year, billed at go-live', 1, 'Year', 24000, 12.5)],
  d9: [itemRow('ERPNext implementation', 'Contracting and MEP entities', 2, 'Entity', 42000), itemRow('Module customization', 'Progress billing, retention and IPC workflow', 162, 'Hour', 250), itemRow('User training', 'Site engineers, QS and accounts', 8, 'Day', 2500), itemRow('Data migration', 'Projects, BOQ and open retention', 1, 'Nos', 18000), itemRow('Print format', 'Payment certificate', 1, 'Nos', 1500)],
};
const BRANCHES = {
  o1: [{ id: uid('b'), b: 'Head office', em: 'Dubai', addr: 'Office 1204, Business Bay', ph: '+971 4 331 2290', trn: '100234567800003', prim: 'Yes' }, { id: uid('b'), b: 'JAFZA warehouse', em: 'Jebel Ali', addr: 'Plot S30512, Jebel Ali Free Zone South', ph: '+971 4 880 1120', trn: '100234567800003', prim: 'No' }, { id: uid('b'), b: 'Muscat branch', em: 'Oman', addr: 'Al Khuwair, Muscat', ph: '+968 2448 1190', trn: '', prim: 'No' }],
  o9: [{ id: uid('b'), b: 'Head office', em: 'Abu Dhabi', addr: 'Mussafah Industrial, M-26', ph: '+971 2 555 4410', trn: '100567812300003', prim: 'Yes' }, { id: uid('b'), b: 'Site office, Yas', em: 'Abu Dhabi', addr: 'Yas Island, Plot 14', ph: '+971 2 555 4499', trn: '100567812300003', prim: 'No' }],
};
const net = r => (+r.qty || 0) * (+r.rate || 0) * (1 - (+r.disc || 0) / 100);
const vatOf = r => net(r) * (+r.vatp || 0) / 100;
function syncDeal(d) { const rows = ITEMS[d.id]; if (rows && rows.length) d.v = Math.round(rows.reduce((s, r) => s + net(r), 0)); }
(function hydrate() {
  DEALS.forEach(d => { d.terms ??= d.v > 100000 ? '40/40/20 milestones' : '50% advance, 50% on go-live'; d.validity ??= 30; d.notes ??= ''; syncDeal(d); });
  const L = { l1: ['Manufacturing', '51-200', 'emiratesglass.ae', 90000, 'This quarter', 'Runs production and costing on spreadsheets. Wants job costing per order and stock by location across 3 factories.'], l3: ['Marine', '201-500', 'coastlinemarine.ae', 220000, 'This month', 'Fleet maintenance, spares inventory and project billing for vessel refits.'], l6: ['Aviation', '51-200', 'skybridgeaviation.ae', 120000, 'Next quarter', 'Ground handling contracts, monthly billing and multi-currency.'] };
  LEADS.forEach(l => { const x = L[l.id] || []; l.ind ??= x[0] || ''; l.emp ??= x[1] || ''; l.web ??= x[2] || ''; l.budget ??= x[3] || 0; l.tl ??= x[4] || ''; l.need ??= x[5] || ''; l.tags ??= l.sc >= 70 ? ['Hot'] : []; });
  CONTACTS.forEach(c => { c.dept ??= /Finance|CFO/.test(c.t) ? 'Finance' : /IT/.test(c.t) ? 'IT' : /Procure|Purchase|Supply/.test(c.t) ? 'Procurement' : 'Management'; c.wa ??= c.p; c.pref ??= c.id === 'c1' ? 'WhatsApp' : 'Email'; c.rep ??= ''; c.tags ??= []; });
  const won = new Set(DEALS.filter(d => d.s === 'won').map(d => d.org));
  ORGS.forEach(o => { o.trn ??= ''; o.emp ??= o.id === 'o1' ? '201-500' : '51-200'; o.credit ??= won.has(o.id) ? 50000 : 0; o.terms ??= won.has(o.id) ? '30 days credit' : '100% advance'; o.rel ??= o.id === 'o1' ? 'Key account' : won.has(o.id) ? 'Customer' : 'Prospect'; o.tags ??= []; o.created ??= '2026-08-12'; });
  ORGS[0].trn = '100234567800003'; ORGS[8].trn = '100567812300003';
  CONTACTS.forEach(c => c.created ??= '2026-09-01');
  ACTS.push(
    { id: 'a20', ref: 'lead:l1', ty: 'email', by: 'u1', at: '2026-11-01T10:40', subj: 'Intro and next steps', text: 'Hi Rashid,\n\nThanks for the time this morning. Sharing two short case studies from glass and aluminium fabricators. Can we book a 45 minute walkthrough on Tuesday?' },
    { id: 'a21', ref: 'lead:l1', ty: 'call', by: 'u1', at: '2026-11-01T10:05', text: 'Three factories, about 140 staff. Evaluating two vendors, decision before year end. Finance manager joins the next call.' },
    { id: 'a22', ref: 'lead:l3', ty: 'meeting', by: 'u3', at: '2026-10-31T15:00', text: 'Site visit at Mina Rashid yard. Strong fit. Asked for a proposal by mid November.' },
    { id: 'a23', ref: 'org:o1', ty: 'note', by: 'u1', at: '2026-10-20T12:15', text: 'Group CFO prefers quarterly billing for AMC across entities.' },
  );
  TASKS.push({ id: 't11', t: 'Book walkthrough with Rashid', ty: 'meeting', due: '2026-11-03', tm: '11:00', deal: '', ref: 'lead:l1', o: ME, done: false });
})();

/* ---------- doc types ---------- */
const DT = {
  deal: { label: 'Deal', list: 'deals', ic: 'handshake', c: 'blue', arr: () => DEALS, get: id => deal(id), title: d => d.t, tk: 't' },
  lead: { label: 'Lead', list: 'leads', ic: 'target', c: 'teal', arr: () => LEADS, get: id => byId(LEADS, id), title: d => d.n, tk: 'n' },
  contact: { label: 'Contact', list: 'contacts', ic: 'users', c: 'violet', arr: () => CONTACTS, get: id => contact(id), title: d => d.n, tk: 'n' },
  org: { label: 'Organization', list: 'orgs', ic: 'building-2', c: 'amber', arr: () => ORGS, get: id => org(id), title: d => d.n, tk: 'n' },
};
const F = {
  deal: { t: ['Deal name', 'text'], org: ['Organization', 'org'], ct: ['Primary contact', 'contact'], o: ['Owner', 'user'], src: ['Source', 'sel', SOURCES], s: ['Stage', 'stage'], v: ['Amount', 'cur'], pr: ['Probability', 'pct'], close: ['Expected close', 'date'], terms: ['Payment terms', 'sel', PAY_TERMS], validity: ['Quote valid for', 'num', null, ' days'], notes: ['Internal notes', 'area'], created: ['Created', 'date', null, null, 1] },
  lead: { n: ['Full name', 'text'], t: ['Job title', 'text'], e: ['Email', 'email'], p: ['Mobile', 'phone'], co: ['Company', 'text'], ind: ['Industry', 'sel', INDUSTRIES], emp: ['Employees', 'sel', EMPS], web: ['Website', 'text'], st: ['Status', 'leadst'], sc: ['Score', 'num'], src: ['Source', 'sel', SOURCES], o: ['Owner', 'user'], budget: ['Budget', 'cur'], tl: ['Buying timeline', 'sel', TIMELINES], need: ['What they need', 'area'], created: ['Created', 'date', null, null, 1] },
  contact: { n: ['Full name', 'text'], t: ['Job title', 'text'], dept: ['Department', 'text'], rep: ['Reports to', 'text'], e: ['Email', 'email'], p: ['Mobile', 'phone'], wa: ['WhatsApp', 'phone'], pref: ['Preferred channel', 'sel', ['Email', 'Phone', 'WhatsApp']], org: ['Organization', 'org'], created: ['Created', 'date', null, null, 1] },
  org: { n: ['Name', 'text'], ind: ['Industry', 'sel', INDUSTRIES], loc: ['Location', 'sel', EMIRATES], web: ['Website', 'text'], trn: ['TRN', 'text'], emp: ['Employees', 'sel', EMPS], rel: ['Relationship', 'sel', ['Prospect', 'Customer', 'Key account']], credit: ['Credit limit', 'cur'], terms: ['Payment terms', 'sel', PAY_TERMS], created: ['Created', 'date', null, null, 1] },
};
const SECTIONS = {
  deal: [['Overview', 'Who, what and where it stands', ['t', 'org', 'ct', 'o', 'src', 's']], ['Commercials', 'Value and terms. Amount follows the items table when it has lines.', ['v', 'pr', 'terms', 'validity'], 'items'], ['Schedule and notes', '', ['close', 'created', 'notes']]],
  lead: [['Person', '', ['n', 't', 'e', 'p']], ['Company', '', ['co', 'ind', 'emp', 'web']], ['Qualification', 'What decides whether this becomes a deal', ['st', 'sc', 'src', 'o', 'budget', 'tl', 'need']]],
  contact: [['Person', '', ['n', 't', 'dept', 'rep']], ['Communication', '', ['e', 'p', 'wa', 'pref']], ['Work', '', ['org', 'created']]],
  org: [['Company', '', ['n', 'ind', 'loc', 'web', 'trn', 'emp']], ['Commercial', 'Applied to new quotations and invoices', ['rel', 'credit', 'terms']], ['Branches', 'Addresses used on documents', [], 'branches']],
};
const KEYF = { deal: ['o', 's', 'v', 'pr', 'close', 'src'], lead: ['st', 'sc', 'o', 'src', 'budget', 'tl'], contact: ['e', 'p', 'org', 'pref'], org: ['rel', 'ind', 'loc', 'credit'] };
const FLOWS = {
  deal: { steps: d => d.s === 'lost' ? [...STAGES.slice(0, 4), LOST].map(s => [s.k, s.n]) : STAGES.map(s => [s.k, s.n]), cur: d => d.s },
  lead: { steps: l => (l.st === 'Unqualified' ? ['New', 'Contacted', 'Unqualified'] : ['New', 'Contacted', 'Qualified', 'Converted']).map(s => [s, s]), cur: l => l.st },
  org: { steps: () => ['Prospect', 'Customer', 'Key account'].map(s => [s, s]), cur: o => o.rel },
};

/* ---------- blocks and template ---------- */
const BLOCKS = [
  { k: 'header', n: 'Header', g: 'Main', lock: 1 },
  { k: 'flow', n: 'Status flow', g: 'Main', only: ['deal', 'lead', 'org'] },
  { k: 'summary', n: 'Summary strip', g: 'Main' },
  { k: 'composer', n: 'Activity composer', g: 'Main' },
  { k: 't_details', n: 'Details tab', g: 'Tabs' },
  { k: 'child', n: 'Child table tab', g: 'Tabs', only: ['deal', 'org'] },
  { k: 't_emails', n: 'Emails tab', g: 'Tabs' },
  { k: 't_tasks', n: 'Tasks tab', g: 'Tabs' },
  { k: 't_notes', n: 'Notes tab', g: 'Tabs' },
  { k: 't_files', n: 'Files tab', g: 'Tabs' },
  { k: 'side', n: 'Side panel', g: 'Side panel' },
  { k: 'next', n: 'Next step', g: 'Side panel' },
  { k: 'key', n: 'Key fields', g: 'Side panel' },
  { k: 'highlight', n: 'Highlight card', g: 'Side panel', only: ['lead', 'contact'] },
  { k: 'people', n: 'People', g: 'Side panel' },
  { k: 'related', n: 'Related records', g: 'Side panel' },
  { k: 'tags', n: 'Tags', g: 'Side panel' },
  { k: 'info', n: 'Record info', g: 'Side panel' },
];
const LAYOUT_SAMPLE = { dt: 'deal', id: 'd1' };
const onLayout = () => S.route === 'layout';
const cur = () => onLayout() ? LAYOUT_SAMPLE : { dt: S.route, id: S.param };
const applies = (b, dt) => !b.only || b.only.includes(dt) || dt === 'any';
const TPL_DEFAULT = () => Object.fromEntries(BLOCKS.map(b => [b.k, true]));
let TPL = { ...TPL_DEFAULT(), ...store.get('docTpl2', {}) };
let UP = store.get('docPrefs.' + ME, {});
const saveTpl = () => store.set('docTpl2', TPL), saveUP = () => store.set('docPrefs.' + ME, UP);
function blockOn(dt, k) { const b = BLOCKS.find(x => x.k === k); if (!b || !applies(b, dt)) return false; if (b.lock) return true; if (onLayout()) return TPL[k]; return UP[dt]?.blocks?.[k] ?? TPL[k]; }
const docPref = (dt, k, def) => onLayout() ? def : UP[dt]?.[k] ?? def;

/* ---------- activity and task lookups ---------- */
function actsFor(dt, d) {
  const mine = a => a.ref === `${dt}:${d.id}`;
  let f;
  if (dt === 'deal') f = a => a.deal === d.id || mine(a);
  else if (dt === 'lead') f = mine;
  else if (dt === 'contact') f = a => mine(a) || a.by === d.id || (a.deal && deal(a.deal)?.ct === d.id);
  else f = a => mine(a) || (a.deal && deal(a.deal)?.org === d.id);
  return ACTS.filter(f).sort((a, b) => b.at.localeCompare(a.at));
}
function tasksFor(dt, d) {
  const mine = t => t.ref === `${dt}:${d.id}`;
  if (dt === 'deal') return TASKS.filter(t => t.deal === d.id || mine(t));
  if (dt === 'lead') return TASKS.filter(mine);
  if (dt === 'contact') return TASKS.filter(t => mine(t) || (t.deal && deal(t.deal)?.ct === d.id));
  return TASKS.filter(t => mine(t) || (t.deal && deal(t.deal)?.org === d.id));
}
function actTarget(a) {
  if (a.deal) { const d = deal(a.deal); return d ? { t: d.t, r: 'deal-' + d.id } : null; }
  if (a.ref) { const [dt, id] = a.ref.split(':'), d = DT[dt]?.get(id); return d ? { t: DT[dt].title(d), r: `${dt}-${id}` } : null; }
  return null;
}
const refRoute = t => t.deal ? 'deal-' + t.deal : t.ref ? t.ref.replace(':', '-') : null;
const refTitle = t => { const r = refRoute(t); if (!r) return ''; const [dt, id] = splitRoute(r); return DT[dt] ? DT[dt].title(DT[dt].get(id)) : ''; };

/* ---------- field display and edit ---------- */
const notSet = '<span class="muted">Not set</span>';
function fieldDisplay(dt, d, k) {
  const [, t, , suf] = F[dt][k], v = d[k];
  switch (t) {
    case 'user': return v ? `${avatar(v, 'sm')}<span class="tx">${USERS[v].n}</span>` : notSet;
    case 'stage': return stagePill(v);
    case 'leadst': return `<span class="pill b-${LEAD_ST[v]}"><span class="d"></span>${v}</span>`;
    case 'org': { const o = org(v); return o ? `${orgMark(o)}<span class="tx">${esc(o.n)}</span>` : notSet; }
    case 'contact': { const c = contact(v); return c ? `${avatar(c.id, 'sm')}<span class="tx">${esc(c.n)}</span>` : notSet; }
    case 'cur': return v ? `<span class="num tx">${money(v)}</span>` : notSet;
    case 'pct': return `<span class="num tx">${v ?? 0}%</span>`;
    case 'date': return v ? `<span class="num tx">${fmtDate(v)}</span>` : notSet;
    case 'num': return v || v === 0 ? `<span class="num tx">${v}${suf || ''}</span>` : notSet;
    case 'area': return v ? `<span class="tx area">${esc(v)}</span>` : notSet;
    default: return v ? `<span class="tx">${esc(v)}</span>` : notSet;
  }
}
const fieldRO = (dt, d, k) => F[dt][k][4] || !!F[dt][k][5] || (dt === 'deal' && k === 'v' && ITEMS[d.id]?.length > 0) || (typeof extraRO === 'function' && extraRO(dt, d, k));
function fieldInput(dt, d, k) {
  const [, t, o] = F[dt][k], v = d[k] ?? '';
  const opt = (arr, cur) => arr.map(([val, lbl]) => `<option value="${esc(val)}" ${String(val) === String(cur) ? 'selected' : ''}>${esc(lbl)}</option>`).join('');
  switch (t) {
    case 'user': return `<select class="input" id="fe">${opt(Object.entries(USERS).map(([k2, u]) => [k2, u.n]), v)}</select>`;
    case 'stage': return `<select class="input" id="fe">${opt([...STAGES, LOST].map(s => [s.k, s.n]), v)}</select>`;
    case 'leadst': return `<select class="input" id="fe">${opt(['New', 'Contacted', 'Qualified', 'Unqualified'].map(s => [s, s]), v)}</select>`;
    case 'org': return `<select class="input" id="fe">${opt(ORGS.map(x => [x.id, x.n]), v)}</select>`;
    case 'contact': { const cs = CONTACTS.filter(c => c.org === (d.org ?? d.customer)); return `<select class="input" id="fe">${opt([['', 'None'], ...cs.map(c => [c.id, c.n])], v)}</select>`; }
    case 'sel': return `<select class="input" id="fe">${opt([['', 'Not set'], ...o.map(x => [x, x])], v)}</select>`;
    case 'date': return `<input class="input" id="fe" type="date" value="${esc(v)}">`;
    case 'cur': case 'num': return `<input class="input num" id="fe" type="number" min="0" value="${esc(v)}">`;
    case 'pct': return `<input class="input num" id="fe" type="number" min="0" max="100" value="${esc(v)}">`;
    case 'area': return `<textarea class="input" id="fe" rows="3">${esc(v)}</textarea>`;
    case 'email': return `<input class="input" id="fe" type="email" value="${esc(v)}">`;
    default: return `<input class="input" id="fe" value="${esc(v)}">`;
  }
}
function setField(dt, d, k, raw) {
  const t = F[dt][k][1];
  let v = raw;
  if (['cur', 'num', 'pct'].includes(t)) { v = Math.max(0, +raw || 0); if (t === 'pct') v = Math.min(100, v); }
  if (String(v) === String(d[k] ?? '')) return false;
  if (dt === 'deal' && k === 's') { moveDeal(d.id, v); return 'moved'; }
  if (dt === 'lead' && k === 'st') { setLeadStatus(d, v); return 'moved'; }
  if (t !== 'area' && t !== 'sel' && v === '' && k === DT[dt].tk) return false;
  d[k] = v;
  if (dt === 'deal' && k === 'org') d.ct = CONTACTS.find(c => c.org === v)?.id || '';
  return true;
}
function docEdit(btn) {
  const dt = btn.dataset.dt, k = btn.dataset.k, d = DT[dt].get(cur().id);
  const host = btn.parentElement;
  host.classList.add('editing');
  host.innerHTML = fieldInput(dt, d, k);
  const el = $('#fe', host); el.focus(); el.select && el.type !== 'date' && el.select();
  let done = false;
  const commit = save => {
    if (done) return; done = true;
    if (save) { const r = setField(dt, d, k, el.value); if (r === 'moved') return; if (r) toast('Saved'); }
    render();
  };
  el.addEventListener('blur', () => commit(true));
  if (el.tagName === 'SELECT') el.addEventListener('change', () => commit(true));
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (el.tagName !== 'TEXTAREA' || e.metaKey || e.ctrlKey)) { e.preventDefault(); commit(true); }
    if (e.key === 'Escape') { e.stopPropagation(); commit(false); }
  });
}
function setLeadStatus(l, v) {
  if (v === 'Converted') { A.convertLead({ dataset: { id: l.id } }); return; }
  const prev = l.st; if (prev === v) return;
  l.st = v; logAct({ ref: 'lead:' + l.id, ty: 'stage', from: prev, to: v, lead: true }); render();
  toast(`Status set to ${v}`, { undo: () => { l.st = prev; ACTS.pop(); render(); } });
}

/* ---------- doc view ---------- */
['deal', 'lead', 'contact', 'org'].forEach(dt => VIEWS[dt] = () => docView(dt));
function docTabs(dt, d) {
  const acts = actsFor(dt, d), tasks = tasksFor(dt, d);
  const all = [
    ['activity', 'Activity', acts.length, 1],
    ['details', 'Details', 0, blockOn(dt, 't_details')],
    ['items', 'Items', (ITEMS[d.id] || []).length, blockOn(dt, 'child') && dt === 'deal'],
    ['branches', 'Branches', (BRANCHES[d.id] || []).length, blockOn(dt, 'child') && dt === 'org'],
    ['emails', 'Emails', acts.filter(a => a.ty === 'email').length, blockOn(dt, 't_emails')],
    ['tasks', 'Tasks', tasks.filter(t => !t.done).length, blockOn(dt, 't_tasks')],
    ['notes', 'Notes', acts.filter(a => a.ty === 'note').length, blockOn(dt, 't_notes')],
    ['files', 'Files', (FILES[d.id] || []).length, blockOn(dt, 't_files')],
  ];
  return all.filter(x => x[3]);
}
function docView(dt) {
  const T = DT[dt], d = T.get(cur().id);
  if (!d) return emptyState(T.ic, T.c, `${T.label} not found`, 'It may have been deleted.');
  const tabs = docTabs(dt, d);
  if (!tabs.some(t => t[0] === S.recTab)) S.recTab = 'activity';
  const side = blockOn(dt, 'side'), dens = docPref(dt, 'dens', 'comfortable'), pos = docPref(dt, 'pos', 'right');
  return `<div class="doc doc-${dt} ${dens === 'compact' ? 'compact' : ''} ${side ? '' : 'no-side'} ${pos === 'left' ? 'side-left' : ''}" style="--acc:var(--t-${T.c});--acc-bg:var(--t-${T.c}-bg)">
    <div class="doc-main">
      ${docHeader(dt, d)}
      ${blockOn(dt, 'flow') ? docFlow(dt, d) : ''}
      ${blockOn(dt, 'summary') ? docSummary(dt, d) : ''}
      <div class="tabs doc-tabs">${tabs.map(([k, n, ct]) => `<button class="${S.recTab === k ? 'on' : ''}" data-a="recTab" data-v="${k}">${n}${ct ? ` <span class="n num">${ct}</span>` : ''}</button>`).join('')}</div>
      <div class="doc-body">${docBody(dt, d)}</div>
    </div>
    ${side ? `<aside class="doc-side">${docSide(dt, d)}</aside>` : ''}
  </div>`;
}

function docHeader(dt, d) {
  const T = DT[dt], c = d.ct ? contact(d.ct) : null;
  let mark, meta, acts;
  const btn = (a, ic, cls, label, attrs = '') => `<button class="btn ${cls}" data-a="${a}" ${attrs}>${icon(ic, 'sm' + (cls ? '' : ' ' + ({ mail: 'c-blue', phone: 'c-green', 'message-circle': 'c-green', globe: 'c-amber' }[ic] || 'tint')))}<span class="hide-sm">${label}</span></button>`;
  const more = `<button class="btn icon" data-a="docMore" aria-label="More actions">${icon('ellipsis', 'sm')}</button>`;
  if (dt === 'deal') {
    const o = org(d.org);
    mark = orgMark(o, 'lg');
    meta = `<span><b class="num">${money(d.v)}</b></span><span>${icon('building-2', 'xs')}<a class="link-q" data-a="go" data-r="org-${o.id}">${esc(o.n)}</a></span><span class="${dayDiff(d.close) < 0 && isOpen(d) ? 'due-over' : ''}">${icon('calendar', 'xs')}Close ${fmtDate(d.close)}</span><span>${avatar(d.o, 'sm')}${USERS[d.o].n}</span>`;
    acts = btn('copy', 'mail', '', 'Email', `data-v="${esc(c?.e || '')}"`) + btn('copy', 'phone', '', 'Call', `data-v="${esc(c?.p || '')}"`) + (isOpen(d) ? `<button class="btn success" data-a="markWon">${icon('trophy', 'sm')}Mark won</button>` : `<button class="btn" data-a="reopen">${icon('repeat', 'sm tint')}Reopen</button>`) + more;
  } else if (dt === 'lead') {
    mark = `<span class="avatar xl b-${d.c}">${initials(d.n)}</span>`;
    meta = `<span>${esc(d.t)} at <b>${esc(d.co)}</b></span><span>${score(d.sc)}</span><span>${icon('sparkles', 'xs')}${d.src}</span><span>${avatar(d.o, 'sm')}${USERS[d.o].n}</span>`;
    acts = btn('copy', 'mail', '', 'Email', `data-v="${esc(d.e)}"`) + btn('copy', 'phone', '', 'Call', `data-v="${esc(d.p)}"`) + (d.st !== 'Converted' ? `<button class="btn primary" data-a="convertLead" data-id="${d.id}">${icon('arrow-right', 'sm')}Convert to deal</button>` : `<span class="pill b-violet">Converted</span>`) + more;
  } else if (dt === 'contact') {
    const o = org(d.org);
    mark = avatar(d.id, 'xl');
    meta = `<span>${esc(d.t)}</span><span>${icon('building-2', 'xs')}<a class="link-q" data-a="go" data-r="org-${o.id}">${esc(o.n)}</a></span><span>${icon('mail', 'xs')}${esc(d.e)}</span><span class="num">${icon('phone', 'xs')}${esc(d.p)}</span>`;
    acts = btn('copy', 'mail', '', 'Email', `data-v="${esc(d.e)}"`) + btn('copy', 'phone', '', 'Call', `data-v="${esc(d.p)}"`) + btn('copy', 'message-circle', '', 'WhatsApp', `data-v="${esc(d.wa)}"`) + `<button class="btn primary" data-a="create" data-t="deal" data-org="${esc(o.n)}">${icon('plus', 'sm')}<span class="hide-sm">New deal</span></button>` + more;
  } else {
    mark = orgMark(d, 'lg');
    meta = `<span>${d.ind}</span><span>${icon('map-pin', 'xs')}${d.loc}</span>${d.web ? `<span>${icon('globe', 'xs')}${esc(d.web)}</span>` : ''}${d.trn ? `<span class="num">TRN ${d.trn}</span>` : ''}`;
    acts = btn('create', 'user-plus', '', 'Contact', 'data-t="contact"') + `<button class="btn primary" data-a="create" data-t="deal" data-org="${esc(d.n)}">${icon('plus', 'sm')}<span class="hide-sm">New deal</span></button>` + more;
  }
  return `<header class="doc-head">
    <div class="doc-mark">${mark}</div>
    <div class="ttl"><div class="eyebrow">${icon(T.ic, 'xs')}${T.label}</div><h1 contenteditable="true" spellcheck="false" id="recTitle" data-dt="${dt}" data-id="${d.id}">${esc(T.title(d))}</h1><div class="rec-meta">${meta}</div></div>
    <div class="ph-actions">${acts}</div></header>`;
}
function docFlow(dt, d) {
  const f = FLOWS[dt], steps = f.steps(d), ci = steps.findIndex(s => s[0] === f.cur(d));
  return `<div class="stepper" role="list">${steps.map(([k, n], i) => `<button class="step ${i < ci ? 'done' : ''} ${i === ci ? 'cur' : ''} ${k === 'won' || k === 'Converted' || k === 'paid' ? 'won' : ''} ${k === 'lost' || k === 'Unqualified' || k === 'void' ? 'lost' : ''}" data-a="flowSet" data-s="${k}" title="Set to ${n}">${i < ci ? icon('check', 'xs') : ''}${n}</button>`).join('')}</div>`;
}
function docSummary(dt, d) {
  const acts = actsFor(dt, d), last = acts[0];
  const tile = (l, v, sub = '') => `<div class="sum-t"><span>${l}</span><b class="num">${v}</b>${sub ? `<em>${sub}</em>` : ''}</div>`;
  let t;
  if (dt === 'deal') {
    const st = acts.find(a => a.ty === 'stage' || a.ty === 'created'), days = st ? -dayDiff(st.at.slice(0, 10)) : 0;
    t = [tile('Amount', money(d.v), ITEMS[d.id]?.length ? `${ITEMS[d.id].length} items` : 'Manual'), tile('Weighted', money(d.v * d.pr / 100), `${d.pr}% probability`), tile('Days in stage', days, stageOf(d.s).n), tile('Closes', relDue(d.close), fmtDate(d.close))];
  } else if (dt === 'lead') {
    t = [tile('Score', d.sc, d.sc >= 70 ? 'Hot' : d.sc >= 45 ? 'Warm' : 'Cold'), tile('Budget', d.budget ? money(d.budget) : 'Not set', d.tl || ''), tile('Age', `${-dayDiff(d.created)} days`, fmtDate(d.created)), tile('Last touch', last ? relTime(last.at) : 'None', last ? last.ty : '')];
  } else if (dt === 'contact') {
    const ds = DEALS.filter(x => x.ct === d.id);
    t = [tile('Open deals', ds.filter(isOpen).length), tile('Open pipeline', money(ds.filter(isOpen).reduce((s, x) => s + x.v, 0))), tile('Won', money(ds.filter(x => x.s === 'won').reduce((s, x) => s + x.v, 0))), tile('Last touch', last ? relTime(last.at) : 'None', last ? last.ty : '')];
  } else {
    const ds = DEALS.filter(x => x.org === d.id);
    t = [tile('Open pipeline', money(ds.filter(isOpen).reduce((s, x) => s + x.v, 0)), `${ds.filter(isOpen).length} open ${ds.filter(isOpen).length === 1 ? 'deal' : 'deals'}`), tile('Won to date', money(ds.filter(x => x.s === 'won').reduce((s, x) => s + x.v, 0))), tile('People', CONTACTS.filter(c => c.org === d.id).length), tile('Credit limit', d.credit ? money(d.credit) : 'None', d.terms)];
  }
  return `<div class="sum">${t.join('')}</div>`;
}
function docBody(dt, d) {
  const acts = actsFor(dt, d), tasks = tasksFor(dt, d), files = FILES[d.id] || [];
  const comp = force => blockOn(dt, 'composer') || force ? composer(force) : '';
  switch (S.recTab) {
    case 'details': return docForm(dt, d);
    case 'items': return gridBlock('deal-items', d);
    case 'branches': return gridBlock('org-branches', d);
    case 'emails': { const e = acts.filter(a => a.ty === 'email'); return comp('email') + (e.length ? timeline(e) : emptyState('mail', 'blue', 'No emails yet', 'Emails sent or received show up here.')); }
    case 'notes': { const n = acts.filter(a => a.ty === 'note'); return comp('note') + (n.length ? timeline(n) : emptyState('sticky-note', 'amber', 'No notes yet', 'Write the first note above.')); }
    case 'tasks': return comp('task') + (tasks.length ? `<section class="panel">${tasks.map(taskRow).join('')}</section>` : emptyState('circle-check', 'teal', 'No tasks', 'Add a follow-up so nothing slips.'));
    case 'files': return `<div style="display:flex;justify-content:flex-end;margin-bottom:12px"><label class="btn">${icon('upload', 'sm tint')}Upload file<input type="file" id="fileUp" hidden></label></div>` + (files.length ? `<div class="files">${files.map(f => `<div class="file"><span class="ext b-${f.c}">${f.ext}</span><div style="flex:1;min-width:0"><div style="font-weight:550;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(f.n)}</div><div class="muted" style="font-size:12px">${f.s} · ${esc(personName(f.by))} · ${f.at}</div></div><button class="btn ghost sm icon" data-a="fakeDl" data-n="${esc(f.n)}" aria-label="Download">${icon('download', 'sm')}</button></div>`).join('')}</div>` : emptyState('paperclip', 'slate', 'No files', 'Proposals, scope sheets and signed copies live here.'));
    default: return comp() + (acts.length ? timeline(acts) : emptyState('activity', 'slate', 'No activity yet', 'Calls, emails and notes for this record land here.'));
  }
}
function fieldCell(dt, d, k, cls = '') {
  const [l] = F[dt][k], ro = fieldRO(dt, d, k), full = F[dt][k][1] === 'area';
  return `<div class="ff ${full ? 'full' : ''} ${cls}"><label>${l}${fieldHint(dt, d, k, ro)}</label><div class="ff-v">${ro ? `<div class="fval ro">${fieldDisplay(dt, d, k)}</div>` : `<button class="fval" data-a="docEdit" data-dt="${dt}" data-k="${k}">${fieldDisplay(dt, d, k)}${icon('pencil', 'xs pen')}</button>`}</div></div>`;
}
function docForm(dt, d) {
  return SECTIONS[dt].map(([h, sub, keys, grid]) => {
    let extra = '';
    if (grid === 'items' && blockOn(dt, 'child')) {
      const rows = ITEMS[d.id] || [], n = rows.reduce((s, r) => s + net(r), 0), v = rows.reduce((s, r) => s + vatOf(r), 0);
      extra = `<button class="fs-link" data-a="recTab" data-v="items"><span class="ph-icon b-blue" style="width:30px;height:30px;border-radius:8px">${icon('package', 'sm')}</span><span class="grow"><b>${rows.length ? `${rows.length} items` : 'No items yet'}</b><span class="muted">${rows.length ? `Net ${money(n)} · VAT ${money(v)} · Total ${money(n + v)}` : 'Add line items to build the quote'}</span></span>${icon('chevron-right', 'sm muted')}</button>`;
    }
    if (grid === 'branches') extra = blockOn(dt, 'child') ? gridBlock('org-branches', d, true) : '';
    if (!keys.length && !extra) return '';
    return `<section class="fs"><div class="fs-h"><h3>${h}</h3>${sub ? `<p>${sub}</p>` : ''}</div>${keys.length ? `<div class="fs-g">${keys.map(k => fieldCell(dt, d, k)).join('')}</div>` : ''}${extra}</section>`;
  }).join('');
}

function sideSec(title, body, extra = '') { return `<div class="side-sec"><h4>${title}${extra}</h4>${body}</div>`; }
function docSide(dt, d) {
  const out = [];
  const tasks = tasksFor(dt, d).filter(t => !t.done).sort((a, b) => a.due.localeCompare(b.due));
  if (blockOn(dt, 'next')) out.push(sideSec('Next step', tasks[0] ? taskCard(tasks[0]) : `<button class="mini-card dashed" data-a="recTab" data-v="tasks">${icon('plus', 'sm muted')}<span class="muted">Plan the next step</span></button>`));
  if (dt === 'lead' && blockOn(dt, 'highlight')) {
    const fit = Math.min(100, (d.ind ? 35 : 10) + (EMPS.indexOf(d.emp) + 1) * 13), eng = Math.min(100, actsFor(dt, d).length * 30 + 10), intent = Math.min(100, (d.budget ? 45 : 5) + (TIMELINES.length - Math.max(0, TIMELINES.indexOf(d.tl))) * 12);
    const bar = (l, v) => `<div class="sc-row"><span>${l}</span><div class="bar"><span style="width:${v}%"></span></div><b class="num">${v}</b></div>`;
    out.push(sideSec('Scorecard', `<div class="scorecard"><div class="ring" style="--p:${d.sc}"><b class="num">${d.sc}</b><span>score</span></div><div class="sc-rows">${bar('Fit', fit)}${bar('Engagement', eng)}${bar('Intent', intent)}</div></div>`));
  }
  if (dt === 'contact' && blockOn(dt, 'highlight')) {
    const ch = (ic, c, l, v) => `<div class="ch-row"><span class="ph-icon b-${c}" style="width:28px;height:28px;border-radius:8px">${icon(ic, 'sm')}</span><div class="grow" style="min-width:0"><div class="muted" style="font-size:12px">${l}${d.pref === l ? ' <span class="chip" style="height:17px">Preferred</span>' : ''}</div><div class="ch-v">${esc(v)}</div></div><button class="icon-btn" data-a="copy" data-v="${esc(v)}" data-tip="Copy">${icon('copy', 'sm')}</button></div>`;
    out.push(sideSec('Channels', ch('mail', 'blue', 'Email', d.e) + ch('phone', 'green', 'Phone', d.p) + ch('message-circle', 'teal', 'WhatsApp', d.wa)));
  }
  if (blockOn(dt, 'key')) out.push(sideSec('Key fields', `<dl class="fields">${KEYF[dt].map(k => `<dt>${F[dt][k][0]}</dt><dd>${fieldRO(dt, d, k) ? `<div class="fval ro">${fieldDisplay(dt, d, k)}</div>` : `<button class="fval" data-a="docEdit" data-dt="${dt}" data-k="${k}">${fieldDisplay(dt, d, k)}${icon('pencil', 'xs pen')}</button>`}</dd>`).join('')}</dl>`, `<button class="h-link" data-a="recTab" data-v="details">All fields</button>`));
  if (blockOn(dt, 'people')) {
    const ppl = dt === 'lead' ? [] : CONTACTS.filter(x => x.org === (dt === 'org' ? d.id : d.org) && x.id !== (dt === 'contact' ? d.id : null));
    const card = x => `<div class="mini-card" style="cursor:pointer" data-a="peekContact" data-id="${x.id}">${avatar(x.id)}<div class="grow"><div class="nm">${esc(x.n)}${dt === 'deal' && x.id === d.ct ? ' <span class="chip">Primary</span>' : ''}</div><div class="sub">${esc(x.t)}</div></div><div class="acts"><button class="icon-btn" style="width:26px;height:26px" data-a="copy" data-v="${esc(x.e)}" data-tip="Copy email">${icon('mail', 'sm')}</button><button class="icon-btn" style="width:26px;height:26px" data-a="copy" data-v="${esc(x.p)}" data-tip="Copy phone">${icon('phone', 'sm')}</button></div></div>`;
    out.push(sideSec(dt === 'contact' ? 'Colleagues' : 'People', ppl.length ? ppl.map(card).join('') : `<p class="muted side-empty">${dt === 'lead' ? 'Converting creates the contact.' : 'No one here yet.'}</p>`, dt === 'lead' ? '' : `<button class="icon-btn" style="width:24px;height:24px" data-a="create" data-t="contact" aria-label="Add contact">${icon('plus', 'sm')}</button>`));
  }
  if (blockOn(dt, 'related')) {
    let body = '';
    const dealCardMini = x => `<button class="mini-card" data-a="go" data-r="deal-${x.id}"><div class="grow"><div class="nm">${esc(x.t)}</div><div class="sub num">${money(x.v)} · ${fmtDate(x.close)}</div></div>${stagePill(x.s)}</button>`;
    const orgCard = o => `<button class="mini-card" data-a="go" data-r="org-${o.id}">${orgMark(o, 'lg')}<div class="grow"><div class="nm">${esc(o.n)}</div><div class="sub">${o.ind} · ${o.loc}</div></div>${icon('chevron-right', 'sm muted')}</button>`;
    if (dt === 'deal') body = orgCard(org(d.org)) + DEALS.filter(x => x.org === d.org && x.id !== d.id).slice(0, 3).map(dealCardMini).join('');
    else if (dt === 'contact') body = orgCard(org(d.org)) + DEALS.filter(x => x.ct === d.id).map(dealCardMini).join('');
    else if (dt === 'org') body = DEALS.filter(x => x.org === d.id).map(dealCardMini).join('') || '<p class="muted side-empty">No deals yet.</p>';
    else { const m = ORGS.find(o => o.n.toLowerCase().split(' ')[0] === d.co.toLowerCase().split(' ')[0]); body = m ? orgCard(m) : `<p class="muted side-empty">No matching organization. Converting creates one.</p>`; }
    out.push(sideSec(dt === 'deal' ? 'Organization and related' : dt === 'org' ? 'Deals' : dt === 'lead' ? 'Possible match' : 'Organization and deals', body));
  }
  if (blockOn(dt, 'tags')) { d.tags ??= []; out.push(sideSec('Tags', `<div class="tag-row">${d.tags.map(t => `<span class="chip ${t === 'Hot' ? 'tag-hot' : ''}">${t === 'Hot' ? icon('flame', 'xs') : ''}${esc(t)}</span>`).join('')}<button class="chip add" data-a="tagMenu">${icon('plus', 'xs')}Tag</button></div>`)); }
  if (blockOn(dt, 'info')) {
    const last = actsFor(dt, d)[0];
    out.push(sideSec('Record info', `<dl class="fields info">${d.o ? `<dt>Owner</dt><dd><div class="fval ro">${avatar(d.o, 'sm')}<span class="tx">${USERS[d.o].n}</span></div></dd>` : ''}<dt>Created</dt><dd><div class="fval ro"><span class="num tx">${fmtDate(d.created || '2026-09-01')}</span></div></dd><dt>Last change</dt><dd><div class="fval ro"><span class="tx">${last ? relTime(last.at) : 'No changes'}</span></div></dd><dt>ID</dt><dd><div class="fval ro"><span class="tx mono">${dt.toUpperCase()}-${d.id.toUpperCase()}</span></div></dd></dl>`));
  }
  return out.join('') || `<p class="muted side-empty">All side blocks are hidden. Turn some on from Layout.</p>`;
}

/* ---------- floating layout control ---------- */
function renderFab() {
  const f = $('#fab'), lay = onLayout();
  const ph = lay && S.layoutMode === 'placeholder';
  if ((!isDocRoute() && !lay) || GS.max) { f.innerHTML = ''; f.hidden = true; document.body.classList.remove('has-fab'); return; }
  f.hidden = false; document.body.classList.add('has-fab');
  const dt = lay ? (ph ? 'any' : cur().dt) : S.route;
  const avail = BLOCKS.filter(b => !b.lock && applies(b, lay ? 'any' : dt));
  const on = k => lay ? TPL[k] : blockOn(dt, k);
  const hidden = avail.filter(b => !on(b.k)).length;
  const sw = b => {
    const v = on(b.k), dis = b.g === 'Side panel' && b.k !== 'side' && !on('side');
    const changed = !lay && UP[dt]?.blocks?.[b.k] !== undefined && UP[dt].blocks[b.k] !== TPL[b.k];
    return `<button class="lp-row" data-a="fabBlock" data-b="${b.k}" ${dis ? 'disabled' : ''} role="switch" aria-checked="${v}"><span class="lbl">${b.n}${changed ? '<i class="lp-dot" title="Different from the template"></i>' : ''}</span><span class="sw ${v ? 'on' : ''}"><i></i></span></button>`;
  };
  const groups = ['Main', 'Tabs', 'Side panel'].map(g => `<div class="lp-g"><div class="lp-gh">${g}</div>${avail.filter(b => b.g === g).map(sw).join('')}</div>`).join('');
  const dens = docPref(dt, 'dens', 'comfortable'), pos = docPref(dt, 'pos', 'right');
  const html = `
    ${S.fabOpen ? `<div class="lp ${S.fabAnim ? 'anim' : ''}" role="dialog" aria-label="Layout">
      <div class="lp-h"><div><b>${lay ? 'Layout template' : 'Layout'}</b><span>${lay ? 'Applies to every record. People can still adjust their own view.' : 'Only you see this'}</span></div><button class="icon-btn" data-a="fabToggle" aria-label="Close">${icon('x', 'sm')}</button></div>
      <div class="lp-b">
        ${lay ? '' : `<div class="lp-opts"><div><span>Density</span><div class="seg sm"><button class="${dens === 'comfortable' ? 'on' : ''}" data-a="fabOpt" data-k="dens" data-v="comfortable">Comfortable</button><button class="${dens === 'compact' ? 'on' : ''}" data-a="fabOpt" data-k="dens" data-v="compact">Compact</button></div></div>
        <div><span>Side panel</span><div class="seg sm"><button class="${pos === 'left' ? 'on' : ''}" data-a="fabOpt" data-k="pos" data-v="left">Left</button><button class="${pos === 'right' ? 'on' : ''}" data-a="fabOpt" data-k="pos" data-v="right">Right</button></div></div></div>`}
        ${groups}
      </div>
      <div class="lp-f"><button class="btn ghost sm" data-a="fabReset">${icon('rotate-ccw', 'sm')}${lay ? 'Reset template' : 'Reset to template'}</button><span class="grow"></span>${lay ? '' : `<button class="btn sm" data-a="go" data-r="layout">${icon('columns-3', 'sm tint')}Template</button>`}</div>
    </div>` : ''}
    <button class="fab-btn ${S.fabOpen ? 'on' : ''}" data-a="fabToggle" aria-expanded="${!!S.fabOpen}">${icon('sliders-horizontal', 'sm')}<span>${lay ? 'Layout template' : 'Layout'}</span>${hidden ? `<span class="fab-n num">${hidden} hidden</span>` : ''}</button>`;
  const sc = $('.lp-b', f)?.scrollTop || 0; f.innerHTML = html; const nb = $('.lp-b', f); if (nb) nb.scrollTop = sc; S.fabAnim = false;
}

/* ---------- doc layout page: sample data or placeholders ---------- */
VIEWS.layout = () => {
  const ph = S.layoutMode === 'placeholder';
  return `<div class="lay-bar"><div class="lay-t"><span class="ph-icon b-slate" style="width:28px;height:28px;border-radius:8px">${icon('columns-3', 'sm')}</span><div><b>Doc layout</b><span>The shared layout behind every record</span></div></div>
    <div class="seg" role="tablist" aria-label="Preview content"><button class="${ph ? '' : 'on'}" data-a="layoutMode" data-v="sample">${icon('file-text', 'sm')}Sample data</button><button class="${ph ? 'on' : ''}" data-a="layoutMode" data-v="placeholder">${icon('hash', 'sm')}Placeholders</button></div></div>
    ${ph ? phDoc() : docView(cur().dt)}`;
};
const P = s => `<span class="ph-tok">{${s}}</span>`;
function phDoc() {
  const on = k => TPL[k];
  const tabs = [['activity', 'Activity', 1], ['details', 'Details', on('t_details')], ['child', '{child table}', on('child')], ['emails', 'Emails', on('t_emails')], ['tasks', 'Tasks', on('t_tasks')], ['notes', 'Notes', on('t_notes')], ['files', 'Files', on('t_files')]].filter(t => t[2]);
  if (!tabs.some(t => t[0] === S.recTab)) S.recTab = 'activity';
  const side = on('side');
  const steps = ['step', 'step', 'step', 'current step', 'step'];
  const sideSecPH = (h, body) => `<div class="side-sec"><h4>${h}</h4>${body}</div>`;
  const card = (a, b) => `<div class="mini-card"><span class="ph-av"></span><div class="grow"><div class="nm">${P(a)}</div><div class="sub">${P(b)}</div></div></div>`;
  const kvs = n => `<dl class="fields">${Array.from({ length: n }, () => `<dt>${P('label')}</dt><dd><div class="fval ro">${P('value')}</div></dd>`).join('')}</dl>`;
  let body;
  if (S.recTab === 'details') body = ['section title', 'section title'].map((s, i) => `<section class="fs"><div class="fs-h"><h3>${P(s)}</h3><p>${P('section description')}</p></div><div class="fs-g">${Array.from({ length: i ? 3 : 6 }, (_, j) => `<div class="ff ${i && j === 2 ? 'full' : ''}"><label>${P('label')}</label><div class="ff-v"><div class="fval ro">${P(i && j === 2 ? 'long text' : 'value')}</div></div></div>`).join('')}</div></section>`).join('');
  else if (S.recTab === 'child') body = `<div class="grid"><div class="g-bar"><div class="g-t"><span class="ph-icon b-slate" style="width:26px;height:26px;border-radius:7px">${icon('table-2', 'sm')}</span>${P('child table')}<span class="n">{rows}</span></div><span class="grow"></span><button class="btn sm">${icon('columns-3', 'sm tint')}Columns</button><button class="btn sm">${icon('maximize-2', 'sm')}Focus</button></div>
    <div class="g-scroll"><table class="g" style="width:${76 + 6 * 150}px"><colgroup><col style="width:76px">${'<col style="width:150px">'.repeat(6)}</colgroup><thead><tr><th class="g-ctl stk" style="left:0"></th>${Array.from({ length: 6 }, (_, i) => `<th class="${i ? '' : 'stk s2'}" ${i ? '' : 'style="left:76px"'}>${P('column ' + (i + 1))}</th>`).join('')}</tr></thead>
    <tbody>${Array.from({ length: 4 }, (_, r) => `<tr><td class="g-ctl stk" style="left:0"><div class="g-ci"><span class="check"></span><span class="g-exp"><span class="num">${r + 1}</span>${icon('chevron-right', 'xs')}</span></div></td>${Array.from({ length: 6 }, (_, i) => `<td class="${i ? '' : 'stk s2'}" ${i ? '' : 'style="left:76px"'}>${P('value')}</td>`).join('')}</tr>`).join('')}</tbody>
    <tfoot><tr><td class="g-ctl stk" style="left:0"></td><td class="stk s2" style="left:76px">${P('total label')}</td>${'<td></td>'.repeat(4)}<td class="r">${P('total')}</td></tr></tfoot></table></div>
    <div class="g-under"><span class="g-add">${icon('plus', 'sm')}Add row</span><span class="grow"></span><div class="g-sum"><div><span>${P('total label')}</span><b>${P('total')}</b></div></div></div></div>`;
  else if (S.recTab === 'activity') body = (on('composer') ? `<div class="composer"><div class="ct">${['Note', 'Email', 'Log call', 'Task'].map((m, i) => `<button class="${i ? '' : 'on'}">${m}</button>`).join('')}</div><div class="ph-area">${P('write here')}</div><div class="cf"><span class="grow"></span><button class="btn primary sm">${P('save')}</button></div></div>` : '') + `<div class="timeline"><div class="tl-day">${P('day')}</div>${[1, 1, 0].map(n => `<div class="tl-item"><span class="ic b-slate">${icon('activity', 'sm')}</span><div class="bd"><div class="hd">${P('actor')} ${P('action')}<span class="tm">${P('time')}</span></div>${n ? `<div class="note">${P('content')}</div>` : ''}</div></div>`).join('')}</div>`;
  else body = `<section class="panel">${Array.from({ length: 3 }, () => `<div class="task-row"><span class="ph-av sq"></span><div class="t"><div class="title">${P('item title')}</div><div class="meta">${P('meta')}</div></div></div>`).join('')}</section>`;
  const sideHtml = [
    on('next') && sideSecPH('Next step', card('next task', 'due')),
    on('highlight') && sideSecPH(P('highlight card'), `<div class="scorecard"><div class="ring" style="--p:66"><b>{n}</b></div><div class="sc-rows">${['metric', 'metric', 'metric'].map(m => `<div class="sc-row"><span>${P(m)}</span><div class="bar"><span style="width:60%"></span></div><b>{n}</b></div>`).join('')}</div></div>`),
    on('key') && sideSecPH('Key fields', kvs(5)),
    on('people') && sideSecPH('People', card('person', 'role') + card('person', 'role')),
    on('related') && sideSecPH('Related records', card('related record', 'detail')),
    on('tags') && sideSecPH('Tags', `<div class="tag-row"><span class="chip">${P('tag')}</span><span class="chip">${P('tag')}</span></div>`),
    on('info') && sideSecPH('Record info', kvs(3)),
  ].filter(Boolean).join('');
  return `<div class="doc ph-doc ${side ? '' : 'no-side'}" style="--acc:var(--accent);--acc-bg:var(--accent-soft)">
    <div class="doc-main">
      <header class="doc-head"><div class="doc-mark"><span class="org-mark lg ph-mark">{m}</span></div>
        <div class="ttl"><div class="eyebrow">${P('record type')}</div><h1>${P('title')}</h1><div class="rec-meta"><span>${P('meta')}</span><span>${P('meta')}</span><span>${P('meta')}</span><span>${P('owner')}</span></div></div>
        <div class="ph-actions"><button class="btn">${P('action')}</button><button class="btn">${P('action')}</button><button class="btn primary">${P('primary action')}</button><button class="btn icon">${icon('ellipsis', 'sm')}</button></div></header>
      ${on('flow') ? `<div class="stepper">${steps.map((s, i) => `<span class="step ${i < 3 ? 'done' : ''} ${i === 3 ? 'cur' : ''}">${i < 3 ? icon('check', 'xs') : ''}{${s}}</span>`).join('')}</div><div class="ph-cap">${P('status flow')}</div>` : ''}
      ${on('summary') ? `<div class="sum">${Array.from({ length: 4 }, () => `<div class="sum-t"><span>${P('metric')}</span><b>${P('value')}</b><em>${P('note')}</em></div>`).join('')}</div>` : ''}
      <div class="tabs doc-tabs">${tabs.map(([k, n]) => `<button class="${S.recTab === k ? 'on' : ''}" data-a="recTab" data-v="${k}">${n.startsWith('{') ? P(n.slice(1, -1)) : n}</button>`).join('')}</div>
      <div class="doc-body">${body}</div>
    </div>
    ${side ? `<aside class="doc-side">${sideHtml || '<p class="muted side-empty">All side blocks are hidden.</p>'}</aside>` : ''}
  </div>`;
}

/* ---------- child table (grid) ---------- */
const GRID_DEF = {
  'deal-items': {
    title: 'Items', ic: 'package', c: 'blue', rows: d => (ITEMS[d.id] ||= []), sticky: 'item', totals: ['net', 'vat', 'amt'],
    cols: [
      { k: 'item', l: 'Item', w: 220, t: 'sel', o: () => CATALOG.map(c => c.n), ph: 'Select item' },
      { k: 'desc', l: 'Description', w: 280, t: 'text' },
      { k: 'qty', l: 'Qty', w: 80, t: 'num', r: 1 },
      { k: 'uom', l: 'UOM', w: 96, t: 'sel', o: () => ['Nos', 'Day', 'Hour', 'Entity', 'Year'] },
      { k: 'rate', l: 'Rate', w: 120, t: 'cur', r: 1 },
      { k: 'disc', l: 'Disc %', w: 88, t: 'pct', r: 1 },
      { k: 'net', l: 'Net', w: 130, t: 'cur', r: 1, calc: net },
      { k: 'vatp', l: 'VAT %', w: 80, t: 'pct', r: 1 },
      { k: 'vat', l: 'VAT', w: 110, t: 'cur', r: 1, calc: vatOf },
      { k: 'amt', l: 'Amount', w: 140, t: 'cur', r: 1, calc: r => net(r) + vatOf(r) },
    ],
    newRow: () => itemRow('', '', 1, 'Nos', 0),
    onSet: (r, k, v) => { if (k === 'item') { const c = CATALOG.find(x => x.n === v); if (c) { r.rate = c.r; r.uom = c.u; if (!r.desc) r.desc = c.d; } } },
    onChange: d => syncDeal(d),
  },
  'org-branches': {
    title: 'Branches', ic: 'map-pin', c: 'amber', rows: d => (BRANCHES[d.id] ||= []), sticky: 'b',
    cols: [
      { k: 'b', l: 'Branch', w: 180, t: 'text', ph: 'Branch name' },
      { k: 'em', l: 'Emirate', w: 140, t: 'sel', o: () => EMIRATES },
      { k: 'addr', l: 'Address', w: 300, t: 'text' },
      { k: 'ph', l: 'Phone', w: 150, t: 'text' },
      { k: 'trn', l: 'TRN', w: 170, t: 'text' },
      { k: 'prim', l: 'Primary', w: 96, t: 'sel', o: () => ['Yes', 'No'] },
    ],
    newRow: () => ({ id: uid('b'), b: '', em: 'Dubai', addr: '', ph: '', trn: '', prim: 'No' }),
    onSet: () => { }, onChange: () => { },
  },
};
let GS = { sel: new Set(), exp: null, max: false, sl: 0, st: 0, edit: null };
function gridReset() { GS = { sel: new Set(), exp: null, max: false, sl: 0, st: 0, edit: null }; }
const CTL = 76;
const gPrefKey = k => `grid.${ME}.${k}`;
const gPrefs = k => store.get(gPrefKey(k), { w: {}, hide: [], order: [] });
function gCols(key, all) {
  const g = GRID_DEF[key], p = gPrefs(key);
  let cols = g.cols.slice();
  if (p.order.length) cols.sort((a, b) => { const ia = p.order.indexOf(a.k), ib = p.order.indexOf(b.k); return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib); });
  cols = cols.filter(c => c.k !== g.sticky); cols.unshift(g.cols.find(c => c.k === g.sticky));
  return cols.filter(c => all || !p.hide.includes(c.k)).map(c => ({ ...c, w: p.w[c.k] || c.w }));
}
const gFmt = (c, r) => { const v = c.calc ? c.calc(r) : r[c.k]; if (v === '' || v == null) return c.ph ? `<span class="g-ph">${c.ph}</span>` : ''; if (c.t === 'cur') return Math.round(v).toLocaleString('en-US'); if (c.t === 'pct') return `${+v}%`; if (c.t === 'num') return (+v).toLocaleString('en-US'); return esc(v); };
let gridCtx = null;
function gridBlock(key, d, inline) {
  const g = GRID_DEF[key], rows = g.rows(d), cols = gCols(key), allCols = gCols(key, true);
  gridCtx = { key, d }; const locked = !!g.locked?.(d);
  const W = CTL + cols.reduce((s, c) => s + c.w, 0);
  const allSel = rows.length && rows.every(r => GS.sel.has(r.id));
  const tot = k => rows.reduce((s, r) => s + (GRID_DEF[key].cols.find(c => c.k === k).calc?.(r) ?? +r[k] ?? 0), 0);
  const body = rows.map((r, i) => {
    const open = GS.exp === r.id;
    return `<tr class="${GS.sel.has(r.id) ? 'sel' : ''} ${open ? 'open' : ''}" data-rid="${r.id}">
      <td class="g-ctl stk" style="left:0"><div class="g-ci"><button class="check" data-a="gSel" data-rid="${r.id}" aria-label="Select row ${i + 1}">${icon('check')}</button><button class="g-exp" data-a="gExp" data-rid="${r.id}" data-tip="${open ? 'Collapse' : 'Expand row'}" aria-expanded="${open}"><span class="num">${i + 1}</span>${icon('chevron-right', 'xs')}</button></div></td>
      ${cols.map((c, ci) => `<td class="${c.calc || locked ? 'calc' : 'ed'} ${ci === 0 ? 'stk s2' : ''} ${c.r ? 'r' : ''} num" ${ci === 0 ? `style="left:${CTL}px"` : ''} ${c.calc || locked ? '' : `data-a="gEdit" data-rid="${r.id}" data-k="${c.k}"`}>${gFmt(c, r)}</td>`).join('')}
    </tr>${open ? `<tr class="g-xrow"><td colspan="${cols.length + 1}"><div class="g-x">
      <div class="g-x-h"><span class="g-x-n num">Row ${i + 1} of ${rows.length}</span><b>${esc(r[g.sticky] || 'New row')}</b><span class="grow"></span>
        <button class="btn ghost sm icon" data-a="gNav" data-d="-1" data-tip="Previous row" ${i === 0 ? 'disabled' : ''}>${icon('arrow-up', 'sm')}</button><button class="btn ghost sm icon" data-a="gNav" data-d="1" data-tip="Next row" ${i === rows.length - 1 ? 'disabled' : ''}>${icon('arrow-down', 'sm')}</button>
        ${locked ? '' : `<span class="sep"></span><button class="btn ghost sm" data-a="gDup" data-rid="${r.id}">${icon('copy', 'sm')}Duplicate</button><button class="btn ghost sm danger-soft" data-a="gDel" data-rid="${r.id}">${icon('trash-2', 'sm')}Delete</button>`}<button class="btn sm" data-a="gExp" data-rid="${r.id}">Done</button></div>
      <div class="g-x-f">${allCols.map(c => `<label class="fld ${c.k === 'desc' || c.k === 'addr' ? 'wide' : ''}"><span>${c.l}${gPrefs(key).hide.includes(c.k) ? ' <i class="muted" style="font-style:normal;font-weight:400">· hidden column</i>' : ''}</span>${c.calc ? `<div class="input ro num">${gFmt(c, r)}</div>` : gInput(c, r[c.k], `data-gx="${c.k}" data-rid="${r.id}" ${locked ? 'disabled' : ''}`)}</label>`).join('')}</div>
    </div></td></tr>` : ''}`;
  }).join('');
  const foot = g.totals ? `<tfoot><tr><td class="g-ctl stk" style="left:0"></td>${cols.map((c, ci) => `<td class="${ci === 0 ? 'stk s2' : ''} ${c.r ? 'r' : ''} num" ${ci === 0 ? `style="left:${CTL}px"` : ''}>${ci === 0 ? `Total · ${rows.length} lines` : g.totals.includes(c.k) ? Math.round(tot(c.k)).toLocaleString('en-US') : ''}</td>`).join('')}</tr></tfoot>` : '';
  const summary = g.totals && rows.length ? `<div class="g-sum"><div><span>Net total</span><b class="num">${money(tot('net'))}</b></div><div><span>VAT</span><b class="num">${money(tot('vat'))}</b></div><div class="gt"><span>Grand total</span><b class="num">${money(tot('amt'))}</b></div></div>` : '';
  return `<div class="grid ${GS.max ? 'max' : ''} ${inline ? 'inline' : ''}" id="grid" data-key="${key}">
    <div class="g-bar">
      ${GS.max ? `<span class="g-crumb">${esc(DT[cur().dt].title(d))}${icon('chevron-right', 'xs')}</span>` : ''}
      <div class="g-t"><span class="ph-icon b-${g.c}" style="width:26px;height:26px;border-radius:7px">${icon(g.ic, 'sm')}</span>${g.title}<span class="n num">${rows.length}</span></div>
      ${GS.sel.size && !locked ? `<span class="g-selinfo">${GS.sel.size} selected</span><button class="btn ghost sm" data-a="gDupSel">${icon('copy', 'sm')}Duplicate</button><button class="btn ghost sm danger-soft" data-a="gDelSel">${icon('trash-2', 'sm')}Delete</button>` : ''}
      <span class="grow"></span>
      <button class="btn sm" data-a="gColsMenu">${icon('columns-3', 'sm tint')}<span class="hide-sm">Columns</span></button>
      <button class="btn sm ${GS.max ? 'primary' : ''}" data-a="gMax" data-tip="${GS.max ? 'Back to record  Esc' : 'Focus view'}">${icon(GS.max ? 'minimize-2' : 'maximize-2', 'sm')}<span class="hide-sm">${GS.max ? 'Exit focus' : 'Focus'}</span></button>
    </div>
    <div class="g-scroll" id="gScroll">
      <table class="g" style="width:${W}px"><colgroup><col style="width:${CTL}px">${cols.map(c => `<col data-k="${c.k}" style="width:${c.w}px">`).join('')}</colgroup>
        <thead><tr><th class="g-ctl stk" style="left:0"><button class="check ${allSel ? 'on' : ''}" data-a="gSelAll" aria-label="Select all">${icon('check')}</button></th>${cols.map((c, ci) => `<th class="${ci === 0 ? 'stk s2' : ''} ${c.r ? 'r' : ''}" ${ci === 0 ? `style="left:${CTL}px"` : ''}><span>${c.l}</span>${c.calc ? icon('lock', 'xs g-lk') : ''}<i class="rz" data-rz="${c.k}" title="Drag to resize, double-click to reset"></i></th>`).join('')}</tr></thead>
        <tbody>${body || `<tr><td colspan="${cols.length + 1}" class="g-empty"><div class="g-empty-in">${icon(g.ic, 'lg muted')}<span>No rows yet</span>${locked ? '' : `<button class="btn sm primary" data-a="gAdd">${icon('plus', 'sm')}Add the first row</button>`}</div></td></tr>`}</tbody>${rows.length ? foot : ''}
      </table>
    </div>
    <div class="g-under">${locked ? `<span class="g-lock">${icon('lock', 'xs')}Locked. Lines can't change after the record is issued.</span>` : `<button class="btn sm g-add" data-a="gAdd">${icon('plus', 'sm')}Add row</button><span class="muted hide-sm"><kbd>Tab</kbd> next cell · <kbd>Enter</kbd> cell below · <kbd>Esc</kbd> cancel</span>`}<span class="grow"></span>${summary}</div>
  </div>`;
}
function gInput(c, v, attrs) {
  v = v ?? '';
  if (c.t === 'sel') return `<select class="input" ${attrs}>${c.ph ? `<option value="">${c.ph}</option>` : ''}${c.o().map(o => `<option ${String(o) === String(v) ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
  if (['num', 'cur', 'pct'].includes(c.t)) return `<input class="input num" type="number" min="0" step="any" value="${esc(v)}" ${attrs}>`;
  return `<input class="input" value="${esc(v)}" ${attrs}>`;
}
function gSet(rid, k, raw) {
  const { key, d } = gridCtx, g = GRID_DEF[key], r = g.rows(d).find(x => x.id === rid), c = g.cols.find(x => x.k === k);
  if (!r || !c) return;
  let v = ['num', 'cur', 'pct'].includes(c.t) ? Math.max(0, +raw || 0) : raw;
  if (c.t === 'pct') v = Math.min(100, v);
  if (String(r[k]) === String(v)) return;
  r[k] = v; g.onSet(r, k, v); g.onChange(d);
}
function gEditCell(rid, k) {
  const td = $(`#grid td[data-rid="${rid}"][data-k="${k}"]`); if (!td || td.querySelector('input,select')) return;
  const { key, d } = gridCtx, g = GRID_DEF[key], c = g.cols.find(x => x.k === k), r = g.rows(d).find(x => x.id === rid);
  td.classList.add('editing'); td.innerHTML = gInput(c, r[k], 'id="gIn"');
  const el = $('#gIn'); el.focus(); el.select && el.select();
  let done = false;
  const order = () => { const cols = gCols(key).filter(x => !x.calc).map(x => x.k), rows = g.rows(d).map(x => x.id); return { cols, rows }; };
  const commit = (save, moveTo) => {
    if (done) return; done = true;
    if (save) gSet(rid, k, el.value);
    render();
    if (moveTo) requestAnimationFrame(() => { gEditCell(moveTo[0], moveTo[1]); keepVisible(moveTo[0], moveTo[1]); });
  };
  el.addEventListener('blur', () => commit(true));
  if (el.tagName === 'SELECT') el.addEventListener('change', () => commit(true));
  el.addEventListener('keydown', e => {
    const { cols, rows } = order(), ci = cols.indexOf(k), ri = rows.indexOf(rid);
    if (e.key === 'Tab') {
      e.preventDefault();
      let nc = ci + (e.shiftKey ? -1 : 1), nr = ri;
      if (nc >= cols.length) { nc = 0; nr++; } if (nc < 0) { nc = cols.length - 1; nr--; }
      commit(true, nr >= 0 && nr < rows.length ? [rows[nr], cols[nc]] : null);
    } else if (e.key === 'Enter') { e.preventDefault(); commit(true, ri + 1 < rows.length ? [rows[ri + 1], k] : null); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); commit(false); }
  });
}
function keepVisible(rid, k) { const td = $(`#grid td[data-rid="${rid}"][data-k="${k}"]`), sc = $('#gScroll'); if (!td || !sc) return; const a = td.getBoundingClientRect(), b = sc.getBoundingClientRect(); const left = b.left + CTL + (gCols(gridCtx.key)[0].w); if (a.left < left) sc.scrollLeft -= left - a.left + 8; else if (a.right > b.right) sc.scrollLeft += a.right - b.right + 8; }
function gridSaveScroll() { const s = $('#gScroll'); if (s) { GS.sl = s.scrollLeft; GS.st = s.scrollTop; } }
function gridAfterRender() {
  const gr = $('#grid'), s = $('#gScroll');
  if (!gr || !s) return;
  s.scrollLeft = GS.sl; s.scrollTop = GS.st;
  if (GS.max) { const v = $('#view').getBoundingClientRect(); Object.assign(gr.style, { top: v.top + 'px', left: v.left + 'px' }); }
  gr.style.setProperty('--gw', s.clientWidth + 'px');
  gr.classList.toggle('scrolled', s.scrollLeft > 0); s.onscroll = () => gr.classList.toggle('scrolled', s.scrollLeft > 0);
  $$('.rz', gr).forEach(h => {
    h.addEventListener('dblclick', e => { e.stopPropagation(); const p = gPrefs(gridCtx.key); delete p.w[h.dataset.rz]; store.set(gPrefKey(gridCtx.key), p); render(); });
    h.addEventListener('pointerdown', e => {
      e.preventDefault(); e.stopPropagation();
      const k = h.dataset.rz, col = $(`col[data-k="${k}"]`, gr), tbl = $('table.g', gr), x0 = e.clientX, w0 = col.offsetWidth || parseInt(col.style.width), t0 = tbl.offsetWidth;
      h.setPointerCapture(e.pointerId); gr.classList.add('resizing');
      const mv = ev => { const w = Math.max(60, w0 + ev.clientX - x0); col.style.width = w + 'px'; tbl.style.width = (t0 + w - w0) + 'px'; };
      const up = ev => { h.removeEventListener('pointermove', mv); h.removeEventListener('pointerup', up); gr.classList.remove('resizing'); const p = gPrefs(gridCtx.key); p.w[k] = Math.max(60, w0 + ev.clientX - x0); store.set(gPrefKey(gridCtx.key), p); gr.style.setProperty('--gw', s.clientWidth + 'px'); };
      h.addEventListener('pointermove', mv); h.addEventListener('pointerup', up);
    });
  });
}
function gColsMenu(anchor) {
  const key = gridCtx.key, p = gPrefs(key), cols = gCols(key, true), g = GRID_DEF[key];
  openPop(anchor, `<div class="mh">Columns · saved for you</div>${cols.map((c, i) => { const lock = c.k === g.sticky, hid = p.hide.includes(c.k); return `<div class="col-row ${hid ? 'hid' : ''}"><button class="col-eye" data-a="gColHide" data-k="${c.k}" ${lock ? 'disabled' : ''} aria-label="${hid ? 'Show' : 'Hide'} ${c.l}">${icon(lock ? 'lock' : hid ? 'eye-off' : 'eye', 'sm')}</button><span class="lbl">${c.l}${lock ? ' <span class="muted" style="font-size:11.5px">· pinned</span>' : ''}</span><button class="icon-btn sm" data-a="gColMove" data-k="${c.k}" data-d="-1" ${lock || i <= 1 ? 'disabled' : ''} aria-label="Move up">${icon('arrow-up', 'xs')}</button><button class="icon-btn sm" data-a="gColMove" data-k="${c.k}" data-d="1" ${lock || i === cols.length - 1 ? 'disabled' : ''} aria-label="Move down">${icon('arrow-down', 'xs')}</button></div>`; }).join('')}<hr><button class="mi" data-a="gColReset">${icon('rotate-ccw', 'sm')}Reset widths and columns</button>`, 'cols-pop');
}

/* ---------- list tables: resizable, sticky first column, saved per user ---------- */
function wireListTable(t) {
  const key = `list.${ME}.${t.dataset.tk}`, saved = store.get(key, null), ths = $$('thead th', t), stick = +(t.dataset.stick || 1);
  const freeze = () => { if (t.classList.contains('fixed')) return; const ws = ths.map(th => th.offsetWidth); ths.forEach((th, i) => th.style.width = ws[i] + 'px'); t.style.width = ws.reduce((a, b) => a + b, 0) + 'px'; t.classList.add('fixed'); };
  if (saved && saved.length === ths.length) { ths.forEach((th, i) => th.style.width = saved[i] + 'px'); t.style.width = saved.reduce((a, b) => a + b, 0) + 'px'; t.classList.add('fixed'); }
  const pinLefts = () => { let left = 0; for (let i = 0; i < stick; i++) { const w = ths[i].offsetWidth; $$(`tr > :nth-child(${i + 1})`, t).forEach(c => { c.classList.add('stk'); c.style.left = left + 'px'; c.classList.toggle('stk-last', i === stick - 1); }); left += w; } };
  pinLefts();
  ths.forEach((th, i) => {
    if (th.classList.contains('w-check')) return;
    const h = document.createElement('i'); h.className = 'rz'; h.title = 'Drag to resize, double-click to reset'; th.appendChild(h);
    h.addEventListener('click', e => e.stopPropagation());
    h.addEventListener('dblclick', e => { e.stopPropagation(); store.set(key, null); render(); });
    h.addEventListener('pointerdown', e => {
      e.preventDefault(); e.stopPropagation(); freeze();
      const x0 = e.clientX, w0 = th.offsetWidth, t0 = t.offsetWidth; h.setPointerCapture(e.pointerId); t.classList.add('resizing');
      const mv = ev => { const w = Math.max(64, w0 + ev.clientX - x0); th.style.width = w + 'px'; t.style.width = (t0 + w - w0) + 'px'; pinLefts(); };
      const up = () => { h.removeEventListener('pointermove', mv); h.removeEventListener('pointerup', up); t.classList.remove('resizing'); store.set(key, ths.map(x => x.offsetWidth)); };
      h.addEventListener('pointermove', mv); h.addEventListener('pointerup', up);
    });
  });
}

/* ---------- composer submit (all record types) ---------- */
function submitComposer() {
  const ta = $('#cmpText'); if (!ta) return;
  const dt = cur().dt, d = DT[dt].get(cur().id), mode = ta.dataset.mode, text = ta.value.trim();
  if (!text) { ta.focus(); ta.closest('.composer').animate([{ transform: 'translateX(0)' }, { transform: 'translateX(-4px)' }, { transform: 'translateX(4px)' }, { transform: 'translateX(0)' }], { duration: 220 }); return; }
  const link = dt === 'deal' ? { deal: d.id } : { ref: `${dt}:${d.id}` };
  const to = dt === 'deal' ? contact(d.ct)?.n : dt === 'org' ? CONTACTS.find(c => c.org === d.id)?.n : d.n;
  if (mode === 'task') { TASKS.push({ id: uid('t'), t: text, ty: 'todo', due: $('#cmpDue').value || todayISO(), tm: '', deal: link.deal || '', ref: link.ref, o: ME, done: false }); logAct({ ...link, ty: 'task' }); toast('Task added'); }
  else if (mode === 'email') { logAct({ ...link, ty: 'email', subj: $('#cmpSubj').value.trim() || '(no subject)', text }); toast(`Email sent to ${esc(to || 'contact')}`, { ic: 'send' }); }
  else { logAct({ ...link, ty: mode, text }); toast(mode === 'call' ? 'Call logged' : 'Note saved'); }
  render();
}

/* ---------- doc actions ---------- */
Object.assign(A, {
  docEdit: el => docEdit(el),
  flowSet: el => { const dt = cur().dt, d = DT[dt].get(cur().id), v = el.dataset.s; if (dt === 'deal') moveDeal(d.id, v); else if (dt === 'lead') setLeadStatus(d, v); else if (d.rel !== v) { const p = d.rel; d.rel = v; render(); toast(`Relationship set to ${v}`, { undo: () => { d.rel = p; render(); } }); } },
  docMore: el => {
    const dt = cur().dt, d = DT[dt].get(cur().id);
    const extra = dt === 'deal' ? (isOpen(d) ? mi('markLost', 'circle-x', 'Mark as lost') : '') + mi('dealInvoice', 'receipt', 'Create invoice') + mi('dupDeal', 'copy', 'Duplicate') : dt === 'lead' && d.st !== 'Unqualified' && d.st !== 'Converted' ? mi('flowSet', 'circle-x', 'Mark unqualified', 'data-s="Unqualified"') : '';
    openPop(el, `${extra}${mi('copyLink', 'link', 'Copy link')}${mi('fabOpen', 'sliders-horizontal', 'Layout')}<hr>${mi('deleteDoc', 'trash-2', `Delete ${DT[dt].label.toLowerCase()}`, 'style="color:var(--t-rose)"')}`);
  },
  copyLink: () => { closePop(); copyText(location.href.split('#')[0] + `#${cur().dt}-${cur().id}`, 'Link copied'); },
  deleteDoc: () => { closePop(); const dt = cur().dt, T = DT[dt], arr = T.arr(), d = T.get(cur().id), i = arr.indexOf(d); arr.splice(i, 1); go(T.list); toast(`${T.label} deleted`, { ic: 'trash-2', undo: () => { arr.splice(i, 0, d); render(); } }); },
  tagMenu: el => { const d = DT[cur().dt].get(cur().id); openPop(el, `<div class="mh">Tags</div>${['Hot', 'Large', 'Renewal', 'Referral', 'Government', 'Partner'].map(t => `<button class="mi" data-a="tagToggle" data-t="${t}"><span class="check ${d.tags.includes(t) ? 'on' : ''}" style="width:16px;height:16px;margin:0">${icon('check')}</span>${t}</button>`).join('')}`); },
  tagToggle: el => { const d = DT[cur().dt].get(cur().id), t = el.dataset.t; d.tags = d.tags.includes(t) ? d.tags.filter(x => x !== t) : [...d.tags, t]; closePop(); render(); },
  fabToggle: () => { S.fabOpen = !S.fabOpen; S.fabAnim = S.fabOpen; renderFab(); },
  fabOpen: () => { closePop(); S.fabOpen = S.fabAnim = true; renderFab(); },
  fabBlock: el => { const k = el.dataset.b; if (onLayout()) { TPL[k] = !TPL[k]; saveTpl(); render(); return; } const dt = S.route; UP[dt] ??= {}; UP[dt].blocks ??= {}; const nv = !blockOn(dt, k); if (nv === TPL[k]) delete UP[dt].blocks[k]; else UP[dt].blocks[k] = nv; saveUP(); render(); },
  fabOpt: el => { const dt = S.route; UP[dt] ??= {}; UP[dt][el.dataset.k] = el.dataset.v; saveUP(); render(); },
  fabReset: () => {
    if (onLayout()) { const prev = { ...TPL }; TPL = TPL_DEFAULT(); saveTpl(); render(); toast('Template reset', { undo: () => { TPL = prev; saveTpl(); render(); } }); return; }
    const dt = S.route, prev = UP[dt]; delete UP[dt]; saveUP(); render(); toast('Layout matches the template again', { undo: () => { UP[dt] = prev; saveUP(); render(); } });
  },
  layoutMode: el => { S.layoutMode = el.dataset.v; store.set('layoutMode', S.layoutMode); render(); },
  sideToggle: () => matchMedia('(max-width:760px)').matches ? A.mobileNav() : toggleSidebar(),
  gEdit: el => gEditCell(el.dataset.rid, el.dataset.k),
  gSel: (el, e) => { e.stopPropagation(); const id = el.dataset.rid; GS.sel.has(id) ? GS.sel.delete(id) : GS.sel.add(id); render(); },
  gSelAll: () => { const rows = GRID_DEF[gridCtx.key].rows(gridCtx.d), all = rows.every(r => GS.sel.has(r.id)); rows.forEach(r => all ? GS.sel.delete(r.id) : GS.sel.add(r.id)); render(); },
  gExp: el => { GS.exp = GS.exp === el.dataset.rid ? null : el.dataset.rid; render(); requestAnimationFrame(() => $('.g-x [data-gx]')?.focus()); },
  gNav: el => { const rows = GRID_DEF[gridCtx.key].rows(gridCtx.d), i = rows.findIndex(r => r.id === GS.exp) + +el.dataset.d; if (rows[i]) { GS.exp = rows[i].id; render(); } },
  gAdd: () => { const { key, d } = gridCtx, g = GRID_DEF[key], r = g.newRow(); g.rows(d).push(r); g.onChange(d); render(); requestAnimationFrame(() => { $('#gScroll').scrollTop = 1e6; gEditCell(r.id, g.sticky); }); },
  gDup: el => { const { key, d } = gridCtx, g = GRID_DEF[key], rows = g.rows(d), i = rows.findIndex(r => r.id === el.dataset.rid), c = { ...rows[i], id: uid('r') }; rows.splice(i + 1, 0, c); g.onChange(d); GS.exp = c.id; render(); toast('Row duplicated'); },
  gDel: el => gDelete([el.dataset.rid]),
  gDelSel: () => gDelete([...GS.sel]),
  gDupSel: () => { const { key, d } = gridCtx, g = GRID_DEF[key], rows = g.rows(d); const copies = rows.filter(r => GS.sel.has(r.id)).map(r => ({ ...r, id: uid('r') })); rows.push(...copies); g.onChange(d); GS.sel.clear(); render(); toast(`${copies.length} rows duplicated`); },
  gMax: () => { GS.max = !GS.max; GS.exp = null; render(); },
  gColsMenu: el => gColsMenu(el),
  gColHide: el => { const key = gridCtx.key, p = gPrefs(key), k = el.dataset.k; p.hide = p.hide.includes(k) ? p.hide.filter(x => x !== k) : [...p.hide, k]; store.set(gPrefKey(key), p); popAnchor = null; render(); gColsMenu($('[data-a="gColsMenu"]')); },
  gColMove: el => { const key = gridCtx.key, p = gPrefs(key), order = gCols(key, true).map(c => c.k), i = order.indexOf(el.dataset.k), j = i + +el.dataset.d; if (j < 1 || j >= order.length) return; [order[i], order[j]] = [order[j], order[i]]; p.order = order; store.set(gPrefKey(key), p); popAnchor = null; render(); gColsMenu($('[data-a="gColsMenu"]')); },
  gColReset: () => { store.set(gPrefKey(gridCtx.key), { w: {}, hide: [], order: [] }); closePop(); render(); toast('Columns reset'); },
});
function gDelete(ids) {
  const { key, d } = gridCtx, g = GRID_DEF[key], rows = g.rows(d), snap = rows.slice();
  ids.forEach(id => { const i = rows.findIndex(r => r.id === id); if (i >= 0) rows.splice(i, 1); });
  ids.forEach(id => GS.sel.delete(id)); if (ids.includes(GS.exp)) GS.exp = null;
  g.onChange(d); render();
  toast(`${ids.length} ${ids.length === 1 ? 'row' : 'rows'} deleted`, { ic: 'trash-2', undo: () => { rows.splice(0, rows.length, ...snap); g.onChange(d); render(); } });
}
document.addEventListener('change', e => { const k = e.target.dataset?.gx; if (!k) return; gSet(e.target.dataset.rid, k, e.target.value); render(); });
document.addEventListener('click', e => { if (S.fabOpen && !e.target.closest('#fab') && !e.target.closest('[data-a="fabOpen"]')) { S.fabOpen = false; renderFab(); } }, true);
window.addEventListener('resize', () => { if ($('#grid')) gridAfterRender(); });

function ctxOrgName() { if (!isDocRoute()) return ''; const x = DT[cur().dt].get(cur().id); return S.route === 'org' ? x.n : x.org ? org(x.org)?.n || '' : ''; }
