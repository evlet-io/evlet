
/* ---------- component library ---------- */
ROUTES.library = { n: 'Library', ic: 'layers', c: 'violet', grp: 'Workspace' };
const LEVELS = { Base: 'slate', Element: 'blue', Composite: 'violet', Section: 'amber' };
const CATS = [
  ['inputs', 'Inputs', 'pencil', 'teal'],
  ['buttons', 'Buttons', 'circle-dot', 'blue'],
  ['elements', 'Elements', 'sparkles', 'violet'],
  ['display', 'Display', 'layout-grid', 'green'],
  ['layout', 'Layout areas', 'columns-3', 'amber'],
  ['sections', 'Sections', 'layers', 'rose'],
];
const LIB = [];
const C = (cat, id, name, level, desc, meta, demo) => LIB.push({ cat, id, name, level, desc, props: [], from: [], used: [], variants: [], ...meta, demo });
const SAFE = new Set(['libSw', 'libSeg', 'libCheck', 'libToast', 'libModal', 'libDrawer', 'libPop', 'libPalette', 'libOpen', 'gEdit', 'gExp', 'gSel', 'gSelAll', 'gNav', 'gAdd', 'gColsMenu', 'gMax', 'gDup', 'gDel', 'gDupSel', 'gDelSel', 'composerMode', 'copy', 'mentionHint']);
const inert = html => html.replace(/data-a="([A-Za-z]+)"/g, (m, a) => SAFE.has(a) ? m : `data-demo="${a}"`).replace(/ id="(recTitle|cmpText|cmpSubj|cmpDue|fileUp|quickTask|dealQ|leadQ|contactQ|orgQ)"/g, '').replace(/contenteditable="true"/g, '').replace(/data-search="[a-zA-Z]+"/g, '').replace(/data-tk="[a-z]+"/g, 'data-tk="lib"');
const fl = (label, input, { req, err, hint = '', cls = '' } = {}) => `<div class="fld ${err ? 'invalid' : ''} ${cls}"><label>${label}${req ? '<span class="req">*</span>' : ''}${hint}</label>${input}<span class="err">${err || ''}</span></div>`;
const inp = (v = '', attrs = '') => `<input class="input" value="${esc(v)}" ${attrs}>`;
const aff = (pre, v, suf = '', cls = 'num r') => `<div class="in-affix">${pre ? `<span>${pre}</span>` : ''}<input class="input ${cls}" value="${esc(v)}">${suf ? `<span>${suf}</span>` : ''}</div>`;
const sw = (on, label = '') => `<button class="sw-wrap" data-a="libSw"><span class="sw ${on ? 'on' : ''}"><i></i></span>${label ? `<span>${label}</span>` : ''}</button>`;
const ck = (on, label) => `<button class="ck-wrap" data-a="libCheck"><span class="check ${on ? 'on' : ''}">${icon('check')}</span><span>${label}</span></button>`;
const segd = (opts, on = 0) => `<div class="seg">${opts.map((o, i) => `<button class="${i === on ? 'on' : ''}" data-a="libSeg">${o}</button>`).join('')}</div>`;
const stack = (...x) => `<div class="lib-stack">${x.join('')}</div>`;
const row = (...x) => `<div class="lib-row">${x.join('')}</div>`;
const d1 = () => deal('d1'), i41 = () => invoice('i41');

/* inputs */
C('inputs', 'base-field', 'BaseField', 'Base', 'The reactive core every field type wraps. It owns the label, the value cell, required, locked and applies-when rules, the error and the hint. Field types only change the input and how the value is parsed and shown.',
  { props: [['label', 'text', 'From the model field label'], ['value', 'cell', 'Two-way bound value'], ['required', 'boolean or rule', 'Static or required_when'], ['locked', 'boolean or rule', 'Static or locked_when'], ['applies', 'rule', 'applies_when; hides and clears when false'], ['error', 'cell', 'Server or client validation message'], ['hint', 'text', 'Small note beside the label']], variants: ['Default', 'Required with error', 'Locked', 'Hidden by rule'], used: ['Every form', 'Details tab', 'Create forms', 'Action dialogs'], model: 'Any Field' },
  () => stack(fl('Label', inp('Value')), fl('Required field', inp(''), { req: 1, err: 'This field is required' }), fl('Locked field', inp('Can\'t change', 'disabled'), { hint: `<span class="ff-hint">${icon('lock', 'xs')}locked</span>` })));
const fieldTypes = [
  ['text-field', 'TextField', 'Single line text with a length limit.', 'Text(max_length=…)', () => fl('Deal name', inp('ERP rollout, 3 entities'), { req: 1 })],
  ['longtext-field', 'LongTextField', 'Multi-line text that grows with content.', 'LongText()', () => fl('Remarks', '<textarea class="input" rows="3">Three entities across Dubai mainland, JAFZA and Oman.</textarea>')],
  ['integer-field', 'IntegerField', 'Whole numbers with optional minimum and maximum.', 'Integer(minimum, maximum)', () => fl('Credit days', aff('', '30', 'days'))],
  ['decimal-field', 'DecimalField', 'Exact decimal with a fixed scale. Never a float; values travel as strings.', 'Decimal(scale=…)', () => fl('Quantity', aff('', '2.500'))],
  ['currency-field', 'CurrencyField', 'Exact money amount with two decimals and the currency shown as a prefix.', 'Currency()', () => fl('Rate', aff('AED', '42,000.00'))],
  ['percent-field', 'PercentField', 'Decimal from 0 to 100 shown with a percent suffix.', 'Percent()', () => fl('Discount', aff('', '12.50', '%'))],
  ['date-field', 'DateField', 'Calendar date shown as DD-MM-YYYY, stored as an ISO date.', 'Date()', () => fl('Posting date', '<input class="input" type="date" value="2026-11-01">')],
  ['datetime-field', 'DateTimeField', 'Date and time in the user\'s timezone, stored in UTC.', 'DateTime()', () => fl('Received at', '<input class="input" type="datetime-local" value="2026-11-01T13:20">')],
  ['time-field', 'TimeField', 'Time of day without a date or timezone.', 'Time()', () => fl('Meeting time', '<input class="input" type="time" value="14:00">')],
  ['duration-field', 'DurationField', 'A length of time typed as 1h 30m, stored in seconds.', 'Duration()', () => fl('Time spent', aff('', '1h 30m', '', ''))],
  ['boolean-field', 'BooleanField', 'A switch for true or false. Wraps the Switch element.', 'Boolean()', () => fl('Active', sw(true, 'Customer is active'))],
  ['select-field', 'SelectField', 'One choice from a declared list.', 'Select([...])', () => fl('Source', `<select class="input">${SOURCES.map(s => `<option>${s}</option>`).join('')}</select>`)],
  ['link-field', 'LinkField', 'Picks a record of another model. Shows the record mark and title, and can filter its options by a rule.', 'Link(Model, filter=…)', () => fl('Customer', `<div class="in-link">${orgMark(org('o1'))}<span>Al Sahra Trading LLC</span>${icon('chevron-down', 'sm')}</div>`, { req: 1 })],
  ['email-field', 'EmailField', 'Text checked for an address shape, with a copy action.', 'Email()', () => fl('Email', aff('', 'khalid@alsahra-trading.ae', '', ''))],
  ['url-field', 'URLField', 'Absolute http or https address.', 'URL()', () => fl('Website', aff('https://', 'alsahra-trading.ae', '', ''))],
  ['phone-field', 'PhoneField', 'Phone number, normalized when it starts with a country code.', 'Phone()', () => fl('Mobile', aff('+971', '50 418 2290', '', ''))],
  ['tags-field', 'TagsField', 'A short list of labels. Type and press Enter to add.', 'Tags()', () => fl('Tags', `<div class="in-tags"><span class="chip tag-hot">${icon('flame', 'xs')}Hot</span><span class="chip">Renewal</span><input placeholder="Add tag"></div>`)],
  ['state-field', 'StateField', 'Shows the current state. It never edits directly; the value changes only through moves.', 'State(…, moves=…)', () => fl('Status', `<div class="fval ro"><span class="pill b-blue"><span class="d"></span>Issued</span><span class="muted" style="font-size:12px;margin-left:6px">changes through moves</span></div>`)],
  ['sequence-field', 'SequenceField', 'The document number. Read only, empty until the state that assigns it.', 'Sequence("INV-{year}-{n:05}")', () => stack(fl('Number', `<div class="fval ro"><span class="tx mono">INV-2026-00041</span></div>`), fl('Number', `<div class="fval ro"><span class="muted">Assigned on issue</span></div>`))],
  ['computed-field', 'ComputedField', 'A read-only value calculated from a rule. Hovering the hint shows the rule.', 'compute=…', () => fl('Total', `<div class="fval ro"><span class="num tx">AED 25,200</span></div>`, { hint: `<span class="ff-hint calc" data-tip="= net_total + vat_total">ƒ calculated</span>` })],
];
fieldTypes.forEach(([id, name, desc, model, demo]) => C('inputs', id, name, 'Element', desc, { props: [['…BaseField', '', 'All BaseField properties'], ['model', 'field', model]], from: ['base-field'], used: ['Details tab', 'Create forms', 'Child table cells'], model }, demo));
C('inputs', 'inline-value', 'InlineValue', 'Composite', 'Shows a field as plain text and turns into its field type on click. Enter saves, Esc cancels. Used wherever a record is read more than edited.',
  { props: [['field', 'field', 'Any field type'], ['display', 'formatter', 'How the value reads at rest'], ['onSave', 'action', 'Partial save of one field']], from: ['base-field', 'icon'], used: ['Key fields', 'Details tab', 'Side panel'], variants: ['At rest', 'Hover with pencil', 'Editing', 'Read only'] },
  () => stack(`<dl class="fields"><dt>Owner</dt><dd><button class="fval">${avatar('u1', 'sm')}<span class="tx">Aslam K</span>${icon('pencil', 'xs pen')}</button></dd><dt>Stage</dt><dd><button class="fval">${stagePill('nego')}${icon('pencil', 'xs pen')}</button></dd><dt>Amount</dt><dd><div class="fval ro"><span class="num tx">AED 186,000</span></div></dd></dl>`));
