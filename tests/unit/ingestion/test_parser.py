import pytest

from controller.ingestion.events import EventValidationError
from controller.ingestion.parser import iter_ndjson, parse_descriptors


def _descriptor(*, chunk=0, kind="history", count=2, first=11, last=14):
    return {
        "chunk": chunk,
        "kind": kind,
        "events": count,
        "first_event_id": first,
        "last_event_id": last,
        "until_minute": 0,
    }


def test_descriptor_listing_is_ordered_without_requiring_consecutive_numbers():
    parsed = parse_descriptors(
        [_descriptor(chunk=4), _descriptor(chunk=9, kind="events")], after=2
    )
    assert [item.chunk for item in parsed] == [4, 9]


@pytest.mark.parametrize(
    "value",
    [
        {},
        _descriptor(chunk=True),
        _descriptor(kind="unknown"),
        _descriptor(count=0),
        _descriptor(first=20, last=10),
    ],
)
def test_invalid_descriptors_are_rejected(value):
    with pytest.raises(EventValidationError):
        parse_descriptors([value])


def test_ndjson_is_yielded_incrementally_and_id_gaps_are_allowed():
    descriptor = parse_descriptors([_descriptor()])[0]
    lines = iter(
        [
            '{"event_id":11,"type":"lead","features":{"opaque":true}}',
            '{"event_id":14,"type":"call"}',
        ]
    )
    events = iter_ndjson(lines, descriptor)
    first = next(events)
    assert first.event_id == 11
    assert next(events).event_id == 14
    with pytest.raises(StopIteration):
        next(events)


@pytest.mark.parametrize(
    "lines",
    [
        ["not json"],
        ["[]"],
        ['{"event_id":1}'],
        ['{"event_id":1,"type":"lead"}', '{"event_id":1,"type":"lead"}'],
        ['{"event_id":2,"type":"lead"}', '{"event_id":1,"type":"lead"}'],
    ],
)
def test_malformed_or_out_of_order_ndjson_fails(lines):
    descriptor = parse_descriptors(
        [_descriptor(count=len(lines), first=1, last=len(lines))]
    )[0]
    with pytest.raises(EventValidationError):
        list(iter_ndjson(lines, descriptor))


def test_descriptor_count_and_event_id_bounds_must_match():
    descriptor = parse_descriptors([_descriptor()])[0]
    with pytest.raises(EventValidationError, match="declared"):
        list(iter_ndjson(['{"event_id":11,"type":"lead"}'], descriptor))
