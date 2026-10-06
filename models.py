from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, Float, Enum as SAEnum, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime, date
from database import Base
import enum
import json


class VolunteerStatus(str, enum.Enum):
    PENDING_REVIEW = "报名待审"
    IN_TRAINING = "培训中"
    PENDING_ASSESSMENT = "待考核"
    CERTIFIED = "已持证"
    DISABLED = "已停用"


class AssessmentResult(str, enum.Enum):
    PENDING = "待考核"
    PASSED = "通过"
    FAILED = "未通过"


class TrainingBatchStatus(str, enum.Enum):
    DRAFT = "草稿"
    ENROLLING = "报名中"
    IN_PROGRESS = "进行中"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


class EnrollmentStatus(str, enum.Enum):
    ENROLLED = "已入班"
    DROPPED = "已退班"
    COMPLETED = "已完成培训"


class TimeSlotStatus(str, enum.Enum):
    AVAILABLE = "可认领"
    CLAIMED = "已认领"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


class School(Base):
    __tablename__ = "schools"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    contact_person = Column(String(50))
    contact_phone = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)

    volunteers = relationship("Volunteer", back_populates="school")


class StarLevel(Base):
    __tablename__ = "star_levels"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(20), nullable=False, unique=True)
    min_hours = Column(Float, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    volunteers = relationship("Volunteer", back_populates="star_level")


class PointsType(str, enum.Enum):
    EARN = "获得"
    SPEND = "消耗"


class PointsSource(str, enum.Enum):
    SERVICE_COMPLETION = "完成讲解"
    TEACHER_RATING = "老师好评"
    EXCHANGE_BADGE = "兑换徽章"
    EXCHANGE_PRIORITY_SLOT = "兑换优先时段"
    OTHER = "其他"


class BenefitType(str, enum.Enum):
    BADGE = "纪念徽章"
    PRIORITY_SLOT = "优先认领时段"
    OTHER = "其他权益"


class ExchangeStatus(str, enum.Enum):
    PENDING = "待处理"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


class Volunteer(Base):
    __tablename__ = "volunteers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    gender = Column(String(10))
    birth_date = Column(Date)
    school_id = Column(Integer, ForeignKey("schools.id"))
    grade = Column(String(20))
    parent_name = Column(String(50))
    parent_phone = Column(String(20))
    preferred_topic = Column(String(100))
    status = Column(SAEnum(VolunteerStatus), default=VolunteerStatus.PENDING_REVIEW)
    star_level_id = Column(Integer, ForeignKey("star_levels.id"))
    total_service_hours = Column(Float, default=0.0)
    points_balance = Column(Integer, default=0)
    registration_date = Column(Date, default=date.today)
    certification_date = Column(Date)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    school = relationship("School", back_populates="volunteers")
    star_level = relationship("StarLevel", back_populates="volunteers")
    trainings = relationship("TrainingAttendance", back_populates="volunteer")
    assessments = relationship("Assessment", back_populates="volunteer")
    time_slots = relationship("TimeSlot", back_populates="volunteer")
    service_records = relationship("ServiceRecord", back_populates="volunteer")
    enrollments = relationship("Enrollment", back_populates="volunteer")
    certifications = relationship("VolunteerCertification", back_populates="volunteer")
    points_records = relationship("PointsRecord", back_populates="volunteer")
    benefit_exchanges = relationship("BenefitExchange", back_populates="volunteer")
    star_certificates = relationship("StarCertificate", back_populates="volunteer")
    authorizations = relationship("GuardianshipAuthorization", back_populates="volunteer")


class AssessmentTopic(Base):
    __tablename__ = "assessment_topics"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    pass_score = Column(Float, default=60.0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    criteria = relationship("AssessmentCriterion", back_populates="topic")
    questions = relationship("AssessmentQuestion", back_populates="topic")
    assessments = relationship("Assessment", back_populates="topic_obj")
    certifications = relationship("VolunteerCertification", back_populates="topic")


class TrainingBatch(Base):
    __tablename__ = "training_batches"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    topic_id = Column(Integer, ForeignKey("assessment_topics.id"))
    description = Column(Text)
    min_attendance_rate = Column(Float, default=80.0)
    capacity = Column(Integer, default=30)
    status = Column(SAEnum(TrainingBatchStatus), default=TrainingBatchStatus.DRAFT)
    start_date = Column(Date)
    end_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    topic = relationship("AssessmentTopic")
    sessions = relationship("TrainingSession", back_populates="batch", cascade="all, delete-orphan")
    enrollments = relationship("Enrollment", back_populates="batch", cascade="all, delete-orphan")
    assessments = relationship("Assessment", back_populates="training_batch")


class TrainingSession(Base):
    __tablename__ = "training_sessions"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("training_batches.id"), nullable=False)
    session_no = Column(Integer, nullable=False)
    title = Column(String(100), nullable=False)
    session_date = Column(Date, nullable=False)
    start_time = Column(String(10))
    end_time = Column(String(10))
    location = Column(String(100))
    trainer = Column(String(50))
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("TrainingBatch", back_populates="sessions")
    attendances = relationship("SessionAttendance", back_populates="session", cascade="all, delete-orphan")


class Enrollment(Base):
    __tablename__ = "enrollments"

    id = Column(Integer, primary_key=True, index=True)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("training_batches.id"), nullable=False)
    status = Column(SAEnum(EnrollmentStatus), default=EnrollmentStatus.ENROLLED)
    enrolled_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    # 若由监护人代办，记录所依据的授权版本；本人/管理员办理时为空
    authorization_id = Column(Integer, ForeignKey("guardianship_authorizations.id"))
    notes = Column(Text)

    volunteer = relationship("Volunteer", back_populates="enrollments")
    batch = relationship("TrainingBatch", back_populates="enrollments")
    attendances = relationship("SessionAttendance", back_populates="enrollment", cascade="all, delete-orphan")


