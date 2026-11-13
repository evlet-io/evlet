"""The query builder: database-neutral statements that a dialect compiles to SQL (docs/database-adapters.md).

Framework code describes what it reads and writes with these plain objects and runs them with
connection.run(statement). It never writes SQL text. The SQLite dialect (evlet/sqlite.py) is the one
compiler shipped; another database adds its own compiler and passes tests/test_database_adapter.py.

Use the module by name, so a call reads as what it is:

    from . import qb

    row = connection.run(qb.select('evlet_users', 'id', 'role').where(username=name)).one()
    connection.run(qb.update('evlet_config', version=qb.col('version') + 1).where(id=1))
    new_id = connection.run(qb.insert('evlet_inbox', user_id=user_id, text=text).returning_id()).id

Rules that keep calls readable:

* The first argument of a condition names a column; the other arguments are values, bound as parameters,
  unless they are wrapped in qb.col() or are another expression. `.where(name=value)` means equality, and
  `name=None` means IS NULL.
* Several conditions in one `.where(...)`, and several `.where(...)` calls, all have to hold (AND).
* Column names may name a table alias: "record.id". Every name is checked as an identifier.
* Statements are immutable: each method returns a new statement.
"""

from dataclasses import dataclass, replace



def identifier(name):
    """A checked SQL identifier (evlet.models.identifier). Imported when called: qb is a leaf module that any
    framework module may import."""
    from .models import identifier as checked
    return checked(name)


# ---------- expressions ----------

class Expression:
    """Anything that has a value in SQL: a column, a bound value, a function, arithmetic, a subquery."""

    def __add__(self, other):
        return Arithmetic(self, '+', expression(other))

    def __sub__(self, other):
        return Arithmetic(self, '-', expression(other))

    def __mul__(self, other):
        return Arithmetic(self, '*', expression(other))

    def __truediv__(self, other):
        """Division; two integers divide to an integer, rounding toward zero."""
        return Arithmetic(self, '/', expression(other))

    def __neg__(self):
        return Arithmetic(Value(0), '-', self)

    def __mod__(self, other):
        """The remainder of integer division; it keeps the sign of the left side."""
        return Arithmetic(self, '%', expression(other))


@dataclass(frozen=True, eq=False)
class Column(Expression):
    name: str
    table: str = None


@dataclass(frozen=True, eq=False)
class AllColumns(Expression):
    """Every column of a table or alias, beside other output: qb.all_columns('record')."""
    table: str


@dataclass(frozen=True, eq=False)
class Value(Expression):
    value: object


@dataclass(frozen=True, eq=False)
class Arithmetic(Expression):
    left: Expression
    operator: str
    right: Expression


# The functions a statement may call. A dialect writes each in its own SQL.
FUNCTIONS = {'count', 'count_distinct', 'sum', 'min', 'max', 'coalesce', 'nullif', 'lower', 'length', 'substr', 'row_number'}


@dataclass(frozen=True, eq=False)
class Function(Expression):
    name: str
    arguments: tuple = ()


@dataclass(frozen=True, eq=False)
class Window(Expression):
    """A function over a window of rows: row_number() or count() per group, in an order."""
    function: Function
    partition_by: tuple = ()
    order_by: tuple = ()


@dataclass(frozen=True, eq=False)
class Case(Expression):
    """The value matching what an expression holds: CASE expression WHEN key THEN value ... END."""
    expression: Expression
    pairs: tuple


@dataclass(frozen=True, eq=False)
class When(Expression):
    """The value of the first condition that holds: CASE WHEN condition THEN value ... ELSE otherwise END."""
    pairs: tuple
    otherwise: Expression


@dataclass(frozen=True, eq=False)
class Named:
    """An expression with a name in the result: qb.named(qb.count(), 'total')."""
    expression: Expression
    name: str


@dataclass(frozen=True)
class Order:
    expression: Expression
    descending: bool = False
    # 'first' or 'last': where empty values go. None keeps the database's own order.
    nulls: str = None


# ---------- conditions ----------

class Condition(Expression):
    """A condition that is true or false for a row."""


@dataclass(frozen=True, eq=False)
class Compare(Condition):
    left: Expression
    operator: str
    right: Expression


