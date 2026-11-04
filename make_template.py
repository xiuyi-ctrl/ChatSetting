from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Inches, Pt, RGBColor


doc = Document()
section = doc.sections[0]
section.top_margin = Inches(0.75)
section.bottom_margin = Inches(0.7)
section.left_margin = Inches(0.85)
section.right_margin = Inches(0.85)

normal = doc.styles['Normal']
normal.font.name = 'Microsoft YaHei'
normal.font.size = Pt(10.5)
normal.paragraph_format.space_after = Pt(8)

title = doc.styles['Title']
title.font.name = 'Microsoft YaHei'
title.font.size = Pt(21)
title.font.bold = True
title.font.color.rgb = RGBColor(0, 0, 0)
doc.styles['Subtitle'].font.color.rgb = RGBColor(0, 0, 0)

role = doc.styles.add_style('AB Role', WD_STYLE_TYPE.PARAGRAPH)
role.style_id = 'ABRole'
role.font.name = 'Microsoft YaHei'
role.font.size = Pt(16)
role.font.bold = True
role.paragraph_format.space_before = Pt(10)
role.paragraph_format.space_after = Pt(3)
role.paragraph_format.keep_with_next = True

body = doc.styles.add_style('Conversation Body', WD_STYLE_TYPE.PARAGRAPH)
body.font.name = 'Microsoft YaHei'
body.font.size = Pt(10.5)
body.paragraph_format.space_after = Pt(8)

doc.add_paragraph('对话记录模板', 'Title')
intro = doc.add_paragraph('每轮依次填写 A 提问和 B 回答。将光标放在内容段落中，可插入图片；复制完整的一组 A、B 即可增加轮次。请保留加粗的大号 A：和 B：标记及其段落样式。')
intro.paragraph_format.space_after = Pt(14)

for number in range(1, 4):
    label = doc.add_paragraph(f'第 {number} 轮')
    label.style = 'Subtitle'
    label.paragraph_format.space_before = Pt(14)
    label.paragraph_format.space_after = Pt(2)
    doc.add_paragraph('A：', 'AB Role')
    doc.add_paragraph('在这里填写提问；可在本段或下一段插入图片。', 'Conversation Body')
    doc.add_paragraph('B：', 'AB Role')
    doc.add_paragraph('在这里填写回答；可包含多段文字和图片。', 'Conversation Body')

doc.save('AB_conversation_template_v2.docx')
