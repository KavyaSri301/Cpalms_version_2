import re
import time
from typing import Dict, List
from azure.search.documents.indexes.models import (
    SearchField,
    SearchableField,
    SimpleField,
    SearchFieldDataType,
    SearchIndex,
    VectorSearch,
    VectorSearchProfile,
    HnswAlgorithmConfiguration
)
from logs_to_blob import log_query_to_blob
from dotenv import load_dotenv
import os
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
import tiktoken

load_dotenv()

# -----------------------------
# Initialize OpenAI Client
# -----------------------------
_openai_credential = DefaultAzureCredential()
_token_provider = get_bearer_token_provider(
    _openai_credential,
    "https://cognitiveservices.azure.com/.default"
)

openai_client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    azure_ad_token_provider=_token_provider,
    api_version=os.getenv("AZURE_OPENAI_API_VERSION")
)

EMBED_MODEL = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

# Mapping of lesson plan question prefixes to field names
# Now supports prefix matching - any question starting with these prefixes will map to the field
LESSON_PLAN_FIELD_MAPPING = {
    "Learning Objectives": "LearningObjectives",
    "Reading Passage 2": "ReadingPassage2",
    "Guiding Questions": "GuidingQuestions",
    "Reading Passage 1": "ReadingPassage1",
    "Prior Knowledge": "PriorKnowledge",
    "Closure": "Closure",
    "Teaching Phase": "TeachingPhase",
    "Readiness Questions": "ReadinessQuestions",
    "Guided Practice": "GuidedPractice",
    "Elaborate": "Elaborate",
    "Independent Practice": "IndependentPractice",
    "Reflection Questions 2": "ReflectionQuestions2",
    "Engage": "Engage",
    "Explore": "Explore",
    "Explain": "Explain",
    "Introduction": "Introduction",
    "Investigate": "Investigate",
    "Analyze": "Analyze",
    "Letter Template 1": "LetterTemplate1",
    "Data Set 1": "DataSet1",
    "Data Set 2": "DataSet2",
    "Formative Assessment": "FormativeAssessment",
    "Guiding/Reflective Questions": "GuidingReflectiveQuestions",
    "Letter Template 2": "LetterTemplate2",
    "Additional Instructions or Materials": "AdditionalInstructionsorMaterials",
    "Instructional Suggestions": "InstructionalSuggestions",
    "Comprehension/Readiness Questions": "ComprehensionReadinessQuestions",
    "Supplemental Reading": "SupplementalReading",
    "Feedback to Students": "FeedbacktoStudents",
    "Summative Assessment": "SummativeAssessment",
    "Florida's B.E.S.T. Benchmark Alignment Notes": "BenchmarkAlignmentNotes",
    "Predict": "Predict",
    "Observe": "Observe",
    "Procedure": "Procedure",
    "Teacher Notes": "TeacherNotes",
    "Optional": "Optional",
    "Universal Design for Learning (UDL)": "UniversalDesignForLearning",
    "CTE Course Info and Benchmark Notes": "CTECourseInfoBenchmarkNotes",
    "Career Connection": "CareerConnection",
    "Unit Outline": "UnitOutline"
}

# Get unique field names only
UNIQUE_LESSON_PLAN_FIELDS = list(dict.fromkeys(LESSON_PLAN_FIELD_MAPPING.values()))


# def map_question_to_field(question_title: str) -> str:
#     """
#     Map a question title to a field name using prefix matching.
#     Returns the field name if matched, otherwise None.
    
#     Examples:
#         "Introduction: How will the teacher..." -> "Introduction"
#         "Learning Objectives: What should..." -> "LearningObjectives"
#         "Closure: How will the teacher assist..." -> "Closure"
#     """
#     if not question_title:
#         return None
    
#     # Normalize the question title (strip whitespace)
#     normalized_title = question_title.strip()
    
#     # Try exact match first (backward compatibility)
#     if normalized_title in LESSON_PLAN_FIELD_MAPPING:
#         return LESSON_PLAN_FIELD_MAPPING[normalized_title]
    
