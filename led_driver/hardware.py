"""
Hardware abstraction layer for the PXI-7853R FPGA (v3).

Provides a unified interface with two backends:
  - MockBackend: simulates the FPGA for GUI development/testing
  - FPGABackend: real nifpga interface for production use

Voltage mapping (PXI-7853R):
  DAC range:  ±10 V  → U16 0..65535
  LED range:  0–10 V → U16 32768..65535
  User-facing: 0–100% intensity

Architecture (v3):
  FPGA loop runs at 8× the desired output rate (single-element FIFO reads).
  Output_Rate_Ticks controls the loop period in FPGA clock ticks.
  Effective output rate = 40 MHz / (Output_Rate_Ticks × 8).
  Default: Output_Rate_Ticks = 50 → 800 kHz loop → 100 kHz output.
"""

import threading
import time
import numpy as np
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Callable

NUM_CHANNELS = 8
FPGA_CLOCK_HZ = 40_000_000

# Voltage / DAC mapping constants
DAC_MIN = 0          # U16 for -10 V
DAC_MAX = 65535      # U16 for +10 V
LED_V_MIN = 0.0      # LED source minimum input voltage
LED_V_MAX = 10.0     # LED source maximum input voltage

# U16 values corresponding to LED voltage range
LED_U16_MIN = 32768  # 0 V on ±10 V DAC
LED_U16_MAX = 65535  # +10 V on ±10 V DAC


def intensity_to_u16(intensity_pct: float) -> int:
    """Convert 0-100% intensity to U16 DAC value (0-10V range)."""
    frac = max(0.0, min(100.0, intensity_pct)) / 100.0
    return int(LED_U16_MIN + frac * (LED_U16_MAX - LED_U16_MIN))


def u16_to_intensity(u16_val: int) -> float:
    """Convert U16 DAC value back to 0-100% intensity."""
    if u16_val <= LED_U16_MIN:
        return 0.0
    if u16_val >= LED_U16_MAX:
        return 100.0
    return (u16_val - LED_U16_MIN) / (LED_U16_MAX - LED_U16_MIN) * 100.0


def u16_to_voltage(u16_val: int) -> float:
    """Convert U16 DAC value to voltage."""
    return -10.0 + (u16_val / 65535.0) * 20.0


def output_rate_to_ticks(rate_hz: float) -> int:
    """Convert desired output rate (Hz) to Output_Rate_Ticks value."""
    return max(1, int(FPGA_CLOCK_HZ / (rate_hz * NUM_CHANNELS)))


def ticks_to_output_rate(ticks: int) -> float:
    """Convert Output_Rate_Ticks value to output rate (Hz)."""
    return FPGA_CLOCK_HZ / (ticks * NUM_CHANNELS)


@dataclass
class ChannelState:
    """State of a single output channel."""
    enabled: bool = False
    mode: int = 0           # 0 = CW, 1 = AWG
    cw_value: int = LED_U16_MIN  # U16 DAC value
    current_output: int = 0      # actual output value (for monitoring)


@dataclass
class HardwareState:
    """Complete hardware state."""
    channels: list = field(default_factory=lambda: [ChannelState() for _ in range(NUM_CHANNELS)])
    awg_active: bool = False
    output_rate_ticks: int = 50  # 50 ticks → 800 kHz loop → 100 kHz output
    loop_count: int = 0
    fifo_underflow: bool = False
    # Trigger state
    trigger_mode: int = 0        # 0=immediate, 1=hardware, 2=software
    trigger_out_enable: bool = True
    trigger_edge: int = 0        # 0=rising, 1=falling
    awg_armed: bool = False
    awg_running: bool = False
    # Playback control
    awg_frame_count: int = 0     # 0=continuous, >0=single-shot
    awg_frames_played: int = 0
    awg_complete: bool = False
    awg_stop_behavior: int = 0   # 0=hold, 1=CW, 2=zero


class HardwareBackend(ABC):
    """Abstract base class for hardware backends."""

    @abstractmethod
    def connect(self) -> bool:
        ...

    @abstractmethod
    def disconnect(self):
        ...

    @abstractmethod
    def set_channel_enable(self, channel: int, enabled: bool):
        ...

    @abstractmethod
    def set_channel_mode(self, channel: int, mode: int):
        ...

    @abstractmethod
    def set_channel_cw_value(self, channel: int, value: int):
        ...

    @abstractmethod
    def set_awg_active(self, active: bool):
        ...

    @abstractmethod
    def set_output_rate(self, ticks: int):
        """Set Output_Rate_Ticks. Output rate = 40MHz / (ticks × 8)."""
        ...

    @abstractmethod
    def write_fifo(self, data: np.ndarray, timeout_ms: int = 5000) -> int:
        """Write interleaved U16 data to AWG FIFO. Returns elements written."""
        ...

    @abstractmethod
    def set_trigger_mode(self, mode: int):
        ...

    @abstractmethod
    def set_trigger_out_enable(self, enabled: bool):
        ...

    @abstractmethod
    def set_trigger_edge(self, edge: int):
        ...

    @abstractmethod
    def fire_software_trigger(self):
        ...

    @abstractmethod
    def is_armed(self) -> bool:
        ...

    @abstractmethod
    def set_frame_count(self, count: int):
        """Set number of frames to play. 0 = continuous."""
        ...

    @abstractmethod
    def set_stop_behavior(self, behavior: int):
        """Set stop behavior: 0=hold, 1=CW, 2=zero."""
        ...

    @abstractmethod
    def is_complete(self) -> bool:
        ...

    @abstractmethod
    def get_frames_played(self) -> int:
        ...

    @abstractmethod
    def get_state(self) -> HardwareState:
        ...

    @abstractmethod
    def get_fifo_depth(self) -> int:
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        ...


