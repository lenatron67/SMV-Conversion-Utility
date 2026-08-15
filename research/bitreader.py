"""
Shared MSB-first bit reader for the SMV/H.263 reverse-engineering effort.

Every prior analysis script (deep_analyze.py, analyze_h263.py, patch_test.py...)
redefined its own get_bits()/get_bit() helpers. That duplication is how the
original "PTYPE bit-layout mismatch" miscount went unnoticed for as long as it
did. From here on, ALL bit-level parsing for this project should go through
this module so every script agrees on bit numbering by construction.

Bit numbering convention: bit 0 is the MSB of byte 0. This matches the H.263
spec's own bit-numbering and every prior script's convention.
"""
from __future__ import annotations


class BitReader:
    """Sequential MSB-first bit reader over a bytes-like object."""

    def __init__(self, data: bytes, start_bit: int = 0):
        self.data = data
        self.pos = start_bit  # absolute bit offset of the next unread bit

    def peek(self, num_bits: int) -> int:
        """Return the next num_bits as an int without advancing."""
        return read_bits(self.data, self.pos, num_bits)

    def read(self, num_bits: int) -> int:
        """Read and consume the next num_bits, returning them as an int."""
        value = read_bits(self.data, self.pos, num_bits)
        self.pos += num_bits
        return value

    def skip(self, num_bits: int) -> None:
        self.pos += num_bits

    def byte_align(self) -> None:
        """Advance to the next byte boundary (no-op if already aligned)."""
        self.pos = (self.pos + 7) & ~7

    def bits_remaining(self) -> int:
        return len(self.data) * 8 - self.pos

    def __repr__(self):
        byte_idx, bit_idx = divmod(self.pos, 8)
        return f"<BitReader pos={self.pos} (byte {byte_idx} bit {bit_idx}) of {len(self.data)*8}>"


def read_bits(data: bytes, bit_offset: int, num_bits: int) -> int:
    """Read num_bits MSB-first starting at absolute bit_offset. Pure function,
    no state -- handy for one-off field probes (e.g. brute-force experiments)."""
    result = 0
    for i in range(num_bits):
        byte_idx = (bit_offset + i) // 8
        bit_idx = 7 - ((bit_offset + i) % 8)
        result = (result << 1) | ((data[byte_idx] >> bit_idx) & 1)
    return result


def find_psc_offsets(data: bytes, start: int = 0, end: int | None = None,
                     require_signature: bool = True):
    """Locate H.263 Picture Start Codes.

    A PSC is the 22-bit pattern 0000 0000 0000 0000 10 0000, byte-aligned in
    this stream (confirmed: every PSC found so far starts at a byte boundary
    with bytes 00 00 8x).

    require_signature=True additionally filters to PSCs followed by the
    constant `28 04` bytes at offsets +4/+5 -- this is the heuristic
    analyze_h263.py used to separate "real" PSCs from coincidental byte
    patterns in the compressed data (9367 of 9849 candidates passed it).

    Returns a list of byte offsets.
    """
    if end is None:
        end = len(data)
    offsets = []
    for i in range(start, min(end, len(data) - 8)):
        if data[i] == 0x00 and data[i+1] == 0x00 and (data[i+2] & 0xFC) == 0x80:
            if require_signature and not (data[i+4] == 0x28 and data[i+5] == 0x04):
                continue
            offsets.append(i)
    return offsets


def decode_picture_header(data: bytes, frame_offset: int) -> dict:
    """Decode the fixed-position fields of an H.263 picture header starting
    at frame_offset (PSC + TR + PTYPE + optional CPM/PSBI + PEI/PSPARE chain).

    This mirrors what FFmpeg's own (verified-correct, see
    VLC_REVERSE_ENGINEERING.md ledger entry "header-is-standard") parser
    extracts. Returns a dict of field name -> value plus 'header_end_bit',
    the absolute bit offset where macroblock/GOB data begins.
    """
    br = BitReader(data, frame_offset * 8)
    psc = br.read(22)
    tr = br.read(8)
    ptype_fixed = br.read(3)       # must be 1,0,0
    split_screen = br.read(1)
    document_camera = br.read(1)
    freeze_release = br.read(1)
    source_format = br.read(3)
    picture_type = br.read(1)      # 0 = I, 1 = P
    annex_d = br.read(1)
    annex_e = br.read(1)
    annex_f = br.read(1)
    cpm = br.read(1)
    pei = br.read(1)
    psbi = None
    if cpm:
        psbi = br.read(2)
    pspare = []
    while pei:
        pspare.append(br.read(8))
        pei = br.read(1)
    gquant = br.read(5)
    return {
        "psc": psc,
        "tr": tr,
        "ptype_fixed": ptype_fixed,
        "split_screen": split_screen,
        "document_camera": document_camera,
        "freeze_release": freeze_release,
        "source_format": source_format,
        "picture_type": picture_type,
        "annex_def": (annex_d, annex_e, annex_f),
        "cpm": cpm,
        "psbi": psbi,
        "pspare": pspare,
        "gquant": gquant,
        "header_end_bit": br.pos,
    }


SOURCE_FORMAT_NAMES = {
    0: "FORBIDDEN", 1: "sub-QCIF (128x96)", 2: "QCIF (176x144)",
    3: "CIF (352x288)", 4: "4CIF (704x576)", 5: "16CIF (1408x1152)",
    6: "reserved", 7: "PLUSPTYPE (extended)",
}
