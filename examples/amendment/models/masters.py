from evlet import Model, Text, Link, Decimal, Boolean, Select, Rule, Date, Integer, Phone


class Company(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    name = Text(required=True)
    phone = Phone()
    payment_days = Integer(default=30, minimum=0)


class Warehouse(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    name = Text(required=True)
    company = Link(Company, required=True)


class ItemMaster(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    name = Text(required=True)
    description = Text()
    unit = Select(('Unit', 'Kg'), required=True, default='Unit')


class BOM(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    name = Text(required=True)
    item = Link(ItemMaster, required=True)
    company = Link(Company, required=True)
    is_active = Boolean(default=True, required=True)
    unit = Select(('Unit', 'Kg'), required=True, default='Unit')


class StockBalance(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    item = Link(ItemMaster, required=True)
    warehouse = Link(Warehouse, required=True)
    actual_qty = Decimal(default='0', required=True)


class WorkOrder(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create')), 'viewer': Rule(('read',))}
    sales_order = Link('amendment.SalesOrder', required=True)
    source_row = Decimal(scale=0, required=True)
    item = Link(ItemMaster, required=True)
    company = Link(Company, required=True)
    warehouse = Link(Warehouse, required=True)
    bom = Link(BOM, required=True)
    unit = Select(('Unit', 'Kg'), required=True)
    qty = Decimal(required=True, minimum='0')
    cancelled = Boolean(default=False, required=True)
    production_date = Date()
