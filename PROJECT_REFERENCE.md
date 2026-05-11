# LED AWG Driver — Complete Project Reference

## Project overview

This is an 8-channel arbitrary waveform generator (AWG) for driving a multiwavelength fiber-coupled LED source used in optical/photobiology experiments. The system uses an NI PXI-7853R FPGA card to generate deterministic, synchronized analog outputs on 8 channels at up to 100 kHz update rate.

The architecture is: **compile FPGA bitfile once in LabVIEW (Windows VM) → run everything from Linux via Python + nifpga**. This is an officially supported NI workflow.

## Hardware

### PXI system
- **Chassis:** NI PXI-1033 (5-slot, built-in MXI-Express controller)
- **FPGA card:** NI PXI-7853R (Virtex-5 LX85, 40 MHz clock, 8 AO, 8 AI, 96 DIO)
- **Also in chassis:** NI PXI-5422 (arbitrary waveform generator, not used by this project)
- **Host link:** PCIe-8361 MXI-Express, connecting PXI chassis to Linux host PC
- **Breakout:** SHC68-68-RMIO cable (P/N 189588-01) → SCB-68A breakout box (not yet located)

### Connectors
- PXI-7853R has 3 VHDCI 68-pin connectors on front panel
- **Connector 0 (MIO)** = analog I/O (AO0-AO7, AI0-AI7) — this is the one we use
- Connectors 1 & 2 = digital I/O only
- **IMPORTANT:** Do NOT use an RDIO cable on the MIO connector — it can blow a fuse. Must use RMIO cable.

### Analog outputs
- 8 channels: AO0 through AO7
- ±10V range, 16-bit resolution
- LED source BNC inputs expect 0-10V, so usable U16 range is 32768 (0V) to 65535 (+10V)

### Digital I/O
- DIO0 = trigger output (asserted high while AWG plays)
- DIO1 = trigger input (external trigger to start AWG)
- 3.3V LVTTL levels

### LED source
- Multiwavelength fiber-coupled LED source
- Each wavelength has its own BNC input (0-10V controls intensity)
- 8 wavelengths, one per channel
- Fiber output to sample

### Power measurement
- Thorlabs PM16-122 (integrating sphere sensor)
- Used for calibrating channel power output

## FPGA architecture (v3)

### Single-element FIFO read constraint
The PXI-7853R DMA FIFO only supports reading **one element per clock cycle** on the FPGA side. You cannot batch-read 8 elements in one tick. This is a hardware limitation of the R-series.

### Channel counter solution
The FPGA loop runs at **8× the desired output rate**. Each tick reads one U16 from the FIFO into a channel buffer register. A channel counter (0-7) determines which buffer gets the sample. Every 8th tick (counter wraps to 0), all 8 AO channels are written simultaneously.

```
Desired output rate: 100 kHz (10 µs per frame)
FIFO read rate:      800 kHz (1.25 µs per sample)
Loop period:         50 ticks at 40 MHz = 1.25 µs
Samples per frame:   8 (one per channel)

Output_Rate_Ticks = 40_000_000 / (desired_rate_hz × 8)
Default: 50 ticks → 100 kHz output
```

### FIFO data format
Samples are interleaved in channel order:
```
Ch0_frame0, Ch1_frame0, ..., Ch7_frame0, Ch0_frame1, Ch1_frame1, ..., Ch7_frame1, ...
```

### Registers (37 total on FPGA front panel)

**Per-channel (×8):**
| Register | Type | Direction | Description |
|---|---|---|---|
| `Ch0_Enable` … `Ch7_Enable` | Bool | Host→Target | Channel on/off |
| `Ch0_Mode` … `Ch7_Mode` | U8 | Host→Target | 0=CW, 1=AWG |
| `Ch0_CW_Value` … `Ch7_CW_Value` | U16 | Host→Target | CW output level |

**Global:**
| Register | Type | Direction | Description |
|---|---|---|---|
| `AWG_Active` | Bool | Host→Target | Enable FIFO consumption |
| `Output_Rate_Ticks` | U16 | Host→Target | Loop period in clock ticks (default 50) |
| `Loop_Count` | U32 | Target→Host | Running tick counter |
| `FIFO_Underflow` | Bool | Target→Host | Latched underflow flag |

