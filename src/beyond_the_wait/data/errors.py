"""Error types for the public-data pipeline."""


class PublicDataError(ValueError):
    """A public source file is missing, malformed, or fails a validation check.

    The pipeline stops on these errors instead of guessing, because a quietly
    "fixed" public number is worse than no number.
    """


class SyntheticDataError(ValueError):
    """A synthetic-generation request or generated table is invalid."""
