# Session handoff — 2026-05-11 (Arch → Ubuntu)

Pickup point for the next Claude Code session, which will run on a fresh **Ubuntu 24.04 LTS** dual-boot on the lab machine (lab machine has the PXI hardware attached). Read this together with `PROJECT_REFERENCE.md`, `fpga_spec_v3.md`, `labview_walkthrough_v3.md`, and `README.md` for full project context.

## State at handoff

### Bitfile recompile — audit PASSED

The recompiled `leddriverfpga_FPGATarget_LEDDriverFPGA_gPdx1Ep2TLE.lvbitx` (same filename, replaced in place) has both prior bugs fixed:

- **`AWG_Frames_Played`** is now `U32` (was `I16`). ✓
- **`AWG_FIFO` depth** is now `NumberOfElements = 65541` (requested 65536, LabVIEW's +5 coercion as expected). ✓ — ~82 ms buffer at 800 kHz, 64× the original.
- VI name: `LED Driver FPGA.vi`. Signature: `30B848EB27BD0F3878E16EF2063B3B62`.
- 43 registers total (37 user + 6 LabVIEW system). All per-channel, trigger, global, and playback registers present with correct types and directions per `fpga_spec_v3.md`.

This bitfile is ready for hardware bring-up. No further LabVIEW work expected unless hardware testing reveals new issues.

### Why dual-boot Ubuntu (and not Arch)

The lab machine was originally EndeavourOS (Arch-based) running kernel 6.19. Investigation this session showed that path is not viable:

- **NI officially supports only kernel 6.8** (per the NI Linux Device Drivers 2025 Q2 compatibility doc and NI forum responses to similar issues).
- Arch's `linux-lts` is at 6.18.23 — still 10 minor versions past NI's ceiling.
- AUR has **no** packages for `ni-rio` / `ni-fpga` / `nikal`. Only stale `ni-daqmx-base-bin` and `ni-visa` exist, neither relevant to R-Series.
- NI's `nipalk` kernel module ships with a **closed-source precompiled binary blob** (`nipalk-bin.o`). DKMS source patches can't relink against blob expectations if kernel internals have drifted — high risk of dead-ending after hours of work.

Decision: dual-boot Ubuntu 24.04 LTS, use HWE kernel 6.8, install NI drivers via apt. User is handling the Ubuntu install themselves.

### Lab machine snapshot (Arch side, pre-install)

For reference if anything matters later:

- Host: `mec107-xps8700` (Dell XPS 8700, Haswell-era)
- Disk: single 1TB SATA (`/dev/sda`) — 2 GB EFI + 929 GB ext4 root, UEFI boot
- 8.8 GB used, 859 GB free
- PCIe-8361 MXI-Express host card visible as TI XIO2000(A) bridge at `04:00.0`
- **PXI-7853R was NOT enumerated** in `lspci` this session — chassis was likely off or cabled-after-boot. PCIe enumerates at boot; chassis must be powered on with cable seated *before* the host boots.

### Project files

`/home/lukas/Documents/PXI-AWG/` on the Arch root partition. From Ubuntu, mount the Arch ext4 partition (or move the project to a shared location, or git-clone from a remote if one exists). The bitfile lives in this directory at the top level.

## Pickup plan (Ubuntu side)

### 1. Hardware: get the PXI-7853R on the PCIe bus

Before installing anything, confirm the chassis is enumerating:

```bash
lspci -tv | grep -A1 -i 'xio2000\|texas instruments'
```

Should show the TI XIO2000 bridge **with a device on the bus downstream** (e.g., `[05]----00.0  National Instruments`). If bus 05 is empty (`[05]--`), the FPGA is not visible — power-cycle the chassis with cable seated, then reboot the host. PCIe is not hotpluggable in this configuration.

**Cable safety:** must be SHC68-68-**RMIO** (P/N 189588-01), not RDIO. RDIO on the MIO connector can blow a fuse on the 7853R.

### 2. Install NI Linux Device Drivers

NI's stack on Ubuntu is installed via apt after a small registration `.deb` adds NI's repo to sources.list.d.

```bash
# Download the latest registration .deb from
#   https://www.ni.com/en/support/downloads/drivers/download.ni-linux-device-drivers.html
# Filename pattern: ni-ubuntu2404-drivers-<release>.deb
# (e.g., ni-ubuntu2404-drivers-2025Q3.deb at time of writing)

sudo apt install ./ni-ubuntu2404-drivers-*.deb
sudo apt update

# Packages needed for the PXI-7853R + Python nifpga workflow:
sudo apt install ni-rseries ni-fpga-interface

# Reboot to load kernel modules + start NI services
sudo reboot
```

Notes:
- `ni-rseries` is the R-Series device driver — required for the PXI-7853R itself.
- `ni-fpga-interface` is the FPGA Interface C API library that the Python `nifpga` package calls into.
- The compatibility doc lists `ni-rio-mxie` as optional — that's for *remote* RIO devices over Ethernet. The PXI-7853R is local PCI (via MXI-Express), so it should not be needed. Skip unless something is missing.

### 3. Verify hardware visible to NI stack

```bash
lsni -v             # should list the PXI-7853R, likely as RIO0
nipkg info ni-rseries
dmesg | grep -i 'nipal\|nirio\|7853'
```

### 4. Set up Python environment and smoke-test

```bash
cd /path/to/PXI-AWG
python3 -m venv .venv
source .venv/bin/activate
pip install PyQt5 pyqtgraph numpy nifpga

# First smoke test:
python -m led_driver \
  --fpga leddriverfpga_FPGATarget_LEDDriverFPGA_gPdx1Ep2TLE.lvbitx \
  --resource RIO0
```

In the GUI: enable Ch0 in CW mode at 50% intensity. Measure AO0 with a multimeter — expect **~5 V** (midpoint of the 0–10 V LED-input range).

**If voltage is way off (e.g., −5 V or 0 V):** the AO data-type assumption in `led_driver/hardware.py` may be wrong. The spec assumes the FPGA exposes AOs as U16 with 0..65535 → −10..+10 V. The actual FPGA AO node may be I16 or Fixed-point — this is the longest-standing unverified item from `PROJECT_REFERENCE.md`. Check `intensity_to_u16` / `u16_to_voltage` and adjust.

### 5. Run the full FPGA testing checklist

`fpga_spec_v3.md` §Testing checklist — 18 items covering registers, CW, full-scale, AWG playback, channel independence, sync, FIFO underflow, trigger in/out, software trigger, edge selection, single-shot frame counting, continuous playback, all three stop behaviors, and re-trigger after completion. This validates everything the v3 spec promises.

## What is NOT yet verified on hardware

(Unchanged from the prior handoff; restated for completeness.)

- Channel counter wraps correctly; AO writes fire only on frame boundaries (scope-level — checklist items 4, 6)
- Trigger edge detection (items 10, 12)
- All three stop behaviors (items 15, 16, 17)
- AO data type and voltage mapping (`intensity_to_u16` / `u16_to_intensity` / `u16_to_voltage` in `led_driver/hardware.py`)
- Calibration workflow with Thorlabs PM16-122 power meter

## Files in this project

- `led_driver/` — Python application (see `PROJECT_REFERENCE.md` §Module details for per-file breakdown)
- `fpga_spec_v3.md` — FPGA spec, register table, FIFO config, pseudocode, testing checklist
- `labview_walkthrough_v3.md` — LabVIEW build guide
- `PROJECT_REFERENCE.md` — full project reference (hardware, FPGA arch, Python modules, design rationale, pending items)
- `README.md` — top-level README
- `led_driver/README.md` — Python-app-focused README
- `leddriverfpga_FPGATarget_LEDDriverFPGA_gPdx1Ep2TLE.lvbitx` — **post-recompile, audit clean** (U32 frames played, 65541 FIFO depth)

## How to brief a fresh Claude Code session on Ubuntu

> "We just installed Ubuntu 24.04 LTS on this lab machine to run the PXI-AWG project. Read `SESSION_STATE.md` and the other markdown files in `/path/to/PXI-AWG/` to get up to speed. The bitfile audit passed. We need to install NI's drivers via apt and smoke-test against the PXI-7853R."
