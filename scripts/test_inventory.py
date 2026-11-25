"""Offline own-shop backfill, quality, freshness and stable feed fixtures."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import build
import inventory

NOW=dt.datetime(2026,11,25,21,tzinfo=dt.timezone.utc)
POLICY=json.dumps({'blocked_listing_ids':['101'],'ornaments_created_after':'2026-11-11T21:59:18.379286+00:00'}).encode()


def row(ident=100,title='Housewarming mug',digital=False,original=None):
    original=int(NOW.timestamp())-86400 if original is None else original
    value={'listing_id':ident,'shop_id':123,'state':'active','url':'https://www.etsy.com/listing/'+str(ident)+'/own-design',
        'title':title,'description':'Our original design. Details on Etsy.','listing_type':'download' if digital else 'physical',
        'is_digital':digital,'price':{'amount':1650,'divisor':100,'currency_code':'USD'},
        'images':[{'listing_image_id':ident+1000,'rank':1,'url_fullxfull':'https://i.etsystatic.com/123/image.jpg'}],'public_image_count':3}
    value.update({key:original for key in inventory.TIMESTAMPS})
    return value


def snapshot(rows=None):
    rows=[row()] if rows is None else rows
    return {'version':1,'source':inventory.SOURCE,'observed_at':NOW.isoformat(),'shop_url':inventory.SHOP,'shop_id':123,
        'pages':max(1,(len(rows)+99)//100),'total_active_count':len(rows),'complete':True,'capture_errors':[],'results':rows}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.patch=patch.object(inventory,'SHOP_ID',123);self.patch.start();self.addCleanup(self.patch.stop)
    def normalize(self,value,now=NOW):return inventory.normalize_snapshot(value,POLICY,now)
    def test_complete_source_normalizes_only_public_data(self):
        result=self.normalize(snapshot())
        self.assertEqual(result['captured_count'],1);self.assertTrue(result['fresh'])
        self.assertEqual(result['products']['100']['kind'],'physical')
        self.assertEqual(result['products']['100']['etsy'],'https://www.etsy.com/listing/100')
        self.assertNotIn('price',result['products']['100'])
    def test_actual_repair_audit_shape_requires_every_count_and_no_capture_error(self):
        source=snapshot();source.pop('total_active_count')
        source.update(total_count=1,unique_active_count=1,captured_count=1,capture_errors=0,capture_error_codes=[])
        self.assertEqual(self.normalize(source)['captured_count'],1)
        for change in ({'unique_active_count':0},{'capture_errors':1},{'capture_error_codes':['invalid_public_listing']},{'captured_count':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.normalize(dict(source,**change))
    def test_foreign_shop_inactive_partial_extra_private_field_future_or_unknown_type_refused(self):
        changes=[lambda s:s.update(shop_id=124),lambda s:s['results'][0].update(shop_id=124),
            lambda s:s['results'][0].update(state='inactive'),lambda s:s.update(complete=False),
            lambda s:s.update(total_active_count=2),lambda s:s.update(capture_errors=['held']),
            lambda s:s['results'][0].update(customer_email='never-public'),lambda s:s.update(unexpected_provider_body={}),
            lambda s:s.update(observed_at=(NOW+dt.timedelta(seconds=1)).isoformat()),
            lambda s:s['results'][0].update(listing_type='both'),lambda s:s['results'][0].update(is_digital=None),
            lambda s:s['results'][0]['images'][0].update(url_fullxfull='https://foreign.example/image.jpg'),
            lambda s:s['results'][0].update(url='https://www.etsy.com/listing/999/other')]
        for change in changes:
            source=snapshot();change(source)
            with self.subTest(change=change),self.assertRaises(ValueError):self.normalize(source)
    def test_snapshot_needs_explicit_pinned_own_shop_identity(self):
        with patch.object(inventory,'SHOP_ID',None),self.assertRaises(ValueError):self.normalize(snapshot())
    def test_duplicate_listings_and_duplicate_json_keys_are_refused(self):
        with self.assertRaises(ValueError):self.normalize(snapshot([row(),row()]))
        with self.assertRaises(ValueError):inventory.strict_json(b'{"version":1,"version":1}')
        with self.assertRaises(ValueError):inventory.strict_json(b'{"value":NaN}')
    def test_all_75_existing_exclusions_stay_excluded_after_title_changes(self):
        actual=json.loads((Path(__file__).resolve().parents[1]/'quality-policy.json').read_text())
        self.assertEqual(len(actual['blocked_listing_ids']),75)
        for ident in actual['blocked_listing_ids']:
            self.assertFalse(build.promotable({'id':ident,'title':'Renamed housewarming gift','source_date':'Wed, 25 Nov 2026 21:00:00 GMT'}))
    def test_original_creation_not_renewal_or_modification_controls_ornament_cutoff(self):
        old=int(dt.datetime(2026,11,10,tzinfo=dt.timezone.utc).timestamp())
        source=snapshot([row(105,'Ceramic ornament',original=old),row(106,'New ceramic ornament')])
        source['results'][0]['created_timestamp']=int(NOW.timestamp())
        result=self.normalize(source)
        self.assertFalse(build.promotable(result['products']['105']))
        self.assertTrue(build.promotable(result['products']['106']))
        source['results'][0]['original_creation_timestamp']=None
        self.assertFalse(build.promotable(self.normalize(source)['products']['105']))
    def test_seven_days_is_hard_new_introduction_expiry_not_fake_continuous_sync(self):
        source=snapshot([row(100),row(102),row(103)])
        seed=self.normalize(source,NOW+dt.timedelta(days=7))
        self.assertFalse(seed['fresh'])
        state={'published':{'100':NOW.isoformat()}}
        display,eligible=inventory.catalog_inputs({},seed,state)
        self.assertEqual(set(display),{'100'});self.assertEqual(eligible,{})
        self.assertEqual(build.schedule(eligible,state,NOW+dt.timedelta(days=7)),state)
        self.assertEqual(len(ET.fromstring(build.render(display,state)['feed.xml']).findall('./channel/item')),1)
    def test_existing_feed_guids_and_timestamps_survive_snapshot_refresh_and_daily_cap(self):
        seed=self.normalize(snapshot([row(n) for n in range(100,110)]))
        state={'published':{'100':(NOW-dt.timedelta(days=1)).isoformat(),'102':NOW.isoformat()}}
        _,eligible=inventory.catalog_inputs({},seed,state)
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'quality-policy.json').write_bytes(POLICY)
            with patch.object(build,'ROOT',Path(folder)):
                first=build.schedule(eligible,state,NOW)
                self.assertEqual(len(first['published']),3)
                self.assertEqual(first,build.schedule(eligible,first,NOW))
                self.assertEqual(first['published']['100'],state['published']['100'])
                more=build.schedule(eligible,first,NOW+dt.timedelta(days=1))
                self.assertEqual(len(more['published']),5)
                output=build.render(seed['products'],more)
                links=[i.findtext('guid') for i in ET.fromstring(output['feed.xml']).findall('./channel/item')]
                self.assertEqual(len(links),len(set(links)))
                self.assertIn('https://lance19c.github.io/products/100.html',links)
    def test_digital_page_and_pin_do_not_claim_physical_printing_shipping(self):
        products=self.normalize(snapshot([row(100,digital=True)]))['products']
        output=build.render(products,{'published':{'100':NOW.isoformat()}})
        page=output['products/100.html'];self.assertIn('DIGITAL DOWNLOAD',page)
        self.assertNotIn('PRINTED TO ORDER',page);self.assertNotIn('printed to order',page)
        self.assertTrue(ET.fromstring(output['feed.xml']).findtext('./channel/item/description').startswith('Digital download.'))
    def test_rss_title_date_change_cannot_unblock_seeded_legacy_ornament(self):
        source=snapshot([row(100,'Ceramic ornament',original=int(NOW.timestamp())-30*86400)])
        seed=self.normalize(source)
        rss={'100':dict(seed['products']['100'],source_date='Wed, 25 Nov 2026 21:00:00 GMT')}
        display,eligible=inventory.catalog_inputs(rss,seed,{})
        self.assertFalse(build.promotable(eligible['100']))
    def test_snapshot_failure_preserves_previous_site_and_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'state.json').write_text('{"published":{}}');(root/'index.html').write_text('old site')
            (root/'quality-policy.json').write_bytes(POLICY);(root/'inventory-snapshot.json').write_text('{"private":true}')
            raw=b'<rss><channel><item><title>Mug</title><link>https://www.etsy.com/listing/100/own</link><description>&lt;img src="https://i.etsystatic.com/p.jpg"&gt;</description></item></channel></rss>'
            class Response:
                url='https://www.etsy.com/shop/CowlingTradingCo/rss'
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def read(self,*a):return raw
            with patch.object(build,'ROOT',root),patch.object(build.urllib.request,'urlopen',return_value=Response()),self.assertRaises(ValueError):build.main()
            self.assertEqual((root/'index.html').read_text(),'old site');self.assertEqual((root/'state.json').read_text(),'{"published":{}}')

if __name__=='__main__':unittest.main()


class HistoricalImageQualityTests(unittest.TestCase):
 def test_missing_or_insufficient_images_hold_only_new_seed_introductions(self):
  source=snapshot([row(n) for n in (100,102,103,104)])
  source['results'][0].pop('public_image_count')
  source['results'][1]['public_image_count']=1
  source['results'][2]['public_image_count']=2
  with patch.object(inventory,'SHOP_ID',123):seed=inventory.normalize_snapshot(source,POLICY,NOW)
  display,eligible=inventory.catalog_inputs({},seed,{})
  self.assertEqual(set(display),{'104'});self.assertEqual(set(eligible),{'104'})
  state={'published':{'100':NOW.isoformat()}}
  display,eligible=inventory.catalog_inputs({},seed,state)
  self.assertIn('100',display);self.assertNotIn('100',eligible)
 def test_existing_rss_path_is_preserved_even_when_seed_image_count_is_low(self):
  source=snapshot();source['results'][0]['public_image_count']=1
  with patch.object(inventory,'SHOP_ID',123):seed=inventory.normalize_snapshot(source,POLICY,NOW)
  rss={'100':dict(seed['products']['100'])}
  display,eligible=inventory.catalog_inputs(rss,seed,{})
  self.assertIn('100',display);self.assertIn('100',eligible)
 def test_blocked_and_pre_cutoff_seed_never_enter_catalog_cards_or_feed(self):
  products={'101':{'id':'101','title':'Renamed gift','image':'https://i.etsystatic.com/one.jpg','description':'Own design','etsy':'https://www.etsy.com/listing/101'},
            '102':{'id':'102','title':'Old ornament','source_date':'Mon, 10 Nov 2026 00:00:00 GMT','image':'https://i.etsystatic.com/two.jpg','description':'Own design','etsy':'https://www.etsy.com/listing/102'}}
  with tempfile.TemporaryDirectory() as folder:
   Path(folder,'quality-policy.json').write_bytes(POLICY)
   with patch.object(build,'ROOT',Path(folder)):output=build.render(products,{'published':{'101':NOW.isoformat(),'102':NOW.isoformat()}})
  self.assertNotIn('products/101.html',output);self.assertNotIn('products/102.html',output)
  self.assertEqual(len(ET.fromstring(output['feed.xml']).findall('./channel/item')),0)


class FreshInventoryFallbackTests(unittest.TestCase):
 def run_build(self,root,source):
  (root/'quality-policy.json').write_bytes(POLICY)
  (root/'inventory-snapshot.json').write_text(json.dumps(source))
  (root/'state.json').write_text('{"published":{}}')
  with patch.object(build,'ROOT',root),patch.object(inventory,'SHOP_ID',123),patch.object(build.urllib.request,'urlopen',side_effect=TimeoutError('public RSS unavailable')):
   build.main()
 def test_fresh_complete_inventory_can_continue_when_public_rss_is_unavailable(self):
  now=dt.datetime.now(dt.timezone.utc);observed=now-dt.timedelta(minutes=1)
  source=snapshot([row(100,original=int(observed.timestamp())-86400),row(102,original=int(observed.timestamp())-86400),row(103,original=int(observed.timestamp())-86400)])
  source['observed_at']=observed.isoformat()
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);self.run_build(root,source)
   status=json.loads((root/'status.json').read_text());state=json.loads((root/'state.json').read_text())
   self.assertEqual(len(state['published']),2);self.assertFalse(status['rss_source_verified']);self.assertIsNone(status['rss_products'])
   self.assertTrue(status['inventory_snapshot']['fresh']);self.assertFalse(status['inventory_refresh_connected'])
 def test_stale_snapshot_and_failed_public_rss_cannot_introduce_products_or_replace_site(self):
  observed=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=8)
  source=snapshot([row(100,original=int(observed.timestamp())-86400)]);source['observed_at']=observed.isoformat()
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'index.html').write_text('last successful site')
   with self.assertRaises(ValueError):self.run_build(root,source)
   self.assertEqual((root/'index.html').read_text(),'last successful site');self.assertEqual((root/'state.json').read_text(),'{"published":{}}')


class NativePromotionHistoryTests(unittest.TestCase):
 def test_observed_pins_suppress_new_introductions_without_removing_existing_guids(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'quality-policy.json').write_bytes(POLICY)
   history={'version':1,'source':'native Pinterest account outbound-link audit','observed_on':'2026-11-25','observed_pins':3,'listing_ids':['100','102']}
   (root/'promotion-history.json').write_text(json.dumps(history))
   products={str(i):{'id':str(i),'title':'Original mug','image':'https://i.etsystatic.com/own.jpg','description':'Own design','etsy':'https://www.etsy.com/listing/'+str(i)} for i in (100,102,103,104)}
   state={'published':{'100':(NOW-dt.timedelta(days=1)).isoformat()}}
   with patch.object(build,'ROOT',root):
    result=build.schedule(products,state,NOW)
    self.assertNotIn('102',result['published']);self.assertEqual(result['published']['100'],state['published']['100'])
    self.assertEqual(len(result['published']),3)
    links=[item.findtext('guid') for item in ET.fromstring(build.render(products,result)['feed.xml']).findall('./channel/item')]
    self.assertIn('https://lance19c.github.io/products/100.html',links)
    self.assertNotIn('https://lance19c.github.io/products/102.html',links)
 def test_real_history_keeps_all_24_observed_listing_ids(self):
  history=json.loads((Path(__file__).resolve().parents[1]/'promotion-history.json').read_text())
  self.assertEqual(history['observed_pins'],25);self.assertEqual(len(set(history['listing_ids'])),24)
 def test_malformed_history_cannot_silently_reset_duplicate_protection(self):
  with tempfile.TemporaryDirectory() as folder:
   Path(folder,'promotion-history.json').write_text('{"version":1}')
   with patch.object(build,'ROOT',Path(folder)),self.assertRaises(ValueError):build.schedule({'100':{}},{},NOW)
