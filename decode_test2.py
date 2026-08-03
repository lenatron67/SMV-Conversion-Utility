import av
from pathlib import Path
from PIL import Image
import os, struct

data = Path("Nana playing conputer.smv").read_bytes()
os.makedirs("test_frames", exist_ok=True)

print("Opening raw H.263 stream...")
container = av.open("raw_h263.bin", format="h263")
stream = container.streams.video[0]
print(f"Codec: {stream.codec_context.name}, Resolution: {stream.width}x{stream.height}")

frame_count = 0
non_black = 0

for packet in container.demux(stream):
    for frame in packet.decode():
        img = frame.to_image()  # PIL Image, no numpy needed
        if frame_count < 30:
            img.save(f"test_frames/frame_{frame_count:04d}.png")

        # Check content: look at individual pixels using PIL
        pixels = list(img.getdata())
        r_vals = [p[0] for p in pixels[:100]]
        g_vals = [p[1] for p in pixels[:100]]
        b_vals = [p[2] for p in pixels[:100]]

        r_range = max(r_vals) - min(r_vals)
        g_range = max(g_vals) - min(g_vals)
        b_range = max(b_vals) - min(b_vals)
        avg_range = (r_range + g_range + b_range) / 3

        has_content = avg_range > 10
        if has_content:
            non_black += 1
            if non_black <= 5:
                print(f"Frame {frame_count}: pixel range R={r_range} G={g_range} B={b_range} ← HAS CONTENT!")
        elif frame_count < 5:
            print(f"Frame {frame_count}: pixel range R={r_range} G={g_range} B={b_range} (uniform)")

        frame_count += 1
        if frame_count >= 100:
            break
    if frame_count >= 100:
        break

container.close()
print(f"\nFrames decoded: {frame_count}, with content: {non_black}")
