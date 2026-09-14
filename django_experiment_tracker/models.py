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
    id = models.AutoField(primary_key=True, db_column='git_commit_id')
    commit_time = models.DateTimeField()
    branch = models.CharField(max_length=50)
    commit_sha = models.CharField(max_length=40)

    def __str__(self):
        return f'{self.commit_time}: {self.branch} ({self.commit_sha})'


class Tag(models.Model):
    id = models.AutoField(primary_key=True, db_column='tag_id')
    tag_value = models.CharField(max_length=500, unique=True)

    def __str__(self):
        return self.tag_value


class ParameterType(models.TextChoices):
    STRING = ('str', _('String'))
    BOOL = ('bool', _('Boolean'))
    INT = ('int', _('Integer'))
    HEX = ('hex', _('Hexadecimal'))
    FLOAT = ('float', _('Float'))


class ParameterEnum(models.Model):
    """
    Enum values assignable to a parameter.
    """
    id = models.AutoField(primary_key=True, db_column='parameter_enum_id')
    parameter_enum_name = models.CharField(max_length=500, unique=True)
    parameter_enum_description = models.CharField(max_length=500, blank=True)
    parameter_enum_type = models.CharField(max_length=max(map(len, ParameterType)), choices=ParameterType)

    def __str__(self):
        return f'{self.parameter_enum_name} ({ParameterType(self.parameter_enum_type).label})'

    def parse_value(self, value):
        return Parameter.parse_value(self.parameter_enum_type, value)


class ParameterEnumValue(models.Model):
    """
    One value in the enumeration of a parameter enum.
    """
    id = models.AutoField(primary_key=True, db_column='parameter_enum_value_id')
    parameter_enum = models.ForeignKey(ParameterEnum, on_delete=models.CASCADE)
    parameter_enum_value = models.CharField(max_length=500)

    def __str__(self):
        return self.parameter_enum_value

    def save(self, *args, **kwargs):
        self.parameter_enum_value = Parameter.format_value(
            self.parameter_enum.parameter_enum_type,
            Parameter.parse_value(self.parameter_enum.parameter_enum_type, self.parameter_enum_value),
        )
        return super().save(*args, **kwargs)

    def clean(self):
        try:
            self.parameter_enum_value = Parameter.format_value(self.parameter_enum.parameter_enum_type, self.parse())
        except ValueError as e:
            raise ValidationError(str(e))

    def parse(self):
        return self.parameter_enum.parse_value(self.parameter_enum_value)


class Parameter(models.Model):
    """
    A parameter assingable to an experiment.
    """
    PLACEHOLDER = '???'
    id = models.AutoField(primary_key=True, db_column='parameter_id')
    parameter_name = models.CharField(max_length=500, unique=True)
    parameter_description = models.CharField(max_length=500, blank=True)
    parameter_type = models.CharField(max_length=max(map(len, ParameterType)), choices=ParameterType)

    def __str__(self):
        return self.parameter_name

    @staticmethod
    def parse_value(typ, value):
        if value == Parameter.PLACEHOLDER:
            return value
        match typ:
            case ParameterType.BOOL:
                if value != 'True' and value != 'False':
                    raise ValueError("Boolean string value must be either 'True' or 'False'")
                return value == 'True'
            case ParameterType.INT:
                base = 10
                if value.startswith('0x'):
                    base = 16
                return int(value, base)
            case ParameterType.HEX:
                return int(value, 16)
            case ParameterType.FLOAT:
                return float(value)
            case _:
                return value

    @staticmethod
    def format_value(typ, value):
        if value == Parameter.PLACEHOLDER:
            return value
        if typ == ParameterType.HEX:
            return hex(value)
        if typ == ParameterType.FLOAT:
            return Parameter._format_float_value(value)
        return str(value)

    @staticmethod
    def _format_float_value(value):
        value_string = repr(value)
        if value_string in {'inf', '-inf', 'nan'}:
            return value_string

        sign = ''
        if value_string.startswith('-'):
            sign, value_string = '-', value_string[1:]
        lowercase_value_string = value_string.lower()
        if 'e' in lowercase_value_string:
            coefficient, exponent = lowercase_value_string.split('e')
        else:
            coefficient, exponent = value_string, '0'
        decimal_position = coefficient.find('.')
        if decimal_position == -1:
            decimal_position = len(coefficient)
        digits = coefficient.replace('.', '')
        significant_digits = digits.lstrip('0')
        if not significant_digits:
            return f'{sign}0e0'
        exponent = int(exponent) + decimal_position - (len(digits) - len(significant_digits)) - 1
        significant_digits = significant_digits.rstrip('0')
        mantissa = significant_digits[0]
        if len(significant_digits) > 1:
            mantissa += f'.{significant_digits[1:]}'
        return f'{sign}{mantissa}e{exponent}'


