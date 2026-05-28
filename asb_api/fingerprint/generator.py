from dataclasses import dataclass
from typing import Literal


@dataclass
class Fingerprint:
    user_agent: str
    viewport: tuple[int, int]
    webgl_vendor: str
    canvas: Literal["noise", "empty", "off"]
    accept_language: str | None = None
    platform: str | None = None


class FingerprintGenerator:
    def __init__(self, presets: dict):
        self.presets = presets

    def get(self, preset_name: str) -> Fingerprint:
        cfg = self.presets.get(preset_name, self.presets.get("general", {}))
        return Fingerprint(
            user_agent=cfg.get("user_agent", ""),
            viewport=tuple(cfg.get("viewport", [1920, 1080])),
            webgl_vendor=cfg.get("webgl_vendor", ""),
            canvas=cfg.get("canvas", "off"),
            accept_language=cfg.get("accept_language"),
            platform=cfg.get("platform"),
        )

    def rotate(self, current: Fingerprint) -> Fingerprint:
        return current
