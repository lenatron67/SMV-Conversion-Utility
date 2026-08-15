"""
Session 5 — ensemble inference of extension-code lengths and LAST flags.

Input: full_scan_results.json (1,493 in-sync sightings of proprietary codes,
window values 0..15 in the 9+-leading-zero space).

For each sighting, re-decode its frame injecting a hypothesis at the exact
sighting bit: "this event consumes L bits, has LAST=l, RUN=0". Decode
onward (stock tables) and record SURVIVAL = bits of valid decode after the
injection point (censored at CAP). Aggregate per (window_value, L, l) over
all sightings of that window value.

Why this beats session-4's per-position scoring (ledger #15): self-sync makes
many (L, l) work at ONE position, but only the true (L, l) keeps the decoder
in sync at EVERY occurrence across ~100 independent contexts. Wrong skips
resync to the true lattice only after consuming garbage events whose random
RUN values pressure the 64-coefficient block budget (run_overflow is the #1
failure kind) — statistically visible in aggregate even if invisible at n=1.

Output: per window value, ranked (L, LAST) hypotheses with median survival
and survival-fraction stats. Also writes length_infer_results.json.
"""
import json
from collections import defaultdict

from bitreader import BitReader, decode_picture_header, find_psc_offsets
from i263_decoder import (peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)

QCIF_MBS = 99
CAP = 4096          # censor survival here (bits)
L_RANGE = range(10, 21)


class Fail(Exception):
    def __init__(self, bit):
        self.bit = bit


def decode_with_injection(data, start_bit, gquant, inject_bit, inj_skip,
                          inj_last, inj_run, cap_bit):
    """Stock structural decode; at the TCOEF event starting exactly at
    inject_bit, apply (inj_skip, inj_last, inj_run) instead of the tables.
    Returns the bit position where decode fails (or cap/frame end)."""
    br = BitReader(data, start_bit)
    g = gquant
    try:
        for mb in range(QCIF_MBS):
            while True:
                pos = br.pos
                if br.bits_remaining() < 1:
                    raise Fail(pos)
                vlc = peek_safe(br, 6)
                sym = MCBPC_INTRA[vlc]
                br.skip(sym & 0xFF)
                if vlc == 0:
                    if br.bits_remaining() <= 0:
                        raise Fail(pos)
                    continue
                break
            mb_type = (sym >> 10) & 7
            cbpc = (sym >> 8) & 3
            if mb_type not in (MB_INTRA, MB_INTRA_Q):
                raise Fail(pos)
            sym = CBPY_TAB[peek_safe(br, 6)]
            if sym == 0:
                raise Fail(br.pos)
            br.skip(sym & 0xFF)
            cbpy = (sym >> 12) & 0xF
            if mb_type == MB_INTRA_Q:
                g += DQUANT_DIFF[br.read(2)]
                if not (1 <= g <= 31):
                    raise Fail(br.pos)
            cbp = (cbpy << 2) | cbpc
            for b in range(6):
                cbp += cbp
                pos = br.pos
                if br.bits_remaining() < 8:
                    raise Fail(pos)
                v = br.read(8)
                if v in (0x00, 0x80):
                    raise Fail(pos)
                if not (cbp & 64):
                    continue
                coef_num = 1
                last = False
                while coef_num < 64 and not last:
                    ev_pos = br.pos
                    if ev_pos > cap_bit:
                        raise Fail(cap_bit)
                    if ev_pos == inject_bit:
                        if br.bits_remaining() < inj_skip:
                            raise Fail(ev_pos)
                        br.skip(inj_skip)
                        last = bool(inj_last)
                        run = inj_run
                    else:
                        vlc = peek_safe(br, 13)
                        sym2 = VLC_TAB5[vlc >> 5]
                        if sym2 == 1:
                            if br.bits_remaining() < 22:
                                raise Fail(ev_pos)
                            br.skip(7)
                            last = bool(br.read(1))
                            run = br.read(6)
                            level = br.read(8)
                            if level in (0x00, 0x80):
                                raise Fail(ev_pos)
                        else:
                            if (sym2 & 1) and (sym2 >> 1):
                                sym2 = VLC_TAB6[vlc]
                            else:
                                sym2 >>= 1
                            skip = (sym2 >> 17) & 0x1F
                            if sym2 == 0 or skip == 0:
                                raise Fail(ev_pos)
                            if br.bits_remaining() < skip:
                                raise Fail(ev_pos)
                            run = ((sym2 >> 8) & 0xFF) - 1
                            last = bool((sym2 >> 16) & 1)
                            br.skip(skip)
                    if coef_num + run > 63:
                        raise Fail(ev_pos)
                    coef_num += run + 1
            if br.pos > cap_bit:
                raise Fail(cap_bit)
    except Fail as f:
        return f.bit
    return br.pos


def main():
    raw = open("raw_h263.bin", "rb").read()
    pscs = find_psc_offsets(raw)
    results = json.load(open("full_scan_results.json"))
    sightings = [r for r in results if r["kind"] == "invalid_tcoef"]
    print(f"{len(sightings)} sightings")

    # survival[w][(L, last)] = list of survival bits
    survival = defaultdict(lambda: defaultdict(list))
    for k, r in enumerate(sightings):
        i = r["frame"]
        off = pscs[i]
        end = pscs[i + 1] if i + 1 < len(pscs) else len(raw)
        frame = raw[off:end]
        hdr = decode_picture_header(frame, 0)
        w = int(r["win32"][:13], 2)
        bit = r["bit"]
        for L in L_RANGE:
            for lst in (0, 1):
                fb = decode_with_injection(
                    frame, hdr["header_end_bit"], hdr["gquant"],
                    bit, L, lst, 0, bit + CAP)
                survival[w][(L, lst)].append(min(fb - bit, CAP))
        if (k + 1) % 200 == 0:
            print(f"  {k + 1} sightings processed", flush=True)

    out = {}
    for w in sorted(survival):
        rows = []
        for (L, lst), vals in survival[w].items():
            vals_s = sorted(vals)
            n = len(vals_s)
            med = vals_s[n // 2]
            q3 = vals_s[(3 * n) // 4]
            p256 = sum(1 for v in vals_s if v >= 256) / n
            p1024 = sum(1 for v in vals_s if v >= 1024) / n
            rows.append((med, q3, p256, p1024, L, lst, n))
        rows.sort(reverse=True)
        out[w] = [{"L": L, "last": lst, "median": med, "q3": q3,
                   "p256": round(p256, 3), "p1024": round(p1024, 3), "n": n}
                  for med, q3, p256, p1024, L, lst, n in rows]
        print(f"\nwindow {w:2d} ({w:013b}, n={rows[0][6]}):")
        print(f"    {'L':>3} {'last':>4} {'median':>7} {'q3':>6} "
              f"{'p256':>6} {'p1024':>6}")
        for med, q3, p256, p1024, L, lst, n in rows[:6]:
            print(f"    {L:>3} {lst:>4} {med:>7} {q3:>6} "
                  f"{p256:>6.2f} {p1024:>6.2f}")

    with open("length_infer_results.json", "w") as f:
        json.dump(out, f, indent=1)
    print("\nsaved length_infer_results.json")


if __name__ == "__main__":
    main()
