from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from database import get_db
import models, schemas
from datetime import date, datetime

router = APIRouter(prefix="/api/assessments", tags=["考核管理"])


def _generate_certificate_no(topic_id: int, volunteer_id: int, assessment_id: int) -> str:
    today = date.today()
    return f"HLJ{today.year}{topic_id:03d}{volunteer_id:04d}{assessment_id:05d}"


def _issue_certification(db: Session, assessment: models.Assessment):
    if not assessment.topic_id:
        return None
    existing = db.query(models.VolunteerCertification).filter(
        models.VolunteerCertification.volunteer_id == assessment.volunteer_id,
        models.VolunteerCertification.topic_id == assessment.topic_id,
        models.VolunteerCertification.is_active == True
    ).first()
    if existing:
        return existing
    cert = models.VolunteerCertification(
        volunteer_id=assessment.volunteer_id,
        topic_id=assessment.topic_id,
        assessment_id=assessment.id,
        certificate_no=_generate_certificate_no(assessment.topic_id, assessment.volunteer_id, assessment.id),
        issued_date=date.today(),
        is_active=True
    )
    db.add(cert)
    db.flush()
    return cert


# ==================== 考核主题管理（具体路径前置） ====================

@router.get("/topics", response_model=List[schemas.AssessmentTopic])
def list_assessment_topics(is_active: Optional[bool] = None, db: Session = Depends(get_db)):
    query = db.query(models.AssessmentTopic)
    if is_active is not None:
        query = query.filter(models.AssessmentTopic.is_active == is_active)
    return query.order_by(models.AssessmentTopic.id).all()


@router.post("/topics", response_model=schemas.AssessmentTopic)
def create_assessment_topic(topic: schemas.AssessmentTopicCreate, db: Session = Depends(get_db)):
    existing = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.name == topic.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="该考核主题已存在")
    db_topic = models.AssessmentTopic(**topic.model_dump())
    db.add(db_topic)
    db.commit()
    db.refresh(db_topic)
    return db_topic


@router.get("/topics/{topic_id}", response_model=schemas.AssessmentTopic)
def get_assessment_topic(topic_id: int, db: Session = Depends(get_db)):
    topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="考核主题不存在")
    return topic


@router.put("/topics/{topic_id}", response_model=schemas.AssessmentTopic)
def update_assessment_topic(topic_id: int, update: schemas.AssessmentTopicUpdate, db: Session = Depends(get_db)):
    topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="考核主题不存在")
    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(topic, key, value)
    db.commit()
    db.refresh(topic)
    return topic


@router.delete("/topics/{topic_id}")
def delete_assessment_topic(topic_id: int, db: Session = Depends(get_db)):
    topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="考核主题不存在")
    db.delete(topic)
    db.commit()
    return {"message": "删除成功"}


# ==================== 评分项管理 ====================

@router.get("/topics/{topic_id}/criteria", response_model=List[schemas.AssessmentCriterion])
def list_topic_criteria(topic_id: int, db: Session = Depends(get_db)):
    topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="考核主题不存在")
    return sorted(topic.criteria, key=lambda c: c.sort_order)


@router.post("/criteria", response_model=schemas.AssessmentCriterion)
def create_assessment_criterion(criterion: schemas.AssessmentCriterionCreate, db: Session = Depends(get_db)):
    topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == criterion.topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="考核主题不存在")
    db_criterion = models.AssessmentCriterion(**criterion.model_dump())
    db.add(db_criterion)
    db.commit()
    db.refresh(db_criterion)
    return db_criterion


@router.get("/criteria/{criterion_id}", response_model=schemas.AssessmentCriterion)
def get_assessment_criterion(criterion_id: int, db: Session = Depends(get_db)):
    criterion = db.query(models.AssessmentCriterion).filter(models.AssessmentCriterion.id == criterion_id).first()
    if not criterion:
        raise HTTPException(status_code=404, detail="评分项不存在")
    return criterion


