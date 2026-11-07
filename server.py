"""Local DOCX conversation viewer. Run: python server.py"""
import base64
import io
import json
import re
import zipfile
import requests
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
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
PENDING_IMPORTS = []
PENDING_LOCK = threading.Lock()


def parse_share_link(url):
    parsed = urlparse(url.strip())
    if parsed.scheme == 'https' and parsed.hostname == 'chatgpt.com':
        return parse_chatgpt_link(url, parsed)
    match = SHARE_PATH.fullmatch(parsed.path.rstrip('/'))
    if parsed.scheme != 'https' or parsed.hostname != 'chat.deepseek.com' or not match:
        raise ValueError('请提供公开的 ChatGPT 或 DeepSeek 分享链接')
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
    attachment_bytes = 0
    for message in shared.get('messages', []):
        role = message.get('role')
        blocks = []
        references = {item.get('cite_index'): quote(item['url'], safe=':/?&=#%+@,;~-._')
                      for fragment in message.get('fragments', []) if fragment.get('type') == 'SEARCH'
                      for item in fragment.get('results', [])
                      if isinstance(item, dict) and isinstance(item.get('cite_index'), int)
                      and isinstance(item.get('url'), str) and urlparse(item['url']).scheme in ('http', 'https')
                      and urlparse(item['url']).hostname}
        for fragment in message.get('fragments', []):
            if fragment.get('type') in ('REQUEST', 'RESPONSE') and fragment.get('content'):
                content = re.sub(r'\[citation:(\d+)\]',
                                 lambda match: f"[{match[1]}](<{references[int(match[1])]}>)" if int(match[1]) in references else match[0],
                                 fragment['content'])
                blocks.append({'type': 'text', 'value': content})
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
                    name = item.get('file_name') or '未命名文件'
                    attachment = {'type': 'attachment', 'value': name, 'size': item.get('file_size') or 0, 'url': url}
                    if path.startswith('/file?') and 0 < attachment['size'] <= 10_000_000 and attachment_bytes + attachment['size'] <= 50_000_000:
                        try:
                            file_response = requests.get('https://files.deepseeksvc.com/api' + path,
                                                         params={'ty': 'r'}, headers=LINK_HEADERS, timeout=15)
                            file_response.raise_for_status()
                            if len(file_response.content) > 10_000_000 or attachment_bytes + len(file_response.content) > 50_000_000:
                                raise ValueError('文件过大')
                            attachment['data'] = base64.b64encode(file_response.content).decode()
                            attachment_bytes += len(file_response.content)
                            if len(file_response.content) <= 1_000_000 and name.lower().endswith(('.md', '.txt')):
                                attachment['preview'] = file_response.content.decode('utf-8', errors='replace')
                        except (requests.RequestException, ValueError):
                            media_warnings.append(f'附件 {name} 未能下载')
                    blocks.append(attachment)
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
            'type': 'link', 'platform': 'DeepSeek', 'sourceUrl': url, 'turns': turns, 'warnings': warnings,
            'imageCount': sum(1 for turn in turns for side in ('question', 'answer') for block in turn[side] if block['type'] == 'image')}


def parse_chatgpt_link(url, parsed=None):
    parsed = parsed or urlparse(url.strip())
    match = re.fullmatch(r'/share/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})', parsed.path.rstrip('/'))
    if parsed.scheme != 'https' or parsed.hostname != 'chatgpt.com' or not match:
        raise ValueError('请提供 https://chatgpt.com/share/… 格式的公开分享链接')
    share_id = match.group(1)
    return parse_chatgpt_payload(url, read_chatgpt_share(share_id))


def read_chatgpt_share(share_id):
    try:
        response = requests.get(f'https://chatgpt.com/backend-api/share/{share_id}', headers=LINK_HEADERS, timeout=15)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValueError('ChatGPT 分享内容读取失败；请确认链接公开有效，或稍后重试') from exc


