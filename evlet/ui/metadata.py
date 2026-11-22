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
META_OPTIONS = frozenset(('label', 'plural', 'description', 'title_field', 'list_fields', 'search_fields', 'option', 'layout',
                          'calculation_language', 'numeric_profile', 'measures', 'key_fields', 'relations', 'attachments',
                          'activity', 'naming', 'mail', 'title', 'open_in', 'api', 'period', 'period_scope', 'autosave',
                          'status', 'board', 'list_tabs', 'list_tab', 'summary', 'side_blocks', 'panel', 'panel_summary', 'ledger', 'append_only', 'timestamps', 'indexes',
                          'focus', 'cards', 'duplicate', 'image', 'script', 'on_open', 'abstract'))

# How a collapsible section starts: open, closed, or closed when every field in it is empty.
COLLAPSED = (False, True, 'empty')
# How a section orders its fields, on screen and for the Tab key: across then down, or down the left column then the right.
FLOWS = ('rows', 'columns')
# The record page's own parts besides its sections, as Meta.panel and the schema's "panel" name them: Activity (the
# side column), Details (every section), and the Related row of "more information". A Table field is named by
# its own name. Record pages have no tabs (removed in 0.4).
TABS = ('activity', 'details', 'related')
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
    """A "more information" row in Model.Meta.layout that a person can open and close.

    `collapsed` is where it starts: True (closed, the default), False (open) or 'empty' (closed when every field in
    it is empty on an existing record; a new record shows it open). A person's own choice wins after that, and on a
    new record a row that holds a required field starts open. A plain ('Title', fields) pair is a section that can't
    collapse, so it starts open. `panel=False` leaves the section out when a list opens the record in its side panel;
    the full page still shows it. `description` is an optional line of text under the title. A wrong option is
    ignored with a warning when the app loads."""

    __slots__ = ('title', 'fields', 'collapsible', 'collapsed', 'flow', 'panel', 'description', 'custom', 'unknown')

    def __init__(self, title, fields=(), *, collapsible=True, collapsed=True, flow='rows', panel=True, description=None, custom=None, **unknown):
        self.title, self.fields, self.collapsible, self.collapsed, self.flow = title, fields, collapsible, collapsed, flow
        # custom="method": the model script draws the section's content instead of fields (docs/model-scripts.md).
        self.panel, self.description, self.custom, self.unknown = panel, description, custom, unknown

    def __repr__(self):
        return (f'Section({self.title!r}, {self.fields!r}, collapsible={self.collapsible!r}, collapsed={self.collapsed!r}'
                + (f', flow={self.flow!r}' if self.flow != 'rows' else '')
                + (f', description={self.description!r}' if self.description is not None else '')
                + (f', custom={self.custom!r}' if self.custom is not None else '')
                + (', panel=False)' if self.panel is False else ')'))

    def options(self, warn):
        """Its group options; warn(text) reports a wrong one, which keeps its default."""
        flow = self.flow if type(self.flow) is str and self.flow in FLOWS else warn(f"flow must be 'rows' or 'columns', not {self.flow!r}") or 'rows'
        description = self.description
        if description is not None and (type(description) is not str or not description.strip()):
            description = warn(f'description must be nonempty text, not {description!r}')
        return {**fold_options(self, warn), 'flow': flow, **({'description': description} if description is not None else {})}


def fold_options(item, warn):
    """collapsible, collapsed and (when False) panel of a Section or TableSection; warn(text) reports an unknown option
    or a wrong value, which keeps its default. One that can't collapse starts open."""
    for key in item.unknown:
        warn(f'has no option {key}')
    collapsible = item.collapsible if type(item.collapsible) is bool else warn('collapsible must be True or False') or True
    collapsed = item.collapsed if starts_valid(item.collapsed) else warn(f"collapsed must be False, True or 'empty', not {item.collapsed!r}") or True
    if type(item.panel) is not bool:
        warn('panel must be True or False')
    return {'collapsible': collapsible, 'collapsed': collapsed if collapsible else False, **({'panel': False} if item.panel is False else {})}


