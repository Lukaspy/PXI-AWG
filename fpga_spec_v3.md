# FPGA VI specification: 8-channel LED AWG driver (v3)

## Target hardware

- **FPGA card:** NI PXI-7853R (Virtex-5 LX85 FPGA, 40 MHz clock)
- **Chassis:** PXI-1033
- **Host link:** PCIe-8361 (MXI-Express)
- **Analog outputs used:** AO0 through AO7 (±10 V, 16-bit)

## Purpose

This bitfile provides deterministic, synchronized analog output on 8 channels at up to 100 kHz update rate. Each channel drives one LED wavelength in a multiwavelength fiber-coupled LED source via BNC. The host PC controls the FPGA through registers and a DMA FIFO using the `nifpga` Python package.

## Operating modes (per channel)

Each channel operates independently in one of three states:

1. **Off** — output held at 0 V
2. **CW (continuous wave)** — output held at a constant voltage set by the host
3. **AWG (arbitrary waveform generation)** — output follows sample data streamed from the host via DMA FIFO

---

## Architecture overview

The PXI-7853R DMA FIFO supports only single-element reads per clock cycle on the FPGA side. To output synchronized data on all 8 channels, the FPGA uses a **channel counter** approach:

- The timed loop runs at **8× the desired output rate** (e.g., 800 kHz for 100 kHz output)
- Each tick, one U16 sample is read from the FIFO and stored in a channel buffer register
- A channel counter (0–7) determines which buffer register receives the sample
- Every 8th tick (when the counter wraps from 7 back to 0), all 8 AO channels are written simultaneously from the buffer registers
- This guarantees synchronized output across all channels

```
Desired output rate: 100 kHz (10 µs per frame)
FIFO read rate:      800 kHz (1.25 µs per sample)
Loop period:         50 ticks at 40 MHz = 1.25 µs
Samples per frame:   8 (one per channel)
```

---

## Registers

All registers are accessible from the host via `nifpga`. Use the exact names below so the Python application can address them.

### Per-channel registers (×8, channels 0–7)

| Register name | Data type | Direction | Description |
|---|---|---|---|
| `Ch0_Enable` … `Ch7_Enable` | Bool | Host-to-Target | Channel on/off. When False, output is 0 V regardless of mode. |
| `Ch0_Mode` … `Ch7_Mode` | U8 | Host-to-Target | 0 = CW, 1 = AWG |
| `Ch0_CW_Value` … `Ch7_CW_Value` | U16 | Host-to-Target | CW output level. 0 = minimum voltage, 65535 = maximum voltage. Host software maps user-facing intensity (e.g., 0–100%) to this range. |

That gives 24 registers total (3 per channel × 8 channels).

### Global registers

| Register name | Data type | Direction | Description |
|---|---|---|---|
| `AWG_Active` | Bool | Host-to-Target | When True, the FPGA consumes samples from the FIFO. When False, FIFO consumption is paused (channels in AWG mode hold their last value). |
| `Output_Rate_Ticks` | U16 | Host-to-Target | Period between FIFO reads, in FPGA clock ticks. The effective output rate is `40 MHz / (Output_Rate_Ticks × 8)`. Default: 50 ticks → 800 kHz FIFO rate → 100 kHz output rate. |
| `Loop_Count` | U32 | Target-to-Host | Running count of completed loop iterations (at the FIFO read rate, not the output rate). Host can read this to verify the FPGA is running. |
| `FIFO_Underflow` | Bool | Target-to-Host | Latched True if the FIFO runs empty during AWG playback. Host reads and clears this to detect buffer underruns. |

### Trigger registers

