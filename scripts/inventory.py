"""Validate an allowlisted own-shop inventory snapshot; no network or credentials."""
import argparse
import datetime as dt
from email.utils import format_datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

SHOP='https://www.etsy.com/shop/CowlingTradingCo'
# Filled only from the reviewed authoritative own-shop capture; absent is OFF.
SHOP_ID=68595983
SOURCE='etsy-active-listings-read'
MAX_ROWS=200
MAX_BYTES=4_000_000
MAX_AGE=dt.timedelta(days=7)
ENVELOPE={'version','source','observed_at','shop_url','shop_id','pages','total_active_count','complete','capture_errors','results'}
AUDIT_ENVELOPE=(ENVELOPE-{'total_active_count'})|{'total_count','unique_active_count','captured_count','capture_error_codes'}
TIMESTAMPS=('original_creation_timestamp','created_timestamp','creation_timestamp','updated_timestamp','last_modified_timestamp')
FIELDS={'listing_id','shop_id','state','url','title','description','listing_type','is_digital','price','images','public_image_count',*TIMESTAMPS}


def require(ok):
    if ok is not True:raise ValueError('inventory snapshot refused')


def strict_json(raw):
    require(type(raw) is bytes and len(raw)<=MAX_BYTES)
    def pairs(values):
        result={}
        for key,value in values:
            require(key not in result);result[key]=value
        return result
    def constant(_):raise ValueError('inventory snapshot refused')
    try:return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)
    except Exception:raise ValueError('inventory snapshot refused') from None


def stamp(value):
    require(type(value) is str and len(value)<=64)
    try:result=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except Exception:raise ValueError('inventory snapshot refused') from None
    require(result.tzinfo is not None and result.utcoffset()==dt.timedelta(0))
    return result


def safe_image(value):
    if type(value) is not str or len(value)>3000:return False
    try:
        u=urlsplit(value)
        return (u.scheme=='https' and u.hostname=='i.etsystatic.com' and not u.username and not u.password
                and u.port in (None,443) and bool(u.path) and not u.fragment)
    except ValueError:return False


def number(value):return type(value) is int and 0<value<10**20