class TableSection:
    """A Table field as a "more information" row in Model.Meta.layout: its title bar (title, row count, Add, Focus and
    Columns), the table, its total line and totals. `collapsed` is True (closed, the default), False (open) or 'empty'
    (closed while the table has no rows). A Table name on its own is TableSection(name). `panel=False` leaves it out
    of a list's side panel."""

    __slots__ = ('name', 'collapsible', 'collapsed', 'panel', 'unknown')

    def __init__(self, name, *, collapsible=True, collapsed=True, panel=True, **unknown):
        self.name, self.collapsible, self.collapsed, self.panel, self.unknown = name, collapsible, collapsed, panel, unknown

    def __repr__(self):
        options = {key: getattr(self, key) for key, default in (('collapsible', True), ('collapsed', True), ('panel', True))
                   if getattr(self, key) != default or type(getattr(self, key)) is not bool}
        return f'TableSection({self.name!r}' + ''.join(f', {key}={value!r}' for key, value in options.items()) + ')'



class Main:
    """A block of the main zone of a record page, drawn open in the order declared; it never collapses.

    Main(fields, title=None) is a group of fields: with a title it shows a small heading; without one it is named by
    the model's label where a name is needed (Meta.panel, Controls), and an untitled group after the first by the label
    and its number ("Invoice 2"). Main("lines") is one Table. The form Main(fields, "lines", ...) of 0.4.9 and before
    reads as Main(fields) followed by Main("lines") for each Table named."""

    __slots__ = ('fields', 'tables', 'title', 'custom', 'unknown')

    def __init__(self, fields=(), *tables, title=None, custom=None, **unknown):
        # Main(custom="method"): a block the model script draws (docs/model-scripts.md).
        self.fields, self.tables, self.title, self.custom, self.unknown = fields, tables, title, custom, unknown

    def __repr__(self):
        return (f'Main({self.fields!r}' + ''.join(f', {name!r}' for name in self.tables)
                + (f', title={self.title!r}' if self.title is not None else '')
                + (f', custom={self.custom!r}' if self.custom is not None else '') + ')')


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


