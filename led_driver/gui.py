"""
PyQt5 GUI for the 8-channel LED AWG driver (v3).
"""

import sys
import numpy as np
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QPushButton, QSlider, QLabel, QComboBox,
    QFileDialog, QStatusBar, QSpinBox, QDoubleSpinBox, QCheckBox,
    QFrame, QSplitter, QSizePolicy, QMessageBox, QProgressBar,
    QTabWidget,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt5.QtGui import QFont, QColor, QPalette

import pyqtgraph as pg

from .hardware import (
    MockBackend, FPGABackend, HardwareBackend,
    NUM_CHANNELS, FPGA_CLOCK_HZ,
    intensity_to_u16, u16_to_intensity, u16_to_voltage,
    output_rate_to_ticks, ticks_to_output_rate,
)
from .waveform import WaveformEngine
from .editor import WaveformEditor
from .config import AppConfig, DEFAULT_COLORS, _get_config_dir
from .settings import ChannelSettingsDialog
from .calibration import CalibrationManager
from .cal_dialog import CalibrationDialog

# Channel colors for plots and UI accents
CHANNEL_COLORS = [
    "#e6194b",  # Ch0 - red
    "#3cb44b",  # Ch1 - green
    "#4363d8",  # Ch2 - blue
    "#f58231",  # Ch3 - orange
    "#911eb4",  # Ch4 - purple
    "#42d4f4",  # Ch5 - cyan
    "#f032e6",  # Ch6 - magenta
    "#bfef45",  # Ch7 - lime
]


