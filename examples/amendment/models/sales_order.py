from datetime import date
from evlet import (Model, Child, Table, Dialog, Link, Text, Decimal, Currency,
                   Boolean, Date, Rule, Ref, depends, action, ValidationError, Select, Related)
from amendment.models.masters import Company, Warehouse, ItemMaster, BOM, WorkOrder
from amendment.services.stock import available
from amendment.services.planning import selection_rows, create_selected


class SalesOrder(Model):
    class Meta:
        # The fields that summarize an order, and its work orders newest production date first.
        # Work orders are found through their sales_order Link without this; Related names and orders them.
        key_fields = ('company', 'warehouse', 'status', 'total')
        relations = (Related(WorkOrder, 'sales_order', label='Work orders', limit=5, order=('-production_date', '-id')),)

    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}
    name = Text(required=True)
    company = Link(Company, required=True)
    warehouse = Link(Warehouse, required=True, filters={'company': Ref('company')})
    status = Text(default='Draft', read_only=True)

    class Item(Child):
        item = Link(ItemMaster, required=True)
        description = Text(fetch='item.description')
        unit = Select(('Unit', 'Kg'), fetch='item.unit', read_only=True)
        warehouse = Link(Warehouse, required=True, default=Ref('parent.warehouse'), filters={'company': Ref('parent.company')})
        qty = Decimal(default='1', required=True, minimum='0')
        rate = Currency(default='0', required=True, minimum='0')
        amount = Currency(compute='qty * rate')

        @depends('item', 'warehouse')
        def stock(self):
            return available(self.context, self.item, self.warehouse)
        available_qty = Decimal(query=stock, stored=False)

        def validate(self):
            if self.qty <= 0: raise ValidationError({'qty': 'Enter a positive quantity'})

    items = Table(Item, required=True)
    total = Currency(compute='sum(items.amount)')

    @action
    def confirm(self):
        if self.status != 'Draft': raise ValidationError({'status': 'Only drafts can be confirmed'})
        self.status = 'Confirmed'

    class CreateWorkOrders(Dialog):
        title = 'Create Work Orders'
        action = 'create_work_orders'
        production_date = Date(default=date.today, required=True)

        class Item(Child):
            selected = Boolean(default=True, required=True)
            source_row_id = Text(read_only=True)
            item = Link(ItemMaster, read_only=True)
            pending_qty = Decimal(read_only=True)
            qty = Decimal(default='0', minimum='0')
            selected_qty = Decimal(compute='qty if selected else 0', stored=False, hidden=True)
            bom = Link(BOM, filters={'item': Ref('item'), 'company': Ref('source.company'), 'is_active': True}, required_if='selected')

        items = Table(Item, required=True)
        total_qty = Decimal(compute='sum(items.selected_qty)', stored=False)

        def load(self, source):
            self.items = selection_rows(self.context, source)

        def submit(self, source):
            return create_selected(self.context, source, self.items, production_date=self.production_date)
