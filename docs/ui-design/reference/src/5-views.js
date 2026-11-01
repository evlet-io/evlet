
/* ---------- views ---------- */
const pageHead = (key, title, count, actions = '') => {
  const r = ROUTES[key];
  return `<div class="ph"><span class="ph-icon b-${r.c}">${icon(r.ic, 'lg')}</span><h1>${title}${count != null ? ` <span class="count num">${count}</span>` : ''}</h1><span class="grow"></span><div class="ph-actions">${actions}</div></div>`;
};
const emptyState = (ic, c, h, p, btn = '') => `<div class="empty"><div class="ei b-${c}">${icon(ic, 'lg')}</div><h3>${h}</h3><p>${p}</p>${btn}</div>`;
const actIcon = { call: ['phone', 'green'], email: ['mail', 'blue'], meeting: ['video', 'violet'], note: ['sticky-note', 'amber'], stage: ['arrow-right', 'slate'], created: ['sparkles', 'slate'], won: ['trophy', 'green'], lost: ['circle-x', 'rose'], task: ['circle-check', 'teal'], file: ['paperclip', 'slate'] };

function actSentence(a, withDeal) {
  const who = `<b>${esc(personName(a.by))}</b>`, d = deal(a.deal) || { t: actTarget(a)?.t || '' }, tg = actTarget(a), on = withDeal && tg ? ` on <a data-a="go" data-r="${tg.r}"><b>${esc(tg.t)}</b></a>` : '';
  switch (a.ty) {
    case 'call': return `${who} logged a call${on}`;
    case 'email': return USERS[a.by] ? `${who} sent an email${on}` : `${who} replied by email${on}`;
    case 'meeting': return `${who} logged a meeting${on}`;
    case 'note': return `${who} added a note${on}`;
    case 'stage': return a.lead ? `${who} changed status${on} from ${esc(a.from)} to <b>${esc(a.to)}</b>` : `${who} moved${on || ' this deal'} from ${stageOf(a.from).n} to <b>${stageOf(a.to).n}</b>`;
    case 'created': return `${who} created ${withDeal ? `<b>${esc(d.t)}</b>` : 'this deal'}${a.text ? ` <span class="muted">· Source: ${esc(a.text)}</span>` : ''}`;
    case 'won': return `${who} marked ${withDeal ? `<b>${esc(d.t)}</b>` : 'this deal'} as <b style="color:var(--t-green)">Won</b>`;
    case 'lost': return `${who} marked ${withDeal ? `<b>${esc(d.t)}</b>` : 'this deal'} as <b style="color:var(--t-rose)">Lost</b>`;
    case 'task': return `${who} added a task${on}`;
    case 'file': return `${who} attached <b>${esc(a.text)}</b>`;
  }
  return who;
}

const VIEWS = {};

