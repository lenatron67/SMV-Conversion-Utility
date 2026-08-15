"""
Detailed per-bit tracer for frame_00000.bin.

Adds the two known placeholder proprietary codes and walks through MB by MB,
printing every field decoded with its exact bit position and bit string.
When drift occurs (CBPY/MCBPC/INTRADC failure), show the raw bits at the
failure point so we can identify the next proprietary code.

Also tries both LAST assignments for the two known codes to see which gives
further decoding.
"""
import json
from pathlib import Path
from bitreader import BitReader
from tcoef_tables import (
    TCOEF_VLC, ESCAPE_CODE, MAX_TCOEF_VLC_LEN,
    FORBIDDEN_LEVEL_BYTES, decode_run_flc, decode_level_flc,
)

SAMPLES = Path("samples")
manifest = json.loads((SAMPLES / "manifest.json").read_text())

MCBPC_INTRA = {
    "1": (3, 0b00), "001": (3, 0b01), "010": (3, 0b10), "011": (3, 0b11),
    "0001": (4, 0b00), "000001": (4, 0b01), "000010": (4, 0b10),
    "000011": (4, 0b11), "000000001": ("stuffing", None),
}
CBPY_INTRA = {
    "0011": 0b0000, "00101": 0b0001, "00100": 0b0010, "1001": 0b0011,
    "00011": 0b0100, "0111": 0b0101, "000010": 0b0110, "1011": 0b0111,
    "00010": 0b1000, "000011": 0b1001, "0101": 0b1010, "1010": 0b1011,
    "0100": 0b1100, "1000": 0b1101, "0110": 0b1110, "11": 0b1111,
}
FORBIDDEN_INTRADC = {0x00, 0x80}
_ESCAPE = "ESCAPE"


def match_vlc(br, table, max_len):
    if br.bits_remaining() < 1:
        return None, None
    peek_len = min(max_len, br.bits_remaining())
    bits = format(br.peek(peek_len), f"0{peek_len}b")
    for length in range(1, peek_len + 1):
        if bits[:length] in table:
            br.skip(length)
            return table[bits[:length]], bits[:length]
    return None, None


def peek_bits(br, n=24):
    n = min(n, br.bits_remaining())
    if n <= 0:
        return ""
    return format(br.peek(n), f"0{n}b")


