import pytest
from test_business import secured, hashed, client, http  # noqa: F401


def test_revision_checks_prevent_stale_update_delete_and_action(secured):
    c = client(secured)
    record = c(
        "POST",
        "/api/project",
        {"title": "Original", "children": {"tasks": [{"title": "Task"}]}},
    )[1]
    path = "/api/project/" + str(record["id"])
    _, _, headers = c("GET", path)
    revision = headers[b"etag"]
    assert (
        c("PATCH", path, {"title": "Changed"}, headers={b"if-match": revision})[0] == 200
    )
    for method, suffix, body in [
        ("PATCH", "", {"title": "Stale"}),
        ("DELETE", "", None),
        ("POST", "/actions/start", {}),
    ]:
        assert c(method, path + suffix, body, headers={b"if-match": revision})[0] == 409
    _, latest, headers = c("GET", path)
    assert latest["title"] == "Changed" and latest["status"] == "planned"
    task = latest["children"]["tasks"][0]
    assert (
        c(
            "PATCH",
            path,
            {"children": {"tasks": [{**task, "done": True}]}},
            headers={b"if-match": headers[b"etag"]},
        )[0]
        == 200
    )
    assert (
        c(
            "PATCH",
            path,
            {"title": "Child stale"},
            headers={b"if-match": headers[b"etag"]},
        )[0]
        == 409
    )
    # Existing clients without a revision remain compatible.
    assert c("PATCH", path, {"title": "Compatible"})[0] == 200


def test_static_ui_preserves_unknown_routes_and_api_authentication(secured):
    from evlet.ui.serving import STATIC

    if not (STATIC / "index.html").is_file():
        pytest.skip("Build browser assets before checking static serving")
    for path in (
        "/",
        "/login",
        "/docs",
        "/docs/components",
        "/app/customer",
        "/app/customer/new",
        "/app/customer/12",
        "/app/customer/12/view",
    ):
        status, payload, headers = http(secured, "GET", path)
        assert status == 200 and b'<div id="evlet-app">' in payload
        assert b"frame-ancestors 'none'" in headers[b"content-security-policy"]
    assert http(secured, "GET", "/missing")[0] == 404
    assert http(secured, "GET", "/assets/../app.py")[0] == 404
    assert http(secured, "GET", "/api/customer")[0] == 401


def test_tables_fill_a_frame_wider_than_their_columns():
    from evlet.ui.serving import STATIC
    styles = [path for path in (STATIC / "assets").glob("*.css")]
    if not styles:
        pytest.skip("Build browser assets before checking the stylesheet")
    css = "\n".join(path.read_text() for path in styles)
    # List and child tables are as wide as their set columns; in a wider frame the last column takes the rest.
    assert "min-width:100%" in css.split(".dt.fixed,table.g{", 1)[1].split("}", 1)[0]
    assert "width:auto!important" in css.split(".dt.fixed col:last-child,table.g col:last-child{", 1)[1].split("}", 1)[0]


def test_fonts_are_bundled_and_served_from_assets(secured):
    import re
    from evlet.ui.serving import STATIC
    styles = [path for path in (STATIC / "assets").glob("*.css")]
    if not styles:
        pytest.skip("Build browser assets before checking static serving")
    css = "\n".join(path.read_text() for path in styles)
    # IBM Plex Sans and JetBrains Mono come from /assets, never from another host, and swap in when loaded.
    fonts = sorted(set(re.findall(r"url\((?:/assets/)?([^)/]+\.woff2)\)", css)))
    assert len(fonts) == 12 and "fonts.googleapis.com" not in css and "font-display:swap" in css.replace(" ", "")
    assert "font-family:IBM Plex Sans;" in css and "font-family:JetBrains Mono;" in css and "ui-monospace" in css and "system-ui" in css
    for name in fonts:
        status, payload, headers = http(secured, "GET", "/assets/" + name)
        assert status == 200 and payload[:4] == b"wOF2" and headers[b"content-type"] == b"font/woff2"
        assert headers[b"content-security-policy"].startswith(b"default-src 'self'")


def test_rendered_deployment_preserves_browser_security_headers():
    from evlet.deployment import deployment_files
    from evlet.ui.serving import CSP
    caddy = deployment_files(['app.example.com'])['Caddyfile']
    assert f'Content-Security-Policy "{CSP}"' in caddy
    assert 'frame-ancestors' in caddy and 'Strict-Transport-Security' in caddy


def test_python_page_routes_are_gone(secured):
    """Python pages and /api/ui were removed in 0.4; the paths are ordinary unknown API routes."""
    c = client(secured)
    assert c("GET", "/api/ui")[0] == c("GET", "/api/ui/resolve?path=/app/customer")[0] == 404


def test_app_definitions_still_accept_an_empty_views_argument():
    """Apps scaffolded before 0.4 pass views=(); anything else names the removal."""
    from evlet import AppDefinition
    assert AppDefinition(views=()).views == ()
    for options in ({'views': (object(),)}, {'ui': object()}):
        with pytest.raises(ValueError, match='removed in Evlet 0.4'):
            AppDefinition(**options)