C('inputs', 'search-input', 'SearchInput', 'Element', 'Text input with a search icon. Filters as you type and keeps the cursor in place.', { props: [['value', 'cell', ''], ['placeholder', 'text', '']], from: ['icon'], used: ['List toolbars', 'Command palette'] },
  () => `<div class="search-field">${icon('search', 'sm')}<input class="input" placeholder="Search deals"></div>`);
C('inputs', 'checkbox', 'Checkbox', 'Base', 'Square check for selection and task completion.', { props: [['checked', 'cell', ''], ['label', 'text', 'Optional']], used: ['Row selection', 'Task rows', 'Filter menus'] },
  () => stack(ck(true, 'Selected'), ck(false, 'Not selected')));
C('inputs', 'switch', 'Switch', 'Base', 'On and off toggle. BooleanField and the layout control use it.', { props: [['on', 'cell', '']], used: ['BooleanField', 'Layout control', 'Template'] },
  () => row(sw(true), sw(false)));
C('inputs', 'segmented', 'SegmentedControl', 'Element', 'Two to four mutually exclusive options shown together.', { props: [['options', 'list', ''], ['value', 'cell', ''], ['size', 'default or small', '']], used: ['Board and list switch', 'Density', 'Report ranges'] },
  () => stack(segd([`${icon('kanban', 'sm')}Board`, `${icon('list', 'sm')}List`]), segd(['30 days', '6 months', 'Year'], 1)));
C('inputs', 'quick-add', 'QuickAddInput', 'Element', 'A borderless input at the top of a list that creates an item on Enter.', { props: [['placeholder', 'text', ''], ['onAdd', 'action', '']], used: ['My tasks'] },
  () => `<div class="task-row" style="align-items:center;border:1px solid var(--border);border-radius:8px">${icon('plus', 'sm muted')}<input class="input" placeholder="Add a task for today and press Enter" style="border:0;box-shadow:none;padding:0;height:28px;background:transparent"></div>`);

/* buttons */
C('buttons', 'button', 'Button', 'Base', 'The one button. Variants set meaning, sizes set density, an icon is optional.', { props: [['variant', 'primary, default, ghost, success, danger, soft danger', ''], ['size', 'default or small', ''], ['icon', 'icon', 'Optional, before the label'], ['onClick', 'action', '']], from: ['icon'], used: ['Everywhere'], variants: ['Primary', 'Default', 'Ghost', 'Success', 'Danger', 'Soft danger', 'Small', 'Icon only'] },
  () => stack(row(`<button class="btn primary">${icon('plus', 'sm')}Primary</button>`, `<button class="btn">${icon('mail', 'sm c-blue')}Default</button>`, `<button class="btn ghost">Ghost</button>`), row(`<button class="btn success">${icon('trophy', 'sm')}Success</button>`, `<button class="btn danger">Danger</button>`, `<button class="btn danger-soft">${icon('circle-x', 'sm')}Soft</button>`), row(`<button class="btn sm">Small</button>`, `<button class="btn icon">${icon('ellipsis', 'sm')}</button>`)));
C('buttons', 'icon-button', 'IconButton', 'Element', 'Square button with only an icon. Always carries a tooltip and an accessible label.', { props: [['icon', 'icon', ''], ['tip', 'text', 'Tooltip and label'], ['active', 'boolean', '']], from: ['icon', 'tooltip'], used: ['Top bar', 'Side blocks', 'Drawers'] },
  () => row(`<button class="icon-btn" data-tip="Notifications">${icon('bell')}</button>`, `<button class="icon-btn" data-tip="Copy">${icon('copy')}</button>`, `<button class="icon-btn on" data-tip="Active">${icon('panel-left')}</button>`));
C('buttons', 'menu-button', 'MenuButton', 'Composite', 'A button that opens a menu of related actions.', { props: [['label', 'text', ''], ['items', 'list of MenuItem', '']], from: ['button', 'popover'], used: ['New', 'More actions'] },
  () => row(`<button class="btn primary sm" data-a="libPop">${icon('plus', 'sm')}New${icon('chevron-down', 'xs')}</button>`, `<button class="btn icon" data-a="libPop">${icon('ellipsis', 'sm')}</button>`));
C('buttons', 'move-button', 'MoveButton', 'Element', 'Runs a state move. When its guard fails it stays visible, dims and explains why on hover or click.', { props: [['move', 'Move', 'From the State field'], ['guard', 'rule', 'Move.when'], ['reason', 'text', 'Shown when blocked']], from: ['button', 'tooltip'], used: ['Doc header'], variants: ['Ready', 'Blocked'] },
  () => row(`<button class="btn primary">${icon('send', 'sm')}Issue</button>`, `<button class="btn" style="opacity:.55" data-tip="Outstanding is AED 25,200">${icon('check', 'sm')}Mark paid</button>`));
C('buttons', 'link-button', 'LinkButton', 'Element', 'Text link for secondary navigation inside panels.', { props: [['label', 'text', ''], ['to', 'route', '']], used: ['Panel headers', 'Side blocks'] },
  () => row(`<a class="link">Open board ${icon('arrow-up-right', 'xs')}</a>`, `<button class="h-link link">All fields</button>`));
C('buttons', 'add-chip', 'AddChip', 'Element', 'Dashed chip that adds an item to a small set.', { props: [['label', 'text', '']], used: ['Tags'] },
  () => `<div class="tag-row"><span class="chip">Renewal</span><button class="chip add">${icon('plus', 'xs')}Tag</button></div>`);
C('buttons', 'fab', 'FloatingButton', 'Element', 'Round-ended button fixed to a screen corner. Shows a count when something is changed.', { props: [['label', 'text', ''], ['count', 'text', 'Optional'], ['open', 'boolean', '']], from: ['button'], used: ['Layout control'] },
  () => `<button class="fab-btn">${icon('sliders-horizontal', 'sm')}<span>Layout</span><span class="fab-n num">2 hidden</span></button>`);
C('buttons', 'app-tab', 'AppTab', 'Element', 'App icon in the app bar. The active one becomes a tab that opens into the sidebar.', { props: [['app', 'app', ''], ['active', 'boolean', '']], from: ['icon-tile', 'tooltip'], used: ['App bar'] },
  () => `<div class="lib-appbar">${sidebarAppbar()}</div>`);
