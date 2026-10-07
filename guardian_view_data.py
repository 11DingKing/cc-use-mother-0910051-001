"""家长/监护人视图数据构造（被 legacy 家长入口与授权监护人入口共用）。"""
from sqlalchemy import func
from sqlalchemy.orm import Session

import models, schemas


def build_parent_service_records(db: Session, volunteer_id: int):
    service_records = db.query(models.ServiceRecord).filter(
        models.ServiceRecord.volunteer_id == volunteer_id
    ).order_by(models.ServiceRecord.service_date.desc()).all()

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


def build_parent_training_records(db: Session, volunteer_id: int):
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
            models.SessionAttendance.attended == True  # noqa: E712
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


def build_parent_view(db: Session, volunteer: models.Volunteer) -> schemas.ParentView:
    volunteer_id = volunteer.id
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

    certificates = db.query(models.StarCertificate).filter(
        models.StarCertificate.volunteer_id == volunteer_id,
        models.StarCertificate.is_active == True  # noqa: E712
    ).order_by(models.StarCertificate.created_at.desc()).all()

    points_records = db.query(models.PointsRecord).filter(
        models.PointsRecord.volunteer_id == volunteer_id
    ).order_by(models.PointsRecord.created_at.desc()).limit(50).all()

    exchanges = db.query(models.BenefitExchange).filter(
        models.BenefitExchange.volunteer_id == volunteer_id
    ).order_by(models.BenefitExchange.created_at.desc()).all()

    return schemas.ParentView(
        volunteer=summary,
        service_records=build_parent_service_records(db, volunteer_id),
        training_records=build_parent_training_records(db, volunteer_id),
        certificates=certificates,
        points_records=points_records,
        exchanges=exchanges
    )
