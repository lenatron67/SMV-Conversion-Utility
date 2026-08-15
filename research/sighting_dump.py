"""
Session 5 — dump full decode traces + raw bits around selected in-sync
sightings, to see the true structure of the zero-dense proprietary element.

For each chosen sighting: re-decode the frame with the validated port,
printing the last MB's full event trace (MCBPC/CBPY/INTRADC/TCOEF events with
bit positions), then the raw bitstream from ~16 bits before the failing
block's INTRADC through ~80 bits after, in 8-bit groups.
"""
import json
import sys
from collections import Counter

from bitreader import BitReader, decode_picture_header, find_psc_offsets
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)


def bits_str(data, lo, hi, group=8):
    total = len(data) * 8
    out = []
    for i in range(lo, hi):
        if i >= total:
            break
        b = (data[i // 8] >> (7 - i % 8)) & 1
        out.append(str(b))
        if (i - lo) % group == group - 1:
            out.append(" ")
    return "".join(out)


def trace_frame(data, start_bit, gquant, fail_bit):
    """Decode with the stock port, collecting an annotated event log;
    stop at first failure. Returns the log (list of strings)."""
    br = BitReader(data, start_bit)
    g = gquant
    log = []
    try:
        for mb in range(99):
            log.append(f"MB {mb} @bit {br.pos}")
            while True:
                pos = br.pos
                vlc = peek_safe(br, 6)
                sym = MCBPC_INTRA[vlc]
                br.skip(sym & 0xFF)
                if vlc == 0:
                    log.append(f"  stuffing @bit {pos}")
                    continue
                break
            mb_type = (sym >> 10) & 7
            cbpc = (sym >> 8) & 3
            if mb_type not in (MB_INTRA, MB_INTRA_Q):
                raise DecodeError(f"non-intra mbtype @bit {pos}")
            log.append(f"  MCBPC @bit {pos} len={sym & 0xFF} cbpc={cbpc:02b}")
            pos = br.pos
            sym = CBPY_TAB[peek_safe(br, 6)]
            if sym == 0:
                raise DecodeError(f"bad CBPY @bit {pos}")
            br.skip(sym & 0xFF)
            cbpy = (sym >> 12) & 0xF
            log.append(f"  CBPY  @bit {pos} len={sym & 0xFF} cbpy={cbpy:04b}")
            if mb_type == MB_INTRA_Q:
                g += DQUANT_DIFF[br.read(2)]
            cbp = (cbpy << 2) | cbpc
            for b in range(6):
                cbp += cbp
                pos = br.pos
                v = br.read(8)
                names = ["Y1", "Y2", "Y3", "Y4", "Cb", "Cr"]
                if v in (0x00, 0x80):
                    raise DecodeError(f"bad INTRADC {v:#x} @bit {pos} "
                                      f"block {names[b]}")
                log.append(f"    {names[b]} INTRADC={v} @bit {pos} "
                           f"coded={bool(cbp & 64)}")
                if not (cbp & 64):
                    continue
                coef_num = 1
                last = False
                while coef_num < 64 and not last:
                    ev_pos = br.pos
                    vlc = peek_safe(br, 13)
                    sym2 = VLC_TAB5[vlc >> 5]
                    if sym2 == 1:
                        br.skip(7)
                        last = bool(br.read(1))
                        run = br.read(6)
                        level = br.read(8)
                        if level in (0x00, 0x80):
                            raise DecodeError(
                                f"forbidden ESC level @bit {ev_pos}")
                        log.append(f"      ESC @bit {ev_pos} last={int(last)} "
                                   f"run={run} lev={level}")
                    else:
                        if (sym2 & 1) and (sym2 >> 1):
                            sym2 = VLC_TAB6[vlc]
                        else:
                            sym2 >>= 1
                        skip = (sym2 >> 17) & 0x1F
                        if sym2 == 0 or skip == 0:
                            raise DecodeError(
                                f"invalid TCOEF @bit {ev_pos} win {vlc:013b}")
                        run = ((sym2 >> 8) & 0xFF) - 1
                        last = bool((sym2 >> 16) & 1)
                        br.skip(skip)
                        log.append(f"      VLC @bit {ev_pos} len={skip} "
                                   f"last={int(last)} run={run}")
                    if coef_num + run > 63:
                        raise DecodeError(f"run overflow @bit {ev_pos}")
                    coef_num += run + 1
    except DecodeError as e:
        log.append(f"  FAIL: {e}")
    return log


def main():
    raw = open("raw_h263.bin", "rb").read()
    pscs = find_psc_offsets(raw)
    results = json.load(open("full_scan_results.json"))
    recs = [r for r in results if r["kind"] == "invalid_tcoef"
            and r["ev"] == 0 and r["intradc"] in (3, 6, 12)]
    print(f"{len(recs)} ev0 sightings with INTRADC in (3,6,12)")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    for r in recs[:n]:
        i = r["frame"]
        off = pscs[i]
        end = pscs[i + 1] if i + 1 < len(pscs) else len(raw)
        frame = raw[off:end]
        hdr = decode_picture_header(frame, 0)
        print("\n" + "=" * 72)
        print(f"frame {i}: sighting @bit {r['bit']} mb={r['mb']} "
              f"block={r['block']} intradc={r['intradc']} "
              f"win32={r['win32']}")
        log = trace_frame(frame, hdr["header_end_bit"], hdr["gquant"],
                          r["bit"])
        # print the last 25 log lines (covers the failing MB)
        for line in log[-25:]:
            print("  " + line)
        lo = r["bit"] - 32
        print(f"  raw bits {lo}..{r['bit'] + 88} "
              f"(| marks failure bit {r['bit']}):")
        print(f"    {bits_str(frame, lo, r['bit'])}| "
              f"{bits_str(frame, r['bit'], r['bit'] + 88)}")


if __name__ == "__main__":
    main()
