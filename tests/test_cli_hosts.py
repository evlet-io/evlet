"""evlet host add/remove/list, safe routes.py edits, and the host evlet install offers for suggested_host."""

import functools
import re
import shutil

import pytest

from evlet.cli import hosts, proxy, site
from evlet.cli.instance import set_domain
from evlet.cli.main import main
from test_business import http
from test_cli_lifecycle import environment_stub, project, runtime  # noqa: F401  (fixtures)


def write_site(root):
    package = root / 'apps/notes/notes'
    (package / 'frontends.py').write_text('from evlet import Frontend\n\nwww = Frontend("www", kind="static", dist="site", path="/www", auth=None, '
                                          'routing="pages", suggested_host="www")\n')
    hooks = package / 'hooks.py'
    hooks.write_text(hooks.read_text().replace('from .migrations import migrations', 'from .migrations import migrations\nfrom .frontends import www')
                     .replace('migrations=migrations)', 'migrations=migrations, frontends=(www,))'))
    (package / 'site/about').mkdir(parents=True)
    (package / 'site/index.html').write_text('<html><head></head><body>Welcome</body></html>')
    (package / 'site/about/index.html').write_text('<html><head></head><body>About</body></html>')


@pytest.fixture
def web(project, monkeypatch):
    write_site(project)
    set_domain(project, 'acme', 'erp.acme.com')
    monkeypatch.setattr(proxy, 'check', functools.partial(proxy.check, resolve=lambda host, port, proto: [(0, 0, 0, '', ('203.0.113.7', 443))]))
    monkeypatch.setattr(site, 'site_answers', lambda host: f'https://{host} answers (status 200).')
    return project


def routes_text(root):
    return (root / 'instances/acme/routes.py').read_text()


def test_install_offers_the_suggested_host_and_yes_takes_it(web, capsys):
    main(['install', 'notes', '--instance', 'acme', '--trust', '--yes', '--project', str(web)])
    out = capsys.readouterr().out
    assert routes_text(web) == 'from evlet import Host\n\nhosts = (\n    Host("www.erp.acme.com", root="notes.www"),\n)\n'
    assert 'Added www.erp.acme.com to instances/acme/routes.py, served by notes.www' in out
    assert 'Next: evlet host add acme www.erp.acme.com --root notes.www --apply' in out
    app = runtime(web)
    try:
        assert app.routes.public('www.erp.acme.com')
        status, page, _ = http(app, 'GET', '/about/', headers={b'host': b'www.erp.acme.com'})
        assert status == 200 and b'About' in page and b'evlet-config' not in page
    finally:
        app.database.close()


def test_install_without_a_terminal_prints_the_command_and_no_host_skips(web, capsys):
    main(['install', 'notes', '--instance', 'acme', '--trust', '--project', str(web)])
    assert 'notes.www suggests the host www.erp.acme.com. Serve it with: evlet host add acme www.erp.acme.com --root notes.www' in capsys.readouterr().out
    assert not (web / 'instances/acme/routes.py').exists()


def test_install_skips_with_no_host_and_asks_for_a_domain(web, capsys):
    main(['install', 'notes', '--instance', 'acme', '--trust', '--no-host', '--project', str(web)])
    assert 'suggests' not in capsys.readouterr().out and not (web / 'instances/acme/routes.py').exists()
    questions = []
    assert hosts.suggest(web, 'acme', {'notes'}, ask=lambda text: questions.append(text) or 'no') == []
    assert 'Press Enter for www.erp.acme.com' in questions[0]
    assert hosts.suggest(web, 'acme', {'notes'}, ask=lambda text: 'acme.example') == ['acme.example']
    assert 'Host("acme.example", root="notes.www")' in routes_text(web)
    # Already served: nothing more to offer.
    assert hosts.suggest(web, 'acme', {'notes'}, yes=True) == []


def test_routes_py_edits_append_keep_the_rest_and_refuse_what_they_cannot_merge(tmp_path):
    path = tmp_path / 'acme'
    path.mkdir()
    target = path / 'routes.py'
    target.write_text('"""Acme routes."""\nfrom evlet import Docs\nfrom crm.frontends import portal\n\ndocs = Docs(enabled=False)\n')
    assert hosts.add_host_line(path, 'www.acme.com', 'site.www')
    assert target.read_text() == ('"""Acme routes."""\nfrom evlet import Docs\nfrom crm.frontends import portal\nfrom evlet import Host\n\n'
                                  'docs = Docs(enabled=False)\n\nhosts = (\n    Host("www.acme.com", root="site.www"),\n)\n')
    assert not hosts.add_host_line(path, 'www.acme.com', 'site.www')
    with pytest.raises(ValueError, match='already serves www.acme.com with root site.www'):
        hosts.add_host_line(path, 'www.acme.com', 'site.other')
    target.write_text(target.read_text().replace('    Host("www.acme.com", root="site.www"),\n', '    Host("portal.acme.com", root=portal, also_on_primary=True),\n'
                                                                                                   '    Host("www.acme.com", root="site.www"),\n'))
    assert hosts.add_host_line(path, 'docs.acme.com', 'site.docs')
    text = target.read_text()
    assert text.count('from evlet import Host') == 1 and 'Host("portal.acme.com", root=portal, also_on_primary=True),\n    Host("www.acme.com"' in text
    assert text.endswith('    Host("docs.acme.com", root="site.docs"),\n)\n')
    assert hosts.remove_host_line(path, 'www.acme.com') and not hosts.remove_host_line(path, 'www.acme.com')
    assert 'www.acme.com' not in target.read_text() and 'docs.acme.com' in target.read_text()
    for hand_edited in ('hosts = make_hosts()\n', 'hosts = ()\nhosts += (Host("a.acme.com"),)\n', 'hosts = (\n    Host("a.acme.com", root="x.y"),  # ours\n)\n',
                        'hosts = (*OTHER, Host("a.acme.com"))\n', 'if True:\n    hosts = ()\n'):
        target.write_text('from evlet import Host\n' + hand_edited)
        with pytest.raises(ValueError, match='cannot change safely. Edit it yourself: add Host\\("b.acme.com", root="x.y"\\)'):
            hosts.add_host_line(path, 'b.acme.com', 'x.y')
        assert target.read_text() == 'from evlet import Host\n' + hand_edited
    target.unlink()
    assert not hosts.remove_host_line(path, 'www.acme.com')


