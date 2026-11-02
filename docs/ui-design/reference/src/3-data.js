
/* ---------- sample data (fictional) ---------- */
const NOW0 = new Date(2026, 10, 1, 13, 20);
const T0 = Date.now();
const now = () => new Date(NOW0.getTime() + (Date.now() - T0));
const ME = 'u1';

const USERS = {
  u1: { n: 'Aslam K', i: 'AK', c: 'blue', role: 'Admin' },
  u2: { n: 'Sara Mathew', i: 'SM', c: 'violet', role: 'Account Executive' },
  u3: { n: 'Omar Haddad', i: 'OH', c: 'teal', role: 'Account Executive' },
  u4: { n: 'Rahul Nair', i: 'RN', c: 'amber', role: 'Sales Manager' },
};

const STAGES = [
  { k: 'qual', n: 'Qualification', c: 'slate', p: 10 },
  { k: 'disc', n: 'Discovery', c: 'blue', p: 25 },
  { k: 'prop', n: 'Proposal', c: 'violet', p: 50 },
  { k: 'nego', n: 'Negotiation', c: 'amber', p: 75 },
  { k: 'won', n: 'Won', c: 'green', p: 100 },
];
const LOST = { k: 'lost', n: 'Lost', c: 'rose', p: 0 };
const stageOf = k => STAGES.find(s => s.k === k) || LOST;
const SOURCES = ['Referral', 'Website', 'LinkedIn', 'Event', 'Google Ads', 'Cold call', 'Partner'];

const ORGS = [
  { id: 'o1', n: 'Al Sahra Trading LLC', ind: 'Trading', loc: 'Dubai', c: 'blue', web: 'alsahra-trading.ae' },
  { id: 'o2', n: 'Marina Steel Fabrication', ind: 'Manufacturing', loc: 'Sharjah', c: 'slate', web: 'marinasteel.ae' },
  { id: 'o3', n: 'Gulf Crest Logistics', ind: 'Logistics', loc: 'Jebel Ali', c: 'teal', web: 'gulfcrest.ae' },
  { id: 'o4', n: 'Noor Interiors & Fitout', ind: 'Fit-out', loc: 'Dubai', c: 'amber', web: 'noorinteriors.ae' },
  { id: 'o5', n: 'Bayan Medical Supplies', ind: 'Healthcare', loc: 'Abu Dhabi', c: 'rose', web: 'bayanmedical.ae' },
  { id: 'o6', n: 'Desert Line Signage', ind: 'Signage', loc: 'Ajman', c: 'violet', web: 'desertline.ae' },
  { id: 'o7', n: 'Zahra Foods FZE', ind: 'FMCG', loc: 'Ras Al Khaimah', c: 'green', web: 'zahrafoods.ae' },
  { id: 'o8', n: 'Horizon Courier Services', ind: 'Courier', loc: 'Dubai', c: 'blue', web: 'horizoncourier.ae' },
  { id: 'o9', n: 'Qasr Building Contracting', ind: 'Construction', loc: 'Abu Dhabi', c: 'amber', web: 'qasrcontracting.ae' },
  { id: 'o10', n: 'Falcon Auto Parts', ind: 'Auto parts', loc: 'Sharjah', c: 'teal', web: 'falconautoparts.ae' },
];

