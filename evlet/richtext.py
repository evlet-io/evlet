"""Rich text: the server's allowlist sanitiser and plain-text extract (docs/internal/rich-text.md).

`sanitise(html)` is the security boundary for RichText fields. It reads any HTML with the standard
library parser, keeps only the editor's schema and writes canonical HTML:

- blocks: p, h2, h3, ul, ol (nested at most three levels, `ul data-type="checklist"` with
  `li data-checked="true"`), blockquote, pre > code, hr;
- inline: strong, em, u, s, code, a href (http, https and mailto only), br;
- images: figure (class rt-sm, rt-md or rt-full) > img data-attachment="<attachment id>" alt="..." and an
  optional figcaption. An image is always one of the record's private attachments, never a URL: src is
  never kept (docs/internal/rich-text.md, "Inline images").

Everything else is dropped (scripts, styles, frames, SVG and their content; every attribute that is not
listed) or unwrapped (unknown tags keep their text). The output is canonical: sanitising it again gives
the same text, and the browser editor (frontend/src/evlet/richtext/html.ts) writes the same form.
"""

from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

__all__ = ['sanitise', 'plain_text', 'image_ids', 'Document']

MARKS = ('strong', 'em', 'u', 's', 'code')  # outermost first, after the link
ALIASES = {'b': 'strong', 'i': 'em', 'strike': 's', 'del': 's', 'ins': 'u', 'tt': 'code', 'kbd': 'code', 'samp': 'code',
           'h1': 'h2', 'h4': 'h3', 'h5': 'h3', 'h6': 'h3', 'div': 'p'}
# Their content is never text the user meant to keep.
DROPPED = frozenset(('script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'template', 'noscript', 'noembed',
                     'noframes', 'textarea', 'select', 'title', 'head', 'frame', 'frameset', 'applet', 'audio', 'video',
                     'canvas', 'xmp', 'plaintext', 'button', 'map', 'picture'))
VOID = frozenset(('br', 'hr', 'img', 'embed', 'input', 'meta', 'link', 'area', 'base', 'col', 'param', 'source', 'track',
                  'wbr', 'keygen'))
# Unknown tags that still separate blocks (their text is kept, in its own paragraph).
BOUNDARIES = frozenset(('section', 'article', 'header', 'footer', 'nav', 'aside', 'main', 'table', 'thead', 'tbody',
                        'tfoot', 'tr', 'td', 'th', 'caption', 'dl', 'dt', 'dd', 'address', 'center', 'details',
                        'summary', 'fieldset', 'legend', 'form', 'body', 'html'))
BLOCKS = frozenset(('p', 'h2', 'h3', 'ul', 'ol', 'li', 'blockquote', 'pre', 'hr'))
INLINE = frozenset(MARKS) | {'a', 'br'}
IMAGES = frozenset(('figure', 'img', 'figcaption'))
SIZES = ('rt-sm', 'rt-md', 'rt-full')
ATTACHMENT = re.compile(r'[1-9][0-9]{0,18}')
MAX_ALT = 300
MAX_DEPTH = 3
SPACES = re.compile(r'[ \t\n\r\f]+')
CONTROL = re.compile(r'[\x00-\x20\x7f-\x9f]')


def safe_href(value):
    """A link's URL when it is http, https or mailto; None otherwise. Strips what a browser ignores first."""
    if not isinstance(value, str):
        return None
    url = re.sub(r'[\t\n\r]', '', value).strip('\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f\x10\x11\x12\x13\x14'
                                                '\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f ')
    if not url or len(url) > 2048 or CONTROL.search(url):
        return None
    match = re.match(r'([a-zA-Z][a-zA-Z0-9+.-]*):', url)
    if not match:
        return None
    scheme = match.group(1).lower()
    if scheme == 'mailto':
        return url if len(url) > 7 else None
    if scheme not in ('http', 'https'):
        return None
    try:
        parts = urlsplit(url)
        parts.port
    except ValueError:
        return None
    return url if parts.hostname else None


class _Node:
    __slots__ = ('tag', 'attrs', 'children')

    def __init__(self, tag, attrs=None):
        self.tag, self.attrs, self.children = tag, attrs or {}, []


