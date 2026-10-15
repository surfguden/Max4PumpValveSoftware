"""Qt presentation. All device and experiment behavior lives in Application."""
from concurrent.futures import ThreadPoolExecutor
import math
import json
from dataclasses import asdict
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QPen, QAction, QGuiApplication, QFont
from PySide6.QtWidgets import (QMainWindow, QWidget, QTabWidget, QVBoxLayout,
    QHBoxLayout, QFormLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox,
    QSpinBox, QLineEdit, QFileDialog, QGroupBox, QDialog, QMessageBox,
    QDialogButtonBox, QProgressBar, QCheckBox)
from .application import Syringe
from .drivers.valve_session import VALVE_TYPES, DetectedUnit
from .storage import VERSION, test_simulation_enabled, data_dir, save
from .drivers.cetoni import SYRINGE_PRESETS


def json_copy(value):
    return json.loads(json.dumps(value))


def number(value=0, minimum=0, maximum=1_000_000, decimals=4):
    control = QDoubleSpinBox()
    control.setRange(minimum, maximum)
    control.setDecimals(decimals)
    control.setValue(value)
    control.setKeyboardTracking(False)
    return control


def button(text, callback, layout):
    control = QPushButton(text)
    control.clicked.connect(callback)
    control.setAttribute(Qt.WA_AlwaysShowToolTips, True)
    layout.addWidget(control)
    return control


def availability(control, reason=''):
    """Keep a useful explanation on disabled controls, including action buttons."""
    if control.isEnabled() and reason:
        control.setProperty('available_tooltip', control.toolTip())
    control.setEnabled(not bool(reason))
    control.setToolTip(reason or control.property('available_tooltip') or '')


def combo(items):
    control = QComboBox()
    control.addItems(items)
    return control


class Bridge(QObject):
    completed = Signal(object, object, object)


class SyringeView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(115, 300)
        self.state = {}

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        geometry = self.state.get('syringe')
        diameter, stroke = geometry if geometry else (4.78, 55)
        width = min(w * 0.65, max(30, diameter * 5))
        height = min(h * 0.66, h - 180, max(100, stroke * 5))
        x, y = (w-width)/2, 35
        gradient = QLinearGradient(x, 0, x+width, 0)
        gradient.setColorAt(0, QColor('#7e939e'))
        gradient.setColorAt(0.45, QColor('#e4edf0'))
        gradient.setColorAt(1, QColor('#526773'))
        p.setBrush(gradient)
        p.setPen(QPen(QColor('#a4bbc8'), 2))
        p.drawRoundedRect(int(x), int(y), int(width), int(height), 6, 6)
        capacity = self.state.get('capacity', 1) or 1
        fill = max(0, min(1, self.state.get('fill', 0)/capacity))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#42c3d2'))
        p.drawRect(int(x+5), int(y+height*(1-fill)), int(width-10), int(height*fill))
        p.setPen(QPen(QColor('#43555f'), 1))
        for i in range(11):
            yy = int(y + height*i/10)
            p.drawLine(int(x+width-12), yy, int(x+width-2), yy)
        p.setPen(QPen(QColor('#ffac66'), 2))
        for level in (self.state.get('minimum', 0), self.state.get('maximum', capacity)):
            yy = int(y+height*(1-level/capacity))
            p.drawLine(int(x-5), yy, int(x+width+5), yy)
        p.setPen(QPen(QColor('#c6d5dc'), 5))
        p.drawLine(int(w/2), int(y+height), int(w/2), int(y+height+35))
        p.setPen(QColor('#dce8ee'))
        p.drawText(0, int(y+height+62), w, 25, Qt.AlignCenter, f'{self.state.get("fill", 0):.3f} mL')
        p.setBrush(QColor('#507c54' if self.state.get('valve') == 1 else '#a87547'))
        p.drawRoundedRect(int(w/2-35), int(y+height+95), 70, 36, 5, 5)
        p.drawText(0, int(y+height+95), w, 36, Qt.AlignCenter, f'Valve {self.state.get("valve") or "—"}')


