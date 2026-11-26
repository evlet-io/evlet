"""Site collections: pages and JSON files generated from records at publish (docs/sites.md#collections).

A site declares its collections in _site/collections.json in its draft. The _site/ folder holds the
declaration, the templates and the partials; it is published with the release (releases/<id>/source/) and is
never served. Each collection reads one model as the public role, with the same permission machinery as the
list route (Business.page): the role's Rule, its row scope and its readable fields, and Meta.api. A field the
public role can't read never reaches a template or a JSON file, and a filter or order on one is refused.

    {"collections": [{
        "name": "posts", "model": "post", "filter": {"published": true}, "order_by": "-published_on", "limit": 100,
        "fields": ["title", "slug", "summary", "published_on"],
        "page": "blog/{slug}.html", "template": "post.html",
        "index": {"path": "blog/index.html", "template": "blog.html"},
        "json": "data/posts.json"}]}
"""

import json
from urllib.parse import urlencode

from . import templates
from .auth import PUBLIC, AccessError, User
from .database import ROW_LIMIT, using
from .models import ValidationError
from .permissions import calling

SOURCE = '_site'
DECLARATION = SOURCE + '/collections.json'
PARTIALS = SOURCE + '/partials/'
MAX_COLLECTIONS, MAX_LIMIT, DEFAULT_LIMIT = 20, 1000, 100
KEYS = {'name', 'model', 'filter', 'order_by', 'limit', 'fields', 'page', 'template', 'index', 'json'}
PUBLIC_USER = User(0, 'public', PUBLIC)


def is_source(path):
    """Whether a site path belongs to _site/ (published with a release, never served)."""
    return path == SOURCE or path.startswith(SOURCE + '/')


def _target(path, what):
    """A generated file's path: a valid site path outside _site/. A leading / is allowed."""
    from .sites import check_path
    if not isinstance(path, str):
        raise ValueError(f'{what} is a site path such as blog/index.html')
    path = path.removeprefix('/')
    check_path(path.replace('{', 'x').replace('}', 'x') if '{' in path else path)
    if is_source(path):
        raise ValueError(f'{what} {path} is inside _site/, which is never served')
    return path


def declared(read):
    """The validated collections of a site, from read(path) -> bytes or None. [] without a declaration."""
    raw = read(DECLARATION)
    if raw is None:
        return []
    try:
        document = json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f'{DECLARATION} is not valid JSON: {exc}') from None
    if not isinstance(document, dict) or set(document) - {'collections'} or not isinstance(document.get('collections'), list):
        raise ValueError(f'{DECLARATION} holds {{"collections": [...]}}')
    items = document['collections']
    if len(items) > MAX_COLLECTIONS:
        raise ValueError(f'A site declares at most {MAX_COLLECTIONS} collections')
    found, names = [], set()
    for number, item in enumerate(items, 1):
        where = f'{DECLARATION}, collection {number}'
        if not isinstance(item, dict) or set(item) - KEYS:
            raise ValueError(f'{where}: use ' + ', '.join(sorted(KEYS)))
        name = item.get('name')
        if not isinstance(name, str) or not templates.re.fullmatch(templates.NAME, name) or name in names:
            raise ValueError(f'{where}: name is a unique identifier such as posts')
        names.add(name)
        where = f'{DECLARATION}, collection {name}'
        model = item.get('model')
        if not isinstance(model, str) or not templates.re.fullmatch(r'[a-z][a-z0-9_]{0,127}', model):
            raise ValueError(f'{where}: model is the API name of a model, such as post')
        limit = item.get('limit', DEFAULT_LIMIT)
        if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f'{where}: limit is a whole number from 1 to {MAX_LIMIT}')
        filters = item.get('filter', {})
        if not isinstance(filters, (dict, list)):
            raise ValueError(f'{where}: filter is an object of the list route, such as {{"published": true}}')
        order = item.get('order_by')
        if order is not None and (not isinstance(order, str) or not 0 < len(order) <= 500):
            raise ValueError(f'{where}: order_by is text such as -published_on')
        fields = item.get('fields')
        if fields is not None and (not isinstance(fields, list) or not fields or len(fields) > 200
                                   or any(not isinstance(field, str) or not templates.re.fullmatch(templates.NAME, field) for field in fields)):
            raise ValueError(f'{where}: fields is a list of field names')
        page = item.get('page')
        template = item.get('template')
        if page is not None:
            page = _target(page, f'{where}: page')
            placeholders = templates.re.findall(r'\{([^{}]*)\}', page)
            if not placeholders or any(not templates.re.fullmatch(templates.NAME, field) for field in placeholders):
                raise ValueError(f'{where}: page names a field in braces, such as blog/{{slug}}.html')
            if not isinstance(template, str):
                raise ValueError(f'{where}: a page needs a template, a file of _site/ such as post.html')
        if template is not None:
            template = _template(template, where)
        index = item.get('index')
        if index is not None:
            if not isinstance(index, dict) or set(index) != {'path', 'template'}:
                raise ValueError(f'{where}: index is {{"path": "blog/index.html", "template": "blog.html"}}')
            index = {'path': _target(index['path'], f'{where}: index path'), 'template': _template(index['template'], where)}
            if '{' in index['path']:
                raise ValueError(f'{where}: an index path names one file')
        output = item.get('json')
        if output is not None:
            output = _target(output, f'{where}: json')
            if not output.endswith('.json') or '{' in output:
                raise ValueError(f'{where}: json is one .json file, such as data/posts.json')
        if page is None and index is None and output is None:
            raise ValueError(f'{where}: write at least one of page, index or json')
        found.append({'name': name, 'model': model, 'filter': filters, 'order_by': order, 'limit': limit, 'fields': fields,
                      'page': page, 'template': template, 'index': index, 'json': output})
    return found


