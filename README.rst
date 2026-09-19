Django experiment tracker
=========================
Parameter tracking for machine learning experiments.

What this package provides
--------------------------
* Core tracker models:

  * ``GitCommit``
  * ``Tag``
  * ``ParameterEnum`` / ``ParameterEnumValue``
  * ``Parameter`` / ``ParameterGroup``
  * abstract ``Experiment`` base model

* Generic experiment-generation helpers in ``django_experiment_tracker.experiment_generation``.
* Management command ``record_git_commit`` for persisting git commit metadata into ``GitCommit``.

Requirements
------------
* Python ``>=3.10``
* Django ``>=5.2.14``

Installation
------------
#. Install the package.
#. Add ``"django_experiment_tracker"`` to ``INSTALLED_APPS``.

   .. code:: python

      INSTALLED_APPS = [
          # ...
          "django_experiment_tracker",
      ]

#. Run migrations.

   .. code:: bash

       python manage.py migrate

Configuration
-------------
Set ``DJANGO_EXPERIMENT_TRACKER_CHARFIELD_MAX_LENGTH`` to change the
default maximum length for tracker names, descriptions, paths, and parameter
values. It defaults to ``500``.

.. code:: python

   DJANGO_EXPERIMENT_TRACKER_CHARFIELD_MAX_LENGTH = 1000

Core model contract
-------------------

``django_experiment_tracker.models.Experiment`` is abstract. Consumer apps should define concrete models, e.g.:

* a concrete ``Experiment`` subclass
* an ``ExperimentParameter`` model with:

  * FK to the concrete ``Experiment``
  * FK to tracker ``ParameterGroupParameter`` as ``definition``
  * ``value`` field

For deterministic deduplication behavior with the generation helpers, enforce:

* unique constraint on ``(experiment, definition)``

``Experiment.alt_id`` notes
---------------------------
The abstract base uses a database-side default for ``alt_id`` based on SQLite functions (``randomblob``/``hex``) via ``db_default``. If you change this strategy, do it with an explicit migration plan.

Generic experiment generation API
---------------------------------
Use ``create_experiments_from_parameters`` to create experiments and associated parameter rows in batches, while reusing existing exact matches.

.. code:: python

   from django_experiment_tracker.experiment_generation import create_experiments_from_parameters
   from my_app.models import Experiment, ExperimentParameter
   create_experiments_from_parameters(
       experiment_model=Experiment,
       experiment_parameter_model=ExperimentParameter,
       experiment_parameters=parameter_cases,  # iterable of parameter sets
       experiment_model_kwargs={"git_commit": git_commit},
       tags=tags_queryset_or_list,
       insert_batch_size=100,
   )
Expected ``experiment_parameters`` shape per experiment:

* iterable of ``(definition, value)``

Git commit recording command
----------------------------
Record the current HEAD commit:

.. code:: bash

   python manage.py record_git_commit

Record last ``N`` commits reachable from HEAD:

.. code:: bash

   python manage.py record_git_commit --backfill 100

Read rewrite map from stdin (for git ``post-rewrite`` hooks):

.. code:: bash

   python manage.py record_git_commit --stdin-rewrite-map

Local experiment scheduling
---------------------------
``run_experiments`` runs a consumer-provided management command once per experiment
and limits concurrency using values assigned through an environment variable::

   python manage.py run_experiments exp_a exp_b \
       --runner run_workflow \
       --runner-argument=--workflow=train \
       --resource CUDA_VISIBLE_DEVICES=0,1

Runner arguments are placed after the experiment alternative ID. Repeat
``--runner-argument`` when the runner needs multiple arguments.

Use ``--resources-per-run`` to assign contiguous groups of values to each run. For
example, the following creates the groups ``0,1`` and ``2,3``::

   python manage.py run_experiments exp_a exp_b \
       --runner run_workflow \
       --runner-argument=--workflow=train \
       --resource CUDA_VISIBLE_DEVICES=0,1,2,3 \
       --resources-per-run 2

The number of resource values must be divisible by ``--resources-per-run``.
``--slots-per-resource`` controls how many runs may share each complete group.

Testing
-------

This repo uses ``pytest`` + ``pytest-django``.

.. code:: bash

   pytest -q