@dataclass(frozen=True, eq=False)
class IsNull(Condition):
    expression: Expression
    negated: bool = False


@dataclass(frozen=True, eq=False)
class In(Condition):
    """In a list of values (an empty list matches nothing) or in the rows of a subquery."""
    expression: Expression
    values: object
    negated: bool = False


@dataclass(frozen=True, eq=False)
class Between(Condition):
    expression: Expression
    low: Expression
    high: Expression


@dataclass(frozen=True, eq=False)
class Text(Condition):
    """Text matching: 'contains', 'contains_ci' (ignoring case), 'excludes_ci', 'starts_with' and 'ends_with'."""
    kind: str
    expression: Expression
    text: Expression


@dataclass(frozen=True, eq=False)
class All(Condition):
    items: tuple


@dataclass(frozen=True, eq=False)
class AnyOf(Condition):
    items: tuple


@dataclass(frozen=True, eq=False)
class Not(Condition):
    item: Condition


@dataclass(frozen=True, eq=False)
class Exists(Condition):
    query: object
    negated: bool = False


@dataclass(frozen=True, eq=False)
class InTree(Condition):
    """The column holds the id of a row of `table` at or below the roots, following `parent` links down a
    hierarchy (a team under its managers). A cycle in the links stops the walk instead of looping."""
    expression: Expression
    table: str
    parent: str
    roots: tuple
    include_roots: bool


TRUE = All(())
FALSE = AnyOf(())


# ---------- statements ----------

@dataclass(frozen=True)
class Table:
    name: str
    alias: str = None


@dataclass(frozen=True, eq=False)
class Derived:
    """A query used as a table, under an alias: qb.from_query(qb.select(...), 'ranked')."""
    query: object
    alias: str


@dataclass(frozen=True)
class Join:
    table: Table
    on: Condition
    left: bool = False


@dataclass(frozen=True, eq=False)
class Select(Expression):
    """A query. Used inside another statement it is a subquery: qb.in_('id', qb.select(...))."""
    table: Table
    columns: tuple = ()
    joins: tuple = ()
    conditions: tuple = ()
    groups: tuple = ()
    orders: tuple = ()
    count: int = None
    skip: int = None
    unique: bool = False
    having_conditions: tuple = ()

    def where(self, /, *conditions, **equals):
        return replace(self, conditions=self.conditions + _conditions(conditions, equals))

    def distinct(self):
        """Each distinct row once: SELECT DISTINCT."""
        return replace(self, unique=True)

    def having(self, /, *conditions):
        """Conditions on the groups of .group_by(), such as qb.gt(qb.count(), 5)."""
        return replace(self, having_conditions=self.having_conditions + _conditions(conditions, {}))

    def join(self, table, on, *, alias=None, left=False):
        return replace(self, joins=self.joins + (Join(_table(table, alias), on, left),))

    def group_by(self, *columns):
        return replace(self, groups=self.groups + tuple(expression(item, column=True) for item in columns))

    def order_by(self, *orders):
        """Each item is a column name ('-name' for descending), an expression or a qb.asc/qb.desc."""
        return replace(self, orders=self.orders + tuple(_order(item) for item in orders))

    def limit(self, count):
        return replace(self, count=count)

    def offset(self, skip):
        return replace(self, skip=skip)


@dataclass(frozen=True)
class Conflict:
    """ON CONFLICT (columns): do nothing (updates None) or set the given columns."""
    columns: tuple
    updates: tuple = None


@dataclass(frozen=True)
class Insert:
    table: str
    values: tuple = ()
    source: Select = None
    columns: tuple = ()
    conflict: Conflict = None
    # A column whose value the insert returns (Result.id reads it): "id", or another column of an upsert.
    returns: str = None

    def on_conflict(self, *columns):
        return _ConflictChoice(self, tuple(_name(name) for name in columns))

    def returning_id(self):
        """Return the new row's id (Result.id). Only for tables with an integer "id"."""
        return replace(self, returns='id')

    def returning(self, column):
        """Return one column of the written row, as Result.id: an upsert's counter, say."""
        return replace(self, returns=_name(column))


