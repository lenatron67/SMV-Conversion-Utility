"""
Session 5 — cross-frame bitstream alignment (the static-scene trick).

Premise: all 9,367 frames are INTRA (proven by full_scan), GQUANT=16 is
constant, and H.263 intra MBs are context-free (absolute 8-bit FLC DC, no
prediction across MBs). Therefore an MB whose pixel content is unchanged
between two frames is encoded as the IDENTICAL bit string. For a near-static
webcam scene, adjacent frames should share most of their MB data, and the
diff segmentation directly reveals TRUE MB boundaries without any VLC table.

Stage 1 (this script): quantify sharing between adjacent frames.
For frame pair (i, i+1): index all 64-bit windows of frame i+1's MB data by
rolling hash; greedily walk frame i's MB data finding maximal common
substrings (anchored, extended in both directions). Report coverage and the
matched-segment offsets.
"""
import sys

from bitreader import find_psc_offsets, decode_picture_header

RAW = open("raw_h263.bin", "rb").read()
PSCS = find_psc_offsets(RAW)


def frame_bits(i: int) -> str:
    off = PSCS[i]
    end = PSCS[i + 1] if i + 1 < len(PSCS) else len(RAW)
    frame = RAW[off:end]
    hdr = decode_picture_header(frame, 0)
    s = []
    for byte in frame:
        s.append(f"{byte:08b}")
    return "".join(s)[hdr["header_end_bit"]:]


def align_pair(a: str, b: str, k: int = 64):
    """Maximal common substrings of a (frame i) found in b (frame i+1).
    Returns list of (pos_a, pos_b, length). Greedy left-to-right in a."""
    # index k-bit windows of b
    idx = {}
    for p in range(0, len(b) - k + 1):
        idx.setdefault(b[p:p + k], []).append(p)
    segs = []
    pa = 0
    while pa + k <= len(a):
        w = a[pa:pa + k]
        cands = idx.get(w)
        if not cands:
            pa += 1
            continue
        # pick candidate with closest offset to previous segment's offset
        prev_off = segs[-1][1] - segs[-1][0] if segs else 0
        pb = min(cands, key=lambda q: abs((q - pa) - prev_off))
        # extend left
        la, lb = pa, pb
        while la > 0 and lb > 0 and a[la - 1] == b[lb - 1]:
            la -= 1
            lb -= 1
        # don't re-cover already-matched territory
        if segs and la < segs[-1][0] + segs[-1][2]:
            covered = segs[-1][0] + segs[-1][2] - la
            la += covered
            lb += covered
        # extend right
        ra, rb = pa + k, pb + k
        while ra < len(a) and rb < len(b) and a[ra] == b[rb]:
            ra += 1
            rb += 1
        segs.append((la, lb, ra - la))
        pa = ra
    return segs


def main():
    i = int(sys.argv[1]) if len(sys.argv) > 1 else 70
    n_pairs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    for j in range(i, i + n_pairs):
        a = frame_bits(j)
        b = frame_bits(j + 1)
        segs = align_pair(a, b)
        cov = sum(s[2] for s in segs)
        print(f"\npair ({j},{j + 1}): len_a={len(a)} len_b={len(b)} "
              f"matched {cov} bits ({cov / len(a):5.1%}) "
              f"in {len(segs)} segments")
        for pa, pb, ln in segs:
            print(f"    a@{pa:6d}  b@{pb:6d}  len {ln:6d}  "
                  f"(offset {pb - pa:+d})")


if __name__ == "__main__":
    main()
