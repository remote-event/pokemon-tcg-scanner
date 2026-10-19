"""Generate the app's original 180 ms confirmation chime (standard library only)."""
import math
import struct
import wave
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "app/src/main/res/raw/scan_ding.wav"
path.parent.mkdir(parents=True, exist_ok=True)
rate = 22050
samples = []
for index in range(int(rate * 0.18)):
    t = index / rate
    envelope = min(1.0, t / 0.004) * math.exp(-t * 24) * min(1.0, (0.18 - t) / 0.015)
    note = math.sin(2 * math.pi * 1318.51 * t) + 0.3 * math.sin(2 * math.pi * 2637.02 * t)
    samples.append(struct.pack("<h", int(18000 * envelope * note / 1.3)))
with wave.open(str(path), "wb") as output:
    output.setnchannels(1)
    output.setsampwidth(2)
    output.setframerate(rate)
    output.writeframes(b"".join(samples))
print(f"Created {path.name}: 180 ms, {path.stat().st_size} bytes")
