"""Bluetooth Audio Sink: play audio from a paired phone on this PC.

Uses the Windows 10 2004+ AudioPlaybackConnection API (Bluetooth A2DP sink).
The phone must be paired first in Settings > Devices > Bluetooth.
"""

import asyncio
import ctypes
import json
import locale
import os
import threading

import wx
import wx.adv

from eq import BANDS, MAX_GAIN, PRESETS, EqBridge, Equalizer
from winrt.windows.devices.enumeration import DeviceInformation
from winrt.windows.media.audio import (
	AudioPlaybackConnection,
	AudioPlaybackConnectionOpenResultStatus,
	AudioPlaybackConnectionState,
)

APP_NAME = "Bluetooth Audio Sink"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "BluetoothAudioSink")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

STRINGS = {
	"en": {
		"devices": "&Paired devices:",
		"connect": "&Connect",
		"disconnect": "&Disconnect",
		"refresh": "&Refresh",
		"auto": "&Automatically connect to the last device on start",
		"status": "&Status:",
		"none": "No paired A2DP devices found. Pair your phone in Settings > Devices > Bluetooth.",
		"idle": "Not connected.",
		"select": "Select a device first.",
		"connecting": "Connecting to {name}...",
		"connected": "Connected to {name}. Play audio on your phone.",
		"closed": "Disconnected from {name}.",
		"failed": "Could not connect to {name}: {reason}.",
		"timeout": "the phone did not respond in time",
		"denied": "access denied by the system",
		"unknown": "unknown error",
		"unreachable": "the phone is not reachable. Make sure Bluetooth is on and the phone is nearby",
		"unsupported": "Bluetooth A2DP sink requires Windows 10 version 2004 or later.",
		"suffix_connected": " (connected)",
		"exit_button": "E&xit",
		"tray_show": "&Show",
		"tray_exit": "E&xit",
		"hidden_hint": "Still running in the system tray. Choose Exit to quit.",
		"eq_button": "&Equalizer...",
		"eq_title": "Equalizer",
		"eq_enable": "&Enable equalizer",
		"eq_preset": "&Preset:",
		"eq_close": "&Close",
		"preset_flat": "Flat",
		"preset_clear": "Clearer",
		"preset_bass": "Bass boost",
		"preset_vocal": "Vocal",
		"preset_custom": "Custom",
		"eq_band": "{freq} (dB):",
	},
	"id": {
		"devices": "&Perangkat yang dipasangkan:",
		"connect": "&Hubungkan",
		"disconnect": "&Putuskan",
		"refresh": "&Segarkan",
		"auto": "Hubungkan &otomatis ke perangkat terakhir saat dibuka",
		"status": "S&tatus:",
		"none": "Tidak ada perangkat A2DP yang dipasangkan. Pasangkan HP di Pengaturan > Perangkat > Bluetooth.",
		"idle": "Tidak terhubung.",
		"select": "Pilih perangkat terlebih dahulu.",
		"connecting": "Menghubungkan ke {name}...",
		"connected": "Terhubung ke {name}. Putar audio di HP Anda.",
		"closed": "Terputus dari {name}.",
		"failed": "Gagal terhubung ke {name}: {reason}.",
		"timeout": "HP tidak merespons tepat waktu",
		"denied": "akses ditolak oleh sistem",
		"unknown": "kesalahan tidak diketahui",
		"unreachable": "HP tidak terjangkau. Pastikan Bluetooth HP menyala dan HP berada di dekat laptop",
		"unsupported": "Bluetooth A2DP sink memerlukan Windows 10 versi 2004 atau lebih baru.",
		"suffix_connected": " (terhubung)",
		"exit_button": "&Keluar",
		"tray_show": "&Tampilkan",
		"tray_exit": "&Keluar",
		"hidden_hint": "Masih berjalan di system tray. Pilih Keluar untuk menutup aplikasi.",
		"eq_button": "&Equalizer...",
		"eq_title": "Equalizer",
		"eq_enable": "&Aktifkan equalizer",
		"eq_preset": "&Preset:",
		"eq_close": "&Tutup",
		"preset_flat": "Datar",
		"preset_clear": "Lebih jernih",
		"preset_bass": "Bass kuat",
		"preset_vocal": "Vokal",
		"preset_custom": "Kustom",
		"eq_band": "{freq} (dB):",
	},
}


def _pick_language():
	try:
		lang = (locale.getdefaultlocale()[0] or "").lower()
	except ValueError:
		lang = ""
	return "id" if lang.startswith("id") or lang.startswith("in_") else "en"


