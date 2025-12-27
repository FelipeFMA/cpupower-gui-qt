# main.py
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

import sys

import dbus
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtCore import QByteArray

from .helper import apply_balanced, apply_performance, apply_cpu_profile
from .config import CpuPowerConfig

BUS = dbus.SystemBus()
SESSION = BUS.get_object(
    "org.rnd2.cpupower_gui.helper", "/org/rnd2/cpupower_gui/helper"
)

HELPER = dbus.Interface(SESSION, "org.rnd2.cpupower_gui.helper")
APP_ID = "org.rnd2.cpupower_gui"


class CpuPowerApp(QApplication):
    """Main Qt Application for cpupower-gui"""

    SOCKET_NAME = "cpupower-gui-single-instance"

    def __init__(self, argv):
        super().__init__(argv)
        self.setApplicationName("cpupower-gui")
        self.setDesktopFileName(APP_ID)
        self.setQuitOnLastWindowClosed(False)

        self.main_window = None
        self.tray_icon = None
        self._server = None

        self._setup_tray()
        self._setup_single_instance()

    def _setup_single_instance(self):
        """Setup single-instance server to listen for new instance requests"""
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)

        # Try to start listening - if it fails, cleanup stale socket and retry
        if not self._server.listen(self.SOCKET_NAME):
            # Remove potentially stale socket file and try again
            QLocalServer.removeServer(self.SOCKET_NAME)
            self._server.listen(self.SOCKET_NAME)

    def _on_new_connection(self):
        """Handle connection from another instance - show our window"""
        socket = self._server.nextPendingConnection()
        if socket:
            socket.waitForReadyRead(1000)
            socket.disconnectFromServer()
            # Another instance requested us to show
            self.show_main_window()

    def _setup_tray(self):
        """Setup system tray icon and menu"""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon.fromTheme(APP_ID, QIcon.fromTheme("cpu")))
        self.tray_icon.setToolTip("CPU Power GUI")

        # Create tray menu
        tray_menu = QMenu()

        # Show GUI action
        show_action = QAction("Show GUI", self)
        show_action.triggered.connect(self.show_main_window)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        # Load profiles into tray menu
        self._add_profile_actions(tray_menu)

        tray_menu.addSeparator()

        # Quit action
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _add_profile_actions(self, menu):
        """Add profile actions to the tray menu"""
        config = CpuPowerConfig()
        profiles = [config.get_profile(profile) for profile in config.profiles]

        # Built-in profiles
        for profile in profiles:
            if profile._custom:
                continue
            action = QAction(profile.name, self)
            action.triggered.connect(lambda checked, p=profile: self.on_apply_profile(p))
            menu.addAction(action)

        menu.addSeparator()

        # System profiles
        for profile in profiles:
            if profile.system:
                action = QAction(profile.name, self)
                action.triggered.connect(lambda checked, p=profile: self.on_apply_profile(p))
                menu.addAction(action)

        menu.addSeparator()

        # User profiles
        for profile in profiles:
            if profile._custom and not profile.system:
                action = QAction(profile.name, self)
                action.triggered.connect(lambda checked, p=profile: self.on_apply_profile(p))
                menu.addAction(action)

    def _on_tray_activated(self, reason):
        """Handle tray icon activation"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_main_window()

    def show_main_window(self):
        """Show or create the main window"""
        if self.main_window is None:
            from .window import CpupowerGuiWindow
            self.main_window = CpupowerGuiWindow()

        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def on_apply_profile(self, profile):
        """Apply a CPU profile"""
        apply_cpu_profile(profile)

        # Update window if exists
        if self.main_window:
            for cpu in self.main_window.settings.keys():
                self.main_window._refresh_cpu_settings(cpu)

        return 0

    def on_apply_performance(self):
        """Apply performance profile"""
        apply_performance()

        if self.main_window:
            for cpu in self.main_window.settings.keys():
                self.main_window._refresh_cpu_settings(cpu)

        return 0

    def on_apply_balanced(self):
        """Apply balanced profile"""
        apply_balanced()

        if self.main_window:
            for cpu in self.main_window.settings.keys():
                self.main_window._refresh_cpu_settings(cpu)

        return 0


def main(version):
    """Main entry point for the Qt application"""
    # Check if another instance is already running
    socket = QLocalSocket()
    socket.connectToServer(CpuPowerApp.SOCKET_NAME)

    if socket.waitForConnected(500):
        # Another instance is running - send activation request and exit
        socket.write(QByteArray(b"show"))
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return 0

    app = CpuPowerApp(sys.argv)
    app.show_main_window()
    return app.exec()

