
/* ---------- helpers ---------- */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const icon = (n, cls = '') => `<svg class="i ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n] || ''}</svg>`;
const uid = p => p + Math.random().toString(36).slice(2, 8);
const byId = (arr, id) => arr.find(x => x.id === id);
const org = id => byId(ORGS, id);
const contact = id => byId(CONTACTS, id);
const deal = id => byId(DEALS, id);

const pad = n => String(n).padStart(2, '0');
const toISO = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseISO = s => { const [y, m, d] = s.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
const fmtDate = s => s ? s.slice(0, 10).split('-').reverse().join('-') : '';
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DOW = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const todayISO = () => toISO(now());
const dayDiff = s => Math.round((parseISO(s) - parseISO(todayISO())) / 864e5);
const shortDate = s => { const d = parseISO(s); return `${pad(d.getDate())} ${MON[d.getMonth()]}`; };
const hhmm = s => s.slice(11, 16);
const relDue = s => { const n = dayDiff(s); return n === 0 ? 'Today' : n === 1 ? 'Tomorrow' : n === -1 ? 'Yesterday' : n < 0 ? `${-n} days overdue` : n < 7 ? DOW[parseISO(s).getDay()] : fmtDate(s); };
const relTime = s => {
  const d = new Date(s), m = Math.round((now() - d) / 6e4);
  if (m < 1) return 'Just now'; if (m < 60) return `${m}m ago`;
  const dd = dayDiff(s.slice(0, 10));
  if (dd === 0) return `${Math.round(m / 60)}h ago`;
  if (dd === -1) return `Yesterday, ${hhmm(s)}`;
  return fmtDate(s);
};
const dayLabel = s => { const n = dayDiff(s); return n === 0 ? 'Today' : n === -1 ? 'Yesterday' : `${DOW[parseISO(s).getDay()]}, ${fmtDate(s)}`; };
const nowStamp = () => { const d = now(); return `${toISO(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };

const money = n => 'AED ' + Math.round(n).toLocaleString('en-US');
const compact = n => n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 1 : 2).replace(/\.?0+$/, '') + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'K' : String(n);
const avatar = (u, cls = '') => { const x = USERS[u] || contact(u); const ini = x.i || initials(x.n); return `<span class="avatar ${cls} b-${x.c}" title="${esc(x.n)}">${ini}</span>`; };
const initials = n => n.split(/\s+/).filter(w => /^[A-Za-z]/.test(w)).slice(0, 2).map(w => w[0]).join('').toUpperCase();
const orgMark = (o, cls = '') => `<span class="org-mark ${cls} b-${o.c}">${esc(o.n[0])}</span>`;
const personName = id => (USERS[id] || contact(id) || { n: 'Someone' }).n;
const stagePill = k => { const s = stageOf(k); return `<span class="pill b-${s.c}"><span class="d"></span>${s.n}</span>`; };
const isOpen = d => d.s !== 'won' && d.s !== 'lost';

/* ---------- state ---------- */
const S = {
  route: 'home', param: null,
  dealView: 'board', dealTab: 'all', dealOwners: new Set(), dealTags: new Set(), dealQ: '',
  sort: { k: 'close', dir: 1 }, sel: new Set(),
  recTab: 'activity', composer: 'note', layoutMode: 'sample', fabOpen: false,
  leadTab: 'all', leadQ: '', contactQ: '', orgQ: '', taskTab: 'today',
  collapsed: false,
};
const store = {
  get(k, d) { try { const v = localStorage.getItem('crmws.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('crmws.' + k, JSON.stringify(v)); } catch { } },
};

/* ---------- routing ---------- */
const ROUTES = {
  home: { n: 'Home', ic: 'house', c: 'slate' },
  inbox: { n: 'Inbox', ic: 'inbox', c: 'blue' },
  tasks: { n: 'My tasks', ic: 'circle-check', c: 'amber' },
  leads: { n: 'Leads', ic: 'target', c: 'teal', grp: 'CRM' },
  deals: { n: 'Deals', ic: 'handshake', c: 'blue', grp: 'CRM' },
  contacts: { n: 'Contacts', ic: 'users', c: 'violet', grp: 'CRM' },
  orgs: { n: 'Organizations', ic: 'building-2', c: 'amber', grp: 'CRM' },
  invoices: { n: 'Invoices', ic: 'receipt', c: 'green', grp: 'CRM' },
  reports: { n: 'Reports', ic: 'chart-column', c: 'rose', grp: 'CRM' },
  layout: { n: 'Doc layout', ic: 'columns-3', c: 'slate', grp: 'Workspace' },
};
const DOC_RX = /^(deal|lead|contact|org|invoice)-(.+)$/;
function splitRoute(h) {
  const m = h.match(DOC_RX);
  if (m && DT[m[1]] && DT[m[1]].get(m[2])) return [m[1], m[2]];
  if (ROUTES[h]) return [h, null];
  return ['home', null];
}
const parseHash = () => splitRoute((location.hash || '').slice(1));
function go(r) {
  closeAll();
  if (('#' + r) === location.hash) { applyRoute(); return; }
  try { location.hash = r; } catch { applyRoute(r); }
  if (location.hash !== '#' + r) applyRoute(r);
}
function applyRoute(r) {
  const [route, param] = r ? splitRoute(r) : parseHash();
  const changed = route !== S.route || param !== S.param;
  S.route = route; S.param = param;
  if (changed) { S.sel.clear(); S.recTab = S.nextTab || 'activity'; S.nextTab = null; gridReset(); }
  render(changed);
  if (changed) $('#view').scrollTop = 0;
  document.getElementById('app').classList.remove('mobile-open');
  $('#scrim').classList.remove('show');
}
const isDocRoute = () => !!DT[S.route];

/* ---------- render shell ---------- */
function render(resetScroll) {
  const v = $('#view'), st = v.scrollTop;
  gridSaveScroll();
  renderSidebar(); renderTopbar();
  v.innerHTML = VIEWS[S.route]();
  if (!resetScroll) v.scrollTop = st;
  renderBulk(); renderFab();
  afterRender();
}
function afterRender() {
  if (S.route === 'deals' && S.dealView === 'board') wireBoard();
  $$('table.dt[data-tk]').forEach(wireListTable);
  gridAfterRender();
}

function navItem(key, extra = '') {
  const r = ROUTES[key], on = S.route === key || (DT[S.route] && DT[S.route].list === key);
  return `<button class="nav-item ${on ? 'active' : ''}" data-a="go" data-r="${key}" ${S.collapsed ? `data-tip="${r.n}"` : ''}>${icon(r.ic, 'c-' + r.c)}<span class="lbl">${r.n}</span>${extra}</button>`;
}
const APPS = [
  { n: 'CRM', ic: 'handshake', c: 'blue', on: true },
  { n: 'Sales', ic: 'receipt', c: 'green' },
  { n: 'Accounting', ic: 'wallet', c: 'teal' },
  { n: 'Inventory', ic: 'package', c: 'amber' },
  { n: 'HR and Payroll', ic: 'user-cog', c: 'violet' },
];
function renderSidebar() {
  const unread = NOTIFS.filter(n => n.unread).length;
  const myOpen = TASKS.filter(t => t.o === ME && !t.done && dayDiff(t.due) <= 0).length;
  const openDeals = DEALS.filter(isOpen).length;
  const newLeads = LEADS.filter(l => l.st === 'New').length;
  $('#sidebar').innerHTML = `
  <div class="ws">
    <button class="ws-btn" data-a="wsMenu" aria-label="Switch workspace"><span class="logo">D</span><span class="ws-text" style="min-width:0;flex:1"><div class="ws-name">Dxbitz</div><div class="ws-sub">Workspace</div></span>${icon('chevrons-up-down', 'sm ws-chev')}</button>
  </div>
  <div class="appbar" role="tablist" aria-label="Apps">
    ${APPS.map(a => `<button class="ab-app ${a.on ? 'on' : ''}" role="tab" aria-selected="${!!a.on}" data-a="${a.on ? 'go' : 'app'}" data-r="home" data-n="${a.n}" data-tip="${a.n}" aria-label="${a.n}"><span class="b-${a.c}">${icon(a.ic, 'sm')}</span></button>`).join('')}
    <span class="grow"></span>
    <button class="ab-app ab-more" data-a="allApps" data-tip="All apps" aria-label="All apps"><span>${icon('layout-grid', 'sm')}</span></button>
  </div>
  <div class="side-body">
    <div class="side-app"><b>CRM</b><span>Dxbitz</span></div>
    <nav class="nav">
      ${navItem('home')}
      ${navItem('inbox', unread ? `<span class="badge">${unread}</span>` : '')}
      ${navItem('tasks', myOpen ? `<span class="count">${myOpen}</span>` : '')}
      <div class="nav-group">
        <div class="nav-label">Records</div>
        ${navItem('leads', `<span class="count">${newLeads}</span>`)}
        ${navItem('deals', `<span class="count">${openDeals}</span>`)}
        ${navItem('invoices', (n => n ? `<span class="count">${n}</span>` : '')(INVOICES.filter(i => i.status === 'issued').length))}
        ${navItem('contacts')}
        ${navItem('orgs')}
        ${navItem('reports')}
      </div>
      <div class="nav-group">
        <div class="nav-label">Saved views</div>
        <button class="nav-item" data-a="savedView" data-v="month"><span class="dot" style="background:var(--t-blue)"></span><span class="lbl">Closing this month</span></button>
        <button class="nav-item" data-a="savedView" data-v="large"><span class="dot" style="background:var(--t-violet)"></span><span class="lbl">Large deals</span></button>
        <button class="nav-item" data-a="savedView" data-v="hot"><span class="dot" style="background:var(--t-rose)"></span><span class="lbl">Hot leads</span></button>
      </div>
    </nav>
    <div class="side-bottom">${navItem('layout')}${navItem('library')}</div>
    <div class="side-foot">
      <button class="me" data-a="meMenu">${avatar(ME)}<div style="min-width:0"><div class="n">Aslam K</div><div class="e">Admin</div></div></button>
      <button class="icon-btn" data-a="theme" data-tip="${isDark() ? 'Light' : 'Dark'} theme" aria-label="Toggle theme">${icon(isDark() ? 'sun' : 'moon')}</button>
    </div>
  </div>`;
}
function renderTopbar() {
  let crumbs;
  if (isDocRoute()) {
    const t = DT[S.route], d = t.get(S.param);
    crumbs = `<a href="#${t.list}" data-a="go" data-r="${t.list}" class="hide-sm">${ROUTES[t.list].n}</a>${icon('chevron-right', 'xs hide-sm')}<span class="cur">${esc(t.title(d))}</span>`;
  } else {
    const r = ROUTES[S.route];
    crumbs = (r.grp ? `<span class="hide-sm" style="padding:3px 6px">${r.grp}</span>${icon('chevron-right', 'xs hide-sm')}` : '') + `<span class="cur">${r.n}</span>`;
  }
  const unread = NOTIFS.some(n => n.unread);
  $('#topbar').innerHTML = `
    <button class="icon-btn" data-a="sideToggle" data-tip="Toggle sidebar  [" aria-label="Toggle sidebar">${icon('panel-left')}</button>
    <span class="sep desktop-only" style="margin:0 2px"></span>
    <div class="crumbs">${crumbs}</div>
    <div class="top-actions">
      <button class="top-search desktop-only" data-a="palette">${icon('search', 'sm')}<span>Search</span><kbd>⌘K</kbd></button>
      <button class="icon-btn mobile-only" data-a="palette" aria-label="Search">${icon('search')}</button>
      <button class="icon-btn" data-a="notifs" aria-label="Notifications" data-tip="Notifications" style="position:relative">${icon('bell')}${unread ? '<span style="position:absolute;top:6px;right:7px;width:7px;height:7px;border-radius:50%;background:var(--t-rose);box-shadow:0 0 0 2px var(--bg)"></span>' : ''}</button>
      <button class="icon-btn desktop-only" data-a="shortcuts" aria-label="Keyboard shortcuts" data-tip="Shortcuts  ?">${icon('circle-help')}</button>
      <span class="sep desktop-only"></span>
      <button class="btn primary sm" data-a="newMenu">${icon('plus', 'sm')}<span class="hide-sm">New</span>${icon('chevron-down', 'xs')}</button>
    </div>`;
}

/* ---------- theme ---------- */
const isDark = () => { const t = document.documentElement.dataset.theme; return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches; };

/* ---------- toast ---------- */
function toast(msg, opt = {}) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.innerHTML = `${icon(opt.ic || 'check', 'sm ok')}<span style="flex:1">${msg}</span>${opt.undo ? '<button>Undo</button>' : ''}`;
  if (opt.undo) el.querySelector('button').onclick = () => { opt.undo(); kill(); };
  $('#toasts').appendChild(el);
  const kill = () => { el.classList.add('out'); setTimeout(() => el.remove(), 180); };
  setTimeout(kill, opt.undo ? 5000 : 2800);
}

/* ---------- popover ---------- */
let popAnchor = null;
function openPop(anchor, html, cls = '', align = 'auto') {
  typeof hideTip === 'function' && hideTip();
  const p = $('#pop');
  if (popAnchor === anchor && p.classList.contains('show')) { closePop(); return; }
  p.className = 'pop ' + cls; p.innerHTML = html;
  const r = anchor.getBoundingClientRect();
  p.style.left = '0px'; p.style.top = '0px';
  const w = p.offsetWidth, h = p.offsetHeight;
  let left = align === 'left' || r.left + w < innerWidth - 12 ? r.left : r.right - w;
  left = Math.max(12, Math.min(left, innerWidth - w - 12));
  let top = r.bottom + 6; if (top + h > innerHeight - 12) top = Math.max(12, r.top - h - 6);
  p.style.left = left + 'px'; p.style.top = top + 'px';
  requestAnimationFrame(() => p.classList.add('show'));
  popAnchor = anchor;
}
function closePop() { $('#pop').classList.remove('show'); popAnchor = null; }

/* ---------- drawer ---------- */
function openDrawer(html) {
  const d = $('#drawer'); d.innerHTML = html; d.classList.add('show'); d.setAttribute('aria-hidden', 'false');
  $('#scrim').classList.add('show');
}
function closeDrawer() { $('#drawer').classList.remove('show'); $('#drawer').setAttribute('aria-hidden', 'true'); if (!$('#app').classList.contains('mobile-open')) $('#scrim').classList.remove('show'); }

/* ---------- modal ---------- */
function openModal(html, onOpen) {
  const w = $('#modalWrap'); w.innerHTML = html; $('#scrim').classList.add('show');
  onOpen && onOpen(w); requestAnimationFrame(() => w.classList.add('show'));
}
function closeModal() { const w = $('#modalWrap'); w.classList.remove('show'); setTimeout(() => { if (!w.classList.contains('show')) w.innerHTML = ''; }, 160); if (!$('#drawer').classList.contains('show')) $('#scrim').classList.remove('show'); }
function closeAll() { closePop(); closeModal(); closeDrawer(); $('#app').classList.remove('mobile-open'); $('#scrim').classList.remove('show'); }

/* ---------- bulk bar ---------- */
function renderBulk() {
  const b = $('#bulk'), n = S.sel.size;
  if (S.route !== 'deals' || S.dealView !== 'list' || !n) { b.classList.remove('show'); return; }
  b.innerHTML = `<span class="n">${n} selected</span>
    <button data-a="bulkOwner">${icon('user', 'sm')}Assign</button>
    <button data-a="bulkStage">${icon('arrow-right', 'sm')}Move stage</button>
    <button data-a="bulkDelete">${icon('trash-2', 'sm')}Delete</button>
    <button data-a="clearSel" title="Clear selection">${icon('x', 'sm')}</button>`;
  b.classList.add('show');
}

/* ---------- sparkline ---------- */
function spark(vals, color) {
  const w = 96, h = 30, mx = Math.max(...vals), mn = Math.min(...vals), rg = mx - mn || 1;
  const pts = vals.map((v, i) => [i * (w - 4) / (vals.length - 1) + 2, h - 4 - (v - mn) / rg * (h - 8)]);
  const line = pts.map(p => p.map(n => n.toFixed(1)).join(',')).join(' ');
  const last = pts[pts.length - 1];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <polygon points="2,${h} ${line} ${w - 2},${h}" fill="var(--t-${color}-bg)"/>
    <polyline points="${line}" fill="none" stroke="var(--t-${color})" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="2.6" fill="var(--t-${color})" stroke="var(--surface)" stroke-width="1.5"/></svg>`;
}
