# Bluetooth Audio Sink

Play audio from your phone on your Windows PC over Bluetooth (A2DP sink).
Uses the `AudioPlaybackConnection` API added in Windows 10 version 2004.

## Requirements

- Windows 10 version 2004 or later (or Windows 11)
- A Bluetooth adapter
- A phone (or other device) that supports A2DP, paired first in
  Settings > Devices > Bluetooth

## Download

Get the latest zip from the [Releases page](https://github.com/InfiArtt/bt-audio-sink/releases),
extract it anywhere and run `BluetoothAudioSink.exe` (or `PCToPhone.exe`).
No Python needed.

## Usage

1. Pair your phone with the PC in Windows Settings.
2. Start the app. Paired devices appear in the list.
3. Select your phone and press Connect (or Enter / double-click).
4. Play audio on the phone. It comes out of the PC's speakers.

The app remembers the last device and can reconnect to it on start.

Closing the window (Alt+F4, Escape or the close button) hides it to the
system tray and keeps the connection. Click the tray icon, or press Enter on
it, to bring the window back; starting the app again does the same. Use the
Exit button or the tray menu's Exit to quit and disconnect.

The interface uses standard controls and works with screen readers
(NVDA, Narrator, JAWS). It is in Indonesian when Windows uses Indonesian,
otherwise English.

## Equalizer

Press Equalizer... to shape the phone's sound: five bands (80 Hz, 250 Hz,
1 kHz, 4 kHz, 10 kHz, each -12 to +12 dB) and presets (Flat, Clearer,
Bass boost, Vocal). Changes apply while you listen.

Windows plays the Bluetooth stream itself with no way to add effects, so when
the equalizer is on the app records the "Line (<phone> A2DP SNK)" input,
filters it, plays it on the default output and mutes Windows' own playback of
that stream. The mute is undone when the equalizer is turned off, the phone
disconnects or the app closes. Delay stays about the same (around 50 ms on
the PC side in testing).

## PC to Phone (Wi-Fi)

Phones cannot receive Bluetooth audio (Android only allows the sending role),
so `pc_to_phone.py` sends what the PC is playing to the phone over Wi-Fi instead.
The phone only needs a browser.

1. Run `pc_to_phone.py` and press Start. Allow it through Windows Firewall
   (private networks) when asked.
2. On the phone, connected to the same Wi-Fi, open the address shown
   (for example `http://192.168.1.10:18765`).
3. Press Listen.

The page keeps about 80 ms of audio buffered and skips ahead when it falls
behind, so expect roughly 100-200 ms of delay in total.
Do not use it while the same phone is streaming to the PC over Bluetooth:
the sound would loop back and echo.

## Running from source

```
py -3.10 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\pythonw bt_audio_sink.py
```

## Building an .exe

```
.venv\Scripts\python -m pip install pyinstaller
.venv\Scripts\pyinstaller build.spec
```

Both programs end up in `dist\BluetoothAudioSink`, sharing one `_internal` folder.

## License

MIT. See [LICENSE](LICENSE).
