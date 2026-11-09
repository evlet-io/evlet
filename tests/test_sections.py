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
        ('Visit', ['title'], False, False), ('Where', ['room'], True, False), ('More', ['notes'], True, True), ('Codes', ['code'], True, 'empty')]
    # A section that can't collapse is a plain group.
    assert view_for(visit(Section('Visit', ('title',), collapsible=False)))['layout'][0] == {
        'label': 'Visit', 'fields': ['title'], 'collapsible': False, 'collapsed': False, 'flow': 'rows'}
    # Fields a layout leaves out still go to Other, which always shows.
    assert view_for(visit(Section('Visit', ('title',), collapsed=True)))['layout'][-1] == {
        'label': 'Other', 'fields': ['room', 'notes', 'code'], 'collapsible': False, 'collapsed': False, 'flow': 'rows'}
    assert repr(Section('More', ('notes',), collapsed='empty')) == "Section('More', ('notes',), collapsible=True, collapsed='empty')"


@pytest.mark.parametrize('section, message', [
    (Section('More', ('notes',), collapsed='yes'), r"Visit layout section 'More': collapsed must be False, True or 'empty', not 'yes'"),
    (Section('More', ('notes',), collapsed=1), r"Visit layout section 'More': collapsed must be False, True or 'empty', not 1"),
    (Section('More', ('notes',), collapsed=None), r"collapsed must be False, True or 'empty', not None"),
    (Section('More', ('notes',), collapsible=False, collapsed=True), r"Visit layout section 'More': collapsed needs collapsible=True"),
    (Section('More', ('notes',), collapsible=False, collapsed='empty'), r'collapsed needs collapsible=True'),
    (Section('More', ('notes',), collapsible='yes'), r"Visit layout section 'More': collapsible must be True or False"),
    (Section('More', 'notes'), r"Visit layout section 'More': list its fields as a tuple of field names"),
    (Section('', ('notes',)), r'Visit layout: give every Section a title'),
])
def test_section_mistakes_fail_registration_naming_the_model_and_section(section, message):
    with pytest.raises(ValueError, match=message):
        Registry(visit(section))


def test_sections_keep_the_existing_layout_rules():
    with pytest.raises(ValueError, match='Layout lists room more than once'):
        Registry(visit(Section('A', ('room',)), Section('B', ('room',))))
    with pytest.raises(ValueError, match='Layout group A names unknown or hidden fields: nope'):
        Registry(visit(Section('A', ('nope',))))
    with pytest.raises(ValueError, match='Use at most eight layout groups'):
        Registry(visit(*(Section(f'S{index}', ()) for index in range(9))))


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
    assert repr(Section('Where', ('room',), flow='columns')) == "Section('Where', ('room',), collapsible=True, collapsed=False, flow='columns')"
    for flow in ('down', 'Columns', None, 1):
        with pytest.raises(ValueError, match=r"Visit layout section 'Where': flow must be 'rows' or 'columns'"):
            Registry(visit(Section('Where', ('room',), flow=flow)))


def tabbed(name='Trip', **meta):
    from evlet import Child, Table
    line = type(f'{name}Stop', (Child,), {'place': Text()})
    return type(name, (Model,), {'title': Text(), 'stops': Table(line), 'Meta': type('Meta', (), meta)})


@pytest.mark.parametrize('meta, message', [
    (dict(tabs=('stops', 'details')), r'Trip.Meta.tabs was removed: record pages have no tabs.*Main\(fields, \*tables\)'),
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
