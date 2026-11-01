
/* ---------- invoice: sample transaction document ---------- */
const INV_ST = { draft: 'slate', issued: 'blue', paid: 'green', void: 'rose' };
const INV_LABEL = { draft: 'Draft', issued: 'Issued', paid: 'Paid', void: 'Void' };
const SEQ = { year: 2026, last: 41 };
const INVOICES = [
  { id: 'i41', number: 'INV-2026-00041', customer: 'o1', contact: 'c2', deal: 'd11', posting_date: '2026-10-30', terms_days: 30, status: 'issued', remarks: 'AMC for 2027, billed at renewal.', o: 'u1', created: '2026-10-30', tags: ['Renewal'], _termsEdited: true },
  { id: 'i40', number: 'INV-2026-00040', customer: 'o8', contact: 'c10', deal: 'd16', posting_date: '2026-10-22', terms_days: 15, status: 'paid', revises: 'i39', remarks: '', o: 'u4', created: '2026-10-22', tags: [], _termsEdited: true },
  { id: 'i39', number: 'INV-2026-00039', customer: 'o8', contact: 'c10', deal: 'd16', posting_date: '2026-10-21', terms_days: 15, status: 'void', void_reason: 'Wrong customer TRN on the invoice', revised_by: 'i40', remarks: '', o: 'u4', created: '2026-10-21', tags: [], _termsEdited: true },
  { id: 'i42d', number: null, customer: 'o10', contact: 'c12', deal: 'd10', posting_date: '2026-11-01', terms_days: 30, status: 'draft', remarks: '', o: 'u2', created: '2026-11-01', tags: [] },
];
const INV_LINES = {
  i41: [itemRow('Annual maintenance (AMC)', 'Support and updates, January to December 2027', 1, 'Year', 24000)],
  i40: [itemRow('Module customization', 'Customer self-service app', 160, 'Hour', 250), itemRow('User training', 'Operations and support team', 2, 'Day', 2500), itemRow('Print format', 'Delivery receipt', 1, 'Nos', 1000)],
  i39: [itemRow('Module customization', 'Customer self-service app', 160, 'Hour', 250), itemRow('User training', 'Operations and support team', 2, 'Day', 2500), itemRow('Print format', 'Delivery receipt', 1, 'Nos', 1000)],
  i42d: [itemRow('ERPNext implementation', 'POS and inventory sync, one entity', 1, 'Entity', 42000)],
};
const PAYMENTS = [{ id: 'p107', inv: 'i40', ref: 'PAY-0107', amount: 48300, on: '2026-10-28', how: 'Bank transfer' }];
const LEDGER = [];
const invoice = id => byId(INVOICES, id);
const invTotals = d => { const rows = INV_LINES[d.id] || []; const n = rows.reduce((s, r) => s + net(r), 0), v = rows.reduce((s, r) => s + vatOf(r), 0); return { net: Math.round(n * 100) / 100, vat: Math.round(v * 100) / 100 }; };
function invCompute(d) {
  const t = invTotals(d);
  d.net_total = t.net; d.vat_total = t.vat; d.total = Math.round((t.net + t.vat) * 100) / 100;
  d.paid = PAYMENTS.filter(p => p.inv === d.id).reduce((s, p) => s + p.amount, 0);
  d.outstanding = d.status === 'void' ? 0 : Math.round((d.total - d.paid) * 100) / 100;
  d.due_on = d.posting_date ? toISO(new Date(parseISO(d.posting_date).getTime() + (+d.terms_days || 0) * 864e5)) : '';
}
const creditDays = o => { const m = /(\d+) days credit/.exec(org(o)?.terms || ''); return m ? +m[1] : 0; };
function ledgerRows(d, at, note = 'Issued') {
  return [
    { account: 'Accounts receivable', party: org(d.customer).n, debit: d.total, credit: 0 },
    { account: 'Sales: services', party: '', debit: 0, credit: d.net_total },
    { account: 'VAT output 5%', party: '', debit: 0, credit: d.vat_total },
  ].map(r => ({ id: uid('g'), src: d.id, at, note, ...r }));
}
(function seedInvoices() {
  INVOICES.forEach(invCompute);
  LEDGER.push(...ledgerRows(invoice('i39'), '2026-10-21T15:10'));
  LEDGER.push(...ledgerRows(invoice('i39'), '2026-10-22T09:05', 'Reversal').map(r => ({ ...r, debit: r.credit, credit: r.debit, rev: true })));
  LEDGER.push(...ledgerRows(invoice('i40'), '2026-10-22T09:20'));
  LEDGER.push({ id: uid('g'), src: 'i40', at: '2026-10-28T12:30', note: 'Payment PAY-0107', account: 'Bank: Emirates NBD', party: '', debit: 48300, credit: 0 }, { id: uid('g'), src: 'i40', at: '2026-10-28T12:30', note: 'Payment PAY-0107', account: 'Accounts receivable', party: org('o8').n, debit: 0, credit: 48300 });
  LEDGER.push(...ledgerRows(invoice('i41'), '2026-10-30T10:00'));
  const j = (inv, ty, by, at, extra) => ACTS.push({ id: uid('a'), ref: 'invoice:' + inv, ty, by, at, ...extra });
  j('i39', 'created', 'u4', '2026-10-21T14:50', { text: 'From deal Customer self-service app' });
  j('i39', 'moved', 'u4', '2026-10-21T15:10', { from: 'draft', to: 'issued' });
  j('i39', 'effects', 'sys', '2026-10-21T15:10', { n: 3, total: 48300 });
  j('i39', 'voided', 'u4', '2026-10-22T09:05', { text: 'Wrong customer TRN on the invoice' });
  j('i39', 'effects', 'sys', '2026-10-22T09:05', { n: 3, total: 48300, rev: true });
  j('i39', 'revised', 'u4', '2026-10-22T09:06', { to: 'i40' });
  j('i40', 'created', 'u4', '2026-10-22T09:06', { text: 'Revision of INV-2026-00039' });
  j('i40', 'moved', 'u4', '2026-10-22T09:20', { from: 'draft', to: 'issued' });
  j('i40', 'effects', 'sys', '2026-10-22T09:20', { n: 3, total: 48300 });
  j('i40', 'payment', 'u4', '2026-10-28T12:30', { amount: 48300, text: 'PAY-0107 · Bank transfer' });
  j('i40', 'moved', 'u4', '2026-10-28T12:31', { from: 'issued', to: 'paid' });
  j('i41', 'created', 'u1', '2026-10-30T09:40', { text: 'From deal AMC renewal 2027' });
  j('i41', 'changed', 'u1', '2026-10-30T09:52', { diffs: [{ l: 'Contact', o: 'Khalid Al Mansoori', n: 'Priya Menon' }, { l: 'Credit days', o: '0 days', n: '30 days' }] });
  j('i41', 'moved', 'u1', '2026-10-30T10:00', { from: 'draft', to: 'issued' });
  j('i41', 'effects', 'sys', '2026-10-30T10:00', { n: 3, total: 25200 });
  j('i41', 'email', 'u1', '2026-10-30T10:04', { subj: 'Invoice INV-2026-00041', text: 'Hi Priya,\n\nPlease find the AMC invoice for 2027 attached. Payment is due by 29-11-2026.' });
  j('i42d', 'created', 'u2', '2026-11-01T11:05', { text: 'From deal POS and inventory sync' });
})();

