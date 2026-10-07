"""
监护授权核心服务。

设计要点：
1. 授权以"版本"为单位（GuardianAuthorization），只追加、不改写；撤回只置状态，
   历史版本永久保留，撤回前完成的操作始终能解释其依据。
2. 所有鉴权与写操作都在 SQLite 的 BEGIN IMMEDIATE 事务中进行：写事务全局串行，
   配合重试，保证"撤回"与"代办"并发时只能有一方生效，且重复提交不会两次生效。
3. 每次监护人操作（含被拒绝的请求）写一条 GuardianOperation 审计流水，
   内嵌授权快照（版本号/范围/有效期），支持按任意时间点还原。
"""
import random
import time
from datetime import datetime
from typing import Callable, Optional, List, Tuple

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from database import SessionLocal
import models

SCOPE_VIEW = "view"
SCOPE_ENROLL = "enroll"
SCOPE_BENEFIT = "benefit"
ALL_SCOPES = [SCOPE_VIEW, SCOPE_ENROLL, SCOPE_BENEFIT]
SCOPE_LABELS = {
    SCOPE_VIEW: "查看孩子信息",
    SCOPE_ENROLL: "报名确认",
    SCOPE_BENEFIT: "权益代领",
}

_LOCK_MARKERS = ("database is locked", "database table is locked")


class AuthzError(Exception):
    def __init__(self, reason: str, status_code: int = 403):
        self.reason = reason
        self.status_code = status_code
        super().__init__(reason)


def parse_scopes(raw) -> List[str]:
    if not raw:
        return []
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def join_scopes(scopes: List[str]) -> str:
    invalid = [s for s in scopes if s not in ALL_SCOPES]
    if invalid:
        raise HTTPException(status_code=422, detail=f"未知授权范围: {invalid}，可选 {ALL_SCOPES}")
    if not scopes:
        raise HTTPException(status_code=422, detail="授权范围不能为空")
    return ",".join(scopes)


def with_immediate(fn: Callable, *, retries: int = 30, base_delay: float = 0.03,
                   commit_exc: Tuple[type, ...] = ()):
    """
    在 BEGIN IMMEDIATE 写事务中执行 fn(session)；锁冲突时整体重试。
    commit_exc 中的异常属于"预期结果"（如鉴权拒绝）：先提交已写入的审计流水再抛出。
    """
    last_err = None
    for attempt in range(retries + 1):
        db = SessionLocal()
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            result = fn(db)
            db.commit()
            return result
        except OperationalError as e:
            db.rollback()
            msg = str(e)
            if any(m in msg for m in _LOCK_MARKERS) and attempt < retries:
                last_err = e
                time.sleep(base_delay * (attempt + 1) + random.uniform(0, base_delay))
                continue
            raise
        except commit_exc:
            # 拒绝流水需要保留：提交后向上抛出
            db.commit()
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    raise last_err  # pragma: no cover


def _get_guardian_by_phone(db, phone: str):
    return db.query(models.Guardian).filter(models.Guardian.phone == phone).first()


def get_or_create_guardian(db, phone: str, name: str = None,
                           relation=None, id_card_no: str = None):
    g = _get_guardian_by_phone(db, phone)
    if not g:
        g = models.Guardian(
            name=name or f"监护人{phone[-4:]}",
            phone=phone,
            relation=relation or models.GuardianRelation.PARENT,
            id_card_no=id_card_no,
        )
        db.add(g)
        db.flush()
    return g


def _lazy_expire(db, now: datetime):
    """把已过有效期但仍标记为 ACTIVE 的版本转为 EXPIRED（在写事务内执行）。"""
    stale = db.query(models.GuardianAuthorization).filter(
        models.GuardianAuthorization.status == models.AuthorizationStatus.ACTIVE,
        models.GuardianAuthorization.valid_until.isnot(None),
        models.GuardianAuthorization.valid_until <= now,
    ).all()
    for a in stale:
        a.status = models.AuthorizationStatus.EXPIRED


def _effective_end(auth: models.GuardianAuthorization) -> Optional[datetime]:
    """版本实际失效时刻：有效期上限 / 撤回时刻 / 被新版本取代时刻，取最早。"""
    end = auth.valid_until
    if auth.status == models.AuthorizationStatus.REVOKED and auth.revoked_at:
        end = auth.revoked_at if end is None else min(end, auth.revoked_at)
    if auth.superseded_by and auth.successor:
        end = auth.successor.valid_from if end is None else min(end, auth.successor.valid_from)
    return end


