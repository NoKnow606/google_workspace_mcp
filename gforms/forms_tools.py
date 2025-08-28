"""
Google Forms MCP Tools

This module provides MCP tools for interacting with Google Forms API.
"""

import logging
import asyncio
from typing import Optional, Dict, Any

from mcp import types
from fastmcp import Context

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors

logger = logging.getLogger(__name__)


@server.tool
@require_google_service("forms", "forms")
@handle_http_errors("create_form")
async def create_form(
    service,
    ctx: Context,
    title: str,
    user_google_email: Optional[str] = None,
    description: Optional[str] = None,
    document_title: Optional[str] = None
):
    """
    <description>Creates a new blank Google Form with specified title and optional description. Form starts empty without questions - use additional tools to add form fields.</description>
    
    <use_case>Creating surveys, feedback forms, event registrations, or data collection forms from scratch. Ideal when you need a clean slate to build custom questionnaires.</use_case>
    
    <limitation>Only creates the form container - does not add questions, validation rules, or response settings. Cannot clone existing forms or import questions from templates.</limitation>
    
    <failure_cases>Fails if user lacks Google Forms creation permissions, if title exceeds character limits (~100 chars), or if Google Drive storage quota is exceeded.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        title (str): The title of the form.
        description (Optional[str]): The description of the form.
        document_title (Optional[str]): The document title (shown in browser tab).

    Returns:
        str: Confirmation message with form ID and edit URL.
    """
    logger.info(f"[create_form] Invoked. Email: '{user_google_email}', Title: {title}")

    form_body: Dict[str, Any] = {
        "info": {
            "title": title
        }
    }

    if description:
        form_body["info"]["description"] = description

    if document_title:
        form_body["info"]["document_title"] = document_title

    created_form = await asyncio.to_thread(
        service.forms().create(body=form_body).execute
    )

    form_id = created_form.get("formId")
    edit_url = f"https://docs.google.com/forms/d/{form_id}/edit"
    responder_url = created_form.get("responderUri", f"https://docs.google.com/forms/d/{form_id}/viewform")

    confirmation_message = f"Successfully created form '{created_form.get('info', {}).get('title', title)}' for {user_google_email}. Form ID: {form_id}. Edit URL: {edit_url}. Responder URL: {responder_url}"
    logger.info(f"Form created successfully for {user_google_email}. ID: {form_id}")
    return confirmation_message


@server.tool
@require_google_service("forms", "forms")
@handle_http_errors("get_form")
async def get_form(
    service,
    ctx: Context,
    form_id: str,
    user_google_email: Optional[str] = None
):
    """
    <description>Retrieves complete form structure including title, description, all questions, and response settings. Shows form metadata and question types but not actual response data.</description>
    
    <use_case>Inspecting existing forms before modification, understanding form structure for response analysis, or documenting form configurations for compliance audits.</use_case>
    
    <limitation>Does not return response data or analytics - only form structure. Cannot retrieve deleted forms or forms without proper access permissions.</limitation>
    
    <failure_cases>Fails with invalid form IDs, forms the user cannot access due to sharing restrictions, or forms that have been permanently deleted by the owner.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        form_id (str): The ID of the form to retrieve.

    Returns:
        str: Form details including title, description, questions, and URLs.
    """
    logger.info(f"[get_form] Invoked. Email: '{user_google_email}', Form ID: {form_id}")

    form = await asyncio.to_thread(
        service.forms().get(formId=form_id).execute
    )

    form_info = form.get("info", {})
    title = form_info.get("title", "No Title")
    description = form_info.get("description", "No Description")
    document_title = form_info.get("documentTitle", title)

    edit_url = f"https://docs.google.com/forms/d/{form_id}/edit"
    responder_url = form.get("responderUri", f"https://docs.google.com/forms/d/{form_id}/viewform")

    items = form.get("items", [])
    questions_summary = []
    for i, item in enumerate(items, 1):
        item_title = item.get("title", f"Question {i}")
        item_type = item.get("questionItem", {}).get("question", {}).get("required", False)
        required_text = " (Required)" if item_type else ""
        questions_summary.append(f"  {i}. {item_title}{required_text}")

    questions_text = "\n".join(questions_summary) if questions_summary else "  No questions found"

    result = f"""Form Details for {user_google_email}:
- Title: "{title}"
- Description: "{description}"
- Document Title: "{document_title}"
- Form ID: {form_id}
- Edit URL: {edit_url}
- Responder URL: {responder_url}
- Questions ({len(items)} total):
{questions_text}"""

    logger.info(f"Successfully retrieved form for {user_google_email}. ID: {form_id}")
    return result


@server.tool
@require_google_service("forms", "forms")
@handle_http_errors("set_publish_settings")
async def set_publish_settings(
    service,
    ctx: Context,
    form_id: str,
    user_google_email: Optional[str] = None,
    publish_as_template: bool = False,
    require_authentication: bool = False
):
    """
    <description>Configures form visibility and access controls, including template publishing and authentication requirements. Controls who can view and submit responses to the form.</description>
    
    <use_case>Setting up public surveys for anonymous feedback, creating secure forms requiring Google sign-in, or publishing template forms for organization-wide reuse.</use_case>
    
    <limitation>Cannot control fine-grained permissions or set custom access lists. Authentication requirement applies to all users - cannot mix authenticated and anonymous access.</limitation>
    
    <failure_cases>Fails if user lacks form ownership permissions, if form is actively collecting responses that would be affected by access changes, or on Google Workspace domains with restricted publishing policies.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        form_id (str): The ID of the form to update publish settings for.
        publish_as_template (bool): Whether to publish as a template. Defaults to False.
        require_authentication (bool): Whether to require authentication to view/submit. Defaults to False.

    Returns:
        str: Confirmation message of the successful publish settings update.
    """
    logger.info(f"[set_publish_settings] Invoked. Email: '{user_google_email}', Form ID: {form_id}")

    settings_body = {
        "publishAsTemplate": publish_as_template,
        "requireAuthentication": require_authentication
    }

    await asyncio.to_thread(
        service.forms().setPublishSettings(formId=form_id, body=settings_body).execute
    )

    confirmation_message = f"Successfully updated publish settings for form {form_id} for {user_google_email}. Publish as template: {publish_as_template}, Require authentication: {require_authentication}"
    logger.info(f"Publish settings updated successfully for {user_google_email}. Form ID: {form_id}")
    return confirmation_message