DT.invoice = { label: 'Invoice', list: 'invoices', ic: 'receipt', c: 'blue', arr: () => INVOICES, get: invoice, title: d => d.number || 'Draft invoice', tk: 'remarks' };
F.invoice = {
  number: ['Number', 'text', null, null, 1],
  status: ['Status', 'invst', null, null, 1],
  customer: ['Customer', 'org'],
  contact: ['Contact', 'contact'],
  deal: ['Source deal', 'dealref', null, null, 1],
  posting_date: ['Posting date', 'date'],
  terms_days: ['Credit days', 'num', null, ' days'],
  due_on: ['Due on', 'date', null, null, 1, 'posting_date + credit_days'],
  net_total: ['Net total', 'cur', null, null, 1, 'sum of line net'],
  vat_total: ['VAT', 'cur', null, null, 1, 'sum of line VAT'],
  total: ['Total', 'cur', null, null, 1, 'net_total + vat_total'],
  paid: ['Paid', 'cur', null, null, 1, 'sum of payments'],
  outstanding: ['Outstanding', 'cur', null, null, 1, 'total - paid'],
  remarks: ['Remarks', 'area'],
  o: ['Owner', 'user'],
  created: ['Created', 'date', null, null, 1],
};
SECTIONS.invoice = [['Customer', '', ['customer', 'contact', 'posting_date', 'terms_days', 'due_on', 'deal']], ['Lines', '', [], 'invlines'], ['Totals', 'Calculated from the lines and payments. Read only.', ['net_total', 'vat_total', 'total', 'paid', 'outstanding']], ['Other', '', ['number', 'o', 'remarks']]];
KEYF.invoice = ['status', 'customer', 'total', 'outstanding', 'due_on', 'o'];
const EDIT_LOCKED = { invoice: ['remarks'] };
const invLocked = d => d.status !== 'draft';
BLOCKS.find(b => b.k === 'flow').only.push('invoice');
BLOCKS.find(b => b.k === 'child').only.push('invoice');
BLOCKS.splice(BLOCKS.findIndex(b => b.k === 'child') + 1, 0, { k: 'effects', n: 'Effects tab', g: 'Tabs', only: ['invoice'] });
TPL.effects ??= true;
VIEWS.invoice = () => docView('invoice');

GRID_DEF['invoice-lines'] = { ...GRID_DEF['deal-items'], title: 'Lines', c: 'green', rows: d => (INV_LINES[d.id] ||= []), locked: d => invLocked(d), onChange: d => invCompute(d) };

const INV_MOVES = {
  draft: [{ to: 'issued', label: 'Issue', ic: 'send', why: d => !d.customer ? 'Choose a customer first' : !(INV_LINES[d.id] || []).length ? 'Add at least one line' : (INV_LINES[d.id] || []).some(r => !r.item || !(+r.rate > 0)) ? 'Every line needs an item and a rate' : null }],
  issued: [{ to: 'paid', label: 'Mark paid', ic: 'check', why: d => d.outstanding > 0 ? `Outstanding is ${money(d.outstanding)}` : null }],
  paid: [], void: [],
};
const moveTo = (d, to) => (INV_MOVES[d.status] || []).find(m => m.to === to);

