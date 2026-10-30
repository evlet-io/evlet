from evlet import Model, Text


class {{Class}}Item(Model):
    title = Text(required=True, max_length=160)

    class Meta:
        label = 'Item'
        plural = 'Items'
        title_field = 'title'
        list_fields = ('title',)
        search_fields = ('title',)


models = ({{Class}}Item,)
