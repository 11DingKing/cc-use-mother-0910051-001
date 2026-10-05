from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models, schemas

router = APIRouter(prefix="/api/time-slots", tags=["讲解时段"])


@router.get("/", response_model=List[schemas.TimeSlot])
def list_time_slots(
    status: str = None,
    volunteer_id: int = None,
    start_date: str = None,
    end_date: str = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    from datetime import date as date_type
    query = db.query(models.TimeSlot)
    if status:
        status_enum = None
        for s in models.TimeSlotStatus:
            if s.value == status or s.name == status:
                status_enum = s
                break
        if status_enum:
            query = query.filter(models.TimeSlot.status == status_enum)
    if volunteer_id:
        query = query.filter(models.TimeSlot.volunteer_id == volunteer_id)
    if start_date:
        query = query.filter(models.TimeSlot.slot_date >= date_type.fromisoformat(start_date))
    if end_date:
        query = query.filter(models.TimeSlot.slot_date <= date_type.fromisoformat(end_date))
    return query.order_by(models.TimeSlot.slot_date, models.TimeSlot.start_time).offset(skip).limit(limit).all()


@router.get("/{slot_id}", response_model=schemas.TimeSlot)
def get_time_slot(slot_id: int, db: Session = Depends(get_db)):
    slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="时段不存在")
    return slot


@router.post("/", response_model=schemas.TimeSlot)
def create_time_slot(slot: schemas.TimeSlotCreate, db: Session = Depends(get_db)):
    db_slot = models.TimeSlot(**slot.model_dump())
    db.add(db_slot)
    db.commit()
    db.refresh(db_slot)
    return db_slot


@router.post("/{slot_id}/claim", response_model=schemas.TimeSlot)
def claim_time_slot(slot_id: int, claim: schemas.TimeSlotClaim, db: Session = Depends(get_db)):
    slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="时段不存在")
    if slot.status != models.TimeSlotStatus.AVAILABLE:
        raise HTTPException(status_code=400, detail="该时段不可认领")
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == claim.volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")
    if volunteer.status != models.VolunteerStatus.CERTIFIED:
        raise HTTPException(status_code=400, detail="只有已持证志愿者才能认领时段")
    slot.status = models.TimeSlotStatus.CLAIMED
    slot.volunteer_id = claim.volunteer_id
    db.commit()
    db.refresh(slot)
    return slot


@router.post("/{slot_id}/cancel", response_model=schemas.TimeSlot)
def cancel_time_slot(slot_id: int, db: Session = Depends(get_db)):
    slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="时段不存在")
    if slot.status not in [models.TimeSlotStatus.CLAIMED, models.TimeSlotStatus.AVAILABLE]:
        raise HTTPException(status_code=400, detail="当前状态不可取消")
    slot.status = models.TimeSlotStatus.CANCELLED
    db.commit()
    db.refresh(slot)
    return slot


@router.delete("/{slot_id}")
def delete_time_slot(slot_id: int, db: Session = Depends(get_db)):
    slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="时段不存在")
    db.delete(slot)
    db.commit()
    return {"message": "删除成功"}
