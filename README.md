# 8-Channel LED AWG Driver

Control application for driving a multiwavelength LED source via NI PXI-7853R FPGA.

## Features

- **Per-channel control**: Enable/disable, CW intensity, AWG mode for each of 8 channels
- **CW mode**: Set constant output intensity (0–100%) per channel
- **AWG mode**: Load arbitrary waveforms from CSV files, streamed to all channels simultaneously
- **100 kHz update rate**: Deterministic FPGA-timed output at up to 100 kHz
- **Waveform preview**: Visual plot of loaded waveforms before playback
- **Test waveforms**: Built-in generator for sine, square, triangle, sawtooth
- **Mock mode**: Full GUI development and testing without hardware

## Requirements

```
pip install PyQt5 pyqtgraph numpy
pip install nifpga          # only for real hardware
```

## Quick Start

```bash
# Development mode (no hardware needed)
python -m led_driver

# With real FPGA hardware
python -m led_driver --fpga path/to/led_driver.lvbitx
python -m led_driver --fpga path/to/led_driver.lvbitx --resource RIO0
```

## CSV Waveform Format

CSV files should have 8 columns (one per channel), with values as intensity
percentages (0–100). Each row is one sample at the configured sample rate.

```csv
Ch0,Ch1,Ch2,Ch3,Ch4,Ch5,Ch6,Ch7
100,0,0,0,0,0,0,0
50,50,0,0,0,0,0,0
0,100,0,0,0,0,0,0
```

- Header row is optional (auto-detected)
- Missing columns are treated as 0%
- Values are clamped to 0–100%

## Hardware Setup

- **PXI chassis**: PXI-1033
- **FPGA card**: PXI-7853R (8 analog outputs, ±10V, 16-bit)
- **Host link**: PCIe-8361 (MXI-Express)
- **Output range**: 0–10V (mapped from the ±10V DAC range)
- **Connections**: AO0–AO7 → BNC cables → LED source modulation inputs

## Architecture

```
┌─────────────────────────────────────────┐
│  Python GUI (PyQt5)                     │
│  ├── Channel controls (CW intensity)    │
│  ├── CSV loading + waveform preview     │
│  └── AWG transport (play/stop/loop)     │
├─────────────────────────────────────────┤
│  nifpga Python package                  │
│  ├── Register read/write                │
│  └── DMA FIFO streaming                 │
├─────────────────────────────────────────┤
│  NI-RIO Driver                          │
├──────────────── cable ──────────────────┤
│  PXI-7853R FPGA                         │
│  ├── Timed loop @ 100 kHz              │
│  ├── Registers: enable, mode, CW value  │
│  ├── DMA FIFO: AWG sample stream        │
│  └── AO0–AO7 → BNC → LEDs              │
└─────────────────────────────────────────┘
```

## File Structure

```
led_driver/
├── __init__.py
├── __main__.py       # Entry point, argument parsing, dark theme
├── hardware.py       # Backend abstraction (MockBackend / FPGABackend)
├── waveform.py       # CSV loading, waveform generation, FIFO prep
├── gui.py            # PyQt5 GUI (channel strips, plots, AWG controls)
└── examples/
    └── sweep_3ch.csv # Sample waveform file
```
