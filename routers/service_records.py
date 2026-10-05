from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models, schemas
from routers.points import add_points
from routers.star_certificates import check_and_issue_star_certificate

router = APIRouter(prefix="/api/service-records", tags=["服务记录"])


def calculate_service_points(service_hours: float, teacher_rating: int = None) -> int:
    base_points = int(service_hours * 10)
    rating_points = 0
    if teacher_rating and teacher_rating >= 4:
        rating_points = (teacher_rating - 3) * 5
    return base_points + rating_points


def update_volunteer_star(volunteer_id: int, db: Session):
    from sqlalchemy import func
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        return
    total_hours = db.query(func.sum(models.ServiceRecord.service_hours)).filter(
        models.ServiceRecord.volunteer_id == volunteer_id
    ).scalar() or 0.0
    volunteer.total_service_hours = total_hours

    star_levels = db.query(models.StarLevel).order_by(models.StarLevel.min_hours.desc()).all()
    new_star = None
    for sl in star_levels:
        if total_hours >= sl.min_hours:
            new_star = sl
            break

    old_star_id = volunteer.star_level_id
    volunteer.star_level_id = new_star.id if new_star else None

    if old_star_id != volunteer.star_level_id and volunteer.star_level_id:
        db.commit()
        check_and_issue_star_certificate(db, volunteer_id)
    else:
        db.commit()


@router.get("/", response_model=List[schemas.ServiceRecord])
def list_service_records(volunteer_id: int = None, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(models.ServiceRecord)
    if volunteer_id:
        query = query.filter(models.ServiceRecord.volunteer_id == volunteer_id)
    return query.order_by(models.ServiceRecord.service_date.desc()).offset(skip).limit(limit).all()


@router.get("/{record_id}", response_model=schemas.ServiceRecord)
def get_service_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(models.ServiceRecord).filter(models.ServiceRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="服务记录不存在")
    return record


@router.post("/", response_model=schemas.ServiceRecord)
def create_service_record(record: schemas.ServiceRecordCreate, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == record.volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    if record.time_slot_id:
        slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == record.time_slot_id).first()
        if slot:
            slot.status = models.TimeSlotStatus.COMPLETED

    points_awarded = calculate_service_points(record.service_hours, record.teacher_rating)

    db_record = models.ServiceRecord(**record.model_dump())
    db_record.points_awarded = points_awarded
    db.add(db_record)
    db.flush()

    if points_awarded > 0:
        add_points(
            db=db,
            volunteer_id=record.volunteer_id,
            points=points_awarded,
            source=models.PointsSource.SERVICE_COMPLETION,
            description=f"完成讲解服务 {record.service_hours}小时",
            service_record_id=db_record.id
        )

        if record.teacher_rating and record.teacher_rating >= 4:
            rating_points = (record.teacher_rating - 3) * 5
            if rating_points > 0:
                add_points(
                    db=db,
                    volunteer_id=record.volunteer_id,
                    points=rating_points,
                    source=models.PointsSource.TEACHER_RATING,
                    description=f"老师好评 {record.teacher_rating}星",
                    service_record_id=db_record.id
                )

    db.commit()
    db.refresh(db_record)

    update_volunteer_star(record.volunteer_id, db)

    db.refresh(db_record)
    return db_record


@router.put("/{record_id}", response_model=schemas.ServiceRecord)
def update_service_record(record_id: int, record_update: schemas.ServiceRecordCreate,
                          db: Session = Depends(get_db)):
    record = db.query(models.ServiceRecord).filter(models.ServiceRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="服务记录不存在")

    old_points = record.points_awarded or 0

    update_data = record_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(record, key, value)

    new_points = calculate_service_points(record.service_hours, record.teacher_rating)
    record.points_awarded = new_points

    if old_points != new_points:
        from routers.points import spend_points
        if old_points > 0:
            spend_points(
                db=db,
                volunteer_id=record.volunteer_id,
                points=old_points,
                source=models.PointsSource.OTHER,
                description="服务记录更新，扣除原积分",
                service_record_id=record.id
            )
        if new_points > 0:
            add_points(
                db=db,
                volunteer_id=record.volunteer_id,
                points=new_points,
                source=models.PointsSource.SERVICE_COMPLETION,
                description=f"更新讲解服务 {record.service_hours}小时",
                service_record_id=record.id
            )

            if record.teacher_rating and record.teacher_rating >= 4:
                rating_points = (record.teacher_rating - 3) * 5
                if rating_points > 0:
                    add_points(
                        db=db,
                        volunteer_id=record.volunteer_id,
                        points=rating_points,
                        source=models.PointsSource.TEACHER_RATING,
                        description=f"老师好评 {record.teacher_rating}星",
                        service_record_id=record.id
                    )

    db.commit()
    db.refresh(record)

    update_volunteer_star(record.volunteer_id, db)

    return record


@router.delete("/{record_id}")
def delete_service_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(models.ServiceRecord).filter(models.ServiceRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="服务记录不存在")
    vid = record.volunteer_id
    points_to_deduct = record.points_awarded or 0

    if points_to_deduct > 0:
        from routers.points import spend_points
        spend_points(
            db=db,
            volunteer_id=vid,
            points=points_to_deduct,
            source=models.PointsSource.OTHER,
            description="删除服务记录，扣除积分",
            service_record_id=record.id
        )

    db.delete(record)
    db.commit()
    update_volunteer_star(vid, db)
    return {"message": "删除成功"}
