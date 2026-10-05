from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List
from database import get_db
import models, schemas
from datetime import date, datetime

router = APIRouter(prefix="/api/stats", tags=["统计分析"])


@router.get("/overview", response_model=schemas.OverviewStats)
def get_overview_stats(db: Session = Depends(get_db)):
    total = db.query(func.count(models.Volunteer.id)).scalar() or 0
    pending_review = db.query(func.count(models.Volunteer.id)).filter(
        models.Volunteer.status == models.VolunteerStatus.PENDING_REVIEW
    ).scalar() or 0
    in_training = db.query(func.count(models.Volunteer.id)).filter(
        models.Volunteer.status == models.VolunteerStatus.IN_TRAINING
    ).scalar() or 0
    pending_assessment = db.query(func.count(models.Volunteer.id)).filter(
        models.Volunteer.status == models.VolunteerStatus.PENDING_ASSESSMENT
    ).scalar() or 0
    certified = db.query(func.count(models.Volunteer.id)).filter(
        models.Volunteer.status == models.VolunteerStatus.CERTIFIED
    ).scalar() or 0
    disabled = db.query(func.count(models.Volunteer.id)).filter(
        models.Volunteer.status == models.VolunteerStatus.DISABLED
    ).scalar() or 0
    total_hours = db.query(func.sum(models.ServiceRecord.service_hours)).scalar() or 0.0

    today = date.today()
    month_start = date(today.year, today.month, 1)
    this_month_new = db.query(func.count(models.Volunteer.id)).filter(
        models.Volunteer.registration_date >= month_start
    ).scalar() or 0
    this_month_hours = db.query(func.sum(models.ServiceRecord.service_hours)).filter(
        models.ServiceRecord.service_date >= month_start
    ).scalar() or 0.0

    return schemas.OverviewStats(
        total_volunteers=total,
        pending_review=pending_review,
        in_training=in_training,
        pending_assessment=pending_assessment,
        certified=certified,
        disabled=disabled,
        total_service_hours=float(total_hours),
        this_month_new=this_month_new,
        this_month_service_hours=float(this_month_hours)
    )


@router.get("/by-school", response_model=List[schemas.SchoolStats])
def get_stats_by_school(db: Session = Depends(get_db)):
    schools = db.query(models.School).all()
    result = []

    for school in schools:
        volunteers_in_school = db.query(models.Volunteer).filter(
            models.Volunteer.school_id == school.id
        ).all()

        total = len(volunteers_in_school)
        pending_review = sum(1 for v in volunteers_in_school if v.status == models.VolunteerStatus.PENDING_REVIEW)
        in_training = sum(1 for v in volunteers_in_school if v.status == models.VolunteerStatus.IN_TRAINING)
        pending_assessment = sum(1 for v in volunteers_in_school if v.status == models.VolunteerStatus.PENDING_ASSESSMENT)
        certified = sum(1 for v in volunteers_in_school if v.status == models.VolunteerStatus.CERTIFIED)
        disabled = sum(1 for v in volunteers_in_school if v.status == models.VolunteerStatus.DISABLED)

        volunteer_ids = [v.id for v in volunteers_in_school]
        total_hours = 0.0
        if volunteer_ids:
            total_hours = db.query(func.sum(models.ServiceRecord.service_hours)).filter(
                models.ServiceRecord.volunteer_id.in_(volunteer_ids)
            ).scalar() or 0.0

        pass_rate = None
        if volunteer_ids:
            total_assess = db.query(func.count(models.Assessment.id)).filter(
                models.Assessment.volunteer_id.in_(volunteer_ids),
                models.Assessment.result != models.AssessmentResult.PENDING
            ).scalar() or 0
            passed_assess = db.query(func.count(models.Assessment.id)).filter(
                models.Assessment.volunteer_id.in_(volunteer_ids),
                models.Assessment.result == models.AssessmentResult.PASSED
            ).scalar() or 0
            if total_assess > 0:
                pass_rate = round(passed_assess / total_assess * 100, 2)

        result.append(schemas.SchoolStats(
            school_id=school.id,
            school_name=school.name,
            total_count=total,
            pending_review=pending_review,
            in_training=in_training,
            pending_assessment=pending_assessment,
            certified=certified,
            disabled=disabled,
            total_service_hours=float(total_hours),
            assessment_pass_rate=pass_rate
        ))

    return sorted(result, key=lambda x: -x.total_count)


