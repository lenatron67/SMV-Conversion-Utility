"""
Python port of Maxim Poliakovski's open-source (LGPL) I263 decoder
(i263_src/I263 Dec Source/) — entropy layer only, intra pictures.

Source archaeology result (session 4): the I263 TCOEF coding differs from
standard H.263 TABLE 13:
  - two-level LUT: vlc_tab5[show(13) >> 5] for short codes; codes starting
    `000` (except ESCAPE 0000011) fall through to vlc_tab6[show(13)] —
    includes 9-13 bit codes in prefix space TABLE 13 leaves unused;
  - NO separate sign bit: sign lives in the level index (lev_tab +v/-v pairs);
  - levels are reconstruction values via lev_tab[levidx + 32*quant];
  - ESCAPE = 0000011 + LAST(1) RUN(6) LEVEL(8 signed), level dequantized
    inline (standard layout — matches our esc_layout_sweep finding);
  - INTRADC: 8-bit FLC, 0x00/0x80 invalid, 0xFF -> 0x80 remap, coef = v<<3.
MCBPC (TABLE 4), CBPY (TABLE 10, intra mapping) and DQUANT are standard.

This script decodes all 15 sample frames with ZERO drift correction.
Success criterion: 99/99 MBs per frame, ending at the frame's end (modulo
stuffing), with a spatially coherent INTRADC grid.
"""
import json
import re
import sys
from pathlib import Path

from PIL import Image

from bitreader import BitReader, decode_picture_header

SRC = Path("i263_src/I263 Dec Source")
QCIF_MB_W, QCIF_MB_H = 11, 9

MB_INTER, MB_INTER_Q, MB_INTER4V, MB_INTRA, MB_INTRA_Q = 0, 1, 2, 3, 4
DQUANT_DIFF = [-1, -2, 1, 2]


# ── parse the C arrays straight from the source files ────────────────────────
def parse_c_array(text: str, name: str, expected_len: int):
    m = re.search(re.escape(name) + r"\s*\[\s*\d*\s*\]\s*=\s*\{(.*?)\};",
                  text, re.S)
    if not m:
        raise ValueError(f"array {name} not found")
    body = re.sub(r"//[^\n]*", "", m.group(1))
    vals = [int(t, 0) for t in re.findall(r"-?(?:0[xX][0-9a-fA-F]+|\d+)", body)]
    if len(vals) != expected_len:
        raise ValueError(f"{name}: got {len(vals)} values, want {expected_len}")
    return vals


_block_h = (SRC / "I263BlockData.h").read_text()
_data_h = (SRC / "I263data.h").read_text()

VLC_TAB5 = parse_c_array(_block_h, "vlc_tab5", 256)
VLC_TAB6 = parse_c_array(_block_h, "vlc_tab6", 1024)
LEV_TAB = parse_c_array(_block_h, "lev_tab", 1024)
MCBPC_INTRA = parse_c_array(_data_h, "mcbpc_vlc_intra_tab", 64)
CBPY_TAB = parse_c_array(_data_h, "cbpy_vlc_tab", 64)


class DecodeError(Exception):
    pass


def peek_safe(br: BitReader, n: int) -> int:
    """ShowBits with zero-padding past end of data (frames may end mid-window)."""
    avail = br.bits_remaining()
    if avail >= n:
        return br.peek(n)
    if avail <= 0:
        return 0
    return br.peek(avail) << (n - avail)


# ── TCOEF block decode (port of I263Decoder::DecodeBlock) ─────────────────────
def decode_block(br: BitReader, intradc_coded: bool, tcoef_coded: bool,
                 quant: int, events=None):
    """Returns (intradc_or_None). Raises DecodeError on invalid codes."""
    coef_num = 0
    intradc = None

    if intradc_coded:
        if br.bits_remaining() < 8:
            raise DecodeError("EOF in INTRADC")
        v = br.read(8)
        if v in (0x00, 0x80):
            raise DecodeError(f"forbidden INTRADC {v:#x}")
        if v == 0xFF:
            v = 0x80
        intradc = v
        coef_num = 1

    if tcoef_coded:
        offset = quant << 5
        last = False
        while coef_num < 64 and not last:
            ev_pos = br.pos
            vlc = peek_safe(br, 13)
            sym = VLC_TAB5[vlc >> 5]
            if sym == 1:
                # ESCAPE: 0000011 + LAST(1) RUN(6) LEVEL(8)
                if br.bits_remaining() < 22:
                    raise DecodeError("EOF in ESCAPE")
                br.skip(7)
                last = bool(br.read(1))
                run = br.read(6)
                level = br.read(8)
                if level in (0x00, 0x80):
                    raise DecodeError("forbidden ESCAPE level")
                if level >= 128:
                    level -= 256
                kind = "ESC"
            else:
                if (sym & 1) and (sym >> 1):
                    sym = VLC_TAB6[vlc]
                else:
                    sym >>= 1
                skip = (sym >> 17) & 0x1F
                if sym == 0 or skip == 0:
                    raise DecodeError(f"invalid TCOEF code at bit {ev_pos}, "
                                      f"window {vlc:013b}")
                if br.bits_remaining() < skip:
                    raise DecodeError("EOF in TCOEF")
                levidx = sym & 0xFF
                run = ((sym >> 8) & 0xFF) - 1
                last = bool((sym >> 16) & 1)
                br.skip(skip)
                level = LEV_TAB[levidx + offset]
                kind = "VLC"
            if coef_num + run > 63:
                raise DecodeError(f"run overflow (run={run} coef_num={coef_num} "
                                  f"kind={kind} bit {ev_pos} window {vlc:013b})")
            coef_num += run + 1
            if events is not None:
                events.append((kind, last, run, level))

    return intradc


