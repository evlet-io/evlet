"""A model-only import installs every build step without loading optional integrations."""
import subprocess
import sys


def test_model_build_registration_precedes_optional_exports():
    code = '''
import sys
from evlet import Model, Text, Registry
import evlet
from evlet.models import BUILD_ORDER, BUILD_STEPS
assert set(BUILD_ORDER) == set(BUILD_STEPS)
assert 'evlet.eva' not in sys.modules and 'evlet.printing' not in sys.modules
class Item(Model):
    title = Text(required=True)
Registry(Item)
assert set(evlet.__all__) <= set(dir(evlet))
from evlet import AI, PrintDesign, TenantApplication, LambdaHTTP, Mail, AppDefinition
from evlet.eva import AI as OriginalAI
assert AI is OriginalAI and evlet.AI is AI
from evlet import *
assert all(hasattr(evlet, name) for name in evlet.__all__)
'''
    subprocess.run([sys.executable, '-c', code], check=True, timeout=20)


def test_type_checkers_see_every_lazy_export_from_its_module():
    import ast
    from pathlib import Path
    import evlet
    tree = ast.parse(Path(evlet.__file__).read_text())
    block = next(node for node in tree.body if isinstance(node, ast.If) and getattr(node.test, 'id', None) == 'TYPE_CHECKING')
    seen = {alias.name: node.module for node in block.body if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert seen == evlet._LAZY_EXPORTS
    # The block is for type checkers only: importing evlet still loads none of these modules.
    code = 'import sys, evlet; assert not {"evlet.eva", "evlet.tenancy", "evlet.printing", "evlet.mcp"} & set(sys.modules)'
    subprocess.run([sys.executable, '-c', code], check=True, timeout=20)
