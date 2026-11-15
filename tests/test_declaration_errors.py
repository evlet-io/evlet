"""Declaration errors name what is wrong, and a layout may leave fields for Other."""

import pytest

from evlet import Child, Integer, Model, Registry, Rule, Table, Text
from evlet.ui.metadata import view_for

EVERYTHING = {'admin': Rule(('read', 'create', 'update', 'delete'))}


def test_a_reserved_document_field_is_named():
    with pytest.raises(ValueError, match=r'Shipment\.source: "source" is reserved on documents.*rename the field'):
        type('Shipment', (Model,), {'permissions': EVERYTHING, 'source': Text(), 'lines': Table(type('ShipmentLine', (Child,), {'qty': Integer()}))})
    with pytest.raises(ValueError, match=r'Shipment\.context, Shipment\.key: '):
        type('Shipment', (Model,), {'permissions': EVERYTHING, 'key': Text(), 'context': Text()})


def test_layout_leaves_fields_for_other_and_names_mistakes():
    def model(layout):
        return type('Visit', (Model,), {'title': Text(), 'room': Text(), 'notes': Text(), 'secret': Text(hidden=True),
                                        'Meta': type('Meta', (), {'layout': layout})})
    main = {'collapsible': False, 'collapsed': False, 'flow': 'rows', 'main': True}
    other = {'label': 'Other', 'collapsible': True, 'collapsed': True, 'flow': 'rows'}
    assert view_for(model((('Visit', ('title',)),)))['layout'] == [
        {'label': 'Visit', 'fields': ['title'], **main}, {'fields': ['room', 'notes'], **other}]
    assert view_for(model((('Visit', ('title', 'room', 'notes')),)))['layout'] == [{'label': 'Visit', 'fields': ['title', 'room', 'notes'], **main}]
    # A mistake is ignored with a warning naming the model and the item; the fields it misplaced go to Other.
    registry = Registry(model((('Visit', ('title', 'room')), ('More', ('room',)), ('Extra', ('title', 'secret', 'nope')))))
    assert [text for _, text in registry.layout_warnings] == [
        "Visit.Meta.layout: ('More', ('room',)) places 'room' again; it is ignored",
        "Visit.Meta.layout: ('Extra', ('title', 'secret', 'nope')) places 'title' again; it is ignored",
        "Visit.Meta.layout: ('Extra', ('title', 'secret', 'nope')) names 'secret', which is not a visible field of Visit; it is ignored",
        "Visit.Meta.layout: ('Extra', ('title', 'secret', 'nope')) names 'nope', which is not a visible field of Visit; it is ignored"]
    assert view_for(registry.models['visit'])['layout'] == [{'label': 'Visit', 'fields': ['title', 'room'], **main}, {'fields': ['notes'], **other}]


def test_a_child_is_not_a_root_model():
    line = type('OrderLine', (Child,), {'qty': Integer()})
    with pytest.raises(ValueError, match=r'OrderLine is a Child row model\. Declare row models inside their document with Table\(\.\.\.\)'):
        Registry(line)
    # Used through a Table it registers as the document's rows.
    order = type('Order', (Model,), {'permissions': EVERYTHING, 'lines': Table(line)})
    assert [child.name for child in Registry(order).children] == ['lines']
