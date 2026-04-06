from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from io import BytesIO
import re

def format_with_icons_and_bold(text: str, resource_url: str = None):
    def bold_caps_words(match):
        word = match.group()
        return f"<b>{word}</b>"
    text = re.sub(r'\b[A-Z]{3,}\b', bold_caps_words, text)
    if resource_url:
        text = re.sub(
            r'RESOURCEID:\s*(\d+)',
            lambda m: f"RESOURCEID: {m.group(1)} (<a href='{resource_url}'>{resource_url}</a>)",
            text
        )

    return text


def generate_structured_pdf(text: str, resource_id: str, title="CPALMS Lesson Plan", resource_url: str = None):
    """Generates a structured PDF with a lesson plan and optional AI customization section, formatted using ReportLab."""
    buffer = BytesIO()
    title=f"cpalms_ai_customization_{resource_id}.pdf"
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=60, bottomMargin=60)
    story = []

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='TitleBlue', fontSize=18, alignment=TA_CENTER, textColor="#003366", spaceAfter=20, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name='Section', fontSize=12, leading=15, spaceBefore=10, spaceAfter=6, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name='Indented', fontSize=11, leading=14, leftIndent=20, spaceAfter=6))
    styles.add(ParagraphStyle(name='Answer', fontSize=11, leading=14, leftIndent=30, textColor="#444444", fontName="Helvetica-Oblique"))
    parts = re.split(r'✨\s*AI Customization Output\s*:', text, flags=re.IGNORECASE)

    story.append(Paragraph("Lesson Plan", styles['TitleBlue']))
    story.append(Spacer(1, 12))
    for line in parts[0].split('\n'):
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 8))
            continue
        formatted = format_with_icons_and_bold(stripped, resource_url)
        story.append(Paragraph(formatted, styles['Normal']))

    if len(parts) > 1:
        story.append(PageBreak())
        story.append(Paragraph("AI Customization", styles['TitleBlue']))
        story.append(Spacer(1, 12))
        for line in parts[1].split('\n'):
            stripped = line.strip()
            if not stripped:
                story.append(Spacer(1, 8))
                continue
            formatted = format_with_icons_and_bold(stripped, resource_url)
            story.append(Paragraph(formatted, styles['Normal']))
    doc.title = title  
    doc.build(story)
    buffer.seek(0)
    return buffer
