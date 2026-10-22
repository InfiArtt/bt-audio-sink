"""PC to Phone: stream what this PC is playing to a phone over Wi-Fi.

Captures the default speaker (loopback) and sends raw PCM over a WebSocket.
The phone only needs a browser: open the address shown and press Listen.
"""

import asyncio
import locale
import socket
import threading
import warnings

import numpy as np
import soundcard as sc
import wx
from aiohttp import WSMsgType, web

APP_NAME = "PC to Phone"
PORT = 18765
SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK = 960  # 20 ms per packet

STRINGS = {
	"en": {
		"address": "&Address to open on your phone:",
		"start": "&Start",
		"stop": "S&top",
		"copy": "&Copy address",
		"status": "St&atus:",
		"stopped": "Stopped.",
		"running": "Running. Listeners: {count}.",
		"port_busy": "Could not start: port {port} is already in use.",
		"capture_failed": "Could not capture the PC audio: {error}",
		"copied": "Address copied.",
		"page_title": "PC Audio",
		"page_listen": "Listen",
		"page_stop": "Stop",
		"page_idle": "Press Listen to hear the PC.",
		"page_connecting": "Connecting...",
		"page_playing": "Playing PC audio.",
		"page_lost": "Connection lost, reconnecting...",
		"page_stopped": "Stopped.",
	},
	"id": {
		"address": "&Alamat untuk dibuka di HP:",
		"start": "&Mulai",
		"stop": "&Berhenti",
		"copy": "&Salin alamat",
		"status": "S&tatus:",
		"stopped": "Berhenti.",
		"running": "Berjalan. Pendengar: {count}.",
		"port_busy": "Gagal mulai: port {port} sedang dipakai.",
		"capture_failed": "Gagal merekam suara PC: {error}",
		"copied": "Alamat disalin.",
		"page_title": "Suara PC",
		"page_listen": "Dengarkan",
		"page_stop": "Berhenti",
		"page_idle": "Tekan Dengarkan untuk mendengar suara PC.",
		"page_connecting": "Menghubungkan...",
		"page_playing": "Memutar suara PC.",
		"page_lost": "Koneksi terputus, menyambung ulang...",
		"page_stopped": "Berhenti.",
	},
}


def _pick_language():
	try:
		lang = (locale.getdefaultlocale()[0] or "").lower()
	except ValueError:
		lang = ""
	return "id" if lang.startswith("id") or lang.startswith("in_") else "en"


LANG = _pick_language()
T = STRINGS[LANG]

PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<style>
body {{ font-family: sans-serif; margin: 0; padding: 24px 16px; background: #111; color: #eee; text-align: center; }}
button {{ font-size: 1.6em; padding: 0.8em 1.6em; width: 100%; max-width: 420px; border-radius: 12px; border: 0; background: #2d7ef7; color: #fff; }}
p {{ font-size: 1.2em; }}
</style>
</head>
<body>
<h1>{page_title}</h1>
<button id="toggle">{page_listen}</button>
<p id="status" role="status" aria-live="polite">{page_idle}</p>
<script>
const SOURCE_RATE = {rate}, CH = {channels};
const T = {{listen: "{page_listen}", stop: "{page_stop}", connecting: "{page_connecting}",
	playing: "{page_playing}", lost: "{page_lost}", stopped: "{page_stopped}"}};
const btn = document.getElementById("toggle"), statusEl = document.getElementById("status");
let ctx = null, node = null, ws = null, running = false, retry = null;
// Ring buffer of interleaved samples at the source rate.
const RING = SOURCE_RATE * CH * 2;
const ring = new Float32Array(RING);
let w = 0, r = 0, avail = 0, pos = 0;
const TARGET = SOURCE_RATE * 0.08, MAX = SOURCE_RATE * 0.2;  // frames

function setStatus(text) {{ if (statusEl.textContent !== text) statusEl.textContent = text; }}

function push(buf) {{
	const s = new Int16Array(buf);
	for (let i = 0; i < s.length; i++) {{ ring[w] = s[i] / 32768; w = (w + 1) % RING; }}
	avail += s.length / CH;
	if (avail > MAX) {{  // too far behind: skip ahead to keep latency low
		const drop = Math.floor(avail - TARGET);
		r = (r + drop * CH) % RING; avail -= drop;
	}}
}}

function process(e) {{
	const outL = e.outputBuffer.getChannelData(0), outR = e.outputBuffer.getChannelData(1);
	const step = SOURCE_RATE / ctx.sampleRate;
	for (let i = 0; i < outL.length; i++) {{
		if (avail < 2) {{ outL[i] = outR[i] = 0; continue; }}
		const a = r, b = (r + CH) % RING;
		outL[i] = ring[a] + (ring[b] - ring[a]) * pos;
		outR[i] = ring[a + 1] + (ring[b + 1] - ring[a + 1]) * pos;
		pos += step;
		while (pos >= 1) {{ pos -= 1; r = (r + CH) % RING; avail--; }}
	}}
}}

function connect() {{
	setStatus(T.connecting);
	ws = new WebSocket("ws://" + location.host + "/ws");
	ws.binaryType = "arraybuffer";
	ws.onopen = () => setStatus(T.playing);
	ws.onmessage = (m) => push(m.data);
	ws.onclose = () => {{
		if (!running) return;
		setStatus(T.lost);
		retry = setTimeout(connect, 1000);
	}};
}}

function start() {{
	ctx = new (window.AudioContext || window.webkitAudioContext)({{sampleRate: SOURCE_RATE, latencyHint: "interactive"}});
	node = ctx.createScriptProcessor(1024, 0, 2);
	node.onaudioprocess = process;
	node.connect(ctx.destination);
	w = r = avail = pos = 0;
	running = true;
	btn.textContent = T.stop;
	connect();
}}

function stop() {{
	running = false;
	clearTimeout(retry);
	if (ws) ws.close();
	if (ctx) ctx.close();
	ws = ctx = node = null;
	btn.textContent = T.listen;
	setStatus(T.stopped);
}}

btn.onclick = () => running ? stop() : start();
</script>
</body>
</html>
"""


def lan_address():
	# No packet is sent; this only asks the OS which interface routes outward.
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	try:
		s.connect(("10.255.255.255", 1))
		return s.getsockname()[0]
	except OSError:
		return "127.0.0.1"
	finally:
		s.close()


class StreamServer:
	"""HTTP + WebSocket server on a background asyncio loop, fed by a capture thread."""

	def __init__(self, on_change):
		self.on_change = on_change  # called with (listener_count, error_or_None)
		self.loop = None
		self.thread = None
		self.clients = {}  # WebSocketResponse -> transport
		self.capturing = False
		self.page = PAGE.format(lang=LANG, rate=SAMPLE_RATE, channels=CHANNELS, **T).encode("utf-8")

	def start(self):
		started = threading.Event()
		self.error = None
		self.thread = threading.Thread(target=self._run, args=(started,), daemon=True)
		self.thread.start()
		started.wait()
		return self.error

	def stop(self):
		self.capturing = False
		if self.loop is not None:
			self.loop.call_soon_threadsafe(self._shutdown)
		if self.thread is not None:
			self.thread.join(timeout=3)
		self.loop = self.thread = None

	def _run(self, started):
		self.loop = asyncio.new_event_loop()
		asyncio.set_event_loop(self.loop)
		app = web.Application()
		app.router.add_get("/", self._index)
		app.router.add_get("/ws", self._ws)
		self.runner = web.AppRunner(app)
		self.loop.run_until_complete(self.runner.setup())
		try:
			self.loop.run_until_complete(web.TCPSite(self.runner, "0.0.0.0", PORT).start())
		except OSError:
			self.error = T["port_busy"].format(port=PORT)
			self.loop.run_until_complete(self.runner.cleanup())
			self.loop.close()
			self.loop = None
			started.set()
			return
		self.capturing = True
		threading.Thread(target=self._capture, daemon=True).start()
		started.set()
		self.loop.run_forever()
		self.loop.run_until_complete(self.runner.cleanup())
		self.loop.close()

	def _shutdown(self):
		for ws in list(self.clients):
			asyncio.ensure_future(ws.close())
		self.loop.call_later(0.2, self.loop.stop)

	async def _index(self, request):
		return web.Response(body=self.page, content_type="text/html", charset="utf-8")

	async def _ws(self, request):
		ws = web.WebSocketResponse(max_msg_size=0)
		await ws.prepare(request)
		self.clients[ws] = request.transport
		self._notify()
		try:
			async for msg in ws:
				if msg.type == WSMsgType.ERROR:
					break
		finally:
			self.clients.pop(ws, None)
			self._notify()
		return ws

	def _notify(self, error=None):
		wx.CallAfter(self.on_change, len(self.clients), error)

	def _broadcast(self, data):
		for ws, transport in list(self.clients.items()):
			if ws.closed:
				continue
			# Skip a slow client rather than queueing up delay for everyone.
			if transport is not None and transport.get_write_buffer_size() > 64000:
				continue
			asyncio.ensure_future(ws.send_bytes(data))

	def _capture(self):
		warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
		try:
			speaker = sc.default_speaker()
			mic = sc.get_microphone(speaker.id, include_loopback=True)
			with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK) as rec:
				while self.capturing:
					frames = rec.record(numframes=BLOCK)
					if not self.clients:
						continue
					pcm = (np.clip(frames, -1, 1) * 32767).astype("<i2").tobytes()
					loop = self.loop
					if loop is not None:
						loop.call_soon_threadsafe(self._broadcast, pcm)
		except Exception as e:
			if self.capturing:
				self._notify(T["capture_failed"].format(error=e))


class MainFrame(wx.Frame):
	def __init__(self):
		super().__init__(None, title=APP_NAME, size=(480, 260))
		self.server = None
		self.url = f"http://{lan_address()}:{PORT}"

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(panel, label=T["address"]), 0, wx.ALL, 6)
		self.address = wx.TextCtrl(panel, value=self.url, style=wx.TE_READONLY)
		sizer.Add(self.address, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		self.toggle_btn = wx.Button(panel, label=T["start"])
		self.copy_btn = wx.Button(panel, label=T["copy"])
		buttons.Add(self.toggle_btn, 0, wx.RIGHT, 6)
		buttons.Add(self.copy_btn, 0)
		sizer.Add(buttons, 0, wx.ALL, 6)

		sizer.Add(wx.StaticText(panel, label=T["status"]), 0, wx.LEFT | wx.RIGHT, 6)
		self.status = wx.TextCtrl(panel, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL, size=(-1, 50))
		sizer.Add(self.status, 0, wx.EXPAND | wx.ALL, 6)
		panel.SetSizer(sizer)

		self.toggle_btn.Bind(wx.EVT_BUTTON, self.on_toggle)
		self.copy_btn.Bind(wx.EVT_BUTTON, self.on_copy)
		self.Bind(wx.EVT_CLOSE, self.on_close)

		self.set_status(T["stopped"])
		self.toggle_btn.SetFocus()

	def set_status(self, text):
		self.status.SetValue(text)
		self.SetTitle(f"{APP_NAME} - {text}")

	def on_toggle(self, event):
		if self.server is None:
			server = StreamServer(self.on_server_change)
			error = server.start()
			if error:
				self.set_status(error)
				return
			self.server = server
			self.toggle_btn.SetLabel(T["stop"])
			self.set_status(T["running"].format(count=0))
		else:
			self.server.stop()
			self.server = None
			self.toggle_btn.SetLabel(T["start"])
			self.set_status(T["stopped"])

	def on_server_change(self, count, error):
		if self.server is None:
			return
		self.set_status(error or T["running"].format(count=count))

	def on_copy(self, event):
		if wx.TheClipboard.Open():
			wx.TheClipboard.SetData(wx.TextDataObject(self.url))
			wx.TheClipboard.Close()
			self.set_status(T["copied"])

	def on_close(self, event):
		if self.server is not None:
			self.server.stop()
		event.Skip()


def main():
	app = wx.App()
	MainFrame().Show()
	app.MainLoop()


if __name__ == "__main__":
	main()
