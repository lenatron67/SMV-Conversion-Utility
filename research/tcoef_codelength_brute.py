"""
Brute-force code-length probe for proprietary TCOEF codes.

tcoef_probe.py (ledger #10) identified two direct TCOEFF VLC no-match failure
points, both starting with the 11-bit prefix `00000000010` that TABLE 13
never uses. The bits at each failure point ARE the proprietary TCOEF code for
some (LAST, RUN, LEVEL) event. We don't know the code length.

Strategy: at each failure bit, sweep candidate code lengths N from 1 to 25
and candidate LAST values {0, 1}. For each (N, LAST) pair:
  - Skip N bits (the unknown code).
  - If LAST=1: block is done, continue from next coded block.
  - If LAST=0: try reading more TCOEF events from TABLE 13 for the same block;
    stop at the first proprietary code (which might be another unknown code).
  - Then continue parsing all remaining blocks in the current MB and all
    subsequent MBs using standard TABLE 13.
  - Score = fully_decoded_mbs * 1000 + partial_blocks_decoded * 10 + tcoef_events

The (N, LAST) pair(s) with the highest score are the most likely candidates
for the code length and LAST bit of the proprietary code. Pairs that work
well for BOTH failure frames are especially trustworthy.

Known failure points (from tcoef_probe.py):
  frame_00000.bin  MB 2  block Y4 @ bit 1593
  frame_09365.bin  MB 4  block Y1 @ bit 1402
"""
import json
from pathlib import Path

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

MAX_CODE_BITS = 25  # sweep this many candidate lengths


# ---------------------------------------------------------------------------
# Low-level TCOEF event and block parsers (TABLE 13 only; return False on any
# failure so the caller can stop and record its score).
# ---------------------------------------------------------------------------
def _read_one_tcoef_event(br: BitReader):
    """Read one TCOEF event using TABLE 13 + ESCAPE. Returns (last, ok)."""
    val, code = match_vlc(br, _TCOEF_LOOKUP, _TCOEF_MAX_LEN)
    if val is None:
        return None, False
    if val is _ESCAPE_SENTINEL:
        if br.bits_remaining() < 15:
            return None, False
        last = br.read(1)
        br.read(6)  # run FLC
        level_byte = br.read(8)
        if level_byte in FORBIDDEN_LEVEL_BYTES:
            return None, False
        return last, True
    last, run, level = val
    if br.bits_remaining() < 1:
        return None, False
    br.read(1)  # sign
    return last, True


def _read_tcoef_block(br: BitReader):
    """Read all TCOEF events for one coded block (until LAST=1 or error).
    Returns (event_count, ok) -- ok=False means TABLE 13 VLC no-match or
    another structural error."""
    count = 0
    for _ in range(64):
        last, ok = _read_one_tcoef_event(br)
        if not ok:
            return count, False
        count += 1
        if last:
            return count, True
    return count, False  # exceeded 64 events without LAST=1


def _read_full_mb(br: BitReader):
    """Decode one complete MB (MCBPC/CBPY/DQUANT/6×[INTRADC+TCOEF]).
    Returns (blocks_decoded, tcoef_events, ok)."""
    mcbpc_val, _ = match_vlc(br, MCBPC_INTRA, 9)
    if mcbpc_val is None:
        return 0, 0, False
    mb_type, cbpc = mcbpc_val
    if mb_type == "stuffing":
        return 0, 0, False

    cbpy_val, _ = match_vlc(br, CBPY_INTRA, 6)
    if cbpy_val is None:
        return 0, 0, False

    if mb_type == 4:
        if br.bits_remaining() < 2:
            return 0, 0, False
        br.read(2)

    coded_flags = [
        (cbpy_val >> 3) & 1, (cbpy_val >> 2) & 1,
        (cbpy_val >> 1) & 1, (cbpy_val >> 0) & 1,
        (cbpc    >> 1) & 1,  (cbpc    >> 0) & 1,
    ]

    blocks_done = 0
    total_events = 0
    for coded in coded_flags:
        if br.bits_remaining() < 8:
            return blocks_done, total_events, False
        intradc = br.read(8)
        if intradc in FORBIDDEN_INTRADC:
            return blocks_done, total_events, False
        if coded:
            ev_count, ok = _read_tcoef_block(br)
            total_events += ev_count
            if not ok:
                return blocks_done, total_events, False
        blocks_done += 1

    return blocks_done, total_events, True


