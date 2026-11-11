"""Own-shop public catalog + Pinterest native RSS. No credentials or API posting."""
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

BASE='https://lance19c.github.io'
SHOP='https://www.etsy.com/shop/CowlingTradingCo'
FEED=SHOP+'/rss'
ROOT=Path(__file__).resolve().parents[1]
LIMIT=2

class Content(HTMLParser):
    def __init__(self):super().__init__();self.text=[];self.images=[]
    def handle_data(self,data):self.text.append(data)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='img' and safe_image(a.get('src','')):self.images.append(a['src'])
        if tag in ('br','p','div'):self.text.append(' ')

def safe_image(url):
    try:
        u=urlsplit(url)
        return u.scheme=='https' and u.hostname=='i.etsystatic.com' and not u.username and not u.password and u.port in (None,443)
    except ValueError:return False

def parse(raw):
    if len(raw)>3_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('unsafe feed')
    root=ET.fromstring(raw)
    if root.tag!='rss':raise ValueError('not RSS')
    result={}
    for item in root.findall('./channel/item'):
        link=item.findtext('link','');u=urlsplit(link)
        m=re.match(r'^/listing/([0-9]+)(?:/|$)',u.path)
        if u.scheme!='https' or u.hostname not in ('www.etsy.com','etsy.com') or not m:continue
        content=Content();content.feed(item.findtext('description',''))
        for enclosure in item.findall('enclosure'):
            if safe_image(enclosure.get('url','')):content.images.append(enclosure.get('url'))
        for media in item.findall('{http://search.yahoo.com/mrss/}content'):
            if safe_image(media.get('url','')):content.images.append(media.get('url'))
        images=list(dict.fromkeys(content.images))
        if not images:continue
        ident=m[1]
        result[ident]={'id':ident,'title':item.findtext('title','').strip()[:180],
                       'description':' '.join(' '.join(content.text).split())[:1600],
                       'image':images[0],'etsy':f'https://www.etsy.com/listing/{ident}',
                       'source_date':item.findtext('pubDate','')}
    if not result:raise ValueError('feed has no usable own-shop products; previous site retained')
    return result

def promotable(product):
    # Existing ornaments use a costly supplier; do not promote them automatically.
    # Set the cutoff only after the cheaper fulfillment mapping is deployed.
    if 'ornament' not in product.get('title', '').lower():
        return True
    policy = ROOT / 'quality-policy.json'
    if not policy.exists():
        return False
    cutoff = json.loads(policy.read_text()).get('ornaments_created_after')
    try:
        return bool(cutoff) and parsedate_to_datetime(product.get('source_date', '')) >= dt.datetime.fromisoformat(cutoff)
    except (ValueError, TypeError):
        return False

def schedule(products,state,now):
    """At most two newly exposed feed items per UTC day; stable IDs prevent repeats."""
    state=dict(state);published=dict(state.get('published',{}));day=now.date().isoformat()
    room=max(0,LIMIT-sum(v[:10]==day for v in published.values()))
    for ident in products:
        if room<=0:break
        if ident not in published and promotable(products[ident]):
            published[ident]=now.isoformat();room-=1
    state['published']=published
    return state

CSS='''body{margin:0;background:#f7f4ed;color:#173e38;font:17px/1.65 system-ui,sans-serif}main,header,footer{max-width:1120px;margin:auto;padding:26px}header{border-bottom:1px solid #d6dfd2;display:flex;justify-content:space-between;gap:20px}a{color:inherit;text-underline-offset:4px}.brand{font-weight:800;letter-spacing:.1em;text-decoration:none;font-size:14px}h1{font:clamp(36px,5vw,62px)/1.1 Georgia,serif;max-width:800px}h2{font:26px/1.2 Georgia,serif}.lead{max-width:700px;color:#58675d}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:26px;margin:40px 0}.card{background:#fff;border:1px solid #d6dfd2;border-radius:8px;overflow:hidden;text-decoration:none}.card img{width:100%;aspect-ratio:1;object-fit:contain;background:#fff}.card h2{font:20px/1.4 system-ui,sans-serif;padding:0 20px}.card p{padding:0 20px;color:#58675d;font-size:14px}.product{display:grid;grid-template-columns:1fr 1fr;gap:40px;padding:40px 0}.product img{width:100%;max-height:650px;object-fit:contain;background:white}.product h1{font-size:38px}.btn{display:inline-block;background:#173e38;color:#fff;padding:14px 24px;border-radius:5px;text-decoration:none;font-weight:700}footer{border-top:1px solid #d6dfd2;color:#58675d;font-size:13px}.muted{color:#58675d;font-size:14px}:focus-visible{outline:3px solid #b57320;outline-offset:4px}@media(max-width:700px){.product{grid-template-columns:1fr}header{font-size:14px}.product h1{font-size:32px}}'''

