from evlet import AppDefinition
from .models import models
from .actions import actions
from .permissions import rules
from .migrations import migrations

app = AppDefinition(models=models, actions=actions,
                    permissions=rules, migrations=migrations)
