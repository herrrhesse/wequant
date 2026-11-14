"""A BaoStock session for the operations that fetch (round 3; S2.11), its failures as service errors."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager

from wequant.data.merge import StalePlan
from wequant.data.sources.baostock import BaoStock, BaoStockError
from wequant.data.store import StoreLocked
from wequant.data.update import UpdateCancelled
from wequant.service.base import ErrorDetail, guarded
from wequant.service.errors import Exit, ServiceError


@contextmanager
def baostock() -> Iterator[BaoStock]:
    """A BaoStock session, its failures as service errors: UNREACHABLE, LOCKED store_busy, INTERRUPTED."""
    try:
        with guarded(), BaoStock() as source:
            yield source
    except UpdateCancelled as error:
        raise ServiceError(Exit.INTERRUPTED, str(error)) from None
    except (StoreLocked, StalePlan) as error:
        raise ServiceError(Exit.LOCKED, str(error), [ErrorDetail(code="store_busy", message=str(error))]) from None
    except (BaoStockError, ConnectionError, TimeoutError, socket.gaierror) as error:
        message = f"BaoStock cannot be reached: {error}"
        raise ServiceError(
            Exit.UNREACHABLE, message, [ErrorDetail(code="source_unreachable", message=message)]
        ) from None