function extraRO(dt, d, k) { return dt === 'invoice' && invLocked(d) && !EDIT_LOCKED.invoice.includes(k); }
function fieldHint(dt, d, k, ro) {
  if (dt === 'deal' && k === 'v' && ro) return `<span class="ff-hint">${icon('lock', 'xs')}from items</span>`;
  const f = F[dt][k];
  if (f[5]) return `<span class="ff-hint calc" data-tip="= ${esc(f[5])}">ƒ calculated</span>`;
  if (dt === 'invoice' && k === 'terms_days' && !d._termsEdited && d.status === 'draft') return `<span class="ff-hint init">from customer</span>`;
  if (dt === 'invoice' && invLocked(d) && ro && !f[4]) return `<span class="ff-hint">${icon('lock', 'xs')}locked</span>`;
  if (dt === 'invoice' && invLocked(d) && EDIT_LOCKED.invoice.includes(k)) return `<span class="ff-hint init">editable after issue</span>`;
  return '';
}
const baseFieldDisplay = fieldDisplay;
fieldDisplay = (dt, d, k) => {
  const t = F[dt][k][1], v = d[k];
  if (t === 'invst') return `<span class="pill b-${INV_ST[v]}"><span class="d"></span>${INV_LABEL[v]}</span>`;
  if (t === 'dealref') { const x = deal(v); return x ? `<a class="link-q tx" data-a="go" data-r="deal-${x.id}">${esc(x.t)}</a>` : notSet; }
  if (dt === 'invoice' && k === 'number' && !v) return '<span class="muted">Assigned on issue</span>';
  if (dt === 'invoice' && t === 'cur') return `<span class="num tx">${money(v || 0)}</span>`;
  return baseFieldDisplay(dt, d, k);
};
const plain = (dt, k, v) => { const t = F[dt][k][1]; if (v === '' || v == null) return 'empty'; if (t === 'cur') return money(v); if (t === 'date') return fmtDate(v); if (t === 'org') return org(v)?.n || v; if (t === 'contact') return contact(v)?.n || v; if (t === 'user') return USERS[v]?.n || v; if (t === 'num') return v + (F[dt][k][3] || ''); return String(v); };
const baseSetField = setField;
setField = (dt, d, k, raw) => {
  if (dt !== 'invoice') return baseSetField(dt, d, k, raw);
  const old = d[k], r = baseSetField(dt, d, k, raw);
  if (r === true) {
    const diffs = [{ l: F.invoice[k][0], o: plain(dt, k, old), n: plain(dt, k, d[k]) }];
    if (k === 'terms_days') d._termsEdited = true;
    if (k === 'customer') { const oc = d.contact; d.contact = CONTACTS.find(c => c.org === d.customer)?.id || ''; if (oc !== d.contact) diffs.push({ l: 'Contact', o: plain(dt, 'contact', oc), n: plain(dt, 'contact', d.contact) }); if (!d._termsEdited) { const ot = d.terms_days; d.terms_days = creditDays(d.customer); if (ot !== d.terms_days) diffs.push({ l: 'Credit days', o: ot + ' days', n: d.terms_days + ' days' }); } }
    invCompute(d);
    logAct({ ref: 'invoice:' + d.id, ty: 'changed', diffs });
  }
  return r;
};
const baseGSet = gSet;
gSet = (rid, k, raw) => {
  const { key, d } = gridCtx;
  if (key !== 'invoice-lines') return baseGSet(rid, k, raw);
  const rows = INV_LINES[d.id], r = rows.find(x => x.id === rid), i = rows.indexOf(r), old = r?.[k];
  baseGSet(rid, k, raw);
  if (r && String(old) !== String(r[k])) logAct({ ref: 'invoice:' + d.id, ty: 'changed', diffs: [{ l: `Line ${i + 1} ${GRID_DEF[key].cols.find(c => c.k === k).l.toLowerCase()}`, o: old === '' || old == null ? 'empty' : String(old), n: String(r[k]) }] });
};

