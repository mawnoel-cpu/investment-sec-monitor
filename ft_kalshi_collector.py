"""Public Kalshi research context; GET only, no account, orders or scoring.

Official API: https://docs.kalshi.com/getting_started/quick_start_market_data
The YES bid/ask midpoint is a market-implied probability proxy, not a forecast
of stock returns. Correlated strikes from one event are one evidence family.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TORONTO = ZoneInfo('America/Toronto')
GAME_END = datetime(2026, 11, 27, 23, 59, 59, tzinfo=TORONTO)
BASE_URL = 'https://external-api.kalshi.com/trade-api/v2'
MAX_MARKETS = 20
MAX_PAGES = 3
MAX_REQUESTS = 90
MAX_SPREAD = .10
MIN_VOLUME = 100
MIN_INTEREST = 100
MIN_SIZE = 10
MAX_TRADE_AGE_HOURS = 24
MOVE_PP = 10
MAX_BASELINE_HOURS = 96
VERSION = '1.0'
# Verified public series; each quota prevents one event family filling the feed.
SERIES = [
    ('KXFEDDECISION', 'Fed decision', 4, 'Rates / growth-stock valuations and financing'),
    ('KXCPI', 'Inflation MoM', 4, 'Rates / consumer costs / valuation'),
    ('KXCPIYOY', 'Inflation YoY', 3, 'Rates / consumer costs; overlaps CPI MoM'),
    ('KXPCECORE', 'Core PCE', 2, 'Fed inflation / valuation / financing'),
    ('KXU3', 'Unemployment', 2, 'Growth / cyclicals / rates'),
    ('KXNEWTARIFFS', 'Tariffs', 2, 'Supply chains / margins; action is not tariff implementation'),
    ('KXEIACRUDEW', 'Crude inventories', 3, 'Oil producers / refiners; inventory direction is not stock-return direction'),
]
HEADERS = [
    'Captured at', 'Market ticker', 'Theme', 'Event ticker', 'Question', 'YES outcome',
    'YES midpoint', 'Change (pp)', 'Prior capture', 'YES bid', 'YES ask', 'Spread (pp)',
    'Contracts 24h', 'Open interest', 'YES bid size', 'YES ask size', 'Recent trade at',
    'Trade age hours', 'Closes at', 'Expected expiry', 'Data quality', 'Research flag',
    'Quality notes', 'Rules hash', 'Resolution rules', 'Exposure channel', 'Source URL',
    'Observation key', 'Market status', 'Series',
]
EPOCH = datetime(1899, 12, 30)


def parse_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def serial(value):
    if not value:
        return ''
    return (value.astimezone(TORONTO).replace(tzinfo=None) - EPOCH).total_seconds()/86400


def number(value):
    try:
        if isinstance(value, bool):
            return None
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError):
        return None


def field(market, modern, legacy=None, scale=1):
    # A malformed modern field must not silently fall back to an obsolete value.
    if modern in market:
        return number(market[modern])
    n = number(market.get(legacy)) if legacy else None
    return n / scale if n is not None else None


def quotes(market):
    bid = field(market, 'yes_bid_dollars', 'yes_bid', 100)
    ask = field(market, 'yes_ask_dollars', 'yes_ask', 100)
    if bid is None or ask is None or not 0 <= bid <= ask <= 1:
        return None, None, None
    return bid, ask, round((bid + ask)/2, 8)


def in_horizon(market, now):
    close = parse_time(market.get('close_time'))
    expiry = parse_time(market.get('expected_expiration_time'))
    return bool(market.get('status') in ('active', 'open')
                and market.get('market_type') == 'binary'
                and not market.get('mve_collection_ticker')
                and not market.get('mve_selected_legs')
                and close and now < close <= GAME_END
                and expiry and now < expiry <= GAME_END)


class KalshiClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.requests = 0

    def get(self, path, params=None):
        if path != '/markets' and path != '/markets/trades' and not re.fullmatch(r'/markets/[A-Z0-9.\-]+', path):
            raise ValueError('Only public market-data GET endpoints are allowed')
        for attempt in range(3):
            if self.requests >= MAX_REQUESTS:
                raise RuntimeError('Kalshi request budget exhausted')
            self.requests += 1
            try:
                response = self.session.get(BASE_URL + path, params=params, timeout=(5, 15),
                                            allow_redirects=False,
                                            headers={'User-Agent': 'FT-Game-Kalshi-Research/1.0'})
            except requests.RequestException:
                if attempt == 2:
                    raise RuntimeError('Kalshi network request failed') from None
                time.sleep(attempt + 1)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    time.sleep(min(5, number(response.headers.get('Retry-After')) or attempt + 1))
                    continue
            if response.status_code != 200:
                raise RuntimeError(f'Kalshi HTTP {response.status_code}')
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError('Kalshi response is not an object')
            return data
        raise RuntimeError('Kalshi retries exhausted')

    def markets(self, series):
        found, seen, cursor = {}, set(), ''
        for _ in range(MAX_PAGES):
            params = {'series_ticker': series, 'status': 'open', 'limit': 1000, 'mve_filter': 'exclude'}
            if cursor:
                params['cursor'] = cursor
            data = self.get('/markets', params)
            if not isinstance(data.get('markets'), list):
                raise ValueError('Missing market list')
            for m in data['markets']:
                ticker = m.get('ticker', '')
                if not ticker.startswith(series + '-'):
                    raise ValueError('Market does not match requested series')
                found[ticker] = m
            cursor = data.get('cursor', '')
            if not cursor:
                return list(found.values())
            if cursor in seen:
                raise ValueError('Repeated market cursor')
            seen.add(cursor)
        raise RuntimeError('Market pagination incomplete; series excluded')

    def recent_trade(self, ticker, now):
        data = self.get('/markets/trades', {'ticker': ticker, 'limit': 100,
            'min_ts': int((now - timedelta(hours=MAX_TRADE_AGE_HOURS)).timestamp()),
            'max_ts': int(now.timestamp()), 'is_block_trade': 'false'})
        if not isinstance(data.get('trades'), list):
            raise ValueError('Missing trade list')
        times = []
        for t in data['trades']:
            if t.get('ticker') != ticker or t.get('is_block_trade'):
                continue
            stamp = parse_time(t.get('created_time'))
            if stamp and 0 <= (now-stamp).total_seconds() <= MAX_TRADE_AGE_HOURS*3600:
                times.append(stamp)
        # Bounded evidence of recent trading; not a claim to exhaust trade history.
        return max(times) if times else None


def quote_reasons(m):
    bid, ask, midpoint = quotes(m)
    reasons = []
    if midpoint is None:
        reasons.append('Missing/invalid bid or ask')
    elif bid <= 0 or ask >= 1:
        reasons.append('One-sided/extreme quote')
    elif ask - bid > MAX_SPREAD + 1e-9:
        reasons.append('Spread >10pp')
    for modern, legacy, minimum, label in [
        ('volume_24h_fp', 'volume_24h', MIN_VOLUME, '24h volume <100 or unknown'),
        ('open_interest_fp', 'open_interest', MIN_INTEREST, 'Open interest <100 or unknown'),
        ('yes_bid_size_fp', None, MIN_SIZE, 'Bid depth <10 or unknown'),
        ('yes_ask_size_fp', None, MIN_SIZE, 'Ask depth <10 or unknown'),
    ]:
        n = field(m, modern, legacy)
        if n is None or n < minimum:
            reasons.append(label)
    if not m.get('rules_primary') or not m.get('yes_sub_title'):
        reasons.append('Missing resolution rules or YES outcome')
    if m.get('is_provisional'):
        reasons.append('Provisional market')
    if field(m, 'notional_value_dollars', 'notional_value', 100) != 1:
        reasons.append('Non-standard/unknown payout')
    return reasons


def select_markets(markets, now, quota, previous=()):
    candidates = [m for m in markets if in_horizon(m, now)]
    previous = set(previous)
    candidates.sort(key=lambda m: (bool(quote_reasons(m)), m['ticker'] not in previous,
                                    -(field(m, 'volume_24h_fp', 'volume_24h') or 0), m['ticker']))
    return candidates[:quota]


def contract_hash(m):
    keys = ['ticker', 'event_ticker', 'title', 'yes_sub_title', 'rules_primary', 'rules_secondary',
            'strike_type', 'floor_strike', 'cap_strike', 'custom_strike', 'close_time',
            'expected_expiration_time', 'notional_value_dollars']
    return hashlib.sha256(json.dumps({k:m.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()


def snapshot(m, theme, channel, series, now, trade, previous, api_error=''):
    reasons = quote_reasons(m)
    if not in_horizon(m, now):
        reasons.append('Closed or outside verified game horizon')
    if trade is None:
        reasons.append('No verified non-block trade within 24h')
    if api_error:
        reasons.append(api_error)
    quality = 'Eligible context' if not reasons else 'Limited'
    bid, ask, midpoint = quotes(m)
    fingerprint = contract_hash(m)
    baseline = None
    for row in previous:
        if row[1] == m['ticker'] and row[0] < serial(now.astimezone(TORONTO).replace(hour=0, minute=0, second=0, microsecond=0)):
            if baseline is None or row[0] > baseline[0]:
                baseline = row
    delta, prior, flag = '', '', 'BASELINE NEEDED'
    if baseline:
        prior = baseline[0]
        if baseline[23] != fingerprint:
            flag = 'RULES CHANGED'
        elif (serial(now) - prior)*24 > MAX_BASELINE_HOURS:
            flag = 'BASELINE STALE'
        elif quality == 'Eligible context' and baseline[20] == 'Eligible context' and isinstance(baseline[6], (float,int)):
            delta = round((midpoint - baseline[6])*100, 4)
            flag = 'RESEARCH MOVE' if abs(delta) >= MOVE_PP else 'NO LARGE MOVE'
        else:
            flag = 'QUALITY LIMITED'
    if quality != 'Eligible context':
        flag = 'QUALITY LIMITED'
    rules = str(m.get('rules_primary') or '') + '\n' + str(m.get('rules_secondary') or '')
    return [serial(now), m['ticker'], theme, m.get('event_ticker',''), m.get('title',''),
        m.get('yes_sub_title',''), midpoint if midpoint is not None else '', delta, prior,
        bid if bid is not None else '', ask if ask is not None else '',
        round((ask-bid)*100,4) if midpoint is not None else '',
        field(m,'volume_24h_fp','volume_24h'), field(m,'open_interest_fp','open_interest'),
        field(m,'yes_bid_size_fp'), field(m,'yes_ask_size_fp'), serial(trade),
        round((now-trade).total_seconds()/3600,3) if trade else '',
        serial(parse_time(m.get('close_time'))), serial(parse_time(m.get('expected_expiration_time'))),
        quality, flag, '; '.join(reasons) if reasons else 'Quality thresholds passed; price proxy, not calibrated probability',
        fingerprint, rules.strip(), channel, BASE_URL + '/markets/' + m['ticker'],
        f"KALSHI:{m['ticker']}:{now.astimezone(TORONTO).date().isoformat()}", m.get('status',''), series]


def history_rows(values):
    result, keys = [], set()
    for raw in values:
        if not any(v not in ('',None) for v in raw):
            continue
        row = (list(raw) + ['']*len(HEADERS))[:len(HEADERS)]
        if not isinstance(row[0], (int,float)) or not str(row[27]).startswith('KALSHI:') or row[27] in keys:
            raise ValueError('Invalid/duplicate history; refusing to overwrite')
        keys.add(row[27]); result.append(row)
    return result


def merge_history(previous, current):
    rows = {r[27]:r for r in history_rows(previous)}
    for r in current:
        rows[r[27]] = ['' if v is None else v for v in r]
    return sorted(rows.values(), key=lambda r:(r[0],r[1]))


def collect(client, now, previous=()):
    if now > GAME_END:
        return {'status':'Stopped','rows':[],'failures':[],'gaps':[],'requests':0,'series_checked':0}
    previous = history_rows(previous)
    rows, failures, gaps = [], [], []
    for series, theme, quota, channel in SERIES:
        try:
            markets = client.markets(series)
            chosen = select_markets(markets, now, quota, [r[1] for r in previous])
            if len(chosen) < quota:
                gaps.append(f'{series}: {len(chosen)}/{quota} in-horizon markets available')
            for m in chosen:
                trade, error = None, ''
                try:
                    trade = client.recent_trade(m['ticker'], now)
                except Exception as exc:
                    error = f'Trade check unavailable: {type(exc).__name__}'
                    failures.append(f"{m['ticker']}: {exc}")
                row = snapshot(m,theme,channel,series,now,trade,previous,error)
                rows.append(['' if v is None else v for v in row])
        except Exception as exc:
            failures.append(f'{series}: {type(exc).__name__}: {exc}')
    rows = rows[:MAX_MARKETS]
    status = 'Complete' if rows and not failures else 'Partial' if rows else 'Failed'
    return {'status':status, 'rows':rows, 'failures':failures, 'gaps':gaps,
            'requests':client.requests, 'series_checked':len(SERIES)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=collect(KalshiClient(),datetime.now(TORONTO))
    if args.output:
        args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='rows'} | {
        'markets':len(result['rows']), 'eligible_context':sum(r[20]=='Eligible context' for r in result['rows'])}))
    return 1 if result['status'] in ('Partial','Failed') else 0


if __name__=='__main__':
    raise SystemExit(main())
