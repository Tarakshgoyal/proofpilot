"""Day 5 — whole-project end-to-end verification.

Runs every user-visible path against the LIVE local stack (Neon + sandbox):
health, webhook safety, sync, cases, analyse, proposal, locate, decision,
plan gating, executed receipt, audit, packet, eval API. Prints PASS/FAIL
per beat. Exit code 0 only if everything passes.

Usage: python tools/e2e_check.py [--base http://127.0.0.1:8001]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8001"
if "--base" in sys.argv:
    BASE = sys.argv[sys.argv.index("--base") + 1]

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=120)

    # 1 health
    r = c.get("/api/health").json()
    check("health", r["status"] == "ok" and r["db"] == "up", f"mode={r['mode']} db={r['db']}")

    # 2 webhook safety: fake event is received but NEVER verified or enqueued
    r = c.post("/webhooks/paypal", json={"id": f"e2e-probe-{len(results)}",
                                         "event_type": "PROBE"})
    w = r.json()
    check("webhook receiver rejects unverifiable events",
          r.status_code == 200 and w.get("received") is True
          and w.get("verified") is False, str(w)[:80])

    # 3 sync (live or fixture per APP_MODE)
    r = c.post("/api/sync")
    check("sync queues", r.status_code == 202, str(r.json()))

    # 4 cases list (batched)
    cases = c.get("/api/cases").json()["cases"]
    check("cases list", len(cases) >= 3, f"{len(cases)} cases")

    def by_dispute(did):
        return next((x for x in cases if x["dispute_id"] == did), None)

    # 5 BFB: blocking contradiction present
    bfb = by_dispute("PP-R-BFB-10196981")
    check("BFB has contradictions", bfb and bfb["contradiction_count"] >= 2,
          f"count={bfb['contradiction_count'] if bfb else 'n/a'}")

    # 6 BFB: plan must be refused (blocking gate)
    r = c.post(f"/api/cases/{bfb['id']}/plan")
    check("plan refused on BLOCKING case", r.status_code == 409
          and "EVIDENCE_INCOMPLETE" in r.json()["detail"])

    # 7 YJH: full proposal surface
    yjh = by_dispute("PP-R-YJH-10196983")
    prop = c.get(f"/api/cases/{yjh['id']}/proposal").json()
    check("YJH proposal has analysis+claims", prop["analysis"] is not None
          and len(prop["claims"]) >= 3, f"{len(prop['claims'])} claims")

    # 8 quote-backed claim -> locator hit (any verified quote locating counts)
    quoted = [cl for cl in prop["claims"]
              if cl["quoteRef"] and cl["status"] == "VERIFIED"]
    loc_ok, loc_detail = False, "no quoted claims"
    for cl in quoted:
        q = cl["quoteRef"]
        hit = c.get(f"/api/documents/{q['document_id']}/locate",
                    params={"quote": q["quote"]}).json()
        if hit.get("found") and hit.get("box") and len(hit["box"]) == 4:
            loc_ok, loc_detail = True, f"{cl['claimType']} {hit.get('matchType')}"
            break
    check("locator resolves a verified quote to a box", loc_ok, loc_detail)

    # 9 page render (PNG)
    if quoted:
        r = c.get(f"/api/documents/{quoted[0]['quoteRef']['document_id']}/page/1")
        check("page renders to PNG", r.status_code == 200
              and r.headers["content-type"] == "image/png", f"{len(r.content)}B")

    # 10 decision (transparent p + EV)
    d = c.get(f"/api/cases/{yjh['id']}/decision").json()
    check("decision present", bool(d.get("recommendedAction"))
          and "not calibrated" in d.get("rationale", ""), str(d.get("pWin")))

    # 11 executed plan + exactly one action (target it via DB: later e2e runs
    # legitimately leave newer REJECTED plan versions on the case)
    from backend.app.db import SessionLocal
    from backend.app.models import Action, Plan as PlanModel
    import sqlalchemy as sa

    with SessionLocal() as db:
        plan_row = db.scalar(sa.select(PlanModel).where(
            PlanModel.case_id == yjh["id"], PlanModel.status == "EXECUTED")
            .order_by(PlanModel.version.desc()).limit(1))
        action_row = db.scalar(sa.select(Action).where(Action.plan_id == plan_row.id))
        n_actions = len(db.scalars(sa.select(Action)
                                   .where(Action.plan_id == plan_row.id)).all())
        plan = {"id": plan_row.id, "payloadHash": plan_row.payload_hash,
                "payload": plan_row.payload}
        check("executed plan + exactly one action", n_actions == 1
              and action_row.status == "COMPLETED",
              f"v{plan_row.version}, actions={n_actions}")

    # 13 packet PDF downloads
    r = c.get(f"/api/plans/{plan['id']}/packet")
    check("packet PDF", r.status_code == 200 and r.content[:4] == b"%PDF",
          f"{len(r.content)}B")

    # 14 replayed approve is idempotent (no second write)
    r = c.post(f"/api/plans/{plan['id']}/approve", json={
        "expectedPlanHash": plan["payloadHash"],
        "expectedCaseVersion": plan["payload"]["case_version"]})
    body = r.json()
    same_action = body.get("action", {}).get("id") == action_row.id
    check("approve replay returns same action", r.status_code == 200 and same_action)

    # 15 audit chain: fresh plan + reject exercises the chain (do this LAST so
    # the executed plan above stays the "latest" for earlier beats)
    r = c.post(f"/api/cases/{yjh['id']}/plan")
    if r.status_code == 201:
        c.post(f"/api/plans/{r.json()['id']}/reject", json={"reason": "e2e check"})
    audit = c.get(f"/api/cases/{yjh['id']}/audit").json()
    check("audit chain valid (with fresh entries)", audit["verification"]["valid"] is True
          and audit["verification"].get("entries", 0) >= 2,
          f"{audit['verification'].get('entries')} entries")

    # 16 eval API surface
    runs = c.get("/api/eval/runs").json()["runs"]
    done = [x for x in runs if x["status"] == "DONE" and x["totals"]]
    check("eval runs recorded", len(done) >= 1,
          f"{len(done)} complete, headline run raw-rate="
          f"{next((x['totals'].get('raw_unsupported_rate') for x in done), 'n/a')}")

    # 16 frontend served
    r = c.get("/")
    check("frontend served", r.status_code == 200 and b"ProofPilot" in r.content)

    failed = [x for x in results if not x[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} beats passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
