"""Equalizer for the phone stream.

Windows plays the Bluetooth stream itself and offers no hook for effects, so
this captures the "Line (<phone> A2DP SNK)" endpoint, filters it, plays it on
the default speaker, and mutes Windows' own playback session meanwhile.
"""

import threading
import time
import warnings

# comtypes must initialise COM before soundcard does, or it fails to import.
import comtypes
from pycaw.pycaw import AudioUtilities

import numpy as np
import soundcard as sc
from scipy.signal import sosfilt

SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK = 480  # 10 ms; smaller blocks dropped out in testing

# (filter type, frequency in Hz)
BANDS = [("lowshelf", 80), ("peak", 250), ("peak", 1000), ("peak", 4000), ("highshelf", 10000)]
MAX_GAIN = 12

PRESETS = {
	"flat": [0, 0, 0, 0, 0],
	"clear": [0, -2, 0, 3, 5],
	"bass": [6, 3, 0, 0, 0],
	"vocal": [-2, 0, 3, 3, 1],
}


def _biquad(kind, freq, gain_db, q=1.0):
	"""RBJ audio EQ cookbook biquad, returned as one SOS row."""
	a = 10 ** (gain_db / 40)
	w0 = 2 * np.pi * freq / SAMPLE_RATE
	cos, sin = np.cos(w0), np.sin(w0)
	if kind == "peak":
		alpha = sin / (2 * q)
		b = [1 + alpha * a, -2 * cos, 1 - alpha * a]
		den = [1 + alpha / a, -2 * cos, 1 - alpha / a]
	else:
		alpha = sin / 2 * np.sqrt(2)  # shelf slope 1
		sq = 2 * np.sqrt(a) * alpha
		if kind == "lowshelf":
			b = [a * ((a + 1) - (a - 1) * cos + sq), 2 * a * ((a - 1) - (a + 1) * cos), a * ((a + 1) - (a - 1) * cos - sq)]
			den = [(a + 1) + (a - 1) * cos + sq, -2 * ((a - 1) + (a + 1) * cos), (a + 1) + (a - 1) * cos - sq]
		else:
			b = [a * ((a + 1) + (a - 1) * cos + sq), -2 * a * ((a - 1) + (a + 1) * cos), a * ((a + 1) + (a - 1) * cos - sq)]
			den = [(a + 1) - (a - 1) * cos + sq, 2 * ((a - 1) - (a + 1) * cos), (a + 1) - (a - 1) * cos - sq]
	return np.array(b + den) / den[0]


class Equalizer:
	def __init__(self, gains=None):
		self.zi = np.zeros((len(BANDS), 2, CHANNELS))
		self.set_gains(gains or PRESETS["flat"])

	def set_gains(self, gains):
		gains = [max(-MAX_GAIN, min(MAX_GAIN, g)) for g in gains]
		self.sos = np.array([_biquad(kind, freq, g) for (kind, freq), g in zip(BANDS, gains)])
		# Leave some headroom for boosts; whatever still overshoots is clipped.
		self.preamp = 10 ** (-max(0, max(gains)) / 2 / 20)

	def process(self, frames):
		out, self.zi = sosfilt(self.sos, frames * self.preamp, axis=0, zi=self.zi)
		return np.clip(out, -1, 1)


def find_line_endpoint(device_name):
	"""The capture endpoint Windows creates for a connected phone."""
	for mic in sc.all_microphones():
		if "A2DP SNK" in mic.name and device_name in mic.name:
			return mic
	return None


def set_session_mute(endpoint_name, mute):
	"""Mute or unmute Windows' own playback of the phone stream."""
	found = False
	for session in AudioUtilities.GetAllSessions():
		try:
			if session._ctl.GetDisplayName() == endpoint_name:
				session.SimpleAudioVolume.SetMute(int(mute), None)
				found = True
		except Exception:
			pass
	return found


class EqBridge:
	"""Captures the phone endpoint, applies the equalizer and plays the result."""

	def __init__(self, device_name, equalizer, on_error=None):
		self.device_name = device_name
		self.equalizer = equalizer
		self.on_error = on_error
		self.running = False
		self.thread = None
		self.endpoint_name = None

	def start(self):
		self.running = True
		self.thread = threading.Thread(target=self._run, daemon=True)
		self.thread.start()

	def stop(self):
		self.running = False
		if self.thread is not None:
			self.thread.join(timeout=2)
			self.thread = None

	def _run(self):
		comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
		warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
		try:
			while self.running:
				mic = find_line_endpoint(self.device_name)
				if mic is None:
					time.sleep(0.5)
					continue
				self.endpoint_name = mic.name
				try:
					self._stream(mic)
				except Exception as e:
					# Usually the endpoint vanished on disconnect; try again shortly.
					if self.on_error is not None:
						self.on_error(str(e))
					time.sleep(1)
		finally:
			if self.endpoint_name:
				set_session_mute(self.endpoint_name, False)
			comtypes.CoUninitialize()

	def _stream(self, mic):
		speaker = sc.default_speaker()
		next_check = 0
		with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK) as rec, \
				speaker.player(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK) as player:
			while self.running:
				now = time.monotonic()
				if now >= next_check:
					next_check = now + 1
					# Windows may recreate its session on reconnect, so keep it muted.
					set_session_mute(mic.name, True)
					if sc.default_speaker().id != speaker.id:
						return  # default output changed: reopen on the new one
				player.play(self.equalizer.process(rec.record(numframes=BLOCK)))