def normalize_snapshot(source,quality_policy_bytes,now):
    """All rows must be active, complete and owned. Old snapshots cannot add IDs.

    Original creation controls the existing ornament cutoff. Renewed/modified
    dates never establish that a legacy physical offer uses cheaper fulfillment.
    """
    if type(source) is dict and set(source)==AUDIT_ENVELOPE:
        audit=source
        require(type(audit['capture_errors']) is int and audit['capture_errors']==0
                and audit['capture_error_codes']==[] and all(type(audit[key]) is int for key in ('total_count','unique_active_count','captured_count'))
                and audit['total_count']==audit['unique_active_count']==audit['captured_count'])
        source={key:value for key,value in audit.items() if key in ENVELOPE}
        source.update(total_active_count=audit['total_count'],capture_errors=[])
    require(type(source) is dict and set(source)==ENVELOPE and type(source['version']) is int and source['version']==1
            and source['source']==SOURCE and source['shop_url']==SHOP and number(SHOP_ID) and number(source['shop_id']) and source['shop_id']==SHOP_ID
            and source['complete'] is True and source['capture_errors']==[]
            and type(source['total_active_count']) is int and 0<=source['total_active_count']<=MAX_ROWS
            and type(source['pages']) is int and source['pages']==max(1,(source['total_active_count']+99)//100)
            and type(source['results']) is list and len(source['results'])==source['total_active_count'])
    observed=stamp(source['observed_at'])
    require(type(now) is dt.datetime and now.tzinfo is not None and now>=observed)
    rules=strict_json(quality_policy_bytes)
    require(type(rules) is dict and set(rules)=={'blocked_listing_ids','ornaments_created_after'}
            and type(rules['blocked_listing_ids']) is list and all(type(v) is str and re.fullmatch('[1-9][0-9]{0,19}',v) is not None for v in rules['blocked_listing_ids'])
            and len(set(rules['blocked_listing_ids']))==len(rules['blocked_listing_ids']))
    stamp(rules['ornaments_created_after'])
    products={}
    for row in source['results']:
        require(type(row) is dict and set(row) in (FIELDS,FIELDS-{'public_image_count'}) and number(row['listing_id']) and row['shop_id']==source['shop_id']
                and type(row['shop_id']) is int and row['state']=='active' and row['listing_type'] in ('physical','download')
                and type(row['is_digital']) is bool and row['is_digital'] is (row['listing_type']=='download')
                and type(row['title']) is str and 0<len(row['title'].strip())<=180
                and type(row['description']) is str and len(row['description'])<=16000
                and type(row['images']) is list and len(row['images'])==1)
        ident=str(row['listing_id']);require(ident not in products)
        u=urlsplit(row['url'])
        require(u.scheme=='https' and u.hostname in ('www.etsy.com','etsy.com') and not u.username and not u.password
                and u.port in (None,443) and re.fullmatch('/listing/'+ident+r'(?:/[^?#]*)?',u.path) is not None)
        price=row['price']
        require(type(price) is dict and set(price)=={'amount','divisor','currency_code'}
                and type(price['amount']) is int and price['amount']>=0 and number(price['divisor'])
                and type(price['currency_code']) is str and re.fullmatch('[A-Z]{3}',price['currency_code']) is not None)
        for key in TIMESTAMPS:require(row[key] is None or type(row[key]) is int and 0<=row[key]<=int(observed.timestamp()))
        image=row['images'][0]
        require(type(image) is dict and set(image)=={'listing_image_id','rank','url_fullxfull'}
                and number(image['listing_image_id']) and type(image['rank']) is int and 0<=image['rank']<=10 and safe_image(image['url_fullxfull']))
        original=row['original_creation_timestamp']
        count=row.get('public_image_count')
        require(count is None or type(count) is int and 0<=count<=20)
        products[ident]={'id':ident,'title':row['title'].strip(),'description':' '.join(row['description'].split())[:1600],
            'image':image['url_fullxfull'],'etsy':'https://www.etsy.com/listing/'+ident,
            'source_date':format_datetime(dt.datetime.fromtimestamp(original,dt.timezone.utc)) if original else '',
            'kind':'digital' if row['is_digital'] else 'physical','public_image_count':count}
    return {'products':products,'observed_at':observed.isoformat(),'expires_at':(observed+MAX_AGE).isoformat(),
            'fresh':now<observed+MAX_AGE,'source':SOURCE,'captured_count':len(products),
            'quality_policy_sha256':hashlib.sha256(quality_policy_bytes).hexdigest(),
            'snapshot_sha256':hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()}


def catalog_inputs(rss_products,seed,state):
    """RSS stays live; seed freshness governs new introductions, never GUID reuse."""
    seeded=seed['products'] if seed else {}
    published=state.get('published',{})
    image_eligible={ident:p for ident,p in seeded.items() if type(p.get('public_image_count')) is int and p['public_image_count']>=3}
    display={ident:p for ident,p in seeded.items() if seed['fresh'] and ident in image_eligible or ident in published}
    eligible=dict(image_eligible) if seed and seed['fresh'] else {}
    for ident,product in rss_products.items():
        product=dict(product)
        if ident in seeded:
            # Original dates/type remain factual even after a listing renewal.
            product.update(source_date=seeded[ident]['source_date'],kind=seeded[ident]['kind'])
        display[ident]=product;eligible[ident]=product
    return display,eligible


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);p.add_argument('--quality-policy',type=Path,default=Path(__file__).resolve().parents[1]/'quality-policy.json')
    a=p.parse_args();result=normalize_snapshot(strict_json(a.snapshot.read_bytes()),a.quality_policy.read_bytes(),dt.datetime.now(dt.timezone.utc))
    print(json.dumps({key:result[key] for key in ('source','captured_count','observed_at','expires_at','fresh','snapshot_sha256','quality_policy_sha256')},sort_keys=True))

if __name__=='__main__':main()
