#!/usr/bin/env python3
"""
8-Channel LED AWG Driver (v3)
=============================
Control application for NI PXI-7853R driving a multiwavelength LED source.

Usage:
    python -m led_driver                         # Mock backend (development)
    python -m led_driver --fpga bitfile.lvbitx    # Real FPGA backend
    python -m led_driver --fpga bitfile.lvbitx --resource RIO0

Dependencies:
    pip install PyQt5 pyqtgraph numpy
    pip install nifpga        # only for real FPGA backend
"""

import sys
import argparse
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from led_driver.hardware import MockBackend, FPGABackend
from led_driver.gui import MainWindow


def main():
    parser = argparse.ArgumentParser(
        description="8-Channel LED AWG Driver for NI PXI-7853R")
    parser.add_argument(
        "--fpga", metavar="BITFILE",
        help="Path to .lvbitx bitfile. If omitted, runs in mock mode.")
    parser.add_argument(
        "--resource", default="RIO0",
        help="NI-RIO resource name (default: RIO0)")
    args = parser.parse_args()

    if args.fpga:
        print(f"Using FPGA backend: {args.fpga} on {args.resource}")
        backend = FPGABackend(args.fpga, args.resource)
    else:
        print("Using mock backend (no hardware)")
        backend = MockBackend()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark theme
    palette = app.palette()
    palette.setColor(palette.Window, Qt.darkGray)
    palette.setColor(palette.WindowText, Qt.white)
    palette.setColor(palette.Base, Qt.black)
    palette.setColor(palette.AlternateBase, Qt.darkGray)
    palette.setColor(palette.ToolTipBase, Qt.white)
    palette.setColor(palette.ToolTipText, Qt.white)
    palette.setColor(palette.Text, Qt.white)
    palette.setColor(palette.Button, Qt.darkGray)
    palette.setColor(palette.ButtonText, Qt.white)
    palette.setColor(palette.BrightText, Qt.red)
    palette.setColor(palette.Link, Qt.cyan)
    palette.setColor(palette.Highlight, Qt.cyan)
    palette.setColor(palette.HighlightedText, Qt.black)
    app.setPalette(palette)

    window = MainWindow(backend)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
