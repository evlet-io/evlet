import { createSignal, onMount, type JSX } from 'solid-js';
import { fireEvent, render, screen, waitFor } from '@solidjs/testing-library';
import { describe, expect, it, vi } from 'vitest';
import {
  ActionMenu,
  ALL_CATEGORIES,
  AppShell,
  Badge,
  buildCategories,
  Button,
  CategoryTabs,
  ExplorerView,
  Field,
  Modal,
  NavigationGroup,
  PageHeader,
  PrimaryActions,
  RecordList,
  RecordPreview,
  Sidebar,
  SummaryCard,
  SummaryRow,
  TopBar,
  type Action,
  type Column,
} from '../ui';

describe('primary actions', () => {
  it('changes between no action, one direct button, and a dropdown as props change', async () => {
    const select = vi.fn();
    const [actions, setActions] = createSignal<Action[]>([]);
    render(() => <PrimaryActions actions={actions()} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    setActions([{ id: 'new', label: 'New record', onSelect: select }]);
    fireEvent.click(screen.getByRole('button', { name: 'New record' }));
    expect(select).toHaveBeenCalledOnce();
    setActions((a) => [...a, { id: 'import', label: 'Import', onSelect: vi.fn() }]);
    expect(screen.queryByRole('button', { name: 'New record' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    expect(screen.getByRole('menuitem', { name: 'Import' })).toBeVisible();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Actions' })).toHaveFocus();
    setActions([{ id: 'hidden', label: 'Hidden', hidden: true, onSelect: select }]);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
  it('supports keyboard navigation while skipping disabled items', async () => {
    const select = vi.fn();
    render(() => (
      <ActionMenu
        actions={[
          { id: 'a', label: 'First', onSelect: select },
          { id: 'b', label: 'Disabled', disabled: true, onSelect: select },
          { id: 'c', label: 'Last', onSelect: select },
        ]}
      />
    ));
    fireEvent.keyDown(screen.getByRole('button', { name: 'Actions' }), { key: 'ArrowDown' });
    await waitFor(() => expect(screen.getByRole('menuitem', { name: 'First' })).toHaveFocus());
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' });
    expect(screen.getByRole('menuitem', { name: 'Last' })).toHaveFocus();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Last' }));
    expect(select).toHaveBeenCalledOnce();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
describe('customization contracts', () => {
  it('keeps class, classList, style, attributes and JSX slots reactive', () => {
    const [name, setName] = createSignal('Projects'),
      [active, setActive] = createSignal(false);
    render(() => (
      <PageHeader
        title={<em>{name()}</em>}
        class={active() ? 'custom-active' : 'custom'}
        classList={{ dense: active() }}
        style={{ color: active() ? 'blue' : 'red' }}
        data-section={name()}
        trailing={<Button>Invite</Button>}
      />
    ));
    const header = screen.getByRole('heading').closest('header')!;
    expect(header).toHaveClass('ev-page-heading', 'custom');
    expect(header.style.color).toBe('red');
    setActive(true);
    setName('Contacts');
    expect(header).toHaveClass('ev-page-heading', 'custom-active', 'dense');
    expect(header).not.toHaveClass('custom');
    expect(header.style.color).toBe('blue');
    expect(header).toHaveAttribute('data-section', 'Contacts');
    expect(screen.getByRole('heading')).toHaveTextContent('Contacts');
    expect(screen.getByRole('button', { name: 'Invite' })).toBeInTheDocument();
  });
  it('forwards native events and refs through composed areas', () => {
    const event = vi.fn(),
      ref = vi.fn(),
      listRef = vi.fn();
    render(() => (
      <AppShell ref={ref} onKeyDown={event} topBar={<TopBar leading="Brand" />}>
        <ExplorerView title="Contacts" listAreaProps={{ ref: listRef, 'data-testid': 'list' }}>
          <p>Content</p>
        </ExplorerView>
      </AppShell>
    ));
    expect(ref).toHaveBeenCalledOnce();
    expect(listRef).toHaveBeenCalledOnce();
    fireEvent.keyDown(screen.getByTestId('list'), { key: 'x' });
    expect(event).toHaveBeenCalledOnce();
  });
  it('allows summary classes, styles and whole elements', () => {
    render(() => (
      <SummaryRow
        items={[{ id: 'one', label: 'Open', value: 4 }]}
        class="summary-custom"
        style={{ gap: '20px' }}
        renderItem={(item) => (
          <SummaryCard
            item={{ ...item, style: { color: 'red' } }}
            style={{ padding: '30px' }}
            data-testid="card"
          >
            <span>Custom graphic</span>
          </SummaryCard>
        )}
      />
    ));
    expect(screen.getByTestId('card').style.padding).toBe('30px');
    expect(screen.getByTestId('card').style.color).toBe('red');
    expect(screen.getByTestId('card').parentElement?.style.gap).toBe('20px');
  });
  it('lets shell areas be replaced independently', () => {
    render(() => (
      <AppShell
        sidebar={
          <Sidebar
            brand={<strong>My brand</strong>}
            navigation={
              <NavigationGroup
                items={[{ value: 'home', label: 'Home' }]}
                value="home"
                onChange={() => {}}
              />
            }
          />
        }
        topBar={
          <TopBar
            leading={<span>Leading</span>}
            center={<input aria-label="Global search" />}
            trailing={<Button>Account</Button>}
          />
        }
        statusBar={<span>All saved</span>}
        overlays={<Badge>Notice</Badge>}
      >
        <section>Custom view</section>
      </AppShell>
    ));
    expect(screen.getByText('My brand')).toBeInTheDocument();
    expect(screen.getByLabelText('Global search')).toBeInTheDocument();
    expect(screen.getByText('Custom view')).toBeInTheDocument();
    expect(screen.getByText('Notice').closest('.ev-overlay-layer')).not.toBeNull();
  });
});
describe('record areas', () => {
  it('groups by an arbitrary select field and keeps zero-count options', () => {
    expect(
      buildCategories(
        [{ owner: 'sam' }, { owner: 'lee' }, { owner: 'sam' }],
        [
          { value: 'sam', label: 'Sam' },
          { value: 'lee', label: 'Lee' },
          { value: 'mo', label: 'Mo' },
        ],
        (r) => r.owner,
      ),
    ).toEqual([
      { value: ALL_CATEGORIES, label: 'All', count: 3 },
      { value: 'sam', label: 'Sam', count: 2 },
      { value: 'lee', label: 'Lee', count: 1 },
      { value: 'mo', label: 'Mo', count: 0 },
    ]);
  });
  it('uses keyboard selection for generic categories', () => {
    const [value, setValue] = createSignal('sam');
    render(() => (
      <CategoryTabs
        items={[
          { value: 'sam', label: 'Sam' },
          { value: 'lee', label: 'Lee' },
        ]}
        value={value()}
        onChange={setValue}
      />
    ));
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: 'Lee' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Lee' })).toHaveFocus();
  });
  it('renders typed columns and preserves selection outside the filtered records', () => {
    type Contact = { email: string; name: string };
    const records: Contact[] = [
      { email: 'a@test.local', name: 'Amal' },
      { email: 's@test.local', name: 'Sam' },
    ];
    const columns: Column<Contact>[] = [
      { key: 'name', header: 'Contact', cell: (r) => <strong>{r.name}</strong> },
    ];
    const [selection, setSelection] = createSignal<Set<string | number>>(new Set(['outside']));
    const [rows, setRows] = createSignal(records);
    render(() => (
      <RecordList
        records={rows()}
        columns={columns}
        getKey={(r) => r.email}
        getLabel={(r) => r.name}
        selection={selection()}
        onSelectionChange={setSelection}
        empty={<p>No contacts</p>}
      />
    ));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Amal' }));
    expect(selection()).toEqual(new Set(['outside', 'a@test.local']));
    expect(
      screen.getByRole('checkbox', { name: 'Select all visible records' }),
    ).toBePartiallyChecked();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all visible records' }));
    expect(selection().size).toBe(3);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all visible records' }));
    expect(selection()).toEqual(new Set(['outside']));
    setRows([]);
    expect(screen.getByText('No contacts')).toBeInTheDocument();
  });
  it('updates preview metadata and tabs without replacing its root', () => {
    const [title, setTitle] = createSignal('Contact'),
      [tab, setTab] = createSignal('details');
    const close = vi.fn();
    render(() => (
      <RecordPreview
        title={title()}
        recordId="C-42"
        badge={<Badge>Active</Badge>}
        metadata={[
          { id: 'email', label: 'Email', value: <a href="mailto:a@test.local">Email Amal</a> },
        ]}
        tab={tab()}
        onTabChange={setTab}
        tabs={[
          { value: 'details', label: 'Details', content: () => <p>Contact details</p> },
          { value: 'notes', label: 'Notes', content: () => <textarea aria-label="Notes" /> },
        ]}
        onClose={close}
      />
    ));
    const root = screen.getByRole('complementary');
    setTitle('Amal');
    expect(screen.getByRole('complementary')).toBe(root);
    expect(screen.getByRole('heading')).toHaveTextContent('Amal');
    fireEvent.click(screen.getByRole('tab', { name: 'Notes' }));
    expect(screen.getByRole('textbox', { name: 'Notes' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close record preview' }));
    expect(close).toHaveBeenCalledOnce();
  });
});
describe('scroll and field behavior', () => {
  it('keeps summary cards expanded during list and preview scrolling by default', () => {
    const now = vi.spyOn(performance, 'now').mockReturnValue(1000);
    render(() => (
      <ExplorerView
        title="Projects"
        summaryItems={[{ id: 'all', label: 'All', value: 8 }]}
        preview={<p>Preview content</p>}
      >
        <p>List content</p>
      </ExplorerView>
    ));
    const view = screen.getByRole('heading').closest('section')!,
      list = screen.getByRole('region', { name: 'Record list' }),
      preview = screen.getByRole('region', { name: 'Record preview' });
    fireEvent.wheel(preview, { deltaY: 60 });
    expect(view).not.toHaveClass('ev-workspace-collapsed');
    fireEvent.wheel(list, { deltaY: 60 });
    expect(view).not.toHaveClass('ev-workspace-collapsed');
    fireEvent.scroll(list);
    expect(view).not.toHaveClass('ev-workspace-collapsed');
    now.mockReturnValue(1400);
    fireEvent.wheel(list, { deltaY: -60 });
    expect(view).not.toHaveClass('ev-workspace-collapsed');
  });
  it('connects field labels and reactive errors to native controls', () => {
    const [error, setError] = createSignal('');
    render(() => (
      <Field label="Project" help="Use a short name" error={error()}>
        {(c) => <input id={c.id} aria-invalid={c.invalid} aria-describedby={c.describedBy} />}
      </Field>
    ));
    const input = screen.getByRole('textbox', { name: 'Project' });
    expect(input).toHaveAccessibleDescription('Use a short name');
    setError('Name is required');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription('Name is required');
  });
  it('opens and closes the native dialog from controlled props', () => {
    const [open, setOpen] = createSignal(true);
    render(() => (
      <Modal open={open()} onClose={() => setOpen(false)} title="Edit">
        <p>Form</p>
      </Modal>
    ));
    expect(screen.getByRole('dialog')).toHaveAttribute('open');
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('JSX slot ownership', () => {
  it('mounts custom sidebar, summary and preview slots once and preserves supplied elements', async () => {
    const mounts = { sidebar: 0, summary: 0, preview: 0 };
    const [folded, setFolded] = createSignal(false);
    const Brand = () => {
      onMount(() => mounts.sidebar++);
      return <strong>One brand</strong>;
    };
    const sharedIcon = (
      <svg data-testid="summary-icon">
        <circle cx="5" cy="5" r="2" />
      </svg>
    );
    const Summary = () => {
      onMount(() => mounts.summary++);
      return <SummaryRow items={[{ id: 'one', label: 'Open', value: 4, icon: sharedIcon }]} />;
    };
    const Preview = () => {
      onMount(() => mounts.preview++);
      return <RecordPreview title="Preview" />;
    };
    render(() => (
      <AppShell
        sidebar={
          <Sidebar
            brand={<Brand />}
            navigation={
              <ActionMenu actions={[{ id: 'one', label: 'First', onSelect: () => {} }]} />
            }
          />
        }
      >
        <ExplorerView
          title="Projects"
          summaryRow={<Summary />}
          preview={<Preview />}
          collapsible
          collapsed={folded()}
          onCollapsedChange={setFolded}
        >
          <p>Records</p>
        </ExplorerView>
      </AppShell>
    ));
    expect(mounts).toEqual({ sidebar: 1, summary: 1, preview: 1 });
    expect(screen.getByTestId('summary-icon').closest('.ev-metric-icon')).not.toBeNull();
    setFolded(true);
    setFolded(false);
    expect(mounts).toEqual({ sidebar: 1, summary: 1, preview: 1 });
    expect(screen.getByTestId('summary-icon').closest('.ev-metric-icon')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    await waitFor(() =>
      expect(screen.getByRole('menu').closest('.ev-overlay-layer')).not.toBeNull(),
    );
  });
});