class SessionAttendance(Base):
    __tablename__ = "session_attendances"

    id = Column(Integer, primary_key=True, index=True)
    enrollment_id = Column(Integer, ForeignKey("enrollments.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("training_sessions.id"), nullable=False)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    attended = Column(Boolean, default=False)
    late = Column(Boolean, default=False)
    leave_early = Column(Boolean, default=False)
    checked_at = Column(DateTime)
    remarks = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    enrollment = relationship("Enrollment", back_populates="attendances")
    session = relationship("TrainingSession", back_populates="attendances")
    volunteer = relationship("Volunteer")


class AssessmentCriterion(Base):
    __tablename__ = "assessment_criteria"

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("assessment_topics.id"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    max_score = Column(Float, default=20.0)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    topic = relationship("AssessmentTopic", back_populates="criteria")
    scores = relationship("AssessmentScore", back_populates="criterion", cascade="all, delete-orphan")


class AssessmentQuestion(Base):
    __tablename__ = "assessment_questions"

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("assessment_topics.id"), nullable=False)
    question = Column(Text, nullable=False)
    reference_answer = Column(Text)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    topic = relationship("AssessmentTopic", back_populates="questions")


class AssessmentScore(Base):
    __tablename__ = "assessment_scores"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False)
    criterion_id = Column(Integer, ForeignKey("assessment_criteria.id"), nullable=False)
    score = Column(Float, default=0.0)
    comments = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    assessment = relationship("Assessment", back_populates="scores")
    criterion = relationship("AssessmentCriterion", back_populates="scores")


class VolunteerCertification(Base):
    __tablename__ = "volunteer_certifications"

    id = Column(Integer, primary_key=True, index=True)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("assessment_topics.id"), nullable=False)
    assessment_id = Column(Integer, ForeignKey("assessments.id"))
    certificate_no = Column(String(50), unique=True)
    issued_date = Column(Date, default=date.today)
    expiry_date = Column(Date)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    volunteer = relationship("Volunteer", back_populates="certifications")
    topic = relationship("AssessmentTopic", back_populates="certifications")
    assessment = relationship("Assessment")


class Training(Base):
    __tablename__ = "trainings"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100), nullable=False)
    training_date = Column(Date, nullable=False)
    start_time = Column(String(10))
    end_time = Column(String(10))
    location = Column(String(100))
    trainer = Column(String(50))
    content = Column(Text)
    max_participants = Column(Integer, default=30)
    created_at = Column(DateTime, default=datetime.utcnow)

    attendances = relationship("TrainingAttendance", back_populates="training")


