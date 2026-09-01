import pytest
from django.db import connection, models

from django_experiment_tracker.experiment_generation import (
    build_parameters_by_group,
    create_parameterized_model_from_parameters,
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
    Tag,
)


class DemoExperiment(BaseExperiment):
    class Meta:
        app_label = "django_experiment_tracker"


class DemoExperimentParameter(models.Model):
    experiment = models.ForeignKey(DemoExperiment, on_delete=models.CASCADE)
    parameter_group = models.ForeignKey(ParameterGroup, on_delete=models.CASCADE)
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE)
    parameter_value = models.CharField(max_length=100)

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