const CONTACTS = [
  { id: 'c1', n: 'Khalid Al Mansoori', t: 'Finance Director', org: 'o1', e: 'khalid@alsahra-trading.ae', p: '+971 50 418 2290', c: 'blue' },
  { id: 'c2', n: 'Priya Menon', t: 'Operations Manager', org: 'o1', e: 'priya@alsahra-trading.ae', p: '+971 55 302 7714', c: 'violet' },
  { id: 'c3', n: 'Youssef Kassem', t: 'General Manager', org: 'o2', e: 'youssef@marinasteel.ae', p: '+971 50 771 4408', c: 'slate' },
  { id: 'c4', n: 'Fatima Rahman', t: 'Head of Procurement', org: 'o3', e: 'fatima@gulfcrest.ae', p: '+971 52 645 1180', c: 'teal' },
  { id: 'c5', n: 'Daniel Pereira', t: 'IT Manager', org: 'o3', e: 'daniel@gulfcrest.ae', p: '+971 56 213 9902', c: 'blue' },
  { id: 'c6', n: 'Mariam Saeed', t: 'Managing Partner', org: 'o4', e: 'mariam@noorinteriors.ae', p: '+971 50 990 3341', c: 'amber' },
  { id: 'c7', n: 'Ahmed Fawzi', t: 'CFO', org: 'o5', e: 'ahmed.f@bayanmedical.ae', p: '+971 55 118 6620', c: 'rose' },
  { id: 'c8', n: 'Nikhil Rao', t: 'Owner', org: 'o6', e: 'nikhil@desertline.ae', p: '+971 50 337 5129', c: 'violet' },
  { id: 'c9', n: 'Hessa Al Ali', t: 'Head of Supply Chain', org: 'o7', e: 'hessa@zahrafoods.ae', p: '+971 52 880 4416', c: 'green' },
  { id: 'c10', n: 'Tariq Aziz', t: 'Operations Director', org: 'o8', e: 'tariq@horizoncourier.ae', p: '+971 56 471 0083', c: 'blue' },
  { id: 'c11', n: 'Leena Joseph', t: 'Project Controls Lead', org: 'o9', e: 'leena@qasrcontracting.ae', p: '+971 50 264 7735', c: 'amber' },
  { id: 'c12', n: 'Imran Siddiqui', t: 'Purchase Manager', org: 'o10', e: 'imran@falconautoparts.ae', p: '+971 55 902 1146', c: 'teal' },
];

const DEALS = [
  { id: 'd1', t: 'ERP rollout, 3 entities', org: 'o1', ct: 'c1', v: 186000, s: 'nego', o: 'u1', close: '2026-11-08', pr: 80, tags: ['Hot'], src: 'Referral', created: '2026-10-15' },
  { id: 'd2', t: 'Production planning module', org: 'o2', ct: 'c3', v: 94000, s: 'prop', o: 'u2', close: '2026-11-20', pr: 50, tags: [], src: 'Website', created: '2026-10-09' },
  { id: 'd3', t: 'Fleet and warehouse ERP', org: 'o3', ct: 'c4', v: 142000, s: 'disc', o: 'u3', close: '2026-12-15', pr: 25, tags: ['Large'], src: 'Event', created: '2026-10-21' },
  { id: 'd4', t: 'Project costing and BOQ', org: 'o4', ct: 'c6', v: 68000, s: 'prop', o: 'u1', close: '2026-11-12', pr: 55, tags: ['Hot'], src: 'Referral', created: '2026-10-02' },
  { id: 'd5', t: 'Batch and expiry tracking', org: 'o5', ct: 'c7', v: 112000, s: 'nego', o: 'u4', close: '2026-11-05', pr: 75, tags: [], src: 'LinkedIn', created: '2026-09-24' },
  { id: 'd6', t: 'Job card workflow', org: 'o6', ct: 'c8', v: 36000, s: 'qual', o: 'u2', close: '2026-11-30', pr: 10, tags: [], src: 'Google Ads', created: '2026-10-28' },
  { id: 'd7', t: 'Distributor portal', org: 'o7', ct: 'c9', v: 78000, s: 'disc', o: 'u1', close: '2026-12-18', pr: 30, tags: [], src: 'Partner', created: '2026-10-19' },
  { id: 'd8', t: 'Shipment tracking integration', org: 'o8', ct: 'c10', v: 54000, s: 'qual', o: 'u3', close: '2026-12-10', pr: 10, tags: [], src: 'Website', created: '2026-10-30' },
  { id: 'd9', t: 'Progress billing and retention', org: 'o9', ct: 'c11', v: 164000, s: 'prop', o: 'u4', close: '2026-11-25', pr: 50, tags: ['Large'], src: 'Referral', created: '2026-10-06' },
  { id: 'd10', t: 'POS and inventory sync', org: 'o10', ct: 'c12', v: 42000, s: 'won', o: 'u2', close: '2026-10-28', pr: 100, tags: [], src: 'Website', created: '2026-09-12' },
  { id: 'd11', t: 'AMC renewal 2027', org: 'o1', ct: 'c2', v: 24000, s: 'won', o: 'u1', close: '2026-10-30', pr: 100, tags: [], src: 'Referral', created: '2026-10-01' },
  { id: 'd12', t: 'HR and WPS payroll', org: 'o2', ct: 'c3', v: 38000, s: 'disc', o: 'u1', close: '2026-11-22', pr: 25, tags: [], src: 'Website', created: '2026-10-24' },
  { id: 'd13', t: 'VAT and e-invoicing readiness', org: 'o5', ct: 'c7', v: 29000, s: 'qual', o: 'u4', close: '2026-11-14', pr: 15, tags: [], src: 'LinkedIn', created: '2026-10-27' },
  { id: 'd14', t: 'Multi-branch accounting', org: 'o7', ct: 'c9', v: 88000, s: 'nego', o: 'u3', close: '2026-10-30', pr: 70, tags: [], src: 'Event', created: '2026-09-18' },
  { id: 'd15', t: 'Subcontractor management', org: 'o9', ct: 'c11', v: 57000, s: 'qual', o: 'u1', close: '2026-12-05', pr: 10, tags: [], src: 'Referral', created: '2026-10-31' },
  { id: 'd16', t: 'Customer self-service app', org: 'o8', ct: 'c10', v: 46000, s: 'won', o: 'u4', close: '2026-10-22', pr: 100, tags: [], src: 'Partner', created: '2026-09-02' },
];

