"""Abstract base models for the domain apps to inherit.

These exist so that when your data model lands, every table gets created/updated
timestamps, optional UUID public identifiers and soft-delete for free and
consistently — rather than each app reinventing them slightly differently.

Nothing here creates a table (all `abstract = True`), so no domain assumptions
are baked in.
"""

import uuid

from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDPrimaryKeyModel(models.Model):
    """Use where an identifier is exposed in URLs.

    Sequential integer ids leak volume and let a client walk the table by
    incrementing; a UUID pk removes both without any extra lookup work.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        # A queryset .delete() must honour soft-delete too, otherwise
        # Model.objects.filter(...).delete() quietly bypasses it.
        return super().update(deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()

    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.filter(deleted_at__isnull=False)


class SoftDeleteManager(models.Manager):
    """Default manager that hides soft-deleted rows."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(
            deleted_at__isnull=True
        )


class SoftDeleteModel(models.Model):
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # `objects` hides deleted rows; `all_objects` is the escape hatch for
    # admin/reporting. Order matters — the FIRST manager declared is the one
    # migrations and related descriptors use.
    objects = SoftDeleteManager()
    all_objects = models.Manager.from_queryset(SoftDeleteQuerySet)()

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def delete(self, using=None, keep_parents=False):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=["deleted_at"])


class BaseModel(TimeStampedModel, UUIDPrimaryKeyModel):
    """The usual starting point: UUID pk + timestamps."""

    class Meta:
        abstract = True
        ordering = ["-created_at"]
