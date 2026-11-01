from evlet import api, cron
from amendment.models.masters import StockBalance


@api(method='GET', permission=(StockBalance, 'read'))
def balance_count(context, warehouse: int):
    return {'count': context.count(StockBalance, filters={'warehouse': warehouse})}


@cron('0 2 * * *', role='editor')
def check_balances(context):
    # A bounded read-only fixture; importing it never runs the check.
    return context.count(StockBalance)