/* HOME */
VIEWS.home = () => {
  const open = DEALS.filter(isOpen), pipe = open.reduce((s, d) => s + d.v, 0);
  const wonOct = DEALS.filter(d => d.s === 'won' && d.close.startsWith('2026-10')).reduce((s, d) => s + d.v, 0);
  const newLeads = LEADS.filter(l => dayDiff(l.created) >= -7).length;
  const mine = TASKS.filter(t => t.o === ME).sort((a, b) => a.done - b.done || a.due.localeCompare(b.due) || (a.tm || '99').localeCompare(b.tm || '99'));
  const dueToday = mine.filter(t => !t.done && dayDiff(t.due) <= 0).length;
  const closingWeek = open.filter(d => { const n = dayDiff(d.close); return n >= 0 && n <= 7; }).length;
  const hr = now().getHours(), greet = hr < 12 ? 'Good morning' : hr < 17 ? 'Good afternoon' : 'Good evening';
  const kpi = (ic, c, label, val, unit, delta, up, vals) => `<div class="kpi"><div class="kpi-top"><span class="ic b-${c}">${icon(ic, 'sm')}</span>${label}</div>
    <div class="kpi-foot"><div><div class="kpi-val num">${unit ? `<small>${unit}</small>` : ''}${val}</div><span class="delta ${up ? 'up' : 'down'}">${icon(up ? 'trending-up' : 'trending-down', 'xs')}${delta}</span> <span class="muted" style="font-size:12px">vs last month</span></div>${spark(vals, c)}</div></div>`;
  const byStage = STAGES.filter(s => s.k !== 'won').map(s => { const ds = open.filter(d => d.s === s.k); return { s, n: ds.length, v: ds.reduce((a, d) => a + d.v, 0) }; });
  const closing = open.slice().sort((a, b) => a.close.localeCompare(b.close)).slice(0, 5);
  const feed = ACTS.slice().sort((a, b) => b.at.localeCompare(a.at)).slice(0, 6);
  return `<div class="page">
    <div class="hello"><div><h1>${greet}, Aslam</h1><p>${DOW[now().getDay()]}, ${fmtDate(todayISO())} · ${dueToday} tasks due today · ${closingWeek} deals closing this week</p></div>
      <div class="quick">
        <button class="btn" data-a="create" data-t="task">${icon('phone', 'sm c-green')}Log a call</button>
        <button class="btn" data-a="create" data-t="lead">${icon('target', 'sm c-teal')}New lead</button>
        <button class="btn primary" data-a="create" data-t="deal">${icon('plus', 'sm')}New deal</button>
      </div></div>
    <div class="kpis">
      ${kpi('wallet', 'blue', 'Open pipeline', compact(pipe), 'AED', '12.4%', true, [610, 680, 720, 690, 810, 860, 940, 1010, 980, 1090, 1150, pipe / 1000])}
      ${kpi('trophy', 'green', 'Won in October', compact(wonOct), 'AED', '8.1%', true, [40, 62, 55, 80, 71, 96, 88, 104, 112])}
      ${kpi('target', 'teal', 'New leads, 7 days', newLeads, '', '+3', true, [4, 6, 5, 7, 6, 5, 8, 7, newLeads])}
      ${kpi('activity', 'violet', 'Win rate, 90 days', '38%', '', '2.1 pts', false, [44, 42, 45, 41, 43, 40, 39, 40, 38])}
    </div>
    <div class="grid-2">
      <div class="col">
        <section class="panel"><div class="panel-h"><h3>Pipeline by stage</h3><a class="link" data-a="go" data-r="deals">Open board ${icon('arrow-up-right', 'xs')}</a></div>
          <div class="panel-b">
            <div class="stagebar">${byStage.map(x => `<span style="flex:${x.v};background:var(--t-${x.s.c})" title="${x.s.n}: ${money(x.v)}"></span>`).join('')}</div>
            <div class="stage-rows">${byStage.map(x => `<button class="stage-row" data-a="go" data-r="deals"><span class="sq" style="background:var(--t-${x.s.c})"></span><span>${x.s.n} <span class="muted">· ${x.n} deals</span></span><span class="muted num" style="font-size:12px">${Math.round(x.v / pipe * 100)}%</span><span class="amt num">${money(x.v)}</span></button>`).join('')}</div>
          </div></section>
        <section class="panel"><div class="panel-h"><h3>Closing soon</h3><a class="link" data-a="savedView" data-v="month">All this month ${icon('arrow-up-right', 'xs')}</a></div>
          ${closing.map(d => { const n = dayDiff(d.close), o = org(d.org); return `<button class="close-row" data-a="go" data-r="deal-${d.id}">${orgMark(o)}<div style="min-width:0"><div class="nm">${esc(d.t)}</div><div class="sub">${esc(o.n)} · ${stageOf(d.s).n}</div></div><div style="text-align:right"><div class="num" style="font-weight:600">${money(d.v)}</div><div class="sub ${n < 0 ? 'due-over' : ''}">${relDue(d.close)}</div></div></button>`; }).join('')}
        </section>
      </div>
      <div class="col">
        <section class="panel"><div class="panel-h"><h3>My tasks</h3><a class="link" data-a="go" data-r="tasks">View all ${icon('arrow-up-right', 'xs')}</a></div>
          ${mine.slice(0, 6).map(taskRow).join('')}</section>
        <section class="panel"><div class="panel-h"><h3>Recent activity</h3></div>
          <div style="padding:4px 0">${feed.map(a => { const [ic, c] = actIcon[a.ty]; return `<div class="feed-item"><span class="ic b-${c}">${icon(ic, 'sm')}</span><div class="tx">${actSentence(a, true)}<div class="tm">${relTime(a.at)}</div></div></div>`; }).join('')}</div></section>
      </div>
    </div></div>`;
};

