"""Declaration errors name what is wrong, and a layout may leave fields for Other."""

import pytest

from evlet import Child, Integer, Model, Registry, Rule, Table, Text
from evlet.metadata import view_for

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
    plain = {'collapsible': False, 'collapsed': False}
    assert view_for(model((('Visit', ('title',)),)))['layout'] == [
        {'label': 'Visit', 'fields': ['title'], **plain}, {'label': 'Other', 'fields': ['room', 'notes'], **plain}]
    assert view_for(model((('Visit', ('title', 'room', 'notes')),)))['layout'] == [{'label': 'Visit', 'fields': ['title', 'room', 'notes'], **plain}]
    with pytest.raises(ValueError, match='Layout lists room more than once'):
        view_for(model((('Visit', ('title', 'room')), ('More', ('room',)))))
    with pytest.raises(ValueError, match='Layout group Visit names unknown or hidden fields: secret, nope'):
        view_for(model((('Visit', ('title', 'secret', 'nope')),)))
    # Registration checks layouts the same way.
    with pytest.raises(ValueError, match='more than once'):
        Registry(model((('Visit', ('title', 'title')),)))


def test_a_child_is_not_a_root_model():
    line = type('OrderLine', (Child,), {'qty': Integer()})
    with pytest.raises(ValueError, match=r'OrderLine is a Child row model\. Declare row models inside their document with Table\(\.\.\.\)'):
        Registry(line)
    # Used through a Table it registers as the document's rows.
    order = type('Order', (Model,), {'permissions': EVERYTHING, 'lines': Table(line)})
    assert [child.name for child in Registry(order).children] == ['lines']
