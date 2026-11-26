"""Logic-less templates for site pages (docs/sites.md#templates).

    {{title}}                  the value of title, HTML-escaped; {{author.name}} reads into a nested object
    {{.}}                      the current item itself (inside {{#each}} over plain values)
    {{#each posts}}...{{/each}}   once per item of a list, with the item's fields in reach first
    {{#if summary}}...{{else}}...{{/if}}   a part shown when a value is there (not empty, zero, false or null)
    {{#each tags}}...{{else}}No tags{{/each}}   {{else}} also shows when a list is empty
    {{> header}}               a partial: another template, with the same data in reach
    {{! a comment }}           left out of the output

There is nothing else: no expressions, no calls, no attribute access, no raw output. A name only ever reads
keys of the JSON-like data given (dicts, lists and plain values), never attributes of Python objects, so a
template cannot run anything whoever wrote it. Templates, nesting, partials, loops and output are bounded;
going over a bound raises TemplateError, as does any tag the grammar doesn't have.
"""

import html
import re

MAX_TEMPLATE = 256 * 1024
MAX_DEPTH = 32  # nested sections in one template
MAX_PARTIALS = 8  # partials inside partials
MAX_OUTPUT = 10 * 2**20
MAX_STEPS = 1_000_000  # nodes visited in one render, so an empty-looking loop over loops still ends

TAG = re.compile(r'\{\{(.*?)\}\}', re.S)
NAME = r'[A-Za-z_][A-Za-z0-9_]{0,63}'
PATH = re.compile(rf'\.|{NAME}(?:\.{NAME}){{0,7}}')
PARTIAL = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,63}(?:/[A-Za-z0-9_][A-Za-z0-9_-]{0,63}){0,3}')
SECTIONS = ('each', 'if')


class TemplateError(ValueError):
    """A template that does not parse, names a missing partial, or goes over a bound."""


def _line(source, offset):
    return source.count('\n', 0, offset) + 1


def parse(source, name='template'):
    """The node tree of a template: ('text', s), ('var', path), ('partial', name), (kind, path, body, otherwise)."""
    if not isinstance(source, str):
        raise TemplateError(f'{name}: a template is text')
    if len(source) > MAX_TEMPLATE:
        raise TemplateError(f'{name} is over {MAX_TEMPLATE // 1024} KiB')
    root = []
    # Each open section: (kind, path, body, otherwise or None, offset); the innermost is last.
    stack = []
    current = root
    position = 0
    for match in TAG.finditer(source):
        text = source[position:match.start()]
        if text:
            current.append(('text', text))
        position = match.end()
        tag = match[1].strip()
        where = f'{name}, line {_line(source, match.start())}'
        if '{' in tag or '}' in tag:
            raise TemplateError(f'{where}: {{{{{match[1]}}}}} is not a tag; use {{{{name}}}}. Raw output is not supported')
        if tag.startswith('!'):
            continue
        if tag.startswith('#'):
            kind, _, path = tag[1:].partition(' ')
            path = path.strip()
            if kind not in SECTIONS:
                raise TemplateError(f'{where}: {{{{#{kind}}}}} is not a section; use {{{{#each list}}}} or {{{{#if name}}}}')
            if not PATH.fullmatch(path):
                raise TemplateError(f'{where}: {{{{#{kind}}}}} needs one name, such as {{{{#{kind} posts}}}}')
            if len(stack) >= MAX_DEPTH:
                raise TemplateError(f'{where}: sections are nested more than {MAX_DEPTH} deep')
            body = []
            stack.append([kind, path, body, None, match.start()])
            current = body
            continue
        if tag == 'else':
            if not stack or stack[-1][3] is not None:
                raise TemplateError(f'{where}: {{{{else}}}} belongs inside {{{{#if}}}} or {{{{#each}}}}, once')
            stack[-1][3] = current = []
            continue
        if tag.startswith('/'):
            kind = tag[1:].strip()
            if not stack or stack[-1][0] != kind:
                expected = f'{{{{/{stack[-1][0]}}}}}' if stack else 'no closing tag'
                raise TemplateError(f'{where}: {{{{/{kind}}}}} closes nothing open here; expected {expected}')
            kind, path, body, otherwise, _ = stack.pop()
            current = stack[-1][3] if stack and stack[-1][3] is not None else stack[-1][2] if stack else root
            current.append((kind, path, tuple(body), tuple(otherwise or ())))
            continue
        if tag.startswith('>'):
            partial = tag[1:].strip()
            if not PARTIAL.fullmatch(partial):
                raise TemplateError(f'{where}: {{{{> {partial}}}}} needs a partial name, such as {{{{> header}}}}')
            current.append(('partial', partial))
            continue
        if not PATH.fullmatch(tag):
            raise TemplateError(f'{where}: {{{{{tag}}}}} is not a name. Templates show values only: {{{{title}}}} or {{{{author.name}}}}')
        current.append(('var', tag))
    if stack:
        raise TemplateError(f'{name}, line {_line(source, stack[-1][4])}: {{{{#{stack[-1][0]}}}}} is never closed')
    text = source[position:]
    if '{{' in text:
        raise TemplateError(f'{name}, line {_line(source, position + text.index("{{"))}: {{{{ is never closed')
    if text:
        root.append(('text', text))
    return tuple(root)