function taskRow(t) {
  const [ic, c] = TASK_TY[t.ty], rr = refRoute(t), rt = refTitle(t), n = dayDiff(t.due);
  return `<div class="task-row ${t.done ? 'done' : ''}">
    <button class="check ${t.done ? 'on' : ''}" data-a="toggleTask" data-id="${t.id}" aria-label="Mark ${t.done ? 'open' : 'done'}">${icon('check')}</button>
    <div class="t"><div class="title">${esc(t.t)}</div>
      <div class="meta"><span class="c-${c}" style="display:inline-flex">${icon(ic, 'xs')}</span>
        <span class="${!t.done && n < 0 ? 'due-over' : ''}">${relDue(t.due)}${t.tm ? ', ' + t.tm : ''}</span>
        ${rr && rt ? `<span>·</span><a class="link" style="font-weight:500;color:var(--fg-2)" data-a="go" data-r="${rr}">${esc(rt)}</a>` : ''}</div></div>
    ${avatar(t.o, 'sm')}</div>`;
}

/* DEALS */
function filteredDeals() {
  const q = S.dealQ.trim().toLowerCase();
  return DEALS.filter(d => {
    if (S.dealTab === 'mine' && d.o !== ME) return false;
    if (S.dealTab === 'month' && !(d.close.startsWith(todayISO().slice(0, 7)) && isOpen(d))) return false;
    if (S.dealTab === 'large' && d.v < 100000) return false;
    if (S.dealOwners.size && !S.dealOwners.has(d.o)) return false;
    if (S.dealTags.size && !d.tags.some(t => S.dealTags.has(t))) return false;
    if (q && !(d.t + ' ' + org(d.org).n + ' ' + (contact(d.ct)?.n || '')).toLowerCase().includes(q)) return false;
    return d.s !== 'lost' || S.dealView === 'list';
  });
}
VIEWS.deals = () => {
  const list = filteredDeals(), open = list.filter(isOpen);
  const pipe = open.reduce((s, d) => s + d.v, 0), wt = open.reduce((s, d) => s + d.v * d.pr / 100, 0);
  const tabs = [['all', 'All deals', DEALS.filter(d => d.s !== 'lost').length], ['mine', 'My deals', DEALS.filter(d => d.o === ME && d.s !== 'lost').length], ['month', 'Closing this month', DEALS.filter(d => isOpen(d) && d.close.startsWith(todayISO().slice(0, 7))).length], ['large', 'Large deals', DEALS.filter(d => d.v >= 100000).length]];
  const chips = [...S.dealTags].map(t => `<span class="filter-chip">Tag: ${t}<button data-a="rmTag" data-t="${t}" aria-label="Remove filter">${icon('x', 'xs')}</button></span>`).join('');
  return `<div class="page">
    ${pageHead('deals', 'Deals', null, `
      <div class="seg" role="tablist"><button class="${S.dealView === 'board' ? 'on' : ''}" data-a="dealView" data-v="board">${icon('kanban', 'sm')}Board</button><button class="${S.dealView === 'list' ? 'on' : ''}" data-a="dealView" data-v="list">${icon('list', 'sm')}List</button></div>
      <button class="btn" data-a="filterMenu">${icon('sliders-horizontal', 'sm tint')}<span class="hide-sm">Filter</span></button>
      <button class="btn primary" data-a="create" data-t="deal">${icon('plus', 'sm')}<span class="hide-sm">New deal</span></button>`)}
    <div class="tabs">${tabs.map(([k, n, c]) => `<button class="${S.dealTab === k ? 'on' : ''}" data-a="dealTab" data-v="${k}">${n} <span class="n num">${c}</span></button>`).join('')}<button class="add" data-a="newView">${icon('plus', 'sm')}View</button></div>
    <div class="toolbar">
      <div class="search-field">${icon('search', 'sm')}<input class="input" id="dealQ" data-search="dealQ" placeholder="Search deals" value="${esc(S.dealQ)}" autocomplete="off"></div>
      <div class="avatars" role="group" aria-label="Filter by owner">${Object.keys(USERS).map(u => `<button data-a="ownerFilter" data-u="${u}" class="${S.dealOwners.has(u) ? 'on' : S.dealOwners.size ? 'dim' : ''}" title="${USERS[u].n}">${avatar(u)}</button>`).join('')}</div>
      ${chips}
      ${S.dealOwners.size || S.dealTags.size || S.dealQ ? `<button class="btn ghost sm" data-a="clearFilters">Clear</button>` : ''}
      <span class="grow"></span>
      <div class="summary"><span>Pipeline <b class="num">${money(pipe)}</b></span><span>Weighted <b class="num">${money(wt)}</b></span></div>
    </div>
    ${list.length ? (S.dealView === 'board' ? dealBoard(list) : dealTable(list)) : emptyState('search', 'slate', 'No deals match', 'Try a different search or clear the filters.', '<button class="btn" data-a="clearFilters" style="margin-top:12px">Clear filters</button>')}
  </div>`;
};
function dealCard(d) {
  const o = org(d.org), n = dayDiff(d.close), over = n < 0 && isOpen(d);
  return `<div class="card" draggable="true" data-id="${d.id}" data-a="openDeal" tabindex="0">
    ${d.tags.length ? `<div class="tags">${d.tags.map(t => `<span class="chip ${t === 'Hot' ? 'tag-hot' : ''}">${t === 'Hot' ? icon('flame', 'xs') : ''}${t}</span>`).join('')}</div>` : ''}
    <div class="t">${esc(d.t)}</div>
    <div class="o">${orgMark(o)}<span>${esc(o.n)}</span></div>
    <div class="f"><span class="v num">${money(d.v)}</span><span class="grow"></span><span class="dt ${over ? 'due-over' : ''}" title="Expected close ${fmtDate(d.close)}">${icon('calendar', 'xs')}${shortDate(d.close)}</span>${avatar(d.o, 'sm')}</div>
  </div>`;
}
function dealBoard(list) {
  const cols = STAGES.map(s => { const ds = list.filter(d => d.s === s.k).sort((a, b) => a.close.localeCompare(b.close)); return { s, ds, v: ds.reduce((a, d) => a + d.v, 0) }; });
  const mx = Math.max(...cols.map(c => c.v), 1);
  return `<div class="board-wrap"><div class="board">${cols.map(c => `
    <div class="col-k" data-stage="${c.s.k}">
      <div class="col-h"><div class="r1"><span class="pill b-${c.s.c}" style="height:20px;padding:0 7px"><span class="d"></span>${c.s.n}</span><span class="ct num">${c.ds.length}</span><span class="grow"></span><span class="amt num">${compact(c.v)}</span></div>
        <div class="prog"><span style="width:${c.v / mx * 100}%;background:var(--t-${c.s.c})"></span></div></div>
      <div class="col-b">${c.ds.map(dealCard).join('') || `<div class="col-empty">Drop deals here</div>`}
        <button class="col-add" data-a="create" data-t="deal" data-stage="${c.s.k}">${icon('plus', 'sm')}Add deal</button></div>
    </div>`).join('')}</div></div>`;
}
function dealTable(list) {
  const k = S.sort.k, dir = S.sort.dir, ord = s => s === 'lost' ? 9 : STAGES.findIndex(x => x.k === s);
  const val = { t: d => d.t.toLowerCase(), org: d => org(d.org).n, s: d => ord(d.s), v: d => d.v, pr: d => d.pr, close: d => d.close, o: d => USERS[d.o].n };
  const rows = list.slice().sort((a, b) => { const x = val[k](a), y = val[k](b); return (x > y ? 1 : x < y ? -1 : 0) * dir; });
  const th = (key, label, cls = '') => `<th class="sortable ${cls}" data-a="sort" data-k="${key}">${label}${k === key ? `<span class="si" style="${dir > 0 ? 'transform:rotate(180deg)' : ''}">${icon('chevron-down', 'xs')}</span>` : ''}</th>`;
  const all = rows.length && rows.every(d => S.sel.has(d.id));
  return `<div class="table-wrap"><table class="dt" data-tk="deals" data-stick="2">
    <thead><tr><th class="w-check"><button class="check ${all ? 'on' : ''}" data-a="selAll" aria-label="Select all">${icon('check')}</button></th>
      ${th('t', 'Deal')}${th('org', 'Organization')}${th('s', 'Stage')}${th('v', 'Amount', 'r')}${th('pr', 'Probability')}${th('close', 'Expected close')}${th('o', 'Owner')}</tr></thead>
    <tbody>${rows.map(d => { const o = org(d.org), n = dayDiff(d.close); return `<tr class="${S.sel.has(d.id) ? 'sel' : ''}" data-a="openDeal" data-id="${d.id}">
      <td class="w-check"><button class="check ${S.sel.has(d.id) ? 'on' : ''}" data-a="sel" data-id="${d.id}" aria-label="Select">${icon('check')}</button></td>
      <td><div class="primary-cell">${esc(d.t)}${d.tags.includes('Hot') ? `<span class="chip tag-hot">${icon('flame', 'xs')}Hot</span>` : ''}</div></td>
      <td><div class="cell-flex">${orgMark(o)}${esc(o.n)}</div></td>
      <td>${stagePill(d.s)}</td>
      <td class="r num" style="font-weight:550">${money(d.v)}</td>
      <td><div class="prob"><div class="bar"><span style="width:${d.pr}%"></span></div><span class="num muted">${d.pr}%</span></div></td>
      <td class="num ${n < 0 && isOpen(d) ? 'due-over' : ''}">${fmtDate(d.close)}</td>
      <td><div class="cell-flex">${avatar(d.o, 'sm')}${USERS[d.o].n}</div></td></tr>`; }).join('')}</tbody></table></div>
    <div class="dt-foot"><span>${rows.length} deals</span><span class="num">Total ${money(rows.reduce((s, d) => s + d.v, 0))}</span></div>`;
}

