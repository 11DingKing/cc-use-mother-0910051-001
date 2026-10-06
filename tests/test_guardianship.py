"""
监护关系可追溯授权：功能 + 并发 + 按时间点还原测试。

覆盖：
- 授权版本化、孩子/范围/有效期限定
- 撤回后新请求立即失效；撤回前已完成操作仍保留授权依据
- 共同监护、监护人交接
- 重复提交幂等（顺序 + 并发都只生效一次）
- 撤回与代办并发：不越权、不两次生效（真实 uvicorn + 多线程）
- 管理接口按时间点还原“谁凭哪一版授权看过/办过什么”
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 每个测试进程使用独立数据库，避免与 redscarf.db 互相污染
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="guard_test_")
os.close(_DB_FD)
os.unlink(_DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

import models  # noqa: E402
from database import SessionLocal, engine  # noqa: E402
from main import app  # noqa: E402
from routers import guardianship as guardianship_mod  # noqa: E402

client = TestClient(app)

ALL_SCOPES = ["查询", "报名确认", "权益代领"]


@pytest.fixture(scope="module", autouse=True)
def _cleanup_db():
    yield
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass


def _first_volunteer(status=None):
    db = SessionLocal()
    try:
        q = db.query(models.Volunteer)
        if status:
            q = q.filter(models.Volunteer.status == status)
        v = q.first()
        return v.id, v.name, v.points_balance or 0
    finally:
        db.close()


def _grant(phone, name, volunteer_id, scopes, valid_from=None, valid_until=None):
    body = {"guardian_phone": phone, "guardian_name": name,
            "volunteer_id": volunteer_id, "scopes": scopes}
    if valid_from:
        body["valid_from"] = valid_from.isoformat()
    if valid_until:
        body["valid_until"] = valid_until.isoformat()
    r = client.post("/api/guardianships/authorizations", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _revoke(auth_id, reason="测试撤回"):
    return client.post(f"/api/guardianships/authorizations/{auth_id}/revoke",
                       json={"reason": reason})


def _make_guardian(prefix="张"):
    return f"{prefix}{time.time_ns() % 1000000}", f"13{time.time_ns() % 10**10:010d}"


# ==================== 版本化 / 范围 / 有效期 ====================

def test_grant_is_versioned_and_immutable():
    vid, _, _ = _first_volunteer()
    name, phone = _make_guardian("甲")
    a1 = _grant(phone, name, vid, ["查询"])
    a2 = _grant(phone, name, vid, ["查询", "报名确认"])
    assert a1["version"] == 1 and a2["version"] == 2

    # 旧版本仍在库且被新版本取代（同一对监护关系最新版生效）
    rows = client.get("/api/guardianships/authorizations",
                      params={"volunteer_id": vid, "guardian_phone": phone}).json()
    versions = {r["version"]: r for r in rows}
    assert set(versions) >= {1, 2}

    # 最新版不含权益代领 -> 拒绝
    r = client.post(f"/api/guardians/children/{vid}/benefit-claims",
                    json={"guardian_phone": phone, "benefit_id": 1,
                          "idempotency_key": f"k{time.time_ns()}"})
    assert r.status_code == 403 and "权益代领" in r.json()["detail"]


def test_scope_validation():
    vid, _, _ = _first_volunteer()
    name, phone = _make_guardian("乙")
    r = client.post("/api/guardianships/authorizations",
                    json={"guardian_phone": phone, "guardian_name": name,
                          "volunteer_id": vid, "scopes": ["删除孩子"]})
    assert r.status_code == 422


def test_authorization_limited_to_named_child():
    v1, _, _ = _first_volunteer()
    db = SessionLocal()
    try:
        other = db.query(models.Volunteer).filter(models.Volunteer.id != v1).first().id
    finally:
        db.close()
    name, phone = _make_guardian("丙")
    _grant(phone, name, v1, ALL_SCOPES)
    # 对未授权的另一个孩子操作 -> 拒绝
    r = client.get(f"/api/guardians/children/{other}/view",
                   params={"guardian_phone": phone})
    assert r.status_code == 403


def test_expired_authorization_rejected():
    vid, _, _ = _first_volunteer()
    name, phone = _make_guardian("丁")
    _grant(phone, name, vid, ["查询"],
           valid_from=datetime.utcnow() - timedelta(days=10),
           valid_until=datetime.utcnow() - timedelta(days=1))
    r = client.get(f"/api/guardians/children/{vid}/view",
                   params={"guardian_phone": phone})
    assert r.status_code == 403 and "有效期" in r.json()["detail"]


# ==================== 撤回 ====================

def test_revocation_blocks_new_requests_but_keeps_prior_basis():
    vid, vname, _ = _first_volunteer(models.VolunteerStatus.CERTIFIED)
    name, phone = _make_guardian("戊")
    auth = _grant(phone, name, vid, ALL_SCOPES)

    # 撤回前完成一次查询与一次权益代领
    r = client.get(f"/api/guardians/children/{vid}/view",
                   params={"guardian_phone": phone})
    assert r.status_code == 200
    assert r.json()["authorization_basis"]["authorization_id"] == auth["id"]

    benefits = client.get("/api/benefits/").json()
    cheap = sorted(benefits, key=lambda b: b["points_cost"])[0]

    # 确保该孩子积分充足，使代领能真正走到业务落库
    db = SessionLocal()
    try:
        v = db.query(models.Volunteer).get(vid)
        v.points_balance = max(v.points_balance or 0, cheap["points_cost"] + 1000)
        db.commit()
    finally:
        db.close()

    claim_key = f"claim-before-revoke-{time.time_ns()}"
    r = client.post(f"/api/guardians/children/{vid}/benefit-claims",
                    json={"guardian_phone": phone, "benefit_id": cheap["id"],
                          "idempotency_key": claim_key})
    assert r.status_code == 200, r.text
    exchange_id = r.json()["exchange_id"]
    db = SessionLocal()
    try:
        ex = db.query(models.BenefitExchange).get(exchange_id)
        assert ex.authorization_id == auth["id"]
    finally:
        db.close()

    # 撤回
    assert _revoke(auth["id"]).status_code == 200

    # 撤回后新查询立即失效
    r = client.get(f"/api/guardians/children/{vid}/view",
                   params={"guardian_phone": phone})
    assert r.status_code == 403 and "撤回" in r.json()["detail"]

    # 撤回后新代领立即失效
    r = client.post(f"/api/guardians/children/{vid}/benefit-claims",
                    json={"guardian_phone": phone, "benefit_id": cheap["id"],
                          "idempotency_key": f"after-{time.time_ns()}"})
    assert r.status_code == 403

    # 重复撤回报错（不产生第二次效果）
    assert _revoke(auth["id"]).status_code == 409

    # 撤回前已完成的兑换仍能解释依据（业务行仍指向授权版本，审计快照仍在）
    audits = client.get("/api/guardianships/audits",
                        params={"volunteer_id": vid, "action": "权益代领"}).json()
    done = [a for a in audits if a["result"] == "成功"]
    a = done[0]
    assert a["authorization_id"] == auth["id"]
    assert a["auth_version"] == auth["version"]
    assert a["guardian_phone_snapshot"] == phone


def test_legacy_parent_endpoint_enforces_revocation():
    """旧版 /api/parents 静态手机号匹配已被授权版本取代：撤回后无法再查看。"""
    vid, _, _ = _first_volunteer()
    name, phone = _make_guardian("己")
    auth = _grant(phone, name, vid, ["查询"])
    assert client.get(f"/api/parents/volunteer/{vid}",
                      params={"parent_phone": phone}).status_code == 200
    _revoke(auth["id"])
    assert client.get(f"/api/parents/volunteer/{vid}",
                      params={"parent_phone": phone}).status_code == 403


# ==================== 共同监护 / 交接 ====================

def test_co_guardians_independent_scopes_and_revocation():
    vid, _, _ = _first_volunteer()
    n1, p1 = _make_guardian("父")
    n2, p2 = _make_guardian("母")
    _grant(p1, n1, vid, ALL_SCOPES)
    a2 = _grant(p2, n2, vid, ["查询"])

    # 母亲只能查询，不能代领
    assert client.get(f"/api/guardians/children/{vid}/view",
                      params={"guardian_phone": p2}).status_code == 200
    r = client.post(f"/api/guardians/children/{vid}/benefit-claims",
                    json={"guardian_phone": p2, "benefit_id": 1,
                          "idempotency_key": f"k{time.time_ns()}"})
    assert r.status_code == 403

    # 撤回母亲授权不影响父亲
    _revoke(a2["id"])
    assert client.get(f"/api/guardians/children/{vid}/view",
                      params={"guardian_phone": p2}).status_code == 403
    assert client.get(f"/api/guardians/children/{vid}/view",
                      params={"guardian_phone": p1}).status_code == 200


def test_guardian_handover_old_cannot_view_new_can():
    vid, _, _ = _first_volunteer()
    old_name, old_phone = _make_guardian("原监护")
    new_name, new_phone = _make_guardian("新监护")
    old_auth = _grant(old_phone, old_name, vid, ALL_SCOPES,
                      valid_from=datetime.utcnow() - timedelta(days=30))
    assert client.get(f"/api/guardians/children/{vid}/view",
                      params={"guardian_phone": old_phone}).status_code == 200

    # 交接：撤回旧授权，授予新监护人
    _revoke(old_auth["id"], "监护权变更")
    new_auth = _grant(new_phone, new_name, vid, ["查询", "报名确认"])

    assert client.get(f"/api/guardians/children/{vid}/view",
                      params={"guardian_phone": old_phone}).status_code == 403
    r = client.get(f"/api/guardians/children/{vid}/view",
                   params={"guardian_phone": new_phone})
    assert r.status_code == 200
    assert r.json()["authorization_basis"]["version"] == new_auth["version"]

    # 旧版本保留可追溯
    rows = client.get("/api/guardianships/authorizations",
                      params={"volunteer_id": vid}).json()
    statuses = {(r["guardian_id"], r["version"]): r["status"] for r in rows}
    old_rows = [r for r in rows if r["version"] == old_auth["version"]
                and r["id"] == old_auth["id"]]
    assert old_rows and old_rows[0]["status"] == "已撤回"


# ==================== 幂等：重复提交只生效一次 ====================

def test_idempotent_enroll_confirm_sequential():
    # 找一个处于可报名状态、且有可报期次的孩子
    db = SessionLocal()
    try:
        vol = db.query(models.Volunteer).filter(
            models.Volunteer.status.in_([models.VolunteerStatus.IN_TRAINING,
                                         models.VolunteerStatus.PENDING_ASSESSMENT])).first()
        vid = vol.id
        batch = db.query(models.TrainingBatch).first()
        batch_id = batch.id
        # 若已报名该期次则换一个全新期次
        existing = db.query(models.Enrollment).filter_by(
            volunteer_id=vid, batch_id=batch_id).first()
        if existing:
            topic_id = batch.topic_id
            nb = models.TrainingBatch(name=f"幂等测试期次-{time.time_ns()}",
                                      topic_id=topic_id, capacity=30,
                                      status=models.TrainingBatchStatus.ENROLLING)
            db.add(nb); db.commit(); db.refresh(nb)
            batch_id = nb.id
    finally:
        db.close()

    name, phone = _make_guardian("报")
    auth = _grant(phone, name, vid, ["报名确认"])
    key = f"enroll-{time.time_ns()}"
    payload = {"guardian_phone": phone, "batch_id": batch_id, "idempotency_key": key}

    r1 = client.post(f"/api/guardians/children/{vid}/enroll-confirm", json=payload)
    assert r1.status_code == 200, r1.text
    assert r1.json()["replayed"] is False
    enroll_id = r1.json()["enrollment_id"]

    r2 = client.post(f"/api/guardians/children/{vid}/enroll-confirm", json=payload)
    assert r2.status_code == 200, r2.text
    assert r2.json()["replayed"] is True
    assert r2.json()["enrollment_id"] == enroll_id

    db = SessionLocal()
    try:
        count = db.query(models.Enrollment).filter_by(
            volunteer_id=vid, batch_id=batch_id).count()
        assert count == 1  # 没有两次生效
        enr = db.query(models.Enrollment).get(enroll_id)
        assert enr.authorization_id == auth["id"]
    finally:
        db.close()


def test_idempotent_enroll_confirm_concurrent():
    """同一幂等键并发提交：只有一次真正入班。"""
    db = SessionLocal()
    try:
        vol = db.query(models.Volunteer).filter(
            models.Volunteer.status.in_([models.VolunteerStatus.IN_TRAINING,
                                         models.VolunteerStatus.PENDING_ASSESSMENT])).first()
        vid = vol.id
        tpl = db.query(models.TrainingBatch).first()
        nb = models.TrainingBatch(name=f"并发幂等期次-{time.time_ns()}",
                                  topic_id=tpl.topic_id, capacity=30,
                                  status=models.TrainingBatchStatus.ENROLLING)
        db.add(nb); db.commit(); db.refresh(nb)
        batch_id = nb.id
    finally:
        db.close()

    name, phone = _make_guardian("并")
    _grant(phone, name, vid, ["报名确认"])
    key = f"enroll-conc-{time.time_ns()}"

    results = []

    def fire():
        r = client.post(f"/api/guardians/children/{vid}/enroll-confirm",
                        json={"guardian_phone": phone, "batch_id": batch_id,
                              "idempotency_key": key})
        results.append((r.status_code, r.json() if r.status_code == 200 else r.text))

    threads = [threading.Thread(target=fire) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = [x for x in results if x[0] == 200]
    assert len(ok) == 5, results
    enroll_ids = {x[1]["enrollment_id"] for x in ok}
    assert len(enroll_ids) == 1  # 只生效一次
    assert sum(1 for x in ok if not x[1]["replayed"]) == 1

    db = SessionLocal()
    try:
        assert db.query(models.Enrollment).filter_by(
            volunteer_id=vid, batch_id=batch_id).count() == 1
    finally:
        db.close()


# ==================== 撤回与代办并发：不越权 / 不两次生效 ====================

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_revoke_vs_claim_race_real_server(tmp_path):
    """
    用真实 uvicorn（多线程）制造“代办进行中 vs 撤回”并发：
    两线程都先拿到写锁后再决定，BEGIN IMMEDIATE 串行化保证：
    - 不会两次生效；
    - 撤回先提交则代办必失败（不越权）；代办先提交则撤回不影响已成操作。
    """
    import httpx
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    try:
        admin = httpx.Client(base_url=base, timeout=10)

        # 准备：可报名孩子 + 全新期次 + 监护人报名授权
        db = SessionLocal()
        try:
            vol = db.query(models.Volunteer).filter(
                models.Volunteer.status.in_([models.VolunteerStatus.IN_TRAINING,
                                             models.VolunteerStatus.PENDING_ASSESSMENT])).first()
            vid = vol.id
            tpl = db.query(models.TrainingBatch).first()
            nb = models.TrainingBatch(name=f"撤回竞态期次-{time.time_ns()}",
                                      topic_id=tpl.topic_id, capacity=30,
                                      status=models.TrainingBatchStatus.ENROLLING)
            db.add(nb); db.commit(); db.refresh(nb)
            batch_id = nb.id
        finally:
            db.close()

        name, phone = _make_guardian("竞")
        auth = admin.post("/api/guardianships/authorizations", json={
            "guardian_phone": phone, "guardian_name": name,
            "volunteer_id": vid, "scopes": ["报名确认"]}).json()

        # 多个全新期次，每个代办请求用独立期次 + 独立幂等键，避免“重复报名”干扰
        db = SessionLocal()
        try:
            tpl = db.query(models.TrainingBatch).first()
            race_batches = []
            for i in range(6):
                b = models.TrainingBatch(name=f"竞态子期次-{time.time_ns()}-{i}",
                                         topic_id=tpl.topic_id, capacity=30,
                                         status=models.TrainingBatchStatus.ENROLLING)
                db.add(b); db.commit(); db.refresh(b)
                race_batches.append(b.id)
        finally:
            db.close()

        barrier = threading.Barrier(len(race_batches) + 1)
        outcomes = []

        def do_claim(batch_id, key):
            barrier.wait()  # 让所有代办与撤回尽可能在同一刻发出
            r = admin.post(f"/api/guardians/children/{vid}/enroll-confirm", json={
                "guardian_phone": phone, "batch_id": batch_id,
                "idempotency_key": key})
            outcomes.append((r.status_code, r.json() if r.status_code == 200 else None))

        claim_threads = [
            threading.Thread(target=do_claim,
                             args=(bid, f"race-{time.time_ns()}-{i}"))
            for i, bid in enumerate(race_batches)
        ]

        def do_revoke():
            barrier.wait()
            r = admin.post(f"/api/guardianships/authorizations/{auth['id']}/revoke",
                           json={"reason": "竞态撤回"})
            outcomes.append(("REVOKE", r.status_code))

        revoke_thread = threading.Thread(target=do_revoke)
        for t in claim_threads:
            t.start()
        revoke_thread.start()
        for t in claim_threads:
            t.join()
        revoke_thread.join()

        assert ("REVOKE", 200) in outcomes

        db = SessionLocal()
        try:
            revoked_at = db.query(models.GuardianshipAuthorization).get(
                auth["id"]).revoked_at
            assert revoked_at is not None

            success_audits = db.query(models.GuardianshipAudit).filter(
                models.GuardianshipAudit.volunteer_id == vid,
                models.GuardianshipAudit.authorization_id == auth["id"],
                models.GuardianshipAudit.action == models.AuditAction.ENROLL_CONFIRM,
                models.GuardianshipAudit.result == "成功").all()

            # 核心不变量2（不两次生效）：成功审计数 == 实际报名行数，且每行冻结同一授权版本
            enroll_rows = db.query(models.Enrollment).filter(
                models.Enrollment.batch_id.in_(race_batches)).all()
            enroll_ids = {str(e.id) for e in enroll_rows}
            # 仅统计本次竞态发起的报名（审计 ref 指向竞态产生的报名行），排除其他测试复用该孩子的数据
            success_audits = [a for a in success_audits if a.ref_id in enroll_ids]

            # 核心不变量1（不越权）：撤回提交之后，绝无任何成功代办
            late = [a for a in success_audits if a.operated_at >= revoked_at]
            assert late == [], "撤回后仍有代办生效，发生越权"

            assert len(enroll_rows) == len(success_audits)
            http_success = [o for o in outcomes if o[0] == 200 and o[1] and not o[1].get("replayed")]
            assert len(enroll_rows) == len(http_success)
            # 每一条报名（若有）都必须冻结本授权版本；撤回抢得写锁全赢时为空集
            assert {e.authorization_id for e in enroll_rows} <= {auth["id"]}

            # 撤回之后到达的请求必须是 403（再发一次，确定性验证立即失效）
            post = admin.post(f"/api/guardians/children/{vid}/enroll-confirm", json={
                "guardian_phone": phone, "batch_id": race_batches[0],
                "idempotency_key": f"after-revoke-{time.time_ns()}"})
            assert post.status_code == 403
        finally:
            db.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_inflight_claim_then_revoke_is_serialized_deterministically():
    """
    确定性地构造“代办在途、撤回排队”：
    T1 已 BEGIN IMMEDIATE 并完成校验+落库但尚未提交；此时 T2 撤回被写锁阻塞；
    T1 提交后撤回才生效。结果：恰好一次代办成功，之后新请求立即 403，不越权、不两次生效。
    """
    from sqlalchemy import text as sql_text

    db = SessionLocal()
    try:
        vol = db.query(models.Volunteer).filter(
            models.Volunteer.status.in_([models.VolunteerStatus.IN_TRAINING,
                                         models.VolunteerStatus.PENDING_ASSESSMENT])).first()
        vid = vol.id
        tpl = db.query(models.TrainingBatch).first()
        b = models.TrainingBatch(name=f"在途代办期次-{time.time_ns()}",
                                 topic_id=tpl.topic_id, capacity=30,
                                 status=models.TrainingBatchStatus.ENROLLING)
        db.add(b); db.commit(); db.refresh(b)
        batch_id = b.id

        gname, gphone = _make_guardian("在途")
        auth = _grant(gphone, gname, vid, ["报名确认"])
    finally:
        db.close()

    # T1：在途代办事务（拿写锁、校验、落库，但先不提交）
    t1 = SessionLocal()
    t1.execute(sql_text("BEGIN IMMEDIATE"))
    guardian, a = guardianship_mod.require_authorization(
        t1, gphone, vid, models.GuardianshipScope.ENROLL_CONFIRM)
    enr = models.Enrollment(volunteer_id=vid, batch_id=batch_id,
                            status=models.EnrollmentStatus.ENROLLED,
                            authorization_id=a.id)
    t1.add(enr); t1.flush()

    revoke_result = {}

    def queued_revoke():
        # 独立连接发起撤回：应阻塞至 T1 提交
        s2 = SessionLocal()
        s2.execute(sql_text("BEGIN IMMEDIATE"))
        row = s2.query(models.GuardianshipAuthorization).get(auth["id"])
        row.status = models.AuthorizationStatus.REVOKED
        row.revoked_at = datetime.utcnow()
        row.revoke_reason = "在途测试撤回"
        s2.commit(); s2.close()
        revoke_result["done"] = True

    rt = threading.Thread(target=queued_revoke)
    rt.start()
    time.sleep(0.4)
    assert "done" not in revoke_result, "撤回不应在代办提交前抢先生效"

    # T1 提交（代办先成），排队的撤回随后生效
    guardianship_mod.write_audit(
        t1, auth=a, guardian=guardian, volunteer_id=vid,
        action=models.AuditAction.ENROLL_CONFIRM, detail="在途代办",
        ref_type="enrollment", ref_id=enr.id)
    t1.commit(); t1.close()
    rt.join(timeout=10)
    assert revoke_result.get("done") is True

    # 恰好一次报名成功；撤回后的新请求立即失效
    s = SessionLocal()
    try:
        assert s.query(models.Enrollment).filter_by(
            volunteer_id=vid, batch_id=batch_id).count() == 1
        assert s.query(models.GuardianshipAuthorization).get(
            auth["id"]).status == models.AuthorizationStatus.REVOKED
    finally:
        s.close()

    r = client.post(f"/api/guardians/children/{vid}/enroll-confirm",
                    json={"guardian_phone": gphone, "batch_id": batch_id,
                          "idempotency_key": f"post-{time.time_ns()}"})
    assert r.status_code == 403


def test_claim_after_committed_revoke_denied_deterministically():
    """确定性地构造“撤回先提交、代办后到”：代办必须被拒绝且无任何业务落库。"""
    db = SessionLocal()
    try:
        vol = db.query(models.Volunteer).filter(
            models.Volunteer.status.in_([models.VolunteerStatus.IN_TRAINING,
                                         models.VolunteerStatus.PENDING_ASSESSMENT])).first()
        vid = vol.id
        tpl = db.query(models.TrainingBatch).first()
        b = models.TrainingBatch(name=f"撤回先期次-{time.time_ns()}",
                                 topic_id=tpl.topic_id, capacity=30,
                                 status=models.TrainingBatchStatus.ENROLLING)
        db.add(b); db.commit(); db.refresh(b)
        batch_id = b.id
        gname, gphone = _make_guardian("先撤")
        auth = _grant(gphone, gname, vid, ["报名确认"])
    finally:
        db.close()

    assert _revoke(auth["id"], "先撤回").status_code == 200

    r = client.post(f"/api/guardians/children/{vid}/enroll-confirm",
                    json={"guardian_phone": gphone, "batch_id": batch_id,
                          "idempotency_key": f"denied-{time.time_ns()}"})
    assert r.status_code == 403 and "撤回" in r.json()["detail"]

    db = SessionLocal()
    try:
        assert db.query(models.Enrollment).filter_by(
            volunteer_id=vid, batch_id=batch_id).count() == 0
        denied = db.query(models.GuardianshipAudit).filter_by(
            authorization_id=None, volunteer_id=vid,
            action=models.AuditAction.DENIED, result="拒绝").count()
        assert denied >= 1
    finally:
        db.close()


def test_new_version_supersedes_old_scope_immediately():
    """v2 缩范围后，即便 v1 旧行状态仍“有效”，决定版本是 v2，旧范围不再放行。"""
    vid, _, _ = _first_volunteer()
    name, phone = _make_guardian("缩")
    v1 = _grant(phone, name, vid, ALL_SCOPES)
    v2 = _grant(phone, name, vid, ["查询"])  # 立即生效的新版本

    # 查询放行（v2），代领被拒（v2 无此范围，v1 不再单独授权）
    assert client.get(f"/api/guardians/children/{vid}/view",
                      params={"guardian_phone": phone}).status_code == 200
    r = client.post(f"/api/guardians/children/{vid}/benefit-claims",
                    json={"guardian_phone": phone, "benefit_id": 1,
                          "idempotency_key": f"k{time.time_ns()}"})
    assert r.status_code == 403 and "权益代领" in r.json()["detail"]

    # 两版本都保留可追溯
    rows = client.get("/api/guardianships/authorizations",
                      params={"volunteer_id": vid, "guardian_phone": phone}).json()
    assert {r["version"] for r in rows} == {v1["version"], v2["version"]}


def test_future_dated_version_does_not_shrink_current_access():
    """valid_from 在未来的 v2 不影响 v1 当下的授权；v2 生效后才覆盖 v1。"""
    vid, _, _ = _first_volunteer()
    name, phone = _make_guardian("未来")
    v1 = _grant(phone, name, vid, ALL_SCOPES)
    future = datetime.utcnow() + timedelta(days=2)
    v2 = _grant(phone, name, vid, ["查询"], valid_from=future)

    # 此刻决定版本仍是 v1（含报名确认/权益代领）
    rep = client.get(f"/api/guardianships/reconstruct/{vid}").json()
    mine = [a for a in rep["active_authorizations"] if a["guardian_phone"] == phone]
    assert len(mine) == 1 and mine[0]["version"] == v1["version"]

    # 三天后：v2 生效，v1 不再单独授权
    rep2 = client.get(f"/api/guardianships/reconstruct/{vid}",
                      params={"at": (future + timedelta(days=1)).isoformat()}).json()
    mine2 = [a for a in rep2["active_authorizations"] if a["guardian_phone"] == phone]
    assert len(mine2) == 1 and mine2[0]["version"] == v2["version"]


def test_revoked_then_regranted_uses_new_version():
    """撤回后重新授予产生新版本：旧版仍标记撤回，新版放行且彼此可追溯。"""
    vid, _, _ = _first_volunteer()
    name, phone = _make_guardian("重授")
    v1 = _grant(phone, name, vid, ["查询"])
    assert _revoke(v1["id"]).status_code == 200
    assert client.get(f"/api/guardians/children/{vid}/view",
                      params={"guardian_phone": phone}).status_code == 403
    v2 = _grant(phone, name, vid, ["查询"])
    assert v2["version"] == v1["version"] + 1
    r = client.get(f"/api/guardians/children/{vid}/view",
                   params={"guardian_phone": phone})
    assert r.status_code == 200
    assert r.json()["authorization_basis"]["version"] == v2["version"]


# ==================== 按时间点还原 ====================

def test_point_in_time_reconstruction():
    vid, _, _ = _first_volunteer()
    name, phone = _make_guardian("溯")
    t0 = datetime.utcnow()
    auth = _grant(phone, name, vid, ["查询"], valid_from=t0 - timedelta(days=2))

    client.get(f"/api/guardians/children/{vid}/view",
               params={"guardian_phone": phone})
    t_view = datetime.utcnow()

    time.sleep(0.01)
    _revoke(auth["id"], "还原测试撤回")
    t_revoke = datetime.utcnow()

    # 在“撤回前”的时间点：授权有效，且能看到撤回前那次查询
    r = client.get(f"/api/guardianships/reconstruct/{vid}",
                   params={"at": t_view.isoformat()})
    assert r.status_code == 200, r.text
    rep = r.json()
    active_ids = {a["authorization_id"] for a in rep["active_authorizations"]}
    assert auth["id"] in active_ids
    my_query_events = [e for e in rep["events"]
                       if e["action"] == "查询" and e["authorization_id"] == auth["id"]]
    assert my_query_events, rep["events"]
    query_ev = my_query_events[0]
    assert query_ev["auth_version"] == auth["version"]
    assert query_ev["guardian_phone"] == phone
    assert "查询" in query_ev["scopes"]

    # 在“撤回后”的时间点：授权不再有效，但历史查询事件仍在（可解释依据）
    rep2 = client.get(f"/api/guardianships/reconstruct/{vid}",
                      params={"at": t_revoke.isoformat()}).json()
    active2 = {a["authorization_id"] for a in rep2["active_authorizations"]}
    assert auth["id"] not in active2
    assert any(e["action"] == "查询" and e["authorization_id"] == auth["id"]
               for e in rep2["events"])
    assert any(e["action"] == "撤回" and e["authorization_id"] == auth["id"]
               for e in rep2["events"])


def test_audit_trail_links_business_records():
    """审计流水可回答：谁凭哪版授权办过什么，业务行可反查授权依据。"""
    db = SessionLocal()
    try:
        vol = db.query(models.Volunteer).filter(
            models.Volunteer.status.in_([models.VolunteerStatus.IN_TRAINING,
                                         models.VolunteerStatus.PENDING_ASSESSMENT])).first()
        vid = vol.id
        tpl = db.query(models.TrainingBatch).first()
        nb = models.TrainingBatch(name=f"审计关联期次-{time.time_ns()}",
                                  topic_id=tpl.topic_id, capacity=30,
                                  status=models.TrainingBatchStatus.ENROLLING)
        db.add(nb); db.commit(); db.refresh(nb)
        batch_id = nb.id
    finally:
        db.close()

    name, phone = _make_guardian("审")
    auth = _grant(phone, name, vid, ["报名确认"])
    key = f"audit-link-{time.time_ns()}"
    r = client.post(f"/api/guardians/children/{vid}/enroll-confirm",
                    json={"guardian_phone": phone, "batch_id": batch_id,
                          "idempotency_key": key})
    assert r.status_code == 200
    enrollment_id = r.json()["enrollment_id"]

    audits = client.get("/api/guardianships/audits",
                        params={"volunteer_id": vid, "action": "报名确认"}).json()
    hit = [a for a in audits if a["ref_type"] == "enrollment"
           and a["ref_id"] == str(enrollment_id) and a["result"] == "成功"]
    assert hit and hit[0]["authorization_id"] == auth["id"]

    db = SessionLocal()
    try:
        enr = db.query(models.Enrollment).get(enrollment_id)
        assert enr.authorization_id == auth["id"]
    finally:
        db.close()
