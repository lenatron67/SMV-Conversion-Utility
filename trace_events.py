"""
Session 5 — verbose per-event tracer using the validated i263 port.

Prints every MB header decision and every TCOEF event (bit position, 13-bit
window, consumed length, LAST/RUN/level, running coef_num) until the decode
fails. Used to inspect the short failure prefix of a frame (e.g. frame_01873
MB 0 dies within 240 bits) and enumerate where a proprietary code could hide.

Usage: python trace_events.py frame_01873 [max_mbs]
"""
import json
import sys
from pathlib import Path

from bitreader import BitReader, decode_picture_header
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6, LEV_TAB,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)


def trace_block(br: BitReader, block_name: str, tcoef_coded: bool, quant: int):
    coef_num = 0
    pos = br.pos
    v = br.read(8)
    if v in (0x00, 0x80):
        raise DecodeError(f"forbidden INTRADC {v:#x} at bit {pos}")
    if v == 0xFF:
        v = 0x80
    print(f"      {block_name}: INTRADC={v:3d} @bit {pos}")
    coef_num = 1
    if not tcoef_coded:
        return
    offset = quant << 5
    last = False
    ev = 0
    while coef_num < 64 and not last:
        ev_pos = br.pos
        vlc = peek_safe(br, 13)
        win = f"{vlc:013b}"
        sym = VLC_TAB5[vlc >> 5]
        if sym == 1:
            br.skip(7)
            last = bool(br.read(1))
            run = br.read(6)
            level = br.read(8)
            if level in (0x00, 0x80):
                raise DecodeError(f"forbidden ESC level at bit {ev_pos}")
            if level >= 128:
                level -= 256
            kind, skip = "ESC", 22
        else:
            if (sym & 1) and (sym >> 1):
                sym = VLC_TAB6[vlc]
            else:
                sym >>= 1
            skip = (sym >> 17) & 0x1F
            if sym == 0 or skip == 0:
                raise DecodeError(f"invalid TCOEF at bit {ev_pos} win {win}")
            levidx = sym & 0xFF
            run = ((sym >> 8) & 0xFF) - 1
            last = bool((sym >> 16) & 1)
            br.skip(skip)
            level = LEV_TAB[levidx + offset]
            kind = "VLC"
        lead0 = len(win) - len(win.lstrip("0"))
        flag = f"  <— {lead0} leading zeros" if lead0 >= 6 else ""
        print(f"        ev{ev:2d} @bit {ev_pos:5d} win {win} {kind} "
              f"len={skip:2d} last={int(last)} run={run:2d} lev={level:4d} "
              f"coef_num->{coef_num + run + 1}{flag}")
        if coef_num + run > 63:
            raise DecodeError(f"run overflow at bit {ev_pos} win {win} "
                              f"(run={run}, coef_num={coef_num})")
        coef_num += run + 1
        ev += 1


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "frame_01873"
    max_mbs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())
    entry = next(e for e in manifest["frames"] if name in e["file"])
    data = (samples / entry["file"]).read_bytes()
    hdr = decode_picture_header(data, 0)
    br = BitReader(data, hdr["header_end_bit"])
    gquant = hdr["gquant"]
    print(f"{entry['file']}: MB data from bit {br.pos}, gquant={gquant}")
    try:
        for mb in range(max_mbs):
            print(f"  MB {mb} @bit {br.pos}")
            # MCBPC
            while True:
                pos = br.pos
                vlc = peek_safe(br, 6)
                sym = MCBPC_INTRA[vlc]
                br.skip(sym & 0xFF)
                if vlc == 0:
                    print(f"    stuffing @bit {pos}")
                    continue
                break
            mb_type = (sym >> 10) & 7
            cbpc = (sym >> 8) & 3
            if mb_type not in (MB_INTRA, MB_INTRA_Q):
                raise DecodeError(f"non-intra MB type {mb_type}")
            print(f"    MCBPC @bit {pos}: win6={vlc:06b} len={sym & 0xFF} "
                  f"type={mb_type} cbpc={cbpc:02b}")
            pos = br.pos
            vlc = peek_safe(br, 6)
            sym = CBPY_TAB[vlc]
            if sym == 0:
                raise DecodeError(f"invalid CBPY at bit {pos} win {vlc:06b}")
            br.skip(sym & 0xFF)
            cbpy = (sym >> 12) & 0xF
            print(f"    CBPY  @bit {pos}: win6={vlc:06b} len={sym & 0xFF} "
                  f"cbpy={cbpy:04b}")
            if mb_type == MB_INTRA_Q:
                d = DQUANT_DIFF[br.read(2)]
                gquant += d
                print(f"    DQUANT: {d:+d} -> gquant={gquant}")
            cbp = (cbpy << 2) | cbpc
            names = ["Y1", "Y2", "Y3", "Y4", "Cb", "Cr"]
            for b in range(6):
                cbp += cbp
                trace_block(br, names[b], bool(cbp & 64), gquant)
    except DecodeError as e:
        print(f"  FAILED: {e}")
    print(f"  reader at bit {br.pos} of {len(data) * 8}")


if __name__ == "__main__":
    main()
