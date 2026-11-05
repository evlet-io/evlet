"""Rich text: the server sanitiser (the security boundary), the RichText field and its routes.

docs/internal/rich-text.md. The cases in frontend/src/evlet/tests/richtext-cases.json are shared with
the browser editor's tests, so both sides write the same canonical HTML."""
from html.parser import HTMLParser
import json
from pathlib import Path
import random
import re
from urllib.parse import quote

import pytest

from evlet import Model, RichText, Text, ValidationError
from evlet.richtext import plain_text, safe_href, sanitise
from tests.test_business import client, hashed, secured  # noqa: F401  (fixtures)

CASES = json.loads((Path(__file__).parents[1] / 'frontend/src/evlet/tests/richtext-cases.json').read_text())['cases']
ALLOWED = {'p': set(), 'h2': set(), 'h3': set(), 'ul': {'data-type'}, 'ol': set(), 'li': {'data-checked'}, 'blockquote': set(),
           'pre': set(), 'code': set(), 'hr': set(), 'br': set(), 'strong': set(), 'em': set(), 'u': set(), 's': set(), 'a': {'href'},
           'figure': {'class'}, 'img': {'data-attachment', 'alt'}, 'figcaption': set()}


class Checker(HTMLParser):
    """Fails on any tag or attribute outside the schema, and on any unsafe link."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.problems = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.problems.append(f'tag {tag}')
            return
        for name, value in attrs:
            if name not in ALLOWED[tag]:
                self.problems.append(f'{tag}[{name}]')
            if name == 'href' and not re.match(r'(https?://|mailto:)', value or ''):
                self.problems.append(f'href {value!r}')
            if name == 'data-type' and value != 'checklist' or name == 'data-checked' and value != 'true':
                self.problems.append(f'{name}={value!r}')
            if name == 'class' and value not in ('rt-sm', 'rt-md', 'rt-full') or name == 'data-attachment' and not re.fullmatch(r'[1-9][0-9]*', value or ''):
                self.problems.append(f'{name}={value!r}')

    def handle_comment(self, data):
        self.problems.append('comment')


def check(markup):
    checker = Checker()
    checker.feed(markup)
    checker.close()
    assert not checker.problems, (markup, checker.problems)


@pytest.mark.parametrize('source, expected, text', CASES)
def test_shared_cases(source, expected, text):
    assert sanitise(source) == (expected, text)
    # Canonical: the stored form comes back unchanged.
    assert sanitise(expected) == (expected, text)
    check(expected)


HOSTILE = [
    '<script>alert(1)</script>',
    '<SCRIPT SRC=//x.io/x.js></SCRIPT>',
    '<scr<script>ipt>alert(1)</script>',
    '<p onclick="alert(1)" onmouseover=alert(1)>x</p>',
    '<a href="javascript:alert(1)">x</a>',
    '<a href="JaVaScRiPt:alert(1)">x</a>',
    '<a href="java\tscript:alert(1)">x</a>',
    '<a href="java&#x09;script:alert(1)">x</a>',
    '<a href="&#106;&#97;&#118;&#97;&#115;&#99;&#114;&#105;&#112;&#116;&#58;alert(1)">x</a>',
    '<a href=" \x01javascript:alert(1)">x</a>',
    '<a href="vbscript:msgbox(1)">x</a>',
    '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
    '<a href="//evil.example/x">x</a>',
    '<a href="/relative">x</a>',
    '<img src=x onerror=alert(1)>',
    '<img src="data:image/svg+xml,<svg onload=alert(1)>">',
    '<svg onload=alert(1)><circle/></svg>',
    '<svg><script>alert(1)</script></svg>',
    '<math><mi xlink:href="javascript:alert(1)">x</mi></math>',
    '<iframe src="javascript:alert(1)"></iframe>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<object data="x.swf"></object><embed src="x.swf">',
    '<style>body{background:url(javascript:alert(1))}</style>',
    '<p style="background:url(javascript:alert(1))">x</p>',
    '<div style="behavior:url(x.htc)">x</div>',
    '<link rel=stylesheet href=//x.io/x.css>',
    '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',
    '<form action="javascript:alert(1)"><input type=submit></form>',
    '<button formaction="javascript:alert(1)">x</button>',
    '<a href="https://ok.example" target="_blank" rel="opener" ping="https://x.io">x</a>',
    '<template><script>alert(1)</script></template>',
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>',
    '<!-- <script>alert(1)</script> -->',
    '<![CDATA[<script>alert(1)</script>]]>',
    '<?xml version="1.0"?><p>x</p>',
    '<textarea><script>alert(1)</script></textarea>',
    '<title><script>alert(1)</script></title>',
    '<p id="x" class="y" data-x="z" contenteditable="true" tabindex="0">x</p>',
    '<li data-checked="javascript:alert(1)">x</li>',
    '<ul data-type="javascript">x</ul>',
    '<a href="https://ok.example" onfocus="alert(1)" autofocus>x</a>',
    '<base href="javascript:alert(1)//">',
    '<video><source onerror="alert(1)"></video>',
    '<details open ontoggle=alert(1)>x</details>',
    '<p>\x00null</p>',
    '<a href="https://x.io/\x00">x</a>',
]


@pytest.mark.parametrize('markup', HOSTILE)
def test_hostile_input_never_survives(markup):
    stored, text = sanitise(markup)
    check(stored)
    lowered = stored.lower()
    for bad in ('<script', 'javascript', 'vbscript', 'data:', 'onerror', 'onload', 'onclick', 'style=', '<svg', '<iframe',
                '<img', 'srcdoc', '\x00'):
        assert bad not in lowered, (markup, stored)
    assert sanitise(stored) == (stored, text)


def test_script_and_style_text_is_dropped_not_kept_as_text():
    assert sanitise('<script>alert(1)</script><p>a</p>') == ('<p>a</p>', 'a')
    assert sanitise('<style>p { color: red }</style>b') == ('<p>b</p>', 'b')


def test_urls():
    assert safe_href('https://example.com/a?b=1') == 'https://example.com/a?b=1'
    assert safe_href('mailto:a@example.com') == 'mailto:a@example.com'
    assert safe_href('  https://example.com  ') == 'https://example.com'
    assert safe_href('ht\ntps://example.com') == 'https://example.com'
    for bad in ('javascript:alert(1)', 'data:text/html,x', 'ftp://x.io', '//x.io', '/a', '#x', 'https://', 'mailto:',
                'https://x.io:99999', 'tel:123', 'https://x .io', 'x' * 3000):
        assert safe_href(bad) is None, bad


def test_lists_nest_three_levels_and_never_skip_one():
    stored, _ = sanitise('<ul><li><ul><li><ul><li><ul><li><ul><li>deep</li></ul></li></ul></li></ul></li></ul></li></ul>')
    assert stored.count('<ul>') == 3
    # An item cannot be two levels deeper than the one before it.
    assert sanitise('<p>a</p><ul><li><ul><li><ul><li>x</li></ul></li></ul></li></ul>')[0].count('<ul>') == 3
    mixed = '<ul><li>a<ol><li>b</li></ol></li></ul><ul data-type="checklist"><li data-checked="true">c</li></ul>'
    assert sanitise(mixed)[0] == mixed


def test_pasted_aliases_and_blocks():
    assert sanitise('<b>bold</b> <i>it</i> <strike>s</strike>')[0] == '<p><strong>bold</strong> <em>it</em> <s>s</s></p>'
    assert sanitise('<h1>Big</h1><h5>Small</h5>')[0] == '<h2>Big</h2><h3>Small</h3>'
    assert sanitise('<blockquote>quoted<h2>head</h2></blockquote>')[0] == '<blockquote><p>quoted</p><p>head</p></blockquote>'
    assert sanitise('<pre>line 1\nline 2</pre>') == ('<pre><code>line 1\nline 2</code></pre>', 'line 1\nline 2')
    assert sanitise('text <p>para</p> tail')[0] == '<p>text</p><p>para</p><p>tail</p>'
    assert sanitise('<o:p></o:p><span lang=EN>Word</span>')[0] == '<p>Word</p>'


def test_whitespace_round_trips():
    for text in ('a  b', ' lead', 'trail ', 'a   b', '   '):
        stored, extract = sanitise('<p>' + text.replace(' ', '&nbsp;') + '</p>')
        if text.strip():
            assert extract == text
            assert sanitise(stored) == (stored, extract)
    assert sanitise('<p>\n  wrapped\n  source\n</p>')[1] == 'wrapped source'


def test_plain_text():
    assert plain_text('<h2>T</h2><ul><li>a</li><li>b<br>c</li></ul><hr><p>d</p>') == 'T\na\nb\nc\nd'
    assert plain_text(None) == '' and plain_text('') == ''


def test_random_fuzz_keeps_only_the_schema():
    pool = ['p', 'h1', 'h2', 'h3', 'ul', 'ol', 'li', 'blockquote', 'pre', 'code', 'strong', 'b', 'em', 'i', 'u', 's', 'a',
            'span', 'div', 'table', 'td', 'script', 'style', 'svg', 'img', 'iframe', 'font', 'br', 'hr', 'math']
    attrs = ['href="https://x.io"', 'href="javascript:alert(1)"', 'onclick="alert(1)"', 'style="color:red"',
             'data-type="checklist"', 'data-checked="true"', 'class="x"', 'src="data:x"', '']
    words = ['alpha', ' ', '&amp;', '&lt;', '&nbsp;', '<', '>', '"', 'javascript:', '\n', 'béta', '<!--x-->']
    rng = random.Random(20261104)
    for _ in range(600):
        parts = []
        for _ in range(rng.randint(1, 30)):
            roll = rng.random()
            tag = rng.choice(pool)
            if roll < 0.4:
                parts.append(f'<{tag} {rng.choice(attrs)}>')
            elif roll < 0.6:
                parts.append(f'</{tag}>')
            else:
                parts.append(rng.choice(words))
        source = ''.join(parts)
        stored, text = sanitise(source)
        check(stored)
        assert 'javascript' not in stored.lower().replace('javascript:</', '') or 'javascript:' in text
        assert sanitise(stored) == (stored, text), source


class Note(Model):
    title = Text(required=True)
    body = RichText(max_length=50)


def test_field_cleans_every_write():
    field = Note.__fields__['body']
    assert field.clean('<p onclick=x>Hi <script>x</script></p>') == '<p>Hi</p>'
    assert field.clean('<p></p><p> </p>') is None
    assert field.clean(None) is None
    with pytest.raises(ValueError, match='at most 50'):
        field.clean('<p>' + 'x' * 51 + '</p>')
    with pytest.raises(ValueError, match='too much formatting'):
        RichText(max_length=1000).clean('<p><strong><em><u><s>y</s></u></em></strong></p>' * 400)
    with pytest.raises(ValueError, match='Expected HTML text'):
        field.clean(12)
    required = RichText(required=True)
    with pytest.raises(ValueError, match='required'):
        required.clean('<p><br></p>')
    assert field.columns('body') == (('body', 'TEXT'), ('body_text', 'TEXT'))
    assert field.to_columns('<p>a<br>b</p>') == ('<p>a<br>b</p>', 'a\nb')
    assert field.metadata()['type'] == 'richtext'
    with pytest.raises(ValueError):
        RichText(unique=True)


def test_routes_store_sanitised_html_and_search_the_text(secured):
    admin = client(secured)
    status, schema, _ = admin('GET', '/api/schema')
    assert status == 200
    project = next(model for model in schema['models'] if model['name'] == 'project')
    brief = next(field for field in project['fields'] if field['name'] == 'brief')
    assert brief['type'] == 'richtext'
    assert set(brief['operators']) == {'contains', 'icontains', 'nicontains', 'isnull'}
    hostile = '<h2>Scope</h2><p onmouseover="x()">Fit out <a href="javascript:alert(1)">Dubai</a> office</p><script>x()</script>'
    status, created, _ = admin('POST', '/api/project', {'title': 'Fit out', 'brief': hostile})
    assert status == 201, created
    assert created['brief'] == '<h2>Scope</h2><p>Fit out Dubai office</p>'
    assert 'brief_text' not in created
    status, other, _ = admin('POST', '/api/project', {'title': 'Other', 'brief': '<p>strong words</p>'})
    assert status == 201
    with secured.database.transaction() as tx:
        row = tx.connection.execute("SELECT brief, brief_text FROM project WHERE title = 'Fit out'").fetchone()
    assert tuple(row) == ('<h2>Scope</h2><p>Fit out Dubai office</p>', 'Scope\nFit out Dubai office')
    # Filters match the text, never the markup: "strong" is a tag name in no stored text but Other's.
    def find(filters):
        status, found, _ = admin('GET', '/api/project?filters=' + quote(json.dumps(filters)))
        return status, found
    status, found = find({'brief__icontains': 'strong'})
    assert status == 200, found
    assert [item['title'] for item in found['items']] == ['Other']
    status, found = find({'brief__icontains': 'h2'})
    assert status == 200 and found['items'] == []
    status, found = find({'brief__icontains': 'dubai'})
    assert [item['title'] for item in found['items']] == ['Fit out']
    status, found = find({'brief__isnull': True})
    assert status == 200 and found['items'] == []
    status, error = find({'brief__eq': 'x'})
    assert status == 422
    # Search over a rich text field reads the text column too.
    from evlet.query import SqlQuery
    from examples.playground.models import Project
    where, values = SqlQuery(secured.registry, Project).where({}, 'dubai', search_fields=['brief'])
    assert 'brief_text' in where and 'dubai' in values
    assert status == 422


try:
    from evlet.printing import available as print_available
except ImportError:  # pragma: no cover
    def print_available():
        return False


@pytest.mark.skipif(not print_available(), reason='needs the print extra: pip install evlet[print]')
def test_print_renders_sanitised_rich_text(secured):
    from markupsafe import Markup
    from evlet import PrintDesign, User
    from evlet.printing import Compiler, Fields, Text as Words, display, document_context
    from examples.playground.models import Project

    class BriefPrint(PrintDesign):
        model = Project
        title = 'Brief'
        main = (Fields('brief'), Words(html='<div class="t">{{ doc.brief }}</div>'))

    admin = client(secured)
    status, created, _ = admin('POST', '/api/project', {'title': 'Fit out', 'brief':
        '<h2>Scope</h2><ul data-type="checklist"><li data-checked="true">Survey</li></ul><p><a href="https://x.io">Plan</a></p>'})
    assert status == 201, created
    business = secured.business
    with secured.database.transaction() as tx:
        row = tx.connection.execute("SELECT id FROM evlet_users WHERE username = 'admin'").fetchone()
        person = User(row['id'], 'admin', 'admin')
        context = document_context(business, tx, person, Project, business.policy.record(tx, person, Project, created['id']))
    assert isinstance(context['doc']['brief'], Markup)
    html = Compiler(BriefPrint, context, business).html()
    assert html.count('<h2>Scope</h2>') == 2
    assert '<li data-checked="true">Survey</li>' in html and '<a href="https://x.io">Plan</a>' in html
    # Display sanitises again: a value that never went through the field cannot inject markup.
    shown = display(Project.__fields__['brief'], '<p onclick="x()">a<script>b()</script></p>', {}, None, business, BriefPrint)
    assert str(shown) == '<div class="evlet-rich"><p>a</p></div>'


@pytest.mark.parametrize('source, expected, text', json.loads((Path(__file__).parents[1] / 'frontend/src/evlet/tests/richtext-cases.json').read_text())['mention_cases'])
def test_shared_mention_cases(source, expected, text):
    assert sanitise(source, mentions=True) == (expected, text)
    assert sanitise(expected, mentions=True) == (expected, text)
