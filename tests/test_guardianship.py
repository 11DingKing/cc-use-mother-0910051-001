"""
监护授权版本化端到端测试：
- 授权限定孩子/范围/有效期；撤回立即生效，撤回前操作可解释
- 版本链、共同监护、监护人交接
- 重复提交幂等、撤回与代办并发不越权不两次生效
- 管理端按时间点还原
"""
import os
import sys
import threading
from datetime import datetime, timedelta

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
import models  # noqa: E402
from database import SessionLocal  # noqa: E402
from seed_data import init_db  # noqa: E402

ADMIN = "/api/admin/guardianship"
G = "/api/guardian"


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    # 模型可能新增了约束，删除旧库后重新建表并播种
    from database import engine
    db_path = os.path.join(PROJECT_ROOT, "redscarf.db")
    engine.dispose()
    if os.path.exists(db_path):
        os.remove(db_path)
    models.Base.metadata.create_all(bind=engine)
    init_db()
    yield


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def ids(client):
    vols = client.get("/api/volunteers").json()
    training_v = next(v for v in vols if v["status"] == "培训中")
    certified = []
    for v in vols:
        if v["status"] == "已持证":
            bal = client.get(f"/api/points/volunteer/{v['id']}").json()["points_balance"]
            v["points_balance"] = bal
            certified.append(v)
    assert certified, "种子数据中应有持证志愿者"
    claim_v = max(certified, key=lambda v: v["points_balance"])  # 并发多轮兑换需要充足积分
    batches = client.get("/api/trainings/batches").json()
    benefits = client.get("/api/benefits/").json()
    cheap = sorted(benefits, key=lambda b: b["points_cost"])[0]
    return {
        "training_v": training_v,
        "claim_v": claim_v,
        "batch": batches[0],
        "benefit": cheap,
    }


_phone_seq = 0


def unique_phone():
    global _phone_seq
    _phone_seq += 1
    return f"139{_phone_seq:07d}"


def grant(client, vid, phone=None, scopes=None, valid_until=None, name=None):
    phone = phone or unique_phone()
    body = {"volunteer_id": vid, "phone": phone, "name": name or f"家长{phone[-4:]}"}
    if scopes is not None:
        body["scopes"] = scopes
    if valid_until is not None:
        body["valid_until"] = valid_until.isoformat()
    r = client.post(f"{ADMIN}/authorizations", json=body)
    assert r.status_code == 201, r.text
    return phone, r.json()


def revoke(client, auth_id, reason="测试撤回"):
    r = client.post(f"{ADMIN}/authorizations/{auth_id}/revoke", json={"reason": reason})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- 基础鉴权：孩子限定、范围限定、有效期 ----------------

def test_login_only_lists_authorized_children(client, ids):
    phone, auth = grant(client, ids["training_v"]["id"])
    r = client.post(f"{G}/login", json={"phone": phone})
    assert r.status_code == 200
    children = r.json()["children"]
    assert {c["volunteer_id"] for c in children} == {ids["training_v"]["id"]}
    assert set(children[0]["scopes"]) == {"view", "enroll", "benefit"}

    # 未登记手机号登录失败
    r = client.post(f"{G}/login", json={"phone": "13700000000"})
    assert r.status_code == 404


def test_view_requires_authorization_for_each_child(client, ids):
    phone, _ = grant(client, ids["training_v"]["id"])
    ok = client.get(f"{G}/children/{ids['training_v']['id']}/view?phone={phone}")
    assert ok.status_code == 200
    assert ok.json()["volunteer"]["name"] == ids["training_v"]["name"]

    # 同一监护人无权查看其他孩子
    other = client.get(f"{G}/children/{ids['claim_v']['id']}/view?phone={phone}")
    assert other.status_code == 403

    # 未登记手机号拒绝，且审计落库（guardian_id 为空）
    denied = client.get(f"{G}/children/{ids['training_v']['id']}/view?phone=13700000000")
    assert denied.status_code == 403
    db = SessionLocal()
    op = db.query(models.GuardianOperation).filter(
        models.GuardianOperation.guardian_id.is_(None),
        models.GuardianOperation.result == models.OperationResult.DENIED,
    ).first()
    db.close()
    assert op is not None


