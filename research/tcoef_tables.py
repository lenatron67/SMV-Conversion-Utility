"""
TABLE 13/H.263 and TABLE 14/H.263 -- VLC/FLC tables for TCOEF (transform
coefficients), sourced verbatim from "Draft Recommendation H.263"
(https://hlevkin.com/hlevkin/Standards/h263v1.pdf, pages 24-25 / pp.21-22 of
the printed doc). Needed for M3 (AC coefficient decoding) and to extend
mcbpc_boundary_walker.py past "DC-only" macroblocks.

=== 5.4.2 Transform coefficient (TCOEF) -- how to read these tables ===

An EVENT = (LAST, RUN, LEVEL):
  - LAST:  0 = more nonzero coefficients follow in this block: \
           1 = this is the last nonzero coefficient in the block
  - RUN:   number of successive zero coefficients preceding this one
  - LEVEL: the nonzero coefficient's magnitude (sign sent separately)

Most EVENTs are coded with the VLC codes in TABLE 13/H.263 below: a prefix
code (the `vlc` field here -- NOT including the trailing sign bit) followed
by ONE extra bit `s` for the sign (0 = positive, 1 = negative). The `bits`
column in the spec table INCLUDES that sign bit; `TCOEF_VLC` here stores the
prefix only (length = bits - 1) -- consume it, then read 1 more bit for sign.

EVENTs not covered by TABLE 13/H.263 use a 22-bit escape word instead:
  7 bits ESCAPE-marker (the code "0000011" below, index 102, no sign bit of
  its own) + 1 bit LAST + 6 bits RUN (FLC, plain binary 0-63) + 8 bits LEVEL
  (FLC, signed, see TABLE 14/H.263 below -- same forbidden-byte convention as
  INTRADC: 0x00 and 0x80 never occur).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# TABLE 13/H.263 -- VLC table for TCOEF (indices 0-101) + ESCAPE (index 102).
#
# Pasted VERBATIM from the spec text as "INDEX LAST RUN LEVEL BITS CODE" rows
# (code spacing/grouping preserved exactly as printed -- e.g. "0001 0010 1s")
# rather than hand-counted into prefix strings: hand-transcribing the bit
# count separately from the code caused several mismatches on the first pass
# (entries 40, 42, 43, 66-69 -- BITS said 8 but the typed prefix was 8 chars
# long, i.e. didn't leave room for the trailing sign bit). Parsing the raw
# string and deriving the prefix length FROM the string removes that whole
# class of error -- the only thing that can now be wrong is a literal
# misreading of a digit, not an arithmetic slip.
# ---------------------------------------------------------------------------
_RAW_TCOEF_TABLE = """
0   0 0  1  3  10s
1   0 0  2  5  1111 s
2   0 0  3  7  0101 01s
3   0 0  4  8  0010 111s
4   0 0  5  9  0001 1111 s
5   0 0  6  10 0001 0010 1s
6   0 0  7  10 0001 0010 0s
7   0 0  8  11 0000 1000 01s
8   0 0  9  11 0000 1000 00s
9   0 0  10 12 0000 0000 111s
10  0 0  11 12 0000 0000 110s
11  0 0  12 12 0000 0100 000s
12  0 1  1  4  110s
13  0 1  2  7  0101 00s
14  0 1  3  9  0001 1110 s
15  0 1  4  11 0000 0011 11s
16  0 1  5  12 0000 0100 001s
17  0 1  6  13 0000 0101 0000s
18  0 2  1  5  1110 s
19  0 2  2  9  0001 1101 s
20  0 2  3  11 0000 0011 10s
21  0 2  4  13 0000 0101 0001s
22  0 3  1  6  0110 1s
23  0 3  2  10 0001 0001 1s
24  0 3  3  11 0000 0011 01s
25  0 4  1  6  0110 0s
26  0 4  2  10 0001 0001 0s
27  0 4  3  13 0000 0101 0010s
28  0 5  1  6  0101 1s
29  0 5  2  11 0000 0011 00s
30  0 5  3  13 0000 0101 0011s
31  0 6  1  7  0100 11s
32  0 6  2  11 0000 0010 11s
33  0 6  3  13 0000 0101 0100s
34  0 7  1  7  0100 10s
35  0 7  2  11 0000 0010 10s
36  0 8  1  7  0100 01s
37  0 8  2  11 0000 0010 01s
38  0 9  1  7  0100 00s
39  0 9  2  11 0000 0010 00s
40  0 10 1  8  0010 110s
41  0 10 2  13 0000 0101 0101s
42  0 11 1  8  0010 101s
43  0 12 1  8  0010 100s
44  0 13 1  9  0001 1100 s
45  0 14 1  9  0001 1011 s
46  0 15 1  10 0001 0000 1s
47  0 16 1  10 0001 0000 0s
48  0 17 1  10 0000 1111 1s
49  0 18 1  10 0000 1111 0s
50  0 19 1  10 0000 1110 1s
51  0 20 1  10 0000 1110 0s
52  0 21 1  10 0000 1101 1s
53  0 22 1  10 0000 1101 0s
54  0 23 1  12 0000 0100 010s
55  0 24 1  12 0000 0100 011s
56  0 25 1  13 0000 0101 0110s
57  0 26 1  13 0000 0101 0111s
58  1 0  1  5  0111 s
59  1 0  2  10 0000 1100 1s
60  1 0  3  12 0000 0000 101s
61  1 1  1  7  0011 11s
62  1 1  2  12 0000 0000 100s
63  1 2  1  7  0011 10s
64  1 3  1  7  0011 01s
65  1 4  1  7  0011 00s
66  1 5  1  8  0010 011s
67  1 6  1  8  0010 010s
68  1 7  1  8  0010 001s
69  1 8  1  8  0010 000s
70  1 9  1  9  0001 1010 s
71  1 10 1  9  0001 1001 s
72  1 11 1  9  0001 1000 s
73  1 12 1  9  0001 0111 s
74  1 13 1  9  0001 0110 s
75  1 14 1  9  0001 0101 s
76  1 15 1  9  0001 0100 s
77  1 16 1  9  0001 0011 s
78  1 17 1  10 0000 1100 0s
79  1 18 1  10 0000 1011 1s
80  1 19 1  10 0000 1011 0s
81  1 20 1  10 0000 1010 1s
82  1 21 1  10 0000 1010 0s
83  1 22 1  10 0000 1001 1s
84  1 23 1  10 0000 1001 0s
85  1 24 1  10 0000 1000 1s
86  1 25 1  11 0000 0001 11s
87  1 26 1  11 0000 0001 10s
88  1 27 1  11 0000 0001 01s
89  1 28 1  11 0000 0001 00s
90  1 29 1  12 0000 0100 100s
91  1 30 1  12 0000 0100 101s
92  1 31 1  12 0000 0100 110s
93  1 32 1  12 0000 0100 111s
94  1 33 1  13 0000 0101 1000s
95  1 34 1  13 0000 0101 1001s
96  1 35 1  13 0000 0101 1010s
97  1 36 1  13 0000 0101 1011s
98  1 37 1  13 0000 0101 1100s
99  1 38 1  13 0000 0101 1101s
100 1 39 1  13 0000 0101 1110s
101 1 40 1  13 0000 0101 1111s
"""

import re as _re

_TCOEF_ROWS = []  # (index, last, run, level, total_bits_incl_sign, vlc_prefix_no_sign)
for _line in _RAW_TCOEF_TABLE.strip().splitlines():
    _m = _re.match(
        r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([01 ]+)s\s*$", _line)
    if not _m:
        raise ValueError(f"could not parse TCOEF table row: {_line!r}")
    _idx, _last, _run, _level, _bits = (int(_m.group(i)) for i in range(1, 6))
    _code_with_spaces = _m.group(6)
    _prefix = _code_with_spaces.replace(" ", "")
    _TCOEF_ROWS.append((_idx, _last, _run, _level, _bits, _prefix))

del _line, _m, _idx, _last, _run, _level, _bits, _code_with_spaces, _prefix


ESCAPE_INDEX = 102
ESCAPE_CODE = "0000011"  # 7 bits, no sign bit of its own

# code (str of '0'/'1', sign bit NOT included) -> (last, run, level)
TCOEF_VLC: dict[str, tuple[int, int, int]] = {}
for _idx, _last, _run, _level, _bits, _vlc in _TCOEF_ROWS:
    assert len(_vlc) == _bits - 1, (_idx, _vlc, _bits)
    assert _vlc not in TCOEF_VLC, f"duplicate TCOEF VLC code {_vlc!r} (index {_idx})"
    TCOEF_VLC[_vlc] = (_last, _run, _level)

assert ESCAPE_CODE not in TCOEF_VLC
MAX_TCOEF_VLC_LEN = max(len(c) for c in TCOEF_VLC)
MAX_TCOEF_VLC_LEN = max(MAX_TCOEF_VLC_LEN, len(ESCAPE_CODE))


# ---------------------------------------------------------------------------
# TABLE 14/H.263 -- FLC table for RUNS and LEVELS (used after the 7-bit
# ESCAPE code). Both are plain fixed-length binary fields:
#
#   RUN   (6 bits): value = the 6-bit field read as an unsigned integer (0-63)
#                   -- table literally lists index N -> run N -> code = N in
#                   6-bit binary, e.g. index 1 -> run 1 -> "000 001".
#
#   LEVEL (8 bits): value = the 8-bit field read as SIGNED (two's complement);
#                   forbidden byte values are 0x00 (would-be level 0 -- never
#                   sent, since LEVEL is by definition non-zero) and 0x80
#                   (would-be level -128 -- explicitly marked FORBIDDEN in the
#                   spec table). This is the SAME forbidden-pair convention as
#                   INTRADC (5.4.1) -- a useful free validity check.
#                   Verified against the spec's worked examples: code
#                   0000 0001 (1) -> level +1; 0111 1111 (127) -> level +127;
#                   1000 0001 (129) -> level -127; 1111 1111 (255) -> level -1.
# ---------------------------------------------------------------------------
FORBIDDEN_LEVEL_BYTES = {0x00, 0x80}


def decode_run_flc(field: int) -> int:
    """6-bit RUN field (0-63) -> run value (identity mapping per TABLE 14)."""
    return field


def decode_level_flc(field: int) -> int:
    """8-bit LEVEL field -> signed level value per TABLE 14/H.263.
    Caller should treat `field` in FORBIDDEN_LEVEL_BYTES as a stream error."""
    return field if field < 0x80 else field - 0x100
