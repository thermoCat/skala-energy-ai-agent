"""데이터 계층 테스트 — 2026-10-19 실데이터 스냅샷"""
from energy import indicators
from energy.store import load_snapshot

SNAPSHOT = load_snapshot("2026-10-19")


def _station(station_id: str):
    st = SNAPSHOT.stations
    return st[st["UNI_ID"] == station_id].iloc[0]


def test_fast_charger_basis_is_seoul_gu_assigned_6045():
    ch = SNAPSHOT.chargers
    assert int(ch["fast"].sum()) == 6045
    assert ch["gu"].nunique() == 25 and ch["gu"].notna().all()


def test_charger_counts_match_supply_table_per_gu():
    ch, balance = SNAPSHOT.chargers, SNAPSHOT.balance
    fast = ch[ch["fast"]].groupby("gu").size()
    slow = ch[~ch["fast"]].groupby("gu").size()
    assert (fast == balance["급속충전기ID"]).all()
    assert (slow == balance["완속충전기ID"]).all()


def test_busy_pct_matches_supply_table_rounded_to_integer():
    # 수급표는 충전 중 비율을 정수로 반올림해 저장했다
    ch = SNAPSHOT.chargers
    for gu, row in SNAPSHOT.balance.iterrows():
        pct = indicators.busy_pct(ch[(ch["gu"] == gu) & ch["fast"]])
        assert round(pct) == row["급속_충전중비율%"], gu


def test_abnormal_status_on_same_basis():
    status = indicators.fast_status(SNAPSHOT)
    assert status["fast_ids"] == 6045
    assert status["abnormal_ids"] == 535
    assert status["abnormal_pct"] == 8.9


def test_session_minutes_on_same_basis():
    all_fast = indicators.session_minutes(SNAPSHOT)
    assert len(all_fast) == 5203
    assert round(all_fast.median(), 1) == 35.1
    assert round(indicators.session_minutes(SNAPSHOT, min_kw=100).median(), 1) == 30.5
    assert round(indicators.session_minutes(SNAPSHOT, max_kw=100).median(), 1) == 46.0


def test_gu_assignment_column_renamed():
    columns = SNAPSHOT.stations.columns
    assert "자치구_판정" in columns and "판정" not in columns
    assert len(SNAPSHOT.stations) == 470


def test_fast_1km_counts_border_chargers_for_boundary_station():
    # 금천구 박미주유소: 서울 급속 20 + 경계 너머 경기 급속 25
    assert _station("A0001146")["fast_1km"] == 45
    assert _station("A0009172")["fast_1km"] == 10   # 동대문구 풍한주유소 (경계에서 멂)


def test_border_chargers_do_not_change_gu_totals():
    supply = indicators.ev_supply(SNAPSHOT, "금천구")
    assert supply["fast_ids"] == SNAPSHOT.balance.loc["금천구", "급속충전기ID"]


def test_dongdaemun_sk_cluster_is_six_stations_and_two_lpg():
    st = SNAPSHOT.stations
    sk = st[(st["자치구"] == "동대문구") & (st["brand"] == "SK에너지")]
    assert (sk["prodcd"] == "B027").sum() == 6
    assert (sk["prodcd"] == "K015").sum() == 2


def test_station_card_has_nearby_fast_fields():
    card = indicators.station_card(SNAPSHOT, "A0009172")
    assert card["fast_1km"] == 10
    assert card["fast_1km_busy_pct"] == 22.2
    assert card["gu_assignment"] == "확정"