function taskCard(t) {
  const [ic, c] = TASK_TY[t.ty], n = dayDiff(t.due);
  return `<div class="mini-card"><span class="ph-icon b-${c}" style="width:28px;height:28px;border-radius:8px">${icon(ic, 'sm')}</span><div class="grow"><div class="nm">${esc(t.t)}</div><div class="sub ${n < 0 ? 'due-over' : ''}">${relDue(t.due)}${t.tm ? ', ' + t.tm : ''}</div></div><button class="check" data-a="toggleTask" data-id="${t.id}" aria-label="Mark done">${icon('check')}</button></div>`;
}
function composer(force) {
  const mode = force || S.composer;
  const modes = [['note', 'sticky-note', 'Note'], ['email', 'mail', 'Email'], ['call', 'phone', 'Log call'], ['task', 'circle-check', 'Task']];
  const ph = { note: 'Write a note. Use @ to mention a teammate.', email: 'Write an email to the primary contact', call: 'What was discussed on the call?', task: 'What needs to happen next?' }[mode];
  const btn = { note: 'Save note', email: 'Send email', call: 'Log call', task: 'Add task' }[mode];
  return `<div class="composer">
    ${force ? '' : `<div class="ct">${modes.map(([k, ic, n]) => `<button class="${mode === k ? 'on' : ''}" data-a="composerMode" data-v="${k}">${icon(ic, 'sm')}${n}</button>`).join('')}</div>`}
    ${mode === 'email' ? `<input class="input" id="cmpSubj" placeholder="Subject" style="border:0;box-shadow:none;border-bottom:1px solid var(--divider);border-radius:0;padding:0 14px;height:38px;font-weight:550">` : ''}
    <textarea id="cmpText" rows="2" placeholder="${ph}" data-mode="${mode}"></textarea>
    <div class="cf">${mode === 'task' ? `<input type="date" class="input" id="cmpDue" value="${toISO(new Date(now().getTime() + 864e5))}" style="width:auto;height:28px">` : `<button class="icon-btn" style="width:28px;height:28px" data-a="attachHint" aria-label="Attach">${icon('paperclip', 'sm')}</button><button class="icon-btn" style="width:28px;height:28px" data-a="mentionHint" aria-label="Mention">${icon('at-sign', 'sm')}</button>`}
      <span class="grow"></span><span class="muted hide-sm" style="font-size:12px"><kbd>⌘</kbd> <kbd>Enter</kbd></span>
      <button class="btn primary sm" data-a="submitComposer" data-mode="${mode}">${btn}</button></div></div>`;
}
function timeline(acts) {
  let last = '', out = '';
  acts.forEach(a => {
    const day = a.at.slice(0, 10);
    if (day !== last) { out += `<div class="tl-day">${dayLabel(day)}</div>`; last = day; }
    const [ic, c] = actIcon[a.ty];
    const body = (typeof actBody === 'function' && actBody(a)) || (a.ty === 'email' ? `<div class="note mail"><div class="subj">${esc(a.subj || '')}</div>${esc(a.text)}</div>` : ['call', 'meeting', 'note'].includes(a.ty) && a.text ? `<div class="note">${esc(a.text)}</div>` : '');
    out += `<div class="tl-item ${a.fresh ? 'new' : ''}"><span class="ic b-${c}">${icon(ic, 'sm')}</span><div class="bd"><div class="hd">${actSentence(a, false)}<span class="tm">${hhmm(a.at)}</span></div>${body}</div></div>`;
    a.fresh = false;
  });
  return `<div class="timeline">${out}</div>`;
}

