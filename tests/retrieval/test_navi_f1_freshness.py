import unittest

from genshin_corpus.retrieval.navi_f1_freshness import Matcher, normalize, paragraphs, screen


def record(text, section_name='成长数值'):
    return {'sections': [{'ordinal': 3, 'source_metadata': {'name': section_name},
                         'units': [{'ordinal': 2, 'kind': 'rich_text',
                                    'value': {'normalized_text': text}}]}]}


class FreshnessTests(unittest.TestCase):
    def test_overlapping_patterns_and_failure_links(self):
        self.assertEqual(sorted(Matcher(['aba', 'ba', 'bab']).find('ababa')),
                         sorted([('aba', 0, 3), ('ba', 1, 3), ('bab', 1, 4),
                                 ('aba', 2, 5), ('ba', 3, 5)]))

    def test_short_critical_text_found_in_full_omitted_section(self):
        text = '雾虚草在密闭空间中会产生的奇妙孢子，是飞萤喜好的食物。'
        x = screen({'new': record(text)}, {'old': record('目录\n'+text+'\n结束')})
        self.assertEqual(len(x['exact_matches']), 1)
        self.assertEqual(x['exact_matches'][0]['old_occurrence']['unit'], 2)
        self.assertEqual(x['exact_matches'][0]['new_occurrences'][0]['section_name'], '成长数值')

    def test_duplicate_candidate_occurrences_are_not_deduplicated_away(self):
        text = '这是一段具有明确定位且必须保留的资料说明。'
        x = screen({'n1': record(text), 'n2': record(text)}, {'old': record(text)})
        self.assertEqual(len(x['exact_matches']), 1)
        self.assertEqual(len(x['exact_matches'][0]['new_occurrences']), 2)

    def test_near_duplicate_name_change_is_recorded(self):
        text = '甲乙丙丁戊己庚辛壬癸'*10
        x = screen({'new': record(text+'新结尾')}, {'old': record(text+'旧结尾')})
        self.assertTrue(x['near_matches'])

    def test_normalized_matching_is_not_a_citation_range(self):
        p = {'source_id': 'n', 'section': 0, 'unit': 2, 'section_name': '',
             'text': ' 起 始 \n下一行'}
        rows = list(paragraphs(p))
        self.assertEqual(p['text'][rows[1]['start']:rows[1]['end']], rows[1]['text'])
        self.assertEqual(normalize(rows[0]['text']), '起始')

    def test_shorter_lines_are_explicitly_outside_screen(self):
        x = screen({'n': record('相同但很短')}, {'old': record('相同但很短')})
        self.assertFalse(x['exact_matches'])
        self.assertIn('Shorter', x['limits'])


if __name__ == '__main__':
    unittest.main()
