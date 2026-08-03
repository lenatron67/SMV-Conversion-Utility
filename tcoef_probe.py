"""
Forensic probe: dump raw bits at TCOEFF VLC failure points.

mcbpc_boundary_walker.py (ledger #9) confirmed TABLE 13/H.263 is wrong for
this stream. Two frames show direct "TCOEFF VLC no-match" failures (meaning
the bits there don't correspond to ANY standard TABLE 13 code):
  frame_00000: MB 2, block Y4
  frame_09365: MB 4, block Y1

At each failure point the bits in the stream ARE the actual proprietary TCOEF
code -- we just don't know which (LAST, RUN, LEVEL) symbol it encodes.

This script replays the walker to the exact failure bit for every frame that
has a TCOEFF no-match, then:
  1. Dumps the raw bits at the failure point (64 bits wide).
  2. Shows which TABLE 13 entries share the longest common prefix with those
     bits -- identifies how "close" the proprietary code is to a standard one.
  3. Checks whether non-TCOEF VLC codes (MCBPC, CBPY) appear as prefixes --
     would suggest structural-level divergence rather than just code remapping.
  4. Scans the failure-point bits for ALL possible prefix lengths 1..13 to
     map out which TABLE 13 codes are compatible/incompatible at each length.
  5. Across all failure points, looks for a shared prefix pattern -- if
     multiple frames hit the same unrecognised code, that fingerprints one
     specific proprietary code with high confidence.
"""
import json
from pathlib import Path
from collections import Counter

from bitreader import BitReader, read_bits
from tcoef_tables import TCOEF_VLC, ESCAPE_CODE, MAX_TCOEF_VLC_LEN, FORBIDDEN_LEVEL_BYTES
from mcbpc_boundary_walker import (
    MCBPC_INTRA, CBPY_INTRA, FORBIDDEN_INTRADC,
    QCIF_MB_COUNT, match_vlc,
)

SAMPLES = Path("samples")
manifest = json.loads((SAMPLES / "manifest.json").read_text())

_ESCAPE_SENTINEL = "ESCAPE"
_TCOEF_LOOKUP = {**TCOEF_VLC, ESCAPE_CODE: _ESCAPE_SENTINEL}
_TCOEF_MAX_LEN = MAX_TCOEF_VLC_LEN

DUMP_BITS = 80  # how many bits to show at each failure point


# ---------------------------------------------------------------------------
# Instrumented TCOEFF reader: returns (events, failure_bit) where failure_bit
# is set only when a VLC no-match occurs (None otherwise).
# ---------------------------------------------------------------------------
def read_tcoeff_instrumented(br: BitReader):
    """Like read_tcoeff_block but returns failure bit position on VLC no-match."""
    events = []
    while True:
        if len(events) >= 64:
            return None, None, "overflow (no LAST=1 in 64 events)"
        event_start = br.pos
        val, code = match_vlc(br, _TCOEF_LOOKUP, _TCOEF_MAX_LEN)
        if val is None:
            return None, event_start, "TCOEFF VLC no-match"
        if val is _ESCAPE_SENTINEL:
            if br.bits_remaining() < 15:
                return None, None, "ran out of bits in ESCAPE"
            last = br.read(1)
            run_bits = br.read(6)
            level_byte = br.read(8)
            if level_byte in FORBIDDEN_LEVEL_BYTES:
                return None, None, f"ESCAPE LEVEL forbidden: {hex(level_byte)}"
            events.append({"type": "escape", "last": last, "run": br.pos,
                           "level": (level_byte if level_byte < 0x80 else level_byte - 0x100)})
        else:
            last, run, level = val
            if br.bits_remaining() < 1:
                return None, None, "no sign bit"
            sign = br.read(1)
            events.append({"type": "vlc", "last": last, "run": run,
                           "level": -level if sign else level, "code": code})
        if last:
            break
    return events, None, None


