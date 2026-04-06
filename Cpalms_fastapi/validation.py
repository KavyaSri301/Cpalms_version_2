

import re
from rapidfuzz import process, fuzz

def fuzzy_match_any_word(query, keywords, threshold):
    """Returns True if any word in the query fuzzily matches a keyword above the given similarity threshold."""
    words = re.findall(r'\w+', query.lower())
    single_word_keywords = [k for k in keywords if ' ' not in k]
    multi_word_keywords = [k for k in keywords if ' ' in k]
    for word in words:
        if single_word_keywords:
            _, score, _ = process.extractOne(word, single_word_keywords, scorer=fuzz.ratio)
            if score >= threshold:
                return True
    query_lower = query.lower()
    for phrase in multi_word_keywords:
        if phrase in query_lower:
            return True

    return False

def validate_educational_query(query: str) -> tuple:
    """
    Validate if the query is education-related and appropriate.
    Returns (is_valid, error_message)

    This is a lightweight pre-filter that only blocks clearly inappropriate content.
    The main classification (normal/reference/unrelated) is handled by the LLM classifier.
    """
    inappropriate_keywords = [
        'celebrity', 'gossip', 'dating',
        'financial advice', 'medical advice', 'legal advice',
        'gun', 'weapon', 'violence', 'alcohol', 'gambling', 'adult content'
    ]

    if fuzzy_match_any_word(query, inappropriate_keywords, threshold=96):
        return False, "❌ This query contains inappropriate or off-topic content. Please focus on educational content."

    return True, ""

