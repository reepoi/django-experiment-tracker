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


@admin.register(models.ParameterGroupParameter)
class ParameterGroupParameterAdmin(admin.ModelAdmin):
    search_fields = ['parameter_group__name', 'parameter__name']
    ordering = ['parameter_group__name', 'parameter__name']

    def has_module_permission(self, request):
        return False


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


class ParameterValueAdmin(admin.TabularInline):
    min_num = 1
    extra = 0
    autocomplete_fields = ['definition']

    def get_queryset(self, request):
        return super().get_queryset(request).order_by(
            'definition__parameter_group__name',
            'definition__parameter__name',
        )


@admin.register(models.ParameterGroup)
class ParameterGroupAdmin(admin.ModelAdmin):
    exclude = ('parameters',)
    inlines = [
        ParameterInline,
    ]


@admin.register(models.Tag)
class TagAdmin(admin.ModelAdmin):
    pass