def replay_to_failures(data: bytes, start_bit: int, frame_byte_len: int):
    """Walk the frame exactly as the main walker does, but return a list of
    TCOEFF VLC no-match failure records (one per failure; typically one per frame
    since we stop at the first desync)."""
    br = BitReader(data, start_bit)
    failures = []

    for mb_index in range(QCIF_MB_COUNT):
        mb_start = br.pos
        mcbpc_val, mcbpc_code = match_vlc(br, MCBPC_INTRA, 9)
        if mcbpc_val is None:
            break
        mb_type, cbpc = mcbpc_val
        if mb_type == "stuffing":
            break

        cbpy_val, cbpy_code = match_vlc(br, CBPY_INTRA, 6)
        if cbpy_val is None:
            break

        if mb_type == 4:
            if br.bits_remaining() < 2:
                break
            br.read(2)

        coded_flags = [
            (cbpy_val >> 3) & 1, (cbpy_val >> 2) & 1,
            (cbpy_val >> 1) & 1, (cbpy_val >> 0) & 1,
            (cbpc    >> 1) & 1,  (cbpc    >> 0) & 1,
        ]
        block_names = ["Y1", "Y2", "Y3", "Y4", "Cb", "Cr"]

        ok = True
        for block_idx, (coded, bname) in enumerate(zip(coded_flags, block_names)):
            if br.bits_remaining() < 8:
                ok = False
                break
            intradc = br.read(8)
            if intradc in FORBIDDEN_INTRADC:
                ok = False
                break
            if coded:
                events, fail_bit, err = read_tcoeff_instrumented(br)
                if err:
                    if "VLC no-match" in err and fail_bit is not None:
                        failures.append({
                            "mb_index": mb_index, "block": bname,
                            "fail_bit": fail_bit,
                            "bits_remaining_at_fail": len(data) * 8 - fail_bit,
                            "err": err,
                        })
                    ok = False
                    break
        if not ok:
            break

    return failures


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------
def get_bits_str(data: bytes, bit_pos: int, n: int) -> str:
    """Return n bits starting at bit_pos as a '0'/'1' string."""
    available = min(n, len(data) * 8 - bit_pos)
    raw = read_bits(data, bit_pos, available)
    return format(raw, f"0{available}b")


def longest_prefix_matches(bits_str: str, table: dict, label: str):
    """For each entry in table, measure how many leading bits of bits_str it
    shares. Return sorted list of (match_len, code, value)."""
    matches = []
    for code, val in table.items():
        shared = 0
        for i, (a, b) in enumerate(zip(bits_str, code)):
            if a == b:
                shared += 1
            else:
                break
        matches.append((shared, len(code), code, val))
    matches.sort(reverse=True)
    return matches


def find_exact_prefix_matches(bits_str: str, tables: dict):
    """Check whether any VLC code from any table is an exact prefix of bits_str
    (i.e. the bits could be decoded as that code starting from this position).
    Returns list of (table_name, code, value)."""
    found = []
    for tname, table in tables.items():
        for code, val in table.items():
            if bits_str.startswith(code):
                found.append((tname, code, val))
    found.sort(key=lambda x: len(x[1]))
    return found


# ---------------------------------------------------------------------------
# Main: replay all 15 frames, collect TCOEFF no-match failures, analyse
# ---------------------------------------------------------------------------
all_failures = []  # (fname, entry, failure_record, data, bits_str)

print("=== Replay: collecting TCOEFF VLC no-match failure points ===\n")
for entry in manifest["frames"]:
    path = SAMPLES / entry["file"]
    data = path.read_bytes()
    start_bit = entry["mb_data_bit_offset_within_frame"]
    failures = replay_to_failures(data, start_bit, entry["byte_length"])
    for f in failures:
        bits_str = get_bits_str(data, f["fail_bit"], DUMP_BITS)
        all_failures.append((entry["file"], entry, f, data, bits_str))
        print(f"{entry['file']:20s}  MB {f['mb_index']:2d} block {f['block']:2s} "
              f"@ bit {f['fail_bit']:6d}  ({f['bits_remaining_at_fail']} bits remaining)")
        print(f"  Bits at failure: {bits_str[:32]} {bits_str[32:64]} {bits_str[64:]}")

if not all_failures:
    print("No direct TCOEFF VLC no-match failures found -- all failures are drift artifacts.")
    print("Consider adding more validation (RUN-overflow checks) to surface earlier failures.")
    raise SystemExit(0)

print(f"\n{len(all_failures)} direct TCOEFF VLC no-match failure(s) found.\n")

# ---------------------------------------------------------------------------
# Deep dive: for each failure point, show prefix-match analysis
# ---------------------------------------------------------------------------
all_tables_for_prefix = {
    "TCOEF_VLC": TCOEF_VLC,
    "ESCAPE":    {ESCAPE_CODE: "escape"},
    "MCBPC":     MCBPC_INTRA,
    "CBPY":      CBPY_INTRA,
}

