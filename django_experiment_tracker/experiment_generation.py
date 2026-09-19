import hashlib
import itertools

import polars as pl
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.query import QuerySet
from django_experiment_tracker.models import Tag

from django_experiment_tracker.models import ParameterGroup


def build_experiment_df(connection, parameter_group_names, derived_enums=None, derived_experiment_funcs=()):
    """Build complete experiment parameter sets from tracker tables with SQL and Polars."""
    if derived_enums is None:
        derived_enums = {}
    placeholders = ", ".join(["?"] * len(parameter_group_names))
    base_pgp = connection.execute(
        f"""
        select
            parameter_group_name,
            parameter_name,
            parameter_default_value,
            parameter_group_parameter_id,
            parameter_enum_id
        from django_experiment_tracker_parametergroup
        join django_experiment_tracker_parametergroupparameter using (parameter_group_id)
        join django_experiment_tracker_parameter using (parameter_id)
        where parameter_group_name in ({placeholders})
        """,
        parameter_group_names,
    ).pl()
    enum_values = connection.execute(
        """
        select parameter_enum_id, parameter_enum_value
        from django_experiment_tracker_parameterenum
        join django_experiment_tracker_parameterenumvalue using (parameter_enum_id)
        """,
    ).pl()
    base_enums = base_pgp.join(enum_values, on="parameter_enum_id", how="inner")

    groups = {
        key: [row["parameter_enum_value"] for row in group]
        for key, group in itertools.groupby(
            base_enums.sort("parameter_group_name", "parameter_name").iter_rows(named=True),
            key=lambda row: (row["parameter_group_name"], row["parameter_name"]),
        )
    }
    groups.update(derived_enums)
    experiment_parameter_rows = []
    choices = [
        [
            {
                "parameter_group_name": parameter_group_name,
                "parameter_name": parameter_name,
                "parameter_value": value,
            }
            for value in values
        ]
        for (parameter_group_name, parameter_name), values in groups.items()
    ]
    for experiment in itertools.product(*choices):
        experiment_parameters = {
            (row["parameter_group_name"], row["parameter_name"]): row.copy()
            for row in experiment
        }
        experiment_parameter_sets = [experiment_parameters]
        for derived_experiment_func in derived_experiment_funcs:
            experiment_parameter_sets = derived_experiment_func(experiment_parameter_sets)
        for experiment_parameters in experiment_parameter_sets:
            experiment_key = ""
            for row in sorted(
                experiment_parameters.values(),
                key=lambda row: (row["parameter_group_name"], row["parameter_name"]),
            ):
                experiment_key += "," + ",".join(
                    (row["parameter_group_name"], row["parameter_name"], row["parameter_value"])
                )
            experiment_key = hashlib.sha256(experiment_key.encode("utf-8")).hexdigest()
            experiment_parameter_rows.extend(
                {**row, "experiment_key": experiment_key}
                for row in experiment_parameters.values()
            )

    selected_parameters = pl.DataFrame(experiment_parameter_rows).unique()
    experiment_dfs = []
    for experiment_id, (_, selected_rows) in enumerate(itertools.groupby(
        selected_parameters.sort("experiment_key").iter_rows(named=True),
        key=lambda row: row["experiment_key"],
    )):
        selected_parameters_df = pl.DataFrame(list(selected_rows)).drop("experiment_key")
        default_parameters = (
            base_pgp.lazy()
            .join(
                selected_parameters_df.lazy(),
                on=["parameter_group_name", "parameter_name"],
                how="anti",
            )
            .select(
                "parameter_group_name",
                "parameter_name",
                parameter_value="parameter_default_value",
            )
        )
        experiment_dfs.append(
            pl.concat([default_parameters, selected_parameters_df.lazy()]).with_columns(
                experiment_id=experiment_id,
            )
        )

    return pl.concat(experiment_dfs).collect()


