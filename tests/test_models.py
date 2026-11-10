from decimal import Decimal

import pytest

from evlet import Currency, Integer, Link, Model, Registry, Text, ValidationError
from examples.playground.models import Customer, Invoice, registry


def test_defaults_inheritance_and_serialization():
    class Named(Model):
        title = Text(required=True)

    class Product(Named):
        quantity = Integer(default=1, minimum=0)
        price = Currency(default="12.50")
        notes = Text(default=lambda: "New")

    item = Product(title="Notebook")
    assert item.price == Decimal("12.50")
    assert item.to_dict() == {"id": None, "title": "Notebook", "quantity": 1,
                              "price": "12.50", "notes": "New", "name": None}
    with pytest.raises(AttributeError):
        item.title = "Changed"


@pytest.mark.parametrize("data,field", [
    ({}, "customer_name"), ({"customer_name": None}, "customer_name"), ({"customer_name": "  "}, "customer_name"),
    ({"customer_name": 12}, "customer_name"), ({"customer_name": "x" * 121}, "customer_name"),
    ({"customer_name": "A", "id": 8}, "id"), ({"customer_name": "A", "unknown": 1}, "unknown"),
    ({"customer_name": "\ud800"}, "customer_name"), ({"customer_name": "A", "self": 1}, "self"),
])
def test_model_rejects_invalid_fields(data, field):
    with pytest.raises(ValidationError) as caught:
        Customer(**data)
    assert field in caught.value.errors


@pytest.mark.parametrize("value", [True, 1.1, "1", 2**63, -(2**63)-1, -1])
def test_integer_validation(value):
    class Counter(Model):
        count = Integer(required=True, minimum=0)

    with pytest.raises(ValidationError):
        Counter(count=value)


# A Currency field keeps 6 places; its currency's own places are checked when the record is written
# (tests/test_currency_decimals.py).
@pytest.mark.parametrize("value", ["NaN", "Infinity", "sNaN", "garbage", "1.0000001", 1.2, True, "1e100", "-0.01", "0"])
def test_currency_rejects_invalid_or_lossy_values(value):
    with pytest.raises(ValidationError):
        Invoice(customer=1, reference="INV-1", total=value)


def test_registry_is_explicit_and_schema_is_renderer_neutral():
    schema = registry.schema()
    assert schema["version"] == 1
    invoice = next(model for model in schema["models"] if model["name"] == "invoice")
    fields = {field["name"]: field for field in invoice["fields"]}
    assert fields["customer"]["target"] == "customer"
    assert fields["total"]["type"] == "currency"
    assert "primevue" not in str(schema).lower()
    assert Registry(Customer).schema()["models"] == [schema["models"][0]]
    with pytest.raises(ValueError, match="not registered"):
        Registry(Invoice)
    with pytest.raises(ValueError, match="Duplicate"):
        Registry(Customer, Customer)


@pytest.mark.parametrize("name", ["id", "ID", "to_dict", "bad-name", "_private"])
def test_unsafe_or_reserved_field_names(name):
    with pytest.raises(ValueError):
        type("Invalid", (Model,), {name: Text()})


def test_invalid_models_and_registry_isolation():
    with pytest.raises(ValueError):
        Registry(Model)
    with pytest.raises(ValueError):
        Registry(type("Schema", (Model,), {}))
    with pytest.raises(ValueError):
        Registry(type("Bad-Name", (Model,), {}))
    with pytest.raises(ValueError):
        type("Bad", (Model,), {"Name": Text(), "name": Text()})
    with pytest.raises(ValueError):
        type("Bad", (Customer,), {"name": "override"})
    class Unregistered(Model):
        pass
    with pytest.raises(ValueError, match="registry"):
        registry.name_for(Unregistered)


def test_link_rejects_zero_and_boolean():
    for value in (0, True):
        with pytest.raises(ValidationError):
            Invoice(customer=value, reference="INV-1", total="1.00")
