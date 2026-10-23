import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import network, provider, provider_stream


@pytest.fixture
def proxy_settings(monkeypatch):
    settings = {
        'environment': {},
        'windows': {'http': 'http://windows.example:8080', 'https': 'http://windows.example:8080'},
        'windows_bypass': False,
    }
    monkeypatch.setattr(network.urllib.request, 'getproxies_environment', lambda: settings['environment'])
    monkeypatch.setattr(network.urllib.request, 'getproxies_registry', lambda: settings['windows'], raising=False)
    monkeypatch.setattr(network.urllib.request, 'proxy_bypass_registry', lambda host: settings['windows_bypass'], raising=False)
    return settings


def test_no_proxy_for_localhost_does_not_hide_windows_proxy(proxy_settings):
    proxy_settings['environment'] = {'no': 'localhost,127.0.0.1,::1'}
    assert network.proxy_for('https://relay.example/v1') == 'http://windows.example:8080'


@pytest.mark.parametrize('bypass', ['*', 'relay.example', '.example', 'relay.example:443'])
def test_environment_bypass_takes_precedence(proxy_settings, bypass):
    proxy_settings['environment'] = {'https': 'http://environment.example:8080', 'no': bypass}
    assert network.proxy_for('https://relay.example:443/v1') is None


@pytest.mark.parametrize('variable', ['https', 'all'])
def test_explicit_environment_proxy_takes_precedence(proxy_settings, variable):
    proxy_settings['windows_bypass'] = True
    proxy_settings['environment'] = {variable: 'http://environment.example:8080'}
    assert network.proxy_for('https://relay.example/v1') == 'http://environment.example:8080'


def test_scheme_specific_environment_proxy_precedes_all_proxy(proxy_settings):
    proxy_settings['environment'] = {'https': 'http://secure.example:8080', 'all': 'http://other.example:8080'}
    assert network.proxy_for('https://relay.example') == 'http://secure.example:8080'


def test_unrelated_environment_proxy_keeps_windows_https_proxy(proxy_settings):
    proxy_settings['environment'] = {'http': 'http://environment.example:8080'}
    assert network.proxy_for('https://relay.example') == 'http://windows.example:8080'


def test_windows_bypass_is_respected(proxy_settings):
    proxy_settings['windows_bypass'] = True
    assert network.proxy_for('https://relay.example') is None


def test_no_configured_proxy_uses_direct_connection(proxy_settings):
    proxy_settings['windows'] = {}
    assert network.proxy_for('https://relay.example') is None


def test_registry_is_optional_on_other_platforms(proxy_settings, monkeypatch):
    monkeypatch.delattr(network.urllib.request, 'getproxies_registry')
    assert network.proxy_for('https://relay.example') is None


def test_proxy_without_scheme_is_normalized(proxy_settings):
    proxy_settings['environment'] = {'https': '127.0.0.1:8080'}
    assert network.proxy_for('https://relay.example') == 'http://127.0.0.1:8080'


def test_proxy_changes_are_read_for_each_client(proxy_settings):
    assert network.proxy_for('https://relay.example') == 'http://windows.example:8080'
    proxy_settings['windows'] = {'https': 'http://changed.example:8080'}
    assert network.proxy_for('https://relay.example') == 'http://changed.example:8080'


@pytest.fixture
def local_proxy(proxy_settings):
    requests = []
    reply = {'status': 200}

    class ProxyHandler(BaseHTTPRequestHandler):
        def log_message(self, message_format, *args):
            pass

        def respond(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            requests.append({'method': self.command, 'target': self.path, 'headers': dict(self.headers), 'body': body})
            if self.command == 'CONNECT':
                self.send_response(407)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            payload = json.loads(body) if body else {}
            if payload.get('stream'):
                content = (
                    'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
                    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                    'data: [DONE]\n\n'
                ).encode()
                content_type = 'text/event-stream'
            else:
                content = json.dumps({'choices': [{'message': {'content': 'hello'}, 'finish_reason': 'stop'}]}).encode()
                content_type = 'application/json'
            self.send_response(reply['status'])
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            if reply['status'] == 302:
                self.send_header('Location', 'http://credential-sink.invalid/')
            self.end_headers()
            self.wfile.write(content)

        do_GET = respond
        do_POST = respond
        do_CONNECT = respond

    server = ThreadingHTTPServer(('127.0.0.1', 0), ProxyHandler)
    proxy_url = f'http://127.0.0.1:{server.server_port}'
    proxy_settings['environment'] = {'no': 'localhost,127.0.0.1'}
    proxy_settings['windows'] = {'http': proxy_url, 'https': proxy_url}
    server_thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    server_thread.start()
    try:
        yield requests, reply
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_actual_client_routes_get_through_system_proxy(local_proxy):
    requests, reply = local_proxy
    with provider.client({'base_url': 'http://relay.invalid', 'timeout_seconds': 15}) as connection:
        response = connection.get('http://relay.invalid/v1/models', headers={'Authorization': 'Bearer fake-network-key'})
        assert connection.timeout.connect == 12 and connection.timeout.read == 15
    assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0]['target'] == 'http://relay.invalid/v1/models'
    assert requests[0]['headers']['Authorization'] == 'Bearer fake-network-key'


@pytest.mark.parametrize('streaming', [False, True])
def test_generation_uses_same_proxy_and_preserves_payload(local_proxy, monkeypatch, streaming):
    requests, reply = local_proxy
    monkeypatch.setattr(provider, 'config', lambda: {**provider.DEFAULT, 'base_url': 'http://relay.invalid', 'model': 'fake-model'})
    monkeypatch.setattr(provider, 'get_key', lambda: 'fake-network-key')
    deltas = []
    result = provider_stream.generate('rules', 'question', on_delta=deltas.append) if streaming else provider.generate('rules', 'question')
    assert result['text'] == 'hello'
    assert len(requests) == 1
    assert requests[0]['method'] == 'POST'
    assert requests[0]['target'] == 'http://relay.invalid/v1/chat/completions'
    body = json.loads(requests[0]['body'])
    assert body['model'] == 'fake-model'
    assert body['messages'][1]['content'] == 'question'
    assert body['stream'] is streaming
    if streaming:
        assert deltas == ['hello']


def test_proxy_redirect_does_not_forward_credentials(local_proxy):
    requests, reply = local_proxy
    reply['status'] = 302
    with provider.client({'base_url': 'http://relay.invalid'}) as connection:
        response = connection.get('http://relay.invalid/v1/models', headers={'Authorization': 'Bearer fake-network-key'})
    assert response.status_code == 302
    assert len(requests) == 1


def test_https_connect_failure_is_not_replayed_or_sent_directly(local_proxy, monkeypatch):
    requests, reply = local_proxy
    monkeypatch.setattr(provider, 'config', lambda: {**provider.DEFAULT, 'base_url': 'https://relay.invalid', 'model': 'fake-model'})
    monkeypatch.setattr(provider, 'get_key', lambda: 'fake-network-key')
    with pytest.raises(provider.ProviderError) as error:
        provider.generate('rules', 'question')
    assert error.value.unknown is True
    assert 'fake-network-key' not in str(error.value)
    assert len(requests) == 1
    assert requests[0]['method'] == 'CONNECT'
    assert requests[0]['target'] == 'relay.invalid:443'
    assert 'Authorization' not in requests[0]['headers']
    assert requests[0]['body'] == b''
