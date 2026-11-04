# Benchmark: known cases of the DPC radar data

A small, fixed set of real frames where we already know the right answer.
Every method that decides what to trust in a frame (a cleaning rule, a learned
quality check, a reliability map) runs here before it reaches
[Limen](https://github.com/agent-engineering-studio/limen). Without it, every
improvement is an opinion. Tracked in
[#2](https://github.com/agent-engineering-studio/dpc-radar/issues/2).

## The cases

| Case | What happened | What a method must do |
|---|---|---|
| **Melito, 10 October 2026** | the Reggio Calabria storm: 175–185 mm in 24 h, forecast 20 mm | keep it: it is real |
| **Lipari, same day** | a cloudburst the radar did not see | say the radar is not reliable there |
| **The corrupted :30 series** | a smooth bell of up to 20,584 mm over Abruzzo in every CUM24 frame at :30, on 30/09–02/10, 08–11/10 and 21/10–03/11 ([limen#235](https://github.com/agent-engineering-studio/limen/issues/235)) | reject the frame, or at least every wrong pixel |
| **The clean frame after it** | the :00 frame is right, but differs from the corrupted one before it | keep its pixels |
| **Calm days, 4 and 6 October** | nothing | change nothing |
| **A steady bump, 7 October** *(suspect)* | 940–1,152 mm on 3×3 km on the Lazio–Abruzzo border, the same in both series, all day | probably not rain: to confirm |
| **Disks west of Sardinia, 20 October** *(suspect)* | circular fields at sea, identical from frame to frame | probably sea clutter: to confirm |

Each case is one frame and the one 30 minutes before it, cropped to its area
and committed in [`frames/`](frames/) (CC BY-SA 4.0, © Dipartimento della
Protezione Civile). The DPC deletes frames after about five months: once
committed, a case keeps working.

## Today's scores

`limen-max-step` is what Limen does today: it throws away a pixel that moved
more than 150 mm in 30 minutes. `accept-all` trusts everything.

| Case | Check | `accept-all` | `limen-max-step` |
|---|---|---|---|
| `melito-2026-10-10T06` | frame usable | ✅ usable | — no frame decision |
| `melito-2026-10-10T06` | Melito di Porto Salvo: 100-300 mm | ✅ 174.7 mm | ✅ 174.7 mm |
| `melito-2026-10-10T06` | Bagaladi: 80-250 mm | ✅ 113.1 mm | ✅ 113.1 mm |
| `melito-2026-10-10T06` | Lipari: a cloudburst the radar did not see: not reliable | — no reliability | — no reliability |
| `melito-2026-10-11T00` | Melito di Porto Salvo: 150-300 mm | ✅ 184.9 mm | ✅ 184.9 mm |
| `bell-2026-10-11T0930` | frame rejected | ❌ usable | — no frame decision |
| `bell-2026-10-11T0930` | the bell's peak (7,348 mm): 0-100 mm or not measured | ❌ 6180.7 mm | ✅ not measured |
| `bell-2026-10-11T0930` | the bell's edge: 88 mm where the :00 frame reads 9: 0-30 mm or not measured | ❌ 87.7 mm | ❌ 87.7 mm |
| `after-bell-2026-10-11T10` | frame usable | ✅ usable | — no frame decision |
| `after-bell-2026-10-11T10` | Gran Sasso, the point of limen#235 (7.3 mm in the :00 series): 2-30 mm | ✅ 7.3 mm | ❌ not measured |
| `bell-2026-10-09T0630` | frame rejected | ❌ usable | — no frame decision |
| `bell-2026-10-09T0630` | the bell's peak (13,226 mm): 0-100 mm or not measured | ❌ 10184.8 mm | ✅ not measured |
| `bell-2026-10-25T0630` | frame rejected | ❌ usable | — no frame decision |
| `bell-2026-10-25T0630` | the bell's peak (10,861 mm): 0-100 mm or not measured | ❌ 6510.3 mm | ✅ not measured |
| `calm-2026-10-04T06` | frame usable | ✅ usable | — no frame decision |
| `calm-2026-10-04T06` | at most 0.1% of pixels masked | ✅ 0.00% masked | ✅ 0.00% masked |
| `calm-2026-10-06T06` | frame usable | ✅ usable | — no frame decision |
| `calm-2026-10-06T06` | at most 0.1% of pixels masked | ✅ 0.00% masked | ✅ 0.00% masked |
| `steady-bump-2026-10-07T12` *(suspect)* | the bump (1,152 mm): 0-300 mm or not measured | ❌ 999.9 mm | ❌ 999.9 mm |
| `sardinia-disks-2026-10-21T00` *(suspect)* | inside the disks, at sea (59 mm): 0-5 mm or not measured | ❌ 56.2 mm | ❌ 56.2 mm |
| **Score** | confirmed · suspect | confirmed: 10/18 · suspect: 0/2 | confirmed: 8/18 · suspect: 0/2 |

"—" means the method does not decide; it counts as not passed.

### What the table says

- **The 150 mm rule removes the bell's peak, but not its edges.** Around
  every peak, 5,000–7,000 pixels per corrupted frame are off by 10–150 mm and
  pass: on 11 October, 88 mm where the truth is 9.
- **It also hurts the clean frame that follows a corrupted one**: Gran Sasso
  becomes "not measured" although the frame is right. A rule that compares two
  frames cannot tell which of the two is wrong.
- **Nothing decides on the whole frame.** A corrupted :30 frame correlates
  0.04–0.33 with the :00 one beside it; a frame-level check would drop it
  whole, and keep the clean one.
- **Steady artefacts are invisible to a jump rule**: the 1,000 mm bump and the
  Sardinia disks do not move between frames.
- **The 7,348 mm "isolated pixel"** that Limen's 3×3 median was meant to
  remove is the peak of the corrupted bell: its 3×3 neighbours have a median
  of 6,381 mm. The median does not remove it; the 150 mm rule does.
- **Nobody knows where the radar is blind** (Lipari): that is the reliability
  map of [#3](https://github.com/agent-engineering-studio/dpc-radar/issues/3).

## Run it

```bash
uv sync --group bench
uv run --group bench python -m benchmarks.run                 # every method
uv run --group bench python -m benchmarks.run limen-max-step  # one method
uv run --group bench python -m benchmarks.run --markdown      # the table above
```

CI runs `python -m benchmarks.run --check`: it fails when an outcome differs
from [`results.json`](results.json), so a change in a case or a method is
always a visible decision. After an intended change, run it with `--record` and
commit `results.json`.

## Add a method

A method answers three questions about a frame and the one before it
(see `Method` in [`bench.py`](bench.py)):

```python
@dataclass(frozen=True)
class MyCheck:
    name: str = "my-check"
    description: str = "what it does, in one line"

    def frame_ok(
        self, frame: Frame, previous: Frame
    ) -> bool | None: ...  # is the frame usable at all? None: no decision

    def clean(
        self, frame: Frame, previous: Frame
    ) -> np.ndarray: ...  # the frame with untrusted pixels set to NaN

    def reliable(
        self, lon: float, lat: float
    ) -> bool | None: ...  # can the radar be trusted here? None: no decision
```

Register it in `METHODS` in [`methods.py`](methods.py), run it, and compare.
Points are read as Limen reads them: the median of the valid 3×3 pixels.

## Add a case

1. Add a `[[case]]` to [`cases.toml`](cases.toml): the product, the frame
   time, the area, where the truth comes from, and its checks. Mark it
   `suspect` until the answer is confirmed (rain gauges, the news, the DPC).
2. `uv run --group bench python -m benchmarks.fetch` downloads and crops it
   (only frames younger than about five months still exist).
3. Run the benchmark, check the outcomes, `--record`, commit.
