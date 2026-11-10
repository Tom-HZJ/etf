"""Signal-independent largest-size representatives in the user-selected alternative pools.

Collector-only selection, no fetching. Size is shares × last traded price (CNY),
not NAV assets or trading volume. All peers must have valid same-day fresh data.
The first complete comparison is frozen for the day; no switch on price noise,
source failures, strategy results or a smaller fund's more attractive signal.
"""
from __future__ import annotations
import copy,math
from . import config,store

POLICY='one-user-reference-by-size-daily-v2'

def positive(value):
    if isinstance(value,bool):return None
    try:number=float(value)
    except (ValueError,TypeError,OverflowError):return None
    return number if math.isfinite(number) and number>0 else None

def member_codes():
    return {code for peers in config.TRADE_REFERENCE_ALTERNATIVES.values() for code in peers}

def size_is_fresh(quote,stamp,moment):
    """Lunch uses the known morning close for sizing only, never for an entry."""
    if stamp is None or stamp>moment:return False
    local=moment.astimezone(config.SHANGHAI_TZ)
    lunch=local.weekday()<5 and '11:30'<=local.strftime('%H:%M')<'13:00'
    reference=local.replace(hour=11,minute=30,second=0,microsecond=0) if lunch else moment
    age=(reference-min(stamp,reference)).total_seconds()
    return age<=90 and (lunch or not quote.get('stale'))

def update(quotes,moment,previous=None):
    date=moment.date().isoformat()
    previous=previous or {}
    selection=copy.deepcopy(previous) if previous.get('date')==date and previous.get('policy')==POLICY else dict(
        policy=POLICY,date=date,metric='shares × latest price',unit='CNY',scope='explicit_user_reference_alternatives',groups={})
    for index,peers in config.TRADE_REFERENCE_ALTERNATIVES.items():
        prior=selection['groups'].get(index,{})
        if prior.get('selected_code') in peers and prior.get('status')=='ready':continue
        comparisons=[]
        for code in peers:
            quote=quotes.get(code,{})
            stamp=store.parse_iso(quote.get('quote_time'))
            shares=positive(quote.get('shares'));price=positive(quote.get('price'))
            if not (stamp and stamp.astimezone(config.SHANGHAI_TZ).date().isoformat()==date
                    and size_is_fresh(quote,stamp,moment) and shares and price):continue
            size=shares*price
            if not math.isfinite(size):continue
            comparisons.append(dict(code=code,shares=shares,price=price,size=size,source=quote.get('source'),quote_time=quote['quote_time']))
        chosen=max(comparisons,key=lambda row:(row['size'],row['code'])) if len(comparisons)==len(peers) else None
        selection['groups'][index]=dict(members=list(peers),status='ready' if chosen else 'waiting_for_complete_sizes',
            selected_code=chosen['code'] if chosen else None,selected_at=moment.isoformat(timespec='seconds') if chosen else None,
            comparisons=comparisons)
    return selection

def allowed(code,selection,date):
    if code not in member_codes():return True
    if not selection or selection.get('policy')!=POLICY or selection.get('date')!=date:return False
    return any(code in peers and selection.get('groups',{}).get(index,{}).get('selected_code')==code
               for index,peers in config.TRADE_REFERENCE_ALTERNATIVES.items())

def duplicates(selection,date):
    return sorted(code for code in member_codes() if not allowed(code,selection,date))
