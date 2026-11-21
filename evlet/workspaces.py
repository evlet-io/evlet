"""Workspaces: an app's named sidebars, Workspace > NavSection > NavGroup > items (docs/ui.md#workspaces).

A NavSection is a bold title that never collapses; under it a NavGroup is a parent with a coloured icon and its
child items, and a Shortcut placed directly in a section is a parent that is a link. An optional Home sits above
every section. An app declares them in workspaces.py (or passes Application(workspaces=...)). They are checked when the
application is built, and /api/schema gives each role only what it can open. An app that declares none
gets one automatic workspace, grouped as the sidebar groups models: Documents, Records and Reports."""

from dataclasses import dataclass, field
import json
import re
from urllib.parse import urlsplit

from . import qb
from .auth import AccessError
from .models import ValidationError
from .query import ChildGroup, SqlQuery, conditions

KEY = re.compile(r'[a-z][a-z0-9_]{0,63}')
ICON = re.compile(r'[a-z0-9][a-z0-9-]{0,39}')
VIEWS = ('list', 'board')
MAX_LABEL = 80
MAX_URL = 2000
# The colours a parent may take: the browser's seven tints. Its children take the parent's colour.
COLORS = ('blue', 'green', 'amber', 'rose', 'violet', 'teal', 'slate')
# A badge reads at most the newest BADGE_WINDOW rows of its model and stops at BADGE_CAP matches, which the
# browser shows as "99+": never an unbounded count over a large table.
BADGE_WINDOW = 1000
BADGE_CAP = 100
# Home's key: no workspace, section, group or item may take it, and no model may be named so (Registry).
HOME = 'home'


@dataclass(frozen=True, repr=False)
class Shortcut:
    """One sidebar entry. Name exactly one target: a model (with an optional view and filters), a report, an @api
    endpoint whose Prompt opens (api='app.quick_expense', with a label) or a URL.

    icon replaces the kind's icon. color is for a parent link only (a Shortcut placed directly in a NavSection):
    a NavGroup's children take its colour. badge is a filter whose bounded count shows beside a model child.
    new_tab opens a URL in a new tab, with the open-externally icon; it defaults to True for an http(s) address."""
    model: object = None
    view: str | None = None
    filters: object = None
    label: str | None = None
    report: object = field(default=None, kw_only=True)
    api: str | None = field(default=None, kw_only=True)
    url: str | None = field(default=None, kw_only=True)
    icon: str | None = field(default=None, kw_only=True)
    color: str | None = field(default=None, kw_only=True)
    badge: object = field(default=None, kw_only=True)
    new_tab: bool | None = field(default=None, kw_only=True)

    def __repr__(self):
        shown = [f'{name}={_shown(getattr(self, name))}' for name in ('model', 'view', 'filters', 'label', 'report', 'api', 'url',
                                                                     'icon', 'color', 'badge', 'new_tab')
                 if getattr(self, name) is not None]
        return f'Shortcut({", ".join(shown)})'


@dataclass(frozen=True)
class NavGroup:
    """A parent in the sidebar: its icon on a tile in its colour, then its child items (model names and Shortcuts),
    which take the same colour. Without a colour, the browser picks a stable one from the key."""
    label: str
    icon: str | None = field(default=None, kw_only=True)
    color: str | None = field(default=None, kw_only=True)
    items: tuple = field(default=(), kw_only=True)

    def __post_init__(self):
        if isinstance(self.items, (list, tuple)):
            object.__setattr__(self, 'items', tuple(self.items))


@dataclass(frozen=True)
class NavSection:
    """A level-one sidebar title, bold and in capitals, that never collapses and has no icon. Its items are
    NavGroups (parents with children) and parent links: model names or Shortcuts."""
    label: str
    items: tuple = field(default=(), kw_only=True)

    def __post_init__(self):
        if isinstance(self.items, (list, tuple)):
            object.__setattr__(self, 'items', tuple(self.items))


