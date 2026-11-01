"""Observe ordinary homework submissions; never intercept or inspect verification traffic."""
from dataclasses import dataclass, field
import json
from urllib.parse import urlparse

from .config import allowed_url

PREFIXES = ('/mooc-api/v1/lms/exercise/', '/c27/online_courseware/exercise/')
SINGLE_PATHS = {
    '/mooc-api/v1/lms/exercise/problem_apply/',
    '/mooc-api/v1/lms/exercise/problem_group_apply/',
    '/mooc-api/v1/lms/exercise/learning_plan/problem_submit/',
}
BATCH_PATH = '/mooc-api/v1/lms/exercise/problem_batch_apply/'
REFUSED_STATUSES = {400, 401, 403, 412, 422, 429}


@dataclass
class SubmissionProbe:
    page: object
    question_id: str
    classroom: str
    trusted_identity: bool = True
    records: dict = field(default_factory=dict)
    unknown_traffic: bool = False

    @property
    def outcome(self):
        states = [record['state'] for record in self.records.values()]
        if 'accepted' in states:
            return 'accepted'
        if 'pending' in states:
            return 'pending'
        if self.unknown_traffic or 'uncertain' in states:
            return 'uncertain'
        if states and all(state == 'rejected' for state in states):
            return 'rejected'
        return 'not-sent'


class SubmissionMonitor:
    def __init__(self, context):
        self.context = context
        self.probes = []
        self.listeners = {'request': self._request, 'requestfinished': self._finished,
                          'requestfailed': self._failed}
        for event, callback in self.listeners.items():
            context.on(event, callback)

    def begin(self, page, question_id, classroom, trusted_identity=True):
        probe = SubmissionProbe(page, str(question_id), str(classroom), trusted_identity)
        self.probes.append(probe)
        return probe

    def _request(self, request):
        if request.method not in {'POST', 'PUT', 'PATCH'} or not allowed_url(request.url):
            return
        path = urlparse(request.url).path
        if not path.startswith(PREFIXES):
            return
        try:
            page = request.frame.page
        except Exception:
            page = None
        candidates = [probe for probe in self.probes if page is None or probe.page == page]
        if not candidates:
            return
        # Read only the normal assignment API's question/classroom identifiers.
        # Answers, headers, cookies, and all verification endpoints are ignored.
        try:
            data = request.post_data_json
            if not isinstance(data, dict):
                raise ValueError
            classroom = str(data.get('classroom_id', ''))
            ids = {str(data['problem_id'])} if data.get('problem_id') is not None else set()
            if isinstance(data.get('problem_ids'), list):
                ids.update(map(str, data['problem_ids']))
            if isinstance(data.get('problems'), list):
                ids.update(str(p['problem_id']) for p in data['problems'] if isinstance(p, dict) and 'problem_id' in p)
            if isinstance(data.get('answer_dict'), dict):
                ids.update(map(str, data['answer_dict'].keys()))
            elif isinstance(data.get('answer_dict'), str):
                answers = json.loads(data['answer_dict'])
                if isinstance(answers, dict):
                    ids.update(map(str, answers.keys()))
        except Exception:
            ids, classroom = set(), ''
        for probe in candidates:
            if classroom and probe.classroom and classroom != probe.classroom:
                continue
            if ids and probe.question_id not in ids:
                if not probe.trusted_identity or not probe.records:
                    probe.unknown_traffic = True
                continue
            if not ids or path not in SINGLE_PATHS | {BATCH_PATH}:
                probe.unknown_traffic = True
                continue
            probe.records[request] = {'state': 'pending', 'batch': path == BATCH_PATH}

    def _finished(self, request):
        matched = [(probe, probe.records[request]) for probe in self.probes if request in probe.records]
        if not matched:
            return
        try:
            response = request.response()
            refused = response.status in REFUSED_STATUSES
            data = response.json() if 200 <= response.status < 300 else {}
            normal = 200 <= response.status < 300 and isinstance(data, dict)
        except Exception:
            normal, data, refused = False, {}, False
        for probe, record in matched:
            state = 'uncertain'
            if refused:
                state = 'rejected'
            elif normal and record['batch']:
                result = data.get('data', {})
                if isinstance(result, dict) and isinstance(result.get('data'), dict):
                    result = result['data']
                item = result.get(probe.question_id) if isinstance(result, dict) else None
                if isinstance(item, list) and len(item) >= 2:
                    try:
                        state = 'accepted' if float(item[1]) == 0 else 'rejected'
                    except (ValueError, TypeError):
                        state = 'uncertain'
                elif data.get('success') is False:
                    state = 'rejected'
            elif normal and data.get('success') is False:
                state = 'rejected'
            elif normal and data.get('success') is True:
                state = 'accepted'
            record['state'] = state

    def _failed(self, request):
        for probe in self.probes:
            if request in probe.records:
                probe.records[request]['state'] = 'uncertain'

    def close(self):
        for event, callback in self.listeners.items():
            self.context.remove_listener(event, callback)
