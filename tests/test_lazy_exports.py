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