@dataclass(frozen=True)
class Workspace:
    """A named sidebar: NavSections, NavGroups and parent links. key defaults to the label in lowercase with
    underscores. home=True puts Home directly under the switcher, opening the start page; a model name or a
    Shortcut makes it open that instead."""
    label: str
    key: str | None = field(default=None, kw_only=True)
    icon: str | None = field(default=None, kw_only=True)
    groups: tuple = field(default=(), kw_only=True)
    home: object = field(default=None, kw_only=True)

    def __post_init__(self):
        if isinstance(self.groups, (list, tuple)):
            object.__setattr__(self, 'groups', tuple(self.groups))
        name = f'Workspace {self.label!r}'
        _label(self.label, name)
        key = slug(self.label) if self.key is None else self.key
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise ValueError(f'{name}: key must be 1 to 64 lowercase letters, digits or underscores, starting with a letter'
                             + (', so give it key=' if self.key is None else f', not {self.key!r}'))
        if key == HOME:
            raise ValueError(f'{name}: the key "home" is reserved for Home; give it another key')
        object.__setattr__(self, 'key', key)
        _icon(self.icon, name)
        if not isinstance(self.groups, tuple) or not self.groups:
            raise ValueError(f'{name}: groups takes one or more NavSection or NavGroup')
        if self.home is not None and self.home is not True:
            if isinstance(self.home, Shortcut) and (self.home.icon, self.home.color, self.home.badge) != (None, None, None):
                raise ValueError(f'{name}, home: Home has its own icon, and takes no colour or badge')
            if self.home is False or not isinstance(self.home, (str, Shortcut)):
                raise ValueError(f'{name}, home: use True, a model name or a Shortcut')
            check_item(self.home, f'{name}, home')
        seen, sections = set(), set()
        for entry in self.groups:
            if isinstance(entry, NavSection):
                where = f'{name}, section {entry.label!r}'
                _label(entry.label, where)
                _not_home(entry.label, where)
                if slug(entry.label) in sections:
                    raise ValueError(f'{where}: two sections share the key {slug(entry.label)!r}; give them different labels')
                sections.add(slug(entry.label))
                if not isinstance(entry.items, tuple) or not entry.items:
                    raise ValueError(f'{where}: items takes one or more NavGroups, model names or Shortcuts')
                for item in entry.items:
                    self._parent(item, where, seen)
            elif isinstance(entry, NavGroup):
                self._parent(entry, name, seen)
            else:
                raise ValueError(f'{name}: groups takes NavSection and NavGroup, not {_shown(entry)}')

    def _parent(self, entry, where, seen):
        """A NavGroup with its children, or a parent link (a model name or a Shortcut)."""
        if not isinstance(entry, NavGroup):
            if isinstance(entry, NavSection):
                raise ValueError(f'{where}: a NavSection holds NavGroups and links, not another NavSection')
            check_item(entry, f'{where}, item {_shown(entry)}', parent=True)
            return
        group = entry
        where = f'{where}, group {group.label!r}'
        _label(group.label, where)
        _not_home(group.label, where)
        if slug(group.label) in seen:
            raise ValueError(f'{where}: two groups share the key {slug(group.label)!r}; give them different labels')
        seen.add(slug(group.label))
        _icon(group.icon, where)
        _color(group.color, where)
        if not isinstance(group.items, tuple) or not group.items:
            raise ValueError(f'{where}: items takes one or more model names or Shortcuts')
        for item in group.items:
            check_item(item, f'{where}, item {_shown(item)}', child=True)


def slug(text):
    """A label as a key: lowercase words joined by underscores ('Open tasks' -> 'open_tasks')."""
    return re.sub(r'\W+', '_', text.strip().casefold()).strip('_') if isinstance(text, str) else ''


def _shown(value):
    return value.__name__ if isinstance(value, type) else repr(value)


def _label(value, where, *, name='label'):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_LABEL:
        raise ValueError(f'{where}: {name} must be text of 1 to {MAX_LABEL} characters')


def _not_home(label, where):
    if slug(label) == HOME:
        raise ValueError(f'{where}: "Home" is reserved for the workspace\'s Home (Workspace(home=...))')


def _color(value, where):
    if value is not None and value not in COLORS:
        raise ValueError(f'{where}: color is one of {", ".join(COLORS)}')


def _icon(value, where):
    if value is not None and (not isinstance(value, str) or not ICON.fullmatch(value)):
        raise ValueError(f'{where}: icon is an icon name such as "receipt" (lowercase letters, digits and hyphens)')