| Register name | Data type | Direction | Description |
|---|---|---|---|
| `Trigger_Mode` | U8 | Host-to-Target | 0 = immediate (AWG starts when `AWG_Active` is set), 1 = hardware trigger (wait for edge on DIO1), 2 = software trigger (wait for `Software_Trigger`). Default: 0. |
| `Software_Trigger` | Bool | Host-to-Target | Write True to fire a software trigger when `Trigger_Mode` = 2. Auto-clears after triggering. |
| `Trigger_Out_Enable` | Bool | Host-to-Target | When True, DIO0 goes high while AWG is actively outputting. Use this to trigger external capture devices. Default: True. |
| `AWG_Armed` | Bool | Target-to-Host | True when FPGA is waiting for a trigger (modes 1 and 2). Goes False once triggered. Host can poll this to confirm the FPGA is ready. |
| `Trigger_Edge` | U8 | Host-to-Target | 0 = rising edge (default), 1 = falling edge. Applies to hardware trigger input on DIO1. |

### Playback control registers

| Register name | Data type | Direction | Description |
|---|---|---|---|
| `AWG_Frame_Count` | U32 | Host-to-Target | Number of frames to play. One frame = 8 interleaved samples (one per channel). Set to 0 for continuous/indefinite playback (host manages stopping). |
| `AWG_Frames_Played` | U32 | Target-to-Host | Running count of frames played since playback started. Host can read this for progress monitoring. |
| `AWG_Complete` | Bool | Target-to-Host | Set True by the FPGA when `AWG_Frames_Played` reaches `AWG_Frame_Count` (and `AWG_Frame_Count` > 0). At completion, the FPGA stops consuming the FIFO and de-asserts trigger out. |
| `AWG_Stop_Behavior` | U8 | Host-to-Target | What happens to AWG channel outputs when playback completes: 0 = hold last value (default), 1 = return to CW value, 2 = return to 0V. |

### Digital I/O allocation

| DIO pin | Function | Direction | Description |
|---|---|---|---|
| DIO0 | Trigger out | Output | Asserted high while AWG playback is active. Connect via BNC to capture device external trigger input. |
| DIO1 | Trigger in | Input | Accepts external trigger signal. Rising or falling edge (configurable) starts AWG playback when `Trigger_Mode` = 1. |
| DIO2–DIO7 | Reserved | — | Available for future use. |

**Electrical notes:** The PXI-7853R DIO lines are 3.3V LVTTL. For interfacing with 5V BNC signals, a simple voltage divider or level shifter may be needed on the trigger input. The trigger output can drive most 3.3V-compatible inputs directly. For 50Ω BNC lines, consider adding a series resistor for impedance matching.

---

## DMA FIFO

### Configuration

| Parameter | Value |
|---|---|
| FIFO name | `AWG_FIFO` |
| Direction | Host-to-Target DMA |
| Data type | U16 |
| Depth | Set via Project Explorer: right-click `AWG_FIFO` under FPGA Target → Properties → General → Requested Number of Elements. Use maximum available. |

### Sample interleaving

Samples are interleaved across all 8 channels in channel order. Each "frame" consists of 8 consecutive U16 values in the FIFO:

```
FIFO contents (sequential U16 values):
  Ch0_frame0, Ch1_frame0, Ch2_frame0, ..., Ch7_frame0,
  Ch0_frame1, Ch1_frame1, Ch2_frame1, ..., Ch7_frame1,
  ...
```

The FPGA reads **one element per tick** and uses a channel counter (0–7) to route each sample to the correct buffer register. After 8 reads (one complete frame), all AO channels are written simultaneously.

Channels in CW or Off mode still consume their FIFO slot (the value is discarded) to keep alignment. This simplifies both the FPGA logic and the host-side waveform generation.

### Throughput

- 100 kHz output × 8 channels × 2 bytes = 1.6 MB/s
- FIFO read rate: 800 kHz × 2 bytes = 1.6 MB/s
- MXI-Express throughput exceeds 100 MB/s
- No bandwidth concerns

---

## FPGA loop logic (pseudocode)

This describes what the timed loop on the FPGA does each iteration. The loop runs at 8× the desired output rate.

