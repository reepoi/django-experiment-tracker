import duckdb
import pytest
from django.db import connection, models
from django.db.models import QuerySet

from django_experiment_tracker.experiment_generation import (
    build_experiment_df,
    build_parameters_by_group,
    create_parameterized_model_from_parameters,
    get_or_create_experiments,
)
from django_experiment_tracker.models import Experiment as BaseExperiment
from django_experiment_tracker.models import (
    GitCommit,
    Parameter,
    ParameterEnum,
    ParameterEnumValue,
    ParameterGroup,
    ParameterGroupParameter,
    ParameterType,
    ParameterValue,
    Tag,
)


class DemoExperiment(BaseExperiment):
    class Meta:
        app_label = "django_experiment_tracker"


class DemoExperimentParameter(ParameterValue):
    experiment = models.ForeignKey(DemoExperiment, on_delete=models.CASCADE)

    class Meta:
        app_label = "django_experiment_tracker"
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "parameter", "parameter_group"],
                name="test_experiment_parameter_and_group_alt_key",
            ),
        ]


@pytest.fixture
def experiment_models(transactional_db):
    with connection.schema_editor() as editor:
        editor.create_model(DemoExperiment)
        editor.create_model(DemoExperimentParameter)

    try:
        yield DemoExperiment, DemoExperimentParameter
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(DemoExperimentParameter)
            editor.delete_model(DemoExperiment)


@pytest.fixture
def git_commit(db):
    return GitCommit.objects.create(
        commit_time="2026-01-01T00:00:00Z",
        branch="main",
        commit_sha="a" * 40,
    )


@pytest.fixture
def tags(db):
    return [Tag.objects.create(tag_value="Generalization")]


@pytest.fixture
def parameter_setup(db):
    group_a = ParameterGroup.objects.create(parameter_group_name="group_a")
    group_b = ParameterGroup.objects.create(parameter_group_name="group_b")
    param_x = Parameter.objects.create(
        parameter_name="x",
        parameter_type=ParameterType.INT,
    )
    param_y = Parameter.objects.create(
        parameter_name="y",
        parameter_type=ParameterType.INT,
    )
    ParameterGroupParameter.objects.create(
        parameter_group=group_a,
        parameter=param_x,
        parameter_default_value="1",
    )
    ParameterGroupParameter.objects.create(
        parameter_group=group_b,
        parameter=param_y,
        parameter_default_value="2",
    )
    return {
        "group_a": group_a,
        "group_b": group_b,
        "param_x": param_x,
        "param_y": param_y,
    }


def _params_case(parameter_setup, x_val="1", y_val="2"):
    return [
        (parameter_setup["group_a"], parameter_setup["param_x"], x_val),
        (parameter_setup["group_b"], parameter_setup["param_y"], y_val),
    ]


@pytest.mark.django_db
def test_creates_new_experiment_when_no_match(git_commit, tags, parameter_setup, experiment_models):
    experiment_model, experiment_parameter_model = experiment_models

    create_parameterized_model_from_parameters(
        model=experiment_model,
        parameter_model=experiment_parameter_model,
        parameters=[_params_case(parameter_setup)],
        model_kwargs={
            "git_commit_created": git_commit,
            "git_commit_valid_for": git_commit,
        },
        tags=tags,
        insert_batch_size=100,
    )

    assert experiment_model.objects.count() == 1
    assert experiment_parameter_model.objects.count() == 2
    experiment = experiment_model.objects.get()
    assert experiment.tags.filter(tag_value="Generalization").exists()


@pytest.mark.django_db
def test_reuses_existing_experiment_for_same_parameter_set(git_commit, tags, parameter_setup, experiment_models):
    experiment_model, experiment_parameter_model = experiment_models
    params = _params_case(parameter_setup)

    create_parameterized_model_from_parameters(
        model=experiment_model,
        parameter_model=experiment_parameter_model,
        parameters=[params],
        model_kwargs={
            "git_commit_created": git_commit,
            "git_commit_valid_for": git_commit,
        },
        tags=tags,
        insert_batch_size=100,
    )
    create_parameterized_model_from_parameters(
        model=experiment_model,
        parameter_model=experiment_parameter_model,
        parameters=[params],
        model_kwargs={
            "git_commit_created": git_commit,
            "git_commit_valid_for": git_commit,
        },
        tags=tags,
        insert_batch_size=100,
    )

    assert experiment_model.objects.count() == 1
    assert experiment_parameter_model.objects.count() == 2