def layout_blocks(model, layout, visible, label):
    """Meta.layout (None for the default) read into its blocks in drawing order, and the warnings for what it ignored.

    Each block is a dict with its kind ('section', 'table', 'related', 'html' or 'chart'), its stable name (a section
    or Table; the others are named by record_layout), main, and the declared item as 'block'; a section carries its
    form group as 'group'. The main zone comes first: every Main in order, or without one the first group of fields.
    "More information" follows in order, then Other (the fields no block places, in declaration order) and a
    TableSection for each Table no block places; without Meta.layout every Table joins the main zone. A wrong item is
    left out with a warning and what it would have placed falls to Other or the trailing tables. Tab still raises."""
    from ..forms import Chart
    from ..meaning import Related
    where = f'{model.__name__}.Meta.layout'
    known = getattr(model, '__tables__', {})
    warnings, blocks, placed, names = [], [], set(), set()
    # A field in a Table's totals shows under that Table (Table(totals=...)), so it counts as placed.
    totals = {field: table for table, item in known.items() for field in getattr(item, 'totals', ()) if field in visible}
    placed.update(totals)
    kept_totals = set()

    def warn(item, text):
        warnings.append(f'{where}: {item!r} {text}; it is ignored')

    def named(item, title):
        if title in names:
            return warn(item, f'uses the name {title!r} again')
        names.add(title)
        return title

    def numbered(base):
        name, number = base, 1
        while name in names:
            number += 1
            name = f'{base} {number}'
        names.add(name)
        return name

    def fields_of(item, listed):
        """The visible fields a group lists, each once; the others are warned about."""
        if not isinstance(listed, (tuple, list)):
            return warn(item, 'lists its fields as a tuple of field names') or []
        if not listed:
            warn(item, 'is empty')
        kept = []
        for name in listed:
            if type(name) is str and name in known:
                warn(item, f'names the Table {name!r} among its fields; give it its own block')
            elif type(name) is str and name in totals:
                # Kept in the group, so a role that cannot read the Table still sees it here (the browser drops it
                # while the Table shows).
                warn(item, f'names {name!r}, which shows in the totals of the Table {totals[name]!r} '
                           '(only a role that cannot read that Table sees it here)')
                if name not in kept_totals:
                    kept_totals.add(name)
                    kept.append(name)
            elif type(name) is not str or name not in visible:
                warn(item, f'names {name!r}, which is not a visible field of {model.__name__}')
            elif name in placed:
                warn(item, f'places {name!r} again')
            else:
                placed.add(name)
                kept.append(name)
        return kept

    def section(item, name, listed, group, main=False):
        if name is None:
            return
        kept = fields_of(item, listed)
        if not kept:
            names.discard(name)
            return
        blocks.append({'kind': 'section', 'name': name, 'main': main, 'block': item, 'group': {'label': name, 'fields': kept, **group}})

    def table(item, name, main, options=None):
        if type(name) is str and name in visible:
            warn(item, f'names the field {name!r} where a Table is expected; put fields in a tuple')
        elif type(name) is not str or name not in known:
            warn(item, f'names {name!r}, which is not a Table field of {model.__name__} ({", ".join(known) or "none"})')
        elif name in placed:
            warn(item, f'places the Table {name!r} again')
        else:
            placed.add(name)
            blocks.append({'kind': 'table', 'name': name, 'main': main, 'block': item, **(options or {})})

    def custom(item, name, method, main, options):
        from ..fields import script_method
        try:
            script_method(method, 'custom=')
        except ValueError as error:
            return warn(item, str(error))
        if name is not None:
            blocks.append({'kind': 'custom', 'name': name, 'main': main, 'block': item, 'custom': method, **options})

    if layout is not None and not isinstance(layout, (tuple, list)):
        warn(layout, 'is not a tuple of blocks, such as (Main(...), Section(...), Related(...))')
        layout = ()
    for block in layout or ():
        if getattr(type(block), '__name__', '') == 'Tab':
            raise ValueError(f'{where}: Tab was removed, record pages have no tabs; use Main(...) and Section(...)')
    for block in (layout or ())[MAX_BLOCKS:]:
        warn(block, f'is past the limit of {MAX_BLOCKS} blocks')
    details = (('Details', shown),) if (shown := tuple(name for name in visible if name not in totals)) else ()
    main_group = {'collapsible': False, 'collapsed': False, 'flow': 'rows', 'main': True}
    for block in details if layout is None else layout[:MAX_BLOCKS]:
        say = lambda text, block=block: warn(block, text)
        if isinstance(block, Main):
            for key in block.unknown:
                say(f'has no option {key}')
            title = block.title
            if title is not None and (type(title) is not str or not title.strip()):
                title = say(f'title must be nonempty text, not {title!r}')
            if block.custom is not None:
                custom(block, named(block, title) if title else numbered(label), block.custom, True, {})
                continue
            if isinstance(block.fields, str):
                table(block, block.fields, True)
            elif block.fields or not block.tables:
                section(block, named(block, title) if title else numbered(label), block.fields,
                        {**main_group, **({'heading': True} if title else {})}, main=True)
            for name in block.tables:
                table(block, name, True)
        elif isinstance(block, Section) or isinstance(block, (tuple, list)) and len(block) == 2:
            item = block if isinstance(block, Section) else Section(*block, collapsible=False)
            if type(item.title) is not str or not item.title.strip():
                say('needs a title')
            elif item.custom is not None:
                custom(block, named(block, item.title), item.custom, False, fold_options(item, say))
            else:
                section(block, named(block, item.title), item.fields, item.options(say))
        elif isinstance(block, (TableSection, str)):
            item = block if isinstance(block, TableSection) else TableSection(block)
            table(block, item.name, False, fold_options(item, say))
        elif isinstance(block, Related):
            if block.hidden:
                say('hides its records; hidden=True belongs in Meta.relations')
            else:
                blocks.append({'kind': 'related', 'main': False, 'block': block})
        elif isinstance(block, Html):
            try:
                unknown = [name for name in block.fields() if name not in visible]
                block.markup()
            except ValueError as error:
                say(str(error))
                continue
            if unknown:
                say(f'shows unknown or hidden fields: {", ".join(unknown)}')
            else:
                blocks.append({'kind': 'html', 'main': False, 'block': block})
        elif isinstance(block, Chart):
            if block.model is None:
                say('names no model; a layout Chart charts records of a model, such as Chart("bar", x="status", y="hours", model="task")')
            else:
                blocks.append({'kind': 'chart', 'main': False, 'block': block})
        else:
            say('is not a layout block: use Main(...), Section(...), TableSection(...), a Table name, Related(...), Html(...) or Chart(...)')
    rest = [name for name in visible if name not in placed]
    other = next((block for block in blocks if block['kind'] == 'section' and block['name'] == 'Other'), None)
    if rest and other:
        other['group']['fields'] += rest
    elif rest:
        names.add('Other')
        blocks.append({'kind': 'section', 'name': 'Other', 'main': False, 'block': None,
                       'group': {'label': 'Other', 'fields': rest, 'collapsible': True, 'collapsed': True, 'flow': 'rows'}})
    if not any(block['main'] for block in blocks):
        # Without Main the first group of fields is the main zone.
        first = next((block for block in blocks if block['kind'] == 'section'), None)
        if first is not None:
            first['main'] = True
            first['group'].update(collapsible=False, collapsed=False, main=True)
    blocks += [{'kind': 'table', 'name': name, 'main': layout is None, 'block': None,
                **({} if layout is None else {'collapsible': True, 'collapsed': True})} for name in known if name not in placed]
    return [block for block in blocks if block['main']] + [block for block in blocks if not block['main']], warnings


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
    """Raise ValueError for a removed Meta option (Meta.tabs and Meta.default_tab in 0.4, Meta.journal after 0.4.7,
    Meta.effects in 0.4.11), saying what to use."""
    if isinstance(meta, type) and 'effects' in vars(meta):
        raise ValueError(f'{name}.Meta.effects was removed: post rows in a @release hook through self.context.session '
                         'and reverse them in @release.exit (docs/states-actions.md#hooks)')
    if isinstance(meta, type) and hasattr(meta, 'journal'):
        raise ValueError(f'{name}.Meta.journal was renamed: use Meta.activity = Activity(...), or Meta.activity = None '
                         'to turn it off (docs/activity.md)')
    for removed in ('tabs', 'default_tab'):
        if isinstance(meta, type) and removed in vars(meta):
            raise ValueError(f'{name}.Meta.{removed} was removed: record pages have no tabs. Lay the page out with '
                             'Meta.layout = (Main(fields), Main("lines"), Section(...), ...); Activity, Revisions and '
                             'Related show beside it or under "more information"')


