from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from seed_data import init_db
from routers import volunteers, trainings, assessments, time_slots, service_records, base_data, stats, points, benefits, star_certificates, parents

Base.metadata.create_all(bind=engine)
init_db()

app = FastAPI(
    title="红领巾讲解员志愿者管理系统",
    description="纪念馆红领巾讲解员志愿者服务端管理系统，支持报名、培训、考核、讲解排班、服务时长积累、星级评定、积分激励、权益兑换、电子证书、家长查看等全流程管理",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(volunteers.router)
app.include_router(trainings.router)
app.include_router(assessments.router)
app.include_router(time_slots.router)
app.include_router(service_records.router)
app.include_router(base_data.router)
app.include_router(stats.router)
app.include_router(points.router)
app.include_router(benefits.router)
app.include_router(star_certificates.router)
app.include_router(parents.router)


@app.get("/", tags=["系统"])
def root():
    return {
        "name": "红领巾讲解员志愿者管理系统",
        "version": "2.0.0",
        "description": "纪念馆红领巾讲解员志愿者服务端管理系统，支持培训期次排期、课次出勤、考核题库按项打分、讲解资格发放、积分激励、权益兑换、星级证书、家长查看等全流程精细化管理",
        "docs": "/docs",
        "redoc": "/redoc",
        "status_flow": [
            "报名待审(审核通过) → 培训中(报名入班→按课次出勤→出勤率达标) → 待考核(安排考核→考官按评分项打分) → 已持证(通过→发对应主题资格证 / 不通过→退回培训中可补考) → 已停用"
        ],
        "points_rules": [
            "完成讲解：每小时10积分",
            "老师好评：4星+5积分，5星+10积分"
        ],
        "api_groups": [
            "志愿者管理 (/api/volunteers)",
            "培训管理 - 期次/课次/报名入班/课次出勤 (/api/trainings/batches, /sessions, /enrollments)",
            "考核管理 - 主题/评分项/题库/按项打分/补考/资格证 (/api/assessments/topics, /criteria, /questions, /submit-scores, /retake, /certifications)",
            "讲解时段 (/api/time-slots)",
            "服务记录 - 自动计算积分与星级评定 (/api/service-records)",
            "积分管理 - 积分记录、余额查询、积分统计 (/api/points)",
            "权益管理 - 权益商品、兑换记录、兑换统计 (/api/benefits)",
            "星级证书 - 自动发放、证书查询 (/api/star-certificates)",
            "家长入口 - 查看孩子报名、培训、服务、积分、证书 (/api/parents)",
            "基础数据 - 学校 & 星级 (/api/schools, /api/star-levels)",
            "统计分析 - 含各期培训出勤率与考核一次通过率、积分发放与兑换统计 (/api/stats)"
        ]
    }


@app.get("/api/dashboard", tags=["系统"])
def dashboard():
    from fastapi import Depends
    from sqlalchemy.orm import Session
    from sqlalchemy import func
    from database import get_db
    import models

    db = next(get_db())

    overview = stats.get_overview_stats(db)

    recent_volunteers = db.query(models.Volunteer).order_by(
        models.Volunteer.created_at.desc()
    ).limit(8).all()

    recent_service = db.query(models.ServiceRecord).order_by(
        models.ServiceRecord.service_date.desc()
    ).limit(10).all()

    upcoming_slots = db.query(models.TimeSlot).filter(
        models.TimeSlot.status.in_([models.TimeSlotStatus.AVAILABLE, models.TimeSlotStatus.CLAIMED])
    ).order_by(models.TimeSlot.slot_date, models.TimeSlot.start_time).limit(8).all()

    return {
        "overview": overview,
        "recent_volunteers": recent_volunteers,
        "recent_service_records": recent_service,
        "upcoming_time_slots": upcoming_slots
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