@dataclass(frozen=True)
class _ConflictChoice:
    insert: Insert
    columns: tuple

    def do_nothing(self):
        return replace(self.insert, conflict=Conflict(self.columns))

    def do_update(self, /, **values):
        return replace(self.insert, conflict=Conflict(self.columns, _assignments(values)))


@dataclass(frozen=True)
class Update:
    table: str
    assignments: tuple
    conditions: tuple = ()

    def where(self, /, *conditions, **equals):
        return replace(self, conditions=self.conditions + _conditions(conditions, equals))


@dataclass(frozen=True)
class Delete:
    table: str
    conditions: tuple = ()

    def where(self, /, *conditions, **equals):
        return replace(self, conditions=self.conditions + _conditions(conditions, equals))


# ---------- building ----------

def _name(name):
    """A column or table name, checked."""
    identifier(name)
    return name


def col(name):
    """A column: "name" or "alias.name"."""
    table, _, column = name.rpartition('.')
    identifier(column)
    if table:
        identifier(table)
    return Column(column, table or None)


def value(item):
    """A bound value, for the rare place where a value stands where a column name would."""
    return Value(item)


def expression(item, *, column=False):
    """item as an expression: expressions as they are; a string is a column when column=True, else a value."""
    if isinstance(item, Expression):
        return item
    if column and isinstance(item, str):
        return col(item)
    return Value(item)


def table(name, alias=None):
    """A table, with an alias for joins and correlated subqueries: qb.table('invoice', 'record')."""
    identifier(name)
    if alias is not None:
        identifier(alias)
    return Table(name, alias)


def from_query(query, alias):
    identifier(alias)
    return Derived(query, alias)


def all_columns(name):
    identifier(name)
    return AllColumns(name)


def _table(item, alias=None):
    if isinstance(item, (Table, Derived)):
        return item
    return table(item, alias)


def select(source, *columns):
    """Read rows of a table (a name or qb.table). No columns means every column of the table."""
    return Select(_table(source), tuple(_output(item) for item in columns))


def _output(item):
    if isinstance(item, Named):
        identifier(item.name)
        return item
    return expression(item, column=True)


def named(item, name):
    return Named(expression(item, column=True), name)


def insert(name, /, **values):
    """Insert one row. Use .returning_id() to get its id and .on_conflict(...) for upserts."""
    identifier(name)
    return Insert(name, _assignments(values))


def insert_rows_from(name, columns, query):
    """Insert the rows a query returns into the named columns."""
    identifier(name)
    return Insert(name, source=query, columns=tuple(_name(item) for item in columns))


def update(name, /, **values):
    identifier(name)
    if not values:
        raise ValueError('An update sets at least one column')
    return Update(name, _assignments(values))


def delete(name):
    identifier(name)
    return Delete(name)


def _assignments(values):
    return tuple((_name(name), expression(item)) for name, item in values.items())


def _conditions(conditions, equals):
    for item in conditions:
        if not isinstance(item, Condition):
            raise TypeError('Pass conditions built with qb (qb.eq, qb.gt, ...), or name=value for equality')
    return tuple(conditions) + tuple(eq(name, item) for name, item in equals.items())


def _order(item):
    if isinstance(item, Order):
        return item
    if isinstance(item, str) and item.startswith('-'):
        return Order(col(item[1:]), descending=True)
    return Order(expression(item, column=True))


def asc(item, nulls=None):
    return Order(expression(item, column=True), False, nulls)


def desc(item, nulls=None):
    return Order(expression(item, column=True), True, nulls)


# ---------- conditions ----------

def _left(item):
    return expression(item, column=True)


def eq(column, item):
    """Equal; None means IS NULL."""
    if item is None:
        return IsNull(_left(column))
    return Compare(_left(column), '=', expression(item))


def ne(column, item):
    """Not equal; None means IS NOT NULL. A row whose column is empty is not "not equal" to a value."""
    if item is None:
        return IsNull(_left(column), negated=True)
    return Compare(_left(column), '!=', expression(item))


def lt(column, item):
    return Compare(_left(column), '<', expression(item))


def le(column, item):
    return Compare(_left(column), '<=', expression(item))


def gt(column, item):
    return Compare(_left(column), '>', expression(item))


