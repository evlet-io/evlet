"""Serve the bundled browser runtime and app frontends; all record APIs keep their own guards."""

from functools import cache
import json
import os
from pathlib import Path, PurePosixPath
import re
from ..business import Download

IMAGE_ORIGIN = re.compile(r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?::[1-9][0-9]{0,4})?")


def content_policy(image_origins=None):
    """The Content-Security-Policy of the pages Evlet serves.

    image_origins None lets pages show images from any https: address (records may hold image URLs). A list of
    exact https origins ("https://cdn.example.com") allows only those besides the app itself and data:; an empty
    list allows none. EVLET_IMAGE_ORIGINS sets this for the process (docs/routing.md)."""
    if image_origins is None:
        images = "https:"
    else:
        origins = image_origins.split() if isinstance(image_origins, str) else list(image_origins)
        if not all(isinstance(origin, str) and IMAGE_ORIGIN.fullmatch(origin) for origin in origins):
            raise ValueError('EVLET_IMAGE_ORIGINS lists exact https origins, such as https://cdn.example.com')
        images = " ".join(origins)
    sources = " ".join(part for part in ("'self'", images, "data:") if part)
    return (f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src {sources}; connect-src 'self'; "
            "frame-src 'self' blob:; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


CSP = content_policy(os.environ.get("EVLET_IMAGE_ORIGINS"))
STATIC = Path(__file__).with_name("static")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
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
    ".woff": "font/woff",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".json": "application/json",
    ".map": "application/json",
    # Files a public website serves: robots.txt, sitemap.xml, a web app manifest, documents.
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml",
    ".webmanifest": "application/manifest+json",
    ".pdf": "application/pdf",
}
NOT_FOUND = b"Not found\n"
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
    """The inline, non-executable runtime config every served page carries.

    values may set version itself: the routes send "" before sign-in, so a visitor cannot read the release."""
    text = json.dumps({**values, "version": values.get("version", version())}, separators=(",", ":"))
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return b'<script id="evlet-config" type="application/json">' + text.encode() + b"</script>"


def with_config(html, values, base=None):
    """Put the config block (and a base element for relative asset paths) at the top of <head>.

    values None leaves the block out and removes one the page carries: public hosts tell a visitor nothing."""
    html = EXISTING.sub(b"", html)
    block = b"" if values is None else config_block(values)
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
    if path == '/assets/evlet-offline.html':
        # A worker may cache this fixed public notice, never personalized HTML/config.
        config, theme = None, None
    if config is not None and target.suffix == ".html":
        content = with_config(content, config)
    theme = theme or (config or {}).get("theme")
    if theme in ("light", "dark") and target.suffix == ".html":
        # The chosen theme is on <html> before the first paint; "system" leaves it to the stylesheet.
        content = HTML.sub(b'<html data-theme="' + theme.encode() + b'"', content, count=1)
    response = page(content, TYPES[target.suffix])
    if path == '/assets/evlet-sw.js':
        response[2].extend([(b'service-worker-allowed', b'/'), (b'cache-control', b'no-cache')])
    return response


MANIFEST = "/assets/evlet.webmanifest"
# The existing Evlet colours: the page background and the brand blue of the icon.
BACKGROUND, THEME = "#f4f5f8", "#2563eb"


def manifest_response(name):
    """The web app manifest of the bundled desk: the instance's name, the Evlet icons, standalone from /.

    Browsers fetch it without credentials, so it says nothing more than the page title already does."""
    name = " ".join(str(name or "").split())[:45] or "Evlet"
    short = name if len(name) <= 12 else name.split(" ")[0][:12]
    icons = [{"src": f"/assets/evlet-icon-{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"}
             for size in (192, 512)]
    icons.append({"src": "/assets/evlet-icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"})
    body = {"id": "/", "name": name, "short_name": short, "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": BACKGROUND, "theme_color": THEME, "icons": icons}
    status, content, headers = page(json.dumps(body, indent=1).encode(), TYPES[".webmanifest"])
    return status, content, headers + [(b"cache-control", b"no-cache")]


def _file(folder, relative):
    """A regular file inside folder, or None: no dotfiles, no .., and no link that leaves the folder."""
    target = (folder / relative).resolve()
    return target if target.is_relative_to(folder) and target.is_file() else None


def not_found_page(folder, config=None, connect=()):
    """A static frontend's own 404.html with status 404, or a plain "Not found"."""
    target = _file(folder, "404.html") if folder is not None and folder.is_dir() else None
    if target is None:
        return 404, Download(NOT_FOUND), [(b"content-type", b"text/plain; charset=utf-8"), (b"content-security-policy", CSP.encode())]
    content = with_config(target.read_bytes(), config)
    return 404, Download(content), [(b"content-type", FRONTEND_TYPES[".html"].encode()), (b"content-security-policy", _csp(connect).encode())]


def _csp(connect):
    return CSP.replace("connect-src 'self'", " ".join(("connect-src 'self'", *connect)))


def frontend_response(folder, rest, config, base, connect=(), routing="app", query=b""):
    """A file from a static frontend's dist folder.

    routing="app": any path without a file type gets index.html, so the app routes in the browser.
    routing="pages": /about/ serves about/index.html and /about serves about.html. /about redirects to
    /about/ when only about/index.html exists, and /about/ to /about when only about.html exists. Any other
    path gets the frontend's 404.html with status 404, or a plain 404. config None leaves out the config block.
    """
    if folder is None or not folder.is_dir():
        return None
    pages = routing == "pages"
    missing = (lambda: not_found_page(folder, config, connect)) if pages else (lambda: None)
    relative = rest.lstrip("/")
    parts = PurePosixPath(relative).parts
    if "\\" in relative or "\x00" in relative or any(part == ".." or part.startswith(".") for part in parts):
        return missing()
    suffix = PurePosixPath(relative).suffix.lower() if relative and not relative.endswith("/") else ""
    if suffix:
        target = _file(folder, relative) if suffix in FRONTEND_TYPES else None
        if target is None:
            return missing()
    elif not pages:
        target, suffix = _file(folder, "index.html"), ".html"
        if target is None:
            return None
    else:
        name, suffix = relative.rstrip("/"), ".html"
        index = _file(folder, name + "/index.html") if name else _file(folder, "index.html")
        flat = _file(folder, name + ".html") if name else None
        prefix = base.rstrip("/") + "/"
        again = (b"?" + query) if query else b""
        if not name or relative.endswith("/"):
            target = index
            if target is None and flat is not None:
                return 301, Download(b""), [(b"location", (prefix + name).encode() + again)]
        else:
            target = flat
            if target is None and index is not None:
                return 301, Download(b""), [(b"location", (prefix + name + "/").encode() + again)]
        if target is None:
            return missing()
    content = target.read_bytes()
    if suffix == ".html":
        # A page of a multi-page site keeps its own relative links, so only app routing gets a base element.
        content = with_config(content, config, None if pages else base.rstrip("/") + "/")
    return page(content, FRONTEND_TYPES[suffix], _csp(connect))