T = STRINGS[_pick_language()]


# HRESULTs seen when the phone's Bluetooth is off or the phone is out of range.
UNREACHABLE_ERRORS = {
	0x8007001F,  # ERROR_GEN_FAILURE
	0x80070015,  # ERROR_NOT_READY
	0x80070079,  # ERROR_SEM_TIMEOUT
	0x8007048F,  # ERROR_DEVICE_NOT_CONNECTED
	0x80070490,  # ERROR_NOT_FOUND
}


def describe_error(code):
	if not code:
		return T["unknown"]
	code &= 0xFFFFFFFF
	if code in UNREACHABLE_ERRORS:
		return T["unreachable"]
	return f"{T['unknown']} (0x{code:08X})"


def load_config():
	try:
		with open(CONFIG_PATH, encoding="utf-8") as f:
			return json.load(f)
	except (OSError, ValueError):
		return {}


def save_config(config):
	try:
		os.makedirs(CONFIG_DIR, exist_ok=True)
		with open(CONFIG_PATH, "w", encoding="utf-8") as f:
			json.dump(config, f, indent=2)
	except OSError:
		pass


class AsyncRunner:
	"""Runs an asyncio loop on a background thread for WinRT async calls."""

	def __init__(self):
		self.loop = asyncio.new_event_loop()
		self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
		self.thread.start()

	def submit(self, coro):
		return asyncio.run_coroutine_threadsafe(coro, self.loop)

	def stop(self):
		self.loop.call_soon_threadsafe(self.loop.stop)


PRESET_KEYS = ["flat", "clear", "bass", "vocal"]


def _freq_label(freq):
	return f"{freq // 1000} kHz" if freq >= 1000 else f"{freq} Hz"


class GainSliderAccessible(wx.Accessible):
	"""Native trackbars expose no name, so screen readers announced only the number."""

	def __init__(self, slider, label):
		super().__init__(slider)
		self.slider = slider
		self.label = label

	def GetName(self, child_id):
		if child_id != 0:
			return (wx.ACC_NOT_IMPLEMENTED, "")
		return (wx.ACC_OK, self.label)

	def GetValue(self, child_id):
		if child_id != 0:
			return (wx.ACC_NOT_IMPLEMENTED, "")
		return (wx.ACC_OK, f"{self.slider.GetValue():+d} dB")


