"""Collapsible form sections: evlet.Section in Model.Meta.layout, its checks and its schema output."""

import pytest

from evlet import CreateModel, LongText, Migration, Model, Registry, Section, Text
from evlet.ui.metadata import view_for


def visit(*layout, name='Visit'):
    """A fresh model with this layout (classes register their fields on creation)."""
    return type(name, (Model,), {'title': Text(), 'room': Text(), 'notes': LongText(), 'code': Text(),
                                 'Meta': type('Meta', (), {'layout': layout})})


def test_sections_and_plain_groups_give_collapsible_and_collapsed_in_the_schema():
    model = visit(('Visit', ('title',)), Section('Where', ('room',)), Section('More', ('notes',), collapsed=True),
                  Section('Codes', ('code',), collapsed='empty'))
    assert [(group['label'], group['fields'], group['collapsible'], group['collapsed']) for group in view_for(model)['layout']] == [
        ('Visit', ['title'], False, False), ('Where', ['room'], True, True), ('More', ['notes'], True, True), ('Codes', ['code'], True, 'empty')]
    # Sections start closed unless they say otherwise; collapsed=False starts open.
    assert view_for(visit(('Visit', ('title',)), Section('Where', ('room',), collapsed=False)))['layout'][1]['collapsed'] is False
    # A section that can't collapse is a plain group; without Main the first group of fields is the main zone.
    assert view_for(visit(Section('Visit', ('title',), collapsible=False)))['layout'][0] == {
        'label': 'Visit', 'fields': ['title'], 'collapsible': False, 'collapsed': False, 'flow': 'rows', 'main': True}
    # Fields a layout leaves out still go to Other, a last section that starts closed.
    assert view_for(visit(Section('Visit', ('title',), collapsed=True)))['layout'][-1] == {
        'label': 'Other', 'fields': ['room', 'notes', 'code'], 'collapsible': True, 'collapsed': True, 'flow': 'rows'}
    assert repr(Section('More', ('notes',), collapsed='empty')) == "Section('More', ('notes',), collapsible=True, collapsed='empty')"


@pytest.mark.parametrize('section, message', [
    (Section('More', ('notes',), collapsed='yes'), r"collapsed must be False, True or 'empty', not 'yes'"),
    (Section('More', ('notes',), collapsed=1), r"collapsed must be False, True or 'empty', not 1"),
    (Section('More', ('notes',), collapsed=None), r"collapsed must be False, True or 'empty', not None"),
    (Section('More', ('notes',), collapsible='yes'), r'collapsible must be True or False'),
    (Section('More', ('notes',), flow='down'), r"flow must be 'rows' or 'columns', not 'down'"),
    (Section('More', ('notes',), folded=True), r'has no option folded'),
])
def test_a_wrong_section_option_keeps_its_default_with_one_warning(section, message):
    registry = Registry(visit(('Visit', ('title', 'room', 'code')), section))
    (model, text), = registry.layout_warnings
    assert model.__name__ == 'Visit' and text.startswith("Visit.Meta.layout: Section('More'") and message in text and text.endswith('it is ignored')
    more = view_for(model)['layout'][1]
    assert (more['label'], more['fields'], more['collapsible'], more['collapsed'], more['flow']) == ('More', ['notes'], True, True, 'rows')


@pytest.mark.parametrize('section, message', [
    (Section('More', 'notes'), 'lists its fields as a tuple of field names'),
    (Section('', ('notes',)), 'needs a title'),
    (Section('More', ()), 'is empty'),
])
def test_a_section_that_places_nothing_is_ignored_and_its_fields_go_to_other(section, message):
    registry = Registry(visit(('Visit', ('title', 'room', 'code')), section))
    (_, text), = registry.layout_warnings
    assert message in text
    assert [(group['label'], group['fields']) for group in view_for(registry.models['visit'])['layout']] == [
        ('Visit', ['title', 'room', 'code']), ('Other', ['notes'])]


def test_a_section_that_cannot_collapse_starts_open():
    registry = Registry(visit(Section('Visit', ('title',)), Section('More', ('notes',), collapsible=False, collapsed=True)))
    assert registry.layout_warnings == ()
    assert view_for(registry.models['visit'])['layout'][1]['collapsed'] is False


def test_names_used_twice_keep_the_first_and_any_number_of_sections_loads():
    registry = Registry(visit(Section('A', ('room',)), Section('B', ('room', 'nope', 'code')), Section('A', ('notes',))))
    texts = [text for _, text in registry.layout_warnings]
    assert len(texts) == 3 and "places 'room' again" in texts[0] and "names 'nope'" in texts[1] and "uses the name 'A' again" in texts[2]
    assert [(group['label'], group['fields']) for group in view_for(registry.models['visit'])['layout']] == [
        ('A', ['room']), ('B', ['code']), ('Other', ['title', 'notes'])]
    # Nine sections load (the limit is twelve blocks); past it each block is ignored with a warning.
    nine = Registry(visit(*(Section(f'S{index}', ('title',) if index == 0 else ()) for index in range(9)), name='Nine'))
    assert len(nine.layout_warnings) == 8
    many = Registry(visit(*(('S' + str(index), ('title',)) for index in range(13)), name='Many'))
    assert "Many.Meta.layout: ('S12', ('title',)) is past the limit of 12 blocks; it is ignored" in [text for _, text in many.layout_warnings]