class _Reader(HTMLParser):
    """HTML to a tree of allowed tags; unknown tags are unwrapped, dropped ones vanish with their content."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node('#root')
        self.stack = [(None, self.root)]  # (source tag, node or None for an unwrapped tag)
        self.skip = []  # open dropped tags

    def node(self):
        for _, node in reversed(self.stack):
            if node is not None:
                return node
        return self.root

    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag not in VOID:
                self.skip.append(tag)
            return
        if tag in DROPPED:
            if tag not in VOID:
                self.skip.append(tag)
            return
        name = ALIASES.get(tag, tag)
        if tag == 'img':
            # Only a reference to an attachment: the image itself is never a URL.
            values = {key.lower(): value for key, value in attrs if isinstance(key, str)}
            if ATTACHMENT.fullmatch(values.get('data-attachment') or ''):
                self.node().children.append(_Node('img', values))
            return
        if name in BLOCKS or name in INLINE or name in IMAGES:
            node = _Node(name, {key.lower(): value for key, value in attrs if isinstance(key, str)})
        elif tag in BOUNDARIES:
            node = _Node('#boundary')
        else:
            node = None
        if node is not None:
            self.node().children.append(node)
        if tag not in VOID:
            self.stack.append((tag, node))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and not self.skip and self.stack[-1][0] == tag:
            self.stack.pop()
        elif tag not in VOID and self.skip and self.skip[-1] == tag:
            self.skip.pop()

    def handle_endtag(self, tag):
        if self.skip:
            # Close the innermost matching dropped tag (and anything left open inside it).
            for index in range(len(self.skip) - 1, -1, -1):
                if self.skip[index] == tag:
                    del self.skip[index:]
                    break
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if not self.skip and data:
            self.node().children.append(data)


# ---- the document model, the same as the editor's (model.ts) ------------------------------------------

def _block(kind, **extra):
    return {'type': kind, 'content': [], **extra}


class Document:
    """A flat list of blocks built from a tree; see docs/internal/rich-text.md."""

    def __init__(self):
        self.blocks = []
        self.current = None

    # Building ----------------------------------------------------------------------------------

    def end(self):
        self.current = None

    def start(self, block):
        self.blocks.append(block)
        self.current = block
        return block

    def inline_target(self, ctx):
        if self.current is None:
            if ctx['code']:
                self.start(_block('code', text=''))
            elif ctx['item'] is not None:
                list_type, indent, checked = ctx['item']
                self.start(_block('item', list=list_type, indent=indent, checked=checked))
            elif ctx['quote']:
                self.start(_block('quote'))
            else:
                self.start(_block('paragraph'))
        return self.current

    def add_text(self, value, ctx):
        # Whitespace between blocks (source formatting) is not a paragraph.
        if self.current is None and not ctx['pre'] and not ctx['code'] and not SPACES.sub('', value):
            return
        block = self.inline_target(ctx)
        if block['type'] == 'code':
            if ctx.get('first_in_pre') and not block['text'] and value.startswith('\n'):
                value = value[1:]
            block['text'] += value
            return
        if ctx['pre']:
            # Preformatted text outside a code block (a list item, a quote): lines become breaks.
            lines = value.replace('\r\n', '\n').split('\n')
            for index, line in enumerate(lines):
                if index:
                    block['content'].append(['break'])
                if line:
                    block['content'].append(['text', line.replace('\t', ' '), ctx['marks'], ctx['href']])
            return
        value = SPACES.sub(' ', value)
        if value:
            block['content'].append(['text', value, ctx['marks'], ctx['href']])

    def line_break(self, ctx):
        block = self.inline_target(ctx)
        if block['type'] == 'code':
            block['text'] += '\n'
        else:
            block['content'].append(['break'])

    def walk(self, node, ctx):
        for child in node.children:
            if isinstance(child, str):
                self.add_text(child, ctx)
                ctx = {**ctx, 'first_in_pre': False}
                continue
            tag = child.tag
            # A browser drops a newline only right after <pre>, never inside a tag within it.
            ctx = {**ctx, 'first_in_pre': False} if tag != 'pre' else ctx
            if tag in ('figure', 'img'):
                if ctx['item'] is None and not ctx['code']:
                    self.image(child)
                continue
            if tag == 'figcaption':
                continue
            if tag == 'br':
                self.line_break(ctx)
            elif tag == 'a':
                self.walk(child, {**ctx, 'href': safe_href(child.attrs.get('href')) or ctx['href']})
            elif tag in MARKS:
                self.walk(child, {**ctx, 'marks': tuple(mark for mark in MARKS if mark in ctx['marks'] or mark == tag)})
            elif tag == 'hr':
                if ctx['item'] is None and not ctx['code']:
                    self.end()
                    self.start(_block('rule'))
                    self.end()
            elif tag in ('ul', 'ol'):
                self.end()
                checklist = tag == 'ul' and child.attrs.get('data-type') == 'checklist'
                kind = 'check' if checklist else 'bullet' if tag == 'ul' else 'ordered'
                depth = ctx['depth'] + 1
                self.walk(child, {**ctx, 'list': kind, 'depth': depth, 'item': None, 'code': False, 'pre': False})
                self.end()
            elif tag == 'li':
                self.end()
                kind = ctx['list'] or 'bullet'
                indent = min(max(ctx['depth'], 1), MAX_DEPTH) - 1
                checked = kind == 'check' and child.attrs.get('data-checked') == 'true'
                item = (kind, indent, checked)
                self.start(_block('item', list=kind, indent=indent, checked=checked))
                self.walk(child, {**ctx, 'item': item, 'code': False, 'pre': False})
                self.end()
            elif ctx['item'] is not None:
                # Inside a list item other blocks are lines of the item.
                if tag in ('p', 'h2', 'h3', 'blockquote', 'pre', '#boundary'):
                    if self.current is not None and self.current['type'] == 'item' and self.current['content']:
                        self.current['content'].append(['break'])
                    self.walk(child, {**ctx, 'pre': ctx['pre'] or tag == 'pre'})
                else:
                    self.walk(child, ctx)
            elif ctx['code']:
                # Inside a code block everything is text.
                if tag in ('p', '#boundary') and self.current is not None and self.current.get('text'):
                    self.current['text'] += '\n'
                self.walk(child, ctx)
            elif tag == 'pre':
                self.end()
                self.walk(child, {**ctx, 'code': True, 'pre': True, 'first_in_pre': True})
                self.end()
            elif tag == 'blockquote':
                self.end()
                self.walk(child, {**ctx, 'quote': True})
                self.end()
            elif tag in ('h2', 'h3'):
                self.end()
                if ctx['quote']:
                    self.start(_block('quote'))
                else:
                    self.start(_block('heading', level=int(tag[1])))
                self.walk(child, ctx)
                self.end()
            elif tag == 'p':
                self.end()
                self.start(_block('quote' if ctx['quote'] else 'paragraph'))
                self.walk(child, ctx)
                self.end()
            elif tag == '#boundary':
                self.end()
                self.walk(child, ctx)
                self.end()

    def image(self, node):
        """A figure (or a bare img) as an image block: the attachment id, alt text, size and caption."""
        img = node if node.tag == 'img' else _find(node, 'img')
        if img is None:
            return
        caption = _find(node, 'figcaption') if node.tag == 'figure' else None
        classes = (node.attrs.get('class') or '').split() if node.tag == 'figure' else []
        size = next((name for name in classes if name in SIZES), 'rt-md')
        self.end()
        self.blocks.append({'type': 'image', 'content': [], 'attachment': int(img.attrs['data-attachment']),
                            'alt': _plain(img.attrs.get('alt') or '')[:MAX_ALT],
                            'caption': _plain(_text_of(caption) if caption else '')[:MAX_ALT], 'size': size})
        self.end()

    @classmethod
    def parse(cls, markup):
        reader = _Reader()
        reader.feed(markup)
        reader.close()
        document = cls()
        document.walk(reader.root, {'marks': (), 'href': None, 'list': None, 'depth': 0, 'item': None, 'quote': False,
                                    'code': False, 'pre': False, 'first_in_pre': False})
        document.normalise()
        return document

    # Normalising -------------------------------------------------------------------------------

    def normalise(self):
        previous_indent = -1
        # An empty code block has nothing to show (and the editor never keeps one either).
        self.blocks = [block for block in self.blocks if block['type'] != 'code' or block['text']]
        for block in self.blocks:
            if block['type'] == 'code':
                block['text'] = block['text'].replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
                continue
            if block['type'] in ('rule', 'image'):
                previous_indent = -1
                continue
            block['content'] = _tidy(block['content'])
            if block['type'] == 'item':
                # A list cannot skip levels: an item is at most one deeper than the item before it.
                block['indent'] = min(block['indent'], previous_indent + 1)
                previous_indent = block['indent']
                if block['list'] != 'check':
                    block['checked'] = False
            else:
                previous_indent = -1

    # Writing -----------------------------------------------------------------------------------

    def empty(self):
        return all(block['type'] not in ('rule', 'image') and not block.get('text') and all(piece[0] == 'break' for piece in block.get('content', ()))
                   for block in self.blocks)

    def html(self):
        # Trailing empty paragraphs are not written (the editor keeps one for the caret).
        blocks = list(self.blocks)
        while blocks and blocks[-1]['type'] == 'paragraph' and not blocks[-1]['content']:
            blocks.pop()
        out, index = [], 0
        while index < len(blocks):
            block = blocks[index]
            kind = block['type']
            if kind == 'item':
                end = index
                while end < len(blocks) and blocks[end]['type'] == 'item':
                    end += 1
                out.append(_lists(blocks[index:end]))
                index = end
                continue
            if kind == 'quote':
                end = index
                while end < len(blocks) and blocks[end]['type'] == 'quote':
                    end += 1
                out.append('<blockquote>' + ''.join(f'<p>{_inline(item["content"])}</p>' for item in blocks[index:end]) + '</blockquote>')
                index = end
                continue
            if kind == 'paragraph':
                out.append(f'<p>{_inline(block["content"])}</p>')
            elif kind == 'heading':
                out.append(f'<h{block["level"]}>{_inline(block["content"])}</h{block["level"]}>')
            elif kind == 'code':
                out.append(f'<pre><code>{_text(block["text"])}</code></pre>')
            elif kind == 'rule':
                out.append('<hr>')
            elif kind == 'image':
                caption = f'<figcaption>{_text(block["caption"])}</figcaption>' if block['caption'] else ''
                out.append(f'<figure class="{block["size"]}"><img data-attachment="{block["attachment"]}" alt="{_attribute(block["alt"])}">{caption}</figure>')
            index += 1
        return ''.join(out)

    def text(self):
        lines = []
        for block in self.blocks:
            if block['type'] == 'code':
                lines.append(block['text'])
            elif block['type'] == 'image':
                if block['alt']:
                    lines.append(block['alt'])
            elif block['type'] != 'rule':
                lines.append(''.join(piece[1] if piece[0] == 'text' else '\n' for piece in block['content']))
        return '\n'.join(lines).strip('\n')


def _tidy(pieces):
    """Collapse whitespace across pieces as HTML shows it, turn no-break spaces into spaces, drop empty
    pieces and merge neighbours with the same marks."""
    out, after_space = [], True  # a block starts as if after a space: leading spaces go
    for piece in pieces:
        if piece[0] == 'break':
            if out and out[-1][0] == 'text':
                out[-1][1] = out[-1][1].rstrip(' ')
            out.append(['break'])
            after_space = True
            continue
        text = piece[1]
        if after_space:
            text = text.lstrip(' ')
        if not text:
            continue
        after_space = text.endswith(' ')
        out.append(['text', text, piece[2], piece[3]])
    if out and out[-1][0] == 'text':
        out[-1][1] = out[-1][1].rstrip(' ')
    merged = []
    for piece in out:
        if piece[0] == 'text':
            piece[1] = piece[1].replace(' ', ' ').replace('\x00', '')
            if not piece[1]:
                continue
            if merged and merged[-1][0] == 'text' and merged[-1][2] == piece[2] and merged[-1][3] == piece[3]:
                merged[-1][1] += piece[1]
                continue
        merged.append(piece)
    return merged


def _find(node, tag):
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag == tag:
                return child
            found = _find(child, tag)
            if found is not None:
                return found
    return None


def _text_of(node):
    return ''.join(child if isinstance(child, str) else _text_of(child) for child in node.children)


def _plain(value):
    return SPACES.sub(' ', value.replace('\u00a0', ' ').replace('\x00', '')).strip()


def image_ids(markup):
    """The attachment ids the images of stored rich text refer to, in order, each once."""
    if not markup:
        return []
    return list(dict.fromkeys(block['attachment'] for block in Document.parse(markup).blocks if block['type'] == 'image'))


def _text(value):
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _attribute(value):
    return _text(value).replace('"', '&quot;')


def _spaced(pieces):
    """Text with the spaces HTML would collapse written as no-break spaces: at the start and end of a
    line and after another space. Returns pieces with their text encoded."""
    out = []
    flat = []
    for index, piece in enumerate(pieces):
        if piece[0] == 'text':
            flat.extend((index, char) for char in piece[1])
        else:
            flat.append((index, None))
    encoded = {index: [] for index in range(len(pieces))}
    previous_plain_space = True  # start of the line
    for position, (index, char) in enumerate(flat):
        if char is None:
            previous_plain_space = True
            continue
        if char == ' ':
            following = flat[position + 1][1] if position + 1 < len(flat) else None
            if previous_plain_space or following is None:
                encoded[index].append('&nbsp;')
                previous_plain_space = False
            else:
                encoded[index].append(' ')
                previous_plain_space = True
        else:
            encoded[index].append(_text(char))
            previous_plain_space = False
    for index, piece in enumerate(pieces):
        out.append(['text', ''.join(encoded[index]), piece[2], piece[3]] if piece[0] == 'text' else piece)
    return out


def _inline(pieces):
    return _group(_spaced(pieces), 0)


def _group(pieces, level):
    """Wrap runs that share the link (level 0), then each mark in MARKS order (levels 1 to 5), so the
    nesting is canonical: the same text and marks always give the same HTML."""
    if level > len(MARKS):
        return ''.join('<br>' if piece[0] != 'text' else piece[1] for piece in pieces)
    out, index = [], 0
    while index < len(pieces):
        key = _key(pieces[index], level)
        end = index + 1
        while end < len(pieces) and _key(pieces[end], level) == key and (key is not None or pieces[end][0] == pieces[index][0] == 'text'):
            end += 1
        if pieces[index][0] != 'text':
            out.append('<br>')
        elif key is None:
            out.append(_group(pieces[index:end], level + 1))
        elif level == 0:
            out.append(f'<a href="{_attribute(key)}">{_group(pieces[index:end], 1)}</a>')
        else:
            out.append(f'<{key}>{_group(pieces[index:end], level + 1)}</{key}>')
        index = end
    return ''.join(out)


def _key(piece, level):
    """What a piece is wrapped in at this level: its link at 0, else the mark MARKS[level - 1] if it has it."""
    if piece[0] != 'text':
        return None
    if level == 0:
        return piece[3]
    mark = MARKS[level - 1]
    return mark if mark in piece[2] else None


def _lists(items):
    out, stack = [], []  # stack: list kind per open level
    for item in items:
        depth = min(item['indent'], len(stack))
        while len(stack) > depth + 1:
            out.append(f'</li></{_list_tag(stack.pop())}>')
        if len(stack) == depth + 1:
            if stack[-1] != item['list']:
                out.append(f'</li></{_list_tag(stack.pop())}>')
            else:
                out.append('</li>')
        if len(stack) == depth:
            out.append('<ul data-type="checklist">' if item['list'] == 'check' else f'<{_list_tag(item["list"])}>')
            stack.append(item['list'])
        checked = ' data-checked="true"' if item['list'] == 'check' and item['checked'] else ''
        out.append(f'<li{checked}>{_inline(item["content"])}')
    while stack:
        out.append(f'</li></{_list_tag(stack.pop())}>')
    return ''.join(out)


def _list_tag(kind):
    return 'ol' if kind == 'ordered' else 'ul'


def sanitise(markup):
    """Canonical safe HTML for any input text, and its plain-text extract: (html, text)."""
    if not isinstance(markup, str):
        raise ValueError('Expected HTML text')
    document = Document.parse(markup.replace('\x00', ''))
    if document.empty():
        return '', ''
    return document.html(), document.text()


def plain_text(markup):
    """The plain-text extract of stored rich text, for search, list cells and exports."""
    return '' if not markup else sanitise(markup)[1]
