"""Public APIs for models, persistence, queries, and explicit migrations."""

# The modules that register model build steps (evlet/models.py BUILD_ORDER), before any model class exists.
from . import effects, guards, journal, ledger, meaning, measures, permissions, sequences, statemanager  # noqa: F401
from .app import Application
from .database import ConflictError, Database, NotFoundError
from .fields import (Boolean, Currency, CurrentUser, Date, DateTime, Decimal, Duration, Email, Integer,
                     Image, Link, LongText, MultiSelect, Percent, Phone, Reference, RichText, Select, Tags, Text, Time, URL)
from .migrations import (AddField, AddIndex, AddTable, AlterField, CreateModel, DropField, Migration, MigrationPlan, Recompute,
                         RenameField, RetireField, RunData, SchemaError)
from .models import Model, Registry, ValidationError, Child
from .declarations import Ref, Table, Query, action, depends
from .sequences import Naming, Sequence, Series
from .statemanager import State, release, state, validate

__all__ = [
    "AddField", "AddIndex", "AddTable", "AlterField", "Application", "Boolean", "ConflictError", "CreateModel", "DropField",
    "Currency", "CurrentUser", "Database", "Date", "DateTime", "Decimal", "Email", "Image", "Integer", "Link",
    "LongText", "Migration", "MigrationPlan", "Model", "MultiSelect", "NotFoundError", "Registry", "RenameField",
    "SchemaError", "Select", "Text", "URL", "ValidationError",
    "Duration", "Naming", "Percent", "Phone", "Recompute", "Reference", "RetireField", "RichText", "RunData", "Sequence", "Series", "State", "Tags", "Time",
]

from .auth import LocalAuth, OIDC, User
from .permissions import Hierarchy, Policy, Rule, any_of, own_customer
from .business import Action, ChildTable

__all__ += ["Action", "ChildTable", "Hierarchy", "LocalAuth", "OIDC", "Policy", "Rule", "User", "any_of", "own_customer"]

from .storage import LocalFileStorage, SQLiteStorage, StorageError

__all__ += ["LocalFileStorage", "SQLiteStorage", "StorageError"]

from .identity import RecordId

__all__ += ["RecordId"]


__all__ += ["Tenant", "TenantApplication", "TenantRegistry"]

from .events import EventStore
from .jobs import JobContext, JobQueue, JobReceipt, Task

__all__ += ["EventStore", "JobContext", "JobQueue", "JobReceipt", "Task"]


__all__ += ["LambdaHTTP", "S3Storage"]


__all__ += ["AI", "ConnectorError", "HTTPClient", "MCPServer"]

from .ui.metadata import Html, Main, Section

__all__ += ["App", "Html", "Main", "Section"]

from .auth import Security

__all__ += ["AppDefinition", "Instance", "Schedule", "Security"]
from .routing import ApiScope, ControlsAccess, Docs, Frontend, Host, Request, Response
__all__ += ["ApiScope", "ControlsAccess", "Docs", "Frontend", "Host", "Request", "Response"]
__all__ += ["Child", "Table", "Ref", "Query", "action", "validate", "depends", "release", "state"]
from .forms import Chart, Dialog, Report, Column
from .effects import Effect, Negate, Swap
__all__ += ["Effect", "Negate", "Swap"]
__all__ += ["Chart", "Dialog", "Report", "Column"]

from .slips import Outcome, Slip, present

__all__ += ["Outcome", "Slip", "present"]
from .services import api, cron
__all__ += ["api", "cron"]
from .measures import Count, CountDistinct, Day, Max, Min, Month, Sum, Year
__all__ += ["Count", "CountDistinct", "Day", "Max", "Min", "Month", "Sum", "Year"]
from .meaning import Attachments, Related
__all__ += ["Attachments", "Related"]
from .journal import Journal
__all__ += ["Journal"]

__all__ += ["SCOPES"]

__all__ += ["AppSettings", "UserPreferences"]

__all__ += ["ConnectionFailed", "Connector", "ConnectorField"]

__all__ += ["Mail"]
__all__ += ["DevSignIn"]


__all__ += ["PrintDesign"]

from .ledger import LockTimeout
from .migrations import SetAppendOnly

__all__ += ["LockTimeout", "SetAppendOnly"]

from .currencies import (currency_decimals, currency_unit, round_money, round_stored_money, set_currency_decimals,
                         set_currency_display)

__all__ += ["currency_decimals", "currency_unit", "round_money", "round_stored_money", "set_currency_decimals",
            "set_currency_display"]

from .workspaces import NavGroup, Shortcut, Workspace

__all__ += ["NavGroup", "Shortcut", "Workspace"]


# Optional subsystems have no model-build registration side effects. Mandatory build steps above
# remain eager; public names resolve to their original objects and are cached after first use.
_LAZY_EXPORTS = {
    'Tenant': 'tenancy',
    'TenantApplication': 'tenancy',
    'TenantRegistry': 'tenancy',
    'S3Storage': 'object_storage',
    'LambdaHTTP': 'serverless',
    'ConnectorError': 'connector',
    'HTTPClient': 'connector',
    'MCPServer': 'mcp',
    'AI': 'eva',
    'App': 'ui.application',
    'AppDefinition': 'instances',
    'Instance': 'instances',
    'Schedule': 'instances',
    'SCOPES': 'controls',
    'AppSettings': 'settings',
    'UserPreferences': 'settings',
    'ConnectionFailed': 'connectors',
    'Connector': 'connectors',
    'ConnectorField': 'connectors',
    'Mail': 'mailboxes',
    'DevSignIn': 'devlink',
    'PrintDesign': 'printing',
}


def __dir__():
    return sorted(set(globals()) | set(__all__))


def __getattr__(name):
    if name in _LAZY_EXPORTS:
        from importlib import import_module
        value = getattr(import_module('.' + _LAZY_EXPORTS[name], __name__), name)
        globals()[name] = value
        return value
    # Removed in 0.4: say where to go instead of a bare "cannot import name".
    if name == 'Tab':
        raise ImportError('evlet.Tab was removed: record pages have no tabs. Use Main(fields, *tables) first in '
                          'Meta.layout and Section(...) after it')
    # Removed in 0.4.5 (docs/database-adapters.md).
    if name == 'PostgreSQLDatabase':
        from .database import POSTGRESQL_REMOVED
        raise ImportError(POSTGRESQL_REMOVED)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
