import unittest
from app.manual import manual_record

class ManualTests(unittest.TestCase):
    def test_invalid_input_types_are_validation_errors(self):
        for args in ((None,'','text'),('',42,'text'),('','',[])):
            with self.assertRaises(ValueError): manual_record(*args)
    def test_link_only_is_provisional_and_persistent(self):
        record=manual_record('', 'https://example.org/paper', 'text')
        self.assertEqual({},record['fields'])
        self.assertEqual('manual',record['method'])
        self.assertIsNone(record['expires_at'])
    def test_bibtex_preserves_chinese_and_custom_data(self):
        record=manual_record('@article{a,title={中文},author={张三},year={2024},custom={Keep {GPU}}}',format='bibtex')
        self.assertEqual('中文',record['fields']['title'])
        self.assertEqual('Keep {GPU}',record['fields']['custom'])
    def test_source_credentials_not_allowed(self):
        with self.assertRaises(ValueError):manual_record('', 'https://user:password@example.org', 'text')

if __name__=='__main__':unittest.main()
