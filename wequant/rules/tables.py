"""Reading limit inputs from the store and writing the limit check back."""


def load_limits(store, manifest=None, book=None):
    """code, date, limit_up, limit_down (Int32 厘), limit_state for every bar of one snapshot, voids applied."""
    raise NotImplementedError


def limit_check_current(manifest, book=None):
    """True when the manifest's limit check ran on its current inputs, rule book and limit format."""
    raise NotImplementedError