#     # Try prefix matching - check if question starts with any mapped prefix
#     for prefix, field_name in LESSON_PLAN_FIELD_MAPPING.items():
#         # Check if the question title starts with this prefix followed by ':' or end of string
#         if normalized_title.startswith(prefix + ":") or normalized_title == prefix:
#             return field_name
    
#     return None


def map_question_to_field(question_title: str) -> str:
    """
    Map a question title to a field name using prefix matching and substring matching.
    Returns the field name if matched, otherwise None.
    
    Priority order:
    1. Exact match
    2. Prefix match (e.g., "Introduction: How will...")
    3. Substring match (e.g., "Assessing Student Progress" contains trigger phrase)
    
    Examples:
        "Introduction: How will the teacher..." -> "Introduction"
        "Learning Objectives: What should..." -> "LearningObjectives"
        "Assessing Student Progress" -> "FormativeAssessment"
        "Practice in Teams or Pairs" -> "GuidedPractice"
    """
    if not question_title:
        return None
    
    # Normalize the question title (strip whitespace)
    normalized_title = question_title.strip()
    
    # These are checked AFTER exact and prefix matches
    SUBSTRING_MAPPINGS = {
        "Assessing Student Progress": "FormativeAssessment",
        "Closing Activities": "Closure",
        "Evaluate Understanding": "FormativeAssessment",
        "Financial Literacy Content Knowledge Notes": "TeacherNotes",
        "Get Started": "Engage",
        "Learning Trajectory": "UnitOutline",
        "Practice Alone": "IndependentPractice",
        "Practice Together": "GuidedPractice",
        "Practice in Teams or Pairs": "GuidedPractice",
        "Resource Information": "SupplementalReading",
        "Student Handout Descriptions": "AdditionalInstructionsorMaterials",
        "Materials":  "AdditionalInstructionsorMaterials",
        "Time": "Procedure"
    }
    
    # 1. Try exact match first (backward compatibility)
    if normalized_title in LESSON_PLAN_FIELD_MAPPING:
        return LESSON_PLAN_FIELD_MAPPING[normalized_title]
    
    # 2. Try prefix matching - check if question starts with any mapped prefix
    for prefix, field_name in LESSON_PLAN_FIELD_MAPPING.items():
        # Check if the question title starts with this prefix followed by ':' or end of string
        if normalized_title.startswith(prefix + ":") or normalized_title == prefix:
            return field_name
    
    # 3. Try substring matching - check if any trigger phrase is contained in the title
    for substring, field_name in SUBSTRING_MAPPINGS.items():
        if substring.lower() in normalized_title.lower():
            return field_name
    
    return None


