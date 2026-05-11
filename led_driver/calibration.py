"""
Power calibration for LED channels.

Corrects for:
  1. LED nonlinearity — so command% maps to proportional optical power
  2. Channel-to-channel power variation — so 100% produces equal power on all channels

Calibration data is a set of (drive_percent, measured_power_mW) points per channel.
Minimum 2 points (0% and 100%) for equalization only.
More points (e.g. 0%, 25%, 50%, 75%, 100%) improve linearity correction.

The correction pipeline:
  command%  →  desired_power = command% * equalized_max / 100
            →  required_drive% = inverse_interp(desired_power)
            →  U16 DAC value
"""

import json
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

from .hardware import NUM_CHANNELS


@dataclass
class ChannelCalibration:
    """Calibration data for a single channel."""
    # Measured points: list of (drive_percent, power_mW) tuples
    # Must be sorted by drive_percent ascending
    points: list = field(default_factory=list)

    @property
    def is_calibrated(self) -> bool:
        return len(self.points) >= 2

    @property
    def max_power(self) -> float:
        """Maximum measured power (at highest drive %)."""
        if not self.points:
            return 0.0
        return max(p[1] for p in self.points)

    @property
    def min_power(self) -> float:
        """Minimum measured power (at lowest drive %, should be ~0)."""
        if not self.points:
            return 0.0
        return min(p[1] for p in self.points)

    def drive_to_power(self, drive_pct: float) -> float:
        """Interpolate: drive% → measured power (mW)."""
        if not self.is_calibrated:
            return drive_pct  # uncalibrated pass-through
        drives = [p[0] for p in self.points]
        powers = [p[1] for p in self.points]
        return float(np.interp(drive_pct, drives, powers))

    def power_to_drive(self, power_mW: float) -> float:
        """Inverse interpolate: desired power (mW) → required drive%.

        This is the core correction function. Given a desired optical power,
        returns the drive percentage needed to achieve it, accounting for
        LED nonlinearity.
        """
        if not self.is_calibrated:
            return power_mW  # uncalibrated pass-through
        drives = [p[0] for p in self.points]
        powers = [p[1] for p in self.points]
        # np.interp requires xp to be increasing; powers should be monotonic
        # but clamp just in case
        result = float(np.interp(power_mW, powers, drives))
        return np.clip(result, 0.0, 100.0)

    def add_point(self, drive_pct: float, power_mW: float):
        """Add a calibration point and keep sorted."""
        # Remove existing point at same drive% if any
        self.points = [(d, p) for d, p in self.points if abs(d - drive_pct) > 0.01]
        self.points.append((drive_pct, power_mW))
        self.points.sort(key=lambda x: x[0])

    def clear(self):
        self.points = []

    def to_dict(self) -> dict:
        return {"points": self.points}

    @classmethod
    def from_dict(cls, data: dict) -> "ChannelCalibration":
        cal = cls()
        cal.points = [tuple(p) for p in data.get("points", [])]
        return cal


