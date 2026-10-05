"""Normalize legacy hour formatting before validation and overlap checks."""
from fastapi import HTTPException
from publication import publication_time
from publication_schema import PublishedLesson

for raw, expected in [('8:00','08:00'), ('8:50','08:50'), ('09:00','09:00'),
                       (' 9:50 ','09:50'), ('10:00','10:00'), ('08:00:00','08:00')]:
    assert publication_time(raw, 1) == expected
assert publication_time('9:50',1) < publication_time('10:00',2)
PublishedLesson(day_of_week=1, time=f"{publication_time('8:00',1)}-{publication_time('8:50',1)}",
                subject='Предмет',room='101')
for value in ['24:00','8:60','-1:00','8:5','08:00:30','abc','']:
    try:
        publication_time(value,3)
    except HTTPException as error:
        assert error.status_code == 409 and '№3' in error.detail
    else:
        raise AssertionError(value)
print('OK: time padding, bounds, readable errors, chronological comparison')