def check_url(url):
    """A relative app path (/reports) or an http(s) address; javascript:, data: and every other scheme are refused."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL or any(ord(char) < 33 or char == '\\' or ord(char) == 127 for char in url):
        return False
    if url.startswith('/'):
        return not url.startswith('//')
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in ('http', 'https') and bool(parts.netloc)


def _filter_value(value, where, name):
    if not isinstance(value, (dict, list, tuple)) or not value:
        raise ValueError(f'{where}: {name} is a filter object such as {{"done": False}} or a list of conditions')
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError(f'{where}: {"filter" if name == "filters" else "badge filter"} values are JSON values: text, numbers, true, false, null or lists') from None


def check_item(item, where, *, parent=False, child=False):
    """Checks that need no registry: one target, a known view, JSON filters, labels and URLs. A parent link
    (parent=True) may take a colour but no badge; a NavGroup's child (child=True) a badge but no colour."""
    if isinstance(item, str):
        if not item:
            raise ValueError(f'{where}: name a model')
        return
    if not isinstance(item, Shortcut):
        raise ValueError(f'{where}: an item is a model name or a Shortcut')
    targets = [name for name in ('model', 'report', 'api', 'url') if getattr(item, name) is not None]
    if len(targets) != 1:
        raise ValueError(f'{where}: name exactly one of model, report, api or url')
    if item.label is not None:
        _label(item.label, where)
        if parent or child:
            _not_home(item.label, where)
    if targets[0] != 'model' and (item.view is not None or item.filters is not None):
        raise ValueError(f'{where}: view and filters go with a model')
    if item.view is not None and item.view not in VIEWS:
        raise ValueError(f'{where}: view is "list" or "board", not {item.view!r}')
    if item.filters is not None:
        _filter_value(item.filters, where, 'filters')
        if item.label is None:
            raise ValueError(f'{where}: a shortcut with filters needs a label')
    _icon(item.icon, where)
    if item.color is not None:
        if child:
            raise ValueError(f'{where}: a child takes its parent\'s colour; set color on the NavGroup')
        if not parent:
            raise ValueError(f'{where}: color goes on a parent: a NavGroup, or a Shortcut placed directly in a NavSection')
        _color(item.color, where)
    if item.badge is not None:
        if targets[0] != 'model':
            raise ValueError(f'{where}: a badge counts a model\'s records; it goes with a model')
        if not child:
            raise ValueError(f'{where}: parents show no badge; put the badge on a child in a NavGroup')
        _filter_value(item.badge, where, 'badge')
    if item.new_tab is not None:
        if targets[0] != 'url':
            raise ValueError(f'{where}: new_tab goes with a url')
        if type(item.new_tab) is not bool:
            raise ValueError(f'{where}: new_tab is True or False')
    if item.url is not None:
        if not check_url(item.url):
            raise ValueError(f'{where}: a URL is an app path starting with / or an http(s) address')
        if item.label is None:
            raise ValueError(f'{where}: a URL shortcut needs a label')
    if item.api is not None and (not isinstance(item.api, str) or item.label is None):
        raise ValueError(f'{where}: an api shortcut names an @api endpoint and needs a label')
    for name in ('model', 'report'):
        value = getattr(item, name)
        if value is not None and not isinstance(value, (str, type)):
            raise ValueError(f'{where}: {name} takes a name or the class')


def model_item(name, label=None, view=None, filters=None, *, icon=None, badge=None):
    """A model item's plain form; its key is model:<name>, plus :<label or view> when it is more than the list.
    badge is the checked badge filter, kept on the server: the schema says only whether there is one."""
    plain = label is None and filters is None and view in (None, 'list')
    return {'key': f'model:{name}' if plain else f'model:{name}:{slug(label) if label else view}', 'kind': 'model',
            'label': label, 'model': name, 'view': view or 'list', 'filters': filters, 'icon': icon, 'badge': badge}


