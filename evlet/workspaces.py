"""Workspaces: an app's named sidebars, Workspace > NavGroup > items (docs/ui.md#workspaces).

An app declares them in workspaces.py (or passes Application(workspaces=...)). They are checked when the
application is built, and /api/schema gives each role only what it can open. An app that declares none
gets one automatic workspace, grouped as the sidebar groups models: Documents, Records and Reports."""

from dataclasses import dataclass, field
import json
import re
from urllib.parse import urlsplit

from .auth import AccessError
from .models import ValidationError

KEY = re.compile(r'[a-z][a-z0-9_]{0,63}')
ICON = re.compile(r'[a-z0-9][a-z0-9-]{0,39}')
VIEWS = ('list', 'board')
MAX_LABEL = 80
MAX_URL = 2000


@dataclass(frozen=True, repr=False)
class Shortcut:
    """One sidebar entry. Name exactly one target: a model (with an optional view and filters), a report, a slip or a URL."""
    model: object = None
    view: str | None = None
    filters: object = None
    label: str | None = None
    report: object = field(default=None, kw_only=True)
    slip: object = field(default=None, kw_only=True)
    url: str | None = field(default=None, kw_only=True)

    def __repr__(self):
        shown = [f'{name}={_shown(getattr(self, name))}' for name in ('model', 'view', 'filters', 'label', 'report', 'slip', 'url')
                 if getattr(self, name) is not None]
        return f'Shortcut({", ".join(shown)})'


@dataclass(frozen=True)
class NavGroup:
    """A titled group of sidebar items: model names (text) and Shortcuts."""
    label: str
    icon: str | None = field(default=None, kw_only=True)
    items: tuple = field(default=(), kw_only=True)

    def __post_init__(self):
        if isinstance(self.items, (list, tuple)):
            object.__setattr__(self, 'items', tuple(self.items))


@dataclass(frozen=True)
class Workspace:
    """A named sidebar: NavGroups of items. key defaults to the label in lowercase with underscores."""
    label: str
    key: str | None = field(default=None, kw_only=True)
    icon: str | None = field(default=None, kw_only=True)
    groups: tuple = field(default=(), kw_only=True)

    def __post_init__(self):
        if isinstance(self.groups, (list, tuple)):
            object.__setattr__(self, 'groups', tuple(self.groups))
        name = f'Workspace {self.label!r}'
        _label(self.label, name)
        key = slug(self.label) if self.key is None else self.key
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise ValueError(f'{name}: key must be 1 to 64 lowercase letters, digits or underscores, starting with a letter'
                             + (', so give it key=' if self.key is None else f', not {self.key!r}'))
        object.__setattr__(self, 'key', key)
        _icon(self.icon, name)
        if not isinstance(self.groups, tuple) or not self.groups:
            raise ValueError(f'{name}: groups takes one or more NavGroup')
        seen = set()
        for group in self.groups:
            if not isinstance(group, NavGroup):
                raise ValueError(f'{name}: groups takes NavGroup, not {_shown(group)}')
            where = f'{name}, group {group.label!r}'
            _label(group.label, where)
            if slug(group.label) in seen:
                raise ValueError(f'{where}: two groups share the key {slug(group.label)!r}; give them different labels')
            seen.add(slug(group.label))
            _icon(group.icon, where)
            if not isinstance(group.items, tuple) or not group.items:
                raise ValueError(f'{where}: items takes one or more model names or Shortcuts')
            for item in group.items:
                check_item(item, f'{where}, item {_shown(item)}')


def slug(text):
    """A label as a key: lowercase words joined by underscores ('Open tasks' -> 'open_tasks')."""
    return re.sub(r'\W+', '_', text.strip().casefold()).strip('_') if isinstance(text, str) else ''


def _shown(value):
    return value.__name__ if isinstance(value, type) else repr(value)


def _label(value, where, *, name='label'):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_LABEL:
        raise ValueError(f'{where}: {name} must be text of 1 to {MAX_LABEL} characters')


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


