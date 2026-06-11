"""
config.py -- Loads config.yaml and exposes settings.

Usage:
    from agent.config import cfg
    print(cfg.namespace)
"""

import yaml
from dataclasses import dataclass, fields
from pathlib import Path

# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class Config:
    # Cluster
    namespace:                str
    poll_interval:            int

    # Anomaly detection
    pending_duration_seconds: int
    restart_count_threshold:  int

    # Cooldown
    cooldown_window_seconds:  int
    cooldown_max_attempts:    int

    # LLM
    ollama_base_url:          str
    model:                    str
    max_iterations:           int
    max_tokens:               int

    # Display
    log_tail_size:            int

    # Logging
    log_file:                 str
    log_level:                str


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

# Explicit type casters for each annotation type used in Config.
# Safer than calling field.type(val) directly, which breaks for
# complex types like Optional, bool, or list.
_TYPE_CASTERS: dict[type, callable] = {
    int:   int,
    str:   str,
    float: float,
    bool:  lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes"),
}


def _load_config() -> Config:
    """Read config.yaml and return a typed Config instance."""
    config_path = Path(__file__).parent.parent / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. "
            "Copy config.yaml to the project root before running."
        )

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f) or {}

    try:
        casted_data = {}
        for field in fields(Config):
            if field.name not in raw:
                raise KeyError(field.name)
            val = raw[field.name]
            caster = _TYPE_CASTERS.get(field.type)
            if caster and val is not None:
                casted_data[field.name] = caster(val)
            else:
                casted_data[field.name] = val

        config_obj = Config(**casted_data)

        # Validation -- explicit raises instead of assert (asserts are
        # stripped when Python runs with -O, silently skipping checks).
        if config_obj.poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
        if config_obj.max_iterations <= 0:
            raise ValueError("max_iterations must be > 0")
        if config_obj.cooldown_max_attempts <= 0:
            raise ValueError("cooldown_max_attempts must be > 0")
        if config_obj.max_tokens < 100:
            raise ValueError("max_tokens too low -- set at least 100")

        return config_obj

    except KeyError as e:
        raise KeyError(f"Missing required config key: {e}. Check config.yaml.")
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid config value: {e}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

cfg = _load_config()
