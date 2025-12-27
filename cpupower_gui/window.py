# window.py
#
# Copyright 2019-2020 Evangelos Rigas
# Copyright 2025 Felipe Figueiredo <felipefmavelar@gmail.com> (Qt port)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from contextlib import contextmanager

import dbus
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QGroupBox, QLabel, QComboBox, QSlider, QDoubleSpinBox,
    QCheckBox, QPushButton, QTableView, QHeaderView, QScrollArea,
    QMessageBox, QLineEdit, QFrame, QSizePolicy, QToolBar, QSpacerItem,
    QAbstractItemView, QFormLayout, QDialog, QDialogButtonBox, QTextBrowser
)
from PyQt6.QtCore import Qt, QTimer, QAbstractTableModel, QModelIndex
from PyQt6.QtGui import QIcon, QAction, QFont, QColor

from .config import CpuPowerConfig, CpuSettings
from .utils import read_available_frequencies, read_current_freq

BUS = dbus.SystemBus()
SESSION = BUS.get_object(
    "org.rnd2.cpupower_gui.helper", "/org/rnd2/cpupower_gui/helper"
)

HELPER = dbus.Interface(SESSION, "org.rnd2.cpupower_gui.helper")

ERRORS = {
    -11: "Setting governor failed.",
    -12: "Setting energy preferences failed.",
    -13: "Setting frequencies failed.",
    -23: "Setting governor and energy preferences failed.",
    -24: "Setting governor and frequencies failed.",
    -25: "Setting frequencies and energy preferences failed.",
}


def error_message(msg, parent=None):
    """Show an error message dialog"""
    QMessageBox.critical(parent, "Error", msg)


class CpuTableModel(QAbstractTableModel):
    """Table model for CPU data"""

    HEADERS = ["CPU", "Online", "Min", "Max", "Governor", "Current freq."]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []  # List of [cpu, online, fmin, fmax, governor, current_freq, changed]

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return 6

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:  # CPU
                return str(self._data[row][0])
            elif col == 1:  # Online
                return None  # Checkbox handled separately
            elif col in (2, 3, 5):  # Min, Max, Current freq
                return f"{self._data[row][col]:.2f}"
            elif col == 4:  # Governor
                return self._data[row][col]
        elif role == Qt.ItemDataRole.CheckStateRole:
            if col == 1:  # Online column
                return Qt.CheckState.Checked if self._data[row][1] else Qt.CheckState.Unchecked
        elif role == Qt.ItemDataRole.ForegroundRole:
            if self._data[row][6]:  # Changed flag
                return QColor(Qt.GlobalColor.red)

        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False

        row = index.row()
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == 1:
            self._data[row][1] = (value == Qt.CheckState.Checked)
            self.dataChanged.emit(index, index)
            return True

        return False

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

        if index.column() == 1:  # Online column
            flags |= Qt.ItemFlag.ItemIsUserCheckable

        return flags

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def set_data(self, data):
        """Set the table data"""
        self.beginResetModel()
        self._data = data
        self.endResetModel()

    def update_row(self, row, data):
        """Update a single row"""
        if 0 <= row < len(self._data):
            self._data[row] = data
            self.dataChanged.emit(
                self.index(row, 0),
                self.index(row, self.columnCount() - 1)
            )

    def update_current_freq(self, row, freq):
        """Update current frequency for a row"""
        if 0 <= row < len(self._data):
            self._data[row][5] = freq
            index = self.index(row, 5)
            self.dataChanged.emit(index, index)

    def set_changed(self, row, changed):
        """Set the changed flag for a row"""
        if 0 <= row < len(self._data):
            self._data[row][6] = changed
            self.dataChanged.emit(
                self.index(row, 0),
                self.index(row, self.columnCount() - 1)
            )