C('buttons', 'nav-item', 'NavItem', 'Element', 'Sidebar link with a tinted icon, label and optional count or badge.', { props: [['icon', 'icon', ''], ['label', 'text', ''], ['count', 'number', ''], ['badge', 'number', 'Unread style'], ['active', 'boolean', '']], from: ['icon', 'badge'], used: ['Sidebar'] },
  () => `<div class="lib-side">${['home', 'inbox', 'deals'].map((k, i) => { const r = ROUTES[k]; return `<button class="nav-item ${i === 2 ? 'active' : ''}">${icon(r.ic, 'c-' + r.c)}<span class="lbl">${r.n}</span>${i === 1 ? '<span class="badge">4</span>' : i === 2 ? '<span class="count">13</span>' : ''}</button>`; }).join('')}</div>`);
C('buttons', 'tab', 'Tab', 'Element', 'One tab in a Tabs bar, with an optional count.', { props: [['label', 'text', ''], ['count', 'number', ''], ['active', 'boolean', '']], used: ['Tabs'] },
  () => `<div class="tabs" style="margin:0"><button class="on">Activity <span class="n">7</span></button><button>Details</button><button>Items <span class="n">5</span></button></div>`);

/* elements */
C('elements', 'icon', 'Icon', 'Base', 'Line icon at 12, 14, 16 or 18 pixels. Takes the text colour or one of the tints.', { props: [['name', 'text', ''], ['size', 'xs, sm, md, lg', ''], ['tint', 'tint', 'Optional']], used: ['Everywhere'] },
  () => row(...['house', 'handshake', 'target', 'users', 'building-2', 'receipt', 'wallet', 'calendar'].map((n, i) => icon(n, 'lg c-' + ['slate', 'blue', 'teal', 'violet', 'amber', 'green', 'teal', 'rose'][i]))));
C('elements', 'icon-tile', 'IconTile', 'Element', 'Icon on a soft tinted square. Marks a page, a timeline entry or a card type.', { props: [['icon', 'icon', ''], ['tint', 'tint', ''], ['size', '22 to 44', '']], from: ['icon'], used: ['Page headers', 'KPI tiles', 'Quick access'] },
  () => row(...['blue', 'green', 'amber', 'violet', 'rose', 'teal'].map((c, i) => `<span class="ph-icon b-${c}">${icon(['handshake', 'wallet', 'circle-check', 'users', 'chart-column', 'target'][i], 'lg')}</span>`)));
C('elements', 'avatar', 'Avatar', 'Element', 'Initials on a tinted circle for people. Four sizes.', { props: [['person', 'user or contact', ''], ['size', 'sm, md, lg, xl', '']], used: ['Owners', 'People blocks', 'Timeline'] },
  () => row(avatar('u1', 'sm'), avatar('u2'), avatar('c1', 'lg'), avatar('u3', 'xl')));
C('elements', 'avatar-group', 'AvatarFilter', 'Composite', 'Overlapping avatars that filter a list by owner. Selected ones ring, others dim.', { props: [['people', 'list', ''], ['selected', 'cell (set)', '']], from: ['avatar'], used: ['Deals toolbar'] },
  () => `<div class="avatars"><button class="on">${avatar('u1')}</button><button class="dim">${avatar('u2')}</button><button class="dim">${avatar('u3')}</button><button class="dim">${avatar('u4')}</button></div>`);
C('elements', 'org-mark', 'RecordMark', 'Element', 'First letter of a company on a rounded square. Square for companies, round avatars for people.', { props: [['record', 'record', ''], ['size', 'sm or lg', '']], used: ['Links', 'Headers', 'Cards'] },
  () => row(orgMark(org('o1')), orgMark(org('o3')), orgMark(org('o4'), 'lg'), orgMark(org('o7'), 'lg')));
C('elements', 'pill', 'StatusPill', 'Element', 'Dot and label on a tint. One colour per state, taken from the State field.', { props: [['state', 'state', ''], ['tint', 'tint', 'From the state']], used: ['Lists', 'Headers', 'Key fields'] },
  () => row(...STAGES.map(s => stagePill(s.k)), `<span class="pill b-rose"><span class="d"></span>Void</span>`));
C('elements', 'chip', 'Chip', 'Element', 'Small neutral label for tags, counts and flags.', { props: [['label', 'text', ''], ['icon', 'icon', 'Optional'], ['tone', 'neutral or tint', '']], used: ['Tags', 'Cards', 'Tables'] },
  () => row(`<span class="chip">Renewal</span>`, `<span class="chip tag-hot">${icon('flame', 'xs')}Hot</span>`, `<span class="chip">${icon('lock', 'xs')}Locked</span>`, `<span class="chip">Primary</span>`));
C('elements', 'badge', 'CountBadge', 'Element', 'Count beside a label. Filled for unread, plain for totals.', { props: [['count', 'number', ''], ['tone', 'filled or plain', '']], used: ['Nav items', 'Tabs'] },
  () => row('<span class="nav-item" style="width:auto">Inbox<span class="badge">4</span></span>', '<span class="nav-item" style="width:auto">Deals<span class="count">13</span></span>', '<span class="tabs" style="border:0;margin:0"><button>Tasks <span class="n">2</span></button></span>'));
C('elements', 'kbd', 'Key', 'Element', 'Keyboard key hint.', { props: [['keys', 'text', '']], used: ['Search', 'Shortcuts', 'Composer'] },
  () => row('<kbd>⌘</kbd><kbd>K</kbd>', '<kbd>Enter</kbd>', '<kbd>Esc</kbd>', '<kbd>G</kbd> <kbd>D</kbd>'));
C('elements', 'tooltip', 'Tooltip', 'Element', 'Dark label with a pointer, shown at once on hover and kept until the pointer leaves. Points down when above, up when below.', { props: [['text', 'text', ''], ['placement', 'auto', '']], used: ['Icon buttons', 'App bar', 'Blocked moves', 'Calculated hints'] },
  () => row(`<span class="tip-demo above">Accounting</span>`, `<span class="tip-demo below">Notifications</span>`, `<button class="btn sm" data-tip="Live tooltip">Hover me</button>`));
C('elements', 'progress', 'ProgressBar', 'Element', 'Thin bar for a share of a whole, with an optional number.', { props: [['value', '0 to 100', ''], ['tint', 'tint', '']], used: ['Probability', 'Board columns', 'Leaderboard'] },
  () => stack(`<div class="prob"><div class="bar"><span style="width:80%"></span></div><span class="num muted">80%</span></div>`, `<div class="col-h" style="padding:0"><div class="prog"><span style="width:55%;background:var(--t-violet)"></span></div></div>`));
C('elements', 'score-bars', 'ScoreBars', 'Element', 'Four rising bars and a number. Colour moves from grey to amber to green with the score.', { props: [['score', '0 to 100', '']], used: ['Leads list', 'Lead header'] },
  () => row(score(91), score(57), score(22)));
C('elements', 'ring', 'ScoreRing', 'Element', 'Circular gauge with the value in the middle.', { props: [['value', '0 to 100', ''], ['label', 'text', '']], used: ['Scorecard'] },
  () => `<div class="ring" style="--p:82;--acc:var(--t-teal)"><b class="num">82</b><span>score</span></div>`);
C('elements', 'sparkline', 'Sparkline', 'Element', 'Small trend line with an area and a marked last point.', { props: [['values', 'list of numbers', ''], ['tint', 'tint', '']], used: ['KPI tiles'] },
  () => row(spark([4, 6, 5, 8, 7, 9, 11], 'blue'), spark([9, 8, 9, 7, 6, 6, 5], 'violet')));
C('elements', 'stage-bar', 'StageBar', 'Element', 'One bar split by share, with a colour per segment.', { props: [['segments', 'list', 'value and tint']], used: ['Pipeline by stage'] },
  () => `<div class="stagebar" style="width:240px;margin:0">${[['slate', 15], ['blue', 23], ['violet', 28], ['amber', 34]].map(([c, v]) => `<span style="flex:${v};background:var(--t-${c})"></span>`).join('')}</div>`);
C('elements', 'token', 'PlaceholderToken', 'Element', 'Shows where data goes in a layout preview.', { props: [['name', 'text', '']], used: ['Doc layout placeholders'] },
  () => row(P('title'), P('status flow'), P('child table')));
C('elements', 'divider', 'Divider', 'Base', 'Thin vertical or horizontal rule between groups.', { props: [['direction', 'vertical or horizontal', '']], used: ['Top bar', 'App bar', 'Menus'] },
  () => row('<span class="muted">Left</span><span class="sep"></span><span class="muted">Right</span>'));

