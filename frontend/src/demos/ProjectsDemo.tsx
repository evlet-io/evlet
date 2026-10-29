import {
  batch,
  createEffect,
  createMemo,
  createSignal,
  For,
  onCleanup,
  onMount,
  Show,
  type JSX,
} from 'solid-js';
import { createStore, reconcile, unwrap } from 'solid-js/store';
import {
  ALL_CATEGORIES,
  AppShell,
  Avatar,
  Badge,
  BrandArea,
  Breadcrumbs,
  buildCategories,
  Button,
  CategoryTabs,
  ExplorerView,
  Field,
  Glyph,
  IconButton,
  ListFooter,
  ListToolbar,
  Modal,
  NavigationGroup,
  PageHeader,
  RecordList,
  RecordPreview,
  SavedViews,
  Sidebar,
  SidebarFooter,
  StatusBar,
  SummaryCard,
  SummaryRow,
  Toast,
  TopBar,
  ViewBar,
  ViewSwitcher,
  WorkspaceSwitcher,
  type Column,
  type SummaryItem,
} from '../ui';
import { colors, initial, people, statusLabels, type Activity, type Project } from './data';
import { Icon, Icons, iconNames } from './icons';

const STORAGE_KEY = 'evlet-solid-studio-v1';
const seedActivity: Activity[] = [
  {
    id: 'a1',
    text: 'Sofia completed Visual direction',
    projectId: 2,
    at: '24 minutes ago',
    who: 'SK',
  },
  {
    id: 'a2',
    text: 'Aslam added Social launch toolkit',
    projectId: 1,
    at: '1 hour ago',
    who: 'AK',
  },
  { id: 'a3', text: 'Dina completed Autumn campaign', projectId: 8, at: 'Yesterday', who: 'DN' },
];
function validProject(value: unknown): value is Project {
  if (!value || typeof value !== 'object') return false;
  const p = value as Project;
  return (
    Number.isSafeInteger(p.id) &&
    p.id > 0 &&
    typeof p.name === 'string' &&
    typeof p.client === 'string' &&
    Object.hasOwn(people, p.owner) &&
    Object.hasOwn(statusLabels, p.status) &&
    Object.hasOwn(colors, p.color) &&
    ['high', 'normal'].includes(p.priority) &&
    typeof p.due === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(p.due) &&
    typeof p.description === 'string' &&
    typeof p.icon === 'string' &&
    iconNames.includes(p.icon) &&
    Array.isArray(p.tasks) &&
    p.tasks.every(
      (t) =>
        t && typeof t.id === 'string' && typeof t.title === 'string' && typeof t.done === 'boolean',
    )
  );
}
function load() {
  try {
    const data = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
    if (
      data?.version === 1 &&
      Array.isArray(data.projects) &&
      data.projects.every(validProject) &&
      new Set(data.projects.map((p: Project) => p.id)).size === data.projects.length
    )
      return {
        projects: data.projects as Project[],
        activity: (Array.isArray(data.activity)
          ? data.activity.filter(
              (a: Activity) =>
                a &&
                typeof a.text === 'string' &&
                typeof a.id === 'string' &&
                typeof a.at === 'string' &&
                Object.hasOwn(people, a.who) &&
                typeof a.projectId === 'number',
            )
          : seedActivity) as Activity[],
      };
  } catch {}
  return { projects: structuredClone(initial), activity: seedActivity };
}
const progress = (p: Project) =>
  p.tasks.length
    ? Math.round((p.tasks.filter((t) => t.done).length / p.tasks.length) * 100)
    : p.status === 'complete'
      ? 100
      : 0;
const soon = (p: Project) =>
  p.status !== 'complete' && p.due >= '2026-10-29' && p.due <= '2026-11-05';
