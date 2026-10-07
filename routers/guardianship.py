"""
监护人授权入口与授权管理接口。

- 监护人端：登录、按授权范围查询孩子信息、报名确认、权益代领；
  所有操作依据"当时有效的授权版本"执行并留痕。
- 管理端：授权发起/新版本/撤回/交接（共同监护、监护人交接），
  以及按时间点还原授权状态与操作审计。
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
import models, schemas
import guardian_auth as ga
from guardian_view_data import build_parent_view, build_parent_service_records, build_parent_training_records

router = APIRouter(prefix="/api/guardian", tags=["监护授权"])


class BusinessError(Exception):
    """授权有效但业务校验未通过（审计以"业务未通过"留痕）。"""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


_COMMIT_EXC = (ga.AuthzError, BusinessError)


def _parse_relation(value: Optional[str]):
    if not value:
        return models.GuardianRelation.PARENT
    for rel in models.GuardianRelation:
        if value in (rel.name, rel.value):
            return rel
    raise HTTPException(status_code=422, detail=f"未知关系类型: {value}")


def _parse_dt(value: Optional[str]):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"时间格式应为 ISO8601: {value}")


def _auth_out(a: models.GuardianAuthorization) -> schemas.AuthorizationOut:
    return schemas.AuthorizationOut(
        id=a.id, grant_seq=a.grant_seq, version_no=a.version_no,
        guardian_id=a.guardian_id,
        guardian_name=a.guardian.name if a.guardian else None,
        guardian_phone=a.guardian.phone if a.guardian else None,
        volunteer_id=a.volunteer_id,
        relation=a.relation.value if a.relation else None,
        scopes=ga.parse_scopes(a.scopes),
        valid_from=a.valid_from, valid_until=a.valid_until,
        status=a.status.value, superseded_by=a.superseded_by,
        revoked_at=a.revoked_at, revoke_reason=a.revoke_reason,
        created_at=a.created_at,
    )


def _op_out(o: models.GuardianOperation) -> schemas.GuardianOperationOut:
    return schemas.GuardianOperationOut(
        id=o.id, guardian_id=o.guardian_id,
        guardian_name=o.guardian.name if o.guardian else None,
        guardian_phone=o.guardian.phone if o.guardian else None,
        volunteer_id=o.volunteer_id,
        volunteer_name=o.volunteer.name if o.volunteer else None,
        authorization_id=o.authorization_id,
        operation_type=o.operation_type.value,
        result=o.result.value,
        idempotency_key=o.idempotency_key,
        target_type=o.target_type, target_id=o.target_id, detail=o.detail,
        auth_version_no=o.auth_version_no,
        auth_scopes_snapshot=o.auth_scopes_snapshot,
        auth_valid_from=o.auth_valid_from,
        auth_valid_until=o.auth_valid_until,
        operated_at=o.operated_at,
    )


# ==================== 监护人端 ====================

@router.post("/login")
def guardian_login(login: schemas.GuardianLogin, db: Session = Depends(get_db)):
    """登录只返回此刻持有有效授权的孩子；授权撤回/过期后立即不可见。"""
    children = ga.list_children_for_phone(db, login.phone)
    if not children:
        raise HTTPException(status_code=404, detail="未找到有效监护授权，请联系少年宫管理人员")
    return {"phone": login.phone, "children": children}


def _guarded_view(phone: str, volunteer_id: int, builder):
    def tx(db):
        guardian, auth, now = ga.authorize(
            db, phone=phone, volunteer_id=volunteer_id,
            scope=ga.SCOPE_VIEW, op_type=models.OperationType.VIEW_CHILD,
        )
        volunteer = db.query(models.Volunteer).filter(
            models.Volunteer.id == volunteer_id
        ).first()
        data = builder(db, volunteer)
        ga.record_success(
            db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
            op_type=models.OperationType.VIEW_CHILD, now=now,
            target_type="volunteer", target_id=volunteer_id,
        )
        return data
    try:
        return ga.with_immediate(tx, commit_exc=_COMMIT_EXC)
    except ga.AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)


@router.get("/children/{volunteer_id}/view", response_model=schemas.ParentView)
def guardian_view_child(volunteer_id: int, phone: str = Query(...)):
    return _guarded_view(phone, volunteer_id, lambda db, v: build_parent_view(db, v))


@router.get("/children/{volunteer_id}/service-records", response_model=List[schemas.ParentServiceRecord])
def guardian_service_records(volunteer_id: int, phone: str = Query(...)):
    return _guarded_view(phone, volunteer_id,
                         lambda db, v: build_parent_service_records(db, volunteer_id))


@router.get("/children/{volunteer_id}/training-records", response_model=List[schemas.ParentTrainingRecord])
def guardian_training_records(volunteer_id: int, phone: str = Query(...)):
    return _guarded_view(phone, volunteer_id,
                         lambda db, v: build_parent_training_records(db, volunteer_id))


@router.post("/children/{volunteer_id}/enrollments/confirm", response_model=schemas.EnrollConfirmOut)
def guardian_confirm_enrollment(volunteer_id: int, payload: schemas.GuardianEnrollConfirm,
                                phone: str = Query(...)):
    """监护人代确认报名入班。重复提交（同一幂等键或已在班）不会二次生效。"""
    def tx(db):
        prior = ga.find_idempotent(db, payload.idempotency_key) if payload.idempotency_key else None
        guardian, auth, now = ga.authorize(
            db, phone=phone, volunteer_id=volunteer_id,
            scope=ga.SCOPE_ENROLL, op_type=models.OperationType.ENROLL_CONFIRM,
            target_type="training_batch", target_id=payload.batch_id,
        )

        if prior and prior.target_type == "enrollment" and prior.target_id:
            ga.record_duplicate(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.ENROLL_CONFIRM, prior=prior, now=now,
                target_type="enrollment", target_id=prior.target_id,
            )
            existing = db.query(models.Enrollment).filter(
                models.Enrollment.id == int(prior.target_id)
            ).first()
            return schemas.EnrollConfirmOut(
                enrollment_id=int(prior.target_id), volunteer_id=volunteer_id,
                batch_id=existing.batch_id if existing else payload.batch_id,
                status=existing.status.value if existing else "已入班",
                operation_id=prior.id, authorization_id=auth.id,
                authorization_version_no=auth.version_no, idempotent=True,
            )

        batch = db.query(models.TrainingBatch).filter(
            models.TrainingBatch.id == payload.batch_id
        ).first()
        if not batch:
            ga.record_business_failed(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.ENROLL_CONFIRM, reason="培训期次不存在",
                now=now, target_type="training_batch", target_id=payload.batch_id,
            )
            raise BusinessError("培训期次不存在")

        volunteer = db.query(models.Volunteer).filter(
            models.Volunteer.id == volunteer_id
        ).first()
        if volunteer.status not in (models.VolunteerStatus.IN_TRAINING,
                                    models.VolunteerStatus.PENDING_ASSESSMENT):
            reason = f"孩子状态({volunteer.status.value})不可报名"
            ga.record_business_failed(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.ENROLL_CONFIRM, reason=reason, now=now,
                target_type="training_batch", target_id=payload.batch_id,
            )
            raise BusinessError(reason)

        existing_enr = db.query(models.Enrollment).filter(
            models.Enrollment.batch_id == payload.batch_id,
            models.Enrollment.volunteer_id == volunteer_id,
        ).first()
        if existing_enr:
            dup = ga.record_duplicate(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.ENROLL_CONFIRM, prior=prior, now=now,
                target_type="enrollment", target_id=existing_enr.id,
            )
            return schemas.EnrollConfirmOut(
                enrollment_id=existing_enr.id, volunteer_id=volunteer_id,
                batch_id=payload.batch_id, status=existing_enr.status.value,
                operation_id=dup.id, authorization_id=auth.id,
                authorization_version_no=auth.version_no, idempotent=True,
            )

        from sqlalchemy import func
        current_count = db.query(func.count(models.Enrollment.id)).filter(
            models.Enrollment.batch_id == payload.batch_id,
            models.Enrollment.status == models.EnrollmentStatus.ENROLLED,
        ).scalar() or 0
        if current_count >= batch.capacity:
            ga.record_business_failed(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.ENROLL_CONFIRM, reason="班额已满", now=now,
                target_type="training_batch", target_id=payload.batch_id,
            )
            raise BusinessError("班额已满")

        enrollment = models.Enrollment(
            volunteer_id=volunteer_id, batch_id=payload.batch_id,
            status=models.EnrollmentStatus.ENROLLED, notes=payload.notes,
        )
        db.add(enrollment)
        db.flush()

        op = ga.record_success(
            db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
            op_type=models.OperationType.ENROLL_CONFIRM, now=now,
            idempotency_key=payload.idempotency_key,
            target_type="enrollment", target_id=enrollment.id,
            detail=f"确认入班：{batch.name}",
        )
        return schemas.EnrollConfirmOut(
            enrollment_id=enrollment.id, volunteer_id=volunteer_id,
            batch_id=payload.batch_id, status=enrollment.status.value,
            operation_id=op.id, authorization_id=auth.id,
            authorization_version_no=auth.version_no, idempotent=False,
        )

    try:
        return ga.with_immediate(tx, commit_exc=_COMMIT_EXC)
    except ga.AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)
    except BusinessError as e:
        raise HTTPException(status_code=400, detail=e.reason)


@router.post("/children/{volunteer_id}/benefits/claim", response_model=schemas.BenefitClaimOut)
def guardian_claim_benefit(volunteer_id: int, payload: schemas.GuardianBenefitClaim,
                           phone: str = Query(...)):
    """监护人代领权益（兑换）。同一幂等键重试只生效一次。"""
    def tx(db):
        prior = ga.find_idempotent(db, payload.idempotency_key) if payload.idempotency_key else None
        guardian, auth, now = ga.authorize(
            db, phone=phone, volunteer_id=volunteer_id,
            scope=ga.SCOPE_BENEFIT, op_type=models.OperationType.BENEFIT_CLAIM,
            target_type="benefit", target_id=payload.benefit_id,
        )

        if prior and prior.target_type == "benefit_exchange" and prior.target_id:
            ga.record_duplicate(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.BENEFIT_CLAIM, prior=prior, now=now,
                target_type="benefit_exchange", target_id=prior.target_id,
            )
            old = db.query(models.BenefitExchange).filter(
                models.BenefitExchange.id == int(prior.target_id)
            ).first()
            return schemas.BenefitClaimOut(
                exchange_id=int(prior.target_id), volunteer_id=volunteer_id,
                benefit_id=old.benefit_id if old else payload.benefit_id,
                points_spent=old.points_spent if old else 0,
                quantity=old.quantity if old else payload.quantity,
                status=old.status.value if old else "待处理",
                operation_id=prior.id, authorization_id=auth.id,
                authorization_version_no=auth.version_no, idempotent=True,
            )

        benefit = db.query(models.Benefit).filter(
            models.Benefit.id == payload.benefit_id
        ).first()
        if not benefit:
            ga.record_business_failed(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.BENEFIT_CLAIM, reason="权益不存在", now=now,
                target_type="benefit", target_id=payload.benefit_id,
            )
            raise BusinessError("权益不存在")
        if not benefit.is_active:
            ga.record_business_failed(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.BENEFIT_CLAIM, reason="该权益已下架", now=now,
                target_type="benefit", target_id=payload.benefit_id,
            )
            raise BusinessError("该权益已下架")

        volunteer = db.query(models.Volunteer).filter(
            models.Volunteer.id == volunteer_id
        ).first()
        total_points = benefit.points_cost * payload.quantity
        if benefit.stock > 0 and benefit.stock < payload.quantity:
            ga.record_business_failed(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.BENEFIT_CLAIM, reason="库存不足", now=now,
                target_type="benefit", target_id=payload.benefit_id,
            )
            raise BusinessError("库存不足")
        if (volunteer.points_balance or 0) < total_points:
            ga.record_business_failed(
                db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
                op_type=models.OperationType.BENEFIT_CLAIM,
                reason=f"积分不足（余额{volunteer.points_balance or 0}，需{total_points}）",
                now=now, target_type="benefit", target_id=payload.benefit_id,
            )
            raise BusinessError("积分不足")

        exchange = models.BenefitExchange(
            volunteer_id=volunteer_id, benefit_id=payload.benefit_id,
            points_spent=total_points, quantity=payload.quantity,
            delivery_info=payload.delivery_info, notes=payload.notes,
            status=models.ExchangeStatus.PENDING,
        )
        db.add(exchange)
        db.flush()

        volunteer.points_balance = (volunteer.points_balance or 0) - total_points
        source = (models.PointsSource.EXCHANGE_BADGE
                  if benefit.benefit_type == models.BenefitType.BADGE
                  else models.PointsSource.EXCHANGE_PRIORITY_SLOT)
        db.add(models.PointsRecord(
            volunteer_id=volunteer_id, points_type=models.PointsType.SPEND,
            points_amount=total_points, source=source,
            description=f"监护人代领：{benefit.name} x{payload.quantity}",
            exchange_id=exchange.id,
        ))
        if benefit.stock > 0:
            benefit.stock -= payload.quantity

        op = ga.record_success(
            db, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
            op_type=models.OperationType.BENEFIT_CLAIM, now=now,
            idempotency_key=payload.idempotency_key,
            target_type="benefit_exchange", target_id=exchange.id,
            detail=f"代领权益：{benefit.name} x{payload.quantity}，消耗{total_points}积分",
        )
        return schemas.BenefitClaimOut(
            exchange_id=exchange.id, volunteer_id=volunteer_id,
            benefit_id=payload.benefit_id, points_spent=total_points,
            quantity=payload.quantity, status=exchange.status.value,
            operation_id=op.id, authorization_id=auth.id,
            authorization_version_no=auth.version_no, idempotent=False,
        )

    try:
        return ga.with_immediate(tx, commit_exc=_COMMIT_EXC)
    except ga.AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)
    except BusinessError as e:
        raise HTTPException(status_code=400, detail=e.reason)


# ==================== 管理端：授权版本管理 ====================

admin = APIRouter(prefix="/api/admin/guardianship", tags=["监护授权-管理"])


@admin.post("/authorizations", response_model=schemas.AuthorizationOut, status_code=201)
def create_authorization(payload: schemas.AuthorizationGrant, db: Session = Depends(get_db)):
    """发起授权（可限定孩子、操作范围、有效期）。共同监护即为同一孩子发起多条独立授权。"""
    def tx(db):
        auth = ga.grant_authorization(
            db, volunteer_id=payload.volunteer_id, phone=payload.phone, name=payload.name,
            relation=_parse_relation(payload.relation), scopes=payload.scopes,
            valid_from=payload.valid_from, valid_until=payload.valid_until,
        )
        db.flush()
        return _auth_out(auth)
    try:
        return ga.with_immediate(tx)
    except HTTPException:
        raise


@admin.post("/authorizations/{authorization_id}/versions", response_model=schemas.AuthorizationOut, status_code=201)
def create_new_version(authorization_id: int, payload: schemas.AuthorizationNewVersion,
                       db: Session = Depends(get_db)):
    """范围/有效期变更：同链出新版本，旧版本立即被取代并保留可溯。"""
    def tx(db):
        new = ga.new_version(db, authorization_id=authorization_id,
                             scopes=payload.scopes, valid_until=payload.valid_until)
        db.flush()
        return _auth_out(new)
    return ga.with_immediate(tx)


@admin.post("/authorizations/{authorization_id}/revoke", response_model=schemas.AuthorizationOut)
def revoke_authorization(authorization_id: int, payload: schemas.AuthorizationRevoke,
                         db: Session = Depends(get_db)):
    def tx(db):
        ids = ga.revoke(db, authorization_id=authorization_id, reason=payload.reason)
        a = db.query(models.GuardianAuthorization).filter(
            models.GuardianAuthorization.id == ids[0]
        ).first()
        return _auth_out(a)
    try:
        return ga.with_immediate(tx)
    except HTTPException:
        raise


@admin.post("/volunteers/{volunteer_id}/guardians/revoke")
def revoke_guardian_for_child(volunteer_id: int, payload: schemas.GuardianRevoke,
                              db: Session = Depends(get_db)):
    """按孩子撤回某监护人（手机号或监护人ID）当前有效授权；不影响其他共同监护人。"""
    def tx(db):
        guardian_id = payload.guardian_id
        if guardian_id is None:
            if not payload.phone:
                raise HTTPException(status_code=422, detail="需提供 guardian_id 或 phone")
            g = db.query(models.Guardian).filter(models.Guardian.phone == payload.phone).first()
            if not g:
                raise HTTPException(status_code=404, detail="监护人不存在")
            guardian_id = g.id
        ids = ga.revoke(db, guardian_id=guardian_id, volunteer_id=volunteer_id,
                        reason=payload.reason)
        return {"revoked_authorization_ids": ids}
    try:
        return ga.with_immediate(tx)
    except HTTPException:
        raise


@admin.post("/volunteers/{volunteer_id}/handover", response_model=schemas.HandoverOut)
def handover_guardianship(volunteer_id: int, payload: schemas.AuthorizationHandover,
                          db: Session = Depends(get_db)):
    """监护人交接：原子撤回原监护授权并向新监护人发起授权。"""
    def tx(db):
        new_auth, revoked_ids = ga.handover(
            db, volunteer_id=volunteer_id, to_phone=payload.to_phone,
            to_name=payload.to_name, relation=_parse_relation(payload.relation),
            scopes=payload.scopes, valid_until=payload.valid_until, reason=payload.reason,
        )
        db.flush()
        return schemas.HandoverOut(
            new_authorization=_auth_out(new_auth),
            revoked_authorization_ids=revoked_ids,
        )
    try:
        return ga.with_immediate(tx)
    except HTTPException:
        raise


@admin.get("/authorizations", response_model=List[schemas.AuthorizationOut])
def list_authorizations(volunteer_id: int = None, phone: str = None,
                        status: str = None, db: Session = Depends(get_db)):
    q = db.query(models.GuardianAuthorization)
    if volunteer_id is not None:
        q = q.filter(models.GuardianAuthorization.volunteer_id == volunteer_id)
    if phone:
        g = db.query(models.Guardian).filter(models.Guardian.phone == phone).first()
        if not g:
            return []
        q = q.filter(models.GuardianAuthorization.guardian_id == g.id)
    if status:
        matched = [s for s in models.AuthorizationStatus if s.value == status or s.name == status]
        if matched:
            q = q.filter(models.GuardianAuthorization.status == matched[0])
    rows = q.order_by(
        models.GuardianAuthorization.volunteer_id,
        models.GuardianAuthorization.grant_seq,
        models.GuardianAuthorization.version_no,
    ).all()
    return [_auth_out(a) for a in rows]


# ==================== 管理端：审计与时间点还原 ====================

@admin.get("/operations", response_model=List[schemas.GuardianOperationOut])
def list_operations(volunteer_id: int = None, phone: str = None,
                    operation_type: str = None, result: str = None,
                    start: str = None, end: str = None,
                    skip: int = 0, limit: int = 200, db: Session = Depends(get_db)):
    op_enum = res_enum = None
    if operation_type:
        op_enum = next((t for t in models.OperationType
                        if t.value == operation_type or t.name == operation_type), None)
    if result:
        res_enum = next((t for t in models.OperationResult
                         if t.value == result or t.name == result), None)
    rows = ga.query_operations(
        db, volunteer_id=volunteer_id, guardian_phone=phone,
        operation_type=op_enum, result=res_enum,
        start=_parse_dt(start), end=_parse_dt(end), skip=skip, limit=limit,
    )
    return [_op_out(o) for o in rows]


@admin.get("/as-of")
def reconstruct_as_of(ts: str, volunteer_id: int = None, db: Session = Depends(get_db)):
    """
    按时间点还原：
    - effective_authorizations：该时刻生效的授权版本（谁有权、凭哪版、范围与有效期）；
    - operations：该时刻之前已发生的全部操作（谁凭哪版授权看过/办过什么）。
    """
    point = _parse_dt(ts)
    auths = ga.effective_authorizations_at(db, point, volunteer_id)
    ops = ga.query_operations(db, volunteer_id=volunteer_id, end=point, limit=10000)
    return {
        "as_of": point,
        "effective_authorizations": [_auth_out(a).model_dump(mode="json") for a in auths],
        "operations": [_op_out(o).model_dump(mode="json") for o in ops],
    }