/* display */
C('display', 'stat', 'Stat', 'Element', 'Label, value and a short note. The unit of the summary strip.', { props: [['label', 'text', ''], ['value', 'cell', ''], ['note', 'text', '']], used: ['Summary strip'] },
  () => `<div class="sum" style="grid-template-columns:1fr;width:200px;margin:0"><div class="sum-t"><span>Outstanding</span><b class="num">AED 25,200</b><em>0% paid</em></div></div>`);
C('display', 'summary-strip', 'SummaryStrip', 'Composite', 'Four stats in one bordered row. Values come from the model\'s measures.', { props: [['stats', 'list of up to 4', 'Field or measure']], from: ['stat'], used: ['Doc view'] },
  () => inert(docSummary('invoice', i41())));
C('display', 'kpi', 'KpiTile', 'Composite', 'Headline number with a trend against last period and a sparkline.', { props: [['label', 'text', ''], ['value', 'measure', ''], ['delta', 'number', ''], ['trend', 'list', '']], from: ['icon-tile', 'sparkline'], used: ['Home'] },
  () => `<div class="kpi" style="width:300px"><div class="kpi-top"><span class="ic b-blue">${icon('wallet', 'sm')}</span>Open pipeline</div><div class="kpi-foot"><div><div class="kpi-val num"><small>AED</small>1.15M</div><span class="delta up">${icon('trending-up', 'xs')}12.4%</span> <span class="muted" style="font-size:12px">vs last month</span></div>${spark([610, 680, 720, 690, 810, 860, 940, 1010, 1150], 'blue')}</div></div>`);
C('display', 'kv-list', 'KeyValueList', 'Composite', 'Two-column label and value list. Values are InlineValues when editable.', { props: [['fields', 'list', '']], from: ['inline-value'], used: ['Key fields', 'Record info', 'Peek drawer'] },
  () => `<dl class="fields" style="width:300px"><dt>Owner</dt><dd><div class="fval ro">${avatar('u1', 'sm')}<span class="tx">Aslam K</span></div></dd><dt>Source</dt><dd><div class="fval ro"><span class="tx">Referral</span></div></dd><dt>Created</dt><dd><div class="fval ro"><span class="tx num">15-10-2026</span></div></dd></dl>`);
C('display', 'mini-card', 'MiniCard', 'Composite', 'Compact bordered row for a related record: mark, title, subtitle and up to two quick actions.', { props: [['record', 'record', ''], ['subtitle', 'text', ''], ['actions', 'list', 'Optional']], from: ['avatar', 'org-mark', 'icon-button'], used: ['People', 'Related', 'Next step'] },
  () => stack(`<div class="mini-card" style="width:320px">${avatar('c1')}<div class="grow"><div class="nm">Khalid Al Mansoori <span class="chip">Primary</span></div><div class="sub">Finance Director</div></div><div class="acts"><button class="icon-btn" style="width:26px;height:26px" data-tip="Copy email">${icon('mail', 'sm')}</button></div></div>`, `<div class="mini-card" style="width:320px">${orgMark(org('o1'), 'lg')}<div class="grow"><div class="nm">Al Sahra Trading LLC</div><div class="sub">Trading · Dubai</div></div>${icon('chevron-right', 'sm muted')}</div>`));
C('display', 'task-row', 'TaskRow', 'Composite', 'Checkbox, title, due and the linked record. Overdue turns the due date red.', { props: [['task', 'record', ''], ['onToggle', 'action', '']], from: ['checkbox', 'avatar'], used: ['My tasks', 'Home', 'Tasks tab'] },
  () => `<div style="width:420px;border:1px solid var(--border);border-radius:12px">${inert(taskRow(TASKS[6]) + taskRow(TASKS[0]))}</div>`);
C('display', 'timeline-item', 'TimelineItem', 'Composite', 'One journal entry: icon by kind, sentence, time and an optional body.', { props: [['entry', 'journal entry', ''], ['kind', 'note, email, call, changed, moved…', '']], from: ['icon-tile', 'message-card', 'diff'], used: ['Journal'] },
  () => inert(timeline([ACTS.find(a => a.id === 'a3')])));
C('display', 'diff', 'ChangeDiff', 'Element', 'Field, old value struck through, new value. One line per changed field.', { props: [['changes', 'list', 'label, old, new']], used: ['Journal'] },
  () => `<div class="diffs" style="width:420px">${[['Contact', 'Khalid Al Mansoori', 'Priya Menon'], ['Credit days', '0 days', '30 days']].map(([l, o, n]) => `<div class="diff"><span class="dl">${l}</span><span class="old">${o}</span>${icon('arrow-right', 'xs')}<span class="new">${n}</span></div>`).join('')}</div>`);
C('display', 'message-card', 'MessageCard', 'Element', 'Bordered body for notes, calls and emails. Emails show a subject line.', { props: [['subject', 'text', 'Emails only'], ['body', 'text', '']], used: ['Journal'] },
  () => stack(`<div class="tl-item" style="padding:0"><div class="bd" style="padding:0"><div class="note mail"><div class="subj">Revised commercial proposal v3</div>Hi Khalid, please find the revised proposal attached.</div></div></div>`));
C('display', 'empty-state', 'EmptyState', 'Composite', 'Tinted icon, a short heading, what to do next and an optional button.', { props: [['icon', 'icon', ''], ['title', 'text', ''], ['text', 'text', ''], ['action', 'button', 'Optional']], from: ['icon-tile', 'button'], used: ['Empty lists', 'Empty tabs'] },
  () => emptyState('paperclip', 'slate', 'No files', 'Proposals and signed copies live here.', '<button class="btn sm" style="margin-top:10px">Upload file</button>'));
C('display', 'banner', 'StateBanner', 'Element', 'Full-width note under a header about the record\'s state, with optional actions.', { props: [['tone', 'neutral, info, danger', ''], ['text', 'text', ''], ['actions', 'list', 'Optional']], from: ['icon', 'button'], used: ['Doc view'] },
  () => stack(inert(invBanner(invoice('i42d'))), inert(invBanner(i41())), inert(invBanner(invoice('i39')))));
C('display', 'info-note', 'InfoNote', 'Element', 'Quiet explanation above a block that behaves differently from the rest.', { props: [['text', 'text', '']], used: ['Postings', 'Journal'] },
  () => stack(`<div class="eff-note" style="margin:0">${icon('info', 'sm')}<span>Written by the engine. Read only.</span></div>`, `<div class="journal-note" style="margin:0">${icon('lock', 'xs')}Append-only history.</div>`));
C('display', 'filter-chip', 'FilterChip', 'Element', 'An active filter with a remove button.', { props: [['label', 'text', ''], ['onRemove', 'action', '']], used: ['List toolbars'] },
  () => row(`<span class="filter-chip">Tag: Hot<button>${icon('x', 'xs')}</button></span>`, `<span class="filter-chip">Owner: Aslam K<button>${icon('x', 'xs')}</button></span>`));
C('display', 'toast', 'Toast', 'Element', 'Short confirmation at the bottom of the screen. Undo when the change can be reversed.', { props: [['text', 'text', ''], ['icon', 'icon', ''], ['undo', 'action', 'Optional']], used: ['Every save and move'] },
  () => stack(`<div class="toast lib-static">${icon('check', 'sm ok')}<span style="flex:1">Moved to Discovery</span><button>Undo</button></div>`, `<button class="btn sm" data-a="libToast">Show a live toast</button>`));
C('display', 'data-table', 'DataTable', 'Composite', 'List table with a sticky header, pinned first columns, resizable columns saved per user, sorting and row selection.', { props: [['columns', 'list', 'From list_fields'], ['rows', 'query', ''], ['pinned', 'number', 'Columns to pin'], ['selectable', 'boolean', '']], from: ['checkbox', 'pill', 'avatar'], used: ['Every list'], variants: ['Plain', 'Selectable', 'With totals'] },
  () => `<div style="width:100%">${inert(dealTable(DEALS.slice(0, 4)))}</div>`);
C('display', 'board-card', 'BoardCard', 'Composite', 'Draggable record card for boards: tags, title, linked record, value, date and owner.', { props: [['record', 'record', ''], ['fields', 'list', 'From key fields']], from: ['chip', 'org-mark', 'avatar'], used: ['Board'] },
  () => `<div style="width:270px">${inert(dealCard(deal('d1')))}</div>`);