for fname, entry, f, data, bits_str in all_failures:
    print(f"{'='*72}")
    print(f"FAILURE POINT: {fname}  MB {f['mb_index']} block {f['block']}")
    print(f"  Absolute bit in sample file: {f['fail_bit']}")
    print(f"  Bits remaining in frame:     {f['bits_remaining_at_fail']}")
    print(f"  Next {DUMP_BITS} bits:  {bits_str}")
    print(f"  As hex nibbles:  ", end="")
    for i in range(0, len(bits_str), 4):
        print(f"{int(bits_str[i:i+4], 2):X}", end="")
    print()

    # --- What exact VLC codes (any table) are a prefix of these bits? ---
    exact = find_exact_prefix_matches(bits_str, all_tables_for_prefix)
    print(f"\n  Exact prefix matches (any table whose code is a prefix of the failure bits):")
    if exact:
        for tname, code, val in exact:
            print(f"    [{tname:10s}] code={code!r:15s}  -> {val}")
    else:
        print("    (none -- these bits don't start with ANY known VLC code)")

    # --- Top-N longest-shared-prefix with TABLE 13 entries ---
    tcoef_prefix_matches = longest_prefix_matches(bits_str, TCOEF_VLC, "TCOEF_VLC")
    print(f"\n  TABLE 13 entries by longest shared prefix (top 10):")
    for shared, codelen, code, val in tcoef_prefix_matches[:10]:
        last, run, level = val
        # Mark whether this entry's code is actually a PREFIX of the failure bits
        is_prefix = bits_str.startswith(code)
        prefix_flag = " ← EXACT PREFIX MATCH" if is_prefix else ""
        print(f"    shared={shared:2d}/{codelen:2d}  code={code!r:15s}  "
              f"LAST={last} RUN={run:2d} LEVEL={level:3d}{prefix_flag}")

    # --- Scan forward: for each length 1..15, what's the prefix, and is it in any table? ---
    print(f"\n  Prefix scan (is bits[:N] a valid code in any table?):")
    found_any = False
    for n in range(1, min(16, len(bits_str) + 1)):
        prefix = bits_str[:n]
        hits = []
        for tname, table in all_tables_for_prefix.items():
            if prefix in table:
                hits.append(f"{tname}:{table[prefix]!r}")
        if hits:
            print(f"    len={n:2d}: {prefix}  ->  {', '.join(hits)}")
            found_any = True
    if not found_any:
        print("    (no prefix of length 1-15 matches any known code)")

    print()

# ---------------------------------------------------------------------------
# Cross-failure pattern: do multiple failure points share a common short prefix?
# If so, they're likely all hitting the SAME proprietary code.
# ---------------------------------------------------------------------------
if len(all_failures) >= 2:
    print(f"{'='*72}")
    print(f"CROSS-FAILURE PREFIX PATTERNS\n")
    bit_strings = [(fname, bits_str) for fname, _, _, _, bits_str in all_failures]

    # For each prefix length, count how many failure points share the same prefix
    print(f"  Shared prefixes across all {len(all_failures)} failure points:")
    print(f"  {'Len':>4}  {'Prefix':>16}  {'Count':>5}  {'Frames'}")
    found_shared = False
    for n in range(1, 20):
        prefix_counter: Counter = Counter()
        frame_by_prefix: dict[str, list] = {}
        for fname, bits_str in bit_strings:
            if len(bits_str) >= n:
                p = bits_str[:n]
                prefix_counter[p] += 1
                frame_by_prefix.setdefault(p, []).append(fname)
        for prefix, count in prefix_counter.most_common():
            if count >= 2:
                frames_str = ", ".join(frame_by_prefix[prefix])
                print(f"  {n:>4}  {prefix:>16}  {count:>5}  {frames_str}")
                found_shared = True
                break  # only show the most common per length to keep output clean
    if not found_shared:
        print("  (no prefix shared by 2+ failure points -- each is hitting a different code)")

    print()

# ---------------------------------------------------------------------------
# Summary: what can we infer?
# ---------------------------------------------------------------------------
print(f"{'='*72}")
print("INTERPRETATION")
print()
print("The bits at each TCOEFF VLC no-match point are the proprietary encoder's")
print("actual TCOEF code for some (LAST, RUN, LEVEL) event. Key questions:")
print()
print("1. If a prefix scan hit shows the failure bits START with a known TCOEF code:")
print("   -> The proprietary encoder may have reordered symbols to different codes,")
print("      but the standard code happens to be a prefix here by coincidence.")
print("   -> Unlikely to be meaningful unless corroborated across many failure points.")
print()
print("2. If a MCBPC or CBPY code appears as a prefix:")
print("   -> Would suggest the MB/CBPY layer is structured differently than assumed,")
print("      and the TCOEFF failure is actually a higher-level desync. Investigate")
print("      whether the INTRADC assumption (always 8 bits, always present) might")
print("      be wrong for this encoder.")
print()
print("3. If no prefix matches anything (most likely based on ledger evidence):")
print("   -> The proprietary table uses different code lengths/patterns entirely.")
print("      The most actionable next step is to look at multiple frames' TCOEFF")
print("      events that DID decode (MBs 0-1 of all frames, and MBs 0-11 of frame_00001)")
print("      and check whether the decoded (LAST, RUN, LEVEL) events are physically")
print("      plausible DCT coefficients -- if they are, TABLE 13 may be correct for")
print("      those specific events and wrong only for others.")
