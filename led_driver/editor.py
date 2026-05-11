"""
Waveform Editor for building AWG CSV files.

Features:
  - Per-channel editing with real-time plot
  - Show/hide individual channels
  - Time-referenced axis (ms) with sample rate awareness
  - Select range and apply functions (constant, ramp, sine, square, exp, gaussian, polynomial)
  - Point editing via click
  - Undo/redo
  - Import/export CSV
  - Duration and sample rate info display
"""

import numpy as np
from pathlib import Path
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QPushButton, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QFileDialog, QMessageBox, QSplitter, QScrollArea,
    QFrame, QToolBar, QAction, QSlider, QLineEdit, QStackedWidget,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence

import pyqtgraph as pg

from .hardware import NUM_CHANNELS
from .config import DEFAULT_COLORS


class UndoStack:
    """Simple undo/redo stack for waveform data."""

    def __init__(self, max_size: int = 50):
        self._stack = []
        self._pos = -1
        self._max_size = max_size

    def push(self, state: np.ndarray):
        # Remove any redo states
        self._stack = self._stack[:self._pos + 1]
        self._stack.append(state.copy())
        if len(self._stack) > self._max_size:
            self._stack.pop(0)
        self._pos = len(self._stack) - 1

    def undo(self) -> np.ndarray:
        if self._pos > 0:
            self._pos -= 1
            return self._stack[self._pos].copy()
        return None

    def redo(self) -> np.ndarray:
        if self._pos < len(self._stack) - 1:
            self._pos += 1
            return self._stack[self._pos].copy()
        return None

    @property
    def can_undo(self) -> bool:
        return self._pos > 0

    @property
    def can_redo(self) -> bool:
        return self._pos < len(self._stack) - 1


