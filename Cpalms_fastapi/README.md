CPALMS AI Customization API
An AI-powered educational content customization platform for CPALMS (Collaboration Portal for Advancing Learning in Mathematics and Science) lesson plans.

📋 Table of Contents
Overview
Features
Architecture
Installation
Configuration
Running the Application
API Endpoints
Project Structure
Docker Deployment
Troubleshooting
🎯 Overview
The CPALMS AI Customization API is a FastAPI-based service that leverages Azure OpenAI to provide intelligent, context-aware educational content generation. It helps educators customize lesson plans, generate assessments, create worksheets, and provide personalized teaching materials aligned with Florida B.E.S.T. standards.

Scalability: The API is designed to handle concurrent load efficiently. Up to 200 users can simultaneously submit up to 3 questions each, ensuring robust performance for classroom and institutional use. The load is managed using FastAPI and Azure OpenAI, allowing high throughput and reliable response times even under heavy usage.

Key Capabilities
🤖 Intelligent Content Generation: AI-powered lesson plan customization using GPT-4
📚 Multi-Benchmark Support: Alignment with Florida B.E.S.T. standards across subjects
💾 Session Management: In-memory conversation history with 30-minute session window
📄 Document Processing: Automatic extraction and formatting from Azure Blob Storage
📝 Worksheet Generation: Auto-generation of downloadable educational materials
📊 Assessment Creation: Quiz and test generation with answer keys
🔍 Smart Search: Azure Cognitive Search integration for lesson content retrieval
🔄 Conversation Context: Multi-turn conversations with full history tracking
✨ Features
Core Features
✅ AI-Powered Chat Interface: Conversational AI for educational content
✅ Benchmark-Based Search: Find content aligned with standards
✅ Conversation History: Smart session management (30-minute window)
✅ Multi-Format Support: Worksheets, assessments, letters, lesson plans
✅ Document Extraction: Automatic parsing of PDFs and documents
✅ HTML Formatting: Auto-formatting for web display
✅ Parallel Processing: Concurrent classification and field extraction
Educational Features
📚 Lesson Plan Enhancement: Modify and customize existing lesson plans
📝 Assessment Generation: Create quizzes, tests, exit tickets, and formative assessments
📄 Worksheet Creation: Auto-generate downloadable worksheets in various formats
💬 Parent Communication: Generate parent letters explaining learning goals
♿ Accessibility Support: UDL (Universal Design for Learning) strategies and ELL support
🎯 Differentiation: Extension activities for advanced learners and scaffolding for struggling students
🎮 Gamification: Create educational games and interactive activities
📊 MEA Lessons: Convert standard lessons to Model Eliciting Activities (MEA)
🎨 Creative Activities: Music, movement, and hands-on learning experiences
🏗️ Architecture
Tech Stack
Framework: FastAPI 0.115.0
AI/ML: Azure OpenAI (GPT-4 deployments)
Search: Azure Cognitive Search
Database: Azure SQL Server
Storage: Azure Blob Storage
Language: Python 3.10
Data Flow
User Query → FastAPI receives educational query with resource ID
Validation → Query validated for educational content using fuzzy matching
Session Management → Session retrieved/created with 30-minute window
Parallel Processing → Query classification and field extraction run concurrently
Search & Retrieval → Azure Cognitive Search fetches relevant documents
AI Generation → Azure OpenAI generates customized response with benchmarks
Response Formatting → HTML formatting, worksheet extraction, benchmark formatting
Logging → Dual logging to Azure SQL and Blob Storage
Response → Formatted response with history, benchmarks, and supporting documents
🚀 Installation
Prerequisites
Python 3.10
Azure Account with OpenAI, Cognitive Search, SQL Database, Blob Storage
ODBC Driver 18 for SQL Server
Local Installation
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
Install ODBC Driver
Windows: Download from Microsoft ODBC Driver

macOS:

brew install unixodbc
brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
brew install msodbcsql18 mssql-tools18
Ubuntu/Debian:

curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
apt-get update
ACCEPT_EULA=Y apt-get install -y msodbcsql18

⚙️ Configuration
Most Azure services use Managed Identity for authentication — no API keys or sensitive credentials are stored in the .env file. (Exception: SQL Database uses username and password credentials.)