@pytest.mark.django_db
def test_batch_insert_creates_multiple_experiments(git_commit, tags, parameter_setup, experiment_models):
    experiment_model, experiment_parameter_model = experiment_models
    cases = [
        _params_case(parameter_setup, x_val="1", y_val="2"),
        _params_case(parameter_setup, x_val="3", y_val="2"),
        _params_case(parameter_setup, x_val="1", y_val="4"),
    ]

    create_parameterized_model_from_parameters(
        model=experiment_model,
        parameter_model=experiment_parameter_model,
        parameters=cases,
        model_kwargs={
            "git_commit_created": git_commit,
            "git_commit_valid_for": git_commit,
        },
        tags=tags,
        insert_batch_size=2,
    )

    assert experiment_model.objects.count() == 3
    assert experiment_parameter_model.objects.count() == 6


@pytest.mark.django_db
def test_parameter_configuration_is_specific_to_group():
    group_a = ParameterGroup.objects.create(parameter_group_name="group_a")
    group_b = ParameterGroup.objects.create(parameter_group_name="group_b")
    parameter = Parameter.objects.create(
        parameter_name="shared",
        parameter_type=ParameterType.INT,
    )
    parameter_enum = ParameterEnum.objects.create(
        parameter_enum_name="choices",
        parameter_enum_type=ParameterType.INT,
    )
    ParameterEnumValue.objects.create(
        parameter_enum=parameter_enum,
        parameter_enum_value="2",
    )
    ParameterEnumValue.objects.create(
        parameter_enum=parameter_enum,
        parameter_enum_value="3",
    )
    ParameterGroupParameter.objects.create(
        parameter_group=group_a,
        parameter=parameter,
        parameter_default_value="1",
    )
    ParameterGroupParameter.objects.create(
        parameter_group=group_b,
        parameter=parameter,
        parameter_enum=parameter_enum,
        parameter_default_value="9",
    )

    cases = list(build_parameters_by_group(ParameterGroup.objects.order_by("id")))

    assert [case[0][2] for case in cases] == ["1", "2", "3"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("parameter_type", "value", "expected"),
    [
        (ParameterType.BOOL, "True", "True"),
        (ParameterType.INT, "0x10", "16"),
        (ParameterType.FLOAT, "1e2", "1e2"),
        (ParameterType.HEX, "0X00FF", "0xff"),
    ],
)
def test_parameter_values_are_normalized_before_saving(
    parameter_type, value, expected, git_commit, experiment_models
):
    experiment_model, experiment_parameter_model = experiment_models
    group = ParameterGroup.objects.create(parameter_group_name=f"group_{parameter_type}")
    parameter = Parameter.objects.create(
        parameter_name=f"parameter_{parameter_type}",
        parameter_type=parameter_type,
    )
    parameter_enum = ParameterEnum.objects.create(
        parameter_enum_name=f"enum_{parameter_type}",
        parameter_enum_type=parameter_type,
    )

    enum_value = ParameterEnumValue.objects.create(
        parameter_enum=parameter_enum,
        parameter_enum_value=value,
    )
    group_parameter = ParameterGroupParameter.objects.create(
        parameter_group=group,
        parameter=parameter,
        parameter_default_value=value,
    )
    experiment = experiment_model.objects.create(
        git_commit_created=git_commit,
        git_commit_valid_for=git_commit,
    )
    parameter_value = experiment_parameter_model.objects.create(
        experiment=experiment,
        parameter_group=group,
        parameter=parameter,
        parameter_value=value,
    )

    enum_value.refresh_from_db()
    group_parameter.refresh_from_db()
    parameter_value.refresh_from_db()

    assert enum_value.parameter_enum_value == expected
    assert group_parameter.parameter_default_value == expected
    assert parameter_value.parameter_value == expected