@router.get("/by-star", response_model=List[schemas.StarStats])
def get_stats_by_star(db: Session = Depends(get_db)):
    star_levels = db.query(models.StarLevel).order_by(models.StarLevel.min_hours).all()
    result = []

    no_star_count = db.query(func.count(models.Volunteer.id)).filter(
        models.Volunteer.star_level_id.is_(None)
    ).scalar() or 0
    no_star_hours = db.query(func.sum(models.ServiceRecord.service_hours)).join(
        models.Volunteer, models.ServiceRecord.volunteer_id == models.Volunteer.id
    ).filter(
        models.Volunteer.star_level_id.is_(None)
    ).scalar() or 0.0
    result.append(schemas.StarStats(
        star_level_id=None,
        star_name="未评星",
        volunteer_count=no_star_count,
        total_service_hours=float(no_star_hours)
    ))

    for sl in star_levels:
        count = db.query(func.count(models.Volunteer.id)).filter(
            models.Volunteer.star_level_id == sl.id
        ).scalar() or 0
        hours = db.query(func.sum(models.ServiceRecord.service_hours)).join(
            models.Volunteer, models.ServiceRecord.volunteer_id == models.Volunteer.id
        ).filter(
            models.Volunteer.star_level_id == sl.id
        ).scalar() or 0.0
        result.append(schemas.StarStats(
            star_level_id=sl.id,
            star_name=sl.name,
            volunteer_count=count,
            total_service_hours=float(hours)
        ))

    return result


@router.get("/monthly", response_model=List[schemas.MonthlyStats])
def get_monthly_stats(year: int = None, months: int = 12, db: Session = Depends(get_db)):
    today = date.today()
    if not year:
        year = today.year

    result = []
    for m in range(1, 13):
        if m > today.month and year >= today.year:
            continue
        month_start = date(year, m, 1)
        if m == 12:
            month_end = date(year, 12, 31)
        else:
            month_end = date(year, m + 1, 1) if False else date(year, m, 28)

        from calendar import monthrange
        last_day = monthrange(year, m)[1]
        month_end = date(year, m, last_day)

        registered = db.query(func.count(models.Volunteer.id)).filter(
            and_(
                models.Volunteer.registration_date >= month_start,
                models.Volunteer.registration_date <= month_end
            )
        ).scalar() or 0

        certified = db.query(func.count(models.Volunteer.id)).filter(
            and_(
                models.Volunteer.certification_date >= month_start,
                models.Volunteer.certification_date <= month_end
            )
        ).scalar() or 0

        service_hours = db.query(func.sum(models.ServiceRecord.service_hours)).filter(
            and_(
                models.ServiceRecord.service_date >= month_start,
                models.ServiceRecord.service_date <= month_end
            )
        ).scalar() or 0.0

        total_assess = db.query(func.count(models.Assessment.id)).filter(
            and_(
                models.Assessment.assessment_date >= month_start,
                models.Assessment.assessment_date <= month_end,
                models.Assessment.result != models.AssessmentResult.PENDING
            )
        ).scalar() or 0

        passed_assess = db.query(func.count(models.Assessment.id)).filter(
            and_(
                models.Assessment.assessment_date >= month_start,
                models.Assessment.assessment_date <= month_end,
                models.Assessment.result == models.AssessmentResult.PASSED
            )
        ).scalar() or 0

        pass_rate = None
        if total_assess > 0:
            pass_rate = round(passed_assess / total_assess * 100, 2)

        result.append(schemas.MonthlyStats(
            year=year,
            month=m,
            registered_count=registered,
            certified_count=certified,
            total_service_hours=float(service_hours),
            assessment_count=total_assess,
            assessment_pass_count=passed_assess,
            assessment_pass_rate=pass_rate
        ))

    return result[-months:]


