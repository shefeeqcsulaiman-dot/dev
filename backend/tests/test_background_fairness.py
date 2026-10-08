"""Background jobs are fair across companies (app/background.py): one company runs at
most MAX_PER_COMPANY jobs at a time; its others wait in its own line, so another
company's job starts without waiting behind them."""
import threading
import time

from app import background


def test_one_company_cannot_take_every_thread(monkeypatch):
    lock = threading.Lock()
    running = {"busy": 0, "other": 0}
    peak = {"busy": 0, "other": 0}
    started: list[str] = []
    finished = threading.Event()
    done: list[str] = []

    def fake_run(job_id, token, work, company_id=None):
        with lock:
            running[company_id] += 1
            peak[company_id] = max(peak[company_id], running[company_id])
            started.append(job_id)
        time.sleep(0.15)
        with lock:
            running[company_id] -= 1
            done.append(job_id)
            if len(done) == 7:
                finished.set()

    monkeypatch.setattr(background, "_run", fake_run)
    for i in range(6):
        background._submit("busy", f"busy-{i}", "token", None)
    background._submit("other", "other-0", "token", None)

    assert finished.wait(10), done
    assert peak["busy"] == background.MAX_PER_COMPANY
    # The other company's job started among the first wave, not after busy's six.
    assert started.index("other-0") <= background.MAX_PER_COMPANY
    assert sorted(done) == sorted([f"busy-{i}" for i in range(6)] + ["other-0"])
    assert background._running == {} and background._waiting == {}


def test_a_crashing_job_still_frees_its_slot(monkeypatch):
    finished = threading.Event()
    ran: list[str] = []

    def fake_run(job_id, token, work, company_id=None):
        ran.append(job_id)
        if job_id == "crash":
            raise RuntimeError("boom")
        if len(ran) == 3:
            finished.set()

    monkeypatch.setattr(background, "_run", fake_run)
    for job_id in ("crash", "next-1", "next-2"):
        background._submit("co", job_id, "token", None)
    assert finished.wait(10), ran
    deadline = time.time() + 5
    while background._running and time.time() < deadline:
        time.sleep(0.01)
    assert background._running == {} and background._waiting == {}