class SyringeDialog(QDialog):
    def __init__(self, saved, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Configure syringe')
        self.saved = saved
        self.profile = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.profiles = combo(['New custom syringe', 'BD 5ml'] + [name for name in saved if name != 'BD 5ml'])
        form.addRow('Saved syringe', self.profiles)
        self.name = QLineEdit('Custom syringe')
        self.material = combo(['Glass', 'Steel', 'Plastic'])
        self.method = combo(['Inner diameter', 'Scale length and volume'])
        self.diameter = number(4.78, 1, 35)
        self.scale_length = number(55, 0.001, 1000)
        self.scale_volume = number(0.98, 0.0001, 1000)
        self.stroke = number(55, 10, 65)
        self.minimum = number(0)
        self.maximum = number(0.98)
        self.pressure = number(1, 0.001, 10000)
        for label, control in [('Unique name', self.name), ('Material', self.material),
              ('Diameter method', self.method), ('Inner diameter (mm)', self.diameter),
              ('Scale length (mm)', self.scale_length), ('Scale volume (mL)', self.scale_volume),
              ('Maximum piston stroke (mm)', self.stroke), ('Minimum fill (mL)', self.minimum),
              ('Maximum fill (mL)', self.maximum), ('Maximum pressure (bar)', self.pressure)]:
            form.addRow(label, control)
        layout.addLayout(form)
        self.capacity = QLabel()
        layout.addWidget(self.capacity)
        layout.addWidget(QLabel('Pressure is profile metadata; this application does not monitor pressure.\n'
                                'Geometry bounds apply to the neMESYS low-pressure module.'))
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Apply Syringe')
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.profiles.currentTextChanged.connect(self.select_profile)
        self.method.currentIndexChanged.connect(self.update_capacity)
        for control in (self.diameter, self.scale_length, self.scale_volume, self.stroke):
            control.valueChanged.connect(self.update_capacity)
        self.update_capacity()

    def select_profile(self, name):
        if name == 'BD 5ml' and name not in self.saved:
            diameter, stroke = SYRINGE_PRESETS[name]
            # Round upward to the dialog precision so the 5 mL fill limit
            # remains within the capacity calculated from the displayed stroke.
            stroke = math.ceil(stroke * 10000) / 10000
            p = Syringe(name=name, material='Plastic', inner_diameter_mm=diameter,
                        max_piston_stroke_mm=stroke, maximum_ml=5)
        elif name in self.saved:
            p = Syringe(**self.saved[name])
        else:
            return
        self.name.setText(p.name)
        self.material.setCurrentText(p.material)
        self.method.setCurrentIndex(0)
        for control, value in [(self.diameter, p.inner_diameter_mm), (self.stroke, p.max_piston_stroke_mm),
              (self.minimum, p.minimum_ml), (self.maximum, p.maximum_ml), (self.pressure, p.max_pressure_bar)]:
            control.setValue(value)

    def get_profile(self):
        diameter = self.diameter.value() if self.method.currentIndex() == 0 else Syringe.diameter_from_scale(self.scale_length.value(), self.scale_volume.value())
        return Syringe(self.name.text(), self.material.currentText(), diameter, self.stroke.value(),
                       self.minimum.value(), self.maximum.value(), self.pressure.value())

    def update_capacity(self, *args):
        scale = self.method.currentIndex() == 1
        self.diameter.setEnabled(not scale)
        self.scale_length.setEnabled(scale)
        self.scale_volume.setEnabled(scale)
        try:
            p = self.get_profile()
            self.capacity.setText(f'Calculated diameter: {p.inner_diameter_mm:.4f} mm  |  Capacity: {p.capacity:.4f} mL')
        except ValueError as exc:
            self.capacity.setText(str(exc))

    def apply(self):
        try:
            self.profile = self.get_profile()
            self.profile.validate()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class MotionRateDialog(QDialog):
    """Edit a rate without sending any device command."""
    def __init__(self, mode, selected, maximum=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'{mode.capitalize()} flow rate')
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel('Flow (µL/min)'))
        self.rate = number(1000 if selected == 'max' else selected, 0.01, decimals=2)
        row.addWidget(self.rate)
        self.max_button = button('Max', lambda: None, row)
        self.max_button.setCheckable(True)
        self.max_button.toggled.connect(lambda checked: self.rate.setEnabled(not checked))
        self.max_button.setChecked(selected == 'max')
        layout.addLayout(row)
        detail = f'Current device maximum: {maximum:g} µL/min.\n' if maximum else ''
        layout.addWidget(QLabel(detail + 'Max uses the current device limit when the motion is started.'))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_rate(self):
        return 'max' if self.max_button.isChecked() else self.rate.value()


