# LabVIEW FPGA walkthrough: 8-channel LED driver (v3)

This guide assumes everything is installed (LabVIEW 2020, FPGA Module,
NI-RIO/CompactRIO drivers, Xilinx ISE 14.7) and you have zero LabVIEW experience.

---

## Key concepts (2-minute crash course)

LabVIEW programs are called **VIs** (Virtual Instruments). Every VI has two views:

- **Front Panel** — the user interface. Controls (inputs) and indicators (outputs)
  live here. In FPGA programming, front panel controls automatically become
  **registers** that the host PC can read/write via nifpga.

- **Block Diagram** — the actual program logic. You wire blocks together visually.
  Data flows left to right along the wires.

Switch between them with Ctrl+E.

**Data types are color-coded on wires:**
- Orange = floating point
- Blue = integer
- Green = boolean
- Pink = string

**Important habit:** Save frequently. LabVIEW can crash during complex operations.

---

## Step 1: Open LabVIEW and create a project

1. Launch **LabVIEW 2020** from the Start menu.

2. Click **Create Project** (or File > Create Project).

3. Select **Blank Project** and click Finish.

4. A Project Explorer window opens showing:
   ```
   Project: Untitled Project 1
     My Computer
   ```

5. **Save immediately:** File > Save All. Create a folder called
   `LED_Driver_FPGA`. Save as `LED_Driver.lvproj`.

---

## Step 2: Add the FPGA target

1. In Project Explorer, **right-click on "My Computer"**.

2. Select **New > Targets and Devices...**

3. In the dialog:
   - Select **New target or device**
   - Expand **FPGA Target** > **R Series**
   - Select **PXI-7853R**
   - Click **OK**

4. Your Project Explorer should now show:
   ```
   Project: LED_Driver
     My Computer
     FPGA Target (RIO0, PXI-7853R)
       40 MHz Onboard Clock
       IO (expandable)
   ```

5. Save the project (Ctrl+S).

---

## Step 3: Add the DMA FIFO

Do this at the project level before creating the VI.

1. In Project Explorer, **right-click** on "FPGA Target (RIO0, PXI-7853R)".

2. Select **New > FIFO**

3. In the FIFO Properties dialog:
   - **Name:** `AWG_FIFO`
   - **Type:** Host to Target - DMA
   - **Data Type:** U16

4. Click **OK**. The FIFO appears under the FPGA Target in Project Explorer.

5. **Set FIFO depth:** Right-click `AWG_FIFO` in Project Explorer →
   **Properties** → **General** tab → **Requested Number of Elements**.
   Set this to the maximum available value.

6. Save the project.

---

## Step 4: Create the FPGA VI

1. In Project Explorer, **right-click** on "FPGA Target (RIO0, PXI-7853R)".

2. Select **New > VI**

3. Two windows open: Front Panel and Block Diagram.

4. **Save immediately** as `LED_Driver_FPGA.vi` in your project folder.

---

## Step 5: Build the front panel (registers)

Everything on the Front Panel becomes a register accessible from Python.
**Names must match exactly** — the Python code references them by string name.

### How to create each type:

**Boolean control** (for enable/trigger registers):
1. Right-click Front Panel background → Controls palette.
2. Navigate to: **Modern > Boolean > Push Button**
3. Place it. Type the label name. Press Enter.

**U8 numeric control:**
1. Right-click > **Modern > Numeric > Numeric Control**
2. Place it, label it.
3. Right-click the control > **Representation > U8**

**U16 numeric control:**
1. Same as above, but right-click > **Representation > U16**

**U32 numeric indicator** (FPGA writes, host reads):
1. Right-click > **Modern > Numeric > Numeric Indicator**
2. Place it, label it.
3. Right-click > **Representation > U32**

**Boolean indicator:**
1. Right-click > **Modern > Boolean > Round LED**
2. Place it, label it.

### Duplication shortcut:
Create one set (e.g., Ch0_Enable, Ch0_Mode, Ch0_CW_Value), select all three
with a drag box, Ctrl+C, Ctrl+V, then rename the copies.

### Complete list of front panel items:

**Per-channel controls (×8, for channels 0–7):**