def check_item(item, where):
    """Checks that need no registry: one target, a known view, JSON filters, labels and URLs."""
    if isinstance(item, str):
        if not item:
            raise ValueError(f'{where}: name a model')
        return
    if not isinstance(item, Shortcut):
        raise ValueError(f'{where}: an item is a model name or a Shortcut')
    targets = [name for name in ('model', 'report', 'slip', 'url') if getattr(item, name) is not None]
    if len(targets) != 1:
        raise ValueError(f'{where}: name exactly one of model, report, slip or url')
    if item.label is not None:
        _label(item.label, where)
    if targets[0] != 'model' and (item.view is not None or item.filters is not None):
        raise ValueError(f'{where}: view and filters go with a model')
    if item.view is not None and item.view not in VIEWS:
        raise ValueError(f'{where}: view is "list" or "board", not {item.view!r}')
    if item.filters is not None:
        if not isinstance(item.filters, (dict, list, tuple)) or not item.filters:
            raise ValueError(f'{where}: filters is a filter object such as {{"done": False}} or a list of conditions')
        try:
            json.dumps(item.filters, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError(f'{where}: filter values are JSON values: text, numbers, true, false, null or lists') from None
        if item.label is None:
            raise ValueError(f'{where}: a shortcut with filters needs a label')
    if item.url is not None:
        if not check_url(item.url):
            raise ValueError(f'{where}: a URL is an app path starting with / or an http(s) address')
        if item.label is None:
            raise ValueError(f'{where}: a URL shortcut needs a label')
    for name in ('model', 'report', 'slip'):
        value = getattr(item, name)
        if value is not None and not isinstance(value, (str, type)):
            raise ValueError(f'{where}: {name} takes a name or the class')


def model_item(name, label=None, view=None, filters=None):
    """A model item's plain form; its key is model:<name>, plus :<label or view> when it is more than the list."""
    plain = label is None and filters is None and view in (None, 'list')
    return {'key': f'model:{name}' if plain else f'model:{name}:{slug(label) if label else view}', 'kind': 'model',
            'label': label, 'model': name, 'view': view or 'list', 'filters': filters}


class Workspaces:
    """The checked workspaces of an application, and each role's view of them in /api/schema."""

    def __init__(self, business, declared=(), *, name='Evlet'):
        self.business = business
        # One app for an Application built by hand; build_instance replaces it with the installed apps (load).
        self.apps = [{'id': 'app', 'title': name, 'workspaces': tuple(declared), 'models': None, 'reports': None}]
        self.plans = self.compile()

    def load(self, definitions, packages=()):
        """The installed apps, from build_instance: each app's declared workspaces, or its automatic one."""
        registry, titles = self.business.registry, {package.name: getattr(package, 'title', '') for package in packages}
        self.apps = [{'id': name, 'title': titles.get(name) or name.replace('_', ' ').title(), 'workspaces': tuple(definition.workspaces),
                      'models': {registry.name_for(model) for model in definition.models},
                      'reports': {getattr(report, '__resource_id__', report.__name__) for report in definition.reports}}
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
                if workspace.key in keys or workspace.key in automatic:
                    other = keys.get(workspace.key, 'the automatic workspace of app ' + workspace.key)
                    raise ValueError(f'Workspace {workspace.label!r}: the key {workspace.key!r} is taken by {other}')
                keys[workspace.key] = f'workspace {workspace.label!r}'
                plans.append({'key': workspace.key, 'label': workspace.label.strip(), 'icon': workspace.icon, 'app': app['id'],
                              'source': 'declared', 'groups': self.groups(workspace)})
            if not app['workspaces']:
                plans.append({'key': app['id'], 'label': app['title'], 'icon': 'layers', 'app': app['id'], 'source': 'automatic',
                              'models': app['models'], 'reports': app['reports']})
        return plans

    def groups(self, workspace):
        groups, seen = [], {}
        for group in workspace.groups:
            items = []
            for item in group.items:
                where = f'Workspace {workspace.label!r}, group {group.label!r}, item {_shown(item)}'
                plan = self.item(item, where)
                if plan['key'] in seen:
                    raise ValueError(f'{where}: the key {plan["key"]!r} is used by {seen[plan["key"]]}; give one a different label')
                seen[plan['key']] = _shown(item)
                items.append(plan)
            groups.append({'key': slug(group.label), 'label': group.label.strip(), 'icon': group.icon, 'items': items})
        return groups

    def item(self, item, where):
        """An item in its plain form, checked against the models, reports and slips of this application."""
        if isinstance(item, str):
            return model_item(self.model(item, where))
        if item.model is not None:
            name = self.model(item.model, where)
            model = self.business.registry.models[name]
            if item.view == 'board' and not getattr(getattr(model, 'Meta', None), 'board', None):
                raise ValueError(f'{where}: {model.__name__} has no board; set Meta.board to its State, or use view="list"')
            return model_item(name, item.label, item.view, None if item.filters is None else self.filters(model, item.filters, where))
        if item.report is not None:
            reports = getattr(getattr(self.business, 'forms', None), 'reports', {})
            name = getattr(item.report, '__resource_id__', item.report.__name__) if isinstance(item.report, type) else item.report
            if name not in reports:
                raise ValueError(f'{where}: unknown report {name!r}; register it with the application')
            return {'key': f'report:{name}', 'kind': 'report', 'label': item.label, 'report': name}
        if item.slip is not None:
            from .slips import slip_name
            slips = getattr(getattr(self.business, 'slips', None), 'slips', {})
            name = slip_name(item.slip) if isinstance(item.slip, type) else item.slip
            if name not in slips:
                raise ValueError(f'{where}: unknown slip {name!r}; register it with the application')
            if slips[name].__source__ is not None:
                raise ValueError(f'{where}: this slip is presented from a {slips[name].__source__.__name__} record; '
                                 'only a slip without a source can be a workspace item')
            return {'key': f'slip:{name}', 'kind': 'slip', 'label': item.label, 'slip': name}
        return {'key': f'url:{slug(item.label)}', 'kind': 'url', 'label': item.label.strip(), 'url': item.url}

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

    def filters(self, model, filters, where):
        """Filters checked as a list request checks them, in the list form a list address carries (?f=)."""
        from .query import ChildGroup, SqlQuery, conditions
        try:
            items = conditions(filters)
            SqlQuery(self.business.registry, model, children=self.business.children.values()).where(filters)
        except ValidationError as exc:
            raise ValueError(f'{where}: filters {"; ".join(f"{key}: {value}" for key, value in exc.errors.items())}') from None
        result = []
        for item in items:
            paths = [inner.path for inner in item.where] if isinstance(item, ChildGroup) else [item.path]
            if any('__' in path for path in paths):
                raise ValueError(f'{where}: filters name fields of {model.__name__} itself, not of linked records')
            if isinstance(item, ChildGroup):
                result.append({'child': item.child, 'match': item.match, 'where': [[inner.path, inner.operator, inner.value] for inner in item.where]})
            else:
                result.append([item.path, item.operator, item.value])
        return result

    # ---------- each role ----------

    def schema(self, user, result):
        """The workspaces this role sees: items it can't open are left out, then empty groups and workspaces."""
        context = Context(self.business, user, result)
        workspaces = []
        for plan in self.effective():
            groups = context.automatic(plan) if plan['source'] == 'automatic' else [
                {'key': group['key'], 'label': group['label'], 'icon': group['icon'],
                 'items': [shown for shown in (context.item(item) for item in group['items']) if shown is not None]}
                for group in plan['groups']]
            groups = [group for group in groups if group['items']]
            if groups:
                workspaces.append({'key': plan['key'], 'label': plan['label'], 'icon': plan['icon'], 'app': plan['app'],
                                   'source': plan['source'], 'groups': groups})
        return workspaces

    def effective(self):
        return self.plans


class Context:
    """What one role can open, read from its already filtered schema."""

    def __init__(self, business, user, result):
        self.business, self.user = business, user
        self.models = {entry['name']: entry for entry in result['models'] if not entry['child_only']}
        self.order = list(self.models)
        self.reports = {entry['name']: entry for entry in result.get('reports', ())}
        slips = getattr(business, 'slips', None)
        self.slips = {name: slip for name, slip in getattr(slips, 'slips', {}).items() if slips.allowed(user, slip)}

    def item(self, item):
        kind = item['kind']
        if kind == 'model':
            entry = self.models.get(item['model'])
            if entry is None or item['view'] == 'board' and not (entry.get('state') and entry.get('board') == entry['state']['field']):
                return None
            if item['filters'] is not None:
                model = self.business.registry.models[item['model']]
                try:
                    self.business.policy.query_fields(self.user, model, item['filters'], (), database=self.business.database,
                                                      children=self.business.children.values())
                except (AccessError, ValidationError):
                    return None
            return {**item, 'label': item['label'] or entry['plural']}
        if kind == 'report':
            entry = self.reports.get(item['report'])
            return None if entry is None else {**item, 'label': item['label'] or entry['label']}
        if kind == 'slip':
            slip = self.slips.get(item['slip'])
            return None if slip is None else {**item, 'label': item['label'] or self.business.slips.entry(item['slip'], slip)['label']}
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
        return [{'key': 'documents', 'label': 'Documents', 'icon': None, 'items': [model(name) for name in documents]},
                {'key': 'records', 'label': 'Records', 'icon': None, 'items': [model(name) for name in records]},
                {'key': 'reports', 'label': 'Reports', 'icon': None,
                 'items': [{'key': f'report:{entry["name"]}', 'kind': 'report', 'label': entry['label'], 'report': entry['name']} for entry in reports]}]
