"""
监护关系可追溯授权。

核心设计：
- 授权是“版本化、不可变”的：每次授予产生新版本，撤回只置状态、保留旧版本；
- 每一次监护人发起的查询 / 报名确认 / 权益代领，都在事务内重新校验“当时有效”的授权版本，
  并冻结授权版本号、监护人快照写入审计流水；
- 写事务使用 BEGIN IMMEDIATE 串行化，撤回与代办并发时二者只会有一个生效；
- 监护人提交携带幂等键，重复提交只生效一次，重复请求回放首次结果；
- 管理接口可按任意时间点还原有效授权与操作流水。
"""
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

import models
import schemas
from database import SessionLocal, get_db

SCOPE_VALUES = {s.value for s in models.GuardianshipScope}

# 监护人自助入口
guardian_router = APIRouter(prefix="/api/guardians", tags=["监护授权-监护人入口"])
# 运营管理入口
admin_router = APIRouter(prefix="/api/guardianships", tags=["监护授权-运营管理"])


# ==================== 事务与通用工具 ====================

@contextmanager
def immediate_transaction():
    """BEGIN IMMEDIATE：立即取得 SQLite 写锁，使“授权校验 + 业务写入 + 审计”原子串行。"""
    db = SessionLocal()
    db.execute(text("BEGIN IMMEDIATE"))
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()


def parse_scopes(scopes: List[str]) -> List[str]:
    if not scopes:
        raise HTTPException(status_code=422, detail="至少需要授予一个操作范围")
    invalid = [s for s in scopes if s not in SCOPE_VALUES]
    if invalid:
        raise HTTPException(status_code=422, detail=f"非法操作范围: {invalid}，可选: {sorted(SCOPE_VALUES)}")
    return scopes


def get_guardian_by_phone(db: Session, phone: str) -> Optional[models.Guardian]:
    return db.query(models.Guardian).filter(models.Guardian.phone == phone).first()


def get_or_create_guardian(db: Session, phone: str, name: Optional[str]) -> models.Guardian:
    guardian = get_guardian_by_phone(db, phone)
    if not guardian:
        guardian = models.Guardian(name=name or "未登记监护人", phone=phone)
        db.add(guardian)
        db.flush()
    elif name and guardian.name != name:
        guardian.name = name  # 补全/更正姓名，不改变身份（以手机号为准）
    return guardian


def find_idempotent(db: Session, guardian_id: int, key: Optional[str]):
    """若同一幂等键已成功生效，返回首次审计记录（用于回放，绝不二次生效）。"""
    if not key:
        return None
    return db.query(models.GuardianshipAudit).filter(
        models.GuardianshipAudit.guardian_id == guardian_id,
        models.GuardianshipAudit.idempotency_key == key,
        models.GuardianshipAudit.result == "成功"
    ).first()


def write_audit(db: Session, *, auth: Optional[models.GuardianshipAuthorization],
                guardian: Optional[models.Guardian], volunteer_id: int,
                action: models.AuditAction, result: str = "成功",
                detail: str = None, ref_type: str = None, ref_id=None,
                idempotency_key: str = None, at: datetime = None) -> models.GuardianshipAudit:
    audit = models.GuardianshipAudit(
        authorization_id=auth.id if auth else None,
        guardian_id=guardian.id if guardian else None,
        volunteer_id=volunteer_id,
        action=action,
        auth_version=auth.version if auth else None,
        guardian_name_snapshot=guardian.name if guardian else None,
        guardian_phone_snapshot=guardian.phone if guardian else None,
        scopes_snapshot=auth.scopes_json if auth else None,
        result=result,
        detail=detail,
        ref_type=ref_type,
        ref_id=str(ref_id) if ref_id is not None else None,
        idempotency_key=idempotency_key,
        operated_at=at or datetime.utcnow(),
    )
    db.add(audit)
    db.flush()
    return audit