class TrainingAttendance(Base):
    __tablename__ = "training_attendances"

    id = Column(Integer, primary_key=True, index=True)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"))
    training_id = Column(Integer, ForeignKey("trainings.id"))
    attended = Column(Integer, default=0)
    remarks = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    volunteer = relationship("Volunteer", back_populates="trainings")
    training = relationship("Training", back_populates="attendances")


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("assessment_topics.id"))
    training_batch_id = Column(Integer, ForeignKey("training_batches.id"))
    parent_assessment_id = Column(Integer, ForeignKey("assessments.id"))
    assessment_date = Column(Date, nullable=False)
    topic = Column(String(100))
    score = Column(Float)
    result = Column(SAEnum(AssessmentResult), default=AssessmentResult.PENDING)
    is_retake = Column(Boolean, default=False)
    attempt_no = Column(Integer, default=1)
    examiner = Column(String(50))
    comments = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    volunteer = relationship("Volunteer", back_populates="assessments")
    topic_obj = relationship("AssessmentTopic", back_populates="assessments")
    training_batch = relationship("TrainingBatch", back_populates="assessments")
    parent_assessment = relationship("Assessment", remote_side=[id])
    scores = relationship("AssessmentScore", back_populates="assessment", cascade="all, delete-orphan")
    certification = relationship("VolunteerCertification", back_populates="assessment", uselist=False)


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id = Column(Integer, primary_key=True, index=True)
    slot_date = Column(Date, nullable=False)
    start_time = Column(String(10), nullable=False)
    end_time = Column(String(10), nullable=False)
    topic = Column(String(100))
    location = Column(String(100))
    status = Column(SAEnum(TimeSlotStatus), default=TimeSlotStatus.AVAILABLE)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    volunteer = relationship("Volunteer", back_populates="time_slots")
    service_record = relationship("ServiceRecord", back_populates="time_slot", uselist=False)


class ServiceRecord(Base):
    __tablename__ = "service_records"

    id = Column(Integer, primary_key=True, index=True)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"))
    time_slot_id = Column(Integer, ForeignKey("time_slots.id"))
    service_date = Column(Date, nullable=False)
    service_hours = Column(Float, nullable=False)
    audience_count = Column(Integer, default=0)
    teacher_name = Column(String(50))
    teacher_rating = Column(Integer)
    teacher_comments = Column(Text)
    points_awarded = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    volunteer = relationship("Volunteer", back_populates="service_records")
    time_slot = relationship("TimeSlot", back_populates="service_record")


class PointsRecord(Base):
    __tablename__ = "points_records"

    id = Column(Integer, primary_key=True, index=True)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    points_type = Column(SAEnum(PointsType), nullable=False)
    points_amount = Column(Integer, nullable=False)
    source = Column(SAEnum(PointsSource), nullable=False)
    service_record_id = Column(Integer, ForeignKey("service_records.id"))
    exchange_id = Column(Integer, ForeignKey("benefit_exchanges.id"))
    description = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)

    volunteer = relationship("Volunteer", back_populates="points_records")
    service_record = relationship("ServiceRecord")
    exchange = relationship("BenefitExchange", back_populates="points_record")


class Benefit(Base):
    __tablename__ = "benefits"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    benefit_type = Column(SAEnum(BenefitType), nullable=False)
    description = Column(Text)
    points_cost = Column(Integer, nullable=False)
    stock = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    image_url = Column(String(500))
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    exchanges = relationship("BenefitExchange", back_populates="benefit")


class BenefitExchange(Base):
    __tablename__ = "benefit_exchanges"

    id = Column(Integer, primary_key=True, index=True)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    benefit_id = Column(Integer, ForeignKey("benefits.id"), nullable=False)
    points_spent = Column(Integer, nullable=False)
    status = Column(SAEnum(ExchangeStatus), default=ExchangeStatus.PENDING)
    quantity = Column(Integer, default=1)
    delivery_info = Column(Text)
    fulfilled_at = Column(DateTime)
    # 若由监护人代领，记录所依据的授权版本；本人/管理员办理时为空
    authorization_id = Column(Integer, ForeignKey("guardianship_authorizations.id"))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    volunteer = relationship("Volunteer", back_populates="benefit_exchanges")
    benefit = relationship("Benefit", back_populates="exchanges")
    points_record = relationship("PointsRecord", back_populates="exchange", uselist=False)