**Trigger:**
| Register | Type | Direction | Description |
|---|---|---|---|
| `Trigger_Mode` | U8 | Host→Target | 0=immediate, 1=hardware, 2=software |
| `Software_Trigger` | Bool | Host→Target | Fire software trigger (auto-clears) |
| `Trigger_Out_Enable` | Bool | Host→Target | DIO0 output enable |
| `AWG_Armed` | Bool | Target→Host | Waiting for trigger |
| `Trigger_Edge` | U8 | Host→Target | 0=rising, 1=falling |

**Playback control:**
| Register | Type | Direction | Description |
|---|---|---|---|
| `AWG_Frame_Count` | U32 | Host→Target | Frames to play (0=continuous) |
| `AWG_Frames_Played` | U32 | Target→Host | Progress counter |
| `AWG_Complete` | Bool | Target→Host | Playback finished |
| `AWG_Stop_Behavior` | U8 | Host→Target | 0=hold, 1=CW, 2=zero |

**DMA FIFO:**
| Parameter | Value |
|---|---|
| Name | `AWG_FIFO` |
| Direction | Host-to-Target DMA |
| Data type | U16 |
| Depth | Maximum available (set in Project Explorer) |

### FPGA compilation status
A minimal bitfile has been successfully compiled using LabVIEW 2020 with ISE 14.7 on a Windows VM. The minimal version includes CW + AWG with immediate trigger. Trigger logic, frame counting, and stop behavior were specified but may not be in the compiled bitfile yet — depends on what was included before the trial period expires.

### LabVIEW build details
- LabVIEW 2020 (32-bit) with FPGA Module
- Xilinx ISE 14.7 compilation tools
- **While Loop + Loop Timer** (not Timed Loop — FPGA palette only has Single-Cycle Timed Loop which has no period control)
- Loop Timer: Counter Units = Ticks, constant 50 wired to input terminal
- Channel counter: Feedback Node with increment, compare >=8, select (true→0, false→value)
- Sample buffer: single Feedback Node with U16 array (8 elements), Replace Array Subset by channel_counter
- AO writes gated by frame_complete Case Structure (only write when counter wraps to 0)
- FIFO read: drag AWG_FIFO from project explorer, wire timeout=0, reads 1 element per tick
- While Loop stop terminal: wired to False constant
- Used Local Variables extensively for accessing front panel controls inside nested structures

### ISE 14.7 Windows 10 fixes applied
1. Replaced `libPortability.dll` from `xilinx-ise-win10-hang-hotfix.zip` in both `/nt/` and `/nt64/` directories
2. Installed Visual C++ 2008 Redistributable (x86 and x64) from Microsoft

## Voltage / intensity mapping

```
DAC range:     ±10V → U16 0..65535
LED range:     0-10V → U16 32768..65535 (upper half)
User-facing:   0-100% intensity

intensity_to_u16(pct):  U16 = 32768 + pct/100 * (65535 - 32768)
u16_to_intensity(u16):  pct = (u16 - 32768) / (65535 - 32768) * 100
u16_to_voltage(u16):    V = -10 + (u16 / 65535) * 20
```

**Note:** The actual AO data type on the FPGA (U16 vs I16 vs FXP) has not been verified on hardware yet. The voltage mapping may need adjustment based on testing.

## Python application structure

```
led_driver/
├── __init__.py          # Empty
├── __main__.py          # Entry point, arg parsing, dark theme
├── hardware.py          # Backend abstraction (MockBackend / FPGABackend)
├── waveform.py          # CSV loading, interleaving, FIFO data prep
├── gui.py               # Main window with Driver + Editor tabs
├── editor.py            # Waveform editor widget
├── config.py            # Persistent config, wavelength-to-color conversion
├── settings.py          # Channel wavelength configuration dialog
├── calibration.py       # Power calibration engine
├── cal_dialog.py        # Calibration data entry dialog
└── examples/
    └── sweep_3ch.csv    # Example waveform
```

### Dependencies
```
pip install PyQt5 pyqtgraph numpy
pip install nifpga          # only for real FPGA backend
```

### Running
```bash
python -m led_driver                                    # Mock mode
python -m led_driver --fpga /path/to/bitfile.lvbitx     # Real hardware
python -m led_driver --fpga bitfile.lvbitx --resource RIO0
```

