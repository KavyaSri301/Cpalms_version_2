import re

def normalize_empty_lines(text: str) -> str:
    """
    - Collapses multiple empty lines into a single empty line
    """
    if not text:
        return ""
    text = re.sub(r'^\s*--\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    return text

def convert_markdown_to_bold_html_1(text: str) -> str:
    """Formatting for AI customization with proper table support"""
    if not text:
        return ""
    
    table_pattern = r'(\|[^\n]+\|\n\|[-:\s|]+\|\n(?:\|[^\n]+\|\n?)*)'
    tables = []
    
    def save_table(match):
        tables.append(match.group(0))
        return f"__TABLE_PLACEHOLDER_{len(tables)-1}__"    
    text = re.sub(table_pattern, save_table, text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)    
    text = re.sub(r'^\s*#{1,6}\s*(.*)', r'__HEADER__<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2" target="_blank">\1</a>', text)
    text = text.replace("*", "")
    text = text.replace("#", "")    
    text = re.sub(r'^\s*-{2,}\s*$', '', text, flags=re.MULTILINE)    
    text = re.sub(r'^\s*$\n', '', text, flags=re.MULTILINE)    
    text = re.sub(r'\n+', '\n', text)    
    text = text.replace('\n', '<br>')    
    text = text.replace('__HEADER__', '<br>')    
    text = re.sub(r'^<br>', '', text)  
    # text = re.sub(r'(<br>)(<br>)(<b>)', r'\1\3', text)
    text = re.sub(r'(<b>[^<]*</b>)<br><br>(<b>)', r'\1<br>\2', text)
    for i, table_md in enumerate(tables):
        table_html = markdown_table_to_html(table_md)
        text = text.replace(f"__TABLE_PLACEHOLDER_{i}__", table_html)
    
    return text


def markdown_table_to_html(table_md: str) -> str:
    """Convert markdown table to HTML table with bold support"""
    lines = [line.strip() for line in table_md.strip().split('\n') if line.strip()]

    if len(lines) < 2:
        return table_md

    def convert_bold(text: str) -> str:
        return re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)

    headers = [convert_bold(cell.strip()) for cell in lines[0].split('|') if cell.strip()]
    rows = []
    for line in lines[2:]:
        cells = [convert_bold(cell.strip()) for cell in line.split('|') if cell.strip()]
        if cells:
            rows.append(cells)

    html = '<table style="border-collapse: collapse; margin: 20px 0; min-width: 300px;">'
    html += '<thead><tr>'
    for header in headers:
        html += (
            '<th style="border: 1px solid #ddd; padding: 12px; '
            'background-color: #667eea; color: white; text-align: left;">'
            f'{header}</th>'
        )
    html += '</tr></thead>'
    html += '<tbody>'
    for row in rows:
        html += '<tr>'
        for cell in row:
            html += f'<td style="border: 1px solid #ddd; padding: 12px;">{cell}</td>'
        html += '</tr>'
    html += '</tbody>'

    html += '</table>'
    return html


def convert_markdown_to_clean_text(text: str) -> str:
    """format for storing the data in blob"""
    if not text:
        return ""
    text = re.sub(r'(?<!\n)\s*(^#{1,6}\s*)', r'\n\n\1', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*#{1,6}\s*(.*)', lambda m: f"{m.group(1).strip().upper()}:", text, flags=re.MULTILINE)
    text = re.sub(r'(?<!\n)\n?(?=^\s*\*\*[^\n]+?:\*\*)', r'\n\n', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.*?)\*\*', lambda m: m.group(1).upper(), text)
    text = re.sub(r'(?<!\n)\n?(?=^[A-Z\s]+:)', r'\n\n', text, flags=re.MULTILINE)
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1 (\2)', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text).strip()
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    text = re.sub(r'\n---\n\s*\n', '\n\n', text)
    text = text.replace("*", "").replace("#", "").strip()
    text = text.replace("---", "")

    return text


def convert_markdown_to_clean_text_for_docs(text: str) -> str:
    """formatting for doc files"""
    if not text:
        return ""
    text = re.sub(r'(?<!\n)\s*(^#{1,6}\s*)', r'\n\n\1', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*#{1,6}\s*(.*)', r'**\1**', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.*?)\*\*', lambda m: f"**{m.group(1).strip()}**", text)
    text = re.sub(r'(?<!\n)\n?(?=^\*\*.*?\*\*:)', r'\n\n', text, flags=re.MULTILINE)
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1 (\2)', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.*?)', r'\1', text)  
    text = text.replace("#", "")
    text = re.sub(r'\n\s*\n+', '\n\n', text)

    return text.strip()
