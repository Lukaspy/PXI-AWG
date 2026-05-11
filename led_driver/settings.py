"""
Settings dialog for configuring channel wavelengths.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QPushButton, QLabel, QDoubleSpinBox, QCheckBox, QLineEdit,
    QDialogButtonBox, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from .hardware import NUM_CHANNELS
from .config import AppConfig, ChannelConfig, wavelength_to_hex, DEFAULT_COLORS


class ChannelConfigRow(QFrame):
    """Single row for configuring one channel's wavelength."""

    def __init__(self, channel: int, config: ChannelConfig, parent=None):
        super().__init__(parent)
        self.channel = channel
        self._setup_ui(config)

    def _setup_ui(self, config: ChannelConfig):
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 2, 4, 2)

        # Color swatch
        self.swatch = QLabel("  ")
        self.swatch.setFixedSize(24, 24)
        self.swatch.setStyleSheet(f"background-color: {DEFAULT_COLORS[self.channel]}; "
                                   "border-radius: 4px;")
        layout.addWidget(self.swatch)

        # Channel number
        self.ch_label = QLabel(f"Ch {self.channel}:")
        self.ch_label.setFixedWidth(45)
        layout.addWidget(self.ch_label)

        # Enable wavelength
        self.enable_check = QCheckBox("Wavelength:")
        self.enable_check.setChecked(config.has_wavelength)
        self.enable_check.stateChanged.connect(self._on_enable_changed)
        layout.addWidget(self.enable_check)

        # Wavelength spinbox
        self.wavelength_spin = QDoubleSpinBox()
        self.wavelength_spin.setRange(200, 1100)
        self.wavelength_spin.setValue(config.wavelength_nm if config.wavelength_nm else 550)
        self.wavelength_spin.setSuffix(" nm")
        self.wavelength_spin.setDecimals(0)
        self.wavelength_spin.setSingleStep(5)
        self.wavelength_spin.setFixedWidth(100)
        self.wavelength_spin.setEnabled(config.has_wavelength)
        self.wavelength_spin.valueChanged.connect(self._update_preview)
        layout.addWidget(self.wavelength_spin)

        # Custom label (optional)
        layout.addWidget(QLabel("Label:"))
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("auto (e.g. 470 nm)")
        self.label_edit.setFixedWidth(120)
        if config.custom_label:
            self.label_edit.setText(config.custom_label)
        layout.addWidget(self.label_edit)

        # Preview label
        self.preview_label = QLabel("")
        self.preview_label.setFixedWidth(80)
        layout.addWidget(self.preview_label)

        layout.addStretch()
        self.setLayout(layout)
        self._update_preview()

    def _on_enable_changed(self, state):
        enabled = state == Qt.Checked
        self.wavelength_spin.setEnabled(enabled)
        self._update_preview()

    def _update_preview(self):
        if self.enable_check.isChecked():
            nm = self.wavelength_spin.value()
            color = wavelength_to_hex(nm)
            self.swatch.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
            self.preview_label.setText(f"{nm:.0f} nm")
            self.preview_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        else:
            color = DEFAULT_COLORS[self.channel]
            self.swatch.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
            self.preview_label.setText("default")
            self.preview_label.setStyleSheet("color: gray;")

    def get_config(self) -> ChannelConfig:
        if self.enable_check.isChecked():
            label = self.label_edit.text().strip() or None
            return ChannelConfig(
                wavelength_nm=self.wavelength_spin.value(),
                custom_label=label)
        return ChannelConfig()


class ChannelSettingsDialog(QDialog):
    """Dialog for configuring channel wavelengths and labels."""

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Channel Configuration")
        self.setMinimumWidth(550)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()

        # Instructions
        info = QLabel(
            "Assign wavelengths to channels to match your LED source. "
            "Colors will automatically match the visible spectrum. "
            "Leave unchecked to use default colors and labels.")
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 8px;")
        layout.addWidget(info)

        # Preset buttons
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Presets:"))

        clear_btn = QPushButton("Clear all")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._preset_clear)
        preset_row.addWidget(clear_btn)

        preset_row.addStretch()
        layout.addLayout(preset_row)

        # Channel rows
        self.rows = []
        for i in range(NUM_CHANNELS):
            row = ChannelConfigRow(i, self.config.channels[i])
            self.rows.append(row)
            layout.addWidget(row)

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def _preset_clear(self):
        for row in self.rows:
            row.enable_check.setChecked(False)
            row.label_edit.clear()

    def get_config(self) -> AppConfig:
        """Get the updated config from the dialog."""
        config = AppConfig()
        for i, row in enumerate(self.rows):
            config.channels[i] = row.get_config()
        return config