C('display', 'feed-item', 'FeedItem', 'Composite', 'Compact activity line with a link to the record, for feeds across records.', { props: [['entry', 'journal entry', '']], from: ['icon-tile'], used: ['Home', 'Inbox'] },
  () => `<div style="width:420px">${inert(`<div class="feed-item"><span class="ic b-green">${icon('phone', 'sm')}</span><div class="tx">${actSentence(ACTS[0], true)}<div class="tm">4h ago</div></div></div>`)}</div>`);
C('display', 'list-row', 'ListRow', 'Composite', 'Mark, title and subtitle on the left, value and date on the right.', { props: [['record', 'record', ''], ['value', 'field', ''], ['meta', 'field', '']], from: ['org-mark'], used: ['Closing soon'] },
  () => { const d = deal('d14'), o = org(d.org); return `<div style="width:420px;border:1px solid var(--border);border-radius:12px"><div class="close-row">${orgMark(o)}<div style="min-width:0"><div class="nm">${d.t}</div><div class="sub">${o.n} · Negotiation</div></div><div style="text-align:right"><div class="num" style="font-weight:600">${money(d.v)}</div><div class="sub due-over">2 days overdue</div></div></div></div>`; });
C('display', 'bar-chart', 'BarChart', 'Composite', 'Vertical bars with values on top and labels below. Current period full colour, past ones lighter.', { props: [['series', 'measure grouped by period', '']], used: ['Reports'] },
  () => `<div style="width:360px"><div class="chart-bars" style="height:120px">${[58, 96, 71, 134, 88, 112].map((v, i) => `<div class="cb"><em class="num">${v}K</em><span class="${i === 5 ? 'cur' : ''}" style="height:${v / 134 * 80}%"></span></div>`).join('')}</div><div class="chart-x">${['May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct'].map(m => `<span>${m}</span>`).join('')}</div></div>`);
C('display', 'file-row', 'FileRow', 'Composite', 'File type tile, name, size, who and when, and a download action.', { props: [['file', 'attachment', '']], from: ['icon-button'], used: ['Files tab'] },
  () => `<div class="file" style="width:420px"><span class="ext b-rose">PDF</span><div style="flex:1;min-width:0"><div style="font-weight:550">Commercial_Proposal_v3.pdf</div><div class="muted" style="font-size:12px">1.4 MB · Aslam K · 31-10-2026</div></div><button class="btn ghost sm icon">${icon('download', 'sm')}</button></div>`);
C('display', 'notification', 'NotificationItem', 'Composite', 'Icon, sentence and time. Tinted background until read.', { props: [['item', 'notification', ''], ['unread', 'boolean', '']], from: ['icon-tile'], used: ['Notifications', 'Inbox'] },
  () => `<div class="notif" style="width:360px;border:1px solid var(--border);border-radius:12px;overflow:hidden">${NOTIFS.slice(0, 2).map(n => `<div class="ni ${n.unread ? 'unread' : ''}"><span class="avatar b-${n.c}" style="border-radius:7px">${icon(n.ic, 'sm')}</span><div>${n.html}<div class="tm">${n.tm}</div></div></div>`).join('')}</div>`);
C('display', 'scorecard', 'Scorecard', 'Composite', 'Score ring beside three labelled bars.', { props: [['score', 'measure', ''], ['parts', 'list of 3', '']], from: ['ring', 'progress'], used: ['Highlight card'] },
  () => `<div style="width:300px;--acc:var(--t-teal)"><div class="scorecard"><div class="ring" style="--p:82"><b class="num">82</b><span>score</span></div><div class="sc-rows">${[['Fit', 74], ['Engagement', 70], ['Intent', 81]].map(([l, v]) => `<div class="sc-row"><span>${l}</span><div class="bar"><span style="width:${v}%"></span></div><b class="num">${v}</b></div>`).join('')}</div></div></div>`);
C('display', 'channel-row', 'ChannelRow', 'Composite', 'Contact channel with a preferred flag and a copy action.', { props: [['channel', 'email, phone, WhatsApp', ''], ['value', 'text', ''], ['preferred', 'boolean', '']], from: ['icon-tile', 'icon-button'], used: ['Highlight card'] },
  () => `<div class="ch-row" style="width:300px"><span class="ph-icon b-teal" style="width:28px;height:28px;border-radius:8px">${icon('message-circle', 'sm')}</span><div class="grow" style="min-width:0"><div class="muted" style="font-size:12px">WhatsApp <span class="chip" style="height:17px">Preferred</span></div><div class="ch-v">+971 50 418 2290</div></div><button class="icon-btn" data-a="copy" data-v="+971 50 418 2290">${icon('copy', 'sm')}</button></div>`);

/* layout areas */
const wire = (cls, label) => `<div class="wf ${cls}"><span>${label}</span></div>`;
C('layout', 'app-shell', 'AppShell', 'Section', 'The frame of every screen: sidebar with app bar on the left, top bar and one scrolling view on the right. Collapses the sidebar to icons; becomes a drawer on phones.', { props: [['collapsed', 'cell', 'Saved per user'], ['view', 'route', '']], from: ['ws-switcher', 'app-bar', 'sidebar-nav', 'top-bar'], used: ['Everything'] },
  () => `<div class="wf-shell"><div class="wf-side">${wire('h1', 'Workspace')}${wire('h2', 'App bar')}${wire('fill', 'Sidebar nav')}${wire('h1', 'Account')}</div><div class="wf-main">${wire('h1', 'Top bar')}${wire('fill', 'View')}</div></div>`);
C('layout', 'ws-switcher', 'WorkspaceSwitcher', 'Composite', 'Logo, workspace name and switch arrows. First row of the sidebar.', { props: [['workspace', 'workspace', '']], from: ['icon'], used: ['Sidebar'] },
  () => `<div class="lib-side" style="background:var(--bg);width:248px"><div class="ws" style="height:52px;padding:0"><button class="ws-btn"><span class="logo">D</span><span class="ws-text" style="flex:1"><div class="ws-name">Dxbitz</div><div class="ws-sub">Workspace</div></span>${icon('chevrons-up-down', 'sm ws-chev')}</button></div></div>`);
C('layout', 'app-bar', 'AppBar', 'Composite', 'Row of AppTabs on white. The active app opens into the grey sidebar below with soft corners.', { props: [['apps', 'list', ''], ['active', 'app', '']], from: ['app-tab'], used: ['Sidebar'] },
  () => `<div class="lib-appbar">${sidebarAppbar()}</div>`);
C('layout', 'sidebar-nav', 'SidebarNav', 'Composite', 'Grouped NavItems with small uppercase group labels. Scrolls on its own.', { props: [['groups', 'list', ''], ['active', 'route', '']], from: ['nav-item'], used: ['Sidebar'] },
  () => `<div class="lib-side" style="width:248px">${navItem('home')}${navItem('inbox')}<div class="nav-group"><div class="nav-label">Records</div>${navItem('leads')}${navItem('deals')}</div></div>`);
C('layout', 'top-bar', 'TopBar', 'Composite', 'Sidebar toggle, breadcrumbs, search, notifications and the New menu.', { props: [['crumbs', 'list', '']], from: ['icon-button', 'breadcrumbs', 'search-input', 'menu-button'], used: ['App shell'] },
  () => `<div class="topbar lib-bar" style="width:100%"><button class="icon-btn">${icon('panel-left')}</button><span class="sep"></span><div class="crumbs"><a>Deals</a>${icon('chevron-right', 'xs')}<span class="cur">ERP rollout, 3 entities</span></div><div class="top-actions"><button class="top-search">${icon('search', 'sm')}<span>Search</span><kbd>⌘K</kbd></button><button class="icon-btn">${icon('bell')}</button><button class="btn primary sm">${icon('plus', 'sm')}New${icon('chevron-down', 'xs')}</button></div></div>`);
C('layout', 'breadcrumbs', 'Breadcrumbs', 'Element', 'Where you are, with links back up.', { props: [['items', 'list', '']], used: ['Top bar'] },
  () => `<div class="crumbs" style="flex:none"><a>Invoices</a>${icon('chevron-right', 'xs')}<span class="cur">INV-2026-00041</span></div>`);
C('layout', 'page-header', 'PageHeader', 'Composite', 'Page icon, title, optional count and the page actions on the right.', { props: [['icon', 'icon', ''], ['title', 'text', ''], ['actions', 'list', '']], from: ['icon-tile', 'button'], used: ['List pages'] },
  () => `<div style="width:100%">${pageHead('deals', 'Deals', null, `<button class="btn">${icon('sliders-horizontal', 'sm tint')}Filter</button><button class="btn primary">${icon('plus', 'sm')}New deal</button>`)}</div>`);
