import io
import unittest
import zipfile
from unittest.mock import patch
from xml.sax.saxutils import escape

from server import parse_chatgpt_payload, parse_docx, parse_share_link


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
    def test_deepseek_citations_and_uploaded_files(self):
        payload = {'data': {'biz_code': 0, 'biz_data': {'messages': [
            {'role': 'USER', 'fragments': [
                {'type': 'FILE', 'files': [{'file_name': 'notes.pdf', 'file_size': 2048, 'is_image': False}]},
                {'type': 'REQUEST', 'content': '请总结'}]},
            {'role': 'ASSISTANT', 'fragments': [
                {'type': 'SEARCH', 'results': [{'cite_index': 5, 'url': 'https://example.com/source', 'title': '来源'}]},
                {'type': 'RESPONSE', 'content': '结论[citation:5]'}]}]}}}
        with patch('server.requests.get') as get:
            get.return_value.json.return_value = payload
            result = parse_share_link('https://chat.deepseek.com/share/abc123')
        self.assertEqual(result['turns'][0]['question'][0],
                         {'type': 'attachment', 'value': 'notes.pdf', 'size': 2048,
                          'url': 'https://chat.deepseek.com/share/abc123'})
        self.assertEqual(result['turns'][0]['answer'][0]['value'], '结论[5](<https://example.com/source>)')

    def test_deepseek_text_attachment_can_be_previewed_and_downloaded(self):
        payload = {'data': {'biz_code': 0, 'biz_data': {'messages': [
            {'role': 'USER', 'fragments': [{'type': 'FILE', 'files': [
                {'file_name': 'notes.md', 'file_size': 5, 'is_image': False,
                 'signed_path': '/file?file_id=example&state=shared'}]}]},
            {'role': 'ASSISTANT', 'fragments': [{'type': 'RESPONSE', 'content': '收到'}]}]}}}
        with patch('server.requests.get') as get:
            get.return_value.json.return_value = payload
            get.return_value.content = b'hello'
            result = parse_share_link('https://chat.deepseek.com/share/abc123')
        attachment = result['turns'][0]['question'][0]
        self.assertEqual(attachment['preview'], 'hello')
        self.assertEqual(attachment['data'], 'aGVsbG8=')

    def test_chatgpt_share_pairs_user_and_assistant_messages(self):
        def node(role, content_type, parts):
            return {'message': {'author': {'role': role}, 'content': {'content_type': content_type, 'parts': parts}}}
        payload = {'title': '测试对话', 'linear_conversation': [
            node('system', 'text', ['隐藏内容']),
            node('user', 'text', ['第一问']),
            node('assistant', 'reasoning_recap', ['不应显示']),
            node('assistant', 'text', ['第一答']),
            node('user', 'text', ['第二问']),
            node('assistant', 'text', ['第二答'])]}
        with patch('server.requests.get') as get:
            get.return_value.json.return_value = payload
            result = parse_share_link('https://chatgpt.com/share/6aebe87e-b8cc-83ea-8d65-7970bc8d7149')
        self.assertEqual(result['platform'], 'ChatGPT')
        self.assertEqual(len(result['turns']), 2)
        self.assertEqual([turn['answer'][0]['value'] for turn in result['turns']], ['第一答', '第二答'])

    def test_chatgpt_image_links_to_original_share(self):
        url = 'https://chatgpt.com/share/6aebeb1b-6c50-83ea-a391-40eff520c5da'
        payload = {'title': '含图对话', 'linear_conversation': [
            {'message': {'author': {'role': 'user'}, 'content': {'content_type': 'multimodal_text', 'parts': [
                '看这张图', {'content_type': 'image_asset_pointer', 'asset_pointer': 'sediment://file_example'}]}}},
            {'message': {'author': {'role': 'assistant'}, 'content': {'content_type': 'text', 'parts': ['回答']}}}]}
        with patch('server.requests.get') as get:
            get.return_value.json.return_value = payload
            result = parse_share_link(url)
        self.assertEqual(result['turns'][0]['question'][1], {'type': 'missing-image', 'value': url})
        self.assertIn('图片需在原分享页查看', result['turns'][0]['warnings'])
        self.assertEqual(result['imageCount'], 0)

    def test_chatgpt_image_from_browser_extension(self):
        url = 'https://chatgpt.com/share/6aebeb1b-6c50-83ea-a391-40eff520c5da'
        payload = {'linear_conversation': [
            {'message': {'author': {'role': 'user'}, 'content': {'content_type': 'multimodal_text', 'parts': [
                '看图', {'content_type': 'image_asset_pointer', 'asset_pointer': 'sediment://file_example?shared_conversation_id=123'}]}}},
            {'message': {'author': {'role': 'assistant'}, 'content': {'content_type': 'text', 'parts': ['回答']}}}]}
        result = parse_chatgpt_payload(url, payload, {'file_example': 'data:image/jpeg;base64,YWJj'})
        self.assertEqual(result['turns'][0]['question'][1], {'type': 'image', 'value': 'data:image/jpeg;base64,YWJj'})
        self.assertEqual(result['imageCount'], 1)
        self.assertEqual(result['warnings'], [])

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