@server.tool
@require_google_service("forms", "forms")
@handle_http_errors("get_form_response")
async def get_form_response(
    service,
    ctx: Context,
    form_id: str,
    response_id: str,
    user_google_email: Optional[str] = None
):
    """
    <description>Retrieves a single form response with all submitted answers, timestamps, and metadata. Shows individual respondent data for detailed analysis or follow-up.</description>
    
    <use_case>Investigating specific survey responses, analyzing individual customer feedback, or extracting detailed data for case studies and qualitative research.</use_case>
    
    <limitation>Returns only one response at a time - not efficient for bulk analysis. Cannot retrieve responses from anonymous forms where response IDs are not tracked.</limitation>
    
    <failure_cases>Fails with invalid response IDs, responses from forms the user cannot access, or responses that have been deleted by form owners.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        form_id (str): The ID of the form.
        response_id (str): The ID of the response to retrieve.

    Returns:
        str: Response details including answers and metadata.
    """
    logger.info(f"[get_form_response] Invoked. Email: '{user_google_email}', Form ID: {form_id}, Response ID: {response_id}")

    response = await asyncio.to_thread(
        service.forms().responses().get(formId=form_id, responseId=response_id).execute
    )

    response_id = response.get("responseId", "Unknown")
    create_time = response.get("createTime", "Unknown")
    last_submitted_time = response.get("lastSubmittedTime", "Unknown")

    answers = response.get("answers", {})
    answer_details = []
    for question_id, answer_data in answers.items():
        question_response = answer_data.get("textAnswers", {}).get("answers", [])
        if question_response:
            answer_text = ", ".join([ans.get("value", "") for ans in question_response])
            answer_details.append(f"  Question ID {question_id}: {answer_text}")
        else:
            answer_details.append(f"  Question ID {question_id}: No answer provided")

    answers_text = "\n".join(answer_details) if answer_details else "  No answers found"

    result = f"""Form Response Details for {user_google_email}:
- Form ID: {form_id}
- Response ID: {response_id}
- Created: {create_time}
- Last Submitted: {last_submitted_time}
- Answers:
{answers_text}"""

    logger.info(f"Successfully retrieved response for {user_google_email}. Response ID: {response_id}")
    return result


@server.tool
@require_google_service("forms", "forms")
@handle_http_errors("list_form_responses")
async def list_form_responses(
    service,
    ctx: Context,
    form_id: str,
    user_google_email: Optional[str] = None,
    page_size: int = 10,
    page_token: Optional[str] = None
):
    """
    <description>Lists all form responses with summary metadata including submission timestamps and response counts. Provides pagination for forms with many responses (>10-100 responses).</description>
    
    <use_case>Getting overview of form response volume, identifying recent submissions for follow-up, or preparing for bulk response analysis with response IDs.</use_case>
    
    <limitation>Shows only response metadata, not actual answers - use get_form_response for detailed content. Limited to 100 responses per page requiring pagination for larger datasets.</limitation>
    
    <failure_cases>Fails on forms without response collection enabled, forms the user cannot access, or when API rate limits are exceeded with high-volume polling.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        form_id (str): The ID of the form.
        page_size (int): Maximum number of responses to return. Defaults to 10.
        page_token (Optional[str]): Token for retrieving next page of results.

    Returns:
        str: List of responses with basic details and pagination info.
    """
    logger.info(f"[list_form_responses] Invoked. Email: '{user_google_email}', Form ID: {form_id}")

    params = {
        "formId": form_id,
        "pageSize": page_size
    }
    if page_token:
        params["pageToken"] = page_token

    responses_result = await asyncio.to_thread(
        service.forms().responses().list(**params).execute
    )

    responses = responses_result.get("responses", [])
    next_page_token = responses_result.get("nextPageToken")

    if not responses:
        return f"No responses found for form {form_id} for {user_google_email}."

    response_details = []
    for i, response in enumerate(responses, 1):
        response_id = response.get("responseId", "Unknown")
        create_time = response.get("createTime", "Unknown")
        last_submitted_time = response.get("lastSubmittedTime", "Unknown")

        answers_count = len(response.get("answers", {}))
        response_details.append(
            f"  {i}. Response ID: {response_id} | Created: {create_time} | Last Submitted: {last_submitted_time} | Answers: {answers_count}"
        )

    pagination_info = f"\nNext page token: {next_page_token}" if next_page_token else "\nNo more pages."

    result = f"""Form Responses for {user_google_email}:
- Form ID: {form_id}
- Total responses returned: {len(responses)}
- Responses:
{chr(10).join(response_details)}{pagination_info}"""

    logger.info(f"Successfully retrieved {len(responses)} responses for {user_google_email}. Form ID: {form_id}")
    return result