class AuthorizationError(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def effective_authorization(db: Session, guardian_id: int, volunteer_id: int,
                            at: datetime) -> Optional[models.GuardianshipAuthorization]:
    """
    返回某监护链在 at 时刻的“决定版本”：valid_from <= at 的最高版本（无论其状态）。
    该版本自身的撤回/有效期决定整条链此刻是否授权；旧版本不单独生效。
    """
    return db.query(models.GuardianshipAuthorization).filter(
        models.GuardianshipAuthorization.guardian_id == guardian_id,
        models.GuardianshipAuthorization.volunteer_id == volunteer_id,
        models.GuardianshipAuthorization.valid_from <= at,
    ).order_by(models.GuardianshipAuthorization.version.desc()).first()


def version_grants_at(auth: models.GuardianshipAuthorization, at: datetime) -> bool:
    """决定版本在 at 时刻是否实际放行：未撤回且未过有效期。"""
    if auth is None:
        return False
    if auth.status == models.AuthorizationStatus.REVOKED:
        return False
    if auth.valid_until is not None and at >= auth.valid_until:
        return False
    return True


def require_authorization(db: Session, phone: str, volunteer_id: int,
                          required_scope: models.GuardianshipScope,
                          at: datetime = None) -> tuple[models.Guardian, models.GuardianshipAuthorization]:
    """
    校验该手机号监护人此刻对该孩子拥有指定操作范围的“当时有效授权版本”。

    取在 at 时刻已经生效（valid_from <= at）的“最高版本”作为授权链的当前决定——
    新版本（含撤回/缩范围）一旦生效即覆盖旧版本，旧版本不会再单独授权；
    而 valid_from 在未来的新版本不影响旧版本在当前的效力。
    孩子限定、操作范围、有效期、撤回状态均据此判定；不通过抛 AuthorizationError。
    """
    at = at or datetime.utcnow()
    guardian = get_guardian_by_phone(db, phone)
    if not guardian:
        raise AuthorizationError("未登记的监护人手机号")

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise AuthorizationError("孩子不存在")

    auth = effective_authorization(db, guardian.id, volunteer_id, at)
    if not auth:
        raise AuthorizationError("不存在对该孩子的监护授权")
    if auth.status == models.AuthorizationStatus.REVOKED:
        raise AuthorizationError("监护授权已撤回，新请求立即失效")
    if auth.valid_until is not None and at >= auth.valid_until:
        raise AuthorizationError("监护授权已过有效期")
    if required_scope.value not in auth.scopes:
        raise AuthorizationError(f"当前授权版本(v{auth.version})不含「{required_scope.value}」操作范围")
    return guardian, auth


def deny_and_raise(db: Session, phone: str, volunteer_id: int,
                  required_scope: models.GuardianshipScope, err: AuthorizationError):
    """拒绝也留痕（登记过的监护人），便于投诉复盘。独立提交，确保不被业务回滚带走。"""
    guardian = get_guardian_by_phone(db, phone)
    if guardian is not None:
        write_audit(db, auth=None, guardian=guardian, volunteer_id=volunteer_id,
                    action=models.AuditAction.DENIED, result="拒绝",
                    detail=f"申请「{required_scope.value}」被拒绝：{err.reason}")
        db.commit()
    raise HTTPException(status_code=403, detail=err.reason)


# ==================== 家长视图装配（与旧版家长入口共用） ====================

def build_parent_view(db: Session, volunteer: models.Volunteer) -> dict:
    service_records = db.query(models.ServiceRecord).filter(
        models.ServiceRecord.volunteer_id == volunteer.id
    ).order_by(models.ServiceRecord.service_date.desc()).all()

    parent_service_records = [
        schemas.ParentServiceRecord(
            id=sr.id, service_date=sr.service_date, service_hours=sr.service_hours,
            topic=sr.time_slot.topic if sr.time_slot else None,
            teacher_name=sr.teacher_name, teacher_rating=sr.teacher_rating,
            teacher_comments=sr.teacher_comments, points_awarded=sr.points_awarded or 0
        ) for sr in service_records
    ]

    training_records = []
    for en in db.query(models.Enrollment).filter(
            models.Enrollment.volunteer_id == volunteer.id).all():
        batch = en.batch
        total_sessions = db.query(func.count(models.TrainingSession.id)).filter(
            models.TrainingSession.batch_id == en.batch_id).scalar() or 0
        attended = db.query(func.count(models.SessionAttendance.id)).filter(
            models.SessionAttendance.enrollment_id == en.id,
            models.SessionAttendance.attended == True  # noqa: E712
        ).scalar() or 0
        rate = round(attended / total_sessions * 100, 2) if total_sessions > 0 else None
        min_rate = batch.min_attendance_rate if batch else 80.0
        training_records.append(schemas.ParentTrainingRecord(
            batch_name=batch.name if batch else f"期次{en.batch_id}",
            topic_name=batch.topic.name if batch and batch.topic else None,
            status=en.status, total_sessions=total_sessions, attended_sessions=attended,
            attendance_rate=rate, eligible_for_assessment=(rate or 0) >= min_rate if rate is not None else False
        ))

    assessments = db.query(models.Assessment).filter(
        models.Assessment.volunteer_id == volunteer.id
    ).order_by(models.Assessment.assessment_date.desc()).all()

    certificates = db.query(models.StarCertificate).filter(
        models.StarCertificate.volunteer_id == volunteer.id,
        models.StarCertificate.is_active == True  # noqa: E712
    ).order_by(models.StarCertificate.created_at.desc()).all()

    points_records = db.query(models.PointsRecord).filter(
        models.PointsRecord.volunteer_id == volunteer.id
    ).order_by(models.PointsRecord.created_at.desc()).limit(50).all()

    exchanges = db.query(models.BenefitExchange).filter(
        models.BenefitExchange.volunteer_id == volunteer.id
    ).order_by(models.BenefitExchange.created_at.desc()).all()

    summary = schemas.ParentVolunteerSummary(
        volunteer_id=volunteer.id, name=volunteer.name, status=volunteer.status,
        star_level_name=volunteer.star_level.name if volunteer.star_level else None,
        total_service_hours=volunteer.total_service_hours or 0.0,
        points_balance=volunteer.points_balance or 0,
        registration_date=volunteer.registration_date,
        certification_date=volunteer.certification_date
    )

    view = schemas.ParentView(
        volunteer=summary, service_records=parent_service_records,
        training_records=training_records, certificates=certificates,
        points_records=points_records, exchanges=exchanges
    )
    payload = view.model_dump()
    payload["assessments"] = [
        {
            "id": a.id, "assessment_date": a.assessment_date,
            "topic": a.topic or (a.topic_obj.name if a.topic_obj else None),
            "score": a.score, "result": a.result.value if a.result else None,
            "is_retake": a.is_retake, "attempt_no": a.attempt_no,
            "examiner": a.examiner, "comments": a.comments
        } for a in assessments
    ]
    return payload


def auth_basis(auth: models.GuardianshipAuthorization, guardian: models.Guardian) -> dict:
    return {
        "authorization_id": auth.id,
        "version": auth.version,
        "guardian_id": guardian.id,
        "guardian_name": guardian.name,
        "scopes": sorted(auth.scopes),
        "valid_from": auth.valid_from,
        "valid_until": auth.valid_until,
        "status": auth.status.value,
    }


# ==================== 监护人自助入口 ====================

@guardian_router.post("/login", response_model=schemas.GuardianLoginResult)
def guardian_login(body: schemas.GuardianLogin, db: Session = Depends(get_db)):
    """监护人登录：仅返回当前仍有效授权范围内的孩子；已撤回/过期/被新版本覆盖的孩子不再出现。"""
    guardian = get_guardian_by_phone(db, body.phone)
    if not guardian:
        raise HTTPException(status_code=404, detail="未登记的监护人手机号")
    now = datetime.utcnow()
    children = []
    for auth in db.query(models.GuardianshipAuthorization).filter(
            models.GuardianshipAuthorization.guardian_id == guardian.id).all():
        # 只处理各监护链的“决定版本”，旧版本不单独授予登录可见性
        if effective_authorization(db, guardian.id, auth.volunteer_id, now).id != auth.id:
            continue
        if not version_grants_at(auth, now):
            continue
        if models.GuardianshipScope.QUERY.value not in auth.scopes:
            continue
        v = auth.volunteer
        children.append(schemas.GuardianChildOut(
            volunteer_id=v.id, name=v.name,
            school_name=v.school.name if v.school else None, grade=v.grade,
            authorization_id=auth.id, version=auth.version,
            scopes=sorted(auth.scopes), valid_until=auth.valid_until
        ))
    return schemas.GuardianLoginResult(guardian=guardian, children=children)


@guardian_router.get("/children/{volunteer_id}/view")
def guardian_view(volunteer_id: int, guardian_phone: str, db: Session = Depends(get_db)):
    """凭当前有效「查询」授权查看孩子培训、考核、服务、积分、权益明细，并留痕。"""
    try:
        guardian, auth = require_authorization(
            db, guardian_phone, volunteer_id, models.GuardianshipScope.QUERY)
    except AuthorizationError as e:
        deny_and_raise(db, guardian_phone, volunteer_id, models.GuardianshipScope.QUERY, e)

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    payload = build_parent_view(db, volunteer)
    write_audit(db, auth=auth, guardian=guardian, volunteer_id=volunteer_id,
                action=models.AuditAction.QUERY, detail="查看孩子全景明细", ref_type="view")
    db.commit()
    payload["authorization_basis"] = auth_basis(auth, guardian)
    return payload


@guardian_router.post("/children/{volunteer_id}/enroll-confirm")
def guardian_enroll_confirm(volunteer_id: int, body: schemas.GuardianEnrollConfirm):
    """监护人凭「报名确认」授权代孩子确认入班。串行事务 + 幂等，撤回并发不能越权或两次生效。"""
    with immediate_transaction() as db:
        guardian = get_guardian_by_phone(db, body.guardian_phone)
        if not guardian:
            raise HTTPException(status_code=403, detail="未登记的监护人手机号")

        # 1) 幂等优先：即使授权随后被撤回，已完成操作仍可凭同一键回放
        existing = find_idempotent(db, guardian.id, body.idempotency_key)
        if existing and existing.ref_type == "enrollment":
            enr = db.query(models.Enrollment).filter(
                models.Enrollment.id == int(existing.ref_id)).first()
            return {"replayed": True, "message": "重复提交，仅生效一次",
                    "enrollment_id": enr.id if enr else existing.ref_id,
                    "audit_id": existing.id}
        if existing:
            raise HTTPException(status_code=409,
                                detail="该幂等键已用于其他操作，不能复用")

        # 2) 事务内校验当时有效授权
        try:
            guardian, auth = require_authorization(
                db, body.guardian_phone, volunteer_id,
                models.GuardianshipScope.ENROLL_CONFIRM)
        except AuthorizationError as e:
            deny_and_raise(db, body.guardian_phone, volunteer_id,
                           models.GuardianshipScope.ENROLL_CONFIRM, e)

        # 3) 业务规则（与运营批量入班一致）
        batch = db.query(models.TrainingBatch).filter(
            models.TrainingBatch.id == body.batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="培训期次不存在")
        volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
        if volunteer.status not in (models.VolunteerStatus.IN_TRAINING,
                                    models.VolunteerStatus.PENDING_ASSESSMENT):
            raise HTTPException(status_code=400,
                                detail=f"孩子当前状态({volunteer.status.value})不可报名")
        dup = db.query(models.Enrollment).filter(
            models.Enrollment.batch_id == batch.id,
            models.Enrollment.volunteer_id == volunteer_id).first()
        if dup:
            raise HTTPException(status_code=409, detail="已报名该期次，不能重复报名")
        current = db.query(func.count(models.Enrollment.id)).filter(
            models.Enrollment.batch_id == batch.id,
            models.Enrollment.status == models.EnrollmentStatus.ENROLLED).scalar() or 0
        if current >= batch.capacity:
            raise HTTPException(status_code=400, detail="班额已满")

        # 4) 业务行冻结授权依据
        enrollment = models.Enrollment(
            volunteer_id=volunteer_id, batch_id=batch.id,
            status=models.EnrollmentStatus.ENROLLED,
            authorization_id=auth.id, notes=body.notes)
        db.add(enrollment)
        db.flush()

        write_audit(db, auth=auth, guardian=guardian, volunteer_id=volunteer_id,
                    action=models.AuditAction.ENROLL_CONFIRM,
                    detail=f"监护人确认入班：{batch.name}",
                    ref_type="enrollment", ref_id=enrollment.id,
                    idempotency_key=body.idempotency_key)

        return {"replayed": False, "message": "报名确认成功",
                "enrollment_id": enrollment.id, "batch_id": batch.id,
                "authorization_basis": auth_basis(auth, guardian)}


@guardian_router.post("/children/{volunteer_id}/benefit-claims")
def guardian_benefit_claim(volunteer_id: int, body: schemas.GuardianBenefitClaim):
    """监护人凭「权益代领」授权代孩子兑换/领取权益。扣积分、库存、审计同一串行事务。"""
    with immediate_transaction() as db:
        guardian = get_guardian_by_phone(db, body.guardian_phone)
        if not guardian:
            raise HTTPException(status_code=403, detail="未登记的监护人手机号")

        existing = find_idempotent(db, guardian.id, body.idempotency_key)
        if existing and existing.ref_type == "benefit_exchange":
            ex = db.query(models.BenefitExchange).filter(
                models.BenefitExchange.id == int(existing.ref_id)).first()
            return {"replayed": True, "message": "重复提交，仅生效一次",
                    "exchange_id": ex.id if ex else existing.ref_id,
                    "points_spent": ex.points_spent if ex else None,
                    "audit_id": existing.id}
        if existing:
            raise HTTPException(status_code=409,
                                detail="该幂等键已用于其他操作，不能复用")

        try:
            guardian, auth = require_authorization(
                db, body.guardian_phone, volunteer_id,
                models.GuardianshipScope.BENEFIT_CLAIM)
        except AuthorizationError as e:
            deny_and_raise(db, body.guardian_phone, volunteer_id,
                           models.GuardianshipScope.BENEFIT_CLAIM, e)

        benefit = db.query(models.Benefit).filter(models.Benefit.id == body.benefit_id).first()
        if not benefit:
            raise HTTPException(status_code=404, detail="权益不存在")
        if not benefit.is_active:
            raise HTTPException(status_code=400, detail="该权益已下架")
        if body.quantity <= 0:
            raise HTTPException(status_code=400, detail="兑换数量必须大于0")
        total_points = benefit.points_cost * body.quantity
        if benefit.stock > 0 and benefit.stock < body.quantity:
            raise HTTPException(status_code=400, detail="库存不足")

        volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
        if (volunteer.points_balance or 0) < total_points:
            raise HTTPException(status_code=400, detail="积分不足")

        exchange = models.BenefitExchange(
            volunteer_id=volunteer_id, benefit_id=benefit.id, points_spent=total_points,
            quantity=body.quantity, delivery_info=body.delivery_info,
            status=models.ExchangeStatus.PENDING, authorization_id=auth.id)
        db.add(exchange)
        db.flush()

        # 同一事务内扣减积分（内联，避免 helpers 内部提前 commit 破坏原子性）
        volunteer.points_balance = (volunteer.points_balance or 0) - total_points
        source = (models.PointsSource.EXCHANGE_BADGE
                  if benefit.benefit_type == models.BenefitType.BADGE
                  else models.PointsSource.EXCHANGE_PRIORITY_SLOT)
        db.add(models.PointsRecord(
            volunteer_id=volunteer_id, points_type=models.PointsType.SPEND,
            points_amount=total_points, source=source,
            description=f"监护人代领：{benefit.name} x{body.quantity}",
            exchange_id=exchange.id))
        if benefit.stock > 0:
            benefit.stock -= body.quantity

        write_audit(db, auth=auth, guardian=guardian, volunteer_id=volunteer_id,
                    action=models.AuditAction.BENEFIT_CLAIM,
                    detail=f"监护人代领权益：{benefit.name} x{body.quantity}，消耗{total_points}积分",
                    ref_type="benefit_exchange", ref_id=exchange.id,
                    idempotency_key=body.idempotency_key)

        return {"replayed": False, "message": "权益代领成功",
                "exchange_id": exchange.id, "benefit_id": benefit.id,
                "points_spent": total_points, "quantity": body.quantity,
                "authorization_basis": auth_basis(auth, guardian)}


# ==================== 运营管理入口 ====================

@admin_router.post("/authorizations", response_model=schemas.AuthorizationOut)
def grant_authorization(body: schemas.AuthorizationGrant):
    """授予（或变更）监护授权：不可变地产生新版本，旧版本保留。"""
    parse_scopes(body.scopes)
    now = datetime.utcnow()
    valid_from = body.valid_from or now
    if body.valid_until and body.valid_until <= valid_from:
        raise HTTPException(status_code=422, detail="有效期截止时间必须晚于生效时间")

    with immediate_transaction() as db:
        volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == body.volunteer_id).first()
        if not volunteer:
            raise HTTPException(status_code=404, detail="孩子不存在")
        guardian = get_or_create_guardian(db, body.guardian_phone, body.guardian_name)

        latest = db.query(func.max(models.GuardianshipAuthorization.version)).filter(
            models.GuardianshipAuthorization.guardian_id == guardian.id,
            models.GuardianshipAuthorization.volunteer_id == body.volunteer_id
        ).scalar()
        version = (latest or 0) + 1

        auth = models.GuardianshipAuthorization(
            guardian_id=guardian.id, volunteer_id=body.volunteer_id, version=version,
            valid_from=valid_from, valid_until=body.valid_until,
            granted_by=body.granted_by or "运营管理员")
        auth.scopes = set(body.scopes)
        db.add(auth)
        db.flush()
        write_audit(db, auth=auth, guardian=guardian, volunteer_id=body.volunteer_id,
                    action=models.AuditAction.GRANT,
                    detail=f"授予授权 v{version}：{sorted(auth.scopes)}",
                    ref_type="authorization", ref_id=auth.id, at=valid_from)
        db.refresh(auth)
        return _authorization_out(db, auth)


