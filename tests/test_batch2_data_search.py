"""Link search reads bounded candidate sets without sorting every match (evlet/search.py ranked())."""

from evlet import Database, Model, Registry, Rule, Text, qb
from evlet import search


class Part(Model):
    class Meta:
        search_fields = ('code',)
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    title = Text(required=True)
    code = Text(default='')


def test_better_matches_win_beyond_the_candidate_limit(tmp_path):
    database = Database(tmp_path / 'db.sqlite3', Registry(Part))
    database.initialize()
    with database.transaction() as tx:
        # More in-order matches of "bolt" than CANDIDATES, then the real matches last by id.
        for number in range(search.CANDIDATES + 50):
            tx.create(Part, title=f'b{number} o l t', code='')
        holding = tx.create(Part, title='Hex bolt', code='')
        starting = tx.create(Part, title='Bolt M8', code='')
        exact = tx.create(Part, title='zz', code='BOLT')
    statements = []
    with database._connection() as connection:
        connection.set_trace_callback(statements.append)
        found = search.ranked(connection, Part, qb.table('part', 'record'), [], ['title', 'code'], 'bolt', 3)
    assert [record_id for record_id, _ in found] == [exact.id, starting.id, holding.id]
    assert dict(found)[exact.id] == 'code'
    selects = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]
    assert len(selects) == 2 and not any('ORDER BY' in sql.upper() for sql in selects)


def test_a_rare_text_is_one_query(tmp_path):
    database = Database(tmp_path / 'db.sqlite3', Registry(Part))
    database.initialize()
    with database.transaction() as tx:
        wanted = tx.create(Part, title='Washer', code='W-1')
        tx.create(Part, title='Nut', code='N-1')
    statements = []
    with database._connection() as connection:
        connection.set_trace_callback(statements.append)
        found = search.ranked(connection, Part, qb.table('part', 'record'), [], ['title', 'code'], 'wsh', 5)
    assert found == [(wanted.id, None)]
    assert len([sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]) == 1
