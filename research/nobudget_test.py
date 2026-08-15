"""
Session 5 — THE critical experiment: are we positionally in sync already?

Hypothesis (from anchor_stats): SmithMicro kept TABLE 13's code LENGTHS and
LAST flags but remapped (run, level) semantics of many codewords. Wrong runs
explain the dominant run_overflow failures WITHOUT positional desync. The
only positionally-unknown codes are the 9+-leading-zero extension space.

Test: structural decode with
  - NO coefficient-budget check (block ends on LAST only, event cap 64);
  - 9-zero windows (window13 < 16) consumed with a swept hypothesis:
    skip = S, LAST = fixed 0 / fixed 1 / the bit at offset 11 / offset S-1.
Success criterion: 99 MBs + landing within 8 bits of frame end (stuffing).
Run on all 15 sample frames for each configuration.
"""
import json
from pathlib import Path

from bitreader import BitReader, decode_picture_header
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)


def bit_at(frame, pos):
    if pos >= len(frame) * 8:
        return 0
    return (frame[pos // 8] >> (7 - pos % 8)) & 1


def decode_frame_nb(frame, start_bit, gquant, ext_skip, last_mode):
    """No-budget decode. last_mode: '0', '1', 'b11', 'blast'."""
    br = BitReader(frame, start_bit)
    g = gquant
    mbs = 0
    n_ext = 0
    try:
        for mb in range(99):
            while True:
                vlc = peek_safe(br, 6)
                sym = MCBPC_INTRA[vlc]
                br.skip(sym & 0xFF)
                if vlc == 0:
                    if br.bits_remaining() <= 0:
                        raise DecodeError("EOF stuffing")
                    continue
                break
            mb_type = (sym >> 10) & 7
            cbpc = (sym >> 8) & 3
            if mb_type not in (MB_INTRA, MB_INTRA_Q):
                raise DecodeError(f"MB {mb}: non-intra")
            sym = CBPY_TAB[peek_safe(br, 6)]
            if sym == 0:
                raise DecodeError(f"MB {mb}: bad cbpy")
            br.skip(sym & 0xFF)
            cbpy = (sym >> 12) & 0xF
            if mb_type == MB_INTRA_Q:
                g += DQUANT_DIFF[br.read(2)]
                if not (1 <= g <= 31):
                    raise DecodeError(f"MB {mb}: gquant")
            cbp = (cbpy << 2) | cbpc
            for b in range(6):
                cbp += cbp
                if br.bits_remaining() < 8:
                    raise DecodeError(f"MB {mb}: EOF intradc")
                v = br.read(8)
                if v in (0x00, 0x80):
                    raise DecodeError(f"MB {mb} blk {b}: bad intradc {v:#x}")
                if not (cbp & 64):
                    continue
                last = False
                n_ev = 0
                while not last and n_ev < 64:
                    ev_pos = br.pos
                    vlc = peek_safe(br, 13)
                    if vlc < 16:
                        # extension space: hypothesis
                        if br.bits_remaining() < ext_skip:
                            raise DecodeError(f"MB {mb}: EOF ext")
                        if last_mode == "0":
                            last = False
                        elif last_mode == "1":
                            last = True
                        elif last_mode == "b11":
                            last = bool(bit_at(frame, ev_pos + 11))
                        else:       # blast: last bit of the code
                            last = bool(bit_at(frame, ev_pos + ext_skip - 1))
                        br.skip(ext_skip)
                        n_ext += 1
                    else:
                        sym2 = VLC_TAB5[vlc >> 5]
                        if sym2 == 1:
                            if br.bits_remaining() < 22:
                                raise DecodeError(f"MB {mb}: EOF esc")
                            br.skip(7)
                            last = bool(br.read(1))
                            br.skip(6)
                            level = br.read(8)
                            if level in (0x00, 0x80):
                                raise DecodeError(
                                    f"MB {mb} blk {b}: esc level")
                        else:
                            if (sym2 & 1) and (sym2 >> 1):
                                sym2 = VLC_TAB6[vlc]
                            else:
                                sym2 >>= 1
                            skip = (sym2 >> 17) & 0x1F
                            if sym2 == 0 or skip == 0:
                                raise DecodeError(
                                    f"MB {mb} blk {b}: invalid tcoef")
                            if br.bits_remaining() < skip:
                                raise DecodeError(f"MB {mb}: EOF tcoef")
                            last = bool((sym2 >> 16) & 1)
                            br.skip(skip)
                    n_ev += 1
                if n_ev >= 64 and not last:
                    raise DecodeError(f"MB {mb} blk {b}: 64 events no LAST")
            mbs += 1
    except DecodeError as e:
        return mbs, br.pos, n_ext, str(e)
    return mbs, br.pos, n_ext, None


def main():
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())
    frames = [(e["file"], (samples / e["file"]).read_bytes())
              for e in manifest["frames"]]

    print(f"{'config':<14} {'full':>5} {'mean_MBs':>9} {'exact_land':>10}")
    results = {}
    for ext_skip in range(10, 17):
        for last_mode in ("0", "1", "b11", "blast"):
            full = 0
            exact = 0
            tot_mbs = 0
            for name, data in frames:
                hdr = decode_picture_header(data, 0)
                mbs, end_bit, n_ext, fail = decode_frame_nb(
                    data, hdr["header_end_bit"], hdr["gquant"],
                    ext_skip, last_mode)
                tot_mbs += mbs
                if fail is None:
                    full += 1
                    if len(data) * 8 - end_bit < 8:
                        exact += 1
            key = f"S={ext_skip},last={last_mode}"
            results[key] = (full, tot_mbs / 15, exact)
            print(f"{key:<14} {full:>5} {tot_mbs / 15:>9.1f} {exact:>10}")

    best = max(results.items(), key=lambda kv: (kv[1][2], kv[1][0], kv[1][1]))
    print(f"\nbest: {best[0]} -> full={best[1][0]}, "
          f"mean MBs={best[1][1]:.1f}, exact landings={best[1][2]}")


if __name__ == "__main__":
    main()
