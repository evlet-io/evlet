
/* ---------- mutations ---------- */
function logAct(a) { const x = { id: uid('a'), at: nowStamp(), by: ME, fresh: true, ...a }; ACTS.push(x); return x; }
function moveDeal(id, to, silent) {
  const d = deal(id); if (!d || d.s === to) return;
  const prev = { s: d.s, pr: d.pr }, from = d.s;
  d.s = to; d.pr = stageOf(to).p || d.pr;
  if (to === 'won' || to === 'lost') d.pr = stageOf(to).p;
  const a = logAct({ deal: id, ty: to === 'won' ? 'won' : to === 'lost' ? 'lost' : 'stage', from, to });
  render();
  flashCard(id);
  if (!silent) toast(to === 'won' ? `${esc(d.t)} marked as won` : `Moved to ${stageOf(to).n}`, { ic: to === 'won' ? 'trophy' : 'check', undo: () => { d.s = prev.s; d.pr = prev.pr; ACTS.splice(ACTS.indexOf(a), 1); render(); flashCard(id); } });
}
function flashCard(id) { const c = $(`.card[data-id="${id}"]`); if (c) { c.classList.remove('flash'); void c.offsetWidth; c.classList.add('flash'); } }

/* ---------- board drag and drop ---------- */
let dragId = null, justDragged = false;
function wireBoard() {
  $$('.card[draggable]').forEach(c => {
    c.addEventListener('dragstart', e => { dragId = c.dataset.id; c.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', dragId); } catch { } });
    c.addEventListener('dragend', () => { c.classList.remove('dragging'); $$('.col-k.over').forEach(x => x.classList.remove('over')); justDragged = true; setTimeout(() => justDragged = false, 60); });
  });
  $$('.col-k').forEach(col => {
    col.addEventListener('dragover', e => { if (!dragId) return; e.preventDefault(); col.classList.add('over'); });
    col.addEventListener('dragleave', e => { if (!col.contains(e.relatedTarget)) col.classList.remove('over'); });
    col.addEventListener('drop', e => { e.preventDefault(); col.classList.remove('over'); const id = dragId; dragId = null; if (id) moveDeal(id, col.dataset.stage); });
  });
}

/* ---------- command palette ---------- */
const QA_ALL = [
  { id: 'm-leads', g: 'Modules', ic: 'target', c: 'teal', n: 'Leads', run: () => go('leads') },
  { id: 'm-deals', g: 'Modules', ic: 'handshake', c: 'blue', n: 'Deals', run: () => go('deals') },
  { id: 'm-contacts', g: 'Modules', ic: 'users', c: 'violet', n: 'Contacts', run: () => go('contacts') },
  { id: 'm-orgs', g: 'Modules', ic: 'building-2', c: 'amber', n: 'Organizations', run: () => go('orgs') },
  { id: 'm-tasks', g: 'Modules', ic: 'circle-check', c: 'amber', n: 'My tasks', run: () => go('tasks') },
  { id: 'm-inbox', g: 'Modules', ic: 'inbox', c: 'blue', n: 'Inbox', run: () => go('inbox') },
  { id: 'm-sales', g: 'Modules', ic: 'receipt', c: 'green', n: 'Sales', run: () => A.app({ dataset: { n: 'Sales' } }) },
  { id: 'm-acc', g: 'Modules', ic: 'wallet', c: 'teal', n: 'Accounting', run: () => A.app({ dataset: { n: 'Accounting' } }) },
  { id: 'm-inv', g: 'Modules', ic: 'package', c: 'amber', n: 'Inventory', run: () => A.app({ dataset: { n: 'Inventory' } }) },
  { id: 'm-hr', g: 'Modules', ic: 'user-cog', c: 'violet', n: 'HR and Payroll', run: () => A.app({ dataset: { n: 'HR and Payroll' } }) },
  { id: 'r-rev', g: 'Reports', ic: 'chart-column', c: 'rose', n: 'Won revenue', run: () => go('reports') },
  { id: 'r-board', g: 'Reports', ic: 'trophy', c: 'green', n: 'Leaderboard', run: () => go('reports') },
  { id: 'r-conv', g: 'Reports', ic: 'activity', c: 'violet', n: 'Stage conversion', run: () => go('reports') },
  { id: 'r-pipe', g: 'Reports', ic: 'wallet', c: 'blue', n: 'Pipeline by stage', run: () => go('home') },
  { id: 'd-deal-new', g: 'Documents', ic: 'handshake', c: 'blue', n: 'New deal', badge: 'plus', run: () => openCreate('deal') },
  { id: 'd-deal-list', g: 'Documents', ic: 'handshake', c: 'blue', n: 'Deal list', badge: 'list', run: () => { S.dealView = 'list'; go('deals'); } },
  { id: 'd-lead-new', g: 'Documents', ic: 'target', c: 'teal', n: 'New lead', badge: 'plus', run: () => openCreate('lead') },
  { id: 'd-lead-list', g: 'Documents', ic: 'target', c: 'teal', n: 'Lead list', badge: 'list', run: () => go('leads') },
  { id: 'd-contact-new', g: 'Documents', ic: 'users', c: 'violet', n: 'New contact', badge: 'plus', run: () => openCreate('contact') },
  { id: 'd-task-new', g: 'Documents', ic: 'circle-check', c: 'amber', n: 'New task', badge: 'plus', run: () => openCreate('task') },
  { id: 'd-quote-new', g: 'Documents', ic: 'file-text', c: 'green', n: 'New quotation', badge: 'plus', run: () => A.app({ dataset: { n: 'Sales' } }) },
  { id: 'd-inv-list', g: 'Documents', ic: 'receipt', c: 'teal', n: 'Invoice list', badge: 'list', run: () => go('invoices') },
  { id: 'd-inv-new', g: 'Documents', ic: 'receipt', c: 'green', n: 'New invoice', badge: 'plus', run: () => A.newInvoice() },
];
const QA = {
  rows: store.get('qaRows', 2),
  ids: store.get('qaIds', ['m-leads', 'm-deals', 'm-contacts', 'm-orgs', 'm-tasks', 'r-rev', 'd-deal-new', 'd-lead-new', 'd-deal-list', 'd-task-new', 'm-acc', 'm-inv']),
  edit: false,
  save() { store.set('qaRows', this.rows); store.set('qaIds', this.ids); },
};
let RECENT_Q = store.get('recentQ', ['Bayan Medical', 'AMC renewal', 'Khalid', 'Qasr']);
const qaCols = () => matchMedia('(max-width:760px)').matches ? 4 : 6;
const qaCap = () => QA.rows * qaCols();
function pushRecent(q) { q = q.trim(); if (!q) return; RECENT_Q = [q, ...RECENT_Q.filter(x => x.toLowerCase() !== q.toLowerCase())].slice(0, 4); store.set('recentQ', RECENT_Q); }

let palSel = 0, palItems = [];
function openPalette() {
  closePop(); QA.edit = false;
  openModal(`<div class="modal palette" role="dialog" aria-label="Search">
    <div class="pal-in">${icon('search', 'lg')}<input id="palIn" placeholder="Search records, pages and actions" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div>
    <div class="pal-list" id="palList"></div>
    <div class="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd><kbd>←</kbd><kbd>→</kbd> Move</span><span><kbd>↵</kbd> Open</span><span class="hide-sm"><kbd>⌘</kbd><kbd>K</kbd> Toggle</span></div></div>`, () => {
    const inp = $('#palIn'); inp.focus(); palRender();
    inp.addEventListener('input', () => { palSel = 0; QA.edit = false; palRender(); });
    inp.addEventListener('keydown', palKey);
  });
}
function palKey(e) {
  const cur = palItems[palSel], cols = qaCols();
  const move = n => { e.preventDefault(); palSel = Math.max(0, Math.min(palItems.length - 1, palSel + n)); palHi(); };
  if (e.key === 'ArrowDown') {
    if (cur?.tile) { const nx = palSel + cols; move(nx < palItems.length && palItems[nx].tile ? cols : 0); } else move(1);
  } else if (e.key === 'ArrowUp') {
    if (cur?.tile && palItems[palSel - cols]?.tile) move(-cols);
    else if (cur?.tile) { e.preventDefault(); const r = palItems.findIndex(x => x.tile); palSel = Math.max(0, r - 1); palHi(); }
    else move(-1);
  } else if (e.key === 'ArrowRight' && cur?.tile && !e.target.value) move(1);
  else if (e.key === 'ArrowLeft' && cur?.tile && !e.target.value) move(-1);
  else if (e.key === 'Enter') { e.preventDefault(); cur && palRun(cur); }
}
function palRender() {
  const q = ($('#palIn')?.value || '').trim();
  const list = $('#palList');
  if (!q) { list.innerHTML = palHome(); }
  else list.innerHTML = palResults(q.toLowerCase());
  $$('#palList [data-pi]').forEach(b => {
    b.onclick = e => { if (e.target.closest('[data-x]')) return; palSel = +b.dataset.pi; palRun(palItems[palSel]); };
    b.onmousemove = () => { if (palSel !== +b.dataset.pi && !QA.edit) { palSel = +b.dataset.pi; palHi(); } };
  });
  $$('#palList [data-x]').forEach(b => b.onclick = e => { e.stopPropagation(); palAct(b.dataset.x, b.dataset.v); });
  palHi();
}
function palHome() {
  palItems = [];
  const idx = it => (palItems.push(it), palItems.length - 1);
  const recents = RECENT_Q.length && !QA.edit ? `<section class="pal-sec">
      <div class="pal-sh"><span>Recent</span><button class="pal-link" data-x="clearRecent">Clear</button></div>
      ${RECENT_Q.map(r => `<div class="pal-it" data-pi="${idx({ lbl: r, run: () => fillQuery(r) })}" role="button" tabindex="-1">
        <span class="ic ghost">${icon('clock', 'sm')}</span><span class="lbl">${esc(r)}</span>
        <button class="pal-rm" data-x="rmRecent" data-v="${esc(r)}" aria-label="Remove ${esc(r)}">${icon('x', 'xs')}</button></div>`).join('')}
    </section>` : '';
  const cap = qaCap(), tiles = QA.ids.map(id => QA_ALL.find(x => x.id === id)).filter(Boolean).slice(0, cap);
  const empties = QA.edit ? Math.max(0, cap - tiles.length) : 0;
  const grid = `<section class="pal-sec">
      <div class="pal-sh"><span>Go to</span>
        ${QA.edit ? `<div class="seg sm"><button class="${QA.rows === 1 ? 'on' : ''}" data-x="rows" data-v="1">1 row</button><button class="${QA.rows === 2 ? 'on' : ''}" data-x="rows" data-v="2">2 rows</button></div>
          <button class="btn primary sm" data-x="editDone">Done</button>`
      : `<button class="pal-link" data-x="edit">${icon('pencil', 'xs')}Customize</button>`}</div>
      <div class="qa-grid ${QA.edit ? 'editing' : ''}" style="--cols:${qaCols()}">
        ${tiles.map(t => `<div class="qa-tile" ${QA.edit ? '' : `data-pi="${idx({ ...t, tile: true })}"`} role="button" tabindex="-1" title="${esc(t.n)}">
          <span class="qa-ic b-${t.c}">${icon(t.ic)}${t.badge ? `<i class="qa-badge">${icon(t.badge === 'plus' ? 'plus' : 'list', 'xs')}</i>` : ''}</span>
          <span class="qa-lbl">${esc(t.n)}</span>
          ${QA.edit ? `<button class="qa-rm" data-x="qaRm" data-v="${t.id}" aria-label="Remove ${esc(t.n)}">${icon('x', 'xs')}</button>` : ''}</div>`).join('')}
        ${Array.from({ length: empties }, () => `<div class="qa-tile empty"><span class="qa-ic">${icon('plus')}</span><span class="qa-lbl">Empty</span></div>`).join('')}
      </div>
      ${QA.edit ? qaPicker(tiles.length >= cap) : ''}
    </section>`;
  return recents + grid;
}
function qaPicker(full) {
  return `<div class="qa-picker"><div class="qa-note">${full ? 'Quick access is full. Remove a tile or switch to 2 rows to add more.' : 'Pick what shows in quick access. Documents can open as a list or as a new form.'}</div>
    ${['Modules', 'Reports', 'Documents'].map(g => `<div class="qa-pg"><div class="qa-pgh">${g}</div><div class="qa-chips">
      ${QA_ALL.filter(x => x.g === g).map(x => { const on = QA.ids.includes(x.id); return `<button class="qa-chip ${on ? 'on' : ''}" data-x="qaToggle" data-v="${x.id}" ${!on && full ? 'disabled' : ''}>
        <span class="c-${x.c}" style="display:inline-flex">${icon(x.ic, 'xs')}</span>${esc(x.n)}${on ? icon('check', 'xs') : ''}</button>`; }).join('')}
    </div></div>`).join('')}</div>`;
}
function palResults(q) {
  const m = s => s.toLowerCase().includes(q);
  const groups = [];
  const recs = [
    ...DEALS.filter(d => m(d.t) || m(org(d.org).n)).map(d => ({ ic: 'handshake', c: 'blue', lbl: esc(d.t), sub: org(d.org).n, hint: 'Deal', run: () => go('deal-' + d.id) })),
    ...CONTACTS.filter(c => m(c.n) || m(c.e)).map(c => ({ ic: 'user', c: 'violet', lbl: esc(c.n), sub: org(c.org).n, hint: 'Contact', run: () => go('contact-' + c.id) })),
    ...LEADS.filter(l => m(l.n) || m(l.co)).map(l => ({ ic: 'target', c: 'teal', lbl: esc(l.n), sub: l.co, hint: 'Lead', run: () => go('lead-' + l.id) })),
    ...ORGS.filter(o => m(o.n)).map(o => ({ ic: 'building-2', c: 'amber', lbl: esc(o.n), sub: o.loc, hint: 'Organization', run: () => go('org-' + o.id) })),
  ].slice(0, 8).map(r => ({ ...r, rec: true }));
  const quick = QA_ALL.filter(x => m(x.n) || m(x.g)).slice(0, 6).map(x => ({ ic: x.ic, c: x.c, lbl: esc(x.n), hint: x.g.replace(/s$/, ''), run: x.run }));
  const prefs = [{ ic: isDark() ? 'sun' : 'moon', c: 'slate', lbl: 'Switch to ' + (isDark() ? 'light' : 'dark') + ' theme', run: toggleTheme }, { ic: 'panel-left', c: 'slate', lbl: 'Toggle sidebar', hint: '[', run: toggleSidebar }].filter(x => m(x.lbl));
  if (recs.length) groups.push(['Records', recs]);
  if (quick.length) groups.push(['Pages and actions', quick]);
  if (prefs.length) groups.push(['Preferences', prefs]);
  palItems = groups.flatMap(g => g[1]);
  let i = 0;
  return palItems.length ? groups.map(([g, items]) => `<section class="pal-sec"><div class="pal-sh"><span>${g}</span></div>` + items.map(it => `<div class="pal-it" data-pi="${i++}" role="button" tabindex="-1"><span class="ic b-${it.c}">${icon(it.ic, 'sm')}</span><span class="lbl">${it.lbl}${it.sub ? `<span class="sub">${esc(it.sub)}</span>` : ''}</span>${it.hint ? `<span class="hint">${it.hint}</span>` : ''}</div>`).join('') + '</section>').join('')
    : `<div class="pal-empty">${icon('search', 'lg')}<div>No results for “${esc(q)}”</div><span>Try a company, person or deal name.</span></div>`;
}
function fillQuery(q) { const inp = $('#palIn'); inp.value = q; inp.focus(); palSel = 0; palRender(); }
function palAct(x, v) {
  if (x === 'clearRecent') { RECENT_Q = []; store.set('recentQ', RECENT_Q); }
  else if (x === 'rmRecent') { RECENT_Q = RECENT_Q.filter(r => r !== v); store.set('recentQ', RECENT_Q); }
  else if (x === 'edit') QA.edit = true;
  else if (x === 'editDone') { QA.edit = false; QA.ids = QA.ids.slice(0, qaCap()); QA.save(); $('#palIn').focus(); }
  else if (x === 'rows') { QA.rows = +v; QA.ids = QA.ids.slice(0, qaCap()); QA.save(); }
  else if (x === 'qaRm') { QA.ids = QA.ids.filter(i => i !== v); QA.save(); }
  else if (x === 'qaToggle') { QA.ids = QA.ids.includes(v) ? QA.ids.filter(i => i !== v) : QA.ids.length < qaCap() ? [...QA.ids, v] : QA.ids; QA.save(); }
  palSel = 0; palRender();
}
function palHi() { $$('#palList [data-pi]').forEach(b => b.classList.toggle('on', +b.dataset.pi === palSel)); const on = $('#palList .on'); on && on.scrollIntoView({ block: 'nearest' }); }
function palRun(it) { const q = $('#palIn')?.value; if (it.rec && q) pushRecent(q); if (it.lbl && !it.tile && !it.rec && !q && RECENT_Q.includes(it.lbl)) return it.run(); closeModal(); it.run(); }

/* ---------- tooltip ---------- */
const tipEl = Object.assign(document.createElement('div'), { className: 'tip', role: 'tooltip' });
document.body.appendChild(tipEl);
let tipFor = null;
function showTip(t) {
  tipFor = t; tipEl.textContent = t.dataset.tip;
  tipEl.className = 'tip'; tipEl.style.left = '0px'; tipEl.style.top = '0px';
  const r = t.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  const side = $('#app').classList.contains('collapsed') && t.closest('.sidebar');
  let x, y, place;
  if (side) { x = r.right + 10; y = r.top + r.height / 2 - h / 2; place = 'right'; }
  else { x = r.left + r.width / 2 - w / 2; y = r.top - h - 10; place = 'above'; if (y < 8) { y = r.bottom + 10; place = 'below'; } }
  x = Math.max(8, Math.min(x, innerWidth - w - 8));
  tipEl.style.left = x + 'px'; tipEl.style.top = y + 'px';
  tipEl.style.setProperty('--ax', (place === 'right' ? 0 : Math.max(10, Math.min(w - 10, r.left + r.width / 2 - x))) + 'px');
  tipEl.classList.add(place, 'show');
}
function hideTip() { tipFor = null; tipEl.classList.remove('show'); }
document.addEventListener('mouseover', e => {
  if (matchMedia('(hover: none)').matches) return;
  const t = e.target.closest('[data-tip]');
  if (t && t !== tipFor) showTip(t); else if (!t && tipFor) hideTip();
});
document.addEventListener('mouseout', e => { if (tipFor && !(e.relatedTarget && tipFor.contains(e.relatedTarget))) { const next = e.relatedTarget?.closest?.('[data-tip]'); next ? showTip(next) : hideTip(); } });
document.addEventListener('click', () => setTimeout(() => { if (tipFor && !tipFor.isConnected) hideTip(); }), true);
document.addEventListener('scroll', hideTip, true);

/* ---------- create modal ---------- */
const sel = (id, opts, v) => `<select class="input" id="${id}">${opts.map(([k, n]) => `<option value="${k}" ${k === v ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select>`;
const fld = (id, label, input, req, full) => `<div class="fld ${full ? 'full' : ''}" data-f="${id}"><label for="${id}">${label}${req ? '<span class="req">*</span>' : ''}</label>${input}<span class="err">${label} is required</span></div>`;
const ownerOpts = Object.entries(USERS).map(([k, u]) => [k, u.n]);
function openCreate(type, preset = {}) {
  closePop();
  const tomorrow = toISO(new Date(now().getTime() + 864e5)), in30 = toISO(new Date(now().getTime() + 30 * 864e5));
  const cfg = {
    deal: ['New deal', 'handshake', 'blue', [
      fld('f_t', 'Deal name', `<input class="input" id="f_t" placeholder="e.g. ERP rollout, phase 2">`, 1, 1),
      fld('f_org', 'Organization', `<input class="input" id="f_org" list="orgList" placeholder="Search or add new" value="${esc(preset.org || '')}"><datalist id="orgList">${ORGS.map(o => `<option value="${esc(o.n)}">`).join('')}</datalist>`, 1),
      fld('f_v', 'Amount (AED)', `<input class="input num" id="f_v" type="number" min="0" step="1000" placeholder="0">`, 1),
      fld('f_s', 'Stage', sel('f_s', STAGES.filter(s => s.k !== 'won').map(s => [s.k, s.n]), preset.stage || 'qual')),
      fld('f_close', 'Expected close', `<input class="input" id="f_close" type="date" value="${in30}">`),
      fld('f_o', 'Owner', sel('f_o', ownerOpts, ME)),
      fld('f_src', 'Source', sel('f_src', SOURCES.map(s => [s, s]), 'Referral')),
    ]],
    lead: ['New lead', 'target', 'teal', [
      fld('f_n', 'Full name', `<input class="input" id="f_n" placeholder="First and last name">`, 1, 1),
      fld('f_co', 'Company', `<input class="input" id="f_co">`, 1),
      fld('f_jt', 'Job title', `<input class="input" id="f_jt">`),
      fld('f_e', 'Email', `<input class="input" id="f_e" type="email">`),
      fld('f_p', 'Mobile', `<input class="input" id="f_p" placeholder="+971">`),
      fld('f_src', 'Source', sel('f_src', SOURCES.map(s => [s, s]), 'Website')),
      fld('f_o', 'Owner', sel('f_o', ownerOpts, ME)),
    ]],
    contact: ['New contact', 'user-plus', 'violet', [
      fld('f_n', 'Full name', `<input class="input" id="f_n">`, 1, 1),
      fld('f_org', 'Organization', `<input class="input" id="f_org" list="orgList" value="${esc(preset.org || ctxOrgName())}"><datalist id="orgList">${ORGS.map(o => `<option value="${esc(o.n)}">`).join('')}</datalist>`, 1),
      fld('f_jt', 'Job title', `<input class="input" id="f_jt">`),
      fld('f_e', 'Email', `<input class="input" id="f_e" type="email">`),
      fld('f_p', 'Mobile', `<input class="input" id="f_p" placeholder="+971">`),
    ]],
    task: ['New task', 'circle-check', 'amber', [
      fld('f_t', 'Task', `<input class="input" id="f_t" placeholder="e.g. Call to confirm scope">`, 1, 1),
      fld('f_ty', 'Type', sel('f_ty', [['call', 'Call'], ['email', 'Email'], ['meeting', 'Meeting'], ['todo', 'To-do']], preset.ty || 'call')),
      fld('f_due', 'Due date', `<input class="input" id="f_due" type="date" value="${tomorrow}">`),
      fld('f_deal', 'Related deal', sel('f_deal', [['', 'None'], ...DEALS.filter(isOpen).map(d => [d.id, d.t])], cur().dt === 'deal' ? cur().id : '')),
      fld('f_o', 'Assigned to', sel('f_o', ownerOpts, ME)),
    ]],
  }[type];
  const [title, ic, c, fields] = cfg;
  openModal(`<form class="modal" id="createForm" data-type="${type}" novalidate role="dialog" aria-label="${title}">
    <div class="modal-h"><span class="ph-icon b-${c}" style="width:28px;height:28px;border-radius:8px">${icon(ic, 'sm')}</span><h2>${title}</h2><button type="button" class="icon-btn" data-a="closeModal" aria-label="Close">${icon('x')}</button></div>
    <div class="modal-b">${fields.join('')}</div>
    <div class="modal-f"><span class="grow"><span class="hide-sm"><kbd>⌘</kbd> <kbd>Enter</kbd> to save</span></span><button type="button" class="btn ghost" data-a="closeModal">Cancel</button><button type="submit" class="btn primary">Create ${type}</button></div></form>`, w => {
    const f = $('#createForm'); f.querySelector('input').focus();
    f.addEventListener('submit', e => { e.preventDefault(); submitCreate(f); });
    f.addEventListener('input', e => e.target.closest('.fld')?.classList.remove('invalid'));
  });
}
function submitCreate(f) {
  const type = f.dataset.type, v = id => (f.querySelector('#' + id)?.value || '').trim();
  let ok = true;
  $$('.fld', f).forEach(x => { if (x.querySelector('.req')) { const val = v(x.dataset.f); if (!val || (x.dataset.f === 'f_v' && +val <= 0)) { x.classList.add('invalid'); ok = false; } } });
  if (!ok) { f.querySelector('.invalid input,.invalid select')?.focus(); return; }
  const findOrg = name => { let o = ORGS.find(x => x.n.toLowerCase() === name.toLowerCase()); if (!o) { o = { id: uid('o'), n: name, ind: 'Not set', loc: 'UAE', c: ['blue', 'teal', 'violet', 'amber', 'green'][ORGS.length % 5], web: '' }; ORGS.push(o); } return o; };
  closeModal();
  if (type === 'deal') {
    const o = findOrg(v('f_org')), s = v('f_s');
    const d = { id: uid('d'), t: v('f_t'), org: o.id, ct: CONTACTS.find(c => c.org === o.id)?.id, v: +v('f_v'), s, o: v('f_o'), close: v('f_close') || todayISO(), pr: stageOf(s).p, tags: [], src: v('f_src'), created: todayISO() };
    DEALS.push(d); logAct({ deal: d.id, ty: 'created', text: d.src });
    if (S.route === 'deals') { render(); flashCard(d.id); } else render();
    toast(`Deal created`, { ic: 'check' });
    if (S.route !== 'deals') go('deal-' + d.id);
  } else if (type === 'lead') {
    LEADS.unshift({ id: uid('l'), n: v('f_n'), co: v('f_co'), t: v('f_jt') || 'Not set', e: v('f_e'), p: v('f_p'), src: v('f_src'), st: 'New', sc: 50, o: v('f_o'), created: todayISO(), c: 'teal' });
    S.leadTab = 'all'; toast('Lead created'); go('leads');
  } else if (type === 'contact') {
    const o = findOrg(v('f_org'));
    CONTACTS.push({ id: uid('c'), n: v('f_n'), t: v('f_jt') || 'Not set', org: o.id, e: v('f_e'), p: v('f_p'), c: 'violet' });
    toast('Contact added'); render();
  } else if (type === 'task') {
    const t = { id: uid('t'), t: v('f_t'), ty: v('f_ty'), due: v('f_due') || todayISO(), tm: '', deal: v('f_deal'), o: v('f_o'), done: false };
    TASKS.push(t); if (t.deal) logAct({ deal: t.deal, ty: 'task' });
    toast('Task added'); render();
  }
}

/* ---------- peek drawers ---------- */
const drawerHead = (label, r) => `<div class="drawer-h"><span class="grow">${label}</span>${r ? `<button class="btn sm" data-a="go" data-r="${r}">${icon('maximize-2', 'sm tint')}Open record</button>` : ''}<button class="icon-btn" data-a="closeDrawer" aria-label="Close">${icon('x')}</button></div>`;
const kv = rows => `<dl class="fields" style="margin:0">${rows.map(([k, v]) => `<dt>${k}</dt><dd><div class="fval" style="cursor:default"><span class="tx">${v}</span></div></dd>`).join('')}</dl>`;
function peekLead(id) {
  const l = byId(LEADS, id);
  openDrawer(`${drawerHead('Lead', 'lead-' + l.id)}<div class="drawer-b">
    <div class="peek-top"><span class="avatar xl b-${l.c}">${initials(l.n)}</span><div><h2>${esc(l.n)}</h2><div class="sub">${esc(l.t)} at ${esc(l.co)}</div></div></div>
    <div class="peek-acts"><button class="btn sm" data-a="copy" data-v="${esc(l.e)}">${icon('mail', 'sm c-blue')}Email</button><button class="btn sm" data-a="copy" data-v="${esc(l.p)}">${icon('phone', 'sm c-green')}Call</button></div>
    <div class="side-sec"><h4>Status</h4><div class="seg" style="flex-wrap:wrap">${['New', 'Contacted', 'Qualified', 'Unqualified'].map(s => `<button class="${l.st === s ? 'on' : ''}" data-a="leadStatus" data-id="${l.id}" data-v="${s}">${s}</button>`).join('')}</div></div>
    <div class="side-sec"><h4>Details</h4>${kv([['Score', score(l.sc)], ['Email', esc(l.e)], ['Mobile', `<span class="num">${esc(l.p)}</span>`], ['Source', l.src], ['Owner', `${avatar(l.o, 'sm')}${USERS[l.o].n}`], ['Created', `<span class="num">${fmtDate(l.created)}</span>`]])}</div>
  </div><div class="drawer-f">${l.st === 'Converted' ? '<span class="muted" style="margin-right:auto;align-self:center">Converted to a deal</span>' : ''}<button class="btn" data-a="closeDrawer">Close</button>${l.st !== 'Converted' ? `<button class="btn primary" data-a="convertLead" data-id="${l.id}">${icon('arrow-right', 'sm')}Convert to deal</button>` : ''}</div>`);
}
function peekContact(id) {
  const c = contact(id), o = org(c.org), ds = DEALS.filter(d => d.org === c.org);
  openDrawer(`${drawerHead('Contact', 'contact-' + c.id)}<div class="drawer-b">
    <div class="peek-top">${avatar(c.id, 'xl')}<div><h2>${esc(c.n)}</h2><div class="sub">${esc(c.t)} at ${esc(o.n)}</div></div></div>
    <div class="peek-acts"><button class="btn sm" data-a="copy" data-v="${esc(c.e)}">${icon('mail', 'sm c-blue')}Email</button><button class="btn sm" data-a="copy" data-v="${esc(c.p)}">${icon('phone', 'sm c-green')}Call</button><button class="btn sm" data-a="create" data-t="task">${icon('circle-check', 'sm c-amber')}Task</button></div>
    <div class="side-sec"><h4>Details</h4>${kv([['Email', esc(c.e)], ['Mobile', `<span class="num">${esc(c.p)}</span>`], ['Organization', esc(o.n)], ['Location', o.loc]])}</div>
    <div class="side-sec"><h4>Deals with ${esc(o.n.split(' ')[0])} ${o.n.split(' ')[1] ? esc(o.n.split(' ')[1]) : ''}</h4>${ds.map(d => `<button class="mini-card" data-a="go" data-r="deal-${d.id}"><div class="grow"><div class="nm">${esc(d.t)}</div><div class="sub num">${money(d.v)} · ${fmtDate(d.close)}</div></div>${stagePill(d.s)}</button>`).join('') || '<p class="muted">No deals yet.</p>'}</div>
  </div>`);
}
function peekOrg(id) {
  const o = org(id), cs = CONTACTS.filter(c => c.org === id), ds = DEALS.filter(d => d.org === id);
  openDrawer(`${drawerHead('Organization', 'org-' + o.id)}<div class="drawer-b">
    <div class="peek-top">${orgMark(o, 'lg')}<div><h2>${esc(o.n)}</h2><div class="sub">${o.ind} · ${o.loc}</div></div></div>
    <div class="side-sec"><h4>Details</h4>${kv([['Website', o.web ? `<span style="color:var(--accent)">${o.web}</span>` : '<span class="muted">Not set</span>'], ['Industry', o.ind], ['Location', o.loc], ['Open pipeline', `<span class="num">${money(ds.filter(isOpen).reduce((s, d) => s + d.v, 0))}</span>`], ['Won to date', `<span class="num c-green">${money(ds.filter(d => d.s === 'won').reduce((s, d) => s + d.v, 0))}</span>`]])}</div>
    <div class="side-sec"><h4>People</h4>${cs.map(c => `<button class="mini-card" data-a="peekContact" data-id="${c.id}">${avatar(c.id)}<div class="grow"><div class="nm">${esc(c.n)}</div><div class="sub">${esc(c.t)}</div></div></button>`).join('') || '<p class="muted">No contacts yet.</p>'}</div>
    <div class="side-sec"><h4>Deals</h4>${ds.map(d => `<button class="mini-card" data-a="go" data-r="deal-${d.id}"><div class="grow"><div class="nm">${esc(d.t)}</div><div class="sub num">${money(d.v)}</div></div>${stagePill(d.s)}</button>`).join('') || '<p class="muted">No deals yet.</p>'}</div>
  </div>`);
}

/* ---------- theme & sidebar ---------- */
function toggleTheme() { const t = isDark() ? 'light' : 'dark'; document.documentElement.dataset.theme = t; store.set('theme', t); render(); }
function toggleSidebar() { S.collapsed = !S.collapsed; $('#app').classList.toggle('collapsed', S.collapsed); store.set('collapsed', S.collapsed); renderSidebar(); }

/* ---------- menus ---------- */
const mi = (a, ic, label, attrs = '', hint = '') => `<button class="mi" data-a="${a}" ${attrs}>${icon(ic, 'sm')}${label}${hint ? `<span class="hint"><kbd>${hint}</kbd></span>` : ''}</button>`;

/* ---------- action map ---------- */
const A = {
  go: el => go(el.dataset.r),
  palette: () => openPalette(),
  collapse: () => toggleSidebar(),
  theme: () => { closePop(); toggleTheme(); },
  mobileNav: () => { $('#app').classList.add('mobile-open'); $('#scrim').classList.add('show'); },
  closeModal: () => closeModal(),
  closeDrawer: () => closeDrawer(),
  noop: () => { },
  allApps: el => openPop(el, `<div class="mh">Apps</div><div class="apps-grid">${APPS.map(x => `<button class="apps-it ${x.on ? 'on' : ''}" data-a="${x.on ? 'go' : 'app'}" data-r="home" data-n="${x.n}"><span class="qa-ic b-${x.c}" style="width:36px;height:36px;border-radius:10px">${icon(x.ic)}</span><span>${x.n}</span></button>`).join('')}</div>`, '', 'left'),
  app: el => (closePop(), toast(`${el.dataset.n} is a separate app. This sample covers CRM only.`, { ic: 'package' })),
  wsMenu: el => openPop(el, `<div class="mh">Workspaces</div><button class="mi">${'<span class="logo" style="width:20px;height:20px;font-size:10px;border-radius:5px">D</span>'}Dxbitz<span class="hint">${icon('check', 'sm c-blue')}</span></button><button class="mi" data-a="app" data-n="Workspace switching"><span class="logo" style="width:20px;height:20px;font-size:10px;border-radius:5px;background:var(--t-teal)">S</span>Sandbox</button><hr>${mi('app', 'settings', 'Workspace settings', 'data-n="Settings"')}`, '', 'left'),
  meMenu: el => openPop(el, `<div class="mh">Signed in as Aslam K</div>${mi('app', 'user', 'Profile', 'data-n="Profile"')}${mi('theme', isDark() ? 'sun' : 'moon', isDark() ? 'Light theme' : 'Dark theme')}${mi('shortcuts', 'command', 'Keyboard shortcuts', '', '?')}<hr>${mi('app', 'log-out', 'Sign out', 'data-n="Sign out"')}`, '', 'left'),
  newMenu: el => openPop(el, `${mi('create', 'handshake', 'Deal', 'data-t="deal"', 'C')}${mi('create', 'target', 'Lead', 'data-t="lead"')}${mi('create', 'user-plus', 'Contact', 'data-t="contact"')}${mi('create', 'circle-check', 'Task', 'data-t="task"')}`),
  notifs: el => openPop(el, `<div class="nh"><b>Notifications</b><button class="link" data-a="readAll">Mark all read</button></div>${NOTIFS.slice(0, 4).map(n => `<div class="ni ${n.unread ? 'unread' : ''}" style="cursor:pointer" data-a="openNotif" data-id="${n.id}"><span class="avatar b-${n.c}" style="border-radius:7px">${icon(n.ic, 'sm')}</span><div>${n.html}<div class="tm">${n.tm}</div></div></div>`).join('')}<div style="padding:10px 14px;text-align:center"><a class="link" data-a="go" data-r="inbox">Open inbox</a></div>`, 'notif'),
  readAll: () => { NOTIFS.forEach(n => n.unread = false); closePop(); render(); toast('All caught up'); },
  openNotif: el => { const n = byId(NOTIFS, el.dataset.id); n.unread = false; go(n.go); },
  shortcuts: () => { closePop(); openModal(`<div class="modal" style="width:min(460px,100%)"><div class="modal-h"><h2>Keyboard shortcuts</h2><button class="icon-btn" data-a="closeModal">${icon('x')}</button></div>
    <div style="padding:8px 20px 18px;display:grid;grid-template-columns:1fr auto;row-gap:10px;font-size:13px">${[['Search and commands', '⌘ K'], ['Create in current view', 'C'], ['Focus search', '/'], ['Go to home', 'G H'], ['Go to deals', 'G D'], ['Go to leads', 'G L'], ['Go to tasks', 'G T'], ['Toggle sidebar', '['], ['Save note or form', '⌘ Enter'], ['Close', 'Esc']].map(([a, k]) => `<span class="muted">${a}</span><span>${k.split(' ').map(x => `<kbd>${x}</kbd>`).join(' ')}</span>`).join('')}</div></div>`); },
  create: el => openCreate(el.dataset.t, { stage: el.dataset.stage, org: el.dataset.org }),
  savedView: el => { const v = el.dataset.v; if (v === 'hot') { S.leadTab = 'hot'; go('leads'); } else { S.dealTab = v; go('deals'); } },
  newView: () => toast('Saved views store any filter and sort combination', { ic: 'sparkles' }),
  dealView: el => { S.dealView = el.dataset.v; S.sel.clear(); store.set('dealView', S.dealView); render(); },
  dealTab: el => { S.dealTab = el.dataset.v; render(); },
  ownerFilter: el => { const u = el.dataset.u; S.dealOwners.has(u) ? S.dealOwners.delete(u) : S.dealOwners.add(u); render(); },
  rmTag: el => { S.dealTags.delete(el.dataset.t); render(); },
  clearFilters: () => { S.dealOwners.clear(); S.dealTags.clear(); S.dealQ = ''; render(); },
  filterMenu: el => openPop(el, `<div class="mh">Tags</div>${['Hot', 'Large'].map(t => `<button class="mi" data-a="toggleTag" data-t="${t}"><span class="check ${S.dealTags.has(t) ? 'on' : ''}" style="width:16px;height:16px;margin:0">${icon('check')}</span>${t}</button>`).join('')}<hr><div class="mh">Owner</div>${Object.keys(USERS).map(u => `<button class="mi" data-a="ownerFilterMenu" data-u="${u}"><span class="check ${S.dealOwners.has(u) ? 'on' : ''}" style="width:16px;height:16px;margin:0">${icon('check')}</span>${USERS[u].n}</button>`).join('')}`),
  toggleTag: el => { const t = el.dataset.t; S.dealTags.has(t) ? S.dealTags.delete(t) : S.dealTags.add(t); closePop(); render(); },
  ownerFilterMenu: el => { A.ownerFilter(el); closePop(); },
  sort: el => { const k = el.dataset.k; S.sort = { k, dir: S.sort.k === k ? -S.sort.dir : 1 }; render(); },
  openDeal: (el, e) => { if (justDragged) return; go('deal-' + (el.dataset.id)); },
  sel: (el, e) => { e.stopPropagation(); const id = el.dataset.id; S.sel.has(id) ? S.sel.delete(id) : S.sel.add(id); render(); },
  selAll: () => { const ids = filteredDeals().map(d => d.id), all = ids.every(i => S.sel.has(i)); ids.forEach(i => all ? S.sel.delete(i) : S.sel.add(i)); render(); },
  clearSel: () => { S.sel.clear(); render(); },
  bulkOwner: el => openPop(el, `<div class="mh">Assign to</div>${Object.keys(USERS).map(u => `<button class="mi" data-a="bulkOwnerSet" data-u="${u}">${avatar(u, 'sm')}${USERS[u].n}</button>`).join('')}`),
  bulkOwnerSet: el => { const n = S.sel.size; S.sel.forEach(id => deal(id).o = el.dataset.u); closePop(); S.sel.clear(); render(); toast(`${n} deals assigned to ${USERS[el.dataset.u].n}`); },
  bulkStage: el => openPop(el, `<div class="mh">Move to</div>${STAGES.map(s => `<button class="mi" data-a="bulkStageSet" data-s="${s.k}"><span class="pill b-${s.c}" style="height:18px;padding:0 6px"><span class="d"></span>${s.n}</span></button>`).join('')}`),
  bulkStageSet: el => { const n = S.sel.size; S.sel.forEach(id => { const d = deal(id); if (d.s !== el.dataset.s) { logAct({ deal: id, ty: 'stage', from: d.s, to: el.dataset.s }); d.s = el.dataset.s; d.pr = stageOf(d.s).p; } }); closePop(); S.sel.clear(); render(); toast(`${n} deals moved to ${stageOf(el.dataset.s).n}`); },
  bulkDelete: () => { const gone = DEALS.filter(d => S.sel.has(d.id)); gone.forEach(d => DEALS.splice(DEALS.indexOf(d), 1)); S.sel.clear(); render(); toast(`${gone.length} deals deleted`, { ic: 'trash-2', undo: () => { DEALS.push(...gone); render(); } }); },
  setStage: el => moveDeal(cur().id, el.dataset.s),
  markWon: () => moveDeal(cur().id, 'won'),
  reopen: () => moveDeal(cur().id, 'nego'),
  dealMore: el => openPop(el, `${mi('markLost', 'circle-x', 'Mark as lost')}${mi('dupDeal', 'copy', 'Duplicate')}${mi('copyLink', 'link', 'Copy link')}<hr>${mi('deleteDeal', 'trash-2', 'Delete deal', 'style="color:var(--t-rose)"')}`),
  markLost: () => { closePop(); moveDeal(cur().id, 'lost'); },
  dupDeal: () => { closePop(); const d = deal(cur().id), n = { ...d, id: uid('d'), t: d.t + ' (copy)', tags: [...d.tags], created: todayISO() }; DEALS.push(n); toast('Deal duplicated'); go('deal-' + n.id); },
  copyLink: () => { closePop(); copyText(location.href.split('#')[0] + '#deal-' + cur().id, 'Link copied'); },
  deleteDeal: () => { closePop(); const d = deal(cur().id), i = DEALS.indexOf(d); DEALS.splice(i, 1); go('deals'); toast('Deal deleted', { ic: 'trash-2', undo: () => { DEALS.splice(i, 0, d); render(); } }); },
  recTab: el => { S.recTab = el.dataset.v; render(); },
  composerMode: el => { S.composer = el.dataset.v; render(); setTimeout(() => $('#cmpText')?.focus(), 0); },
  submitComposer: () => submitComposer(),
  attachHint: () => { S.recTab = 'files'; render(); },
  mentionHint: () => { const t = $('#cmpText'); t.value += (t.value && !t.value.endsWith(' ') ? ' ' : '') + '@Sara Mathew '; t.focus(); },
  toggleTask: (el, e) => { e.stopPropagation(); const t = byId(TASKS, el.dataset.id); t.done = !t.done; render(); if (t.done) toast('Task completed', { undo: () => { t.done = false; render(); } }); },
  taskTab: el => { S.taskTab = el.dataset.v; render(); },
  leadTab: el => { S.leadTab = el.dataset.v; render(); },
  peekLead: el => peekLead(el.dataset.id),
  peekContact: (el, e) => { if (e.target.closest('[data-a="copy"]')) return; peekContact(el.dataset.id); },
  peekOrg: el => peekOrg(el.dataset.id),
  leadStatus: el => { const l = byId(LEADS, el.dataset.id); l.st = el.dataset.v; render(); peekLead(l.id); toast(`Status set to ${l.st}`); },
  convertLead: el => {
    const l = byId(LEADS, el.dataset.id);
    let o = ORGS.find(x => x.n === l.co); if (!o) { o = { id: uid('o'), n: l.co, ind: 'Not set', loc: 'UAE', c: l.c, web: l.e.split('@')[1] || '' }; ORGS.push(o); }
    const c = { id: uid('c'), n: l.n, t: l.t, org: o.id, e: l.e, p: l.p, c: l.c }; CONTACTS.push(c);
    const d = { id: uid('d'), t: `${l.co.split(' ').slice(0, 2).join(' ')} ERP`, org: o.id, ct: c.id, v: 50000, s: 'qual', o: l.o, close: toISO(new Date(now().getTime() + 45 * 864e5)), pr: 10, tags: l.sc >= 70 ? ['Hot'] : [], src: l.src, created: todayISO() };
    DEALS.push(d); logAct({ deal: d.id, ty: 'created', text: 'Converted from lead' }); l.st = 'Converted';
    closeDrawer(); toast(`${esc(l.n)} converted. Deal created.`, { ic: 'sparkles' }); go('deal-' + d.id);
  },
  copy: (el, e) => { e.stopPropagation(); copyText(el.dataset.v, `Copied ${el.dataset.v}`); },
  fakeDl: el => toast(`${esc(el.dataset.n)} is sample data`, { ic: 'file-text' }),
  importHint: () => toast('Import takes CSV or Excel with column mapping', { ic: 'upload' }),
};
function copyText(t, msg) {
  if (!t) return;
  const fallback = () => toast(esc(t), { ic: 'copy' });
  try { navigator.clipboard.writeText(t).then(() => toast(esc(msg)), fallback); } catch { fallback(); }
}
/* ---------- global events ---------- */
document.addEventListener('click', e => {
  const t = e.target.closest('[data-a]');
  if (!e.target.closest('#pop') && !(t && t.dataset.a && ['newMenu', 'notifs', 'wsMenu', 'meMenu', 'filterMenu', 'bulkOwner', 'bulkStage', 'dealMore', 'allApps', 'docMore', 'tagMenu', 'gColsMenu'].includes(t.dataset.a))) closePop();
  if (!t) return;
  if (t.tagName === 'A') e.preventDefault();
  const fn = A[t.dataset.a]; if (fn) fn(t, e);
});
$('#scrim').addEventListener('click', closeAll);
document.addEventListener('input', e => {
  const k = e.target.dataset.search; if (!k) return;
  S[k] = e.target.value; const pos = e.target.selectionStart;
  render();
  const el = document.getElementById(k); if (el) { el.focus(); el.setSelectionRange(pos, pos); }
});
document.addEventListener('change', e => {
  if (e.target.id === 'fileUp' && e.target.files[0]) {
    const f = e.target.files[0], ext = (f.name.split('.').pop() || 'FILE').slice(0, 4).toUpperCase();
    (FILES[cur().id] = FILES[cur().id] || []).unshift({ n: f.name, s: f.size > 1e6 ? (f.size / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(f.size / 1e3)) + ' KB', by: ME, at: fmtDate(todayISO()), ext, c: 'blue' });
    logAct({ ...(cur().dt === 'deal' ? { deal: cur().id } : { ref: `${S.route}:${cur().id}` }), ty: 'file', text: f.name }); render(); toast('File attached');
  }
});
document.addEventListener('focusout', e => {
  if (e.target.id === 'recTitle') { const T = DT[e.target.dataset.dt], d = T.get(e.target.dataset.id), v = e.target.textContent.trim(); if (v && v !== d[T.tk]) { d[T.tk] = v; renderTopbar(); toast('Title updated'); } else e.target.textContent = d[T.tk]; }
});
document.addEventListener('keydown', e => {
  if (e.target.id === 'quickTask' && e.key === 'Enter' && e.target.value.trim()) {
    TASKS.push({ id: uid('t'), t: e.target.value.trim(), ty: 'todo', due: todayISO(), tm: '', deal: '', o: ME, done: false });
    S.taskTab = 'today'; render(); $('#quickTask')?.focus(); toast('Task added'); return;
  }
  if (e.target.id === 'recTitle' && e.key === 'Enter') { e.preventDefault(); e.target.blur(); return; }
});
let gPending = false;
document.addEventListener('keydown', e => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#palIn') ? closeModal() : openPalette(); return; }
  if (mod && e.key === 'Enter') {
    if ($('#createForm')) { e.preventDefault(); $('#createForm').requestSubmit(); return; }
    if (document.activeElement?.id === 'cmpText') { e.preventDefault(); submitComposer(); return; }
  }
  if (e.key === 'Escape') { if (S.fabOpen && !$('#pop').classList.contains('show')) { S.fabOpen = false; renderFab(); return; } if (GS.max && !$('#pop').classList.contains('show') && !$('#modalWrap').classList.contains('show')) { GS.max = false; render(); return; } if ($('#pop').classList.contains('show')) closePop(); else if ($('#modalWrap').classList.contains('show')) closeModal(); else if ($('#drawer').classList.contains('show')) closeDrawer(); else if (S.sel.size) { S.sel.clear(); render(); } return; }
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if (typing || mod || e.altKey || $('#modalWrap').classList.contains('show')) return;
  const k = e.key.toLowerCase();
  if (gPending) { gPending = false; const m = { h: 'home', d: 'deals', l: 'leads', t: 'tasks', c: 'contacts', o: 'orgs', r: 'reports', i: 'inbox' }[k]; if (m) { e.preventDefault(); go(m); } return; }
  if (k === 'g') { gPending = true; setTimeout(() => gPending = false, 900); return; }
  if (k === '/') { const s = $('[data-search]'); if (s) { e.preventDefault(); s.focus(); } return; }
  if (k === 'c') { e.preventDefault(); if (S.route === 'invoices' || S.route === 'invoice') { A.newInvoice(); return; } openCreate({ leads: 'lead', contacts: 'contact', orgs: 'contact', tasks: 'task' }[S.route] || 'deal'); return; }
  if (k === '[') { toggleSidebar(); return; }
  if (e.key === '?') { A.shortcuts(); return; }
  if (e.key === 'Enter' && document.activeElement?.classList.contains('card')) { go('deal-' + document.activeElement.dataset.id); }
});
window.addEventListener('hashchange', () => applyRoute());
window.addEventListener('resize', closePop);

