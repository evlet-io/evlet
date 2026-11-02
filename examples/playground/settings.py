"""Projects settings: shared by everyone ("For everyone") and chosen by each person ("Just for you")."""

from evlet import AppSettings, Boolean, Decimal, Integer, MultiSelect, Select, UserPreferences


class ProjectSettings(AppSettings):
    editors = ('editor',)
    sections = {'Work week': ('days', 'hours'), 'Progress': ('progress', 'task_status', 'vat')}
    days = MultiSelect(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], default=['mon', 'tue', 'wed', 'thu', 'fri'], label='Working days')
    hours = Integer(default=8, minimum=1, maximum=24, label='Hours per day', suffix='hours')
    progress = Select(['tasks', 'manual'], default='tasks', label='Project progress comes from')
    task_status = Select(['todo', 'planned'], default='todo', label='New tasks start as')
    vat = Decimal(scale=2, default='5', minimum='0', maximum='100', label='Default VAT rate', suffix='%')


class ProjectPreferences(UserPreferences):
    group = Select(['status', 'project', 'due'], default='status', label='Group my board by')
    cc_me = Boolean(default=False, label='Copy me on project updates')