class EqDialog(wx.Dialog):
	"""Changes apply immediately; the parent saves them."""

	def __init__(self, parent):
		super().__init__(parent, title=T["eq_title"])
		self.frame = parent
		config = parent.config
		sizer = wx.BoxSizer(wx.VERTICAL)

		self.enable_cb = wx.CheckBox(self, label=T["eq_enable"])
		self.enable_cb.SetValue(config.get("eq_enabled", False))
		sizer.Add(self.enable_cb, 0, wx.ALL, 6)

		sizer.Add(wx.StaticText(self, label=T["eq_preset"]), 0, wx.LEFT | wx.RIGHT, 6)
		self.preset = wx.Choice(self, choices=[T["preset_" + k] for k in PRESET_KEYS] + [T["preset_custom"]])
		sizer.Add(self.preset, 0, wx.EXPAND | wx.ALL, 6)

		gains = config.get("eq_gains", PRESETS["flat"])
		self.sliders = []
		for (kind, freq), gain in zip(BANDS, gains):
			label = _freq_label(freq)
			sizer.Add(wx.StaticText(self, label=T["eq_band"].format(freq=label)), 0, wx.LEFT | wx.RIGHT, 6)
			slider = wx.Slider(self, value=gain, minValue=-MAX_GAIN, maxValue=MAX_GAIN, style=wx.SL_HORIZONTAL | wx.SL_LABELS)
			slider.SetAccessible(GainSliderAccessible(slider, label))
			slider.SetLineSize(1)
			slider.SetPageSize(3)
			slider.Bind(wx.EVT_SLIDER, self.on_slider)
			sizer.Add(slider, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
			self.sliders.append(slider)
		self.select_matching_preset()

		close = wx.Button(self, wx.ID_CLOSE, label=T["eq_close"])
		self.SetEscapeId(wx.ID_CLOSE)
		sizer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
		self.SetSizerAndFit(sizer)
		self.SetSize((420, -1))

		self.enable_cb.Bind(wx.EVT_CHECKBOX, lambda e: self.frame.set_eq_enabled(self.enable_cb.GetValue()))
		self.preset.Bind(wx.EVT_CHOICE, self.on_preset)
		self.enable_cb.SetFocus()

	def gains(self):
		return [s.GetValue() for s in self.sliders]

	def select_matching_preset(self):
		gains = self.gains()
		for i, key in enumerate(PRESET_KEYS):
			if PRESETS[key] == gains:
				self.preset.SetSelection(i)
				return
		self.preset.SetSelection(len(PRESET_KEYS))

	def on_preset(self, event):
		index = self.preset.GetSelection()
		if index >= len(PRESET_KEYS):
			return
		for slider, gain in zip(self.sliders, PRESETS[PRESET_KEYS[index]]):
			slider.SetValue(gain)
		self.frame.set_eq_gains(self.gains())

	def on_slider(self, event):
		self.select_matching_preset()
		self.frame.set_eq_gains(self.gains())


class MainFrame(wx.Frame):
	def __init__(self):
		super().__init__(None, title=APP_NAME, size=(520, 380))
		self.config = load_config()
		self.runner = AsyncRunner()
		self.devices = {}  # id -> name, in discovery order
		self.connections = {}  # id -> AudioPlaybackConnection
		self.bridges = {}  # id -> EqBridge
		self.equalizer = Equalizer(self.config.get("eq_gains"))
		self.watcher = None
		self.auto_tried = False
		self.exiting = False
		self.hint_shown = False
		self.tray = TrayIcon(self)

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)

		sizer.Add(wx.StaticText(panel, label=T["devices"]), 0, wx.ALL, 6)
		self.list = wx.ListBox(panel, style=wx.LB_SINGLE)
		sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		self.connect_btn = wx.Button(panel, label=T["connect"])
		self.disconnect_btn = wx.Button(panel, label=T["disconnect"])
		self.refresh_btn = wx.Button(panel, label=T["refresh"])
		self.eq_btn = wx.Button(panel, label=T["eq_button"])
		self.exit_btn = wx.Button(panel, label=T["exit_button"])
		for b in (self.connect_btn, self.disconnect_btn, self.refresh_btn, self.eq_btn, self.exit_btn):
			buttons.Add(b, 0, wx.RIGHT, 6)
		sizer.Add(buttons, 0, wx.ALL, 6)

		self.auto_cb = wx.CheckBox(panel, label=T["auto"])
		self.auto_cb.SetValue(self.config.get("auto_connect", True))
		sizer.Add(self.auto_cb, 0, wx.LEFT | wx.RIGHT, 6)

		sizer.Add(wx.StaticText(panel, label=T["status"]), 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.status = wx.TextCtrl(panel, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL, size=(-1, 50))
		sizer.Add(self.status, 0, wx.EXPAND | wx.ALL, 6)

		panel.SetSizer(sizer)

		self.connect_btn.Bind(wx.EVT_BUTTON, self.on_connect)
		self.disconnect_btn.Bind(wx.EVT_BUTTON, self.on_disconnect)
		self.refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self.start_watcher())
		self.eq_btn.Bind(wx.EVT_BUTTON, self.on_eq)
		self.exit_btn.Bind(wx.EVT_BUTTON, lambda e: self.exit_app())
		self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)
		self.list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_connect)
		self.list.Bind(wx.EVT_LISTBOX, lambda e: self.update_buttons())
		self.auto_cb.Bind(wx.EVT_CHECKBOX, self.on_auto_toggle)
		self.Bind(wx.EVT_CLOSE, self.on_close)

		self.set_status(T["idle"])
		self.update_buttons()
		self.start_watcher()
		self.list.SetFocus()

	# Device discovery

	def start_watcher(self):
		self.stop_watcher()
		try:
			selector = AudioPlaybackConnection.get_device_selector()
		except Exception:
			self.set_status(T["unsupported"])
			return
		for dev_id in list(self.devices):
			if dev_id not in self.connections:
				del self.devices[dev_id]
		self.refresh_list()
		watcher = DeviceInformation.create_watcher_aqs_filter(selector)
		watcher.add_added(lambda s, info: wx.CallAfter(self.on_device_added, info.id, info.name))
		watcher.add_removed(lambda s, upd: wx.CallAfter(self.on_device_removed, upd.id))
		watcher.add_enumeration_completed(lambda s, a: wx.CallAfter(self.on_enumeration_completed))
		watcher.start()
		self.watcher = watcher

	def stop_watcher(self):
		if self.watcher is not None:
			try:
				self.watcher.stop()
			except Exception:
				pass
			self.watcher = None

	def on_device_added(self, dev_id, name):
		self.devices[dev_id] = name
		self.refresh_list()

	def on_device_removed(self, dev_id):
		if dev_id in self.connections:
			return
		self.devices.pop(dev_id, None)
		self.refresh_list()

	def on_enumeration_completed(self):
		if not self.devices and not self.connections:
			self.set_status(T["none"])
		last = self.config.get("last_device")
		if not self.auto_tried and self.auto_cb.GetValue() and last in self.devices:
			self.auto_tried = True
			self.connect(last)

	def refresh_list(self):
		selected = self.selected_id()
		ids = list(self.devices)
		self.list.Set([
			self.devices[i] + (T["suffix_connected"] if i in self.connections else "")
			for i in ids
		])
		self._ids = ids
		if selected in ids:
			self.list.SetSelection(ids.index(selected))
		elif ids:
			self.list.SetSelection(0)
		self.update_buttons()

	def selected_id(self):
		index = self.list.GetSelection()
		ids = getattr(self, "_ids", [])
		if index == wx.NOT_FOUND or index >= len(ids):
			return None
		return ids[index]

	def update_buttons(self):
		dev_id = self.selected_id()
		self.connect_btn.Enable(dev_id is not None and dev_id not in self.connections)
		self.disconnect_btn.Enable(dev_id is not None and dev_id in self.connections)

	# Connection handling

	def on_connect(self, event):
		dev_id = self.selected_id()
		if dev_id is None:
			self.set_status(T["select"])
			return
		if dev_id not in self.connections:
			self.connect(dev_id)

	def on_disconnect(self, event):
		dev_id = self.selected_id()
		if dev_id in self.connections:
			self.close_connection(dev_id)
			self.set_status(T["closed"].format(name=self.devices.get(dev_id, "")))

	def connect(self, dev_id):
		name = self.devices.get(dev_id, dev_id)
		self.set_status(T["connecting"].format(name=name))
		self.connect_btn.Disable()
		self.runner.submit(self._connect_async(dev_id, name))

	async def _connect_async(self, dev_id, name):
		connection = None
		try:
			connection = AudioPlaybackConnection.try_create_from_id(dev_id)
			if connection is None:
				raise RuntimeError(T["unknown"])
			connection.add_state_changed(
				lambda conn, args: wx.CallAfter(self.on_state_changed, dev_id, conn.state)
			)
			await connection.start_async()
			result = await connection.open_async()
			status = result.status
			if status == AudioPlaybackConnectionOpenResultStatus.SUCCESS:
				wx.CallAfter(self.on_connected, dev_id, name, connection)
				return
			reason = {
				AudioPlaybackConnectionOpenResultStatus.REQUEST_TIMED_OUT: T["timeout"],
				AudioPlaybackConnectionOpenResultStatus.DENIED_BY_SYSTEM: T["denied"],
			}.get(status)
			if reason is None:
				error = result.extended_error
				reason = describe_error(getattr(error, "value", None))
		except OSError as e:
			reason = describe_error(e.winerror) if e.winerror else (str(e) or T["unknown"])
		except Exception as e:
			reason = str(e) or T["unknown"]
		if connection is not None:
			try:
				connection.close()
			except Exception:
				pass
		wx.CallAfter(self.on_connect_failed, name, reason)

	def on_connected(self, dev_id, name, connection):
		self.connections[dev_id] = connection
		self.config["last_device"] = dev_id
		save_config(self.config)
		self.set_status(T["connected"].format(name=name))
		self.refresh_list()
		if self.config.get("eq_enabled", False):
			self.start_bridge(dev_id)

	def on_connect_failed(self, name, reason):
		self.set_status(T["failed"].format(name=name, reason=reason))
		self.update_buttons()

	def on_state_changed(self, dev_id, state):
		if state == AudioPlaybackConnectionState.CLOSED and dev_id in self.connections:
			self.close_connection(dev_id)
			self.set_status(T["closed"].format(name=self.devices.get(dev_id, "")))

	def close_connection(self, dev_id):
		self.stop_bridge(dev_id)
		connection = self.connections.pop(dev_id, None)
		if connection is not None:
			try:
				connection.close()
			except Exception:
				pass
		self.refresh_list()

	# Equalizer

	def start_bridge(self, dev_id):
		if dev_id in self.bridges:
			return
		bridge = EqBridge(self.devices.get(dev_id, ""), self.equalizer)
		bridge.start()
		self.bridges[dev_id] = bridge

	def stop_bridge(self, dev_id):
		bridge = self.bridges.pop(dev_id, None)
		if bridge is not None:
			bridge.stop()

	def set_eq_enabled(self, enabled):
		self.config["eq_enabled"] = enabled
		save_config(self.config)
		for dev_id in list(self.connections):
			if enabled:
				self.start_bridge(dev_id)
			else:
				self.stop_bridge(dev_id)

	def set_eq_gains(self, gains):
		self.equalizer.set_gains(gains)
		self.config["eq_gains"] = gains
		save_config(self.config)

	def on_eq(self, event):
		with EqDialog(self) as dialog:
			dialog.ShowModal()

	# Misc

	def set_status(self, text):
		self.status.SetValue(text)
		self.SetTitle(f"{APP_NAME} - {text}")
		self.tray.set_tooltip(f"{APP_NAME} - {text}")

	def on_auto_toggle(self, event):
		self.config["auto_connect"] = self.auto_cb.GetValue()
		save_config(self.config)

	# Window and tray

	def on_char_hook(self, event):
		if event.GetKeyCode() == wx.WXK_ESCAPE:
			self.hide_to_tray()
		else:
			event.Skip()

	def hide_to_tray(self):
		self.Hide()
		if not self.hint_shown:
			self.hint_shown = True
			self.tray.ShowBalloon(APP_NAME, T["hidden_hint"])

	def show_window(self):
		self.Show()
		self.Iconize(False)
		self.Raise()
		self.list.SetFocus()

	def exit_app(self):
		self.exiting = True
		self.Close()

	def on_close(self, event):
		# Alt+F4 and the close button only hide; Exit really quits.
		if not self.exiting and event.CanVeto():
			event.Veto()
			self.hide_to_tray()
			return
		self.stop_watcher()
		for dev_id in list(self.connections):
			self.close_connection(dev_id)
		self.runner.stop()
		self.tray.RemoveIcon()
		self.tray.Destroy()
		event.Skip()


