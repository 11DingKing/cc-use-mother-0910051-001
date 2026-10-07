from datetime import date, timedelta
from sqlalchemy.orm import Session
from database import SessionLocal, engine
import models


def init_db():
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(models.School).count() == 0:
            seed_data(db)
        recompute_volunteer_hours(db)
    finally:
        db.close()


def recompute_volunteer_hours(db: Session):
    from sqlalchemy import func
    from datetime import date

    star_levels = db.query(models.StarLevel).order_by(models.StarLevel.min_hours.desc()).all()
    volunteers = db.query(models.Volunteer).all()

    for volunteer in volunteers:
        total = db.query(func.sum(models.ServiceRecord.service_hours)).filter(
            models.ServiceRecord.volunteer_id == volunteer.id
        ).scalar() or 0.0
        volunteer.total_service_hours = total
        new_star = None
        for sl in star_levels:
            if total >= sl.min_hours:
                new_star = sl
                break
        volunteer.star_level_id = new_star.id if new_star else None

    db.flush()

    service_records = db.query(models.ServiceRecord).all()
    for sr in service_records:
        base_points = int(sr.service_hours * 10)
        rating_points = 0
        if sr.teacher_rating and sr.teacher_rating >= 4:
            rating_points = (sr.teacher_rating - 3) * 5
        sr.points_awarded = base_points + rating_points

        existing_points = db.query(models.PointsRecord).filter(
            models.PointsRecord.service_record_id == sr.id
        ).first()

        if not existing_points and sr.points_awarded > 0:
            db.add(models.PointsRecord(
                volunteer_id=sr.volunteer_id,
                points_type=models.PointsType.EARN,
                points_amount=base_points,
                source=models.PointsSource.SERVICE_COMPLETION,
                description=f"完成讲解服务 {sr.service_hours}小时",
                service_record_id=sr.id
            ))

            if rating_points > 0:
                db.add(models.PointsRecord(
                    volunteer_id=sr.volunteer_id,
                    points_type=models.PointsType.EARN,
                    points_amount=rating_points,
                    source=models.PointsSource.TEACHER_RATING,
                    description=f"老师好评 {sr.teacher_rating}星",
                    service_record_id=sr.id
                ))

    db.flush()

    for volunteer in volunteers:
        total_earned = db.query(func.sum(models.PointsRecord.points_amount)).filter(
            models.PointsRecord.volunteer_id == volunteer.id,
            models.PointsRecord.points_type == models.PointsType.EARN
        ).scalar() or 0
        total_spent = db.query(func.sum(models.PointsRecord.points_amount)).filter(
            models.PointsRecord.volunteer_id == volunteer.id,
            models.PointsRecord.points_type == models.PointsType.SPEND
        ).scalar() or 0
        volunteer.points_balance = total_earned - total_spent

        if volunteer.star_level_id:
            existing_cert = db.query(models.StarCertificate).filter(
                models.StarCertificate.volunteer_id == volunteer.id,
                models.StarCertificate.star_level_id == volunteer.star_level_id,
                models.StarCertificate.is_active == True
            ).first()

            if not existing_cert:
                today = date.today()
                prefix = f"STAR{volunteer.star_level_id:02d}{today.year}{today.month:02d}"
                count = db.query(func.count(models.StarCertificate.id)).filter(
                    models.StarCertificate.certificate_no.like(f"{prefix}%")
                ).scalar() or 0
                cert_no = f"{prefix}{(count + 1):04d}"

                db.add(models.StarCertificate(
                    volunteer_id=volunteer.id,
                    star_level_id=volunteer.star_level_id,
                    certificate_no=cert_no,
                    total_hours=volunteer.total_service_hours,
                    is_active=True
                ))

    db.commit()


