"""Trusted background work: module-level Task and Schedule objects (docs/jobs-realtime.md#background-work-in-a-flat-app).

The nightly check runs with the job's database session, not as a role-limited @cron user."""
from evlet import Schedule, Task
from amendment.models.masters import StockBalance


def check_stock(tx, job, payload):
    # Count balances that went negative; the result is kept in the job history.
    negative = tx.count(StockBalance, filters={'actual_qty__lt': 0})
    job.progress(negative, message=f'{negative} negative stock balance' + ('' if negative == 1 else 's'))


stock_check = Task('amendment_stock_check', check_stock)
nightly_stock_check = Schedule('amendment_stock_check', 'amendment_stock_check', every=86400)