def test_scope_limitation(client, ids):
    vid = ids["training_v"]["id"]
    phone, auth = grant(client, vid, scopes=["view"])
    assert client.get(f"{G}/children/{vid}/view?phone={phone}").status_code == 200

    r = client.post(f"{G}/children/{vid}/enrollments/confirm?phone={phone}",
                    json={"batch_id": ids["batch"]["id"]})
    assert r.status_code == 403 and "范围" in r.json()["detail"]

    r = client.post(f"{G}/children/{vid}/benefits/claim?phone={phone}",
                    json={"benefit_id": ids["benefit"]["id"], "quantity": 1})
    assert r.status_code == 403

    # 出新版扩权 → 报名确认可用
    r = client.post(f"{ADMIN}/authorizations/{auth['id']}/versions",
                    json={"scopes": ["view", "enroll", "benefit"]})
    assert r.status_code == 201 and r.json()["version_no"] == 2
    r = client.post(f"{G}/children/{vid}/enrollments/confirm?phone={phone}",
                    json={"batch_id": ids["batch"]["id"]})
    assert r.status_code == 200, r.text


def test_validity_period(client, ids):
    vid = ids["training_v"]["id"]
    # 已过期授权：立即失效
    phone, _ = grant(client, vid, valid_until=datetime.utcnow() - timedelta(days=1))
    r = client.get(f"{G}/children/{vid}/view?phone={phone}")
    assert r.status_code == 403
    assert client.post(f"{G}/login", json={"phone": phone}).status_code == 404


# ---------------- 撤回：立即失效 + 历史可解释 ----------------

def test_revoke_immediate_but_history_explained(client, ids):
    vid = ids["training_v"]["id"]
    phone, auth = grant(client, vid)

    ok = client.get(f"{G}/children/{vid}/view?phone={phone}")
    assert ok.status_code == 200
    t_before = datetime.utcnow()

    revoke(client, auth["id"])

    denied = client.get(f"{G}/children/{vid}/view?phone={phone}")
    assert denied.status_code == 403

    # 授权记录保留为已撤回
    r = client.get(f"{ADMIN}/authorizations", params={"volunteer_id": vid, "phone": phone})
    assert r.status_code == 200
    assert r.json()[0]["status"] == "已撤回"

    # 审计：一次成功（带授权版本快照）+ 一次拒绝
    ops = client.get(f"{ADMIN}/operations",
                     params={"volunteer_id": vid, "phone": phone}).json()
    results = [o["result"] for o in ops]
    assert "成功" in results and "已拒绝" in results
    success = next(o for o in ops if o["result"] == "成功")
    assert success["authorization_id"] == auth["id"]
    assert success["auth_version_no"] == 1
    assert success["auth_scopes_snapshot"] == "view,enroll,benefit"

    # 按撤回前时间点还原：该授权当时有效；按现在还原：无效
    asof = client.get(f"{ADMIN}/as-of",
                      params={"volunteer_id": vid, "ts": t_before.isoformat()}).json()
    assert any(a["id"] == auth["id"] for a in asof["effective_authorizations"])
    asof_now = client.get(f"{ADMIN}/as-of",
                          params={"volunteer_id": vid, "ts": datetime.utcnow().isoformat()}).json()
    assert not any(a["id"] == auth["id"] for a in asof_now["effective_authorizations"])


def test_version_chain_and_supersede(client, ids):
    vid = ids["training_v"]["id"]
    phone, v1 = grant(client, vid, scopes=["view"])
    r = client.post(f"{ADMIN}/authorizations/{v1['id']}/versions",
                    json={"scopes": ["view", "enroll"]})
    v2 = r.json()
    assert v2["grant_seq"] == v1["grant_seq"] and v2["version_no"] == 2

    versions = client.get(f"{ADMIN}/authorizations",
                          params={"volunteer_id": vid, "phone": phone}).json()
    old = next(a for a in versions if a["id"] == v1["id"])
    assert old["status"] == "已被新版本取代" and old["superseded_by"] == v2["id"]

    # 旧版本在其存活期内仍可作为依据被还原
    asof = client.get(f"{ADMIN}/as-of",
                      params={"volunteer_id": vid, "ts": v1["valid_from"]}).json()
    assert any(a["id"] == v1["id"] for a in asof["effective_authorizations"])


# ---------------- 共同监护 & 交接 ----------------

