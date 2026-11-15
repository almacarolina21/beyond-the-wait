"""Load site profiles and scenarios from YAML files.

Two safety decisions worth knowing:

1. We parse YAML with a *safe* loader. The full YAML loader can construct
   arbitrary Python objects from tags like `!!python/object/apply:os.system`,
   which means a malicious config file could run code. The safe loader only
   produces plain data: dicts, lists, strings, numbers, dates.

2. We reject duplicate keys. Standard YAML parsing silently keeps the *last*
   value if a key appears twice (e.g., two `acute_beds:` lines), which can
   hide an editing mistake. We treat it as an error instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from beyond_the_wait.config.errors import ConfigError
from beyond_the_wait.config.models import Scenario, SiteProfile


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader that raises an error on duplicate mapping keys."""


def _construct_mapping_without_duplicates(
    loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)  # resolve YAML merge keys ("<<") first
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_without_duplicates
)


def read_yaml_mapping(path: str | Path) -> dict[str, Any]:
    """Read a YAML file whose top level is a mapping (key: value pairs)."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.load(handle, Loader=_UniqueKeySafeLoader)  # noqa: S506 - safe subclass
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML:\n{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path} must contain key: value pairs at the top level, "
            f"but it contains {type(data).__name__}"
        )
    return data


def format_validation_errors(exc: ValidationError) -> str:
    """Turn a Pydantic error into readable bullet points with dotted paths."""
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(top level)"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)


def _validate_file[ModelT: BaseModel](model_cls: type[ModelT], path: str | Path) -> ModelT:
    data = read_yaml_mapping(path)
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"{path} failed validation ({exc.error_count()} problem(s)):\n"
            f"{format_validation_errors(exc)}"
        ) from exc


def load_site_profile(path: str | Path) -> SiteProfile:
    """Load and validate a site profile YAML file."""
    return _validate_file(SiteProfile, path)


def load_scenario(path: str | Path) -> Scenario:
    """Load and validate a scenario YAML file."""
    return _validate_file(Scenario, path)
