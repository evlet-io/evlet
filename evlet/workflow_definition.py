"""Workflow definitions: the neutral JSON document, its validation and its conditions.

A definition (schema `evlet.workflow/1`, docs/internal/workflow-contract.md section 2) is checked
here against the model's fact catalogue (evlet/workflow.py) before it is saved or published. Every
problem is reported with the id of the part it concerns, so an editor can show it in place.

Conditions are Field, Operator and Value rows grouped by All or Any. Each row compiles to finite-v1
expression nodes (evlet/calc.py), checked with the same node checker as calculation plans and
evaluated by the same exact evaluator, so money and numbers compare exactly. A condition is true,
false or undecided. A row over a fact with no value is undecided (only `isnull` tests for an empty
value), and a group with an undecided item is undecided: finite-v1 groups are strict. Undecided
never counts as false; the engine blocks and names the missing facts.
"""

from datetime import date
import json
import re

from . import calc
from .identity import decode
from .workflow import protect_key

SCHEMA = 'evlet.workflow/1'
ID = re.compile('^(stg|act|rev|cnd|rte)_[a-z0-9]{6,26}$')
WORKFLOW_ID = re.compile('^wfl_[a-z0-9]{6,26}$')
LIMITS = {'bytes': 256 * 1024, 'stages': 40, 'actions': 20, 'reviews': 20, 'depth': 3,
          'condition_depth': 4, 'condition_rows': 50, 'definition_rows': 1000, 'automatic_steps': 20,
          'values': 100, 'name': 120, 'notes': 2000, 'label': 120, 'message': 300, 'prompt': 500,
          'feedback': 4000, 'hours': 8760}
