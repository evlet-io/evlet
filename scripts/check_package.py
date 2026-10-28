"""Run with a clean wheel-installed interpreter and -I to exclude checkout imports."""

from pathlib import Path
import tempfile

import evlet
from evlet import Currency, Database, Model, Registry, Text


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
    print("Installed wheel: import, model definition, SQLite persistence, and currency round trip passed")


if __name__ == "__main__":
    main()
