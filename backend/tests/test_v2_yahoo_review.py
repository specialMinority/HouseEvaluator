"""Independent final review: configured imports, HTTP protection and host pinning."""
import threading
import time
from unittest.mock import Mock, patch

import pytest

from backend.src import server as server_module
from backend.tests.test_v2_chintai_review import Connection, Response
from backend.tests.test_v2_personal_http import request, server_factory
from backend.v2 import listing_import, public_fetch
from backend.v2.public_fetch import PublicFetchError, PublicResponse

YAHOO = 'https://realestate.yahoo.co.jp/rent/detail/_0000051128845b96f83d36aeab33165a97bd60863e9d/'
CHINTAI = 'https://www.chintai.net/detail/bk-C000000000000000000000000001/'
ROBOTS = 'https://realestate.yahoo.co.jp/robots.txt'


@pytest.mark.parametrize('url', [ROBOTS, YAHOO,
    'https://realestate.yahoo.co.jp/rent/search/03/13/13104/',
    'https://realestate.yahoo.co.jp/rent/search/station/2167/theme/156/?page=2'])
def test_yahoo_host_is_pinned_consistently_for_dns_and_tls_with_unchanged_identity(url):
    connection=Connection(Response())
    with patch.object(public_fetch,'_bounded_dns',return_value=['203.0.113.15']) as dns, \
            patch.object(public_fetch,'_PinnedHTTPSConnection',return_value=connection) as factory:
        assert public_fetch.fetch_public(url,deadline=time.monotonic()+2).status==200
    assert dns.call_args.args[:2]==('realestate.yahoo.co.jp',443)
    assert factory.call_args.args==('realestate.yahoo.co.jp','203.0.113.15')
    assert len(connection.requests)==1 and connection.closed
    method,path,headers=connection.requests[0]
    assert method=='GET' and url=='https://realestate.yahoo.co.jp'+path
    assert headers=={'User-Agent':public_fetch.USER_AGENT,'Accept':'text/html,text/plain;q=0.9','Accept-Encoding':'identity'}


@pytest.mark.parametrize('destination',[CHINTAI,'https://suumo.jp/robots.txt','http://127.0.0.1/'])
def test_yahoo_redirect_does_not_cross_provider_or_network_route(destination):
    connection=Connection(Response(302,headers={'Location':destination}))
    with patch.object(public_fetch,'_bounded_dns',return_value=['203.0.113.15']) as dns, \
            patch.object(public_fetch,'_PinnedHTTPSConnection',return_value=connection) as factory:
        with pytest.raises(PublicFetchError,match='redirect_denied'):
            public_fetch.fetch_public(YAHOO,deadline=time.monotonic()+2)
    assert dns.call_count==factory.call_count==len(connection.requests)==1