```
configure timed loop period = Output_Rate_Ticks (default 50 FPGA clock ticks)

state awg_running = False
state prev_dio1 = False
state channel_counter = 0          (U8, cycles 0–7)
state sample_buffer[0..7] = {0}    (8 × U16 registers)

loop forever:
    Loop_Count += 1

    // === TRIGGER LOGIC ===
    // (runs every tick, but trigger events only matter when not already running)

    if AWG_Active and not awg_running and not AWG_Complete:
        if Trigger_Mode == 0:                    // Immediate
            awg_running = True
            AWG_Frames_Played = 0
            channel_counter = 0
        else if Trigger_Mode == 1:               // Hardware trigger
            AWG_Armed = True
            current_dio1 = read DIO1
            if Trigger_Edge == 0:                // Rising edge
                if current_dio1 and not prev_dio1:
                    awg_running = True
                    AWG_Armed = False
                    AWG_Frames_Played = 0
                    channel_counter = 0
            else:                                // Falling edge
                if not current_dio1 and prev_dio1:
                    awg_running = True
                    AWG_Armed = False
                    AWG_Frames_Played = 0
                    channel_counter = 0
            prev_dio1 = current_dio1
        else if Trigger_Mode == 2:               // Software trigger
            AWG_Armed = True
            if Software_Trigger:
                awg_running = True
                AWG_Armed = False
                AWG_Frames_Played = 0
                channel_counter = 0
                Software_Trigger = False         // Auto-clear

    if not AWG_Active:
        awg_running = False
        AWG_Armed = False
        AWG_Complete = False                     // Reset on deactivation
        channel_counter = 0

    // === TRIGGER OUTPUT ===

    if Trigger_Out_Enable:
        DIO0 = awg_running
    else:
        DIO0 = False

    // === FIFO READ (one element per tick) ===

    if awg_running:
        success = read 1 element from AWG_FIFO → sample
        if not success:
            FIFO_Underflow = True
            // hold previous sample_buffer values
        else:
            sample_buffer[channel_counter] = sample

        channel_counter += 1

        if channel_counter >= 8:
            channel_counter = 0
            AWG_Frames_Played += 1

            // --- Write all 8 AO outputs simultaneously ---
            for ch = 0 to 7:
                if not Ch{ch}_Enable:
                    AO{ch} = 32768               // 0V
                else if Ch{ch}_Mode == 0:        // CW mode
                    AO{ch} = Ch{ch}_CW_Value
                else:                            // AWG mode
                    AO{ch} = sample_buffer[ch]

            // --- Check for playback completion ---
            if AWG_Frame_Count > 0 and AWG_Frames_Played >= AWG_Frame_Count:
                awg_running = False
                AWG_Complete = True
                // Apply stop behavior
                for ch = 0 to 7:
                    if Ch{ch}_Enable and Ch{ch}_Mode == 1:
                        if AWG_Stop_Behavior == 1:
                            AO{ch} = Ch{ch}_CW_Value
                        else if AWG_Stop_Behavior == 2:
                            AO{ch} = 32768       // 0V
                        // else: hold (already written above)

    // === CW-ONLY OUTPUT (when AWG is not running) ===
    // When AWG is not running, CW channels still need updating.
    // This happens on every tick but only affects CW-mode channels.

    if not awg_running and not AWG_Complete:
        for ch = 0 to 7:
            if not Ch{ch}_Enable:
                AO{ch} = 32768                   // 0V
            else if Ch{ch}_Mode == 0:            // CW mode
                AO{ch} = Ch{ch}_CW_Value
```

### Timing summary

| Desired output rate | Output_Rate_Ticks | Loop rate | FIFO read rate |
|---|---|---|---|
| 100 kHz | 50 | 800 kHz | 800 kHz |
| 50 kHz | 100 | 400 kHz | 400 kHz |
| 10 kHz | 500 | 80 kHz | 80 kHz |
| 1 kHz | 5000 | 8 kHz | 8 kHz |

