import random
import string

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


def generate_random_string(k=8, chars=string.ascii_lowercase+string.digits):
    return ''.join(random.SystemRandom().choices(chars, k=k))


def generate_unique(model_class, field_name, sampler=generate_random_string):
    while model_class.objects.filter(**{field_name: (s := sampler)}).exists():
        pass
    return s


def db_default_random_string(half_length, prefix=''):
    return models.functions.Concat(
        models.Value(prefix),
        models.functions.Lower(
            models.Func(
                models.Func(models.Value(half_length), function='randomblob'),
                function='hex'
            )
        )
    )


class GitCommit(models.Model):
    commit_time = models.DateTimeField()
    branch = models.CharField(max_length=50)
    commit_sha = models.CharField(max_length=40)

    def __str__(self):
        return f'{self.commit_time}: {self.branch} ({self.commit_sha})'


class Tag(models.Model):
    tag_value = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.tag_value


class ParameterEnumType(models.TextChoices):
    STRING = ('str', _('String'))
    BOOL = ('bool', _('Boolean'))
    INT = ('int', _('Integer'))
    FLOAT = ('float', _('Float'))


class ParameterEnum(models.Model):
    """
    Enum values assignable to a parameter.
    """
    parameter_enum_name = models.CharField(max_length=100, unique=True)
    parameter_enum_description = models.CharField(max_length=100, blank=True)
    parameter_enum_type = models.CharField(max_length=max(map(len, ParameterEnumType)), choices=ParameterEnumType)

    def __str__(self):
        return f'{self.parameter_enum_name} ({ParameterEnumType(self.parameter_enum_type).label})'

    def parse_value(self, value):
        match self.parameter_enum_type:
            case ParameterEnumType.BOOL:
                if value != 'True' and value != 'False':
                    raise ValueError("Boolean string value must be either 'True' or 'False'")
                return value == 'True'
            case ParameterEnumType.INT:
                return int(value)
            case ParameterEnumType.FLOAT:
                return float(value)
            case _:
                return value


class ParameterEnumValue(models.Model):
    """
    One value in the enumeration of a parameter enum.
    """
    parameter_enum = models.ForeignKey(ParameterEnum, on_delete=models.CASCADE)
    parameter_enum_value = models.CharField(max_length=100)

    def __str__(self):
        return self.parameter_enum_value

    def clean(self):
        try:
            self.parameter_enum_value = str(self.parameter_enum.parse_value(self.parameter_enum_value))
        except ValueError as e:
            raise ValidationError(str(e))


class Parameter(models.Model):
    """
    A parameter assingable to an experiment.
    """
    parameter_name = models.CharField(max_length=100, unique=True)
    parameter_description = models.CharField(max_length=100, blank=True)
    parameter_enum = models.ForeignKey(ParameterEnum, blank=True, null=True, on_delete=models.PROTECT)
    parameter_default_value = models.CharField(max_length=100)

    def __str__(self):
        return f'{self.parameter_name} ({self.parameter_default_value})'


class ParameterGroup(models.Model):
    parameter_group_name = models.CharField(max_length=100, unique=True)
    parameter_group_description = models.CharField(max_length=100, blank=True)
    parameters = models.ManyToManyField(Parameter, blank=True)

    def __str__(self):
        return self.parameter_group_name


class Experiment(models.Model):
    PREFIX_ALT_ID = 'exp_'

    alt_id = models.CharField(max_length=8+len(PREFIX_ALT_ID), unique=True, editable=False, db_default=db_default_random_string(4, PREFIX_ALT_ID))
    git_commit = models.ForeignKey(GitCommit, on_delete=models.CASCADE, related_name='%(app_label)s_%(class)s_related', related_query_name='%(app_label)s_%(class)ss')
    time_created = models.DateTimeField(blank=True, db_default=models.functions.Now())
    time_completed = models.DateTimeField(blank=True, null=True)
    exit_code = models.IntegerField(blank=True, null=True)
    tags = models.ManyToManyField(Tag)

    class Meta:
        abstract = True

# class OptunaOptimizationDirection(models.TextChoices):
#     MINIMIZE = ('min', _('Minimize'))
#     MAXIMIZE = ('max', _('Maximize'))
#
#
# class OptunaStudy(models.Model):
#     optimization_direction = models.CharField(max_length=max(map(len, OptunaOptimizationDirection)), choices=OptunaOptimizationDirection)
#     trial_count = models.PositiveIntegerField()
#
#     class Meta:
#         abstract = True
#
#
# class OptunaTrialParamType(models.TextChoices):
#     INTEGER = ('int', _('Integer'))
#     FLOAT = ('float', _('Float'))
#
#
# class OptunaTrialParam(models.Model):
#     param_name = models.CharField(max_length=50)
#     param_type = models.CharField(max_length=max(map(len, OptunaTrialParamType)), choices=OptunaTrialParamType)
#     lower_bound = models.FloatField()
#     upper_bound = models.FloatField()
#     log_sample = models.BooleanField(default=False)
#
#     class Meta:
#         abstract = True
#
#
# class SharedFile(models.Model):
#     PREFIX_ALT_ID = 'sf'
#
#     shared_file_alt_id = models.CharField(max_length=8+len(PREFIX_ALT_ID), unique=True, editable=False)
#
#     class Meta:
#         abstract = True
#
#     def save(self, *args, **kwargs):
#         if not self.shared_file_alt_id:
#             self.shared_file_alt_id = f"{self.PREFIX_ALT_ID}_{generate_unique(type(self), 'shared_file_alt_id')}"
#         return super().save(*args, **kwargs)