const LEADS = [
  { id: 'l1', n: 'Rashid Al Hammadi', co: 'Emirates Glass Works', t: 'Operations Head', e: 'rashid@emiratesglass.ae', p: '+971 50 128 4471', src: 'Website', st: 'New', sc: 82, o: 'u1', created: '2026-11-01', c: 'teal' },
  { id: 'l2', n: 'Anjali Varma', co: 'Bluewave Pharmacy Group', t: 'IT Lead', e: 'anjali@bluewavepharmacy.ae', p: '+971 55 640 2218', src: 'LinkedIn', st: 'Contacted', sc: 64, o: 'u2', created: '2026-10-31', c: 'violet' },
  { id: 'l3', n: 'Samir Haddad', co: 'Coastline Marine Services', t: 'General Manager', e: 'samir@coastlinemarine.ae', p: '+971 50 772 9013', src: 'Referral', st: 'Qualified', sc: 91, o: 'u3', created: '2026-10-29', c: 'blue' },
  { id: 'l4', n: 'Grace Thomas', co: 'Petal and Stem Florists', t: 'Owner', e: 'grace@petalandstem.ae', p: '+971 52 304 6650', src: 'Website', st: 'New', sc: 38, o: 'u4', created: '2026-10-30', c: 'rose' },
  { id: 'l5', n: 'Majid Obaid', co: 'Al Waha Furniture', t: 'Purchase Manager', e: 'majid@alwahafurniture.ae', p: '+971 56 118 3392', src: 'Event', st: 'Contacted', sc: 57, o: 'u1', created: '2026-10-26', c: 'amber' },
  { id: 'l6', n: 'Reem Khalifa', co: 'Sky Bridge Aviation Services', t: 'Finance Manager', e: 'reem@skybridgeaviation.ae', p: '+971 50 845 7720', src: 'Google Ads', st: 'Qualified', sc: 76, o: 'u2', created: '2026-10-24', c: 'violet' },
  { id: 'l7', n: 'Vivek Pillai', co: 'Metro Cold Stores', t: 'Director', e: 'vivek@metrocoldstores.ae', p: '+971 55 230 9184', src: 'Cold call', st: 'Unqualified', sc: 22, o: 'u3', created: '2026-10-20', c: 'slate' },
  { id: 'l8', n: 'Noura Al Shamsi', co: 'Lumen Lighting Trading', t: 'Operations Manager', e: 'noura@lumenlighting.ae', p: '+971 50 663 0417', src: 'Website', st: 'New', sc: 69, o: 'u4', created: '2026-10-31', c: 'green' },
  { id: 'l9', n: 'Hassan Jaber', co: 'Atlas Scaffolding', t: 'Projects Manager', e: 'hassan@atlasscaffolding.ae', p: '+971 52 917 2205', src: 'Referral', st: 'Contacted', sc: 48, o: 'u1', created: '2026-10-22', c: 'amber' },
  { id: 'l10', n: 'Elena Kovacs', co: 'Verde Packaging FZE', t: 'CFO', e: 'elena@verdepackaging.ae', p: '+971 56 405 8831', src: 'LinkedIn', st: 'New', sc: 71, o: 'u2', created: '2026-10-28', c: 'teal' },
];
const LEAD_ST = { New: 'blue', Contacted: 'amber', Qualified: 'green', Unqualified: 'slate', Converted: 'violet' };