/* LEADS */
VIEWS.leads = () => {
  const q = S.leadQ.toLowerCase();
  const tabs = [['all', 'All', l => true], ['New', 'New', l => l.st === 'New'], ['Contacted', 'Contacted', l => l.st === 'Contacted'], ['Qualified', 'Qualified', l => l.st === 'Qualified'], ['hot', 'Hot', l => l.sc >= 70 && l.st !== 'Converted']];
  const f = tabs.find(t => t[0] === S.leadTab)[2];
  const rows = LEADS.filter(f).filter(l => !q || (l.n + l.co + l.e).toLowerCase().includes(q)).sort((a, b) => b.created.localeCompare(a.created) || b.sc - a.sc);
  return `<div class="page">${pageHead('leads', 'Leads', null, `<button class="btn" data-a="importHint">${icon('upload', 'sm tint')}<span class="hide-sm">Import</span></button><button class="btn primary" data-a="create" data-t="lead">${icon('plus', 'sm')}<span class="hide-sm">New lead</span></button>`)}
    <div class="tabs">${tabs.map(([k, n, fn]) => `<button class="${S.leadTab === k ? 'on' : ''}" data-a="leadTab" data-v="${k}">${k === 'hot' ? icon('flame', 'sm c-rose') : ''}${n} <span class="n num">${LEADS.filter(fn).length}</span></button>`).join('')}</div>
    <div class="toolbar"><div class="search-field">${icon('search', 'sm')}<input class="input" id="leadQ" data-search="leadQ" placeholder="Search leads" value="${esc(S.leadQ)}" autocomplete="off"></div></div>
    ${rows.length ? `<div class="table-wrap"><table class="dt" data-tk="leads"><thead><tr><th>Name</th><th>Company</th><th>Status</th><th>Score</th><th>Source</th><th>Owner</th><th>Created</th></tr></thead><tbody>
    ${rows.map(l => `<tr data-a="go" data-r="lead-${l.id}"><td><div class="primary-cell"><span class="avatar b-${l.c}">${initials(l.n)}</span><div><div>${esc(l.n)}</div><div class="sub">${esc(l.t)}</div></div></div></td>
      <td>${esc(l.co)}</td><td><span class="pill b-${LEAD_ST[l.st]}"><span class="d"></span>${l.st}</span></td><td>${score(l.sc)}</td>
      <td class="muted">${l.src}</td><td><div class="cell-flex">${avatar(l.o, 'sm')}${USERS[l.o].n}</div></td><td class="num muted">${fmtDate(l.created)}</td></tr>`).join('')}
    </tbody></table></div>` : emptyState('target', 'teal', 'No leads here', 'Nothing matches this view yet.')}</div>`;
};
function score(n) {
  const c = n >= 70 ? 'green' : n >= 45 ? 'amber' : 'slate';
  return `<span class="score c-${c}"><span class="bars">${[1, 2, 3, 4].map(i => `<i class="${n >= i * 22 ? 'on' : ''}" style="height:${4 + i * 3}px"></i>`).join('')}</span><span class="num">${n}</span></span>`;
}