class CpupowerGuiWindow(QMainWindow):
    """Main window for cpupower-gui Qt version"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("CPU Power GUI")
        self.setWindowIcon(QIcon.fromTheme("org.rnd2.cpupower_gui", QIcon.fromTheme("cpu")))
        self.setMinimumSize(400, 500)
        self.resize(500, 600)

        # Read configuration
        self.conf = CpuPowerConfig()
        self.gui_conf = self.conf.get_gui_settings()
        self.profiles = self.conf.profiles
        self.profile = None

        self.refreshing = False
        self.settings = {}
        self.energy_pref_avail = False
        self.energy_per_cpu = False
        self.tick_marks_enabled = True
        self.ticks_markup = True

        # Initialize settings
        self.load_cpu_settings()

        # Setup UI
        self._setup_toolbar()
        self._setup_central_widget()
        self._configure_gui()

        # Start timer for updating current frequencies
        self.freq_timer = QTimer(self)
        self.freq_timer.timeout.connect(self._update_current_freq)
        self.freq_timer.start(500)

    @contextmanager
    def lock(self):
        """Helper function to stop widgets from refreshing"""
        self.refreshing = True
        yield
        self.refreshing = False

    def load_cpu_settings(self):
        """Initialize the configuration store"""
        for cpu in self.online_cpus:
            self.settings[cpu] = CpuSettings(cpu)
        if self.settings:
            self.energy_pref_avail = self.settings[0].energy_pref_avail

    @property
    def online_cpus(self):
        """Convenience function to get a list of available CPUs"""
        avail = HELPER.get_cpus_available()
        if avail:
            return [int(cpu) for cpu in avail]
        return avail

    @property
    def is_conf_changed(self):
        """Helper function to check if settings were changed"""
        changed = [cpu for cpu, conf in self.settings.items() if conf.changed]
        return len(changed) > 0

    def _setup_toolbar(self):
        """Setup the toolbar with actions"""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Refresh button
        refresh_action = QAction(QIcon.fromTheme("view-refresh"), "Refresh", self)
        refresh_action.setToolTip("Refresh CPU settings")
        refresh_action.triggered.connect(self.on_refresh_clicked)
        toolbar.addAction(refresh_action)

        # All CPUs toggle
        self.toall_btn = QPushButton()
        self.toall_btn.setIcon(QIcon.fromTheme("edit-select-all"))
        self.toall_btn.setToolTip("Apply to all CPUs")
        self.toall_btn.setCheckable(True)
        self.toall_btn.toggled.connect(self.on_toall_toggled)
        toolbar.addWidget(self.toall_btn)

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # Apply button
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setToolTip("Apply changes")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.on_apply_clicked)
        # Style as suggested action (accent color)
        self.apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #3daee9;
                color: white;
                border: none;
                padding: 6px 16px;
                border-radius: 4px;
            }
            QPushButton:disabled {
                background-color: #4d4d4d;
                color: #808080;
            }
            QPushButton:hover:!disabled {
                background-color: #2da0dc;
            }
        """)
        toolbar.addWidget(self.apply_btn)

        # Menu button
        menu_btn = QPushButton()
        menu_btn.setIcon(QIcon.fromTheme("application-menu", QIcon.fromTheme("open-menu-symbolic")))
        menu_btn.setToolTip("Menu")
        toolbar.addWidget(menu_btn)

        # Create menu
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about_dialog)
        menu.addAction(about_action)
        menu_btn.setMenu(menu)

    def _setup_central_widget(self):
        """Setup the central widget with tabs"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._create_settings_page(), QIcon.fromTheme("preferences-system"), "Settings")
        self.tab_widget.addTab(self._create_preferences_page(), QIcon.fromTheme("preferences-desktop"), "Preferences")
        self.tab_widget.addTab(self._create_profiles_page(), QIcon.fromTheme("org.rnd2.cpupower_gui"), "Profiles")
        layout.addWidget(self.tab_widget)

    def _create_settings_page(self):
        """Create the main settings page"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # Frequency Profiles group
        profiles_group = QGroupBox("Frequency profiles")
        profiles_layout = QVBoxLayout(profiles_group)

        # Profile selector
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self.on_profile_changed)
        profile_row.addWidget(self.profile_combo, 1)
        profiles_layout.addLayout(profile_row)

        # CPU Table
        self.cpu_table = QTableView()
        self.cpu_model = CpuTableModel(self)
        self.cpu_table.setModel(self.cpu_model)
        self.cpu_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.cpu_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.cpu_table.selectionModel().selectionChanged.connect(self.on_table_selection_changed)
        self.cpu_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.cpu_table.setMinimumHeight(150)
        self.cpu_table.setMaximumHeight(200)
        profiles_layout.addWidget(self.cpu_table)

        layout.addWidget(profiles_group)

        # Frequency Settings group
        freq_group = QGroupBox("Frequency settings")
        freq_layout = QVBoxLayout(freq_group)

        # CPU selector with online checkbox
        cpu_row = QHBoxLayout()
        self.cpu_online_check = QCheckBox()
        self.cpu_online_check.toggled.connect(self.on_cpu_online_toggled)
        cpu_row.addWidget(self.cpu_online_check)
        cpu_row.addWidget(QLabel("CPU:"))
        self.cpu_combo = QComboBox()
        self.cpu_combo.currentIndexChanged.connect(self.on_cpu_changed)
        cpu_row.addWidget(self.cpu_combo, 1)
        freq_layout.addLayout(cpu_row)

        # Frequency sliders grid
        freq_grid = QGridLayout()
        freq_grid.setSpacing(8)

        # Min frequency
        freq_grid.addWidget(QLabel("Min freq. (MHz)"), 0, 0)
        freq_grid.addWidget(QLabel("Max freq. (MHz)"), 0, 1)

        self.min_slider = QSlider(Qt.Orientation.Horizontal)
        self.min_slider.valueChanged.connect(self.on_min_slider_changed)
        freq_grid.addWidget(self.min_slider, 1, 0)

        self.max_slider = QSlider(Qt.Orientation.Horizontal)
        self.max_slider.valueChanged.connect(self.on_max_slider_changed)
        freq_grid.addWidget(self.max_slider, 1, 1)

        self.min_spin = QDoubleSpinBox()
        self.min_spin.setDecimals(2)
        self.min_spin.valueChanged.connect(self.on_min_spin_changed)
        freq_grid.addWidget(self.min_spin, 2, 0)

        self.max_spin = QDoubleSpinBox()
        self.max_spin.setDecimals(2)
        self.max_spin.valueChanged.connect(self.on_max_spin_changed)
        freq_grid.addWidget(self.max_spin, 2, 1)

        freq_layout.addLayout(freq_grid)
        layout.addWidget(freq_group)

        # Power Settings group
        power_group = QGroupBox("Power settings")
        power_layout = QFormLayout(power_group)

        # Governor selector
        self.gov_combo = QComboBox()
        self.gov_combo.currentIndexChanged.connect(self.on_governor_changed)
        power_layout.addRow("Governor policy:", self.gov_combo)

        # Energy preference selector
        self.energy_combo = QComboBox()
        self.energy_combo.currentIndexChanged.connect(self.on_energy_pref_changed)
        power_layout.addRow("Energy preference:", self.energy_combo)

        layout.addWidget(power_group)

        # Add stretch to push everything up
        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def _create_preferences_page(self):
        """Create the preferences page"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # Power settings group
        power_group = QGroupBox("Power settings")
        power_layout = QVBoxLayout(power_group)

        # Default profile selector
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Default profile at boot:"))
        self.default_profile_combo = QComboBox()
        self.default_profile_combo.currentIndexChanged.connect(self.on_default_profile_changed)
        profile_row.addWidget(self.default_profile_combo, 1)
        power_layout.addLayout(profile_row)

        # Energy preference per CPU
        self.energy_per_cpu_check = QCheckBox("Energy performance preference per CPU")
        self.energy_per_cpu_check.toggled.connect(self.on_energy_per_cpu_changed)
        power_layout.addWidget(self.energy_per_cpu_check)

        layout.addWidget(power_group)

        # GUI settings group
        gui_group = QGroupBox("GUI settings")
        gui_layout = QVBoxLayout(gui_group)

        # All CPUs toggle default
        self.default_allcpus_check = QCheckBox("All CPUs toggle enabled by default")
        self.default_allcpus_check.toggled.connect(self.on_default_allcpus_changed)
        gui_layout.addWidget(self.default_allcpus_check)

        # Display tick marks
        self.default_ticks_check = QCheckBox("Display tick marks")
        self.default_ticks_check.toggled.connect(self.on_default_ticks_changed)
        gui_layout.addWidget(self.default_ticks_check)

        # Display frequency at tick marks
        self.default_ticks_num_check = QCheckBox("Display frequency at tick marks")
        self.default_ticks_num_check.toggled.connect(self.on_default_ticks_num_changed)
        gui_layout.addWidget(self.default_ticks_num_check)

        layout.addWidget(gui_group)

        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def _create_profiles_page(self):
        """Create the profiles management page"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # New profile group
        new_group = QGroupBox("New profile")
        new_layout = QHBoxLayout(new_group)

        self.profile_name_entry = QLineEdit()
        self.profile_name_entry.setPlaceholderText("Profile name")
        self.profile_name_entry.textChanged.connect(self.on_profile_name_changed)
        new_layout.addWidget(self.profile_name_entry, 1)

        self.save_profile_btn = QPushButton()
        self.save_profile_btn.setIcon(QIcon.fromTheme("document-save"))
        self.save_profile_btn.setToolTip("Save profile")
        self.save_profile_btn.setEnabled(False)
        self.save_profile_btn.clicked.connect(self.on_save_profile_clicked)
        new_layout.addWidget(self.save_profile_btn)

        layout.addWidget(new_group)

        # User profiles group
        self.user_profiles_group = QGroupBox("User profiles")
        self.user_profiles_layout = QVBoxLayout(self.user_profiles_group)
        layout.addWidget(self.user_profiles_group)

        # System profiles group
        self.system_profiles_group = QGroupBox("System profiles")
        self.system_profiles_layout = QVBoxLayout(self.system_profiles_group)
        layout.addWidget(self.system_profiles_group)

        # Built-in profiles group
        self.builtin_profiles_group = QGroupBox("Built-in profiles")
        self.builtin_profiles_layout = QVBoxLayout(self.builtin_profiles_group)
        layout.addWidget(self.builtin_profiles_group)

        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def _configure_gui(self):
        """Configure GUI based on config file"""
        # Update CPU combo
        self._update_cpu_combo()

        # Update table
        self._update_table()

        # Configure preferences - block signals to prevent handlers from firing during init
        toggle_default = self.gui_conf.getboolean("allcpus_default", False)
        self.toall_btn.setChecked(toggle_default)
        self.default_allcpus_check.blockSignals(True)
        self.default_allcpus_check.setChecked(toggle_default)
        self.default_allcpus_check.blockSignals(False)

        default_ticks = self.gui_conf.getboolean("tick_marks_enabled", True)
        self.tick_marks_enabled = default_ticks
        self.default_ticks_check.blockSignals(True)
        self.default_ticks_check.setChecked(default_ticks)
        self.default_ticks_check.blockSignals(False)

        default_ticks_num = self.gui_conf.getboolean("frequency_ticks", True)
        self.ticks_markup = default_ticks_num
        self.default_ticks_num_check.blockSignals(True)
        self.default_ticks_num_check.setChecked(default_ticks_num)
        self.default_ticks_num_check.blockSignals(False)

        default_energy_percpu = self.gui_conf.getboolean("energy_pref_per_cpu", False)
        self.energy_per_cpu = default_energy_percpu
        self.energy_per_cpu_check.blockSignals(True)
        self.energy_per_cpu_check.setChecked(default_energy_percpu)
        self.energy_per_cpu_check.blockSignals(False)

        # Update profile boxes
        self._update_profile_boxes()

        # Generate profiles page
        self._generate_profiles_list()

        # Check if energy prefs available
        if self.energy_pref_avail:
            self.energy_combo.setVisible(True)
            self.energy_per_cpu_check.setVisible(True)
        else:
            self.energy_combo.setVisible(False)
            self.energy_per_cpu_check.setVisible(False)

        # Initial slider update
        self._update_sliders()

    def _update_cpu_combo(self):
        """Update the CPU combo box"""
        self.cpu_combo.blockSignals(True)
        self.cpu_combo.clear()
        for cpu in self.online_cpus:
            self.cpu_combo.addItem(f"CPU {cpu}", cpu)
        self.cpu_combo.setCurrentIndex(0)
        self.cpu_combo.blockSignals(False)

    def _update_table(self):
        """Update the CPU table"""
        data = []
        for cpu, conf in self.settings.items():
            fmin, fmax = conf.freqs
            data.append([
                cpu,
                conf.online,
                fmin,
                fmax,
                conf.governor.capitalize() if conf.governor else "",
                0.0,
                conf.changed
            ])
        self.cpu_model.set_data(data)

    def _update_profile_boxes(self):
        """Update profile combo boxes"""
        # Main profile combo
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("No profile", None)
        for prof_name in self.conf.profiles:
            self.profile_combo.addItem(prof_name, prof_name)
        self.profile_combo.blockSignals(False)

        # Default profile combo in preferences
        self.default_profile_combo.blockSignals(True)
        self.default_profile_combo.clear()
        for prof_name in self.conf.profiles:
            self.default_profile_combo.addItem(prof_name, prof_name)
        index = self.conf.get_profile_index(self.conf.default_profile)
        if index >= 0:
            self.default_profile_combo.setCurrentIndex(index)
        self.default_profile_combo.blockSignals(False)

    def _generate_profiles_list(self):
        """Generate profile listings"""
        # Clear existing
        self._clear_layout(self.user_profiles_layout)
        self._clear_layout(self.system_profiles_layout)
        self._clear_layout(self.builtin_profiles_layout)

        for prof_name in self.conf.profiles:
            profile = self.conf.get_profile(prof_name)
            if profile._custom:
                if not profile.system:
                    self._add_profile_row(prof_name, self.user_profiles_layout, deletable=True)
                else:
                    self._add_profile_row(prof_name, self.system_profiles_layout)
            else:
                self._add_profile_row(prof_name, self.builtin_profiles_layout)

    def _add_profile_row(self, name, layout, deletable=False):
        """Add a profile row to a layout"""
        row = QHBoxLayout()
        row.addWidget(QLabel(name))
        row.addStretch()

        if deletable:
            delete_btn = QPushButton()
            delete_btn.setIcon(QIcon.fromTheme("edit-delete"))
            delete_btn.setToolTip("Delete profile")
            delete_btn.clicked.connect(lambda: self.on_delete_profile_clicked(name))
            row.addWidget(delete_btn)

        widget = QWidget()
        widget.setLayout(row)
        layout.addWidget(widget)

    def _clear_layout(self, layout):
        """Clear all widgets from a layout"""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _update_sliders(self):
        """Update sliders and controls for current CPU"""
        cpu = self._get_active_cpu()
        conf = self.settings.get(cpu)
        if not conf:
            return

        freq_min_hw, freq_max_hw = conf.hw_lims
        cpu_online = conf.online
        freq_min, freq_max = conf.freqs

        with self.lock():
            # Update governor combo
            self._update_gov_combo()

            # Update energy pref combo
            if self.energy_pref_avail:
                self._update_energy_pref_combo()

            # Update sliders
            self._set_sliders_sensitive(cpu_online)

            # Set slider ranges (using integer values for slider)
            self.min_slider.setRange(int(freq_min_hw), int(freq_max_hw))
            self.max_slider.setRange(int(freq_min_hw), int(freq_max_hw))
            self.min_slider.setValue(int(freq_min))
            self.max_slider.setValue(int(freq_max))

            # Set spin box ranges
            self.min_spin.setRange(freq_min_hw, freq_max_hw)
            self.max_spin.setRange(freq_min_hw, freq_max_hw)
            self.min_spin.setValue(freq_min)
            self.max_spin.setValue(freq_max)

            # Update online checkbox
            self.cpu_online_check.setChecked(cpu_online)
            self.cpu_online_check.setEnabled(bool(HELPER.cpu_allowed_offline(cpu)))

            # Update apply button
            self.apply_btn.setEnabled(self.is_conf_changed)

    def _get_active_cpu(self):
        """Get the currently selected CPU"""
        index = self.cpu_combo.currentIndex()
        if index >= 0:
            return self.cpu_combo.currentData()
        return 0

    def _set_sliders_sensitive(self, state):
        """Enable/disable frequency controls"""
        self.min_slider.setEnabled(state)
        self.max_slider.setEnabled(state)
        self.min_spin.setEnabled(state)
        self.max_spin.setEnabled(state)
        self.gov_combo.setEnabled(state)
        self.energy_combo.setEnabled(state)

    def _update_gov_combo(self):
        """Update the governor combo box"""
        cpu = self._get_active_cpu()
        conf = self.settings.get(cpu)

        governor = conf.govid
        governors = conf.governors

        self.gov_combo.blockSignals(True)
        self.gov_combo.clear()

        if governor is not None:
            for gov in governors:
                self.gov_combo.addItem(gov.capitalize(), gov)
            self.gov_combo.setCurrentIndex(governor)
            self.gov_combo.setEnabled(True)
        else:
            self.gov_combo.setEnabled(False)

        self.gov_combo.blockSignals(False)

    def _update_energy_pref_combo(self):
        """Update the energy preference combo box"""
        cpu = self._get_active_cpu()
        conf = self.settings.get(cpu)

        energy_pref = conf.energy_pref_id
        energy_prefs = conf.energy_prefs

        self.energy_combo.blockSignals(True)
        self.energy_combo.clear()

        if energy_pref != -1:
            for pref in energy_prefs:
                display_pref = pref.replace("_", " ").capitalize()
                self.energy_combo.addItem(display_pref, pref)
            self.energy_combo.setCurrentIndex(energy_pref)
            self.energy_combo.setEnabled(True)
        else:
            self.energy_combo.setEnabled(False)

        self.energy_combo.blockSignals(False)

    def _update_current_freq(self):
        """Timer callback to update current frequencies"""
        for i, cpu in enumerate(self.online_cpus):
            current_freq = read_current_freq(cpu) / 1e3
            self.cpu_model.update_current_freq(i, current_freq)

    def _refresh_cpu_settings(self, cpu):
        """Refresh settings for a CPU"""
        self.settings[cpu].update_conf()
        self._update_table()
        self._update_sliders()

    def _update_settings_freqs(self, cpu, fmin, fmax):
        """Update frequency settings"""
        if self.toall_btn.isChecked():
            for c, conf in self.settings.items():
                conf.freqs = (fmin, fmax)
                self.cpu_model.set_changed(c, conf.changed)
        else:
            conf = self.settings.get(cpu)
            if conf:
                conf.freqs = (fmin, fmax)
                self.cpu_model.set_changed(cpu, conf.changed)

        self._update_table()

    # Signal handlers

    def on_cpu_changed(self, index):
        """CPU combo changed"""
        if self.refreshing:
            return
        # Sync table selection
        self.cpu_table.selectRow(index)
        self._update_sliders()

    def on_table_selection_changed(self):
        """Table selection changed"""
        indexes = self.cpu_table.selectionModel().selectedRows()
        if indexes:
            row = indexes[0].row()
            self.cpu_combo.setCurrentIndex(row)

    def on_cpu_online_toggled(self, checked):
        """CPU online checkbox toggled"""
        if self.refreshing:
            return

        cpu = self._get_active_cpu()
        conf = self.settings[cpu]
        conf.online = checked
        self._set_sliders_sensitive(checked)
        self._update_table()
        self.apply_btn.setEnabled(self.is_conf_changed)

    def on_min_slider_changed(self, value):
        """Min frequency slider changed"""
        if self.refreshing:
            return
        self.min_spin.blockSignals(True)
        self.min_spin.setValue(value)
        self.min_spin.blockSignals(False)
        self._on_freq_changed()

    def on_max_slider_changed(self, value):
        """Max frequency slider changed"""
        if self.refreshing:
            return
        self.max_spin.blockSignals(True)
        self.max_spin.setValue(value)
        self.max_spin.blockSignals(False)
        self._on_freq_changed()

    def on_min_spin_changed(self, value):
        """Min frequency spin box changed"""
        if self.refreshing:
            return
        self.min_slider.blockSignals(True)
        self.min_slider.setValue(int(value))
        self.min_slider.blockSignals(False)
        self._on_freq_changed()

    def on_max_spin_changed(self, value):
        """Max frequency spin box changed"""
        if self.refreshing:
            return
        self.max_slider.blockSignals(True)
        self.max_slider.setValue(int(value))
        self.max_slider.blockSignals(False)
        self._on_freq_changed()

    def _on_freq_changed(self):
        """Handle frequency change"""
        cpu = self._get_active_cpu()
        fmin = self.min_spin.value()
        fmax = self.max_spin.value()

        conf = self.settings[cpu]
        fmin_hw, fmax_hw = conf.hw_lims

        # Clamp values
        if fmin > fmax:
            fmax = min(fmin + 10, fmax_hw)
        elif fmax < fmin:
            fmin = max(fmax - 10, fmin_hw)

        with self.lock():
            self.min_spin.setValue(fmin)
            self.max_spin.setValue(fmax)
            self.min_slider.setValue(int(fmin))
            self.max_slider.setValue(int(fmax))

        self._update_settings_freqs(cpu, fmin, fmax)
        self.apply_btn.setEnabled(self.is_conf_changed)

    def on_governor_changed(self, index):
        """Governor combo changed"""
        if self.refreshing or index < 0:
            return

        gov = self.gov_combo.currentData()
        cpu = self._get_active_cpu()

        if self.toall_btn.isChecked():
            for c, conf in self.settings.items():
                conf.governor = gov
                self.cpu_model.set_changed(c, conf.changed)
        else:
            conf = self.settings.get(cpu)
            if conf:
                conf.governor = gov
                self.cpu_model.set_changed(cpu, conf.changed)

        self._update_table()
        self.apply_btn.setEnabled(self.is_conf_changed)

    def on_energy_pref_changed(self, index):
        """Energy preference combo changed"""
        if self.refreshing or index < 0:
            return

        pref = self.energy_combo.currentData()
        cpu = self._get_active_cpu()

        if self.energy_per_cpu:
            conf = self.settings.get(cpu)
            if conf:
                conf.energy_pref = pref
                self.cpu_model.set_changed(cpu, conf.changed)
        else:
            for c, conf in self.settings.items():
                conf.energy_pref = pref
                self.cpu_model.set_changed(c, conf.changed)

        self.apply_btn.setEnabled(self.is_conf_changed)

    def on_profile_changed(self, index):
        """Profile combo changed"""
        if self.refreshing or index < 0:
            return

        prof_name = self.profile_combo.currentData()
        if prof_name is None:
            # No profile - reset
            self.toall_btn.setEnabled(True)
            self.load_cpu_settings()
            for cpu, conf in self.settings.items():
                conf.reset_conf()
            self._update_table()
            self._update_sliders()
            return

        profile = self.conf.get_profile(prof_name)
        if profile:
            self._set_profile_settings(profile)
            self._update_sliders()

        self.apply_btn.setEnabled(self.is_conf_changed)

    def _set_profile_settings(self, profile):
        """Apply profile settings"""
        self.load_cpu_settings()
        self.toall_btn.setChecked(False)
        self.toall_btn.setEnabled(False)

        prof_settings = profile.settings
        for cpu, settings in prof_settings.items():
            conf = self.settings.get(cpu)
            if not conf:
                continue
            conf.freqs_scaled = settings["freqs"]
            if settings["governor"] in conf.governors:
                conf.governor = settings["governor"]
            conf.online = settings["online"]

        self._update_table()

    def on_toall_toggled(self, checked):
        """Apply to all CPUs toggle"""
        if checked:
            cpu = self._get_active_cpu()
            settings = self.settings[cpu]
            for c, conf in self.settings.items():
                conf.freqs = settings.freqs
                conf.governor = settings.governor
                conf.online = settings.online
                conf.energy_pref = settings.energy_pref
            self._update_table()
        self.apply_btn.setEnabled(self.is_conf_changed)

    def on_refresh_clicked(self):
        """Refresh button clicked"""
        if self.toall_btn.isChecked():
            for cpu in self.settings.keys():
                self._refresh_cpu_settings(cpu)
        else:
            cpu = self._get_active_cpu()
            self._refresh_cpu_settings(cpu)

    def on_apply_clicked(self):
        """Apply button clicked"""
        ret = 0

        if not HELPER.isauthorized():
            error_message("You don't have permissions to update CPU settings!", self)
            return

        # Update only changed CPUs
        changed_cpus = [cpu for cpu, conf in self.settings.items() if conf.changed]
        for cpu in changed_cpus:
            conf = self.settings.get(cpu)
            cpu_online = conf.online
            ret += self._set_cpu_online(cpu)

            if cpu_online:
                if conf.setting_changed("freqs"):
                    ret += self._set_cpu_frequencies(cpu)
                if conf.setting_changed("governor"):
                    ret += self._set_cpu_governor(cpu)
                if conf.setting_changed("energy_pref"):
                    ret += self._set_cpu_energy_preferences(cpu)

        for cpu in self.settings.keys():
            self._refresh_cpu_settings(cpu)

        self.profile_combo.setCurrentIndex(0)
        self.load_cpu_settings()
        self._update_sliders()

        if ret == 0:
            self.apply_btn.setEnabled(False)
        else:
            error = ERRORS.get(ret, "Unknown error occurred.")
            error_message(error, self)

    @staticmethod
    def is_online(cpu):
        """Check if CPU is online"""
        online = HELPER.get_cpus_online()
        present = HELPER.get_cpus_present()
        return (cpu in present) and (cpu in online)

    @staticmethod
    def is_offline(cpu):
        """Check if CPU is offline"""
        offline = HELPER.get_cpus_offline()
        present = HELPER.get_cpus_present()
        return (cpu in present) and (cpu in offline)

    def _set_cpu_online(self, cpu):
        """Set CPU online/offline"""
        conf = self.settings.get(cpu)
        if conf is None:
            return 0

        cpu_online = conf.online

        if self.is_offline(cpu) and cpu_online:
            ret = HELPER.set_cpu_online(cpu)
            self._update_sliders()
            return ret

        if self.is_online(cpu) and not cpu_online:
            if HELPER.cpu_allowed_offline(cpu):
                return HELPER.set_cpu_offline(cpu)

        return 0

    def _set_cpu_governor(self, cpu):
        """Set CPU governor"""
        conf = self.settings.get(cpu)
        if conf is None:
            return -1

        gov = conf.governor
        if gov is None:
            return -1

        ret = HELPER.update_cpu_governor(cpu, gov)
        return -11 if ret != 0 else 0

    def _set_cpu_energy_preferences(self, cpu):
        """Set CPU energy preferences"""
        if not self.energy_pref_avail:
            return 0

        conf = self.settings.get(cpu)
        if conf is None:
            return -1

        pref = conf.energy_pref
        if not pref:
            return -1

        ret = HELPER.update_cpu_energy_prefs(cpu, pref)
        return -12 if ret != 0 else 0

    def _set_cpu_frequencies(self, cpu):
        """Set CPU frequencies"""
        conf = self.settings.get(cpu)
        if conf is None:
            return -1

        fmin, fmax = conf.freqs_scaled
        if fmin is not None and fmax is not None:
            ret = HELPER.update_cpu_settings(cpu, fmin, fmax)
            return -13 if ret != 0 else 0

        return -1

    # Preferences handlers

    def on_default_profile_changed(self, index):
        """Default profile preference changed"""
        prof_name = self.default_profile_combo.currentData()
        if prof_name:
            self.conf.set("Profile", "profile", prof_name)
            self.conf.write_settings()

    def on_energy_per_cpu_changed(self, checked):
        """Energy per CPU preference changed"""
        self.energy_per_cpu = checked
        self.gui_conf["energy_pref_per_cpu"] = str(checked)
        self.conf.write_settings()

    def on_default_allcpus_changed(self, checked):
        """Default all CPUs preference changed"""
        self.gui_conf["allcpus_default"] = str(checked)
        self.conf.write_settings()

    def on_default_ticks_changed(self, checked):
        """Default tick marks preference changed"""
        self.tick_marks_enabled = checked
        self.gui_conf["tick_marks_enabled"] = str(checked)
        self.conf.write_settings()

    def on_default_ticks_num_changed(self, checked):
        """Default tick numbers preference changed"""
        self.ticks_markup = checked
        self.gui_conf["frequency_ticks"] = str(checked)
        self.conf.write_settings()

    # Profile management handlers

    def on_profile_name_changed(self, text):
        """Profile name entry changed"""
        self.save_profile_btn.setEnabled(bool(text.strip()))

    def on_save_profile_clicked(self):
        """Save profile button clicked"""
        name = self.profile_name_entry.text().strip()
        if name:
            self.conf.create_profile_from_settings(name, self.settings)
            self.profiles = self.conf.profiles
            self._update_profile_boxes()
            self._generate_profiles_list()
            self.profile_name_entry.clear()

    def on_delete_profile_clicked(self, profile_name):
        """Delete profile button clicked"""
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Are you sure you want to delete the profile '{profile_name}'?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )

        if reply == QMessageBox.StandardButton.Ok:
            self.conf.delete_profile(profile_name)
            self.profiles = self.conf.profiles
            self._update_profile_boxes()
            self._generate_profiles_list()

    def show_about_dialog(self):
        """Show the about dialog"""
        QMessageBox.about(
            self,
            "About cpupower-gui",
            "<h3>cpupower-gui</h3>"
            "<p>GUI utility to change the CPU frequency and governor</p>"
            "<p>Copyright (C) 2017-2020 [RnD]²</p>"
            "<p>Qt port by Felipe</p>"
            "<p><a href='https://github.com/vagnum08/cpupower-gui'>GitHub</a></p>"
            "<p>This program is free software under the GNU GPL v3.</p>"
        )

    def closeEvent(self, event):
        """Handle window close - minimize to tray instead"""
        event.ignore()
        self.hide()
