"""
config.py -- Loads config.yaml and exposes settings.

Usage:
    from agent.config import cfg
    print(cfg.namespace)

"""

import yaml
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Config dataclass -- one field per setting, typed
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
    """
    Read config.yaml from the root and return a Config instance.
    Raises a clear error if the file is missing or a required key is absent.
    """
    # config.yaml sits at the project root -- one level above agent/
    config_path = Path(__file__).parent.parent / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. "
            "Copy config.yaml to the project root before running."
        )

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    try:
        return Config(
            namespace                = raw["namespace"],
            poll_interval            = int(raw["poll_interval"]),
            pending_duration_seconds = int(raw["pending_duration_seconds"]),
            restart_count_threshold  = int(raw["restart_count_threshold"]),
            cooldown_window_seconds  = int(raw["cooldown_window_seconds"]),
            cooldown_max_attempts    = int(raw["cooldown_max_attempts"]),
            ollama_base_url          = raw["ollama_base_url"],
            model                    = raw["model"],
            max_iterations           = int(raw["max_iterations"]),
            max_tokens               = int(raw["max_tokens"]),
            log_tail_size            = int(raw["log_tail_size"]),
            log_file                 = raw["log_file"],
            log_level                = raw["log_level"],
        )
    except KeyError as e:
        raise KeyError(f"Missing required config key: {e}. Check config.yaml.")


# ---------------------------------------------------------------------------
# Module-level singleton -- import this everywhere
# ---------------------------------------------------------------------------

cfg = _load_config()