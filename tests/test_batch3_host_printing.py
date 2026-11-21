"""Print templates are bounded in time, output and value size, without WeasyPrint."""

import threading

import pytest

pytest.importorskip('jinja2')

from evlet import printing
from evlet.auth import AccessError
from evlet.models import ValidationError
from evlet.printing import Budget, environment, fill


def filled(template, budget=None, **variables):
    return fill(environment(budget), template, variables)


def refused(template, budget=None, **variables):
    with pytest.raises(ValidationError) as caught:
        filled(template, budget, **variables)
    return caught.value.errors['design']


def test_ordinary_templates_still_fill():
    assert filled('{{ "-" * 3 }} {{ 2 ** 10 }} {{ "%s-%d" % ("a", 3) }} {{ "{:>4}".format("a") }} {{ "ab"|center(6) }}|') == \
        '--- 1024 a-3    a   ab  |'
    assert filled('{% for i in range(3) %}{{ i }}{% endfor %} {{ items|join(", ") }} {{ "%s"|format(5000000) }}', items=['x', 'y']) == \
        '012 x, y 5000000'
    # The sandbox's own guard stays: no safe filter.
    with pytest.raises(Exception, match="No filter named 'safe'"):
        filled('{{ value | safe }}', value='<b>')


@pytest.mark.parametrize('template', [
    '{{ "x" * 300000000 }}', '{{ "x" * 10 ** 10 }}', '{{ [1] * 2000000 }}', '{{ 2 ** 100000 }}',
    '{{ "%999999999s" % "a" }}', '{{ "{:>999999999}".format("a") }}', '{{ "a"|center(999999999) }}',
    '{{ "a"|indent(width=999999999) }}', '{{ ("x" * 900000)|replace("x", "yy") }}', '{{ ("x" * 900000)|replace("", "yy") }}',
    '{{ "%99999999d"|format(1) }}', '{{ ([("x" * 900000)] * 2) | join }}',
])
def test_huge_values_are_refused_before_they_are_made(template):
    assert refused(template) == printing.TOO_LARGE


def test_output_and_time_budgets_stop_runaway_loops():
    # Output: a nested loop that writes is stopped once the document's templates made too much text.
    assert refused('{% for a in range(1000) %}{% for b in range(1000) %}xxxxxxxxxx{% endfor %}{% endfor %}',
                   Budget(output=100_000)) == printing.TOO_LARGE
    # Time: a nested loop that writes nothing still stops at the deadline, here on a fake clock.
    now = [0.0]

    def clock():
        now[0] += 0.001
        return now[0]
    assert refused('{% for a in range(100000) %}{% for b in range(100000) %}{% endfor %}{% endfor %}',
                   Budget(seconds=1, clock=clock)) == printing.TOO_LONG
    # One budget covers every block of a document: a second fill sees what the first used.
    env = environment(Budget(output=10))
    assert fill(env, '{{ "x" * 6 }}', {}) == 'xxxxxx'
    with pytest.raises(ValidationError):
        fill(env, '{{ "y" * 6 }}', {})


def test_only_a_few_documents_lay_out_at_once(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(design, context, business):
        started.set()
        release.wait(5)
        return type('Document', (), {'write_pdf': lambda self: b'%PDF'})()
    monkeypatch.setattr(printing, 'document', slow)
    monkeypatch.setattr(printing, '_renders', threading.BoundedSemaphore(1))
    monkeypatch.setattr(printing, 'RENDER_WAIT', 0.05)
    worker = threading.Thread(target=printing.render, args=(None, None, None))
    worker.start()
    assert started.wait(5)
    with pytest.raises(AccessError) as caught:
        printing.render(None, None, None)
    assert caught.value.status == 503
    release.set()
    worker.join(5)
    assert printing.render(None, None, None) == b'%PDF'
