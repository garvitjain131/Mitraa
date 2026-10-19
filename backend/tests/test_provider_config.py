import pytest
from dotenv import dotenv_values
from mitraa.assistant import provider_config as provider
from mitraa.config import get_settings


@pytest.fixture
def location(monkeypatch,tmp_path):
    import mitraa.config as config
    monkeypatch.setattr(config,'BACKEND_DIR',tmp_path)
    for key in ('NEBIUS_API_KEY','NEBIUS_BASE_URL','NEMOTRON_MODEL'): monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(provider,'status',lambda:{'configured':True})
    return tmp_path


def test_atomic_provider_save_preserves_unrelated_settings_and_blank_key(location):
    path=location/'.env'
    path.write_text('# personal settings\nMITRAA_HOTKEY=f12\nNEBIUS_API_KEY=private-fixture\n',encoding='utf-8')
    provider.save('https://provider.example/v1/','chosen-model')
    values=dotenv_values(path)
    assert values['MITRAA_HOTKEY']=='f12' and values['NEBIUS_API_KEY']=='private-fixture'
    assert values['NEMOTRON_MODEL']=='chosen-model'
    assert '# personal settings' in path.read_text()
    assert list(location.glob('.mitraa-provider-*'))==[]


@pytest.mark.parametrize('url',['http://external.example/v1/','https://user:secret@example.com/',
    'file:///secret','https://example.com/?api_key=secret','https://example.com:bad/'])
def test_invalid_endpoint_does_not_write(location,url):
    with pytest.raises(ValueError): provider.save(url,'model','fixture')
    assert not (location/'.env').exists()


def test_missing_key_keeps_local_mode_available_without_fake_configuration(location):
    with pytest.raises(ValueError,match='API key'): provider.save('https://example.com/v1/','model')
    assert not (location/'.env').exists()


def test_replacement_failure_keeps_original_configuration(monkeypatch,location):
    path=location/'.env'; path.write_text('MITRAA_HOTKEY=f12\n')
    def fail(*args): raise OSError('fixture replace failure')
    monkeypatch.setattr(provider.os,'replace',fail)
    with pytest.raises(OSError): provider.save('https://example.com/v1/','model','fixture')
    assert path.read_text()=='MITRAA_HOTKEY=f12\n'
    assert list(location.glob('.mitraa-provider-*'))==[]
