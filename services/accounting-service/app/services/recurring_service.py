from sqlalchemy.orm import Session, joinedload
from sqlalchemy import extract
from datetime import date
from .. import models, schemas
from fastapi import HTTPException
import logging
from .accounting_validation import (
    ensure_category_exists,
    ensure_payment_method_exists,
    resolve_card_payment_defaults,
)
from .billing_service import safe_date_replace

logger = logging.getLogger(__name__)


def _get_or_create_installment_category_id(db: Session) -> int:
    category = db.query(models.Category).filter(models.Category.name == "分期付款").first()
    if category:
        return int(category.id)

    category = models.Category(name="分期付款")
    db.add(category)
    db.flush()
    return int(category.id)

# --- 自動化生成邏輯 ---

def generate_recurring_items(db: Session):
    today = date.today()
    current_year = today.year
    current_month = today.month
    
    subs = db.query(models.Subscription).filter(
        models.Subscription.active == True
    ).all()
    
    for sub in subs:
        exists = db.query(models.Transaction).filter(
            models.Transaction.subscription_id == sub.id,
            extract('year', models.Transaction.date) == current_year,
            extract('month', models.Transaction.date) == current_month
        ).first()
        
        if not exists:
            new_pending = models.Transaction(
                date=safe_date_replace(today.year, today.month, sub.day_of_month),
                category_id=sub.category_id,
                item=sub.name,
                paid_amount=sub.amount,
                transaction_amount=sub.amount,
                payment_method=sub.payment_method or "信用卡",
                card_id=sub.card_id,
                subscription_id=sub.id,
                transaction_type="EXPENSE"
            )
            db.add(new_pending)

    insts = db.query(models.Installment).filter(
        models.Installment.remaining_periods > 0
    ).all()
    
    for inst in insts:
        # 計算基於 start_date 與當前年份月份的實際期數
        elapsed_months = (current_year - inst.start_date.year) * 12 + (current_month - inst.start_date.month)
        current_period = elapsed_months + 1
        
        if current_period <= 0:
            logger.info("分期付款 %s 尚未到期 (開始日期為 %s)，跳過生成", inst.name, inst.start_date)
            continue
            
        if current_period > inst.total_periods:
            # 已經超出分期總期數，將 remaining_periods 歸零，標記為已結束
            if inst.remaining_periods > 0:
                inst.remaining_periods = 0
                logger.info("分期付款 %s 已超出總期數，自動更新剩餘期數為 0", inst.name)
            continue
            
        exists = db.query(models.Transaction).filter(
            models.Transaction.installment_id == inst.id,
            extract('year', models.Transaction.date) == current_year,
            extract('month', models.Transaction.date) == current_month
        ).first()
        
        if not exists:
            item_name = f"{inst.name} (第 {current_period}/{inst.total_periods} 期)"
            new_pending = models.Transaction(
                date=safe_date_replace(today.year, today.month, inst.start_date.day),
                category_id=_get_or_create_installment_category_id(db),
                item=item_name,
                paid_amount=inst.monthly_amount,
                transaction_amount=inst.monthly_amount,
                payment_method=inst.payment_method or "信用卡",
                card_id=inst.card_id,
                installment_id=inst.id,
                transaction_type="EXPENSE"
            )
            db.add(new_pending)
            inst.remaining_periods = inst.total_periods - current_period

    db.commit()

# --- 訂閱管理 (Subscription CRUD) ---

def get_subscriptions(db: Session):
    subs = db.query(models.Subscription).options(
        joinedload(models.Subscription.card),
        joinedload(models.Subscription.category_info),
    ).all()
    for s in subs:
        if s.card:
            s.card_name = s.card.name
    return subs

def create_subscription(db: Session, sub: schemas.SubscriptionCreate):
    # 校驗分類
    ensure_category_exists(
        db,
        sub.category_id,
        invalid_detail_template="Invalid category_id",
    )

    # 校驗卡片
    if sub.card_id:
        sub.payment_method = resolve_card_payment_defaults(
            db,
            sub.card_id,
            sub.payment_method,
            invalid_detail_template="Invalid card_id",
        )
    
    # 校驗付款工具
    ensure_payment_method_exists(
        db,
        sub.payment_method,
        invalid_detail_template="Invalid payment_method: {payment_method}",
    )

    db_sub = models.Subscription(**sub.model_dump())
    db.add(db_sub)
    db.commit()
    db.refresh(db_sub)
    
    if db_sub.card:
        db_sub.card_name = db_sub.card.name
        
    return db_sub