const dateText = (date: string) =>
  new Date(date + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
function PersonAvatar(props: { id: keyof typeof people }) {
  return (
    <Avatar
      initials={props.id}
      label={people[props.id].name}
      title={people[props.id].name}
      background={people[props.id].bg}
      color={people[props.id].fg}
    />
  );
}
function Mark(props: { project: Project }) {
  return (
    <span
      class="ev-project-mark"
      style={{
        '--ev-markbg': colors[props.project.color].bg,
        '--ev-markfg': colors[props.project.color].fg,
      }}
    >
      <Icon name={props.project.icon} />
    </span>
  );
}
function Status(props: { status: Project['status'] }) {
  return (
    <Badge
      tone={
        props.status === 'active' ? 'info' : props.status === 'complete' ? 'success' : 'warning'
      }
    >
      {statusLabels[props.status]}
    </Badge>
  );
}
function Progress(props: { project: Project }) {
  return (
    <span class="ev-progress-cell">
      <span
        class="ev-track"
        style={{
          '--ev-progress': progress(props.project) + '%',
          '--ev-progress-color':
            props.project.status === 'complete' ? '#70b99c' : colors[props.project.color].fg,
        }}
      >
        <span />
      </span>
      <span class="ev-progress-num">{progress(props.project)}%</span>
    </span>
  );
}

export default function ProjectsDemo(props: { shell: (props: import("../ui").AppShellProps) => JSX.Element }) {
  const Shell = props.shell;
  const [state, setState] = createStore(load());
  const [route, setRoute] = createSignal('projects'),
    [category, setCategory] = createSignal(ALL_CATEGORIES),
    [mode, setMode] = createSignal('list'),
    [query, setQuery] = createSignal(''),
    [owner, setOwner] = createSignal('all'),
    [priority, setPriority] = createSignal('all'),
    [due, setDue] = createSignal('all'),
    [sort, setSort] = createSignal('manual');
  const [selected, setSelected] = createSignal<number | null>(
      innerWidth > 1080 ? (state.projects.find((p) => !p.archived)?.id ?? null) : null,
    ),
    [previewTab, setPreviewTab] = createSignal('tasks'),
    [checked, setChecked] = createSignal<Set<string | number>>(new Set()),
    [sidebarOpen, setSidebarOpen] = createSignal(false);
  const [dialog, setDialog] = createSignal<
      'project' | 'filters' | 'command' | 'people' | 'help' | null
    >(null),
    [editing, setEditing] = createSignal<number | null>(null),
    [command, setCommand] = createSignal('');
  const [form, setForm] = createStore({
    name: '',
    client: '',
    owner: 'AK',
    status: 'planned',
    priority: 'normal',
    due: '2026-11-12',
    color: 'violet',
    description: '',
  });
  const [formError, setFormError] = createSignal(''),
    [message, setMessage] = createSignal(''),
    [undo, setUndo] = createSignal<(() => void) | undefined>(),
    [saveState, setSaveState] = createSignal('Sample workspace · changes saved on this device');
  let toastTimer: ReturnType<typeof setTimeout> | undefined,
    searchInput: HTMLInputElement | undefined,
    nameInput: HTMLInputElement | undefined,
    commandInput: HTMLInputElement | undefined;
  const live = createMemo(() => state.projects.filter((p) => !p.archived));
  const current = createMemo(() => live().find((p) => p.id === selected()));
  const myTasks = createMemo(() =>
    live()
      .filter((p) => p.owner === 'AK')
      .flatMap((project) =>
        project.tasks.filter((t) => !t.done).map((task) => ({ project, task })),
      ),
  );
  const title = () =>
    route() === 'projects' ? 'Projects' : route() === 'tasks' ? 'My tasks' : 'Activity';
  const subtitle = () =>
    route() === 'projects'
      ? 'Plan, prioritize, and keep work moving.'
      : route() === 'tasks'
        ? 'Your open tasks, across all projects.'
        : 'The latest updates across your workspace.';
  const filtered = createMemo(() =>
    live()
      .filter(
        (p) =>
          (category() === ALL_CATEGORIES || p.status === category()) &&
          (owner() === 'all' || p.owner === owner()) &&
          (priority() === 'all' || p.priority === priority()) &&
          (due() === 'all' || soon(p)) &&
          `${p.name} ${p.client} ${people[p.owner].name}`
            .toLowerCase()
            .includes(query().toLowerCase()),
      )
      .sort((a, b) =>
        sort() === 'due'
          ? a.due.localeCompare(b.due)
          : sort() === 'name'
            ? a.name.localeCompare(b.name)
            : sort() === 'progress'
              ? progress(b) - progress(a)
              : (b.updated ?? 0) - (a.updated ?? 0),
      ),
  );
  const categories = createMemo(() =>
    buildCategories(
      live(),
      Object.entries(statusLabels).map(([value, label]) => ({ value, label })),
      (p) => p.status,
      'All projects',
    ),
  );
  const summaries = createMemo<SummaryItem[]>(() => [
    {
      id: 'total',
      label: 'Total projects',
      value: live().length,
      compactLabel: 'total',
      caption: 'Across your workspace',
      icon: <Icon name="grid" />,
      tone: 'primary',
    },
    {
      id: 'active',
      label: 'In progress',
      value: live().filter((p) => p.status === 'active').length,
      compactLabel: 'active',
      caption: 'Moving forward',
      icon: <Icon name="bolt" />,
      style: { '--ev-iconbg': '#eaf0ff', '--ev-iconfg': '#6686dc' },
    },
    {
      id: 'due',
      label: 'Due this week',
      value: live().filter(soon).length,
      compactLabel: 'due',
      caption: 'October 29 – November 5',
      icon: <Icon name="calendar" />,
      style: { '--ev-iconbg': '#fff1e1', '--ev-iconfg': '#d7a05c' },
    },
    {
      id: 'complete',
      label: 'Completed',
      value: live().filter((p) => p.status === 'complete').length,
      compactLabel: 'done',
      caption: 'Ready to hand over',
      icon: <Icon name="check-circle" />,
      style: { '--ev-iconbg': '#e4f5ed', '--ev-iconfg': '#55a68a' },
    },
  ]);
  const toast = (text: string, undoAction?: () => void) => {
    clearTimeout(toastTimer);
    setMessage(text);
    setUndo(() => undoAction);
    toastTimer = setTimeout(
      () => {
        setMessage('');
        setUndo(undefined);
      },
      undoAction ? 12000 : 4500,
    );
  };
  const log = (text: string, projectId: number) => {
    setState('activity', (a) =>
      [
        { id: crypto.randomUUID(), text, projectId, at: 'Just now', who: 'AK' as const },
        ...a,
      ].slice(0, 40),
    );
    setState('projects', (p) => p.id === projectId, 'updated', Date.now());
  };
  createEffect(() => {
    const data = JSON.stringify({ version: 1, projects: state.projects, activity: state.activity });
    try {
      localStorage.setItem(STORAGE_KEY, data);
      setSaveState('Sample workspace · changes saved on this device');
    } catch {
      setSaveState('Sample workspace · changes kept for this visit');
    }
  });
  const navigate = (value: string) => {
    if (value === 'people') {
      setDialog('people');
      setSidebarOpen(false);
      return;
    }
    setRoute(value);
    setSidebarOpen(false);
    setChecked(new Set<string | number>());
  };
  const resetFilters = () =>
    batch(() => {
      setQuery('');
      setCategory(ALL_CATEGORIES);
      setOwner('all');
      setPriority('all');
      setDue('all');
      setChecked(new Set<string | number>());
    });
  const openProject = (id: number) => {
    setSelected(id);
    setPreviewTab('tasks');
    navigate('projects');
  };
  const openForm = (id: number | null = null) => {
    const p = state.projects.find((p) => p.id === id);
    setEditing(id);
    setForm(
      p
        ? {
            name: p.name,
            client: p.client,
            owner: p.owner,
            status: p.status,
            priority: p.priority,
            due: p.due,
            color: p.color,
            description: p.description,
          }
        : {
            name: '',
            client: '',
            owner: 'AK',
            status: 'planned',
            priority: 'normal',
            due: '2026-11-12',
            color: 'violet',
            description: '',
          },
    );
    setFormError('');
    setDialog('project');
  };
  const saveProject = (event: SubmitEvent) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setFormError('Enter a project name.');
      nameInput?.focus();
      return;
    }
    const id = editing() ?? Math.max(0, ...state.projects.map((p) => p.id)) + 1;
    const existing = state.projects.find((p) => p.id === id);
    const data = {
      ...unwrap(form),
      name: form.name.trim(),
      client: form.client.trim(),
      owner: form.owner as Project['owner'],
      status: form.status as Project['status'],
      priority: form.priority as Project['priority'],
      color: form.color as Project['color'],
    };
    batch(() => {
      if (existing) {
        setState('projects', (p) => p.id === id, data);
      } else {
        setState('projects', (p) => [...p, { ...data, id, icon: 'grid' as const, tasks: [] }]);
      }
      if (data.status === 'complete')
        setState(
          'projects',
          (p) => p.id === id,
          'tasks',
          () => true,
          'done',
          true,
        );
      log(`${data.name} was ${existing ? 'updated' : 'created'}`, id);
      setDialog(null);
      resetFilters();
      openProject(id);
    });
    toast(existing ? 'Project updated.' : 'Project created.');
  };
  const toggleTask = (project: Project, taskId: string, done: boolean) =>
    batch(() => {
      setState(
        'projects',
        (p) => p.id === project.id,
        'tasks',
        (t) => t.id === taskId,
        'done',
        done,
      );
      if (!done && project.status === 'complete')
        setState('projects', (p) => p.id === project.id, 'status', 'active');
      log(
        `${done ? 'Completed' : 'Reopened'} ${project.tasks.find((t) => t.id === taskId)?.title}`,
        project.id,
      );
    });
  const addTask = (project: Project, title: string) => {
    if (!title.trim()) return;
    setState(
      'projects',
      (p) => p.id === project.id,
      'tasks',
      (t) => [...t, { id: crypto.randomUUID(), title: title.trim(), done: false }],
    );
    if (project.status === 'complete')
      setState('projects', (p) => p.id === project.id, 'status', 'active');
    log(`Added ${title.trim()}`, project.id);
  };
  const complete = (project: Project) => {
    const before = structuredClone(unwrap(project)),
      done = project.status !== 'complete';
    batch(() => {
      setState('projects', (p) => p.id === project.id, 'status', done ? 'complete' : 'active');
      if (done)
        setState(
          'projects',
          (p) => p.id === project.id,
          'tasks',
          () => true,
          'done',
          true,
        );
      log(`${project.name} was ${done ? 'completed' : 'reopened'}`, project.id);
    });
    toast(done ? 'Project completed.' : 'Project reopened.', () => {
      setState('projects', (p) => p.id === project.id, reconcile(before));
      toast('Change undone.');
    });
  };
  const archive = () => {
    const ids = new Set(checked());
    setState('projects', (p) => ids.has(p.id), 'archived', true);
    setChecked(new Set<string | number>());
    toast(`${ids.size} project${ids.size === 1 ? '' : 's'} archived.`, () => {
      setState('projects', (p) => ids.has(p.id), 'archived', false);
      toast('Projects restored.');
    });
  };
  const exportCSV = () => {
    const cell = (value: string) =>
      '"' + (/^[=+@\-\t\r]/.test(value) ? "'" : '') + value.replaceAll('"', '""') + '"';
    const rows = [
      ['Project', 'Client', 'Status', 'Owner', 'Due date', 'Progress'],
      ...filtered().map((p) => [
        p.name,
        p.client,
        statusLabels[p.status],
        people[p.owner].name,
        p.due,
        progress(p) + '%',
      ]),
    ];
    const blob = new Blob(['\uFEFF' + rows.map((row) => row.map(cell).join(',')).join('\r\n')], {
      type: 'text/csv;charset=utf-8',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'evlet-projects.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('Project list exported.');
  };
  const quickView = (kind: string) => {
    resetFilters();
    if (kind === 'mine') setOwner('AK');
    if (kind === 'soon') setDue('soon');
    if (kind === 'complete') setCategory('complete');
    navigate('projects');
  };
  onMount(() => {
    const keys = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const writing = target.matches('input,textarea,select,[contenteditable="true"]');
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setCommand('');
        setDialog('command');
        return;
      }
      if (e.key === 'Escape' && !dialog()) {
        setSidebarOpen(false);
        setSelected(null);
      }
      if (dialog() || writing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'n') {
        e.preventDefault();
        openForm();
      }
      if (e.key === '/') {
        e.preventDefault();
        navigate('projects');
        queueMicrotask(() => searchInput?.focus());
      }
    };
    document.addEventListener('keydown', keys);
    onCleanup(() => document.removeEventListener('keydown', keys));
  });
  onCleanup(() => clearTimeout(toastTimer));
  const columns: Column<Project>[] = [
    {
      key: 'project',
      header: 'Project',
      cell: (p) => (
        <button
          type="button"
          class="ev-project-cell"
          aria-label={'Open ' + p.name}
          onClick={() => openProject(p.id)}
        >
          <Mark project={p} />
          <span>
            <span class="ev-project-name">{p.name}</span>
            <span class="ev-project-client">{p.client}</span>
          </span>
        </button>
      ),
    },
    { key: 'status', header: 'Status', cell: (p) => <Status status={p.status} /> },
    { key: 'progress', header: 'Progress', cell: (p) => <Progress project={p} /> },
    {
      key: 'due',
      header: 'Due date',
      class: 'ev-due-column',
      cell: (p) => (
        <span class="ev-due" classList={{ 'ev-soon': p.due === '2026-10-30' }}>
          {p.due === '2026-10-30' ? 'Tomorrow' : dateText(p.due)}
        </span>
      ),
    },
    {
      key: 'owner',
      header: 'Owner',
      class: 'ev-owner-column',
      cell: (p) => <PersonAvatar id={p.owner} />,
    },
  ];
  const Activities = (props: { projectId?: number }) => (
    <Show
      when={state.activity.some(
        (a) => props.projectId === undefined || a.projectId === props.projectId,
      )}
      fallback={
        <div class="ev-empty">
          <p>Your project updates will appear here.</p>
        </div>
      }
    >
      <For
        each={state.activity.filter(
          (a) => props.projectId === undefined || a.projectId === props.projectId,
        )}
      >
        {(a) => (
          <div class="ev-activity-row">
            <Show when={props.projectId === undefined} fallback={<span class="ev-activity-dot" />}>
              <PersonAvatar id={a.who} />
            </Show>
            <div class="ev-activity-copy">
              {a.text}
              <small>{a.at}</small>
            </div>
          </div>
        )}
      </For>
    </Show>
  );
  function ProjectTasks(props: { project: Project }) {
    const [newTask, setNewTask] = createSignal('');
    return (
      <>
        <div class="ev-task-summary">
          <span>Project progress</span>
          <small>
            {props.project.tasks.filter((t) => t.done).length} of {props.project.tasks.length}{' '}
            complete
          </small>
        </div>
        <div class="ev-detail-progress" style={{ '--ev-progress': progress(props.project) + '%' }}>
          <span />
        </div>
        <For each={props.project.tasks}>
          {(t, i) => (
            <label class="ev-task-row" classList={{ 'ev-is-done': t.done }}>
              <input
                class="ev-check"
                type="checkbox"
                checked={t.done}
                onChange={(e) => toggleTask(props.project, t.id, e.currentTarget.checked)}
              />
              <span class="ev-task-label">
                <span class="ev-task-text">{t.title}</span>
                <Show
                  when={
                    !t.done &&
                    i() === props.project.tasks.filter((t) => t.done).length &&
                    props.project.priority === 'high'
                  }
                >
                  <span class="ev-task-tag">Up next</span>
                </Show>
              </span>
            </label>
          )}
        </For>
        <form
          class="ev-add-task"
          onSubmit={(e) => {
            e.preventDefault();
            addTask(props.project, newTask());
            setNewTask('');
          }}
        >
          <IconButton type="submit" aria-label="Add task">
            <Icon name="plus" />
          </IconButton>
          <input
            class="demo-preview-task-input"
            aria-label="New task title"
            placeholder="Add a small next step…"
            required
            maxLength={100}
            value={newTask()}
            onInput={(e) => setNewTask(e.currentTarget.value)}
          />
        </form>
      </>
    );
  }
  const Preview = () => (
    <Show when={current()}>
      {(p) => (
        <RecordPreview
          title={p().name}
          recordId={'PRJ-' + String(p().id).padStart(3, '0')}
          icon={<Mark project={p()} />}
          badge={<Status status={p().status} />}
          description={p().description}
          onClose={() => setSelected(null)}
          onEdit={() => openForm(p().id)}
          metadata={[
            {
              id: 'owner',
              label: 'Owner',
              value: (
                <>
                  <PersonAvatar id={p().owner} />
                  {people[p().owner].name}
                </>
              ),
            },
            {
              id: 'due',
              label: 'Due date',
              value: (
                <>
                  <Icon name="calendar" />
                  {dateText(p().due)}, {p().due.slice(0, 4)}
                </>
              ),
            },
            { id: 'client', label: 'Client / team', value: p().client || 'Studio workspace' },
            {
              id: 'priority',
              label: 'Priority',
              value: (
                <span class="ev-priority-label" classList={{ 'ev-high': p().priority === 'high' }}>
                  <Icon name="flag" />
                  {p().priority === 'high' ? 'High priority' : 'Normal'}
                </span>
              ),
            },
          ]}
          tab={previewTab()}
          onTabChange={setPreviewTab}
          tabs={[
            {
              value: 'tasks',
              label: 'Tasks',
              get count() {
                return p().tasks.length;
              },
              content: () => <ProjectTasks project={p()} />,
            },
            {
              value: 'activity',
              label: 'Activity',
              content: () => <Activities projectId={p().id} />,
            },
          ]}
          footer={
            <>
              <span>
                <Icon name="clock" class="ev-sm" /> {p().tasks.filter((t) => !t.done).length} tasks
                remaining
              </span>
              <Button
                size="small"
                icon={p().status === 'complete' ? undefined : <Icon name="check" />}
                onClick={() => complete(p())}
              >
                {p().status === 'complete' ? 'Reopen' : 'Complete'}
              </Button>
            </>
          }
        />
      )}
    </Show>
  );
  const empty = () => (
    <div class="ev-empty">
      <Icon name="search" />
      <h3>No matching projects</h3>
      <p>Try another search or clear your filters.</p>
      <Button onClick={resetFilters}>Show all projects</Button>
    </div>
  );
  const newAction = () => [
    { id: 'new', label: 'New project', icon: <Icon name="plus" />, onSelect: () => openForm() },
  ];
  const headerTrailing = () => (
    <button
      type="button"
      class="ev-avatar-stack"
      aria-label="View your four team members"
      onClick={() => setDialog('people')}
    >
      <PersonAvatar id="SK" />
      <PersonAvatar id="AJ" />
      <PersonAvatar id="DN" />
      <Avatar initials="+1" />
    </button>
  );
  return (
    <>
      <Shell
        sidebarOpen={sidebarOpen()}
        onSidebarClose={() => setSidebarOpen(false)}
        sidebar={
          <Sidebar
            brand={
              <BrandArea
                logo={
                  <span class="ev-brand-mark">
                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <path
                        d="M17 6H9a3 3 0 0 0-3 3v6a3 3 0 0 0 3 3h8M6 12h9"
                        stroke="white"
                        stroke-width="3"
                        stroke-linecap="round"
                      />
                    </svg>
                  </span>
                }
                name="evlet"
              />
            }
            workspaceSwitcher={
              <WorkspaceSwitcher
                name="Studio"
                description="Team workspace"
                icon={<span class="ev-workspace-letter">S</span>}
                indicator={<Glyph name="down" />}
                onClick={() => setDialog('people')}
              />
            }
            navigation={
              <NavigationGroup
                title="WORKSPACE"
                value={route()}
                onChange={navigate}
                items={[
                  {
                    value: 'projects',
                    label: 'Projects',
                    icon: <Icon name="grid" />,
                    count: live().length,
                  },
                  {
                    value: 'tasks',
                    label: 'My tasks',
                    icon: <Icon name="tasks" />,
                    count: myTasks().length,
                  },
                  { value: 'activity', label: 'Activity', icon: <Icon name="activity" /> },
                  { value: 'people', label: 'People', icon: <Icon name="people" /> },
                ]}
              />
            }
            savedViews={
              <SavedViews
                title="QUICK VIEWS"
                items={[
                  {
                    id: 'mine',
                    label: 'Assigned to me',
                    icon: <span class="ev-square-dot" style={{ '--ev-dot': '#9c91df' }} />,
                    onSelect: () => quickView('mine'),
                  },
                  {
                    id: 'soon',
                    label: 'Due this week',
                    icon: <span class="ev-square-dot" style={{ '--ev-dot': '#d5a164' }} />,
                    onSelect: () => quickView('soon'),
                  },
                  {
                    id: 'complete',
                    label: 'Recently completed',
                    icon: <span class="ev-square-dot" style={{ '--ev-dot': '#70af98' }} />,
                    onSelect: () => quickView('complete'),
                  },
                ]}
              />
            }
            footer={
              <SidebarFooter>
                <div class="ev-mini-note">
                  <strong>
                    <span class="ev-green-dot" />A little more flow.
                  </strong>
                  Your work, all in one place.
                </div>
                <button type="button" class="ev-user-card" onClick={() => setDialog('people')}>
                  <PersonAvatar id="AK" />
                  <span>
                    <strong>Aslam K.</strong>
                    <small>Workspace admin</small>
                  </span>
                  <Icon name="more" />
                </button>
              </SidebarFooter>
            }
          />
        }
        topBar={
          <TopBar
            leading={
              <>
                <IconButton
                  class="demo-menu-toggle"
                  aria-label="Open navigation"
                  onClick={() => setSidebarOpen(true)}
                >
                  <Glyph name="menu" />
                </IconButton>
                <Breadcrumbs
                  separator={<Icon name="chevron" />}
                  leading={<Icon name="grid" />}
                  items={[
                    { label: 'Workspace', onSelect: () => navigate('projects') },
                    { label: <strong>{title()}</strong> },
                  ]}
                />
              </>
            }
            trailing={
              <>
                <button
                  type="button"
                  class="ev-global-search"
                  onClick={() => {
                    setCommand('');
                    setDialog('command');
                  }}
                >
                  <Icon name="search" />
                  <span>Search anything</span>
                  <kbd class="ev-shortcut">⌘ K</kbd>
                </button>
                <span class="ev-top-separator" />
                <IconButton aria-label="Help and shortcuts" onClick={() => setDialog('help')}>
                  <Icon name="help" />
                </IconButton>
                <IconButton
                  class="ev-notification"
                  aria-label="Workspace activity"
                  onClick={() => navigate('activity')}
                >
                  <Icon name="bell" />
                </IconButton>
                <PersonAvatar id="AK" />
              </>
            }
          />
        }
        statusBar={
          <StatusBar
            leading={
              <>
                <Icon name="check-circle" />
                {saveState()}
              </>
            }
            trailing={
              <button class="ev-keyboard-tip" type="button" onClick={() => setDialog('help')}>
                A few shortcuts go a long way <span>?</span>
              </button>
            }
          />
        }
        overlays={
          <>
            <Modal
              open={dialog() === 'project'}
              onClose={() => setDialog(null)}
              title={editing() ? 'Edit project' : 'A new project'}
              description="Start with the essentials. The rest can take shape as you go."
              initialFocus={() => nameInput}
              footer={
                <>
                  <Button variant="ghost" onClick={() => setDialog(null)}>
                    Cancel
                  </Button>
                  <Button variant="primary" type="submit" form="project-form">
                    {editing() ? 'Save changes' : 'Create project'}
                  </Button>
                </>
              }
            >
              <form id="project-form" class="ev-form-grid" onSubmit={saveProject}>
                <Field class="ev-full" label="Project name" required error={formError()}>
                  {(c) => (
                    <input
                      ref={nameInput}
                      id={c.id}
                      aria-describedby={c.describedBy}
                      aria-invalid={c.invalid}
                      value={form.name}
                      onInput={(e) => {
                        setForm('name', e.currentTarget.value);
                        setFormError('');
                      }}
                      maxLength={80}
                      required
                      placeholder="What are we working on?"
                    />
                  )}
                </Field>
                <Field label="Client / team">
                  {(c) => (
                    <input
                      id={c.id}
                      value={form.client}
                      onInput={(e) => setForm('client', e.currentTarget.value)}
                      maxLength={60}
                      placeholder="Team or company"
                    />
                  )}
                </Field>
                <Field label="Owner">
                  {(c) => (
                    <select
                      id={c.id}
                      value={form.owner}
                      onChange={(e) => setForm('owner', e.currentTarget.value)}
                    >
                      <For each={Object.entries(people)}>
                        {([id, person]) => <option value={id}>{person.name}</option>}
                      </For>
                    </select>
                  )}
                </Field>
                <Field label="Status">
                  {(c) => (
                    <select
                      id={c.id}
                      value={form.status}
                      onChange={(e) => setForm('status', e.currentTarget.value)}
                    >
                      <For each={Object.entries(statusLabels)}>
                        {([id, label]) => <option value={id}>{label}</option>}
                      </For>
                    </select>
                  )}
                </Field>
                <Field label="Due date" required>
                  {(c) => (
                    <input
                      id={c.id}
                      type="date"
                      value={form.due}
                      onInput={(e) => setForm('due', e.currentTarget.value)}
                      required
                    />
                  )}
                </Field>
                <Field label="Priority">
                  {(c) => (
                    <select
                      id={c.id}
                      value={form.priority}
                      onChange={(e) => setForm('priority', e.currentTarget.value)}
                    >
                      <option value="normal">Normal</option>
                      <option value="high">High priority</option>
                    </select>
                  )}
                </Field>
                <fieldset class="ev-field demo-color-field">
                  <legend>Project color</legend>
                  <div class="ev-color-options">
                    <For each={Object.entries(colors)}>
                      {([id, color]) => (
                        <label
                          class="ev-color-radio"
                          style={{ '--ev-swatch': color.fg }}
                          title={color.label}
                        >
                          <input
                            type="radio"
                            name="project-color"
                            aria-label={color.label}
                            checked={form.color === id}
                            onChange={() => setForm('color', id)}
                          />
                          <span />
                        </label>
                      )}
                    </For>
                  </div>
                </fieldset>
                <Field class="ev-full" label="A little context">
                  {(c) => (
                    <textarea
                      id={c.id}
                      value={form.description}
                      onInput={(e) => setForm('description', e.currentTarget.value)}
                      maxLength={500}
                      rows={3}
                      placeholder="What does a good outcome look like?"
                    />
                  )}
                </Field>
              </form>
            </Modal>
            <Modal
              open={dialog() === 'filters'}
              onClose={() => setDialog(null)}
              title="Filter projects"
              description="Find the work you want to focus on."
              footer={
                <>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      resetFilters();
                      setDialog(null);
                    }}
                  >
                    Clear filters
                  </Button>
                  <Button variant="primary" onClick={() => setDialog(null)}>
                    Done
                  </Button>
                </>
              }
            >
              <div class="ev-form-grid">
                <Field label="Owner">
                  {(c) => (
                    <select
                      id={c.id}
                      value={owner()}
                      onChange={(e) => setOwner(e.currentTarget.value)}
                    >
                      <option value="all">Anyone</option>
                      <For each={Object.entries(people)}>
                        {([id, person]) => <option value={id}>{person.name}</option>}
                      </For>
                    </select>
                  )}
                </Field>
                <Field label="Priority">
                  {(c) => (
                    <select
                      id={c.id}
                      value={priority()}
                      onChange={(e) => setPriority(e.currentTarget.value)}
                    >
                      <option value="all">Any priority</option>
                      <option value="high">High priority</option>
                      <option value="normal">Normal</option>
                    </select>
                  )}
                </Field>
                <Field label="Due date" class="ev-full">
                  {(c) => (
                    <select id={c.id} value={due()} onChange={(e) => setDue(e.currentTarget.value)}>
                      <option value="all">Any time</option>
                      <option value="soon">Due this week</option>
                    </select>
                  )}
                </Field>
              </div>
            </Modal>
            <Modal
              open={dialog() === 'command'}
              onClose={() => setDialog(null)}
              title="Find your next step"
              initialFocus={() => commandInput}
            >
              <div class="demo-command-search">
                <Icon name="search" />
                <input
                  ref={commandInput}
                  aria-label="Search workspace"
                  value={command()}
                  onInput={(e) => setCommand(e.currentTarget.value)}
                  placeholder="Search projects…"
                />
              </div>
              <button type="button" class="demo-command-result" onClick={() => openForm()}>
                <Icon name="plus" />
                <span>
                  New project<small>Start something new</small>
                </span>
              </button>
              <For
                each={live().filter((p) =>
                  `${p.name} ${p.client}`.toLowerCase().includes(command().toLowerCase()),
                )}
              >
                {(p) => (
                  <button
                    type="button"
                    class="demo-command-result"
                    onClick={() => {
                      setDialog(null);
                      resetFilters();
                      openProject(p.id);
                    }}
                  >
                    <Mark project={p} />
                    <span>
                      {p.name}
                      <small>{p.client}</small>
                    </span>
                  </button>
                )}
              </For>
            </Modal>
            <Modal
              open={dialog() === 'people'}
              onClose={() => setDialog(null)}
              title="Studio workspace"
              description="The people behind the work."
            >
              <For each={Object.entries(people)}>
                {([id, person]) => (
                  <div class="demo-person">
                    <PersonAvatar id={id as keyof typeof people} />
                    <span>
                      <strong>{person.name}</strong>
                      <small>{person.role}</small>
                    </span>
                  </div>
                )}
              </For>
            </Modal>
            <Modal
              open={dialog() === 'help'}
              onClose={() => setDialog(null)}
              title="A few useful shortcuts"
            >
              <div class="demo-help">
                <p>
                  <kbd>⌘ K</kbd> or <kbd>Ctrl K</kbd> Search the workspace
                  <br />
                  <kbd>N</kbd> Create a project
                  <br />
                  <kbd>/</kbd> Search this list
                  <br />
                  <kbd>Esc</kbd> Close a dialog or preview
                </p>
                <p>
                  The summary cards stay visible while you scroll the list. The project preview
                  scrolls in its own area.
                </p>
                <p>
                  This is a local sample workspace. Changes stay in this browser. No account or
                  server is connected.
                </p>
              </div>
            </Modal>
            <Toast
              message={message()}
              onDismiss={() => setMessage('')}
              action={undo() ? { label: 'Undo', onSelect: () => undo()?.() } : undefined}
            />
          </>
        }
      >
        <Show
          when={route() === 'projects'}
          fallback={
            <>
              <PageHeader title={title()} subtitle={subtitle()} actions={newAction()} />
              <div class="ev-secondary-view">
                <section class="ev-section-card">
                  <h2>
                    {route() === 'tasks' ? 'Your next small steps' : 'The latest around here'}
                  </h2>
                  <Show when={route() === 'tasks'} fallback={<Activities />}>
                    <For
                      each={myTasks()}
                      fallback={
                        <div class="ev-empty">
                          <Icon name="check-circle" />
                          <h3>A clear list. A clear head.</h3>
                          <p>You’re all caught up.</p>
                        </div>
                      }
                    >
                      {({ project, task }) => (
                        <div class="ev-my-task">
                          <label>
                            <input
                              class="ev-check"
                              type="checkbox"
                              checked={task.done}
                              onChange={(e) =>
                                toggleTask(project, task.id, e.currentTarget.checked)
                              }
                            />
                            <span>
                              {task.title}
                              <small>
                                {project.name} · {dateText(project.due)}
                              </small>
                            </span>
                          </label>
                          <button type="button" onClick={() => openProject(project.id)}>
                            Open project <Icon name="arrow" class="ev-sm" />
                          </button>
                        </div>
                      )}
                    </For>
                  </Show>
                </section>
              </div>
            </>
          }
        >
          <ExplorerView
            title="Projects"
            subtitle={subtitle()}
            actions={newAction()}
            headerTrailing={headerTrailing()}
            summaryItems={summaries()}
            summaryRow={
              <SummaryRow
                items={summaries()}
                renderItem={(item) => (
                  <SummaryCard item={item}>
                    <Show when={item.id === 'total'}>
                      <div class="ev-metric-spark" aria-hidden="true">
                        <For each={[6, 9, 7, 13, 10, 15, 18, 16]}>
                          {(height) => <span style={{ height: height + 'px' }} />}
                        </For>
                      </div>
                    </Show>
                  </SummaryCard>
                )}
              />
            }
            collapsible={false}
            listLabel="Project list"
            previewLabel="Project preview"
            previewKey={String(selected()) + previewTab()}
            preview={current() ? <Preview /> : undefined}
            onPreviewClose={() => setSelected(null)}
            viewBar={
              <ViewBar
                categories={
                  <CategoryTabs
                    items={categories()}
                    value={category()}
                    label="Project stages"
                    onChange={(value) => {
                      setCategory(value);
                      setChecked(new Set<string | number>());
                    }}
                  />
                }
                viewSwitcher={
                  <ViewSwitcher
                    items={[
                      { value: 'list', label: 'List', icon: <Icon name="list" /> },
                      { value: 'board', label: 'Board', icon: <Icon name="board" /> },
                    ]}
                    value={mode()}
                    onChange={setMode}
                  />
                }
              />
            }
            toolbar={
              <ListToolbar
                leading={
                  <>
                    <label class="ev-search-field">
                      <Icon name="search" />
                      <input
                        ref={searchInput}
                        aria-label="Search projects"
                        placeholder="Find a project…"
                        value={query()}
                        onInput={(e) => setQuery(e.currentTarget.value)}
                      />
                      <Show when={query()}>
                        <button
                          type="button"
                          aria-label="Clear search"
                          onClick={() => setQuery('')}
                        >
                          <Glyph name="close" />
                        </button>
                      </Show>
                    </label>
                    <Button icon={<Icon name="filter" />} onClick={() => setDialog('filters')}>
                      Filter
                      <Show when={owner() !== 'all' || priority() !== 'all' || due() !== 'all'}>
                        <span class="ev-filter-dot" />
                      </Show>
                    </Button>
                  </>
                }
                trailing={
                  <>
                    <label class="ev-select-wrap demo-owner-select">
                      <Icon name="people" />
                      <select
                        aria-label="Filter by owner"
                        value={owner()}
                        onChange={(e) => setOwner(e.currentTarget.value)}
                      >
                        <option value="all">Anyone</option>
                        <For each={Object.entries(people)}>
                          {([id, person]) => <option value={id}>{person.name}</option>}
                        </For>
                      </select>
                    </label>
                    <label class="ev-select-wrap">
                      <Icon name="sort" />
                      <select
                        aria-label="Sort projects"
                        value={sort()}
                        onChange={(e) => setSort(e.currentTarget.value)}
                      >
                        <option value="manual">Last updated</option>
                        <option value="due">Due date</option>
                        <option value="name">Name</option>
                        <option value="progress">Progress</option>
                      </select>
                    </label>
                    <IconButton aria-label="Export projects" title="Export CSV" onClick={exportCSV}>
                      <Icon name="download" />
                    </IconButton>
                  </>
                }
              />
            }
          >
            <Show
              when={mode() === 'list'}
              fallback={
                <Show when={filtered().length} fallback={empty()}>
                  <div class="ev-board">
                    <For each={['planned', 'active', 'complete'] as const}>
                      {(stage) => (
                        <section class="ev-board-column">
                          <div class="ev-board-heading">
                            <Status status={stage} />
                            <span class="ev-count">
                              {filtered().filter((p) => p.status === stage).length}
                            </span>
                          </div>
                          <For
                            each={filtered().filter((p) => p.status === stage)}
                            fallback={
                              <div class="ev-empty">
                                <p>Room for what’s next.</p>
                              </div>
                            }
                          >
                            {(p) => (
                              <button
                                type="button"
                                class="ev-board-card"
                                classList={{ 'ev-selected': p.id === selected() }}
                                onClick={() => openProject(p.id)}
                              >
                                <div class="ev-board-card-top">
                                  <Mark project={p} />
                                  <PersonAvatar id={p.owner} />
                                </div>
                                <div class="ev-project-name">{p.name}</div>
                                <div class="ev-project-client">{p.client}</div>
                                <Progress project={p} />
                                <div class="ev-board-card-bottom">
                                  <span class="ev-due">{dateText(p.due)}</span>
                                  <span class="ev-project-client">
                                    {p.tasks.filter((t) => t.done).length}/{p.tasks.length} tasks
                                  </span>
                                </div>
                              </button>
                            )}
                          </For>
                        </section>
                      )}
                    </For>
                  </div>
                </Show>
              }
            >
              <RecordList
                records={filtered()}
                columns={columns}
                getKey={(p) => p.id}
                getLabel={(p) => p.name}
                selectedKey={selected()}
                selection={checked()}
                onSelectionChange={setChecked}
                selectionActions={
                  <Button variant="ghost" size="small" onClick={archive}>
                    Archive selected
                  </Button>
                }
                rowActions={(p) => (
                  <IconButton
                    class="ev-row-menu"
                    aria-label={'Edit ' + p.name}
                    onClick={() => openForm(p.id)}
                  >
                    <Icon name="more" />
                  </IconButton>
                )}
                empty={empty()}
                footer={
                  <ListFooter
                    leading={
                      <>
                        {filtered().length} project{filtered().length === 1 ? '' : 's'}
                        {filtered().length !== live().length ? ' of ' + live().length : ''}
                      </>
                    }
                    trailing={
                      <>
                        <Icon name="check-circle" />
                        Your shared project workspace
                      </>
                    }
                  />
                }
              />
            </Show>
          </ExplorerView>
        </Show>
      </Shell>
    </>
  );
}
