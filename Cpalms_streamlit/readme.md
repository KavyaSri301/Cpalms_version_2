# CPALMS AI Customization Generator

An AI-powered educational content generation system for CPALMS (Florida's learning standards repository). This Streamlit application helps educators create customized lesson plans, assessments, worksheets, and teaching materials aligned with Florida state benchmarks.

## 🎯 Features

- **AI-Powered Content Generation**: Generate lesson plans, assessments, worksheets, and activities
- **Benchmark Alignment**: Automatically align content with Florida state standards
- **Smart Query Classification**: Automatically categorizes queries as normal, reference-based, or unrelated
- **Document Generation**: Create downloadable DOCX and PDF files
- **Recommended Questions**: Get AI-suggested follow-up questions based on lesson content
- **Query History**: Track and revisit previous customizations
- **Multi-Source Integration**: Combines data from Azure AI Search indexes and blob storage

## 📋 Prerequisites

- Python 3.10 or higher
- Azure subscription with:
  - Azure OpenAI Service (3 deployments)
  - Azure Cognitive Search (2 indexes)
  - Azure Blob Storage
  - Azure SQL Database
- Required API keys and connection strings (see Configuration section)

## 🚀 Installation

### 1. Clone or Download the Repository

```bash
mkdir cpalms-ai-generator
cd cpalms-ai-generator
```

### 2. Install Required Dependencies

```bash
pip install streamlit
pip install azure-search-documents
pip install azure-core
pip install openai
pip install python-dotenv
pip install pyodbc
pip install rapidfuzz
pip install python-docx
pip install tiktoken
```

Or use requirements.txt (create this file):

```bash
pip install -r requirements.txt
```

**requirements.txt:**
```
streamlit>=1.28.0
azure-search-documents>=11.4.0
azure-core>=1.29.0
openai>=1.3.0
python-dotenv>=1.0.0
pyodbc>=4.0.39
rapidfuzz>=3.5.0
python-docx>=0.8.11
tiktoken>=0.5.1
```

### 3. Set Up Environment Variables

Create a `.env` file in the project root directory:
Most Azure services use Managed Identity for authentication — no API keys or sensitive credentials are stored in the .env file. (Exception: SQL Database uses username and password credentials.)

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

# Azure Blob Storage
AZURE_BLOB_SAS_TOKEN=your-sas-token
```

## 📁 Project Structure

```
cpalms-ai-generator/
│
├── main.py                      # Main Streamlit application
├── prompts.py                   # AI prompt generation functions
├── utils.py                     # Utility functions
├── dataformatting.py            # Data formatting utilities (required)
├── convert_to_pdf.py            # PDF generation (required)
├── recommendation.py            # Question recommendation logic (required)
├── docx_formatting.py           # DOCX file generation (required)
├── validation.py                # Query validation (required)
├── logs.py                      # Logging to Azure Blob (required)
├── fields_description.json      # Field descriptions for AI (required)
├── .env                         # Environment variables (create this)
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```

## 🔧 Configuration

### Azure AI Search Indexes

**Main Index (AZURE_SEARCH_INDEX):**
- Contains lesson plan metadata and content
- Required fields: id, BenchmarkCodes, Benchmark_Description, Title, Description, GradeLevelNames, etc.

**Documents Index (AZURE_SEARCH_INDEX_1):**
- Contains chunked document content
- Required fields: chunk, metadata_storage_path, metadata_storage_name

### Azure OpenAI Deployments

The application uses 3 separate OpenAI deployments for load balancing and different tasks:
- **Deployment 1**: Primary content generation
- **Deployment 2**: Query classification and field extraction
- **Deployment 3**: Secondary content generation and fallback

## Usage

### Starting the Application

```bash
streamlit run main.py
```

The application will open in your default web browser at `http://localhost:8501`

### Using the Application

1. **Enter Resource ID**: Input a 2-7 digit CPALMS resource identifier (e.g., 176009)
2. **Review Recommended Questions**: AI-generated suggestions appear automatically
3. **Enter Your Query**: Type your educational content request
4. **Generate Content**: Click "🚀 Generate AI Customization"
5. **Download Results**: Export as DOCX or PDF

### Example Queries

- "Generate 10 assessment questions for this lesson"
- "Create an exit ticket for this benchmark"
- "Develop UDL recommendations for diverse learners"
- "Add extension activities for advanced students"
- "Create a parent communication letter about this lesson"
- "Refer to benchmark MA.K.NSO.1.1 and generate practice worksheets"


## 📸 Sample Output

### Input Example
**Resource ID:** 30777  
**Query:** "Can you suggest ways to introduce the concept of comparing characteristics, like taste and smell, in a fun and interactive way for young students?"

### Output Preview

The application generates:

1. **Recommended Questions** (AI-generated suggestions):
   - "How can this lesson be adjusted for students who need extra help understanding or participating in the decision-making process?"
   - "What key skills or ideas should students have before starting this activity to help them succeed?"

2. **Supporting Benchmarks** (automatically retrieved):
   - SC.K.L.14.1-1563
   - ELA.K12.EE.1.1-15201
   - MA.K.NSO.1.1-15232
   - MA.K.NSO.1.4-15235

3. **Supporting Documents** (from Azure Blob Storage):
   - cupcakesletter1_2021.docx
   - cupcakesdataset1_2021.docx
   - vegitablesincupcakesrubric_2021.docx

4. **AI-Generated Content** (customized response):

```markdown
Here are fun, interactive ways to introduce comparing characteristics like 
taste and smell for kindergarteners. These ideas connect directly to the 
cupcake‑and‑vegetable lesson, helping children explore their senses...

## Sensory Stations
Set up small "exploration spots" around the room. Each station focuses on 
one sense so children can compare how things are alike and different.

### Smell Station
Place cotton balls in small cups or baggies. Add safe, familiar scents like 
vanilla extract, lemon, peppermint, or cocoa powder. Children smell two items 
and talk about which smell is stronger, sweeter, or more pleasant.

### Taste Station
Provide tiny pieces of simple foods, such as apple slices, cucumber, a pretzel, 
or a small cracker. Invite children to taste two foods and decide which is 
sweeter, saltier, or crunchier.

## Partner Comparisons
Have children work in pairs. One child smells or "pretends to taste" an item 
and describes it. The partner chooses another item to compare and says whether 
it smells stronger, sweeter, or milder.

## Graphing Favorites
After exploring scents or tastes, create a simple class graph. Ask: "Which 
smell did you like best?" Children place a sticky note above their choice. 
Then compare which has more and which has fewer...
```

5. **Download Options**:
   - DOCX format with enhanced formatting
   - PDF format with structured layout



## 📚 Module Descriptions

### main.py
Main Streamlit application handling:
- UI rendering and user interactions
- Azure Search queries
- OpenAI API orchestration
- Session state management
- File generation and downloads

### prompts.py
Contains all AI prompt generation functions:
- `get_fields_from_index()`: Extracts required fields based on query
- `classify_query()`: Classifies queries as normal/reference/unrelated
- `generate_creative_response()`: Creates prompts for general queries
- `generate_creative_response_for_reference()`: Creates prompts for benchmark-specific queries
- `generate_summary_for_primary_benchmarks()`: Summarizes lesson content

### utils.py
Utility functions for:
- Session state management
- Azure OpenAI API calls with retry logic
- Data formatting and cleaning
- Benchmark processing
- URL generation
- History management

## 🔒 Security Notes

- Never commit `.env` file to version control
- Use Azure Key Vault for production deployments
- Rotate API keys regularly
- Implement proper access controls on Azure resources
- Use managed identities where possible

## 🚦 Required Supporting Files

The following files must be present for the application to work (not included in this restructuring):

- `dataformatting.py`: Markdown and HTML conversion utilities
- `convert_to_pdf.py`: PDF generation functions
- `recommendation.py`: AI question recommendation logic
- `docx_formatting.py`: DOCX file creation and formatting
- `validation.py`: Query validation functions
- `logs.py`: Azure Blob Storage logging
- `fields_description.json`: JSON schema for CPALMS fields

Ensure these files are present in your project directory alongside main.py.

## 🎨 UI Customization

The application includes custom CSS styling for:
- Gradient header with CPALMS branding
- Styled input containers
- Formatted AI output boxes
- Recommended question buttons
- Download buttons
