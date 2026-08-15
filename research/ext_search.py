"""
Session 5 — constrained bootstrap of the SMV TCOEF extension table.

Builds on the VALIDATED i263 port (i263_avi_sanity.py: 21/21 genuine I263
intra frames decode perfectly), so any SMV failure is an SMV-specific code.

Model: the proprietary table = stock I263 (== TABLE 13 + sign folding) PLUS
extension entries keyed by bit-pattern prefix. An extension entry is
(prefix, skip, last, run): when the next len(prefix) bits at a TCOEF event
position equal `prefix`, consume `skip` bits (== len(prefix); the sign is
folded into the pattern) and apply (last, run). Levels are irrelevant for
structural search.

Search loop (per failing frame):
  1. decode with current ext table, collect the event trace to the failure;
  2. for every traced event whose window is deep-zero space (>= MIN_LZ
     leading zeros), and every (L, last) hypothesis, tentatively add
     prefix=bits[pos:pos+L] -> (skip=L, last, run=0) and re-decode;
  3. score = new failure bit position (further = better);
  4. cross-validate survivors on ALL 15 frames: an entry must not regress
     any frame's failure point.

Usage:
  python ext_search.py baseline            # first-failure table, current ext
  python ext_search.py sweep frame_01873   # hypothesis sweep on one frame
"""
import json
import sys
from pathlib import Path

from bitreader import BitReader, decode_picture_header
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)

QCIF_MB_W, QCIF_MB_H = 11, 9
MIN_LZ = 4          # candidate events: window has >= this many leading zeros
MAX_PREFIX = 14     # longest extension code length hypothesised
MIN_PREFIX = 6      # shortest (anything shorter would collide with hot codes)

# committed extension entries: {prefix_str: (last, run)}; skip == len(prefix)
# candidate from ext_solver exact-fit votes (session 5): the recurring
# session-4 culprit `0000011000000...` = ESC+LAST+RUN=0 *without* a level
# field, consuming 14 bits, LAST=1; both values of the trailing (14th) bit.
EXT: dict[str, tuple[int, int]] = {
    "00000110000000": (1, 0),
    "00000110000001": (1, 0),
}


