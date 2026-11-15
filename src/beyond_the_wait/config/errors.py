"""Error types for configuration problems.

Using our own exception classes (instead of letting raw library errors leak
out) lets callers -- later, the dashboard -- catch "the config is wrong" in
one place and show a friendly message.
"""


class ConfigError(ValueError):
    """A configuration file is missing, unreadable, or fails validation."""


class ScenarioError(ConfigError):
    """A scenario cannot be applied to a site profile."""