/* doc view branches */
const baseHeader = docHeader;
docHeader = (dt, d) => {
  if (dt !== 'invoice') return baseHeader(dt, d);
  const o = org(d.customer), moves = INV_MOVES[d.status] || [];
  const primary = d.status === 'issued' ? `<button class="btn primary" data-a="invPay">${icon('wallet', 'sm')}Record payment</button>` : '';
  const moveBtns = moves.map(m => { const why = m.why(d); return `<button class="btn ${d.status === 'draft' ? 'primary' : ''}" data-a="invMove" data-to="${m.to}" ${why ? `data-tip="${esc(why)}" aria-disabled="true"` : ''} ${why ? 'data-blocked="1"' : ''}>${icon(m.ic, 'sm')}${m.label}</button>`; }).join('');
  return `<header class="doc-head">
    <div class="doc-mark">${o ? orgMark(o, 'lg') : `<span class="org-mark lg b-green">${icon('receipt', 'sm')}</span>`}</div>
    <div class="ttl"><div class="eyebrow">${icon('receipt', 'xs')}Invoice</div><h1 class="${d.number ? 'num' : 'muted-title'}">${esc(DT.invoice.title(d))}</h1>
      <div class="rec-meta"><span class="pill b-${INV_ST[d.status]}"><span class="d"></span>${INV_LABEL[d.status]}</span><span><b class="num">${money(d.total)}</b></span>${o ? `<span>${icon('building-2', 'xs')}<a class="link-q" data-a="go" data-r="org-${o.id}">${esc(o.n)}</a></span>` : '<span class="muted">No customer yet</span>'}<span class="${d.status === 'issued' && dayDiff(d.due_on) < 0 ? 'due-over' : ''}">${icon('calendar', 'xs')}Due ${fmtDate(d.due_on)}</span><span>${avatar(d.o, 'sm')}${USERS[d.o].n}</span></div></div>
    <div class="ph-actions">${moveBtns}${primary}<button class="btn icon" data-a="docMore" aria-label="More actions">${icon('ellipsis', 'sm')}</button></div></header>
    ${invBanner(d)}`;
};
function invBanner(d) {
  if (d.status === 'draft') return `<div class="inv-banner b-slate">${icon('pencil', 'sm')}<div><b>Draft.</b> Everything can change. Issuing assigns the number, locks the invoice and posts it to the ledger.${d.revises ? ` Revision of <a class="link-q" data-a="go" data-r="invoice-${d.revises}">${invoice(d.revises).number}</a>.` : ''}</div></div>`;
  if (d.status === 'void') { const r = d.revised_by && invoice(d.revised_by); return `<div class="inv-banner b-rose">${icon('circle-x', 'sm')}<div><b>Void.</b> ${esc(d.void_reason || '')}. Postings were reversed and the number is kept.${r ? ` Replaced by <a class="link-q" data-a="go" data-r="invoice-${r.id}">${r.number || 'a draft'}</a>.` : ''}</div></div>`; }
  return `<div class="inv-banner b-blue">${icon('lock', 'sm')}<div><b>${INV_LABEL[d.status]} and locked.</b> Only remarks can change. ${d.status === 'issued' ? 'To correct it, void or revise.' : 'Fully paid.'}${d.revises ? ` Revision of <a class="link-q" data-a="go" data-r="invoice-${d.revises}">${invoice(d.revises).number}</a>.` : ''}</div>${d.status === 'issued' ? `<div class="grow"></div><button class="btn sm" data-a="invRevise">${icon('copy', 'sm tint')}Revise</button><button class="btn sm danger-soft" data-a="invVoid">${icon('circle-x', 'sm')}Void</button>` : ''}</div>`;
}
FLOWS.invoice = { steps: d => (d.status === 'void' ? ['draft', 'issued', 'void'] : ['draft', 'issued', 'paid']).map(s => [s, INV_LABEL[s]]), cur: d => d.status };
const baseSummary = docSummary;
docSummary = (dt, d) => {
  if (dt !== 'invoice') return baseSummary(dt, d);
  const tile = (l, v, sub = '', cls = '') => `<div class="sum-t"><span>${l}</span><b class="num ${cls}">${v}</b>${sub ? `<em>${sub}</em>` : ''}</div>`;
  const pays = PAYMENTS.filter(p => p.inv === d.id);
  return `<div class="sum">${tile('Total', money(d.total), `VAT ${money(d.vat_total)}`)}${tile('Outstanding', money(d.outstanding), d.status === 'void' ? 'Void' : d.outstanding ? `${Math.round(d.paid / (d.total || 1) * 100)}% paid` : 'Settled', d.outstanding && d.status === 'issued' && dayDiff(d.due_on) < 0 ? 'due-over' : '')}${tile('Due', d.status === 'issued' ? relDue(d.due_on) : fmtDate(d.due_on), d.terms_days + ' days credit')}${tile('Payments', pays.length, pays.length ? fmtDate(pays[pays.length - 1].on) : 'None yet')}</div>`;
};
const baseTabs = docTabs;
docTabs = (dt, d) => {
  if (dt !== 'invoice') return baseTabs(dt, d);
  const acts = actsFor(dt, d), tasks = tasksFor(dt, d);
  return [['activity', 'Activity', acts.length, 1], ['details', 'Details', 0, blockOn(dt, 't_details')], ['lines', 'Lines', (INV_LINES[d.id] || []).length, blockOn(dt, 'child')], ['effects', 'Postings', LEDGER.filter(g => g.src === d.id).length, blockOn(dt, 'effects')], ['emails', 'Emails', acts.filter(a => a.ty === 'email').length, blockOn(dt, 't_emails')], ['tasks', 'Tasks', tasks.filter(t => !t.done).length, blockOn(dt, 't_tasks')], ['notes', 'Notes', acts.filter(a => a.ty === 'note').length, blockOn(dt, 't_notes')], ['files', 'Files', (FILES[d.id] || []).length, blockOn(dt, 't_files')]].filter(x => x[3]);
};
const baseBody = docBody;
docBody = (dt, d) => {
  if (dt !== 'invoice') return baseBody(dt, d);
  if (S.recTab === 'lines') return gridBlock('invoice-lines', d);
  if (S.recTab === 'effects') return invPostings(d);
  if (S.recTab === 'activity') return `<div class="journal-note">${icon('lock', 'xs')}Append-only history. Entries can't be edited or deleted. Chain verified.</div>` + baseBody(dt, d);
  return baseBody(dt, d);
};
const baseForm = docForm;
docForm = (dt, d) => {
  if (dt !== 'invoice') return baseForm(dt, d);
  return SECTIONS.invoice.map(([h, sub, keys, grid]) => {
    let extra = '';
    if (grid === 'invlines') { const rows = INV_LINES[d.id] || []; extra = `<button class="fs-link" data-a="recTab" data-v="lines"><span class="ph-icon b-green" style="width:30px;height:30px;border-radius:8px">${icon('table-2', 'sm')}</span><span class="grow"><b>${rows.length ? `${rows.length} ${rows.length === 1 ? 'line' : 'lines'}` : 'No lines yet'}</b><span class="muted">${rows.length ? `Net ${money(d.net_total)} · VAT ${money(d.vat_total)} · Total ${money(d.total)}` : 'Add the items to bill'}</span></span>${invLocked(d) ? `<span class="chip">${icon('lock', 'xs')}Locked</span>` : ''}${icon('chevron-right', 'sm muted')}</button>`; }
    return `<section class="fs"><div class="fs-h"><h3>${h}</h3>${sub ? `<p>${sub}</p>` : ''}</div>${keys.length ? `<div class="fs-g">${keys.map(k => fieldCell(dt, d, k)).join('')}</div>` : ''}${extra}</section>`;
  }).join('');
};
function invPostings(d) {
  const rows = LEDGER.filter(g => g.src === d.id).sort((a, b) => a.at.localeCompare(b.at));
  if (!rows.length) return emptyState('layers', 'teal', 'Nothing posted yet', 'Issuing this invoice writes receivable, revenue and VAT rows to the ledger.');
  const dr = rows.reduce((s, r) => s + r.debit, 0), cr = rows.reduce((s, r) => s + r.credit, 0);
  return `<div class="eff-note">${icon('info', 'sm')}<span>Written by the engine from this invoice. Read only. Voiding writes mirror rows; nothing is ever deleted.</span></div>
  <div class="table-wrap"><table class="dt"><thead><tr><th>Posted</th><th>Account</th><th>Party</th><th>Source</th><th class="r">Debit</th><th class="r">Credit</th></tr></thead><tbody>
  ${rows.map(r => `<tr class="${r.rev ? 'rev' : ''}" style="cursor:default"><td class="num muted">${fmtDate(r.at)} ${hhmm(r.at)}</td><td><div class="cell-flex">${r.rev ? icon('repeat', 'xs c-rose') : ''}${esc(r.account)}</div></td><td class="muted">${esc(r.party)}</td><td><span class="chip ${r.rev ? 'b-rose' : ''}">${esc(r.note)}</span></td><td class="r num">${r.debit ? money(r.debit) : ''}</td><td class="r num">${r.credit ? money(r.credit) : ''}</td></tr>`).join('')}
  </tbody><tfoot><tr><td colspan="4"><span class="${Math.abs(dr - cr) < 0.005 ? 'c-green' : 'c-rose'}" style="display:inline-flex;gap:5px;align-items:center;font-weight:600">${icon(Math.abs(dr - cr) < 0.005 ? 'check' : 'x', 'xs')}${Math.abs(dr - cr) < 0.005 ? 'Balanced' : 'Not balanced'}</span></td><td class="r num"><b>${money(dr)}</b></td><td class="r num"><b>${money(cr)}</b></td></tr></tfoot></table></div>`;
}
const baseSide = docSide;
docSide = (dt, d) => {
  if (dt !== 'invoice') return baseSide(dt, d);
  const out = [];
  const tasks = tasksFor(dt, d).filter(t => !t.done);
  if (blockOn(dt, 'next')) out.push(sideSec('Next step', tasks[0] ? taskCard(tasks[0]) : d.status === 'draft' ? `<button class="mini-card dashed" data-a="invMove" data-to="issued">${icon('send', 'sm muted')}<span class="muted">Issue when ready</span></button>` : d.status === 'issued' ? `<button class="mini-card dashed" data-a="invPay">${icon('wallet', 'sm muted')}<span class="muted">Record the payment when it arrives</span></button>` : `<p class="muted side-empty">Nothing left to do.</p>`));
  if (blockOn(dt, 'key')) out.push(sideSec('Key fields', `<dl class="fields">${KEYF.invoice.map(k => `<dt>${F.invoice[k][0]}</dt><dd>${fieldRO(dt, d, k) ? `<div class="fval ro">${fieldDisplay(dt, d, k)}</div>` : `<button class="fval" data-a="docEdit" data-dt="${dt}" data-k="${k}">${fieldDisplay(dt, d, k)}${icon('pencil', 'xs pen')}</button>`}</dd>`).join('')}</dl>`, `<button class="h-link" data-a="recTab" data-v="details">All fields</button>`));
  if (blockOn(dt, 'related')) {
    const o = org(d.customer), src = deal(d.deal), pays = PAYMENTS.filter(p => p.inv === d.id);
    const rel = [o && `<button class="mini-card" data-a="go" data-r="org-${o.id}">${orgMark(o, 'lg')}<div class="grow"><div class="nm">${esc(o.n)}</div><div class="sub">${o.trn ? 'TRN ' + o.trn : o.loc}</div></div>${icon('chevron-right', 'sm muted')}</button>`,
      src && `<button class="mini-card" data-a="go" data-r="deal-${src.id}"><span class="ph-icon b-blue" style="width:28px;height:28px;border-radius:8px">${icon('handshake', 'sm')}</span><div class="grow"><div class="nm">${esc(src.t)}</div><div class="sub">Source deal · ${stageOf(src.s).n}</div></div></button>`,
      ...pays.map(p => `<div class="mini-card"><span class="ph-icon b-green" style="width:28px;height:28px;border-radius:8px">${icon('wallet', 'sm')}</span><div class="grow"><div class="nm num">${money(p.amount)}</div><div class="sub">${p.ref} · ${fmtDate(p.on)} · ${p.how}</div></div></div>`),
      ...[d.revises, d.revised_by].filter(Boolean).map(id => { const x = invoice(id); return `<button class="mini-card" data-a="go" data-r="invoice-${x.id}"><span class="ph-icon b-${INV_ST[x.status]}" style="width:28px;height:28px;border-radius:8px">${icon('copy', 'sm')}</span><div class="grow"><div class="nm num">${x.number || 'Draft invoice'}</div><div class="sub">${id === d.revises ? 'Revised from' : 'Revised as'} · ${INV_LABEL[x.status]}</div></div></button>`; })].filter(Boolean);
    out.push(sideSec('Related', rel.join('') || '<p class="muted side-empty">Nothing linked yet.</p>'));
  }
  if (blockOn(dt, 'people')) { const ppl = CONTACTS.filter(x => x.org === d.customer); out.push(sideSec('People', ppl.map(x => `<div class="mini-card" style="cursor:pointer" data-a="peekContact" data-id="${x.id}">${avatar(x.id)}<div class="grow"><div class="nm">${esc(x.n)}${x.id === d.contact ? ' <span class="chip">Billing</span>' : ''}</div><div class="sub">${esc(x.t)}</div></div></div>`).join('') || '<p class="muted side-empty">Choose a customer first.</p>')); }
  if (blockOn(dt, 'tags')) out.push(sideSec('Tags', `<div class="tag-row">${d.tags.map(t => `<span class="chip">${esc(t)}</span>`).join('')}<button class="chip add" data-a="tagMenu">${icon('plus', 'xs')}Tag</button></div>`));
  if (blockOn(dt, 'info')) { const n = actsFor(dt, d).length; out.push(sideSec('Record info', `<dl class="fields info"><dt>Number</dt><dd><div class="fval ro"><span class="tx mono">${d.number || 'On issue'}</span></div></dd><dt>Created</dt><dd><div class="fval ro"><span class="num tx">${fmtDate(d.created)}</span></div></dd><dt>Journal</dt><dd><div class="fval ro"><span class="tx">${n} entries</span><span class="chip b-green" style="margin-left:auto">${icon('check', 'xs')}Chain OK</span></div></dd><dt>ID</dt><dd><div class="fval ro"><span class="tx mono">INVOICE-${d.id.toUpperCase()}</span></div></dd></dl>`)); }
  return out.join('');
};