def window_str(data: bytes, pos: int, n: int) -> str:
    total = len(data) * 8
    out = []
    for i in range(pos, pos + n):
        if i >= total:
            out.append("0")
        else:
            out.append(str((data[i // 8] >> (7 - i % 8)) & 1))
    return "".join(out)


def decode_block_ext(br: BitReader, quant: int, tcoef_coded: bool,
                     ext: dict, trace=None):
    """INTRADC + TCOEF for one block under stock-tables + ext entries."""
    pos = br.pos
    if br.bits_remaining() < 8:
        raise DecodeError("EOF in INTRADC")
    v = br.read(8)
    if v in (0x00, 0x80):
        raise DecodeError(f"forbidden INTRADC {v:#x} at bit {pos}")
    coef_num = 1
    if not tcoef_coded:
        return
    data = br.data
    last = False
    while coef_num < 64 and not last:
        ev_pos = br.pos
        win = window_str(data, ev_pos, MAX_PREFIX)
        # extension entries take precedence (longest match first)
        matched = None
        for L in range(MAX_PREFIX, MIN_PREFIX - 1, -1):
            e = ext.get(win[:L])
            if e is not None:
                matched = (L, e)
                break
        if matched is not None:
            L, (lst, run) = matched
            if br.bits_remaining() < L:
                raise DecodeError("EOF in ext code")
            br.skip(L)
            last = bool(lst)
            if trace is not None:
                trace.append((ev_pos, win[:L], "EXT", L, lst, run))
        else:
            vlc = peek_safe(br, 13)
            sym = VLC_TAB5[vlc >> 5]
            if sym == 1:
                if br.bits_remaining() < 22:
                    raise DecodeError("EOF in ESCAPE")
                br.skip(7)
                last = bool(br.read(1))
                run = br.read(6)
                level = br.read(8)
                if level in (0x00, 0x80):
                    raise DecodeError(f"forbidden ESC level at bit {ev_pos}")
                if trace is not None:
                    trace.append((ev_pos, win[:7], "ESC", 22, int(last), run))
            else:
                if (sym & 1) and (sym >> 1):
                    sym = VLC_TAB6[vlc]
                else:
                    sym >>= 1
                skip = (sym >> 17) & 0x1F
                if sym == 0 or skip == 0:
                    raise DecodeError(f"invalid TCOEF at bit {ev_pos} "
                                      f"win {win[:13]}")
                if br.bits_remaining() < skip:
                    raise DecodeError("EOF in TCOEF")
                run = ((sym >> 8) & 0xFF) - 1
                last = bool((sym >> 16) & 1)
                br.skip(skip)
                if trace is not None:
                    trace.append((ev_pos, win[:skip], "VLC", skip,
                                  int(last), run))
        if coef_num + run > 63:
            raise DecodeError(f"run overflow at bit {ev_pos} "
                              f"(run={run}, coef_num={coef_num})")
        coef_num += run + 1


def decode_frame_ext(data: bytes, ext: dict, trace=None):
    """Full-frame structural decode. Returns (mbs, end_bit, fail)."""
    hdr = decode_picture_header(data, 0)
    br = BitReader(data, hdr["header_end_bit"])
    gquant = hdr["gquant"]
    mbs = 0
    for mb in range(QCIF_MB_W * QCIF_MB_H):
        try:
            while True:
                vlc = peek_safe(br, 6)
                sym = MCBPC_INTRA[vlc]
                br.skip(sym & 0xFF)
                if vlc == 0:
                    if br.bits_remaining() <= 0:
                        raise DecodeError("EOF in stuffing")
                    continue
                break
            mb_type = (sym >> 10) & 7
            cbpc = (sym >> 8) & 3
            if mb_type not in (MB_INTRA, MB_INTRA_Q):
                raise DecodeError(f"non-intra MB type {mb_type}")
            sym = CBPY_TAB[peek_safe(br, 6)]
            if sym == 0:
                raise DecodeError(f"invalid CBPY at bit {br.pos}")
            br.skip(sym & 0xFF)
            cbpy = (sym >> 12) & 0xF
            if mb_type == MB_INTRA_Q:
                gquant += DQUANT_DIFF[br.read(2)]
                if not (1 <= gquant <= 31):
                    raise DecodeError("gquant out of range")
            cbp = (cbpy << 2) | cbpc
            for b in range(6):
                cbp += cbp
                decode_block_ext(br, gquant, bool(cbp & 64), ext, trace)
        except DecodeError as e:
            return mbs, br.pos, f"MB {mb}: {e}"
        mbs += 1
    return mbs, br.pos, None


def load_samples():
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())
    return [(e["file"], (samples / e["file"]).read_bytes())
            for e in manifest["frames"]]


def baseline(ext):
    rows = []
    for name, data in load_samples():
        mbs, end_bit, fail = decode_frame_ext(data, ext)
        total = len(data) * 8
        rows.append((name, mbs, end_bit, total, fail))
        status = "OK" if fail is None else f"FAIL @ {fail}"
        print(f"{name:<18} {mbs:>3}/99  end {end_bit:>6}/{total:<6} "
              f"(slack {total - end_bit:>6})  {status}")
    full = sum(1 for r in rows if r[4] is None)
    print(f"\nfully decoded: {full}/15")
    return rows


def sweep(frame_name, ext):
    frames = load_samples()
    target = next((n, d) for n, d in frames if frame_name in n)
    name, data = target

    trace = []
    mbs0, end0, fail0 = decode_frame_ext(data, ext, trace)
    print(f"{name}: baseline {mbs0} MBs, fails at bit {end0}: {fail0}")
    print(f"{len(trace)} events traced; sweeping deep-zero events "
          f"(>= {MIN_LZ} leading zeros)\n")

    # current failure positions for all 15 frames (for cross-validation)
    base_fail = {}
    for n, d in frames:
        m, e, f = decode_frame_ext(d, ext)
        base_fail[n] = (m, e, f)

    candidates = []
    for ev_pos, pat, kind, skip, lst, run in trace:
        win = window_str(data, ev_pos, MAX_PREFIX)
        lz = len(win) - len(win.lstrip("0"))
        if lz < MIN_LZ:
            continue
        for L in range(MIN_PREFIX, MAX_PREFIX + 1):
            prefix = win[:L]
            if prefix in ext:
                continue
            for last_h in (0, 1):
                trial = dict(ext)
                trial[prefix] = (last_h, 0)
                m, e, f = decode_frame_ext(data, trial)
                if e > end0 or (f is None and fail0 is not None):
                    candidates.append((e, m, prefix, last_h, ev_pos,
                                       kind, skip))
    candidates.sort(reverse=True)

    print(f"{'new_end':>8} {'MBs':>4}  {'prefix':<15} last  "
          f"{'@bit':>6}  was        cross-validation")
    seen = set()
    for e, m, prefix, last_h, ev_pos, kind, skip in candidates[:40]:
        key = (prefix, last_h)
        if key in seen:
            continue
        seen.add(key)
        # cross-validate on all frames
        trial = dict(ext)
        trial[prefix] = (last_h, 0)
        adv = reg = 0
        for n, d in frames:
            m2, e2, f2 = decode_frame_ext(d, trial)
            b = base_fail[n]
            if e2 > b[1]:
                adv += 1
            elif e2 < b[1]:
                reg += 1
        print(f"{e:>8} {m:>4}  {prefix:<15} {last_h}    {ev_pos:>6}  "
              f"{kind}/len{skip:<2}  +{adv} frames adv, -{reg} regress")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    if mode == "baseline":
        baseline(EXT)
    elif mode == "sweep":
        sweep(sys.argv[2], EXT)


if __name__ == "__main__":
    main()