@router.put("/criteria/{criterion_id}", response_model=schemas.AssessmentCriterion)
def update_assessment_criterion(criterion_id: int, update: schemas.AssessmentCriterionUpdate, db: Session = Depends(get_db)):
    criterion = db.query(models.AssessmentCriterion).filter(models.AssessmentCriterion.id == criterion_id).first()
    if not criterion:
        raise HTTPException(status_code=404, detail="评分项不存在")
    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(criterion, key, value)
    db.commit()
    db.refresh(criterion)
    return criterion


@router.delete("/criteria/{criterion_id}")
def delete_assessment_criterion(criterion_id: int, db: Session = Depends(get_db)):
    criterion = db.query(models.AssessmentCriterion).filter(models.AssessmentCriterion.id == criterion_id).first()
    if not criterion:
        raise HTTPException(status_code=404, detail="评分项不存在")
    db.delete(criterion)
    db.commit()
    return {"message": "删除成功"}


# ==================== 考核题目管理 ====================

@router.get("/topics/{topic_id}/questions", response_model=List[schemas.AssessmentQuestion])
def list_topic_questions(topic_id: int, db: Session = Depends(get_db)):
    topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="考核主题不存在")
    return sorted(topic.questions, key=lambda q: q.sort_order)


@router.post("/questions", response_model=schemas.AssessmentQuestion)
def create_assessment_question(question: schemas.AssessmentQuestionCreate, db: Session = Depends(get_db)):
    topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == question.topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="考核主题不存在")
    db_question = models.AssessmentQuestion(**question.model_dump())
    db.add(db_question)
    db.commit()
    db.refresh(db_question)
    return db_question


@router.get("/questions/{question_id}", response_model=schemas.AssessmentQuestion)
def get_assessment_question(question_id: int, db: Session = Depends(get_db)):
    question = db.query(models.AssessmentQuestion).filter(models.AssessmentQuestion.id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="考核题目不存在")
    return question


@router.put("/questions/{question_id}", response_model=schemas.AssessmentQuestion)
def update_assessment_question(question_id: int, update: schemas.AssessmentQuestionUpdate, db: Session = Depends(get_db)):
    question = db.query(models.AssessmentQuestion).filter(models.AssessmentQuestion.id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="考核题目不存在")
    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(question, key, value)
    db.commit()
    db.refresh(question)
    return question


@router.delete("/questions/{question_id}")
def delete_assessment_question(question_id: int, db: Session = Depends(get_db)):
    question = db.query(models.AssessmentQuestion).filter(models.AssessmentQuestion.id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="考核题目不存在")
    db.delete(question)
    db.commit()
    return {"message": "删除成功"}


# ==================== 新版考核创建与评分 ====================

@router.post("/create-v2", response_model=schemas.AssessmentDetail)
def create_assessment_v2(data: schemas.AssessmentCreateV2, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == data.volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    if volunteer.status != models.VolunteerStatus.PENDING_ASSESSMENT:
        raise HTTPException(status_code=400, detail=f"志愿者状态({volunteer.status.value})不可安排考核")
    if data.topic_id:
        topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == data.topic_id).first()
        if not topic:
            raise HTTPException(status_code=404, detail="考核主题不存在")
    topic_name = None
    if data.topic_id:
        topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == data.topic_id).first()
        topic_name = topic.name if topic else None

    attempt_no = 1
    prev_assessments = db.query(models.Assessment).filter(
        models.Assessment.volunteer_id == data.volunteer_id,
        models.Assessment.topic_id == data.topic_id
    ).count()
    if prev_assessments > 0:
        attempt_no = prev_assessments + 1

    db_assessment = models.Assessment(
        volunteer_id=data.volunteer_id,
        topic_id=data.topic_id,
        training_batch_id=data.training_batch_id,
        assessment_date=data.assessment_date,
        topic=topic_name,
        result=models.AssessmentResult.PENDING,
        is_retake=attempt_no > 1,
        attempt_no=attempt_no,
        examiner=data.examiner,
        comments=data.comments
    )
    db.add(db_assessment)
    db.commit()
    db.refresh(db_assessment)
    return db_assessment


