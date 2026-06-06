from app.models import portfolio as models
from app.services import broker_dispatch_service


def test_sniff_detects_ib_header() -> None:
    raw = "Statement,Header,域名稱,域值\nStatement,Data,Title,轉賬歷史\n".encode()

    assert broker_dispatch_service.sniff(raw) == models.Broker.IB


def test_sniff_detects_firstrade_header() -> None:
    raw = '"日期","交易類別","數量","說明","代號","賬戶類別","價格","金額"\n'.encode()

    assert broker_dispatch_service.sniff(raw) == models.Broker.FIRSTRADE


def test_sniff_detects_schwab_header() -> None:
    raw = (
        '"Date","Action","Symbol","Description","Quantity","Price",'
        '"Fees & Comm","Amount"\n'
    ).encode()

    assert broker_dispatch_service.sniff(raw) == models.Broker.SCHWAB


def test_sniff_unknown_header_falls_back() -> None:
    assert broker_dispatch_service.sniff(b"symbol,type,quantity\n") is None


def test_sniff_empty_body_falls_back() -> None:
    assert broker_dispatch_service.sniff(b"\n\n") is None