class Workspaces:
    """The checked workspaces of an application, and each role's view of them in /api/schema."""

    def __init__(self, business, declared=(), *, name='Evlet'):
        self.business = business
        # One app for an Application built by hand; build_instance replaces it with the installed apps (load).
        self.apps = [{'id': 'app', 'title': name, 'workspaces': tuple(declared), 'models': None, 'reports': None,
                      'source': 'Application(workspaces=...)'}]
        self.plans = self.compile()
        # Site overrides by workspace key, checked and compiled (load_overrides, from Controls).
        self.overridden = {}

    def load(self, definitions, packages=()):
        """The installed apps, from build_instance: each app's declared workspaces, or its automatic one."""
        registry, titles = self.business.registry, {package.name: getattr(package, 'title', '') for package in packages}
        layouts = {package.name: getattr(package, 'layout', None) for package in packages}
        # A flat app's workspaces come from its workspaces.py; a legacy app's only from its AppDefinition.
        self.apps = [{'id': name, 'title': titles.get(name) or name.replace('_', ' ').title(), 'workspaces': tuple(definition.workspaces),
                      'models': {registry.name_for(model) for model in definition.models},
                      'reports': {getattr(report, '__resource_id__', report.__name__) for report in definition.reports},
                      'source': f'app {name} ({name}/workspaces.py)' if layouts.get(name) == 'flat' else f'app {name} (AppDefinition)'}
                     for name, definition in definitions]
        self.plans = self.compile()

    # ---------- registration ----------

    def compile(self):
        plans, keys = [], {}
        automatic = {app['id'] for app in self.apps if not app['workspaces']}
        for app in self.apps:
            for workspace in app['workspaces']:
                if not isinstance(workspace, Workspace):
                    raise ValueError(f'{app["id"]}: workspaces takes Workspace definitions, not {_shown(workspace)}')
                if workspace.key in keys and keys[workspace.key][0] is workspace and keys[workspace.key][1] is app:
                    continue  # The same Workspace listed twice by one app is registered once.
                if workspace.key in keys or workspace.key in automatic:
                    other = (f'workspace {keys[workspace.key][0].label!r} in {keys[workspace.key][1]["source"]}' if workspace.key in keys
                             else 'the automatic workspace of app ' + workspace.key)
                    raise ValueError(f'Workspace {workspace.label!r} in {app["source"]}: the key {workspace.key!r} is taken by {other}')
                keys[workspace.key] = (workspace, app)
                plans.append({'key': workspace.key, 'label': workspace.label.strip(), 'icon': workspace.icon, 'app': app['id'],
                              'source': 'declared', 'home': self.home(workspace), 'groups': self.groups(workspace)})
            if not app['workspaces']:
                plans.append({'key': app['id'], 'label': app['title'], 'icon': 'layers', 'app': app['id'], 'source': 'automatic',
                              'home': None, 'models': app['models'], 'reports': app['reports']})
        return plans

    def home(self, workspace):
        """Home's item: the start page (home=True), or the model, report, endpoint or URL it names, keyed "home"."""
        if workspace.home is None:
            return None
        if workspace.home is True:
            return {'key': HOME, 'kind': HOME, 'label': 'Home', 'icon': None}
        plan = self.item(workspace.home, f'Workspace {workspace.label!r}, home')
        return {**plan, 'key': HOME, 'label': plan['label'] or 'Home', 'badge': None} if plan['kind'] == 'model' else {**plan, 'key': HOME,
                                                                                                                     'label': plan['label'] or 'Home'}

    def groups(self, workspace):
        """The workspace's parents in order: each NavGroup, and each parent link as a group of its one item
        ("link": true). Each says the section above it ({key, label}, or None outside a section)."""
        groups, seen, keys = [], {}, set()
        for entry in workspace.groups:
            section = {'key': slug(entry.label), 'label': entry.label.strip()} if isinstance(entry, NavSection) else None
            for parent in (entry.items if section else (entry,)):
                if isinstance(parent, NavGroup):
                    items = [self.planned(item, f'Workspace {workspace.label!r}, group {parent.label!r}, item {_shown(item)}', seen)
                             for item in parent.items]
                    group = {'key': slug(parent.label), 'label': parent.label.strip(), 'icon': parent.icon, 'color': parent.color,
                             'section': section, 'link': False, 'items': items}
                else:
                    where = f'Workspace {workspace.label!r}' + (f', section {section["label"]!r}' if section else '') + f', item {_shown(parent)}'
                    item = self.planned(parent, where, seen)
                    color = parent.color if isinstance(parent, Shortcut) else None
                    group = {'key': slug(item['key']), 'label': item['label'], 'icon': item['icon'], 'color': color,
                             'section': section, 'link': True, 'items': [item]}
                    if group['key'] in keys:
                        raise ValueError(f'{where}: the key {group["key"]!r} is used by another group; give one a different label')
                keys.add(group['key'])
                groups.append(group)
        return groups

    def planned(self, item, where, seen):
        plan = self.item(item, where)
        if plan['key'] in seen:
            raise ValueError(f'{where}: the key {plan["key"]!r} is used by {seen[plan["key"]]}; give one a different label')
        seen[plan['key']] = _shown(item)
        return plan

    def item(self, item, where):
        """An item in its plain form, checked against the models, reports and endpoints of this application."""
        if isinstance(item, str):
            return model_item(self.model(item, where))
        if item.model is not None:
            name = self.model(item.model, where)
            model = self.business.registry.models[name]
            if item.view == 'board' and not getattr(getattr(model, 'Meta', None), 'board', None):
                raise ValueError(f'{where}: {model.__name__} has no board; set Meta.board to its State, or use view="list"')
            return model_item(name, item.label, item.view, None if item.filters is None else self.filters(model, item.filters, where),
                              icon=item.icon, badge=None if item.badge is None else self.filters(model, item.badge, where, 'badge'))
        if item.report is not None:
            reports = self.business.forms.reports if self.business.forms else {}
            name = getattr(item.report, '__resource_id__', item.report.__name__) if isinstance(item.report, type) else item.report
            if name not in reports:
                raise ValueError(f'{where}: unknown report {name!r}; register it with the application')
            return {'key': f'report:{name}', 'kind': 'report', 'label': item.label, 'report': name, 'icon': item.icon}
        if item.api is not None:
            if not any(endpoint.name == item.api for endpoint in self.business.apis):
                raise ValueError(f'{where}: unknown endpoint {item.api!r}; register it with the application')
            return {'key': f'api:{item.api}', 'kind': 'api', 'label': item.label.strip(), 'api': item.api, 'icon': item.icon}
        new_tab = item.new_tab if item.new_tab is not None else not item.url.startswith('/')
        return {'key': f'url:{slug(item.label)}', 'kind': 'url', 'label': item.label.strip(), 'url': item.url, 'icon': item.icon, 'new_tab': new_tab}

    def model(self, value, where):
        registry, business = self.business.registry, self.business
        try:
            model = registry.resolve(value)
        except ValueError:
            raise ValueError(f'{where}: unknown model {_shown(value)}') from None
        if registry.api.get(model) is None:
            raise ValueError(f'{where}: {model.__name__} has no screens (a ledger model or Meta.api = None)')
        if model in business.child_models:
            raise ValueError(f'{where}: {model.__name__} holds child rows, which open from their parent record')
        return registry.name_for(model)

    def filters(self, model, filters, where, name='filters'):
        """Filters checked as a list request checks them, in the list form a list address carries (?f=)."""
        try:
            items = conditions(filters)
            SqlQuery(self.business.registry, model, children=self.business.children.values()).where(filters)
        except ValidationError as exc:
            raise ValueError(f'{where}: {name} {"; ".join(f"{key}: {value}" for key, value in exc.errors.items())}') from None
        result = []
        for item in items:
            paths = [inner.path for inner in item.where] if isinstance(item, ChildGroup) else [item.path]
            if any('__' in path for path in paths):
                raise ValueError(f'{where}: {name} name fields of {model.__name__} itself, not of linked records')
            if isinstance(item, ChildGroup):
                result.append({'child': item.child, 'match': item.match, 'where': [[inner.path, inner.operator, inner.value] for inner in item.where]})
            else:
                result.append([item.path, item.operator, item.value])
        return result

    # ---------- each role ----------

    def schema(self, user, result):
        """The workspaces this role sees: declared, then the site's overrides, then what the role can open. Items it
        can't open are left out, then empty groups and workspaces, so an override never widens access."""
        return self.view(user, result, overrides=True)

    def view(self, user, result, *, overrides):
        context = Context(self.business, user, result)
        workspaces = []
        for plan in self.effective() if overrides else self.plans:
            override = self.overridden.get(plan['key']) if overrides else None
            groups = context.automatic(plan) if plan['source'] == 'automatic' else plan['groups']
            if override is not None:
                groups = apply_override(groups, override)
            groups = [shown for shown in (context.group(group) for group in groups) if shown is not None]
            if groups:
                home = None if plan.get('home') is None else context.item(plan['home'])
                workspaces.append({'key': plan['key'], 'label': (override or {}).get('label') or plan['label'],
                                   'icon': (override or {}).get('icon') or plan['icon'], 'app': plan['app'],
                                   'source': plan['source'], 'home': home, 'groups': groups})
        return workspaces

    # ---------- badges ----------

    def badges(self, scope, user):
        """GET /api/_nav/badges?workspace=<key>: the bounded counts of the badges this role sees in that workspace.
        Reads only, in one snapshot. An item the role can't open, or whose badge it can't filter by, has none."""
        from urllib.parse import parse_qs
        try:
            raw = scope.get('query_string', b'')
            query = parse_qs(raw.decode('ascii'), keep_blank_values=True, max_num_fields=2) if len(raw) <= 200 else None
            if query is None or set(query) != {'workspace'} or len(query['workspace']) != 1:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValidationError({'query': 'Send the workspace key: ?workspace=<key>'}) from None
        key = query['workspace'][0]
        shown = next((workspace for workspace in self.business.schema(user).get('workspaces', ()) if workspace['key'] == key), None)
        if shown is None:
            raise AccessError(404, 'Workspace not found')
        plan = next((plan for plan in self.plans if plan['key'] == key and plan['source'] == 'declared'), None)
        declared = {item['key']: item for group in (plan or {}).get('groups', ()) for item in group['items']}
        wanted = [declared[item['key']] for group in shown['groups'] for item in group['items']
                  if item.get('badge') and declared.get(item['key'], {}).get('badge') is not None]
        counts = {}
        if wanted:
            database = self.business.database
            with database._connection() as connection:
                connection.snapshot()
                for item in wanted:
                    counts[item['key']] = self.bounded(connection, user, item)
        return 200, {'badges': counts}, []

    def bounded(self, connection, user, item):
        """{count, more, partial} for one badge: the rows among the newest BADGE_WINDOW of the model that this role
        reads (its row scope) and that meet the shortcut's filters and the badge filter, counted up to BADGE_CAP.
        more: BADGE_CAP or more matched. partial: fewer did, and the model holds more rows than the window."""
        business = self.business
        model = business.registry.models[item['model']]
        compiler = SqlQuery(business.registry, model, children=business.children.values())
        where = [condition for condition in (compiler.where(item['filters']), compiler.where(item['badge']), business.policy.scope(user, model))
                 if not qb.always(condition)]
        name = business.registry.name_for(model)
        window = qb.from_query(qb.select(qb.table(name)).order_by(qb.desc('id')).limit(BADGE_WINDOW), 'record')
        hits = qb.select(window, qb.col('record.id')).where(*where).limit(BADGE_CAP)
        count = int(connection.run(qb.select(qb.from_query(hits, 'hits'), qb.count())).value() or 0)
        partial = False
        if count < BADGE_CAP:
            rows = qb.select(qb.table(name), qb.col('id')).limit(BADGE_WINDOW + 1)
            partial = int(connection.run(qb.select(qb.from_query(rows, 'rows'), qb.count())).value() or 0) > BADGE_WINDOW
        return {'count': count, 'more': count >= BADGE_CAP, 'partial': partial}

    def effective(self):
        """The declared and automatic workspaces, then the site's custom ones by key."""
        return [*self.plans, *({'key': key, 'label': value['label'], 'icon': value['icon'], 'app': None, 'source': 'custom', 'home': None, 'groups': []}
                               for key, value in sorted(self.overridden.items()) if value['custom'])]

    # ---------- site overrides (Controls) ----------

    def load_overrides(self, rows, problems):
        """Stored overrides (evlet_overrides kind "workspace"), from Controls on every config change. One that no
        longer validates is left out and listed under Needs attention, so the declared workspace shows instead."""
        overridden = {}
        for row in rows:
            try:
                overridden[row['key']] = self.check_override(row['key'], json.loads(row['value']))[1]
            except (ValueError, TypeError) as exc:
                text = '; '.join(f'{key}: {value}' for key, value in exc.errors.items()) if isinstance(exc, ValidationError) else str(exc)
                problems.append({'kind': 'workspace', 'key': row['key'],
                                 'error': f'{text}. The workspace shows as declared until this is fixed or reset.'})
        self.overridden = overridden

    def known(self, plan):
        """{group key: label} and {item key: True} an override of this workspace may refer to."""
        if plan['source'] == 'declared':
            return ({group['key']: group['label'] for group in plan['groups']},
                    {item['key'] for group in plan['groups'] for item in group['items']})
        registry, business = self.business.registry, self.business
        models = {name for name, model in registry.exposed.items() if model not in business.child_models
                  and (plan['models'] is None or name in plan['models'])}
        reports = {name for name in getattr(getattr(business, 'forms', None), 'reports', {}) if plan['reports'] is None or name in plan['reports']}
        return ({'documents': 'Documents', 'records': 'Records', 'reports': 'Reports'},
                {f'model:{name}' for name in models} | {f'report:{name}' for name in reports})

    def check_override(self, key, value):
        """(value to store, compiled value) of one workspace override; ValidationError names each problem by its path."""
        if not isinstance(value, dict):
            raise ValidationError({'_form': 'Send an object with label, icon and groups'})
        custom = value.get('custom', False)
        if type(custom) is not bool or set(value) - {'custom', 'label', 'icon', 'groups'}:
            raise ValidationError({'_form': 'Use custom, label, icon and groups'})
        plan = next((plan for plan in self.plans if plan['key'] == key), None)
        if custom:
            if not isinstance(key, str) or not KEY.fullmatch(key):
                raise ValidationError({'key': 'Use 1 to 64 lowercase letters, digits or underscores, starting with a letter'})
            if key == HOME:
                raise ValidationError({'key': 'The key home is reserved for Home'})
            if plan is not None:
                raise ValidationError({'key': f'The workspace {plan["label"]} has this key. Choose another key for a new workspace'})
            if 'label' not in value or not value.get('groups'):
                raise ValidationError({'_form': 'A new workspace needs a label and at least one group'})
            groups_known, items_known = {}, set()
        elif plan is None:
            raise ValidationError({'key': 'No workspace has this key. Send "custom": true to add one'})
        else:
            groups_known, items_known = self.known(plan)
        errors, compiled = {}, {'custom': custom, 'label': None, 'icon': None, 'groups': []}
        for name in ('label', 'icon'):
            if name in value:
                try:
                    (_label if name == 'label' else _icon)(value[name], 'This workspace')
                    compiled[name] = value[name].strip() if name == 'label' else value[name]
                except ValueError as exc:
                    errors[name] = str(exc).removeprefix('This workspace: ')
        groups = value.get('groups', [])
        if not isinstance(groups, list) or len(groups) > 50:
            raise ValidationError({**errors, 'groups': 'Send a list of at most 50 groups'})
        seen_groups, seen_items = set(), set()
        where = f'Workspace {key!r}'
        for index, group in enumerate(groups):
            path = f'groups.{index}'
            if not isinstance(group, dict) or set(group) - {'key', 'label', 'icon', 'hidden', 'items'} or not isinstance(group.get('key'), str):
                errors[path] = 'Send key, with label, icon, hidden or items'
                continue
            gkey = group['key']
            if gkey in seen_groups:
                errors[path + '.key'] = 'Name each group once'
            seen_groups.add(gkey)
            if gkey not in groups_known:
                if not re.fullmatch(r'\w{1,64}', gkey):
                    errors[path + '.key'] = 'A new group key is 1 to 64 letters, digits or underscores'
                elif gkey == HOME:
                    errors[path + '.key'] = 'The key home is reserved for Home'
                if 'label' not in group:
                    errors[path + '.label'] = 'A new group needs a label'
            entry = {'key': gkey, 'label': None, 'icon': None, 'hidden': False, 'items': []}
            for name in ('label', 'icon'):
                if name in group:
                    try:
                        (_label if name == 'label' else _icon)(group[name], 'This group')
                        entry[name] = group[name].strip() if name == 'label' else group[name]
                    except ValueError as exc:
                        errors[f'{path}.{name}'] = str(exc).removeprefix('This group: ')
            if 'hidden' in group:
                if type(group['hidden']) is not bool:
                    errors[path + '.hidden'] = 'Use true or false'
                entry['hidden'] = group['hidden'] is True
            items = group.get('items', [])
            if not isinstance(items, list) or len(items) > 200:
                errors[path + '.items'] = 'Send a list of at most 200 items'
                items = []
            for position, spec in enumerate(items):
                ipath = f'{path}.items.{position}'
                if not isinstance(spec, dict) or set(spec) - {'key', 'shortcut', 'label', 'hidden'} or ('key' in spec) == ('shortcut' in spec):
                    errors[ipath] = 'Send key or shortcut, with label or hidden'
                    continue
                item = None
                if 'key' in spec:
                    if not isinstance(spec['key'], str) or spec['key'] not in items_known:
                        errors[ipath + '.key'] = 'No item of this workspace has this key' if not custom else 'A new workspace holds shortcuts only'
                        continue
                    ikey = spec['key']
                else:
                    shortcut = spec['shortcut']
                    allowed = {'model', 'view', 'filters', 'label', 'report', 'api', 'url'}
                    if not isinstance(shortcut, dict) or set(shortcut) - allowed or any(
                            name in shortcut and not isinstance(shortcut[name], str) for name in allowed - {'filters'}):
                        errors[ipath + '.shortcut'] = 'Send model, view, filters, label, report, api or url, as text (filters as JSON)'
                        continue
                    try:
                        made = Shortcut(**shortcut)
                        label = f'{where}, group {gkey!r}, item {made!r}'
                        check_item(made, label)
                        item = self.item(made, label)
                    except ValueError as exc:
                        errors[ipath + '.shortcut'] = str(exc)
                        continue
                    ikey = item['key']
                    if ikey in items_known:
                        errors[ipath + '.shortcut'] = f'An item of this workspace has the key {ikey}; give the shortcut a different label'
                        continue
                if ikey in seen_items:
                    errors[ipath] = f'Use each item once ({ikey})'
                seen_items.add(ikey)
                shown = {'key': ikey, 'item': item, 'label': None, 'hidden': False}
                if 'label' in spec:
                    try:
                        _label(spec['label'], 'This item')
                        shown['label'] = spec['label'].strip()
                    except ValueError as exc:
                        errors[ipath + '.label'] = str(exc).removeprefix('This item: ')
                if 'hidden' in spec:
                    if type(spec['hidden']) is not bool:
                        errors[ipath + '.hidden'] = 'Use true or false'
                    shown['hidden'] = spec['hidden'] is True
                entry['items'].append(shown)
            compiled['groups'].append(entry)
        if errors:
            raise ValidationError(errors)
        return value, compiled