def form_fields(model, meta):
    """The fields a form shows, in order: app fields only (instance fields, x_, differ per database and are added by
    Controls). The standard name shows first on models whose naming fills it; the standard title never shows."""
    named = getattr(getattr(meta, 'naming', None), 'field', None) == 'name' and standard(model.__app_fields__.get('name')) == 'name'
    visible = {name: field for name, field in model.__app_fields__.items()
               if (standard(field) == 'name' and named) or (not standard(field) and not field.hidden)}
    return {'name': visible.pop('name'), **visible} if named else visible


def view_for(model):
    meta = getattr(model, 'Meta', None)
    refuse_removed(model.__name__, meta)
    allowed = META_OPTIONS
    if meta is not None:
        if not isinstance(meta, type):
            raise ValueError(f'{model.__name__}.Meta must be a class: class Meta: ...')
        unknown = sorted(key for key in vars(meta) if not key.startswith('_') and key not in allowed)
        if unknown:
            import difflib
            close = difflib.get_close_matches(unknown[0], sorted(allowed), n=1)
            raise ValueError(f'{model.__name__}.Meta.{unknown[0]} is not a Meta option'
                             + (f'; did you mean {close[0]}?' if close else '; the options are ' + ', '.join(sorted(allowed))))
    visible = form_fields(model, meta)
    texts = [name for name, field in visible.items() if isinstance(field, Text) and not isinstance(field, LongText)]
    label = getattr(meta, 'label', label_for(model.__name__))
    plural = getattr(meta, 'plural', plural_for(label))
    description = getattr(meta, 'description', '')
    if any(not isinstance(value, str) for value in (label, plural, description)) or not label or not plural:
        raise ValueError(f'{model.__name__}.Meta: label and plural must be nonempty strings and description must be text, '
                         f'not label={label!r}, plural={plural!r}, description={description!r}')
    title = getattr(meta, 'title_field', next((name for name in ('name', 'title', 'reference') if name in texts), texts[0] if texts else 'id'))
    if title != 'id' and title not in texts:
        raise ValueError(f'{model.__name__}.Meta.title_field = {title!r}: use a visible short Text field '
                         f"({', '.join(texts) or 'none'}) or 'id'")
    if not hasattr(meta, 'title_field') and 'title' in model.__app_fields__ and (standard(model.__app_fields__['title']) or 'title' in texts):
        # Without Meta.title_field, records show their title column (a template, the name or the text
        # id). With it, that field holds the same text the title does.
        title = "title"
    # Without Meta.list_fields: the first four visible fields, leaving out those declared column=False.
    columns = getattr(meta, 'list_fields', tuple(name for name, field in visible.items()
                                                 if not isinstance(field, LongText) and getattr(field, 'column', True))[:4])
    search = getattr(meta, 'search_fields', tuple(texts[:8]))
    standard_text = {key for key, field in model.__app_fields__.items() if standard(field)}
    for value, maximum, names in ((columns, 8, {'id', *visible}), (search, 8, {name for name, field in visible.items() if isinstance(field, Text)} | standard_text)):
        if not isinstance(value, (tuple, list)) or len(value) > maximum or any(not isinstance(name, str) or name not in names for name in value) or len(set(value)) != len(value):
            option = 'list_fields' if value is columns else 'search_fields'
            raise ValueError(f'{model.__name__}.Meta.{option} = {value!r}: name at most {maximum} distinct visible '
                             f'fields, from {", ".join(sorted(names))}')
    # How a list opens its records: the full record page, or a side panel beside the narrowed list.
    open_in = getattr(meta, 'open_in', 'page')
    if open_in not in OPEN_IN:
        raise ValueError(f'{model.__name__}.Meta.open_in must be "page" or "panel", not {open_in!r}')
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
    blocks, _ = layout_blocks(model, getattr(meta, 'layout', None), visible, label)
    groups = [block['group'] for block in blocks if block['kind'] == 'section']
    # TableSection(panel=False) leaves its Table out of the side panel, as Table(panel=False) does.
    tables_off = {block['name'] for block in blocks if block['kind'] == 'table' and block.get('panel') is False}
    panel = panel_for(model, meta, groups, visible, tables_off)
    groups = [{key: value for key, value in group.items() if key != 'panel'} for group in groups]
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


