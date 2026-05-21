from datetime import date

from app import models, schemas
from app.services import recurring_service
import pytest
from fastapi.testclient import TestClient


def _get_or_create_category(db_session, name: str, color: str = "#64748b") -> models.Category:
    category = db_session.query(models.Category).filter(models.Category.name == name).first()
    if category:
        return category

    category = models.Category(name=name, color=color)
    db_session.add(category)
    db_session.flush()
    return category


def _subscription_create(db_session, *, category_name: str, **kwargs) -> schemas.SubscriptionCreate:
    category = _get_or_create_category(db_session, category_name)
    return schemas.SubscriptionCreate(category_id=category.id, **kwargs)


def test_subscription_and_auto_gen(db_session):
    card = models.CreditCard(name="訂閱卡", billing_day=5, default_payment_method="Apple Pay")
    db_session.add(card)
    db_session.add(models.PaymentMethod(name="Apple Pay", is_active=True))
    db_session.commit()
    db_session.refresh(card)

    subscription = recurring_service.create_subscription(
        db_session,
        _subscription_create(
            db_session,
            category_name="T",
            name="AutoTestSub",
            amount=100,
            day_of_month=1,
            card_id=card.id,
            payment_method="Apple Pay",
        ),
    )
    assert subscription.id is not None

    toggled = recurring_service.toggle_subscription_active(db_session, subscription.id)
    assert toggled.active is False

    reenabled = recurring_service.toggle_subscription_active(db_session, subscription.id)
    assert reenabled.active is True

    recurring_service.generate_recurring_items(db_session)
    generated = db_session.query(models.Transaction).filter(models.Transaction.subscription_id == subscription.id).all()
    assert len(generated) == 1


def test_create_subscription_requires_category_id(client: TestClient, db_session):
    db_session.add(models.PaymentMethod(name="Cash", is_active=True))
    db_session.commit()

    response = client.post(
        "/recurring/subscriptions",
        json={
            "name": "串流",
            "amount": 290,
            "subType": "SUBSCRIPTION",
            "paymentMethod": "Cash",
            "dayOfMonth": 5,
            "active": True,
        },
    )

    assert response.status_code == 422