/* journal entries */
Object.assign(actIcon, { changed: ['pencil', 'slate'], moved: ['arrow-right', 'blue'], voided: ['circle-x', 'rose'], revised: ['copy', 'violet'], effects: ['layers', 'teal'], payment: ['wallet', 'green'] });
const baseSentence = actSentence;
actSentence = (a, withDeal) => {
  const tg = actTarget(a), on = withDeal && tg ? ` on <a data-a="go" data-r="${tg.r}"><b>${esc(tg.t)}</b></a>` : '';
  const who = a.by === 'sys' ? '<b>Engine</b>' : `<b>${esc(personName(a.by))}</b>`;
  const self = withDeal ? '' : ' this invoice';
  switch (a.ty) {
    case 'changed': return `${who} changed ${a.diffs.map(x => `<b>${esc(x.l)}</b>`).join(', ')}${on}`;
    case 'moved': return `${who} moved${on || self} from ${INV_LABEL[a.from]} to <b>${INV_LABEL[a.to]}</b>`;
    case 'effects': return `${who} ${a.rev ? 'reversed' : 'posted'} ${a.n} ledger rows${on} <span class="muted">· ${money(a.total)}</span>`;
    case 'voided': return `${who} voided${on || self}`;
    case 'revised': return `${who} revised${on || self} as <a class="link-q" data-a="go" data-r="invoice-${a.to}"><b>${invoice(a.to)?.number || 'a new draft'}</b></a>`;
    case 'payment': return `${who} recorded a payment of <b class="num">${money(a.amount)}</b>${on}`;
    case 'created': if (a.ref?.startsWith('invoice:')) return `${who} created ${withDeal ? `<b>${esc(tg?.t || '')}</b>` : 'this invoice'}${a.text ? ` <span class="muted">· ${esc(a.text)}</span>` : ''}`;
  }
  return baseSentence(a, withDeal);
};
function actBody(a) {
  if (a.ty === 'changed') return `<div class="diffs">${a.diffs.map(x => `<div class="diff"><span class="dl">${esc(x.l)}</span><span class="old">${esc(x.o)}</span>${icon('arrow-right', 'xs')}<span class="new">${esc(x.n)}</span></div>`).join('')}</div>`;
  if (a.ty === 'voided') return `<div class="note">${esc(a.text)}</div>`;
  if (a.ty === 'payment' && a.text) return `<div class="note muted-note">${esc(a.text)}</div>`;
  return '';
}

