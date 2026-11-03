"""An item master as wide as an ERP one: about 200 fields and two child tables, so list filters
can be tried at scale (search the field picker, filter on price rows)."""

from evlet import Boolean, Child, Currency, Date, Decimal, Integer, Link, LongText, Model, Rule, Select, Table, Text

CURRENCIES = ['AED', 'SAR', 'USD', 'EUR', 'INR']
GROUPS = ['raw material', 'finished good', 'spare part', 'consumable', 'service']
UNITS = ['nos', 'kg', 'box', 'litre', 'metre']
MARKETS = (('uae', 'UAE'), ('ksa', 'KSA'), ('oman', 'Oman'), ('qatar', 'Qatar'), ('bahrain', 'Bahrain'),
           ('kuwait', 'Kuwait'), ('india', 'India'), ('egypt', 'Egypt'), ('jordan', 'Jordan'), ('export', 'Export'))
WAREHOUSES = (('dubai', 'Dubai'), ('jebel_ali', 'Jebel Ali'), ('abu_dhabi', 'Abu Dhabi'), ('sharjah', 'Sharjah'),
              ('riyadh', 'Riyadh'), ('jeddah', 'Jeddah'), ('muscat', 'Muscat'), ('doha', 'Doha'))
SPECIFICATIONS = ('colour', 'material', 'finish', 'grade', 'model number', 'part number', 'standard', 'voltage',
                  'power rating', 'thread size', 'pressure rating', 'temperature range', 'shelf life', 'pack type',
                  'certification', 'manufacturer', 'manufacturer part', 'range', 'season', 'style',
                  'size code', 'pattern', 'capacity', 'connector', 'coating', 'hardness', 'density', 'flavour',
                  'fragrance', 'gauge')


def item_fields():
    """Per-market selling terms, per-warehouse stock settings and free-text specifications.

    Frozen with migration 0005_items; add item fields through a new migration, not here."""
    fields = {}
    for key, name in MARKETS:
        fields[f'{key}_price'] = Currency(minimum='0', currency='AED', label=f'{name} price')
        fields[f'{key}_min_qty'] = Integer(minimum=0, label=f'{name} minimum quantity')
        fields[f'{key}_lead_days'] = Integer(minimum=0, label=f'{name} lead days')
        fields[f'{key}_listed'] = Boolean(required=True, default=False, label=f'Listed in {name}')
        fields[f'{key}_launch'] = Date(label=f'{name} launch date')
        fields[f'{key}_channel'] = Select(['retail', 'wholesale', 'online', 'project'], label=f'{name} channel')
        fields[f'{key}_vat'] = Select(['standard', 'zero rated', 'exempt'], label=f'{name} VAT')
        fields[f'{key}_discount'] = Decimal(scale=2, minimum='0', maximum='100', suffix='%', label=f'{name} discount')
        fields[f'{key}_promo_price'] = Currency(minimum='0', currency='AED', label=f'{name} promotion price')
        fields[f'{key}_status'] = Select(['active', 'paused', 'discontinued'], label=f'{name} status')
    for key, name in WAREHOUSES:
        fields[f'{key}_reorder_level'] = Integer(minimum=0, label=f'{name} reorder level')
        fields[f'{key}_reorder_qty'] = Integer(minimum=0, label=f'{name} reorder quantity')
        fields[f'{key}_bin'] = Text(max_length=20, label=f'{name} bin')
        fields[f'{key}_stocked'] = Boolean(required=True, default=False, label=f'Stocked in {name}')
        fields[f'{key}_counted_on'] = Date(label=f'{name} last counted')
        fields[f'{key}_safety_stock'] = Integer(minimum=0, label=f'{name} safety stock')
    for name in SPECIFICATIONS:
        fields[name.replace(' ', '_')] = Text(max_length=80, label=name.capitalize())
    return fields


