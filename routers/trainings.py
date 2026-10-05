from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from database import get_db
import models, schemas
from datetime import datetime

router = APIRouter(prefix="/api/trainings", tags=["培训管理"])


def _compute_attendance_rate(db: Session, enrollment: models.Enrollment) -> Optional[float]:
    total_sessions = db.query(func.count(models.TrainingSession.id)).filter(
        models.TrainingSession.batch_id == enrollment.batch_id
    ).scalar() or 0
    if total_sessions == 0:
        return None
    attended = db.query(func.count(models.SessionAttendance.id)).filter(
        models.SessionAttendance.enrollment_id == enrollment.id,
        models.SessionAttendance.attended == True
    ).scalar() or 0
    return round(attended / total_sessions * 100, 2)


def _is_eligible_for_assessment(db: Session, enrollment: models.Enrollment) -> bool:
    rate = _compute_attendance_rate(db, enrollment)
    if rate is None:
        return False
    batch = db.query(models.TrainingBatch).filter(
        models.TrainingBatch.id == enrollment.batch_id
    ).first()
    if not batch:
        return False
    return rate >= batch.min_attendance_rate


def _build_enrollment_detail(db: Session, enrollment: models.Enrollment) -> schemas.EnrollmentDetail:
    total_sessions = db.query(func.count(models.TrainingSession.id)).filter(
        models.TrainingSession.batch_id == enrollment.batch_id
    ).scalar() or 0
    attendance_count = db.query(func.count(models.SessionAttendance.id)).filter(
        models.SessionAttendance.enrollment_id == enrollment.id,
        models.SessionAttendance.attended == True
    ).scalar() or 0
    attendance_rate = _compute_attendance_rate(db, enrollment)
    eligible = _is_eligible_for_assessment(db, enrollment)
    batch = db.query(models.TrainingBatch).filter(
        models.TrainingBatch.id == enrollment.batch_id
    ).first()
    return schemas.EnrollmentDetail(
        id=enrollment.id,
        volunteer_id=enrollment.volunteer_id,
        batch_id=enrollment.batch_id,
        notes=enrollment.notes,
        status=enrollment.status,
        enrolled_at=enrollment.enrolled_at,
        completed_at=enrollment.completed_at,
        volunteer=enrollment.volunteer,
        batch=batch,
        attendance_count=attendance_count,
        total_sessions=total_sessions,
        attendance_rate=attendance_rate,
        eligible_for_assessment=eligible
    )


# ==================== 培训期次管理 ====================

@router.get("/batches", response_model=List[schemas.TrainingBatch])
def list_training_batches(
    status: Optional[str] = None,
    topic_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(models.TrainingBatch)
    if status:
        status_enum = None
        for s in models.TrainingBatchStatus:
            if s.value == status or s.name == status:
                status_enum = s
                break
        if status_enum:
            query = query.filter(models.TrainingBatch.status == status_enum)
    if topic_id:
        query = query.filter(models.TrainingBatch.topic_id == topic_id)
    return query.order_by(models.TrainingBatch.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/batches/{batch_id}", response_model=schemas.TrainingBatchDetail)
def get_training_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="培训期次不存在")
    enrollment_count = db.query(func.count(models.Enrollment.id)).filter(
        models.Enrollment.batch_id == batch_id,
        models.Enrollment.status == models.EnrollmentStatus.ENROLLED
    ).scalar() or 0
    return schemas.TrainingBatchDetail(
        id=batch.id,
        name=batch.name,
        topic_id=batch.topic_id,
        description=batch.description,
        min_attendance_rate=batch.min_attendance_rate,
        capacity=batch.capacity,
        start_date=batch.start_date,
        end_date=batch.end_date,
        status=batch.status,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        topic=batch.topic,
        sessions=sorted(batch.sessions, key=lambda s: s.session_no),
        enrollment_count=enrollment_count
    )


@router.post("/batches", response_model=schemas.TrainingBatch)
def create_training_batch(batch: schemas.TrainingBatchCreate, db: Session = Depends(get_db)):
    if batch.topic_id:
        topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == batch.topic_id).first()
        if not topic:
            raise HTTPException(status_code=404, detail="考核主题不存在")
    db_batch = models.TrainingBatch(**batch.model_dump())
    db.add(db_batch)
    db.commit()
    db.refresh(db_batch)
    return db_batch


