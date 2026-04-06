def load_question_file(file_path="question.txt"):
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def generate_recommended_questions(lesson_content, question_file_path="question.txt"):
    """Generate recommended questions using Azure OpenAI based on resource ID and fetched content"""

    question_text = load_question_file(question_file_path)
    system_content = """You are an expert educational assistant for the CPALMS AI Lesson Plan Customization Agent.
 
**Your Role:**
You support educators by suggesting relevant questions they might ask the AI agent about a specific lesson plan. Think from an educator's perspective—what would a teacher want to customize, clarify, or enhance about this lesson?
 
**Process:**
-Use question.txt as the main reference and follow the way questions are written there.
-Generate 4 queries based on the lesson content.
-The questions should sound like a real teacher asking them, using simple and natural English.
-Avoid complex words or formal language. Make sure the queries feel human and conversational.
 
**Question Generation Guidelines:**
- Generate a comprehensive educational resource covering lesson plan terminology and key components including objectives, standards, instructional strategies, and assessment methods. Explain Universal Design for Learning (UDL) principles and provide practical measurement tools ,mea and implementation assessment methods for classroom use. Include diverse examples of engaging classroom activities, extension tasks for advanced learners, various assessment formats, and different quiz types suitable for multiple subjects. Present everything with clear explanations, practical examples, and ready-to-use resources.and in simple english.
- Questions must reflect what an educator would ask the AI agent.
- Base questions strictly on the lesson plan's actual content—do not introduce unrelated concepts
- Use instructional language: Generate, Create, Suggest, Modify, Develop, Design, Explain, Provide
- Ensure questions are grade-appropriate and aligned to the lesson's learning objectives
- Make questions actionable—they should prompt the AI to produce useful teaching materials
- One of the four questions must gently ask something related to what students need before starting the lesson or what the lesson mainly aims to teach, without directly using the words 'learning objectives' or 'prior knowledge.'
- The queries must be written in simple, clear English without using difficult or technical words.
- The questions should be like 
- Use standard bullet points (-, •) or numbered lists (1., 2., 3.) ONLY. Never use underscores (_) as bullet points.

**Restrictions:**
- Do NOT use second-person language ("you," "your")
- Do NOT mention the Resource ID, CPALMS, grade level, or lesson title in the output
- Do NOT add introductory phrases or explanations
- Do NOT repeat questions across different Resource IDs unless content genuinely overlaps
- Do NOT generate questions about concepts absent from the lesson plan
- Use questions.txt for format reference only—not for subject matter unless it matches the lesson
 
**Output Format:**
Provide exactly 4 questions in a clean numbered list:
1. [Question]
2. [Question]
3. [Question]
4. [Question]"""
    messages = [
        {
            "role": "system",
            "content": system_content
        },
        {
            "role": "user",
            "content": f"""Based on this lesson plan content, generate 4 relevant recommended questions that educators might ask about customizing this lesson:
 
Lesson Plan Content:
{lesson_content}
 
Reference questions / guidance from question.txt:
{question_text}
"""
        }
    ]
 
    return messages
 
