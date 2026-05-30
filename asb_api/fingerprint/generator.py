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

    @staticmethod
    def _validate_viewport(value) -> tuple[int, int]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError("Fingerprint viewport must contain exactly two integers")
        try:
            width, height = int(value[0]), int(value[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("Fingerprint viewport must contain exactly two integers") from exc
        return width, height

    @staticmethod
    def _validate_canvas(value) -> Literal["noise", "empty", "off"]:
        if value not in {"noise", "empty", "off"}:
            raise ValueError("Fingerprint canvas must be one of: noise, empty, off")
        return value

    def get(self, preset_name: str) -> Fingerprint:
        cfg = self.presets.get(preset_name, self.presets.get("general", {}))
        return Fingerprint(
            user_agent=cfg.get("user_agent", ""),
            viewport=self._validate_viewport(cfg.get("viewport", [1920, 1080])),
            webgl_vendor=cfg.get("webgl_vendor", ""),
            canvas=self._validate_canvas(cfg.get("canvas", "off")),
            accept_language=cfg.get("accept_language"),
            platform=cfg.get("platform"),
        )

    def rotate(self, current: Fingerprint) -> Fingerprint:
        return current
