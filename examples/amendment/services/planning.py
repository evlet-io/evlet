from decimal import Decimal
from evlet import ValidationError
from amendment.models.masters import WorkOrder, BOM, ItemMaster


def remaining(context, source):
    commitments = context.list(WorkOrder, filters={'sales_order': source.id, 'cancelled': False}, limit=100)
    if commitments['total'] > 100: raise ValidationError({'items': 'Reference planning is limited to 100 commitments'})
    quantities = {row.id: Decimal(str(row.qty)) for row in source.items}
    for row in commitments['rows']:
        key = int(Decimal(row['source_row']))
        quantities[key] = quantities.get(key, Decimal(0)) - Decimal(row['qty'])
    return quantities


def selection_rows(context, source):
    quantities = remaining(context, source)
    return [{'source_row_id': row.id, 'item': row.item, 'pending_qty': quantities[row.id],
             'qty': quantities[row.id], 'selected': quantities[row.id] > 0}
            for row in source.items if quantities[row.id] > 0]


def create_selected(context, source, selections, *, production_date=None):
    quantities = remaining(context, source)
    source_rows = {row.id: row for row in source.items}
    prepared = []
    for selection in selections:
        if not selection.selected: continue
        row = source_rows.get(selection.source_row_id)
        if row is None or selection.qty <= 0 or selection.qty > quantities[row.id]:
            raise ValidationError({'items.' + selection.key + '.qty': 'Quantity exceeds the current unplanned quantity'})
        bom = context.get(BOM, selection.bom)
        item = context.get(ItemMaster, row.item)
        if bom.item != row.item or bom.company != source.company or not bom.is_active:
            raise ValidationError({'items.' + selection.key + '.bom': 'Choose an active BOM for this item and company'})
        if bom.unit != item.unit or row.unit != item.unit:
            raise ValidationError({'items.' + selection.key + '.bom': 'Source item and BOM units must agree'})
        quantities[row.id] -= selection.qty
        prepared.append({'sales_order': source.id, 'source_row': row.id, 'item': row.item,
                         'company': source.company, 'warehouse': row.warehouse, 'bom': bom.id,
                         'unit': item.unit, 'qty': selection.qty, 'production_date': production_date})
    if not prepared: raise ValidationError({'items': 'Select at least one row'})
    return {'created': [context.create(WorkOrder, **values)['id'] for values in prepared]}
