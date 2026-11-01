from evlet import Model, Rule, Text


class {{Class}}Item(Model):
    permissions = {
        'admin': Rule(('read', 'create', 'update', 'delete')),
        'editor': Rule(('read', 'create', 'update')),
        'viewer': Rule(('read',)),
    }
    title = Text(required=True, max_length=160)

    class Meta:
        label = 'Item'
        plural = 'Items'
        title_field = 'title'
        list_fields = ('title',)
        search_fields = ('title',)