@router.post("/{assessment_id}/submit-scores", response_model=schemas.AssessmentDetail)
def submit_assessment_scores(assessment_id: int, data: schemas.AssessmentSubmitScores, db: Session = Depends(get_db)):
    assessment = db.query(models.Assessment).filter(models.Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    if assessment.result != models.AssessmentResult.PENDING:
        raise HTTPException(status_code=400, detail="该考核已完成评分，不可重复提交")

    for score_data in data.scores:
        criterion = db.query(models.AssessmentCriterion).filter(
            models.AssessmentCriterion.id == score_data.criterion_id
        ).first()
        if not criterion:
            raise HTTPException(status_code=400, detail=f"评分项ID={score_data.criterion_id}不存在")
        if score_data.score < 0 or score_data.score > criterion.max_score:
            raise HTTPException(
                status_code=400,
                detail=f"评分项'{criterion.name}'分数超出范围(0-{criterion.max_score})"
            )
        existing_score = db.query(models.AssessmentScore).filter(
            models.AssessmentScore.assessment_id == assessment_id,
            models.AssessmentScore.criterion_id == score_data.criterion_id
        ).first()
        if existing_score:
            existing_score.score = score_data.score
            existing_score.comments = score_data.comments
        else:
            score = models.AssessmentScore(
                assessment_id=assessment_id,
                criterion_id=score_data.criterion_id,
                score=score_data.score,
                comments=score_data.comments
            )
            db.add(score)

    db.flush()

    total_score = db.query(func.sum(models.AssessmentScore.score)).filter(
        models.AssessmentScore.assessment_id == assessment_id
    ).scalar() or 0.0
    assessment.score = float(total_score)

    if data.examiner:
        assessment.examiner = data.examiner
    if data.comments:
        assessment.comments = data.comments

    pass_score = 60.0
    if assessment.topic_id:
        topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == assessment.topic_id).first()
        if topic:
            pass_score = topic.pass_score

    if data.result:
        assessment.result = data.result
    else:
        if assessment.score >= pass_score:
            assessment.result = models.AssessmentResult.PASSED
        else:
            assessment.result = models.AssessmentResult.FAILED

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == assessment.volunteer_id).first()
    if volunteer:
        if assessment.result == models.AssessmentResult.PASSED:
            if volunteer.status == models.VolunteerStatus.PENDING_ASSESSMENT:
                volunteer.status = models.VolunteerStatus.CERTIFIED
                volunteer.certification_date = date.today()
            _issue_certification(db, assessment)
        elif assessment.result == models.AssessmentResult.FAILED:
            if volunteer.status == models.VolunteerStatus.PENDING_ASSESSMENT:
                volunteer.status = models.VolunteerStatus.IN_TRAINING

    db.commit()
    db.refresh(assessment)
    return assessment


@router.post("/{assessment_id}/retake", response_model=schemas.AssessmentDetail)
def create_retake_assessment(assessment_id: int, new_date: Optional[date] = None, db: Session = Depends(get_db)):
    original = db.query(models.Assessment).filter(models.Assessment.id == assessment_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="原考核记录不存在")
    if original.result != models.AssessmentResult.FAILED:
        raise HTTPException(status_code=400, detail="只有考核未通过才能安排补考")

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == original.volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    if volunteer.status != models.VolunteerStatus.IN_TRAINING:
        volunteer.status = models.VolunteerStatus.IN_TRAINING
        db.flush()

    new_assessment = models.Assessment(
        volunteer_id=original.volunteer_id,
        topic_id=original.topic_id,
        training_batch_id=original.training_batch_id,
        parent_assessment_id=original.id,
        assessment_date=new_date or date.today(),
        topic=original.topic,
        result=models.AssessmentResult.PENDING,
        is_retake=True,
        attempt_no=original.attempt_no + 1,
        comments=f"补考安排（原考核ID={original.id}）"
    )
    db.add(new_assessment)
    db.commit()
    db.refresh(new_assessment)
    return new_assessment


# ==================== 讲解资格管理 ====================