def is_effective_at(auth, ts: datetime, scope: str = None) -> bool:
    if not auth.valid_from or auth.valid_from > ts:
        return False
    end = _effective_end(auth)
    if end is not None and end <= ts:
        return False
    if scope and scope not in parse_scopes(auth.scopes):
        return False
    return True


def _record_operation(db, *, volunteer_id: int, guardian_id, op_type, result,
                      authorization=None, idempotency_key: str = None,
                      target_type: str = None, target_id=None, detail: str = None,
                      operated_at: datetime = None):
    row = models.GuardianOperation(
        guardian_id=guardian_id,
        volunteer_id=volunteer_id,
        authorization_id=authorization.id if authorization else None,
        operation_type=op_type,
        result=result,
        idempotency_key=idempotency_key,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=detail,
        auth_version_no=authorization.version_no if authorization else None,
        auth_scopes_snapshot=authorization.scopes if authorization else None,
        auth_valid_from=authorization.valid_from if authorization else None,
        auth_valid_until=authorization.valid_until if authorization else None,
        operated_at=operated_at or datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def authorize(db, *, phone: str, volunteer_id: int, scope: str, op_type,
              target_type: str = None, target_id=None):
    """
    在当前 IMMEDIATE 事务内鉴权。
    通过 -> (guardian, auth, now)；失败 -> 写入 DENIED 审计并抛 AuthzError。
    成功的审计流水由调用方在业务落定后用 record_* 写入，确保 result 反映真实结局。
    """
    now = datetime.utcnow()
    _lazy_expire(db, now)
    guardian = _get_guardian_by_phone(db, phone)

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise AuthzError("孩子不存在", 404)

    def deny(reason):
        _record_operation(
            db, volunteer_id=volunteer_id, guardian_id=guardian.id if guardian else None,
            op_type=op_type, result=models.OperationResult.DENIED,
            target_type=target_type, target_id=target_id,
            detail=f"拒绝原因:{reason}；手机号:{phone}",
            operated_at=now,
        )
        raise AuthzError(reason)

    if not guardian:
        deny("未登记的监护人手机号")

    auths = db.query(models.GuardianAuthorization).filter(
        models.GuardianAuthorization.guardian_id == guardian.id,
        models.GuardianAuthorization.volunteer_id == volunteer_id,
    ).all()
    effective = [a for a in auths if is_effective_at(a, now, scope)]
    if not effective:
        deny(f"授权不存在、已撤回/过期或不包含范围[{SCOPE_LABELS.get(scope, scope)}]")
    # 同一条授权链同一时刻只有一个有效版本；并存授权链时取最新版本
    effective.sort(key=lambda a: (a.version_no, a.valid_from), reverse=True)
    return guardian, effective[0], now


def record_success(db, *, guardian, auth, volunteer_id, op_type, now=None,
                   idempotency_key=None, target_type=None, target_id=None, detail=None):
    return _record_operation(
        db, volunteer_id=volunteer_id, guardian_id=guardian.id,
        op_type=op_type, result=models.OperationResult.SUCCESS, authorization=auth,
        idempotency_key=idempotency_key, target_type=target_type, target_id=target_id,
        detail=detail, operated_at=now,
    )


def record_duplicate(db, *, guardian, auth, volunteer_id, op_type, prior=None, now=None,
                     target_type=None, target_id=None, detail=None):
    if detail is None:
        if prior is not None:
            detail = f"重复提交，与操作#{prior.id}（结果:{prior.result.value if prior.result else '?'}）幂等命中，未再次生效"
        else:
            detail = "重复提交，业务已处于完成状态，未再次生效"
    return _record_operation(
        db, volunteer_id=volunteer_id, guardian_id=guardian.id,
        op_type=op_type, result=models.OperationResult.DUPLICATE, authorization=auth,
        target_type=target_type, target_id=target_id,
        detail=detail,
        operated_at=now,
    )


def record_business_failed(db, *, guardian, auth, volunteer_id, op_type, reason, now=None,
                           target_type=None, target_id=None):
    return _record_operation(
        db, volunteer_id=volunteer_id, guardian_id=guardian.id,
        op_type=op_type, result=models.OperationResult.BUSINESS_FAILED, authorization=auth,
        target_type=target_type, target_id=target_id,
        detail=f"授权有效但业务校验未通过:{reason}",
        operated_at=now,
    )


def find_idempotent(db, key: str):
    if not key:
        return None
    return db.query(models.GuardianOperation).filter(
        models.GuardianOperation.idempotency_key == key
    ).first()


# ==================== 授权管理（版本化） ====================

def grant_authorization(db, *, volunteer_id: int, phone: str, name: str = None,
                        scopes: List[str], valid_until: datetime = None,
                        relation=None, valid_from: datetime = None):
    """发起一条全新授权链（grant_seq 分配在串行写事务内，单调递增）。"""
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="孩子不存在")
    guardian = get_or_create_guardian(db, phone, name, relation)

    active = db.query(models.GuardianAuthorization).filter(
        models.GuardianAuthorization.guardian_id == guardian.id,
        models.GuardianAuthorization.volunteer_id == volunteer_id,
        models.GuardianAuthorization.status == models.AuthorizationStatus.ACTIVE,
    ).all()
    now = datetime.utcnow()
    active = [a for a in active if is_effective_at(a, now)]
    if active:
        raise HTTPException(status_code=409, detail="该监护人对此孩子已有有效授权，如需变更范围/有效期请创建新版本")

    next_seq = (db.query(
        models.GuardianAuthorization.grant_seq
    ).filter(
        models.GuardianAuthorization.volunteer_id == volunteer_id
    ).order_by(
        models.GuardianAuthorization.grant_seq.desc()
    ).first() or (0,))[0] + 1

    auth = models.GuardianAuthorization(
        grant_seq=next_seq,
        version_no=1,
        guardian_id=guardian.id,
        volunteer_id=volunteer_id,
        relation=guardian.relation,
        scopes=join_scopes(scopes),
        valid_from=valid_from or now,
        valid_until=valid_until,
        status=models.AuthorizationStatus.ACTIVE,
    )
    db.add(auth)
    db.flush()
    return auth