# -----------------------------
# Create Index if Not Exists
# -----------------------------
async def create_index_if_not_exists(self):
    """Create Azure Search index if it doesn't exist"""
    try:
        log_query_to_blob(f"\n{'='*60}")
        log_query_to_blob(f"Checking if index '{self.index_name}' exists...")
        log_query_to_blob(f"{'='*60}")
        
        # Check if index exists
        try:
            await self.index_client.get_index(self.index_name)
            log_query_to_blob(f"✓ Index '{self.index_name}' already exists\n")
            return
        except Exception:
            log_query_to_blob(f"✗ Index '{self.index_name}' does not exist")
            log_query_to_blob(f"→ Creating new index...\n")

        # Define basic fields
        fields = [
            SearchableField(name="id", type=SearchFieldDataType.String, key=True, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="BenchmarkCodes", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="Benchmark_Description",type=SearchFieldDataType.String,searchable=True,filterable=False,retrievable=True),
            SearchableField(name="Title", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="Description", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="PrimaryICT", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="SpecialMaterialsNeeded", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="Files", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="BenchmarkIds", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                vector_search_dimensions=3072,
                vector_search_profile_name="default-vector-profile",
                retrievable=True
            ),
            SearchableField(name="GradeLevelNames", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="SubjectAreaNames", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="IntendedAudienceNames", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="ResourceUrl", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="PublishedDate", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True, sortable=True),
            SearchableField(name="ResourceTypeId", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="PrimaryResourceICTId", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="Accomodation", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="Extensions", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
            SearchableField(name="FurtherRecommendations", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True),
        ]

        # Add all lesson plan question fields (unique only)
        for field_name in UNIQUE_LESSON_PLAN_FIELDS:
            fields.append(
                SearchableField(name=field_name, type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True)
            )

        # Add metadata field for unmapped questions
        fields.append(
            SearchableField(name="metadata", type=SearchFieldDataType.String, searchable=True, filterable=True, retrievable=True)
        )

        log_query_to_blob(f"→ Configured {len(fields)} fields for the index")

        # Configure vector search
        vector_search = VectorSearch(
            profiles=[VectorSearchProfile(name="default-vector-profile", algorithm_configuration_name="default-algorithm")],
            algorithms=[HnswAlgorithmConfiguration(name="default-algorithm")]
        )
        log_query_to_blob(f"→ Configured vector search with HNSW algorithm")

        # Create index
        index = SearchIndex(name=self.index_name, fields=fields, vector_search=vector_search)
        await self.index_client.create_index(index)
        
        log_query_to_blob(f"✓ Successfully created index '{self.index_name}'")
        log_query_to_blob(f"{'='*60}\n")

    except Exception as e:
        log_query_to_blob(f"\n✗ ERROR creating index: {str(e)}")
        log_query_to_blob(f"{'='*60}\n")
        raise


def trim_text_by_tokens(text: str, max_tokens: int) -> str:
    """Trim text to specified token count"""
    encoder = tiktoken.get_encoding("cl100k_base")
    tokens = encoder.encode(text)

    if len(tokens) > max_tokens:
        tokens = tokens[:max_tokens]

    return encoder.decode(tokens)


# -----------------------------
# Generate Embedding
# -----------------------------
def generate_embedding(text: str) -> List[float]:
    """Generate embedding vector for text using Azure OpenAI"""
    try:
        EMBED_DIMENSIONS = 3072
        MAX_TOKENS = 8192  # for text-embedding-3-large

        if not text or not text.strip():
            log_query_to_blob("⚠ Warning: Empty text provided for embedding, returning zero vector")
            return [0.0] * EMBED_DIMENSIONS

        encoder = tiktoken.get_encoding("cl100k_base")
        tokens = encoder.encode(text)

        original_token_count = len(tokens)

        if original_token_count > MAX_TOKENS:
            tokens = tokens[:MAX_TOKENS]
            text = encoder.decode(tokens)
            log_query_to_blob(f"⚠ Warning: Text truncated from {original_token_count} → {MAX_TOKENS} tokens")

        log_query_to_blob(f"→ Generating embedding for text ({len(tokens)} tokens)...")
        start_time = time.time()

        response = openai_client.embeddings.create(
            input=text,
            model=EMBED_MODEL
        )

        embedding = response.data[0].embedding
        elapsed = time.time() - start_time

        log_query_to_blob(f"✓ Embedding generated successfully ({len(embedding)} dimensions) in {elapsed:.2f}s")
        return embedding

    except Exception as e:
        log_query_to_blob(f"✗ ERROR generating embedding: {str(e)}")
        log_query_to_blob("→ Returning zero vector as fallback")
        return [0.0] * 3072


# -----------------------------
# Prepare Document for Indexing
# -----------------------------
def prepare_document(self, resource_json: Dict) -> Dict:
    """Prepare document for indexing from resource JSON"""
    try:
        log_query_to_blob(f"\n{'='*60}")
        log_query_to_blob(f"PREPARING DOCUMENT FOR INDEXING")
        print("PREPARING DOCUMENT FOR INDEXING")
        
        # Extract resource ID early
        resource_id = str(resource_json.get("ResourceID", ""))
        log_query_to_blob(f"→ Resource ID: {resource_id}")
        print(f"→ Resource ID: {resource_id}")
        
        # Extract basic information
        log_query_to_blob(f"\n→ Extracting basic fields...")
        title = resource_json.get('Title', '')
        log_query_to_blob(f"  • Title: {title[:50]}..." if len(title) > 50 else f"  • Title: {title}")
        
        description = _clean_html(resource_json.get('Description', ''))
        log_query_to_blob(f"  • Description length: {len(description)} chars")
        
        # Process Lesson Plan Questions
        log_query_to_blob(f"\n→ Processing lesson plan questions...")
        lesson_plan_questions = resource_json.get("LessonPlanQuestions", [])
        lesson_plan_data = {}
        metadata_items = []
        
        for idx, question in enumerate(lesson_plan_questions, 1):
            question_title = question.get("Title", "")
            log_query_to_blob(f"Question Title: {question_title}")
            answer = _clean_html(question.get("ResLessPlanQuestionAnswer", ""))
            
            if question_title and answer:
                # Use the new flexible mapping function
                field_name = map_question_to_field(question_title)
                
                if field_name:
                    # log_query_to_blob(f"Field name: {field_name}")
                    lesson_plan_data[field_name] = f"{question_title}: {answer}"
                    # log_query_to_blob(f"  • Mapped: {question_title[:50]}... → {field_name}")
                else:
                    # Add to metadata if not mapped
                    metadata_items.append(f"{question_title}: {answer}")
                    log_query_to_blob(f"  • Unmapped (added to metadata): {question_title[:50]}...")
        
        log_query_to_blob(f"  • Total lesson plan fields mapped: {len(lesson_plan_data)}")
        log_query_to_blob(f"  • Total unmapped items in metadata: {len(metadata_items)}")

        log_query_to_blob(f"\n→ Processing benchmark descriptions...")

        benchmark_descriptions = resource_json.get("BenchmarkDescriptions", [])

        benchmark_desc_list = []

        for b in benchmark_descriptions:
            benchmark_id = str(b.get("BenchmarkID", "")).strip()
            code = b.get("BenchmarkCode", "").strip()
            desc = _clean_html(b.get("Descriptor", "")).strip()

            if code and desc:
                benchmark_desc_list.append(
                    f"BenchmarkCode:{code}, Description: {desc}, BenchmarkId:{benchmark_id}"
                )
                log_query_to_blob(f"  • Added benchmark description for {code}")

        benchmark_description_text = " | ".join(benchmark_desc_list)
        log_query_to_blob(f"  • Total benchmark descriptions: {len(benchmark_desc_list)}")

        # Extract additional information
        log_query_to_blob(f"\n→ Processing additional information...")
        accommodation = _clean_html(resource_json.get("Accomodation", ""))
        extensions = _clean_html(resource_json.get("Extensions", ""))
        further = _clean_html(resource_json.get("FurtherRecommendations", ""))
        
        extra_info = []
        
        benchmark_ids = resource_json.get("BenchmarkIds", "")
        if benchmark_ids:
            extra_info.append(f"Benchmark Ids: {benchmark_ids}")
            log_query_to_blob(f"  • Added benchmark Ids")
        extra_info_text = " ".join(extra_info)

        # Process Files
        log_query_to_blob(f"\n→ Processing files...")
        files = resource_json.get("Files", [])
        files_str = []
        
        for idx, f in enumerate(files, 1):
            title_file = f.get("FileTitle", "").strip()
            desc = f.get("FileDescription", "")
            path = f.get("FinalPath", "")
            
            if title_file:
                files_str.append(f"{title_file}: {_clean_html(desc)} ({path})")
                log_query_to_blob(f"  • File {idx}: {title_file}")
        
        files_text = ", ".join(files_str)
        log_query_to_blob(f"  • Total files processed: {len(files_str)}")

        # Process Benchmark Codes
        benchmark_codes = [b.strip() for b in resource_json.get("BenchmarkCodes", "").split(",") if b.strip()]
        if benchmark_codes:
            log_query_to_blob(f"\n→ Benchmark codes: {', '.join(benchmark_codes)}")
        
        # Build text for embedding (including all lesson plan data)
        log_query_to_blob(f"\n→ Building text for embedding...")
        all_lesson_plan_text = " ".join(lesson_plan_data.values())
        
        text_for_embedding = (
            f"{title} {description} "
            f"Grade Levels: {resource_json.get('GradeLevelNames', '')} "
            f"Subject Areas: {resource_json.get('SubjectAreaNames', '')} "
            f"Type: {resource_json.get('PrimaryICT', '')} "
            f"Resource ID: {resource_id} "
            f"{benchmark_description_text} " 
            f"{extra_info_text} "
            f"{all_lesson_plan_text}"
        ).strip()
        
        log_query_to_blob(f"  • Embedding text length: {len(text_for_embedding)} chars")
        
        # Generate embedding
        log_query_to_blob(f"\n→ Generating embedding vector...")
        embedding = generate_embedding(text_for_embedding)

        # Ensure embedding is valid
        if isinstance(embedding, (list, tuple)):
            embedding = [float(x) if x is not None else 0.0 for x in embedding]
        else:
            log_query_to_blob(f"⚠ Warning: Invalid embedding type, using zero vector")
            embedding = [0.0] * 3072

        # Create document structure
        log_query_to_blob(f"\n→ Creating document structure...")
        document = {
            "id": resource_id,
            "BenchmarkCodes": ", ".join(benchmark_codes),
            "Title": title,
            "Description": description,
            "PrimaryICT": resource_json.get("PrimaryICT", ""),
            "Benchmark_Description": benchmark_description_text,
            "ResourceTypeId": str(resource_json.get("ResourceTypeId", "")).strip(),
            "PrimaryResourceICTId": str(resource_json.get("PrimaryResourceICTId", "")).strip(),
            "SpecialMaterialsNeeded": _clean_html(resource_json.get("SpecialMaterialsNeeded", "")),
            "Files": files_text,
            "BenchmarkIds": extra_info_text,
            "embedding": embedding,
            "GradeLevelNames": str(resource_json.get("GradeLevelNames", "")).strip(),
            "SubjectAreaNames": str(resource_json.get("SubjectAreaNames", "")).strip(),
            "IntendedAudienceNames": str(resource_json.get("IntendedAudienceNames", "")).strip(),
            "ResourceUrl": str(resource_json.get("ResourceUrl", "")).strip(),
            "PublishedDate": str(resource_json.get("PublishedDate", "")).strip(),
            "Accomodation": accommodation if accommodation else None,
            "Extensions": extensions if extensions else None,
            "FurtherRecommendations": further if further else None,
            "metadata": " | ".join(metadata_items) if metadata_items else None
        }

        # Add all lesson plan fields (set to None if not present)
        for field_name in UNIQUE_LESSON_PLAN_FIELDS:
            document[field_name] = lesson_plan_data.get(field_name, None)

        # Clean document - remove None and empty values
        original_count = len(document)
        document = {k: v for k, v in document.items() if v is not None and v != "" and v != []}
        removed_count = original_count - len(document)
        
        if removed_count > 0:
            log_query_to_blob(f"  • Removed {removed_count} empty fields")

        # Validate embedding
        if "embedding" not in document or not isinstance(document["embedding"], list):
            raise ValueError("Invalid embedding format")
        
        log_query_to_blob(f"\n✓ Document prepared successfully")
        log_query_to_blob(f"  • Total fields: {len(document)}")
        log_query_to_blob(f"  • Embedding dimensions: {len(document['embedding'])}")
        log_query_to_blob(f"{'='*60}\n")
        
        return document

    except Exception as e:
        log_query_to_blob(f"\n✗ ERROR preparing document: {str(e)}")
        print(f"✗ ERROR preparing document: {str(e)}")
        log_query_to_blob(f"→ Creating fallback document...")
        log_query_to_blob(f"{'='*60}\n")
        
        fallback_doc = {
            "id": resource_id if resource_id else "fallback",
            "BenchmarkIds": "",
            "Title": title if 'title' in locals() else "Resource",
            "PrimaryICT": "problem_solving",
            "text": str(resource_json)[:500],
            "embedding": [0.0] * 3072,
            "Files": ""
        }
        return fallback_doc


# -----------------------------
# Helper Function: Clean HTML
# -----------------------------
def _clean_html(text: str) -> str:
    """Remove HTML tags from text"""
    if not text:
        return ""
    clean = re.compile("<.*?>")
    return re.sub(clean, "", text).strip()