def _template(path, where):
    if not isinstance(path, str) or not path.endswith('.html'):
        raise ValueError(f'{where}: a template is an .html file of _site/, such as post.html')
    from .sites import check_path
    check_path(SOURCE + '/' + path.removeprefix(SOURCE + '/'))
    return SOURCE + '/' + path.removeprefix(SOURCE + '/')


def _model(business, name):
    """The model a collection reads, as the list route finds it."""
    registry = business.registry
    model = registry.models.get(name)
    if model is None or model in business.child_models or registry.api.get(model) is None:
        raise ValueError(f'{name} is not a model the API serves')
    return model


def records(business, collection):
    """The collection's records as the public role reads them on the list route: up to its limit, each with
    the public role's readable fields only (and only the declared ones when fields is given)."""
    model = _model(business, collection['model'])
    found = []
    with calling(), business.gate.shared(), using(business.database):
        while len(found) < collection['limit']:
            query = {'limit': min(ROW_LIMIT, collection['limit'] - len(found)), 'offset': len(found),
                     'filters': json.dumps(collection['filter'], separators=(',', ':'))}
            if collection['order_by']:
                query['order_by'] = collection['order_by']
            try:
                business.policy.require(PUBLIC_USER, model, 'read')
            except AccessError as exc:
                raise ValueError(f'Collection {collection["name"]}: the public role cannot read {collection["model"]} '
                                 f'({exc.message}). Give it a Rule for ("public", Model) with the fields a site may show') from None
            try:
                _, page, _ = business.page({'query_string': urlencode(query).encode()}, PUBLIC_USER, model)
            except (AccessError, ValidationError) as exc:
                reason = exc.message if isinstance(exc, AccessError) else json.dumps(exc.errors)
                raise ValueError(f'Collection {collection["name"]}: {reason}. A filter or order may name only fields the '
                                 'public role reads') from None
            found.extend(page['items'])
            if len(page['items']) < query['limit']:
                break
    kept = []
    for record in found:
        # Only the record's values: no _-prefixed metadata of the route (revisions, moves, who changed it).
        values = {key: value for key, value in record.items() if not key.startswith('_')}
        if collection['fields'] is not None:
            values = {key: values[key] for key in collection['fields'] if key in values}
            if 'id' in record:
                values.setdefault('id', record['id'])
        kept.append(values)
    return kept


def _segment(value):
    from .sites import SEGMENT
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return text if SEGMENT.fullmatch(text) else None


def render(business, read, collections, *, max_file_bytes):
    """Render every collection. Returns (files {path: bytes}, info [{name, model, records, pages, skipped}],
    model names read). read(path) gives a source file's bytes or None. Raises ValueError (or TemplateError)
    when a collection cannot be rendered; a record whose page path would not be a valid file name is skipped."""
    files, info = {}, []
    cache = {}

    def source(path):
        if path not in cache:
            raw = read(path)
            cache[path] = None if raw is None else raw.decode('utf-8', errors='replace')
        return cache[path]

    def partial(name):
        return source(PARTIALS + name + '.html')

    def page(template, data, what):
        text = source(template)
        if text is None:
            raise ValueError(f'{what}: the template {template} is not in the site')
        output = templates.render(text, data, partials=partial, max_output=max_file_bytes, name=template).encode('utf-8')
        if len(output) > max_file_bytes:
            raise ValueError(f'{what} is over the site file limit of {max_file_bytes} bytes')
        return output

    def put(path, content, what):
        if path in files:
            raise ValueError(f'{what}: {path} is written twice')
        files[path] = content

    for collection in collections:
        what = f'Collection {collection["name"]}'
        items = records(business, collection)
        skipped, planned = [], []
        if collection['page'] is not None:
            taken = set(files)
            for record in items:
                path, missing = collection['page'], None
                for field in templates.re.findall(r'\{([A-Za-z_][A-Za-z0-9_]*)\}', collection['page']):
                    segment = _segment(record.get(field))
                    if segment is None:
                        missing = field
                        break
                    path = path.replace('{' + field + '}', segment)
                if missing is not None:
                    skipped.append({'id': record.get('id'), 'reason': f'{missing} is empty or not a file name'})
                    continue
                if path in taken:
                    skipped.append({'id': record.get('id'), 'reason': f'{path} is written by another record'})
                    continue
                taken.add(path)
                # Where the page is served: index.html files by their folder.
                record['_url'] = '/' + (path.removesuffix('index.html') if path == 'index.html' or path.endswith('/index.html') else path)
                planned.append((record, path))
        for record, path in planned:
            put(path, page(collection['template'], {**record, 'collection': collection['name']}, what), what)
        if collection['index'] is not None:
            put(collection['index']['path'], page(collection['index']['template'], {'items': items, 'count': len(items),
                                                                                    'collection': collection['name']}, what), what)
        if collection['json'] is not None:
            put(collection['json'], json.dumps({'items': items}, ensure_ascii=False, separators=(',', ':'), default=str).encode('utf-8'), what)
            if len(files[collection['json']]) > max_file_bytes:
                raise ValueError(f'{what}: {collection["json"]} is over the site file limit of {max_file_bytes} bytes')
        info.append({'name': collection['name'], 'model': collection['model'], 'records': len(items), 'pages': len(planned), 'skipped': skipped[:50]})
    return files, info, sorted({collection['model'] for collection in collections})