def new_version(db, *, authorization_id: int, scopes: List[str] = None,
                valid_until: datetime = None):
    """在同一授权链上出新版本（变更范围/续期），旧版本置为"已被新版本取代"。"""
    old = db.query(models.GuardianAuthorization).filter(
        models.GuardianAuthorization.id == authorization_id
    ).first()
    if not old:
        raise HTTPException(status_code=404, detail="授权版本不存在")
    if old.status != models.AuthorizationStatus.ACTIVE or not is_effective_at(old, datetime.utcnow()):
        raise HTTPException(status_code=409, detail="只能为当前有效的版本创建新版本")

    new = models.GuardianAuthorization(
        grant_seq=old.grant_seq,
        version_no=old.version_no + 1,
        guardian_id=old.guardian_id,
        volunteer_id=old.volunteer_id,
        relation=old.relation,
        scopes=join_scopes(scopes) if scopes is not None else old.scopes,
        valid_from=datetime.utcnow(),
        valid_until=valid_until,
        status=models.AuthorizationStatus.ACTIVE,
    )
    db.add(new)
    db.flush()
    old.status = models.AuthorizationStatus.SUPERSEDED
    old.superseded_by = new.id
    return new


def revoke(db, *, authorization_id: int = None, guardian_id: int = None,
           volunteer_id: int = None, reason: str = None) -> List[int]:
    """
    撤回授权：撤回后新请求立即失效；撤回前已完成操作的历史不变。
    可按版本撤回，或按"监护人+孩子"撤回其当前有效版本。
    """
    now = datetime.utcnow()
    _lazy_expire(db, now)
    q = db.query(models.GuardianAuthorization).filter(
        models.GuardianAuthorization.status == models.AuthorizationStatus.ACTIVE
    )
    if authorization_id is not None:
        q = q.filter(models.GuardianAuthorization.id == authorization_id)
    else:
        if guardian_id is None or volunteer_id is None:
            raise ValueError("撤回需提供 authorization_id 或 guardian_id+volunteer_id")
        q = q.filter(
            models.GuardianAuthorization.guardian_id == guardian_id,
            models.GuardianAuthorization.volunteer_id == volunteer_id,
        )
    actives = q.all()
    actives = [a for a in actives if is_effective_at(a, now)]
    if not actives:
        raise HTTPException(status_code=404, detail="没有可撤回的有效授权")
    ids = []
    for a in actives:
        a.status = models.AuthorizationStatus.REVOKED
        a.revoked_at = now
        a.revoke_reason = reason
        ids.append(a.id)
    return ids