/* CONTACTS */
VIEWS.contacts = () => {
  const q = S.contactQ.toLowerCase();
  const rows = CONTACTS.filter(c => !q || (c.n + c.e + org(c.org).n + c.t).toLowerCase().includes(q));
  return `<div class="page">${pageHead('contacts', 'Contacts', CONTACTS.length, `<button class="btn primary" data-a="create" data-t="contact">${icon('plus', 'sm')}<span class="hide-sm">New contact</span></button>`)}
    <div class="toolbar"><div class="search-field">${icon('search', 'sm')}<input class="input" id="contactQ" data-search="contactQ" placeholder="Search contacts" value="${esc(S.contactQ)}" autocomplete="off"></div></div>
    ${rows.length ? `<div class="table-wrap"><table class="dt" data-tk="contacts"><thead><tr><th>Name</th><th>Organization</th><th>Email</th><th>Phone</th><th class="r">Open deals</th></tr></thead><tbody>
    ${rows.map(c => { const o = org(c.org), n = DEALS.filter(d => d.ct === c.id && isOpen(d)).length; return `<tr data-a="go" data-r="contact-${c.id}"><td><div class="primary-cell">${avatar(c.id)}<div><div>${esc(c.n)}</div><div class="sub">${esc(c.t)}</div></div></div></td>
      <td><div class="cell-flex">${orgMark(o)}${esc(o.n)}</div></td><td class="muted">${esc(c.e)}</td><td class="num muted">${esc(c.p)}</td><td class="r num">${n || '<span class="muted">0</span>'}</td></tr>`; }).join('')}
    </tbody></table></div>` : emptyState('users', 'violet', 'No contacts found', 'Try another name or company.')}</div>`;
};