class FunctionPanel(QGroupBox):
    """Panel for selecting and configuring a function to apply to a range."""

    apply_requested = pyqtSignal()
    preview_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Apply function to range", parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(6)

        # Range selection
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("From:"))
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setSuffix(" ms")
        self.start_spin.setDecimals(3)
        self.start_spin.setRange(0, 100000)
        range_row.addWidget(self.start_spin)

        range_row.addWidget(QLabel("To:"))
        self.end_spin = QDoubleSpinBox()
        self.end_spin.setSuffix(" ms")
        self.end_spin.setDecimals(3)
        self.end_spin.setRange(0, 100000)
        range_row.addWidget(self.end_spin)

        range_row.addWidget(QLabel("Channel:"))
        self.channel_combo = QComboBox()
        for i in range(NUM_CHANNELS):
            self.channel_combo.addItem(f"Ch{i}")
        range_row.addWidget(self.channel_combo)
        layout.addLayout(range_row)

        # Function type
        func_row = QHBoxLayout()
        func_row.addWidget(QLabel("Function:"))
        self.func_combo = QComboBox()
        self.func_combo.addItems([
            "Constant", "Linear ramp", "Sine wave", "Square wave",
            "Exponential", "Gaussian pulse", "Polynomial",
        ])
        self.func_combo.currentIndexChanged.connect(self._on_func_changed)
        func_row.addWidget(self.func_combo)
        func_row.addStretch()
        layout.addLayout(func_row)

        # Function parameters (stacked widget)
        self.param_stack = QStackedWidget()
        self._build_param_pages()
        layout.addWidget(self.param_stack)

        # Apply/preview buttons
        btn_row = QHBoxLayout()
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.clicked.connect(self.preview_requested.emit)
        btn_row.addWidget(self.preview_btn)

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; "
            "border: none; border-radius: 4px; font-weight: bold; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #2ecc71; }")
        self.apply_btn.clicked.connect(self.apply_requested.emit)
        btn_row.addWidget(self.apply_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _build_param_pages(self):
        # 0: Constant
        p = QWidget()
        l = QHBoxLayout(p)
        l.addWidget(QLabel("Value:"))
        self.const_value = QDoubleSpinBox()
        self.const_value.setRange(0, 100)
        self.const_value.setSuffix(" %")
        self.const_value.setValue(50)
        l.addWidget(self.const_value)
        l.addStretch()
        self.param_stack.addWidget(p)

        # 1: Linear ramp
        p = QWidget()
        l = QHBoxLayout(p)
        l.addWidget(QLabel("Start:"))
        self.ramp_start = QDoubleSpinBox()
        self.ramp_start.setRange(0, 100)
        self.ramp_start.setSuffix(" %")
        l.addWidget(self.ramp_start)
        l.addWidget(QLabel("End:"))
        self.ramp_end = QDoubleSpinBox()
        self.ramp_end.setRange(0, 100)
        self.ramp_end.setSuffix(" %")
        self.ramp_end.setValue(100)
        l.addWidget(self.ramp_end)
        l.addStretch()
        self.param_stack.addWidget(p)

        # 2: Sine wave
        p = QWidget()
        l = QGridLayout(p)
        l.addWidget(QLabel("Freq:"), 0, 0)
        self.sine_freq = QDoubleSpinBox()
        self.sine_freq.setRange(0.001, 100000)
        self.sine_freq.setValue(1000)
        self.sine_freq.setSuffix(" Hz")
        l.addWidget(self.sine_freq, 0, 1)
        l.addWidget(QLabel("Amplitude:"), 0, 2)
        self.sine_amp = QDoubleSpinBox()
        self.sine_amp.setRange(0, 50)
        self.sine_amp.setValue(50)
        self.sine_amp.setSuffix(" %")
        l.addWidget(self.sine_amp, 0, 3)
        l.addWidget(QLabel("Offset:"), 1, 0)
        self.sine_offset = QDoubleSpinBox()
        self.sine_offset.setRange(0, 100)
        self.sine_offset.setValue(50)
        self.sine_offset.setSuffix(" %")
        l.addWidget(self.sine_offset, 1, 1)
        l.addWidget(QLabel("Phase:"), 1, 2)
        self.sine_phase = QDoubleSpinBox()
        self.sine_phase.setRange(0, 360)
        self.sine_phase.setSuffix(" deg")
        l.addWidget(self.sine_phase, 1, 3)
        self.param_stack.addWidget(p)

        # 3: Square wave
        p = QWidget()
        l = QGridLayout(p)
        l.addWidget(QLabel("Freq:"), 0, 0)
        self.sq_freq = QDoubleSpinBox()
        self.sq_freq.setRange(0.001, 100000)
        self.sq_freq.setValue(1000)
        self.sq_freq.setSuffix(" Hz")
        l.addWidget(self.sq_freq, 0, 1)
        l.addWidget(QLabel("Duty:"), 0, 2)
        self.sq_duty = QDoubleSpinBox()
        self.sq_duty.setRange(0, 100)
        self.sq_duty.setValue(50)
        self.sq_duty.setSuffix(" %")
        l.addWidget(self.sq_duty, 0, 3)
        l.addWidget(QLabel("High:"), 1, 0)
        self.sq_high = QDoubleSpinBox()
        self.sq_high.setRange(0, 100)
        self.sq_high.setValue(100)
        self.sq_high.setSuffix(" %")
        l.addWidget(self.sq_high, 1, 1)
        l.addWidget(QLabel("Low:"), 1, 2)
        self.sq_low = QDoubleSpinBox()
        self.sq_low.setRange(0, 100)
        self.sq_low.setValue(0)
        self.sq_low.setSuffix(" %")
        l.addWidget(self.sq_low, 1, 3)
        self.param_stack.addWidget(p)

        # 4: Exponential
        p = QWidget()
        l = QHBoxLayout(p)
        l.addWidget(QLabel("Start:"))
        self.exp_start = QDoubleSpinBox()
        self.exp_start.setRange(0, 100)
        self.exp_start.setSuffix(" %")
        self.exp_start.setValue(100)
        l.addWidget(self.exp_start)
        l.addWidget(QLabel("End:"))
        self.exp_end = QDoubleSpinBox()
        self.exp_end.setRange(0, 100)
        self.exp_end.setSuffix(" %")
        l.addWidget(self.exp_end)
        l.addWidget(QLabel("Tau:"))
        self.exp_tau = QDoubleSpinBox()
        self.exp_tau.setRange(0.001, 10000)
        self.exp_tau.setValue(1)
        self.exp_tau.setSuffix(" ms")
        l.addWidget(self.exp_tau)
        l.addStretch()
        self.param_stack.addWidget(p)

        # 5: Gaussian pulse
        p = QWidget()
        l = QHBoxLayout(p)
        l.addWidget(QLabel("Peak:"))
        self.gauss_peak = QDoubleSpinBox()
        self.gauss_peak.setRange(0, 100)
        self.gauss_peak.setValue(100)
        self.gauss_peak.setSuffix(" %")
        l.addWidget(self.gauss_peak)
        l.addWidget(QLabel("Width (FWHM):"))
        self.gauss_width = QDoubleSpinBox()
        self.gauss_width.setRange(0.001, 10000)
        self.gauss_width.setValue(0.5)
        self.gauss_width.setSuffix(" ms")
        l.addWidget(self.gauss_width)
        l.addWidget(QLabel("Baseline:"))
        self.gauss_base = QDoubleSpinBox()
        self.gauss_base.setRange(0, 100)
        self.gauss_base.setSuffix(" %")
        l.addWidget(self.gauss_base)
        l.addStretch()
        self.param_stack.addWidget(p)

        # 6: Polynomial
        p = QWidget()
        l = QHBoxLayout(p)
        l.addWidget(QLabel("Coefficients (a0, a1, a2, ...):"))
        self.poly_coeffs = QLineEdit("0, 100")
        self.poly_coeffs.setPlaceholderText("a0, a1, a2, ... (in % units)")
        l.addWidget(self.poly_coeffs)
        l.addStretch()
        self.param_stack.addWidget(p)

    def _on_func_changed(self, index):
        self.param_stack.setCurrentIndex(index)

    def set_range_from_samples(self, start_sample: int, end_sample: int, sample_rate: float):
        """Set the range spinboxes from sample indices."""
        self.start_spin.setValue(start_sample / sample_rate * 1000)
        self.end_spin.setValue(end_sample / sample_rate * 1000)

    def get_range_samples(self, sample_rate: float) -> tuple:
        """Get the range as sample indices."""
        start = int(self.start_spin.value() / 1000 * sample_rate)
        end = int(self.end_spin.value() / 1000 * sample_rate)
        return start, end

    def generate_samples(self, num_samples: int, sample_rate: float) -> np.ndarray:
        """Generate the function values for the given range."""
        func_idx = self.func_combo.currentIndex()
        t = np.arange(num_samples) / sample_rate  # time in seconds

        if func_idx == 0:  # Constant
            return np.full(num_samples, self.const_value.value())

        elif func_idx == 1:  # Linear ramp
            return np.linspace(self.ramp_start.value(), self.ramp_end.value(), num_samples)

        elif func_idx == 2:  # Sine wave
            freq = self.sine_freq.value()
            amp = self.sine_amp.value()
            offset = self.sine_offset.value()
            phase = np.radians(self.sine_phase.value())
            return offset + amp * np.sin(2 * np.pi * freq * t + phase)

        elif func_idx == 3:  # Square wave
            freq = self.sq_freq.value()
            duty = self.sq_duty.value() / 100.0
            high = self.sq_high.value()
            low = self.sq_low.value()
            phase = (freq * t) % 1.0
            return np.where(phase < duty, high, low)

        elif func_idx == 4:  # Exponential
            start_v = self.exp_start.value()
            end_v = self.exp_end.value()
            tau = self.exp_tau.value() / 1000.0  # convert ms to seconds
            if tau <= 0:
                return np.full(num_samples, end_v)
            return end_v + (start_v - end_v) * np.exp(-t / tau)

        elif func_idx == 5:  # Gaussian pulse
            peak = self.gauss_peak.value()
            fwhm = self.gauss_width.value() / 1000.0  # ms to seconds
            base = self.gauss_base.value()
            sigma = fwhm / (2 * np.sqrt(2 * np.log(2)))
            center = t[-1] / 2 if num_samples > 0 else 0
            return base + (peak - base) * np.exp(-((t - center) ** 2) / (2 * sigma ** 2))

        elif func_idx == 6:  # Polynomial
            try:
                coeffs = [float(c.strip()) for c in self.poly_coeffs.text().split(",")]
            except ValueError:
                return np.zeros(num_samples)
            t_norm = np.linspace(0, 1, num_samples)  # normalized 0-1
            result = np.zeros(num_samples)
            for i, c in enumerate(coeffs):
                result += c * (t_norm ** i)
            return np.clip(result, 0, 100)

        return np.zeros(num_samples)


class ChannelToggle(QWidget):
    """Small widget for channel visibility and selection."""

    visibility_changed = pyqtSignal(int, bool)
    selected = pyqtSignal(int)

    def __init__(self, channel: int, color: str, label: str = None, parent=None):
        super().__init__(parent)
        self.channel = channel
        layout = QHBoxLayout()
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        self.vis_check = QCheckBox()
        self.vis_check.setChecked(True)
        self.vis_check.stateChanged.connect(
            lambda s: self.visibility_changed.emit(self.channel, s == Qt.Checked))
        layout.addWidget(self.vis_check)

        self.label_btn = QPushButton(label or f"Ch{channel}")
        self.label_btn.setFixedWidth(70)
        self._apply_color(color)
        self.label_btn.clicked.connect(lambda: self.selected.emit(self.channel))
        layout.addWidget(self.label_btn)

        self.setLayout(layout)

    def _apply_color(self, color: str):
        self.label_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {color}; color: white; border: none;
                border-radius: 3px; font-size: 10px; font-weight: bold; padding: 2px;
            }}
            QPushButton:hover {{ opacity: 0.8; }}
        """)

    def update_appearance(self, color: str, label: str):
        self.label_btn.setText(label)
        self._apply_color(color)


class WaveformEditor(QWidget):
    """Interactive waveform editor widget."""

    waveform_ready = pyqtSignal(np.ndarray, float)  # data, sample_rate

    def __init__(self, colors=None, labels=None, parent=None):
        super().__init__(parent)
        self.sample_rate = 100000.0  # Hz
        self.num_samples = 1000
        self.data = np.zeros((self.num_samples, NUM_CHANNELS), dtype=np.float64)
        self.undo_stack = UndoStack()
        self.undo_stack.push(self.data)
        self._preview_data = None
        self._preview_channel = None
        self._active_channel = 0
        self._selection_start = None
        self._selection_end = None
        self._colors = colors or list(DEFAULT_COLORS)
        self._labels = labels or [f"Ch{i}" for i in range(NUM_CHANNELS)]
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)

        # ---- Toolbar ----
        toolbar = QHBoxLayout()

        # New waveform
        new_btn = QPushButton("New")
        new_btn.setFixedWidth(60)
        new_btn.clicked.connect(self._on_new)
        toolbar.addWidget(new_btn)

        # Duration
        toolbar.addWidget(QLabel("Duration:"))
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.01, 60000)
        self.duration_spin.setValue(self.num_samples / self.sample_rate * 1000)
        self.duration_spin.setSuffix(" ms")
        self.duration_spin.setDecimals(2)
        self.duration_spin.valueChanged.connect(self._on_duration_changed)
        toolbar.addWidget(self.duration_spin)

        # Sample rate
        toolbar.addWidget(QLabel("Rate:"))
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(1, 100000)
        self.rate_spin.setValue(int(self.sample_rate))
        self.rate_spin.setSuffix(" Hz")
        self.rate_spin.setSingleStep(1000)
        self.rate_spin.valueChanged.connect(self._on_rate_changed)
        toolbar.addWidget(self.rate_spin)

        # Info label
        self.info_label = QLabel()
        self.info_label.setStyleSheet("color: gray; font-size: 11px;")
        self._update_info()
        toolbar.addWidget(self.info_label)

        toolbar.addStretch()

        # Undo/redo
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setFixedWidth(60)
        self.undo_btn.clicked.connect(self._on_undo)
        self.undo_btn.setShortcut(QKeySequence.Undo)
        toolbar.addWidget(self.undo_btn)

        self.redo_btn = QPushButton("Redo")
        self.redo_btn.setFixedWidth(60)
        self.redo_btn.clicked.connect(self._on_redo)
        self.redo_btn.setShortcut(QKeySequence.Redo)
        toolbar.addWidget(self.redo_btn)

        # Import/export
        import_btn = QPushButton("Import CSV")
        import_btn.setFixedWidth(90)
        import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(import_btn)

        export_btn = QPushButton("Export CSV")
        export_btn.setFixedWidth(90)
        export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(export_btn)

        layout.addLayout(toolbar)

        # ---- Main content: plot + channel panel ----
        content = QHBoxLayout()

        # Channel toggles panel
        ch_panel = QVBoxLayout()
        ch_panel.addWidget(QLabel("Channels:"))
        self.channel_toggles = []
        for i in range(NUM_CHANNELS):
            toggle = ChannelToggle(i, self._colors[i], self._labels[i])
            toggle.visibility_changed.connect(self._on_visibility_changed)
            toggle.selected.connect(self._on_channel_selected)
            self.channel_toggles.append(toggle)
            ch_panel.addWidget(toggle)

        ch_panel.addStretch()

        # Copy channel
        copy_row = QHBoxLayout()
        copy_row.addWidget(QLabel("Copy:"))
        self.copy_from_combo = QComboBox()
        self.copy_from_combo.addItems([f"Ch{i}" for i in range(NUM_CHANNELS)])
        copy_row.addWidget(self.copy_from_combo)
        copy_row.addWidget(QLabel("to"))
        self.copy_to_combo = QComboBox()
        self.copy_to_combo.addItems([f"Ch{i}" for i in range(NUM_CHANNELS)])
        self.copy_to_combo.setCurrentIndex(1)
        copy_row.addWidget(self.copy_to_combo)
        copy_btn = QPushButton("Copy")
        copy_btn.setFixedWidth(50)
        copy_btn.clicked.connect(self._on_copy_channel)
        copy_row.addWidget(copy_btn)
        ch_panel.addLayout(copy_row)

        # Clear channel
        clear_row = QHBoxLayout()
        clear_btn = QPushButton("Clear active channel")
        clear_btn.clicked.connect(self._on_clear_channel)
        clear_row.addWidget(clear_btn)
        ch_panel.addLayout(clear_row)

        ch_widget = QWidget()
        ch_widget.setLayout(ch_panel)
        ch_widget.setFixedWidth(160)
        content.addWidget(ch_widget)

        # Plot
        pg.setConfigOptions(antialias=True)
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground(None)
        self.plot_widget.setLabel("left", "Intensity", units="%")
        self.plot_widget.setLabel("bottom", "Time", units="ms")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setYRange(-5, 105)

        # Plot curves
        self.plot_curves = []
        for i in range(NUM_CHANNELS):
            curve = self.plot_widget.plot(
                pen=pg.mkPen(self._colors[i], width=1.5), name=self._labels[i])
            self.plot_curves.append(curve)

        # Preview curve (dashed)
        self.preview_curve = self.plot_widget.plot(
            pen=pg.mkPen("#ffffff", width=2, style=Qt.DashLine))

        # Selection region
        self.selection_region = pg.LinearRegionItem(
            values=[0, 1], brush=pg.mkBrush(255, 255, 255, 30),
            movable=True)
        self.selection_region.sigRegionChanged.connect(self._on_region_changed)
        self.plot_widget.addItem(self.selection_region)

        # Cursor readout
        self.cursor_label = QLabel("Cursor: -- ms, -- %")
        self.cursor_label.setStyleSheet("color: gray; font-size: 11px;")
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        plot_layout = QVBoxLayout()
        plot_layout.addWidget(self.plot_widget, stretch=1)
        plot_layout.addWidget(self.cursor_label)

        content.addLayout(plot_layout, stretch=1)
        layout.addLayout(content, stretch=1)

        # ---- Function panel ----
        self.func_panel = FunctionPanel()
        # Update channel combo with config labels
        self.func_panel.channel_combo.clear()
        for i in range(NUM_CHANNELS):
            self.func_panel.channel_combo.addItem(self._labels[i])
        self.func_panel.apply_requested.connect(self._on_apply_function)
        self.func_panel.preview_requested.connect(self._on_preview_function)
        layout.addWidget(self.func_panel)

        # ---- Send to driver button ----
        send_row = QHBoxLayout()
        send_row.addStretch()
        self.send_btn = QPushButton("Send to AWG driver")
        self.send_btn.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; "
            "border: none; border-radius: 4px; font-weight: bold; "
            "padding: 8px 24px; font-size: 13px; }"
            "QPushButton:hover { background-color: #3498db; }")
        self.send_btn.clicked.connect(self._on_send)
        send_row.addWidget(self.send_btn)
        send_row.addStretch()
        layout.addLayout(send_row)

        self.setLayout(layout)
        self._update_plot()

    # ---- Data management ----

    def _commit(self):
        """Save current state to undo stack."""
        self.undo_stack.push(self.data)

    def _update_info(self):
        duration_ms = self.num_samples / self.sample_rate * 1000
        self.info_label.setText(
            f"{self.num_samples:,} samples  |  {duration_ms:.2f} ms  |  "
            f"{self.sample_rate / 1000:.0f} kHz")

    def _update_plot(self):
        t_ms = np.arange(self.num_samples) / self.sample_rate * 1000
        for ch in range(NUM_CHANNELS):
            if self.plot_curves[ch].isVisible():
                self.plot_curves[ch].setData(t_ms, self.data[:, ch])
            else:
                self.plot_curves[ch].clear()

        # Clear preview
        self.preview_curve.clear()
        self._preview_data = None

    # ---- Callbacks ----

    def _on_new(self):
        self.data = np.zeros((self.num_samples, NUM_CHANNELS), dtype=np.float64)
        self._commit()
        self._update_plot()

    def _on_duration_changed(self, value_ms):
        new_samples = max(1, int(value_ms / 1000 * self.sample_rate))
        if new_samples != self.num_samples:
            old_data = self.data
            self.num_samples = new_samples
            self.data = np.zeros((self.num_samples, NUM_CHANNELS), dtype=np.float64)
            copy_len = min(old_data.shape[0], self.num_samples)
            self.data[:copy_len, :] = old_data[:copy_len, :]
            self._commit()
            self._update_info()
            self._update_plot()

    def _on_rate_changed(self, value):
        old_rate = self.sample_rate
        self.sample_rate = float(value)
        # Keep the same duration, recalculate number of samples
        duration_ms = self.duration_spin.value()
        new_samples = max(1, int(duration_ms / 1000 * self.sample_rate))
        if new_samples != self.num_samples:
            # Resample data
            old_data = self.data
            self.num_samples = new_samples
            self.data = np.zeros((self.num_samples, NUM_CHANNELS), dtype=np.float64)
            for ch in range(NUM_CHANNELS):
                old_x = np.linspace(0, 1, old_data.shape[0])
                new_x = np.linspace(0, 1, self.num_samples)
                self.data[:, ch] = np.interp(new_x, old_x, old_data[:, ch])
            self._commit()
        self._update_info()
        self._update_plot()

    def _on_visibility_changed(self, channel: int, visible: bool):
        self.plot_curves[channel].setVisible(visible)
        if visible:
            self._update_plot()

    def _on_channel_selected(self, channel: int):
        self._active_channel = channel
        self.func_panel.channel_combo.setCurrentIndex(channel)

    def _on_region_changed(self):
        region = self.selection_region.getRegion()
        start_ms, end_ms = min(region), max(region)
        self.func_panel.start_spin.setValue(start_ms)
        self.func_panel.end_spin.setValue(end_ms)

    def _on_mouse_moved(self, pos):
        mouse_point = self.plot_widget.plotItem.vb.mapSceneToView(pos)
        t_ms = mouse_point.x()
        val = mouse_point.y()
        sample = int(t_ms / 1000 * self.sample_rate)
        if 0 <= sample < self.num_samples:
            ch = self._active_channel
            actual_val = self.data[sample, ch]
            self.cursor_label.setText(
                f"Cursor: {t_ms:.3f} ms (sample {sample})  |  "
                f"Ch{ch}: {actual_val:.1f}%")
        else:
            self.cursor_label.setText(f"Cursor: {t_ms:.3f} ms, {val:.1f}%")

    def _on_preview_function(self):
        ch = self.func_panel.channel_combo.currentIndex()
        start, end = self.func_panel.get_range_samples(self.sample_rate)
        start = max(0, min(start, self.num_samples))
        end = max(start + 1, min(end, self.num_samples))
        num = end - start

        values = self.func_panel.generate_samples(num, self.sample_rate)
        values = np.clip(values, 0, 100)

        # Show preview on plot
        t_ms = np.arange(start, end) / self.sample_rate * 1000
        self.preview_curve.setData(t_ms, values)
        self._preview_data = values
        self._preview_channel = ch

    def _on_apply_function(self):
        ch = self.func_panel.channel_combo.currentIndex()
        start, end = self.func_panel.get_range_samples(self.sample_rate)
        start = max(0, min(start, self.num_samples))
        end = max(start + 1, min(end, self.num_samples))
        num = end - start

        if self._preview_data is not None and self._preview_channel == ch and len(self._preview_data) == num:
            values = self._preview_data
        else:
            values = self.func_panel.generate_samples(num, self.sample_rate)

        values = np.clip(values, 0, 100)
        self.data[start:end, ch] = values
        self._commit()
        self._update_plot()

    def _on_undo(self):
        state = self.undo_stack.undo()
        if state is not None:
            self.data = state
            self.num_samples = self.data.shape[0]
            self.duration_spin.blockSignals(True)
            self.duration_spin.setValue(self.num_samples / self.sample_rate * 1000)
            self.duration_spin.blockSignals(False)
            self._update_info()
            self._update_plot()

    def _on_redo(self):
        state = self.undo_stack.redo()
        if state is not None:
            self.data = state
            self.num_samples = self.data.shape[0]
            self.duration_spin.blockSignals(True)
            self.duration_spin.setValue(self.num_samples / self.sample_rate * 1000)
            self.duration_spin.blockSignals(False)
            self._update_info()
            self._update_plot()

    def _on_copy_channel(self):
        src = self.copy_from_combo.currentIndex()
        dst = self.copy_to_combo.currentIndex()
        if src != dst:
            self.data[:, dst] = self.data[:, src]
            self._commit()
            self._update_plot()

    def _on_clear_channel(self):
        self.data[:, self._active_channel] = 0
        self._commit()
        self._update_plot()

    def _on_import(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Import Waveform CSV", "", "CSV Files (*.csv);;All Files (*)")
        if not filepath:
            return
        try:
            import csv
            with open(filepath, 'r', newline='') as f:
                all_lines = f.readlines()

            # Check for metadata comment lines at the top
            imported_rate = None
            content_lines = []
            for line in all_lines:
                stripped = line.strip()
                if stripped.startswith('#'):
                    # Parse metadata
                    if 'sample_rate=' in stripped:
                        try:
                            imported_rate = float(stripped.split('sample_rate=')[1].strip())
                        except ValueError:
                            pass
                else:
                    content_lines.append(stripped)

            # Parse CSV from remaining lines
            import io
            reader = csv.reader(io.StringIO("\n".join(content_lines)))
            rows = list(reader)

            if not rows:
                raise ValueError("CSV file has no data")

            # Detect header
            has_header = False
            try:
                [float(v) for v in rows[0] if v.strip()]
            except ValueError:
                has_header = True

            data_rows = rows[1:] if has_header else rows
            parsed = []
            for row in data_rows:
                values = []
                for j in range(NUM_CHANNELS):
                    if j < len(row) and row[j].strip():
                        try:
                            values.append(np.clip(float(row[j].strip()), 0, 100))
                        except ValueError:
                            values.append(0.0)
                    else:
                        values.append(0.0)
                parsed.append(values)

            self.data = np.array(parsed, dtype=np.float64)
            self.num_samples = self.data.shape[0]

            # Apply imported sample rate if found
            if imported_rate is not None:
                self.sample_rate = imported_rate
                self.rate_spin.blockSignals(True)
                self.rate_spin.setValue(int(self.sample_rate))
                self.rate_spin.blockSignals(False)

            self.duration_spin.blockSignals(True)
            self.duration_spin.setValue(self.num_samples / self.sample_rate * 1000)
            self.duration_spin.blockSignals(False)
            self._commit()
            self._update_info()
            self._update_plot()

            duration_ms = self.num_samples / self.sample_rate * 1000
            rate_info = f" (rate from file: {self.sample_rate:.0f} Hz)" if imported_rate else ""
            QMessageBox.information(self, "Import",
                                     f"Imported {self.num_samples} samples from {Path(filepath).name}\n"
                                     f"Duration: {duration_ms:.2f} ms{rate_info}")
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    def _on_export(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export Waveform CSV", "waveform.csv", "CSV Files (*.csv)")
        if not filepath:
            return
        try:
            duration_ms = self.num_samples / self.sample_rate * 1000
            lines = [
                f"# sample_rate={self.sample_rate:.0f}",
                f"# duration_ms={duration_ms:.3f}",
                f"# num_samples={self.num_samples}",
                ",".join([f"Ch{i}" for i in range(NUM_CHANNELS)]),
            ]
            for row in self.data:
                lines.append(",".join([f"{v:.2f}" for v in row]))
            Path(filepath).write_text("\n".join(lines))
            QMessageBox.information(self, "Export",
                                     f"Exported {self.num_samples} samples to {Path(filepath).name}\n"
                                     f"Sample rate: {self.sample_rate:.0f} Hz\n"
                                     f"Duration: {duration_ms:.2f} ms")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _on_send(self):
        """Send the current waveform data to the AWG driver."""
        self.waveform_ready.emit(self.data.copy(), self.sample_rate)

    def update_config(self, colors: list, labels: list):
        """Update colors and labels from config changes."""
        self._colors = colors
        self._labels = labels

        # Update channel toggles
        for i, toggle in enumerate(self.channel_toggles):
            toggle.update_appearance(colors[i], labels[i])

        # Update plot curves
        for i, curve in enumerate(self.plot_curves):
            curve.setPen(pg.mkPen(colors[i], width=1.5))

        # Update function panel channel combo
        self.func_panel.channel_combo.blockSignals(True)
        current = self.func_panel.channel_combo.currentIndex()
        self.func_panel.channel_combo.clear()
        for i in range(NUM_CHANNELS):
            self.func_panel.channel_combo.addItem(labels[i])
        self.func_panel.channel_combo.setCurrentIndex(current)
        self.func_panel.channel_combo.blockSignals(False)
