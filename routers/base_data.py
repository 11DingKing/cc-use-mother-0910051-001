from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models, schemas

router = APIRouter(prefix="/api", tags=["基础数据"])


@router.get("/schools", response_model=List[schemas.School])
def list_schools(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(models.School).offset(skip).limit(limit).all()


@router.get("/schools/{school_id}", response_model=schemas.School)
def get_school(school_id: int, db: Session = Depends(get_db)):
    school = db.query(models.School).filter(models.School.id == school_id).first()
    if not school:
        raise HTTPException(status_code=404, detail="学校不存在")
    return school


@router.post("/schools", response_model=schemas.School)
def create_school(school: schemas.SchoolCreate, db: Session = Depends(get_db)):
    existing = db.query(models.School).filter(models.School.name == school.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="学校已存在")
    db_school = models.School(**school.model_dump())
    db.add(db_school)
    db.commit()
    db.refresh(db_school)
    return db_school


@router.put("/schools/{school_id}", response_model=schemas.School)
def update_school(school_id: int, school: schemas.SchoolCreate, db: Session = Depends(get_db)):
    db_school = db.query(models.School).filter(models.School.id == school_id).first()
    if not db_school:
        raise HTTPException(status_code=404, detail="学校不存在")
    for key, value in school.model_dump().items():
        setattr(db_school, key, value)
    db.commit()
    db.refresh(db_school)
    return db_school


@router.delete("/schools/{school_id}")
def delete_school(school_id: int, db: Session = Depends(get_db)):
    school = db.query(models.School).filter(models.School.id == school_id).first()
    if not school:
        raise HTTPException(status_code=404, detail="学校不存在")
    db.delete(school)
    db.commit()
    return {"message": "删除成功"}


@router.get("/star-levels", response_model=List[schemas.StarLevel])
def list_star_levels(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(models.StarLevel).order_by(models.StarLevel.min_hours).offset(skip).limit(limit).all()


@router.get("/star-levels/{level_id}", response_model=schemas.StarLevel)
def get_star_level(level_id: int, db: Session = Depends(get_db)):
    level = db.query(models.StarLevel).filter(models.StarLevel.id == level_id).first()
    if not level:
        raise HTTPException(status_code=404, detail="星级不存在")
    return level


@router.post("/star-levels", response_model=schemas.StarLevel)
def create_star_level(level: schemas.StarLevelCreate, db: Session = Depends(get_db)):
    existing = db.query(models.StarLevel).filter(models.StarLevel.name == level.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="星级已存在")
    db_level = models.StarLevel(**level.model_dump())
    db.add(db_level)
    db.commit()
    db.refresh(db_level)
    return db_level


@router.put("/star-levels/{level_id}", response_model=schemas.StarLevel)
def update_star_level(level_id: int, level: schemas.StarLevelCreate, db: Session = Depends(get_db)):
    db_level = db.query(models.StarLevel).filter(models.StarLevel.id == level_id).first()
    if not db_level:
        raise HTTPException(status_code=404, detail="星级不存在")
    for key, value in level.model_dump().items():
        setattr(db_level, key, value)
    db.commit()
    db.refresh(db_level)
    return db_level


@router.delete("/star-levels/{level_id}")
def delete_star_level(level_id: int, db: Session = Depends(get_db)):
    level = db.query(models.StarLevel).filter(models.StarLevel.id == level_id).first()
    if not level:
        raise HTTPException(status_code=404, detail="星级不存在")
    db.delete(level)
    db.commit()
    return {"message": "删除成功"}
