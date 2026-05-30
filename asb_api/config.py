import os
import re
import yaml
from pathlib import Path

_ENV_VAR_RE = re.compile(r"\$\{([^}:]+)(?::(-)?([^\}]*))?\}")


def _resolve_env(value):
    if isinstance(value, str):
        def _replace(m):
            var = m.group(1)
            has_default = m.group(2) is not None or m.group(3) is not None  # : or :- or :default
            default = m.group(3) if m.group(3) is not None else ""
            val = os.environ.get(var)
            if val is not None:
                return val
            return default
        return _ENV_VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def _normalize_none_keys(d):
    if isinstance(d, dict):
        return {"null" if k is None else k: _normalize_none_keys(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_normalize_none_keys(v) for v in d]
    return d


def load_config(path: str | None = None) -> dict:
    if path is None:
        path = os.environ.get("ASB_CONFIG_PATH", "config.yaml")
    cfg_path = Path(path)
    if not cfg_path.is_absolute():
        cfg_path = Path.cwd() / cfg_path
    with open(cfg_path, "r") as f:
        raw = yaml.safe_load(f)
    raw = _normalize_none_keys(raw)
    return _resolve_env(raw)
