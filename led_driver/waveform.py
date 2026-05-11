"""
Waveform engine for CSV loading and FIFO data preparation (v3).

CSV format:
  - 8 columns, one per channel (Ch0, Ch1, ..., Ch7)
  - Values are intensity percentages (0-100)
  - Each row is one time step at the configured sample rate
  - Missing columns are treated as 0
  - Header row is optional (auto-detected)

Example CSV:
  Ch0,Ch1,Ch2,Ch3,Ch4,Ch5,Ch6,Ch7
  100,0,0,0,0,0,0,0
  50,50,0,0,0,0,0,0
  0,100,0,0,0,0,0,0
"""

import csv
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from .hardware import NUM_CHANNELS, intensity_to_u16


@dataclass
class WaveformInfo:
    """Metadata about a loaded waveform."""
    filepath: str
    num_samples: int        # samples per channel (= number of frames)
    num_channels: int       # channels with non-zero data
    duration_s: float       # at current sample rate
    sample_rate_hz: float
    min_values: list        # per-channel minimum (%)
    max_values: list        # per-channel maximum (%)


class WaveformEngine:
    """Loads CSV waveforms and prepares interleaved FIFO data."""

    def __init__(self, sample_rate_hz: float = 100_000):
        self.sample_rate_hz = sample_rate_hz
        self._raw_data: Optional[np.ndarray] = None   # (N, 8) float, 0-100%
        self._u16_data: Optional[np.ndarray] = None    # (N, 8) uint16
        self._interleaved: Optional[np.ndarray] = None  # (N*8,) uint16
        self._filepath: Optional[str] = None
        self._info: Optional[WaveformInfo] = None

    def load_csv(self, filepath: str) -> WaveformInfo:
        """Load a CSV waveform file. Returns waveform metadata.

        Supports metadata comment lines at the top of the file:
            # sample_rate=100000
            # duration_ms=10.000
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Waveform file not found: {filepath}")

        with open(path, 'r', newline='') as f:
            all_lines = f.readlines()

        if not all_lines:
            raise ValueError("CSV file is empty")

        # Parse metadata comment lines and separate from data
        imported_rate = None
        content_lines = []
        for line in all_lines:
            stripped = line.strip()
            if stripped.startswith('#'):
                if 'sample_rate=' in stripped:
                    try:
                        imported_rate = float(stripped.split('sample_rate=')[1].strip())
                    except ValueError:
                        pass
            else:
                content_lines.append(stripped)

        if imported_rate is not None:
            self.sample_rate_hz = imported_rate

        # Parse CSV from content lines
        import io
        reader = csv.reader(io.StringIO("\n".join(content_lines)))
        rows = list(reader)

        if not rows:
            raise ValueError("CSV file has no data rows")

        # Detect header row
        first_row = rows[0]
        has_header = False
        try:
            [float(v) for v in first_row if v.strip()]
        except ValueError:
            has_header = True

        data_rows = rows[1:] if has_header else rows

        if not data_rows:
            raise ValueError("CSV file has no data rows")

        # Parse data
        parsed = []
        for row in data_rows:
            values = []
            for j in range(NUM_CHANNELS):
                if j < len(row) and row[j].strip():
                    try:
                        val = float(row[j].strip())
                        values.append(np.clip(val, 0.0, 100.0))
                    except ValueError:
                        values.append(0.0)
                else:
                    values.append(0.0)
            parsed.append(values)

        self._raw_data = np.array(parsed, dtype=np.float64)
        self._filepath = str(path)

        # Convert to U16
        self._u16_data = np.vectorize(intensity_to_u16)(self._raw_data).astype(np.uint16)

        # Interleave: flatten row-by-row
        # Each row is [Ch0, Ch1, ..., Ch7] = one frame
        self._interleaved = self._u16_data.flatten()

        # Build info
        num_samples = self._raw_data.shape[0]
        num_channels_used = 0
        for ch in range(NUM_CHANNELS):
            if np.any(self._raw_data[:, ch] > 0):
                num_channels_used += 1

        self._info = WaveformInfo(
            filepath=str(path),
            num_samples=num_samples,
            num_channels=num_channels_used,
            duration_s=num_samples / self.sample_rate_hz,
            sample_rate_hz=self.sample_rate_hz,
            min_values=[float(self._raw_data[:, ch].min()) for ch in range(NUM_CHANNELS)],
            max_values=[float(self._raw_data[:, ch].max()) for ch in range(NUM_CHANNELS)],
        )

        return self._info

    def get_interleaved_u16(self, cal_manager=None) -> Optional[np.ndarray]:
        """Get the interleaved U16 data ready for FIFO writing.

        If cal_manager is provided and enabled, applies power calibration
        correction to the raw data before converting to U16.
        """
        if self._raw_data is None:
            return None
        if cal_manager and cal_manager.enabled and cal_manager.has_calibration:
            corrected = cal_manager.correct_waveform_vectorized(self._raw_data)
            u16 = np.vectorize(intensity_to_u16)(corrected).astype(np.uint16)
            return u16.flatten()
        return self._interleaved

    def get_repeated_interleaved_u16(self, repetitions: int = 1,
                                      cal_manager=None) -> Optional[np.ndarray]:
        """Get interleaved data repeated N times for multi-shot playback."""
        data = self.get_interleaved_u16(cal_manager)
        if data is None:
            return None
        if repetitions <= 1:
            return data
        return np.tile(data, repetitions)

    def get_channel_data(self, channel: int) -> Optional[np.ndarray]:
        """Get raw percentage data for a single channel (for plotting)."""
        if self._raw_data is None or channel >= NUM_CHANNELS:
            return None
        return self._raw_data[:, channel]

    def get_time_axis(self) -> Optional[np.ndarray]:
        """Get time axis in seconds (for plotting)."""
        if self._raw_data is None:
            return None
        n = self._raw_data.shape[0]
        return np.arange(n) / self.sample_rate_hz

    def get_all_channel_data(self) -> Optional[np.ndarray]:
        """Get raw percentage data for all channels, shape (N, 8)."""
        return self._raw_data

    @property
    def info(self) -> Optional[WaveformInfo]:
        return self._info

    @property
    def is_loaded(self) -> bool:
        return self._raw_data is not None

    @property
    def num_samples(self) -> int:
        """Number of samples per channel (= number of frames)."""
        return self._raw_data.shape[0] if self._raw_data is not None else 0

    def set_sample_rate(self, rate_hz: float):
        """Update sample rate and recalculate duration."""
        self.sample_rate_hz = rate_hz
        if self._info:
            self._info.sample_rate_hz = rate_hz
            self._info.duration_s = self._info.num_samples / rate_hz

    def generate_test_waveform(self, waveform_type: str = "sine",
                                frequency_hz: float = 1000,
                                num_cycles: int = 5,
                                channels: list = None) -> WaveformInfo:
        """Generate a test waveform for development/testing."""
        if channels is None:
            channels = [0]

        num_samples = int(num_cycles * self.sample_rate_hz / frequency_hz)
        t = np.arange(num_samples) / self.sample_rate_hz

        self._raw_data = np.zeros((num_samples, NUM_CHANNELS), dtype=np.float64)

        for ch in channels:
            if ch >= NUM_CHANNELS:
                continue
            if waveform_type == "sine":
                self._raw_data[:, ch] = 50.0 + 50.0 * np.sin(2 * np.pi * frequency_hz * t)
            elif waveform_type == "square":
                self._raw_data[:, ch] = np.where(
                    np.sin(2 * np.pi * frequency_hz * t) >= 0, 100.0, 0.0
                )
            elif waveform_type == "triangle":
                self._raw_data[:, ch] = 100.0 * np.abs(
                    2 * (frequency_hz * t - np.floor(frequency_hz * t + 0.5))
                )
            elif waveform_type == "sawtooth":
                self._raw_data[:, ch] = 100.0 * (
                    frequency_hz * t - np.floor(frequency_hz * t)
                )

        self._u16_data = np.vectorize(intensity_to_u16)(self._raw_data).astype(np.uint16)
        self._interleaved = self._u16_data.flatten()
        self._filepath = f"<generated: {waveform_type} {frequency_hz}Hz>"

        self._info = WaveformInfo(
            filepath=self._filepath,
            num_samples=num_samples,
            num_channels=len(channels),
            duration_s=num_samples / self.sample_rate_hz,
            sample_rate_hz=self.sample_rate_hz,
            min_values=[float(self._raw_data[:, ch].min()) for ch in range(NUM_CHANNELS)],
            max_values=[float(self._raw_data[:, ch].max()) for ch in range(NUM_CHANNELS)],
        )

        return self._info
