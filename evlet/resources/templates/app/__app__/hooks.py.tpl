from evlet import AppDefinition
from .models import models
from .permissions import rules
from .migrations import migrations

app = AppDefinition(models=models, permissions=rules, migrations=migrations)
