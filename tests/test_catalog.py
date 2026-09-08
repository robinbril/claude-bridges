import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'delegate'))
from run_task import check_model, child_environment, privacy_check


@contextlib.contextmanager
def catalog(payload, redirect=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.seen.append((self.path, self.headers.get('Authorization')))
            if redirect:
                self.send_response(302)
                self.send_header('Location', redirect)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class CatalogTests(unittest.TestCase):
    def test_known_model_uses_authenticated_get(self):
        with catalog({'data': [{'id': 'chosen'}]}) as server:
            config = {'bridge_url': f'http://127.0.0.1:{server.server_port}', 'model': 'chosen'}
            check_model(config, {'MODEL_ROUTER_KEY': 'fixture'})
            self.assertEqual(server.seen, [('/v1/models', 'Bearer fixture')])

    def test_unknown_model_stops_instead_of_using_alias_fallback(self):
        with catalog({'data': [{'id': 'other'}]}) as server:
            config = {'bridge_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'chosen'}
            with self.assertRaisesRegex(ValueError, 'ontbreekt'):
                check_model(config, {'MODEL_ROUTER_KEY': 'fixture'})

    def test_invalid_catalog_is_rejected(self):
        with catalog({'data': 'not a model list'}) as server:
            config = {'bridge_url': f'http://127.0.0.1:{server.server_port}', 'model': 'chosen'}
            with self.assertRaises(ValueError):
                check_model(config, {'MODEL_ROUTER_KEY': 'fixture'})

    def test_internal_bridge_models_are_pinned_to_selected_model(self):
        config = {'bridge_url': 'http://127.0.0.1:8317', 'model': 'chosen', 'config_dir': '/fixture'}
        child = child_environment(config, {'MODEL_ROUTER_KEY': 'fixture', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'other'})
        self.assertEqual(child['ANTHROPIC_DEFAULT_HAIKU_MODEL'], 'chosen')
        self.assertEqual(child['ANTHROPIC_SMALL_FAST_MODEL'], 'chosen')

    def test_catalog_redirect_never_forwards_router_credentials(self):
        with catalog({'data': [{'id': 'chosen'}]}) as destination:
            with catalog({}, redirect=f'http://127.0.0.1:{destination.server_port}/other') as source:
                config = {'bridge_url': f'http://127.0.0.1:{source.server_port}', 'model': 'chosen'}
                with self.assertRaises(ValueError):
                    check_model(config, {'MODEL_ROUTER_KEY': 'fixture'})
                self.assertEqual(destination.seen, [])

    def test_sensitive_injected_context_is_also_blocked(self):
        for rail in ('codex', 'grok', 'openrouter'):
            with self.subTest(rail=rail), self.assertRaisesRegex(ValueError, 'Gevoelige'):
                privacy_check('Safe task\n<context>BSN fixture</context>', {'rail': rail}, {'SENSITIVE_RE': 'BSN'})

    def test_explicit_sensitivity_flag_is_enforced_without_regex(self):
        with self.assertRaises(ValueError):
            privacy_check('task', {'rail': 'grok'}, {'DELEGATE_SENSITIVE': '1'})
        privacy_check('task', {'rail': 'native'}, {'DELEGATE_SENSITIVE': '1'})


if __name__ == '__main__':
    unittest.main()