/* ORGS */
VIEWS.orgs = () => {
  const q = S.orgQ.toLowerCase();
  const rows = ORGS.filter(o => !q || (o.n + o.ind + o.loc).toLowerCase().includes(q));
  return `<div class="page">${pageHead('orgs', 'Organizations', ORGS.length, `<button class="btn primary" data-a="create" data-t="contact">${icon('plus', 'sm')}<span class="hide-sm">New contact</span></button>`)}
    <div class="toolbar"><div class="search-field">${icon('search', 'sm')}<input class="input" id="orgQ" data-search="orgQ" placeholder="Search organizations" value="${esc(S.orgQ)}" autocomplete="off"></div></div>
    <div class="table-wrap"><table class="dt" data-tk="orgs"><thead><tr><th>Organization</th><th>Industry</th><th>Location</th><th class="r">Contacts</th><th class="r">Open pipeline</th><th class="r">Won</th></tr></thead><tbody>
    ${rows.map(o => { const ds = DEALS.filter(d => d.org === o.id); return `<tr data-a="go" data-r="org-${o.id}"><td><div class="primary-cell">${orgMark(o)}${esc(o.n)}</div></td><td>${o.ind}</td><td class="muted">${o.loc}</td>
      <td class="r num">${CONTACTS.filter(c => c.org === o.id).length}</td><td class="r num" style="font-weight:550">${money(ds.filter(isOpen).reduce((s, d) => s + d.v, 0))}</td><td class="r num c-green">${money(ds.filter(d => d.s === 'won').reduce((s, d) => s + d.v, 0))}</td></tr>`; }).join('')}
    </tbody></table></div></div>`;
};

/* TASKS */
VIEWS.tasks = () => {
  const mine = TASKS.filter(t => t.o === ME);
  const tabs = [['today', 'Today', t => !t.done && dayDiff(t.due) === 0], ['overdue', 'Overdue', t => !t.done && dayDiff(t.due) < 0], ['upcoming', 'Upcoming', t => !t.done && dayDiff(t.due) > 0], ['done', 'Completed', t => t.done]];
  const f = tabs.find(t => t[0] === S.taskTab)[2];
  const rows = mine.filter(f).sort((a, b) => a.due.localeCompare(b.due) || (a.tm || '99').localeCompare(b.tm || '99'));
  return `<div class="page" style="max-width:880px">${pageHead('tasks', 'My tasks', null, `<button class="btn primary" data-a="create" data-t="task">${icon('plus', 'sm')}<span class="hide-sm">New task</span></button>`)}
    <div class="tabs">${tabs.map(([k, n, fn]) => { const c = mine.filter(fn).length; return `<button class="${S.taskTab === k ? 'on' : ''}" data-a="taskTab" data-v="${k}">${n} <span class="n num" ${k === 'overdue' && c ? 'style="background:var(--t-rose-bg);color:var(--t-rose)"' : ''}>${c}</span></button>`; }).join('')}</div>
    <section class="panel">
      <div class="task-row" style="align-items:center">${icon('plus', 'sm muted')}<input class="input" id="quickTask" placeholder="Add a task for today and press Enter" style="border:0;box-shadow:none;padding:0;height:28px;background:transparent"></div>
      ${rows.map(taskRow).join('') || `<div style="padding:8px 0">${emptyState('circle-check', 'green', S.taskTab === 'done' ? 'Nothing completed yet' : 'All clear', S.taskTab === 'done' ? 'Finished tasks land here.' : 'No tasks in this list.')}</div>`}
    </section></div>`;
};