def handover(db, *, volunteer_id: int, to_phone: str, to_name: str = None,
             scopes: List[str], valid_until: datetime = None, relation=None,
             from_guardian_id: int = None, reason: str = "监护人交接"):
    """
    监护人交接：在同一事务内撤回原监护人的有效授权，并为新监护人立一条全新授权链。
    原子完成，不会出现新旧双方同时有权限、也不会出现权限空档之外的越权。
    """
    now = datetime.utcnow()
    _lazy_expire(db, now)

    q = db.query(models.GuardianAuthorization).filter(
        models.GuardianAuthorization.volunteer_id == volunteer_id,
        models.GuardianAuthorization.status == models.AuthorizationStatus.ACTIVE,
    )
    if from_guardian_id is not None:
        q = q.filter(models.GuardianAuthorization.guardian_id == from_guardian_id)
    revoked_ids = []
    for a in q.all():
        if not is_effective_at(a, now):
            continue
        if a.guardian and a.guardian.phone == to_phone:
            continue  # 目标监护人已有的有效授权保留
        a.status = models.AuthorizationStatus.REVOKED
        a.revoked_at = now
        a.revoke_reason = reason
        revoked_ids.append(a.id)

    new_auth = grant_authorization(
        db, volunteer_id=volunteer_id, phone=to_phone, name=to_name,
        scopes=scopes, valid_until=valid_until, relation=relation,
    )
    return new_auth, revoked_ids


def list_children_for_phone(db, phone: str, now: datetime = None):
    """登录：仅返回此刻有有效授权的孩子及范围。"""
    now = now or datetime.utcnow()
    guardian = _get_guardian_by_phone(db, phone)
    if not guardian:
        return []
    rows = db.query(models.GuardianAuthorization).filter(
        models.GuardianAuthorization.guardian_id == guardian.id
    ).all()
    by_child = {}
    for a in rows:
        if not is_effective_at(a, now):
            continue
        cur = by_child.get(a.volunteer_id)
        if cur is None or a.version_no > cur.version_no:
            by_child[a.volunteer_id] = a
    out = []
    for vid, auth in by_child.items():
        v = db.query(models.Volunteer).filter(models.Volunteer.id == vid).first()
        out.append({
            "volunteer_id": vid,
            "name": v.name if v else None,
            "school_name": v.school.name if v and v.school else None,
            "grade": v.grade if v else None,
            "authorization_id": auth.id,
            "version_no": auth.version_no,
            "scopes": parse_scopes(auth.scopes),
            "valid_from": auth.valid_from,
            "valid_until": auth.valid_until,
        })
    return out


# ==================== 审计查询与时间点还原 ====================

def query_operations(db, *, volunteer_id: int = None, guardian_phone: str = None,
                     operation_type=None, result=None,
                     start: datetime = None, end: datetime = None,
                     as_of: datetime = None, skip: int = 0, limit: int = 200):
    q = db.query(models.GuardianOperation)
    if volunteer_id is not None:
        q = q.filter(models.GuardianOperation.volunteer_id == volunteer_id)
    if guardian_phone:
        g = _get_guardian_by_phone(db, guardian_phone)
        if not g:
            return []
        q = q.filter(models.GuardianOperation.guardian_id == g.id)
    if operation_type is not None:
        q = q.filter(models.GuardianOperation.operation_type == operation_type)
    if result is not None:
        q = q.filter(models.GuardianOperation.result == result)
    if start is not None:
        q = q.filter(models.GuardianOperation.operated_at >= start)
    if end is not None:
        q = q.filter(models.GuardianOperation.operated_at <= end)
    if as_of is not None:
        q = q.filter(models.GuardianOperation.operated_at <= as_of)
    return q.order_by(
        models.GuardianOperation.operated_at.desc(),
        models.GuardianOperation.id.desc(),
    ).offset(skip).limit(limit).all()


def effective_authorizations_at(db, ts: datetime, volunteer_id: int = None):
    """返回某时间点对某孩子（或全部）生效的授权版本——即当时"谁有权"。"""
    q = db.query(models.GuardianAuthorization)
    if volunteer_id is not None:
        q = q.filter(models.GuardianAuthorization.volunteer_id == volunteer_id)
    return [a for a in q.all() if is_effective_at(a, ts)]


def authorization_versions(db, *, volunteer_id: int = None, guardian_id: int = None,
                           phone: str = None):
    q = db.query(models.GuardianAuthorization)
    if volunteer_id is not None:
        q = q.filter(models.GuardianAuthorization.volunteer_id == volunteer_id)
    if guardian_id is not None:
        q = q.filter(models.GuardianAuthorization.guardian_id == guardian_id)
    if phone:
        g = _get_guardian_by_phone(db, phone)
        if not g:
            return []
        q = q.filter(models.GuardianAuthorization.guardian_id == g.id)
    return q.order_by(
        models.GuardianAuthorization.volunteer_id,
        models.GuardianAuthorization.grant_seq,
        models.GuardianAuthorization.version_no,
    ).all()
