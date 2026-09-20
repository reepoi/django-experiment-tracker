import random
import string

from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _


CHARFIELD_MAX_LENGTH = getattr(settings, 'DJANGO_EXPERIMENT_TRACKER_CHARFIELD_MAX_LENGTH', 500)


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
    value = models.CharField(max_length=CHARFIELD_MAX_LENGTH, unique=True, db_column='tag_value')

    def __str__(self):
        return self.value


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
    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, unique=True, db_column='parameter_enum_name')
    description = models.CharField(max_length=CHARFIELD_MAX_LENGTH, blank=True, db_column='parameter_enum_description')
    data_type = models.CharField(max_length=max(map(len, ParameterType)), choices=ParameterType, db_column='parameter_enum_data_type')

    def __str__(self):
        return f'{self.name} ({ParameterType(self.data_type).label})'

    def parse_value(self, value):
        return Parameter.parse_value(self.data_type, value)


class ParameterEnumValue(models.Model):
    """
    One value in the enumeration of a parameter enum.
    """
    id = models.AutoField(primary_key=True, db_column='parameter_enum_value_id')
    enum = models.ForeignKey(ParameterEnum, on_delete=models.CASCADE, db_column=ParameterEnum._meta.pk.column)
    value = models.CharField(max_length=CHARFIELD_MAX_LENGTH, db_column='parameter_enum_value')

    def __str__(self):
        return self.value

    def save(self, *args, **kwargs):
        self.value = Parameter.format_value(
            self.enum.data_type,
            Parameter.parse_value(self.enum.data_type, self.value),
        )
        return super().save(*args, **kwargs)

    def clean(self):
        try:
            self.value = Parameter.format_value(self.enum.data_type, self.parse())
        except ValueError as e:
            raise ValidationError(str(e))

    def parse(self):
        return self.enum.parse_value(self.value)


class Parameter(models.Model):
    """
    A parameter assingable to an experiment.
    """
    PLACEHOLDER = '???'
    id = models.AutoField(primary_key=True, db_column='parameter_id')
    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, unique=True, db_column='parameter_name')
    description = models.CharField(max_length=CHARFIELD_MAX_LENGTH, blank=True, db_column='parameter_description')
    data_type = models.CharField(max_length=max(map(len, ParameterType)), choices=ParameterType, db_column='parameter_data_type')

    def __str__(self):
        return self.name

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
    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, unique=True, db_column='parameter_group_name')
    description = models.CharField(max_length=CHARFIELD_MAX_LENGTH, blank=True, db_column='parameter_group_description')
    parameters = models.ManyToManyField(Parameter, through='ParameterGroupParameter', blank=True)

    def __str__(self):
        return self.name


class ParameterGroupParameter(models.Model):
    id = models.AutoField(primary_key=True, db_column='parameter_group_parameter_id')
    parameter_group = models.ForeignKey(ParameterGroup, on_delete=models.CASCADE, related_name='parameter_memberships', db_column=ParameterGroup._meta.pk.column)
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE, related_name='parameter_group_memberships', db_column=Parameter._meta.pk.column)
    parameter_enum = models.ForeignKey(ParameterEnum, blank=True, null=True, on_delete=models.PROTECT, db_column=ParameterEnum._meta.pk.column)
    parameter_default_value = models.CharField(max_length=CHARFIELD_MAX_LENGTH)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['parameter_group', 'parameter'], name='group_parameter_alt_key'),
        ]

    def __str__(self):
        return f'{self.parameter_group}->{self.parameter}'

    def save(self, *args, **kwargs):
        self.parameter_default_value = Parameter.format_value(
            self.parameter.data_type,
            Parameter.parse_value(self.parameter.data_type, self.parameter_default_value),
        )
        super().save(*args, **kwargs)


@receiver(post_save, sender=ParameterGroupParameter)
def add_parameters_with_default_value_on_parameter_group_parameter_creation(
    sender, instance, created, **kwargs,
):
    if not created:
        return
    with transaction.atomic():
        parameter_models = [
            c for c in ParameterValue.__subclasses__() if not c._meta.abstract
        ]
        for c in parameter_models:
            parameterized_model_ids = (
                c.objects
                .filter(definition__parameter_group=instance.parameter_group)
                .values(f'{c._meta.constraints[0].fields[0]}_id')  # assuming ParameterValue.parameter_value_constraints
                .distinct()
            )
            to_create = []
            for m in parameterized_model_ids:
                m['definition'] = instance
                m['value'] = instance.parameter_default_value
                to_create.append(c(**m))
            c.objects.bulk_create(to_create)


class ParameterValue(models.Model):
    id = models.AutoField(primary_key=True, db_column='parameter_value_id')
    definition = models.ForeignKey(ParameterGroupParameter, on_delete=models.CASCADE, db_column=ParameterGroupParameter._meta.pk.column)
    value = models.CharField(max_length=CHARFIELD_MAX_LENGTH, db_column='parameter_value')

    class Meta:
        abstract = True

    @classmethod
    def parameter_value_constraints(cls, parameterized_model_field_name):
        return [
            models.UniqueConstraint(
                fields=[parameterized_model_field_name, 'definition'],
                name=f'{parameterized_model_field_name}_parameter_definition_alt_key',
            ),
        ]

    def __str__(self):
        return f'{self.definition}, {self.value}'

    def save(self, *args, **kwargs):
        self.value = Parameter.format_value(
            self.definition.parameter.data_type,
            Parameter.parse_value(self.definition.parameter.data_type, self.value),
        )
        return super().save(*args, **kwargs)

    def parse(self):
        return Parameter.parse_value(self.definition.parameter.data_type, self.value)


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
    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, db_column='shared_file_name')
    path = models.CharField(max_length=CHARFIELD_MAX_LENGTH, db_column='shared_file_path')
    description = models.CharField(max_length=CHARFIELD_MAX_LENGTH, blank=True, db_column='shared_file_description')
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