## Module details

### hardware.py
Abstract `HardwareBackend` with two implementations:

**MockBackend** — simulates the FPGA in a background thread at 1 kHz. Implements channel counter architecture (increments loop_count by 8 per frame). Has `set_output_callback(callback)` for monitoring and `clear_fifo()` for resetting.

**FPGABackend** — real nifpga interface. Opens a `Session(bitfile, resource)`, reads/writes registers by exact string name, writes to `AWG_FIFO` via `session.fifos["AWG_FIFO"]`. `get_state()` reads all registers from FPGA into local `HardwareState` snapshot.

Key functions:
- `output_rate_to_ticks(rate_hz)` — converts Hz to Output_Rate_Ticks (divides by 8 for channel counter)
- `ticks_to_output_rate(ticks)` — inverse
- `intensity_to_u16(pct)` / `u16_to_intensity(u16)` — bidirectional mapping

### waveform.py
`WaveformEngine` — loads CSV waveforms and prepares interleaved FIFO data.

CSV format: 8 columns (Ch0-Ch7), values as intensity % (0-100), one row per frame. Optional header row (auto-detected). Supports metadata comment lines at top:
```
# sample_rate=100000
# duration_ms=10.000
# num_samples=1000
Ch0,Ch1,...,Ch7
```

Key methods:
- `load_csv(filepath)` → `WaveformInfo` (detects metadata sample_rate)
- `get_interleaved_u16(cal_manager=None)` → flat U16 array for FIFO (applies calibration if provided)
- `get_repeated_interleaved_u16(reps, cal_manager=None)` → tiled for N-rep playback
- `generate_test_waveform(type, freq, cycles, channels)` → synthetic waveforms
- `get_channel_data(ch)` / `get_time_axis()` — for plotting

### gui.py
`MainWindow` with `QTabWidget` containing two tabs:

**Tab 1: Driver** — channel strips (2×4 grid), waveform preview plot, AWG controls (load CSV, rate, playback mode, triggers, transport), status bar with loop count and FIFO depth. Settings and Calibrate buttons in top bar.

**Tab 2: Waveform Editor** — interactive editor widget (see editor.py below).

`ChannelStrip` — per-channel control: enable toggle (ON/OFF button with channel color), mode selector (CW/AWG), intensity slider (0-100% in 0.1% steps), voltage readout. Supports dynamic color/label updates via `update_appearance()`.

`AWGStreamThread` — background QThread that chunks data to the FIFO. Reports progress %.

Playback modes: single shot, N repetitions, continuous. Frame count calculated and sent to FPGA. Stop behavior: hold/CW/zero.

Trigger modes: immediate, hardware (DIO1 with edge selection), software (fire button).

Status polling at 250ms: reads loop count, FIFO depth, armed/running/complete state.

Calibration correction applied to both CW intensity changes and AWG FIFO data.

### editor.py
`WaveformEditor` — full waveform construction tool.

Features:
- Configurable duration (ms) and sample rate (Hz) with auto-resample
- Per-channel visibility toggles with colored buttons
- Time axis in milliseconds, cursor readout showing time + sample index + channel value
- Draggable `LinearRegionItem` for range selection on plot
- Function application to selected range:
  - Constant, linear ramp, sine wave, square wave, exponential, gaussian pulse, polynomial
  - Preview (dashed white overlay) before committing
- Undo/redo stack (50 states) with Ctrl+Z/Ctrl+Shift+Z
- Copy channel to channel, clear active channel
- Import/export CSV (with sample_rate metadata preservation)
- "Send to AWG driver" button → emits `waveform_ready` signal → MainWindow receives data, populates waveform engine, switches to Driver tab

`FunctionPanel` — QGroupBox with function selector, parameter inputs (stacked widget per function type), range spinboxes, preview/apply buttons.

`ChannelToggle` — checkbox + colored button for visibility and selection.

### config.py
`AppConfig` — persistent channel configuration saved as JSON at `~/.config/led_driver/config.json`.

Each channel can optionally have a wavelength (nm) and custom label. When wavelength is set, the channel color is derived from the visible spectrum.

