"""Check the browser's MP4s against what smv2mp4.py would produce.

    python web/test/compare.py [web/test/out/results.json]

For each converted file (as recorded by test/e2e.mjs), recompute the
expectations with smv2mp4.py's own functions and compare:
  - video frame count and every frame timestamp (within 1 ms; the web
    version carries timestamps at 1 ms resolution)
  - audio duration after pause restoration (within one AAC frame)
  - picture quality: luma PSNR against the decoded source, every 50th frame
Exits non-zero if anything fails.
"""

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import av
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import smv2mp4 as S  # noqa: E402

STEP = 50
PTS_TOL = 0.001
AUDIO_TOL = 1024 / S.ARATE + 0.01
MIN_PSNR = 38.0


def expected(smv_path):
    """Frame times / sample frames / audio length, per smv2mp4.py."""
    data = Path(smv_path).read_bytes()
    with redirect_stdout(io.StringIO()):
        video, audio, a_vbytes = S.depacketize(data, S.find_first_video_tag(data))
    src = av.open(io.BytesIO(video), format="h263")
    ist = src.streams.video[0]
    times, ys = [], []
    cum, prev, last = 0, None, -1
    for packet in src.demux(ist):           # same timing as encode_video
        if packet.size == 0:
            continue
        tr = S.tr_of_packet(bytes(packet)[:4])
        cum += S.cum_tr_delta(tr, prev)
        prev = tr
        for frame in packet.decode():
            if cum <= last:
                cum = last + 1
            last = cum
            if len(times) % STEP == 0:
                ys.append(frame.to_ndarray(format="gray").astype(np.float64))
            times.append(float(cum * S.TICK))
    audio_secs = None
    if audio:
        pscs, vtimes = S.video_timeline(video)
        with redirect_stdout(io.StringIO()):
            pcm = S.retime_audio(audio, a_vbytes, pscs, vtimes)
        audio_secs = len(pcm) / 2 / S.ARATE
    return times, ys, audio_secs


def actual(mp4_path):
    c = av.open(str(mp4_path))
    vs = c.streams.video[0]
    times = sorted(float(p.pts * vs.time_base) for p in c.demux(vs) if p.pts is not None)
    c.close()
    ys = []
    c = av.open(str(mp4_path))
    for i, frame in enumerate(c.decode(video=0)):
        if i % STEP == 0:
            ys.append(frame.to_ndarray(format="gray").astype(np.float64))
    audio_secs = None
    if c.streams.audio:
        a = c.streams.audio[0]
        audio_secs = float(a.duration * a.time_base)
    c.close()
    return times, ys, audio_secs


def psnr(x, y):
    mse = np.mean((x - y) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def check(smv_path, mp4_path):
    exp_t, exp_y, exp_a = expected(smv_path)
    act_t, act_y, act_a = actual(mp4_path)
    problems = []
    if len(act_t) != len(exp_t):
        problems.append(f"frames {len(act_t)} != expected {len(exp_t)}")
    worst = max((abs(a - e) for a, e in zip(act_t, exp_t)), default=0.0)
    if worst > PTS_TOL:
        problems.append(f"frame timestamps off by up to {worst * 1000:.2f} ms")
    if (exp_a is None) != (act_a is None):
        problems.append(f"audio presence differs (expected {exp_a}, got {act_a})")
    elif exp_a is not None and abs(act_a - exp_a) > AUDIO_TOL:
        problems.append(f"audio {act_a:.3f}s != expected {exp_a:.3f}s")
    ps = [psnr(x, y) for x, y in zip(exp_y, act_y)]
    if ps and min(ps) < MIN_PSNR:
        problems.append(f"min PSNR {min(ps):.1f} dB < {MIN_PSNR}")
    audio = f"audio {act_a:.2f}s (exp {exp_a:.2f}s)" if exp_a is not None else "no audio"
    summary = (f"{len(act_t)} frames, last {act_t[-1] if act_t else 0:.3f}s, "
               f"max pts err {worst * 1000:.2f} ms, {audio}, "
               f"PSNR mean {np.mean(ps):.1f} / min {min(ps):.1f} dB")
    return problems, summary


def main():
    results_path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "out" / "results.json")
    results = json.loads(results_path.read_text())
    av.logging.set_level(None)
    bad = 0
    for r in results:
        if r["state"] != "done" or not r.get("download"):
            print(f"SKIP {r['name']}: {r['state']}")
            bad += 1
            continue
        problems, summary = check(r["smv"], r["download"])
        print(f"{'PASS' if not problems else 'FAIL'} {r['name']}: {summary}")
        for p in problems:
            print(f"       {p}")
        bad += bool(problems)
    print(f"{len(results) - bad}/{len(results)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
