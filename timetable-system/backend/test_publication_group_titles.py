"""Same group codes on different courses stay separate when publishing."""
from types import SimpleNamespace
from uuid import uuid4
from fastapi import HTTPException
from publication import published_group_titles
from publication_schema import PublishedGroup


def group(id, code, course):
    return SimpleNamespace(id=id, code=code, course=course)


groups = [group(1, 'ДЗ-252', 1), group(2, ' ДЗ-252 ', 2), group(3, 'ИС-21', 2)]
titles = published_group_titles(groups)
assert titles == {1: 'ДЗ-252 · 1 курс', 2: 'ДЗ-252 · 2 курс', 3: 'ИС-21'}
for title in titles.values():
    PublishedGroup(source_id=uuid4(), title=title, lessons=[])
assert groups[0].code == 'ДЗ-252' and groups[1].code == ' ДЗ-252 '
for ambiguous in (
    [group(1, 'A', 1), group(2, 'A', 1)],
    [group(1, 'A', 1), group(2, 'A', 2), group(3, 'A · 1 курс', 3)],
):
    try:
        published_group_titles(ambiguous)
    except HTTPException as error:
        assert error.status_code == 409 and 'Уточните названия' in error.detail
    else:
        raise AssertionError('Ambiguous titles must be rejected without merging groups')
print('OK: duplicate codes across courses, whitespace, unchanged originals, ambiguous names')
