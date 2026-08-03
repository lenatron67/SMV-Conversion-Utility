"""
Bootstrap proprietary TCOEF table reconstruction.

Strategy: the proprietary table is TABLE 13 + extension codes in the
`000000000` prefix space (9+ leading zeros).  We know from tcoef_probe.py
that two specific 12-bit codes appear at the first explicit VLC-no-match
failures:

    000000000101  (LAST=0, RUN=?, LEVEL=?)   -- frame_00000 MB 2 block Y4
    000000000100  (LAST=1, RUN=?, LEVEL=?)   -- frame_09365 MB 4 block Y1

The RUN/LEVEL values are unknowns, but they do NOT affect subsequent bit
parsing — only the code LENGTH matters for keeping the parse on track.
So we can add these as placeholder entries and re-run the walker to find
the NEXT failure point, which will reveal the next proprietary code.

This script:
1. Starts with TABLE 13 + placeholder entries for the two known codes.
2. Runs the walker across all 15 frames, collecting EVERY new failure.
3. Inspects the bits at each new failure to find additional `000000000`-
   prefix codes, adds them as placeholders, and iterates.
4. After convergence (or a set number of rounds), reports the full
   candidate proprietary code list and how many MBs decoded per frame.

Placeholder RUN=0, LEVEL=1 for LAST=0 entries; RUN=0, LEVEL=1 for LAST=1.
These are wrong semantically but right structurally -- they keep the
walker's bit pointer at the correct position.
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
QCIF_MB_COUNT = 11 * 9

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
_ESCAPE_SENTINEL = "ESCAPE"


def build_tcoef_lookup(extra_codes):
    """Build the TCOEF lookup dict: TABLE 13 + extra placeholder codes.
    extra_codes: list of (code_str, last, run, level)
    """
    lookup = {**TCOEF_VLC, ESCAPE_CODE: _ESCAPE_SENTINEL}
    for code, last, run, level in extra_codes:
        if code in lookup:
            print(f"  WARNING: {code!r} already in table -- skipping")
        else:
            lookup[code] = (last, run, level)
    return lookup


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


def read_tcoeff_block(br, lookup, max_len):
    events = []
    while len(events) < 64:
        val, code = match_vlc(br, lookup, max_len)
        if val is None:
            # Return the raw bits at failure for analysis
            peek = min(20, br.bits_remaining())
            fail_bits = format(br.peek(peek), f"0{peek}b") if peek > 0 else ""
            return None, f"no VLC match", fail_bits
        if val is _ESCAPE_SENTINEL:
            if br.bits_remaining() < 15:
                return None, "ESCAPE truncated", ""
            last = br.read(1)
            run = decode_run_flc(br.read(6))
            lb = br.read(8)
            if lb in FORBIDDEN_LEVEL_BYTES:
                return None, f"ESCAPE forbidden LEVEL {hex(lb)}", ""
            level = decode_level_flc(lb)
            events.append({"last": last, "run": run, "level": level})
        else:
            last, run, level = val
            if br.bits_remaining() < 1:
                return None, "no sign bit", ""
            sign = br.read(1)
            if sign:
                level = -level
            events.append({"last": last, "run": run, "level": level})
        if last:
            break
    else:
        return None, ">64 events", ""
    return events, None, ""


def walk_frame(data, start_bit, frame_byte_len, lookup, max_len):
    """Returns (mb_count, stop_reason, fail_bits_at_first_tcoef_nomatch)."""
    br = BitReader(data, start_bit)
    for mb_index in range(QCIF_MB_COUNT):
        mcbpc_val, _ = match_vlc(br, MCBPC_INTRA, 9)
        if mcbpc_val is None:
            return mb_index, "MCBPC no match", ""
        mb_type, cbpc = mcbpc_val
        if mb_type == "stuffing":
            return mb_index, "stuffing", ""
        cbpy_val, _ = match_vlc(br, CBPY_INTRA, 6)
        if cbpy_val is None:
            return mb_index, "CBPY no match", ""
        if mb_type == 4:
            if br.bits_remaining() < 2:
                return mb_index, "DQUANT eof", ""
            br.read(2)
        coded_flags = [
            (cbpy_val >> 3) & 1, (cbpy_val >> 2) & 1,
            (cbpy_val >> 1) & 1, (cbpy_val >> 0) & 1,
            (cbpc >> 1) & 1, (cbpc >> 0) & 1,
        ]
        block_names = ["Y1", "Y2", "Y3", "Y4", "Cb", "Cr"]
        for coded, bname in zip(coded_flags, block_names):
            if br.bits_remaining() < 8:
                return mb_index, f"INTRADC eof ({bname})", ""
            idc = br.read(8)
            if idc in FORBIDDEN_INTRADC:
                return mb_index, f"INTRADC forbidden {hex(idc)} ({bname})", ""
            if coded:
                evs, err, fail_bits = read_tcoeff_block(br, lookup, max_len)
                if err:
                    return mb_index, f"TCOEF {bname} MB{mb_index}: {err}", fail_bits
    return QCIF_MB_COUNT, "SUCCESS", ""


def run_round(extra_codes, round_num):
    lookup = build_tcoef_lookup(extra_codes)
    max_len = max(len(c) for c in lookup if c != ESCAPE_CODE) if lookup else MAX_TCOEF_VLC_LEN
    max_len = max(max_len, len(ESCAPE_CODE))

    print(f"\n{'='*70}")
    print(f"Round {round_num}: TABLE 13 + {len(extra_codes)} proprietary placeholder(s)")
    for code, last, run, level in sorted(extra_codes):
        print(f"  {code!r} -> (LAST={last}, RUN={run}, LEVEL={level})")
    print()

    total_mbs = 0
    new_failures = {}  # fail_bits -> list of (frame, mb, reason)

    for entry in manifest["frames"]:
        data = (SAMPLES / entry["file"]).read_bytes()
        mb_count, stop, fail_bits = walk_frame(
            data, entry["mb_data_bit_offset_within_frame"],
            entry["byte_length"], lookup, max_len
        )
        total_mbs += mb_count
        tag = "OK" if mb_count == QCIF_MB_COUNT else f"{mb_count:2d}/{QCIF_MB_COUNT}"
        print(f"  {entry['file']}: {tag}  {stop[:60]}")

        if fail_bits and "no VLC match" in stop:
            key = fail_bits[:12]  # first 12 bits as candidate code
            if key not in new_failures:
                new_failures[key] = []
            new_failures[key].append((entry["file"], mb_count, stop, fail_bits))

    print(f"\n  Total MBs decoded: {total_mbs} / {len(manifest['frames']) * QCIF_MB_COUNT}")

    # Analyse failure bit patterns
    print(f"\n  New VLC-no-match failures ({len(new_failures)} unique 12-bit prefixes):")
    candidate_codes = []
    for key, occurrences in sorted(new_failures.items(),
                                   key=lambda x: -len(x[1])):
        # Only suggest codes in the 000000000x space
        in_prop_space = key.startswith("000000000")
        note = " [PROPRIETARY SPACE]" if in_prop_space else " [unexpected prefix]"
        print(f"    {key!r} appears in {len(occurrences)} frame(s){note}")
        for fname, mb, reason, bits in occurrences[:3]:
            print(f"      {fname} MB{mb}: {bits}")
        if in_prop_space and key not in [c for c, *_ in extra_codes]:
            # Guess LAST from the 12th bit following convention
            # (uncertain -- log as a placeholder with LAST=0 by default)
            candidate_codes.append((key, 0, 0, 1))

    return candidate_codes, total_mbs


# ── Bootstrap iteration ───────────────────────────────────────────────────────
# Seed: two known proprietary codes from tcoef_probe.py analysis.
# LAST values are from tcoef_codelength_brute.py scoring.
# RUN=0, LEVEL=1 are structural placeholders -- wrong semantically but
# correct in bit-length, which is all that matters for continuing the parse.
known_codes = [
    ("000000000101", 0, 0, 1),   # LAST=0 -- frame_00000 MB2 Y4
    ("000000000100", 1, 0, 1),   # LAST=1 -- frame_09365 MB4 Y1
]

MAX_ROUNDS = 6
extra = list(known_codes)

for rnd in range(1, MAX_ROUNDS + 1):
    new_candidates, total = run_round(extra, rnd)

    if total == len(manifest["frames"]) * QCIF_MB_COUNT:
        print("\n  ALL FRAMES FULLY DECODED -- table is complete!")
        break

    if not new_candidates:
        print("\n  No new proprietary codes found at VLC-no-match failures.")
        print("  Remaining failures are drift (INTRADC/MCBPC/CBPY mismatches),")
        print("  meaning the current code set is insufficient to stop further desync.")
        print("  Manual inspection of the first new drift failure may be needed.")
        break

    # Add new candidates and continue
    for cand in new_candidates:
        if cand[0] not in [c for c, *_ in extra]:
            extra.append(cand)
            print(f"  + Adding placeholder: {cand[0]!r} -> LAST={cand[1]}")

print("\n\n=== Final proprietary code list ===")
for code, last, run, level in sorted(extra):
    print(f"  {code!r:20}  LAST={last}  (RUN/LEVEL are placeholders)")
print(f"\nTotal proprietary codes found: {len(extra)}")
print("Note: RUN and LEVEL values are placeholder 0/1 -- not yet determined.")
print("Next step: determine correct RUN/LEVEL via statistical/context analysis.")