C('layout', 'tabs', 'Tabs', 'Composite', 'Row of Tabs with an underline in the record accent. Scrolls sideways on phones.', { props: [['tabs', 'list', ''], ['active', 'cell', '']], from: ['tab'], used: ['Doc view', 'Lists'] },
  () => `<div class="tabs" style="width:100%;margin:0"><button class="on">All deals <span class="n">16</span></button><button>My deals <span class="n">6</span></button><button>Closing this month <span class="n">8</span></button><button class="add">${icon('plus', 'sm')}View</button></div>`);
C('layout', 'toolbar', 'Toolbar', 'Composite', 'Search, filters and a summary in one wrapping row above a list.', { props: [['left', 'slots', ''], ['right', 'slots', '']], from: ['search-input', 'avatar-group', 'filter-chip'], used: ['List pages'] },
  () => `<div class="toolbar" style="width:100%;margin:0"><div class="search-field">${icon('search', 'sm')}<input class="input" placeholder="Search deals"></div><span class="filter-chip">Tag: Hot<button>${icon('x', 'xs')}</button></span><span class="grow"></span><div class="summary"><span>Pipeline <b class="num">AED 1,146,000</b></span></div></div>`);
C('layout', 'panel', 'Panel', 'Composite', 'Bordered card with a header row and a body. The standard container on dashboards.', { props: [['title', 'text', ''], ['action', 'link', 'Optional'], ['body', 'slot', '']], from: ['link-button'], used: ['Home', 'Reports', 'Tasks'] },
  () => `<section class="panel" style="width:360px"><div class="panel-h"><h3>My tasks</h3><a class="link">View all ${icon('arrow-up-right', 'xs')}</a></div><div class="panel-b muted">Panel body</div></section>`);
C('layout', 'doc-frame', 'DocFrame', 'Section', 'The record layout: main column on the left, side panel on the right (or left). Side panel drops below on narrow screens.', { props: [['side', 'left, right or off', 'Per user'], ['density', 'comfortable or compact', 'Per user']], used: ['Every record'] },
  () => `<div class="wf-doc"><div class="wf-col">${wire('h2', 'Header')}${wire('h1', 'Status flow')}${wire('h1', 'Summary strip')}${wire('h1', 'Tabs')}${wire('fill', 'Tab body')}</div><div class="wf-col side">${wire('h1', 'Next step')}${wire('h2', 'Key fields')}${wire('h2', 'People')}${wire('h1', 'Record info')}</div></div>`);
C('layout', 'side-block', 'SideBlock', 'Composite', 'Titled block inside the side panel, with an optional header action.', { props: [['title', 'text', ''], ['action', 'button', 'Optional'], ['body', 'slot', '']], used: ['Side panel'] },
  () => `<div class="doc-side" style="padding:16px;border:1px solid var(--border);border-radius:12px;width:300px"><div class="side-sec" style="margin:0"><h4>Key fields <button class="h-link">All fields</button></h4><p class="muted side-empty">Block body</p></div></div>`);
C('layout', 'drawer', 'Drawer', 'Composite', 'Panel that slides in from the right over the view. For quick looks without leaving the page.', { props: [['title', 'text', ''], ['body', 'slot', ''], ['footer', 'slot', 'Optional']], used: ['Record peek'] },
  () => `<button class="btn" data-a="libDrawer">${icon('panel-right', 'sm tint')}Open a drawer</button>`);
C('layout', 'modal', 'Modal', 'Composite', 'Centred dialog with a header, body and footer. Esc and the scrim close it.', { props: [['title', 'text', ''], ['body', 'slot', ''], ['footer', 'slot', '']], used: ['Create forms', 'Action dialogs', 'Shortcuts'] },
  () => `<button class="btn" data-a="libModal">${icon('maximize-2', 'sm tint')}Open a dialog</button>`);
C('layout', 'popover', 'Popover', 'Composite', 'Floating menu anchored to its button. Repositions near screen edges.', { props: [['anchor', 'element', ''], ['items', 'list of MenuItem', '']], used: ['Menus', 'Filters', 'Columns'] },
  () => `<div class="pop show lib-static">${mi('x', 'handshake', 'Deal', '', 'C')}${mi('x', 'target', 'Lead')}<hr>${mi('x', 'trash-2', 'Delete', 'style="color:var(--t-rose)"')}</div>`);
C('layout', 'bulk-bar', 'BulkBar', 'Composite', 'Dark bar that rises from the bottom when rows are selected.', { props: [['count', 'number', ''], ['actions', 'list', '']], used: ['Lists'] },
  () => `<div class="bulk show lib-static"><span class="n">3 selected</span><button>${icon('user', 'sm')}Assign</button><button>${icon('arrow-right', 'sm')}Move stage</button><button>${icon('trash-2', 'sm')}Delete</button><button>${icon('x', 'sm')}</button></div>`);
C('layout', 'floating-panel', 'FloatingPanel', 'Composite', 'Panel that opens above a FloatingButton and closes on outside click or Esc.', { props: [['title', 'text', ''], ['body', 'slot', ''], ['footer', 'slot', '']], from: ['fab'], used: ['Layout control'] },
  () => `<div class="lp lib-static" style="width:260px"><div class="lp-h"><div><b>Panel title</b><span>Short note</span></div><button class="icon-btn">${icon('x', 'sm')}</button></div><div class="lp-b"><p class="muted" style="margin:6px 8px 10px">Body</p></div></div>`);
C('layout', 'sticky-bar', 'StickyBar', 'Composite', 'Bar that sticks to the top of the view while the page scrolls.', { props: [['left', 'slot', ''], ['right', 'slot', '']], used: ['Doc layout'] },
  () => `<div class="lay-bar lib-bar" style="position:static;width:100%"><div class="lay-t"><span class="ph-icon b-slate" style="width:28px;height:28px;border-radius:8px">${icon('columns-3', 'sm')}</span><div><b>Doc layout</b><span>Sticky title</span></div></div>${segd(['Sample data', 'Placeholders'])}</div>`);

/* sections */
C('sections', 'doc-header', 'DocHeader', 'Section', 'Record mark, record type, title, key facts and the main actions. Moves and actions come from the model.', { props: [['record', 'record', ''], ['meta', 'key fields', ''], ['actions', 'moves and actions', '']], from: ['org-mark', 'pill', 'move-button', 'button', 'menu-button'], used: ['Every record'] },
  () => inert(docHeader('deal', d1())));
C('sections', 'status-flow', 'StatusFlow', 'Section', 'The states of the State field as connected steps. Done steps tint, the current one fills, terminal states turn green or red.', { props: [['state', 'State field', ''], ['moves', 'list', 'Click runs a move']], used: ['Doc view'], variants: ['In progress', 'Won', 'Lost or void'] },
  () => stack(inert(docFlow('deal', d1())), inert(docFlow('invoice', invoice('i39')))));
C('sections', 'state-banner', 'LockBanner', 'Section', 'The record\'s lifecycle under the header: draft, locked with void and revise, or void with the reason.', { props: [['record', 'record', '']], from: ['banner', 'button'], used: ['Transaction records'] },
  () => inert(invBanner(i41())));
C('sections', 'activity-composer', 'ActivityComposer', 'Section', 'One box for a note, email, call log or task. Cmd Enter saves to the journal.', { props: [['modes', 'list', 'note, email, call, task'], ['record', 'record', '']], from: ['segmented', 'button', 'icon-button', 'kbd'], used: ['Activity tab'] },
  () => `<div style="width:100%">${inert(composer())}</div>`);
C('sections', 'journal', 'Journal', 'Section', 'Append-only history grouped by day: notes, messages, field changes, moves, postings and payments.', { props: [['record', 'record', ''], ['kinds', 'filter', 'Optional']], from: ['timeline-item', 'diff', 'message-card'], used: ['Activity tab'] },
  () => `<div style="width:100%">${inert(timeline(actsFor('invoice', i41()).slice(0, 4)))}</div>`);
