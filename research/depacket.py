"""
Session 11 -- THE depacketizer.

Discovery chain (ledger #37): the 25 known-plaintext insertion pairs all
insert the constant byte 0x56 ('V') at byte-aligned stream positions on a
5-byte grid; grid-aligned 0x56 bytes recur every 65 bytes with deviations
of exactly +25k; the byte at +65 inside every deviant interval is 0x41
('A'). raw_h263.bin is therefore NOT a raw H.263 stream but a VLMUX
PACKET stream:

    'V' + 64 bytes video payload   (65-byte packet)
    'A' + 24 bytes audio payload   (25-byte packet)

with the first video byte at file offset 454 (its 'V' tag is at 453, the
last byte of what we called the "452-byte header" plus one). This script
walks the whole file deterministically from the first in-raw tag at
raw[64], strips tags, splits the two elementary streams, and reports any
tag-byte violation.

Outputs: video.h263 (pure video elementary stream, prepended with
raw[0:64] which is the first video payload, tagged at file 453),
audio.raw (concatenated 'A' payloads).

Usage: python depacket.py
"""
SRC = "raw_h263.bin"


def main():
    raw = open(SRC, "rb").read()
    video = [raw[0:64]]          # payload of the V tag at file offset 453
    audio = []
    bad = []
    q = 64
    n_v = n_a = 0
    while q < len(raw):
        tag = raw[q]
        if tag == 0x56:
            video.append(raw[q + 1:q + 65])
            q += 65
            n_v += 1
        elif tag == 0x41:
            audio.append(raw[q + 1:q + 25])
            q += 25
            n_a += 1
        else:
            bad.append(q)
            if len(bad) > 20:
                print("too many bad tags -- aborting walk")
                break
            q += 1  # desperate resync (should never happen)
    v = b"".join(video)
    a = b"".join(audio)
    open("video.h263", "wb").write(v)
    open("audio.raw", "wb").write(a)
    print(f"walked {q}/{len(raw)} bytes")
    print(f"V packets: {n_v}  A packets: {n_a}  bad tags: {len(bad)}")
    if bad:
        print("bad tag positions:", bad[:20])
    print(f"video.h263: {len(v)} bytes   audio.raw: {len(a)} bytes")
    tail = len(raw) - q
    if 0 < tail:
        print(f"(trailing partial packet: {tail} bytes)")


if __name__ == "__main__":
    main()
