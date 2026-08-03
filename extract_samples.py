"""
Freeze a small set of canonical sample frames for the VLC reverse-engineering
effort, so future sessions don't need to re-scan the 62MB source file just to
get a test case.

Writes to samples/:
  - frame_NNNN.bin   raw bytes from this PSC up to (not including) the next PSC
  - manifest.json    metadata for every frozen sample (offsets, TR, header
                     fields, byte length, position in the overall sequence)

Selection: first 5 frames (where the VLC desync was already characterized in
the prior session -- see VLC_REVERSE_ENGINEERING.md ledger), plus a handful
spread across the middle and end of the file, so hypotheses can be checked
against frames the encoder produced under different conditions (motion,
scene changes, etc).
"""
import json
from pathlib import Path

from bitreader import find_psc_offsets, decode_picture_header

SMV = Path("Nana playing conputer.smv")
OUT = Path("samples")
OUT.mkdir(exist_ok=True)

data = SMV.read_bytes()

print("Scanning for PSCs across the full file (this takes a little while)...")
pscs = find_psc_offsets(data, start=454)
print(f"Found {len(pscs)} PSCs total")

# Indices into `pscs` to freeze: first 5 (known VLC-error frames from the
# prior session), then evenly spaced samples through the rest of the file.
n = len(pscs)
indices = sorted(set([0, 1, 2, 3, 4] + [n * k // 10 for k in range(1, 10)] + [n - 2]))
indices = [i for i in indices if 0 <= i < n - 1]  # need a "next PSC" to bound each frame

manifest = []
for idx in indices:
    start = pscs[idx]
    end = pscs[idx + 1]
    chunk = data[start:end]
    hdr = decode_picture_header(data, start)
    fname = f"frame_{idx:05d}.bin"
    (OUT / fname).write_bytes(chunk)
    entry = {
        "file": fname,
        "sequence_index": idx,
        "byte_offset": start,
        "byte_length": len(chunk),
        "tr": hdr["tr"],
        "picture_type": "I" if hdr["picture_type"] == 0 else "P",
        "header_end_bit": hdr["header_end_bit"],
        "header_end_byte_offset": hdr["header_end_bit"] // 8,
        "mb_data_bit_offset_within_frame": hdr["header_end_bit"] - start * 8,
    }
    manifest.append(entry)
    print(f"  [{idx:5d}] {fname}: offset={start} len={len(chunk)} "
          f"TR={hdr['tr']} type={entry['picture_type']} "
          f"header_ends_at_bit={hdr['header_end_bit']} "
          f"(byte {hdr['header_end_bit']//8}, "
          f"+{entry['mb_data_bit_offset_within_frame']} bits into frame)")

(OUT / "manifest.json").write_text(json.dumps({
    "source_file": str(SMV),
    "source_file_size": len(data),
    "video_stream_start_offset": 454,
    "total_psc_count": n,
    "note": ("byte_offset is absolute within the .smv file. "
             "mb_data_bit_offset_within_frame is where macroblock/GOB data "
             "starts, relative to the start of this frame's bytes -- this is "
             "where VLC-table hypotheses should start being tested against."),
    "frames": manifest,
}, indent=2))

print(f"\nWrote {len(manifest)} sample frames + manifest.json to {OUT}/")
