"""Run with a clean wheel-installed interpreter and -I to exclude checkout imports."""

from pathlib import Path
import tempfile

import evlet
from evlet import AddField, Boolean, Currency, Database, Migration, Model, Registry, Text


def main():
    checkout = Path(__file__).resolve().parents[1]
    if Path(evlet.__file__).resolve().is_relative_to(checkout / "evlet"):
        raise RuntimeError("Package check imported the checkout instead of the installed wheel")

    class Product(Model):
        name = Text(required=True)
        price = Currency(required=True)

    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "package.sqlite3", Registry(Product))
        db.initialize()
        item = db.create(Product, name="Installed package", price="12.50")
        if db.get(Product, item.id).to_dict()["price"] != "12.50":
            raise RuntimeError("Installed package failed the SQLite round trip")
        UpdatedProduct = type('Product', (Model,), {
            'name': Text(required=True), 'price': Currency(required=True), 'active': Boolean(default=True),
        })
        updated = Database(db.path, Registry(UpdatedProduct))
        updated.migrate([Migration('active', AddField('product', 'active', Boolean(default=True)))])
        saved = updated.update(UpdatedProduct, item.id, price='15.25')
        if saved.to_dict()['price'] != '15.25' or not saved.active:
            raise RuntimeError('Installed package failed migration/update')
        if updated.count(UpdatedProduct, filters={'price__gte': '15.00'}) != 1:
            raise RuntimeError('Installed package failed query')
        schema = updated.registry.ui_schema()
        if schema['ui_version'] != 1 or schema['models'][0]['title_field'] != 'name':
            raise RuntimeError('Installed package failed UI metadata')
        records, total = updated.page(UpdatedProduct, search='installed', limit=1)
        if total != 1 or records[0].id != item.id:
            raise RuntimeError('Installed package failed search/count')
        updated.delete(UpdatedProduct, item.id)
    print("Installed wheel: migrations, updates, queries, deletes; import, model definition, SQLite persistence, and currency round trip passed")


if __name__ == "__main__":
    main()