def apply_override(groups, override):
    """Groups after a site override: its groups first, in its order, each with its items in its order and then its
    other declared items; declared groups it doesn't list follow. An item it places anywhere leaves its own group.
    Hidden groups and items, and keys this role's groups don't hold, are left out."""
    declared = {item['key']: item for group in groups for item in group['items']}
    placed = {entry['key'] for group in override['groups'] for entry in group['items']}
    by_key = {group['key']: group for group in groups}
    result = []
    for entry in override['groups']:
        base = by_key.get(entry['key'])
        if entry['hidden']:
            continue
        items = []
        for spec in entry['items']:
            item = spec['item'] if spec['item'] is not None else declared.get(spec['key'])
            if item is None or spec['hidden']:
                continue
            items.append({**item, 'label': spec['label']} if spec['label'] else item)
        if base is not None:
            items += [item for item in base['items'] if item['key'] not in placed]
        result.append({'key': entry['key'], 'label': entry['label'] or (base['label'] if base else entry['key']),
                       'icon': entry['icon'] or (base['icon'] if base else None), 'color': base.get('color') if base else None,
                       'section': base.get('section') if base else None, 'link': bool(base and base.get('link')), 'items': items})
    listed = {entry['key'] for entry in override['groups']}
    result += [{**group, 'items': [item for item in group['items'] if item['key'] not in placed]} for group in groups if group['key'] not in listed]
    return result


