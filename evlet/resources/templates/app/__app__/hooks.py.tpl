from evlet import AppDefinition
from .models import models
from .views import views
from .actions import actions
from .permissions import rules
from .migrations import migrations

app = AppDefinition(models=models, views=views, actions=actions,
                    permissions=rules, migrations=migrations)
