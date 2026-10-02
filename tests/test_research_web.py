import pytest
import web_server
from research.store import ResearchStore
from research.service import ResearchService


@pytest.fixture
def client(monkeypatch,tmp_path):
    service=ResearchService(ResearchStore(tmp_path/'research'))
    monkeypatch.setattr(web_server,'_research_service',lambda:service)
    return web_server.app.test_client(),service


def headers():
    return {'X-Quant-Session':web_server.WEB_SESSION_TOKEN}


def test_research_reads_and_writes_require_session_and_trusted_origin(client):
    client,service=client
    assert client.get('/api/research/formulas').status_code==403
    assert client.post('/api/research/formulas',json={'source':'C>0','name':'test'}).status_code==403
    assert client.get('/api/research/formulas',headers={**headers(),'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/research/formulas',headers=headers()).status_code==200


def test_formula_version_reads_archive_and_bounds(client):
    client,service=client
    one=client.post('/api/research/formulas',headers=headers(),json={'name':'test','source':'C>0'}).get_json()['data']
    two=client.post('/api/research/formulas/'+one['id'],headers=headers(),json={'name':'test','source':'C>1'}).get_json()['data']
    assert two['revision']==2
    old=client.get('/api/research/formulas/'+one['id']+'?revision=1',headers=headers()).get_json()['data']
    assert old['source']=='C>0'
    assert client.get('/api/research/runs?limit=1000000',headers=headers()).status_code==400
    assert client.get('/api/research/formulas/..',headers=headers()).status_code==400
    assert client.post('/api/research/formulas',headers=headers(),json={'name':'test','source':'__import__("os")'}).status_code==400
    assert client.post('/api/research/formulas/'+one['id']+'/archive',headers=headers(),json={}).status_code==200
    assert service.store.get('formula',one['id'])['archived']


def test_research_job_shares_existing_admission_and_has_bounded_inputs(client):
    client,service=client
    job=web_server._create_update_job('tushare')
    try:
        response=client.post('/api/research/capture',headers=headers(),json={})
        assert response.status_code==409
    finally:
        with web_server.update_jobs_lock:
            web_server.update_jobs.pop(job,None)
            web_server.update_cancel_events.pop(job,None)
    assert client.post('/api/research/capture',headers=headers(),json={'symbols':['000001']*101}).status_code==400
    assert client.get('/api/research/chart?snapshot_id=x&symbol=000001&indicators=bogus',headers=headers()).status_code==400
    assert client.post('/api/update/start',headers=headers(),json={'batch_daily':'true'}).status_code==400


def test_research_html_contains_accessible_controls_and_lifecycle(client):
    client,service=client
    html=client.get('/').get_data(as_text=True)
    assert 'research-page' in html and 'research-session' in html and 'research-account' in html
    script=client.get('/static/js/research_workspace.js').get_data(as_text=True)
    assert "this.controller?.abort()" in script and "this.priceChart?.dispose()" in script
    assert "X-Quant-Session" in script and 'chartEpoch' in script


def test_research_background_job_executes_and_releases_admission(client,monkeypatch):
    client,service=client
    from threading import Thread
    threads=[]
    def make_thread(*,target,args,name):
        thread=Thread(target=target,args=args,name=name)
        threads.append(thread)
        return thread
    monkeypatch.setattr(web_server,'_job_thread',make_thread)
    with web_server.app.test_request_context('/api/research/runs',method='POST',headers=headers()):
        response,status=web_server._start_research_job('offline job',lambda cancel,progress:{'id':'fixture','status':'completed'})
    assert status==202
    job_id=response.get_json()['job_id']
    try:
        threads[0].join(timeout=3)
        assert not threads[0].is_alive()
        job=client.get('/api/select/status/'+job_id).get_json()['data']
        assert job['status']=='completed' and job['research_result']['id']=='fixture'
    finally:
        with web_server.selection_jobs_lock:
            web_server.selection_jobs.pop(job_id,None)
            web_server.selection_cancel_events.pop(job_id,None)