def test_update_subscription_rejects_null_category_id(client: TestClient, db_session):
    db_session.add(models.PaymentMethod(name="Cash", is_active=True))
    db_session.commit()

    subscription = recurring_service.create_subscription(
        db_session,
        _subscription_create(
            db_session,
            category_name="娛樂",
            name="串流",
            amount=290,
            sub_type="SUBSCRIPTION",
            payment_method="Cash",
            day_of_month=5,
            active=True,
        ),
    )

    response = client.put(
        f"/recurring/subscriptions/{subscription.id}",
        json={"categoryId": None},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "category_id cannot be null"


def test_completed_installment_can_be_deleted_and_detaches_history(db_session):
    db_session.add(models.PaymentMethod(name="信用卡", is_active=True))
    db_session.commit()

    installment = recurring_service.create_installment(
        db_session,
        schemas.InstallmentCreate(
            name="已完成分期",
            total_amount=12000,
            monthly_amount=1000,
            total_periods=12,
            remaining_periods=0,
            start_date=date(2026, 1, 1),
        ),
    )

    transaction = models.Transaction(
        date=date.today(),
        category_id=_get_or_create_category(db_session, "分期付款").id,
        item="已完成分期 (第 12/12 期)",
        paid_amount=1000,
        transaction_amount=1000,
        payment_method="Cash",
        installment_id=installment.id,
        transaction_type="EXPENSE",
    )
    db_session.add(transaction)
    db_session.commit()

    result = recurring_service.delete_installment(db_session, installment.id)

    db_session.expire_all()
    assert result["message"] == "Installment deleted"
    assert db_session.get(models.Installment, installment.id) is None
    assert db_session.get(models.Transaction, transaction.id).installment_id is None


def test_active_installment_cannot_be_deleted(db_session):
    db_session.add(models.PaymentMethod(name="信用卡", is_active=True))
    db_session.commit()

    installment = recurring_service.create_installment(
        db_session,
        schemas.InstallmentCreate(
            name="進行中分期",
            total_amount=6000,
            monthly_amount=500,
            total_periods=12,
            remaining_periods=3,
            start_date=date(2026, 1, 1),
        ),
    )

    with pytest.raises(Exception) as exc_info:
        recurring_service.delete_installment(db_session, installment.id)

    assert getattr(exc_info.value, "status_code", None) == 400
    assert getattr(exc_info.value, "detail", None) == "Only completed installments can be deleted"


def test_recurring_services_apply_card_default_payment_method_consistently(db_session):
    category = models.Category(name="娛樂", color="#123456")
    card = models.CreditCard(name="訂閱卡", billing_day=5, default_payment_method="Apple Pay")
    db_session.add_all(
        [
            category,
            card,
            models.PaymentMethod(name="Apple Pay", is_active=True),
        ]
    )
    db_session.commit()
    db_session.refresh(category)
    db_session.refresh(card)

    subscription = recurring_service.create_subscription(
        db_session,
        _subscription_create(
            db_session,
            category_name="娛樂",
            name="串流",
            amount=100,
            day_of_month=15,
            card_id=card.id,
        ),
    )
    assert subscription.payment_method == "Apple Pay"

    updated_subscription = recurring_service.update_subscription(
        db_session,
        subscription.id,
        schemas.SubscriptionUpdate(card_id=card.id),
    )
    assert updated_subscription.payment_method == "Apple Pay"

    installment = recurring_service.create_installment(
        db_session,
        schemas.InstallmentCreate(
            name="筆電",
            total_amount=36000,
            monthly_amount=3000,
            total_periods=12,
            remaining_periods=12,
            start_date=date(2026, 1, 15),
            card_id=card.id,
        ),
    )
    assert installment.payment_method == "Apple Pay"

    updated_installment = recurring_service.update_installment(
        db_session,
        installment.id,
        schemas.InstallmentUpdate(card_id=card.id),
    )
    assert updated_installment.payment_method == "Apple Pay"


@pytest.mark.parametrize(
    ("today_value", "expected_day"),
    [
        (date(2026, 2, 10), 28),
        (date(2026, 4, 10), 30),
        (date(2026, 3, 10), 31),
    ],
)
def test_generate_recurring_items_uses_real_month_end_for_subscription(db_session, monkeypatch, today_value, expected_day):
    db_session.add(models.PaymentMethod(name="Cash", is_active=True))
    db_session.commit()

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(today_value.year, today_value.month, today_value.day)

    monkeypatch.setattr(recurring_service, "date", FixedDate)

    subscription = recurring_service.create_subscription(
        db_session,
        _subscription_create(
            db_session,
            category_name="娛樂",
            name=f"月底訂閱-{today_value.month}",
            amount=250,
            day_of_month=31,
            payment_method="Cash",
        ),
    )

    recurring_service.generate_recurring_items(db_session)
    generated = db_session.query(models.Transaction).filter(models.Transaction.subscription_id == subscription.id).one()

    assert generated.date == date(today_value.year, today_value.month, expected_day)


def test_generate_recurring_items_uses_real_month_end_for_installment(db_session, monkeypatch):
    db_session.add(models.PaymentMethod(name="Cash", is_active=True))
    db_session.commit()

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 3, 10)

    monkeypatch.setattr(recurring_service, "date", FixedDate)

    installment = recurring_service.create_installment(
        db_session,
        schemas.InstallmentCreate(
            name="月底分期",
            total_amount=9000,
            monthly_amount=3000,
            total_periods=3,
            remaining_periods=3,
            start_date=date(2026, 1, 31),
            payment_method="Cash",
        ),
    )

    recurring_service.generate_recurring_items(db_session)
    generated = db_session.query(models.Transaction).filter(models.Transaction.installment_id == installment.id).one()

    assert generated.date == date(2026, 3, 31)


def test_generate_recurring_items_skips_future_installments(db_session, monkeypatch):
    db_session.add(models.PaymentMethod(name="Cash", is_active=True))
    db_session.commit()

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 20)

    monkeypatch.setattr(recurring_service, "date", FixedDate)

    installment = recurring_service.create_installment(
        db_session,
        schemas.InstallmentCreate(
            name="未來分期",
            total_amount=9000,
            monthly_amount=3000,
            total_periods=3,
            remaining_periods=3,
            start_date=date(2026, 6, 15), # June 15, 2026 (future)
            payment_method="Cash",
        ),
    )

    recurring_service.generate_recurring_items(db_session)
    generated = db_session.query(models.Transaction).filter(models.Transaction.installment_id == installment.id).all()

    # Should be empty because it hasn't started yet
    assert len(generated) == 0


def test_generate_recurring_items_determines_period_by_elapsed_months(db_session, monkeypatch):
    db_session.add(models.PaymentMethod(name="Cash", is_active=True))
    db_session.commit()

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 20)

    monkeypatch.setattr(recurring_service, "date", FixedDate)

    # Created a 3-period installment with start date Feb 15, 2026
    # Today is May 20, 2026.
    # Feb -> Period 1
    # Mar -> Period 2
    # Apr -> Period 3
    # May -> Period 4 (already elapsed/out of range, should skip and set remaining_periods = 0)
    installment = recurring_service.create_installment(
        db_session,
        schemas.InstallmentCreate(
            name="過期分期",
            total_amount=9000,
            monthly_amount=3000,
            total_periods=3,
            remaining_periods=3,
            start_date=date(2026, 2, 15),
            payment_method="Cash",
        ),
    )

    recurring_service.generate_recurring_items(db_session)
    
    # Verify remaining periods is updated to 0 automatically
    db_session.refresh(installment)
    assert installment.remaining_periods == 0

    generated = db_session.query(models.Transaction).filter(models.Transaction.installment_id == installment.id).all()
    assert len(generated) == 0