The host sets `Output_Rate_Ticks` and can calculate: `output_rate = 40_000_000 / (Output_Rate_Ticks × 8)`

---

## LabVIEW FPGA implementation notes

### Project setup

1. Create a new LabVIEW project
2. Add the PXI-7853R as an FPGA target
3. Create a new FPGA VI under the target

### Key LabVIEW FPGA constructs to use

- **Timed loop:** Use a standard timed loop (not single-cycle) with period wired from `Output_Rate_Ticks` register or set to a constant of 50.
- **FPGA I/O nodes:** Drag AO0–AO7 and DIO0/DIO1 from the project tree into the block diagram.
- **Controls as registers:** Front panel controls/indicators automatically become host-accessible registers. Name them exactly as specified in the register table above.
- **DMA FIFO:** Add via Project Explorer → FPGA Target → right-click → New → FIFO. Configure as Host-to-Target DMA, U16. Set depth via right-click → Properties → General → Requested Number of Elements.
- **FIFO read:** Use the FIFO read node inside the loop. Read **1 element per iteration** (not 8). The "timed out" output indicates underflow.
- **Channel counter:** Use a **feedback node** (or shift register) initialized to 0. Increment each tick. When it reaches 8, reset to 0.
- **Sample buffers:** Use 8 separate **feedback nodes** (U16) to hold the sample for each channel. Write to the appropriate one based on the channel counter using a Case Structure.
- **AO write gating:** Only write to AO0–AO7 when channel_counter wraps to 0 (i.e., a complete frame has been read). Use a Case Structure on `channel_counter == 0` after the increment/wrap.

### What NOT to do

- Do not use a while loop with a software-timed wait — use a hardware-timed loop for deterministic behavior.
- Do not put separate timed loops for each channel — use one loop that handles all 8 channels to guarantee synchronization.
- Do not use floating-point math on the FPGA — keep everything in U16 integer space. The host Python application handles all scaling.
- Do not attempt to read more than 1 element from the DMA FIFO per tick — the R-series hardware does not support batch reads.

---

## Compilation

1. Right-click the FPGA VI → Compile
2. Compilation takes 30–60 minutes (Xilinx synthesis, place-and-route)
3. Output: a `.lvbitx` file
4. Save this file — it is the only artifact needed at runtime
5. The Python host application references this file path when opening an `nifpga` session

---

## Host-side interface (for reference)

The Python application will interface with these registers and FIFO using code like:

```python
from nifpga import Session

BITFILE = "path/to/led_driver.lvbitx"
RESOURCE = "RIO0"  # or whatever the 7853R enumerates as

with Session(bitfile=BITFILE, resource=RESOURCE) as session:
    # Enable channel 0 in CW mode at 50% intensity
    session.registers["Ch0_Enable"].write(True)
    session.registers["Ch0_Mode"].write(0)        # CW
    session.registers["Ch0_CW_Value"].write(32768) # midpoint

    # --- Single-shot AWG playback with hardware trigger ---
    num_samples_per_channel = 10000
    session.registers["AWG_Frame_Count"].write(num_samples_per_channel)
    session.registers["AWG_Stop_Behavior"].write(1)  # Return to CW after
    session.registers["Trigger_Mode"].write(1)       # Hardware trigger
    session.registers["Trigger_Out_Enable"].write(True)
    fifo = session.fifos["AWG_FIFO"]
    fifo.write(waveform_data, timeout_ms=5000)       # Interleaved U16 data
    session.registers["AWG_Active"].write(True)      # Arms the FPGA
    # FPGA waits for rising edge on DIO1...
    # When triggered: AWG plays exactly num_samples_per_channel frames,
    # then stops, de-asserts DIO0, and returns channels to CW values.

    # Poll for completion
    while not session.registers["AWG_Complete"].read():
        time.sleep(0.001)
    print("Playback complete!")

    # --- N repetitions ---
    num_reps = 5
    total_frames = num_samples_per_channel * num_reps
    session.registers["AWG_Frame_Count"].write(total_frames)
    for _ in range(num_reps):
        fifo.write(waveform_data, timeout_ms=5000)

    # --- Continuous streaming (frame count = 0) ---
    session.registers["AWG_Frame_Count"].write(0)    # Indefinite
    session.registers["AWG_Active"].write(True)
    # Host manages stopping via AWG_Active = False
```

