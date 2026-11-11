"""Renderer-neutral screen metadata, separate from persistent schema history."""

import re

from ..fields import CurrentUser, LongText, MISSING, Text
from ..declarations import Ref

from ..naming import form_hidden, standard


def label_for(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ").capitalize()


def plural_for(label):
    """A default plural label by simple English rules; Meta.plural overrides it."""
    lower = label.lower()
    if len(lower) > 1 and lower.endswith('y') and lower[-2] not in 'aeiou':
        return label[:-1] + 'ies'
    if lower.endswith(('s', 'x', 'z', 'ch', 'sh')):
        return label + 'es'
    return label + 's'


OPEN_IN = ('page', 'panel')
# The options a model's Meta may declare; each is public (docs/api-stability.md).
META_OPTIONS = frozenset(('label', 'plural', 'description', 'title_field', 'list_fields', 'search_fields', 'layout',
                          'calculation_language', 'numeric_profile', 'revisable', 'measures', 'key_fields', 'relations', 'attachments',
                          'effects', 'journal', 'naming', 'mail', 'title', 'open_in', 'api', 'period', 'period_scope', 'autosave',
                          'status', 'board', 'list_tabs', 'list_tab', 'summary', 'side_blocks', 'panel', 'panel_summary', 'ledger', 'append_only', 'timestamps', 'indexes',
                          'focus', 'cards', 'duplicate', 'derives', 'image'))

# How a collapsible section starts: open, closed, or closed when every field in it is empty.
COLLAPSED = (False, True, 'empty')
# How a section orders its fields, on screen and for the Tab key: across then down, or down the left column then the right.
FLOWS = ('rows', 'columns')
# The record page's own parts besides its sections, as Meta.panel and the schema's "panel" name them: Activity (the
# side column), Details (every section), and the Revisions, Related and Postings rows of "more information". A Table
# field is named by its own name. Record pages have no tabs (removed in 0.4).
TABS = ('activity', 'details', 'revisions', 'related', 'postings')
# The record page's side column blocks, for Meta.side_blocks: Key fields, Related, Files and Record info.
SIDE_BLOCKS = ('key_fields', 'related', 'files', 'info')
# What a list's side panel can leave out besides sections and fields: the summary strip and the side blocks.
PANEL_BLOCKS = ('summary', *SIDE_BLOCKS)
# The kinds of element a record's side panel can hide, as keys of the schema's "panel".
PANEL_KINDS = ('sections', 'fields', 'tabs', 'blocks')


def starts_valid(value):
    """Whether value is one of COLLAPSED exactly: 1 and 0 are not True and False here."""
    return value is True or value is False or (type(value) is str and value == 'empty')


class Section:
    """A form section in Model.Meta.layout that a person can open and close.

    `collapsed` is where it starts: False (open), True (closed) or 'empty' (closed when every field in
    it is empty on an existing record; a new record shows it open). A person's own choice wins after that.
    A plain ('Title', fields) pair is a section that can't collapse. `panel=False` leaves the section out when a
    list opens the record in its side panel; the full page still shows it. `description` is an optional line of
    text under the title."""

    __slots__ = ('title', 'fields', 'collapsible', 'collapsed', 'flow', 'panel', 'description')

    def __init__(self, title, fields, *, collapsible=True, collapsed=False, flow='rows', panel=True, description=None):
        self.title, self.fields, self.collapsible, self.collapsed, self.flow = title, fields, collapsible, collapsed, flow
        self.panel, self.description = panel, description

    def __repr__(self):
        return (f'Section({self.title!r}, {self.fields!r}, collapsible={self.collapsible!r}, collapsed={self.collapsed!r}'
                + (f', flow={self.flow!r}' if self.flow != 'rows' else '')
                + (f', description={self.description!r}' if self.description is not None else '')
                + (', panel=False)' if self.panel is False else ')'))

    def check(self, model):
        """Raise ValueError naming the model and the section when the options can't work."""
        where = f'{model} layout section {self.title!r}'
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError(f'{model} layout: give every Section a title')
        if not isinstance(self.fields, (tuple, list)):
            raise ValueError(f'{where}: list its fields as a tuple of field names')
        if type(self.collapsible) is not bool:
            raise ValueError(f'{where}: collapsible must be True or False')
        if not starts_valid(self.collapsed):
            raise ValueError(f"{where}: collapsed must be False, True or 'empty', not {self.collapsed!r}")
        if not self.collapsible and self.collapsed is not False:
            raise ValueError(f'{where}: collapsed needs collapsible=True, because this section always shows')
        if type(self.flow) is not str or self.flow not in FLOWS:
            raise ValueError(f"{where}: flow must be 'rows' or 'columns', not {self.flow!r}")
        if type(self.panel) is not bool:
            raise ValueError(f'{where}: panel must be True or False')
        if self.description is not None and (type(self.description) is not str or not self.description.strip()):
            raise ValueError(f'{where}: description must be nonempty text, not {self.description!r}')


class Main:
    """The primary area of a compact record page, first in Model.Meta.layout: a grid of fields with no heading,
    then any Table fields, named after the fields.

    Every Section, Table, Related, Html and Chart after it is the page's "more information". The area is named by
    the model's label where a name is needed (Meta.panel, Controls). A layout without Main uses its first section as
    the primary area."""

    __slots__ = ('fields', 'tables')

    def __init__(self, fields, *tables):
        if not isinstance(fields, (tuple, list)) or any(type(name) is not str or not name for name in fields):
            raise ValueError('Main: list its fields as a tuple of field names, such as Main(("customer", "due_on"), "lines")')
        if any(type(name) is not str or not name for name in tables):
            raise ValueError('Main: name Table fields after the fields, such as Main(("customer",), "lines")')
        if not fields and not tables:
            raise ValueError('Main needs fields, Table fields or both')
        if len(set(fields)) != len(fields) or len(set(tables)) != len(tables):
            raise ValueError('Main: name each field and Table once')
        self.fields, self.tables = tuple(fields), tuple(tables)

    def __repr__(self):
        return f'Main({self.fields!r}' + ''.join(f', {name!r}' for name in self.tables) + ')'


# At most this many blocks in a layout.
MAX_BLOCKS = 12
PLACEHOLDER = re.compile(r'\{\{|\}\}|\{([a-z_][a-z0-9_]*)\}|[{}]')
UNSAFE_HTML = re.compile(r'<\s*/?\s*(script|style|iframe|frame|object|embed|form|input|button|svg|math|link|meta|base)\b'
                         r'|\son[a-z]+\s*=|javascript\s*:', re.IGNORECASE)


class Html:
    """Static content in Model.Meta.layout, written by the app (trusted code, never by users).

    It keeps to the rich text allowlist (paragraphs, h2 and h3 headings, lists, quotes, code, strong, em, links and
    line breaks); scripts, styles, frames and event attributes are refused. `{field}` shows that field of the record,
    formatted and always escaped as text; `{{` and `}}` are literal braces."""

    __slots__ = ('content',)

    def __init__(self, content):
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Html needs its content as text')
        if UNSAFE_HTML.search(content):
            raise ValueError('Html keeps to the rich text allowlist: no scripts, styles, frames, forms or event attributes')
        self.content = content

    def __repr__(self):
        return f'Html({self.content!r})'

    def fields(self):
        """The field names its {field} placeholders show."""
        names = []
        for match in PLACEHOLDER.finditer(self.content):
            if match[0] in ('{', '}'):
                raise ValueError('Html: write {field} to show a field, and {{ or }} for a literal brace')
            if match[1]:
                names.append(match[1])
        return names

    def markup(self):
        """The sanitised HTML the schema sends; a placeholder lost in an attribute is refused."""
        from ..richtext import sanitise
        markup = sanitise(self.content)[0]
        if not markup:
            raise ValueError('Html has no content left after keeping to the rich text allowlist')
        kept = [match[1] for match in PLACEHOLDER.finditer(markup) if match[1]]
        if sorted(kept) != sorted(self.fields()):
            raise ValueError('Html: put {field} placeholders in the text, not in attributes')
        return markup


def layout_blocks(model, layout, visible):
    """The blocks of Meta.layout in order, as (None, kind, block), and an empty list (kept for callers' unpacking).

    Kinds: 'main' (the Main block), 'section' (a Section or a ('Title', fields) pair), 'table' (a Table field
    name), 'related', 'html' and 'chart'. Raises ValueError for a block in the wrong place, an unknown Table or a
    repeated one."""
    from ..forms import Chart
    from ..meaning import Related
    where = f'{model.__name__}.Meta.layout'
    if not isinstance(layout, (tuple, list)):
        raise ValueError(f'{where}: list its blocks as a tuple, such as (Main(...), Section(...), Related(...))')
    mains = [entry for entry in layout if isinstance(entry, Main)]
    if mains:
        if len(mains) > 1:
            raise ValueError(f'{where}: use one Main')
        if not isinstance(layout[0], Main):
            raise ValueError(f'{where}: put Main first')
    tables = getattr(model, '__tables__', {})
    entries, placed = [], set()
    for block in layout:
        if getattr(type(block), '__name__', '') == 'Tab':
            raise ValueError(f'{where}: Tab was removed, record pages have no tabs; use Main(...) first and Section(...) '
                             'after it')
        if isinstance(block, Main):
            for name in block.tables:
                if name not in tables:
                    raise ValueError(f'{where}: Main names {name!r}, which is not a Table field of {model.__name__} '
                                     f'({", ".join(tables) or "none"}). Put fields in its tuple of fields')
                placed.add(name)
            entries.append((None, 'main', block))
            continue
        if isinstance(block, Section) or isinstance(block, (tuple, list)):
            entries.append((None, 'section', block))
        elif isinstance(block, str):
            if block not in tables:
                raise ValueError(f'{where}: {block!r} is not a Table field of {model.__name__}. Put fields in a Section, '
                                 f'and name Table fields ({", ".join(tables) or "none"}) on their own')
            if block in placed:
                raise ValueError(f'{where}: Table {block!r} is placed more than once')
            placed.add(block)
            entries.append((None, 'table', block))
        elif isinstance(block, Related):
            if block.hidden:
                raise ValueError(f'{where}: a Related block shows records; hidden=True belongs in Meta.relations')
            entries.append((None, 'related', block))
        elif isinstance(block, Html):
            unknown = [name for name in block.fields() if name not in visible]
            if unknown:
                raise ValueError(f'{where}: Html shows unknown or hidden fields: {", ".join(unknown)}')
            block.markup()
            entries.append((None, 'html', block))
        elif isinstance(block, Chart):
            if block.model is None:
                raise ValueError(f'{where}: a layout Chart names the model whose records it charts, such as Chart("bar", x="status", y="hours", model="task")')
            entries.append((None, 'chart', block))
        else:
            raise ValueError(f'{where}: use Main(...), Section(...), a Table field name, Related(...), Html(...) or Chart(...), not {block!r}')
    if len(layout) > MAX_BLOCKS:
        raise ValueError(f'{where}: use at most {MAX_BLOCKS} blocks')
    if mains:
        # Every Table field is placed, in Main or after it.
        unplaced = [name for name in tables if name not in placed]
        if unplaced:
            raise ValueError(f'{where}: a layout with Main places every Table field, in Main or after it; '
                             f'{", ".join(unplaced)} is not placed')
    return entries, []


def check_focus(where, definition, name):
    """Check a focus option (Model Meta.focus, Slip.focus): the field a new form puts the cursor in. It must be a
    visible, writable, stored field of `definition`: not calculated, read only, hidden, a Table or a Sequence.
    Raises ValueError starting with `where`. Display only: permissions never read it."""
    from ..sequences import Sequence
    if type(name) is not str or not name:
        raise ValueError(f'{where}: name one field, such as "customer", not {name!r}')
    if name in getattr(definition, '__tables__', {}):
        raise ValueError(f'{where}: {name} is a table; name a field')
    field = getattr(definition, '__app_fields__', definition.__declared_fields__).get(name)
    if field is None or standard(field):
        raise ValueError(f'{where}: {definition.__name__} has no field {name!r}')
    problem = ('is numbered by a Sequence' if isinstance(field, Sequence) else
               'is hidden' if field.hidden else
               'is calculated' if field.compute is not None or field.query is not None or field.fetch is not None else
               'is read only' if field.read_only else
               'is not stored' if not field.stored else None)
    if problem:
        raise ValueError(f'{where}: {name} {problem}; name a field people fill in')
    return name


def refuse_removed(name, meta):
    """Raise ValueError for a Meta option removed in 0.4 (Meta.tabs, Meta.default_tab), saying what to use."""
    for removed in ('tabs', 'default_tab'):
        if isinstance(meta, type) and removed in vars(meta):
            raise ValueError(f'{name}.Meta.{removed} was removed: record pages have no tabs. Lay the page out with '
                             'Meta.layout = (Main(fields, *tables), Section(...), ...); Activity, Revisions and '
                             'Related show beside it or under "more information"')


def view_for(model):
    meta = getattr(model, 'Meta', None)
    refuse_removed(model.__name__, meta)
    allowed = META_OPTIONS
    if meta is not None:
        if not isinstance(meta, type) or any(not key.startswith('_') and key not in allowed for key in vars(meta)):
            raise ValueError('Model.Meta supports ' + ', '.join(sorted(allowed)))
    # App fields only: instance fields (x_) differ per database and are added by Controls. The standard
    # name shows (first) on models whose naming fills it; the standard title follows its source and never shows.
    named = getattr(getattr(meta, 'naming', None), 'field', None) == 'name' and standard(model.__app_fields__.get('name')) == 'name'
    visible = {name: field for name, field in model.__app_fields__.items()
               if (standard(field) == 'name' and named) or (not standard(field) and not field.hidden)}
    if named:
        visible = {'name': visible.pop('name'), **visible}
    texts = [name for name, field in visible.items() if isinstance(field, Text) and not isinstance(field, LongText)]
    label = getattr(meta, 'label', label_for(model.__name__))
    plural = getattr(meta, 'plural', plural_for(label))
    description = getattr(meta, 'description', '')
    if any(not isinstance(value, str) for value in (label, plural, description)) or not label or not plural:
        raise ValueError('View labels must be nonempty strings and description must be text')
    title = getattr(meta, 'title_field', next((name for name in ('name', 'title', 'reference') if name in texts), texts[0] if texts else 'id'))
    if title != 'id' and title not in texts:
        raise ValueError('title_field must be a visible short text field or id')
    if not hasattr(meta, 'title_field') and 'title' in model.__app_fields__ and (standard(model.__app_fields__['title']) or 'title' in texts):
        # Without Meta.title_field, records show their title column (a template, the name or the text
        # id). With it, that field holds the same text the title does.
        title = "title"
    columns = getattr(meta, 'list_fields', tuple(name for name, field in visible.items() if not isinstance(field, LongText))[:4])
    search = getattr(meta, 'search_fields', tuple(texts[:8]))
    standard_text = {key for key, field in model.__app_fields__.items() if standard(field)}
    for value, maximum, names in ((columns, 8, {'id', *visible}), (search, 8, {name for name, field in visible.items() if isinstance(field, Text)} | standard_text)):
        if not isinstance(value, (tuple, list)) or len(value) > maximum or any(not isinstance(name, str) or name not in names for name in value) or len(set(value)) != len(value):
            raise ValueError('View fields must be distinct visible field names, with at most eight entries')
    # How a list opens its records: the full record page, or a side panel beside the narrowed list.
    open_in = getattr(meta, 'open_in', 'page')
    if open_in not in OPEN_IN:
        raise ValueError('open_in must be "page" or "panel"')
    # The summary strip under the record header: Meta.summary = False leaves it out.
    summary = getattr(meta, 'summary', None)
    if summary is not None and type(summary) is not bool:
        raise ValueError(f'{model.__name__}.Meta.summary must be True or False, not {summary!r}')
    # Meta.duplicate = False hides the record's Duplicate action. Display only: a create is checked as any other.
    duplicate = getattr(meta, 'duplicate', True)
    if type(duplicate) is not bool:
        raise ValueError(f'{model.__name__}.Meta.duplicate must be True or False, not {duplicate!r}')
    # Which side column blocks a record page shows, in this order.
    side_blocks = getattr(meta, 'side_blocks', None)
    if side_blocks is not None and (not isinstance(side_blocks, (tuple, list)) or len(set(side_blocks)) != len(side_blocks)
                                    or any(type(name) is not str or name not in SIDE_BLOCKS for name in side_blocks)):
        raise ValueError(f'{model.__name__}.Meta.side_blocks: list blocks once each from {", ".join(SIDE_BLOCKS)}, '
                         f'such as ("related", "files"), not {side_blocks!r}')
    list_tabs = list_tabs_for(model, meta)
    # A list offers a board only when Meta.board names the field its columns follow: the model's State.
    board = getattr(meta, 'board', None)
    if board is not None and (type(board) is not str or board != getattr(model, '__state__', None)):
        raise ValueError(f'{model.__name__}.Meta.board: name the model\'s State field'
                         + (f', {model.__state__!r}' if getattr(model, '__state__', None) else '; this model has no State'))
    # Leaving a record keeps its unsaved edits apart from it (evlet.unsaved); False keeps the leave prompt.
    from ..unsaved import autosave as keeps_unsaved
    autosave = keeps_unsaved(model)
    # Search also finds records by their title and name.
    search = tuple(dict.fromkeys((*search, *(key for key in ('title', 'name') if key in standard_text))))
    # The field a new record's form puts the cursor in; the browser falls back to the first editable field.
    focus = getattr(meta, 'focus', None)
    if focus is not None:
        check_focus(f'{model.__name__}.Meta.focus', model, focus)
    layout = getattr(meta, 'layout', (('Details', tuple(visible)),))
    used = []
    groups = []
    entries, _ = layout_blocks(model, layout, visible)
    # Blocks besides sections make a record layout (record_layout in the schema, added by the registry).
    blocks = any(kind != 'section' for _, kind, _ in entries)
    if not blocks and len(layout) > 8:
        raise ValueError('Use at most eight layout groups')
    for _, kind, group in entries:
        if kind == 'main':
            # The primary area has no heading; the model's label names it where a name is needed.
            group = (label, group.fields) if group.fields else None
            if group is None:
                continue
            unknown = [name for name in group[1] if name not in visible]
            if unknown:
                raise ValueError(f'{model.__name__}.Meta.layout: Main names unknown or hidden fields: {", ".join(unknown)}')
            for name in group[1]:
                if name in used:
                    raise ValueError(f'Layout lists {name} more than once')
                used.append(name)
            groups.append({'label': label, 'fields': list(group[1]), 'collapsible': False, 'collapsed': False, 'flow': 'rows', 'main': True})
            continue
        if kind != 'section':
            continue
        section = group if isinstance(group, Section) else None
        if section is not None:
            section.check(model.__name__)
            group = (section.title, section.fields)
        if not isinstance(group, (tuple, list)) or len(group) != 2 or not isinstance(group[0], str) or not group[0] or not isinstance(group[1], (tuple, list)):
            raise ValueError('Layout groups are (label, field names) pairs or Section(title, fields)')
        unknown = [str(name) for name in group[1] if not isinstance(name, str) or name not in visible]
        if unknown:
            raise ValueError(f'Layout group {group[0]} names unknown or hidden fields: {", ".join(unknown)}')
        for name in group[1]:
            if name in used:
                raise ValueError(f'Layout lists {name} more than once')
            used.append(name)
        # A plain (label, fields) pair is a section that can't collapse.
        groups.append({'label': group[0], 'fields': list(group[1]), 'collapsible': bool(section and section.collapsible),
                       'collapsed': section.collapsed if section and section.collapsible else False, 'flow': section.flow if section else 'rows',
                       **({'description': section.description} if section and section.description is not None else {}),
                       **({'panel': False} if section and section.panel is False else {})})
    # Visible fields the layout leaves out follow under Other, in declaration order.
    rest = [name for name in visible if name not in used]
    if rest:
        groups.append({'label': 'Other', 'fields': rest, 'collapsible': False, 'collapsed': False, 'flow': 'rows'})
    titles = [group['label'] for group in groups]
    if len(set(titles)) != len(titles):
        if blocks:
            raise ValueError(f'{model.__name__}.Meta.layout: give sections different titles, so the record layout can place them'
                             + (' (Other holds the fields no section lists)' if rest else ''))
        if getattr(meta, 'panel', None) is not None or any(group.get('panel') is False for group in groups):
            raise ValueError(f'{model.__name__}.Meta.layout: give sections different titles, so the side panel can name them')
    panel = panel_for(model, meta, groups, visible)
    for group in groups:
        group.pop('panel', None)
    return {'layout': groups, 'label': label, 'plural': plural, 'description': description, 'title_field': title,
            'list_fields': list(dict.fromkeys(('id', *columns))), 'search_fields': list(search), 'open_in': open_in,
            'autosave': autosave, **({'list_tabs': list_tabs} if list_tabs else {}),
            **({'board': board} if board else {}),
            # Additive keys: only a model that declares them carries them.
            **({'summary': summary} if summary is not None else {}),
            **({'side_blocks': list(side_blocks)} if side_blocks is not None else {}),
            **({'focus': focus} if focus is not None else {}),
            # Meta.image: the Image field shown as the record's avatar or thumbnail (checked when the class is made).
            **({'image': model.__image__} if getattr(model, '__image__', None) else {}),
            **({'duplicate': False} if not duplicate else {}),
            **({'panel': panel} if panel is not None else {})}


def panel_names(model, groups, visible):
    """The names Meta.panel may use, each with its kind: section titles, fields, Table fields, the record's parts
    (TABS), the summary strip and the side blocks. A section or field wins over a part or block of the same name,
    so a field called info is the field."""
    names = {name: 'tabs' for name in TABS}
    names.update((name, 'blocks') for name in PANEL_BLOCKS if name != 'related')
    names.update((name, 'tabs') for name in getattr(model, '__tables__', {}))
    names.update((name, 'fields') for name in visible)
    names.update((group['label'], 'sections') for group in groups)
    return names


def panel_for(model, meta, groups, visible):
    """What a record opened in a list's side panel leaves out, or None when it shows everything.

    The result lists hidden names by kind ({'sections', 'fields', 'tabs', 'blocks'}, each sorted) and, for
    Meta.panel, the order it names parts and blocks in. Meta.panel wins over panel=False flags. Display only:
    permissions never read it. Related means the Related rows (the panel has no Related block). The "tabs" kind
    names the record's parts (TABS) and Table fields; record pages have no tabs."""
    tables = getattr(model, '__tables__', {})
    # Every section is part of Details.
    home = {group['label']: 'details' for group in groups}
    keep = getattr(meta, 'panel', None)
    where = f'{model.__name__}.Meta.panel'
    # Meta.panel_summary = False leaves the summary strip (on a compact page, the key cards) out of the panel.
    summary = getattr(meta, 'panel_summary', True)
    if type(summary) is not bool:
        raise ValueError(f'{model.__name__}.Meta.panel_summary must be True or False, not {summary!r}')
    if keep is None:
        hidden = {'sections': [group['label'] for group in groups if group.get('panel') is False],
                  'fields': [name for name, field in visible.items() if field.panel is False],
                  'tabs': [name for name, table in tables.items() if getattr(table, 'panel', True) is False], 'blocks': [] if summary else ['summary']}
        hidden = {kind: sorted(names) for kind, names in hidden.items() if names}
        return hidden or None
    if not isinstance(keep, (tuple, list)) or any(type(name) is not str or not name for name in keep) or len(set(keep)) != len(keep):
        raise ValueError(f'{where}: list what the side panel shows once each, such as ("Contact", "notes", "activity")')
    known = panel_names(model, groups, visible)
    unknown = [name for name in keep if name not in known]
    if unknown:
        raise ValueError(f'{where} names unknown elements: {", ".join(unknown)}. Use section titles, field names, '
                         f'parts ({", ".join(dict.fromkeys((*TABS, *tables)))}) or blocks ({", ".join(name for name in PANEL_BLOCKS if name != "related")})')
    kept = {kind: {name for name in keep if known[name] == kind} for kind in PANEL_KINDS}
    # 'details' keeps every section; a named section keeps its fields;
    # a named field keeps its section with only it.
    shown_sections = {group['label'] for group in groups if home[group['label']] in kept['tabs'] or group['label'] in kept['sections']
                      or any(name in kept['fields'] for name in group['fields'])}
    whole = {group['label'] for group in groups if home[group['label']] in kept['tabs'] or group['label'] in kept['sections']}
    needed = {home[label] for label in shown_sections}
    hidden = {
        'sections': sorted(group['label'] for group in groups if group['label'] not in shown_sections),
        # Fields of a hidden section go with it; a section shown for some of its fields hides the rest.
        'fields': sorted(name for group in groups for name in group['fields']
                         if group['label'] in shown_sections and group['label'] not in whole and name not in kept['fields']),
        'tabs': sorted(name for name in dict.fromkeys((*TABS, *tables))
                       if name != 'details' and name not in kept['tabs'] and name not in needed),
        'blocks': sorted(name for name in PANEL_BLOCKS if name != 'related' and name not in kept['blocks']),
    }
    # The order tabs and blocks show in; a section or field stands for the tab it lives on.
    order = list(dict.fromkeys(home[name] if known[name] == 'sections' else next((home[group['label']] for group in groups if name in group['fields']), 'details')
                               if known[name] == 'fields' else name for name in keep))
    return {**{kind: names for kind, names in hidden.items() if names}, 'order': order}


# The field kinds whose values can be list tabs (Meta.list_tabs).
LIST_TAB_KINDS = ('state', 'select', 'link')


def list_tabs_for(model, meta, fields=None):
    """The list page's tabs: {field, kind, default}, or None for no tabs.

    Meta.list_tabs names a State, Select or Link field; unset means the State field, if any, and None
    means no tabs. Meta.list_tab is the tab the list opens on: "all" (the default) or one of the field's
    values. For a Link it is the linked record's title or name, looked up when the page loads.
    `fields` defaults to the app's fields; Controls passes the instance's."""
    where = f'{model.__name__}.Meta'
    name = getattr(meta, 'list_tabs', model.__state__)
    default = getattr(meta, 'list_tab', 'all')
    if type(default) is not str or not default.strip():
        raise ValueError(f'{where}.list_tab: name the tab the list opens on, such as "all" or "active"')
    if name is None:
        if default != 'all':
            raise ValueError(f'{where}.list_tab needs Meta.list_tabs: this list has no tabs')
        return None
    field = (model.__app_fields__ if fields is None else fields).get(name) if type(name) is str else None
    kind = None if field is None else 'state' if name == model.__state__ else field.kind
    if kind not in LIST_TAB_KINDS:
        raise ValueError(f'{where}.list_tabs: name a State, Select or Link field of {model.__name__}, not {name!r}')
    if kind != 'link' and default != 'all' and default not in field.choices:
        raise ValueError(f'{where}.list_tab: {default!r} is not a value of {name}. Use "all" or one of {", ".join(field.choices)}')
    return {'field': name, 'kind': kind, 'default': default}


# Filter operators that may compare with a field of the record (Ref('field')).
REF_OPERATORS = ('eq', 'ne', 'gt', 'gte', 'lt', 'lte')
# Columns a Related block shows at most, and the rows it shows a page by default and at most.
RELATED_FIELDS = 6
RELATED_ROWS, RELATED_MAX = 5, 20


def layout_filters(registry, model, source, filters, where):
    """A block's filters as [[field, operator, value], ...] on source, validated like list filters. A Ref('field')
    value compares with that field of the record, sent as {"$expr": "ref", "args": [field]}."""
    from ..declarations import Ref
    from ..query import ChildGroup, KIND_OPERATORS, SqlQuery, conditions
    from ..models import ValidationError
    if filters is None:
        return []
    try:
        items = conditions(filters)
    except ValidationError as error:
        raise ValueError(f'{where}: {"; ".join(error.errors.values()) if hasattr(error, "errors") else error}') from None
    visible = {name for name, field in model.__app_fields__.items() if not field.hidden or name == 'id'}
    result = []
    for item in items:
        if isinstance(item, ChildGroup) or '__' in item.path or '.' in item.path:
            raise ValueError(f'{where}: filter {item.label!r} must name a field of {source.__name__} itself, not a path or child rows')
        field = source.__fields__.get(item.path) if item.path != 'id' else None
        if item.path != 'id' and (field is None or not field.stored or getattr(field, 'hidden', False)):
            raise ValueError(f'{where}: filter {item.label!r} names no stored, visible field of {source.__name__}')
        kind = 'link' if item.path == 'id' else field.kind
        if item.operator not in KIND_OPERATORS.get(kind, ()):
            raise ValueError(f'{where}: filter {item.label!r}: {kind} fields do not take {item.operator}')
        value = item.value
        if isinstance(value, Ref):
            if item.operator not in REF_OPERATORS or '.' in value.path:
                raise ValueError(f"{where}: filter {item.label!r}: Ref('field') compares with one field of the record, using "
                                 f"{', '.join(REF_OPERATORS)}")
            if value.path not in visible and value.path != 'id':
                raise ValueError(f'{where}: filter {item.label!r}: Ref({value.path!r}) is not a visible field of {model.__name__}')
            result.append([item.path, item.operator, {'$expr': 'ref', 'args': [value.path]}])
            continue
        if item.path != 'id':
            try:
                compiler = SqlQuery(registry, source)
                compiler.where([[item.path, item.operator, value]])
            except ValidationError as error:
                raise ValueError(f'{where}: filter {item.label!r}: {next(iter(getattr(error, "errors", {}).values()), error)}') from None
            if item.operator not in ('isnull',):
                value = serialize_filter_value(field, value)
        result.append([item.path, item.operator, value])
    return result


def serialize_filter_value(field, value):
    """A literal filter value as JSON: one value, or each value of an in, nin or between list."""
    def one(item):
        return None if item is None else field.serialize(field.clean(item))
    try:
        return [one(item) for item in value] if isinstance(value, (list, tuple)) else one(value)
    except (ValueError, TypeError):
        return value


def back_link(model, source, link, where):
    """The Link field of source that points to model: `link`, or the only one when it is left out."""
    from ..fields import Link
    links = [name for name, field in source.__declared_fields__.items() if isinstance(field, Link) and field.target is model and not field.hidden]
    if link is None:
        if len(links) != 1:
            raise ValueError(f'{where}: {source.__name__} has ' + (f'{len(links)} Links to {model.__name__} ({", ".join(links)}); name one as link='
                             if links else f'no visible Link to {model.__name__}'))
        return links[0]
    if link not in links:
        raise ValueError(f'{where}: {source.__name__}.{link} is not a visible Link to {model.__name__}')
    return link


def layout_block(registry, model, kind, block, where):
    """One block of a record layout as the neutral schema sends it."""
    from ..measures import declared
    if kind == 'section':
        return {'kind': 'section', 'label': block.title if isinstance(block, Section) else block[0]}
    if kind == 'table':
        return {'kind': 'table', 'name': block}
    if kind == 'html':
        return {'kind': 'html', 'html': block.markup()}
    source = registry.resolve(block.model)
    children = {child.model for child in registry.children}
    if source in children or getattr(source, '__ledger__', False):
        raise ValueError(f'{where}: {source.__name__} rows belong to their document; a {kind} block lists root models')
    link = back_link(model, source, block.link, where)
    filters = layout_filters(registry, model, source, block.filters, where)
    visible = {name for name, field in source.__app_fields__.items() if not field.hidden}
    if kind == 'related':
        unknown = [name for name in block.fields if name not in visible]
        if unknown:
            raise ValueError(f'{where}: Related fields are unknown or hidden on {source.__name__}: {", ".join(unknown)}')
        if len(block.fields) > RELATED_FIELDS:
            raise ValueError(f'{where}: a Related block shows at most {RELATED_FIELDS} fields')
        if block.limit is not None and block.limit > RELATED_MAX:
            raise ValueError(f'{where}: a Related block shows 1 to {RELATED_MAX} rows a page')
        for entry in block.order:
            name = entry.lstrip('-')
            if name != 'id' and (name not in source.__fields__ or not source.__fields__[name].stored or source.__fields__[name].kind == 'reference'):
                raise ValueError(f'{where}: Related order must name stored fields of {source.__name__} (not References)')
        return {'kind': 'related', 'model': registry.name_for(source), 'link': link, 'label': block.label or view_for(source)['plural'],
                'filters': filters, 'fields': list(block.fields), 'limit': block.limit or RELATED_ROWS, 'order': list(block.order)}
    measures = {item.name: item for item in declared(registry, source) if item.link is None}
    unknown = [name for name in block.y if name not in measures]
    if unknown:
        raise ValueError(f'{where}: Chart y names declared measures of {source.__name__} (Meta.measures, not per record): '
                         f'{", ".join(unknown)} ' + (f'is not one of {", ".join(measures)}' if measures else 'but it declares none'))
    x = None
    if block.x is not None:
        name, _, part = block.x.partition(':')
        field = source.__fields__.get(name)
        if field is None or name not in visible or not field.stored or field.kind in ('tags', 'reference', 'longtext', 'richtext', 'multiselect', 'image'):
            raise ValueError(f'{where}: Chart x groups by a stored, visible field of {source.__name__}, not {block.x!r}')
        if part and (part not in ('day', 'month', 'year') or field.kind not in ('date', 'datetime')):
            raise ValueError(f"{where}: Chart x takes :day, :month or :year after a Date or DateTime field, not {block.x!r}")
        x = block.x
    return {'kind': 'chart', 'type': block.type, 'model': registry.name_for(source), 'link': link, 'x': x, 'y': list(block.y),
            'label': block.label, 'filters': filters}


def record_layout(registry, model):
    """The record layout of Meta.layout as the schema's record_layout, or None for a layout of sections only.

    {"blocks": [...]}: with Main first, Main's fields (a section named by the model's label) and its Tables, each
    marked main, then the blocks of "more information"; without Main, the blocks in order. Sections are named by
    title (their fields stay in the schema's layout); Other, the fields no section lists, goes last. Every record
    page is compact: without a record_layout the first section is the primary area and every Table follows it."""
    meta = getattr(model, 'Meta', None)
    layout = getattr(meta, 'layout', None)
    if layout is None:
        return None
    view = view_for(model)
    visible = {name: field for name, field in model.__app_fields__.items() if not field.hidden}
    entries, _ = layout_blocks(model, layout, visible)
    if all(kind == 'section' for _, kind, _ in entries):
        return None
    where = f'{model.__name__}.Meta.layout'
    placed = {block.title if isinstance(block, Section) else block[0] for _, kind, block in entries if kind == 'section'}
    placed |= {group['label'] for group in view['layout'] if group.get('main')}
    other = [{'kind': 'section', 'label': group['label']} for group in view['layout'] if group['label'] not in placed]
    if entries and entries[0][1] == 'main':
        # Main's fields (a section named by the model's label) and its Tables, each marked main, then the blocks of
        # "more information". A renderer that ignores main shows them in order.
        main = entries[0][2]
        head = ([{'kind': 'section', 'label': view['label'], 'main': True}] if main.fields else []) + \
            [{'kind': 'table', 'name': name, 'main': True} for name in main.tables]
        return {'blocks': head + [layout_block(registry, model, kind, block, where) for _, kind, block in entries[1:]] + other}
    return {'blocks': [layout_block(registry, model, kind, block, where) for _, kind, block in entries] + other}


def compile_layouts(registry):
    """{model: record_layout} for every model whose Meta.layout has blocks besides sections."""
    result = {}
    for model in registry.models.values():
        layout = record_layout(registry, model)
        if layout is not None:
            result[model] = layout
    return result


def layout_for_role(business, user, model, layout, readable):
    """The record layout this role sees: Related and Chart blocks only over records, Links, filter fields and
    measures it may read (a block that needs more is left out), Related columns and order cut to readable fields."""
    from types import SimpleNamespace
    from ..measures import available, visible as measurable
    policy, registry = business.policy, business.registry

    def refs(block):
        return {value['args'][0] for _, _, value in block['filters'] if isinstance(value, dict) and value.get('$expr') == 'ref'}

    def keep(block):
        if block['kind'] not in ('related', 'chart'):
            return block
        source = registry.models[block['model']]
        if not policy.allows(user, source, 'read'):
            return None
        fields = {'id', *policy.fields(user, source)}
        if block['link'] not in fields or any(path not in fields for path, _, _ in block['filters']) or not refs(block) <= {'id', *readable}:
            return None
        if block['kind'] == 'related':
            order = business.relation_order(user, SimpleNamespace(source=source, order=tuple(block['order'])))
            return {**block, 'fields': [name for name in block['fields'] if name in fields], 'order': order}
        names = {item.name for item in available(business, user, source, False)}
        y = [name for name in block['y'] if name in names]
        if not y or block['x'] is not None and not measurable(policy, user, source, block['x'].split(':')[0], fields):
            return None
        return {**block, 'y': y}

    def blocks(items):
        return [block for block in map(keep, items) if block is not None]
    return {**layout, 'blocks': blocks(layout['blocks'])}


def currency_schema(field):
    """A fixed code as currency; any other source as currency_source, resolved per user by /api/schema."""
    source = field.currency_source() if hasattr(field, 'currency_source') else None
    if source is None:
        return {}
    return {'currency': source['code']} if 'code' in source else {'currency_source': source}


def field_schema(field):
    result = {**field.metadata(), 'label': field.label or label_for(field.name),
              'help_text': field.help_text, **({'tooltip': field.tooltip} if field.tooltip else {}), 'placeholder': field.placeholder,
              **({'suffix': field.suffix} if field.suffix else {}),
              # Integer(grouping=False): shown without thousands separators (display only).
              **({'grouping': False} if getattr(field, 'grouping', True) is False else {}),
              # Forms leave out the standard title and a name no naming fills (evlet/naming.py); responses keep them.
              'hidden': field.hidden or form_hidden(field), 'read_only': field.read_only,
              **({'stored': field.stored} if field.compute is not None or field.query is not None or not field.stored else {}),
              **currency_schema(field),
              **({'editable_when_locked': True} if getattr(field, 'editable_when_locked', False) else {}),
              # Duplicate leaves this value out of the new record (display only).
              **({'copy': False} if getattr(field, 'copy', True) is False else {}),
              **({'sequence': field.schema()} if field.kind == 'sequence' else {}),
              'default_kind': 'none' if field.default is MISSING else 'reference' if isinstance(field.default, Ref) else 'user' if field.default is CurrentUser
              else 'callable' if callable(field.default) else 'static'}
    if result['default_kind'] == 'static':
        value = None if field.default is None else field.validate(field.default)
        result['default'] = field.serialize(value)
    if getattr(field, 'ledger_target', False):
        # A Link to a ledger row shows its plain integer id and is never written by a caller (evlet/ledger.py).
        result.pop('target', None)
        # An id, not a quantity: shown without thousands separators.
        result.update(type='integer', read_only=True, grouping=False)
    return result


def ui_schema(registry):
    # A model with Meta.api = None is left out; "read" is stated so screens offer no writes ("full" is the default).
    api = getattr(registry, 'api', {})
    return {'version': 1, 'ui_version': 1, 'models': [
        {'name': name, **view_for(model), **({'record_layout': registry.layouts[model]} if model in getattr(registry, 'layouts', {}) else {}),
         'fields': [field_schema(field) for field in model.__app_fields__.values()],
         **({'api': 'read'} if api.get(model) == 'read' else {}), **behavior(model, registry)}
        for name, model in registry.models.items() if api.get(model, 'full') is not None
    ]}


def behavior(model, registry):
    from ..reactivity import plans, serialized, graph
    if not getattr(model, '__declaration_mode__', False):
        # A plain model still tells the form which other fields its Link filters read.
        filters = {name: {'filters': {key: serialized(value) for key, value in entry['filters'].items()}}
                   for name, entry in plans(model).items() if entry.get('filters')}
        return {'behavior': filters} if filters else {}
    nodes, order = ({}, []) if any(child.model is model for child in registry.children) else graph(registry, model)
    fields = plans(model)
    return {'document_version': 1, 'resource': getattr(model, '__resource_id__', registry.name_for(model)),
            'behavior': {name: {key: {field: serialized(value) for field, value in item.items()} if key == 'filters' else serialized(item)
                                for key, item in entry.items()} for name, entry in fields.items()},
            'order': order, 'sources': source_schema(model)}


def source_schema(model):
    return {name: {key: {'type': field.kind, **({'scale': field.scale} if hasattr(field, 'scale') else {})} for key, field in source.fields.items()}
            for name, source in model.__sources__.items()}