const TASKS = [
  { id: 't1', t: 'Call Khalid about final pricing', ty: 'call', due: '2026-11-01', tm: '10:30', deal: 'd1', o: 'u1', done: false },
  { id: 't2', t: 'Send revised proposal to Noor Interiors', ty: 'email', due: '2026-11-01', tm: '', deal: 'd4', o: 'u1', done: false },
  { id: 't3', t: 'Production planning walkthrough', ty: 'meeting', due: '2026-11-02', tm: '14:00', deal: 'd2', o: 'u2', done: false },
  { id: 't4', t: 'Chase VAT readiness questionnaire', ty: 'todo', due: '2026-10-30', tm: '', deal: 'd13', o: 'u4', done: false },
  { id: 't5', t: 'Prepare data migration checklist', ty: 'todo', due: '2026-11-03', tm: '', deal: 'd1', o: 'u1', done: false },
  { id: 't6', t: 'Discovery workshop at Gulf Crest', ty: 'meeting', due: '2026-11-04', tm: '11:00', deal: 'd3', o: 'u3', done: false },
  { id: 't7', t: 'Share reference client list', ty: 'email', due: '2026-10-31', tm: '', deal: 'd7', o: 'u1', done: false },
  { id: 't8', t: 'Confirm go-live date with Falcon', ty: 'call', due: '2026-11-01', tm: '09:00', deal: 'd10', o: 'u2', done: true },
  { id: 't9', t: 'Review subcontractor workflow notes', ty: 'todo', due: '2026-11-05', tm: '', deal: 'd15', o: 'u1', done: false },
  { id: 't10', t: 'Book site visit at Qasr head office', ty: 'meeting', due: '2026-11-01', tm: '16:30', deal: 'd9', o: 'u1', done: false },
];
const TASK_TY = { call: ['phone', 'green'], email: ['mail', 'blue'], meeting: ['video', 'violet'], todo: ['circle-check', 'amber'] };

