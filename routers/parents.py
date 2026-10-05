from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from database import get_db
import models, schemas

router = APIRouter(prefix="/api/parents", tags=["家长入口"])


@router.post("/login")
def parent_login(login_data: schemas.ParentLogin, db: Session = Depends(get_db)):
    query = db.query(models.Volunteer).filter(
        models.Volunteer.parent_phone == login_data.parent_phone
    )

    if login_data.volunteer_name:
        query = query.filter(models.Volunteer.name == login_data.volunteer_name)

    volunteers = query.all()

    if not volunteers:
        raise HTTPException(status_code=404, detail="未找到相关信息，请检查手机号和孩子姓名")

    return {
        "volunteers": [
            {
                "id": v.id,
                "name": v.name,
                "school_name": v.school.name if v.school else None,
                "grade": v.grade
            }
            for v in volunteers
        ]
    }


@router.get("/volunteer/{volunteer_id}", response_model=schemas.ParentView)
def get_parent_view(volunteer_id: int, parent_phone: str = None, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    if parent_phone and volunteer.parent_phone != parent_phone:
        raise HTTPException(status_code=403, detail="无权查看该信息")

    star_level_name = volunteer.star_level.name if volunteer.star_level else None

    summary = schemas.ParentVolunteerSummary(
        volunteer_id=volunteer.id,
        name=volunteer.name,
        status=volunteer.status,
        star_level_name=star_level_name,
        total_service_hours=volunteer.total_service_hours or 0.0,
        points_balance=volunteer.points_balance or 0,
        registration_date=volunteer.registration_date,
        certification_date=volunteer.certification_date
    )

    service_records = db.query(models.ServiceRecord).filter(
        models.ServiceRecord.volunteer_id == volunteer_id
    ).order_by(models.ServiceRecord.service_date.desc()).all()

    parent_service_records = []
    for sr in service_records:
        topic = sr.time_slot.topic if sr.time_slot else None
        parent_service_records.append(schemas.ParentServiceRecord(
            id=sr.id,
            service_date=sr.service_date,
            service_hours=sr.service_hours,
            topic=topic,
            teacher_name=sr.teacher_name,
            teacher_rating=sr.teacher_rating,
            teacher_comments=sr.teacher_comments,
            points_awarded=sr.points_awarded or 0
        ))

    enrollments = db.query(models.Enrollment).filter(
        models.Enrollment.volunteer_id == volunteer_id
    ).all()

    training_records = []
    for en in enrollments:
        total_sessions = db.query(func.count(models.TrainingSession.id)).filter(
            models.TrainingSession.batch_id == en.batch_id
        ).scalar() or 0

        attended = db.query(func.count(models.SessionAttendance.id)).filter(
            models.SessionAttendance.enrollment_id == en.id,
            models.SessionAttendance.attended == True
        ).scalar() or 0

        attendance_rate = round(attended / total_sessions * 100, 2) if total_sessions > 0 else None

        batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == en.batch_id).first()
        topic_name = batch.topic.name if batch and batch.topic else None

        min_rate = batch.min_attendance_rate if batch else 80.0
        eligible = (attendance_rate or 0) >= min_rate if attendance_rate else False

        training_records.append(schemas.ParentTrainingRecord(
            batch_name=batch.name if batch else f"期次{en.batch_id}",
            topic_name=topic_name,
            status=en.status,
            total_sessions=total_sessions,
            attended_sessions=attended,
            attendance_rate=attendance_rate,
            eligible_for_assessment=eligible
        ))

    certificates = db.query(models.StarCertificate).filter(
        models.StarCertificate.volunteer_id == volunteer_id,
        models.StarCertificate.is_active == True
    ).order_by(models.StarCertificate.created_at.desc()).all()

    points_records = db.query(models.PointsRecord).filter(
        models.PointsRecord.volunteer_id == volunteer_id
    ).order_by(models.PointsRecord.created_at.desc()).limit(50).all()

    exchanges = db.query(models.BenefitExchange).filter(
        models.BenefitExchange.volunteer_id == volunteer_id
    ).order_by(models.BenefitExchange.created_at.desc()).all()

    return schemas.ParentView(
        volunteer=summary,
        service_records=parent_service_records,
        training_records=training_records,
        certificates=certificates,
        points_records=points_records,
        exchanges=exchanges
    )


