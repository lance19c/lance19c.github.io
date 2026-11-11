import datetime as dt
import unittest
import xml.etree.ElementTree as ET
import build

class CatalogTests(unittest.TestCase):
 def test_daily_cap_and_stable_ids(self):
  items={str(n):{} for n in range(10)};now=dt.datetime(2026,11,11,tzinfo=dt.timezone.utc)
  a=build.schedule(items,{},now);self.assertEqual(len(a['published']),2)
  self.assertEqual(a,build.schedule(items,a,now))
  b=build.schedule(items,a,now+dt.timedelta(days=1));self.assertEqual(len(b['published']),4)
 def test_untrusted_links_and_markup(self):
  raw=b'<rss><channel><item><title>&lt;script&gt;bad&lt;/script&gt;</title><link>https://www.etsy.com/listing/123/test</link><description>&lt;img src="https://i.etsystatic.com/p.jpg"&gt; &lt;script&gt;bad&lt;/script&gt;</description></item><item><title>Other</title><link>https://evil.test/listing/123</link></item></channel></rss>'
  p=build.parse(raw);self.assertEqual(list(p),['123'])
  s=build.schedule(p,{},dt.datetime(2026,11,11,tzinfo=dt.timezone.utc));out=build.render(p,s)
  self.assertNotIn('<script>',out['index.html']);self.assertIn('&lt;script&gt;',out['index.html'])
  root=ET.fromstring(out['feed.xml']);self.assertEqual(root.findtext('./channel/item/link'),'https://lance19c.github.io/products/123.html')
 def test_unsafe_and_empty_feeds_preserve_previous_outputs(self):
  for raw in (b'<!DOCTYPE rss><rss/>',b'<rss><channel/></rss>'):
   with self.assertRaises(ValueError):build.parse(raw)
 def test_foreign_image_hosts_rejected(self):
  self.assertFalse(build.safe_image('https://i.etsystatic.com.evil.test/p.jpg'))
  self.assertFalse(build.safe_image('https://user:pass@i.etsystatic.com/p.jpg'))
  self.assertTrue(build.safe_image('https://i.etsystatic.com/p.jpg'))
if __name__=='__main__':unittest.main()
