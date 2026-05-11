"""
Calibration dialog for entering power measurements.
"""

import numpy as np
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QPushButton, QLabel, QDoubleSpinBox, QCheckBox, QComboBox,
    QDialogButtonBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFileDialog,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

import pyqtgraph as pg

from .hardware import NUM_CHANNELS
from .config import AppConfig, DEFAULT_COLORS
from .calibration import CalibrationManager, ChannelCalibration


class CalibrationDialog(QDialog):
    """Dialog for entering and viewing power calibration data."""

    def __init__(self, cal_manager: CalibrationManager, config: AppConfig, parent=None):
        super().__init__(parent)
        self.cal = cal_manager
        self.config = config
        self.setWindowTitle("Power Calibration")
        self.setMinimumSize(750, 600)
        self._setup_ui()
        self._refresh_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()

        # Info
        info = QLabel(
            "Enter measured optical power at different drive levels for each channel. "
            "Use a power meter to measure the output at the fiber tip. "
            "Minimum 2 points per channel (0% and 100%). More points improve linearity correction.")
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 6px;")
        layout.addWidget(info)

        # Enable/equalize controls
        toggle_row = QHBoxLayout()
        self.enable_check = QCheckBox("Enable calibration correction")
        self.enable_check.setChecked(self.cal.enabled)
        self.enable_check.setStyleSheet("font-weight: bold;")
        toggle_row.addWidget(self.enable_check)

        self.equalize_check = QCheckBox("Equalize power across channels")
        self.equalize_check.setChecked(self.cal.equalize)
        self.equalize_check.setToolTip(
            "When enabled, 100% command produces the same optical power\n"
            "on all channels (limited by the weakest channel).")
        toggle_row.addWidget(self.equalize_check)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        # Channel selector
        ch_row = QHBoxLayout()
        ch_row.addWidget(QLabel("Channel:"))
        self.ch_combo = QComboBox()
        colors = self.config.get_all_colors()
        labels = self.config.get_all_labels()
        for i in range(NUM_CHANNELS):
            self.ch_combo.addItem(f"{labels[i]}")
        self.ch_combo.currentIndexChanged.connect(self._on_channel_changed)
        ch_row.addWidget(self.ch_combo)

        self.cal_status_label = QLabel("")
        self.cal_status_label.setStyleSheet("color: gray; font-size: 11px;")
        ch_row.addWidget(self.cal_status_label, stretch=1)
        layout.addLayout(ch_row)

        # Main content: table + plot side by side
        content = QHBoxLayout()

        # Measurement table
        table_group = QGroupBox("Measurement points")
        table_layout = QVBoxLayout()

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Drive %", "Power (mW)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumWidth(220)
        table_layout.addWidget(self.table)

        # Add/remove point buttons
        point_btns = QHBoxLayout()
        add_btn = QPushButton("Add point")
        add_btn.clicked.connect(self._on_add_point)
        point_btns.addWidget(add_btn)

        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._on_remove_point)
        point_btns.addWidget(remove_btn)
        table_layout.addLayout(point_btns)

        # Quick-fill button
        quick_row = QHBoxLayout()
        quick_btn = QPushButton("Add standard points (0, 25, 50, 75, 100%)")
        quick_btn.clicked.connect(self._on_quick_fill)
        quick_row.addWidget(quick_btn)
        table_layout.addLayout(quick_row)

        # Apply table edits
        save_ch_btn = QPushButton("Save channel data")
        save_ch_btn.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; "
            "border: none; border-radius: 4px; font-weight: bold; padding: 6px; }"
            "QPushButton:hover { background-color: #2ecc71; }")
        save_ch_btn.clicked.connect(self._on_save_channel)
        table_layout.addWidget(save_ch_btn)

        clear_ch_btn = QPushButton("Clear channel")
        clear_ch_btn.clicked.connect(self._on_clear_channel)
        table_layout.addWidget(clear_ch_btn)

        table_group.setLayout(table_layout)
        content.addWidget(table_group)

        # Calibration curve plot
        plot_group = QGroupBox("Correction curves")
        plot_layout = QVBoxLayout()

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground(None)
        self.plot_widget.setLabel("left", "Optical Power", units="mW")
        self.plot_widget.setLabel("bottom", "Command", units="%")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.addLegend(offset=(10, 10))

        # Raw curve (measured)
        self.raw_curve = self.plot_widget.plot(
            pen=pg.mkPen("#888888", width=2, style=Qt.DashLine), name="Measured")
        # Corrected curve
        self.corrected_curve = self.plot_widget.plot(
            pen=pg.mkPen("#27ae60", width=2), name="Corrected")
        # Ideal line
        self.ideal_curve = self.plot_widget.plot(
            pen=pg.mkPen("#3498db", width=1, style=Qt.DotLine), name="Ideal linear")
        # Measurement points
        self.points_scatter = pg.ScatterPlotItem(
            pen=pg.mkPen(None), brush=pg.mkBrush("#e74c3c"), size=10)
        self.plot_widget.addItem(self.points_scatter)

        plot_layout.addWidget(self.plot_widget)

        # Summary
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: gray; font-size: 11px;")
        self.summary_label.setWordWrap(True)
        plot_layout.addWidget(self.summary_label)

        plot_group.setLayout(plot_layout)
        content.addWidget(plot_group, stretch=1)

        layout.addLayout(content, stretch=1)

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def _refresh_ui(self):
        """Refresh the table and plot for the current channel."""
        ch = self.ch_combo.currentIndex()
        cal = self.cal.channels[ch]

        # Update table
        self.table.setRowCount(len(cal.points))
        for row, (drive, power) in enumerate(cal.points):
            drive_item = QTableWidgetItem(f"{drive:.1f}")
            drive_item.setTextAlignment(Qt.AlignCenter)
            power_item = QTableWidgetItem(f"{power:.2f}")
            power_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, drive_item)
            self.table.setItem(row, 1, power_item)

        # Update status
        self.cal_status_label.setText(self.cal.get_channel_summary(ch))

        # Update plot
        self._update_plot()

    def _update_plot(self):
        ch = self.ch_combo.currentIndex()
        cal = self.cal.channels[ch]

        if not cal.is_calibrated:
            self.raw_curve.clear()
            self.corrected_curve.clear()
            self.ideal_curve.clear()
            self.points_scatter.clear()
            self.summary_label.setText("No calibration data for this channel.")
            return

        # X axis: command percentage 0-100
        cmd = np.linspace(0, 100, 200)

        # Raw measured response: drive% → power
        drives = [p[0] for p in cal.points]
        powers = [p[1] for p in cal.points]
        raw_power = np.interp(cmd, drives, powers)
        self.raw_curve.setData(cmd, raw_power)

        # Measurement points scatter
        self.points_scatter.setData(
            [p[0] for p in cal.points], [p[1] for p in cal.points])

        # Corrected response: what power you get after correction
        eq_max = self.cal.equalized_max_power
        if self.equalize_check.isChecked() and eq_max != float('inf') and eq_max > 0:
            target_max = eq_max
        else:
            target_max = cal.max_power

        corrected_power = np.zeros_like(cmd)
        for i, c in enumerate(cmd):
            corrected_drive = self.cal.correct_intensity(ch, c)
            corrected_power[i] = cal.drive_to_power(corrected_drive)
        self.corrected_curve.setData(cmd, corrected_power)

        # Ideal linear response
        ideal = cmd / 100.0 * target_max
        self.ideal_curve.setData(cmd, ideal)

        self.plot_widget.setYRange(0, max(cal.max_power * 1.1, 0.1))

        # Summary text
        cal_channels = [i for i in range(NUM_CHANNELS) if self.cal.channels[i].is_calibrated]
        max_powers = [self.cal.channels[i].max_power for i in cal_channels]
        labels = self.config.get_all_labels()

        lines = [f"Calibrated channels: {len(cal_channels)}/{NUM_CHANNELS}"]
        if max_powers:
            lines.append(f"Equalized max: {min(max_powers):.1f} mW "
                         f"(limited by {labels[cal_channels[max_powers.index(min(max_powers))]]})")
            for i in cal_channels:
                pct_used = min(max_powers) / self.cal.channels[i].max_power * 100
                lines.append(f"  {labels[i]}: {self.cal.channels[i].max_power:.1f} mW "
                             f"(using {pct_used:.0f}% of range)")
        self.summary_label.setText("\n".join(lines))

    def _on_channel_changed(self, index):
        self._refresh_ui()

    def _on_add_point(self):
        row = self.table.rowCount()
        self.table.setRowCount(row + 1)
        # Default to next standard percentage
        existing = set()
        for r in range(row):
            item = self.table.item(r, 0)
            if item:
                try:
                    existing.add(float(item.text()))
                except ValueError:
                    pass
        standard = [0, 25, 50, 75, 100]
        next_val = 0
        for s in standard:
            if s not in existing:
                next_val = s
                break

        drive_item = QTableWidgetItem(f"{next_val:.1f}")
        drive_item.setTextAlignment(Qt.AlignCenter)
        power_item = QTableWidgetItem("0.00")
        power_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 0, drive_item)
        self.table.setItem(row, 1, power_item)

    def _on_remove_point(self):
        rows = set(item.row() for item in self.table.selectedItems())
        for row in sorted(rows, reverse=True):
            self.table.removeRow(row)

    def _on_quick_fill(self):
        """Add standard measurement points (0, 25, 50, 75, 100%)."""
        standard = [0, 25, 50, 75, 100]
        # Check what's already there
        existing = {}
        for r in range(self.table.rowCount()):
            d_item = self.table.item(r, 0)
            p_item = self.table.item(r, 1)
            if d_item and p_item:
                try:
                    existing[float(d_item.text())] = float(p_item.text())
                except ValueError:
                    pass

        self.table.setRowCount(len(standard))
        for i, drive in enumerate(standard):
            drive_item = QTableWidgetItem(f"{drive:.1f}")
            drive_item.setTextAlignment(Qt.AlignCenter)
            power_val = existing.get(drive, 0.0)
            power_item = QTableWidgetItem(f"{power_val:.2f}")
            power_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 0, drive_item)
            self.table.setItem(i, 1, power_item)

    def _on_save_channel(self):
        """Save the current table data to the channel's calibration."""
        ch = self.ch_combo.currentIndex()
        cal = self.cal.channels[ch]
        cal.clear()

        for row in range(self.table.rowCount()):
            d_item = self.table.item(row, 0)
            p_item = self.table.item(row, 1)
            if d_item and p_item:
                try:
                    drive = float(d_item.text())
                    power = float(p_item.text())
                    cal.add_point(drive, power)
                except ValueError:
                    pass

        if not cal.is_calibrated:
            QMessageBox.warning(self, "Calibration",
                                "Need at least 2 points for calibration.")
        else:
            # Temporarily set equalize state for preview
            self.cal.equalize = self.equalize_check.isChecked()
            self.cal.enabled = True  # enable temporarily for preview

        self._refresh_ui()

    def _on_clear_channel(self):
        ch = self.ch_combo.currentIndex()
        self.cal.channels[ch].clear()
        self._refresh_ui()

    def get_calibration(self) -> CalibrationManager:
        """Return the updated calibration manager."""
        self.cal.enabled = self.enable_check.isChecked()
        self.cal.equalize = self.equalize_check.isChecked()
        return self.cal