Create a .env file:
```env
# Azure Cognitive Search — auth via Managed Identity, no key needed
AZURE_SEARCH_ENDPOINT=https://your-search.search.windows.net
AZURE_SEARCH_INDEX=main-index
AZURE_SEARCH_INDEX_1=documents-index

# Azure OpenAI — auth via Managed Identity, no API key needed
# Deployment 1
OPENAI_API_VERSION=2024-02-15-preview
OPENAI_API_BASE=https://your-openai-1.openai.azure.com
OPENAI_DEPLOYMENT_NAME=gpt-4-deployment-1

# Deployment 2
OPENAI_API_VERSION_2=2024-02-15-preview
OPENAI_API_BASE_2=https://your-openai-2.openai.azure.com
OPENAI_DEPLOYMENT_NAME_2=gpt-4-deployment-2

# Deployment 3
OPENAI_API_VERSION_3=2024-02-15-preview
OPENAI_API_BASE_3=https://your-openai-3.openai.azure.com
OPENAI_DEPLOYMENT_NAME_3=gpt-4-deployment-3

# Deployment 4
OPENAI_API_VERSION_4=2024-02-15-preview
OPENAI_API_BASE_4=https://your-openai-4.openai.azure.com
OPENAI_DEPLOYMENT_NAME_4=gpt-4-deployment-4

# Azure SQL
AZURE_SQL_CONNECTION_STRING=Driver={ODBC Driver 18 for SQL Server};Server=...
SQL_SERVER=your-server.database.windows.net
SQL_DATABASE=your-db
SQL_USERNAME=username
SQL_PASSWORD=password

# Azure Blob — auth via Managed Identity, no connection string needed
AZURE_BLOB_CONTAINER_NAME=your-container-name
AZURE_STORAGE_ACCOUNT_NAME=your-storage-account-name

AZURE_BLOB_SAS_TOKEN=your-sas-token

🎮 Running the Application
Development Mode
uvicorn app:app --reload --host 0.0.0.0 --port 8000
Access
API Docs: http://localhost:8000/docs
Alternative Docs: http://localhost:8000/redoc
📡 API Endpoints
🔐 Authentication - All API endpoints require an API key passed via the X-API-Key header - Requests without a valid key return 401 Unauthorized - API keys are validated against a server-side allowlist - Authentication is enforced using FastAPI security dependencies - API keys are checked on every request (no session-based auth) - Keys are not exposed in responses or logs - Authentication occurs before any AI or database processing - Designed for secure server-to-server and frontend-to-backend usage

1. POST /chat - Generate AI Customization
Generate customized educational content based on lesson plans.

Request Body:

{
  "resource_id": "12345",
  "Session_ID": "session-abc-123",
  "User_ID": "user-456",
  "query": "Create 5 assessment questions for this lesson"
}
Response:

{
  "User_ID": "user-456",
  "Session_ID": "session-abc-123",
  "resource_id": "12345",
  "query": "Create 5 assessment questions for this lesson",
  "response_type": "question-answer",
  "supporting_documents": ["worksheet.pdf", "lesson_plan.pdf"],
  "benchmarks": "MA.K.NSO.1.1, MA.K.NSO.1.2",
  "response": "Assessment Questions...",
  "worksheet": "Question 1: ...\nQuestion 2: ...",
  "timestamp": "2025-02-06T10:30:00Z",
  "previous_response": [
    {
      "resource_id": "12345",
      "response_type": "plain text",
      "query": "Previous query",
      "response": "Previous response",
      "timestamp": "2025-02-06T10:25:00Z"
    }
  ]
}
Features: - ✅ Validates educational content - ✅ Maintains 30-minute conversation history - ✅ Supports multi-turn conversations - ✅ Extracts worksheets automatically - ✅ Formats benchmarks with resource IDs - ✅ Provides HTML-formatted responses

2. POST /recommendation - Get Recommended Questions
Generate recommended questions for a specific lesson.

Request Body:

{
  "User_ID": "user-456",
  "Session_ID": "session-abc-123",
  "resource_id": "12345"
}
Response:

{
  "User_ID": "user-456",
  "Session_ID": "session-abc-123",
  "resource_id": "12345",
  "recommendation_questions": [
    "Create a quiz with 5 questions for this lesson",
    "Generate UDL strategies for students with special needs",
    "Write a parent letter explaining what we are studying",
    "Design extension activities for advanced learners"
  ]
}
Use Cases: - Provide suggested queries to users - Help educators discover customization options - Guide users on what they can ask

3. POST /sidebar - Get User Sessions
Retrieve all sessions and resources for a user.

Request Body:

{
  "User_ID": "user-456"
}
Response:

{
  "User_ID": "user-456",
  "session_resource_combinations": [
    {
      "Session_ID": "session-abc-123",
      "resource_id": "12345"
    },
    {
      "Session_ID": "session-def-456",
      "resource_id": "67890"
    }
  ],
  "resource_title_combinations": [
    {
      "resource_id": "12345",
      "title": "Introduction to Counting for Kindergarten"
    },
    {
      "resource_id": "67890",
      "title": "Advanced Multiplication Strategies"
    }
  ]
}
Use Cases: - Display user's chat history in sidebar - Show all resources user has worked with - Enable navigation between sessions

4. POST /previous_history - Get Chat History
Retrieve full conversation history for a session.

Request Body:

{
  "User_ID": "user-456",
  "Session_ID": "session-abc-123",
  "resource_id": "12345"
}
Response:

{
  "User_ID": "user-456",
  "Session_ID": "session-abc-123",
  "resource_id": "12345",
  "history": [
    {
      "query_text": "Create 5 assessment questions",
      "response_text": "Assessment Questions...",
      "timestamp": "2025-02-06T10:30:00Z",
      "response_type": "question-answer",
      "supporting_documents": "worksheet.pdf,lesson_plan.pdf",
      "benchmarks": "MA.K.NSO.1.1",
      "worksheet": "Question 1: ..."
    }
  ]
}
📁 Project Structure
cpalms-ai-api/
├── app.py                    # FastAPI application
├── config.py                 # Configuration
├── db_pool.py                # Database connection pooling
├── models.py                 # Pydantic models
├── utils.py                  # Utilities & session management
├── prompts.py                # AI prompt templates
├── validation.py             # Query validation
├── recommendation.py         # Recommendation generation
├── logs.py                   # Blob logging
├── logs_sql.py               # SQL logging
├── fields_description.json   # Field schema
├── question.txt              # Sample questions
├── requirements.txt          # Dependencies
├── Dockerfile                # Docker config
├── README.md                 # Documentation
└── __pycache__/              # Compiled Python files
File Descriptions
File	Purpose
app.py	Main FastAPI application with 4 API endpoints
config.py	Azure service clients and environment configuration
db_pool.py	Database connection pooling for efficient SQL access
models.py	Pydantic schemas for type-safe API contracts
utils.py	Session management, search, OpenAI calls, formatting
prompts.py	System prompts for different query types
validation.py	Educational query validation with fuzzy matching
recommendation.py	Generate contextual question suggestions
logs.py	Append-based logging to Azure Blob Storage
logs_sql.py	Structured logging to Azure SQL Database
fields_description.json	Defines 60+ lesson plan field types
question.txt	27 example questions for recommendation reference
requirements.txt	Python dependencies for the project
Dockerfile	Docker configuration for containerized deployment
README.md	Project documentation and usage instructions
__pycache__/	Compiled Python files (auto-generated, not source code)
🐳 Docker Deployment
Build Image
docker build -t cpalms-ai-api:latest .
Run Container
docker run -d \
  --name cpalms-api \
  -p 8000:8000 \
  --env-file .env \
  cpalms-ai-api:latest
💡 Usage Examples
Example 1: Create Assessment Questions
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "resource_id": "12345",
    "Session_ID": "session-001",
    "User_ID": "teacher-123",
    "query": "Create 5 multiple choice questions for this kindergarten counting lesson"
  }'
Example 2: Generate UDL Strategies
```bash curl -X POST "http://localhost:8000/chat" \ -H "Content-Type: application/json" \ -d '{ "resource_id": "12345", "Session_ID": "session-001", "User_ID": "teacher-123", "query": "I have two special needs students. Can you integrate UDL strategies?" }'