| Name pattern | Type | Representation |
|---|---|---|
| `Ch0_Enable` … `Ch7_Enable` | Boolean control (Push Button) | — |
| `Ch0_Mode` … `Ch7_Mode` | Numeric control | U8 |
| `Ch0_CW_Value` … `Ch7_CW_Value` | Numeric control | U16 |

**Global controls:**

| Name | Type | Representation |
|---|---|---|
| `AWG_Active` | Boolean control | — |
| `Output_Rate_Ticks` | Numeric control | U16 |
| `Trigger_Mode` | Numeric control | U8 |
| `Software_Trigger` | Boolean control | — |
| `Trigger_Out_Enable` | Boolean control | — |
| `Trigger_Edge` | Numeric control | U8 |
| `AWG_Frame_Count` | Numeric control | U32 |
| `AWG_Stop_Behavior` | Numeric control | U8 |

**Indicators (FPGA writes, host reads):**

| Name | Type | Representation |
|---|---|---|
| `Loop_Count` | Numeric indicator | U32 |
| `FIFO_Underflow` | Boolean indicator | — |
| `AWG_Armed` | Boolean indicator | — |
| `AWG_Complete` | Boolean indicator | — |
| `AWG_Frames_Played` | Numeric indicator | U32 |

**Total: 37 items.** Take your time, double-check names and data types.

Save the VI (Ctrl+S).

---

## Step 6: Add I/O nodes to the block diagram

Switch to the Block Diagram (Ctrl+E).

### Analog outputs:

1. In Project Explorer, expand **FPGA Target > IO**.
2. Find the **Analog Output** section: AO0 through AO7.
3. **Drag AO0** from Project Explorer onto the Block Diagram.
4. Repeat for **AO1 through AO7**.

### Digital I/O:

5. Find **DIO0** and **DIO1** in the digital I/O section.
6. Drag both onto the Block Diagram.
7. **DIO0** needs to be configured as **Output** (Write):
   Right-click the DIO0 I/O node → select Write direction.
8. **DIO1** needs to be configured as **Input** (Read):
   Right-click the DIO1 I/O node → select Read direction.

---

## Step 7: Build the block diagram — MINIMAL VERSION FIRST

**Start simple.** Build a minimal VI with just CW + AWG (no triggers, no
frame counting). Get this compiling first. Add features in subsequent passes.

### 7a: Create the timed loop

1. Right-click Block Diagram → **Programming > Structures > Timed Loop**
2. Draw a large rectangle — this is your main loop. Make it BIG.
3. Double-click the clock icon (top-left of the loop) to configure:
   - **Source:** 40 MHz Onboard Clock
   - **Period:** 50 (this gives 800 kHz = 40 MHz / 50)

   Or for a simpler start, set period to 400 (100 kHz). You can change
   this later. The channel counter approach works at any rate — it just
   means the output rate will be loop_rate / 8.

### 7b: Create the channel counter

The channel counter is a U8 value that cycles 0, 1, 2, ..., 7, 0, 1, 2, ...
In LabVIEW FPGA, you implement this with a **Feedback Node** (shift register).

1. Inside the timed loop, right-click → **Programming > Numeric > Increment**
   (this adds 1 to a value).

2. Right-click → **Programming > Comparison > Equal?**

3. Right-click → **Programming > Numeric > Quotient & Remainder**
   (alternative: use comparison with 8 and a select/reset)

**Simplest approach for the counter:**

1. Place a **Feedback Node**: Right-click → **Programming > Structures > Feedback Node**
   This creates a node with a left input and right output, carrying a value
   across loop iterations.

2. Wire it like this conceptually:
   ```
   [Feedback output (previous value)] → [Add +1] → [result]
                                                       |
                                         [Compare: result >= 8?]
                                                       |
                                         [Select: if true → 0, else → result]
                                                       |
                                         → [Feedback input (next value)]
   ```

   In LabVIEW blocks:
   - Add an **Increment** function (adds 1)
   - Add a **Greater or Equal?** comparison with a constant of 8
   - Add a **Select** function: True case = constant 0, False case = the incremented value
   - Wire the Select output back to the Feedback Node input

3. Right-click the Feedback Node → **Properties** → set initial value to 0
   and data type to U8.

4. The output of the Select (before it feeds back) is your current
   `channel_counter` value. Branch this wire — you'll use it in several places.