def test_http_auth_and_source_selection_precede_import_handler(server_factory,monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES','yahoo_realestate')
    monkeypatch.setenv('HOUSE_EVALUATOR_SEARCH_SOURCE','yahoo_realestate')
    calls=[]
    def imported(payload,*,deadline):
        calls.append((payload,deadline))
        return {'status':'partial','listing':{'rent_yen':100000},'missing_fields':['built_year'],
                'source':{'id':'yahoo_realestate','url':YAHOO},'warnings':[]}
    token='synthetic-private-access-code-0000000000'
    instance=server_factory(access_token=token,import_fn=imported)
    assert request(instance,'POST','/api/v2/personal/import',{'url':YAHOO})[0]==401
    assert request(instance,'POST','/api/v2/personal/import',{'url':CHINTAI})[0]==401
    assert request(instance,'POST','/api/v2/personal/import',{'url':CHINTAI},token)[0]==403
    assert request(instance,'POST','/api/v2/personal/import',{'url':'https://127.0.0.1/'},token)[0]==400
    assert calls==[]
    start=time.monotonic()
    status,result=request(instance,'POST','/api/v2/personal/import',{'url':YAHOO},token)
    assert status==200 and result['source']['id']=='yahoo_realestate' and len(calls)==1
    assert start<calls[0][1]<=time.monotonic()+9.5
    options=request(instance,'GET','/api/v2/personal/options',token=token)[1]
    assert options['import_sources']==[{'id':'yahoo_realestate','name':'Yahoo! 부동산'}]
    assert options['import_enabled'] is True


@pytest.mark.parametrize('flags',[{'personal_enabled':False},{'public_search_enabled':False}])
def test_personal_or_public_read_stop_switch_prevents_all_import_work(server_factory,monkeypatch,flags):
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES','yahoo_realestate')
    handler=Mock(side_effect=AssertionError('stop switch must precede imports'))
    instance=server_factory(import_fn=handler,**flags)
    assert request(instance,'POST','/api/v2/personal/import',{'url':YAHOO})[0]==403
    assert request(instance,'GET','/api/v2/personal/options')[1]['import_enabled'] is False
    handler.assert_not_called()


def test_empty_provider_selection_is_disabled_in_options_and_post(server_factory,monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES','')
    handler=Mock(side_effect=AssertionError('no provider enabled'))
    instance=server_factory(import_fn=handler)
    options=request(instance,'GET','/api/v2/personal/options')[1]
    assert options['import_sources']==[] and options['import_enabled'] is False
    assert request(instance,'POST','/api/v2/personal/import',{'url':YAHOO})[0]==403
    handler.assert_not_called()


@pytest.mark.parametrize('value',['yahoo_realestate,yahoo_realestate','unknown-secret-value','yahoo_realestate,','x'*257])
def test_invalid_provider_configuration_fails_before_socket_without_echoing_value(monkeypatch,value):
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES',value)
    with patch.object(server_module,'BoundedServer',side_effect=AssertionError('must not open socket')) as factory:
        with pytest.raises(ValueError) as caught:
            server_module.create_server()
    assert value not in str(caught.value)
    factory.assert_not_called()


def test_disabled_source_never_consumes_fetch_or_shared_slot(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES','yahoo_realestate')
    slot=Mock(side_effect=AssertionError('disabled source must not acquire slot'))
    monkeypatch.setattr(listing_import,'_SEARCH_SLOT',slot)
    fetcher=Mock(side_effect=AssertionError('disabled source must not fetch'))
    with pytest.raises(listing_import.ListingImportError,match='source_disabled'):
        listing_import.import_listing({'url':CHINTAI},fetcher=fetcher)
    fetcher.assert_not_called()
    slot.acquire.assert_not_called()


def test_robots_and_detail_share_total_eight_point_five_second_deadline(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES','yahoo_realestate')
    slot=threading.BoundedSemaphore(1)
    monkeypatch.setattr(listing_import,'_SEARCH_SLOT',slot)
    clock=[100.0]
    monkeypatch.setattr(listing_import.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(listing_import.time,'sleep',lambda amount:clock.__setitem__(0,clock[0]+amount))
    parser=Mock(return_value={'rent_yen':100000,'source_id':'yahoo_realestate','source_url':YAHOO,'fetched_at':'2026-01-01T00:00:00Z'})
    monkeypatch.setattr(listing_import,'parse_yahoo_detail',parser)
    calls=[]
    def fetcher(url,*,deadline):
        calls.append((url,deadline))
        clock[0]+=1
        return PublicResponse(200,b'User-agent: *\nDisallow:\n' if url==ROBOTS else b'<html>synthetic</html>')
    result=listing_import.import_listing({'url':YAHOO},fetcher=fetcher)
    assert [url for url,_ in calls]==[ROBOTS,YAHOO]
    assert {deadline for _,deadline in calls}=={108.5}
    assert result['source']['id']=='yahoo_realestate' and parser.call_count==1
    assert slot.acquire(blocking=False)
    slot.release()


def test_parser_crossing_deadline_returns_timeout_and_releases_slot(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES','yahoo_realestate')
    clock=[100.0]
    monkeypatch.setattr(listing_import.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(listing_import.time,'sleep',lambda _:None)
    slot=threading.BoundedSemaphore(1)
    monkeypatch.setattr(listing_import,'_SEARCH_SLOT',slot)
    def parse(*args,**kwargs):
        clock[0]=108.6
        return {'rent_yen':100000}
    monkeypatch.setattr(listing_import,'parse_yahoo_detail',parse)
    fetcher=Mock(side_effect=[PublicResponse(200,b'User-agent: *\nDisallow:\n'),PublicResponse(200,b'<html>synthetic</html>')])
    with pytest.raises(listing_import.ListingImportError,match='import_timeout'):
        listing_import.import_listing({'url':YAHOO},fetcher=fetcher)
    assert fetcher.call_count==2 and slot.acquire(blocking=False)
    slot.release()


def test_blocked_import_never_tries_another_provider(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES','chintai,yahoo_realestate')
    slot=threading.BoundedSemaphore(1)
    monkeypatch.setattr(listing_import,'_SEARCH_SLOT',slot)
    fetcher=Mock(return_value=PublicResponse(403))
    with pytest.raises(listing_import.ListingImportError,match='source_blocked'):
        listing_import.import_listing({'url':YAHOO},fetcher=fetcher)
    assert fetcher.call_count==1 and fetcher.call_args.args[0]==ROBOTS
    assert slot.acquire(blocking=False)
    slot.release()