# ---------------------------------------------------------------------------
# Replay to TCOEFF failure and capture decoder state.
# ---------------------------------------------------------------------------
def _coded_flags_from(cbpy_val, cbpc):
    return [
        (cbpy_val >> 3) & 1, (cbpy_val >> 2) & 1,
        (cbpy_val >> 1) & 1, (cbpy_val >> 0) & 1,
        (cbpc    >> 1) & 1,  (cbpc    >> 0) & 1,
    ]


def replay_to_tcoef_failure(data: bytes, start_bit: int):
    """Replay the full walker until a direct TCOEFF VLC no-match; return state."""
    br = BitReader(data, start_bit)

    for mb_index in range(QCIF_MB_COUNT):
        mcbpc_val, _ = match_vlc(br, MCBPC_INTRA, 9)
        if mcbpc_val is None:
            return None
        mb_type, cbpc = mcbpc_val
        if mb_type == "stuffing":
            return None

        cbpy_val, _ = match_vlc(br, CBPY_INTRA, 6)
        if cbpy_val is None:
            return None

        if mb_type == 4:
            if br.bits_remaining() < 2:
                return None
            br.read(2)

        coded_flags = _coded_flags_from(cbpy_val, cbpc)

        for block_idx, coded in enumerate(coded_flags):
            if br.bits_remaining() < 8:
                return None
            intradc = br.read(8)
            if intradc in FORBIDDEN_INTRADC:
                return None
            if coded:
                # Instrument each TCOEF event
                while True:
                    event_start = br.pos
                    val, code = match_vlc(br, _TCOEF_LOOKUP, _TCOEF_MAX_LEN)
                    if val is None:
                        # Direct TCOEFF VLC no-match -- this is the failure point
                        return {
                            "fail_bit": event_start,
                            "mb_index": mb_index,
                            "block_idx": block_idx,
                            "coded_flags": coded_flags,
                            "next_mb_index": mb_index + 1,
                            # blocks still to process after current one
                            "remaining_block_idxs": list(range(block_idx + 1, 6)),
                        }
                    if val is _ESCAPE_SENTINEL:
                        if br.bits_remaining() < 15:
                            return None
                        last = br.read(1)
                        br.read(6)
                        level_byte = br.read(8)
                        if level_byte in FORBIDDEN_LEVEL_BYTES:
                            return None
                    else:
                        last, run, level = val
                        if br.bits_remaining() < 1:
                            return None
                        br.read(1)
                    if last:
                        break
    return None