/* actions */
function openForm(title, ic, c, fields, submit, onSubmit) {
  openModal(`<form class="modal" id="miniForm" novalidate role="dialog" aria-label="${esc(title)}" style="width:min(460px,100%)">
    <div class="modal-h"><span class="ph-icon b-${c}" style="width:28px;height:28px;border-radius:8px">${icon(ic, 'sm')}</span><h2>${title}</h2><button type="button" class="icon-btn" data-a="closeModal" aria-label="Close">${icon('x')}</button></div>
    <div class="modal-b" style="grid-template-columns:1fr">${fields}</div>
    <div class="modal-f"><span class="grow"></span><button type="button" class="btn ghost" data-a="closeModal">Cancel</button><button type="submit" class="btn ${c === 'rose' ? 'danger' : 'primary'}">${submit}</button></div></form>`, () => {
    const f = $('#miniForm'); f.querySelector('input,textarea,select')?.focus();
    f.addEventListener('submit', e => { e.preventDefault(); $$('.fld', f).forEach(x => x.classList.remove('invalid')); const errs = onSubmit(f); if (errs) { Object.entries(errs).forEach(([k, m]) => { const x = $(`[data-f="${k}"]`, f); if (x) { x.classList.add('invalid'); $('.err', x).textContent = m; } }); return; } closeModal(); });
  });
}
function invIssue(d) {
  const why = moveTo(d, 'issued').why(d);
  if (why) { toast(esc(why), { ic: 'info' }); S.recTab = 'details'; render(); return; }
  const prev = { ...d }, rowsBefore = LEDGER.length, actsBefore = ACTS.length, seqBefore = SEQ.last;
  SEQ.last += 1; d.number = `INV-${SEQ.year}-${String(SEQ.last).padStart(5, '0')}`; d.status = 'issued';
  invCompute(d);
  const at = nowStamp(), rows = ledgerRows(d, at); LEDGER.push(...rows);
  logAct({ ref: 'invoice:' + d.id, ty: 'moved', from: 'draft', to: 'issued' });
  logAct({ ref: 'invoice:' + d.id, ty: 'effects', by: 'sys', n: rows.length, total: d.total });
  render();
  toast(`Issued as ${d.number}. Posted ${rows.length} ledger rows.`, { ic: 'send', undo: () => { Object.assign(d, prev); LEDGER.length = rowsBefore; ACTS.length = actsBefore; SEQ.last = seqBefore; render(); } });
}
Object.assign(A, {
  invMove: el => {
    const d = invoice(cur().id) || invoice(S.param), m = moveTo(d, el.dataset.to);
    if (!m) { toast(invLocked(d) ? 'This invoice is locked. Use void or revise to correct it.' : 'That move is not available', { ic: 'lock' }); return; }
    const why = m.why(d); if (why) { toast(esc(why), { ic: 'info' }); return; }
    if (m.to === 'issued') return invIssue(d);
    const prev = d.status; d.status = m.to; logAct({ ref: 'invoice:' + d.id, ty: 'moved', from: prev, to: m.to }); render();
    toast(`Marked ${INV_LABEL[m.to].toLowerCase()}`, { undo: () => { d.status = prev; ACTS.pop(); render(); } });
  },
  invPay: () => {
    const d = invoice(S.param);
    openForm('Record payment', 'wallet', 'green',
      fld('p_amount', 'Amount (AED)', `<input class="input num" id="p_amount" type="number" step="0.01" min="0" value="${d.outstanding}">`, 1) +
      fld('p_on', 'Received on', `<input class="input" id="p_on" type="date" value="${todayISO()}">`, 1) +
      fld('p_how', 'Method', sel('p_how', [['Bank transfer', 'Bank transfer'], ['Cheque', 'Cheque'], ['Card', 'Card'], ['Cash', 'Cash']], 'Bank transfer')),
      'Record payment', f => {
        const amt = Math.round(+$('#p_amount', f).value * 100) / 100, on = $('#p_on', f).value;
        if (!(amt > 0)) return { p_amount: 'Enter an amount above zero' };
        if (amt > d.outstanding + 0.001) return { p_amount: `Outstanding is only ${money(d.outstanding)}` };
        if (!on) return { p_on: 'Choose a date' };
        const ref = 'PAY-0' + (108 + PAYMENTS.length - 1); PAYMENTS.push({ id: uid('p'), inv: d.id, ref, amount: amt, on, how: $('#p_how', f).value });
        const at = nowStamp(); LEDGER.push({ id: uid('g'), src: d.id, at, note: 'Payment ' + ref, account: 'Bank: Emirates NBD', party: '', debit: amt, credit: 0 }, { id: uid('g'), src: d.id, at, note: 'Payment ' + ref, account: 'Accounts receivable', party: org(d.customer).n, debit: 0, credit: amt });
        invCompute(d); logAct({ ref: 'invoice:' + d.id, ty: 'payment', amount: amt, text: `${ref} · ${$('#p_how', f).value}` });
        render(); toast(d.outstanding ? `Payment recorded. ${money(d.outstanding)} still outstanding.` : 'Paid in full. You can mark it paid now.', { ic: 'wallet' });
      });
  },
  invVoid: () => {
    const d = invoice(S.param);
    if (PAYMENTS.some(p => p.inv === d.id)) { toast('Payments are recorded against this invoice. Reverse them first.', { ic: 'info' }); return; }
    openForm(`Void ${d.number}`, 'circle-x', 'rose', `<p class="muted" style="margin:0">The number stays used. Every ledger row gets a mirror reversal. This can't be undone.</p>` + fld('v_reason', 'Reason', `<textarea class="input" id="v_reason" rows="3" placeholder="What was wrong"></textarea>`, 1), 'Void invoice', f => {
      const reason = $('#v_reason', f).value.trim(); if (!reason) return { v_reason: 'A reason is required' };
      invVoidNow(d, reason); render(); toast(`${d.number} voided. ${LEDGER.filter(g => g.src === d.id && g.rev).length} rows reversed.`, { ic: 'circle-x' });
    });
  },
  invRevise: () => {
    const d = invoice(S.param);
    if (PAYMENTS.some(p => p.inv === d.id)) { toast('Payments are recorded against this invoice. Reverse them first.', { ic: 'info' }); return; }
    openForm(`Revise ${d.number}`, 'copy', 'violet', `<p class="muted" style="margin:0">Voids this invoice and opens an editable copy. The copy gets a new number when issued.</p>` + fld('r_reason', 'What needs to change', `<textarea class="input" id="r_reason" rows="3"></textarea>`, 1), 'Void and revise', f => {
      const reason = $('#r_reason', f).value.trim(); if (!reason) return { r_reason: 'Describe the correction' };
      invVoidNow(d, reason);
      const n = { ...d, id: uid('i'), number: null, status: 'draft', revises: d.id, revised_by: null, void_reason: null, created: todayISO(), posting_date: todayISO(), tags: [...d.tags] };
      INVOICES.unshift(n); INV_LINES[n.id] = (INV_LINES[d.id] || []).map(r => ({ ...r, id: uid('r') })); d.revised_by = n.id; invCompute(n);
      logAct({ ref: 'invoice:' + d.id, ty: 'revised', to: n.id });
      logAct({ ref: 'invoice:' + n.id, ty: 'created', text: `Revision of ${d.number}` });
      S.nextTab = 'lines'; go('invoice-' + n.id); toast(`Draft revision created from ${d.number}`, { ic: 'copy' });
    });
  },
  newInvoice: () => {
    const n = { id: uid('i'), number: null, customer: '', contact: '', deal: '', posting_date: todayISO(), terms_days: 0, status: 'draft', remarks: '', o: ME, created: todayISO(), tags: [] };
    INVOICES.unshift(n); INV_LINES[n.id] = []; invCompute(n); logAct({ ref: 'invoice:' + n.id, ty: 'created' });
    S.nextTab = 'details'; go('invoice-' + n.id);
  },
  dealInvoice: () => {
    closePop(); const dl = deal(cur().id);
    const n = { id: uid('i'), number: null, customer: dl.org, contact: dl.ct, deal: dl.id, posting_date: todayISO(), terms_days: creditDays(dl.org), status: 'draft', remarks: '', o: dl.o, created: todayISO(), tags: [] };
    INVOICES.unshift(n); INV_LINES[n.id] = (ITEMS[dl.id] || []).map(r => ({ ...r, id: uid('r') }));
    if (!INV_LINES[n.id].length) INV_LINES[n.id] = [itemRow('ERPNext implementation', dl.t, 1, 'Nos', dl.v)];
    invCompute(n); logAct({ ref: 'invoice:' + n.id, ty: 'created', text: 'From deal ' + dl.t });
    S.nextTab = 'lines'; go('invoice-' + n.id); toast('Draft invoice created from the deal', { ic: 'receipt' });
  },
});
function invVoidNow(d, reason) {
  const mine = LEDGER.filter(g => g.src === d.id && !g.rev), at = nowStamp();
  LEDGER.push(...mine.map(r => ({ ...r, id: uid('g'), at, note: 'Reversal', debit: r.credit, credit: r.debit, rev: true })));
  d.status = 'void'; d.void_reason = reason; invCompute(d);
  logAct({ ref: 'invoice:' + d.id, ty: 'voided', text: reason });
  logAct({ ref: 'invoice:' + d.id, ty: 'effects', by: 'sys', n: mine.length, total: d.total, rev: true });
}
const baseFlowSet = A.flowSet;
A.flowSet = el => {
  if (cur().dt !== 'invoice') return baseFlowSet(el);
  const d = invoice(cur().id), to = el.dataset.s;
  if (to === d.status) return;
  if (to === 'void') return A.invVoid();
  A.invMove({ dataset: { to } });
};
const baseMore = A.docMore;
A.docMore = el => {
  if (cur().dt !== 'invoice') return baseMore(el);
  const d = invoice(cur().id);
  openPop(el, `${d.status === 'issued' ? mi('invRevise', 'copy', 'Revise') + mi('invVoid', 'circle-x', 'Void') + '<hr>' : ''}${mi('copyLink', 'link', 'Copy link')}${mi('fabOpen', 'sliders-horizontal', 'Layout')}${d.status === 'draft' ? `<hr>${mi('deleteDoc', 'trash-2', 'Delete draft', 'style="color:var(--t-rose)"')}` : ''}`);
};
document.addEventListener('click', e => { const b = e.target.closest('[data-blocked]'); if (b) { e.stopImmediatePropagation(); toast(esc(b.dataset.tip), { ic: 'info' }); } }, true);