/* INBOX */
VIEWS.inbox = () => `<div class="page" style="max-width:880px">${pageHead('inbox', 'Inbox', null, `<button class="btn" data-a="readAll">${icon('check', 'sm tint')}Mark all as read</button>`)}
  <section class="panel">${NOTIFS.map(n => `<div class="feed-item" style="cursor:pointer;border-bottom:1px solid var(--divider);${n.unread ? 'background:var(--accent-soft)' : ''}" data-a="openNotif" data-id="${n.id}">
    <span class="ic b-${n.c}">${icon(n.ic, 'sm')}</span><div class="tx">${n.html}<div class="tm">${n.tm}</div></div>${n.unread ? '<span style="width:8px;height:8px;border-radius:50%;background:var(--accent);margin-top:8px"></span>' : ''}</div>`).join('')}</section></div>`;

/* REPORTS */
VIEWS.reports = () => {
  const months = [['May', 58000], ['Jun', 96000], ['Jul', 71000], ['Aug', 134000], ['Sep', 88000], ['Oct', DEALS.filter(d => d.s === 'won' && d.close.startsWith('2026-10')).reduce((s, d) => s + d.v, 0)]];
  const mx = Math.max(...months.map(m => m[1]));
  const lb = Object.keys(USERS).map(u => ({ u, v: DEALS.filter(d => d.o === u && d.s === 'won').reduce((s, d) => s + d.v, 0) + { u1: 186000, u2: 142000, u3: 98000, u4: 121000 }[u] })).sort((a, b) => b.v - a.v);
  const lmx = lb[0].v;
  const funnel = STAGES.map((s, i) => ({ s, n: DEALS.filter(d => STAGES.findIndex(x => x.k === d.s) >= i).length }));
  return `<div class="page">${pageHead('reports', 'Reports', null, `<div class="seg"><button>30 days</button><button class="on">6 months</button><button>Year</button></div>`)}
  <div class="grid-2">
    <section class="panel"><div class="panel-h"><h3>Won revenue by month</h3><span class="muted num" style="font-size:12.5px">Total ${money(months.reduce((s, m) => s + m[1], 0))}</span></div>
      <div class="panel-b"><div class="chart-bars">${months.map((m, i) => `<div class="cb" title="${m[0]} 2026: ${money(m[1])}"><em class="num">${compact(m[1])}</em><span class="${i === months.length - 1 ? 'cur' : ''}" style="height:${m[1] / mx * 82}%"></span></div>`).join('')}</div>
      <div class="chart-x">${months.map(m => `<span>${m[0]}</span>`).join('')}</div></div></section>
    <section class="panel"><div class="panel-h"><h3>Leaderboard</h3><span class="muted" style="font-size:12.5px">Won, 6 months</span></div>
      <div class="panel-b">${lb.map(x => `<div class="lb-row">${avatar(x.u)}<div><div style="font-weight:550">${USERS[x.u].n}</div><div class="bar"><span style="width:${x.v / lmx * 100}%"></span></div></div><span class="num" style="font-weight:600">${money(x.v)}</span></div>`).join('')}</div></section>
  </div>
  <section class="panel" style="margin-top:16px"><div class="panel-h"><h3>Stage conversion</h3><span class="muted" style="font-size:12.5px">Deals that reached each stage</span></div>
    <div class="panel-b" style="display:grid;gap:10px">${funnel.map((f, i) => `<div style="display:grid;grid-template-columns:110px 1fr 70px;gap:12px;align-items:center"><span>${f.s.n}</span><div style="height:22px;background:var(--bg-muted);border-radius:5px;overflow:hidden"><div style="height:100%;width:${f.n / funnel[0].n * 100}%;background:var(--t-${f.s.c});opacity:.8;border-radius:5px"></div></div><span class="num muted" style="text-align:right">${f.n} ${i ? `· ${Math.round(f.n / funnel[i - 1].n * 100)}%` : ''}</span></div>`).join('')}</div></section>
  </div>`;
};
