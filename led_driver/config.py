"""
Configuration management for the LED driver.

Handles persistent channel configuration (wavelength labels, colors)
stored as JSON in the user's config directory.

Config file location:
  Linux:   ~/.config/led_driver/config.json
  Windows: %APPDATA%/led_driver/config.json
  macOS:   ~/Library/Application Support/led_driver/config.json
"""

import json
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

from .hardware import NUM_CHANNELS


def _get_config_dir() -> Path:
    """Get the platform-appropriate config directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "led_driver"


CONFIG_PATH = _get_config_dir() / "config.json"


def wavelength_to_rgb(nm: float) -> tuple:
    """Convert a wavelength in nanometers to an (R, G, B) tuple (0-255).

    Uses a piecewise linear approximation of the CIE visible spectrum.
    Wavelengths outside 380-780 nm are clamped with sensible UV/IR colors.
    """
    # Ultraviolet
    if nm < 380:
        return (120, 0, 180)  # deep violet/purple for UV

    # Infrared
    if nm > 780:
        return (120, 0, 0)  # deep red for IR

    # Visible spectrum approximation
    if nm < 440:
        r = -(nm - 440) / (440 - 380)
        g = 0.0
        b = 1.0
    elif nm < 490:
        r = 0.0
        g = (nm - 440) / (490 - 440)
        b = 1.0
    elif nm < 510:
        r = 0.0
        g = 1.0
        b = -(nm - 510) / (510 - 490)
    elif nm < 580:
        r = (nm - 510) / (580 - 510)
        g = 1.0
        b = 0.0
    elif nm < 645:
        r = 1.0
        g = -(nm - 645) / (645 - 580)
        b = 0.0
    else:
        r = 1.0
        g = 0.0
        b = 0.0

    # Intensity falloff at edges of visible spectrum
    if nm < 420:
        factor = 0.3 + 0.7 * (nm - 380) / (420 - 380)
    elif nm > 700:
        factor = 0.3 + 0.7 * (780 - nm) / (780 - 700)
    else:
        factor = 1.0

    r = int(min(255, r * factor * 255))
    g = int(min(255, g * factor * 255))
    b = int(min(255, b * factor * 255))

    # Ensure minimum brightness for visibility on dark backgrounds
    max_component = max(r, g, b)
    if max_component < 80:
        scale = 80 / max(max_component, 1)
        r = min(255, int(r * scale))
        g = min(255, int(g * scale))
        b = min(255, int(b * scale))

    return (r, g, b)


def wavelength_to_hex(nm: float) -> str:
    """Convert wavelength in nm to hex color string."""
    r, g, b = wavelength_to_rgb(nm)
    return f"#{r:02x}{g:02x}{b:02x}"


# Default channel colors (when no wavelength is set)
DEFAULT_COLORS = [
    "#e6194b",  # Ch0 - red
    "#3cb44b",  # Ch1 - green
    "#4363d8",  # Ch2 - blue
    "#f58231",  # Ch3 - orange
    "#911eb4",  # Ch4 - purple
    "#42d4f4",  # Ch5 - cyan
    "#f032e6",  # Ch6 - magenta
    "#bfef45",  # Ch7 - lime
]


@dataclass
class ChannelConfig:
    """Configuration for a single channel."""
    wavelength_nm: Optional[float] = None  # None = default, float = wavelength
    custom_label: Optional[str] = None     # None = auto-generate from wavelength

    @property
    def label(self) -> str:
        if self.custom_label:
            return self.custom_label
        if self.wavelength_nm is not None:
            return f"{self.wavelength_nm:.0f} nm"
        return None  # caller uses default "Ch N"

    @property
    def has_wavelength(self) -> bool:
        return self.wavelength_nm is not None


@dataclass
class AppConfig:
    """Full application configuration."""
    channels: list = field(
        default_factory=lambda: [ChannelConfig() for _ in range(NUM_CHANNELS)])

    def get_label(self, channel: int) -> str:
        """Get display label for a channel."""
        label = self.channels[channel].label
        return label if label else f"Ch {channel}"

    def get_color(self, channel: int) -> str:
        """Get hex color for a channel."""
        cfg = self.channels[channel]
        if cfg.has_wavelength:
            return wavelength_to_hex(cfg.wavelength_nm)
        return DEFAULT_COLORS[channel]

    def get_all_colors(self) -> list:
        """Get hex colors for all channels."""
        return [self.get_color(i) for i in range(NUM_CHANNELS)]

    def get_all_labels(self) -> list:
        """Get labels for all channels."""
        return [self.get_label(i) for i in range(NUM_CHANNELS)]

    def save(self):
        """Save config to disk."""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "channels": [
                {
                    "wavelength_nm": ch.wavelength_nm,
                    "custom_label": ch.custom_label,
                }
                for ch in self.channels
            ]
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls) -> "AppConfig":
        """Load config from disk, or return defaults if not found."""
        config = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
                channels = data.get("channels", [])
                for i, ch_data in enumerate(channels):
                    if i < NUM_CHANNELS:
                        config.channels[i] = ChannelConfig(
                            wavelength_nm=ch_data.get("wavelength_nm"),
                            custom_label=ch_data.get("custom_label"),
                        )
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"Warning: could not load config from {CONFIG_PATH}: {e}")
        return config