# ── MB header + MB decode (intra pictures only) ───────────────────────────────
def decode_mb(br: BitReader, gquant: int):
    """Returns (idc_list, new_gquant). idc_list = 6 entries (INTRADC or None)."""
    # MCBPC with stuffing loop
    while True:
        vlc = peek_safe(br, 6)
        sym = MCBPC_INTRA[vlc]
        br.skip(sym & 0xFF)
        if vlc == 0:        # stuffing 000000001
            continue
        break
    mb_type = (sym >> 10) & 7
    cbpc = (sym >> 8) & 3
    if mb_type not in (MB_INTRA, MB_INTRA_Q):
        raise DecodeError(f"non-intra MB type {mb_type} in I-picture")

    sym = CBPY_TAB[peek_safe(br, 6)]
    if sym == 0:
        raise DecodeError("invalid CBPY")
    br.skip(sym & 0xFF)
    cbpy = (sym >> 12) & 0xF        # intra mapping

    if mb_type == MB_INTRA_Q:
        gquant += DQUANT_DIFF[br.read(2)]
        if not (1 <= gquant <= 31):
            raise DecodeError(f"gquant out of range: {gquant}")

    cbp = (cbpy << 2) | cbpc
    idc_list = []
    for block_num in range(6):
        cbp += cbp                      # shift left, test bit 5
        tcoef_coded = bool(cbp & 64)    # after the shift, original bit 5
        # intra MB: INTRADC always present
        idc = decode_block(br, True, tcoef_coded, gquant)
        idc_list.append(idc)
    return idc_list, gquant


def maybe_gob_header(br: BitReader, pquant: int):
    """Port of DecodeGOBHeader: optional GOB start code at a GOB row start.
    Returns new gquant (pquant-derived) and consumes the header if present."""
    save = br.pos
    if br.bits_remaining() < 17:
        return None
    val = br.read(17)
    found = (val == 1)
    for _ in range(7):
        if found or br.bits_remaining() < 1:
            break
        val = ((val << 1) & 0x1FFFF) | br.read(1)
        found = (val == 1)
    if not found:
        br.pos = save
        return None
    gob_num = br.read(5)
    br.read(2)                  # gfid
    gquant = br.read(5)
    return gquant


def decode_frame(data: bytes, start_bit: int, pquant: int, collect_idc=True):
    """Decode one full QCIF intra frame. Returns (mb_idc, end_bit, fail)."""
    br = BitReader(data, start_bit)
    gquant = pquant
    mb_idc = []
    for row in range(QCIF_MB_H):
        if row > 0:
            g = maybe_gob_header(br, pquant)
            if g is not None:
                gquant = g
        for col in range(QCIF_MB_W):
            try:
                idc_list, gquant = decode_mb(br, gquant)
            except DecodeError as e:
                return mb_idc, br.pos, f"MB {row * 11 + col} ({row},{col}): {e}"
            mb_idc.append(idc_list if collect_idc else None)
    return mb_idc, br.pos, None


# ── main: run on all 15 sample frames ─────────────────────────────────────────
def main():
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())
    names = sys.argv[1:]
    frames = [e for e in manifest["frames"]
              if not names or any(n in e["file"] for n in names)]

    print(f"{'frame':<18} {'MBs':>7} {'end_bit':>8} {'frame_bits':>10} "
          f"{'slack':>6}  result")
    grid_frame = None
    for entry in frames:
        data = (samples / entry["file"]).read_bytes()
        hdr = decode_picture_header(data, 0)
        start_bit = hdr["header_end_bit"]
        pquant = hdr["gquant"]
        total_bits = len(data) * 8

        mb_idc, end_bit, fail = decode_frame(data, start_bit, pquant)
        n = len(mb_idc)
        slack = total_bits - end_bit
        status = "OK" if fail is None else f"FAIL @ {fail}"
        print(f"{entry['file']:<18} {n:>4}/99 {end_bit:>8} {total_bits:>10} "
              f"{slack:>6}  {status}")

        if entry["file"] == "frame_00000.bin" and fail is None:
            grid_frame = mb_idc

    if grid_frame:
        print("\nY1 INTRADC grid for frame_00000 (11 cols × 9 rows):")
        for row in range(QCIF_MB_H):
            vals = []
            for col in range(QCIF_MB_W):
                idc = grid_frame[row * 11 + col]
                vals.append(f"{idc[0]:3d}" if idc and idc[0] is not None else "  ?")
            print(" ".join(vals))

        # render DC-only grayscale image
        img = Image.new("L", (176, 144), 128)
        offs = [(0, 0), (8, 0), (0, 8), (8, 8)]
        for mb_idx, idc_list in enumerate(grid_frame):
            x0, y0 = (mb_idx % 11) * 16, (mb_idx // 11) * 16
            for bi in range(4):
                if idc_list[bi] is None:
                    continue
                px = min(255, max(0, idc_list[bi]))
                dx, dy = offs[bi]
                for r in range(8):
                    for c in range(8):
                        img.putpixel((x0 + dx + c, y0 + dy + r), px)
        img.save("frame0_dc_i263.png")
        print("\nSaved: frame0_dc_i263.png")


if __name__ == "__main__":
    main()
