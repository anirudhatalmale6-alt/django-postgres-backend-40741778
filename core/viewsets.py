"""Reusable CRUD building blocks for the domain apps.

When the data model arrives, each resource should be a few lines: a serializer,
a filterset and a viewset inheriting from here. Everything the endpoints have in
common — ownership scoping, `created_by` stamping, select_related hygiene — lives
in one place instead of being copy-pasted per resource.
"""

from rest_framework import viewsets


class BaseModelViewSet(viewsets.ModelViewSet):
    """Standard CRUD viewset.

    `select_related_fields` / `prefetch_related_fields` are the cheapest fix for
    the N+1 queries that otherwise appear the moment a list endpoint serialises
    a foreign key. Set them per viewset.
    """

    select_related_fields: tuple[str, ...] = ()
    prefetch_related_fields: tuple[str, ...] = ()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.select_related_fields:
            queryset = queryset.select_related(*self.select_related_fields)
        if self.prefetch_related_fields:
            queryset = queryset.prefetch_related(*self.prefetch_related_fields)
        return queryset


class OwnedModelViewSet(BaseModelViewSet):
    """CRUD restricted to rows belonging to the requesting user.

    Scoping in `get_queryset` rather than in a permission check is deliberate:
    a non-owner then gets a 404 from every verb including detail GET, and there
    is no code path where a forgotten permission class exposes another user's
    row.
    """

    owner_field = "created_by"

    def get_queryset(self):
        return super().get_queryset().filter(**{self.owner_field: self.request.user})

    def perform_create(self, serializer):
        serializer.save(**{self.owner_field: self.request.user})