def panel_for(model, meta, groups, visible, tables_off=()):
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
                  'tabs': [name for name, table in tables.items() if getattr(table, 'panel', True) is False or name in tables_off], 'blocks': [] if summary else ['summary']}
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
# The most fields a Related block lists as columns: as many as the app names, up to this.
RELATED_FIELDS = 12
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
    """The schema's record_layout and the warnings for what Meta.layout ignored.

    {"blocks": [...]}: every block in drawing order, each with its kind, its stable name and "main". Main blocks come
    first and never collapse; the rest are "more information". Sections are named by title (their fields stay in the
    schema's layout), a Table by its field name, a Related, Html or Chart by its label ("Notes" for Html, "Chart" for
    a chart without one), numbered from 2 when a name repeats. A block the registry cannot place (an unknown model,
    Link, filter or measure) is left out with a warning."""
    meta = getattr(model, 'Meta', None)
    blocks, warnings = layout_blocks(model, getattr(meta, 'layout', None), form_fields(model, meta), view_for(model)['label'])
    where = f'{model.__name__}.Meta.layout'
    names = {block['name'] for block in blocks if block['kind'] in ('section', 'custom')}
    result = []
    for block in blocks:
        if block['kind'] == 'section':
            result.append({'kind': 'section', 'name': block['name'], 'label': block['name'], 'main': block['main']})
        elif block['kind'] in ('table', 'custom'):
            result.append({'kind': block['kind'], 'name': block['name'], 'main': block['main'],
                           **({'label': block['name'], 'custom': block['custom']} if block['kind'] == 'custom' else {}),
                           **{key: block[key] for key in ('collapsible', 'collapsed') if key in block}})
        else:
            try:
                entry = layout_block(registry, model, block['kind'], block['block'], where)
            except ValueError as error:
                warnings.append(f'{where}: {block["block"]!r}: {error}; it is ignored')
                continue
            base = entry.get('label') or ('Notes' if block['kind'] == 'html' else 'Chart')
            name, number = base, 1
            while name in names:
                number += 1
                name = f'{base} {number}'
            names.add(name)
            result.append({**entry, 'name': name, 'main': False})
    return {'blocks': result}, warnings