@router.put("/batches/{batch_id}", response_model=schemas.TrainingBatch)
def update_training_batch(batch_id: int, update: schemas.TrainingBatchUpdate, db: Session = Depends(get_db)):
    batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="培训期次不存在")
    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(batch, key, value)
    db.commit()
    db.refresh(batch)
    return batch


@router.delete("/batches/{batch_id}")
def delete_training_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="培训期次不存在")
    db.delete(batch)
    db.commit()
    return {"message": "删除成功"}


# ==================== 课次管理 ====================

@router.get("/batches/{batch_id}/sessions", response_model=List[schemas.TrainingSession])
def list_batch_sessions(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="培训期次不存在")
    return sorted(batch.sessions, key=lambda s: s.session_no)


@router.post("/sessions", response_model=schemas.TrainingSession)
def create_training_session(session: schemas.TrainingSessionCreate, db: Session = Depends(get_db)):
    batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == session.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="培训期次不存在")
    existing = db.query(models.TrainingSession).filter(
        models.TrainingSession.batch_id == session.batch_id,
        models.TrainingSession.session_no == session.session_no
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该期次中已存在相同课次号")
    db_session = models.TrainingSession(**session.model_dump())
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    return db_session


@router.put("/sessions/{session_id}", response_model=schemas.TrainingSession)
def update_training_session(session_id: int, update: schemas.TrainingSessionUpdate, db: Session = Depends(get_db)):
    session = db.query(models.TrainingSession).filter(models.TrainingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="课次不存在")
    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(session, key, value)
    db.commit()
    db.refresh(session)
    return session


@router.delete("/sessions/{session_id}")
def delete_training_session(session_id: int, db: Session = Depends(get_db)):
    session = db.query(models.TrainingSession).filter(models.TrainingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="课次不存在")
    db.delete(session)
    db.commit()
    return {"message": "删除成功"}


# ==================== 报名入班管理 ====================

@router.get("/batches/{batch_id}/enrollments", response_model=List[schemas.EnrollmentDetail])
def list_batch_enrollments(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="培训期次不存在")
    enrollments = db.query(models.Enrollment).filter(
        models.Enrollment.batch_id == batch_id
    ).all()
    return [_build_enrollment_detail(db, e) for e in enrollments]


@router.get("/volunteers/{volunteer_id}/enrollments", response_model=List[schemas.EnrollmentDetail])
def list_volunteer_enrollments(volunteer_id: int, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    enrollments = db.query(models.Enrollment).filter(
        models.Enrollment.volunteer_id == volunteer_id
    ).all()
    return [_build_enrollment_detail(db, e) for e in enrollments]


@router.post("/enrollments/batch", response_model=dict)
def batch_enroll(data: schemas.BatchEnroll, db: Session = Depends(get_db)):
    batch = db.query(models.TrainingBatch).filter(models.TrainingBatch.id == data.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="培训期次不存在")

    current_count = db.query(func.count(models.Enrollment.id)).filter(
        models.Enrollment.batch_id == data.batch_id,
        models.Enrollment.status == models.EnrollmentStatus.ENROLLED
    ).scalar() or 0

    enrolled = []
    skipped = []
    for vid in data.volunteer_ids:
        volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == vid).first()
        if not volunteer:
            skipped.append({"volunteer_id": vid, "reason": "志愿者不存在"})
            continue
        if volunteer.status not in [models.VolunteerStatus.IN_TRAINING, models.VolunteerStatus.PENDING_ASSESSMENT]:
            skipped.append({"volunteer_id": vid, "reason": f"志愿者状态({volunteer.status.value})不可报名"})
            continue
        if current_count >= batch.capacity:
            skipped.append({"volunteer_id": vid, "reason": "班额已满"})
            continue
        existing = db.query(models.Enrollment).filter(
            models.Enrollment.batch_id == data.batch_id,
            models.Enrollment.volunteer_id == vid
        ).first()
        if existing:
            skipped.append({"volunteer_id": vid, "reason": "已报名该期次"})
            continue
        enrollment = models.Enrollment(
            volunteer_id=vid,
            batch_id=data.batch_id,
            status=models.EnrollmentStatus.ENROLLED
        )
        db.add(enrollment)
        enrolled.append(vid)
        current_count += 1

    db.commit()
    return {"enrolled_count": len(enrolled), "enrolled_ids": enrolled, "skipped": skipped}


@router.post("/enrollments/{enrollment_id}/drop")
def drop_enrollment(enrollment_id: int, db: Session = Depends(get_db)):
    enrollment = db.query(models.Enrollment).filter(models.Enrollment.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    enrollment.status = models.EnrollmentStatus.DROPPED
    db.commit()
    return {"message": "已退班"}


@router.post("/enrollments/{enrollment_id}/complete")
def complete_enrollment(enrollment_id: int, db: Session = Depends(get_db)):
    enrollment = db.query(models.Enrollment).filter(models.Enrollment.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    enrollment.status = models.EnrollmentStatus.COMPLETED
    enrollment.completed_at = datetime.utcnow()
    db.commit()

    eligible = _is_eligible_for_assessment(db, enrollment)
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == enrollment.volunteer_id).first()
    if eligible and volunteer and volunteer.status == models.VolunteerStatus.IN_TRAINING:
        volunteer.status = models.VolunteerStatus.PENDING_ASSESSMENT
        db.commit()

    return {"message": "已完成培训", "eligible_for_assessment": eligible}


@router.delete("/enrollments/{enrollment_id}")
def delete_enrollment(enrollment_id: int, db: Session = Depends(get_db)):
    enrollment = db.query(models.Enrollment).filter(models.Enrollment.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    db.delete(enrollment)
    db.commit()
    return {"message": "删除成功"}


# ==================== 课次出勤管理 ====================

@router.get("/sessions/{session_id}/attendances", response_model=List[schemas.SessionAttendanceWithVolunteer])
def list_session_attendances(session_id: int, db: Session = Depends(get_db)):
    session = db.query(models.TrainingSession).filter(models.TrainingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="课次不存在")
    attendances = db.query(models.SessionAttendance).filter(
        models.SessionAttendance.session_id == session_id
    ).all()
    return attendances


@router.post("/sessions/{session_id}/attendances/init")
def init_session_attendances(session_id: int, db: Session = Depends(get_db)):
    session = db.query(models.TrainingSession).filter(models.TrainingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="课次不存在")
    enrollments = db.query(models.Enrollment).filter(
        models.Enrollment.batch_id == session.batch_id,
        models.Enrollment.status.in_([models.EnrollmentStatus.ENROLLED, models.EnrollmentStatus.COMPLETED])
    ).all()
    created = 0
    for en in enrollments:
        existing = db.query(models.SessionAttendance).filter(
            models.SessionAttendance.session_id == session_id,
            models.SessionAttendance.enrollment_id == en.id
        ).first()
        if not existing:
            att = models.SessionAttendance(
                enrollment_id=en.id,
                session_id=session_id,
                volunteer_id=en.volunteer_id
            )
            db.add(att)
            created += 1
    db.commit()
    return {"created_count": created, "total_enrollments": len(enrollments)}


@router.post("/attendances/{attendance_id}/mark-v2", response_model=schemas.SessionAttendance)
def mark_session_attendance(
    attendance_id: int,
    update: schemas.SessionAttendanceUpdate,
    db: Session = Depends(get_db)
):
    attendance = db.query(models.SessionAttendance).filter(models.SessionAttendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="出勤记录不存在")
    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(attendance, key, value)
    attendance.checked_at = datetime.utcnow()
    db.commit()
    db.refresh(attendance)
    return attendance


@router.post("/sessions/{session_id}/attendances/batch-mark", response_model=dict)
def batch_mark_session_attendance(
    session_id: int,
    marks: List[schemas.SessionAttendanceCreate],
    db: Session = Depends(get_db)
):
    session = db.query(models.TrainingSession).filter(models.TrainingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="课次不存在")
    updated = 0
    for mark in marks:
        attendance = db.query(models.SessionAttendance).filter(
            models.SessionAttendance.session_id == session_id,
            models.SessionAttendance.enrollment_id == mark.enrollment_id
        ).first()
        if attendance:
            attendance.attended = mark.attended
            attendance.late = mark.late
            attendance.leave_early = mark.leave_early
            if mark.remarks:
                attendance.remarks = mark.remarks
            attendance.checked_at = datetime.utcnow()
            updated += 1
    db.commit()
    return {"updated_count": updated}


# ==================== 出勤校验与考核资格 ====================

@router.get("/enrollments/{enrollment_id}/attendance-check")
def check_enrollment_attendance(enrollment_id: int, db: Session = Depends(get_db)):
    enrollment = db.query(models.Enrollment).filter(models.Enrollment.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    detail = _build_enrollment_detail(db, enrollment)
    return {
        "enrollment_id": enrollment_id,
        "volunteer_id": enrollment.volunteer_id,
        "batch_id": enrollment.batch_id,
        "min_attendance_rate": enrollment.batch.min_attendance_rate if enrollment.batch else 80.0,
        "total_sessions": detail.total_sessions,
        "attended_count": detail.attendance_count,
        "attendance_rate": detail.attendance_rate,
        "eligible_for_assessment": detail.eligible_for_assessment
    }


@router.post("/enrollments/{enrollment_id}/promote-to-assessment")
def promote_to_assessment(enrollment_id: int, db: Session = Depends(get_db)):
    enrollment = db.query(models.Enrollment).filter(models.Enrollment.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    if not _is_eligible_for_assessment(db, enrollment):
        raise HTTPException(status_code=400, detail="出勤未达标，无法进入考核阶段")
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == enrollment.volunteer_id).first()
    if volunteer:
        if volunteer.status != models.VolunteerStatus.IN_TRAINING:
            raise HTTPException(status_code=400, detail=f"当前状态({volunteer.status.value})不可变更")
        volunteer.status = models.VolunteerStatus.PENDING_ASSESSMENT
        enrollment.status = models.EnrollmentStatus.COMPLETED
        enrollment.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(volunteer)
    return {"message": "已进入考核阶段", "volunteer_status": volunteer.status.value if volunteer else None}


# ==================== 旧版简单培训课程（保留向后兼容） ====================

@router.get("/legacy", response_model=List[schemas.Training])
def list_trainings(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(models.Training).offset(skip).limit(limit).all()


@router.get("/legacy/{training_id}", response_model=schemas.Training)
def get_training(training_id: int, db: Session = Depends(get_db)):
    training = db.query(models.Training).filter(models.Training.id == training_id).first()
    if not training:
        raise HTTPException(status_code=404, detail="培训不存在")
    return training


@router.post("/legacy", response_model=schemas.Training)
def create_training(training: schemas.TrainingCreate, db: Session = Depends(get_db)):
    db_training = models.Training(**training.model_dump())
    db.add(db_training)
    db.commit()
    db.refresh(db_training)
    return db_training


@router.post("/legacy/{training_id}/assign", response_model=dict)
def assign_volunteers(training_id: int, assign: schemas.TrainingAssign, db: Session = Depends(get_db)):
    training = db.query(models.Training).filter(models.Training.id == training_id).first()
    if not training:
        raise HTTPException(status_code=404, detail="培训不存在")
    assigned = []
    for vid in assign.volunteer_ids:
        volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == vid).first()
        if volunteer and volunteer.status in [models.VolunteerStatus.IN_TRAINING, models.VolunteerStatus.PENDING_REVIEW]:
            existing = db.query(models.TrainingAttendance).filter(
                models.TrainingAttendance.training_id == training_id,
                models.TrainingAttendance.volunteer_id == vid
            ).first()
            if not existing:
                attendance = models.TrainingAttendance(
                    volunteer_id=vid,
                    training_id=training_id
                )
                db.add(attendance)
                assigned.append(vid)
    db.commit()
    return {"assigned_count": len(assigned), "assigned_ids": assigned}


@router.post("/legacy-attendance/{attendance_id}/mark")
def mark_attendance_legacy(attendance_id: int, attended: int = 1, remarks: str = None, db: Session = Depends(get_db)):
    attendance = db.query(models.TrainingAttendance).filter(models.TrainingAttendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="考勤记录不存在")
    attendance.attended = attended
    if remarks:
        attendance.remarks = remarks
    db.commit()
    return {"message": "考勤已更新"}


@router.delete("/legacy/{training_id}")
def delete_training(training_id: int, db: Session = Depends(get_db)):
    training = db.query(models.Training).filter(models.Training.id == training_id).first()
    if not training:
        raise HTTPException(status_code=404, detail="培训不存在")
    db.delete(training)
    db.commit()
    return {"message": "删除成功"}
