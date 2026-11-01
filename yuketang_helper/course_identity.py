"""Identify a selected course by portal/classroom before using its display name."""
import re
from urllib.parse import parse_qs, urlparse

from .config import allowed_url


def course_identity(url):
    if not allowed_url(url):return None
    parsed=urlparse(url)
    match=re.search(r'/(?:lms-graph|studentLog)/(\d+)(?:/|$)',parsed.path)
    classroom=match[1] if match else parse_qs(parsed.query).get('classroom_id',[''])[0]
    return (parsed.netloc.lower(),classroom) if classroom else None


def normalized_name(name):
    return re.sub(r'\s+','',name or '').casefold()


def matches_course(config, url, name):
    if not allowed_url(url):return False
    expected=course_identity(config.course_url)
    if expected:return course_identity(url)==expected
    return bool(normalized_name(name) and normalized_name(name)==normalized_name(config.course_name))