def parse_chatgpt_payload(url, shared, images=None):
    match = re.fullmatch(r'https://chatgpt\.com/share/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})/?', url) if isinstance(url, str) else None
    if not match or not isinstance(shared, dict):
        raise ValueError('分享数据格式不正确')
    share_id = match.group(1)
    images = images or {}
    if not isinstance(images, dict):
        raise ValueError('图片数据格式不正确')
    turns, warnings = [], []
    for node in shared.get('linear_conversation') or []:
        message = node.get('message') or {}
        role = (message.get('author') or {}).get('role')
        if role not in ('user', 'assistant'):
            continue
        content = message.get('content') or {}
        if content.get('content_type') not in ('text', 'multimodal_text'):
            continue
        blocks = []
        for part in content.get('parts') or []:
            if isinstance(part, str) and part.strip():
                blocks.append({'type': 'text', 'value': part})
            elif isinstance(part, dict):
                text = part.get('text')
                if isinstance(text, str) and text.strip():
                    blocks.append({'type': 'text', 'value': text})
                elif part.get('content_type') == 'image_asset_pointer':
                    pointer = part.get('asset_pointer', '')
                    file_id = pointer.split('://', 1)[-1].split('?', 1)[0]
                    image = images.get(file_id)
                    if isinstance(image, str) and re.fullmatch(r'data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+', image):
                        blocks.append({'type': 'image', 'value': image})
                    else:
                        blocks.append({'type': 'missing-image', 'value': url})
        if role == 'user':
            turns.append({'question': blocks, 'answer': [], 'warnings': [], 'line': len(turns) + 1})
        elif turns:
            turns[-1]['answer'].extend(blocks)
    if not turns:
        raise ValueError('链接已读取，但没有识别到用户提问；该分享可能仅包含单条回答')
    for index, turn in enumerate(turns, 1):
        if any(block['type'] == 'missing-image' for side in ('question', 'answer') for block in turn[side]):
            turn['warnings'].append('图片需在原分享页查看')
            warnings.append(f'第 {index} 轮：图片需在原分享页查看')
        if not turn['answer']:
            turn['warnings'].append('缺少回答')
            warnings.append(f'第 {index} 轮：缺少回答')
    return {'title': shared.get('title') or 'ChatGPT 对话', 'filename': f'ChatGPT · {share_id}',
            'type': 'link', 'platform': 'ChatGPT', 'sourceUrl': url, 'turns': turns,
            'warnings': warnings, 'imageCount': sum(1 for turn in turns for side in ('question', 'answer') for block in turn[side] if block['type'] == 'image')}


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
        if self.path in ('/api/extension-import', '/api/extension-share') and self.headers.get('Origin', '').startswith('chrome-extension://'):
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
        super().end_headers()

    def do_OPTIONS(self):
        if self.path not in ('/api/extension-import', '/api/extension-share') or not self.headers.get('Origin', '').startswith('chrome-extension://'):
            self.send_error(403)
            return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods', 'POST')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/pending-import':
            with PENDING_LOCK:
                result = PENDING_IMPORTS.pop(0) if PENDING_IMPORTS else None
            payload = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
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
        if self.path not in ('/api/parse', '/api/parse-link', '/api/extension-import', '/api/extension-share'):
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length', 0))
        limit = 4_096 if self.path in ('/api/parse-link', '/api/extension-share') else 40_000_000
        if not 0 < length <= limit:
            self.send_error(413, 'Input is too large')
            return
        try:
            if self.path == '/api/extension-share':
                if not self.headers.get('Origin', '').startswith('chrome-extension://') or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('只接受浏览器扩展导入')
                url = json.loads(self.rfile.read(length)).get('url', '')
                parsed = urlparse(url.strip())
                match = re.fullmatch(r'/share/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})', parsed.path.rstrip('/'))
                if parsed.scheme != 'https' or parsed.hostname != 'chatgpt.com' or not match:
                    raise ValueError('请提供公开的 ChatGPT 分享链接')
                result = read_chatgpt_share(match.group(1))
            elif self.path == '/api/extension-import':
                if not self.headers.get('Origin', '').startswith('chrome-extension://') or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('只接受浏览器扩展导入')
                data = json.loads(self.rfile.read(length))
                result = parse_chatgpt_payload(data.get('url', ''), data.get('shared'), data.get('images'))
                with PENDING_LOCK:
                    PENDING_IMPORTS.append(result)
                    del PENDING_IMPORTS[:-3]
                result = {'ok': True, 'turns': len(result['turns']), 'images': result['imageCount']}
            elif self.path == '/api/parse-link':
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
