"""Offline, version-matched documentation without a project or database."""

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from importlib.resources import files
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def documentation():
    folder = files('evlet.resources').joinpath('docs')
    if not folder.is_dir():
        folder = Path(__file__).resolve().parents[2] / 'docs'
    result = {path.name: path.read_text(encoding='utf-8') for path in sorted(folder.glob('*.md')) if path.name != 'handoff.md'}
    if 'cli.md' not in result:
        raise ValueError('Installed documentation is incomplete')
    return result


def render_markdown(source):
    """Render our plain Markdown safely; raw HTML stays escaped."""
    parts, code = [], False
    for line in source.splitlines():
        if line.startswith('```'):
            parts.append('</code></pre>' if code else '<pre><code>')
            code = not code
        elif code:
            parts.append(escape(line) + '\n')
        elif match := re.match(r'^(#{1,6}) (.*)', line):
            level, title = len(match[1]), escape(match[2])
            parts.append(f'<h{level}>{title}</h{level}>')
        elif line.strip():
            text = escape(line)
            text = re.sub(r'\[([^\]]+)\]\(([^ )]+)\)', lambda item: '<a href="' + item[2] + '">' + item[1] + '</a>' if item[2].startswith('https://') or re.fullmatch(r'[a-z0-9_-]+\.md(?:#[a-z0-9_-]+)?', item[2]) else item[1], text)
            text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
            parts.append('<p>' + text + '</p>')
    if code:
        parts.append('</code></pre>')
    return ''.join(parts)


def handler(documents):
    class DocumentationHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = unquote(urlsplit(self.path).path).lstrip('/') or 'cli.md'
            if path not in documents:
                self.send_error(404)
                return
            nav = ''.join(f'<a href="/{escape(name)}">{escape(name.removesuffix(".md").replace("-", " "))}</a>' for name in documents)
            body = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>Evlet documentation</title><style>body{margin:0;font:16px/1.6 system-ui;color:#172339;background:#fafbfc}'
                    'nav{padding:24px;max-width:240px}nav a{display:block;padding:4px;color:#245aac}main{padding:28px;max-width:850px;min-width:0}'
                    '.layout{display:flex}pre{padding:16px;background:#eef1f5;overflow:auto}code{font-family:monospace}a{color:#245aac}'
                    '@media(max-width:700px){.layout{display:block}nav{max-width:none}nav a{display:inline-block;margin-right:12px}}</style>'
                    '<div class="layout"><nav><strong>Evlet ' + escape(metadata.version('evlet')) + '</strong>' + nav + '</nav><main>' + render_markdown(documents[path]) + '</main></div></html>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)
    return DocumentationHandler


def docs(*, port=8001):
    with ThreadingHTTPServer(('127.0.0.1', port), handler(documentation())) as server:
        print(f'Evlet documentation: http://127.0.0.1:{port}', flush=True)
        server.serve_forever()