@router.get("/volunteers/{volunteer_id}/certifications", response_model=List[schemas.VolunteerCertification])
def list_volunteer_certifications(volunteer_id: int, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    return db.query(models.VolunteerCertification).filter(
        models.VolunteerCertification.volunteer_id == volunteer_id
    ).all()


@router.post("/certifications", response_model=schemas.VolunteerCertification)
def issue_certification(cert: schemas.VolunteerCertificationCreate, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == cert.volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == cert.topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="考核主题不存在")
    existing = db.query(models.VolunteerCertification).filter(
        models.VolunteerCertification.volunteer_id == cert.volunteer_id,
        models.VolunteerCertification.topic_id == cert.topic_id,
        models.VolunteerCertification.is_active == True
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该主题已有有效资格证")
    db_cert = models.VolunteerCertification(**cert.model_dump())
    db_cert.certificate_no = _generate_certificate_no(cert.topic_id, cert.volunteer_id, cert.assessment_id or 0)
    db_cert.issued_date = date.today()
    db_cert.is_active = True
    db.add(db_cert)
    db.commit()
    db.refresh(db_cert)
    return db_cert


@router.get("/certifications/by-topic/{topic_id}", response_model=List[schemas.VolunteerCertification])
def list_certifications_by_topic(topic_id: int, db: Session = Depends(get_db)):
    topic = db.query(models.AssessmentTopic).filter(models.AssessmentTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="考核主题不存在")
    return db.query(models.VolunteerCertification).filter(
        models.VolunteerCertification.topic_id == topic_id,
        models.VolunteerCertification.is_active == True
    ).all()


@router.post("/certifications/{cert_id}/revoke")
def revoke_certification(cert_id: int, reason: Optional[str] = None, db: Session = Depends(get_db)):
    cert = db.query(models.VolunteerCertification).filter(models.VolunteerCertification.id == cert_id).first()
    if not cert:
        raise HTTPException(status_code=404, detail="资格证不存在")
    cert.is_active = False
    db.commit()
    return {"message": "资格证已撤销", "reason": reason}


# ==================== 旧版考核 API（参数化路径在最后） ====================

@router.get("/", response_model=List[schemas.Assessment])
def list_assessments(volunteer_id: int = None, result: str = None, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(models.Assessment)
    if volunteer_id:
        query = query.filter(models.Assessment.volunteer_id == volunteer_id)
    if result:
        result_enum = None
        for r in models.AssessmentResult:
            if r.value == result or r.name == result:
                result_enum = r
                break
        if result_enum:
            query = query.filter(models.Assessment.result == result_enum)
    return query.order_by(models.Assessment.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{assessment_id}", response_model=schemas.AssessmentDetail)
def get_assessment(assessment_id: int, db: Session = Depends(get_db)):
    assessment = db.query(models.Assessment).filter(models.Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    return assessment


@router.post("/", response_model=schemas.Assessment)
def create_assessment(assessment: schemas.AssessmentCreate, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == assessment.volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    db_assessment = models.Assessment(**assessment.model_dump())
    db.add(db_assessment)
    db.commit()
    db.refresh(db_assessment)
    return db_assessment


@router.put("/{assessment_id}", response_model=schemas.Assessment)
def update_assessment(assessment_id: int, update: schemas.AssessmentUpdate, db: Session = Depends(get_db)):
    assessment = db.query(models.Assessment).filter(models.Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(assessment, key, value)
    db.commit()
    db.refresh(assessment)

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == assessment.volunteer_id).first()
    if volunteer:
        if assessment.result == models.AssessmentResult.PASSED and volunteer.status == models.VolunteerStatus.PENDING_ASSESSMENT:
            volunteer.status = models.VolunteerStatus.CERTIFIED
            volunteer.certification_date = date.today()
            db.commit()
        elif assessment.result == models.AssessmentResult.FAILED and volunteer.status == models.VolunteerStatus.PENDING_ASSESSMENT:
            volunteer.status = models.VolunteerStatus.IN_TRAINING
            db.commit()
    db.refresh(assessment)
    return assessment


@router.delete("/{assessment_id}")
def delete_assessment(assessment_id: int, db: Session = Depends(get_db)):
    assessment = db.query(models.Assessment).filter(models.Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    db.delete(assessment)
    db.commit()
    return {"message": "删除成功"}