def test_section_options_do_not_change_migration_checksums():
    plain = visit(('Visit', ('title', 'room')), ('More', ('notes', 'code')), name='Trip')
    folded = visit(Section('Visit', ('title', 'room'), collapsible=False), Section('More', ('notes', 'code'), collapsed='empty'), name='Trip')
    assert Migration('0001_trip', CreateModel(plain)).checksum() == Migration('0001_trip', CreateModel(folded)).checksum()


def test_the_playground_shows_all_three_starting_states():
    from examples.playground.models import Project
    assert [(group['label'], group['collapsible'], group['collapsed']) for group in view_for(Project)['layout']] == [
        ('Project', False, False), ('Budget', True, False), ('Schedule', True, True), ('Notes', True, 'empty')]


# ---------- flow, and the record tabs removed in 0.4 ----------

def test_a_section_flows_in_rows_by_default_or_down_each_column():
    model = visit(('Visit', ('title',)), Section('Where', ('room', 'code'), flow='columns'), Section('More', ('notes',)))
    assert [(group['label'], group['flow']) for group in view_for(model)['layout']] == [('Visit', 'rows'), ('Where', 'columns'), ('More', 'rows')]
    assert repr(Section('Where', ('room',), flow='columns')) == "Section('Where', ('room',), collapsible=True, collapsed=True, flow='columns')"
    for flow in ('down', 'Columns', None, 1):
        assert "flow must be 'rows' or 'columns'" in Registry(visit(Section('Where', ('room',), flow=flow))).layout_warnings[0][1]


def tabbed(name='Trip', **meta):
    from evlet import Child, Table
    line = type(f'{name}Stop', (Child,), {'place': Text()})
    return type(name, (Model,), {'title': Text(), 'stops': Table(line), 'Meta': type('Meta', (), meta)})


@pytest.mark.parametrize('meta, message', [
    (dict(tabs=('stops', 'details')), r'Trip.Meta.tabs was removed: record pages have no tabs.*Main\(fields\)'),
    (dict(default_tab='stops'), r'Trip.Meta.default_tab was removed: record pages have no tabs'),
])
def test_meta_tabs_and_default_tab_fail_when_the_model_is_defined(meta, message):
    with pytest.raises(ValueError, match=message):
        tabbed(**meta)


def test_the_playground_shows_column_flow():
    from examples.playground.models import Customer, Project
    assert [group['flow'] for group in view_for(Customer)['layout']] == ['columns']
    assert 'tabs' not in view_for(Project) and 'default_tab' not in view_for(Project)
    assert view_for(Project)['side_blocks'] == ['related', 'info', 'key_fields']


def test_meta_summary_and_side_blocks_go_into_the_schema_only_when_set():
    view = view_for(Registry(tabbed(name='Tour', summary=False, side_blocks=('related', 'info'))).models['tour'])
    assert (view['summary'], view['side_blocks']) == (False, ['related', 'info'])
    assert view_for(Registry(tabbed(name='Hike', side_blocks=())).models['hike'])['side_blocks'] == []
    plain = view_for(Registry(tabbed(name='Walk')).models['walk'])
    assert 'summary' not in plain and 'side_blocks' not in plain


@pytest.mark.parametrize('meta, message', [
    (dict(summary='no'), r"Trip.Meta.summary must be True or False, not 'no'"),
    (dict(summary=0), r'Trip.Meta.summary must be True or False, not 0'),
    (dict(side_blocks=('related', 'history')), r'Trip.Meta.side_blocks: list blocks once each from key_fields, related, files, info'),
    (dict(side_blocks=('info', 'info')), r'Trip.Meta.side_blocks: list blocks once each'),
    (dict(side_blocks='related'), r'Trip.Meta.side_blocks: list blocks once each'),
])
def test_meta_summary_and_side_block_mistakes_fail_registration(meta, message):
    with pytest.raises(ValueError, match=message):
        Registry(tabbed(**meta))


# ---------- display options ----------

def test_integer_grouping_false_shows_a_year_without_separators_and_is_checked():
    from evlet import Integer
    from evlet.ui.metadata import field_schema

    class Machine(Model):
        title = Text()
        year_made = Integer(grouping=False)
        count = Integer()
    fields = {field['name']: field for field in Registry(Machine).ui_schema()['models'][0]['fields']}
    assert fields['year_made']['grouping'] is False and 'grouping' not in fields['count']
    # Display only: the field's metadata (and so its migrations) does not change.
    assert Integer(grouping=False).metadata() == Integer().metadata()
    assert 'grouping' not in field_schema(Integer())
    with pytest.raises(ValueError, match='grouping must be True or False'):
        Integer(grouping='no')
