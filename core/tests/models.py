"""Test-only concrete model.

The abstract bases in core.models cannot be tested directly, so this module
declares one concrete subclass. It lives under core/tests/ and has no migration,
so it never reaches a real database — the test case that uses it creates and
drops the table itself.
"""

from django.db import models

from core.models import BaseModel, SoftDeleteModel


class SoftDeletableThing(SoftDeleteModel, BaseModel):
    label = models.CharField(max_length=50)

    class Meta:
        app_label = "core"
        # Tells Django not to look for/require a migration for this table.
        managed = False