def _lookup(stack, path):
    if path == '.':
        return stack[-1]
    first, *rest = path.split('.')
    for scope in reversed(stack):
        if isinstance(scope, dict) and first in scope:
            value = scope[first]
            break
    else:
        return None
    for key in rest:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _text(value):
    if value is None or isinstance(value, (dict, list, tuple)):
        return ''
    if value is True or value is False:
        return 'true' if value else 'false'
    if isinstance(value, (str, int, float)):
        return html.escape(str(value), quote=True)
    # Decimals and dates come as text from the API; anything else shows nothing rather than its repr.
    return ''


def _truthy(value):
    return value not in (None, False, '', 0) and value != [] and value != {} and value != ()


class _Render:
    def __init__(self, partials, max_output):
        self.partials, self.max_output = partials, max_output
        self.parsed, self.out, self.size, self.steps = {}, [], 0, 0

    def emit(self, text):
        self.size += len(text)
        if self.size > self.max_output:
            raise TemplateError(f'The page is over {self.max_output} characters')
        self.out.append(text)

    def partial(self, name):
        if name not in self.parsed:
            source = self.partials(name) if self.partials is not None else None
            if source is None:
                raise TemplateError(f'{{{{> {name}}}}}: there is no partial {name}')
            self.parsed[name] = parse(source, f'partial {name}')
        return self.parsed[name]

    def step(self):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise TemplateError(f'The template takes more than {MAX_STEPS} steps to render')

    def nodes(self, nodes, stack, depth):
        for node in nodes:
            self.step()
            kind = node[0]
            if kind == 'text':
                self.emit(node[1])
            elif kind == 'var':
                self.emit(_text(_lookup(stack, node[1])))
            elif kind == 'partial':
                if depth >= MAX_PARTIALS:
                    raise TemplateError(f'Partials include partials more than {MAX_PARTIALS} deep (does {node[1]} include itself?)')
                self.nodes(self.partial(node[1]), stack, depth + 1)
            elif kind == 'if':
                self.nodes(node[2] if _truthy(_lookup(stack, node[1])) else node[3], stack, depth)
            else:
                items = _lookup(stack, node[1])
                if isinstance(items, (list, tuple)) and items:
                    for item in items:
                        self.step()
                        stack.append(item)
                        try:
                            self.nodes(node[2], stack, depth)
                        finally:
                            stack.pop()
                else:
                    self.nodes(node[3], stack, depth)


def render(template, data, *, partials=None, max_output=MAX_OUTPUT, name='template'):
    """Render a template (its text, or a tree from parse) with data, a dict of JSON-like values.

    partials(name) returns a partial's text or None when there is none."""
    nodes = parse(template, name) if isinstance(template, str) else template
    work = _Render(partials, max_output)
    work.nodes(nodes, [data], 0)
    return ''.join(work.out)