# ---------------------------------------------------------------------------
# Scoring: given a failure state, try skipping N bits (assume_last) and score.
# ---------------------------------------------------------------------------
def score_candidate(data: bytes, state: dict, code_bits: int, assume_last: int) -> dict:
    """Skip code_bits at state['fail_bit'], treat as LAST=assume_last, continue
    parsing. Returns a score dict."""
    br = BitReader(data, state["fail_bit"])

    if br.bits_remaining() < code_bits:
        return {"score": 0, "mbs_decoded": 0, "blocks_decoded": 0,
                "tcoef_events": 0, "stop": "not enough bits to skip"}

    br.skip(code_bits)

    blocks_decoded = 0
    tcoef_events = 0

    # If LAST=0, continue reading TCOEF events for the current block
    if assume_last == 0:
        for _ in range(63):
            last, ok = _read_one_tcoef_event(br)
            if not ok:
                # Another proprietary code or error -- stop at block level
                score = blocks_decoded * 10 + tcoef_events
                return {"score": score, "mbs_decoded": 0,
                        "blocks_decoded": blocks_decoded,
                        "tcoef_events": tcoef_events,
                        "stop": "2nd proprietary code in same block"}
            tcoef_events += 1
            if last:
                break
        else:
            score = blocks_decoded * 10 + tcoef_events
            return {"score": score, "mbs_decoded": 0,
                    "blocks_decoded": blocks_decoded, "tcoef_events": tcoef_events,
                    "stop": "LAST=0 block exceeded 63 more events"}

    # Process remaining coded blocks in the current MB
    coded_flags = state["coded_flags"]
    for blk_idx in state["remaining_block_idxs"]:
        if br.bits_remaining() < 8:
            score = blocks_decoded * 10 + tcoef_events
            return {"score": score, "mbs_decoded": 0,
                    "blocks_decoded": blocks_decoded, "tcoef_events": tcoef_events,
                    "stop": "out of bits (intradc, cur MB)"}
        intradc = br.read(8)
        if intradc in FORBIDDEN_INTRADC:
            score = blocks_decoded * 10 + tcoef_events
            return {"score": score, "mbs_decoded": 0,
                    "blocks_decoded": blocks_decoded, "tcoef_events": tcoef_events,
                    "stop": f"INTRADC forbidden {hex(intradc)} in cur MB blk {blk_idx}"}
        if coded_flags[blk_idx]:
            ev_count, ok = _read_tcoef_block(br)
            tcoef_events += ev_count
            if not ok:
                score = blocks_decoded * 10 + tcoef_events
                return {"score": score, "mbs_decoded": 0,
                        "blocks_decoded": blocks_decoded, "tcoef_events": tcoef_events,
                        "stop": f"TCOEF error in cur MB blk {blk_idx}"}
        blocks_decoded += 1

    # Process subsequent full MBs
    mbs_decoded = 0
    for mb_idx in range(state["next_mb_index"], QCIF_MB_COUNT):
        blk_done, ev_count, ok = _read_full_mb(br)
        blocks_decoded += blk_done
        tcoef_events += ev_count
        if not ok:
            score = mbs_decoded * 1000 + blocks_decoded * 10 + tcoef_events
            return {"score": score, "mbs_decoded": mbs_decoded,
                    "blocks_decoded": blocks_decoded, "tcoef_events": tcoef_events,
                    "stop": f"fail in MB {mb_idx} after {blk_done}/6 blocks"}
        mbs_decoded += 1
        blocks_decoded += 6

    score = mbs_decoded * 1000 + blocks_decoded * 10 + tcoef_events
    return {"score": score, "mbs_decoded": mbs_decoded,
            "blocks_decoded": blocks_decoded, "tcoef_events": tcoef_events,
            "stop": "SUCCESS -- decoded all remaining MBs"}


# ---------------------------------------------------------------------------
# Main: run across both failure frames, rank candidates.
# ---------------------------------------------------------------------------
def bits_at(data: bytes, bit_pos: int, n: int = 30) -> str:
    n = min(n, len(data) * 8 - bit_pos)
    raw = read_bits(data, bit_pos, n)
    return format(raw, f"0{n}b")


target_frames = [
    entry for entry in manifest["frames"]
    if entry["file"] in ("frame_00000.bin", "frame_09365.bin")
]

frame_results = {}  # fname -> list of (code_bits, assume_last, score_dict)

