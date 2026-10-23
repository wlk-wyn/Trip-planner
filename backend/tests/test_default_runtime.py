"""A clean default server must not need the incompatible legacy SDK."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class DefaultRuntimeTests(unittest.TestCase):
    def test_startup_without_hello_agents(self):
        source = Path(__file__).resolve().parents[1] / 'app'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(source, root / 'app', ignore=shutil.ignore_patterns('__pycache__'))
            script = r'''
import importlib.abc
import sys
sys.path.insert(0, sys.argv[1])
class NoLegacySDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'hello_agents' or fullname.startswith('hello_agents.'):
            raise ModuleNotFoundError('Legacy SDK intentionally absent', name=fullname)
sys.meta_path.insert(0, NoLegacySDK())
from app.api.main import app
from app.services import amap_service, llm_service
from app.agents.langgraph_planner import build_chat_model
from fastapi.testclient import TestClient
model = build_chat_model('synthetic-model')
assert model.model_name == 'synthetic-model'
with TestClient(app) as client:
    assert client.get('/health').status_code == 200
    response = client.get('/ready')
    assert response.status_code == 200, response.text
from app.agents.trip_planner_agent import MultiAgentTripPlanner
try:
    MultiAgentTripPlanner()
except RuntimeError as error:
    assert 'PLANNER_ENGINE=agent' in str(error)
else:
    raise AssertionError('Missing legacy dependency must be explained')
assert not any(name == 'hello_agents' or name.startswith('hello_agents.') for name in sys.modules)
'''
            env = {name: value for name, value in os.environ.items()
                   if name.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'HOME'}}
            env.update(AMAP_API_KEY='synthetic', LLM_API_KEY='synthetic',
                       LLM_BASE_URL='https://example.invalid/v1', PLANNER_ENGINE='agent',
                       PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', script, str(root)],
                                    cwd=root, env=env, capture_output=True, text=True,
                                    encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
