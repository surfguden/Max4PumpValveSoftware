"""Qt presentation. All device and experiment behavior lives in Application."""
from concurrent.futures import ThreadPoolExecutor
import json
from dataclasses import asdict
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QPen
from PySide6.QtWidgets import (QMainWindow, QWidget, QTabWidget, QVBoxLayout,
    QHBoxLayout, QFormLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox,
    QSpinBox, QLineEdit, QFileDialog, QGroupBox, QScrollArea, QDialog,
    QDialogButtonBox, QProgressBar, QCheckBox)
from .application import Syringe
from .drivers.valve_session import VALVE_TYPES, DetectedUnit
from .storage import VERSION


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
    layout.addWidget(control)
    return control


def combo(items):
    control = QComboBox()
    control.addItems(items)
    return control


class Bridge(QObject):
    completed = Signal(object, object, object)


class SyringeView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(115, 390)
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
        self.profiles = combo(['New custom syringe'] + list(saved))
        form.addRow('Saved syringe', self.profiles)
        self.name = QLineEdit('Custom syringe')
        self.material = combo(['Glass', 'Steel'])
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
        if name not in self.saved:
            return
        p = Syringe(**self.saved[name])
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


class PumpCard(QGroupBox):
    def __init__(self, window, index):
        super().__init__(f'neMESYS · Pump {index+1}')
        self.window, self.index = window, index
        self.setFixedWidth(395)
        root = QVBoxLayout(self)
        self.status = QLabel('Disconnected')
        root.addWidget(self.status)
        row = QHBoxLayout()
        button('▶ Start', self.start, row)
        button('■ Stop', lambda: window.stop_pump(index), row)
        button('↑ Refill', lambda: self.command('refill'), row)
        button('↓ Empty', lambda: self.command('empty'), row)
        root.addLayout(row)
        body = QHBoxLayout()
        self.syringe = SyringeView()
        body.addWidget(self.syringe)
        right = QVBoxLayout()
        target = QGroupBox('Target')
        form = QFormLayout(target)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
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
        root.addLayout(body)
        row = QHBoxLayout()
        button('Configure syringe…', self.configure, row)
        button('Reference move', lambda: self.command('reference'), row)
        root.addLayout(row)
        row = QHBoxLayout()
        button('Valve 1', lambda: self.command('valve', 1), row)
        button('Valve 2', lambda: self.command('valve', 2), row)
        root.addLayout(row)
        mapping = QFormLayout()
        self.energized = combo(['Unmapped', 'Position 1', 'Position 2'])
        self.deenergized = combo(['Unmapped', 'Position 1', 'Position 2'])
        mapping.addRow('Energized', self.energized)
        mapping.addRow('De-energized', self.deenergized)
        root.addLayout(mapping)
        button('Apply valve mapping', self.map, root)
        self.initial = None
        self.last_mapping = None
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.mode_changed()

    def mode_changed(self):
        self.volume.setEnabled(self.mode.currentIndex() == 0)
        self.level.setEnabled(self.mode.currentIndex() == 2)

    def start(self):
        mode = ['volume', 'flow', 'level'][self.mode.currentIndex()]
        self.initial = self.syringe.state.get('fill')
        self.command(mode, self.volume.value() if mode == 'volume' else self.level.value())

    def command(self, mode, value=0):
        if mode in ('refill', 'empty'):
            self.initial = self.syringe.state.get('fill')
        rate = self.flow.value()
        self.window.issue('pump.command', dict(pump=self.index+1, mode=mode, value=value, rate=rate))

    def configure(self):
        dialog = SyringeDialog(self.window.settings.get('syringes', {}), self)
        if dialog.exec() == QDialog.Accepted:
            profile = dialog.profile
            def done(result):
                self.flow.setValue(0)
                self.window.message.setText('Syringe applied. Set flow again before refill or empty.')
            self.window.issue('pump.syringe', dict(pump=self.index+1, profile=asdict(profile)), done)

    def map(self):
        energized, deenergized = self.energized.currentIndex(), self.deenergized.currentIndex()
        self.window.issue('pump.map', dict(pump=self.index+1, energized=energized, deenergized=deenergized))

    def update_state(self, state):
        self.syringe.state = state
        self.syringe.update()
        self.actual_flow.setText(f'{state["flow"]:.2f}')
        self.actual_level.setText(f'{state["fill"]:.4f}')
        self.actual_volume.setText(f'{abs(state["fill"] - self.initial):.4f}' if self.initial is not None else '0.0000')
        self.status.setText('FAULT' if state['fault'] else '● Pumping' if state['pumping'] else '● Ready · Stopped')
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
        self.resize(1240, 920)
        root = QWidget()
        layout = QVBoxLayout(root)
        title = QLabel('MAX4  /  PUMP & VALVE CONTROL')
        title.setStyleSheet('font-size:20px; font-weight:600; padding:8px;')
        layout.addWidget(title)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.message = QLabel('Disconnected · Select Simulation or explicitly connect devices.')
        self.message.setWordWrap(True)
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
        self.setStyleSheet('''QMainWindow,QWidget { background:#202a31; color:#e3edf2; }
          QGroupBox { border:1px solid #4a606c; border-radius:5px; margin-top:14px; padding:10px; }
          QGroupBox::title { subcontrol-origin:margin; left:12px; }
          QPushButton { background:#365668; border:1px solid #52798d; border-radius:4px; padding:7px; }
          QPushButton:hover { background:#47768b; } QPushButton:disabled { color:#788a93; }
          QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox { background:#162028; border:1px solid #49606d; padding:5px; }
          QTabBar::tab { padding:10px 20px; } QTabBar::tab:selected { background:#365668; }
          QProgressBar { border:1px solid #52798d; text-align:center; } QProgressBar::chunk { background:#299eb0; }''')
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)

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
        self.settings.update(config=self.config.text(), sdk=self.sdk.text(), port=self.port.currentText(), baud=int(self.baud.currentText()))
        self.settings['valves'] = [{'address': a, 'name': self.names.get(a, f'Valve {a:X}')} for a in self.app.units]
        try:
            response = self.commands.execute('settings.save', json_copy(self.settings), source='gui')
            if not response['ok']:
                raise ValueError(response['error'])
        except Exception as exc:
            self.message.setText(f'Settings save failed: {exc}')

    def browse(self, target):
        path = QFileDialog.getExistingDirectory(self, 'Select folder', target.text())
        if path:
            target.setText(path)

    def build_pumps(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.pump_sim = QCheckBox('Simulation (no hardware)')
        self.pump_sim.setChecked(True)
        self.count = QSpinBox()
        self.count.setRange(1, 4)
        self.count.setValue(2)
        self.config = QLineEdit(self.settings.get('config', ''))
        self.sdk = QLineEdit(self.settings.get('sdk', ''))
        for label, target in [('Qmix Elements XML folder', self.config), ('Vendor SDK runtime folder', self.sdk)]:
            row = QHBoxLayout()
            row.addWidget(target)
            button('Browse…', lambda checked=False, t=target: self.browse(t), row)
            form.addRow(label, row)
        form.addRow(self.pump_sim, self.count)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.pump_connect = button('Connect pumps', self.connect_pumps, row)
        button('Disconnect pumps', lambda: self.issue('pumps.disconnect', done=lambda _: self.set_cards(0)), row)
        button('Save settings', self.persist, row)
        layout.addLayout(row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.pump_layout = QHBoxLayout(content)
        self.pump_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.tabs.addTab(tab, 'Cetoni Pumps')
        self.set_cards(2)

    def set_cards(self, count):
        for card in self.cards:
            self.pump_layout.removeWidget(card)
            card.deleteLater()
        self.cards = [PumpCard(self, i) for i in range(count)]
        for card in self.cards:
            self.pump_layout.addWidget(card)
            card.setEnabled(bool(self.app.pumps))
        if hasattr(self, 'stir_pump'):
            for c in (self.stir_pump, self.inject_pump):
                c.clear()
                for i in range(count):
                    c.addItem(f'Pump {i+1}', i)

    def connect_pumps(self):
        simulation, count, config, sdk = self.pump_sim.isChecked(), self.count.value(), self.config.text(), self.sdk.text()
        self.pump_connect.setEnabled(False)
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
        self.valve_sim.setChecked(True)
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
        button('Connect & Scan', self.connect_valves, row)
        button('Scan bus', lambda: self.issue('valves.scan', done=self.show_valves), row)
        button('Disconnect valves', lambda: self.issue('valves.disconnect', done=lambda _: self.show_valves(list(self.app.units.values()))), row)
        button('Save configuration', self.persist, row)
        layout.addLayout(row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.valve_layout = QVBoxLayout(content)
        self.valve_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(content)
        layout.addWidget(scroll)
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
        for card in self.valve_cards:
            self.valve_layout.removeWidget(card)
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
                b.setEnabled(not unit.error and bool(self.app.valves))
            button('Query / Identify', lambda checked=False, a=unit.address: self.issue('valve.query', dict(address=a)), row)
            layout.addLayout(row)
            self.valve_layout.addWidget(card)
            self.valve_cards.append(card)
        if hasattr(self, 'inject_valve'):
            old = self.inject_valve.currentData()
            self.inject_valve.clear()
            for unit in units:
                if not unit.error and VALVE_TYPES[unit.valve_type]:
                    self.inject_valve.addItem(self.names.get(unit.address, str(unit.address)), unit.address)
            self.inject_valve.setCurrentIndex(max(0, self.inject_valve.findData(old)))
            self.valve_choices()

    def build_helpers(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        stir = QGroupBox('Stirring Cycle · One cycle per Start')
        form = QFormLayout(stir)
        self.stir_pump = combo([f'Pump {i+1}' for i in range(len(self.cards))])
        self.speed = combo(['Full speed', 'Defined flow rate'])
        self.stir_rate = number(1000, 0.01)
        self.refill = combo(['Full syringe', 'Defined volume', 'Volume before emptying'])
        self.refill_volume = number(0.5)
        form.addRow('Pump', self.stir_pump)
        form.addRow('Speed mode', self.speed)
        form.addRow('Defined flow (µL/min)', self.stir_rate)
        form.addRow('Refill target', self.refill)
        form.addRow('Defined refill level (mL)', self.refill_volume)
        row = QHBoxLayout()
        self.stir_start = button('Start cycle', self.start_stir, row)
        button('Stop', lambda: self.issue('helper.stop'), row)
        form.addRow(row)
        self.stir_status = QLabel('Idle')
        self.stir_status.setWordWrap(True)
        form.addRow(self.stir_status)
        layout.addWidget(stir)
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
        button('Stop', lambda: self.issue('helper.stop'), row)
        form.addRow(row)
        self.inject_status = QLabel('Idle')
        self.inject_status.setWordWrap(True)
        form.addRow(self.inject_status)
        layout.addWidget(inject)
        self.deadline = number(120, 1, 3600, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel('Maximum sequence duration (seconds)'))
        row.addWidget(self.deadline)
        layout.addLayout(row)
        self.helper_status = QLabel('Idle')
        self.helper_status.setWordWrap(True)
        self.helper_values = QLabel('Injected: —   Remaining: —')
        self.progress = QProgressBar()
        layout.addWidget(self.helper_status)
        layout.addWidget(self.helper_values)
        layout.addWidget(self.progress)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(tab)
        self.tabs.addTab(scroll, 'Experimental Helpers')

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
                refill=self.refill.currentText(), volume=self.refill_volume.value(), deadline=self.deadline.value()))
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
        if cached.get('pump_connected'):
            self.pump_sim.setChecked(cached.get('pump_simulation', False))
        if cached.get('valve_connected'):
            self.valve_sim.setChecked(cached.get('valve_simulation', False))
        self.pump_sim.setEnabled(not cached.get('pump_connected'))
        self.valve_sim.setEnabled(not cached.get('valve_connected'))
        # The monitor may lag a just-completed connect/disconnect callback.
        # Device-count metadata is in memory; do not replace a fresh UI selection
        # with an older cached disconnected snapshot.
        count = self.app.pumps.unit_count if self.app.pumps else 0
        if count and (len(self.cards) != count or not self.cards or not self.cards[0].isEnabled()):
            self.set_cards(count)
        elif not count and self.cards and self.cards[0].isEnabled():
            self.set_cards(0)
        for card in self.cards:
            mapping = cached.get('mapping', {}).get(str(card.index+1))
            if mapping and mapping != card.last_mapping:
                card.last_mapping = mapping
                card.energized.setCurrentIndex(mapping[0])
                card.deenergized.setCurrentIndex(mapping[1])
        if cached.get('settings') and cached['settings'] != self.last_settings:
            self.last_settings = json_copy(cached['settings'])
            self.settings = json_copy(cached['settings'])
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
        self.stir_start.setEnabled(not state['running'] and self.stir_pump.currentIndex() in self.app.mapping and bool(self.app.pumps))
        self.inject_start.setEnabled(not state['running'] and bool(self.app.pumps) and self.inject_valve.count() > 0)
        self.pump_connect.setEnabled(not self.app.pumps and not self.app.qmix.stalled)
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

    def closeEvent(self, event):
        self.timer.stop()
        self.persist()
        self.commands.execute('shutdown', source='gui')
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.stop_pool.shutdown(wait=False, cancel_futures=True)
        event.accept()
