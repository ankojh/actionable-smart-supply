"""Acceptance must distinguish real native execution from incomplete traces."""
import copy
import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.northstar_agent import AGENT_NAMES, TOOLS, agent_spec

spec = importlib.util.spec_from_file_location('northstar_verification', Path(__file__).parents[2] / 'snowflake/verify/northstar.py')
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


class Source:
    prefix = 'TEST_DB.NORTHSTAR_RECOVERY_V2'
    settings = SimpleNamespace(cortex_model='claude-sonnet-4-6', app_role='APP')

    def __init__(self):
        self.query_ids = []
        self.audit = {}
        self.specs = {name: agent_spec(self.settings.cortex_model, kind) for kind, name in AGENT_NAMES.items()}
        self.privileges = ['USAGE', 'MONITOR']

    @contextmanager
    def session(self):
        yield

    def sql(self, sql, params=None):
        self.query_ids.append(str(len(self.query_ids) + 1))
        if sql.startswith('DESCRIBE AGENT'):
            return [{'agent_spec': self.specs[sql.rsplit('.', 1)[1]]}]
        if sql.startswith('SHOW GRANTS'):
            return [{'grantee_name': 'APP', 'privilege': p} for p in self.privileges]
        return [{'PAYLOAD': {'execution': self.audit[params[2]]}}] if params[2] in self.audit else []


def execution(source):
    items = [dict(kind=kind, agent=source.prefix + '.' + name, status='COMPLETED',
                  snapshot_id='snapshot', thread_id=str(i + 1),
                  tools=[dict(name=t, snapshot_id='snapshot') for t in TOOLS[kind]],
                  turns=[dict(thread_id=str(i + 1), assistant_message_id='1')])
             for i, (kind, name) in enumerate(AGENT_NAMES.items())]
    items[0]['children'] = items[1:]
    source.audit = {item['thread_id']: copy.deepcopy(item) for item in items}
    return items[0]


def test_verification_checks_cloud_specs_and_application_grants():
    source = Source()
    assert set(verification.verify_agents(source)) == set(AGENT_NAMES)
    source.specs[AGENT_NAMES['risk_detection']]['tools'] = []
    with pytest.raises(ValueError, match='specification differs'):
        verification.verify_agents(source)
    source = Source()
    source.privileges = ['MONITOR']
    with pytest.raises(ValueError, match='grants are incomplete'):
        verification.verify_agents(source)


@pytest.mark.parametrize('corruption', ['missing_audit', 'wrong_thread', 'duplicate_tools', 'other_snapshot', 'missing_child'])
def test_verification_rejects_incomplete_or_unbound_native_evidence(corruption):
    source = Source()
    root = execution(source)
    assert set(verification.verify_execution(source, root, 'snapshot')) == set(AGENT_NAMES)
    if corruption == 'missing_audit':
        source.audit.clear()
    elif corruption == 'wrong_thread':
        root['children'][0]['turns'][0]['thread_id'] = root['thread_id']
    elif corruption == 'duplicate_tools':
        root['tools'].append(copy.deepcopy(root['tools'][0]))
    elif corruption == 'other_snapshot':
        root['children'][0]['tools'][0]['snapshot_id'] = 'future'
    else:
        root['children'].pop()
    with pytest.raises(ValueError):
        verification.verify_execution(source, root, 'snapshot')
