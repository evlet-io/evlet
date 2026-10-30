from evlet import CreateModel, Migration, Model, Text


class {{Class}}Item(Model):
    """Frozen initial schema. Add new migrations when the live model changes."""
    title = Text(required=True, max_length=160)


migrations = (Migration('0001_initial', CreateModel({{Class}}Item)),)