def _make_icon():
	bitmap = wx.Bitmap(32, 32)
	dc = wx.MemoryDC(bitmap)
	dc.SetBackground(wx.Brush(wx.Colour(30, 110, 230)))
	dc.Clear()
	dc.SetTextForeground(wx.WHITE)
	dc.SetFont(wx.Font(18, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
	w, h = dc.GetTextExtent("B")
	dc.DrawText("B", (32 - w) // 2, (32 - h) // 2)
	dc.SelectObject(wx.NullBitmap)
	return wx.Icon(bitmap)


class TrayIcon(wx.adv.TaskBarIcon):
	def __init__(self, frame):
		super().__init__()
		self.frame = frame
		self.icon = _make_icon()
		self.set_tooltip(APP_NAME)
		# Single click (and Enter from the keyboard) opens the window.
		self.Bind(wx.adv.EVT_TASKBAR_LEFT_UP, lambda e: frame.show_window())

	def set_tooltip(self, text):
		self.SetIcon(self.icon, text[:127])

	def CreatePopupMenu(self):
		menu = wx.Menu()
		show = menu.Append(wx.ID_ANY, T["tray_show"])
		menu.AppendSeparator()
		exit_item = menu.Append(wx.ID_EXIT, T["tray_exit"])
		self.Bind(wx.EVT_MENU, lambda e: self.frame.show_window(), show)
		self.Bind(wx.EVT_MENU, lambda e: self.frame.exit_app(), exit_item)
		return menu


class InstanceSignal:
	"""Lets a second launch tell the running instance to show its window."""

	NAME = "Local\\BluetoothAudioSink-Show"
	ERROR_ALREADY_EXISTS = 183

	def __init__(self):
		self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		self.kernel32.CreateEventW.restype = ctypes.c_void_p
		self.kernel32.SetEvent.argtypes = [ctypes.c_void_p]
		self.kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
		self.handle = self.kernel32.CreateEventW(None, False, False, self.NAME)
		self.already_running = ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS

	def notify(self):
		self.kernel32.SetEvent(self.handle)

	def listen(self, callback):
		def wait():
			while self.kernel32.WaitForSingleObject(self.handle, 0xFFFFFFFF) == 0:
				callback()
		threading.Thread(target=wait, daemon=True).start()


def main():
	# Two instances would fight over the same phone connection.
	signal = InstanceSignal()
	if signal.already_running:
		signal.notify()
		return
	app = wx.App()
	frame = MainFrame()
	signal.listen(lambda: wx.CallAfter(frame.show_window))
	frame.Show()
	app.MainLoop()


if __name__ == "__main__":
	main()
