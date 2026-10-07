"""
家长入口（兼容旧前端）。

自监护授权版本化改造后，本入口不再以 Volunteer.parent_phone 做静态绑定，
而是统一走 guardian_auth：依据当时有效的授权版本鉴权，撤回/过期立即失效，
每次查看都写入操作审计。授权在管理端 (/api/admin/guardianship) 发起与撤回。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

from database import get_db
import models, schemas
import guardian_auth as ga
from guardian_view_data import (
    build_parent_view, build_parent_service_records, build_parent_training_records,
)

router = APIRouter(prefix="/api/parents", tags=["家长入口"])


@router.post("/login")
def parent_login(login_data: schemas.ParentLogin, db: Session = Depends(get_db)):
    """登录仅返回此刻持有有效授权（含查看范围）的孩子；授权撤回/过期后立即不可见。"""
    children = ga.list_children_for_phone(db, login_data.parent_phone)
    children = [c for c in children if ga.SCOPE_VIEW in c["scopes"]]
    if login_data.volunteer_name:
        children = [c for c in children if c["name"] == login_data.volunteer_name]
    if not children:
        raise HTTPException(status_code=404, detail="未找到有效监护授权，请检查手机号、孩子姓名或联系少年宫")

    return {
        "volunteers": [
            {
                "id": c["volunteer_id"],
                "name": c["name"],
                "school_name": c["school_name"],
                "grade": c["grade"],
            }
            for c in children
        ]
    }


def _guarded(phone: str, volunteer_id: int, builder):
    def tx(db):
        guardian, auth, now = ga.authorize(
            db, phone=phone, volunteer_id=volunteer_id,
            scope=ga.SCOPE_VIEW, op_type=models.OperationType.VIEW_CHILD,
            target_type="volunteer", target_id=volunteer_id,
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
        return ga.with_immediate(tx, commit_exc=(ga.AuthzError,))
    except ga.AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)


@router.get("/volunteer/{volunteer_id}", response_model=schemas.ParentView)
def get_parent_view(volunteer_id: int, parent_phone: str = Query(...)):
    return _guarded(parent_phone, volunteer_id, lambda db, v: build_parent_view(db, v))


@router.get("/volunteer/{volunteer_id}/service-records", response_model=List[schemas.ParentServiceRecord])
def get_parent_service_records(volunteer_id: int, parent_phone: str = Query(...),
                               skip: int = 0, limit: int = 100):
    records = _guarded(parent_phone, volunteer_id,
                       lambda db, v: build_parent_service_records(db, volunteer_id))
    return records[skip:skip + limit]


@router.get("/volunteer/{volunteer_id}/training-records", response_model=List[schemas.ParentTrainingRecord])
def get_parent_training_records(volunteer_id: int, parent_phone: str = Query(...)):
    return _guarded(parent_phone, volunteer_id,
                    lambda db, v: build_parent_training_records(db, volunteer_id))


@router.get("/volunteer/{volunteer_id}/certificates", response_model=List[schemas.StarCertificate])
def get_parent_certificates(volunteer_id: int, parent_phone: str = Query(...),
                            db: Session = Depends(get_db)):
    # 鉴权与留痕
    def tx(session):
        guardian, auth, now = ga.authorize(
            session, phone=parent_phone, volunteer_id=volunteer_id,
            scope=ga.SCOPE_VIEW, op_type=models.OperationType.VIEW_CHILD,
            target_type="certificate", target_id=volunteer_id,
        )
        rows = session.query(models.StarCertificate).filter(
            models.StarCertificate.volunteer_id == volunteer_id,
            models.StarCertificate.is_active == True  # noqa: E712
        ).order_by(models.StarCertificate.created_at.desc()).all()
        ga.record_success(
            session, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
            op_type=models.OperationType.VIEW_CHILD, now=now,
            target_type="certificate", target_id=volunteer_id,
        )
        return [schemas.StarCertificate.model_validate(r) for r in rows]
    try:
        return ga.with_immediate(tx, commit_exc=(ga.AuthzError,))
    except ga.AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)


@router.get("/volunteer/{volunteer_id}/points-records", response_model=List[schemas.PointsRecord])
def get_parent_points_records(volunteer_id: int, parent_phone: str = Query(...),
                              skip: int = 0, limit: int = 100,
                              db: Session = Depends(get_db)):
    def tx(session):
        guardian, auth, now = ga.authorize(
            session, phone=parent_phone, volunteer_id=volunteer_id,
            scope=ga.SCOPE_VIEW, op_type=models.OperationType.VIEW_CHILD,
            target_type="points_record", target_id=volunteer_id,
        )
        rows = session.query(models.PointsRecord).filter(
            models.PointsRecord.volunteer_id == volunteer_id
        ).order_by(models.PointsRecord.created_at.desc()).offset(skip).limit(limit).all()
        ga.record_success(
            session, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
            op_type=models.OperationType.VIEW_CHILD, now=now,
            target_type="points_record", target_id=volunteer_id,
        )
        return [schemas.PointsRecord.model_validate(r) for r in rows]
    try:
        return ga.with_immediate(tx, commit_exc=(ga.AuthzError,))
    except ga.AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)


@router.get("/volunteer/{volunteer_id}/exchanges", response_model=List[schemas.BenefitExchange])
def get_parent_exchanges(volunteer_id: int, parent_phone: str = Query(...),
                         skip: int = 0, limit: int = 100,
                         db: Session = Depends(get_db)):
    def tx(session):
        guardian, auth, now = ga.authorize(
            session, phone=parent_phone, volunteer_id=volunteer_id,
            scope=ga.SCOPE_VIEW, op_type=models.OperationType.VIEW_CHILD,
            target_type="exchange", target_id=volunteer_id,
        )
        rows = session.query(models.BenefitExchange).filter(
            models.BenefitExchange.volunteer_id == volunteer_id
        ).order_by(models.BenefitExchange.created_at.desc()).offset(skip).limit(limit).all()
        ga.record_success(
            session, guardian=guardian, auth=auth, volunteer_id=volunteer_id,
            op_type=models.OperationType.VIEW_CHILD, now=now,
            target_type="exchange", target_id=volunteer_id,
        )
        return [schemas.BenefitExchange.model_validate(r) for r in rows]
    try:
        return ga.with_immediate(tx, commit_exc=(ga.AuthzError,))
    except ga.AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)