@router.get("/training-batches", response_model=List[schemas.TrainingBatchStats])
def get_training_batch_stats(db: Session = Depends(get_db)):
    batches = db.query(models.TrainingBatch).order_by(models.TrainingBatch.created_at.desc()).all()
    result = []

    for batch in batches:
        total_sessions = db.query(func.count(models.TrainingSession.id)).filter(
            models.TrainingSession.batch_id == batch.id
        ).scalar() or 0

        enrollment_count = db.query(func.count(models.Enrollment.id)).filter(
            models.Enrollment.batch_id == batch.id,
            models.Enrollment.status.in_([models.EnrollmentStatus.ENROLLED, models.EnrollmentStatus.COMPLETED])
        ).scalar() or 0

        enrollment_ids = [e.id for e in db.query(models.Enrollment).filter(
            models.Enrollment.batch_id == batch.id
        ).all()]

        total_attendance_marked = 0
        attended_count = 0
        if enrollment_ids and total_sessions > 0:
            total_possible = enrollment_count * total_sessions
            attended_count = db.query(func.count(models.SessionAttendance.id)).filter(
                models.SessionAttendance.enrollment_id.in_(enrollment_ids),
                models.SessionAttendance.attended == True
            ).scalar() or 0
            total_attendance_marked = attended_count

        attendance_rate = None
        if total_sessions > 0 and enrollment_count > 0:
            total_possible = total_sessions * enrollment_count
            if total_possible > 0:
                attendance_rate = round(attended_count / total_possible * 100, 2)

        eligible_count = 0
        if total_sessions > 0:
            enrollments = db.query(models.Enrollment).filter(
                models.Enrollment.batch_id == batch.id
            ).all()
            for en in enrollments:
                en_attended = db.query(func.count(models.SessionAttendance.id)).filter(
                    models.SessionAttendance.enrollment_id == en.id,
                    models.SessionAttendance.attended == True
                ).scalar() or 0
                rate = en_attended / total_sessions * 100 if total_sessions > 0 else 0
                if rate >= batch.min_attendance_rate:
                    eligible_count += 1

        batch_assessments = db.query(models.Assessment).filter(
            models.Assessment.training_batch_id == batch.id
        ).all()
        assessment_count = len(batch_assessments)

        first_time_pass = [a for a in batch_assessments if not a.is_retake]
        first_time_pass_count = sum(1 for a in first_time_pass if a.result == models.AssessmentResult.PASSED)

        first_time_pass_rate = None
        if len(first_time_pass) > 0:
            first_time_pass_rate = round(first_time_pass_count / len(first_time_pass) * 100, 2)

        topic_name = batch.topic.name if batch.topic else None

        result.append(schemas.TrainingBatchStats(
            batch_id=batch.id,
            batch_name=batch.name,
            topic_name=topic_name,
            total_sessions=total_sessions,
            enrollment_count=enrollment_count,
            total_attendance_marked=attended_count,
            attendance_rate=attendance_rate,
            eligible_count=eligible_count,
            assessment_count=assessment_count,
            first_time_pass_count=first_time_pass_count,
            first_time_pass_rate=first_time_pass_rate
        ))

    return result