class CalibrationManager:
    """Manages calibration data for all channels and applies corrections."""

    def __init__(self):
        self.channels = [ChannelCalibration() for _ in range(NUM_CHANNELS)]
        self.enabled = False
        self.equalize = True  # equalize power across channels
        self._equalized_max = None  # computed on demand

    @property
    def has_calibration(self) -> bool:
        """True if at least one channel is calibrated."""
        return any(ch.is_calibrated for ch in self.channels)

    @property
    def num_calibrated(self) -> int:
        return sum(1 for ch in self.channels if ch.is_calibrated)

    @property
    def equalized_max_power(self) -> float:
        """The equalized maximum power — minimum of all calibrated channels' max power.

        If equalization is off, returns a large number (no capping).
        """
        if not self.equalize:
            return float('inf')

        calibrated_maxes = [ch.max_power for ch in self.channels if ch.is_calibrated]
        if not calibrated_maxes:
            return float('inf')
        return min(calibrated_maxes)

    def correct_intensity(self, channel: int, command_pct: float) -> float:
        """Apply calibration correction to a single channel command.

        Args:
            channel: channel index (0-7)
            command_pct: desired intensity as percentage (0-100)

        Returns:
            Corrected drive percentage (0-100) that produces the desired
            optical power, accounting for nonlinearity and equalization.
        """
        if not self.enabled:
            return command_pct

        cal = self.channels[channel]
        if not cal.is_calibrated:
            return command_pct  # uncalibrated channel passes through

        # Determine the target power
        if self.equalize:
            eq_max = self.equalized_max_power
            if eq_max == float('inf') or eq_max <= 0:
                return command_pct
            # Scale: 100% command → equalized max power
            desired_power = (command_pct / 100.0) * eq_max
        else:
            # No equalization: 100% command → this channel's max power
            desired_power = (command_pct / 100.0) * cal.max_power

        # Clamp to this channel's achievable range
        desired_power = np.clip(desired_power, cal.min_power, cal.max_power)

        # Inverse lookup: desired power → required drive%
        return cal.power_to_drive(desired_power)

    def correct_waveform(self, data: np.ndarray) -> np.ndarray:
        """Apply calibration correction to an entire waveform array.

        Args:
            data: (N, 8) array of intensity percentages (0-100)

        Returns:
            (N, 8) array of corrected drive percentages (0-100)
        """
        if not self.enabled or not self.has_calibration:
            return data

        corrected = data.copy()
        for ch in range(NUM_CHANNELS):
            if self.channels[ch].is_calibrated:
                for i in range(data.shape[0]):
                    corrected[i, ch] = self.correct_intensity(ch, data[i, ch])
        return corrected

    def correct_waveform_vectorized(self, data: np.ndarray) -> np.ndarray:
        """Faster vectorized correction for large waveforms.

        Uses np.interp on entire channel columns at once.
        """
        if not self.enabled or not self.has_calibration:
            return data

        corrected = data.copy()
        eq_max = self.equalized_max_power

        for ch in range(NUM_CHANNELS):
            cal = self.channels[ch]
            if not cal.is_calibrated:
                continue

            drives = np.array([p[0] for p in cal.points])
            powers = np.array([p[1] for p in cal.points])

            # Compute desired power for entire column
            if self.equalize and eq_max != float('inf') and eq_max > 0:
                desired = data[:, ch] / 100.0 * eq_max
            else:
                desired = data[:, ch] / 100.0 * cal.max_power

            desired = np.clip(desired, cal.min_power, cal.max_power)

            # Vectorized inverse interpolation
            corrected[:, ch] = np.interp(desired, powers, drives)
            corrected[:, ch] = np.clip(corrected[:, ch], 0, 100)

        return corrected

    def get_channel_summary(self, channel: int) -> str:
        """Get a human-readable summary of a channel's calibration."""
        cal = self.channels[channel]
        if not cal.is_calibrated:
            return "Not calibrated"
        points_str = ", ".join([f"{d:.0f}%→{p:.1f}mW" for d, p in cal.points])
        return f"Max: {cal.max_power:.1f} mW | {len(cal.points)} points ({points_str})"

    def save(self, filepath: Path):
        """Save calibration data to JSON file."""
        data = {
            "enabled": self.enabled,
            "equalize": self.equalize,
            "channels": [ch.to_dict() for ch in self.channels],
        }
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, filepath: Path) -> "CalibrationManager":
        """Load calibration data from JSON file."""
        mgr = cls()
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text())
                mgr.enabled = data.get("enabled", False)
                mgr.equalize = data.get("equalize", True)
                for i, ch_data in enumerate(data.get("channels", [])):
                    if i < NUM_CHANNELS:
                        mgr.channels[i] = ChannelCalibration.from_dict(ch_data)
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"Warning: could not load calibration from {filepath}: {e}")
        return mgr
