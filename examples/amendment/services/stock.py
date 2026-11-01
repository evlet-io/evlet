from evlet import ValidationError
from amendment.models.masters import StockBalance


def available(context, item, warehouse):
    page = context.list(StockBalance, filters={'item': item, 'warehouse': warehouse}, limit=2)
    if page['total'] > 1: raise ValidationError({'stock': 'Resolve duplicate stock balances'})
    return page['rows'][0]['actual_qty'] if page['rows'] else None
