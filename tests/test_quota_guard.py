import json
from datetime import datetime, timezone, timedelta
from freezegun import freeze_time
from quota_guard import QuotaConfig, UsageCounter, pacific_date_str


def make_counter(tmp_path, **kw):
    cfg = QuotaConfig(state_path=str(tmp_path / "state.json"), **kw)
    return UsageCounter(cfg)


def test_counts_accumulate(tmp_path):
    c = make_counter(tmp_path)
    c.add_reads(5)
    c.add_reads(3)
    c.add_writes(2)
    snap = c.snapshot()
    assert snap["reads"] == 8
    assert snap["writes"] == 2


def test_mode_normal_then_read_only_then_maintenance(tmp_path):
    c = make_counter(tmp_path, read_limit=100, write_limit=100,
                     read_only_write_ratio=0.8, maintenance_read_ratio=0.9)
    assert c.mode() == "normal"
    c.add_writes(80)            # hits 80% of writes
    assert c.mode() == "read_only"
    c.add_reads(90)             # hits 90% of reads -> maintenance wins
    assert c.mode() == "maintenance"


def test_counts_reset_on_new_pacific_day(tmp_path):
    c = make_counter(tmp_path)
    with freeze_time("2026-06-20 12:00:00", tz_offset=0):
        c.add_reads(10)
        assert c.snapshot()["reads"] == 10
    with freeze_time("2026-06-21 12:00:00", tz_offset=0):
        # New day -> counter auto-resets on next access.
        assert c.snapshot()["reads"] == 0


def test_state_persists_across_instances(tmp_path):
    c1 = make_counter(tmp_path)
    c1.add_writes(4)
    c2 = make_counter(tmp_path)
    assert c2.snapshot()["writes"] == 4


def test_pacific_date_str_format():
    # Implementation uses a fixed -8h (PST) offset (no DST), documented as a
    # soft-safeguard simplification. 08:00 UTC == 00:00 PST on 2026-06-20.
    s = pacific_date_str(datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc))
    assert s == "2026-06-20"
    # 07:59 UTC is still the previous Pacific day under the fixed offset.
    earlier = pacific_date_str(datetime(2026, 6, 20, 7, 59, tzinfo=timezone.utc))
    assert earlier == "2026-06-19"