const ACTS = [
  { id: 'a1', deal: 'd1', ty: 'call', by: 'u1', at: '2026-11-01T09:12', text: 'Khalid confirmed board approval is in. He asked for 5% off the AMC if we sign before 15-11-2026. Offered 3%, pending partner sign-off.' },
  { id: 'a2', deal: 'd1', ty: 'email', by: 'u1', at: '2026-10-31T16:40', subj: 'Revised commercial proposal v3', text: 'Hi Khalid,\n\nPlease find the revised proposal attached. We brought implementation down to 14 weeks by running the two trading entities in parallel. Oman goes live four weeks after.\n\nHappy to walk your team through it on Monday.' },
  { id: 'a3', deal: 'd1', ty: 'stage', by: 'u1', at: '2026-10-30T11:05', from: 'prop', to: 'nego' },
  { id: 'a4', deal: 'd1', ty: 'meeting', by: 'u1', at: '2026-10-29T14:00', text: 'Scope review with finance team. Priya wants landed cost on imports and consolidated P&L across all three entities by month end.' },
  { id: 'a5', deal: 'd1', ty: 'email', by: 'c2', at: '2026-10-27T10:18', subj: 'Re: Data templates', text: 'Thanks. We will share the item master and opening balances by Thursday. Customer list may take longer.' },
  { id: 'a6', deal: 'd1', ty: 'note', by: 'u1', at: '2026-10-22T17:30', text: 'Three entities: Al Sahra Trading LLC (Dubai mainland), Al Sahra FZE (JAFZA), Al Sahra Oman SPC. Consolidated reporting is the main pain point. Currently on Tally and Excel.' },
  { id: 'a7', deal: 'd1', ty: 'created', by: 'u1', at: '2026-10-15T09:40', text: 'Referral' },
  { id: 'a8', deal: 'd2', ty: 'meeting', by: 'u2', at: '2026-11-01T11:30', text: 'Youssef wants work order costing by machine hour. Follow-up demo booked for tomorrow.' },
  { id: 'a9', deal: 'd5', ty: 'email', by: 'c7', at: '2026-11-01T08:05', subj: 'Re: Proposal', text: 'Reviewed with the board. Two questions on the batch recall report, can we discuss this week?' },
  { id: 'a10', deal: 'd10', ty: 'won', by: 'u2', at: '2026-10-28T15:22' },
  { id: 'a11', deal: 'd9', ty: 'stage', by: 'u4', at: '2026-10-31T12:10', from: 'disc', to: 'prop' },
  { id: 'a12', deal: 'd15', ty: 'created', by: 'u1', at: '2026-10-31T10:02', text: 'Referral' },
  { id: 'a13', deal: 'd4', ty: 'call', by: 'u1', at: '2026-10-30T15:45', text: 'Mariam is comparing us with one other vendor. Price is close, she cares more about BOQ import from Excel.' },
  { id: 'a14', deal: 'd11', ty: 'won', by: 'u1', at: '2026-10-30T10:00' },
];

const FILES = {
  d1: [
    { n: 'Commercial_Proposal_v3.pdf', s: '1.4 MB', by: 'u1', at: '31-10-2026', ext: 'PDF', c: 'rose' },
    { n: 'Scope_matrix_3_entities.xlsx', s: '86 KB', by: 'u1', at: '29-10-2026', ext: 'XLS', c: 'green' },
    { n: 'Entity_structure.png', s: '312 KB', by: 'c2', at: '22-10-2026', ext: 'PNG', c: 'violet' },
  ],
};

const NOTIFS = [
  { id: 'n1', ic: 'at-sign', c: 'violet', html: '<b>Sara Mathew</b> mentioned you on <b>Production planning module</b>', tm: '24m ago', unread: true, go: 'deal-d2' },
  { id: 'n2', ic: 'mail', c: 'blue', html: '<b>Ahmed Fawzi</b> replied on <b>Batch and expiry tracking</b>', tm: '5h ago', unread: true, go: 'deal-d5' },
  { id: 'n3', ic: 'clock', c: 'rose', html: 'Task overdue: <b>Share reference client list</b>', tm: 'Yesterday', unread: true, go: 'tasks' },
  { id: 'n4', ic: 'user-plus', c: 'teal', html: 'New lead assigned to you: <b>Rashid Al Hammadi</b>', tm: 'Today, 08:41', unread: true, go: 'leads' },
  { id: 'n5', ic: 'trophy', c: 'green', html: '<b>Aslam K</b> won <b>AMC renewal 2027</b> worth AED 24,000', tm: '30-10-2026', unread: false, go: 'deal-d11' },
];
