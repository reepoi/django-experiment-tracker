import shlex

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured

from django_experiment_tracker import models


class ParameterValueSearchAdminMixin:
    """Add structured tag and parameter searches to a model admin."""

    search_fields = ('pk',)
    search_help_text = (
        'Use t:<tag>, g:<group>, or p:<name>=<value>. '
        'All terms must match; quote values containing spaces.'
    )

    def get_parameter_value_relation(self):
        relations = [
            relation
            for relation in self.model._meta.get_fields()
            if relation.auto_created
            and relation.one_to_many
            and issubclass(relation.related_model, models.ParameterValue)
        ]
        if len(relations) != 1:
            raise ImproperlyConfigured(
                f'{self.model._meta.label} must have exactly one reverse foreign key '
                'from a ParameterValue subclass to use ParameterValueSearchAdminMixin.'
            )
        return relations[0].field.related_query_name()

    def get_search_results(self, request, queryset, search_term):
        try:
            terms = shlex.split(search_term)
        except ValueError:
            return queryset.none(), False

        parameter_value_relation = None
        for term in terms:
            if term.startswith('t:') and len(term) > len('t:'):
                try:
                    self.model._meta.get_field('tags')
                except FieldDoesNotExist:
                    return queryset.none(), False
                queryset = queryset.filter(
                    tags__tag_value__iexact=term.removeprefix('t:'),
                )
            elif term.startswith('g:') and len(term) > len('g:'):
                parameter_value_relation = (
                    parameter_value_relation or self.get_parameter_value_relation()
                )
                queryset = queryset.filter(**{
                    f'{parameter_value_relation}__parameter_group__parameter_group_name__iexact': (
                        term.removeprefix('g:')
                    ),
                })
            elif term.startswith('p:'):
                parameter = term.removeprefix('p:')
                if '=' not in parameter:
                    return queryset.none(), False
                name, value = parameter.split('=', maxsplit=1)
                if not name or not value:
                    return queryset.none(), False
                try:
                    parameter = models.Parameter.objects.get(
                        parameter_name__iexact=name,
                    )
                    value = models.Parameter.format_value(
                        parameter.parameter_type,
                        models.Parameter.parse_value(parameter.parameter_type, value),
                    )
                except (models.Parameter.DoesNotExist, ValueError):
                    return queryset.none(), False
                parameter_value_relation = (
                    parameter_value_relation or self.get_parameter_value_relation()
                )
                queryset = queryset.filter(**{
                    f'{parameter_value_relation}__parameter': parameter,
                    f'{parameter_value_relation}__parameter_value': value,
                })
            else:
                return queryset.none(), False

        return queryset.distinct(), bool(terms)