def trace_frame(data, start_bit, frame_byte_len, extra_codes, label, verbose=True):
    lookup = {**TCOEF_VLC, ESCAPE_CODE: _ESCAPE}
    for code, last, run, level in extra_codes:
        lookup[code] = (last, run, level)
    max_len = max(len(c) for c in lookup if c != ESCAPE_CODE)
    max_len = max(max_len, len(ESCAPE_CODE))

    br = BitReader(data, start_bit)
    QCIF_MB_COUNT = 11 * 9

    if verbose:
        print(f"\n{'='*70}")
        print(f"Trace: {label}")
        print(f"{'='*70}")

    decoded_mbs = 0
    for mb_index in range(QCIF_MB_COUNT):
        mb_start = br.pos

        # MCBPC
        pos0 = br.pos
        mcbpc_val, mcbpc_code = match_vlc(br, MCBPC_INTRA, 9)
        if mcbpc_val is None:
            if verbose:
                print(f"  MB {mb_index}: FAIL MCBPC at bit {pos0}, bits={peek_bits(BitReader(data, pos0))}")
            return decoded_mbs, "MCBPC fail", pos0
        mb_type, cbpc = mcbpc_val
        if mb_type == "stuffing":
            if verbose:
                print(f"  MB {mb_index}: stuffing")
            return decoded_mbs, "stuffing", pos0

        # CBPY
        pos1 = br.pos
        cbpy_val, cbpy_code = match_vlc(br, CBPY_INTRA, 6)
        if cbpy_val is None:
            if verbose:
                print(f"  MB {mb_index}: FAIL CBPY at bit {pos1}, bits={peek_bits(BitReader(data, pos1))}")
            return decoded_mbs, "CBPY fail", pos1

        # DQUANT
        if mb_type == 4:
            if br.bits_remaining() < 2:
                return decoded_mbs, "DQUANT eof", br.pos
            br.read(2)

        coded_flags = [
            (cbpy_val >> 3) & 1, (cbpy_val >> 2) & 1,
            (cbpy_val >> 1) & 1, (cbpy_val >> 0) & 1,
            (cbpc >> 1) & 1, (cbpc >> 0) & 1,
        ]
        block_names = ["Y1", "Y2", "Y3", "Y4", "Cb", "Cr"]
        block_ok = True

        if verbose and mb_index <= 6:
            print(f"\n  MB {mb_index} @bit {mb_start}: type={mb_type} "
                  f"MCBPC={mcbpc_code!r} CBPY={cbpy_code!r}={cbpy_val:04b} CBPC={cbpc:02b}")

        for coded, bname in zip(coded_flags, block_names):
            pos_idc = br.pos
            if br.bits_remaining() < 8:
                if verbose:
                    print(f"    {bname}: INTRADC eof at {pos_idc}")
                return decoded_mbs, f"INTRADC eof {bname}", pos_idc
            idc = br.read(8)
            if idc in FORBIDDEN_INTRADC:
                if verbose:
                    print(f"    {bname}: FAIL INTRADC forbidden {hex(idc)} at {pos_idc}, "
                          f"bits={peek_bits(BitReader(data, pos_idc))}")
                return decoded_mbs, f"INTRADC forbidden {bname}", pos_idc

            tcoef_events = []
            if coded:
                while len(tcoef_events) < 64:
                    pos_tc = br.pos
                    val, code = match_vlc(br, lookup, max_len)
                    if val is None:
                        raw = peek_bits(BitReader(data, pos_tc))
                        if verbose:
                            print(f"    {bname}: FAIL TCOEF at bit {pos_tc}: {raw}")
                        return decoded_mbs, f"TCOEF fail {bname} MB{mb_index}", pos_tc
                    if val is _ESCAPE:
                        if br.bits_remaining() < 15:
                            return decoded_mbs, "ESCAPE eof", br.pos
                        last = br.read(1); run = decode_run_flc(br.read(6))
                        lb = br.read(8)
                        if lb in FORBIDDEN_LEVEL_BYTES:
                            if verbose:
                                print(f"    {bname}: FAIL ESCAPE LEVEL {hex(lb)}")
                            return decoded_mbs, f"ESC LEVEL {bname}", br.pos
                        level = decode_level_flc(lb)
                        ev = {"last": last, "run": run, "level": level, "code": "ESCAPE"}
                    else:
                        last, run, level = val
                        sign = br.read(1)
                        if sign:
                            level = -level
                        ev = {"last": last, "run": run, "level": level, "code": code}
                    tcoef_events.append(ev)
                    if last:
                        break
                else:
                    return decoded_mbs, f">64 events {bname}", br.pos

            if verbose and mb_index <= 6:
                idc_marker = " [PLACEHOLDER]" if "" else ""
                tstr = ""
                for ev in tcoef_events:
                    flag = " [PROP]" if ev["code"] in [c for c, *_ in extra_codes] else ""
                    tstr += f"({ev['code']!r}:L={ev['last']} R={ev['run']} V={ev['level']}{flag}) "
                print(f"    {bname}: INTRADC={hex(idc)}  coded={bool(coded)} "
                      f"{'TCOEF: ' + tstr if coded else ''}")

        decoded_mbs += 1

    return QCIF_MB_COUNT, "SUCCESS", br.pos


# Load frame_00000
entry = next(e for e in manifest["frames"] if e["file"] == "frame_00000.bin")
data = (SAMPLES / entry["file"]).read_bytes()
start = entry["mb_data_bit_offset_within_frame"]
blen = entry["byte_length"]

print("=" * 70)
print("Testing LAST combinations for the two known proprietary codes")
print("on frame_00000.bin")
print("=" * 70)

combos = [
    ("LAST=0/LAST=1 (from brute-force)",
     [("000000000101", 0, 0, 1), ("000000000100", 1, 0, 1)]),
    ("LAST=1/LAST=0 (swapped)",
     [("000000000101", 1, 0, 1), ("000000000100", 0, 0, 1)]),
    ("LAST=0/LAST=0 (both)",
     [("000000000101", 0, 0, 1), ("000000000100", 0, 0, 1)]),
    ("LAST=1/LAST=1 (both)",
     [("000000000101", 1, 0, 1), ("000000000100", 1, 0, 1)]),
    ("No placeholders (baseline)",
     []),
]

for label, extra in combos:
    mbs, stop, bit = trace_frame(data, start, blen, extra, label, verbose=False)
    print(f"  {label}: {mbs:2d}/99 MBs  stop={stop} @bit {bit}")

# Full verbose trace for the best known combination
print()
trace_frame(data, start, blen,
            [("000000000101", 0, 0, 1), ("000000000100", 1, 0, 1)],
            "LAST=0/LAST=1 (best known) -- VERBOSE", verbose=True)

# Also show the raw bits at MB 3 failure point
print("\n\n--- Checking raw bits at MB 3 CBPY failure ---")
# Find where MB 3 starts by running the trace up to MB 2 completion
# We use the trace_frame which returns the bit pos at failure
_, stop, fail_bit = trace_frame(data, start, blen,
    [("000000000101", 0, 0, 1), ("000000000100", 1, 0, 1)],
    "", verbose=False)
print(f"Failure bit position: {fail_bit}")
print(f"Stop reason: {stop}")
n = 40
raw = format(BitReader(data, fail_bit).peek(min(n, (blen*8 - fail_bit))),
             f"0{min(n, blen*8 - fail_bit)}b")
print(f"Raw bits at failure: {raw}")
