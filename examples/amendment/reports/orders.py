from evlet import Report, Link, Column, Ref
from amendment.models.masters import Company, Warehouse
from amendment.models.sales_order import SalesOrder


class SalesRegister(Report):
    title = 'Sales Register'
    roles = ('admin', 'editor')
    company = Link(Company, required=True)
    warehouse = Link(Warehouse, filters={'company': Ref('company')})
    columns = (Column('id', type='integer'), Column('name'), Column('total', type='currency'))

    def run(self):
        filters = {'company': self.company}
        if self.warehouse: filters['warehouse'] = self.warehouse
        page = self.context.list(SalesOrder, filters=filters, limit=self.context.limit, offset=self.context.offset)
        return {**page, 'rows': [{key: row[key] for key in ('id', 'name', 'total')} for row in page['rows']]}


class LiveSalesRegister(SalesRegister):
    title = 'Live Sales Register'
    autorun = True
