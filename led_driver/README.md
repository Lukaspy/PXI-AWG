# 8-Channel LED AWG Driver (v3)

Control application for driving a multiwavelength LED source via NI PXI-7853R FPGA.

## Features

- **Per-channel control**: Enable/disable, CW intensity, AWG mode
- **CW mode**: Constant output intensity (0-100%) per channel
- **AWG mode**: Arbitrary waveforms from CSV, streamed to all 8 channels simultaneously
- **Playback modes**: Single-shot, N repetitions, or continuous streaming
- **Triggering**: Immediate, hardware (DIO1 edge), or software trigger
- **Trigger output**: DIO0 asserts during playback for synchronizing capture devices
- **Frame counting**: Precise control over playback length with completion detection
- **100 kHz output rate**: Deterministic FPGA-timed, configurable down to 1 kHz
- **Stop behavior**: Hold last value, return to CW, or return to 0V on completion
- **Mock mode**: Full GUI testing without hardware

## Requirements

```
pip install PyQt5 pyqtgraph numpy
pip install nifpga          # only for real hardware
```

## Quick Start

```bash
# Development mode (no hardware)
python -m led_driver

# With real FPGA hardware
python -m led_driver --fpga path/to/LED_Driver_FPGA.lvbitx
python -m led_driver --fpga path/to/LED_Driver_FPGA.lvbitx --resource RIO0
```

## CSV Waveform Format

8 columns (one per channel), values as intensity percentages (0-100).
Each row is one output frame at the configured sample rate.

```csv
Ch0,Ch1,Ch2,Ch3,Ch4,Ch5,Ch6,Ch7
100,0,0,0,0,0,0,0
50,50,0,0,0,0,0,0
0,100,0,0,0,0,0,0
```

- Header row is optional (auto-detected)
- Missing columns treated as 0%
- Values clamped to 0-100%

## FPGA Architecture (v3)

The FPGA loop runs at 8x the desired output rate using single-element FIFO
reads with a channel counter. At 100 kHz output rate, the loop runs at 800 kHz
(50 ticks of the 40 MHz FPGA clock per iteration).

```
Output_Rate_Ticks = 40_000_000 / (desired_rate_hz × 8)
Default: 50 ticks → 100 kHz output
```

## Hardware Setup

- **PXI chassis**: PXI-1033
- **FPGA card**: PXI-7853R (Virtex-5 LX85, 8 AO, ±10V, 16-bit)
- **Host link**: PCIe-8361 (MXI-Express)
- **Output range**: 0-10V (mapped from ±10V DAC)
- **Trigger out**: DIO0 → BNC → capture device
- **Trigger in**: DIO1 ← BNC ← external trigger source

## File Structure

```
led_driver/
├── __init__.py
├── __main__.py       # Entry point, dark theme
├── hardware.py       # Backend abstraction (Mock / FPGA)
├── waveform.py       # CSV loading, waveform generation, FIFO prep
├── gui.py            # PyQt5 GUI
└── examples/
    └── sweep_3ch.csv # Sample waveform
```