def test_parameter_formats_parsed_hex_values():
    assert Parameter.parse_value(ParameterType.HEX, "ff") == 255
    assert Parameter.format_value(ParameterType.HEX, 255) == "0xff"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (100.0, "1e2"),
        (0.001, "1e-3"),
        (-0.0, "-0e0"),
        (1.2345678901234567, "1.2345678901234567e0"),
    ],
)
def test_parameter_formats_floats_as_compact_scientific_notation(value, expected):
    formatted_value = Parameter.format_value(ParameterType.FLOAT, value)

    assert formatted_value == expected
    assert float(formatted_value) == value


def _clean_frame_parameter_sets(parameter_sets):
    derived_parameter_sets = []
    for parameters in parameter_sets:
        for clean_frame_factor in (0, 1 / 2):
            derived_parameters = parameters.copy()
            frame_count = int(
                derived_parameters[("dataset_window", "frame_count")]["parameter_value"]
            )
            derived_parameters[("rolling_diffusion_frames", "clean_frame_count")] = {
                "parameter_group_name": "rolling_diffusion_frames",
                "parameter_name": "clean_frame_count",
                "parameter_value": str(int((frame_count - 1) * clean_frame_factor)),
            }
            derived_parameter_sets.append(derived_parameters)
    return derived_parameter_sets


@pytest.fixture
def duckdb_connection():
    duckdb_connection = duckdb.connect()
    duckdb_connection.execute(
        """
        create table django_experiment_tracker_parametergroup (
            parameter_group_id bigint,
            parameter_group_name varchar
        );
        create table django_experiment_tracker_parameter (
            parameter_id bigint,
            parameter_name varchar
        );
        create table django_experiment_tracker_parametergroupparameter (
            parameter_group_id bigint,
            parameter_id bigint,
            parameter_enum_id bigint,
            parameter_default_value varchar
        );
        create table django_experiment_tracker_parameterenum (
            parameter_enum_id bigint
        );
        create table django_experiment_tracker_parameterenumvalue (
            parameter_enum_value_id bigint,
            parameter_enum_id bigint,
            parameter_enum_value varchar
        );
        """
    )
    try:
        yield duckdb_connection
    finally:
        duckdb_connection.close()


def _sync_duckdb_table(duckdb_connection, table, columns):
    with connection.cursor() as cursor:
        cursor.execute(f"select {', '.join(columns)} from {table}")
        rows = cursor.fetchall()
    duckdb_connection.execute(f"delete from {table}")
    if rows:
        placeholders = ", ".join("?" for _ in columns)
        duckdb_connection.executemany(
            f"insert into {table} values ({placeholders})",
            rows,
        )


def _sync_tracker_tables(duckdb_connection):
    _sync_duckdb_table(
        duckdb_connection,
        "django_experiment_tracker_parametergroup",
        ["parameter_group_id", "parameter_group_name"],
    )
    _sync_duckdb_table(
        duckdb_connection,
        "django_experiment_tracker_parameter",
        ["parameter_id", "parameter_name"],
    )
    _sync_duckdb_table(
        duckdb_connection,
        "django_experiment_tracker_parametergroupparameter",
        [
            "parameter_group_id",
            "parameter_id",
            "parameter_enum_id",
            "parameter_default_value",
        ],
    )
    _sync_duckdb_table(
        duckdb_connection,
        "django_experiment_tracker_parameterenum",
        ["parameter_enum_id"],
    )
    _sync_duckdb_table(
        duckdb_connection,
        "django_experiment_tracker_parameterenumvalue",
        ["parameter_enum_value_id", "parameter_enum_id", "parameter_enum_value"],
    )


@pytest.fixture
def sweep_parameter_setup(db):
    dataset_window = ParameterGroup.objects.create(
        parameter_group_name="dataset_window"
    )
    rolling_diffusion_frames = ParameterGroup.objects.create(
        parameter_group_name="rolling_diffusion_frames"
    )
    frame_count = Parameter.objects.create(
        parameter_name="frame_count",
        parameter_type=ParameterType.INT,
    )
    clean_frame_count = Parameter.objects.create(
        parameter_name="clean_frame_count",
        parameter_type=ParameterType.INT,
    )
    frame_count_enum = ParameterEnum.objects.create(
        parameter_enum_name="frame_count_choices",
        parameter_enum_type=ParameterType.INT,
    )
    ParameterEnumValue.objects.bulk_create(
        [
            ParameterEnumValue(
                parameter_enum=frame_count_enum,
                parameter_enum_value=value,
            )
            for value in ("5", "9")
        ]
    )
    ParameterGroupParameter.objects.create(
        parameter_group=dataset_window,
        parameter=frame_count,
        parameter_enum=frame_count_enum,
        parameter_default_value="5",
    )
    ParameterGroupParameter.objects.create(
        parameter_group=rolling_diffusion_frames,
        parameter=clean_frame_count,
        parameter_default_value="0",
    )
    return dataset_window, rolling_diffusion_frames


