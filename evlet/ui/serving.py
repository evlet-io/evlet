"""Serve the bundled browser runtime; all record APIs keep their own guards."""

from pathlib import Path
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


def static_response(path):
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
    return (
        200,
        Download(target.read_bytes()),
        [
            (b"content-type", TYPES[target.suffix].encode()),
            (
                b"content-security-policy",
                CSP.encode(),
            ),
        ],
    )
