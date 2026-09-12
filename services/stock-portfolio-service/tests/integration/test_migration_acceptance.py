"""Isolated migration acceptance additions; synthetic data and vendor fixtures only."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import portfolio as models
from app.models.cash_transaction import CashTransaction
from app.models.fx_rate import FXRate, FxRate
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.models.symbol_map import SymbolMap
from app.routers import imports
from app.services import per_date_verify, fx_rate_service, post_import_orchestrator as orch
from app.services import networth_backfill_service as nbs, dividend_event_service
from tests.unit.test_fx_rate_service import FakeResponse

BASE = '/api/portfolio'
TX = dict(symbol='2330',name='E2E',type='BUY',quantity=10,price='100',fee='2',tax='0',trade_date='2026-09-01T09:00:00+08:00')
DIV = dict(symbol='2330',amount='25',fee='1',tax='0',ex_dividend_date='2026-09-02T09:00:00+08:00',received_date='2026-09-03T09:00:00+08:00')

def account(client):
 r=client.post(BASE+'/accounts/',json=dict(broker='cathay',nickname='E2E-Cathay',currency='TWD',opening_balance='100000',opening_date='2026-01-01',is_active=True))
 assert r.status_code==200,r.text
 return r.json()['id']

def test_http_transaction_dividend_crud_and_cash_invariants(client,db_session,monkeypatch):
 monkeypatch.setenv('CASH_LEG_ENABLED','true')
 account(client)
 tx=client.post(BASE+'/transactions',json=TX);assert tx.status_code==200,tx.text
 tid=tx.json()['id']
 def legs():
  db_session.expire_all()
  return {r.type.value:r.amount for r in db_session.query(CashTransaction).filter_by(related_transaction_id=tid)}
 assert legs()=={'buy_settle':Decimal('-1000'),'fee':Decimal('-2')}
 assert client.put(BASE+f'/transactions/{tid}',json={**TX,'quantity':12,'fee':'3'}).status_code==200
 assert legs()=={'buy_settle':Decimal('-1200'),'fee':Decimal('-3')}
 before=legs()
 assert client.put(BASE+f'/transactions/{tid}',json={**TX,'quantity':0}).status_code==422
 assert legs()==before
 dr=client.post(BASE+'/dividends',json=DIV);assert dr.status_code==200,dr.text
 did=dr.json()['id']
 update=client.put(BASE+f'/dividends/{did}',json={**DIV,'amount':'30'})
 assert update.status_code==200,update.text
 assert Decimal(update.json()['amount'])==30
 assert client.delete(BASE+f'/dividends/{did}').status_code==200
 assert client.delete(BASE+f'/dividends/{did}').status_code==404
 assert db_session.query(CashTransaction).filter_by(related_dividend_id=did).count()==0
 assert client.delete(BASE+f'/transactions/{tid}').status_code==200
 assert legs()=={}
 assert client.delete(BASE+f'/transactions/{tid}').status_code==404
 assert db_session.query(models.Transaction).count()==0
 assert db_session.query(models.Dividend).count()==0
 assert db_session.query(CashTransaction).count()==0

@pytest.mark.parametrize('kind,header,row,model',[
 ('transactions','symbol,type,quantity,price,trade_date,fee,tax,name','0050,BUY,10,100,2026-09-01,0,0,E2E',models.Transaction),
 ('dividends','symbol,amount,ex_dividend_date,received_date','0050,25,2026-09-02,2026-09-03',models.Dividend),
])
def test_generic_csv_preview_commit_duplicate(client,db_session,kind,header,row,model):
 data=(header+'\n'+row+'\n').encode()
 def upload(dry):return client.post(BASE+'/imports/'+kind,params={'dry_run':dry},files={'file':('synthetic.csv',data,'text/csv')})
 preview=upload('true');assert preview.status_code==200,preview.text
 assert preview.json()['errors']==[]
 assert db_session.query(model).count()==0
 committed=upload('false');assert committed.status_code==200,committed.text
 assert committed.json()['created']==1 and db_session.query(model).count()==1
 if kind == 'dividends':
  filtered=client.get(BASE+'/dividends',params={'source':'csv'})
  assert filtered.status_code==200 and filtered.json()['total']==1,filtered.text
 repeated=upload('false');assert repeated.json()['created']==0
 assert repeated.json()['skipped_duplicates']==1 and db_session.query(model).count()==1

@pytest.mark.parametrize('vendor_status,name,wanted',[('ok','台積電','verified'),('ok','不同','name_mismatch'),('not_traded',None,'not_traded_on_date'),('failed',None,'fetch_failed')])
def test_verify_symbol_http_vendor_states(client,monkeypatch,vendor_status,name,wanted):
 monkeypatch.setattr(per_date_verify,'fetch_name_for_date',lambda *a:(name,vendor_status))
 response=client.post(BASE+'/imports/verify-symbol',json={'name':'台積電','code':'2330','trade_date':'2026-09-01'})
 assert response.status_code==200 and response.json()['status']==wanted
 assert client.post(BASE+'/imports/verify-symbol',json={}).status_code==400


def test_broker_cash_flow_http_sign_idempotence_and_cathay(client,db_session):
 account(client)
 payload=dict(broker='IB',date='2026-09-01',type='deposit',amount='100',currency='USD',fx_rate_to_twd='32',import_fingerprint='e2e-deposit')
 r=client.post(BASE+'/broker-cash-flows',json=payload);assert r.status_code==201,r.text
 assert Decimal(r.json()['balance'])==100
 assert Decimal(client.post(BASE+'/broker-cash-flows',json=payload).json()['balance'])==100
 withdrawal={**payload,'type':'withdrawal','amount':'20','import_fingerprint':'e2e-withdraw'}
 assert Decimal(client.post(BASE+'/broker-cash-flows',json=withdrawal).json()['balance'])==80
 cathay={**payload,'broker':'TW_CATHAY','currency':'TWD','import_fingerprint':'e2e-cathay'}
 r=client.post(BASE+'/broker-cash-flows',json=cathay);assert r.status_code==201,r.text
 assert db_session.query(CashTransaction).count()==1


def test_fx_http_lookup_refresh_persistence_and_repeat(client,db_session,monkeypatch):
 for currency,value in [('USD','32'),('GBP','40')]:
  db_session.merge(FXRate(currency=currency,date=date(2026,9,1),rate_to_twd=Decimal(value),source='fixture'))
 db_session.commit()
 for currency,expected in [('TWD','1'),('USD','32'),('GBP','40'),('GBp','0.4')]:
  r=client.get(BASE+'/fx/rate',params={'currency':currency,'on':'2026-09-02'})
  assert r.status_code==200 and Decimal(r.json()['rate_to_twd'])==Decimal(expected)
 assert client.get(BASE+'/fx/rate',params={'currency':'EUR','on':'2026-09-02'}).json()['rate_to_twd'] is None
 real=fx_rate_service.fetch_and_store
 def configured(db,**kw):
  return real(db,**kw,http_get=lambda *a,**k:FakeResponse(200,{'date':'2026-09-02','usd':{'twd':'32.5'}}))
 monkeypatch.setattr(fx_rate_service,'fetch_and_store',configured)
 for _ in range(2):
  r=client.post(BASE+'/fx/refresh',json={'base_currencies':['USD'],'quote_currencies':['TWD'],'asof':'2026-09-02'})
  assert r.status_code==200 and r.json()['success'] is True,r.text
  assert db_session.query(FxRate).filter_by(date=date(2026,9,2),base_currency='USD',quote_currency='TWD').count()==1


def test_symbol_refresh_http_upsert_repeat(client,db_session,monkeypatch):
 import twstock
 monkeypatch.setattr(twstock,'__update_codes',lambda:None)
 monkeypatch.setattr(twstock,'codes',{'2330':SimpleNamespace(name='E2E台積電',market='TWSE',type='股票')})
 for _ in range(2):
  r=client.post(BASE+'/symbol-map/refresh');assert r.status_code==200,r.text
  assert r.json()['refreshed_count']==1
  assert db_session.query(SymbolMap).count()==1
 assert db_session.get(SymbolMap,'E2E台積電').symbol=='2330'


def test_real_http_background_chain_and_quotes_repeat(client,db_session,monkeypatch):
 # Use actual independently opened DB sessions as production BackgroundTasks do.
 monkeypatch.setattr(imports,'SessionLocal',sessionmaker(bind=db_session.get_bind()))
 monkeypatch.setattr(orch,'today_tw',lambda:date(2026,9,4))
 orch.reset_state_for_tests()
 assert client.post(BASE+'/transactions',json=TX).status_code==200
 real=nbs.backfill_prices_range
 def row(d,symbol='2330'):return SimpleNamespace(symbol=symbol,date=d,open=None,high=None,low=None,close=Decimal('110'),volume=None,turnover=None,source='TWSE')
 def prices(db,start,end,**kw):return real(db,start,end,throttle_sec=0,sleep=lambda _:None,twse_fetcher=lambda d:[row(d)],tpex_fetcher=lambda d:[row(d,'6488')],active_dates=kw.get('active_dates'))
 monkeypatch.setattr(nbs,'backfill_prices_range',prices)
 from app.services import dividend_history_service as history
 monkeypatch.setattr(history,'_http_get',lambda *a,**k:{'stat':'OK','fields':['資料日期','股票代號','權值+息值','權/息'],'data':[['115/09/02','2330','2','息']]})
 monkeypatch.setattr(history,'_http_post_form',lambda *a,**k:{'tables':[{'data':[]}]})
 for _ in range(2):
  r=client.post(BASE+'/imports/recalc',json={'start_date':'2026-09-01','end_date':'2026-09-04'});assert r.status_code==200,r.text
  status=client.get(BASE+'/imports/recalc/status').json()
  assert status['state']=='partial',repr(status)
  assert [s['status'] for s in status['steps']]==['ok','partial','ok']
  assert status['steps'][1]['detail']['deferred_events'][0]['cash_dividend_per_share']=='2'
  db_session.expire_all()
  assert db_session.query(models.Dividend).filter_by(symbol='2330').count()==1
  assert db_session.query(PortfolioSnapshot).count()==4
  assert db_session.query(PriceHistory).count()==8
 r=client.post(BASE+'/imports/refresh-quotes');assert r.status_code==202,r.text
 status=client.get(BASE+'/imports/recalc/status').json()
 assert status['state']=='partial' and status['quote_refresh']['state']=='completed'
 assert status['steps'][1]['detail']['deferred_events']
 assert db_session.query(PortfolioSnapshot).count()==4
 # A vendor failure must surface as partial, with earlier persisted data retained.
 def fail(*a,**k):raise RuntimeError('fixture provider unavailable')
 monkeypatch.setattr(history,'_http_get',fail)
 monkeypatch.setattr(history,'_http_post_form',fail)
 assert client.post(BASE+'/imports/recalc',json={'start_date':'2026-09-01','end_date':'2026-09-04'}).status_code==200
 status=client.get(BASE+'/imports/recalc/status').json()
 assert status['state']=='partial' and status['steps'][1]['status']=='failed',status
 assert db_session.query(models.Dividend).count()==1
 assert db_session.query(models.Dividend).one().receipt_status=='pending'
 assert db_session.query(CashTransaction).filter_by(type='dividend_cash').count()==0