class StarCertificate(Base):
    __tablename__ = "star_certificates"

    id = Column(Integer, primary_key=True, index=True)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    star_level_id = Column(Integer, ForeignKey("star_levels.id"), nullable=False)
    certificate_no = Column(String(50), unique=True, nullable=False)
    issued_date = Column(Date, default=date.today)
    total_hours = Column(Float, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    volunteer = relationship("Volunteer", back_populates="star_certificates")
    star_level = relationship("StarLevel")


# ==================== 监护关系可追溯授权 ====================

class GuardianshipScope(str, enum.Enum):
    """监护人可被授予的操作范围（可在一版授权中叠加多个）。"""
    QUERY = "查询"            # 查看孩子培训、考核、服务、积分等明细
    ENROLL_CONFIRM = "报名确认"  # 代孩子确认培训报名
    BENEFIT_CLAIM = "权益代领"  # 代孩子兑换/领取权益


class AuthorizationStatus(str, enum.Enum):
    ACTIVE = "有效"
    REVOKED = "已撤回"
    EXPIRED = "已过期"   # 由有效期判定，审计中显式落库


class AuditAction(str, enum.Enum):
    GRANT = "授权"
    REVOKE = "撤回"
    QUERY = "查询"
    ENROLL_CONFIRM = "报名确认"
    BENEFIT_CLAIM = "权益代领"
    DENIED = "拒绝"


class Guardian(Base):
    """监护人身份。一个监护人可共同监护多个孩子，一个孩子也可有多个监护人。"""
    __tablename__ = "guardians"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    phone = Column(String(20), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    authorizations = relationship("GuardianshipAuthorization", back_populates="guardian",
                                  cascade="all, delete-orphan")


class GuardianshipAuthorization(Base):
    """
    不可变的授权版本。
    - 每次（重新）授权都产生新版本号，旧版本保留以便历史追溯；
    - 撤回只把 status 置为“已撤回”，绝不删除或覆盖；
    - scopes 以 JSON 数组保存授予的操作范围；
    - valid_from/valid_until 界定有效期。
    """
    __tablename__ = "guardianship_authorizations"

    id = Column(Integer, primary_key=True, index=True)
    guardian_id = Column(Integer, ForeignKey("guardians.id"), nullable=False)
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    version = Column(Integer, nullable=False)
    scopes_json = Column(Text, nullable=False, default="[]")
    status = Column(SAEnum(AuthorizationStatus), nullable=False, default=AuthorizationStatus.ACTIVE)
    valid_from = Column(DateTime, nullable=False, default=datetime.utcnow)
    valid_until = Column(DateTime)
    granted_by = Column(String(50), default="运营管理员")
    revoke_reason = Column(Text)
    revoked_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("guardian_id", "volunteer_id", "version", name="uq_auth_version"),
        Index("ix_auth_subject_time", "volunteer_id", "status", "valid_from", "valid_until"),
    )

    guardian = relationship("Guardian", back_populates="authorizations")
    volunteer = relationship("Volunteer", back_populates="authorizations")

    @property
    def scopes(self):
        try:
            return set(json.loads(self.scopes_json or "[]"))
        except (ValueError, TypeError):
            return set()

    @scopes.setter
    def scopes(self, values):
        self.scopes_json = json.dumps(sorted(values), ensure_ascii=False)

    def is_active_at(self, at: datetime) -> bool:
        if self.status != AuthorizationStatus.ACTIVE:
            return False
        if at < self.valid_from:
            return False
        if self.valid_until is not None and at >= self.valid_until:
            return False
        return True


class GuardianshipAudit(Base):
    """
    授权使用流水。每次授权生命周期事件（授予/撤回）和每一次代办或查询都落一条，
    并冻结操作发生时所依据的授权版本号与监护人快照，保证事后“谁凭哪一版办过什么”可还原。
    """
    __tablename__ = "guardianship_audits"

    id = Column(Integer, primary_key=True, index=True)
    authorization_id = Column(Integer, ForeignKey("guardianship_authorizations.id"))
    guardian_id = Column(Integer, ForeignKey("guardians.id"))
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    action = Column(SAEnum(AuditAction), nullable=False)
    # 冻结快照：即便授权随后被撤回/交接，本条记录仍能自解释
    auth_version = Column(Integer)
    guardian_name_snapshot = Column(String(50))
    guardian_phone_snapshot = Column(String(20))
    scopes_snapshot = Column(Text)
    result = Column(String(20), nullable=False, default="成功")  # 成功 / 拒绝
    detail = Column(Text)
    ref_type = Column(String(30))   # enrollment / benefit_exchange / view ...
    ref_id = Column(String(50))
    idempotency_key = Column(String(80))
    operated_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # 重复提交防护：同一监护人同一幂等键只能生效一次
        UniqueConstraint("guardian_id", "idempotency_key", name="uq_audit_idempotency"),
        Index("ix_audit_subject_time", "volunteer_id", "operated_at"),
        Index("ix_audit_guardian_time", "guardian_id", "operated_at"),
    )

    authorization = relationship("GuardianshipAuthorization")
    guardian = relationship("Guardian")
    volunteer = relationship("Volunteer")
