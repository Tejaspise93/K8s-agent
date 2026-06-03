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
        # Dynamically parse and cast fields based on dataclass annotations
        casted_data = {}
        for field in fields(Config):
            val = raw[field.name]
            # Convert type if necessary (e.g., str to int)
            casted_data[field.name] = field.type(val) if val is not None else val
            
        config_obj = Config(**casted_data)

        assert config_obj.poll_interval > 0,        "poll_interval must be > 0"
        assert config_obj.max_iterations > 0,       "max_iterations must be > 0"
        assert config_obj.cooldown_max_attempts > 0, "cooldown_max_attempts must be > 0"
        assert config_obj.max_tokens >= 100,        "max_tokens too low -- set at least 100"

        return config_obj
        
    except KeyError as e:
        raise KeyError(f"Missing required config key: {e}. Check config.yaml.")
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid value type in config.yaml: {e}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

cfg = _load_config()
