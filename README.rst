Django experiment tracker
=========================

Parameter tracking for machine learning experiments.

Quick start
-----------

#. Add ``"django_experiment_tracker"`` to your ``INSTALLED_APPS`` setting like this:

   .. code:: python

      INSTALLED_APPS = [
         ...,
         "django_experiment_tracker",
      ]

#. Run ``python manage.py migrate`` to create the models.
