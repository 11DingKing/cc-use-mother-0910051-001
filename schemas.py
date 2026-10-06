from pydantic import BaseModel, Field
from datetime import date, datetime
from typing import Optional, List
from models import (
    VolunteerStatus, AssessmentResult, TimeSlotStatus, TrainingBatchStatus, EnrollmentStatus,
    PointsType, PointsSource, BenefitType, ExchangeStatus
)


class SchoolBase(BaseModel):
    name: str
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None


class SchoolCreate(SchoolBase):
    pass


class School(SchoolBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class StarLevelBase(BaseModel):
    name: str
    min_hours: float
    description: Optional[str] = None


class StarLevelCreate(StarLevelBase):
    pass


class StarLevel(StarLevelBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class VolunteerBase(BaseModel):
    name: str
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    school_id: int
    grade: Optional[str] = None
    parent_name: Optional[str] = None
    parent_phone: Optional[str] = None
    preferred_topic: Optional[str] = None
    notes: Optional[str] = None


class VolunteerCreate(VolunteerBase):
    pass


class VolunteerUpdate(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    school_id: Optional[int] = None
    grade: Optional[str] = None
    parent_name: Optional[str] = None
    parent_phone: Optional[str] = None
    preferred_topic: Optional[str] = None
    status: Optional[VolunteerStatus] = None
    star_level_id: Optional[int] = None
    notes: Optional[str] = None


class Volunteer(VolunteerBase):
    id: int
    status: VolunteerStatus
    star_level_id: Optional[int] = None
    total_service_hours: float
    registration_date: date
    certification_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime
    school: Optional[School] = None
    star_level: Optional[StarLevel] = None

    class Config:
        from_attributes = True


class VolunteerDetail(Volunteer):
    trainings: List["TrainingAttendance"] = []
    assessments: List["Assessment"] = []
    service_records: List["ServiceRecord"] = []


class ReviewApplication(BaseModel):
    approved: bool
    notes: Optional[str] = None


class TrainingBase(BaseModel):
    title: str
    training_date: date
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location: Optional[str] = None
    trainer: Optional[str] = None
    content: Optional[str] = None
    max_participants: int = 30


class TrainingCreate(TrainingBase):
    pass


class Training(TrainingBase):
    id: int
    created_at: datetime
    attendances: List["TrainingAttendance"] = []

    class Config:
        from_attributes = True


class TrainingBrief(TrainingBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class TrainingAttendanceBase(BaseModel):
    volunteer_id: int
    training_id: int
    attended: int = 0
    remarks: Optional[str] = None


class TrainingAttendanceCreate(TrainingAttendanceBase):
    pass


class TrainingAttendance(TrainingAttendanceBase):
    id: int
    created_at: datetime
    training: Optional[TrainingBrief] = None

    class Config:
        from_attributes = True


class TrainingAssign(BaseModel):
    volunteer_ids: List[int]


class AssessmentBase(BaseModel):
    volunteer_id: int
    assessment_date: date
    topic: Optional[str] = None
    score: Optional[float] = None
    result: AssessmentResult = AssessmentResult.PENDING
    examiner: Optional[str] = None
    comments: Optional[str] = None


class AssessmentCreate(AssessmentBase):
    pass


class AssessmentUpdate(BaseModel):
    assessment_date: Optional[date] = None
    topic: Optional[str] = None
    score: Optional[float] = None
    result: Optional[AssessmentResult] = None
    examiner: Optional[str] = None
    comments: Optional[str] = None


class Assessment(AssessmentBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class TimeSlotBase(BaseModel):
    slot_date: date
    start_time: str
    end_time: str
    topic: Optional[str] = None
    location: Optional[str] = None


class TimeSlotCreate(TimeSlotBase):
    pass


class TimeSlot(TimeSlotBase):
    id: int
    status: TimeSlotStatus
    volunteer_id: Optional[int] = None
    created_at: datetime
    volunteer: Optional[Volunteer] = None

    class Config:
        from_attributes = True


class TimeSlotClaim(BaseModel):
    volunteer_id: int


class ServiceRecordBase(BaseModel):
    volunteer_id: int
    time_slot_id: Optional[int] = None
    service_date: date
    service_hours: float
    audience_count: int = 0
    teacher_name: Optional[str] = None
    teacher_rating: Optional[int] = None
    teacher_comments: Optional[str] = None


class ServiceRecordCreate(ServiceRecordBase):
    pass


class ServiceRecord(ServiceRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class SchoolStats(BaseModel):
    school_id: int
    school_name: str
    total_count: int
    pending_review: int
    in_training: int
    pending_assessment: int
    certified: int
    disabled: int
    total_service_hours: float
    assessment_pass_rate: Optional[float] = None


class StarStats(BaseModel):
    star_level_id: Optional[int]
    star_name: str
    volunteer_count: int
    total_service_hours: float


class MonthlyStats(BaseModel):
    year: int
    month: int
    registered_count: int
    certified_count: int
    total_service_hours: float
    assessment_count: int
    assessment_pass_count: int
    assessment_pass_rate: Optional[float] = None


class OverviewStats(BaseModel):
    total_volunteers: int
    pending_review: int
    in_training: int
    pending_assessment: int
    certified: int
    disabled: int
    total_service_hours: float
    this_month_new: int
    this_month_service_hours: float


class AssessmentTopicBase(BaseModel):
    name: str
    description: Optional[str] = None
    pass_score: float = 60.0
    is_active: bool = True


class AssessmentTopicCreate(AssessmentTopicBase):
    pass


class AssessmentTopicUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    pass_score: Optional[float] = None
    is_active: Optional[bool] = None


class AssessmentTopic(AssessmentTopicBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class TrainingBatchBase(BaseModel):
    name: str
    topic_id: Optional[int] = None
    description: Optional[str] = None
    min_attendance_rate: float = 80.0
    capacity: int = 30
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class TrainingBatchCreate(TrainingBatchBase):
    pass


class TrainingBatchUpdate(BaseModel):
    name: Optional[str] = None
    topic_id: Optional[int] = None
    description: Optional[str] = None
    min_attendance_rate: Optional[float] = None
    capacity: Optional[int] = None
    status: Optional[TrainingBatchStatus] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class TrainingBatch(TrainingBatchBase):
    id: int
    status: TrainingBatchStatus
    created_at: datetime
    updated_at: datetime
    topic: Optional[AssessmentTopic] = None

    class Config:
        from_attributes = True


class TrainingSessionBase(BaseModel):
    batch_id: int
    session_no: int
    title: str
    session_date: date
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location: Optional[str] = None
    trainer: Optional[str] = None
    content: Optional[str] = None


class TrainingSessionCreate(TrainingSessionBase):
    pass


class TrainingSessionUpdate(BaseModel):
    title: Optional[str] = None
    session_date: Optional[date] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location: Optional[str] = None
    trainer: Optional[str] = None
    content: Optional[str] = None


class TrainingSession(TrainingSessionBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class TrainingBatchDetail(TrainingBatch):
    sessions: List[TrainingSession] = []
    enrollment_count: int = 0


class EnrollmentBase(BaseModel):
    volunteer_id: int
    batch_id: int
    notes: Optional[str] = None


class EnrollmentCreate(EnrollmentBase):
    pass


class Enrollment(EnrollmentBase):
    id: int
    status: EnrollmentStatus
    enrolled_at: datetime
    completed_at: Optional[datetime] = None
    volunteer: Optional["Volunteer"] = None

    class Config:
        from_attributes = True


class EnrollmentDetail(Enrollment):
    batch: Optional[TrainingBatch] = None
    attendance_count: int = 0
    total_sessions: int = 0
    attendance_rate: Optional[float] = None
    eligible_for_assessment: bool = False


class BatchEnroll(BaseModel):
    volunteer_ids: List[int]
    batch_id: int


class SessionAttendanceBase(BaseModel):
    enrollment_id: int
    session_id: int
    volunteer_id: int
    attended: bool = False
    late: bool = False
    leave_early: bool = False
    remarks: Optional[str] = None


class SessionAttendanceCreate(SessionAttendanceBase):
    pass


class SessionAttendanceUpdate(BaseModel):
    attended: Optional[bool] = None
    late: Optional[bool] = None
    leave_early: Optional[bool] = None
    remarks: Optional[str] = None


class SessionAttendance(SessionAttendanceBase):
    id: int
    checked_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SessionAttendanceWithVolunteer(SessionAttendance):
    volunteer: Optional["Volunteer"] = None


class AssessmentCriterionBase(BaseModel):
    topic_id: int
    name: str
    description: Optional[str] = None
    max_score: float = 20.0
    sort_order: int = 0


class AssessmentCriterionCreate(AssessmentCriterionBase):
    pass


class AssessmentCriterionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    max_score: Optional[float] = None
    sort_order: Optional[int] = None


class AssessmentCriterion(AssessmentCriterionBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class AssessmentQuestionBase(BaseModel):
    topic_id: int
    question: str
    reference_answer: Optional[str] = None
    sort_order: int = 0


class AssessmentQuestionCreate(AssessmentQuestionBase):
    pass


class AssessmentQuestionUpdate(BaseModel):
    question: Optional[str] = None
    reference_answer: Optional[str] = None
    sort_order: Optional[int] = None


class AssessmentQuestion(AssessmentQuestionBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class AssessmentScoreBase(BaseModel):
    assessment_id: int
    criterion_id: int
    score: float = 0.0
    comments: Optional[str] = None


class AssessmentScoreCreate(AssessmentScoreBase):
    pass


class AssessmentScoreUpdate(BaseModel):
    score: Optional[float] = None
    comments: Optional[str] = None


class AssessmentScore(AssessmentScoreBase):
    id: int
    created_at: datetime
    criterion: Optional[AssessmentCriterion] = None

    class Config:
        from_attributes = True


class AssessmentDetail(Assessment):
    scores: List[AssessmentScore] = []
    topic_obj: Optional[AssessmentTopic] = None


class AssessmentCreateV2(BaseModel):
    volunteer_id: int
    topic_id: Optional[int] = None
    training_batch_id: Optional[int] = None
    assessment_date: date
    examiner: Optional[str] = None
    comments: Optional[str] = None


class AssessmentSubmitScores(BaseModel):
    scores: List[AssessmentScoreCreate]
    result: Optional[AssessmentResult] = None
    examiner: Optional[str] = None
    comments: Optional[str] = None


class VolunteerCertificationBase(BaseModel):
    volunteer_id: int
    topic_id: int
    assessment_id: Optional[int] = None
    expiry_date: Optional[date] = None


class VolunteerCertificationCreate(VolunteerCertificationBase):
    pass


class VolunteerCertification(VolunteerCertificationBase):
    id: int
    certificate_no: Optional[str] = None
    issued_date: date
    is_active: bool
    created_at: datetime
    topic: Optional[AssessmentTopic] = None

    class Config:
        from_attributes = True


class VolunteerDetailV2(VolunteerDetail):
    enrollments: List[Enrollment] = []
    certifications: List[VolunteerCertification] = []


class TrainingBatchStats(BaseModel):
    batch_id: int
    batch_name: str
    topic_name: Optional[str] = None
    total_sessions: int
    enrollment_count: int
    total_attendance_marked: int
    attendance_rate: Optional[float] = None
    eligible_count: int = 0
    assessment_count: int = 0
    first_time_pass_count: int = 0
    first_time_pass_rate: Optional[float] = None


class TopicAssessmentStats(BaseModel):
    topic_id: int
    topic_name: str
    total_assessments: int
    first_time_count: int
    first_time_pass_count: int
    first_time_pass_rate: Optional[float] = None
    retake_count: int
    retake_pass_count: int
    total_pass_count: int
    total_pass_rate: Optional[float] = None


class PointsRecordBase(BaseModel):
    volunteer_id: int
    points_type: PointsType
    points_amount: int
    source: PointsSource
    description: Optional[str] = None


class PointsRecordCreate(PointsRecordBase):
    service_record_id: Optional[int] = None
    exchange_id: Optional[int] = None


class PointsRecord(PointsRecordBase):
    id: int
    service_record_id: Optional[int] = None
    exchange_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class VolunteerPoints(BaseModel):
    volunteer_id: int
    name: str
    points_balance: int
    total_earned: int
    total_spent: int


class BenefitBase(BaseModel):
    name: str
    benefit_type: BenefitType
    description: Optional[str] = None
    points_cost: int
    stock: int = 0
    is_active: bool = True
    image_url: Optional[str] = None
    sort_order: int = 0


class BenefitCreate(BenefitBase):
    pass


class BenefitUpdate(BaseModel):
    name: Optional[str] = None
    benefit_type: Optional[BenefitType] = None
    description: Optional[str] = None
    points_cost: Optional[int] = None
    stock: Optional[int] = None
    is_active: Optional[bool] = None
    image_url: Optional[str] = None
    sort_order: Optional[int] = None


class Benefit(BenefitBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class BenefitExchangeBase(BaseModel):
    volunteer_id: int
    benefit_id: int
    quantity: int = 1


class BenefitExchangeCreate(BenefitExchangeBase):
    delivery_info: Optional[str] = None
    notes: Optional[str] = None


class BenefitExchangeUpdate(BaseModel):
    status: Optional[ExchangeStatus] = None
    delivery_info: Optional[str] = None
    notes: Optional[str] = None


class BenefitExchange(BaseModel):
    id: int
    volunteer_id: int
    benefit_id: int
    points_spent: int
    status: ExchangeStatus
    quantity: int
    delivery_info: Optional[str] = None
    fulfilled_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    volunteer: Optional["Volunteer"] = None
    benefit: Optional[Benefit] = None

    class Config:
        from_attributes = True


class StarCertificateBase(BaseModel):
    volunteer_id: int
    star_level_id: int
    total_hours: float


class StarCertificateCreate(StarCertificateBase):
    certificate_no: Optional[str] = None
    issued_date: Optional[date] = None


class StarCertificate(BaseModel):
    id: int
    volunteer_id: int
    star_level_id: int
    certificate_no: str
    issued_date: date
    total_hours: float
    is_active: bool
    created_at: datetime
    star_level: Optional[StarLevel] = None

    class Config:
        from_attributes = True


class ParentLogin(BaseModel):
    parent_phone: str
    volunteer_name: Optional[str] = None


# ==================== 监护关系可追溯授权 ====================

from models import GuardianshipScope, AuthorizationStatus, AuditAction  # noqa: E402


class GuardianCreate(BaseModel):
    name: Optional[str] = None
    phone: str


class GuardianLogin(BaseModel):
    phone: str
    name: Optional[str] = None


class GuardianOut(BaseModel):
    id: int
    name: str
    phone: str
    created_at: datetime

    class Config:
        from_attributes = True


class AuthorizationGrant(BaseModel):
    guardian_phone: str
    guardian_name: Optional[str] = None
    volunteer_id: int
    scopes: List[str] = Field(..., description="授权操作范围：查询 / 报名确认 / 权益代领")
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    granted_by: Optional[str] = "运营管理员"


class AuthorizationRevoke(BaseModel):
    reason: Optional[str] = None


class AuthorizationOut(BaseModel):
    id: int
    guardian_id: int
    volunteer_id: int
    version: int
    scopes: List[str] = []
    status: AuthorizationStatus
    valid_from: datetime
    valid_until: Optional[datetime] = None
    granted_by: Optional[str] = None
    revoke_reason: Optional[str] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime
    guardian: Optional[GuardianOut] = None
    volunteer_name: Optional[str] = None

    class Config:
        from_attributes = True


class GuardianshipAuditOut(BaseModel):
    id: int
    authorization_id: Optional[int] = None
    guardian_id: Optional[int] = None
    volunteer_id: int
    action: AuditAction
    auth_version: Optional[int] = None
    guardian_name_snapshot: Optional[str] = None
    guardian_phone_snapshot: Optional[str] = None
    scopes_snapshot: Optional[str] = None
    result: str
    detail: Optional[str] = None
    ref_type: Optional[str] = None
    ref_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    operated_at: datetime

    class Config:
        from_attributes = True


class GuardianChildOut(BaseModel):
    volunteer_id: int
    name: str
    school_name: Optional[str] = None
    grade: Optional[str] = None
    authorization_id: int
    version: int
    scopes: List[str] = []
    valid_until: Optional[datetime] = None


class GuardianLoginResult(BaseModel):
    guardian: GuardianOut
    children: List[GuardianChildOut] = []


class GuardianEnrollConfirm(BaseModel):
    guardian_phone: str
    batch_id: int
    idempotency_key: Optional[str] = None
    notes: Optional[str] = None


class GuardianBenefitClaim(BaseModel):
    guardian_phone: str
    benefit_id: int
    quantity: int = 1
    delivery_info: Optional[str] = None
    idempotency_key: Optional[str] = None


class AuthorizationStateAt(BaseModel):
    authorization_id: int
    guardian_id: int
    guardian_name: str
    guardian_phone: str
    version: int
    scopes: List[str] = []
    status: AuthorizationStatus
    valid_from: datetime
    valid_until: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ReconstructEvent(BaseModel):
    audit_id: int
    operated_at: datetime
    guardian_name: Optional[str] = None
    guardian_phone: Optional[str] = None
    action: str
    auth_version: Optional[int] = None
    authorization_id: Optional[int] = None
    scopes: List[str] = []
    result: str
    detail: Optional[str] = None
    ref_type: Optional[str] = None
    ref_id: Optional[str] = None


class ReconstructReport(BaseModel):
    volunteer_id: int
    volunteer_name: str
    at: datetime
    active_authorizations: List[AuthorizationStateAt] = []
    events: List[ReconstructEvent] = []


class ParentVolunteerSummary(BaseModel):
    volunteer_id: int
    name: str
    status: VolunteerStatus
    star_level_name: Optional[str] = None
    total_service_hours: float
    points_balance: int
    registration_date: date
    certification_date: Optional[date] = None


class ParentServiceRecord(BaseModel):
    id: int
    service_date: date
    service_hours: float
    topic: Optional[str] = None
    teacher_name: Optional[str] = None
    teacher_rating: Optional[int] = None
    teacher_comments: Optional[str] = None
    points_awarded: int


class ParentTrainingRecord(BaseModel):
    batch_name: str
    topic_name: Optional[str] = None
    status: EnrollmentStatus
    total_sessions: int
    attended_sessions: int
    attendance_rate: Optional[float] = None
    eligible_for_assessment: bool


class ParentView(BaseModel):
    volunteer: ParentVolunteerSummary
    service_records: List[ParentServiceRecord] = []
    training_records: List[ParentTrainingRecord] = []
    certificates: List[StarCertificate] = []
    points_records: List[PointsRecord] = []
    exchanges: List[BenefitExchange] = []


class PointsStats(BaseModel):
    total_points_earned: int
    total_points_spent: int
    net_points: int
    earn_count: int
    spend_count: int


class ExchangeStats(BaseModel):
    total_exchanges: int
    pending_exchanges: int
    completed_exchanges: int
    cancelled_exchanges: int
    total_points_spent: int


class BenefitStats(BaseModel):
    benefit_id: int
    benefit_name: str
    benefit_type: BenefitType
    total_exchanged: int
    total_quantity: int
    total_points: int


Volunteer.model_rebuild()
VolunteerDetail.model_rebuild()
Training.model_rebuild()
TrainingBrief.model_rebuild()
Enrollment.model_rebuild()
TrainingBatchDetail.model_rebuild()
SessionAttendanceWithVolunteer.model_rebuild()
VolunteerDetailV2.model_rebuild()
BenefitExchange.model_rebuild()