This is provided for context so the LabVIEW developer understands how the registers and FIFO will be accessed from the host side.

---

## Voltage mapping

The PXI-7853R analog outputs are ±10 V with 16-bit resolution. The mapping from U16 register/FIFO value to output voltage is:

| U16 value | Output voltage |
|---|---|
| 0 | −10.0 V |
| 32768 | 0.0 V |
| 65535 | +10.0 V |

The host Python application must determine the appropriate voltage range for the LED source BNC inputs and map user-facing intensity values (e.g., 0–100%) to the correct U16 range. If the LED source expects 0–5 V, the usable U16 range would be 32768–49152.

---

## Testing checklist

After compilation and deployment, verify the following:

1. **Registers respond:** Write to `Ch0_Enable`, read back, confirm it changed
2. **CW output:** Enable Ch0 in CW mode, set `Ch0_CW_Value` to 32768, measure 0 V on AO0 with a multimeter
3. **Full scale:** Set `Ch0_CW_Value` to 65535, confirm +10 V on AO0
4. **AWG playback:** Write a known waveform (e.g., 1 kHz sine) to the FIFO, enable AWG mode (immediate trigger), verify on oscilloscope
5. **Channel independence:** Enable channels individually, confirm no crosstalk
6. **Synchronization:** Put all channels in AWG mode with the same waveform, verify simultaneous output on a multi-channel scope
7. **FIFO underflow:** Start AWG without writing data, confirm `FIFO_Underflow` flag sets
8. **Loop rate:** Read `Loop_Count` at known intervals, confirm it increments at the expected rate (should be 8× the output rate)
9. **Trigger out:** Enable `Trigger_Out_Enable`, start AWG in immediate mode, confirm DIO0 goes high on oscilloscope. Confirm DIO0 goes low when AWG stops.
10. **Hardware trigger:** Set `Trigger_Mode` to 1, arm AWG, confirm `AWG_Armed` reads True. Apply 3.3V pulse to DIO1 — confirm AWG starts and DIO0 asserts. Measure delay between trigger-in edge and first AO transition on scope.
11. **Software trigger:** Set `Trigger_Mode` to 2, arm AWG, confirm `AWG_Armed` reads True. Write `Software_Trigger` = True — confirm AWG starts.
12. **Trigger edge:** Repeat hardware trigger test with `Trigger_Edge` = 1 (falling edge), confirm it triggers on falling edge only.
13. **Single-shot playback:** Set `AWG_Frame_Count` to 100, load 800 samples (100 frames × 8 channels) into FIFO, start AWG. Confirm `AWG_Complete` goes True after exactly 100 frames. Confirm DIO0 de-asserts. Confirm `AWG_Frames_Played` reads 100.
14. **Continuous playback:** Set `AWG_Frame_Count` to 0, start AWG. Confirm it plays indefinitely until `AWG_Active` is set False. Confirm `AWG_Complete` stays False.
15. **Stop behavior — hold:** Set `AWG_Stop_Behavior` to 0, run single-shot. After completion, measure AO — should hold the last waveform value.
16. **Stop behavior — CW:** Set `AWG_Stop_Behavior` to 1, set a known CW value, run single-shot. After completion, confirm AO returns to the CW value.
17. **Stop behavior — zero:** Set `AWG_Stop_Behavior` to 2, run single-shot. After completion, confirm AO returns to 0V (U16 32768).
18. **Re-trigger after completion:** After single-shot completes, set `AWG_Active` False then True again. Confirm FPGA re-arms and can be triggered for another playback.