class Context:
    """What one role can open, read from its already filtered schema."""

    def __init__(self, business, user, result):
        self.business, self.user = business, user
        self.models = {entry['name']: entry for entry in result['models'] if not entry['child_only']}
        self.order = list(self.models)
        self.reports = {entry['name']: entry for entry in result.get('reports', ())}
        self.apis = {endpoint.name: endpoint for endpoint in getattr(business, 'apis', ())}

    def group(self, group):
        """A group as this role sees it, or None when it is left with no items. A parent link takes its item's
        label (the role's plural for a model), and stays a link only while it holds just that item."""
        items = [shown for shown in (self.item(item) for item in group['items']) if shown is not None]
        if not items:
            return None
        link = group.get('link', False) and len(items) == 1
        return {'key': group['key'], 'label': group['label'] or items[0]['label'],
                'icon': group['icon'], 'color': group.get('color'), 'section': group.get('section'), 'link': link, 'items': items}

    def readable(self, model, filters):
        """Whether this role may filter the model by these filters (a list request's check)."""
        try:
            self.business.policy.query_fields(self.user, model, filters, (), database=self.business.database,
                                              children=self.business.children.values())
        except (AccessError, ValidationError):
            return False
        return True

    def item(self, item):
        kind = item['kind']
        if kind == 'model':
            entry = self.models.get(item['model'])
            if entry is None or item['view'] == 'board' and not (entry.get('state') and entry.get('board') == entry['state']['field']):
                return None
            model = self.business.registry.models[item['model']]
            if item['filters'] is not None and not self.readable(model, item['filters']):
                return None
            # The badge filter stays on the server; a role that can't filter by it sees the item without a badge.
            badge = item.get('badge') is not None and self.readable(model, item['badge'])
            return {**item, 'label': item['label'] or entry['plural'], 'icon': item.get('icon'), 'badge': badge}
        if kind == 'report':
            entry = self.reports.get(item['report'])
            return None if entry is None else {**item, 'label': item['label'] or entry['label']}
        if kind == 'api':
            endpoint = self.apis.get(item['api'])
            model = None if endpoint is None else self.business.registry.resolve(endpoint.permission[0])
            return dict(item) if model is not None and self.business.policy.allows(self.user, model, endpoint.permission[1]) else None
        return dict(item)

    def automatic(self, plan):
        """As the sidebar groups an app's models: Documents (a State or child rows), Records, then its Reports."""
        names = [name for name in self.order if plan['models'] is None or name in plan['models']]
        documents = [name for name in names if self.models[name].get('state') or self.models[name].get('children')]
        records = [name for name in names if name not in documents]
        reports = [entry for entry in self.reports.values() if plan['reports'] is None or entry['name'] in plan['reports']]
        # Reports without a group first, then by group in declaration order, as the sidebar lists them.
        first = {}
        for index, entry in enumerate(reports):
            first.setdefault(entry['group'], index)
        reports.sort(key=lambda entry: (1 if entry['group'] else 0, first[entry['group']]))
        model = lambda name: {**model_item(name), 'label': self.models[name]['plural']}
        plain = {'color': None, 'section': None, 'link': False}
        return [{'key': 'documents', 'label': 'Documents', 'icon': None, **plain, 'items': [model(name) for name in documents]},
                {'key': 'records', 'label': 'Records', 'icon': None, **plain, 'items': [model(name) for name in records]},
                {'key': 'reports', 'label': 'Reports', 'icon': None, **plain,
                 'items': [{'key': f'report:{entry["name"]}', 'kind': 'report', 'label': entry['label'], 'report': entry['name'], 'icon': None}
                           for entry in reports]}]