/* invoices list */
VIEWS.invoices = () => {
  const tabs = [['all', 'All', () => true], ['draft', 'Draft', i => i.status === 'draft'], ['issued', 'Issued', i => i.status === 'issued'], ['paid', 'Paid', i => i.status === 'paid'], ['void', 'Void', i => i.status === 'void']];
  const t = S.invTab || 'all', f = tabs.find(x => x[0] === t)[2], rows = INVOICES.filter(f);
  const out = INVOICES.filter(i => i.status === 'issued').reduce((s, i) => s + i.outstanding, 0), overdue = INVOICES.filter(i => i.status === 'issued' && dayDiff(i.due_on) < 0).reduce((s, i) => s + i.outstanding, 0);
  return `<div class="page">${pageHead('invoices', 'Invoices', null, `<button class="btn primary" data-a="newInvoice">${icon('plus', 'sm')}<span class="hide-sm">New invoice</span></button>`)}
    <div class="tabs">${tabs.map(([k, n, fn]) => `<button class="${t === k ? 'on' : ''}" data-a="invTab" data-v="${k}">${n} <span class="n num">${INVOICES.filter(fn).length}</span></button>`).join('')}</div>
    <div class="toolbar"><span class="grow"></span><div class="summary"><span>Outstanding <b class="num">${money(out)}</b></span><span>Overdue <b class="num ${overdue ? 'due-over' : ''}">${money(overdue)}</b></span></div></div>
    ${rows.length ? `<div class="table-wrap"><table class="dt" data-tk="invoices"><thead><tr><th>Number</th><th>Customer</th><th>Status</th><th>Posting date</th><th>Due</th><th class="r">Total</th><th class="r">Outstanding</th></tr></thead><tbody>
    ${rows.map(i => { const o = org(i.customer); return `<tr data-a="go" data-r="invoice-${i.id}"><td><div class="primary-cell"><span class="${i.number ? 'mono' : 'muted'}" style="font-weight:550">${i.number || 'Draft'}</span>${i.revises ? `<span class="chip">${icon('copy', 'xs')}Revision</span>` : ''}</div></td><td>${o ? `<div class="cell-flex">${orgMark(o)}${esc(o.n)}</div>` : '<span class="muted">Not set</span>'}</td><td><span class="pill b-${INV_ST[i.status]}"><span class="d"></span>${INV_LABEL[i.status]}</span></td><td class="num">${fmtDate(i.posting_date)}</td><td class="num ${i.status === 'issued' && dayDiff(i.due_on) < 0 ? 'due-over' : ''}">${fmtDate(i.due_on)}</td><td class="r num" style="font-weight:550">${money(i.total)}</td><td class="r num">${i.outstanding ? money(i.outstanding) : '<span class="muted">0</span>'}</td></tr>`; }).join('')}
    </tbody></table></div>` : emptyState('receipt', 'green', 'No invoices here', 'Nothing matches this view.')}</div>`;
};
A.invTab = el => { S.invTab = el.dataset.v; render(); };