def test_joint_guardianship_independent_revoke(client, ids):
    vid = ids["training_v"]["id"]
    phone_a, auth_a = grant(client, vid, name="父亲")
    phone_b, auth_b = grant(client, vid, name="母亲")  # 共同监护

    assert client.get(f"{G}/children/{vid}/view?phone={phone_a}").status_code == 200
    assert client.get(f"{G}/children/{vid}/view?phone={phone_b}").status_code == 200

    revoke(client, auth_a["id"], reason="更换监护人")
    assert client.get(f"{G}/children/{vid}/view?phone={phone_a}").status_code == 403
    # 另一方不受影响
    assert client.get(f"{G}/children/{vid}/view?phone={phone_b}").status_code == 200


def test_duplicate_grant_conflict(client, ids):
    vid = ids["training_v"]["id"]
    phone, _ = grant(client, vid)
    r = client.post(f"{ADMIN}/authorizations",
                    json={"volunteer_id": vid, "phone": phone, "scopes": ["view"]})
    assert r.status_code == 409


def test_handover_atomic(client, ids):
    vid = ids["training_v"]["id"]
    old_phone, old_auth = grant(client, vid, name="原监护人")
    assert client.get(f"{G}/children/{vid}/view?phone={old_phone}").status_code == 200

    new_phone = unique_phone()
    r = client.post(f"{ADMIN}/volunteers/{vid}/handover", json={
        "to_phone": new_phone, "to_name": "新监护人",
        "scopes": ["view", "enroll", "benefit"], "reason": "监护权交接",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert old_auth["id"] in body["revoked_authorization_ids"]
    new_auth = body["new_authorization"]
    assert new_auth["version_no"] == 1 and new_auth["grant_seq"] != old_auth["grant_seq"]

    # 原监护人立即失效，新监护人立即可用
    assert client.get(f"{G}/children/{vid}/view?phone={old_phone}").status_code == 403
    assert client.get(f"{G}/children/{vid}/view?phone={new_phone}").status_code == 200

    # 原监护人撤回前的成功操作仍挂在旧版授权上，可解释
    ops = client.get(f"{ADMIN}/operations",
                     params={"volunteer_id": vid, "phone": old_phone}).json()
    early = next(o for o in ops if o["result"] == "成功")
    assert early["authorization_id"] == old_auth["id"]


# ---------------- 重复提交：不两次生效 ----------------

def test_duplicate_enrollment_idempotent(client, ids):
    # 新建一个培训期次，保证孩子尚未报名
    vid = ids["training_v"]["id"]
    phone, _ = grant(client, vid, scopes=["view", "enroll"])
    topic_id = client.get("/api/assessments/topics").json()[0]["id"]
    batch = client.post("/api/trainings/batches", json={
        "name": f"幂等测试期次-{vid}",
        "topic_id": topic_id,
        "min_attendance_rate": 70.0,
        "capacity": 10,
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
    }).json()

    key = f"enroll-{vid}-{batch['id']}"
    payload = {"batch_id": batch["id"], "idempotency_key": key}
    r1 = client.post(f"{G}/children/{vid}/enrollments/confirm?phone={phone}", json=payload)
    r2 = client.post(f"{G}/children/{vid}/enrollments/confirm?phone={phone}", json=payload)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["idempotent"] is False and r2.json()["idempotent"] is True
    assert r1.json()["enrollment_id"] == r2.json()["enrollment_id"]

    # 不带幂等键的重复报名同样不产生第二条
    r3 = client.post(f"{G}/children/{vid}/enrollments/confirm?phone={phone}",
                     json={"batch_id": batch["id"]})
    assert r3.status_code == 200 and r3.json()["idempotent"] is True

    db = SessionLocal()
    cnt = db.query(models.Enrollment).filter_by(volunteer_id=vid, batch_id=batch["id"]).count()
    dup_ops = db.query(models.GuardianOperation).filter(
        models.GuardianOperation.volunteer_id == vid,
        models.GuardianOperation.operation_type == models.OperationType.ENROLL_CONFIRM,
        models.GuardianOperation.result == models.OperationResult.SUCCESS,
        models.GuardianOperation.idempotency_key == key,
    ).count()
    db.close()
    assert cnt == 1 and dup_ops == 1  # 只入班一次、成功只记一次


def test_duplicate_benefit_claim_idempotent(client, ids):
    vid = ids["claim_v"]["id"]
    benefit = ids["benefit"]
    phone, _ = grant(client, vid, scopes=["benefit"])
    before = client.get(f"/api/points/volunteer/{vid}").json()["points_balance"]

    key = f"claim-{vid}-{benefit['id']}-1"
    payload = {"benefit_id": benefit["id"], "quantity": 1, "idempotency_key": key}
    r1 = client.post(f"{G}/children/{vid}/benefits/claim?phone={phone}", json=payload)
    r2 = client.post(f"{G}/children/{vid}/benefits/claim?phone={phone}", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["idempotent"] is False and r2.json()["idempotent"] is True
    assert r1.json()["exchange_id"] == r2.json()["exchange_id"]

    after = client.get(f"/api/points/volunteer/{vid}").json()["points_balance"]
    assert before - after == benefit["points_cost"]  # 只扣一次


def test_business_failure_audited(client, ids):
    vid = ids["claim_v"]["id"]
    phone, _ = grant(client, vid, scopes=["benefit"])
    r = client.post(f"{G}/children/{vid}/benefits/claim?phone={phone}",
                    json={"benefit_id": 999999, "quantity": 1})
    assert r.status_code == 400
    ops = client.get(f"{ADMIN}/operations",
                     params={"volunteer_id": vid, "result": "业务未通过"}).json()
    assert ops and "权益不存在" in ops[0]["detail"]


# ---------------- 并发：撤回 vs 代办 / 重复代办 ----------------

def test_concurrent_revoke_vs_claim(client, ids):
    vid = ids["claim_v"]["id"]
    benefit = ids["benefit"]

    outcomes = []

    def one_round(round_no):
        phone = unique_phone()
        _, auth = grant(client, vid, phone=phone, scopes=["benefit"], name=f"并发家长{round_no}")
        barrier = threading.Barrier(2)
        results = {}

        def revoke_side():
            barrier.wait()
            with TestClient(app) as tc:
                results["revoke"] = tc.post(
                    f"{ADMIN}/authorizations/{auth['id']}/revoke",
                    json={"reason": "并发撤回"},
                ).status_code

        def claim_side():
            barrier.wait()
            with TestClient(app) as tc:
                r = tc.post(f"{G}/children/{vid}/benefits/claim?phone={phone}",
                            json={"benefit_id": benefit["id"], "quantity": 1,
                                  "idempotency_key": f"cc-{round_no}"})
                results["claim_code"] = r.status_code
                results["claim_body"] = r.json() if r.status_code == 200 else None

        t1 = threading.Thread(target=revoke_side)
        t2 = threading.Thread(target=claim_side)
        t1.start(); t2.start(); t1.join(); t2.join()
        outcomes.append((auth["id"], phone, results))

    # 连续 5 轮，制造撤回与代办的真实竞争
    for i in range(5):
        one_round(i)

    db = SessionLocal()
    try:
        success_count = 0
        created_exchange_ids = []
        for auth_id, phone, res in outcomes:
            assert res["revoke"] == 200
            code = res["claim_code"]
            assert code in (200, 403)
            auth = db.get(models.GuardianAuthorization, auth_id)
            assert auth.status == models.AuthorizationStatus.REVOKED
            revoked_at = auth.revoked_at

            g = db.query(models.Guardian).filter_by(phone=phone).first()
            ops = db.query(models.GuardianOperation).filter(
                models.GuardianOperation.guardian_id == g.id,
                models.GuardianOperation.operation_type == models.OperationType.BENEFIT_CLAIM,
            ).all()
            if code == 200:
                success_count += 1
                # 成功的代办必须确实发生在撤回生效之前
                op = next(o for o in ops if o.result == models.OperationResult.SUCCESS)
                assert op.operated_at < revoked_at
                # 且兑换记录真实存在、积分只扣一次
                ex_id = res["claim_body"]["exchange_id"]
                created_exchange_ids.append(ex_id)
                ex = db.get(models.BenefitExchange, ex_id)
                assert ex is not None and ex.points_spent == benefit["points_cost"]
            else:
                # 撤回先生效 → 必须拒绝，不得产生兑换
                assert all(o.result == models.OperationResult.DENIED for o in ops)

        # 每一轮成功代办最多产生一笔兑换，且彼此不同（不两次生效）
        assert len(created_exchange_ids) == len(set(created_exchange_ids)) == success_count
    finally:
        db.close()


def test_concurrent_duplicate_claims_single_effect(client, ids):
    vid = ids["claim_v"]["id"]
    benefit = ids["benefit"]
    phone, _ = grant(client, vid, scopes=["benefit"])
    key = f"concurrent-dup-{vid}-{benefit['id']}"
    barrier = threading.Barrier(2)
    responses = []

    def claim():
        barrier.wait()
        with TestClient(app) as tc:
            r = tc.post(f"{G}/children/{vid}/benefits/claim?phone={phone}",
                        json={"benefit_id": benefit["id"], "quantity": 1,
                              "idempotency_key": key})
            responses.append(r)

    t1 = threading.Thread(target=claim)
    t2 = threading.Thread(target=claim)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert [r.status_code for r in responses] == [200, 200]
    bodies = [r.json() for r in responses]
    assert [b["idempotent"] for b in bodies] == [True, False] or \
           [b["idempotent"] for b in bodies] == [False, True]
    exchange_ids = {b["exchange_id"] for b in bodies}
    assert len(exchange_ids) == 1  # 只产生一笔兑换

    db = SessionLocal()
    try:
        # 唯一幂等键只允许一条成功流水
        success = db.query(models.GuardianOperation).filter(
            models.GuardianOperation.idempotency_key == key,
            models.GuardianOperation.result == models.OperationResult.SUCCESS,
        ).count()
        # 另一条请求以"重复提交"留痕，指向同一笔兑换
        duplicate = db.query(models.GuardianOperation).filter(
            models.GuardianOperation.target_type == "benefit_exchange",
            models.GuardianOperation.target_id == str(exchange_ids.pop()),
            models.GuardianOperation.result == models.OperationResult.DUPLICATE,
        ).count()
        assert success == 1 and duplicate == 1
    finally:
        db.close()


# ---------------- 时间点还原 ----------------

def test_as_of_reconstruction_who_what_which_version(client, ids):
    vid = ids["training_v"]["id"]
    phone, v1 = grant(client, vid, scopes=["view", "enroll"], name="还原测试家长")
    client.get(f"{G}/children/{vid}/view?phone={phone}")  # 凭 v1 查看
    t_after_first_view = datetime.utcnow()

    client.post(f"{ADMIN}/authorizations/{v1['id']}/versions",
                json={"scopes": ["view", "enroll", "benefit"]})
    client.get(f"{G}/children/{vid}/view?phone={phone}")  # 凭 v2 查看
    t_after_v2 = datetime.utcnow()

    r = client.get(f"{ADMIN}/as-of",
                   params={"volunteer_id": vid, "ts": t_after_first_view.isoformat()})
    assert r.status_code == 200
    snap = r.json()
    eff = snap["effective_authorizations"]
    assert any(a["id"] == v1["id"] and a["version_no"] == 1 for a in eff)
    # 当时该监护人名下只有 v1 下的操作（按手机号过滤，排除同孩子其他共同监护人）
    mine = [o for o in snap["operations"] if o["guardian_phone"] == phone]
    assert mine and all(o["auth_version_no"] in (1, None) for o in mine)

    snap2 = client.get(f"{ADMIN}/as-of",
                       params={"volunteer_id": vid, "ts": t_after_v2.isoformat()}).json()
    eff2 = snap2["effective_authorizations"]
    assert not any(a["id"] == v1["id"] for a in eff2)
    versions_seen = {o["auth_version_no"] for o in snap2["operations"]
                     if o["guardian_phone"] == phone and o["result"] == "成功"}
    assert versions_seen == {1, 2}  # 谁凭哪一版办过什么，逐一对得上


# ---------------- 遗留家长入口：投诉场景回归 ----------------

def test_legacy_parent_entry_blocked_after_revoke(client, ids):
    """更换监护人撤回授权后，旧版 /api/parents 入口同样立即失效。"""
    vid = ids["training_v"]["id"]
    phone, auth = grant(client, vid)

    r = client.post("/api/parents/login", json={"parent_phone": phone})
    assert r.status_code == 200 and r.json()["volunteers"][0]["id"] == vid
    assert client.get(f"/api/parents/volunteer/{vid}?parent_phone={phone}").status_code == 200

    revoke(client, auth["id"], reason="更换监护人")

    assert client.post("/api/parents/login", json={"parent_phone": phone}).status_code == 404
    assert client.get(f"/api/parents/volunteer/{vid}?parent_phone={phone}").status_code == 403
    # 无关手机号同样被拒
    assert client.get(f"/api/parents/volunteer/{vid}?parent_phone=13700000000").status_code == 403
