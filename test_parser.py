import io
import unittest
import zipfile
from xml.sax.saxutils import escape

from server import parse_docx


def document_with_paragraphs(paragraphs):
    body = ''
    for item in paragraphs:
        style, text = item if isinstance(item, tuple) else (None, item)
        style_id = {'role': 'ABRole', 'subtitle': 'Subtitle'}.get(style)
        ppr = f'<w:pPr><w:pStyle w:val="{style_id}"/></w:pPr>' if style_id else ''
        body += f'<w:p>{ppr}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>'
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('word/document.xml', xml)
        archive.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
    return out.getvalue()


class ParserTests(unittest.TestCase):
    def test_template_without_horizontal_separators(self):
        data = document_with_paragraphs(['对话记录', 'A：第一问', 'B：第一答', '', 'A：第二问', 'B：第二答', '', 'A：第三问', 'B：第三答'])
        result = parse_docx(data, 'conversation.docx')
        self.assertEqual(len(result['turns']), 3)
        self.assertEqual([turn['question'][0]['value'] for turn in result['turns']], ['第一问', '第二问', '第三问'])

    def test_styled_markers_ignore_role_examples_in_body(self):
        data = document_with_paragraphs([
            '对话记录', ('role', 'A：'), '请照此格式填写：', 'A：问题', 'B：回答',
            ('role', 'B：'), '实际回答', ('role', 'A：'), '第二问', ('role', 'B：'), '第二答'
        ])
        result = parse_docx(data, 'styled.docx')
        self.assertEqual(len(result['turns']), 2)
        self.assertEqual(result['turns'][0]['question'][1]['value'], 'A：问题')

    def test_styled_turn_labels_do_not_enter_previous_answer(self):
        data = document_with_paragraphs([
            ('subtitle', '第 1 轮'), ('role', 'A：'), '第一问', ('role', 'B：'), '第一答',
            ('subtitle', '第 2 轮'), ('role', 'A：'), '第二问', ('role', 'B：'), '第二答',
            '我刚才提到第 2 轮。'
        ])
        result = parse_docx(data, 'styled.docx')
        self.assertEqual(len(result['turns']), 2)
        self.assertEqual([block['value'] for block in result['turns'][0]['answer']], ['第一答'])
        self.assertEqual([block['value'] for block in result['turns'][1]['answer']], ['第二答', '我刚才提到第 2 轮。'])


if __name__ == '__main__':
    unittest.main()