@router.get("/volunteer/{volunteer_id}/service-records", response_model=List[schemas.ParentServiceRecord])
def get_parent_service_records(volunteer_id: int, parent_phone: str = None,
                               skip: int = 0, limit: int = 100,
                               db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    if parent_phone and volunteer.parent_phone != parent_phone:
        raise HTTPException(status_code=403, detail="无权查看该信息")

    service_records = db.query(models.ServiceRecord).filter(
        models.ServiceRecord.volunteer_id == volunteer_id
    ).order_by(models.ServiceRecord.service_date.desc()).offset(skip).limit(limit).all()

    result = []
    for sr in service_records:
        topic = sr.time_slot.topic if sr.time_slot else None
        result.append(schemas.ParentServiceRecord(
            id=sr.id,
            service_date=sr.service_date,
            service_hours=sr.service_hours,
            topic=topic,
            teacher_name=sr.teacher_name,
            teacher_rating=sr.teacher_rating,
            teacher_comments=sr.teacher_comments,
            points_awarded=sr.points_awarded or 0
        ))

    return result


@router.get("/volunteer/{volunteer_id}/training-records", response_model=List[schemas.ParentTrainingRecord])
def get_parent_training_records(volunteer_id: int, parent_phone: str = None,
                                db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    if parent_phone and volunteer.parent_phone != parent_phone:
        raise HTTPException(status_code=403, detail="无权查看该信息")

    enrollments = db.query(models.Enrollment).filter(
        models.Enrollment.volunteer_id == volunteer_id
    ).all()

    result = []
    for en in enrollments:
        total_sessions = db.query(func.count(models.TrainingSession.id)).filter(
            models.TrainingSession.batch_id == en.batch_id
        ).scalar() or 0

        attended = db.query(func.count(models.SessionAttendance.id)).filter(
            models.SessionAttendance.enrollment_id == en.id,
            models.SessionAttendance.attended == True
        ).scalar() or 0

        attendance_rate = round(attended / total_sessions * 100, 2) if total_sessions > 0 else None

        batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == en.batch_id).first()
        topic_name = batch.topic.name if batch and batch.topic else None

        min_rate = batch.min_attendance_rate if batch else 80.0
        eligible = (attendance_rate or 0) >= min_rate if attendance_rate else False

        result.append(schemas.ParentTrainingRecord(
            batch_name=batch.name if batch else f"期次{en.batch_id}",
            topic_name=topic_name,
            status=en.status,
            total_sessions=total_sessions,
            attended_sessions=attended,
            attendance_rate=attendance_rate,
            eligible_for_assessment=eligible
        ))

    return result


@router.get("/volunteer/{volunteer_id}/certificates", response_model=List[schemas.StarCertificate])
def get_parent_certificates(volunteer_id: int, parent_phone: str = None,
                            db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    if parent_phone and volunteer.parent_phone != parent_phone:
        raise HTTPException(status_code=403, detail="无权查看该信息")

    return db.query(models.StarCertificate).filter(
        models.StarCertificate.volunteer_id == volunteer_id,
        models.StarCertificate.is_active == True
    ).order_by(models.StarCertificate.created_at.desc()).all()


@router.get("/volunteer/{volunteer_id}/points-records", response_model=List[schemas.PointsRecord])
def get_parent_points_records(volunteer_id: int, parent_phone: str = None,
                              skip: int = 0, limit: int = 100,
                              db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    if parent_phone and volunteer.parent_phone != parent_phone:
        raise HTTPException(status_code=403, detail="无权查看该信息")

    return db.query(models.PointsRecord).filter(
        models.PointsRecord.volunteer_id == volunteer_id
    ).order_by(models.PointsRecord.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/volunteer/{volunteer_id}/exchanges", response_model=List[schemas.BenefitExchange])
def get_parent_exchanges(volunteer_id: int, parent_phone: str = None,
                         skip: int = 0, limit: int = 100,
                         db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    if parent_phone and volunteer.parent_phone != parent_phone:
        raise HTTPException(status_code=403, detail="无权查看该信息")

    return db.query(models.BenefitExchange).filter(
        models.BenefitExchange.volunteer_id == volunteer_id
    ).order_by(models.BenefitExchange.created_at.desc()).offset(skip).limit(limit).all()