5. The Boolean output of the `>= 8` comparison is your `frame_complete` signal.
   This is True every 8th tick and is what triggers the AO writes.

### 7c: Add the FIFO read

1. Inside the timed loop, right-click → **Programming > Memory & FIFO > Read**
   (or look for "FIFO Read" — it may be under different submenus depending
   on your version).

2. When prompted, select your `AWG_FIFO`.

3. The FIFO Read has:
   - Input: **Number of Elements** — wire a constant of **1** (right-click
     terminal → Create > Constant, type 1)
   - Input: **Timeout** — wire a constant of **0** (don't wait — if no data,
     report timeout immediately)
   - Output: **Data** — a U16 value (the sample for the current channel)
   - Output: **Timed Out** — Boolean, True if FIFO was empty (underflow)

4. Only read when AWG is running: wrap the FIFO read in a **Case Structure**
   wired to a Boolean representing `awg_running` (more on this below).

### 7d: Route FIFO samples to channel buffer registers

You need 8 separate U16 storage registers (one per channel) that hold the
most recent sample for each channel. Use **Feedback Nodes** for these.

1. Create a **Case Structure** wired to the `channel_counter` value.
   This creates a case for each value (0, 1, 2, ..., 7).

2. Add cases 0 through 7. In each case:
   - The FIFO data sample is wired to the appropriate channel's Feedback Node.
   - All other channels' Feedback Nodes pass through unchanged.

**Simpler alternative using Array:** Instead of 8 separate feedback nodes
and a case structure, you can use a single **Array** feedback node:

1. Create a Feedback Node initialized to an array of 8 × U16 zeros.
2. Use **Replace Array Subset** (Programming > Array) to write the new
   sample at index `channel_counter`.
3. The array output carries all 8 channel values.

This is cleaner but requires comfort with LabVIEW arrays.

### 7e: Write AO outputs on frame completion

The AO writes should only happen when `frame_complete` is True (every 8th tick).

1. Create a **Case Structure** wired to the `frame_complete` Boolean.

2. In the **True case** (frame_complete = True):
   For each channel 0–7:
   - Check `Ch{n}_Enable`:
     - If False → wire constant 32768 (0V) to AO{n}
     - If True → check `Ch{n}_Mode`:
       - If 0 (CW) → wire `Ch{n}_CW_Value` to AO{n}
       - If 1 (AWG) → wire `sample_buffer[n]` to AO{n}

3. In the **False case** (frame_complete = False):
   Don't write to AOs (they hold their previous value).
   LabVIEW may require you to wire something — you can wire the same
   values through or use "pass-through" wiring.

### 7f: Wire the Loop_Count indicator

1. Create another Feedback Node (U32, initial value 0).
2. Wire: Feedback output → Increment → Feedback input
3. Also wire the Increment output to the `Loop_Count` indicator terminal.

### 7g: Wire the FIFO_Underflow indicator

1. The "Timed Out" output of the FIFO Read indicates underflow.
2. Wire it to the `FIFO_Underflow` indicator.
3. For latching behavior: OR the current "Timed Out" with a Feedback Node
   of the previous underflow state. Only clear when `AWG_Active` goes False.

### Summary of minimal VI data flow:

```
Each tick of the timed loop (800 kHz):

1. Increment channel_counter (0→7, wrapping)
2. Compute frame_complete = (counter was >= 8 before wrapping)
3. If AWG_Active:
   a. Read 1 element from AWG_FIFO
   b. Store in sample_buffer[channel_counter]
4. If frame_complete:
   a. Write AO0–AO7 based on enable/mode/buffer
5. Increment Loop_Count
```

Save the VI and attempt to compile (Step 9).

---

## Step 8: Add triggers and playback control (SECOND PASS)

Only do this after the minimal version compiles and works.

### 8a: AWG running state machine

Replace the simple `AWG_Active` gating with a state variable `awg_running`
(Boolean Feedback Node):

- `awg_running` starts False
- When `AWG_Active` is True and `Trigger_Mode` is 0 (immediate):
  set `awg_running` = True
- When `AWG_Active` goes False: set `awg_running` = False
- Gate the FIFO read on `awg_running` instead of `AWG_Active`

### 8b: Hardware trigger (Trigger_Mode = 1)

