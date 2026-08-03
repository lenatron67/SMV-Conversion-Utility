import av
import struct
from pathlib import Path
from PIL import Image
import io
import os

data = Path("Nana playing conputer.smv").read_bytes()
# Write the raw H.263 stream starting at first PSC
h263_data = data[454:]
Path("raw_h263.bin").write_bytes(h263_data)

os.makedirs("test_frames", exist_ok=True)

print("Opening raw H.263 stream with PyAV...")
container = av.open("raw_h263.bin", format="h263")
print(f"Format: {container.format.name}")

stream = container.streams.video[0]
print(f"Codec: {stream.codec_context.name}")
print(f"Resolution: {stream.width}x{stream.height}")
print(f"Frame rate: {stream.average_rate}")

# Decode first 30 frames and save them
frame_count = 0
non_black = 0

for packet in container.demux(stream):
    for frame in packet.decode():
        if frame_count < 30:
            img = frame.to_image()
            img.save(f"test_frames/frame_{frame_count:04d}.png")
            # Check if frame is blank (all same color)
            arr = frame.to_ndarray(format='rgb24')
            mean = arr.mean()
            std = arr.std()
            if std > 5:
                non_black += 1
                print(f"Frame {frame_count}: mean={mean:.1f}, std={std:.1f} ← HAS CONTENT!")
            else:
                print(f"Frame {frame_count}: mean={mean:.1f}, std={std:.1f} (blank)")
        frame_count += 1
        if frame_count >= 50:
            break
    if frame_count >= 50:
        break

container.close()
print(f"\nTotal decoded: {frame_count} frames, {non_black} with actual content")