`wavelength_to_rgb(nm)` — piecewise linear approximation of CIE visible spectrum. Handles UV (<380nm) and IR (>780nm) with sensible fallback colors. Minimum brightness enforcement for dark UI backgrounds.

Default colors (when no wavelength set): red, green, blue, orange, purple, cyan, magenta, lime.

### settings.py
`ChannelSettingsDialog` — per-channel configuration rows with:
- Enable wavelength checkbox
- Wavelength spinner (200-1100 nm)
- Live color swatch preview
- Optional custom label field
- "Clear all" preset

### calibration.py
`CalibrationManager` with per-channel `ChannelCalibration`.

Each channel stores measured (drive%, power_mW) points. Minimum 2 points for calibration.

Two correction modes:
1. **Linearity correction** — inverts each channel's transfer function via `np.interp` so command% maps to proportional optical power
2. **Power equalization** — caps all channels to the weakest channel's max power so 100% = same power everywhere

Key methods:
- `correct_intensity(channel, command_pct)` → corrected drive% for CW
- `correct_waveform_vectorized(data)` → corrected (N,8) array for AWG
- `save(filepath)` / `load(filepath)` — JSON persistence at `~/.config/led_driver/calibration.json`

### cal_dialog.py
`CalibrationDialog` — data entry for power measurements.

Features:
- Channel selector (uses configured wavelength labels)
- Table for (drive%, power_mW) measurement points
- "Add standard points" quick-fill (0, 25, 50, 75, 100%)
- Live correction curve plot: measured (gray dashed), corrected (green), ideal linear (blue dotted)
- Scatter plot of measurement points
- Summary showing equalized max power and limiting channel
- Enable/disable and equalize toggles
- Per-channel save/clear

## Linux host requirements

To run the Python app with real hardware:
1. NI-RIO for Linux (kernel driver)
2. NI PXI Platform Services (`ni-pxiplatformservices`)
3. NI R Series Multifunction RIO driver
4. `pip install nifpga`
5. Verify hardware visible: `lsni -v`

## Pending / TODO items

### Hardware
- [ ] Find or purchase SHC68-68-RMIO cable + SCB-68A breakout box
- [ ] Install NI-RIO Linux drivers on host machine
- [ ] First hardware test: CW mode, Ch0 at 50%, measure 5V with multimeter
- [ ] Determine actual AO data type (U16 vs I16 vs FXP) — affects voltage mapping
- [ ] Wire DIO0/DIO1 to BNC for trigger I/O

### FPGA
- [ ] Verify compiled bitfile has all features (triggers, frame counting, stop behavior) or recompile
- [ ] FPGA Module trial period management
- [ ] Run full testing checklist from spec

### Python application
- [ ] Test with real FPGA hardware end-to-end
- [ ] Verify waveform.py load_csv metadata parsing handles edge cases
- [ ] Test calibration workflow with power meter
- [ ] Consider adding Digilent WaveForms integration for capture-side triggering
- [ ] Consider GPIB oscilloscope integration for automated measurement

### Future ideas discussed
- Automated measurement script: PXI drives LEDs → scope captures response via GPIB → all triggered from DIO0
- Digilent Analog Discovery 2 integration (pydwf or dwfpy packages)
- GPIB scope control via pyvisa for automated data collection

## Key design decisions and rationale

1. **FPGA bitfile compiled once, run from Python** — avoids needing LabVIEW at runtime, allows Linux host
2. **Channel counter architecture** — forced by single-element FIFO read hardware limitation
3. **Interleaved FIFO format** — simplifies FPGA (just a rotating counter), all channels consume slots even if disabled (maintains alignment)
4. **Output_Rate_Ticks register** — allows changing sample rate without recompiling FPGA
5. **Mock backend** — enables full GUI development and testing without PXI hardware
6. **Calibration as optional post-processing** — correction applied in Python before FIFO write, FPGA stays simple
7. **Wavelength-to-color in config** — makes multi-wavelength experiments visually intuitive
8. **CSV metadata comments** — preserves sample rate across save/load without breaking CSV compatibility with other tools

## Reference documents

The following specification and guide documents exist (produced during this project):
- `fpga_spec_v3.md` — complete FPGA specification with pseudocode and testing checklist
- `labview_walkthrough_v3.md` — step-by-step LabVIEW FPGA build guide for beginners
