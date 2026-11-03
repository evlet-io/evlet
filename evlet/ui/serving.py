"""Serve the bundled browser runtime and app frontends; all record APIs keep their own guards."""

from functools import cache
import json
from pathlib import Path, PurePosixPath
import re
from ..business import Download

CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
STATIC = Path(__file__).with_name("static")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}
# What a static frontend may serve; anything else answers 404.
FRONTEND_TYPES = {
    **TYPES,
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".json": "application/json",
    ".map": "application/json",
}
HEAD = re.compile(rb"<head(?:\s[^>]*)?>", re.IGNORECASE)
HTML = re.compile(rb"<html(?=[\s>])", re.IGNORECASE)
EXISTING = re.compile(rb'<script id="evlet-config" type="application/json">.*?</script>', re.DOTALL)


@cache
def version():
    from importlib import metadata
    try:
        return metadata.version("evlet")
    except metadata.PackageNotFoundError:
        return ""


def config_block(values):
    """The inline, non-executable runtime config every served page carries."""
    text = json.dumps({**values, "version": version()}, separators=(",", ":"))
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return b'<script id="evlet-config" type="application/json">' + text.encode() + b"</script>"


def with_config(html, values, base=None):
    """Put the config block (and a base element for relative asset paths) at the top of <head>."""
    html = EXISTING.sub(b"", html)
    block = config_block(values)
    if base is not None and b"<base" not in html.lower():
        block = b'<base href="' + base.encode() + b'">' + block
    found = HEAD.search(html)
    return html[: found.end()] + block + html[found.end():] if found else block + html


def page(content, content_type, csp=CSP):
    return 200, Download(content), [(b"content-type", content_type.encode()), (b"content-security-policy", csp.encode())]


def static_response(path, config=None, theme=None):
    """A built asset, or index.html with the config block and the page's theme (light or dark) on <html>."""
    if path.startswith("/assets/"):
        relative = path.lstrip("/")
        if ".." in Path(relative).parts or "\\" in relative:
            return None
        target = STATIC / relative
        if target.suffix not in TYPES or not target.is_file():
            return None
    else:
        target = STATIC / "index.html"
        if not target.is_file():
            return None
    content = target.read_bytes()
    if config is not None and target.suffix == ".html":
        content = with_config(content, config)
    theme = theme or (config or {}).get("theme")
    if theme in ("light", "dark") and target.suffix == ".html":
        # The chosen theme is on <html> before the first paint; "system" leaves it to the stylesheet.
        content = HTML.sub(b'<html data-theme="' + theme.encode() + b'"', content, count=1)
    return page(content, TYPES[target.suffix])


def frontend_response(folder, rest, config, base, connect=()):
    """A file from a static frontend's dist folder, or its index.html for any path without a file type."""
    if folder is None or not folder.is_dir():
        return None
    relative = rest.lstrip("/")
    parts = PurePosixPath(relative).parts
    if "\\" in relative or "\x00" in relative or any(part == ".." or part.startswith(".") for part in parts):
        return None
    suffix = PurePosixPath(relative).suffix.lower() if relative else ""
    if suffix:
        target = (folder / relative).resolve()
        if suffix not in FRONTEND_TYPES or not target.is_relative_to(folder) or not target.is_file():
            return None
    else:
        target = (folder / "index.html").resolve()
        suffix = ".html"
        if not target.is_relative_to(folder) or not target.is_file():
            return None
    content = target.read_bytes()
    if suffix == ".html":
        content = with_config(content, config, base.rstrip("/") + "/")
    csp = CSP.replace("connect-src 'self'", " ".join(("connect-src 'self'", *connect)))
    return page(content, FRONTEND_TYPES[suffix], csp)