C('sections', 'child-table', 'ChildTable', 'Section', 'Lines of a record. Pinned row and first column, resizable and reorderable columns saved per user, Tab and Enter editing, inline row expand, focus view and live totals. Locks with the record.', { props: [['lines', 'Lines field', ''], ['columns', 'line fields', ''], ['totals', 'list', ''], ['locked', 'rule', '']], from: ['checkbox', 'button', 'data-table', 'base-field'], used: ['Items', 'Lines', 'Branches'], live: 1 },
  () => `<div style="width:100%">${gridBlock('deal-items', d1())}</div>`);
C('sections', 'form-section', 'FormSection', 'Section', 'Titled group of fields in two columns. Long text spans both.', { props: [['title', 'text', ''], ['note', 'text', ''], ['fields', 'list', '']], from: ['inline-value', 'base-field'], used: ['Details tab'] },
  () => `<div style="width:100%"><section class="fs" style="margin:0"><div class="fs-h"><h3>Overview</h3><p>Who, what and where it stands</p></div><div class="fs-g">${inert(['t', 'org', 'ct', 'o'].map(k => fieldCell('deal', d1(), k)).join(''))}</div></section></div>`);
C('sections', 'postings', 'PostingsTable', 'Section', 'Rows the record wrote to other models, with reversals and a balance check. Read only.', { props: [['effects', 'Effects', '']], from: ['info-note', 'data-table', 'chip'], used: ['Transaction records'] },
  () => `<div style="width:100%">${inert(invPostings(i41()))}</div>`);
C('sections', 'board', 'Board', 'Section', 'Columns by state with counts, totals and drag to move.', { props: [['group_by', 'State field', ''], ['measure', 'measure', 'Column total'], ['card', 'BoardCard', '']], from: ['board-card', 'pill', 'progress'], used: ['Deals'] },
  () => `<div class="lib-scroll">${inert(dealBoard(DEALS.filter(d => d.s !== 'won').slice(0, 8)))}</div>`);
C('sections', 'list-view', 'ListView', 'Section', 'Page header, saved views, toolbar and DataTable, with a board switch when the model has a State.', { props: [['model', 'model', ''], ['views', 'saved views', '']], from: ['page-header', 'tabs', 'toolbar', 'data-table'], used: ['Every list'] },
  () => `<div class="wf-doc" style="grid-template-columns:1fr"><div class="wf-col">${wire('h1', 'Page header')}${wire('h1', 'Saved view tabs')}${wire('h1', 'Toolbar')}${wire('fill', 'Data table or board')}</div></div>`);
C('sections', 'command-palette', 'CommandPalette', 'Section', 'Search across records, pages and actions, with recent searches and a customizable quick access grid.', { props: [['recent', 'list', 'Last 4'], ['quick', 'list', '1 or 2 rows']], from: ['search-input', 'quick-access', 'kbd'], used: ['Cmd K'] },
  () => `<button class="btn" data-a="libPalette">${icon('search', 'sm tint')}Open search</button>`);
C('sections', 'quick-access', 'QuickAccess', 'Section', 'Grid of tiles for modules, reports and documents. Documents carry a plus or list badge.', { props: [['tiles', 'list', ''], ['rows', '1 or 2', '']], from: ['icon-tile'], used: ['Command palette'] },
  () => `<div class="qa-grid" style="--cols:6;width:100%">${QA.ids.slice(0, 6).map(id => QA_ALL.find(x => x.id === id)).map(t => `<div class="qa-tile"><span class="qa-ic b-${t.c}">${icon(t.ic)}${t.badge ? `<i class="qa-badge">${icon(t.badge, 'xs')}</i>` : ''}</span><span class="qa-lbl">${t.n}</span></div>`).join('')}</div>`);
C('sections', 'layout-control', 'LayoutControl', 'Section', 'Floating control on records to show or hide blocks, set density and side panel position. On the layout page it edits the template.', { props: [['scope', 'user or template', '']], from: ['fab', 'floating-panel', 'switch', 'segmented'], used: ['Every record', 'Doc layout'] },
  () => `<div class="lp lib-static" style="width:280px"><div class="lp-h"><div><b>Layout</b><span>Only you see this</span></div></div><div class="lp-b"><div class="lp-gh">Main</div>${['Status flow', 'Summary strip', 'Activity composer'].map((n, i) => `<button class="lp-row"><span class="lbl">${n}</span><span class="sw ${i !== 1 ? 'on' : ''}"><i></i></span></button>`).join('')}</div></div>`);
C('sections', 'create-form', 'CreateForm', 'Section', 'Dialog with the fields a new record needs, validated before it closes.', { props: [['model', 'model', ''], ['fields', 'required fields', '']], from: ['modal', 'base-field', 'button'], used: ['New menu'] },
  () => `<div class="modal lib-static" style="width:420px"><div class="modal-h"><span class="ph-icon b-blue" style="width:28px;height:28px;border-radius:8px">${icon('handshake', 'sm')}</span><h2>New deal</h2></div><div class="modal-b">${fl('Deal name', inp(''), { req: 1, err: 'Deal name is required', cls: 'full' })}${fl('Amount (AED)', aff('', '45,000'), { req: 1 })}${fl('Stage', '<select class="input"><option>Qualification</option></select>')}</div><div class="modal-f"><span class="grow"></span><button class="btn ghost">Cancel</button><button class="btn primary">Create deal</button></div></div>`);
C('sections', 'action-dialog', 'ActionDialog', 'Section', 'Small dialog that collects an action\'s inputs before it runs.', { props: [['action', 'model action', ''], ['inputs', 'fields', '']], from: ['modal', 'base-field'], used: ['Record payment', 'Void', 'Revise'] },
  () => `<div class="modal lib-static" style="width:380px"><div class="modal-h"><span class="ph-icon b-green" style="width:28px;height:28px;border-radius:8px">${icon('wallet', 'sm')}</span><h2>Record payment</h2></div><div class="modal-b" style="grid-template-columns:1fr">${fl('Amount (AED)', aff('', '25,200.00'), { req: 1 })}${fl('Received on', '<input class="input" type="date" value="2026-11-01">', { req: 1 })}</div><div class="modal-f"><span class="grow"></span><button class="btn ghost">Cancel</button><button class="btn primary">Record payment</button></div></div>`);
C('sections', 'peek-drawer', 'RecordPeek', 'Section', 'Drawer with a record\'s header, quick actions and details, plus a button to open the full record.', { props: [['record', 'record', '']], from: ['drawer', 'kv-list', 'mini-card'], used: ['People', 'Related'] },
  () => `<button class="btn" data-a="libDrawer">${icon('panel-right', 'sm tint')}Peek a contact</button>`);
C('sections', 'notifications', 'NotificationsPanel', 'Section', 'Popover list of recent notifications with mark all read and a link to the inbox.', { props: [['items', 'list', '']], from: ['popover', 'notification'], used: ['Top bar'] },
  () => `<div class="pop notif show lib-static" style="width:340px"><div class="nh"><b>Notifications</b><span class="link">Mark all read</span></div>${NOTIFS.slice(0, 2).map(n => `<div class="ni ${n.unread ? 'unread' : ''}"><span class="avatar b-${n.c}" style="border-radius:7px">${icon(n.ic, 'sm')}</span><div>${n.html}<div class="tm">${n.tm}</div></div></div>`).join('')}</div>`);
C('sections', 'side-panel', 'SidePanel', 'Section', 'Stack of side blocks: next step, key fields, highlight card, people, related records, tags and record info. Each can be turned off.', { props: [['blocks', 'list', 'From the template and user layout']], from: ['side-block', 'kv-list', 'mini-card', 'scorecard'], used: ['Every record'] },
  () => `<div class="doc-side lib-scroll" style="max-height:420px;width:340px;border:1px solid var(--border);border-radius:12px">${inert(docSide('deal', d1()))}</div>`);

function sidebarAppbar() { return `<div class="appbar" style="padding:0 12px">${APPS.map(a => `<button class="ab-app ${a.on ? 'on' : ''}" data-tip="${a.n}"><span class="b-${a.c}">${icon(a.ic, 'sm')}</span></button>`).join('')}</div><div class="lib-appbar-below"></div>`; }

