"""
Pydantic models for request and response schemas
"""
from pydantic import BaseModel, Field
from typing import List, Optional


class ChatRequest(BaseModel):
    resource_id: str = Field(..., description="Resource ID (2-7 digit number)")
    Session_ID: str = Field(..., description="Session identifier")
    User_ID: str = Field(..., description="User identifier")
    query: str = Field(..., description="Educational query")


class RecommendationRequest(BaseModel):
    User_ID: str = Field(..., description="User identifier")
    Session_ID: str = Field(..., description="Session identifier")
    resource_id: str = Field(..., description="Resource ID (2-7 digit number)")


class SidebarRequest(BaseModel):
    User_ID: str = Field(..., description="User identifier")


class PreviousHistoryRequest(BaseModel):
    User_ID: str = Field(..., description="User identifier")
    Session_ID: str = Field(..., description="Session identifier")
    resource_id: str = Field(..., description="Resource ID (2-7 digit number)")


class SessionFetchRequest(BaseModel):
    User_ID: str = Field(..., description="User identifier")
    resource_id: str = Field(..., description="Resource ID (2-7 digit number)")

class PreviousResponseItem(BaseModel):
    resource_id: str = Field(..., description="Resource ID")
    response_type: str = Field(..., description="Type of response")
    query: str = Field(..., description="User query")
    supporting_documents: List[str] = Field(default_factory=list, description="Supporting documents")
    benchmarks: str = Field(default="", description="Short benchmark codes")
    response: str = Field(..., description="AI response")
    timestamp: str = Field(..., description="ISO format timestamp")
    worksheet: str = Field(default="", description="Worksheet content")


class ChatResponse(BaseModel):
    User_ID: str = Field(..., description="User identifier")
    Session_ID: str = Field(..., description="Session identifier")
    resource_id: str = Field(..., description="Resource ID (2-7 digit number)")
    query: str = Field(..., description="Educational query")
    response_type: str = Field(..., description="Type of response: question-answer, letter, lesson plan, or plain text")
    supporting_documents: List[str] = Field(default_factory=list, description="List of supporting document filenames")
    benchmarks: str = Field(default="", description="Formatted benchmarks")
    response: str = Field(..., description="AI customization response")
    worksheet: str = Field(default="", description="Worksheet content (if any)")
    timestamp: str = Field(..., description="UTC timestamp (ISO format)")
    previous_response: List[PreviousResponseItem] = Field(default_factory=list, description="Previous responses in the last 30 minutes")


class RecommendationResponse(BaseModel):
    User_ID: str = Field(..., description="User identifier")
    Session_ID: str = Field(..., description="Session identifier")
    resource_id: str = Field(..., description="Resource ID (2-7 digit number)")
    recommendation_questions: List[str] = Field(default_factory=list, description="List of recommended questions")


class SessionResourceCombo(BaseModel):
    Session_ID: str = Field(..., description="Session identifier")
    resource_id: str = Field(..., description="Resource ID")


class ResourceTitleCombo(BaseModel):
    resource_id: str = Field(..., description="Resource ID")
    title: str = Field(..., description="Resource title")


class SidebarResponse(BaseModel):
    User_ID: str = Field(..., description="User identifier")
    session_resource_combinations: List[SessionResourceCombo] = Field(default_factory=list, description="List of session-resource combinations")
    resource_title_combinations: List[ResourceTitleCombo] = Field(default_factory=list, description="List of resource-title combinations")


class HistoryItem(BaseModel):
    query_text: str = Field(..., description="User query")
    response_type: Optional[str] = Field(None, description="Response type")
    supporting_documents: Optional[str] = Field(None, description="Supporting documents")
    benchmarks: Optional[str] = Field(None, description="Short benchmarks")
    response_text: str = Field(..., description="AI response")
    timestamp: Optional[str] = Field(None, description="UTC Timestamp")
    worksheet: Optional[str] = Field(None, description="Worksheet content")


class PreviousHistoryResponse(BaseModel):
    User_ID: str = Field(..., description="User identifier")
    Session_ID: str = Field(..., description="Session identifier")
    resource_id: str = Field(..., description="Resource ID")
    history: List[HistoryItem] = Field(default_factory=list, description="Chat history")


class SessionFetchResponse(BaseModel):
    Session_ID: str = Field(..., description="Session ID for this user and resource")