def get_or_create_experiments(
    connection,
    experiment_df,
    *,
    experiment_model,
    parameter_model,
    experiment_parameter_table,
    model_kwargs,
    tags=(),
    database_alias="default",
):
    """Fetch exact parameter-set matches or create missing concrete experiments."""
    match_counts = connection.sql(
        f"""
        with expected_counts as (
            select experiment_id as requested_experiment_id, count(*) as expected_count
            from experiment_df
            group by experiment_id
        ),
        total_counts as (
            select experiment_id, count(*) as total_count
            from {experiment_parameter_table}
            group by experiment_id
        ),
        matched_counts as (
            select
                experiment_df.experiment_id as requested_experiment_id,
                {experiment_parameter_table}.experiment_id as existing_experiment_id,
                count(*) as matched_count
            from {experiment_parameter_table}
            join django_experiment_tracker_parametergroupparameter using (parameter_group_parameter_id)
            join django_experiment_tracker_parameter using (parameter_id)
            join django_experiment_tracker_parametergroup using (parameter_group_id)
            join experiment_df using (parameter_group_name, parameter_name, parameter_value)
            group by requested_experiment_id, existing_experiment_id
        )
        select requested_experiment_id, existing_experiment_id
        from matched_counts
        join expected_counts using (requested_experiment_id)
        join total_counts on total_counts.experiment_id = matched_counts.existing_experiment_id
        where matched_count = expected_count
          and total_count = expected_count
        """
    ).pl()
    duplicate_matches = match_counts.group_by("requested_experiment_id").len().filter(pl.col("len") > 1)
    if duplicate_matches.height:
        raise ValueError(f"Multiple experiments match parameter sets: {duplicate_matches}")
    experiment_ids_by_id = dict(
        match_counts.select("requested_experiment_id", "existing_experiment_id").iter_rows()
    )
    experiment_manager = experiment_model.objects.using(database_alias)
    parameter_manager = parameter_model.objects.using(database_alias)
    existing_experiments = experiment_manager.in_bulk(experiment_ids_by_id.values())
    parameter_group_parameter_ids_relation = connection.sql(
        """
        select
            parameter_group_name,
            parameter_name,
            parameter_group_parameter_id
        from django_experiment_tracker_parametergroup
        join django_experiment_tracker_parametergroupparameter using (parameter_group_id)
        join django_experiment_tracker_parameter using (parameter_id)
        join experiment_df using (parameter_group_name, parameter_name)
        group by all
        """
    )
    parameter_group_parameter_ids = {
        (parameter_group_name, parameter_name): parameter_group_parameter_id
        for parameter_group_name, parameter_name, parameter_group_parameter_id
        in parameter_group_parameter_ids_relation.fetchall()
    }
    tags = list(tags)
    fk_name = _fk_field_name(parameter_model, experiment_model)
    experiment_pks = []

    with transaction.atomic(using=database_alias):
        for experiment_id, rows in itertools.groupby(
            experiment_df.sort("experiment_id").iter_rows(named=True),
            key=lambda row: row["experiment_id"],
        ):
            if experiment_id in experiment_ids_by_id:
                experiment = existing_experiments[experiment_ids_by_id[experiment_id]]
                experiment.tags.add(*tags)
            else:
                experiment = experiment_manager.create(**model_kwargs)
                parameter_manager.bulk_create(
                    [
                        parameter_model(
                            **{
                                f"{fk_name}_id": experiment.id,
                                "parameter_group_parameter_id": parameter_group_parameter_ids[
                                    (row["parameter_group_name"], row["parameter_name"])
                                ],
                                "parameter_value": row["parameter_value"],
                            }
                        )
                        for row in rows
                    ]
                )
                experiment.tags.add(*tags)
            experiment_pks.append(experiment.pk)

    return experiment_manager.filter(pk__in=experiment_pks)


def _prefetch_parameters(parameter_groups):
    return parameter_groups.prefetch_related(
        "parameter_memberships__parameter",
        "parameter_memberships__parameter_enum__parameterenumvalue_set",
    )


def build_parameter_group_sweep(group, substitutes=None):
    choices = {}
    substitutes = substitutes or {}

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
        choices[key] = dict(values=values, parameter_group_parameter=membership)

    return choices


def build_parameter_sweep(parameter_groups, substitutes=None):
    substitutes = substitutes or {}
    choices = {}

    if isinstance(parameter_groups, QuerySet):
        prefetched_groups = _prefetch_parameters(parameter_groups)
    else:
        prefetched_groups = _prefetch_parameters(ParameterGroup.objects.filter(
            parameter_group_name__in=[pg.parameter_group_name for pg in parameter_groups],
        ))

    for group in prefetched_groups:
        choices.update(build_parameter_group_sweep(group, substitutes))

    return choices


def build_parameter_sets(sweep_dict):
    choices = []
    for (group, parameter), v in sweep_dict.items():
        choices.append(
            [
                ((group, parameter), dict(
                    value=value,
                    parameter_group_parameter=v['parameter_group_parameter'],
                ))
                for value in v['values']
            ]
        )

    return (dict(p) for p in itertools.product(*choices))


def _build_parameter_choices(group, substitutes):
    choices = []
    for choice in build_parameter_group_sweep(group, substitutes).values():
        choices.append(
            [
                (choice["parameter_group_parameter"], value)
                for value in choice["values"]
            ]
        )

    return choices


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
    for parameter_group_parameter, parameter_value in parameters:
        q |= Q(
            **{
                f"{relation_query_name}__parameter_group_parameter": parameter_group_parameter,
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
        for parameter_group_parameter, parameter_value in parameters:
            parameter_rows.append(
                parameter_model(
                    **{
                        fk_name: row,
                        "parameter_group_parameter": parameter_group_parameter,
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
