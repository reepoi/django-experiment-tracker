import pytest
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.db import connection, models

from django_experiment_tracker.admin_search import ParameterValueSearchAdminMixin
from django_experiment_tracker.models import (
    Parameter,
    ParameterGroup,
    ParameterType,
    ParameterValue,
    Tag,
)


class SearchableRecord(models.Model):
    tags = models.ManyToManyField(Tag, blank=True)

    class Meta:
        app_label = 'django_experiment_tracker'


class RecordConfiguration(ParameterValue):
    record = models.ForeignKey(
        SearchableRecord,
        on_delete=models.CASCADE,
        related_name='configurations',
        related_query_name='configured_records',
    )

    class Meta:
        app_label = 'django_experiment_tracker'


class SearchableRecordAdmin(ParameterValueSearchAdminMixin, admin.ModelAdmin):
    pass


@pytest.fixture
def searchable_record_models(transactional_db):
    with connection.schema_editor() as editor:
        editor.create_model(SearchableRecord)
        editor.create_model(RecordConfiguration)

    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(RecordConfiguration)
            editor.delete_model(SearchableRecord)


@pytest.fixture
def searchable_records(db, searchable_record_models):
    baseline = Tag.objects.create(tag_value='baseline')
    candidate = Tag.objects.create(tag_value='candidate')
    optimizer = ParameterGroup.objects.create(parameter_group_name='optimizer')
    scheduler = ParameterGroup.objects.create(parameter_group_name='scheduler')
    learning_rate = Parameter.objects.create(
        parameter_name='learning_rate',
        parameter_type=ParameterType.FLOAT,
    )
    matching = SearchableRecord.objects.create()
    other_tag = SearchableRecord.objects.create()
    other_group = SearchableRecord.objects.create()
    other_value = SearchableRecord.objects.create()
    matching.tags.add(baseline)
    other_group.tags.add(baseline)
    other_value.tags.add(baseline)
    other_tag.tags.add(candidate)
    for record, group, value in (
        (matching, optimizer, '0.001'),
        (other_tag, optimizer, '0.001'),
        (other_group, scheduler, '0.001'),
        (other_value, optimizer, '0.01'),
    ):
        RecordConfiguration.objects.create(
            record=record,
            parameter_group=group,
            parameter=learning_rate,
            parameter_value=value,
        )
    return {
        'admin': SearchableRecordAdmin(SearchableRecord, AdminSite()),
        'matching': matching,
        'other_group': other_group,
        'other_tag': other_tag,
        'other_value': other_value,
    }


def result_ids(searchable_records, query):
    queryset, may_have_duplicates = searchable_records['admin'].get_search_results(
        None,
        SearchableRecord.objects.all(),
        query,
    )

    assert may_have_duplicates
    return set(queryset.values_list('pk', flat=True))


def test_parameter_value_search_discovers_custom_reverse_relation(searchable_records):
    assert result_ids(searchable_records, 'p:learning_rate=1e-3') == {
        searchable_records['matching'].pk,
        searchable_records['other_tag'].pk,
        searchable_records['other_group'].pk,
    }


def test_parameter_value_search_filters_by_tag_and_group(searchable_records):
    assert result_ids(searchable_records, 't:baseline g:optimizer') == {
        searchable_records['matching'].pk,
        searchable_records['other_value'].pk,
    }


def test_parameter_value_search_combines_all_term_types(searchable_records):
    assert result_ids(
        searchable_records,
        't:baseline g:optimizer p:learning_rate=0.001',
    ) == {searchable_records['matching'].pk}


def test_parameter_value_search_rejects_invalid_terms(searchable_records):
    queryset, may_have_duplicates = searchable_records['admin'].get_search_results(
        None,
        SearchableRecord.objects.all(),
        'p:learning_rate',
    )

    assert not may_have_duplicates
    assert not queryset.exists()