@admin_router.post("/authorizations/{auth_id}/revoke", response_model=schemas.AuthorizationOut)
def revoke_authorization(auth_id: int, body: schemas.AuthorizationRevoke = None):
    """撤回授权：新版本状态置“已撤回”，新请求立即失效；历史操作依据不受影响。"""
    reason = (body.reason if body else None) or "监护人/运营撤回授权"
    with immediate_transaction() as db:
        auth = db.query(models.GuardianshipAuthorization).filter(
            models.GuardianshipAuthorization.id == auth_id).first()
        if not auth:
            raise HTTPException(status_code=404, detail="授权版本不存在")
        if auth.status == models.AuthorizationStatus.REVOKED:
            raise HTTPException(status_code=409, detail="该授权版本已撤回，请勿重复操作")
        auth.status = models.AuthorizationStatus.REVOKED
        auth.revoked_at = datetime.utcnow()
        auth.revoke_reason = reason
        write_audit(db, auth=auth, guardian=auth.guardian, volunteer_id=auth.volunteer_id,
                    action=models.AuditAction.REVOKE, detail=reason,
                    ref_type="authorization", ref_id=auth.id, at=auth.revoked_at)
        db.refresh(auth)
        return _authorization_out(db, auth)


def _authorization_out(db: Session, auth: models.GuardianshipAuthorization) -> schemas.AuthorizationOut:
    out = schemas.AuthorizationOut.model_validate(auth)
    out.scopes = sorted(auth.scopes)
    out.volunteer_name = auth.volunteer.name if auth.volunteer else None
    return out


