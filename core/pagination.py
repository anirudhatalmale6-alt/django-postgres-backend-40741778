from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class DefaultPagination(PageNumberPagination):
    """Page-number pagination with a client-settable, server-capped page size.

    The cap matters: without max_page_size a single `?page_size=100000` request
    can pull an entire table into memory.
    """

    page_size_query_param = "page_size"
    max_page_size = 200

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "page": self.page.number,
                "pages": self.page.paginator.num_pages,
                "page_size": self.get_page_size(self.request),
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )

    def get_paginated_response_schema(self, schema):
        # Keep the Swagger response shape in step with the override above,
        # otherwise the docs promise DRF's default envelope and lie to the
        # front-end team.
        return {
            "type": "object",
            "required": ["count", "results"],
            "properties": {
                "count": {"type": "integer", "example": 123},
                "page": {"type": "integer", "example": 1},
                "pages": {"type": "integer", "example": 5},
                "page_size": {"type": "integer", "example": 25},
                "next": {"type": "string", "nullable": True, "format": "uri"},
                "previous": {"type": "string", "nullable": True, "format": "uri"},
                "results": schema,
            },
        }