@router.get("/assessment-topics", response_model=List[schemas.TopicAssessmentStats])
def get_topic_assessment_stats(db: Session = Depends(get_db)):
    topics = db.query(models.AssessmentTopic).all()
    result = []

    for topic in topics:
        total = db.query(models.Assessment).filter(
            models.Assessment.topic_id == topic.id,
            models.Assessment.result != models.AssessmentResult.PENDING
        ).all()
        total_assessments = len(total)

        first_time = [a for a in total if not a.is_retake]
        first_time_count = len(first_time)
        first_time_pass_count = sum(1 for a in first_time if a.result == models.AssessmentResult.PASSED)

        retake = [a for a in total if a.is_retake]
        retake_count = len(retake)
        retake_pass_count = sum(1 for a in retake if a.result == models.AssessmentResult.PASSED)

        total_pass_count = first_time_pass_count + retake_pass_count

        first_time_pass_rate = None
        if first_time_count > 0:
            first_time_pass_rate = round(first_time_pass_count / first_time_count * 100, 2)

        total_pass_rate = None
        if total_assessments > 0:
            total_pass_rate = round(total_pass_count / total_assessments * 100, 2)

        result.append(schemas.TopicAssessmentStats(
            topic_id=topic.id,
            topic_name=topic.name,
            total_assessments=total_assessments,
            first_time_count=first_time_count,
            first_time_pass_count=first_time_pass_count,
            first_time_pass_rate=first_time_pass_rate,
            retake_count=retake_count,
            retake_pass_count=retake_pass_count,
            total_pass_count=total_pass_count,
            total_pass_rate=total_pass_rate
        ))

    return result


@router.get("/points", response_model=schemas.PointsStats)
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


@router.get("/points/monthly")
def get_monthly_points_stats(year: int = None, months: int = 12, db: Session = Depends(get_db)):
    today = date.today()
    if not year:
        year = today.year

    result = []
    for m in range(1, 13):
        if m > today.month and year >= today.year:
            continue
        month_start = date(year, m, 1)
        from calendar import monthrange
        last_day = monthrange(year, m)[1]
        month_end = date(year, m, last_day)

        earned = db.query(func.sum(models.PointsRecord.points_amount)).filter(
            and_(
                models.PointsRecord.created_at >= month_start,
                models.PointsRecord.created_at <= month_end,
                models.PointsRecord.points_type == models.PointsType.EARN
            )
        ).scalar() or 0

        spent = db.query(func.sum(models.PointsRecord.points_amount)).filter(
            and_(
                models.PointsRecord.created_at >= month_start,
                models.PointsRecord.created_at <= month_end,
                models.PointsRecord.points_type == models.PointsType.SPEND
            )
        ).scalar() or 0

        earn_count = db.query(func.count(models.PointsRecord.id)).filter(
            and_(
                models.PointsRecord.created_at >= month_start,
                models.PointsRecord.created_at <= month_end,
                models.PointsRecord.points_type == models.PointsType.EARN
            )
        ).scalar() or 0

        spend_count = db.query(func.count(models.PointsRecord.id)).filter(
            and_(
                models.PointsRecord.created_at >= month_start,
                models.PointsRecord.created_at <= month_end,
                models.PointsRecord.points_type == models.PointsType.SPEND
            )
        ).scalar() or 0

        result.append({
            "year": year,
            "month": m,
            "points_earned": earned,
            "points_spent": spent,
            "net_points": earned - spent,
            "earn_count": earn_count,
            "spend_count": spend_count
        })

    return result[-months:]


@router.get("/points/by-source")
def get_points_by_source(db: Session = Depends(get_db)):
    sources = [s for s in models.PointsSource]
    result = []

    for source in sources:
        total_earned = db.query(func.sum(models.PointsRecord.points_amount)).filter(
            models.PointsRecord.source == source,
            models.PointsRecord.points_type == models.PointsType.EARN
        ).scalar() or 0

        total_spent = db.query(func.sum(models.PointsRecord.points_amount)).filter(
            models.PointsRecord.source == source,
            models.PointsRecord.points_type == models.PointsType.SPEND
        ).scalar() or 0

        count = db.query(func.count(models.PointsRecord.id)).filter(
            models.PointsRecord.source == source
        ).scalar() or 0

        result.append({
            "source": source.value,
            "source_name": source.name,
            "total_earned": total_earned,
            "total_spent": total_spent,
            "net": total_earned - total_spent,
            "record_count": count
        })

    return result