class ChannelStrip(QGroupBox):
    """Control strip for a single LED channel."""

    enable_changed = pyqtSignal(int, bool)
    mode_changed = pyqtSignal(int, int)
    intensity_changed = pyqtSignal(int, float)

    def __init__(self, channel: int, color: str = None, label: str = None, parent=None):
        super().__init__(parent)
        self.channel = channel
        self.color = color or CHANNEL_COLORS[channel]
        self.channel_label = label or f"Channel {channel}"
        self._setup_ui()

    def _setup_ui(self):
        self.setTitle(f"  {self.channel_label}  ")
        self._apply_border_style()

        layout = QVBoxLayout()
        layout.setSpacing(6)
        layout.setContentsMargins(8, 14, 8, 8)

        # Enable toggle
        self.enable_btn = QPushButton("OFF")
        self.enable_btn.setCheckable(True)
        self.enable_btn.setFixedHeight(30)
        self.enable_btn.clicked.connect(self._on_enable_toggled)
        self._update_enable_style(False)
        layout.addWidget(self.enable_btn)

        # Mode selector
        mode_layout = QHBoxLayout()
        mode_label = QLabel("Mode:")
        mode_label.setFixedWidth(38)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["CW", "AWG"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(mode_label)
        mode_layout.addWidget(self.mode_combo)
        layout.addLayout(mode_layout)

        # CW Intensity slider
        self.intensity_label = QLabel("Intensity:")
        layout.addWidget(self.intensity_label)

        slider_layout = QHBoxLayout()
        self.intensity_slider = QSlider(Qt.Horizontal)
        self.intensity_slider.setRange(0, 1000)  # 0.0% to 100.0% in 0.1% steps
        self.intensity_slider.setValue(0)
        self.intensity_slider.valueChanged.connect(self._on_intensity_changed)
        slider_layout.addWidget(self.intensity_slider)

        self.intensity_readout = QLabel("0.0%")
        self.intensity_readout.setFixedWidth(52)
        self.intensity_readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider_layout.addWidget(self.intensity_readout)
        layout.addLayout(slider_layout)

        # Voltage readout
        self.voltage_label = QLabel("Output: 0.000 V")
        self.voltage_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.voltage_label)

        self.setLayout(layout)

    def _on_enable_toggled(self, checked):
        self._update_enable_style(checked)
        self.enable_changed.emit(self.channel, checked)

    def _update_enable_style(self, enabled):
        if enabled:
            self.enable_btn.setText("ON")
            self.enable_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.color};
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-weight: bold;
                    font-size: 13px;
                }}
            """)
        else:
            self.enable_btn.setText("OFF")
            self.enable_btn.setStyleSheet("""
                QPushButton {
                    background-color: #555;
                    color: #aaa;
                    border: none;
                    border-radius: 4px;
                    font-weight: bold;
                    font-size: 13px;
                }
            """)

    def _on_mode_changed(self, index):
        is_cw = (index == 0)
        self.intensity_slider.setEnabled(is_cw)
        self.mode_changed.emit(self.channel, index)

    def _on_intensity_changed(self, value):
        pct = value / 10.0
        self.intensity_readout.setText(f"{pct:.1f}%")
        voltage = pct / 100.0 * 10.0
        self.voltage_label.setText(f"Output: {voltage:.3f} V")
        self.intensity_changed.emit(self.channel, pct)

    def set_output_voltage(self, voltage: float):
        self.voltage_label.setText(f"Output: {voltage:.3f} V")

    def get_intensity_pct(self) -> float:
        return self.intensity_slider.value() / 10.0

    def _apply_border_style(self):
        self.setStyleSheet(f"""
            ChannelStrip {{
                border: 2px solid {self.color};
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 8px;
                font-weight: bold;
            }}
            ChannelStrip::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: {self.color};
            }}
        """)

    def update_appearance(self, color: str, label: str):
        """Update the channel color and label dynamically."""
        self.color = color
        self.channel_label = label
        self.setTitle(f"  {label}  ")
        self._apply_border_style()
        self._update_enable_style(self.enable_btn.isChecked())


class AWGStreamThread(QThread):
    """Background thread for streaming waveform data to the FIFO."""

    progress = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, backend: HardwareBackend, data: np.ndarray,
                 chunk_size: int = 4096):
        super().__init__()
        self.backend = backend
        self.data = data
        self.chunk_size = chunk_size
        self._stop_requested = False

    def run(self):
        total = len(self.data)
        pos = 0

        while not self._stop_requested and pos < total:
            end = min(pos + self.chunk_size, total)
            chunk = self.data[pos:end]

            try:
                self.backend.write_fifo(chunk)
            except Exception as e:
                self.error.emit(str(e))
                return

            pos = end
            pct = int(pos / total * 100)
            self.progress.emit(pct)

        self.finished.emit()

    def stop(self):
        self._stop_requested = True


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, backend: HardwareBackend):
        super().__init__()
        self.backend = backend
        self.config = AppConfig.load()
        self.cal_manager = CalibrationManager.load(_get_config_dir() / "calibration.json")
        self.waveform_engine = WaveformEngine()
        self.stream_thread: AWGStreamThread = None
        self.channel_strips: list[ChannelStrip] = []

        self.setWindowTitle("LED AWG Driver — 8-Channel Controller")
        self.setMinimumSize(1100, 750)
        self._setup_ui()
        self._connect_backend()

        # Status update timer
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(250)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Tab widget
        self.tabs = QTabWidget()
        outer_layout.addWidget(self.tabs)

        # ---- Tab 1: Driver ----
        driver_tab = QWidget()
        main_layout = QHBoxLayout(driver_tab)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # ---- Left panel: channel strips ----
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        # Connection status
        conn_bar = QHBoxLayout()
        self.conn_label = QLabel("Disconnected")
        self.conn_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        conn_bar.addWidget(self.conn_label)
        conn_bar.addStretch()

        settings_btn = QPushButton("Settings")
        settings_btn.setFixedWidth(80)
        settings_btn.clicked.connect(self._on_settings)
        conn_bar.addWidget(settings_btn)

        cal_btn = QPushButton("Calibrate")
        cal_btn.setFixedWidth(80)
        cal_btn.clicked.connect(self._on_calibrate)
        conn_bar.addWidget(cal_btn)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setFixedWidth(100)
        self.connect_btn.clicked.connect(self._on_connect)
        conn_bar.addWidget(self.connect_btn)
        left_layout.addLayout(conn_bar)

        # Channel strips in 2x4 grid
        channels_grid = QGridLayout()
        channels_grid.setSpacing(6)
        colors = self.config.get_all_colors()
        labels = self.config.get_all_labels()
        for i in range(NUM_CHANNELS):
            strip = ChannelStrip(i, color=colors[i], label=labels[i])
            strip.enable_changed.connect(self._on_channel_enable)
            strip.mode_changed.connect(self._on_channel_mode)
            strip.intensity_changed.connect(self._on_channel_intensity)
            self.channel_strips.append(strip)
            channels_grid.addWidget(strip, i // 2, i % 2)
        left_layout.addLayout(channels_grid)

        # All-channels controls
        all_controls = QHBoxLayout()
        for label, handler in [("All On", self._all_on), ("All Off", self._all_off),
                                ("All CW", self._all_cw), ("All AWG", self._all_awg)]:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.clicked.connect(handler)
            all_controls.addWidget(btn)
        left_layout.addLayout(all_controls)

        left_panel.setFixedWidth(440)

        # ---- Right panel: waveform display + AWG controls ----
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Waveform plot
        plot_group = QGroupBox("Waveform preview")
        plot_layout = QVBoxLayout()
        pg.setConfigOptions(antialias=True)
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground(None)
        self.plot_widget.setLabel("left", "Intensity", units="%")
        self.plot_widget.setLabel("bottom", "Time", units="s")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setYRange(0, 105)
        self.plot_widget.addLegend(offset=(10, 10))
        self.plot_curves = []
        for i in range(NUM_CHANNELS):
            curve = self.plot_widget.plot(
                pen=pg.mkPen(colors[i], width=1.5), name=labels[i])
            self.plot_curves.append(curve)
        plot_layout.addWidget(self.plot_widget)
        plot_group.setLayout(plot_layout)
        right_layout.addWidget(plot_group, stretch=3)

        # AWG controls
        awg_group = QGroupBox("AWG controls")
        awg_layout = QVBoxLayout()

        # File loading
        file_row = QHBoxLayout()
        self.load_csv_btn = QPushButton("Load CSV")
        self.load_csv_btn.setFixedWidth(100)
        self.load_csv_btn.clicked.connect(self._on_load_csv)
        file_row.addWidget(self.load_csv_btn)
        self.file_label = QLabel("No waveform loaded")
        self.file_label.setStyleSheet("color: gray;")
        file_row.addWidget(self.file_label, stretch=1)
        awg_layout.addLayout(file_row)

        # Waveform info
        self.waveform_info_label = QLabel("")
        self.waveform_info_label.setStyleSheet("color: gray; font-size: 11px;")
        awg_layout.addWidget(self.waveform_info_label)

        # Sample rate
        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Output rate:"))
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(1, 100000)
        self.rate_spin.setValue(100000)
        self.rate_spin.setSuffix(" Hz")
        self.rate_spin.setSingleStep(1000)
        self.rate_spin.valueChanged.connect(self._on_rate_changed)
        rate_row.addWidget(self.rate_spin)
        rate_row.addStretch()
        awg_layout.addLayout(rate_row)

        # Playback control row
        playback_row = QHBoxLayout()
        playback_row.addWidget(QLabel("Playback:"))
        self.playback_mode_combo = QComboBox()
        self.playback_mode_combo.addItems(["Single shot", "N repetitions", "Continuous"])
        self.playback_mode_combo.currentIndexChanged.connect(self._on_playback_mode_changed)
        playback_row.addWidget(self.playback_mode_combo)

        self.reps_spin = QSpinBox()
        self.reps_spin.setRange(1, 10000)
        self.reps_spin.setValue(1)
        self.reps_spin.setPrefix("x")
        self.reps_spin.setFixedWidth(80)
        self.reps_spin.setVisible(False)
        playback_row.addWidget(self.reps_spin)

        playback_row.addStretch()

        playback_row.addWidget(QLabel("On complete:"))
        self.stop_behavior_combo = QComboBox()
        self.stop_behavior_combo.addItems(["Hold last", "Return to CW", "Return to 0V"])
        self.stop_behavior_combo.setFixedWidth(120)
        playback_row.addWidget(self.stop_behavior_combo)
        awg_layout.addLayout(playback_row)

        # Trigger controls
        trigger_row = QHBoxLayout()
        trigger_row.addWidget(QLabel("Trigger:"))
        self.trigger_mode_combo = QComboBox()
        self.trigger_mode_combo.addItems(["Immediate", "Hardware (DIO1)", "Software"])
        self.trigger_mode_combo.currentIndexChanged.connect(self._on_trigger_mode_changed)
        trigger_row.addWidget(self.trigger_mode_combo)

        self.trigger_edge_combo = QComboBox()
        self.trigger_edge_combo.addItems(["Rising", "Falling"])
        self.trigger_edge_combo.setFixedWidth(80)
        self.trigger_edge_combo.currentIndexChanged.connect(self._on_trigger_edge_changed)
        self.trigger_edge_combo.setEnabled(False)
        trigger_row.addWidget(self.trigger_edge_combo)

        self.trigger_out_check = QCheckBox("Trig out (DIO0)")
        self.trigger_out_check.setChecked(True)
        self.trigger_out_check.stateChanged.connect(self._on_trigger_out_changed)
        trigger_row.addWidget(self.trigger_out_check)
        awg_layout.addLayout(trigger_row)

        # Armed status + software trigger
        arm_row = QHBoxLayout()
        self.armed_label = QLabel("")
        self.armed_label.setStyleSheet("color: gray; font-size: 11px;")
        arm_row.addWidget(self.armed_label)
        arm_row.addStretch()

        self.sw_trigger_btn = QPushButton("Fire trigger")
        self.sw_trigger_btn.setFixedWidth(110)
        self.sw_trigger_btn.setStyleSheet("""
            QPushButton {
                background-color: #e67e22; color: white; border: none;
                border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover { background-color: #f39c12; }
            QPushButton:disabled { background-color: #555; color: #888; }
        """)
        self.sw_trigger_btn.clicked.connect(self._on_sw_trigger)
        self.sw_trigger_btn.setEnabled(False)
        self.sw_trigger_btn.setVisible(False)
        arm_row.addWidget(self.sw_trigger_btn)
        awg_layout.addLayout(arm_row)

        # Transport controls
        transport_row = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.setFixedHeight(36)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60; color: white; border: none;
                border-radius: 4px; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { background-color: #2ecc71; }
            QPushButton:disabled { background-color: #555; color: #888; }
        """)
        self.play_btn.clicked.connect(self._on_play)
        self.play_btn.setEnabled(False)
        transport_row.addWidget(self.play_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #c0392b; color: white; border: none;
                border-radius: 4px; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { background-color: #e74c3c; }
            QPushButton:disabled { background-color: #555; color: #888; }
        """)
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        transport_row.addWidget(self.stop_btn)
        awg_layout.addLayout(transport_row)

        # Progress bar + frames counter
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_bar, stretch=1)
        self.frames_label = QLabel("")
        self.frames_label.setStyleSheet("color: gray; font-size: 11px;")
        self.frames_label.setFixedWidth(140)
        progress_row.addWidget(self.frames_label)
        awg_layout.addLayout(progress_row)

        # Test waveform generator
        test_row = QHBoxLayout()
        test_row.addWidget(QLabel("Test:"))
        self.test_type_combo = QComboBox()
        self.test_type_combo.addItems(["sine", "square", "triangle", "sawtooth"])
        test_row.addWidget(self.test_type_combo)
        self.test_freq_spin = QSpinBox()
        self.test_freq_spin.setRange(1, 50000)
        self.test_freq_spin.setValue(1000)
        self.test_freq_spin.setSuffix(" Hz")
        test_row.addWidget(self.test_freq_spin)
        gen_btn = QPushButton("Generate")
        gen_btn.clicked.connect(self._on_generate_test)
        test_row.addWidget(gen_btn)
        test_row.addStretch()
        awg_layout.addLayout(test_row)

        awg_group.setLayout(awg_layout)
        right_layout.addWidget(awg_group, stretch=1)

        # Assemble
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)

        self.tabs.addTab(driver_tab, "Driver")

        # ---- Tab 2: Waveform Editor ----
        self.editor = WaveformEditor(colors=colors, labels=labels)
        self.editor.waveform_ready.connect(self._on_editor_waveform_ready)
        self.tabs.addTab(self.editor, "Waveform Editor")

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.loop_count_label = QLabel("Loop: 0")
        self.fifo_label = QLabel("FIFO: 0")
        self.status_bar.addPermanentWidget(self.fifo_label)
        self.status_bar.addPermanentWidget(self.loop_count_label)

    def _connect_backend(self):
        if isinstance(self.backend, MockBackend):
            self._on_connect()

    # ---- Callbacks ----

    def _on_connect(self):
        if self.backend.is_connected:
            self.backend.disconnect()
            self.conn_label.setText("Disconnected")
            self.conn_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.connect_btn.setText("Connect")
            self.status_bar.showMessage("Disconnected from hardware")
        else:
            if self.backend.connect():
                self.conn_label.setText("Connected")
                self.conn_label.setStyleSheet("color: #27ae60; font-weight: bold;")
                self.connect_btn.setText("Disconnect")
                mode = "Mock" if isinstance(self.backend, MockBackend) else "FPGA"
                self.status_bar.showMessage(f"Connected ({mode} backend)")
            else:
                self.status_bar.showMessage("Connection failed!")

    def _on_settings(self):
        dialog = ChannelSettingsDialog(self.config, self)
        if dialog.exec_() == dialog.Accepted:
            self.config = dialog.get_config()
            self.config.save()
            self._apply_config()
            self.status_bar.showMessage("Channel configuration saved")

    def _on_calibrate(self):
        dialog = CalibrationDialog(self.cal_manager, self.config, self)
        if dialog.exec_() == dialog.Accepted:
            self.cal_manager = dialog.get_calibration()
            self.cal_manager.save(_get_config_dir() / "calibration.json")
            cal_status = "ON" if self.cal_manager.enabled else "OFF"
            self.status_bar.showMessage(
                f"Calibration saved ({self.cal_manager.num_calibrated} channels, "
                f"correction {cal_status})")

    def _apply_config(self):
        """Apply current config to all UI elements."""
        colors = self.config.get_all_colors()
        labels = self.config.get_all_labels()

        # Update channel strips
        for i, strip in enumerate(self.channel_strips):
            strip.update_appearance(colors[i], labels[i])

        # Update driver plot curves
        for i, curve in enumerate(self.plot_curves):
            curve.setPen(pg.mkPen(colors[i], width=1.5))

        # Update editor
        self.editor.update_config(colors, labels)

    def _on_channel_enable(self, channel: int, enabled: bool):
        if self.backend.is_connected:
            self.backend.set_channel_enable(channel, enabled)

    def _on_channel_mode(self, channel: int, mode: int):
        if self.backend.is_connected:
            self.backend.set_channel_mode(channel, mode)

    def _on_channel_intensity(self, channel: int, pct: float):
        if self.backend.is_connected:
            corrected_pct = self.cal_manager.correct_intensity(channel, pct)
            self.backend.set_channel_cw_value(channel, intensity_to_u16(corrected_pct))

    def _on_load_csv(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Load Waveform CSV", "", "CSV Files (*.csv);;All Files (*)")
        if filepath:
            try:
                info = self.waveform_engine.load_csv(filepath)
                self.file_label.setText(Path(filepath).name)
                self.file_label.setStyleSheet("color: white;")
                self._update_waveform_info(info)
                self._update_plot()
                self.play_btn.setEnabled(True)
                self.status_bar.showMessage(
                    f"Loaded: {info.num_samples} samples, {info.num_channels} active channels")
            except Exception as e:
                QMessageBox.critical(self, "Error Loading CSV", str(e))

    def _on_rate_changed(self, value):
        self.waveform_engine.set_sample_rate(value)
        if self.waveform_engine.info:
            self._update_waveform_info(self.waveform_engine.info)
            self._update_plot()
        if self.backend.is_connected:
            ticks = output_rate_to_ticks(value)
            self.backend.set_output_rate(ticks)

    def _on_playback_mode_changed(self, index):
        # 0=single shot, 1=N reps, 2=continuous
        self.reps_spin.setVisible(index == 1)
        self.stop_behavior_combo.setEnabled(index != 2)

    def _on_trigger_mode_changed(self, index):
        if self.backend.is_connected:
            self.backend.set_trigger_mode(index)
        self.trigger_edge_combo.setEnabled(index == 1)
        self.sw_trigger_btn.setVisible(index == 2)

    def _on_trigger_edge_changed(self, index):
        if self.backend.is_connected:
            self.backend.set_trigger_edge(index)

    def _on_trigger_out_changed(self, state):
        if self.backend.is_connected:
            self.backend.set_trigger_out_enable(state == Qt.Checked)

    def _on_sw_trigger(self):
        if self.backend.is_connected:
            self.backend.fire_software_trigger()
            self.status_bar.showMessage("Software trigger fired!")

    def _on_play(self):
        if not self.waveform_engine.is_loaded or not self.backend.is_connected:
            self.status_bar.showMessage("Not ready — load a waveform and connect first")
            return

        # Clear FIFO
        if hasattr(self.backend, 'clear_fifo'):
            self.backend.clear_fifo()

        # Determine playback parameters
        playback_mode = self.playback_mode_combo.currentIndex()
        num_samples = self.waveform_engine.num_samples

        if playback_mode == 0:  # Single shot
            frame_count = num_samples
            reps = 1
        elif playback_mode == 1:  # N repetitions
            reps = self.reps_spin.value()
            frame_count = num_samples * reps
        else:  # Continuous
            frame_count = 0
            reps = 1

        # Set FPGA registers
        self.backend.set_frame_count(frame_count)
        self.backend.set_stop_behavior(self.stop_behavior_combo.currentIndex())

        # Prepare FIFO data (with calibration correction if enabled)
        if playback_mode == 2:  # Continuous — just load one copy, host will re-stream
            data = self.waveform_engine.get_interleaved_u16(self.cal_manager)
        else:
            data = self.waveform_engine.get_repeated_interleaved_u16(reps, self.cal_manager)

        if data is None:
            return

        # Set channel modes
        for strip in self.channel_strips:
            if strip.mode_combo.currentIndex() == 1:
                self.backend.set_channel_mode(strip.channel, 1)

        # Stream data to FIFO
        self.stream_thread = AWGStreamThread(self.backend, data)
        self.stream_thread.progress.connect(self._on_stream_progress)
        self.stream_thread.finished.connect(self._on_stream_finished)
        self.stream_thread.error.connect(self._on_stream_error)
        self.stream_thread.start()

        # Arm the FPGA
        self.backend.set_awg_active(True)
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        trigger_mode = self.trigger_mode_combo.currentIndex()
        if trigger_mode == 0:
            self.status_bar.showMessage("AWG playback running...")
        elif trigger_mode == 1:
            self.status_bar.showMessage("AWG armed — waiting for hardware trigger on DIO1...")
            self.sw_trigger_btn.setEnabled(False)
        elif trigger_mode == 2:
            self.status_bar.showMessage("AWG armed — press 'Fire trigger' to start...")
            self.sw_trigger_btn.setEnabled(True)

    def _on_stop(self):
        if self.stream_thread:
            self.stream_thread.stop()
            self.stream_thread.wait(2000)
            self.stream_thread = None
        self.backend.set_awg_active(False)
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.frames_label.setText("")
        self.status_bar.showMessage("AWG playback stopped")

    def _on_stream_progress(self, pct):
        self.progress_bar.setValue(pct)

    def _on_stream_finished(self):
        self.progress_bar.setValue(100)
        # Don't re-enable play yet if waiting for FPGA completion
        state = self.backend.get_state()
        if state.awg_frame_count == 0:
            # Continuous mode — stream thread finished but FPGA keeps playing
            self.status_bar.showMessage("FIFO loaded — streaming continuously")

    def _on_stream_error(self, msg):
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_bar.showMessage(f"AWG error: {msg}")

    def _on_generate_test(self):
        wtype = self.test_type_combo.currentText()
        freq = self.test_freq_spin.value()
        try:
            info = self.waveform_engine.generate_test_waveform(
                waveform_type=wtype, frequency_hz=freq,
                num_cycles=5, channels=[0, 1])
            self.file_label.setText(f"Test: {wtype} @ {freq} Hz")
            self.file_label.setStyleSheet("color: #f0ad4e;")
            self._update_waveform_info(info)
            self._update_plot()
            self.play_btn.setEnabled(True)
            self.status_bar.showMessage(f"Generated test waveform: {wtype} {freq} Hz")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_editor_waveform_ready(self, data: np.ndarray, sample_rate: float):
        """Receive waveform data from the editor tab."""
        from .waveform import WaveformInfo
        from .hardware import intensity_to_u16

        # Update the waveform engine with editor data
        self.waveform_engine.sample_rate_hz = sample_rate
        self.waveform_engine._raw_data = data
        self.waveform_engine._u16_data = np.vectorize(intensity_to_u16)(data).astype(np.uint16)
        self.waveform_engine._interleaved = self.waveform_engine._u16_data.flatten()
        self.waveform_engine._filepath = "<from editor>"

        num_channels_used = sum(1 for ch in range(NUM_CHANNELS) if np.any(data[:, ch] > 0))
        self.waveform_engine._info = WaveformInfo(
            filepath="<from editor>",
            num_samples=data.shape[0],
            num_channels=num_channels_used,
            duration_s=data.shape[0] / sample_rate,
            sample_rate_hz=sample_rate,
            min_values=[float(data[:, ch].min()) for ch in range(NUM_CHANNELS)],
            max_values=[float(data[:, ch].max()) for ch in range(NUM_CHANNELS)],
        )

        # Update the driver tab UI
        self.rate_spin.setValue(int(sample_rate))
        self.file_label.setText("From editor")
        self.file_label.setStyleSheet("color: #3498db;")
        self._update_waveform_info(self.waveform_engine._info)
        self._update_plot()
        self.play_btn.setEnabled(True)

        # Switch to the driver tab
        self.tabs.setCurrentIndex(0)
        self.status_bar.showMessage(
            f"Waveform loaded from editor: {data.shape[0]} frames at {sample_rate/1000:.0f} kHz")

    def _all_on(self):
        for strip in self.channel_strips:
            strip.enable_btn.setChecked(True)
            strip._on_enable_toggled(True)

    def _all_off(self):
        for strip in self.channel_strips:
            strip.enable_btn.setChecked(False)
            strip._on_enable_toggled(False)

    def _all_cw(self):
        for strip in self.channel_strips:
            strip.mode_combo.setCurrentIndex(0)

    def _all_awg(self):
        for strip in self.channel_strips:
            strip.mode_combo.setCurrentIndex(1)

    # ---- Display updates ----

    def _update_waveform_info(self, info):
        duration = info.duration_s
        if duration < 0.001:
            dur_str = f"{duration * 1e6:.1f} us"
        elif duration < 1.0:
            dur_str = f"{duration * 1e3:.2f} ms"
        else:
            dur_str = f"{duration:.3f} s"
        self.waveform_info_label.setText(
            f"{info.num_samples:,} frames  |  {dur_str}  |  "
            f"{info.sample_rate_hz / 1000:.0f} kHz  |  "
            f"{info.num_channels} active ch")

    def _update_plot(self):
        t = self.waveform_engine.get_time_axis()
        if t is None:
            return
        for ch in range(NUM_CHANNELS):
            data = self.waveform_engine.get_channel_data(ch)
            if data is not None and np.any(data > 0):
                if len(t) > 10000:
                    step = len(t) // 5000
                    self.plot_curves[ch].setData(t[::step], data[::step])
                else:
                    self.plot_curves[ch].setData(t, data)
            else:
                self.plot_curves[ch].clear()

    def _update_status(self):
        if not self.backend.is_connected:
            return

        state = self.backend.get_state()
        self.loop_count_label.setText(f"Loop: {state.loop_count:,}")
        fifo_depth = self.backend.get_fifo_depth()
        self.fifo_label.setText(f"FIFO: {fifo_depth:,}")

        if state.fifo_underflow:
            self.status_bar.showMessage("FIFO underflow detected!")

        # Frames played counter
        if state.awg_running or state.awg_complete:
            if state.awg_frame_count > 0:
                self.frames_label.setText(
                    f"{state.awg_frames_played:,} / {state.awg_frame_count:,} frames")
            else:
                self.frames_label.setText(f"{state.awg_frames_played:,} frames")

        # Armed/running/complete indicator
        if state.awg_armed:
            self.armed_label.setText("ARMED — waiting for trigger")
            self.armed_label.setStyleSheet("color: #e67e22; font-weight: bold; font-size: 11px;")
        elif state.awg_running:
            self.armed_label.setText("RUNNING")
            self.armed_label.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 11px;")
        elif state.awg_complete:
            self.armed_label.setText("COMPLETE")
            self.armed_label.setStyleSheet("color: #3498db; font-weight: bold; font-size: 11px;")
            self.play_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        else:
            self.armed_label.setText("")
            self.frames_label.setText("")

    def closeEvent(self, event):
        if self.stream_thread:
            self.stream_thread.stop()
            self.stream_thread.wait(2000)
        if self.backend.is_connected:
            for ch in range(NUM_CHANNELS):
                self.backend.set_channel_enable(ch, False)
            self.backend.disconnect()
        event.accept()