@pytest.mark.django_db
def test_build_experiment_df_uses_polars_for_enum_and_default_joins(
    sweep_parameter_setup, duckdb_connection
):
    _sync_tracker_tables(duckdb_connection)
    experiment_df = build_experiment_df(
        duckdb_connection,
        ["dataset_window", "rolling_diffusion_frames"],
        [_clean_frame_parameter_sets],
    )

    assert experiment_df.height == 8
    assert experiment_df.group_by("experiment_id").len().get_column("len").to_list() == [2] * 4
    assert set(experiment_df.columns) == {
        "experiment_id",
        "parameter_group_name",
        "parameter_name",
        "parameter_value",
    }
    assert sorted(
        experiment_df.filter(
            experiment_df["parameter_name"] == "clean_frame_count"
        )["parameter_value"].to_list()
    ) == ["0", "0", "2", "4"]


@pytest.mark.django_db
def test_get_or_create_experiments_uses_exact_polars_matches(
    git_commit, tags, sweep_parameter_setup, experiment_models, duckdb_connection
):
    experiment_model, experiment_parameter_model = experiment_models
    _sync_tracker_tables(duckdb_connection)
    duckdb_connection.execute(
        f"""
        create table {experiment_parameter_model._meta.db_table} (
            experiment_id bigint,
            parameter_group_id bigint,
            parameter_id bigint,
            parameter_value varchar
        )
        """
    )
    experiment_df = build_experiment_df(
        duckdb_connection,
        ["dataset_window", "rolling_diffusion_frames"],
        [_clean_frame_parameter_sets],
    )
    kwargs = {
        "git_commit_created": git_commit,
        "git_commit_valid_for": git_commit,
    }

    created = get_or_create_experiments(
        duckdb_connection,
        experiment_df,
        experiment_model=experiment_model,
        parameter_model=experiment_parameter_model,
        experiment_parameter_table=experiment_parameter_model._meta.db_table,
        model_kwargs=kwargs,
        tags=tags,
    )

    assert isinstance(created, QuerySet)
    assert len(created) == 4
    assert experiment_model.objects.count() == 4
    assert experiment_parameter_model.objects.count() == 8
    created_ids = set(created.values_list("pk", flat=True))
    experiment_with_extra_parameter = created.first()

    extra_group = ParameterGroup.objects.create(parameter_group_name="extra")
    extra_parameter = Parameter.objects.create(
        parameter_name="extra_parameter",
        parameter_type=ParameterType.INT,
    )
    ParameterGroupParameter.objects.create(
        parameter_group=extra_group,
        parameter=extra_parameter,
        parameter_default_value="1",
    )
    experiment_parameter_model.objects.create(
        experiment=experiment_with_extra_parameter,
        parameter_group=extra_group,
        parameter=extra_parameter,
        parameter_value="1",
    )
    _sync_duckdb_table(
        duckdb_connection,
        experiment_parameter_model._meta.db_table,
        ["experiment_id", "parameter_group_id", "parameter_id", "parameter_value"],
    )

    fetched_or_created = get_or_create_experiments(
        duckdb_connection,
        experiment_df,
        experiment_model=experiment_model,
        parameter_model=experiment_parameter_model,
        experiment_parameter_table=experiment_parameter_model._meta.db_table,
        model_kwargs=kwargs,
        tags=tags,
    )

    assert len(fetched_or_created) == 4
    assert experiment_model.objects.count() == 5
    assert experiment_parameter_model.objects.count() == 11
    fetched_or_created_ids = set(fetched_or_created.values_list("pk", flat=True))
    assert created_ids - {experiment_with_extra_parameter.pk} <= fetched_or_created_ids
    assert experiment_with_extra_parameter.pk not in fetched_or_created_ids
    assert all(experiment.tags.filter(tag_value="Generalization").exists() for experiment in fetched_or_created)