@router.get("/exchanges", response_model=schemas.ExchangeStats)
def get_exchange_stats(db: Session = Depends(get_db)):
    total_exchanges = db.query(func.count(models.BenefitExchange.id)).scalar() or 0
    pending_exchanges = db.query(func.count(models.BenefitExchange.id)).filter(
        models.BenefitExchange.status == models.ExchangeStatus.PENDING
    ).scalar() or 0
    completed_exchanges = db.query(func.count(models.BenefitExchange.id)).filter(
        models.BenefitExchange.status == models.ExchangeStatus.COMPLETED
    ).scalar() or 0
    cancelled_exchanges = db.query(func.count(models.BenefitExchange.id)).filter(
        models.BenefitExchange.status == models.ExchangeStatus.CANCELLED
    ).scalar() or 0
    total_points_spent = db.query(func.sum(models.BenefitExchange.points_spent)).scalar() or 0

    return schemas.ExchangeStats(
        total_exchanges=total_exchanges,
        pending_exchanges=pending_exchanges,
        completed_exchanges=completed_exchanges,
        cancelled_exchanges=cancelled_exchanges,
        total_points_spent=total_points_spent
    )


@router.get("/exchanges/by-benefit", response_model=List[schemas.BenefitStats])
def get_exchange_by_benefit(db: Session = Depends(get_db)):
    benefits = db.query(models.Benefit).all()
    result = []

    for benefit in benefits:
        exchanges = db.query(models.BenefitExchange).filter(
            models.BenefitExchange.benefit_id == benefit.id,
            models.BenefitExchange.status != models.ExchangeStatus.CANCELLED
        ).all()

        total_exchanged = len(exchanges)
        total_quantity = sum(e.quantity for e in exchanges)
        total_points = sum(e.points_spent for e in exchanges)

        result.append(schemas.BenefitStats(
            benefit_id=benefit.id,
            benefit_name=benefit.name,
            benefit_type=benefit.benefit_type,
            total_exchanged=total_exchanged,
            total_quantity=total_quantity,
            total_points=total_points
        ))

    return sorted(result, key=lambda x: -x.total_points)


@router.get("/exchanges/monthly")
def get_monthly_exchange_stats(year: int = None, months: int = 12, db: Session = Depends(get_db)):
    today = date.today()
    if not year:
        year = today.year

    result = []
    for m in range(1, 13):
        if m > today.month and year >= today.year:
            continue
        month_start = date(year, m, 1)
        from calendar import monthrange
        last_day = monthrange(year, m)[1]
        month_end = date(year, m, last_day)

        total_exchanges = db.query(func.count(models.BenefitExchange.id)).filter(
            and_(
                models.BenefitExchange.created_at >= month_start,
                models.BenefitExchange.created_at <= month_end
            )
        ).scalar() or 0

        completed_exchanges = db.query(func.count(models.BenefitExchange.id)).filter(
            and_(
                models.BenefitExchange.created_at >= month_start,
                models.BenefitExchange.created_at <= month_end,
                models.BenefitExchange.status == models.ExchangeStatus.COMPLETED
            )
        ).scalar() or 0

        total_points = db.query(func.sum(models.BenefitExchange.points_spent)).filter(
            and_(
                models.BenefitExchange.created_at >= month_start,
                models.BenefitExchange.created_at <= month_end
            )
        ).scalar() or 0

        result.append({
            "year": year,
            "month": m,
            "total_exchanges": total_exchanges,
            "completed_exchanges": completed_exchanges,
            "total_points_spent": total_points
        })

    return result[-months:]