class PumpCard(QGroupBox):
    def __init__(self, window, index):
        super().__init__(f'neMESYS · Pump {index+1}')
        self.window, self.index = window, index
        self.device_buttons = []
        root = QVBoxLayout(self)
        self.status = QLabel('Disconnected')
        root.addWidget(self.status)
        row = QHBoxLayout()
        self.device_buttons.append(button('▶ Start', self.start, row))
        self.stop_button = button('■ Stop', lambda: window.stop_pump(index), row)
        self.motion_rates = dict(refill=1000, empty=1000)
        self.motion_buttons = {}
        for mode, text in [('refill', '↑ Refill'), ('empty', '↓ Empty')]:
            control = button(text, lambda checked=False, m=mode: self.command(m), row)
            control.setContextMenuPolicy(Qt.CustomContextMenu)
            control.customContextMenuRequested.connect(lambda position, m=mode: self.edit_motion_rate(m))
            self.motion_buttons[mode] = control
            self.device_buttons.append(control)
        self.update_motion_tooltips()
        root.addLayout(row)
        body = QHBoxLayout()
        self.syringe = SyringeView()
        body.addWidget(self.syringe)
        right = QVBoxLayout()
        target = QGroupBox('Target')
        form = QFormLayout(target)
        self.mode = combo(['Volume', 'Flow', 'Syringe level'])
        self.volume = number(0.1, -1000, 1000)
        self.flow = number(1000, -1_000_000, 1_000_000, 2)
        self.level = number(0.5)
        form.addRow('Mode', self.mode)
        form.addRow('Volume (mL)', self.volume)
        form.addRow('Flow (µL/min)', self.flow)
        form.addRow('Level (mL)', self.level)
        right.addWidget(target)
        actual = QGroupBox('Actual')
        actual_form = QFormLayout(actual)
        self.actual_volume, self.actual_flow, self.actual_level = QLabel('—'), QLabel('—'), QLabel('—')
        actual_form.addRow('Delivered (mL)', self.actual_volume)
        actual_form.addRow('Flow (µL/min)', self.actual_flow)
        actual_form.addRow('Level (mL)', self.actual_level)
        right.addWidget(actual)
        self.limits = QLabel('Limits unverified')
        self.limits.setWordWrap(True)
        right.addWidget(self.limits)
        body.addLayout(right)
        tools = QVBoxLayout()
        row = QHBoxLayout()
        self.configure_button = button('Configure syringe…', self.configure, row)
        self.device_buttons.append(self.configure_button)
        self.reference_button = button('Reference move', lambda: self.command('reference'), row)
        self.device_buttons.append(self.reference_button)
        tools.addLayout(row)
        row = QHBoxLayout()
        self.diagnose_button = button('Diagnostics', self.diagnose, row)
        self.recover_button = button('Clear fault & verify', self.recover, row)
        tools.addLayout(row)
        row = QHBoxLayout()
        self.valve_buttons = [button('Valve 1', lambda: self.command('valve', 1), row),
                              button('Valve 2', lambda: self.command('valve', 2), row)]
        self.device_buttons.extend(self.valve_buttons)
        tools.addLayout(row)
        mapping = QFormLayout()
        self.energized = combo(['Unmapped', 'Position 1', 'Position 2'])
        self.deenergized = combo(['Unmapped', 'Position 1', 'Position 2'])
        mapping.addRow('Energized', self.energized)
        mapping.addRow('De-energized', self.deenergized)
        tools.addLayout(mapping)
        self.map_button = button('Apply valve mapping', self.map, tools)
        self.device_buttons.append(self.map_button)
        tools.addStretch()
        body.addLayout(tools)
        root.addLayout(body)
        root.addStretch()
        self.connected = bool(window.app.pumps)
        self.initial = None
        self.last_mapping = None
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.mode_changed()
        self.refresh_availability()

    def refresh_availability(self):
        state = self.syringe.state
        app = self.window.app
        reference_reason = 'Reference required. Use Reference move to initialize the pump position.'
        reason = ('Connect pumps first using Connect pumps.' if not app.pumps else
                  'The pump bus is unresponsive; resolve hardware state before reconnecting.' if app.qmix.stalled else
                  'This pump is reserved by an active operation.' if ('pump', self.index) in app.reserved else
                  'Waiting for verified pump status.' if not state else
                  'Pump reports a fault; disconnect and reconnect to attempt fault recovery.' if state.get('fault') else
                  'Stop the pump before starting another operation.' if state.get('pumping') else
                  reference_reason if not state.get('position_initialized', True) else '')
        for control in self.device_buttons:
            availability(control, reason)
        if reason == reference_reason:
            availability(self.reference_button, '')
            availability(self.configure_button, '')
        if self.reference_button.isEnabled():
            self.reference_button.setToolTip('Reference move initializes the pump encoder by moving to its reference position.')
        if not reason and state.get('valve') not in (1, 2):
            for control in [*self.valve_buttons, self.map_button]:
                availability(control, 'This pump has no supported two-position valve.')
        if not reason and state.get('syringe_required') and not state.get('syringe_applied'):
            for control in [self.device_buttons[0], *self.motion_buttons.values()]:
                availability(control, 'Saved syringe is not applied. Configure syringe before dosing.')
        availability(self.recover_button, 'Connect pumps first.' if not app.pumps else
                     'Stop active operations before fault recovery.' if ('pump', self.index) in app.reserved else
                     'Restart after resolving stalled I/O.' if app.qmix.stalled else '')
        availability(self.stop_button, '' if app.pumps else 'Connect pumps before requesting a stop.')

    def mode_changed(self):
        self.volume.setEnabled(self.mode.currentIndex() == 0)
        self.level.setEnabled(self.mode.currentIndex() == 2)
        self.volume.setToolTip('Select Volume mode to edit dosing volume.' if not self.volume.isEnabled() else '')
        self.level.setToolTip('Select Syringe level mode to edit the target level.' if not self.level.isEnabled() else '')

    def start(self):
        mode = ['volume', 'flow', 'level'][self.mode.currentIndex()]
        self.initial = self.syringe.state.get('fill')
        self.command(mode, self.volume.value() if mode == 'volume' else self.level.value())

    def command(self, mode, value=0):
        confirmation = {}
        if mode == 'reference':
            answer = QMessageBox.warning(self, 'Disconnect syringe before reference move',
                'Disconnect the syringe from the pump before starting a reference move.\n\n'
                'The drive will move to its reference position. Is the syringe disconnected?',
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
            confirmation['syringe_disconnected'] = True
        if mode in ('refill', 'empty'):
            self.initial = self.syringe.state.get('fill')
        rate = self.motion_rates[mode] if mode in self.motion_rates else self.flow.value()
        self.window.issue('pump.command', dict(pump=self.index+1, mode=mode, value=value, rate=rate, **confirmation))

    def update_motion_tooltips(self):
        for mode, control in self.motion_buttons.items():
            rate = self.motion_rates[mode]
            text = 'Max (current device limit)' if rate == 'max' else f'{rate:g} µL/min'
            control.setToolTip(f'{mode.capitalize()}: {text}\nRight-click to set flow rate')
            control.setProperty('available_tooltip', control.toolTip())

    def edit_motion_rate(self, mode):
        dialog = MotionRateDialog(mode, self.motion_rates[mode], self.syringe.state.get('max_flow'), self)
        if dialog.exec() == QDialog.Accepted:
            self.motion_rates[mode] = dialog.selected_rate()
            self.update_motion_tooltips()
            self.window.message.setText(f'{mode.capitalize()} rate set · Save all settings to retain it')

    def configure(self):
        dialog = SyringeDialog(self.window.settings.get('syringes', {}), self)
        selected = self.window.settings.get('pump_profiles', {}).get(str(self.index+1))
        if selected:
            dialog.profiles.setCurrentText(selected)
        if dialog.exec() == QDialog.Accepted:
            profile = dialog.profile
            def done(result):
                self.flow.setValue(0)
                self.window.message.setText('Syringe applied. Verify flow settings before starting motion.')
            self.window.issue('pump.syringe', dict(pump=self.index+1, profile=asdict(profile)), done)

    def map(self):
        energized, deenergized = self.energized.currentIndex(), self.deenergized.currentIndex()
        self.window.issue('pump.map', dict(pump=self.index+1, energized=energized, deenergized=deenergized))

    def diagnose(self):
        def show(result):
            dialog = QDialog(self)
            dialog.setWindowTitle(f'Pump {self.index+1} diagnostics')
            layout = QVBoxLayout(dialog)
            from PySide6.QtWidgets import QPlainTextEdit
            import json
            text = QPlainTextEdit(json.dumps(result, indent=2))
            text.setReadOnly(True)
            layout.addWidget(text)
            dialog.resize(660, 480)
            dialog.exec()
        self.window.issue('pump.diagnose', dict(pump=self.index+1), show)

    def recover(self):
        self.window.issue('pump.recover', dict(pump=self.index+1),
                          lambda _: self.window.message.setText('Fault clear and stopped state verified. Check syringe before motion.'))

    def update_state(self, state):
        self.syringe.state = state
        self.syringe.update()
        self.actual_flow.setText(f'{state["flow"]:.2f}')
        self.actual_level.setText(f'{state["fill"]:.4f}')
        self.actual_volume.setText(f'{abs(state["fill"] - self.initial):.4f}' if self.initial is not None else '0.0000')
        self.status.setText('FAULT' if state['fault'] else '● Pumping' if state['pumping'] else
                           'REFERENCE REQUIRED · Connected' if not state.get('position_initialized', True) else '● Ready · Stopped')
        if state.get('syringe_required'):
            self.status.setText(self.status.text() + '\n' + ('Syringe applied: ' if state.get('syringe_applied') else
                                'APPLY SYRINGE BEFORE DOSING: ') + state['syringe_expected'])
        self.limits.setText(f'Fill: {state["minimum"]:.4f}–{state["maximum"]:.4f} mL\nMax flow: {state["max_flow"]:g} µL/min')


class MainWindow(QMainWindow):
    def __init__(self, commands):
        super().__init__()
        self.commands = commands
        self.app = commands.app
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='UI request')
        self.stop_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='UI stop')
        self.bridge = Bridge()
        self.bridge.completed.connect(self.completed)
        self.pending_poll = False
        self.last_units = None
        self.settings = {}
        self.setWindowTitle(f'Max4 Pump & Valve · {VERSION}')
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.setFont(QFont('Segoe UI', 9))
        self.resize(1240, 680)
        self.setMinimumSize(960, 560)
        root = QWidget()
        layout = QVBoxLayout(root)
        file_menu = self.menuBar().addMenu('&File')
        self.save_settings_action = QAction('&Save settings…', self)
        self.save_settings_action.setShortcut('Ctrl+S')
        self.save_settings_action.triggered.connect(self.save_settings_file)
        file_menu.addAction(self.save_settings_action)
        self.load_settings_action = QAction('&Load settings…', self)
        self.load_settings_action.setShortcut('Ctrl+O')
        self.load_settings_action.triggered.connect(self.load_settings_file)
        file_menu.addAction(self.load_settings_action)
        file_menu.addSeparator()
        self.connection_action = file_menu.addAction('Connection settings…', self.edit_connection_settings)
        file_menu.addSeparator()
        file_menu.addAction('Exit', self.close)
        self.settings_filename = str(data_dir() / 'settings.json')
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.message = QLabel('Disconnected · Hardware mode. Connect devices explicitly.')
        self.message.setWordWrap(True)
        self.message.setMaximumHeight(40)
        layout.addWidget(self.message)
        self.setCentralWidget(root)
        try:
            self.settings = json_copy(commands.settings)
        except Exception as exc:
            self.message.setText(f'Settings could not be loaded: {exc}')
        self.last_settings = json_copy(self.settings)
        self.cards = []
        self.build_pumps()
        self.build_valves()
        self.build_helpers()
        self.restore_controls()
        self.last_connection_mode = None
        self.setStyleSheet('''QMainWindow,QWidget { background:#202a31; color:#e3edf2; }
          QGroupBox { border:1px solid #4a606c; border-radius:5px; margin-top:14px; padding:10px; }
          QGroupBox::title { subcontrol-origin:margin; left:12px; }
          QPushButton { background:#365668; border:1px solid #52798d; border-radius:4px; padding:4px; }
          QPushButton:hover { background:#47768b; } QPushButton:disabled { color:#788a93; }
          QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox { background:#162028; border:1px solid #49606d; padding:3px; }
          QTabBar::tab { padding:6px 14px; } QTabBar::tab:selected { background:#365668; }
          QProgressBar { border:1px solid #52798d; text-align:center; } QProgressBar::chunk { background:#299eb0; }''')
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)
        self.fit_screen()

    def fit_screen(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(self.width(), max(960, available.width()-24)),
                        min(self.height(), max(560, available.height()-60)))

    def save_settings_file(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save all settings', self.settings_filename, 'Settings (*.json)')
        if not path:
            return
        if not path.lower().endswith('.json'):
            path += '.json'
        try:
            if not self.persist():
                raise ValueError(self.message.text())
            save(self.settings, path)
            self.settings_filename = path
            self.statusBar().showMessage(f'Settings saved: {path}', 15000)
            QMessageBox.information(self, 'Settings saved', f'All settings saved to:\n{path}')
        except Exception as exc:
            QMessageBox.critical(self, 'Settings save failed', str(exc))

    def load_settings_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Load settings', self.settings_filename, 'Settings (*.json)')
        if not path:
            return
        if self.load_settings(path):
            self.settings_filename = path
            self.statusBar().showMessage(f'Settings loaded: {path}', 15000)
            QMessageBox.information(self, 'Settings loaded', f'Settings loaded from:\n{path}\nNo devices were connected or moved.')
        else:
            QMessageBox.critical(self, 'Settings load failed', self.message.text())

    def edit_connection_settings(self):
        previous = self.config.text(), self.sdk.text(), self.count.value()
        if self.connection_dialog.exec() == QDialog.Accepted:
            self.persist()
            self.statusBar().showMessage('Connection settings saved for the next connection.', 10000)
        else:
            self.config.setText(previous[0])
            self.sdk.setText(previous[1])
            self.count.setValue(previous[2])

    def issue(self, command, args=None, done=None, *, quiet=False):
        def execute():
            response = self.commands.execute(command, args, source='gui')
            if not response['ok']:
                raise RuntimeError(response['error'])
            return response['result']
        self.task(execute, done, quiet=quiet, priority=command in ('pump.stop', 'helper.stop', 'shutdown'))

    def task(self, action, done=None, *, quiet=False, priority=False):
        future = (self.stop_pool if priority else self.pool).submit(action)
        def callback(f):
            try:
                result, error = f.result(), None
            except Exception as exc:
                result, error = None, exc
            self.bridge.completed.emit(result, error, (done, quiet))
        future.add_done_callback(callback)

    def completed(self, result, error, context):
        done, quiet = context
        if error:
            self.message.setText(str(error))
        elif done:
            done(result)
        elif not quiet:
            self.message.setText('Operation completed')

    def stop_pump(self, index):
        self.message.setText(f'Pump {index+1}: stop requested')
        self.issue('pump.stop', dict(pump=index+1), lambda _: self.message.setText(f'Pump {index+1}: stopped (readback verified)'))

    def persist(self):
        # Start with the latest dispatcher settings so a recently applied syringe
        # is retained even if the monitor has not refreshed the window yet.
        saved = json_copy(self.commands.settings)
        self.capture_pump_controls()
        saved.update(config=self.config.text(), sdk=self.sdk.text(), port=self.port.currentText(), baud=int(self.baud.currentText()))
        saved['valves'] = [{'address': a, 'name': self.names.get(a, f'Valve {a:X}')}
                           for a in sorted(set(self.names) | set(self.app.units))]
        ui = json_copy(self.settings.get('ui', {}))
        previous = ui.get('helpers', {})
        ui.update(count=self.count.value(), tab=self.tabs.currentIndex(),
                  window_size=[self.width(), self.height()], helpers=dict(
            stir_pump=self.stir_pump.currentIndex() if self.stir_pump.currentIndex() >= 0 else previous.get('stir_pump', 0), speed=self.speed.currentText(),
            stir_rate=self.stir_rate.value(), refill=self.refill.currentText(),
            refill_volume=self.refill_volume.value(), inject_pump=self.inject_pump.currentIndex() if self.inject_pump.currentIndex() >= 0 else previous.get('inject_pump', 0),
            inject_valve=self.inject_valve.currentData() if self.inject_valve.currentData() is not None else previous.get('inject_valve'), inject_volume=self.inject_volume.value(),
            inject_rate=self.inject_rate.value(), before=self.before.currentData() or previous.get('before'),
            after=self.after.currentData() or previous.get('after'), deadline=self.deadline.value(),
            wait_empty=self.wait_empty.value(), wait_full=self.wait_full.value(),
            valve_begin=self.valve_begin.currentText(), valve_end=self.valve_end.currentText()))
        saved['ui'] = ui
        try:
            response = self.commands.execute('settings.save', saved, source='gui')
            if not response['ok']:
                raise ValueError(response['error'])
            self.settings = json_copy(response['result'])
            self.last_settings = json_copy(self.settings)
            self.message.setText('All settings saved')
            return True
        except Exception as exc:
            self.message.setText(f'Settings save failed: {exc}')
            return False

    def capture_pump_controls(self):
        pumps = self.settings.setdefault('ui', {}).setdefault('pumps', {})
        for card in self.cards:
            pumps[str(card.index+1)] = dict(mode=card.mode.currentText(), volume=card.volume.value(),
                flow=card.flow.value(), level=card.level.value(), energized=card.energized.currentIndex(),
                deenergized=card.deenergized.currentIndex(), refill_rate=card.motion_rates['refill'],
                empty_rate=card.motion_rates['empty'])

    def restore_pump_controls(self):
        pumps = self.settings.get('ui', {}).get('pumps', {})
        for card in self.cards:
            values = pumps.get(str(card.index+1), {})
            card.mode.setCurrentText(values.get('mode', 'Volume'))
            for mode in ('refill', 'empty'):
                # Older settings used the main Flow field for both actions.
                card.motion_rates[mode] = values.get(mode+'_rate', abs(values.get('flow', 1000)) or 1000)
            card.update_motion_tooltips()
            for key, control in [('volume', card.volume), ('flow', card.flow), ('level', card.level)]:
                if key in values:
                    control.setValue(values[key])
            for key, control in [('energized', card.energized), ('deenergized', card.deenergized)]:
                control.setCurrentIndex(values.get(key, 0))

    def restore_controls(self):
        ui = self.settings.get('ui', {})
        self.config.setText(self.settings.get('config', ''))
        self.sdk.setText(self.settings.get('sdk', ''))
        self.port.setCurrentText(self.settings.get('port', 'COM3'))
        self.baud.setCurrentText(str(self.settings.get('baud', 9600)))
        self.names = {v['address']: v['name'] for v in self.settings.get('valves', [])}
        self.count.setValue(ui.get('count', 2))
        if not self.app.pumps and len(self.cards) != self.count.value():
            self.set_cards(self.count.value(), capture=False)
        self.restore_pump_controls()
        helpers = ui.get('helpers', {})
        for key, control in [('speed', self.speed), ('refill', self.refill),
                             ('valve_begin', self.valve_begin), ('valve_end', self.valve_end)]:
            if key in helpers:
                control.setCurrentText(helpers[key])
        for key, control in [('stir_rate', self.stir_rate), ('refill_volume', self.refill_volume),
                             ('inject_volume', self.inject_volume), ('inject_rate', self.inject_rate),
                             ('deadline', self.deadline), ('wait_full', self.wait_full), ('wait_empty', self.wait_empty)]:
            if key in helpers:
                control.setValue(helpers[key])
        for key, control in [('stir_pump', self.stir_pump), ('inject_pump', self.inject_pump)]:
            control.setCurrentIndex(min(helpers.get(key, 0), control.count()-1))
        self.restore_valve_choices()
        if 'window_size' in ui:
            self.resize(*ui['window_size'])
            self.fit_screen()
        self.tabs.setCurrentIndex(ui.get('tab', 0))
        self.pump_sim.setChecked(False)
        self.valve_sim.setChecked(False)

    def restore_valve_choices(self):
        helpers = self.settings.get('ui', {}).get('helpers', {})
        address = helpers.get('inject_valve')
        index = self.inject_valve.findData(address)
        if index >= 0:
            self.inject_valve.setCurrentIndex(index)
        for key, control in [('before', self.before), ('after', self.after)]:
            index = control.findData(helpers.get(key))
            if index >= 0:
                control.setCurrentIndex(index)

    def load_settings(self, path=None):
        response = self.commands.execute('settings.load', dict(path=path) if path else {}, source='gui')
        if not response['ok']:
            self.message.setText(f'Settings load failed: {response["error"]}')
            return False
        self.settings = json_copy(response['result'])
        self.last_settings = json_copy(self.settings)
        self.restore_controls()
        self.last_units = None
        self.message.setText('All settings loaded · No device commands were sent')
        return True

    def browse(self, target):
        path = QFileDialog.getExistingDirectory(self, 'Select folder', target.text())
        if path:
            target.setText(path)

    def build_pumps(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.connection_dialog = QDialog(self)
        self.connection_dialog.setWindowTitle('Connection settings')
        dialog_layout = QVBoxLayout(self.connection_dialog)
        form = QFormLayout()
        self.pump_sim = QCheckBox('Simulation (no hardware)')
        self.pump_sim.setChecked(False)
        self.pump_sim.setVisible(test_simulation_enabled())
        self.pump_sim.setEnabled(test_simulation_enabled())
        self.count = QSpinBox()
        self.count.setRange(1, 4)
        self.count.setValue(self.settings.get('ui', {}).get('count', 2))
        self.config = QLineEdit(self.settings.get('config', ''))
        self.sdk = QLineEdit(self.settings.get('sdk', ''))
        for label, target in [('Qmix Elements XML folder', self.config), ('Vendor SDK runtime folder', self.sdk)]:
            row = QHBoxLayout()
            row.addWidget(target)
            button('Browse…', lambda checked=False, t=target: self.browse(t), row)
            form.addRow(label, row)
        form.addRow('Number of units', self.count)
        dialog_layout.addLayout(form)
        dialog_layout.addWidget(QLabel('Hardware units are discovered from the Qmix configuration.\nSDK changes may require restarting the application.'))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.connection_dialog.accept)
        buttons.rejected.connect(self.connection_dialog.reject)
        dialog_layout.addWidget(buttons)
        row = QHBoxLayout()
        row.addWidget(self.pump_sim)
        self.pump_connect = button('Connect pumps', self.connect_pumps, row)
        self.pump_disconnect = button('Disconnect pumps', lambda: self.issue('pumps.disconnect', done=lambda _: self.set_cards(self.count.value())), row)
        row.addStretch()
        layout.addLayout(row)
        self.pump_pages = QTabWidget()
        self.pump_error = QLabel('')
        layout.addWidget(self.pump_error)
        layout.addWidget(self.pump_pages)
        self.tabs.addTab(tab, 'Cetoni Pumps')
        self.set_cards(self.count.value())

    def set_cards(self, count, capture=True):
        if capture:
            self.capture_pump_controls()
        self.pump_pages.clear()
        for card in self.cards:
            card.deleteLater()
        self.cards = [PumpCard(self, i) for i in range(count)]
        for card in self.cards:
            self.pump_pages.addTab(card, f'Pump {card.index+1}')
        if hasattr(self, 'stir_pump'):
            for key, c in (('stir_pump', self.stir_pump), ('inject_pump', self.inject_pump)):
                c.clear()
                for i in range(count):
                    c.addItem(f'Pump {i+1}', i)
                selected = self.settings.get('ui', {}).get('helpers', {}).get(key, 0)
                c.setCurrentIndex(min(selected, count-1))
        self.restore_pump_controls()

    def connect_pumps(self):
        simulation, count, config, sdk = self.pump_sim.isChecked(), self.count.value(), self.config.text(), self.sdk.text()
        availability(self.pump_connect, 'Connecting pumps and checking fault recovery. Please wait.')
        self.persist()
        def done(n):
            self.set_cards(n)
            self.message.setText('Pumps connected · Simulation' if simulation else 'Pumps connected · Hardware')
        self.issue('pumps.connect', dict(simulation=simulation, count=count, configuration=config, sdk=sdk), lambda result: done(result['count']))

    def build_valves(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.valve_sim = QCheckBox('Simulation (no hardware)')
        self.valve_sim.setChecked(False)
        self.valve_sim.setVisible(test_simulation_enabled())
        self.valve_sim.setEnabled(test_simulation_enabled())
        row.addWidget(self.valve_sim)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.addItem(self.settings.get('port', ''))
        row.addWidget(QLabel('COM port'))
        row.addWidget(self.port)
        button('Refresh ports', self.refresh_ports, row)
        self.baud = combo(['9600', '38400'])
        self.baud.setCurrentText(str(self.settings.get('baud', 9600)))
        row.addWidget(QLabel('Baud'))
        row.addWidget(self.baud)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.valve_connect = button('Connect & Scan', self.connect_valves, row)
        self.valve_scan = button('Scan bus', lambda: self.issue('valves.scan', done=self.show_valves), row)
        self.valve_disconnect = button('Disconnect valves', lambda: self.issue('valves.disconnect', done=lambda _: self.show_valves(list(self.app.units.values()))), row)
        row.addStretch()
        layout.addLayout(row)
        self.valve_pages = QTabWidget()
        layout.addWidget(self.valve_pages)
        self.tabs.addTab(tab, 'TriContinent Valves')
        self.names = {v['address']: v['name'] for v in self.settings.get('valves', [])}
        self.valve_cards = []
        self.show_valves(list(self.app.units.values()))

    def refresh_ports(self):
        self.issue('ports', done=self.ports_found)

    def ports_found(self, ports):
        current = self.port.currentText()
        self.port.clear()
        self.port.addItems(ports)
        self.port.setCurrentText(current)

    def connect_valves(self):
        simulation, port, baud = self.valve_sim.isChecked(), self.port.currentText(), int(self.baud.currentText())
        self.issue('valves.connect', dict(simulation=simulation, port=port, baud=baud), self.show_valves)

    def show_valves(self, units):
        units = [DetectedUnit(**u) if isinstance(u, dict) else u for u in units]
        self.valve_pages.clear()
        for card in self.valve_cards:
            card.deleteLater()
        self.valve_cards = []
        for unit in units:
            card = QGroupBox(f'Switch {unit.address:X} · DT /{unit.address+1:X}')
            layout = QVBoxLayout(card)
            name = QLineEdit(self.names.get(unit.address, f'Valve {unit.address:X}'))
            self.names.setdefault(unit.address, name.text())
            name.textChanged.connect(lambda text, a=unit.address: self.names.update({a: text}))
            layout.addWidget(name)
            layout.addWidget(QLabel(f'{unit.firmware or "Unidentified"}  ·  {unit.configuration or "Unknown profile"}'))
            status = QLabel(f'Position: {unit.position.title()}  |  {unit.error or "Verified"}')
            status.setWordWrap(True)
            layout.addWidget(status)
            row = QHBoxLayout()
            for position in VALVE_TYPES[unit.valve_type]:
                b = button(f'{position.title()} ({position[0].upper()})', lambda checked=False, a=unit.address, p=position: self.issue('valve.move', dict(address=a, position=p)), row)
                availability(b, 'Connect valves first.' if not self.app.valves else
                             f'Valve identification/status must be verified: {unit.error}' if unit.error else '')
            b = button('Query / Identify', lambda checked=False, a=unit.address: self.issue('valve.query', dict(address=a)), row)
            availability(b, '' if self.app.valves else 'Connect valves before querying device identification.')
            layout.addLayout(row)
            layout.addStretch()
            self.valve_pages.addTab(card, self.names[unit.address])
            self.valve_cards.append(card)
        if hasattr(self, 'inject_valve'):
            old = self.inject_valve.currentData()
            self.inject_valve.clear()
            for unit in units:
                if not unit.error and VALVE_TYPES[unit.valve_type]:
                    self.inject_valve.addItem(self.names.get(unit.address, str(unit.address)), unit.address)
            self.inject_valve.setCurrentIndex(max(0, self.inject_valve.findData(old)))
            self.valve_choices()
            self.restore_valve_choices()

    def build_helpers(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        groups = QHBoxLayout()
        layout.addLayout(groups)
        stir = QGroupBox('Stirring Cycle · One cycle per Start')
        form = QFormLayout(stir)
        self.stir_pump = combo([f'Pump {i+1}' for i in range(len(self.cards))])
        self.speed = combo(['Full speed', 'Defined flow rate'])
        self.stir_rate = number(1000, 0.01)
        self.refill = combo(['Full syringe', 'Defined volume', 'Volume before emptying'])
        self.refill_volume = number(0.5)
        self.speed.currentIndexChanged.connect(lambda: availability(self.stir_rate,
            'Full speed uses the device maximum; select Defined flow rate to enter a rate.' if self.speed.currentIndex() == 0 else ''))
        self.refill.currentIndexChanged.connect(lambda: availability(self.refill_volume,
            'Select Defined volume to enter a refill level.' if self.refill.currentText() != 'Defined volume' else ''))
        availability(self.stir_rate, 'Full speed uses the device maximum; select Defined flow rate to enter a rate.')
        availability(self.refill_volume, 'Select Defined volume to enter a refill level.')
        self.wait_empty = number(0, 0, 3600, 2)
        self.wait_full = number(0, 0, 3600, 2)
        self.valve_begin = combo(['Energized', 'Not energized'])
        self.valve_end = combo(['Energized', 'Not energized'])
        self.valve_end.setCurrentText('Not energized')
        form.addRow('Pump', self.stir_pump)
        form.addRow('Speed mode', self.speed)
        form.addRow('Defined flow (µL/min)', self.stir_rate)
        form.addRow('Refill target', self.refill)
        form.addRow('Defined refill level (mL)', self.refill_volume)
        form.addRow('Wait after empty (s)', self.wait_empty)
        form.addRow('Wait after full / refill (s)', self.wait_full)
        form.addRow('Valve at beginning', self.valve_begin)
        form.addRow('Valve at end', self.valve_end)
        row = QHBoxLayout()
        self.stir_start = button('Start cycle', self.start_stir, row)
        self.stir_stop = button('Stop', lambda: self.issue('helper.stop'), row)
        form.addRow(row)
        self.stir_status = QLabel('Idle')
        self.stir_status.setWordWrap(True)
        form.addRow(self.stir_status)
        self.stir_status.setMaximumHeight(45)
        groups.addWidget(stir)
        inject = QGroupBox('Inject Volume and Stop Flow')
        form = QFormLayout(inject)
        self.inject_pump = combo([f'Pump {i+1}' for i in range(len(self.cards))])
        self.inject_valve = QComboBox()
        self.inject_volume = number(0.1, 0.0001)
        self.inject_rate = number(1000, 0.01)
        self.before, self.after = QComboBox(), QComboBox()
        for label, control in [('Pump', self.inject_pump), ('TriContinent valve', self.inject_valve),
                              ('Volume (mL)', self.inject_volume), ('Flow (µL/min)', self.inject_rate),
                              ('Before injection', self.before), ('After injection', self.after)]:
            form.addRow(label, control)
        self.inject_valve.currentIndexChanged.connect(self.valve_choices)
        row = QHBoxLayout()
        self.inject_start = button('Start injection', self.start_inject, row)
        self.inject_stop = button('Stop', lambda: self.issue('helper.stop'), row)
        form.addRow(row)
        self.inject_status = QLabel('Idle')
        self.inject_status.setWordWrap(True)
        form.addRow(self.inject_status)
        self.inject_status.setMaximumHeight(45)
        groups.addWidget(inject)
        self.deadline = number(120, 1, 3600, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel('Maximum sequence duration (seconds)'))
        row.addWidget(self.deadline)
        layout.addLayout(row)
        self.helper_status = QLabel('Idle')
        self.helper_status.setWordWrap(True)
        self.helper_status.setMaximumHeight(42)
        self.helper_values = QLabel('Injected: —   Remaining: —')
        self.progress = QProgressBar()
        layout.addWidget(self.helper_status)
        layout.addWidget(self.helper_values)
        layout.addWidget(self.progress)
        layout.addStretch()
        self.tabs.addTab(tab, 'Experimental Helpers')

    def valve_choices(self, *args):
        unit = self.app.units.get(self.inject_valve.currentData())
        positions = VALVE_TYPES[unit.valve_type] if unit and not unit.error else ()
        for control in (self.before, self.after):
            old = control.currentData()
            control.clear()
            for p in positions:
                control.addItem(f'{p.title()} ({p[0].upper()})', p)
            control.setCurrentIndex(max(0, control.findData(old)))

    def start_stir(self):
        try:
            self.issue('helper.start', dict(kind='stir', pump=self.stir_pump.currentIndex()+1,
                rate=None if self.speed.currentIndex() == 0 else self.stir_rate.value(),
                refill=self.refill.currentText(), volume=self.refill_volume.value(), deadline=self.deadline.value(),
                wait_empty=self.wait_empty.value(), wait_full=self.wait_full.value(),
                valve_begin=self.valve_begin.currentText(), valve_end=self.valve_end.currentText()))
        except Exception as exc:
            self.message.setText(str(exc))

    def start_inject(self):
        try:
            self.issue('helper.start', dict(kind='inject', pump=self.inject_pump.currentIndex()+1, rate=self.inject_rate.value(),
                volume=self.inject_volume.value(), address=self.inject_valve.currentData(),
                before=self.before.currentData(), after=self.after.currentData(), deadline=self.deadline.value()))
        except Exception as exc:
            self.message.setText(str(exc))

    def poll(self):
        cached = self.commands.cached_state()
        error = cached.get('pump_connection_error', '')
        self.pump_error.setText('Pump connection failed. Open Diagnostics for details.' if error else '')
        self.pump_error.setToolTip(error)
        self.pump_error.setVisible(bool(error))
        if cached.get('pump_connected'):
            self.pump_sim.setChecked(cached.get('pump_simulation', False))
        if cached.get('valve_connected'):
            self.valve_sim.setChecked(cached.get('valve_simulation', False))
        self.pump_sim.setEnabled(test_simulation_enabled() and not cached.get('pump_connected'))
        self.valve_sim.setEnabled(test_simulation_enabled() and not cached.get('valve_connected'))
        connection_mode = tuple(cached.get(k, False) for k in
            ('pump_connected', 'pump_simulation', 'valve_connected', 'valve_simulation'))
        if connection_mode != self.last_connection_mode:
            self.last_connection_mode = connection_mode
            def mode(connected, simulated):
                return ('Test simulation' if simulated else 'Hardware') if connected else 'Disconnected'
            self.message.setText(f'Pumps: {mode(*connection_mode[:2])} · Valves: {mode(*connection_mode[2:])}')
        # The monitor may lag a just-completed connect/disconnect callback.
        # Device-count metadata is in memory; do not replace a fresh UI selection
        # with an older cached disconnected snapshot.
        count = self.app.pumps.unit_count if self.app.pumps else 0
        if count and (len(self.cards) != count or not self.cards or not self.cards[0].connected):
            self.set_cards(count)
        elif not count and self.cards and self.cards[0].connected:
            self.set_cards(self.count.value())
        for card in self.cards:
            card.refresh_availability()
            mapping = cached.get('mapping', {}).get(str(card.index+1))
            if mapping and mapping != card.last_mapping:
                card.last_mapping = mapping
                card.energized.setCurrentIndex(mapping[0])
                card.deenergized.setCurrentIndex(mapping[1])
        # Settings writes are synchronous; the device monitor can still hold an
        # older snapshot. Never overwrite freshly saved controls from that cache.
        with self.commands.lock:
            saved_settings = json_copy(self.commands.settings)
        if saved_settings != self.last_settings:
            self.last_settings = json_copy(saved_settings)
            self.settings = saved_settings
            for key, control in [('config', self.config), ('sdk', self.sdk)]:
                control.setText(self.settings.get(key, ''))
            self.port.setCurrentText(self.settings.get('port', ''))
            self.baud.setCurrentText(str(self.settings.get('baud', 9600)))
            self.names.update({v['address']: v['name'] for v in self.settings.get('valves', [])})
            self.last_units = None
        units = list(self.app.units.values())
        signature = repr(units)
        if signature != self.last_units:
            self.last_units = signature
            self.show_valves(units)
        state = self.app.snapshot()
        for kind, label in [('stir', self.stir_status), ('inject', self.inject_status)]:
            status = self.app.helper_snapshot(kind)
            label.setText(f'{"RUNNING" if status["running"] else "STOPPED"} · {status["step"]}\n{status["message"]}')
        self.helper_status.setText(f'{"● RUNNING" if state["running"] else "● STOPPED"} · {state["step"]}\n{state["message"]}')
        self.helper_values.setText(f'Injected / moved: {state.get("injected", 0):.4f} mL   Remaining: {state.get("remaining", 0):.4f} mL')
        self.progress.setValue(int(state['progress']))
        common = ('A helper is already running; stop it or wait for completion.' if state['running'] else
                  'Connect pumps first.' if not self.app.pumps else
                  'The pump bus is unresponsive; resolve hardware state before reconnecting.' if self.app.qmix.stalled else '')
        def selected_pump_reason(selector):
            index = selector.currentIndex()
            if not 0 <= index < len(self.cards):
                return 'Select a connected pump first.'
            observed = self.cards[index].syringe.state
            return ('Waiting for verified pump status.' if not observed else
                    'The selected pump reports a fault; reconnect to attempt fault recovery.' if observed.get('fault') else
                    'Reference required. Use Reference move on the pump page.' if not observed.get('position_initialized', True) else
                    'Configure syringe: saved profile is not applied to this connection.' if observed.get('syringe_required') and not observed.get('syringe_applied') else
                    'Stop the selected pump before starting a helper.' if observed.get('pumping') else '')
        availability(self.stir_start, common or selected_pump_reason(self.stir_pump) or ('Apply the selected pump’s energized/not energized valve mapping first.'
                     if self.stir_pump.currentIndex() not in self.app.mapping else ''))
        availability(self.inject_start, common or selected_pump_reason(self.inject_pump) or ('Connect and identify a supported TriContinent valve first.'
                     if self.inject_valve.count() == 0 else ''))
        availability(self.stir_stop, '' if state['running'] and state.get('kind') == 'stir' else 'No stirring cycle is running.')
        availability(self.inject_stop, '' if state['running'] and state.get('kind') == 'inject' else 'No injection is running.')
        availability(self.pump_connect, 'Pumps are already connected.' if self.app.pumps else
                     'Connection is in progress.' if self.app.pump_connecting.is_set() else
                     'The pump bus is unresponsive; resolve hardware state before reconnecting.' if self.app.qmix.stalled else '')
        availability(self.pump_disconnect, 'No pumps are connected.' if not self.app.pumps else
                     'Stop the active operation before disconnecting pumps.' if self.app.reserved else '')
        availability(self.valve_connect, 'Valves are already connected.' if self.app.valves else '')
        serial_reason = 'Connect valves first.' if not self.app.valves else 'Stop injection before changing the valve bus.' if ('serial', 0) in self.app.reserved else ''
        availability(self.valve_scan, serial_reason)
        availability(self.valve_disconnect, serial_reason)
        if not self.pending_poll and not self.app.qmix.stalled:
            self.pending_poll = True
            def action():
                try:
                    return self.commands.execute('state.cached', source='gui')['result'].get('pumps', [])
                finally:
                    self.pending_poll = False
            self.task(action, self.update_pumps, quiet=True)

    def update_pumps(self, states):
        for card, state in zip(self.cards, states):
            card.update_state(state)
            card.refresh_availability()

    def closeEvent(self, event):
        self.timer.stop()
        self.persist()
        self.commands.execute('shutdown', source='gui')
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.stop_pool.shutdown(wait=False, cancel_futures=True)
        event.accept()
