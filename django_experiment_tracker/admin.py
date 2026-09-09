from django.contrib import admin

from django_experiment_tracker import models
from django_experiment_tracker.admin_search import ParameterValueSearchAdminMixin


@admin.register(models.GitCommit)
class GitCommitAdmin(admin.ModelAdmin):
    pass


class ParameterEnumValueInline(admin.TabularInline):
    model = models.ParameterEnumValue
    min_num = 1
    extra = 0


@admin.register(models.ParameterEnum)
class ParameterEnumAdmin(admin.ModelAdmin):
    inlines = [
        ParameterEnumValueInline,
    ]


class ParameterGroupInline(admin.TabularInline):
    model = models.ParameterGroupParameter
    min_num = 0
    extra = 0


@admin.register(models.Parameter)
class ParameterAdmin(admin.ModelAdmin):
    inlines = [
        ParameterGroupInline,
    ]


class ParameterInline(admin.TabularInline):
    model = models.ParameterGroupParameter
    min_num = 0
    extra = 0


@admin.register(models.ParameterGroup)
class ParameterGroupAdmin(admin.ModelAdmin):
    exclude = ('parameters',)
    inlines = [
        ParameterInline,
    ]