/* page */
VIEWS.library = () => {
  const q = (S.libQ || '').toLowerCase();
  const items = LIB.filter(c => !q || (c.name + ' ' + c.cat).toLowerCase().includes(q));
  return `<div class="page lib">
    <div class="ph"><span class="ph-icon b-violet">${icon('layers', 'lg')}</span><h1>Library</h1><span class="count num">${LIB.length} components</span><span class="grow"></span>
      <div class="lib-legend">${Object.entries(LEVELS).map(([l, c]) => `<span class="lvl b-${c}">${l}</span>`).join('')}</div></div>
    ${CATS.map(([k, n, ic, c]) => { const list = items.filter(x => x.cat === k); if (!list.length) return ''; return `<section class="lib-sec" id="lib-${k}" data-libsec="${k}"><div class="lib-sh"><span class="ph-icon b-${c}" style="width:26px;height:26px;border-radius:7px">${icon(ic, 'sm')}</span><h2>${n}</h2><span class="muted num">${list.length}</span></div>
      <div class="lib-grid">${list.map(libCard).join('')}</div></section>`; }).join('') || emptyState('search', 'slate', 'No components match', 'Try another name.')}
  </div>`;
};
function libCard(c) {
  let demo; try { demo = c.demo(); } catch (e) { demo = `<span class="muted">Preview unavailable</span>`; }
  const wide = c.level === 'Section' || ['data-table', 'summary-strip', 'tabs', 'toolbar', 'top-bar', 'page-header', 'quick-access', 'banner', 'journal'].includes(c.id);
  return `<article class="lib-card ${wide ? 'wide' : ''}" id="libc-${c.id}" data-libcard="${c.id}">
    <div class="lib-demo">${demo}</div>
    <button class="lib-foot" data-a="libOpen" data-id="${c.id}"><b>${c.name}</b><span class="lvl b-${LEVELS[c.level]}">${c.level}</span><span class="grow"></span><span class="muted">Details</span>${icon('chevron-right', 'xs muted')}</button></article>`;
}
function libDetail(id) {
  const c = LIB.find(x => x.id === id); if (!c) return;
  const by = LIB.filter(x => x.from.includes(id));
  const chips = ids => ids.map(i => { const x = LIB.find(y => y.id === i); return x ? `<button class="chip lib-link" data-a="libOpen" data-id="${x.id}">${x.name}</button>` : ''; }).join('');
  openModal(`<div class="modal lib-modal" role="dialog" aria-label="${c.name}">
    <div class="modal-h"><span class="lvl b-${LEVELS[c.level]}">${c.level}</span><h2>${c.name}</h2><span class="muted" style="font-size:12.5px">${CATS.find(x => x[0] === c.cat)[1]}</span><button class="icon-btn" data-a="closeModal" aria-label="Close">${icon('x')}</button></div>
    <div class="lib-mb">
      ${c.live ? '' : `<div class="lib-prev">${c.demo()}</div>`}
      <p class="lib-desc">${c.desc}</p>
      ${c.model ? `<div class="lib-kv"><span>Model</span><code>${esc(c.model)}</code></div>` : ''}
      ${c.props.length ? `<h4>Properties</h4><table class="lib-props"><tbody>${c.props.map(([n, t, d]) => `<tr><td><code>${esc(n)}</code></td><td class="muted">${esc(t)}</td><td>${esc(d)}</td></tr>`).join('')}</tbody></table>` : ''}
      ${c.variants.length ? `<h4>Variants and states</h4><div class="tag-row">${c.variants.map(v => `<span class="chip">${v}</span>`).join('')}</div>` : ''}
      ${c.from.length ? `<h4>Built from</h4><div class="tag-row">${chips(c.from)}</div>` : ''}
      ${by.length ? `<h4>Used by components</h4><div class="tag-row">${chips(by.map(x => x.id))}</div>` : ''}
      ${c.used.length ? `<h4>Appears in</h4><div class="tag-row">${c.used.map(u => `<span class="chip">${u}</span>`).join('')}</div>` : ''}
    </div></div>`);
}

/* sidebar index and scroll sync */
const baseSidebar = renderSidebar;
renderSidebar = () => {
  baseSidebar();
  if (S.route !== 'library') return;
  const q = (S.libQ || '').toLowerCase();
  $('#sidebar .side-app').innerHTML = `<button class="lib-back" data-a="go" data-r="home">${icon('arrow-left', 'sm')}</button><b>Library</b><span>${LIB.length}</span>`;
  $('#sidebar .nav').innerHTML = `<div class="lib-filter search-field">${icon('search', 'sm')}<input class="input" id="libQ" placeholder="Filter components" value="${esc(S.libQ || '')}" autocomplete="off"></div>` +
    CATS.map(([k, n, ic, c]) => { const list = LIB.filter(x => x.cat === k && (!q || x.name.toLowerCase().includes(q))); if (!list.length) return ''; return `<div class="nav-group"><button class="nav-label lib-cat" data-a="libJump" data-id="lib-${k}">${n}<span>${list.length}</span></button>${list.map(x => `<button class="nav-item lib-idx ${S.libActive === x.id ? 'active' : ''}" data-a="libJump" data-id="libc-${x.id}" data-idx="${x.id}"><span class="lvl-dot" style="background:var(--t-${LEVELS[x.level]})"></span><span class="lbl">${x.name}</span></button>`).join('')}</div>`; }).join('');
};
let libLock = 0, libRaf = 0;
function libSpy() {
  libRaf = 0;
  if (S.route !== 'library' || Date.now() < libLock) return;
  const v = $('#view'), line = v.getBoundingClientRect().top + 90;
  const card = $$('[data-libcard]').find(el => el.getBoundingClientRect().bottom > line);
  if (card && card.dataset.libcard !== S.libActive) libMark(card.dataset.libcard);
}
$('#view').addEventListener('scroll', () => { if (S.route === 'library' && !libRaf) libRaf = requestAnimationFrame(libSpy); }, { passive: true });
const baseAfter = afterRender;
afterRender = () => { baseAfter(); if (S.route === 'library') requestAnimationFrame(libSpy); };
function libMark(id) {
  S.libActive = id;
  $$('.lib-idx.active').forEach(x => x.classList.remove('active'));
  const it = $(`.lib-idx[data-idx="${id}"]`); if (!it) return;
  it.classList.add('active');
  const nav = $('#sidebar .nav'), a = it.getBoundingClientRect(), b = nav.getBoundingClientRect();
  if (a.top < b.top + 60 || a.bottom > b.bottom - 20) nav.scrollTo({ top: nav.scrollTop + (a.top - b.top) - b.height / 3, behavior: 'smooth' });
}
document.addEventListener('input', e => { if (e.target.id !== 'libQ') return; S.libQ = e.target.value; const pos = e.target.selectionStart; render(); const el = $('#libQ'); if (el) { el.focus(); el.setSelectionRange(pos, pos); } });
document.addEventListener('click', e => {
  if (S.route !== 'library') return;
  const card = e.target.closest('.lib-demo'); if (!card) return;
  if (e.target.closest('[data-demo]')) { e.preventDefault(); e.stopPropagation(); libDetail(card.closest('[data-libcard]').dataset.libcard); return; }
  if (e.target.closest('input,select,textarea,button,a,[data-a],[contenteditable],.g-scroll')) return;
  libDetail(card.closest('[data-libcard]').dataset.libcard);
}, true);
Object.assign(A, {
  libOpen: el => libDetail(el.dataset.id),
  libJump: el => { const t = document.getElementById(el.dataset.id); if (!t) return; const v = $('#view'); libLock = Date.now() + 900; v.scrollTo({ top: v.scrollTop + t.getBoundingClientRect().top - v.getBoundingClientRect().top - ($('.lib .ph')?.offsetHeight || 0) - 14, behavior: 'smooth' }); if (el.dataset.idx) libMark(el.dataset.idx); $('#app').classList.remove('mobile-open'); $('#scrim').classList.remove('show'); },
  libSw: el => el.querySelector('.sw').classList.toggle('on'),
  libCheck: el => el.querySelector('.check').classList.toggle('on'),
  libSeg: el => { $$('button', el.parentElement).forEach(b => b.classList.toggle('on', b === el)); },
  libToast: () => toast('Saved', { undo: () => { } }),
  libModal: () => openForm('Example dialog', 'sparkles', 'violet', fld('lx_n', 'Name', '<input class="input" id="lx_n">', 1), 'Save', f => $('#lx_n', f).value.trim() ? (toast('Saved'), null) : { lx_n: 'Name is required' }),
  libDrawer: () => peekContact('c1'),
  libPop: el => openPop(el, `${mi('x', 'handshake', 'Deal', '', 'C')}${mi('x', 'target', 'Lead')}${mi('x', 'user-plus', 'Contact')}`),
  libPalette: () => openPalette(),
});