def shell(title,body,verify=''):
    meta=f'<meta name="p:domain_verify" content="{html.escape(verify,quote=True)}">' if verify else ''
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} | Cowling Trading Co</title>{meta}<link rel="alternate" type="application/rss+xml" href="{BASE}/feed.xml"><style>{CSS}</style></head><body><header><a class="brand" href="/">COWLING TRADING CO</a><a href="{SHOP}">Shop on Etsy</a></header><main>{body}</main><footer>Original designs, printed to order through our print partners. Design created with AI assistance from original prompts. Purchases, current prices, shipping, returns and availability are handled on Etsy. <a href="{SHOP}">Visit our Etsy shop</a>.</footer></body></html>'

def render(products,state,verify=''):
    out={};cards=[]
    for ident,p in products.items():
        title=html.escape(p['title']);image=html.escape(p['image'],quote=True);link=f'/products/{ident}.html'
        cards.append(f'<a class="card" href="{link}"><img src="{image}" alt="{title}" loading="lazy"><h2>{title}</h2><p>See details and current price on Etsy</p></a>')
        body=f'<a href="/">All gifts</a><div class="product"><img src="{image}" alt="{title}"><div><p class="muted">PRINTED TO ORDER · SOLD ON ETSY</p><h1>{title}</h1><p>{html.escape(p["description"])}</p><a class="btn" href="{p["etsy"]}?utm_source=pinterest&amp;utm_medium=organic&amp;utm_campaign=cowling_catalog">View details &amp; buy on Etsy</a><p class="muted">Check Etsy for the selected size, finish, current price, delivery estimate and availability before ordering.</p></div></div>'
        out[f'products/{ident}.html']=shell(p['title'],body,verify)
    out['index.html']=shell('Thoughtful gifts for home', '<p class="muted">COWLING TRADING CO</p><h1>A little character.<br>A place to call home.</h1><p class="lead">Explore our printed-to-order gifts and home accents. Find a design you like, then review the details and order securely through our Etsy shop.</p><div class="grid">'+''.join(cards)+'</div>',verify)
    rss=ET.Element('rss',{'version':'2.0'});channel=ET.SubElement(rss,'channel')
    for key,val in [('title','Cowling Trading Co — new designs'),('link',BASE),('description','Our own products, with details and checkout on Etsy.')]:ET.SubElement(channel,key).text=val
    for ident,stamp in state.get('published',{}).items():
        if ident not in products or not promotable(products[ident]):continue
        p=products[ident];item=ET.SubElement(channel,'item');link=f'{BASE}/products/{ident}.html'
        for key,val in [('title',p['title'][:100]),('link',link),('guid',link),('description',('Printed to order. Design created with AI assistance from original prompts. '+p['description'])[:500]),('pubDate',dt.datetime.fromisoformat(stamp).strftime('%a, %d %b %Y %H:%M:%S GMT'))]:ET.SubElement(item,key).text=val
        ET.SubElement(item,'enclosure',{'url':p['image'],'type':'image/jpeg','length':'0'})
    out['feed.xml']=ET.tostring(rss,encoding='unicode',xml_declaration=True)
    return out

def main():
    req=urllib.request.Request(FEED,headers={'User-Agent':'CowlingTradingCoCatalog/1.0'})
    with urllib.request.urlopen(req,timeout=30) as response:
        if urlsplit(response.url).hostname not in ('www.etsy.com','etsy.com'):raise ValueError('unexpected feed destination')
        raw=response.read(3_000_001)
    products=parse(raw);statepath=ROOT/'state.json';state=json.loads(statepath.read_text()) if statepath.exists() else {}
    now=dt.datetime.now(dt.timezone.utc);state=schedule(products,state,now)
    verify=(ROOT/'pinterest-verification.txt').read_text().strip() if (ROOT/'pinterest-verification.txt').exists() else ''
    output=render(products,state,verify)
    output['status.json']=json.dumps({'refreshed_at':now.isoformat(),'source':'public Etsy shop RSS','current_products':len(products),'daily_new_feed_limit':LIMIT,'feed_items':len(ET.fromstring(output['feed.xml']).findall('./channel/item'))},indent=2)+'\n'
    # Validate all inputs before replacing any generated file. Keep product pages for
    # previously published Pins even when they drop out of Etsy's ten-item RSS window.
    for name,text in output.items():
        path=ROOT/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    statepath.write_text(json.dumps(state,indent=2)+'\n')
    (ROOT/'.nojekyll').touch()
    print('Catalog refreshed:',len(products),'current products;',len(state['published']),'stable feed entries recorded')
if __name__=='__main__':main()