for entry in target_frames:
    fname = entry["file"]
    data = (SAMPLES / fname).read_bytes()
    start_bit = entry["mb_data_bit_offset_within_frame"]

    state = replay_to_tcoef_failure(data, start_bit)
    if state is None:
        print(f"{fname}: no direct TCOEFF VLC no-match found -- skipping")
        continue

    print(f"\n{'='*72}")
    print(f"Frame: {fname}")
    print(f"  Failure: MB {state['mb_index']}, block idx {state['block_idx']},"
          f" @ bit {state['fail_bit']}")
    print(f"  Coded flags: {state['coded_flags']}  "
          f"Remaining block idxs in this MB: {state['remaining_block_idxs']}")
    print(f"  Bits at failure: {bits_at(data, state['fail_bit'], 40)}")
    print(f"  Sweeping code_bits 1..{MAX_CODE_BITS}, LAST in {{0,1}} ...")

    candidates = []
    for code_bits in range(1, MAX_CODE_BITS + 1):
        for assume_last in (0, 1):
            r = score_candidate(data, state, code_bits, assume_last)
            candidates.append((code_bits, assume_last, r))

    candidates.sort(key=lambda x: x[2]["score"], reverse=True)
    frame_results[fname] = candidates

    print(f"\n  Top 20 candidates (sorted by score):")
    print(f"  {'bits':>5}  {'LAST':>4}  {'score':>8}  {'MBs':>4}  {'blks':>5}  stop reason")
    prev_score = None
    for code_bits, assume_last, r in candidates[:20]:
        marker = " <-- drop" if (prev_score is not None and r["score"] < prev_score * 0.5) else ""
        print(f"  {code_bits:>5}  {assume_last:>4}  {r['score']:>8}  {r['mbs_decoded']:>4}  "
              f"{r['blocks_decoded']:>5}  {r['stop']}{marker}")
        prev_score = r["score"]


# ---------------------------------------------------------------------------
# Cross-frame consensus: which (code_bits, assume_last) pairs rank in the
# top-N for BOTH failure frames?
# ---------------------------------------------------------------------------
if len(frame_results) == 2:
    print(f"\n{'='*72}")
    print(f"CROSS-FRAME CONSENSUS")
    print(f"Pairs that appear in the top-10 for BOTH frames:\n")
    fnames = list(frame_results.keys())
    top10_a = {(cb, la) for cb, la, _ in frame_results[fnames[0]][:10]}
    top10_b = {(cb, la) for cb, la, _ in frame_results[fnames[1]][:10]}
    consensus = top10_a & top10_b

    # Build lookup: (cb, la) -> score for each frame
    score_a = {(cb, la): r["score"] for cb, la, r in frame_results[fnames[0]]}
    score_b = {(cb, la): r["score"] for cb, la, r in frame_results[fnames[1]]}

    if consensus:
        rows = sorted(consensus,
                      key=lambda k: score_a[k] + score_b[k], reverse=True)
        print(f"  {'bits':>5}  {'LAST':>4}  {fnames[0]:>25}  {fnames[1]:>25}")
        for key in rows:
            cb, la = key
            print(f"  {cb:>5}  {la:>4}  {score_a[key]:>25}  {score_b[key]:>25}")
    else:
        print("  (none in common -- the two frames hit different code lengths)")

    # Also show what happens at code_bits=12 specifically (the shared 11-bit
    # prefix suggests the code is ≥12 bits; 12 is the shortest unambiguous
    # possibility after the shared prefix diverges at bit 11)
    print(f"\nDetailed results for code_bits=11,12,13 (motivated by shared 11-bit prefix):")
    print(f"  {'bits':>5}  {'LAST':>4}  {fnames[0]:>8}  {fnames[1]:>8}")
    for cb in (11, 12, 13):
        for la in (0, 1):
            sa = score_a.get((cb, la), "N/A")
            sb = score_b.get((cb, la), "N/A")
            print(f"  {cb:>5}  {la:>4}  {sa:>8}  {sb:>8}")

print(f"\n{'='*72}")
print(f"INTERPRETATION")
print(f"""
High-scoring (code_bits, LAST) pairs suggest the likely code structure:
  - If LAST=1 scores higher: the unknown code IS the last coefficient in
    the block (common for blocks with few non-zero AC coefficients).
  - If LAST=0 scores higher: more coefficients follow in the same block.
  - Consistent high scores across BOTH frames: strong signal.
  - If scores are ALL very low (< 100): TABLE 13 may also be wrong for the
    events immediately following the skip position (more proprietary codes).
    In that case even the correct code length gives a low score -- investigate
    whether the entire TCOEF structure (not just specific codes) differs.
""")
