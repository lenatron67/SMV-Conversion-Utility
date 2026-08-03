"""
Session 5 — scan SMV sample frames for GOB start codes (17-bit 0...01).

If the SmithMicro encoder emits GOB headers (like genuine I263 files can),
every hit is a TRUE resync point: GOB N starts a known MB row (N*11), giving
hard pinned positions that constrain the extension-table search for free.

A 17-bit all-zero-then-1 pattern can also occur inside entropy data by
chance, so report all hits with their bit position and the GN (5 bits) that
follows — real GOB headers have GN in 1..8 for QCIF and should appear in
increasing order once per frame at plausible positions (~1/9th spacings).
"""
import json
from pathlib import Path

from bitreader import BitReader, decode_picture_header


def find_gob_codes(data: bytes, start_bit: int):
    """All bit positions where a 17-bit GOB start code begins."""
    total = len(data) * 8
    hits = []
    # build a bit array once for speed
    bits = []
    for byte in data:
        for k in range(7, -1, -1):
            bits.append((byte >> k) & 1)
    run = 0  # count of consecutive zeros ending at i-1
    for i in range(start_bit, total):
        if bits[i] == 1:
            if run >= 16:
                pos = i - 16
                br = BitReader(data, i + 1)
                gn = br.read(5) if total - i - 1 >= 5 else -1
                gfid = br.read(2) if total - br.pos >= 2 else -1
                gq = br.read(5) if total - br.pos >= 5 else -1
                hits.append((pos, gn, gfid, gq))
            run = 0
        else:
            run += 1
    return hits


def main():
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())
    for entry in manifest["frames"]:
        data = (samples / entry["file"]).read_bytes()
        hdr = decode_picture_header(data, 0)
        start_bit = hdr["header_end_bit"]
        total = len(data) * 8
        hits = find_gob_codes(data, start_bit)
        plausible = [h for h in hits if 1 <= h[1] <= 8]
        print(f"{entry['file']}: bits {start_bit}..{total}, "
              f"{len(hits)} raw 17-bit hits, {len(plausible)} with GN in 1..8")
        for pos, gn, gfid, gq in hits:
            frac = pos / total
            mark = " <- GN plausible" if 1 <= gn <= 8 else ""
            print(f"    bit {pos:6d} ({frac:5.1%})  GN={gn:2d} GFID={gfid} "
                  f"GQUANT={gq}{mark}")


if __name__ == "__main__":
    main()