def seed_data(db: Session):
    schools = [
        models.School(name="实验小学", contact_person="王老师", contact_phone="13800138001"),
        models.School(name="育才小学", contact_person="李老师", contact_phone="13800138002"),
        models.School(name="向阳小学", contact_person="张老师", contact_phone="13800138003"),
        models.School(name="红领巾小学", contact_person="刘老师", contact_phone="13800138004"),
    ]
    db.add_all(schools)
    db.flush()

    star_levels = [
        models.StarLevel(name="一星级", min_hours=10.0, description="累计服务满10小时"),
        models.StarLevel(name="二星级", min_hours=30.0, description="累计服务满30小时"),
        models.StarLevel(name="三星级", min_hours=60.0, description="累计服务满60小时"),
        models.StarLevel(name="四星级", min_hours=100.0, description="累计服务满100小时"),
        models.StarLevel(name="五星级", min_hours=150.0, description="累计服务满150小时，金牌讲解员"),
    ]
    db.add_all(star_levels)
    db.flush()

    today = date.today()

    volunteers = [
        models.Volunteer(
            name="赵小明", gender="男", birth_date=date(2014, 5, 12),
            school_id=schools[0].id, grade="四年级",
            parent_name="赵先生", parent_phone="13912345678",
            preferred_topic="革命先烈事迹",
            status=models.VolunteerStatus.PENDING_REVIEW,
            registration_date=today - timedelta(days=3),
            notes="孩子表达能力强，热爱历史故事"
        ),
        models.Volunteer(
            name="钱朵朵", gender="女", birth_date=date(2013, 8, 20),
            school_id=schools[1].id, grade="五年级",
            parent_name="钱女士", parent_phone="13912345679",
            preferred_topic="抗日战争历史",
            status=models.VolunteerStatus.IN_TRAINING,
            registration_date=today - timedelta(days=20),
            notes="已完成2次培训课程"
        ),
        models.Volunteer(
            name="孙浩然", gender="男", birth_date=date(2013, 3, 8),
            school_id=schools[2].id, grade="五年级",
            parent_name="孙先生", parent_phone="13912345680",
            preferred_topic="长征故事",
            status=models.VolunteerStatus.PENDING_ASSESSMENT,
            registration_date=today - timedelta(days=45),
            notes="培训全部完成，等待安排考核"
        ),
        models.Volunteer(
            name="李小萌", gender="女", birth_date=date(2012, 11, 15),
            school_id=schools[0].id, grade="六年级",
            parent_name="李先生", parent_phone="13912345681",
            preferred_topic="新中国发展史",
            status=models.VolunteerStatus.CERTIFIED,
            registration_date=today - timedelta(days=90),
            certification_date=today - timedelta(days=60),
            notes="持证上岗，表现优秀"
        ),
        models.Volunteer(
            name="周子轩", gender="男", birth_date=date(2012, 1, 25),
            school_id=schools[3].id, grade="六年级",
            parent_name="周女士", parent_phone="13912345682",
            preferred_topic="雷锋精神",
            status=models.VolunteerStatus.CERTIFIED,
            registration_date=today - timedelta(days=180),
            certification_date=today - timedelta(days=150),
            notes="二星级讲解员，深受观众喜爱"
        ),
        models.Volunteer(
            name="吴雨桐", gender="女", birth_date=date(2011, 7, 3),
            school_id=schools[1].id, grade="七年级",
            parent_name="吴先生", parent_phone="13912345683",
            preferred_topic="改革开放历程",
            status=models.VolunteerStatus.CERTIFIED,
            star_level_id=star_levels[2].id,
            total_service_hours=78.5,
            registration_date=today - timedelta(days=300),
            certification_date=today - timedelta(days=270),
            notes="三星级讲解员，资深志愿者"
        ),
        models.Volunteer(
            name="郑思琪", gender="女", birth_date=date(2010, 9, 18),
            school_id=schools[2].id, grade="八年级",
            parent_name="郑女士", parent_phone="13912345684",
            preferred_topic="社会主义建设",
            status=models.VolunteerStatus.CERTIFIED,
            star_level_id=star_levels[3].id,
            total_service_hours=125.0,
            registration_date=today - timedelta(days=450),
            certification_date=today - timedelta(days=420),
            notes="四星级讲解员，可带新人培训"
        ),
        models.Volunteer(
            name="王浩宇", gender="男", birth_date=date(2009, 12, 1),
            school_id=schools[3].id, grade="九年级",
            parent_name="王先生", parent_phone="13912345685",
            preferred_topic="全面党史",
            status=models.VolunteerStatus.CERTIFIED,
            registration_date=today - timedelta(days=600),
            certification_date=today - timedelta(days=570),
            notes="五星级金牌讲解员，培训助教"
        ),
        models.Volunteer(
            name="陈雨欣", gender="女", birth_date=date(2014, 2, 28),
            school_id=schools[0].id, grade="四年级",
            parent_name="陈先生", parent_phone="13912345686",
            preferred_topic="少年英雄故事",
            status=models.VolunteerStatus.DISABLED,
            registration_date=today - timedelta(days=100),
            notes="审核未通过：年龄偏小，暂不适合讲解工作"
        ),
        models.Volunteer(
            name="韩佳怡", gender="女", birth_date=date(2013, 6, 10),
            school_id=schools[1].id, grade="五年级",
            parent_name="韩女士", parent_phone="13912345687",
            preferred_topic="井冈山精神",
            status=models.VolunteerStatus.PENDING_REVIEW,
            registration_date=today - timedelta(days=1),
            notes="家长代报，孩子有朗诵基础"
        ),
    ]
    db.add_all(volunteers)
    db.flush()

    trainings = [
        models.Training(
            title="讲解员基础礼仪培训",
            training_date=today - timedelta(days=15),
            start_time="09:00", end_time="11:30",
            location="纪念馆培训室A",
            trainer="张主任",
            content="讲解礼仪、站姿、手势、微笑服务规范",
            max_participants=30
        ),
        models.Training(
            title="展品讲解话术训练",
            training_date=today - timedelta(days=10),
            start_time="14:00", end_time="16:30",
            location="纪念馆展厅",
            trainer="李讲师",
            content="重要展品背景介绍、讲解技巧、互动问答方法",
            max_participants=25
        ),
        models.Training(
            title="突发情况应对培训",
            training_date=today - timedelta(days=5),
            start_time="09:00", end_time="11:00",
            location="纪念馆多功能厅",
            trainer="王队长",
            content="观众突发身体不适、走失儿童、设备故障等应急处理",
            max_participants=40
        ),
        models.Training(
            title="高级讲解进阶课程",
            training_date=today - timedelta(days=60),
            start_time="14:00", end_time="17:00",
            location="纪念馆培训室B",
            trainer="专家讲师团",
            content="深度历史背景、情感表达、即兴讲解能力提升",
            max_participants=20
        ),
    ]
    db.add_all(trainings)
    db.flush()

    attendances = [
        models.TrainingAttendance(volunteer_id=volunteers[1].id, training_id=trainings[0].id, attended=1, remarks="学习认真"),
        models.TrainingAttendance(volunteer_id=volunteers[1].id, training_id=trainings[1].id, attended=1),
        models.TrainingAttendance(volunteer_id=volunteers[2].id, training_id=trainings[0].id, attended=1),
        models.TrainingAttendance(volunteer_id=volunteers[2].id, training_id=trainings[1].id, attended=1, remarks="表现积极"),
        models.TrainingAttendance(volunteer_id=volunteers[2].id, training_id=trainings[2].id, attended=1),
        models.TrainingAttendance(volunteer_id=volunteers[3].id, training_id=trainings[0].id, attended=1),
        models.TrainingAttendance(volunteer_id=volunteers[3].id, training_id=trainings[1].id, attended=1),
        models.TrainingAttendance(volunteer_id=volunteers[4].id, training_id=trainings[3].id, attended=1, remarks="课程成绩优秀"),
        models.TrainingAttendance(volunteer_id=volunteers[5].id, training_id=trainings[3].id, attended=1),
        models.TrainingAttendance(volunteer_id=volunteers[6].id, training_id=trainings[3].id, attended=1, remarks="作为助教参与"),
        models.TrainingAttendance(volunteer_id=volunteers[7].id, training_id=trainings[3].id, attended=1, remarks="作为助教参与分享经验"),
    ]
    db.add_all(attendances)

    assessments = [
        models.Assessment(
            volunteer_id=volunteers[3].id,
            assessment_date=today - timedelta(days=60),
            topic="新中国发展史展厅",
            score=88.0,
            result=models.AssessmentResult.PASSED,
            examiner="考评组A",
            comments="讲解流畅，富有感染力，通过考核"
        ),
        models.Assessment(
            volunteer_id=volunteers[4].id,
            assessment_date=today - timedelta(days=150),
            topic="雷锋精神展区",
            score=92.0,
            result=models.AssessmentResult.PASSED,
            examiner="考评组B",
            comments="情感真挚，互动性强，优秀通过"
        ),
        models.Assessment(
            volunteer_id=volunteers[5].id,
            assessment_date=today - timedelta(days=270),
            topic="改革开放主题展厅",
            score=90.5,
            result=models.AssessmentResult.PASSED,
            examiner="考评组A",
            comments="历史知识扎实，讲解条理清晰"
        ),
        models.Assessment(
            volunteer_id=volunteers[6].id,
            assessment_date=today - timedelta(days=420),
            topic="社会主义建设时期",
            score=95.0,
            result=models.AssessmentResult.PASSED,
            examiner="考评组C",
            comments="表现卓越，可作为培训助教候选人"
        ),
        models.Assessment(
            volunteer_id=volunteers[7].id,
            assessment_date=today - timedelta(days=570),
            topic="全馆综合讲解",
            score=97.5,
            result=models.AssessmentResult.PASSED,
            examiner="馆长亲考",
            comments="金牌讲解员水平，全馆通讲能力"
        ),
        models.Assessment(
            volunteer_id=volunteers[2].id,
            assessment_date=today + timedelta(days=3),
            topic="长征故事展区",
            result=models.AssessmentResult.PENDING,
            examiner="待安排考评组",
            comments="培训完成，等待正式考核"
        ),
    ]
    db.add_all(assessments)

    assessment_topics = [
        models.AssessmentTopic(
            name="革命先烈事迹",
            description="涵盖革命烈士纪念馆主要展区的讲解内容",
            pass_score=70.0,
            is_active=True
        ),
        models.AssessmentTopic(
            name="抗日战争历史",
            description="抗日战争主题展区的讲解考核",
            pass_score=70.0,
            is_active=True
        ),
        models.AssessmentTopic(
            name="长征故事",
            description="长征精神主题展区讲解",
            pass_score=70.0,
            is_active=True
        ),
        models.AssessmentTopic(
            name="新中国发展史",
            description="建国以来重大历史事件讲解",
            pass_score=70.0,
            is_active=True
        ),
        models.AssessmentTopic(
            name="雷锋精神",
            description="雷锋事迹与精神传承展区",
            pass_score=65.0,
            is_active=True
        ),
        models.AssessmentTopic(
            name="改革开放历程",
            description="改革开放伟大成就展区",
            pass_score=70.0,
            is_active=True
        ),
    ]
    db.add_all(assessment_topics)
    db.flush()

    training_batches = [
        models.TrainingBatch(
            name="2026年春季讲解员培训班（第一期）",
            topic_id=assessment_topics[0].id,
            description="针对革命先烈事迹主题的系统培训，共4次课程",
            min_attendance_rate=75.0,
            capacity=25,
            status=models.TrainingBatchStatus.IN_PROGRESS,
            start_date=today - timedelta(days=25),
            end_date=today - timedelta(days=5)
        ),
        models.TrainingBatch(
            name="2026年春季讲解员培训班（第二期）",
            topic_id=assessment_topics[3].id,
            description="新中国发展史主题进阶培训",
            min_attendance_rate=80.0,
            capacity=20,
            status=models.TrainingBatchStatus.ENROLLING,
            start_date=today + timedelta(days=3),
            end_date=today + timedelta(days=20)
        ),
    ]
    db.add_all(training_batches)
    db.flush()

    training_sessions = []
    for i, batch in enumerate(training_batches):
        if i == 0:
            session_dates = [25, 20, 15, 5]
            session_titles = [
                "第1课：革命先烈生平概述",
                "第2课：重点展品背景深度解析",
                "第3课：讲解礼仪与话术训练",
                "第4课：模拟讲解与互动技巧"
            ]
            for j in range(4):
                training_sessions.append(models.TrainingSession(
                    batch_id=batch.id,
                    session_no=j + 1,
                    title=session_titles[j],
                    session_date=today - timedelta(days=session_dates[j]),
                    start_time="09:00",
                    end_time="11:30",
                    location=f"培训室{'A' if j % 2 == 0 else 'B'}",
                    trainer=["张主任", "李讲师", "王队长", "专家讲师团"][j],
                    content=f"{session_titles[j]}详细内容"
                ))
        else:
            for j in range(3):
                training_sessions.append(models.TrainingSession(
                    batch_id=batch.id,
                    session_no=j + 1,
                    title=f"第{j + 1}课：新中国发展史{'开篇' if j == 0 else '进阶' if j == 1 else '综合'}",
                    session_date=today + timedelta(days=3 + j * 7),
                    start_time="14:00",
                    end_time="16:30",
                    location="培训室C",
                    trainer="党史专家团",
                    content=f"新中国发展史系列课程第{j + 1}讲"
                ))
    db.add_all(training_sessions)
    db.flush()

    training_volunteers = [volunteers[1], volunteers[2], volunteers[3]]
    enrollments = []
    for v in training_volunteers:
        enrollments.append(models.Enrollment(
            volunteer_id=v.id,
            batch_id=training_batches[0].id,
            status=models.EnrollmentStatus.COMPLETED,
            enrolled_at=today - timedelta(days=28),
            completed_at=today - timedelta(days=4)
        ))
    enrollments.append(models.Enrollment(
        volunteer_id=volunteers[0].id,
        batch_id=training_batches[0].id,
        status=models.EnrollmentStatus.ENROLLED,
        enrolled_at=today - timedelta(days=26)
    ))
    db.add_all(enrollments)
    db.flush()

    session_attendances = []
    batch_1_sessions = [s for s in training_sessions if s.batch_id == training_batches[0].id]
    for en in enrollments[:3]:
        for session in batch_1_sessions:
            session_attendances.append(models.SessionAttendance(
                enrollment_id=en.id,
                session_id=session.id,
                volunteer_id=en.volunteer_id,
                attended=True,
                checked_at=today - timedelta(days=5)
            ))
    en4 = enrollments[3]
    for idx, session in enumerate(batch_1_sessions):
        session_attendances.append(models.SessionAttendance(
            enrollment_id=en4.id,
            session_id=session.id,
            volunteer_id=en4.volunteer_id,
            attended=idx < 2,
            checked_at=today - timedelta(days=5) if idx < 2 else None
        ))
    db.add_all(session_attendances)
    db.flush()

    criteria_data = [
        ("仪容仪表与站姿", "着装规范、站姿端正、精神面貌", 15.0, 1),
        ("讲解内容准确性", "史实准确、数据无误、内容完整", 30.0, 2),
        ("语言表达流畅度", "吐字清晰、语速适中、感染力强", 25.0, 3),
        ("互动与应变能力", "观众提问应答、突发情况处理", 20.0, 4),
        ("讲解时间把控", "时长合理、重点突出、节奏得当", 10.0, 5),
    ]
    criteria = []
    for topic in assessment_topics[:4]:
        for name, desc, max_score, order in criteria_data:
            c = models.AssessmentCriterion(
                topic_id=topic.id,
                name=name,
                description=desc,
                max_score=max_score,
                sort_order=order
            )
            criteria.append(c)
    db.add_all(criteria)
    db.flush()

    questions_data = [
        ("请介绍一位你最熟悉的革命先烈的主要事迹？", "按讲解内容准确性评分"),
        ("面对观众提出超出准备范围的问题，你如何应对？", "考察应变能力和知识储备"),
        ("如何在讲解中调动不同年龄段观众的兴趣？", "考察互动能力和讲解技巧"),
    ]
    questions = []
    for topic in assessment_topics[:4]:
        for idx, (q, ref) in enumerate(questions_data):
            questions.append(models.AssessmentQuestion(
                topic_id=topic.id,
                question=q,
                reference_answer=ref,
                sort_order=idx + 1
            ))
    db.add_all(questions)
    db.flush()

    v2_assessments = []
    assessment_scores_list = []
    certifications_list = []

    certified_cases = [
        (volunteers[3], 3, [60, 150, 270, 420, 570][0], ["考评组A", 88.0, "讲解流畅，富有感染力，通过考核"]),
        (volunteers[4], 4, [60, 150, 270, 420, 570][1], ["考评组B", 92.0, "情感真挚，互动性强，优秀通过"]),
        (volunteers[5], 5, [60, 150, 270, 420, 570][2], ["考评组A", 90.5, "历史知识扎实，讲解条理清晰"]),
        (volunteers[6], 3, [60, 150, 270, 420, 570][3], ["考评组C", 95.0, "表现卓越，可作为培训助教候选人"]),
        (volunteers[7], 2, [60, 150, 270, 420, 570][4], ["馆长亲考", 97.5, "金牌讲解员水平，全馆通讲能力"]),
    ]

    for v, tidx, days_ago, meta in certified_cases:
        examiner, total_score_val, comment = meta
        topic = assessment_topics[tidx] if tidx < len(assessment_topics) else assessment_topics[0]
        criteria_for_topic = [c for c in criteria if c.topic_id == topic.id]

        a = models.Assessment(
            volunteer_id=v.id,
            topic_id=topic.id,
            training_batch_id=training_batches[0].id if tidx < 3 else None,
            assessment_date=today - timedelta(days=days_ago),
            topic=topic.name,
            score=float(total_score_val),
            result=models.AssessmentResult.PASSED,
            is_retake=False,
            attempt_no=1,
            examiner=examiner,
            comments=comment
        )
        v2_assessments.append(a)
        db.add(a)
        db.flush()

        if criteria_for_topic:
            remaining = total_score_val
            for ci, c in enumerate(criteria_for_topic):
                if ci < len(criteria_for_topic) - 1:
                    s_val = round(c.max_score * (total_score_val / 100.0), 1)
                    remaining -= s_val
                else:
                    s_val = round(remaining, 1)
                assessment_scores_list.append(models.AssessmentScore(
                    assessment_id=a.id,
                    criterion_id=c.id,
                    score=float(s_val),
                    comments="评分项表现良好"
                ))

        cert_no = f"HLJ{2026 if days_ago < 365 else 2025}{topic.id:03d}{v.id:04d}{a.id:05d}"
        certifications_list.append(models.VolunteerCertification(
            volunteer_id=v.id,
            topic_id=topic.id,
            assessment_id=a.id,
            certificate_no=cert_no,
            issued_date=today - timedelta(days=days_ago),
            is_active=True
        ))

    db.add_all(assessment_scores_list)
    db.add_all(certifications_list)
    db.flush()

    topics = ["革命先烈事迹", "抗日战争历史", "长征故事", "新中国发展史", "雷锋精神", "改革开放历程"]
    locations = ["展厅A", "展厅B", "展厅C", "中央大厅", "专题展区"]
    time_slots = []

    for i in range(10):
        d = today + timedelta(days=i + 2)
        if d.weekday() >= 5:
            time_slots.append(models.TimeSlot(
                slot_date=d, start_time="09:00", end_time="11:00",
                topic=topics[i % len(topics)], location=locations[i % len(locations)],
                status=models.TimeSlotStatus.AVAILABLE
            ))
            time_slots.append(models.TimeSlot(
                slot_date=d, start_time="14:00", end_time="16:00",
                topic=topics[(i + 2) % len(topics)], location=locations[(i + 1) % len(locations)],
                status=models.TimeSlotStatus.AVAILABLE
            ))

    time_slots.append(models.TimeSlot(
        slot_date=today + timedelta(days=3),
        start_time="09:00", end_time="11:00",
        topic="雷锋精神", location="展厅B",
        status=models.TimeSlotStatus.CLAIMED,
        volunteer_id=volunteers[3].id
    ))
    time_slots.append(models.TimeSlot(
        slot_date=today + timedelta(days=4),
        start_time="14:00", end_time="16:00",
        topic="改革开放历程", location="专题展区",
        status=models.TimeSlotStatus.CLAIMED,
        volunteer_id=volunteers[4].id
    ))

    db.add_all(time_slots)
    db.flush()

    service_records = []
    teachers = ["王老师", "李老师", "张老师", "刘老师", "陈老师"]
    ratings = [4, 5, 5, 4, 5, 5, 4]
    hours_pattern = [2.0, 1.5, 2.5, 2.0, 3.0, 1.5, 2.0]

    certified_volunteers = volunteers[3:8]
    target_hours = [12.0, 32.0, 62.0, 102.0, 152.0]

    for vi, v in enumerate(certified_volunteers):
        accumulated = 0.0
        ri = 0
        while accumulated < target_hours[vi]:
            service_date = today - timedelta(days=2 + ri * 2 + vi * 5)
            hours = hours_pattern[(ri + vi) % len(hours_pattern)]
            service_records.append(models.ServiceRecord(
                volunteer_id=v.id,
                time_slot_id=None,
                service_date=service_date,
                service_hours=hours,
                audience_count=20 + ri * 3 + vi * 5,
                teacher_name=teachers[(ri + vi) % len(teachers)],
                teacher_rating=ratings[(ri + vi) % len(ratings)],
                teacher_comments="讲解认真细致，观众反馈良好" if ratings[(ri + vi) % len(ratings)] >= 4 else "基本完成讲解任务，继续努力"
            ))
            accumulated += hours
            ri += 1

    db.add_all(service_records)

    benefits = [
        models.Benefit(
            name="一星纪念徽章",
            benefit_type=models.BenefitType.BADGE,
            description="达到一星级讲解员可兑换，精美金属纪念徽章",
            points_cost=100,
            stock=100,
            is_active=True,
            sort_order=1
        ),
        models.Benefit(
            name="二星纪念徽章",
            benefit_type=models.BenefitType.BADGE,
            description="达到二星级讲解员可兑换，镀银纪念徽章",
            points_cost=200,
            stock=80,
            is_active=True,
            sort_order=2
        ),
        models.Benefit(
            name="三星纪念徽章",
            benefit_type=models.BenefitType.BADGE,
            description="达到三星级讲解员可兑换，镀金纪念徽章",
            points_cost=350,
            stock=50,
            is_active=True,
            sort_order=3
        ),
        models.Benefit(
            name="四星纪念徽章",
            benefit_type=models.BenefitType.BADGE,
            description="达到四星级讲解员可兑换，镶钻纪念徽章",
            points_cost=500,
            stock=30,
            is_active=True,
            sort_order=4
        ),
        models.Benefit(
            name="五星金牌纪念章",
            benefit_type=models.BenefitType.BADGE,
            description="达到五星级讲解员可兑换，纯金纪念徽章",
            points_cost=800,
            stock=10,
            is_active=True,
            sort_order=5
        ),
        models.Benefit(
            name="周末热门时段优先认领券",
            benefit_type=models.BenefitType.PRIORITY_SLOT,
            description="使用后可优先认领一个周末热门讲解时段",
            points_cost=50,
            stock=200,
            is_active=True,
            sort_order=6
        ),
        models.Benefit(
            name="节假日热门时段优先认领券",
            benefit_type=models.BenefitType.PRIORITY_SLOT,
            description="使用后可优先认领一个节假日热门讲解时段",
            points_cost=80,
            stock=100,
            is_active=True,
            sort_order=7
        ),
        models.Benefit(
            name="定制讲解员专属笔记本",
            benefit_type=models.BenefitType.OTHER,
            description="印有纪念馆logo的专属笔记本，记录讲解心得",
            points_cost=30,
            stock=150,
            is_active=True,
            sort_order=8
        ),
    ]
    db.add_all(benefits)
    db.flush()

    # 为现有家长建立 v1 监护授权（替代静态手机号绑定），权限为全部范围、长期有效
    for seq, v in enumerate(volunteers, start=1):
        if not v.parent_phone:
            continue
        guardian = models.Guardian(
            name=v.parent_name or f"监护人{v.parent_phone[-4:]}",
            phone=v.parent_phone,
            relation=models.GuardianRelation.PARENT,
        )
        db.add(guardian)
        db.flush()
        db.add(models.GuardianAuthorization(
            grant_seq=seq, version_no=1, guardian_id=guardian.id,
            volunteer_id=v.id, relation=models.GuardianRelation.PARENT,
            scopes="view,enroll,benefit", status=models.AuthorizationStatus.ACTIVE,
        ))

    db.commit()
    print("种子数据创建完成！")
    print(f"  - 学校: {db.query(models.School).count()} 所")
    print(f"  - 星级标准: {db.query(models.StarLevel).count()} 个")
    print(f"  - 志愿者: {db.query(models.Volunteer).count()} 名")
    print(f"  - 考核主题: {db.query(models.AssessmentTopic).count()} 个")
    print(f"  - 培训期次: {db.query(models.TrainingBatch).count()} 期")
    print(f"  - 培训课次: {db.query(models.TrainingSession).count()} 节")
    print(f"  - 报名入班: {db.query(models.Enrollment).count()} 人次")
    print(f"  - 课次出勤: {db.query(models.SessionAttendance).count()} 条")
    print(f"  - 评分项: {db.query(models.AssessmentCriterion).count()} 项")
    print(f"  - 考核题目: {db.query(models.AssessmentQuestion).count()} 道")
    print(f"  - 培训课程(旧): {db.query(models.Training).count()} 场")
    print(f"  - 考核记录: {db.query(models.Assessment).count()} 条")
    print(f"  - 评分明细: {db.query(models.AssessmentScore).count()} 条")
    print(f"  - 讲解资格证: {db.query(models.VolunteerCertification).count()} 张")
    print(f"  - 服务记录: {db.query(models.ServiceRecord).count()} 条")
    print(f"  - 权益商品: {db.query(models.Benefit).count()} 个")