def test_host_add_checks_dns_writes_caddy_and_applies_with_sudo(web, capsys, monkeypatch, tmp_path):
    main(['install', 'notes', '--instance', 'acme', '--trust', '--no-host', '--project', str(web)])
    capsys.readouterr()
    command = ['host', 'add', 'acme', 'www.acme.com', '--root', 'notes.www', '--project', str(web)]
    main(command)
    out = capsys.readouterr().out
    assert re.search(r'www\.acme\.com +203\.0\.113\.7 ', out)
    assert 'Host("www.acme.com", root="notes.www")' in routes_text(web)
    caddy = (web / 'deploy/evlet-acme.caddy').read_text()
    assert 'www.acme.com {' in caddy and 'erp.acme.com {' in caddy and '@primary_only' not in caddy and 'strict_sni_host' not in caddy
    assert 'strict_sni_host on' in (web / 'deploy/evlet-global.caddy').read_text()
    assert 'sudo systemctl restart evlet-acme' in out and 'sudo systemctl reload caddy' in out and 'Or run the same command again with --apply' in out
    # --apply runs the same steps, each shown first.
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/' + name)
    caddyfile = tmp_path / 'Caddyfile'
    caddyfile.write_text('import /etc/caddy/evlet-global.caddy\nimport /etc/caddy/evlet-acme.caddy\n')
    monkeypatch.setattr(site, 'CADDYFILE', str(caddyfile))
    ran = []
    monkeypatch.setattr(site.subprocess, 'run', lambda args, **values: ran.append(args))
    main(command + ['--apply'])
    out = capsys.readouterr().out
    assert 'already serves www.acme.com with notes.www' in out and 'https://www.acme.com answers' in out
    assert ['sudo', 'systemctl', 'restart', 'evlet-acme'] in ran and ran[-1] == ['sudo', 'systemctl', 'reload', 'caddy']
    assert out.count('$ sudo') == len(ran)
    main(['host', 'list', 'acme', '--project', str(web)])
    listed = capsys.readouterr().out
    assert listed.splitlines()[1].split() == ['erp.acme.com', 'evlet.desk', 'desk']
    assert listed.splitlines()[2].split() == ['www.acme.com', 'notes.www', 'public']
    main(['host', 'remove', 'acme', 'www.acme.com', '--routes-only', '--project', str(web)])
    assert 'Restart the instance' in capsys.readouterr().out and 'www.acme.com' not in routes_text(web)
    with pytest.raises(SystemExit):
        main(['host', 'remove', 'acme', 'www.acme.com', '--project', str(web)])
    assert 'does not list www.acme.com' in capsys.readouterr().err


def test_host_add_refuses_before_writing(web, capsys, monkeypatch):
    main(['install', 'notes', '--instance', 'acme', '--trust', '--no-host', '--project', str(web)])
    capsys.readouterr()
    for host, root, message in (('www.acme.com', 'notes.missing', 'not a frontend of an installed app'),
                                ('erp.acme.com', 'notes.www', 'primary host'),
                                ('shop.localhost', 'notes.www', 'public lowercase hostname'),
                                ('WWW.acme.com', 'notes.www', 'public lowercase hostname')):
        with pytest.raises(SystemExit):
            main(['host', 'add', 'acme', host, '--root', root, '--project', str(web)])
        assert message in capsys.readouterr().err
    monkeypatch.setattr(proxy, 'check', functools.partial(proxy.check, resolve=lambda *args, **kwargs: []))
    with pytest.raises(SystemExit):
        main(['host', 'add', 'acme', 'www.acme.com', '--root', 'notes.www', '--project', str(web)])
    assert 'no usable A or AAAA record' in capsys.readouterr().err
    assert not (web / 'instances/acme/routes.py').exists() and not (web / 'deploy').exists()
    # With on-demand TLS only routes.py changes; no DNS check and no Caddy file.
    main(['host', 'add', 'acme', 'www.acme.com', '--root', 'notes.www', '--routes-only', '--project', str(web)])
    assert 'on-demand TLS' in capsys.readouterr().out and not (web / 'deploy').exists()
    with pytest.raises(SystemExit):
        main(['host', 'add', 'acme', 'www.acme.com', '--root', 'notes.www', '--routes-only', '--apply', '--project', str(web)])


def test_a_routes_py_that_no_longer_starts_is_put_back(web, capsys):
    main(['install', 'notes', '--instance', 'acme', '--trust', '--no-host', '--project', str(web)])
    target = web / 'instances/acme/routes.py'
    target.write_text('from evlet import Host\n\nhosts = ()\n')
    with pytest.raises(ValueError):
        hosts._checked(web, 'acme', lambda: hosts.add_host_line(web / 'instances/acme', 'www.acme.com', 'notes.nothing'))
    assert target.read_text() == 'from evlet import Host\n\nhosts = ()\n'