def ge(column, item):
    return Compare(_left(column), '>=', expression(item))


def is_null(column):
    return IsNull(_left(column))


def not_null(column):
    return IsNull(_left(column), negated=True)


def in_(column, items):
    """In a list of values or a subquery; an empty list matches no row."""
    return In(_left(column), items if isinstance(items, Select) else tuple(expression(item) for item in items))


def not_in(column, items):
    return In(_left(column), items if isinstance(items, Select) else tuple(expression(item) for item in items), negated=True)


def between(column, low, high):
    return Between(_left(column), expression(low), expression(high))


def contains(column, text):
    """The text appears in the column, matching case."""
    return Text('contains', _left(column), expression(text))


def contains_ci(column, text):
    """The text appears in the column, ignoring case."""
    return Text('contains_ci', _left(column), expression(text))


def excludes_ci(column, text):
    """The column is empty or does not contain the text, ignoring case."""
    return Text('excludes_ci', _left(column), expression(text))


def starts_with(column, text):
    return Text('starts_with', _left(column), expression(text))


def ends_with(column, text):
    """The column's text ends with text, matching case."""
    return Text('ends_with', _left(column), expression(text))


def in_tree(column, table, parent, roots, *, include_roots):
    """The column names a row of table under the roots (ids), following the parent column; with include_roots
    the roots themselves too. An empty list of roots matches nothing."""
    identifier(table)
    identifier(parent)
    return InTree(_left(column), table, parent, tuple(roots), include_roots)


def always(condition):
    """Whether a condition holds for every row without checking anything (qb.TRUE, or and_() of nothing)."""
    return isinstance(condition, All) and all(always(item) for item in condition.items)


def and_(*items):
    return All(tuple(items))


def or_(*items):
    return AnyOf(tuple(items))


def not_(item):
    return Not(item)


def exists(query):
    return Exists(query)


def not_exists(query):
    return Exists(query, negated=True)


# ---------- functions ----------

def _function(name, *arguments):
    return Function(name, tuple(expression(item, column=True) for item in arguments))


def count(column=None):
    """count(*) without a column, else the rows where the column is not empty."""
    return _function('count') if column is None else _function('count', column)


def sum(column):  # noqa: A001 - read as qb.sum
    return _function('sum', column)


def min(column):  # noqa: A001 - read as qb.min
    return _function('min', column)


def max(column):  # noqa: A001 - read as qb.max
    return _function('max', column)


def coalesce(column, *fallbacks):
    return Function('coalesce', (expression(column, column=True), *(expression(item) for item in fallbacks)))


def nullif(column, item):
    """The column's value, or NULL when it equals item: qb.nullif('short_name', '')."""
    return Function('nullif', (expression(column, column=True), expression(item)))


def count_distinct(column):
    """The number of distinct non-empty values in the column."""
    return _function('count_distinct', column)


def lower(column):
    return _function('lower', column)


def substr(column, start, size):
    """size characters of the text from position start (1 is the first): qb.substr('token', 1, 16)."""
    return Function('substr', (expression(column, column=True), expression(start), expression(size)))


def length(column):
    return _function('length', column)


def case(column, choices):
    """The value choices maps the column's value to: qb.case('id', {12: 1, 15: 2}). Other rows get NULL."""
    return Case(_left(column), tuple((expression(key), expression(item)) for key, item in choices.items()))


def when(*pairs, otherwise=None):
    """The value of the first (condition, value) pair whose condition holds, else otherwise:
    qb.sum(qb.when((qb.eq('outcome', 'denied'), 1), otherwise=0))."""
    if not pairs:
        raise ValueError('qb.when needs at least one (condition, value) pair')
    for condition, _ in pairs:
        if not isinstance(condition, Condition):
            raise TypeError('qb.when takes (condition, value) pairs built with qb')
    return When(tuple((condition, expression(item)) for condition, item in pairs), expression(otherwise))


def row_number():
    return Function('row_number')


def over(function, *, partition_by=(), order_by=()):
    """function computed per group of rows (partition_by), in an order: qb.over(qb.row_number(), ...)."""
    return Window(function, tuple(expression(item, column=True) for item in partition_by), tuple(_order(item) for item in order_by))
