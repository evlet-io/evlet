"""Meta.board: a list offers a board only when the model names the State field its columns follow."""

import pytest

from evlet import Model, Registry, Select, State, Text
from evlet.ui.metadata import ui_schema, view_for


def task_model(name='Task', **meta):
    fields = {'title': Text(required=True), 'kind': Select(('bug', 'chore'), default='bug'),
              'stage': State(State.Step('open'), State.Step('done'), rules=(State.Rule('open', 'done', 'Finish'),))}
    if meta:
        fields['Meta'] = type('Meta', (), meta)
    return type(Model)(name, (Model,), {'__module__': __name__, **fields})


def test_a_model_with_a_state_has_no_board_unless_meta_board_names_it():
    assert 'board' not in view_for(task_model())
    assert view_for(task_model(board='stage'))['board'] == 'stage'


def test_the_schema_carries_board_only_for_models_that_set_it():
    registry = Registry(task_model('PlainTask'), task_model('BoardTask', board='stage'))
    models = {entry['label']: entry for entry in ui_schema(registry)['models']}
    assert 'board' not in models['Plain task']
    assert models['Board task']['board'] == 'stage'


def test_registration_refuses_a_board_on_another_field():
    with pytest.raises(ValueError, match='Meta.board'):
        Registry(task_model(board='kind'))


@pytest.mark.parametrize('value', ['kind', 'title', 'missing', '', ('stage',), True])
def test_meta_board_must_name_the_state_field(value):
    with pytest.raises(ValueError, match=r"Task.Meta.board: name the model's State field, 'stage'"):
        view_for(task_model(board=value))


def test_meta_board_on_a_model_without_a_state_says_so():
    class Note(Model):
        title = Text(required=True)
        kind = Select(('a', 'b'), default='a')

        class Meta:
            board = 'kind'

    with pytest.raises(ValueError, match='this model has no State'):
        view_for(Note)
