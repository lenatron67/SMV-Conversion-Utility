"""
Session 5 — scan ALL 9,367 frames with the validated stock-I263 port and
catalog every frame's FIRST failure.

Why: a first failure of kind `invalid TCOEF` (13-bit window < 16, i.e. 9+
leading zeros — the only explicitly-detectable proprietary space) occurs
while the decoder is still IN SYNC, so it is a direct, trustworthy sighting
of a proprietary code at a known bit position. The two sightings found in
session 2 (frames 00000/09365) came from exactly this mechanism; the full
file should yield hundreds, turning code-length inference from per-position
brute force (defeated by self-sync, ledger #15) into ensemble statistics.

Output: full_scan_results.json — per frame: first-failure kind, bit offset
(within frame), MBs decoded, the 32-bit window at the failure point.
Also prints aggregate histograms.
"""
import json
import sys
from collections import Counter

from bitreader import BitReader, decode_picture_header, find_psc_offsets
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)

QCIF_MBS = 99


class Fail(Exception):
    def __init__(self, kind, bit, extra="", mb=-1, block=-1, ev=-1,
                 coef=-1, intradc=-1):
        self.kind, self.bit, self.extra = kind, bit, extra
        self.mb, self.block, self.ev = mb, block, ev
        self.coef, self.intradc = coef, intradc
        super().__init__(f"{kind} @ {bit} {extra}")


def decode_frame_scan(data: bytes, start_bit: int, gquant: int):
    """Decode until first failure. Returns (mbs_done, Fail-or-None, end_bit)."""
    br = BitReader(data, start_bit)
    mbs = 0
    try:
        for mb in range(QCIF_MBS):
            while True:
                pos = br.pos
                if br.bits_remaining() < 1:
                    raise Fail("EOF_mcbpc", pos)
                vlc = peek_safe(br, 6)
                sym = MCBPC_INTRA[vlc]
                br.skip(sym & 0xFF)
                if vlc == 0:
                    if br.bits_remaining() <= 0:
                        raise Fail("EOF_stuffing", pos)
                    continue
                break
            mb_type = (sym >> 10) & 7
            cbpc = (sym >> 8) & 3
            if mb_type not in (MB_INTRA, MB_INTRA_Q):
                raise Fail("non_intra_mbtype", pos)
            pos = br.pos
            sym = CBPY_TAB[peek_safe(br, 6)]
            if sym == 0:
                raise Fail("bad_cbpy", pos)
            br.skip(sym & 0xFF)
            cbpy = (sym >> 12) & 0xF
            if mb_type == MB_INTRA_Q:
                gquant += DQUANT_DIFF[br.read(2)]
                if not (1 <= gquant <= 31):
                    raise Fail("gquant_range", br.pos)
            cbp = (cbpy << 2) | cbpc
            for b in range(6):
                cbp += cbp
                # INTRADC
                pos = br.pos
                if br.bits_remaining() < 8:
                    raise Fail("EOF_intradc", pos, mb=mb, block=b)
                v = br.read(8)
                if v in (0x00, 0x80):
                    raise Fail("bad_intradc", pos, f"{v:#x}", mb=mb, block=b)
                if not (cbp & 64):
                    continue
                idc = v
                coef_num = 1
                last = False
                ev_i = 0
                while coef_num < 64 and not last:
                    ev_pos = br.pos
                    vlc = peek_safe(br, 13)
                    sym = VLC_TAB5[vlc >> 5]
                    if sym == 1:
                        if br.bits_remaining() < 22:
                            raise Fail("EOF_escape", ev_pos, mb=mb, block=b,
                                       ev=ev_i, coef=coef_num, intradc=idc)
                        br.skip(7)
                        last = bool(br.read(1))
                        run = br.read(6)
                        level = br.read(8)
                        if level in (0x00, 0x80):
                            raise Fail("forbidden_esc_level", ev_pos,
                                       f"{level:#x}", mb=mb, block=b,
                                       ev=ev_i, coef=coef_num, intradc=idc)
                    else:
                        if (sym & 1) and (sym >> 1):
                            sym = VLC_TAB6[vlc]
                        else:
                            sym >>= 1
                        skip = (sym >> 17) & 0x1F
                        if sym == 0 or skip == 0:
                            raise Fail("invalid_tcoef", ev_pos, mb=mb,
                                       block=b, ev=ev_i, coef=coef_num,
                                       intradc=idc)
                        if br.bits_remaining() < skip:
                            raise Fail("EOF_tcoef", ev_pos, mb=mb, block=b,
                                       ev=ev_i, coef=coef_num, intradc=idc)
                        run = ((sym >> 8) & 0xFF) - 1
                        last = bool((sym >> 16) & 1)
                        br.skip(skip)
                    if coef_num + run > 63:
                        raise Fail("run_overflow", ev_pos,
                                   f"run={run} coef={coef_num}", mb=mb,
                                   block=b, ev=ev_i, coef=coef_num,
                                   intradc=idc)
                    coef_num += run + 1
                    ev_i += 1
            mbs += 1
    except Fail as f:
        return mbs, f, br.pos
    return mbs, None, br.pos


