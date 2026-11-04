"""Local DOCX conversation viewer. Run: python server.py"""
import base64
import io
import json
import re
import zipfile
import requests
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'
ROLE = re.compile(r'^\s*([AB])\s*[：:]\s*(.*)$', re.S)
SEPARATOR = re.compile(r'^[─━—\-_=\s]{5,}$')
SHARE_PATH = re.compile(r'^/share/([a-zA-Z0-9]+)$')
LINK_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}


def parse_share_link(url):
    parsed = urlparse(url.strip())
    match = SHARE_PATH.fullmatch(parsed.path.rstrip('/'))
    if parsed.scheme != 'https' or parsed.hostname != 'chat.deepseek.com' or not match:
        raise ValueError('目前支持 https://chat.deepseek.com/share/… 格式的公开分享链接')
    share_id = match.group(1)
    try:
        response = requests.get(
            'https://chat.deepseek.com/api/v0/share/content',
            params={'share_id': share_id},
            headers={**LINK_HEADERS, 'Referer': f'https://chat.deepseek.com/share/{share_id}'},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ValueError(f'DeepSeek 分享页获取失败（{getattr(exc.response, "status_code", "连接错误")}），请稍后重试') from exc
    if payload.get('data', {}).get('biz_code') != 0:
        raise ValueError('DeepSeek 未返回这条分享记录，请检查链接是否仍公开有效')
    shared = payload['data'].get('biz_data') or {}
    turns = []
    media_warnings = []
    for message in shared.get('messages', []):
        role = message.get('role')
        blocks = []
        for fragment in message.get('fragments', []):
            if fragment.get('type') in ('REQUEST', 'RESPONSE') and fragment.get('content'):
                blocks.append({'type': 'text', 'value': fragment['content']})
            elif fragment.get('type') == 'FILE':
                for item in fragment.get('files', []):
                    path = item.get('signed_path', '')
                    if item.get('is_image') and path.startswith('/file?'):
                        try:
                            image = requests.get('https://files.deepseeksvc.com/api' + path,
                                                 params={'ty': 'p'}, headers=LINK_HEADERS, timeout=15)
                            image.raise_for_status()
                            mime = image.headers.get('content-type', '').split(';')[0]
                            if mime not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif') or len(image.content) > 10_000_000:
                                raise ValueError('图片格式或大小不支持')
                            blocks.append({'type': 'image', 'value': f'data:{mime};base64,{base64.b64encode(image.content).decode()}'})
                            continue
                        except (requests.RequestException, ValueError):
                            media_warnings.append(f"图片 {item.get('file_name', '未命名文件')} 未能下载")
                    blocks.append({'type': 'text', 'value': f"[附件：{item.get('file_name', '未命名文件')}]"})
        if role == 'USER':
            turns.append({'question': blocks, 'answer': [], 'warnings': [], 'line': len(turns) + 1})
        elif role == 'ASSISTANT' and turns:
            turns[-1]['answer'].extend(blocks)
    if not turns:
        raise ValueError('链接已读取，但没有识别到问答消息')
    warnings = media_warnings
    for index, turn in enumerate(turns, 1):
        if not turn['answer']:
            turn['warnings'].append('缺少回答')
            warnings.append(f'第 {index} 轮：缺少回答')
    return {'title': shared.get('title') or 'DeepSeek 对话', 'filename': f'DeepSeek · {share_id}',
            'type': 'link', 'sourceUrl': url, 'turns': turns, 'warnings': warnings,
            'imageCount': sum(1 for turn in turns for side in ('question', 'answer') for block in turn[side] if block['type'] == 'image')}


def parse_docx(data, filename):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        document = ET.fromstring(archive.read('word/document.xml'))
        rels = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError('无法读取 Word 文档，请确认文件是有效的 .docx') from exc
    image_paths = {}
    for rel in rels.findall(PKG + 'Relationship'):
        target = rel.get('Target', '')
        if '/image' in rel.get('Type', '') and not rel.get('TargetMode'):
            image_paths[rel.get('Id')] = 'word/' + target.lstrip('/') if not target.startswith('/word/') else target.lstrip('/')
    paragraphs = []
    for number, paragraph in enumerate(document.findall('.//' + W + 'body/' + W + 'p'), 1):
        pieces = []
        for node in paragraph.iter():
            if node.tag == W + 't' and node.text:
                pieces.append({'type': 'text', 'value': node.text})
            elif node.tag == A + 'blip':
                path = image_paths.get(node.get(R + 'embed'))
                if path:
                    try:
                        raw = archive.read(path)
                        mime = 'image/png' if path.lower().endswith('.png') else 'image/jpeg'
                        pieces.append({'type': 'image', 'value': f'data:{mime};base64,{base64.b64encode(raw).decode()}'})
                    except KeyError:
                        pieces.append({'type': 'text', 'value': '[图片无法提取]'})
        text = ''.join(item['value'] for item in pieces if item['type'] == 'text').strip()
        if text or any(item['type'] == 'image' for item in pieces):
            style = paragraph.find('./' + W + 'pPr/' + W + 'pStyle')
            style_id = style.get(W + 'val') if style is not None else None
            if style_id == 'Subtitle' and re.fullmatch(r'第\s*[0-9０-９一二三四五六七八九十百]+\s*轮', text):
                continue
            styled_role = text[:1] if style_id == 'ABRole' and re.fullmatch(r'[AB]\s*[：:]', text) else None
            paragraphs.append({'line': number, 'text': text, 'pieces': pieces, 'styled_role': styled_role})
    archive.close()

    use_styled_roles = any(p['styled_role'] for p in paragraphs)

    def role_of(paragraph):
        if use_styled_roles:
            return paragraph['styled_role']
        match = ROLE.match(paragraph['text'])
        return match.group(1) if match else None

    title = next((p['text'] for p in paragraphs if p['text'] and not ROLE.match(p['text']) and not SEPARATOR.match(p['text'])), Path(filename).stem)
    groups, group = [], []
    has_separator = False
    for paragraph in paragraphs:
        if SEPARATOR.fullmatch(paragraph['text']):
            has_separator = True
            if group:
                groups.append(group)
                group = []
        else:
            group.append(paragraph)
    if group:
        groups.append(group)

    if not has_separator:
        groups, group = [], []
        has_answer = False
        for paragraph in paragraphs:
            role = role_of(paragraph)
            if role == 'A' and has_answer:
                groups.append(group)
                group = []
                has_answer = False
            group.append(paragraph)
            if role == 'B' and any(role_of(p) == 'A' for p in group):
                has_answer = True
        if group:
            groups.append(group)

    turns, warnings = [], []
    for group in groups:
        a_index = next((i for i, p in enumerate(group) if role_of(p) == 'A'), None)
        if a_index is None:
            continue  # title and introduction
        b_index = next((i for i in range(a_index + 1, len(group)) if role_of(group[i]) == 'B'), None)
        question = make_blocks(group[a_index:b_index])
        answer = make_blocks(group[b_index:]) if b_index is not None else []
        issue = [] if b_index is not None else ['缺少回答']
        if issue:
            warnings.append(f'第 {len(turns) + 1} 轮：缺少回答')
        turns.append({'question': question, 'answer': answer, 'warnings': issue, 'line': group[a_index]['line']})
    if not turns:
        raise ValueError('没有识别到 A：提问。请检查文档中的角色标记和分隔线。')
    return {'title': title, 'filename': filename, 'turns': turns, 'warnings': warnings,
            'imageCount': sum(1 for turn in turns for side in ('question', 'answer') for b in turn[side] if b['type'] == 'image')}


def make_blocks(paragraphs):
    blocks = []
    for index, paragraph in enumerate(paragraphs):
        pieces = paragraph['pieces']
        if index == 0:
            match = ROLE.match(paragraph['text'])
            if match:
                prefix = match.group(0)[:match.start(2)]
                remaining = len(prefix)
                trimmed = []
                for piece in pieces:
                    if piece['type'] == 'text' and remaining:
                        cut = min(remaining, len(piece['value']))
                        remaining -= cut
                        value = piece['value'][cut:]
                        if value:
                            trimmed.append({'type': 'text', 'value': value})
                    else:
                        trimmed.append(piece)
                pieces = trimmed
        pending = ''
        for piece in pieces:
            if piece['type'] == 'text':
                pending += piece['value']
            else:
                if pending.strip():
                    blocks.append({'type': 'text', 'value': pending.strip()})
                pending = ''
                blocks.append(piece)
        if pending.strip():
            blocks.append({'type': 'text', 'value': pending.strip()})
    return blocks


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        if self.path.split('?', 1)[0] in ('/interface-sample.html', '/app.js'):
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        if self.path == '/api/sample':
            sample = ROOT / 'sample.docx'
            if not sample.exists():
                self.send_error(404)
                return
            payload = json.dumps(parse_docx(sample.read_bytes(), sample.name), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.split('?', 1)[0] in ('/interface-sample.html', '/app.js'):
            if 'If-Modified-Since' in self.headers:
                del self.headers['If-Modified-Since']
        super().do_GET()

    def do_POST(self):
        if self.path not in ('/api/parse', '/api/parse-link'):
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length', 0))
        limit = 4_096 if self.path == '/api/parse-link' else 25_000_000
        if not 0 < length <= limit:
            self.send_error(413, 'Input is too large')
            return
        try:
            if self.path == '/api/parse-link':
                result = parse_share_link(json.loads(self.rfile.read(length)).get('url', ''))
            else:
                filename = unquote(self.headers.get('X-Filename', 'conversation.docx'))
                result = parse_docx(self.rfile.read(length), filename)
            status = 200
        except (ValueError, json.JSONDecodeError) as exc:
            result, status = {'error': str(exc)}, 400
        payload = json.dumps(result, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
    print('Open http://127.0.0.1:8765/interface-sample.html', flush=True)
    server.serve_forever()