def core_fields():
    """Frozen with migration 0005_items, like item_fields()."""
    return {
        'item_code': Text(required=True, unique=True, max_length=40, label='Item code'),
        'item_name': Text(required=True, max_length=140, label='Item name'),
        'item_group': Select(GROUPS, required=True, default='finished good', label='Item group'),
        'stock_uom': Select(UNITS, required=True, default='nos', label='Stock unit'),
        'brand': Text(max_length=80),
        'standard_rate': Currency(minimum='0', default='0.00', currency='AED', label='Standard rate'),
        'weight_kg': Decimal(scale=3, minimum='0', label='Weight', suffix='kg'),
        'disabled': Boolean(required=True, default=False),
        'introduced_on': Date(label='Introduced on'),
        'default_customer': Link('customer', label='Default customer'),
        'description': LongText(),
        'hs_code': Text(max_length=12, label='HS code'),
        'country_of_origin': Select(['UAE', 'China', 'India', 'Germany', 'Italy', 'USA', 'Japan'], label='Country of origin'),
        'length_cm': Decimal(scale=1, minimum='0', label='Length', suffix='cm'),
        'width_cm': Decimal(scale=1, minimum='0', label='Width', suffix='cm'),
        'height_cm': Decimal(scale=1, minimum='0', label='Height', suffix='cm'),
        'volume_litres': Decimal(scale=2, minimum='0', label='Volume', suffix='L'),
        'shelf_life_days': Integer(minimum=0, label='Shelf life', suffix='days'),
        'warranty_months': Integer(minimum=0, label='Warranty', suffix='months'),
        'is_stock_item': Boolean(required=True, default=True, label='Maintain stock'),
        'has_batch': Boolean(required=True, default=False, label='Has batch numbers'),
        'has_serial': Boolean(required=True, default=False, label='Has serial numbers'),
        'inspection_required': Boolean(required=True, default=False, label='Inspection required'),
        'valuation_method': Select(['FIFO', 'moving average'], label='Valuation method'),
        'lead_time_days': Integer(minimum=0, label='Lead time', suffix='days'),
        'tax_category': Select(['standard 5%', 'zero rated', 'exempt'], label='Tax category'),
    }


class Price(Child):
    price_list = Select(['standard selling', 'wholesale', 'export', 'standard buying'], required=True,
                        default='standard selling', label='Price list')
    customer = Link('customer')
    currency = Select(CURRENCIES, required=True, default='AED')
    rate = Currency(minimum='0', default='0.00', currency='currency')
    min_qty = Integer(minimum=0, default=0, label='Minimum quantity')
    valid_from = Date(label='Valid from')


class Barcode(Child):
    barcode = Text(required=True, max_length=40)
    barcode_type = Select(['EAN', 'UPC', 'Code 128'], default='EAN', label='Barcode type')


ITEM_FIELDS = ('item_code', 'item_name', 'item_group', 'stock_uom', 'brand', 'standard_rate', 'disabled')


def market_fields(markets):
    return tuple(f'{key}_{suffix}' for key, _ in markets
                 for suffix in ('price', 'min_qty', 'lead_days', 'listed', 'launch', 'channel', 'vat', 'discount', 'promo_price', 'status'))


class Meta:
    description = 'Items with about 200 fields, prices and barcodes: a list to try filters on.'
    list_fields = ('item_code', 'item_name', 'item_group', 'standard_rate', 'disabled')
    search_fields = ('item_code', 'item_name')
    layout = (('Item', ITEM_FIELDS),
              ('Details', tuple(name for name in core_fields() if name not in ITEM_FIELDS)),
              ('GCC markets', market_fields(MARKETS[:6])), ('Other markets', market_fields(MARKETS[6:])),
              ('Warehouses', tuple(f'{key}_{suffix}' for key, _ in WAREHOUSES
                                   for suffix in ('reorder_level', 'reorder_qty', 'bin', 'stocked', 'counted_on', 'safety_stock'))),
              ('Specifications', tuple(name.replace(' ', '_') for name in SPECIFICATIONS)))


Item = type('Item', (Model,), {'__module__': __name__, '__doc__': 'An item and its selling, stock and specification fields.',
                               'Meta': Meta, **core_fields(), **item_fields(),
                               # A model with tables denies by default; these are the Playground's usual roles.
                               'permissions': {'admin': Rule(('read', 'create', 'update', 'delete')),
                                               'editor': Rule(('read', 'create', 'update', 'delete')), 'viewer': Rule(('read',))},
                               'prices': Table(Price, label='Prices'), 'barcodes': Table(Barcode, label='Barcodes')})