def compile_layouts(registry):
    """({model: record_layout}, warnings) for every model; each warning, (model, text), is logged once here."""
    import logging
    result, warnings = {}, []
    for model in registry.models.values():
        result[model], problems = record_layout(registry, model)
        for text in problems:
            logging.getLogger('evlet.layout').warning('%s', text)
            warnings.append((model, text))
    return result, tuple(warnings)


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


def help_schema(field):
    """help_fields: the fields a reactive help text names ("per {uom}" -> ["uom"]). The browser fills them in from the
    record's values as they change, and a Table row shows it as subtext under the cell (docs/model-engine.md)."""
    from ..fields import help_references
    try:
        names = help_references(field.help_text)
    except ValueError:
        return {}
    return {'help_fields': list(names)} if names else {}


def field_schema(field):
    result = {**field.metadata(), 'label': field.label or label_for(field.name),
              'help_text': field.help_text, **help_schema(field), **({'tooltip': field.tooltip} if field.tooltip else {}), 'placeholder': field.placeholder,
              **({'suffix': field.suffix} if field.suffix else {}),
              # Integer(grouping=False): shown without thousands separators (display only).
              **({'grouping': False} if getattr(field, 'grouping', True) is False else {}),
              # Decimal(display_scale=2): shown with at most that many places, without trailing zeros (display only).
              **({'display_scale': field.display_scale} if getattr(field, 'display_scale', None) is not None else {}),
              # Forms leave out the standard title and a name no naming fills (evlet/naming.py); responses keep them.
              'hidden': field.hidden or form_hidden(field), 'read_only': field.read_only,
              **({'stored': field.stored} if field.compute is not None or field.query is not None or not field.stored else {}),
              **currency_schema(field),
              **({'versioned': False} if not getattr(field, 'versioned', True) else {}),
              # Duplicate leaves this value out of the new record (display only).
              **({'copy': False} if getattr(field, 'copy', True) is False else {}),
              # Left out of the default list and Table columns (display only).
              **({'column': False} if getattr(field, 'column', True) is False else {}),
              **({'sequence': field.schema()} if field.kind == 'sequence' else {}),
              # The model script's hooks on this field and its fixed look (docs/model-scripts.md).
              **({'script': dict(field.hooks)} if getattr(field, 'hooks', None) else {}),
              **({'look': field.look} if getattr(field, 'look', None) else {}),
              # The form paths a Link's Option reads (Ref in where=): the dropdown sends their values (evlet/search.py).
              **({'inputs': list(field.option.refs())} if getattr(field, 'option', None) is not None and field.option.refs() else {}),
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
         **({'script': registry.scripts[model]} if model in getattr(registry, 'scripts', {}) else {}),
         **({'extensions': [entry for _, entry in registry.extensions[model] if entry]} if any(entry for _, entry in getattr(registry, 'extensions', {}).get(model, ())) else {}),
         'fields': [field_schema(field) for field in model.__app_fields__.values()],
         **({'api': 'read'} if api.get(model) == 'read' else {}), **behavior(model, registry)}
        for name, model in registry.models.items() if api.get(model, 'full') is not None
    ]}


def behavior(model, registry):
    from ..reactivity import plans, serialized, graph, map_filters
    if not getattr(model, '__declaration_mode__', False):
        # A plain model still tells the form which other fields its Link filters read.
        filters = {name: {'filters': map_filters(entry['filters'], serialized)}
                   for name, entry in plans(model).items() if entry.get('filters')}
        return {'behavior': filters} if filters else {}
    nodes, order = ({}, []) if any(child.model is model for child in registry.children) else graph(registry, model)
    fields = plans(model)
    return {'document_version': 1, 'resource': getattr(model, '__resource_id__', registry.name_for(model)),
            'behavior': {name: {key: map_filters(item, serialized) if key == 'filters' else serialized(item)
                                for key, item in entry.items()} for name, entry in fields.items()},
            'order': order, 'sources': source_schema(model)}


def source_schema(model):
    return {name: {key: {'type': field.kind, **({'scale': field.scale} if hasattr(field, 'scale') else {})} for key, field in source.fields.items()}
            for name, source in model.__sources__.items()}