STAGE_KINDS = ('work', 'review', 'operational', 'end')
OUTCOMES = ('approved', 'rejected', 'closed')
ACTION_KINDS = ('submit', 'approve', 'reject', 'request_changes', 'move')
# Which action kinds each stage kind offers.
STAGE_ACTIONS = {'work': ('submit',), 'review': ('approve', 'reject', 'request_changes'), 'operational': ('move',), 'end': ()}
VOTES = ('approve', 'reject', 'request_changes')
FEEDBACK = ('hidden', 'optional', 'required')
FEEDBACK_DEFAULT = {'submit': 'optional', 'approve': 'optional', 'move': 'optional', 'reject': 'required', 'request_changes': 'required'}
EDIT_WHO = ('nobody', 'requester', 'assigned', 'selected', 'existing')
OPERATORS = {
    'money': ('eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'nin', 'isnull'),
    'number': ('eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'nin', 'isnull'),
    'enum': ('eq', 'ne', 'in', 'nin', 'isnull'), 'link': ('eq', 'ne', 'in', 'nin', 'isnull'),
    'user': ('eq', 'ne', 'in', 'nin', 'isnull'), 'text': ('eq', 'ne', 'in', 'nin', 'isnull'),
    'date': ('eq', 'lt', 'gt', 'between', 'isnull'), 'boolean': ('eq',),
}
SETTINGS = {'completion': {'mode': 'enable', 'executor': None}, 'self_approval': 'block', 'withdraw': {'allowed': True},
            'delegation': {'allowed': True}, 'invalidation': {'mode': 'any_change'}, 'deadlines': {'unit': 'elapsed_hours'},
            'notifications': {'on_assign': True, 'on_decision': True, 'reminder_hours': 24, 'escalate_hours': None},
            'paused': False}


class DefinitionError(ValueError):
    """A definition that cannot be saved or published; `problems` lists [{"id", "message"}]."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__('; '.join(f'{item["id"]}: {item["message"]}' for item in problems))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class Known:
    """What a definition may name besides the model's catalogue: roles (role -> may review) and accounts."""

    def __init__(self, roles, users=None):
        self.roles, self.users = dict(roles), users


# ---------- conditions ----------

class Undecided:
    """A condition that cannot be decided; `causes` names the facts it is missing."""
    __slots__ = ('causes',)

    def __init__(self, causes):
        self.causes = tuple(sorted(set(causes)))

    def __repr__(self):
        return f'Undecided({", ".join(self.causes)})'


def is_row(node):
    return isinstance(node, dict) and 'field' in node


def condition_facts(node):
    """The fact names a condition reads."""
    if node is None:
        return set()
    if is_row(node):
        return {node['field']}
    return set().union(*(condition_facts(item) for item in node['items']))


def wire_value(kind, value):
    """A condition value or a fact value as the evaluator compares it."""
    if value is None:
        return None
    if kind in ('money', 'number', 'user'):
        return calc.parse(str(value) if type(value) is int else value)
    return value


def literal(kind, value):
    if value is None:
        return {'$expr': 'literal', 'type': 'null', 'value': None}
    if kind in ('money', 'number', 'user'):
        return {'$expr': 'literal', 'type': 'decimal', 'value': str(value) if type(value) is int else value}
    if kind == 'boolean':
        return {'$expr': 'literal', 'type': 'boolean', 'value': value}
    return {'$expr': 'literal', 'type': 'text', 'value': value}


SLOT = {'$expr': 'ref', 'scope': 'self', 'field': 'fact'}


def compile_row(row, kind):
    """(combine, finite-v1 nodes) for one row: `one` node, or `all`, `any` or `none` of several."""
    op, value = row['op'], row['value']
    compare = lambda name, item: {'$expr': name, 'args': [SLOT, literal(kind, item)]}
    if op == 'isnull':
        return 'one', [compare('eq' if value else 'ne', None)]
    if op == 'between':
        return 'all', [compare('gte', value[0]), compare('lte', value[1])]
    if op in ('in', 'nin'):
        return ('any' if op == 'in' else 'none'), [compare('eq', item) for item in value]
    return 'one', [compare(op, value)]


class _Scope:
    """The one value a compiled row reads."""
    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value

    def read(self, scope, field):
        return calc.ready(self.value)

    def causes(self):
        return ['fact']


def evaluate(node, facts, types):
    """True, False or Undecided for a condition over frozen facts ({name: value} or missing when unknown)."""
    if node is None:
        return True
    if is_row(node):
        name = node['field']
        if name not in facts:
            return Undecided([name])
        value = facts[name]
        if value is None and node['op'] != 'isnull':
            return Undecided([name])
        kind = types[name]
        combine, nodes = compile_row(node, kind)
        scope, results = _Scope(wire_value(kind, value)), []
        try:
            for item in nodes:
                result = calc.evaluate(item, scope, calc.Budget())
                if result.state != 'ready' or result.value is None:
                    return Undecided([name])
                results.append(result.value)
        except calc.CalcError:
            return Undecided([name])
        return {'one': results[0] if results else False, 'all': all(results), 'any': any(results), 'none': not any(results)}[combine]
    results = [evaluate(item, facts, types) for item in node['items']]
    waiting = [result for result in results if isinstance(result, Undecided)]
    if waiting:
        return Undecided([cause for result in waiting for cause in result.causes])
    return all(results) if node['match'] == 'all' else any(results)


# ---------- validation ----------

class Checker:
    def __init__(self, compiled, known, model_name):
        self.compiled, self.known, self.model_name = compiled, known, model_name
        self.problems, self.ids, self.rows = [], set(), 0
        self.types = {name: spec.type for name, spec in compiled.facts.items()}

    def problem(self, where, message):
        self.problems.append({'id': where, 'message': message})

    def node_id(self, node, prefix, where):
        value = node.get('id') if isinstance(node, dict) else None
        if not isinstance(value, str) or not ID.fullmatch(value) or not value.startswith(prefix + '_'):
            self.problem(where, f'Give it an id such as {prefix}_abc123')
            return where
        if value in self.ids:
            self.problem(value, 'This id is used twice')
        self.ids.add(value)
        return value

    def keys(self, node, where, allowed, required=()):
        if not isinstance(node, dict):
            self.problem(where, 'Use an object')
            return False
        unknown = sorted(set(node) - set(allowed))
        if unknown:
            self.problem(where, 'Unknown keys: ' + ', '.join(unknown))
        missing = [key for key in required if key not in node]
        if missing:
            self.problem(where, 'Missing ' + ', '.join(missing))
        return not unknown and not missing

    def text(self, value, where, label, limit, *, required=True):
        if value is None and not required:
            return
        if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit or '\x00' in value:
            self.problem(where, f'{label} is text of at most {limit} characters' + ('' if not required else ', not empty'))

    # Conditions

    def condition(self, node, where, depth=1, counted=None):
        counted = [0] if counted is None else counted
        if node is None:
            return
        if is_row(node):
            self.row(node, where)
            counted[0] += 1
            self.rows += 1
        elif not self.keys(node, where, ('id', 'match', 'items'), ('id', 'match', 'items')):
            return
        else:
            ident = self.node_id(node, 'cnd', where)
            if node['match'] not in ('all', 'any'):
                self.problem(ident, 'Match all or any')
            if depth > LIMITS['condition_depth']:
                self.problem(ident, f'Conditions nest at most {LIMITS["condition_depth"]} groups deep')
                return
            if not isinstance(node['items'], list) or not node['items']:
                self.problem(ident, 'A group needs at least one row')
                return
            for item in node['items']:
                self.condition(item, ident, depth + 1, counted)
        if depth == 1:
            if counted[0] > LIMITS['condition_rows']:
                self.problem(where, f'A condition has at most {LIMITS["condition_rows"]} rows')

    def row(self, node, where):
        if not self.keys(node, where, ('id', 'field', 'op', 'value'), ('id', 'field', 'op', 'value')):
            return
        ident = self.node_id(node, 'cnd', where)
        kind = self.types.get(node['field'])
        if kind is None:
            self.problem(ident, f'{node["field"]!r} is not a fact of {self.model_name}')
            return
        if node['op'] not in OPERATORS[kind]:
            self.problem(ident, f'{node["op"]!r} does not apply to {kind} values; use ' + ', '.join(OPERATORS[kind]))
            return
        op, value = node['op'], node['value']
        if op == 'isnull':
            if type(value) is not bool:
                self.problem(ident, 'Is empty takes true or false')
        elif op == 'between':
            if not isinstance(value, list) or len(value) != 2 or not all(self.value(kind, node['field'], item) for item in value):
                self.problem(ident, f'Between takes two {kind} values')
            elif kind != 'date' and calc.compare(wire_value(kind, value[0]), wire_value(kind, value[1])) > 0 \
                    or kind == 'date' and value[0] > value[1]:
                self.problem(ident, 'Between takes the smaller value first')
        elif op in ('in', 'nin'):
            if not isinstance(value, list) or not 1 <= len(value) <= LIMITS['values'] \
                    or not all(self.value(kind, node['field'], item) for item in value):
                self.problem(ident, f'Use a list of 1 to {LIMITS["values"]} {kind} values')
            elif len({canonical(item) for item in value}) != len(value):
                self.problem(ident, 'A value is listed twice')
        elif not self.value(kind, node['field'], value):
            self.problem(ident, f'Use a {kind} value')

    def value(self, kind, name, value):
        if kind == 'money':
            return isinstance(value, str) and self.decimal(value)
        if kind == 'number':
            return calc.integer(value) is not None and type(value) is int or isinstance(value, str) and self.decimal(value)
        if kind == 'user':
            return type(value) is int and value > 0 and (self.known.users is None or value in self.known.users)
        if kind == 'boolean':
            return type(value) is bool
        if kind == 'date':
            try:
                return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except ValueError:
                return False
        if kind == 'link':
            try:
                decode(value)
                return len(value) == 13 and value == value.lower()
            except ValueError:
                return False
        if not isinstance(value, str) or not value or len(value) > 200 or '\x00' in value:
            return False
        if kind == 'enum':
            if name == 'requester.role':
                return value in self.known.roles
            choices = self.compiled.facts[name].choices
            return choices is None or value in choices
        return True

    @staticmethod
    def decimal(value):
        try:
            calc.parse(value)
            return True
        except calc.CalcError:
            return False

    # Assignees, routes, actions

    def assignee(self, node, where, *, review):
        if not isinstance(node, dict):
            self.problem(where, 'Choose who is assigned')
            return
        kind = node.get('kind')
        shapes = {'requester': (), 'role': ('role',), 'user': ('user',), 'fact': ('fact',), 'field': ('field',)}
        if kind not in shapes or set(node) != {'kind', *shapes[kind]}:
            self.problem(where, 'Assign the requester, a role, a user, a person fact or a field')
            return
        if kind == 'role':
            if node['role'] not in self.known.roles:
                self.problem(where, f'{node["role"]!r} is not a role')
            elif review and not self.known.roles[node['role']]:
                self.problem(where, f'The role {node["role"]} cannot review {self.model_name} records; give its Rule "review"')
        elif kind == 'user':
            if type(node['user']) is not int or node['user'] < 1 or self.known.users is not None and node['user'] not in self.known.users:
                self.problem(where, 'Choose an existing account')
        elif kind == 'fact' and node['fact'] not in self.compiled.person_facts:
            self.problem(where, f'{node["fact"]!r} is not a person fact of {self.model_name}'
                         + ('' if self.compiled.person_facts else '; declare WorkflowOptions(people=...)'))
        elif kind == 'field' and node['field'] not in self.compiled.assignable:
            self.problem(where, f'{node["field"]!r} is not an assignable field of {self.model_name}')

    def route(self, node, where, stages):
        """Destinations of a route, after checking it."""
        if not isinstance(node, dict) or node.get('kind') not in ('stage', 'first_match'):
            self.problem(where, 'Go to a stage, or to the first matching destination')
            return []
        if node['kind'] == 'stage':
            if not self.keys(node, where, ('kind', 'stage'), ('kind', 'stage')):
                return []
            if node['stage'] not in stages:
                self.problem(where, f'{node["stage"]!r} is not a stage')
                return []
            return [node['stage']]
        if not self.keys(node, where, ('kind', 'rows', 'otherwise'), ('kind', 'rows', 'otherwise')):
            return []
        found = []
        if node['otherwise'] not in stages:
            self.problem(where, 'Choose the Otherwise destination')
        else:
            found.append(node['otherwise'])
        if not isinstance(node['rows'], list) or not node['rows']:
            self.problem(where, 'Add a conditional destination, or go straight to a stage')
            return found
        for row in node['rows']:
            if not self.keys(row, where, ('id', 'when', 'stage'), ('id', 'when', 'stage')):
                continue
            ident = self.node_id(row, 'rte', where)
            if row['when'] is None:
                self.problem(ident, 'A conditional destination needs a condition; use Otherwise for the rest')
            self.condition(row['when'], ident)
            if row['stage'] not in stages:
                self.problem(ident, f'{row["stage"]!r} is not a stage')
            else:
                found.append(row['stage'])
        return found

    def feedback(self, node, kind, where):
        default = {'mode': FEEDBACK_DEFAULT[kind], 'label': 'Feedback', 'prompt': '', 'max_length': 2000}
        if node is None:
            return default
        if not self.keys(node, where, ('mode', 'label', 'prompt', 'max_length')):
            return default
        result = {**default, **node}
        if result['mode'] not in FEEDBACK:
            self.problem(where, 'Feedback is hidden, optional or required')
        if kind in ('reject', 'request_changes') and result['mode'] != 'required':
            self.problem(where, 'Reject and Request changes always ask for feedback')
        self.text(result['label'], where, 'The feedback label', LIMITS['label'])
        self.text(result['prompt'], where, 'The prompt', LIMITS['prompt'], required=False)
        if type(result['max_length']) is not int or not 1 <= result['max_length'] <= LIMITS['feedback']:
            self.problem(where, f'Feedback is at most 1 to {LIMITS["feedback"]} characters')
        return result

    def execute(self, node, where, settings):
        if node is None:
            return
        as_ = node.get('as') if isinstance(node, dict) else None
        key = protect_key({key: value for key, value in node.items() if key != 'as'}) if isinstance(node, dict) else None
        if not key or as_ not in ('actor', 'executor'):
            self.problem(where, 'Run a listed operation as the actor or the executor')
        elif key not in self.compiled.executes:
            self.problem(where, f'{key} is not an operation {self.model_name} lets a workflow run')
        elif as_ == 'executor' and settings['completion']['executor'] is None:
            self.problem(where, 'Name the executor in the completion settings')

    def edit(self, node, stage_kind, where):
        default = ({'who': 'requester', 'fields': {'mode': 'all'},
                    'tables': {name: {'edit': True, 'add': True, 'remove': True, 'reorder': True} for name in self.compiled.tables},
                    'attachments': {'add': True, 'remove': True}} if stage_kind == 'work' else {'who': 'nobody'})
        if node is None:
            return default
        if not self.keys(node, where, ('who', 'people', 'fields', 'tables', 'attachments'), ('who',)):
            return node
        if node['who'] not in EDIT_WHO:
            self.problem(where, 'Who can edit is nobody, requester, assigned, selected or existing')
        if node['who'] == 'selected':
            people = node.get('people')
            if not isinstance(people, list) or not people:
                self.problem(where, 'Choose who can edit')
            for item in people or []:
                self.assignee(item, where, review=False)
        elif 'people' in node:
            self.problem(where, 'People are listed only for selected editors')
        fields = node.get('fields')
        if fields is not None:
            if not isinstance(fields, dict) or fields.get('mode') not in ('all', 'only') \
                    or set(fields) - {'mode', 'names'} or (fields['mode'] == 'only') != ('names' in fields):
                self.problem(where, 'Fields are all, or only the listed names')
            elif fields['mode'] == 'only':
                names = fields['names']
                if not isinstance(names, list) or any(name not in self.compiled.fields for name in names) or len(set(map(str, names))) != len(names):
                    self.problem(where, 'List editable fields of the model, each once')
        tables = node.get('tables')
        if tables is not None:
            if not isinstance(tables, dict):
                self.problem(where, 'Tables map table names to row permissions')
            else:
                for name, rights in tables.items():
                    if name not in self.compiled.tables:
                        self.problem(where, f'{name!r} is not a table of the model')
                    elif not isinstance(rights, dict) or set(rights) != {'edit', 'add', 'remove', 'reorder'} \
                            or any(type(value) is not bool for value in rights.values()):
                        self.problem(where, f'{name}: give edit, add, remove and reorder as true or false')
        attachments = node.get('attachments')
        if attachments is not None and (not isinstance(attachments, dict) or set(attachments) != {'add', 'remove'}
                                        or any(type(value) is not bool for value in attachments.values())):
            self.problem(where, 'Attachments take add and remove as true or false')
        return node

    def settings(self, node):
        if node is None:
            node = {}
        if not self.keys(node, 'settings', tuple(SETTINGS)):
            return json.loads(canonical(SETTINGS))
        result = json.loads(canonical(SETTINGS))
        for key, value in node.items():
            default = SETTINGS[key]
            if isinstance(default, dict):
                if not isinstance(value, dict) or set(value) - set(default):
                    self.problem('settings', f'{key} takes ' + ', '.join(default))
                    continue
                result[key] = {**default, **value}
            else:
                result[key] = value
        completion = result['completion']
        if completion['mode'] not in ('enable', 'execute'):
            self.problem('settings', 'Completion is enable or execute')
        executor = completion['executor']
        if executor is not None and (type(executor) is not int or executor < 1 or self.known.users is not None and executor not in self.known.users):
            self.problem('settings', 'The executor is an existing account')
        if completion['mode'] == 'execute' and executor is None:
            self.problem('settings', 'Executing on completion needs an executor')
        if result['self_approval'] not in ('block', 'allow'):
            self.problem('settings', 'Self approval is block or allow')
        for key in ('withdraw', 'delegation'):
            if type(result[key]['allowed']) is not bool:
                self.problem('settings', f'{key} allowed is true or false')
        if result['invalidation'] != {'mode': 'any_change'}:
            self.problem('settings', 'Invalidation is any_change')
        if result['deadlines'] != {'unit': 'elapsed_hours'}:
            self.problem('settings', 'Deadlines are in elapsed hours until working calendars are available')
        notifications = result['notifications']
        if any(type(notifications[key]) is not bool for key in ('on_assign', 'on_decision')) or any(
                notifications[key] is not None and (type(notifications[key]) is not int or not 1 <= notifications[key] <= LIMITS['hours'])
                for key in ('reminder_hours', 'escalate_hours')):
            self.problem('settings', 'Notifications take true or false, and hours from 1 to 8760 or null')
        if type(result['paused']) is not bool:
            self.problem('settings', 'Paused is true or false')
        return result


def check(compiled, known, model_name, definition):
    """The normalized definition, or DefinitionError listing every problem."""
    if not isinstance(definition, dict):
        raise DefinitionError([{'id': 'definition', 'message': 'A definition is a JSON object'}])
    try:
        size = len(canonical(definition).encode())
    except (TypeError, ValueError):
        raise DefinitionError([{'id': 'definition', 'message': 'A definition holds only JSON values'}]) from None
    if size > LIMITS['bytes']:
        raise DefinitionError([{'id': 'definition', 'message': f'A definition is at most {LIMITS["bytes"] // 1024} KB'}])
    definition = json.loads(canonical(definition))
    checker = Checker(compiled, known, model_name)
    envelope = ('schema', 'id', 'version', 'name', 'model', 'protects', 'applies_when', 'start', 'settings', 'notes', 'stages')
    if not checker.keys(definition, 'definition', envelope, ('schema', 'name', 'model', 'protects', 'start', 'stages')):
        raise DefinitionError(checker.problems)
    result = {key: definition.get(key) for key in envelope}
    if result['schema'] != SCHEMA:
        checker.problem('definition', f'The schema is {SCHEMA}')
    if result['id'] is not None and (not isinstance(result['id'], str) or not WORKFLOW_ID.fullmatch(result['id'])):
        checker.problem('definition', 'The workflow id looks like wfl_abc123')
    if result['version'] is not None and (type(result['version']) is not int or result['version'] < 1):
        checker.problem('definition', 'The version is a whole number from 1')
    checker.text(result['name'], 'definition', 'The name', LIMITS['name'])
    if result['model'] != model_name:
        checker.problem('definition', f'This definition is for {model_name}')
    key = protect_key(result['protects'])
    if key is None or key and key not in compiled.protects:
        checker.problem('definition', f'{model_name} lets a workflow protect ' + (', '.join(compiled.protects) or 'nothing')
                        + '; use null for an operational workflow')
    checker.condition(result['applies_when'], 'definition')
    result['notes'] = '' if result['notes'] is None else result['notes']
    checker.text(result['notes'], 'definition', 'The notes', LIMITS['notes'], required=False)
    settings = result['settings'] = checker.settings(result['settings'])
    stages = result['stages']
    if not isinstance(stages, list) or not 1 <= len(stages) <= LIMITS['stages']:
        checker.problem('definition', f'A workflow has 1 to {LIMITS["stages"]} stages')
        raise DefinitionError(checker.problems)
    by_id = {stage['id']: stage for stage in stages if isinstance(stage, dict) and isinstance(stage.get('id'), str)}
    edges, automatic, normalized = {}, {}, []
    for stage in stages:
        ident = checker.node_id(stage, 'stg', 'stages')
        if not checker.keys(stage, ident, ('id', 'label', 'kind', 'outcome', 'assign', 'require', 'deadline', 'edit', 'reviews', 'actions'),
                            ('id', 'label', 'kind')):
            continue
        stage = {'outcome': None, 'assign': None, 'require': None, 'deadline': None, 'edit': None, 'reviews': None, 'actions': [], **stage}
        checker.text(stage['label'], ident, 'The stage name', LIMITS['label'])
        kind = stage['kind']
        edges[ident], automatic[ident] = [], []
        if kind not in STAGE_KINDS:
            checker.problem(ident, 'A stage is work, review, operational or end')
            continue
        if kind == 'end':
            if stage['outcome'] not in OUTCOMES:
                checker.problem(ident, 'An end stage has the outcome approved, rejected or closed')
            if stage['outcome'] == 'approved' and key == '' or stage['outcome'] == 'closed' and key:
                checker.problem(ident, 'Approval workflows end approved or rejected; operational workflows end closed')
            if stage['assign'] is not None or stage['reviews'] is not None or stage['actions'] or stage['require'] is not None:
                checker.problem(ident, 'An end stage has no assignee, reviews, actions or requirement')
            stage['edit'] = checker.edit(stage['edit'], kind, ident)
            normalized.append(stage)
            continue
        if stage['outcome'] is not None:
            checker.problem(ident, 'Only end stages have an outcome')
        if stage['require'] is not None:
            if checker.keys(stage['require'], ident, ('when', 'message'), ('when', 'message')):
                checker.condition(stage['require']['when'], ident)
                checker.text(stage['require']['message'], ident, 'The message', LIMITS['message'])
        if stage['deadline'] is not None and (not isinstance(stage['deadline'], dict) or set(stage['deadline']) != {'hours'}
                                              or type(stage['deadline']['hours']) is not int or not 1 <= stage['deadline']['hours'] <= LIMITS['hours']):
            checker.problem(ident, f'A deadline is 1 to {LIMITS["hours"]} elapsed hours')
        stage['edit'] = checker.edit(stage['edit'], kind, ident)
        group = stage['reviews']
        if group is not None and kind != 'review':
            checker.problem(ident, 'Only review stages hold reviews')
            group = None
        if group is None:
            checker.assignee(stage['assign'], ident, review=kind == 'review')
        elif stage['assign'] is not None:
            checker.problem(ident, 'A stage with reviews assigns each review instead')
        if group is not None:
            group = stage['reviews'] = check_group(checker, group, ident, by_id, edges[ident], automatic[ident])
        actions = stage['actions']
        if not isinstance(actions, list) or not 1 <= len(actions) <= LIMITS['actions']:
            checker.problem(ident, f'A stage has 1 to {LIMITS["actions"]} actions')
            actions = []
        kinds, seen = [], []
        for action in actions:
            where = checker.node_id(action, 'act', ident)
            if not checker.keys(action, where, ('id', 'label', 'kind', 'who', 'when', 'go_to', 'feedback', 'execute'), ('id', 'label', 'kind')):
                continue
            action = {'who': {'kind': 'assigned'}, 'when': None, 'go_to': None, 'feedback': None, 'execute': None, **action}
            checker.text(action['label'], where, 'The button label', LIMITS['label'])
            if action['kind'] not in STAGE_ACTIONS[kind]:
                checker.problem(where, f'A {kind} stage offers ' + ', '.join(STAGE_ACTIONS[kind]))
                continue
            kinds.append(action['kind'])
            who = action['who']
            if who not in ({'kind': 'assigned'}, {'kind': 'requester'}) and not (
                    isinstance(who, dict) and set(who) == {'kind', 'people'} and who['kind'] == 'people' and kind == 'operational'
                    and isinstance(who['people'], list) and who['people']):
                checker.problem(where, 'Who can use it is the assigned people, the requester, or (on operational stages) listed people')
            elif who.get('kind') == 'people':
                for item in who['people']:
                    checker.assignee(item, where, review=False)
            if who == {'kind': 'requester'} and action['kind'] in VOTES:
                checker.problem(where, 'Reviews are decided by their assigned people')
            checker.condition(action['when'], where)
            if group is not None:
                if action['go_to'] is not None:
                    checker.problem(where, 'On a stage with reviews the review group decides where the stage goes')
            elif action['go_to'] is None:
                checker.problem(where, 'Choose where the action goes')
            else:
                found = checker.route(action['go_to'], where, by_id)
                edges[ident] += found
                if action['kind'] == 'request_changes':
                    for target in found:
                        if by_id.get(target, {}).get('kind') != 'work':
                            checker.problem(where, 'Request changes returns to a work stage')
            action['feedback'] = checker.feedback(action['feedback'], action['kind'], where)
            checker.execute(action['execute'], where, settings)
            seen.append(action)
        stage['actions'] = seen
        if kind == 'review' and 'approve' not in kinds:
            checker.problem(ident, 'A review stage needs an Approve action')
        if kind == 'review' and len(kinds) != len(set(kinds)) and group is None:
            checker.problem(ident, 'Offer each decision once on a review stage')
        if group is not None:
            for action_kind, route in (('reject', 'on_reject'), ('request_changes', 'on_changes')):
                if action_kind in kinds and group.get(route) is None:
                    checker.problem(ident, f'Choose where the stage goes {"when a review rejects" if route == "on_reject" else "when changes are requested"}')
        if kind == 'work' and 'submit' not in kinds:
            checker.problem(ident, 'A work stage needs a Send action')
        normalized.append(stage)
    if checker.rows > LIMITS['definition_rows']:
        checker.problem('definition', f'A definition has at most {LIMITS["definition_rows"]} condition rows')
    start = by_id.get(result['start'])
    if start is None:
        checker.problem('definition', 'Choose the starting stage')
    elif start.get('kind') != 'work':
        checker.problem(result['start'], 'The starting stage is a work stage')
    else:
        reached, queue = {result['start']}, [result['start']]
        while queue:
            for target in edges.get(queue.pop(), ()):
                if target not in reached:
                    reached.add(target)
                    queue.append(target)
        for ident, stage in by_id.items():
            if ident not in reached:
                checker.problem(ident, 'No action leads to this stage')
            elif stage.get('kind') != 'end' and not edges.get(ident):
                checker.problem(ident, 'This stage has no way out')
        cycle = automatic_cycle(automatic)
        if cycle:
            checker.problem(cycle[0], 'These stages pass to each other automatically forever: ' + ' → '.join(cycle))
    if checker.problems:
        raise DefinitionError(checker.problems)
    result['stages'] = normalized
    return result


def check_group(checker, group, ident, stages, edges, automatic):
    allowed = ('run', 'complete', 'rejection', 'when_none', 'on_complete', 'on_reject', 'on_changes', 'items')
    if not checker.keys(group, ident, allowed, ('items', 'on_complete')):
        return group
    group = {'run': 'together', 'complete': {'kind': 'all'}, 'rejection': 'veto', 'when_none': 'block',
             'on_reject': None, 'on_changes': None, **group}
    if group['run'] not in ('together', 'sequence'):
        checker.problem(ident, 'Reviews run together or in sequence')
    if group['when_none'] not in ('block', 'continue'):
        checker.problem(ident, 'When no review applies, block or continue')
    if group['rejection'] not in ('veto', 'branch'):
        checker.problem(ident, 'A rejection is a veto or fails its own review')
    items = group['items']
    if not isinstance(items, list) or not 1 <= len(items) <= LIMITS['reviews']:
        checker.problem(ident, f'A stage has 1 to {LIMITS["reviews"]} reviews')
        items = []
    complete = group['complete']
    if complete not in ({'kind': 'all'}, {'kind': 'any'}) and not (
            isinstance(complete, dict) and set(complete) == {'kind', 'count'} and complete['kind'] == 'count'
            and type(complete['count']) is int and 1 <= complete['count'] <= max(len(items), 1)):
        checker.problem(ident, 'Complete when all, any or a number of reviews (at most the number of reviews) approve')
    if group['rejection'] == 'branch' and complete == {'kind': 'all'}:
        checker.problem(ident, 'When all reviews must approve, a rejection is a veto')
    for item in items:
        where = checker.node_id(item, 'rev', ident)
        if not checker.keys(item, where, ('id', 'label', 'assign', 'when', 'required', 'stages'), ('id', 'label', 'assign')):
            continue
        item.setdefault('when', None)
        item.setdefault('required', {'kind': 'any'})
        item.setdefault('stages', None)
        checker.text(item['label'], where, 'The review name', LIMITS['label'])
        checker.assignee(item['assign'], where, review=True)
        checker.condition(item['when'], where)
        required = item['required']
        if required not in ({'kind': 'any'}, {'kind': 'all'}) and not (
                isinstance(required, dict) and set(required) == {'kind', 'count'} and required['kind'] == 'count'
                and type(required['count']) is int and required['count'] >= 1):
            checker.problem(where, 'Required decisions are any, all or a number of people')
        if item['stages'] is not None:
            # Reserved for nested reviews (a review that runs its own stages), built in a later phase.
            checker.problem(where, 'Nested reviews are not available yet; leave stages empty')
    for route in ('on_complete', 'on_reject', 'on_changes'):
        if group[route] is None:
            continue
        found = checker.route(group[route], ident, stages)
        edges += found
        if route == 'on_complete' and group['when_none'] == 'continue':
            automatic += found
        if route == 'on_changes':
            for target in found:
                if stages.get(target, {}).get('kind') != 'work':
                    checker.problem(ident, 'Request changes returns to a work stage')
    return group


def automatic_cycle(automatic):
    """A list of stage ids that pass to each other only automatically, or None."""
    state, path = {}, []

    def visit(node):
        state[node] = 'open'
        path.append(node)
        for target in automatic.get(node, ()):
            if state.get(target) == 'open':
                return path[path.index(target):] + [target]
            if target not in state:
                found = visit(target)
                if found:
                    return found
        state[node] = 'done'
        path.pop()
        return None

    for node in automatic:
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


def facts_read(definition, stage):
    """Every fact a stage's conditions read: its requirement, review conditions, action conditions and routes."""
    names = set()
    if stage.get('require'):
        names |= condition_facts(stage['require']['when'])
    group = stage.get('reviews')
    routes = [action['go_to'] for action in stage.get('actions', ())] + ([group[key] for key in ('on_complete', 'on_reject', 'on_changes')] if group else [])
    for item in (group or {}).get('items', ()):
        names |= condition_facts(item['when'])
    for action in stage.get('actions', ()):
        names |= condition_facts(action['when'])
    for route in routes:
        if route and route['kind'] == 'first_match':
            for row in route['rows']:
                names |= condition_facts(row['when'])
    return names


def resolve_route(route, facts, types):
    """(stage id, None) for a route over frozen facts, or (None, Undecided) when a row cannot be decided."""
    if route['kind'] == 'stage':
        return route['stage'], None
    for row in route['rows']:
        found = evaluate(row['when'], facts, types)
        if isinstance(found, Undecided):
            return None, found
        if found:
            return row['stage'], None
    return route['otherwise'], None


def split(definition):
    """(fact, values) when applies_when selects records by one choice fact with eq or in, else None."""
    node = definition.get('applies_when')
    while isinstance(node, dict) and not is_row(node) and len(node.get('items', ())) == 1:
        node = node['items'][0]
    if not is_row(node) or node['op'] not in ('eq', 'in'):
        return None
    return node['field'], frozenset(node['value'] if node['op'] == 'in' else [node['value']])
