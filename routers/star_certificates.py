from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import date
from database import get_db
import models, schemas

router = APIRouter(prefix="/api/star-certificates", tags=["星级证书"])


def generate_certificate_no(db: Session, star_level_id: int, volunteer_id: int) -> str:
    today = date.today()
    prefix = f"STAR{star_level_id:02d}{today.year}{today.month:02d}"
    count = db.query(func.count(models.StarCertificate.id)).filter(
        models.StarCertificate.certificate_no.like(f"{prefix}%")
    ).scalar() or 0
    return f"{prefix}{(count + 1):04d}"


def check_and_issue_star_certificate(db: Session, volunteer_id: int):
    from sqlalchemy import func

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer or not volunteer.star_level_id:
        return None

    total_hours = db.query(func.sum(models.ServiceRecord.service_hours)).filter(
        models.ServiceRecord.volunteer_id == volunteer_id
    ).scalar() or 0.0

    existing_cert = db.query(models.StarCertificate).filter(
        models.StarCertificate.volunteer_id == volunteer_id,
        models.StarCertificate.star_level_id == volunteer.star_level_id,
        models.StarCertificate.is_active == True
    ).first()

    if existing_cert:
        return None

    certificate_no = generate_certificate_no(db, volunteer.star_level_id, volunteer_id)

    certificate = models.StarCertificate(
        volunteer_id=volunteer_id,
        star_level_id=volunteer.star_level_id,
        certificate_no=certificate_no,
        total_hours=total_hours,
        is_active=True
    )
    db.add(certificate)
    db.commit()
    db.refresh(certificate)
    return certificate


@router.get("/", response_model=List[schemas.StarCertificate])
def list_certificates(volunteer_id: int = None, star_level_id: int = None,
                      is_active: bool = None, skip: int = 0, limit: int = 100,
                      db: Session = Depends(get_db)):
    query = db.query(models.StarCertificate)
    if volunteer_id:
        query = query.filter(models.StarCertificate.volunteer_id == volunteer_id)
    if star_level_id:
        query = query.filter(models.StarCertificate.star_level_id == star_level_id)
    if is_active is not None:
        query = query.filter(models.StarCertificate.is_active == is_active)
    return query.order_by(models.StarCertificate.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{certificate_id}", response_model=schemas.StarCertificate)
def get_certificate(certificate_id: int, db: Session = Depends(get_db)):
    certificate = db.query(models.StarCertificate).filter(
        models.StarCertificate.id == certificate_id
    ).first()
    if not certificate:
        raise HTTPException(status_code=404, detail="证书不存在")
    return certificate


@router.post("/", response_model=schemas.StarCertificate)
def create_certificate(cert: schemas.StarCertificateCreate, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == cert.volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    star_level = db.query(models.StarLevel).filter(models.StarLevel.id == cert.star_level_id).first()
    if not star_level:
        raise HTTPException(status_code=404, detail="星级不存在")

    certificate_no = cert.certificate_no or generate_certificate_no(db, cert.star_level_id, cert.volunteer_id)

    db_cert = models.StarCertificate(
        volunteer_id=cert.volunteer_id,
        star_level_id=cert.star_level_id,
        certificate_no=certificate_no,
        total_hours=cert.total_hours,
        issued_date=cert.issued_date or date.today(),
        is_active=True
    )
    db.add(db_cert)
    db.commit()
    db.refresh(db_cert)
    return db_cert


@router.get("/volunteer/{volunteer_id}", response_model=List[schemas.StarCertificate])
def get_volunteer_certificates(volunteer_id: int, db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    return db.query(models.StarCertificate).filter(
        models.StarCertificate.volunteer_id == volunteer_id
    ).order_by(models.StarCertificate.created_at.desc()).all()


@router.post("/volunteer/{volunteer_id}/check-and-issue", response_model=schemas.StarCertificate)
def check_and_issue(volunteer_id: int, db: Session = Depends(get_db)):
    certificate = check_and_issue_star_certificate(db, volunteer_id)
    if not certificate:
        raise HTTPException(status_code=400, detail="未达到发证条件或证书已存在")
    return certificate


@router.delete("/{certificate_id}")
def revoke_certificate(certificate_id: int, db: Session = Depends(get_db)):
    certificate = db.query(models.StarCertificate).filter(
        models.StarCertificate.id == certificate_id
    ).first()
    if not certificate:
        raise HTTPException(status_code=404, detail="证书不存在")
    certificate.is_active = False
    db.commit()
    return {"message": "证书已撤销"}