@admin_router.get("/authorizations", response_model=List[schemas.AuthorizationOut])
def list_authorizations(volunteer_id: int = None, guardian_phone: str = None,
                        status: str = None, db: Session = Depends(get_db)):
    query = db.query(models.GuardianshipAuthorization)
    if volunteer_id:
        query = query.filter(models.GuardianshipAuthorization.volunteer_id == volunteer_id)
    if guardian_phone:
        guardian = get_guardian_by_phone(db, guardian_phone)
        if not guardian:
            return []
        query = query.filter(models.GuardianshipAuthorization.guardian_id == guardian.id)
    if status:
        status_enum = next((s for s in models.AuthorizationStatus
                            if s.value == status or s.name == status), None)
        if status_enum:
            query = query.filter(models.GuardianshipAuthorization.status == status_enum)
    rows = query.order_by(
        models.GuardianshipAuthorization.volunteer_id,
        models.GuardianshipAuthorization.guardian_id,
        models.GuardianshipAuthorization.version.desc()
    ).all()
    result = []
    for auth in rows:
        out = schemas.AuthorizationOut.model_validate(auth)
        out.scopes = sorted(auth.scopes)
        out.volunteer_name = auth.volunteer.name if auth.volunteer else None
        result.append(out)
    return result


@admin_router.get("/audits", response_model=List[schemas.GuardianshipAuditOut])
def list_audits(volunteer_id: int = None, guardian_phone: str = None,
                action: str = None, at: datetime = None,
                skip: int = 0, limit: int = 200, db: Session = Depends(get_db)):
    """
    审计流水。传 at 时仅返回该时间点（含）之前发生的事件，用于按时间点还原。
    """
    query = db.query(models.GuardianshipAudit)
    if volunteer_id:
        query = query.filter(models.GuardianshipAudit.volunteer_id == volunteer_id)
    if guardian_phone:
        guardian = get_guardian_by_phone(db, guardian_phone)
        if not guardian:
            return []
        query = query.filter(models.GuardianshipAudit.guardian_id == guardian.id)
    if action:
        action_enum = next((a for a in models.AuditAction
                            if a.value == action or a.name == action), None)
        if action_enum:
            query = query.filter(models.GuardianshipAudit.action == action_enum)
    if at:
        query = query.filter(models.GuardianshipAudit.operated_at <= at)
    return query.order_by(models.GuardianshipAudit.operated_at.desc(),
                          models.GuardianshipAudit.id.desc()
                          ).offset(skip).limit(limit).all()