- Read DIO1 each tick
- Use a Feedback Node to store previous DIO1 state
- Detect rising edge: current DIO1 = True AND previous DIO1 = False
- When edge detected and AWG_Armed: set awg_running = True

### 8c: Software trigger (Trigger_Mode = 2)

- When `Software_Trigger` is True and AWG_Armed:
  set awg_running = True, clear Software_Trigger

### 8d: Trigger output

- Wire: `awg_running AND Trigger_Out_Enable` → DIO0

### 8e: Frame counting

- Add another U32 Feedback Node for frames_played
- Increment it on each frame_complete while awg_running
- Compare with AWG_Frame_Count (if > 0): when equal, set
  awg_running = False and AWG_Complete = True
- Wire frames_played to `AWG_Frames_Played` indicator

### 8f: Stop behavior

- On AWG_Complete, apply stop behavior to each AWG channel:
  - 0: hold (no action — last values already written)
  - 1: write Ch{n}_CW_Value to AO{n}
  - 2: write 32768 (0V) to AO{n}

---

## Step 9: Compile

1. Save the VI.

2. Click the **Run** button, OR right-click the FPGA VI in Project Explorer
   and select **Compile**.

3. Select **Local compile server** (uses ISE 14.7).

4. **Wait 30–60 minutes.** Stages shown:
   - Generating intermediate files
   - Synthesizing
   - Translating
   - Mapping
   - Placing and routing
   - Generating bitfile

5. **If compilation succeeds:** Find the `.lvbitx` file in your project
   folder or compilation output directory. **Copy this file somewhere safe.**

6. **If compilation fails**, common issues:
   - **Broken wires** (dashed lines on block diagram): type mismatch or
     unconnected terminals. Fix the wiring.
   - **Timing violation:** logic too complex for the clock period. Try
     increasing `Output_Rate_Ticks` or simplifying the logic.
   - **Resource exceeded:** unlikely for this design but possible if
     something is wired in an unexpected way.

---

## Step 10: Get the bitfile to your Linux machine

1. Find the compiled bitfile:
   ```
   <Project Folder>\FPGA Bitfiles\LED_Driver_FPGA.lvbitx
   ```

2. Copy to your Linux machine (USB drive, SCP, etc.)

3. Run the Python application:
   ```bash
   python -m led_driver --fpga /path/to/LED_Driver_FPGA.lvbitx
   ```

---

## Suggested build order (for trial time management)

**Before activating FPGA trial:**
- Get comfortable with base LabVIEW (your university license)
- Practice wiring, case structures, loops, feedback nodes
- Read NI's R Series examples (Help > Find Examples > Hardware I/O > R Series)

**Day 1 of trial:**
- Create project, add target, add FIFO (Steps 1–3)
- Build entire front panel with all 37 registers (Step 5)
- Add I/O nodes (Step 6)

**Day 2:**
- Build minimal block diagram: channel counter, FIFO read, AO writes (Step 7)
- First compilation attempt

**Day 3:**
- Debug any compilation issues, recompile
- If minimal version works: add trigger logic (Step 8a–8d)

**Day 4–5:**
- Add frame counting and stop behavior (Step 8e–8f)
- Final compilation
- Copy bitfile to Linux and test with Python app

**Day 6–7:**
- Buffer for debugging and re-compilation

---

## Quick reference: useful keyboard shortcuts

| Action                    | Shortcut    |
|---------------------------|-------------|
| Switch Panel/Diagram      | Ctrl+E      |
| Show Controls palette     | Right-click on Front Panel  |
| Show Functions palette    | Right-click on Block Diagram |
| Run VI                    | Ctrl+R      |
| Save                      | Ctrl+S      |
| Undo                      | Ctrl+Z      |
| Create constant           | Right-click terminal > Create > Constant |
| Create control            | Right-click terminal > Create > Control  |
| Create indicator          | Right-click terminal > Create > Indicator |
| Highlight execution       | Lightbulb icon on toolbar |

---

## LabVIEW learning resources (use before trial)

- NI's built-in tutorials: Help > Find Examples > Browse tab
- R Series examples: Help > Find Examples > Hardware I/O > R Series
- LabVIEW FPGA course (included with FPGA Module): Help menu
- YouTube: search "LabVIEW FPGA tutorial beginner"
- NI Community forums: forums.ni.com (search for "R series DMA FIFO example")
