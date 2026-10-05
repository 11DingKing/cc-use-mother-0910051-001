from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from database import get_db
import models, schemas

router = APIRouter(prefix="/api/points", tags=["积分管理"])


def add_points(db: Session, volunteer_id: int, points: int, source: models.PointsSource,
               description: str = None, service_record_id: int = None, exchange_id: int = None):
    if points <= 0:
        return None

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        return None

    volunteer.points_balance = (volunteer.points_balance or 0) + points

    record = models.PointsRecord(
        volunteer_id=volunteer_id,
        points_type=models.PointsType.EARN,
        points_amount=points,
        source=source,
        description=description,
        service_record_id=service_record_id,
        exchange_id=exchange_id
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def spend_points(db: Session, volunteer_id: int, points: int, source: models.PointsSource,
                description: str = None, exchange_id: int = None):
    if points <= 0:
        return None

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    if (volunteer.points_balance or 0) < points:
        raise HTTPException(status_code=400, detail="积分不足")

    volunteer.points_balance -= points

    record = models.PointsRecord(
        volunteer_id=volunteer_id,
        points_type=models.PointsType.SPEND,
        points_amount=points,
        source=source,
        description=description,
        exchange_id=exchange_id
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/records", response_model=List[schemas.PointsRecord])
def list_points_records(volunteer_id: int = None, points_type: str = None,
                        source: str = None, skip: int = 0, limit: int = 100,
                        db: Session = Depends(get_db)):
    query = db.query(models.PointsRecord)
    if volunteer_id:
        query = query.filter(models.PointsRecord.volunteer_id == volunteer_id)
    if points_type:
        query = query.filter(models.PointsRecord.points_type == points_type)
    if source:
        query = query.filter(models.PointsRecord.source == source)
    return query.order_by(models.PointsRecord.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/volunteer/{volunteer_id}", response_model=schemas.VolunteerPoints)
def get_volunteer_points(volunteer_id: int, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    total_earned = db.query(func.sum(models.PointsRecord.points_amount)).filter(
        models.PointsRecord.volunteer_id == volunteer_id,
        models.PointsRecord.points_type == models.PointsType.EARN
    ).scalar() or 0

    total_spent = db.query(func.sum(models.PointsRecord.points_amount)).filter(
        models.PointsRecord.volunteer_id == volunteer_id,
        models.PointsRecord.points_type == models.PointsType.SPEND
    ).scalar() or 0

    return schemas.VolunteerPoints(
        volunteer_id=volunteer_id,
        name=volunteer.name,
        points_balance=volunteer.points_balance or 0,
        total_earned=total_earned,
        total_spent=total_spent
    )


@router.get("/volunteer/{volunteer_id}/records", response_model=List[schemas.PointsRecord])
def get_volunteer_points_records(volunteer_id: int, skip: int = 0, limit: int = 100,
                                 db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    return db.query(models.PointsRecord).filter(
        models.PointsRecord.volunteer_id == volunteer_id
    ).order_by(models.PointsRecord.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/manual-adjust", response_model=schemas.PointsRecord)
def manual_adjust_points(adjust: schemas.PointsRecordCreate, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == adjust.volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    if adjust.points_type == models.PointsType.EARN:
        volunteer.points_balance = (volunteer.points_balance or 0) + adjust.points_amount
    else:
        if (volunteer.points_balance or 0) < adjust.points_amount:
            raise HTTPException(status_code=400, detail="积分不足")
        volunteer.points_balance -= adjust.points_amount

    record = models.PointsRecord(**adjust.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/stats", response_model=schemas.PointsStats)
def get_points_stats(db: Session = Depends(get_db)):
    total_earned = db.query(func.sum(models.PointsRecord.points_amount)).filter(
        models.PointsRecord.points_type == models.PointsType.EARN
    ).scalar() or 0

    total_spent = db.query(func.sum(models.PointsRecord.points_amount)).filter(
        models.PointsRecord.points_type == models.PointsType.SPEND
    ).scalar() or 0

    earn_count = db.query(func.count(models.PointsRecord.id)).filter(
        models.PointsRecord.points_type == models.PointsType.EARN
    ).scalar() or 0

    spend_count = db.query(func.count(models.PointsRecord.id)).filter(
        models.PointsRecord.points_type == models.PointsType.SPEND
    ).scalar() or 0

    return schemas.PointsStats(
        total_points_earned=total_earned,
        total_points_spent=total_spent,
        net_points=total_earned - total_spent,
        earn_count=earn_count,
        spend_count=spend_count
    )
