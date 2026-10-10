"""Read-only load test of the live site's PUBLIC endpoints: /health (one DB query + a Redis
ping) and / (the login page). No logins, no writes, no test data.

    python load-tests/live/live_public_load.py https://e4cs.com 25,50,100,150,200,300 30

Each simulated user makes about one request a second. Stops when 95% of answers take over
3 s or more than 2% fail. Run it from a machine with a fast connection (the GitHub Actions
workflow "Live public load test" does): from one home connection the client itself became the
limit at ~75-110 requests/s (2026-10-10). Prints a Markdown table.
"""
import asyncio, random, statistics, sys, time
import httpx

BASE = sys.argv[1].rstrip("/")
STAGES = [int(x) for x in sys.argv[2].split(",")]
SECS = int(sys.argv[3])

async def user(client, stop_at, out):
    while time.monotonic() < stop_at:
        path = "/health" if random.random() < 0.7 else "/"
        t = time.monotonic()
        try:
            r = await client.get(BASE + path)
            ok = r.status_code == 200 and (path != "/health" or '"db":"ok"' in r.text)
            out.append((path, (time.monotonic() - t) * 1000, ok, r.status_code))
        except Exception as e:
            out.append((path, (time.monotonic() - t) * 1000, False, type(e).__name__))
        await asyncio.sleep(random.uniform(0.5, 1.5))   # each simulated user ~1 request/s

async def main():
    limits = httpx.Limits(max_connections=max(STAGES) + 20, max_keepalive_connections=max(STAGES) + 20)
    async with httpx.AsyncClient(timeout=15, limits=limits, http2=False, headers={"User-Agent": "etaxflow-owner-loadtest"}) as client:
        print("| Users at once | Requests/s | Median ms | 95% under ms | Max ms | /health median | Errors |")
        print("|---|---|---|---|---|---|---|")
        for n in STAGES:
            out = []
            start = time.monotonic(); stop_at = start + SECS
            await asyncio.gather(*(user(client, stop_at, out) for _ in range(n)))
            dur = time.monotonic() - start
            ms = sorted(x[1] for x in out); errs = [x for x in out if not x[2]]
            p95 = ms[int(len(ms) * 0.95) - 1] if ms else 0
            hm = statistics.median([x[1] for x in out if x[0] == "/health"] or [0])
            print(f"| {n} | {len(out)/dur:.1f} | {statistics.median(ms):.0f} | {p95:.0f} | {ms[-1]:.0f} | {hm:.0f} | "
                  f"{100*len(errs)/max(1,len(out)):.1f}% {sorted(set(str(e[3]) for e in errs))[:4] if errs else ''} |", flush=True)
            if p95 > 3000 or len(errs) > 0.02 * len(out):
                print("STOPPED: limit reached"); break
            await asyncio.sleep(5)

asyncio.run(main())
