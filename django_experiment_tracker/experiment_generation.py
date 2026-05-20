from django.db.models import Count, Q
from django_experiment_tracker.models import Tag


def _experiment_fk_field_name(experiment_parameter_model, experiment_model):
    for field in experiment_parameter_model._meta.get_fields():
        if getattr(field, "many_to_one", False) and getattr(field, "related_model", None) is experiment_model:
            return field.name
    raise ValueError(
        f"Could not find ForeignKey from {experiment_parameter_model.__name__} to {experiment_model.__name__}."
    )


def _experiment_param_query_name(experiment_parameter_model, experiment_model):
    fk_name = _experiment_fk_field_name(experiment_parameter_model, experiment_model)
    fk_field = experiment_parameter_model._meta.get_field(fk_name)
    return fk_field.related_query_name()


def _experiment_param_accessor_name(experiment_parameter_model, experiment_model):
    fk_name = _experiment_fk_field_name(experiment_parameter_model, experiment_model)
    fk_field = experiment_parameter_model._meta.get_field(fk_name)
    return fk_field.remote_field.get_accessor_name()


def get_or_create_experiment(
    *,
    experiment_model,
    experiment_parameter_model,
    experiment_parameters,
    experiment_model_kwargs,
):
    experiment_parameters = list(experiment_parameters)
    relation_query_name = _experiment_param_query_name(experiment_parameter_model, experiment_model)

    q = Q()
    for (parameter_group, parameter), parameter_value in experiment_parameters:
        q |= Q(
            **{
                f"{relation_query_name}__parameter_group": parameter_group,
                f"{relation_query_name}__parameter": parameter,
                f"{relation_query_name}__parameter_value": parameter_value,
            }
        )

    candidate_experiments = experiment_model.objects.annotate(
        total_params=Count(relation_query_name, distinct=True),
        matched_params=Count(relation_query_name, filter=q, distinct=True),
    ).filter(
        total_params=len(experiment_parameters),
        matched_params=len(experiment_parameters),
    )

    candidate_count = candidate_experiments.count()
    if candidate_count == 0:
        experiment_row = experiment_model(**experiment_model_kwargs)
        experiment_parameter_rows = []
        fk_name = _experiment_fk_field_name(experiment_parameter_model, experiment_model)
        for (parameter_group, parameter), parameter_value in experiment_parameters:
            experiment_parameter_rows.append(
                experiment_parameter_model(
                    **{
                        fk_name: experiment_row,
                        "parameter_group": parameter_group,
                        "parameter": parameter,
                        "parameter_value": parameter_value,
                    }
                )
            )
    else:
        assert candidate_count == 1, candidate_experiments
        relation_accessor = _experiment_param_accessor_name(experiment_parameter_model, experiment_model)
        experiment_row = candidate_experiments.prefetch_related(relation_accessor).first()
        experiment_parameter_rows = getattr(experiment_row, relation_accessor).all()

    return candidate_count != 0, (experiment_row, experiment_parameter_rows)


def create_experiments_and_parameters(
    *,
    experiment_model,
    experiment_parameter_model,
    experiment_rows_to_create,
    experiment_parameter_rows_to_create,
    tags,
):
    created_experiment_rows = experiment_model.objects.bulk_create(experiment_rows_to_create)
    through_model = experiment_model.tags.through
    experiment_fk_name = None
    tag_fk_name = None
    for field in through_model._meta.get_fields():
        if not getattr(field, "many_to_one", False):
            continue
        related_model = getattr(field, "related_model", None)
        if related_model is experiment_model:
            experiment_fk_name = field.name
        elif related_model is Tag:
            tag_fk_name = field.name

    if experiment_fk_name is None or tag_fk_name is None:
        raise ValueError("Could not determine through-model foreign keys for experiment tags.")

    assigned_tags = [
        through_model(**{f"{experiment_fk_name}_id": experiment.id, f"{tag_fk_name}_id": tag.id})
        for experiment in created_experiment_rows
        for tag in tags
    ]
    through_model.objects.bulk_create(assigned_tags)
    experiment_parameter_model.objects.bulk_create(experiment_parameter_rows_to_create)


def create_experiments_from_parameters(
    *,
    experiment_model,
    experiment_parameter_model,
    experiment_parameters,
    experiment_model_kwargs,
    tags,
    insert_batch_size=100,
):
    experiment_rows_to_create = []
    experiment_parameter_rows_to_create = []
    for eps in experiment_parameters:
        existed, (experiment_row, experiment_parameter_rows) = get_or_create_experiment(
            experiment_model=experiment_model,
            experiment_parameter_model=experiment_parameter_model,
            experiment_parameters=eps,
            experiment_model_kwargs=experiment_model_kwargs,
        )
        if not existed:
            experiment_rows_to_create.append(experiment_row)
            experiment_parameter_rows_to_create.extend(experiment_parameter_rows)

        if len(experiment_rows_to_create) >= insert_batch_size:
            create_experiments_and_parameters(
                experiment_model=experiment_model,
                experiment_parameter_model=experiment_parameter_model,
                experiment_rows_to_create=experiment_rows_to_create,
                experiment_parameter_rows_to_create=experiment_parameter_rows_to_create,
                tags=tags,
            )
            experiment_rows_to_create = []
            experiment_parameter_rows_to_create = []

    if experiment_rows_to_create:
        create_experiments_and_parameters(
            experiment_model=experiment_model,
            experiment_parameter_model=experiment_parameter_model,
            experiment_rows_to_create=experiment_rows_to_create,
            experiment_parameter_rows_to_create=experiment_parameter_rows_to_create,
            tags=tags,
        )