def main():
    raw = open("raw_h263.bin", "rb").read()
    print(f"raw stream: {len(raw)} bytes; locating PSCs...", flush=True)
    pscs = find_psc_offsets(raw)
    print(f"{len(pscs)} frames", flush=True)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(pscs)

    results = []
    kinds = Counter()
    mbs_hist = Counter()
    for i, off in enumerate(pscs[:limit]):
        end = pscs[i + 1] if i + 1 < len(pscs) else len(raw)
        frame = raw[off:end]
        try:
            hdr = decode_picture_header(frame, 0)
        except Exception:
            results.append({"frame": i, "kind": "bad_header"})
            kinds["bad_header"] += 1
            continue
        mbs, fail, end_bit = decode_frame_scan(frame, hdr["header_end_bit"],
                                               hdr["gquant"])
        if fail is None:
            rec = {"frame": i, "kind": "OK", "mbs": mbs,
                   "end_bit": end_bit, "total_bits": len(frame) * 8}
            kinds["OK"] += 1
        else:
            win = "".join(str((frame[b // 8] >> (7 - b % 8)) & 1)
                          if b < len(frame) * 8 else "0"
                          for b in range(fail.bit, fail.bit + 32))
            rec = {"frame": i, "kind": fail.kind, "mbs": mbs,
                   "bit": fail.bit, "win32": win, "extra": fail.extra,
                   "pict_type": hdr["picture_type"], "tr": hdr["tr"],
                   "gquant": hdr["gquant"], "mb": fail.mb,
                   "block": fail.block, "ev": fail.ev, "coef": fail.coef,
                   "intradc": fail.intradc}
            kinds[fail.kind] += 1
            mbs_hist[mbs] += 1
        results.append(rec)
        if (i + 1) % 1000 == 0:
            print(f"  {i + 1} frames scanned", flush=True)

    with open("full_scan_results.json", "w") as f:
        json.dump(results, f)
    print(f"\nsaved full_scan_results.json ({len(results)} frames)")

    print("\nFirst-failure kinds:")
    for k, n in kinds.most_common():
        print(f"  {k:<22} {n:>6}")
    print("\nMBs decoded before failure (top 12):")
    for m, n in sorted(mbs_hist.items())[:12]:
        print(f"  {m:>3} MBs: {n}")

    # the gold: invalid_tcoef windows (in-sync proprietary code sightings)
    inv = [r for r in results if r["kind"] == "invalid_tcoef"]
    print(f"\ninvalid_tcoef sightings: {len(inv)}")
    pref = Counter(r["win32"][:13] for r in inv)
    print("13-bit window histogram (top 20):")
    for p, n in pref.most_common(20):
        print(f"  {p}  {n}")

    # also interesting: forbidden ESC level sightings
    esc = [r for r in results if r["kind"] == "forbidden_esc_level"]
    print(f"\nforbidden_esc_level sightings: {len(esc)}")
    for r in esc[:10]:
        print(f"  frame {r['frame']} bit {r['bit']} extra {r['extra']} "
              f"win {r['win32']}")

    # picture_type tally while we're here (open question #1)
    pt = Counter(r.get("pict_type") for r in results if "pict_type" in r)
    print(f"\npicture_type tally (failing frames): {dict(pt)}")


if __name__ == "__main__":
    main()
