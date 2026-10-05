from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime
from database import get_db
import models, schemas
from routers.points import spend_points

router = APIRouter(prefix="/api/benefits", tags=["权益管理"])


@router.get("/", response_model=List[schemas.Benefit])
def list_benefits(benefit_type: str = None, is_active: bool = None,
                  skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(models.Benefit)
    if benefit_type:
        query = query.filter(models.Benefit.benefit_type == benefit_type)
    if is_active is not None:
        query = query.filter(models.Benefit.is_active == is_active)
    return query.order_by(models.Benefit.sort_order, models.Benefit.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{benefit_id}", response_model=schemas.Benefit)
def get_benefit(benefit_id: int, db: Session = Depends(get_db)):
    benefit = db.query(models.Benefit).filter(models.Benefit.id == benefit_id).first()
    if not benefit:
        raise HTTPException(status_code=404, detail="权益不存在")
    return benefit


@router.post("/", response_model=schemas.Benefit)
def create_benefit(benefit: schemas.BenefitCreate, db: Session = Depends(get_db)):
    db_benefit = models.Benefit(**benefit.model_dump())
    db.add(db_benefit)
    db.commit()
    db.refresh(db_benefit)
    return db_benefit


@router.put("/{benefit_id}", response_model=schemas.Benefit)
def update_benefit(benefit_id: int, benefit_update: schemas.BenefitUpdate, db: Session = Depends(get_db)):
    benefit = db.query(models.Benefit).filter(models.Benefit.id == benefit_id).first()
    if not benefit:
        raise HTTPException(status_code=404, detail="权益不存在")
    update_data = benefit_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(benefit, key, value)
    db.commit()
    db.refresh(benefit)
    return benefit


@router.delete("/{benefit_id}")
def delete_benefit(benefit_id: int, db: Session = Depends(get_db)):
    benefit = db.query(models.Benefit).filter(models.Benefit.id == benefit_id).first()
    if not benefit:
        raise HTTPException(status_code=404, detail="权益不存在")
    db.delete(benefit)
    db.commit()
    return {"message": "删除成功"}


@router.get("/exchanges", response_model=List[schemas.BenefitExchange])
def list_exchanges(volunteer_id: int = None, status: str = None,
                   skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(models.BenefitExchange)
    if volunteer_id:
        query = query.filter(models.BenefitExchange.volunteer_id == volunteer_id)
    if status:
        status_enum = None
        for s in models.ExchangeStatus:
            if s.value == status or s.name == status:
                status_enum = s
                break
        if status_enum:
            query = query.filter(models.BenefitExchange.status == status_enum)
    return query.order_by(models.BenefitExchange.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/exchanges/{exchange_id}", response_model=schemas.BenefitExchange)
def get_exchange(exchange_id: int, db: Session = Depends(get_db)):
    exchange = db.query(models.BenefitExchange).filter(models.BenefitExchange.id == exchange_id).first()
    if not exchange:
        raise HTTPException(status_code=404, detail="兑换记录不存在")
    return exchange


@router.post("/exchanges", response_model=schemas.BenefitExchange)
def create_exchange(exchange: schemas.BenefitExchangeCreate, db: Session = Depends(get_db)):
    benefit = db.query(models.Benefit).filter(models.Benefit.id == exchange.benefit_id).first()
    if not benefit:
        raise HTTPException(status_code=404, detail="权益不存在")
    if not benefit.is_active:
        raise HTTPException(status_code=400, detail="该权益已下架")

    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == exchange.volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    total_points = benefit.points_cost * exchange.quantity
    if benefit.stock > 0 and benefit.stock < exchange.quantity:
        raise HTTPException(status_code=400, detail="库存不足")

    if (volunteer.points_balance or 0) < total_points:
        raise HTTPException(status_code=400, detail="积分不足")

    db_exchange = models.BenefitExchange(
        volunteer_id=exchange.volunteer_id,
        benefit_id=exchange.benefit_id,
        points_spent=total_points,
        quantity=exchange.quantity,
        delivery_info=exchange.delivery_info,
        notes=exchange.notes,
        status=models.ExchangeStatus.PENDING
    )
    db.add(db_exchange)
    db.flush()

    spend_points(
        db=db,
        volunteer_id=exchange.volunteer_id,
        points=total_points,
        source=models.PointsSource.EXCHANGE_BADGE if benefit.benefit_type == models.BenefitType.BADGE
               else models.PointsSource.EXCHANGE_PRIORITY_SLOT,
        description=f"兑换{benefit.name} x{exchange.quantity}",
        exchange_id=db_exchange.id
    )

    if benefit.stock > 0:
        benefit.stock -= exchange.quantity

    db.commit()
    db.refresh(db_exchange)
    return db_exchange


@router.put("/exchanges/{exchange_id}", response_model=schemas.BenefitExchange)
def update_exchange(exchange_id: int, exchange_update: schemas.BenefitExchangeUpdate,
                    db: Session = Depends(get_db)):
    exchange = db.query(models.BenefitExchange).filter(models.BenefitExchange.id == exchange_id).first()
    if not exchange:
        raise HTTPException(status_code=404, detail="兑换记录不存在")

    update_data = exchange_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(exchange, key, value)

    if exchange_update.status == models.ExchangeStatus.COMPLETED:
        exchange.fulfilled_at = datetime.utcnow()

    db.commit()
    db.refresh(exchange)
    return exchange


@router.get("/exchanges/volunteer/{volunteer_id}", response_model=List[schemas.BenefitExchange])
def get_volunteer_exchanges(volunteer_id: int, skip: int = 0, limit: int = 100,
                            db: Session = Depends(get_db)):
    volunteer = db.query(models.Volunteer).filter(models.Volunteer.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="志愿者不存在")

    return db.query(models.BenefitExchange).filter(
        models.BenefitExchange.volunteer_id == volunteer_id
    ).order_by(models.BenefitExchange.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/stats/exchanges", response_model=schemas.ExchangeStats)
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


@router.get("/stats/by-benefit", response_model=List[schemas.BenefitStats])
def get_benefit_stats(db: Session = Depends(get_db)):
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

    return result