def _version_grants_at_history(auth: models.GuardianshipAuthorization, at: datetime) -> bool:
    """按历史时间点判定决定版本在 at 时是否放行：撤回/过期若发生在 at 之后，当时仍有效。"""
    if auth.status == models.AuthorizationStatus.REVOKED and auth.revoked_at and at >= auth.revoked_at:
        return False
    if auth.valid_until is not None and at >= auth.valid_until:
        return False
    return True


@admin_router.get("/reconstruct/{volunteer_id}", response_model=schemas.ReconstructReport)
def reconstruct(volunteer_id: int, at: datetime = None, db: Session = Depends(get_db)):
    """
    按时间点还原：某孩子在指定时刻（默认现在）每个监护链的“决定授权版本”及是否有效，
    以及截至该时刻“谁凭哪一版授权看过或办理过什么”。
    """
    at = at or datetime.utcnow()
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="孩子不存在")

    all_auths = db.query(models.GuardianshipAuthorization).filter(
        models.GuardianshipAuthorization.volunteer_id == volunteer_id,
        models.GuardianshipAuthorization.valid_from <= at).all()
    # 每个监护人取在 at 时的最高版本（决定版本）
    deciding = {}
    for auth in all_auths:
        cur = deciding.get(auth.guardian_id)
        if cur is None or auth.version > cur.version:
            deciding[auth.guardian_id] = auth

    active = []
    for auth in deciding.values():
        if not _version_grants_at_history(auth, at):
            continue
        g = auth.guardian
        active.append(schemas.AuthorizationStateAt(
            authorization_id=auth.id, guardian_id=g.id, guardian_name=g.name,
            guardian_phone=g.phone, version=auth.version, scopes=sorted(auth.scopes),
            status=auth.status, valid_from=auth.valid_from, valid_until=auth.valid_until,
            revoked_at=auth.revoked_at))

    audits = db.query(models.GuardianshipAudit).filter(
        models.GuardianshipAudit.volunteer_id == volunteer_id,
        models.GuardianshipAudit.operated_at <= at
    ).order_by(models.GuardianshipAudit.operated_at.asc(),
               models.GuardianshipAudit.id.asc()).all()

    import json
    events = []
    for a in audits:
        try:
            scopes = json.loads(a.scopes_snapshot or "[]")
        except (ValueError, TypeError):
            scopes = []
        events.append(schemas.ReconstructEvent(
            audit_id=a.id, operated_at=a.operated_at,
            guardian_name=a.guardian_name_snapshot, guardian_phone=a.guardian_phone_snapshot,
            action=a.action.value if a.action else None, auth_version=a.auth_version,
            authorization_id=a.authorization_id, scopes=scopes, result=a.result,
            detail=a.detail, ref_type=a.ref_type, ref_id=a.ref_id))

    return schemas.ReconstructReport(
        volunteer_id=volunteer_id, volunteer_name=volunteer.name, at=at,
        active_authorizations=active, events=events)