def update_subscription(db: Session, sub_id: int, sub_update: schemas.SubscriptionUpdate):
    db_sub = db.query(models.Subscription).filter(models.Subscription.id == sub_id).first()
    if not db_sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    
    update_data = sub_update.model_dump(exclude_unset=True)
    
    # 校驗與同步分類
    if "category_id" in update_data:
        if update_data["category_id"] is None:
            raise HTTPException(status_code=400, detail="category_id cannot be null")
        ensure_category_exists(
            db,
            update_data["category_id"],
            invalid_detail_template="Invalid category_id",
        )

    # 校驗卡片
    if "card_id" in update_data and update_data["card_id"]:
        update_data["payment_method"] = resolve_card_payment_defaults(
            db,
            update_data["card_id"],
            update_data.get("payment_method"),
            invalid_detail_template="Invalid card_id",
        )

    if "payment_method" in update_data and update_data["payment_method"]:
        ensure_payment_method_exists(
            db,
            update_data["payment_method"],
            invalid_detail_template="Invalid payment_method: {payment_method}",
        )

    for key, value in update_data.items():
        setattr(db_sub, key, value)
    db.commit()
    db.refresh(db_sub)
    
    if db_sub.card:
        db_sub.card_name = db_sub.card.name
        
    return db_sub

def toggle_subscription_active(db: Session, sub_id: int):
    db_sub = db.query(models.Subscription).filter(models.Subscription.id == sub_id).first()
    if not db_sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    db_sub.active = not db_sub.active
    db.commit()
    db.refresh(db_sub)
    return db_sub

def delete_subscription(db: Session, sub_id: int):
    db_sub = db.query(models.Subscription).filter(models.Subscription.id == sub_id).first()
    if not db_sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    db.delete(db_sub)
    db.commit()
    return {"message": "Subscription deleted"}

# --- 分期管理 (Installment CRUD) ---

def get_installments(db: Session):
    insts = db.query(models.Installment).options(joinedload(models.Installment.card)).all()
    for i in insts:
        if i.card:
            i.card_name = i.card.name
    return insts


def _get_installment_or_404(db: Session, inst_id: int):
    db_inst = db.query(models.Installment).filter(models.Installment.id == inst_id).first()
    if not db_inst:
        raise HTTPException(status_code=404, detail="Installment not found")
    return db_inst

def create_installment(db: Session, inst: schemas.InstallmentCreate):
    # 校驗卡片
    if inst.card_id:
        inst.payment_method = resolve_card_payment_defaults(
            db,
            inst.card_id,
            inst.payment_method,
            invalid_detail_template="Invalid card_id",
        )

    # 校驗付款工具
    ensure_payment_method_exists(
        db,
        inst.payment_method,
        invalid_detail_template="Invalid payment_method: {payment_method}",
    )

    db_inst = models.Installment(**inst.model_dump())
    db.add(db_inst)
    db.commit()
    db.refresh(db_inst)
    
    if db_inst.card:
        db_inst.card_name = db_inst.card.name
        
    return db_inst

def update_installment(db: Session, inst_id: int, inst_update: schemas.InstallmentUpdate):
    db_inst = _get_installment_or_404(db, inst_id)
    
    update_data = inst_update.model_dump(exclude_unset=True)
    
    # 校驗卡片
    if "card_id" in update_data and update_data["card_id"]:
        update_data["payment_method"] = resolve_card_payment_defaults(
            db,
            update_data["card_id"],
            update_data.get("payment_method"),
            invalid_detail_template="Invalid card_id",
        )

    if "payment_method" in update_data and update_data["payment_method"]:
        ensure_payment_method_exists(
            db,
            update_data["payment_method"],
            invalid_detail_template="Invalid payment_method: {payment_method}",
        )

    for key, value in update_data.items():
        setattr(db_inst, key, value)
    db.commit()
    db.refresh(db_inst)
    
    if db_inst.card:
        db_inst.card_name = db_inst.card.name
        
    return db_inst

def delete_installment(db: Session, inst_id: int):
    db_inst = _get_installment_or_404(db, inst_id)

    if int(db_inst.remaining_periods or 0) > 0:
        raise HTTPException(status_code=400, detail="Only completed installments can be deleted")

    db.query(models.Transaction).filter(models.Transaction.installment_id == inst_id).update(
        {models.Transaction.installment_id: None},
        synchronize_session=False,
    )
    db.delete(db_inst)
    db.commit()
    return {"message": "Installment deleted"}