class MockBackend(HardwareBackend):
    """
    Simulated FPGA backend for development and testing.
    Runs a background thread that mimics the FPGA behavior.
    """

    def __init__(self):
        self._state = HardwareState()
        self._connected = False
        self._fifo_buffer = np.array([], dtype=np.uint16)
        self._fifo_lock = threading.Lock()
        self._sim_thread: Optional[threading.Thread] = None
        self._sim_running = False
        self._fifo_read_pos = 0
        self._output_callback: Optional[Callable] = None

    def connect(self) -> bool:
        self._connected = True
        self._sim_running = True
        self._sim_thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self._sim_thread.start()
        return True

    def disconnect(self):
        self._sim_running = False
        if self._sim_thread:
            self._sim_thread.join(timeout=2.0)
        self._connected = False

    def set_channel_enable(self, channel: int, enabled: bool):
        self._state.channels[channel].enabled = enabled

    def set_channel_mode(self, channel: int, mode: int):
        self._state.channels[channel].mode = mode

    def set_channel_cw_value(self, channel: int, value: int):
        self._state.channels[channel].cw_value = value

    def set_awg_active(self, active: bool):
        self._state.awg_active = active
        if active:
            self._state.fifo_underflow = False
            self._state.awg_complete = False
            self._state.awg_frames_played = 0
            if self._state.trigger_mode == 0:
                self._state.awg_running = True
            else:
                self._state.awg_armed = True
                self._state.awg_running = False
        else:
            self._state.awg_running = False
            self._state.awg_armed = False
            self._state.awg_complete = False

    def set_output_rate(self, ticks: int):
        self._state.output_rate_ticks = max(1, ticks)

    def write_fifo(self, data: np.ndarray, timeout_ms: int = 5000) -> int:
        with self._fifo_lock:
            self._fifo_buffer = np.concatenate([self._fifo_buffer, data.astype(np.uint16)])
            return len(data)

    def set_trigger_mode(self, mode: int):
        self._state.trigger_mode = mode

    def set_trigger_out_enable(self, enabled: bool):
        self._state.trigger_out_enable = enabled

    def set_trigger_edge(self, edge: int):
        self._state.trigger_edge = edge

    def fire_software_trigger(self):
        if self._state.trigger_mode == 2 and self._state.awg_armed:
            self._state.awg_running = True
            self._state.awg_armed = False

    def is_armed(self) -> bool:
        return self._state.awg_armed

    def set_frame_count(self, count: int):
        self._state.awg_frame_count = count

    def set_stop_behavior(self, behavior: int):
        self._state.awg_stop_behavior = behavior

    def is_complete(self) -> bool:
        return self._state.awg_complete

    def get_frames_played(self) -> int:
        return self._state.awg_frames_played

    def clear_fifo(self):
        with self._fifo_lock:
            self._fifo_buffer = np.array([], dtype=np.uint16)
            self._fifo_read_pos = 0

    def get_state(self) -> HardwareState:
        return self._state

    def get_fifo_depth(self) -> int:
        with self._fifo_lock:
            return len(self._fifo_buffer) - self._fifo_read_pos

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_output_callback(self, callback: Callable):
        """Set callback for output updates: callback(channel_values: list[int])"""
        self._output_callback = callback

    def _simulation_loop(self):
        """Background thread simulating the FPGA behavior at reduced rate."""
        while self._sim_running:
            time.sleep(0.001)  # 1 kHz simulation rate
            self._state.loop_count += 8  # 8 ticks per frame

            fifo_samples = [0] * NUM_CHANNELS

            if self._state.awg_running:
                with self._fifo_lock:
                    remaining = len(self._fifo_buffer) - self._fifo_read_pos
                    if remaining >= NUM_CHANNELS:
                        fifo_samples = self._fifo_buffer[
                            self._fifo_read_pos:self._fifo_read_pos + NUM_CHANNELS
                        ].tolist()
                        self._fifo_read_pos += NUM_CHANNELS
                        self._state.awg_frames_played += 1

                        if (self._state.awg_frame_count > 0 and
                                self._state.awg_frames_played >= self._state.awg_frame_count):
                            self._state.awg_running = False
                            self._state.awg_complete = True
                    else:
                        self._state.fifo_underflow = True

            outputs = []
            for ch in range(NUM_CHANNELS):
                if not self._state.channels[ch].enabled:
                    val = LED_U16_MIN
                elif self._state.channels[ch].mode == 0:
                    val = self._state.channels[ch].cw_value
                elif self._state.awg_complete:
                    if self._state.awg_stop_behavior == 0:
                        val = fifo_samples[ch]
                    elif self._state.awg_stop_behavior == 1:
                        val = self._state.channels[ch].cw_value
                    else:
                        val = LED_U16_MIN
                else:
                    val = fifo_samples[ch]
                self._state.channels[ch].current_output = val
                outputs.append(val)

            if self._output_callback:
                self._output_callback(outputs)


