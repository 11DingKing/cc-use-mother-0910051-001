from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models, schemas

router = APIRouter(prefix="/api/volunteers", tags=["志愿者管理"])


@router.get("/", response_model=List[schemas.Volunteer])
def list_volunteers(status: str = None, school_id: int = None, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(models.Volunteer)
    if status:
        status_enum = None
        for s in models.VolunteerStatus:
            if s.value == status or s.name == status:
                status_enum = s
                break
        if status_enum:
            query = query.filter(models.Volunteer.status == status_enum)
    if school_id:
        query = query.filter(models.Volunteer.school_id == school_id)
    return query.offset(skip).limit(limit).all()


@router.get("/{volunteer_id}", response_model=schemas.VolunteerDetail)
def get_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    return volunteer


@router.post("/", response_model=schemas.Volunteer)
def create_volunteer(volunteer: schemas.VolunteerCreate, db: Session = Depends(get_db)):
    db_volunteer = models.Volunteer(**volunteer.model_dump())
    db.add(db_volunteer)
    db.commit()
    db.refresh(db_volunteer)
    return db_volunteer


@router.put("/{volunteer_id}", response_model=schemas.Volunteer)
def update_volunteer(volunteer_id: int, volunteer_update: schemas.VolunteerUpdate, db: Session = Depends(get_db)):
    db_volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not db_volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    update_data = volunteer_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_volunteer, key, value)
    db.commit()
    db.refresh(db_volunteer)
    return db_volunteer


@router.post("/{volunteer_id}/review", response_model=schemas.Volunteer)
def review_application(volunteer_id: int, review: schemas.ReviewApplication, db: Session = Depends(get_db)):
    db_volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not db_volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    if db_volunteer.status != models.VolunteerStatus.PENDING_REVIEW:
        raise HTTPException(status_code=400, detail="当前状态不可审核")
    if review.approved:
        db_volunteer.status = models.VolunteerStatus.IN_TRAINING
    else:
        db_volunteer.status = models.VolunteerStatus.DISABLED
        if review.notes:
            db_volunteer.notes = (db_volunteer.notes or "") + f"\n审核不通过: {review.notes}"
    db.commit()
    db.refresh(db_volunteer)
    return db_volunteer


@router.post("/{volunteer_id}/ready-for-assessment", response_model=schemas.Volunteer)
def mark_ready_for_assessment(volunteer_id: int, db: Session = Depends(get_db)):
    from sqlalchemy import func
    db_volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not db_volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    if db_volunteer.status != models.VolunteerStatus.IN_TRAINING:
        raise HTTPException(status_code=400, detail="只有培训中的志愿者才能进入考核阶段")

    enrollments = db.query(models.Enrollment).filter(
        models.Enrollment.volunteer_id == volunteer_id,
        models.Enrollment.status.in_([models.EnrollmentStatus.ENROLLED, models.EnrollmentStatus.COMPLETED])
    ).all()

    if enrollments:
        any_eligible = False
        for en in enrollments:
            total_sessions = db.query(func.count(models.TrainingSession.id)).filter(
                models.TrainingSession.batch_id == en.batch_id
            ).scalar() or 0
            if total_sessions == 0:
                continue
            attended = db.query(func.count(models.SessionAttendance.id)).filter(
                models.SessionAttendance.enrollment_id == en.id,
                models.SessionAttendance.attended == True
            ).scalar() or 0
            batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == en.batch_id).first()
            min_rate = batch.min_attendance_rate if batch else 80.0
            rate = attended / total_sessions * 100
            if rate >= min_rate:
                any_eligible = True
                en.status = models.EnrollmentStatus.COMPLETED
                from datetime import datetime
                en.completed_at = datetime.utcnow()
                break
        if not any_eligible:
            raise HTTPException(status_code=400, detail="出勤未达标，请先完成培训课次并达到最低出勤率要求")

    db_volunteer.status = models.VolunteerStatus.PENDING_ASSESSMENT
    db.commit()
    db.refresh(db_volunteer)
    return db_volunteer


@router.delete("/{volunteer_id}")
def delete_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    db_volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not db_volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    db.delete(db_volunteer)
    db.commit()
    return {"message": "删除成功"}


@router.get("/{volunteer_id}/service-hours")
def get_volunteer_service_hours(volunteer_id: int, db: Session = Depends(get_db)):
    db_volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not db_volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    from sqlalchemy import func
    total = db.query(func.sum(models.ServiceRecord.service_hours)).filter(
        models.ServiceRecord.volunteer_id == volunteer_id
    ).scalar() or 0.0
    return {"volunteer_id": volunteer_id, "name": db_volunteer.name, "total_service_hours": total}