class ParameterGroup(models.Model):
    id = models.AutoField(primary_key=True, db_column='parameter_group_id')
    parameter_group_name = models.CharField(max_length=500, unique=True)
    parameter_group_description = models.CharField(max_length=500, blank=True)
    parameters = models.ManyToManyField(Parameter, through='ParameterGroupParameter', blank=True)

    def __str__(self):
        return self.parameter_group_name


class ParameterGroupParameter(models.Model):
    id = models.AutoField(primary_key=True, db_column='parameter_group_parameter_id')
    parameter_group = models.ForeignKey(ParameterGroup, on_delete=models.CASCADE, related_name='parameter_memberships')
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE, related_name='parameter_group_memberships')
    parameter_enum = models.ForeignKey(ParameterEnum, blank=True, null=True, on_delete=models.PROTECT)
    parameter_default_value = models.CharField(max_length=500)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['parameter_group', 'parameter'], name='group_parameter_alt_key'),
        ]

    def __str__(self):
        return f'{self.parameter_group}->{self.parameter} ({self.parameter_default_value})'

    def save(self, *args, **kwargs):
        self.parameter_default_value = Parameter.format_value(
            self.parameter.parameter_type,
            Parameter.parse_value(self.parameter.parameter_type, self.parameter_default_value),
        )
        return super().save(*args, **kwargs)


class ParameterValue(models.Model):
    id = models.AutoField(primary_key=True, db_column='parameter_value_id')
    parameter_group = models.ForeignKey(ParameterGroup, on_delete=models.CASCADE)
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE)
    parameter_value = models.CharField(max_length=500)

    class Meta:
        abstract = True

    @classmethod
    def parameter_value_constraints(cls, parameterized_model_field_name):
        return [
            models.UniqueConstraint(
                fields=[parameterized_model_field_name, 'parameter_group', 'parameter'],
                name=f'{parameterized_model_field_name}_group_and_parameter_alt_key',
            ),
        ]

    def __str__(self):
        return f'{self.parameter_group}, {self.parameter}, {self.parameter_value}'

    def save(self, *args, **kwargs):
        self.parameter_value = Parameter.format_value(
            self.parameter.parameter_type,
            Parameter.parse_value(self.parameter.parameter_type, self.parameter_value),
        )
        return super().save(*args, **kwargs)

    def parse(self):
        return Parameter.parse_value(self.parameter.parameter_type, self.parameter_value)


class Experiment(models.Model):
    PREFIX_ALT_ID = 'exp_'
    id = models.AutoField(primary_key=True, db_column='experiment_id')
    alt_id = models.CharField(max_length=8+len(PREFIX_ALT_ID), unique=True, editable=False, db_default=db_default_random_string(4, PREFIX_ALT_ID))
    git_commit_created = models.ForeignKey(GitCommit, on_delete=models.CASCADE, related_name='%(app_label)s_%(class)s_created_related', related_query_name='%(app_label)s_%(class)ss_created')
    git_commit_valid_for = models.ForeignKey(GitCommit, on_delete=models.CASCADE, related_name='%(app_label)s_%(class)s_valid_for_related', related_query_name='%(app_label)s_%(class)ss_valid_for')
    time_created = models.DateTimeField(blank=True, db_default=models.functions.Now())
    time_completed = models.DateTimeField(blank=True, null=True)
    exit_code = models.IntegerField(blank=True, null=True)
    tags = models.ManyToManyField(Tag, blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.alt_id


class SharedFile(models.Model):
    PREFIX_ALT_ID = 'ds_'
    id = models.AutoField(primary_key=True, db_column='shared_file_id')
    alt_id = models.CharField(max_length=8+len(PREFIX_ALT_ID), unique=True, editable=False, db_default=db_default_random_string(4, PREFIX_ALT_ID))
    git_commit_created = models.ForeignKey(GitCommit, on_delete=models.CASCADE, related_name='%(app_label)s_%(class)s_created_related', related_query_name='%(app_label)s_%(class)ss_created')
    git_commit_valid_for = models.ForeignKey(GitCommit, on_delete=models.CASCADE, related_name='%(app_label)s_%(class)s_valid_for_related', related_query_name='%(app_label)s_%(class)ss_valid_for')
    time_created = models.DateTimeField(blank=True, auto_now_add=True, db_default=models.functions.Now())
    shared_file_name = models.CharField(max_length=500)
    shared_file_description = models.CharField(max_length=500, blank=True)
    tags = models.ManyToManyField(Tag, blank=True)

    class Meta:
        abstract = True
        # constraints = [
        #     models.UniqueConstraint(fields=['git_commit', 'shared_file_name'], name='git_commit_and_shared_file_name_alt_key')
        # ]

    def __str__(self):
        return self.alt_id


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