class FPGABackend(HardwareBackend):
    """
    Real NI FPGA backend using the nifpga package.
    Requires NI-RIO drivers and a compiled .lvbitx bitfile.
    """

    def __init__(self, bitfile_path: str, resource: str = "RIO0"):
        self._bitfile = bitfile_path
        self._resource = resource
        self._session = None
        self._state = HardwareState()

    def connect(self) -> bool:
        try:
            from nifpga import Session
            self._session = Session(bitfile=self._bitfile, resource=self._resource)
            return True
        except Exception as e:
            print(f"FPGA connection failed: {e}")
            return False

    def disconnect(self):
        if self._session:
            self._session.close()
            self._session = None

    def set_channel_enable(self, channel: int, enabled: bool):
        if self._session:
            self._session.registers[f"Ch{channel}_Enable"].write(enabled)
            self._state.channels[channel].enabled = enabled

    def set_channel_mode(self, channel: int, mode: int):
        if self._session:
            self._session.registers[f"Ch{channel}_Mode"].write(mode)
            self._state.channels[channel].mode = mode

    def set_channel_cw_value(self, channel: int, value: int):
        if self._session:
            self._session.registers[f"Ch{channel}_CW_Value"].write(value)
            self._state.channels[channel].cw_value = value

    def set_awg_active(self, active: bool):
        if self._session:
            self._session.registers["AWG_Active"].write(active)
            self._state.awg_active = active

    def set_output_rate(self, ticks: int):
        if self._session:
            self._session.registers["Output_Rate_Ticks"].write(ticks)
            self._state.output_rate_ticks = ticks

    def write_fifo(self, data: np.ndarray, timeout_ms: int = 5000) -> int:
        if self._session:
            fifo = self._session.fifos["AWG_FIFO"]
            fifo.write(data.astype(np.uint16), timeout_ms=timeout_ms)
            return len(data)
        return 0

    def set_trigger_mode(self, mode: int):
        if self._session:
            self._session.registers["Trigger_Mode"].write(mode)
            self._state.trigger_mode = mode

    def set_trigger_out_enable(self, enabled: bool):
        if self._session:
            self._session.registers["Trigger_Out_Enable"].write(enabled)
            self._state.trigger_out_enable = enabled

    def set_trigger_edge(self, edge: int):
        if self._session:
            self._session.registers["Trigger_Edge"].write(edge)
            self._state.trigger_edge = edge

    def fire_software_trigger(self):
        if self._session:
            self._session.registers["Software_Trigger"].write(True)

    def is_armed(self) -> bool:
        if self._session:
            return self._session.registers["AWG_Armed"].read()
        return False

    def set_frame_count(self, count: int):
        if self._session:
            self._session.registers["AWG_Frame_Count"].write(count)
            self._state.awg_frame_count = count

    def set_stop_behavior(self, behavior: int):
        if self._session:
            self._session.registers["AWG_Stop_Behavior"].write(behavior)
            self._state.awg_stop_behavior = behavior

    def is_complete(self) -> bool:
        if self._session:
            return self._session.registers["AWG_Complete"].read()
        return False

    def get_frames_played(self) -> int:
        if self._session:
            return self._session.registers["AWG_Frames_Played"].read()
        return 0

    def get_state(self) -> HardwareState:
        if self._session:
            for ch in range(NUM_CHANNELS):
                self._state.channels[ch].enabled = \
                    self._session.registers[f"Ch{ch}_Enable"].read()
                self._state.channels[ch].mode = \
                    self._session.registers[f"Ch{ch}_Mode"].read()
                self._state.channels[ch].cw_value = \
                    self._session.registers[f"Ch{ch}_CW_Value"].read()
            self._state.awg_active = self._session.registers["AWG_Active"].read()
            self._state.loop_count = self._session.registers["Loop_Count"].read()
            self._state.fifo_underflow = self._session.registers["FIFO_Underflow"].read()
            self._state.awg_armed = self._session.registers["AWG_Armed"].read()
            self._state.awg_complete = self._session.registers["AWG_Complete"].read()
            self._state.awg_frames_played = self._session.registers["AWG_Frames_Played"].read()
        return self._state

    def get_fifo_depth(self) -> int:
        return 0

    @property
    def is_connected(self) -> bool:
        return self._session is not None
