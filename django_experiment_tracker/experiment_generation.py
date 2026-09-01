import itertools

from django.db.models import Count, Q
from django_experiment_tracker.models import Tag


def _build_parameter_choices(group, substitutes):
    choices = []

    for membership in group.parameter_memberships.all():
        parameter = membership.parameter
        key = (group.parameter_group_name, parameter.parameter_name)

        if key in substitutes:
            values = substitutes[key]
        elif membership.parameter_enum_id:
            values = [
                enum_value.parameter_enum_value
                for enum_value
                in membership.parameter_enum.parameterenumvalue_set.all()
            ]
        else:
            values = [membership.parameter_default_value]
        if '???' in values:
            raise ValueError(
                f"{group.parameter_group_name}->{parameter.parameter_name}: "
                "Missing value."
            )

        choices.append(
            [(group, parameter, value) for value in values]
        )

    return choices


def _prefetch_parameters(parameter_groups):
    return parameter_groups.prefetch_related(
        "parameter_memberships__parameter",
        "parameter_memberships__parameter_enum__parameterenumvalue_set",
    )


def build_parameters(parameter_groups, substitutes=None):
    """Produce one Cartesian product combining all supplied groups."""
    substitutes = substitutes or {}
    choices = []

    for group in _prefetch_parameters(parameter_groups):
        choices.extend(_build_parameter_choices(group, substitutes))

    return itertools.product(*choices)


def build_parameters_by_group(parameter_groups, substitutes=None):
    """Produce a separate Cartesian product for each supplied group."""
    substitutes = substitutes or {}

    for group in _prefetch_parameters(parameter_groups):
        choices = _build_parameter_choices(group, substitutes)
        yield from itertools.product(*choices)


def _fk_field_name(experiment_parameter_model, experiment_model):
    for field in experiment_parameter_model._meta.get_fields():
        if getattr(field, "many_to_one", False) and getattr(field, "related_model", None) is experiment_model:
            return field.name
    raise ValueError(
        f"Could not find ForeignKey from {experiment_parameter_model.__name__} to {experiment_model.__name__}."
    )


def _param_query_name(experiment_parameter_model, experiment_model):
    fk_name = _fk_field_name(experiment_parameter_model, experiment_model)
    fk_field = experiment_parameter_model._meta.get_field(fk_name)
    return fk_field.related_query_name()


def _param_accessor_name(experiment_parameter_model, experiment_model):
    fk_name = _fk_field_name(experiment_parameter_model, experiment_model)
    fk_field = experiment_parameter_model._meta.get_field(fk_name)
    return fk_field.remote_field.get_accessor_name()


def get_or_create_parameterized_model(
    *,
    model,
    parameter_model,
    parameters,
    model_kwargs,
):
    parameters = list(parameters)
    relation_query_name = _param_query_name(parameter_model, model)

    q = Q()
    for parameter_group, parameter, parameter_value in parameters:
        q |= Q(
            **{
                f"{relation_query_name}__parameter_group": parameter_group,
                f"{relation_query_name}__parameter": parameter,
                f"{relation_query_name}__parameter_value": parameter_value,
            }
        )

    candidates = model.objects.annotate(
        total_params=Count(relation_query_name, distinct=True),
        matched_params=Count(relation_query_name, filter=q, distinct=True),
    ).filter(
        total_params=len(parameters),
        matched_params=len(parameters),
    )

    if 'git_commit_valid_for' in model_kwargs:
        candidates = candidates.filter(git_commit_valid_for=model_kwargs['git_commit_valid_for'])

    candidate_count = candidates.count()
    if candidate_count == 0:
        row = model(**model_kwargs)
        parameter_rows = []
        fk_name = _fk_field_name(parameter_model, model)
        for parameter_group, parameter, parameter_value in parameters:
            parameter_rows.append(
                parameter_model(
                    **{
                        fk_name: row,
                        "parameter_group": parameter_group,
                        "parameter": parameter,
                        "parameter_value": parameter_value,
                    }
                )
            )
    else:
        assert candidate_count == 1, candidates
        relation_accessor = _param_accessor_name(parameter_model, model)
        row = candidates.prefetch_related(relation_accessor).first()
        parameter_rows = getattr(row, relation_accessor).all()

    return candidate_count != 0, (row, parameter_rows)


def create_parameterized_model_and_parameters(
    *,
    model,
    parameter_model,
    rows_to_create,
    parameter_rows_to_create,
    tags,
):
    created_rows = model.objects.bulk_create(rows_to_create)
    through_model = model.tags.through
    fk_name = None
    tag_fk_name = None
    for field in through_model._meta.get_fields():
        if not getattr(field, "many_to_one", False):
            continue
        related_model = getattr(field, "related_model", None)
        if related_model is model:
            fk_name = field.name
        elif related_model is Tag:
            tag_fk_name = field.name

    if fk_name is None or tag_fk_name is None:
        raise ValueError(f"Could not determine through-model foreign keys for {model.__name__} tags.")

    assigned_tags = [
        through_model(**{f"{fk_name}_id": row.id, f"{tag_fk_name}_id": tag.id})
        for row in created_rows
        for tag in tags
    ]
    through_model.objects.bulk_create(assigned_tags)
    parameter_model.objects.bulk_create(parameter_rows_to_create)


def create_parameterized_model_from_parameters(
    *,
    model,
    parameter_model,
    parameters,
    model_kwargs,
    tags,
    insert_batch_size=100,
):
    rows_to_create = []
    parameter_rows_to_create = []
    for eps in parameters:
        existed, (row, parameter_rows) = get_or_create_parameterized_model(
            model=model,
            parameter_model=parameter_model,
            parameters=eps,
            model_kwargs=model_kwargs,
        )
        if existed:
            row.tags.add(*tags)
        else:
            rows_to_create.append(row)
            parameter_rows_to_create.extend(parameter_rows)

        if len(rows_to_create) >= insert_batch_size:
            create_parameterized_model_and_parameters(
                model=model,
                parameter_model=parameter_model,
                rows_to_create=rows_to_create,
                parameter_rows_to_create=parameter_rows_to_create,
                tags=tags,
            )
            rows_to_create = []
            parameter_rows_to_create = []

    if rows_to_create:
        create_parameterized_model_and_parameters(
            model=model,
            parameter_model=parameter_model,
            rows_to_create=rows_to_create,
            parameter_rows_to_create=parameter_rows_to_create,
            tags=tags,
        )
