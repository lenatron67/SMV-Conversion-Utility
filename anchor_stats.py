"""
Session 5 — TCOEF event statistics from ANCHORED MBs only.

Anchored MBs (mb_catalog.json) are boundary-validated by cross-frame
alignment + unique stock tiling, so their event decodes are trustworthy.
Question: does the stock-decodable regime only contain "tame" events?
If anchored events show a hard ceiling (e.g. run <= 10, |level| <= X,
no/few ESCapes) that the failing gap MBs would have to exceed, the
proprietary extension is the coding of HIGH-ACTIVITY events generally,
not a few rare codewords.
"""
import json
from collections import Counter

from bitreader import BitReader, find_psc_offsets
from i263_decoder import (peek_safe, VLC_TAB5, VLC_TAB6, LEV_TAB,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)

RAW = open("raw_h263.bin", "rb").read()
PSCS = find_psc_offsets(RAW)
cat = json.load(open("mb_catalog.json"))
anchors = cat["anchors"]
print(f"{len(anchors)} anchored MBs")

runs = Counter()
levels = Counter()
n_esc = 0
n_vlc = 0
esc_runs = Counter()
esc_levels = Counter()
ev_per_block = Counter()
maxrun_per_block = Counter()

cur = None
for a in anchors:
    i = a["frame"]
    if cur is None or cur[0] != i:
        off = PSCS[i]
        end = PSCS[i + 1] if i + 1 < len(PSCS) else len(RAW)
        cur = (i, RAW[off:end])
    frame = cur[1]
    br = BitReader(frame, a["start"])
    # MCBPC
    while True:
        vlc = peek_safe(br, 6)
        sym = MCBPC_INTRA[vlc]
        br.skip(sym & 0xFF)
        if vlc == 0:
            continue
        break
    mb_type = (sym >> 10) & 7
    cbpc = (sym >> 8) & 3
    sym = CBPY_TAB[peek_safe(br, 6)]
    br.skip(sym & 0xFF)
    cbpy = (sym >> 12) & 0xF
    g = 16
    if mb_type == MB_INTRA_Q:
        g += DQUANT_DIFF[br.read(2)]
    cbp = (cbpy << 2) | cbpc
    for b in range(6):
        cbp += cbp
        br.skip(8)          # INTRADC
        if not (cbp & 64):
            continue
        coef_num = 1
        last = False
        n_ev = 0
        mx = 0
        while coef_num < 64 and not last:
            vlc = peek_safe(br, 13)
            sym2 = VLC_TAB5[vlc >> 5]
            if sym2 == 1:
                br.skip(7)
                last = bool(br.read(1))
                run = br.read(6)
                level = br.read(8)
                if level >= 128:
                    level -= 256
                n_esc += 1
                esc_runs[run] += 1
                esc_levels[abs(level)] += 1
            else:
                if (sym2 & 1) and (sym2 >> 1):
                    sym2 = VLC_TAB6[vlc]
                else:
                    sym2 >>= 1
                skip = (sym2 >> 17) & 0x1F
                levidx = sym2 & 0xFF
                run = ((sym2 >> 8) & 0xFF) - 1
                last = bool((sym2 >> 16) & 1)
                br.skip(skip)
                lev = LEV_TAB[levidx + (g << 5)]
                n_vlc += 1
                runs[run] += 1
                levels[abs(lev)] += 1
            coef_num += run + 1
            n_ev += 1
            mx = max(mx, run)
        ev_per_block[min(n_ev, 15)] += 1
        maxrun_per_block[mx] += 1

print(f"\nVLC events: {n_vlc}, ESC events: {n_esc} "
      f"({n_esc / max(n_vlc + n_esc, 1):.2%})")
print("\nVLC run histogram:", dict(sorted(runs.items())))
print("\nVLC |level| histogram (top):", levels.most_common(12))
print("\nESC run histogram:", dict(sorted(esc_runs.items())))
print("\nESC |level| histogram (top):", esc_levels.most_common(12))
print("\nevents per coded block:", dict(sorted(ev_per_block.items())))
print("\nmax run per coded block:", dict(sorted(maxrun_per_block.items())))
