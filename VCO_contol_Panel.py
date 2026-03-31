import os
import re
import sys
import time
import tempfile
import threading

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit,
    QComboBox, QGroupBox, QRadioButton,
    QMessageBox, QTabWidget, QLCDNumber,
    QButtonGroup, QSlider, QSizePolicy,
    QFileDialog, QDialog, QFormLayout,
    QStyle, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView
)
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtGui import QRegularExpressionValidator, QIcon
from PySide6.QtCore import QRegularExpression

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import imslib
from imslib import MHz, Percent, RFChannel, VCO
from ims_scan import iMSScanner
from ims_events import EventWaiter

# ----------------------------
# Resource path helper (PyInstaller-friendly)
# ----------------------------
def resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)

# ----------------------------
# App Version helper (PyInstaller .exe version info)
# ----------------------------
def get_application_version() -> str:
    fallback = "dev"

    # Running as PyInstaller .exe
    exe_path = sys.executable if getattr(sys, "frozen", False) else None
    if not exe_path or not os.path.exists(exe_path):
        return fallback

    try:
        import ctypes
        from ctypes import wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return fallback

        res = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(exe_path, 0, size, res):
            return fallback

        u_len = wintypes.UINT()
        lp = ctypes.c_void_p()

        if not ctypes.windll.version.VerQueryValueW(
            res,
            "\\",
            ctypes.byref(lp),
            ctypes.byref(u_len),
        ):
            return fallback

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        ffi = ctypes.cast(lp, ctypes.POINTER(VS_FIXEDFILEINFO)).contents

        major = (ffi.dwFileVersionMS >> 16) & 0xFFFF
        minor = ffi.dwFileVersionMS & 0xFFFF
        build = (ffi.dwFileVersionLS >> 16) & 0xFFFF
        revision = ffi.dwFileVersionLS & 0xFFFF

        return f"{major}.{minor}.{build}.{revision}"

    except Exception:
        return fallback

# -----------------------------
# Pre-connection device chooser
# -----------------------------

def scan_systems() -> list[imslib.IMSSystem]:
    conn = imslib.ConnectionList()
    systems = conn.Scan()
    return [ims for ims in systems if ims.Synth().Model() == "iVCS"]


class DeviceSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select iVCS / iVSA Device")

        ico = resource_path("Isomet.ico")
        if os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))

        self.setModal(True)

        self._systems: list[imslib.IMSSystem] = []
        self._splash_closed = False

        self._secret_keys = []
        self.trial_mode_requested = False

        self.setFocusPolicy(Qt.StrongFocus)

        lay = QVBoxLayout(self)

        self.lbl = QLabel("Scanning for devices...")
        lay.addWidget(self.lbl)

        row = QHBoxLayout()
        row.addWidget(QLabel("Device:"))
        self.cmb = QComboBox()
        row.addWidget(self.cmb, 1)
        lay.addLayout(row)

        self.btn_refresh = QPushButton("Rescan")
        self.btn_connect = QPushButton("Connect")
        self.btn_close = QPushButton("Close")

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_connect)
        btn_row.addWidget(self.btn_close)
        lay.addLayout(btn_row)

        self.btn_refresh.clicked.connect(self.rescan)
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_close.clicked.connect(self.reject)

        self.rescan()

    # Ensures that .exe splash screen closes just before the connection dialogue appears.
    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.raise_()
        self.setFocus()

        if not self._splash_closed:
            try:
                import pyi_splash
                pyi_splash.close()
            except Exception:
                pass
            self._splash_closed = True

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.ShiftModifier:
            key = event.key()

            if key == Qt.Key_I:
                self._secret_keys.append("I")
            elif key == Qt.Key_U:
                self._secret_keys.append("U")
            elif key == Qt.Key_K:
                self._secret_keys.append("K")
            else:
                self._secret_keys.clear()
                super().keyPressEvent(event)
                return

            self._secret_keys = self._secret_keys[-3:]

            if self._secret_keys == ["I", "U", "K"]:
                self.trial_mode_requested = True
                self.accept()
                return
        else:
            self._secret_keys.clear()

        super().keyPressEvent(event)

    def _system_label(self, s: imslib.IMSSystem) -> str:
        try:
            port = str(s.ConnPort())
        except Exception:
            port = "UnknownPort"
        try:
            model = str(s.Synth().Model())
        except Exception:
            model = "UnknownModel"
        return f"{port}  |  {model}"

    def rescan(self):
        self.cmb.clear()
        try:
            self.lbl.setText("Scanning for devices...")
            QApplication.processEvents()
            self._systems = list(scan_systems())
        except Exception as e:
            self._systems = []
            self.lbl.setText(f"Scan failed: {e}")
            self.btn_connect.setEnabled(False)
            return

        if not self._systems:
            self.lbl.setText("No iVCS/iVSA devices found.")
            self.btn_connect.setEnabled(False)
            return

        self.lbl.setText(f"Found {len(self._systems)} device(s). Select one and click Connect.")
        for s in self._systems:
            self.cmb.addItem(self._system_label(s))
        self.cmb.setCurrentIndex(0)
        self.btn_connect.setEnabled(True)

    def selected_system(self) -> imslib.IMSSystem | None:
        if not self._systems:
            return None
        i = self.cmb.currentIndex()
        if i < 0 or i >= len(self._systems):
            return None
        return self._systems[i]

    def _on_connect(self):
        sysobj = self.selected_system()
        if sysobj is None:
            return
        try:
            try:
                sysobj.Connect()
            except Exception as e_connect:
                try:
                    ok = sysobj.Open()
                except Exception as e_open:
                    raise RuntimeError(f"Connect() failed ({e_connect}); Open() also failed ({e_open})")
                if not ok:
                    raise RuntimeError(f"Connect() failed ({e_connect}); Open() returned False")
                sysobj.Connect()

            synth = sysobj.Synth()
            if not synth.IsValid():
                raise RuntimeError("Synthesiser is not valid on this system (check interface/connection).")

        except Exception as e:
            QMessageBox.critical(self, "Connect Failed", str(e))
            return

        self.accept()

    @staticmethod
    def get_connected_system(parent=None) -> imslib.IMSSystem | str | None:
        dlg = DeviceSelectDialog(parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        if dlg.trial_mode_requested:
            return "trial_mode"
        return dlg.selected_system()


# -------------------------------------------------
# Utilities
# -------------------------------------------------

def error_box(msg):
    msg = str(msg)

    if "could not convert" in msg and "float" in msg:
        QMessageBox.critical(None, "Invalid Input", "Please enter a real numeric value")
    else:
        QMessageBox.critical(None, "Error", msg)


def channel_from_text(text):
    if text == "1":
        return RFChannel(1)
    if text == "2":
        return RFChannel(2)
    if text.lower() == "both":
        return RFChannel()
    raise ValueError("Invalid channel")


def filesystem_type_to_text(fs_type):
    mapping = {
        getattr(imslib, "FileSystemTypes_NO_FILE", None): "No File",
        getattr(imslib, "FileSystemTypes_COMPENSATION_TABLE", None): "Compensation Table",
        getattr(imslib, "FileSystemTypes_TONE_BUFFER", None): "Tone Buffer",
        getattr(imslib, "FileSystemTypes_DDS_SCRIPT", None): "DDS Script",
        getattr(imslib, "FileSystemTypes_USER_DATA", None): "User Data",
    }
    return mapping.get(fs_type, f"Unknown ({fs_type})")

# -------------------------------------------------
# Filter Controls
# -------------------------------------------------

class FilterWidget(QGroupBox):
    def __init__(self, vco):
        super().__init__("Filters")
        self.vco = vco

        layout = QGridLayout(self)

        layout.addWidget(QLabel("CIC Length (1-10)"), 0, 0)
        self.cic_len = QLineEdit("8")
        layout.addWidget(self.cic_len, 0, 1)

        btn_cic_on = QPushButton("Enable CIC")
        btn_cic_off = QPushButton("Disable CIC")
        layout.addWidget(btn_cic_on, 0, 4)
        layout.addWidget(btn_cic_off, 0, 5)

        btn_cic_on.clicked.connect(self.enable_cic)
        btn_cic_off.clicked.connect(self.disable_cic)

        layout.addWidget(QLabel("IIR Cutoff (kHz)"), 1, 0)
        self.iir_cutoff = QLineEdit("10.0")
        layout.addWidget(self.iir_cutoff, 1, 1)

        layout.addWidget(QLabel("Stages (1-8)"), 1, 2)
        self.iir_stages = QLineEdit("2")
        layout.addWidget(self.iir_stages, 1, 3)

        btn_iir_on = QPushButton("Enable IIR")
        btn_iir_off = QPushButton("Disable IIR")
        layout.addWidget(btn_iir_on, 1, 4)
        layout.addWidget(btn_iir_off, 1, 5)

        btn_iir_on.clicked.connect(self.enable_iir)
        btn_iir_off.clicked.connect(self.disable_iir)

    def enable_cic(self):
        try:
            if self.vco is None:
                return
            self.vco.ConfigureCICFilter(True, int(self.cic_len.text()))
        except Exception as e:
            error_box(str(e))

    def disable_cic(self):
        try:
            if self.vco is None:
                return
            self.vco.ConfigureCICFilter(False)
        except Exception as e:
            error_box(str(e))

    def enable_iir(self):
        try:
            if self.vco is None:
                return
            self.vco.ConfigureIIRFilter(
                True,
                float(self.iir_cutoff.text()),
                int(self.iir_stages.text())
            )
        except Exception as e:
            error_box(str(e))

    def disable_iir(self):
        try:
            if self.vco is None:
                return
            self.vco.ConfigureIIRFilter(False)
        except Exception as e:
            error_box(str(e))


# -------------------------------------------------
# Range Controls
# -------------------------------------------------

class RangeWidget(QGroupBox):
    def __init__(self, vco):
        super().__init__("Ranges")
        self.vco = vco

        layout = QGridLayout(self)

        self.channel = QComboBox()
        self.channel.addItems(["1", "2", "Both"])
        self.channel.setCurrentText("Both")

        layout.addWidget(QLabel("Channel"), 0, 0)
        layout.addWidget(self.channel, 0, 1)

        self.f_min = QLineEdit("")
        self.f_max = QLineEdit("")

        layout.addWidget(QLabel("Freq Min (MHz)"), 1, 0)
        layout.addWidget(self.f_min, 1, 1)
        layout.addWidget(QLabel("Freq Max (MHz)"), 1, 2)
        layout.addWidget(self.f_max, 1, 3)

        btn_freq = QPushButton("Set Frequency Range")
        layout.addWidget(btn_freq, 1, 4)
        btn_freq.clicked.connect(self.set_freq)

        self.a_min = QLineEdit("")
        self.a_max = QLineEdit("")

        layout.addWidget(QLabel("Ampl Min (%)"), 2, 0)
        layout.addWidget(self.a_min, 2, 1)
        layout.addWidget(QLabel("Ampl Max (%)"), 2, 2)
        layout.addWidget(self.a_max, 2, 3)

        btn_amp = QPushButton("Set Amplitude Range")
        layout.addWidget(btn_amp, 2, 4)
        btn_amp.clicked.connect(self.set_amp)

    def set_freq(self):
        try:
            if self.vco is None:
                return
            ch = channel_from_text(self.channel.currentText())
            self.vco.SetFrequencyRange(
                MHz(float(self.f_min.text())),
                MHz(float(self.f_max.text())),
                ch
            )
        except Exception as e:
            error_box(str(e))

    def set_amp(self):
        try:
            if self.vco is None:
                return
            ch = channel_from_text(self.channel.currentText())
            self.vco.SetAmplitudeRange(
                Percent(float(self.a_min.text())),
                Percent(float(self.a_max.text())),
                ch
            )
        except Exception as e:
            error_box(str(e))


# -------------------------------------------------
# Gain
# -------------------------------------------------

class GainWidget(QGroupBox):
    def __init__(self, vco):
        super().__init__("Digital Gain")
        self.vco = vco

        layout = QHBoxLayout(self)
        
        self.gains = {}
        for g in (1, 2, 4, 8):
            rb = QRadioButton(f"{g}x")
            self.gains[g] = rb
            layout.addWidget(rb)

        self.gains[1].setChecked(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for g, rb in self.gains.items():
            group.addButton(rb)
            rb.toggled.connect(lambda checked, b=rb: self._on_toggled(b, checked))

    def _on_toggled(self, button, checked):
        if not checked:
            return
        if self.vco is None:
            return

        mapping = {
            1: VCO.VCOGain_X1,
            2: VCO.VCOGain_X2,
            4: VCO.VCOGain_X4,
            8: VCO.VCOGain_X8,
        }
        for g, rb in self.gains.items():
            if rb.isChecked():
                self.vco.ApplyDigitalGain(mapping[g])
                return


# -------------------------------------------------
# Routing
# -------------------------------------------------

class RoutingWidget(QGroupBox):
    def __init__(self, vco, constant_widget, output_rows):
        super().__init__("Routing / Tracking")
        self.vco = vco
        self.constant_widget = constant_widget
        self.rows = {}

        layout = QGridLayout(self)

        layout.addWidget(QLabel("Output"), 0, 0)
        layout.addWidget(QLabel("Input"), 0, 1)
        layout.addWidget(QLabel("Mode"), 0, 2, 1, 4)

        for row, (label, output_enum, default_input) in enumerate(output_rows, start=1):
            self._add_row(layout, row, label, output_enum, default_input)

        constant_widget.constantPressed.connect(self._on_constant_pressed)

    def _add_row(self, layout, row, label, output_enum, default_input=0):
        layout.addWidget(QLabel(label), row, 0)

        cb = QComboBox()
        cb.addItems(["Input A", "Input B"])
        cb.setCurrentIndex(default_input)
        layout.addWidget(cb, row, 1)

        rb_track = QRadioButton("Track")
        rb_hold = QRadioButton("Hold")
        rb_ext = QRadioButton("External")
        rb_const = QRadioButton("Constant")
        rb_track.setChecked(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for rb in (rb_track, rb_hold, rb_ext, rb_const):
            group.addButton(rb)

        layout.addWidget(rb_track, row, 2)
        layout.addWidget(rb_hold, row, 3)
        layout.addWidget(rb_ext, row, 4)
        layout.addWidget(rb_const, row, 5)

        self.rows[output_enum] = (cb, rb_track, rb_hold, rb_ext, rb_const)

        cb.currentIndexChanged.connect(
            lambda _, out=output_enum: self._route(out)
        )
        for rb in (rb_track, rb_hold, rb_ext, rb_const):
            rb.toggled.connect(
                lambda checked, out=output_enum: checked and self._route(out)
            )

    def _route(self, output_enum):
        if self.vco is None:
            return

        cb, rb_track, rb_hold, rb_ext, rb_const = self.rows[output_enum]

        in_map = {
            0: VCO.VCOInput_A,
            1: VCO.VCOInput_B,
        }

        if rb_track.isChecked():
            mode = VCO.VCOTracking_TRACK
        elif rb_hold.isChecked():
            mode = VCO.VCOTracking_HOLD
        elif rb_ext.isChecked():
            mode = VCO.VCOTracking_PIN_CONTROLLED
        elif rb_const.isChecked():
            mode = VCO.VCOTracking_CONSTANT
        else:
            return

        self.vco.Route(output_enum, in_map[cb.currentIndex()])
        self.vco.TrackingMode(output_enum, mode)

    def _on_constant_pressed(self, output_enum):
        if output_enum not in self.rows:
            return
        _, _, _, _, rb_const = self.rows[output_enum]
        rb_const.setChecked(True)


# -------------------------------------------------
# Muting
# -------------------------------------------------

class RFMuteWidget(QGroupBox):
    def __init__(self, vco):
        super().__init__("RF Mute Control")
        self.vco = vco

        layout = QGridLayout(self)
        layout.addWidget(QLabel("RF Channel"), 0, 0)
        layout.addWidget(QLabel("Mode"), 0, 1)

        self._create_row(layout, row=1, label="Ch 1", channel=RFChannel(1))
        self._create_row(layout, row=2, label="Ch 2", channel=RFChannel(2))

    def _create_row(self, layout, row, label, channel):
        layout.addWidget(QLabel(label), row, 0)

        rb_run = QRadioButton("Run")
        rb_mute = QRadioButton("Mute")
        rb_pin = QRadioButton("Pin Control")

        rb_run.setChecked(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(rb_run)
        group.addButton(rb_mute)
        group.addButton(rb_pin)

        layout.addWidget(rb_run, row, 1)
        layout.addWidget(rb_mute, row, 2)
        layout.addWidget(rb_pin, row, 3)

        for rb in (rb_run, rb_mute, rb_pin):
            rb.toggled.connect(lambda checked, b=rb, ch=channel: self._on_toggled(b, ch, checked))

    def _on_toggled(self, button, channel, checked):
        if not checked:
            return

        text = button.text()
        if text == "Run":
            mute = VCO.VCOMute_UNMUTE
        elif text == "Mute":
            mute = VCO.VCOMute_MUTE
        elif text == "Pin Control":
            mute = VCO.VCOMute_PIN_CONTROLLED
        else:
            return

        if self.vco is None:
            return

        try:
            self.vco.RFMute(mute, channel)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


# -------------------------------------------------
# Constants & Startup
# -------------------------------------------------

class ConstantWidget(QGroupBox):
    constantPressed = Signal(object)

    def __init__(self, vco, sp=None):
        super().__init__("Constant Output")
        self.vco = vco
        self.sp = sp

        layout = QGridLayout(self)

        self.channel = QComboBox()
        self.channel.addItems(["1", "2", "Both"])
        self.channel.setCurrentText("Both")

        self.freq = QLineEdit("")
        self.amp = QLineEdit("")

        layout.addWidget(QLabel("Channel"), 0, 0)
        layout.addWidget(self.channel, 0, 1)

        layout.addWidget(QLabel("Frequency (MHz)"), 1, 0)
        layout.addWidget(self.freq, 1, 1)

        btn_f = QPushButton("Set Frequency")
        layout.addWidget(btn_f, 1, 2)
        btn_f.clicked.connect(self.set_freq)

        layout.addWidget(QLabel("Amplitude (%)"), 2, 0)
        layout.addWidget(self.amp, 2, 1)

        btn_a = QPushButton("Set Amplitude")
        layout.addWidget(btn_a, 2, 2)
        btn_a.clicked.connect(self.set_amp)

    def set_freq(self):
        try:
            if self.vco is None:
                return

            chan = channel_from_text(self.channel.currentText())
            self.vco.SetConstantFrequency(MHz(float(self.freq.text())), chan)
            if chan == RFChannel(1) or chan.IsAll():
                self.constantPressed.emit(VCO.VCOOutput_CH1_FREQUENCY)
            if chan == RFChannel(2) or chan.IsAll():
                self.constantPressed.emit(VCO.VCOOutput_CH2_FREQUENCY)

            if self.sp is not None:
                self.sp.PhaseResync()

        except Exception as e:
            error_box(str(e))

    def set_amp(self):
        try:
            if self.vco is None:
                return

            chan = channel_from_text(self.channel.currentText())
            self.vco.SetConstantAmplitude(Percent(float(self.amp.text())), chan)
            if chan == RFChannel(1) or chan.IsAll():
                self.constantPressed.emit(VCO.VCOOutput_CH1_AMPLITUDE)
            if chan == RFChannel(2) or chan.IsAll():
                self.constantPressed.emit(VCO.VCOOutput_CH2_AMPLITUDE)
        except Exception as e:
            error_box(str(e))


# -------------------------------------------------
# Compensation bypass
# -------------------------------------------------

class CompensationBypassWidget(QGroupBox):
    def __init__(self, sp):
        super().__init__("Compensation Bypass")
        self.sp = sp

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)

        self.tgl_bypass_amp = QPushButton("Amplitude")
        self.tgl_bypass_amp.setCheckable(True)
        self.tgl_bypass_amp.setChecked(False)
        self.tgl_bypass_amp.setToolTip("Enable/disable amplitude compensation bypass.")

        self.tgl_bypass_phase = QPushButton("Phase")
        self.tgl_bypass_phase.setCheckable(True)
        self.tgl_bypass_phase.setChecked(False)
        self.tgl_bypass_phase.setToolTip("Enable/disable phase compensation bypass.")

        layout.addWidget(self.tgl_bypass_amp, 1)
        layout.addWidget(self.tgl_bypass_phase, 1)

        self.tgl_bypass_amp.toggled.connect(self.on_bypass_changed)
        self.tgl_bypass_phase.toggled.connect(self.on_bypass_changed)

    def _apply_bypass_to_device(self):
        bypass_amp = bool(self.tgl_bypass_amp.isChecked())
        bypass_phase = bool(self.tgl_bypass_phase.isChecked())

        if self.sp is None:
            return

        try:
            self.sp.EnableImagePathCompensation(not bypass_amp, not bypass_phase)
        except Exception as e:
            error_box(f"Failed to apply bypass settings: {e}")

    def on_bypass_changed(self, _checked: bool):
        self._apply_bypass_to_device()

    def get_startup_values(self):
        return {
            "phase_bypass": bool(self.tgl_bypass_phase.isChecked()),
            "amp_bypass": bool(self.tgl_bypass_amp.isChecked()),
        }


# -------------------------------------------------
# RF Drive Controls
# -------------------------------------------------

class RFDriveWidget(QGroupBox):
    def __init__(self, sp, sysf):
        super().__init__("RF Drive Controls")
        self.sp = sp
        self.sysf = sysf

        self.sync_wipers = False
        self._sync_guard = False

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        root = QVBoxLayout(self)

        top = QHBoxLayout()

        self.tgl_amp_enable = QPushButton("Amplifier")
        self.tgl_amp_enable.setCheckable(True)
        self.tgl_amp_enable.setChecked(False)
        self.tgl_amp_enable.setToolTip("Enable/disable amplifier output. Default OFF on startup.")

        self.btn_sync_wipers = QPushButton("Sync CH1/CH2")
        self.btn_sync_wipers.setCheckable(True)
        self.btn_sync_wipers.setChecked(False)
        self.btn_sync_wipers.setToolTip("When enabled, CH1 and CH2 wipers track each other.")

        top.addWidget(self.tgl_amp_enable)
        top.addWidget(self.btn_sync_wipers)
        root.addLayout(top)

        sliders = QHBoxLayout()
        sliders.setSpacing(18)

        def _make_vslider_column(title: str, slider: QSlider, value_label: QLabel):
            col = QVBoxLayout()
            lbl_title = QLabel(title)
            lbl_title.setAlignment(Qt.AlignHCenter)

            slider.setOrientation(Qt.Vertical)
            slider.setTickPosition(QSlider.TicksRight)
            slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

            value_label.setAlignment(Qt.AlignHCenter)

            col.addWidget(lbl_title)
            col.addWidget(slider, 1, Qt.AlignHCenter)
            col.addWidget(value_label)
            return col

        self.sld_dds = QSlider()
        self.sld_dds.setRange(0, 1000)
        self.sld_dds.setValue(500)
        self.sld_dds.setSingleStep(1)
        self.sld_dds.setPageStep(10)
        self.sld_dds.setTickInterval(100)
        self.lbl_dds = QLabel("50.00 %")
        sliders.addLayout(_make_vslider_column("DDS", self.sld_dds, self.lbl_dds), 1)

        self.sld_w1 = QSlider()
        self.sld_w1.setRange(0, 1000)
        self.sld_w1.setValue(1000)
        self.sld_w1.setSingleStep(1)
        self.sld_w1.setPageStep(10)
        self.sld_w1.setTickInterval(100)
        self.lbl_w1 = QLabel("100.00 %")
        sliders.addLayout(_make_vslider_column("CH1 (W1)", self.sld_w1, self.lbl_w1), 1)

        self.sld_w2 = QSlider()
        self.sld_w2.setRange(0, 1000)
        self.sld_w2.setValue(1000)
        self.sld_w2.setSingleStep(1)
        self.sld_w2.setPageStep(10)
        self.sld_w2.setTickInterval(100)
        self.lbl_w2 = QLabel("100.00 %")
        sliders.addLayout(_make_vslider_column("CH2 (W2)", self.sld_w2, self.lbl_w2), 1)

        root.addLayout(sliders, 1)

        self.tgl_amp_enable.toggled.connect(self.on_amp_enable_toggled)
        self.btn_sync_wipers.toggled.connect(self.on_sync_wipers_toggled)
        self.sld_dds.valueChanged.connect(self.on_dds_changed)
        self.sld_w1.valueChanged.connect(self.on_w1_changed)
        self.sld_w2.valueChanged.connect(self.on_w2_changed)

    def on_amp_enable_toggled(self, enabled: bool):
        if self.sysf is None:
            return
        try:
            self.sysf.EnableAmplifier(bool(enabled))
        except Exception as e:
            error_box(f"Amplifier toggle failed: {e}")
            self.tgl_amp_enable.blockSignals(True)
            self.tgl_amp_enable.setChecked(not enabled)
            self.tgl_amp_enable.blockSignals(False)

    def on_sync_wipers_toggled(self, enabled: bool):
        self.sync_wipers = bool(enabled)
        if self.sync_wipers:
            try:
                self._sync_guard = True
                if self.sld_w1.value() < self.sld_w2.value():
                    self.sld_w2.setValue(self.sld_w1.value())
                else:
                    self.sld_w1.setValue(self.sld_w2.value())
            finally:
                self._sync_guard = False

    def on_dds_changed(self, v: int):
        self.lbl_dds.setText(f"{v/10.0:.2f} %")
        if self.sp is None:
            return
        try:
            self.sp.UpdateDDSPowerLevel(Percent(v / 10.0))
        except Exception as e:
            error_box(f"DDS update error: {e}")

    def on_w1_changed(self, v: int):
        self.lbl_w1.setText(f"{v/10.0:.2f} %")
        if self.sp is None:
            return

        if self.sync_wipers and not self._sync_guard:
            try:
                self._sync_guard = True
                self.sld_w2.setValue(v)
            finally:
                self._sync_guard = False

        try:
            self.sp.UpdateRFAmplitude(
                imslib.SignalPath.AmplitudeControl_WIPER_1,
                Percent(v / 10.0),
                RFChannel(1)
            )
        except Exception as e:
            error_box(f"Wiper1 update error: {e}")

    def on_w2_changed(self, v: int):
        self.lbl_w2.setText(f"{v/10.0:.2f} %")
        if self.sp is None:
            return

        if self.sync_wipers and not self._sync_guard:
            try:
                self._sync_guard = True
                self.sld_w1.setValue(v)
            finally:
                self._sync_guard = False

        try:
            self.sp.UpdateRFAmplitude(
                imslib.SignalPath.AmplitudeControl_WIPER_2,
                Percent(v / 10.0),
                RFChannel(2)
            )
        except Exception as e:
            error_box(f"Wiper2 update error: {e}")

    def get_startup_values(self):
        return {
            "dds": Percent(self.sld_dds.value() / 10.0),
            "w1": Percent(self.sld_w1.value() / 10.0),
            "w2": Percent(self.sld_w2.value() / 10.0),
            "amp_en": bool(self.tgl_amp_enable.isChecked()),
        }

# -------------------------------------------------
# file table
# -------------------------------------------------
class DeviceFileTableWidget(QGroupBox):
    def __init__(self, ims):
        super().__init__("Stored Files on Device")
        self.ims = ims

        root = QVBoxLayout(self)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File", "Type", "Default"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.Fixed)

        self.table.setColumnWidth(1, 170)  
        self.table.setColumnWidth(2, 300)   

        root.addWidget(self.table)

        btns = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_set_default = QPushButton("Set Default")
        self.btn_clear_default = QPushButton("Clear Default")
        self.btn_delete_selected = QPushButton("Delete Selected")
        self.btn_delete_all = QPushButton("Delete All")

        btns.addWidget(self.btn_refresh)
        btns.addStretch(1)
        btns.addWidget(self.btn_set_default)
        btns.addWidget(self.btn_clear_default)
        btns.addWidget(self.btn_delete_selected)
        btns.addWidget(self.btn_delete_all)

        root.addLayout(btns)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_set_default.clicked.connect(self.set_selected_default)
        self.btn_clear_default.clicked.connect(self.clear_selected_default)
        self.btn_delete_selected.clicked.connect(self.delete_selected)
        self.btn_delete_all.clicked.connect(self.delete_all)

        if self.ims == "trial_mode":
            for btn in (
                self.btn_refresh,
                self.btn_set_default,
                self.btn_clear_default,
                self.btn_delete_selected,
                self.btn_delete_all,
            ):
                btn.setEnabled(False)
                btn.setToolTip("Trial mode: no device connected")
            self.table.setRowCount(0)
        else:
            self.refresh()

    def _viewer(self):
        viewer = imslib.FileSystemTableViewer(self.ims)
        if hasattr(viewer, "IsValid") and not bool(viewer.IsValid):
            raise RuntimeError("Could not read device file table.")
        return viewer

    def _manager(self):
        return imslib.FileSystemManager(self.ims)

    def _selected_name(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return item.text().strip()

    def refresh(self):
        if self.ims == "trial_mode":
            self.table.setRowCount(0)
            return

        try:
            viewer = self._viewer()

            entries = []
            for entry in viewer:
                name = str(entry.Name).strip()
                if not name:
                    name = "<unnamed>"
                entries.append(
                    (
                        name,
                        filesystem_type_to_text(entry.Type),
                        "Yes" if bool(entry.IsDefault) else "No",
                    )
                )

            self.table.setRowCount(len(entries))
            for row, (name, typ, default_text) in enumerate(entries):
                self.table.setItem(row, 0, QTableWidgetItem(name))
                self.table.setItem(row, 1, QTableWidgetItem(typ))
                self.table.setItem(row, 2, QTableWidgetItem(default_text))

            if entries:
                self.table.selectRow(0)

        except Exception as e:
            error_box(f"Failed to read device file table: {e}")

    def set_selected_default(self):
        name = self._selected_name()
        if not name:
            error_box("Select a file first.")
            return

        try:
            ok = self._manager().SetDefault(name)
            if not ok:
                raise RuntimeError(f"SetDefault returned False for '{name}'")
            self.refresh()
        except Exception as e:
            error_box(f"Failed to set default: {e}")

    def clear_selected_default(self):
        name = self._selected_name()
        if not name:
            error_box("Select a file first.")
            return

        try:
            ok = self._manager().ClearDefault(name)
            if not ok:
                raise RuntimeError(f"ClearDefault returned False for '{name}'")
            self.refresh()
        except Exception as e:
            error_box(f"Failed to clear default: {e}")

    def delete_selected(self):
        name = self._selected_name()
        if not name:
            error_box("Select a file first.")
            return

        ans = QMessageBox.question(
            self,
            "Delete File",
            f"Delete '{name}' from device storage?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        try:
            ok = self._manager().Delete(name)
            if not ok:
                raise RuntimeError(f"Delete returned False for '{name}'")

            try:
                self._manager().Sanitize()
            except Exception:
                pass

            self.refresh()

        except Exception as e:
            error_box(f"Failed to delete file: {e}")

    def delete_all(self):
        ans = QMessageBox.warning(
            self,
            "Delete All Files",
            "Delete all stored files from device storage?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        try:
            viewer = self._viewer()
            names = []

            for entry in viewer:
                name = str(entry.Name).strip()
                if name:
                    names.append(name)

            if not names:
                QMessageBox.information(self, "Delete All", "No stored files were found.")
                return

            fsm = self._manager()
            failed = []

            for name in names:
                try:
                    ok = fsm.Delete(name)
                    if not ok:
                        failed.append(name)
                except Exception:
                    failed.append(name)

            try:
                fsm.Sanitize()
            except Exception:
                pass

            self.refresh()

            if failed:
                QMessageBox.warning(
                    self,
                    "Delete All Complete",
                    "Some files could not be deleted:\n- " + "\n- ".join(failed),
                )
            else:
                QMessageBox.information(
                    self,
                    "Delete All Complete",
                    "All stored files were deleted successfully.",
                )

        except Exception as e:
            error_box(f"Failed to delete all files: {e}")


# -------------------------------------------------
# Compensation plotting helpers
# -------------------------------------------------

class MplCanvas(FigureCanvas):
    def __init__(self, title):
        self.figure = Figure(figsize=(5, 4), tight_layout=True)
        self.ax = self.figure.add_subplot(111)
        super().__init__(self.figure)
        self._title = title
        self.clear_plot()

    def clear_plot(self, message="Load a LUT to display data"):
        self.ax.clear()
        self.ax.set_title(self._title)
        self.ax.text(0.5, 0.5, message, ha="center", va="center", transform=self.ax.transAxes)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.draw_idle()


class CompensationWidget(QWidget):
    def __init__(self, ims, sp=None, nvm_widget=None):
        super().__init__()
        self.ims = ims
        self.sp = sp

        self.importer = None
        self.loaded_path = ""
        self.is_global_lut = False
        self.global_table = None
        self.channel_tables = {}
        self.channel_count = 0

        self.nvm_widget = nvm_widget

        root = QVBoxLayout(self)

        file_box = QGroupBox("Compensation LUT")
        file_lay = QGridLayout(file_box)

        self.ed_file = QLineEdit()
        self.ed_file.setReadOnly(True)
        self.ed_file.setPlaceholderText("No LUT loaded")

        self.lbl_kind = QLabel("Type: -")
        self.lbl_kind.setWordWrap(True)

        self.lbl_range = QLabel("Range: -")
        self.lbl_range.setWordWrap(True)

        self.lbl_points = QLabel("Points: -")
        self.lbl_points.setWordWrap(True)

        self.btn_load = QPushButton("Load .lut")
        self.btn_load.clicked.connect(self.load_lut)

        file_lay.addWidget(QLabel("File"), 0, 0)
        file_lay.addWidget(self.ed_file, 0, 1)
        file_lay.addWidget(self.btn_load, 0, 2)

        file_lay.addWidget(self.lbl_kind, 1, 0, 1, 3)
        file_lay.addWidget(self.lbl_range, 2, 0, 1, 3)
        file_lay.addWidget(self.lbl_points, 3, 0, 1, 3)

        root.addWidget(file_box)

        plots = QHBoxLayout()
        self.amp_canvas = MplCanvas("Amplitude Compensation")
        self.phase_canvas = MplCanvas("Phase Compensation")
        plots.addWidget(self.amp_canvas, 1)
        plots.addWidget(self.phase_canvas, 1)
        root.addLayout(plots, 1)

        store_box = QGroupBox("Device storage")
        store_lay = QGridLayout(store_box)

        self.ed_store_name = QLineEdit("LUT1")
        self.ed_store_name.setMaxLength(24)

        rx = QRegularExpression(r"[A-Za-z0-9_-]{1,24}")
        self.ed_store_name.setValidator(
            QRegularExpressionValidator(rx, self.ed_store_name)
        )
        self.ed_store_name.setToolTip(
            "Allowed: A-Z a-z 0-9 _ -   (max 24 chars)"
        )

        self.lbl_store_hint = QLabel(
            "Stored name may only contain letters, numbers, '_' or '-'. Maximum 24 characters."
        )
        self.lbl_store_hint.setWordWrap(True)

        store_lay.addWidget(QLabel("Stored name"), 0, 0)
        store_lay.addWidget(self.ed_store_name, 0, 1)
        store_lay.addWidget(self.lbl_store_hint, 1, 0, 1, 2)

        self.device_files = DeviceFileTableWidget(ims)
        root.addWidget(store_box)

        btns = QHBoxLayout()
        btns.addStretch(1)

        self.btn_download = QPushButton("Download to device")
        self.btn_store = QPushButton("Store to non-volatile memory")

        self.btn_download.clicked.connect(self.download_loaded_lut)
        self.btn_store.clicked.connect(self.store_loaded_lut)

        btns.addWidget(self.btn_download)
        btns.addWidget(self.btn_store)

        root.addLayout(btns)

        self._set_buttons_enabled(False)
        self._clear_meta()
        self.amp_canvas.clear_plot("No amplitude data")
        self.phase_canvas.clear_plot("No phase data")

        if self.ims == "trial_mode":
            self.btn_download.setEnabled(False)
            self.btn_store.setEnabled(False)
            self.btn_download.setToolTip("Trial mode: no device connected")
            self.btn_store.setToolTip("Trial mode: no device connected")

    def _set_buttons_enabled(self, enabled: bool):
        self.btn_download.setEnabled(enabled)
        self.btn_store.setEnabled(enabled)

    def _clear_meta(self):
        self.lbl_kind.setText("Type: -")
        self.lbl_range.setText("Range: -")
        self.lbl_points.setText("Points: -")

    def _validated_store_name(self):
        name = self.ed_store_name.text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name):
            raise ValueError(
                "Stored name must be 1-24 characters using only letters, numbers, '_' or '-'."
            )
        return name

    def _reset_loaded_lut(self):
        self.importer = None
        self.loaded_path = ""
        self.is_global_lut = False
        self.global_table = None
        self.channel_tables = {}
        self.channel_count = 0
        self.ed_file.clear()
        self._clear_meta()
        self.amp_canvas.clear_plot("No amplitude data")
        self.phase_canvas.clear_plot("No phase data")
        self._set_buttons_enabled(False)

    def _wait_for_comp_event(self, ctdl, action: str, timeout_s: float = 15.0):
        waiter = EventWaiter()
        evt_map = {
            imslib.CompensationEvents_DOWNLOAD_FINISHED: "download_finished",
            imslib.CompensationEvents_DOWNLOAD_ERROR: "download_error",
            imslib.CompensationEvents_VERIFY_SUCCESS: "verify_success",
            imslib.CompensationEvents_VERIFY_FAIL: "verify_fail",
        }

        waiter.listen_for(list(evt_map.keys()))
        for evt in evt_map:
            ctdl.CompensationTableDownloadEventSubscribe(evt, waiter)

        try:
            start = time.time()
            while time.time() - start < timeout_s:
                try:
                    msg, _args = waiter.wait(timeout=0.2)
                except TimeoutError:
                    QApplication.processEvents()
                    continue
                return evt_map.get(msg, f"unknown:{msg}")
            raise TimeoutError(f"Timed out waiting for {action} result")
        finally:
            for evt in evt_map:
                try:
                    ctdl.CompensationTableDownloadEventUnsubscribe(evt, waiter)
                except Exception:
                    pass

    def _download_table(self, comp_table, label):
        ctdl = imslib.CompensationTableDownload(self.ims, comp_table)

        if not ctdl.StartDownload():
            raise RuntimeError(f"StartDownload returned False for {label}")

        result = self._wait_for_comp_event(ctdl, f"download {label}")
        if result != "download_finished":
            raise RuntimeError(f"Download failed for {label}: {result}")

        if not ctdl.StartVerify():
            raise RuntimeError(f"StartVerify returned False for {label}")

        result = self._wait_for_comp_event(ctdl, f"verify {label}")
        if result != "verify_success":
            err = ctdl.GetVerifyError()
            raise RuntimeError(
                f"Verify failed for {label}: {result} (error={err})"
            )

        return ctdl

    def _table_to_xy(self, comp_table, attr_name):
        pts = list(comp_table)
        freqs = []
        vals = []

        for i, pt in enumerate(pts):
            freqs.append(float(comp_table.FrequencyAt(i)))
            vals.append(float(getattr(pt, attr_name)))

        return freqs, vals

    def _plot_series(self, ax, title, ylabel, series_dict):
        ax.clear()

        for label, (freqs, values) in series_dict.items():
            ax.plot(freqs, values, label=label)

        ax.set_title(title)
        ax.set_xlabel("Frequency (MHz)")
        ax.set_ylabel(ylabel)
        ax.grid(True)

        if series_dict:
            ax.legend()

    def _refresh_plots(self):
        amp_series = {}
        phase_series = {}

        if self.is_global_lut and self.global_table is not None:
            amp_series["Global"] = self._table_to_xy(self.global_table, "Amplitude")
            phase_series["Global"] = self._table_to_xy(self.global_table, "Phase")
        else:
            for ch in sorted(self.channel_tables.keys()):
                label = f"CH{ch}"
                amp_series[label] = self._table_to_xy(
                    self.channel_tables[ch], "Amplitude"
                )
                phase_series[label] = self._table_to_xy(
                    self.channel_tables[ch], "Phase"
                )

        if amp_series:
            self._plot_series(
                self.amp_canvas.ax,
                "Amplitude Compensation",
                "Amplitude (%)",
                amp_series,
            )
            self.amp_canvas.draw_idle()
        else:
            self.amp_canvas.clear_plot("No amplitude data")

        if phase_series:
            self._plot_series(
                self.phase_canvas.ax,
                "Phase Compensation",
                "Phase (°)",
                phase_series,
            )
            self.phase_canvas.draw_idle()
        else:
            self.phase_canvas.clear_plot("No phase data")

    def load_lut(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select LUT file",
            "",
            "LUT files (*.lut);;All files (*)"
        )
        if not file_path:
            return

        try:
            importer = imslib.CompensationTableImporter(file_path)
            if not importer.IsValid():
                raise RuntimeError("Selected LUT file is not valid.")

            self.importer = importer
            self.loaded_path = file_path
            self.global_table = None
            self.channel_tables = {}
            self.channel_count = int(importer.Channels())
            self.is_global_lut = bool(importer.IsGlobal())

            if self.is_global_lut:
                self.global_table = importer.RetrieveGlobalLUT()
                self.lbl_kind.setText("Type: Global LUT")
            else:
                self.lbl_kind.setText(
                    f"Type: Per-channel LUT ({self.channel_count} channel(s))"
                )

                for ch in range(1, max(self.channel_count, 4) + 1):
                    try:
                        tbl = importer.RetrieveChannelLUT(RFChannel(ch))
                        if len(tbl):
                            self.channel_tables[ch] = tbl
                    except Exception:
                        pass

                if not self.channel_tables:
                    raise RuntimeError(
                        "Per-channel LUT detected, but no channel tables could be retrieved."
                    )

            self.ed_file.setText(file_path)
            self.lbl_range.setText(
                f"Range: {float(importer.LowerFrequency):.3f} to "
                f"{float(importer.UpperFrequency):.3f} MHz"
            )
            self.lbl_points.setText(f"Points: {int(importer.Size)}")

            self._refresh_plots()
            self._set_buttons_enabled(True)

        except Exception as e:
            self._reset_loaded_lut()
            error_box(str(e))

    def download_loaded_lut(self):
        if self.ims == "trial_mode":
            error_box("Trial mode: LUT download is unavailable.")
            return

        if self.importer is None:
            error_box("Load a LUT file first.")
            return

        try:
            if self.sp is not None:
                try:
                    self.sp.EnableImagePathCompensation(True, True)
                except Exception:
                    pass

            if self.is_global_lut and self.global_table is not None:
                self._download_table(self.global_table, "global LUT")
                QMessageBox.information(
                    self,
                    "Download complete",
                    "Global LUT downloaded and verified successfully."
                )
                return

            done = []
            for ch in sorted(self.channel_tables.keys()):
                self._download_table(self.channel_tables[ch], f"CH{ch} LUT")
                done.append(f"CH{ch}")

            if not done:
                raise RuntimeError("No channel LUT data was found to download.")

            QMessageBox.information(
                self,
                "Download complete",
                "Per-channel LUT downloaded and verified successfully: "
                + ", ".join(done)
            )

        except Exception as e:
            error_box(str(e))

    def store_loaded_lut(self):
        if self.ims == "trial_mode":
            error_box("Trial mode: LUT storage is unavailable.")
            return

        if self.importer is None:
            error_box("Load a LUT file first.")
            return

        try:
            base_name = self._validated_store_name()

            if self.is_global_lut and self.global_table is not None:
                ctdl = imslib.CompensationTableDownload(self.ims, self.global_table)
                ok = ctdl.Store(imslib.FileDefault_DEFAULT, base_name)

                if ok:
                    if self.nvm_widget is not None:
                        self.nvm_widget.refresh()
                    QMessageBox.information(
                        self,
                        "Stored",
                        f"Stored global LUT in device non-volatile memory as '{base_name}'."
                    )
                return

            stored = []
            for ch in sorted(self.channel_tables.keys()):
                name = f"{base_name}_CH{ch}"
                ctdl = imslib.CompensationTableDownload(self.ims, self.channel_tables[ch])
                ctdl.Store(imslib.FileDefault_DEFAULT, name)
                stored.append(name)

            if not stored:
                raise RuntimeError("No channel LUT data was found to store.")

            if self.nvm_widget is not None:
                self.nvm_widget.refresh()

            QMessageBox.information(
                self,
                "Stored",
                "Stored per-channel LUTs in device non-volatile memory as:\n- "
                + "\n- ".join(stored)
            )

        except Exception as e:
            error_box(str(e))


# -------------------------------------------------
# Non-volatile memory file viewer and manager
# -------------------------------------------------
class NVMFilesWidget(QWidget):
    def __init__(self, ims):
        super().__init__()
        self.ims = ims

        root = QVBoxLayout(self)

        info = QLabel(
            "View files stored in device non-volatile memory, change whether a file is default, "
            "and delete selected or all entries."
        )
        info.setWordWrap(True)
        root.addWidget(info)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File", "Type", "Default"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        root.addWidget(self.table, 1)

        btn_row = QHBoxLayout()

        self.btn_refresh = QPushButton("Refresh")
        self.btn_set_default = QPushButton("Set Default")
        self.btn_clear_default = QPushButton("Clear Default")
        self.btn_delete_selected = QPushButton("Delete Selected")
        self.btn_delete_all = QPushButton("Delete All")

        btn_row.addWidget(self.btn_refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_set_default)
        btn_row.addWidget(self.btn_clear_default)
        btn_row.addWidget(self.btn_delete_selected)
        btn_row.addWidget(self.btn_delete_all)

        root.addLayout(btn_row)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_set_default.clicked.connect(self.set_selected_default)
        self.btn_clear_default.clicked.connect(self.clear_selected_default)
        self.btn_delete_selected.clicked.connect(self.delete_selected)
        self.btn_delete_all.clicked.connect(self.delete_all)

        if self.ims == "trial_mode":
            for btn in (
                self.btn_refresh,
                self.btn_set_default,
                self.btn_clear_default,
                self.btn_delete_selected,
                self.btn_delete_all,
            ):
                btn.setEnabled(False)
                btn.setToolTip("Trial mode: no device connected")
        else:
            self.refresh()

    def _selected_name(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return item.text()

    def refresh(self):
        self.table.setRowCount(0)

        if self.ims == "trial_mode":
            return

        try:
            viewer = imslib.FileSystemTableViewer(self.ims)

            if not viewer.IsValid:
                return

            self.table.setRowCount(len(viewer))
            for row, entry in enumerate(viewer):
                name = str(entry.Name)
                typ = filesystem_type_to_text(entry.Type)
                default_text = "✓" if bool(entry.IsDefault) else ""

                item_default = QTableWidgetItem(default_text)
                item_default.setTextAlignment(Qt.AlignCenter)

                self.table.setItem(row, 0, QTableWidgetItem(name))
                self.table.setItem(row, 1, QTableWidgetItem(typ))
                self.table.setItem(row, 2, QTableWidgetItem(item_default))

            if len(viewer):
                self.table.selectRow(0)

        except Exception as e:
            error_box(f"Failed to read device files: {e}")

    def set_selected_default(self):
        name = self._selected_name()
        if not name:
            error_box("Select a file first.")
            return

        try:
            fsm = imslib.FileSystemManager(self.ims)
            ok = fsm.SetDefault(name)
            if not ok:
                raise RuntimeError(f"SetDefault returned False for '{name}'")
            self.refresh()
        except Exception as e:
            error_box(f"Failed to set default: {e}")

    def clear_selected_default(self):
        name = self._selected_name()
        if not name:
            error_box("Select a file first.")
            return

        try:
            fsm = imslib.FileSystemManager(self.ims)
            ok = fsm.ClearDefault(name)
            if not ok:
                raise RuntimeError(f"ClearDefault returned False for '{name}'")
            self.refresh()
        except Exception as e:
            error_box(f"Failed to clear default: {e}")

    def delete_selected(self):
        name = self._selected_name()
        if not name:
            error_box("Select a file first.")
            return

        ans = QMessageBox.question(
            self,
            "Delete File",
            f"Delete '{name}' from device storage?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        try:
            fsm = imslib.FileSystemManager(self.ims)
            ok = fsm.Delete(name)
            if not ok:
                raise RuntimeError(f"Delete returned False for '{name}'")

            try:
                fsm.Sanitize()
            except Exception:
                pass

            self.refresh()

        except Exception as e:
            error_box(f"Failed to delete file: {e}")

    def delete_all(self):
        ans = QMessageBox.warning(
            self,
            "Delete All Files",
            "Delete all stored files from device storage?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        try:
            viewer = imslib.FileSystemTableViewer(self.ims)
            if not viewer.IsValid:
                return

            names = [str(entry.Name) for entry in viewer if str(entry.Name)]

            if not names:
                QMessageBox.information(self, "Delete All", "No files found.")
                return

            fsm = imslib.FileSystemManager(self.ims)
            failed = []

            for name in names:
                try:
                    ok = fsm.Delete(name)
                    if not ok:
                        failed.append(name)
                except Exception:
                    failed.append(name)

            try:
                fsm.Sanitize()
            except Exception:
                pass

            self.refresh()

            if failed:
                QMessageBox.warning(
                    self,
                    "Delete All",
                    "Some files could not be deleted:\n- " + "\n- ".join(failed)
                )
            else:
                QMessageBox.information(
                    self,
                    "Delete All",
                    "All files deleted successfully."
                )

        except Exception as e:
            error_box(f"Failed to delete all files: {e}")


# -------------------------------------------------
# About tab
# -------------------------------------------------

class AboutWidget(QWidget):
    def __init__(self, ims):
        super().__init__()
        self.ims = ims

        root = QVBoxLayout(self)

        if ims == "trial_mode":
            lbl = QLabel("Trial mode active.\nNo device connected.")
            lbl.setWordWrap(True)
            root.addWidget(lbl)

            sdk_box = QGroupBox("SDK Information")
            sdk_form = QFormLayout(sdk_box)
            self.lbl_sdk_ver = QLabel("-")
            self.lbl_app_ver = QLabel("-")

            for w in (self.lbl_sdk_ver, self.lbl_app_ver):
                w.setTextInteractionFlags(Qt.TextSelectableByMouse)
                w.setWordWrap(True)

            sdk_form.addRow("SDK version:", self.lbl_sdk_ver)
            sdk_form.addRow("Application version:", self.lbl_app_ver)
            root.addWidget(sdk_box)
            root.addStretch(1)
            self.lbl_sdk_ver.setText(self._safe_text(lambda: imslib.LibVersion().GetVersion()))
            self.lbl_app_ver.setText(get_application_version())
            return

        dev_box = QGroupBox("Device Versions")
        dev_form = QFormLayout(dev_box)

        self.lbl_conn = QLabel("-")
        self.lbl_device_model = QLabel("-")
        self.lbl_device_desc = QLabel("-")
        self.lbl_device_fw = QLabel("-")

        self.lbl_ctrl_fw = QLabel("-")
        self.lbl_synth_fw = QLabel("-")

        for w in (
            self.lbl_conn,
            self.lbl_device_model,
            self.lbl_device_desc,
            self.lbl_device_fw,
            self.lbl_ctrl_fw,
            self.lbl_synth_fw,
        ):
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
            w.setWordWrap(True)

        dev_form.addRow("Connection:", self.lbl_conn)
        dev_form.addRow("Device model:", self.lbl_device_model)
        dev_form.addRow("Device description:", self.lbl_device_desc)

        self._ctrl_fw_row = QLabel("Controller FW version:")
        self._synth_fw_row = QLabel("Synth FW version:")

        dev_form.addRow("Device version:", self.lbl_device_fw)
        dev_form.addRow(self._ctrl_fw_row, self.lbl_ctrl_fw)
        dev_form.addRow(self._synth_fw_row, self.lbl_synth_fw)

        root.addWidget(dev_box)

        sdk_box = QGroupBox("SDK Information")
        sdk_form = QFormLayout(sdk_box)

        self.lbl_sdk_ver = QLabel("-")
        self.lbl_app_ver = QLabel("-")

        for w in (self.lbl_sdk_ver, self.lbl_app_ver):
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
            w.setWordWrap(True)

        sdk_form.addRow("SDK version:", self.lbl_sdk_ver)
        sdk_form.addRow("Application version:", self.lbl_app_ver)
        root.addWidget(sdk_box)
        root.addStretch(1)

        self._populate()

    def _safe_text(self, fn, default="-"):
        try:
            v = fn()
            if v is None:
                return default
            return str(v)
        except Exception:
            return default

    def _set_row_visible(self, label_widget: QLabel, value_widget: QLabel, visible: bool):
        label_widget.setVisible(visible)
        value_widget.setVisible(visible)

    def _populate(self):
        self.lbl_conn.setText(self._safe_text(lambda: self.ims.ConnPort()))
        self.lbl_device_model.setText(self._safe_text(lambda: self.ims.Ctlr().Model()))
        self.lbl_device_desc.setText(self._safe_text(lambda: self.ims.Ctlr().Description()))

        ctrl_fw = self._safe_text(lambda: self.ims.Ctlr().GetVersion())
        synth_fw = self._safe_text(lambda: self.ims.Synth().GetVersion())

        if ctrl_fw == synth_fw:
            self.lbl_device_fw.setText(ctrl_fw)
            self.lbl_device_fw.setVisible(True)
            self._set_row_visible(self._ctrl_fw_row, self.lbl_ctrl_fw, False)
            self._set_row_visible(self._synth_fw_row, self.lbl_synth_fw, False)
        else:
            self.lbl_device_fw.setText("-")
            self.lbl_device_fw.setVisible(False)
            self._set_row_visible(self._ctrl_fw_row, self.lbl_ctrl_fw, True)
            self._set_row_visible(self._synth_fw_row, self.lbl_synth_fw, True)
            self.lbl_ctrl_fw.setText(ctrl_fw)
            self.lbl_synth_fw.setText(synth_fw)

        self.lbl_sdk_ver.setText(self._safe_text(lambda: imslib.LibVersion().GetVersion()))
        self.lbl_app_ver.setText(get_application_version())


# -------------------------------------------------
# Main Window
# -------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, ims, vco, sp, sysf, event_bridge):
        super().__init__()
        self._startup_warning_shown = False

        self.setWindowTitle("iVCS Control Panel")
        ico = resource_path("Isomet.ico")
        if os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))

        self.ims = ims
        self.vco = vco
        self.sp = sp
        self.sysf = sysf

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.setEnabled(False)

        output_rows = [
            ("CH1 Frequency", VCO.VCOOutput_CH1_FREQUENCY, 0),
            ("CH1 Level",     VCO.VCOOutput_CH1_AMPLITUDE, 1),
            ("CH2 Frequency", VCO.VCOOutput_CH2_FREQUENCY, 0),
            ("CH2 Level",     VCO.VCOOutput_CH2_AMPLITUDE, 1),
        ]

        control = QWidget()

        control_root = QVBoxLayout(control)
        outer = QHBoxLayout()

        left = QVBoxLayout()
        constantWidget = ConstantWidget(vco, sp)
        left.addWidget(RangeWidget(vco))
        left.addWidget(RoutingWidget(vco, constantWidget, output_rows))
        left.addWidget(RFMuteWidget(vco))
        left.addWidget(constantWidget)

        right = QVBoxLayout()
        self.comp_bypass = CompensationBypassWidget(sp)
        self.rf_drive = RFDriveWidget(sp, sysf)

        right.addWidget(self.comp_bypass, 0)
        right.addWidget(self.rf_drive, 1)

        self.sync_box = QGroupBox("Sync")
        sync_lay = QVBoxLayout(self.sync_box)

        self.btn_phase_resync = QPushButton("Phase Resync")
        self.btn_phase_resync.clicked.connect(self.phase_resync)
        sync_lay.addWidget(self.btn_phase_resync)

        right.addWidget(self.sync_box)

        outer.addLayout(left, 3)
        outer.addLayout(right, 1)

        self.btn_save = QPushButton("Save Startup State")
        self.btn_save.clicked.connect(self.save_state)
        self.btn_save.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        control_root.addLayout(outer, 1)
        control_root.addWidget(self.btn_save)

        advanced = QWidget()
        advanced_root = QVBoxLayout(advanced)
        self.lbl = QLabel(
            "Adjusting advanced settings may cause unintended behavior. "
            " Please refer to the documentation and contact Isomet support if you have questions about these features."
        )
        self.lbl.setTextFormat(Qt.RichText)
        self.lbl.setWordWrap(True)
        advanced_root.addWidget(self.lbl)
        advanced_root.addWidget(FilterWidget(vco))
        advanced_root.addWidget(GainWidget(vco))

        advanced_root.addStretch(1)

        self.tab_control = control
        self.tab_monitoring = MonitoringWidget(vco, event_bridge)
        self.tab_advanced = advanced
        self.tab_comp = CompensationWidget(ims, sp)
        self.tab_nvm = NVMFilesWidget(ims)
        self.tab_about = AboutWidget(ims)

        self.tabs.addTab(self.tab_control, "Control")
        self.tabs.addTab(self.tab_monitoring, "Monitoring")
        self.tabs.addTab(self.tab_advanced, "Advanced")
        self.tabs.addTab(self.tab_comp, "Compensation")
        self.tabs.addTab(self.tab_nvm, "NVM Files")
        self.tabs.addTab(self.tab_about, "About")

        self.is_trial_mode = (ims == "trial_mode")
        if self.is_trial_mode:
            self.btn_save.setEnabled(False)
            self.btn_phase_resync.setEnabled(False)
            self.btn_save.setToolTip("Trial mode: no device connected")
            self.btn_phase_resync.setToolTip("Trial mode: no device connected")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._startup_warning_shown:
            self._startup_warning_shown = True
            QTimer.singleShot(0, self.show_startup_warning)

    def show_startup_warning(self):
        dlg = StartupWarningDialog(self)
        dlg.exec()
        self.tabs.setEnabled(True)

    def phase_resync(self):
        if self.sp is None:
            return

        try:
            self.sp.PhaseResync()
            QMessageBox.information(self, "Phase Resync", "Phase resync command sent.")
        except Exception as e:
            QMessageBox.critical(self, "Phase Resync Failed", str(e))

    def save_state(self):
        if self.sysf is None or self.vco is None:
            QMessageBox.information(self, "Trial Mode", "Startup state cannot be saved in trial mode.")
            return

        try:
            rf = self.rf_drive.get_startup_values()
            comp = self.comp_bypass.get_startup_values()

            cfg = imslib.StartupConfiguration()
            cfg.DDSPower = rf["dds"]
            cfg.RFAmplitudeCh1 = rf["w1"]
            cfg.RFAmplitudeCh2 = rf["w2"]
            cfg.AmplitudeControlSource = imslib.SignalPath.AmplitudeControl_INDEPENDENT
            cfg.RFGate = rf["amp_en"]
            cfg.ImageUseAmplitudeCompensation = not comp["amp_bypass"]
            cfg.ImageUsePhaseCompensation = not comp["phase_bypass"]

            ok = self.sysf.StoreStartupConfig(cfg)
            if not ok:
                raise RuntimeError("StoreStartupConfig returned False")

            self.vco.SaveStartupState()

            QMessageBox.information(
                self,
                "Saved",
                "Saved startup state to device:\n"
                f"- VCO settings (frequency, gain, routing, etc.)\n"
                f"- DDS: {float(rf['dds']):.2f}%\n"
                f"- CH1 (W1): {float(rf['w1']):.2f}%\n"
                f"- CH2 (W2): {float(rf['w2']):.2f}%\n"
                f"- Amplifier: {'ON' if rf['amp_en'] else 'OFF'}\n"
                f"- Phase Compensation: {'BYPASSED' if comp['phase_bypass'] else 'ENABLED'}\n"
                f"- Amplitude Compensation: {'BYPASSED' if comp['amp_bypass'] else 'ENABLED'}\n"
            )
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", str(e))


# -------------------------------------------------
# VCO Monitor Event loop thread
# -------------------------------------------------

class VCOEventBridge(QObject):
    voltage_ready = Signal(dict)

    def __init__(self, vco):
        super().__init__()
        self.vco = vco

    def on_voltage_read_complete(self):
        try:
            data = dict(self.vco.GetVoltageInputDataStr().items())
            self.voltage_ready.emit(data)
        except Exception as e:
            print("Voltage read error:", e)


class VCOEventLoop(threading.Thread):
    def __init__(self, vco, waiter, event_messages, event_bridge):
        super().__init__(daemon=True)
        self.waiter = waiter
        self.vco = vco
        self.event_messages = event_messages
        self._running = threading.Event()
        self.event_bridge = event_bridge

    def subscribe(self):
        for evt in self.event_messages.keys():
            self.vco.VCOEventSubscribe(evt, self.waiter)

    def unsubscribe(self):
        for evt in self.event_messages.keys():
            self.vco.VCOEventUnsubscribe(evt, self.waiter)

    def run(self):
        self._running.set()
        self.subscribe()
        try:
            while self._running.is_set():
                try:
                    msg, args = self.waiter.wait(timeout=0.1)
                    if msg == imslib.VCOEvents_VCO_UPDATE_AVAILABLE:
                        self.event_bridge.on_voltage_read_complete()
                except TimeoutError:
                    continue
        finally:
            self.unsubscribe()

    def stop(self):
        self._running.clear()


VCO_EVENT_MESSAGES = {
    imslib.VCOEvents_VCO_UPDATE_AVAILABLE: "VCO Update",
    imslib.VCOEvents_VCO_READ_FAILED: "VCO Read Failed",
}


class MonitoringWidget(QGroupBox):
    def __init__(self, vco, event_bridge):
        super().__init__("Input Monitoring")
        self.vco = vco

        layout = QGridLayout(self)
        self.displays = {}

        labels = [
            "Voltage Input Ch A",
            "Voltage Input Ch B",
            "Processed Value Ch A",
            "Processed Value Ch B",
        ]

        for row, name in enumerate(labels):
            layout.addWidget(QLabel(name), row, 0)

            lcd = QLCDNumber()
            lcd.setDigitCount(7)
            lcd.setSmallDecimalPoint(True)
            lcd.setSegmentStyle(QLCDNumber.Flat)
            lcd.display("0.000")

            layout.addWidget(lcd, row, 1)
            if "Voltage" in name:
                layout.addWidget(QLabel("Volts"), row, 2)
            else:
                layout.addWidget(QLabel("%"), row, 2)

            self.displays[name] = lcd

        if event_bridge is not None:
            event_bridge.voltage_ready.connect(self.update_values)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.request_update)
        self.timer.start(200)

    def request_update(self):
        try:
            if self.vco is None:
                return
            self.vco.ReadVoltageInput()
        except Exception as e:
            print("ReadVoltageInput failed:", e)

    def update_values(self, data):
        for key, percent_obj in data.items():
            if key not in self.displays:
                continue
            try:
                value = float(percent_obj)
                if "Voltage" in key:
                    value /= 10.0
                self.displays[key].display(f"{value:.3f}")
            except Exception as e:
                print(f"Failed to update {key}: {e}")

# -------------------------------------------------
# Startup Warning Dialog
# -------------------------------------------------
class StartupWarningDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Warning")
        self.setModal(True)
        self.setWindowFlags(
            Qt.Dialog |
            Qt.CustomizeWindowHint |
            Qt.WindowTitleHint
        )

        lay = QVBoxLayout(self)
        top = QHBoxLayout()

        icon = self.style().standardIcon(QStyle.SP_MessageBoxWarning)
        self.setWindowIcon(icon)

        self.lbl = QLabel(
            "The default control panel settings shown when this application starts do not necessarily "
            "reflect the settings currently active on the connected device.<br><br>"
            "<b>There is currently no readback of the device's active settings into this control panel.</b><br><br>"
            "The displayed values are only the application's startup defaults until you manually "
            "change a setting."
        )
        self.lbl.setTextFormat(Qt.RichText)
        self.lbl.setWordWrap(True)
        top.addWidget(self.lbl, 1)

        lay.addLayout(top)

        self.btn = QPushButton("I Understand")
        self.btn.clicked.connect(self.accept)
        lay.addWidget(self.btn)

        self.resize(400, 200)


# -------------------------------------------------
# Entry Point
# -------------------------------------------------

def main():
    app = QApplication(sys.argv)

    ico = resource_path("Isomet.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))

    ims = DeviceSelectDialog.get_connected_system(None)
    if ims is None:
        return 0

    vco = None
    sp = None
    sysf = None
    event_bridge = None
    vco_event_loop = None

    if ims == "trial_mode":
        QMessageBox.information(None, "Mode", "Trial mode selected.")
    else:
        ims.Connect()

        vco = VCO(ims)

        VCOWaiter = EventWaiter()
        VCOWaiter.listen_for(list(VCO_EVENT_MESSAGES.keys()))

        event_bridge = VCOEventBridge(vco)
        vco_event_loop = VCOEventLoop(vco, VCOWaiter, VCO_EVENT_MESSAGES, event_bridge)
        vco_event_loop.start()

        sp = imslib.SignalPath(ims)
        sysf = imslib.SystemFunc(ims)

        sp.PhaseResync()

    win = MainWindow(ims, vco, sp, sysf, event_bridge)
    win.show()
    win.resize(500, 600)
    rc = app.exec()

    try:
        if vco_event_loop is not None:
            vco_event_loop.stop()
    except Exception:
        pass

    try:
        if ims != "trial_mode":
            ims.Disconnect()
    except Exception:
        pass

    return rc


if __name__ == "__main__":
    main()