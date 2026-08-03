"""
Structural diff: Maxim's I263 LUT tables vs standard TABLE 13/H.263 (+sign).

For every 13-bit window, decode the first TCOEF event under both schemes and
compare (total bits consumed, LAST, RUN). Level magnitudes are compared as
indices (I263 levidx>>1 vs TABLE 13 |level|). Windows where the I263 LUT is
invalid are reported separately — that's the extension space the SMV encoder
uses.
"""
from i263_decoder import VLC_TAB5, VLC_TAB6
from tcoef_tables import TCOEF_VLC, ESCAPE_CODE

# T13 longest-prefix matcher over a 13-bit window string
T13 = dict(TCOEF_VLC)
MAXLEN = max(len(c) for c in T13)


def t13_decode(bits: str):
    if bits.startswith(ESCAPE_CODE):
        return ("ESC",)
    for ln in range(1, min(MAXLEN, len(bits)) + 1):
        if bits[:ln] in T13:
            last, run, level = T13[bits[:ln]]
            return (ln + 1, last, run, level)   # +1 sign bit
    return ("NOMATCH",)


def i263_decode(w: int):
    sym = VLC_TAB5[w >> 5]
    if sym == 1:
        return ("ESC",)
    if (sym & 1) and (sym >> 1):
        sym = VLC_TAB6[w]
    else:
        sym >>= 1
    skip = (sym >> 17) & 0x1F
    if sym == 0 or skip == 0:
        return ("INVALID",)
    levidx = sym & 0xFF
    run = ((sym >> 8) & 0xFF) - 1
    last = (sym >> 16) & 1
    return (skip, last, run, levidx >> 1)   # levidx>>1 = magnitude index


diffs = []
invalid_only = []
agree = 0
for w in range(8192):
    bits = format(w, "013b")
    a = i263_decode(w)
    b = t13_decode(bits)
    if a == ("ESC",) and b == ("ESC",):
        agree += 1
        continue
    if a == ("INVALID",):
        if b == ("NOMATCH",):
            agree += 1
        else:
            invalid_only.append((bits, b))
        continue
    if b in (("NOMATCH",), ("ESC",)):
        diffs.append((bits, a, b))
        continue
    # compare (len,last,run); level magnitude: T13 level vs levidx magnitude
    if a[0] == b[0] and a[1] == b[1] and a[2] == b[2] and a[3] == b[3]:
        agree += 1
    else:
        diffs.append((bits, a, b))

print(f"windows agreeing: {agree}/8192")
print(f"\nI263-INVALID but TABLE13-valid ({len(invalid_only)}):")
seen = set()
for bits, b in invalid_only:
    key = bits[:11]
    if key in seen:
        continue
    seen.add(key)
    print(f"  {bits}  T13: len={b[0]} last={b[1]} run={b[2]} |lev|={b[3]}")

print(f"\nSTRUCTURAL DIFFERENCES ({len(diffs)}):")
seen = set()
for bits, a, b in diffs[:200]:
    key = (a, b)
    if key in seen:
        continue
    seen.add(key)
    print(f"  {bits}  I263: {a}   T13: {b}")
print(f"  ... ({len(diffs)} raw windows, {len(seen)} distinct (a,b) pairs)")
