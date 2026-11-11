"""wequant serve: the service operations over local HTTP; every route is built from the operation list."""

from wequant.api.app import STATUS, create_app

__all__ = ["STATUS", "create_app"]
