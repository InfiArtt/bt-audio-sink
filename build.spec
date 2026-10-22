# PyInstaller spec: builds both programs into one folder that shares its libraries.
# Usage: .venv\Scripts\pyinstaller build.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# pywinrt is a namespace package of compiled modules PyInstaller cannot see.
winrt_modules = collect_submodules("winrt")
# soundcard loads its C declarations from .h files next to the modules.
soundcard_data = collect_data_files("soundcard")


def analysis(script, hiddenimports=()):
	return Analysis(
		[script],
		hiddenimports=list(hiddenimports),
		datas=soundcard_data,
		excludes=["tkinter", "matplotlib", "IPython"],
	)


def program(a, name):
	return EXE(
		PYZ(a.pure),
		a.scripts,
		exclude_binaries=True,
		name=name,
		console=False,
		upx=False,
	)


sink = analysis("bt_audio_sink.py", winrt_modules)
streamer = analysis("pc_to_phone.py")

COLLECT(
	program(sink, "BluetoothAudioSink"),
	sink.binaries,
	sink.datas,
	program(streamer, "PCToPhone"),
	streamer.binaries,
	streamer.datas,
	name="BluetoothAudioSink",
	upx=False,
)
