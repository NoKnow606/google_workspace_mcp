"""
Google Gmail MCP Tools

This module provides MCP tools for interacting with the Gmail API.
"""

import logging
import asyncio
import base64
from typing import Optional, List, Dict, Literal
from pydantic import BaseModel, Field

from email.mime.text import MIMEText

from mcp import types
from fastmcp import Context
from fastapi import Body

from auth.service_decorator import require_google_service
from core.utils import handle_http_errors
from core.server import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_LABELS_SCOPE,
    server,
)

logger = logging.getLogger(__name__)


# Response Models
class GmailMessageRef(BaseModel):
    """Reference to a Gmail message with ID and web link."""
    message_id: str = Field(..., description="Unique Gmail message ID")
    thread_id: str = Field(..., description="Gmail thread ID this message belongs to")
    message_url: str = Field(..., description="Gmail web interface URL for the message")
    thread_url: str = Field(..., description="Gmail web interface URL for the thread")


class SearchGmailMessagesResponse(BaseModel):
    """Response for Gmail message search."""
    query: str = Field(..., description="The search query used")
    total_found: int = Field(..., description="Number of messages found")
    messages: List[GmailMessageRef] = Field(default_factory=list, description="List of message references")


class GmailMessageContent(BaseModel):
    """Complete Gmail message content."""
    message_id: str = Field(..., description="Unique Gmail message ID")
    subject: str = Field(..., description="Email subject")
    sender: str = Field(..., description="Email sender (From field)")
    body: Optional[str] = Field(None, description="Plain text email body")
    web_url: str = Field(..., description="Gmail web interface URL")


class BatchGmailMessagesResponse(BaseModel):
    """Response for batch message retrieval."""
    total_requested: int = Field(..., description="Total number of messages requested")
    total_retrieved: int = Field(..., description="Number of messages successfully retrieved")
    messages: List[GmailMessageContent] = Field(default_factory=list, description="List of message contents")
    errors: List[Dict[str, str]] = Field(default_factory=list, description="List of errors encountered")


class SendGmailMessageResponse(BaseModel):
    """Response for sending a Gmail message."""
    success: bool = Field(..., description="Whether the email was sent successfully")
    message_id: str = Field(..., description="Gmail message ID of the sent email")
    message: str = Field(..., description="Human-readable confirmation message")


class DraftGmailMessageResponse(BaseModel):
    """Response for creating a Gmail draft."""
    success: bool = Field(..., description="Whether the draft was created successfully")
    draft_id: str = Field(..., description="Gmail draft ID")
    message: str = Field(..., description="Human-readable confirmation message")


class GmailThreadMessage(BaseModel):
    """A message within a Gmail thread."""
    message_number: int = Field(..., description="Sequential number of the message in the thread")
    sender: str = Field(..., description="Email sender (From field)")
    date: str = Field(..., description="Date the message was sent")
    subject: Optional[str] = Field(None, description="Message subject (if different from thread subject)")
    body: Optional[str] = Field(None, description="Plain text email body")


class GmailThreadContent(BaseModel):
    """Complete Gmail thread content."""
    thread_id: str = Field(..., description="Unique Gmail thread ID")
    subject: str = Field(..., description="Thread subject")
    message_count: int = Field(..., description="Number of messages in the thread")
    messages: List[GmailThreadMessage] = Field(default_factory=list, description="List of messages in the thread")


class GmailLabel(BaseModel):
    """Gmail label information."""
    label_id: str = Field(..., description="Unique Gmail label ID")
    name: str = Field(..., description="Label name")
    label_type: str = Field(..., description="Label type (system or user)")


class ListGmailLabelsResponse(BaseModel):
    """Response for listing Gmail labels."""
    total_labels: int = Field(..., description="Total number of labels")
    system_labels: List[GmailLabel] = Field(default_factory=list, description="System labels")
    user_labels: List[GmailLabel] = Field(default_factory=list, description="User-created labels")


class ManageGmailLabelResponse(BaseModel):
    """Response for managing Gmail labels."""
    success: bool = Field(..., description="Whether the operation was successful")
    action: str = Field(..., description="Action performed (create/update/delete)")
    label_id: Optional[str] = Field(None, description="Label ID (for create and update)")
    label_name: Optional[str] = Field(None, description="Label name")
    message: str = Field(..., description="Human-readable confirmation message")


class ModifyGmailMessageLabelsResponse(BaseModel):
    """Response for modifying message labels."""
    success: bool = Field(..., description="Whether the operation was successful")
    message_id: str = Field(..., description="Gmail message ID")
    added_labels: List[str] = Field(default_factory=list, description="Label IDs that were added")
    removed_labels: List[str] = Field(default_factory=list, description="Label IDs that were removed")
    message: str = Field(..., description="Human-readable confirmation message")


def _extract_message_body(payload):
    """
    Helper function to extract plain text body from a Gmail message payload.

    Args:
        payload (dict): The message payload from Gmail API

    Returns:
        str: The plain text body content, or empty string if not found
    """
    body_data = ""
    parts = [payload] if "parts" not in payload else payload.get("parts", [])

    part_queue = list(parts)  # Use a queue for BFS traversal of parts
    while part_queue:
        part = part_queue.pop(0)
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            data = base64.urlsafe_b64decode(part["body"]["data"])
            body_data = data.decode("utf-8", errors="ignore")
            break  # Found plain text body
        elif part.get("mimeType", "").startswith("multipart/") and "parts" in part:
            part_queue.extend(part.get("parts", []))  # Add sub-parts to the queue

    # If no plain text found, check the main payload body if it exists
    if (
        not body_data
        and payload.get("mimeType") == "text/plain"
        and payload.get("body", {}).get("data")
    ):
        data = base64.urlsafe_b64decode(payload["body"]["data"])
        body_data = data.decode("utf-8", errors="ignore")

    return body_data


def _extract_headers(payload: dict, header_names: List[str]) -> Dict[str, str]:
    """
    Extract specified headers from a Gmail message payload.

    Args:
        payload: The message payload from Gmail API
        header_names: List of header names to extract

    Returns:
        Dict mapping header names to their values
    """
    headers = {}
    for header in payload.get("headers", []):
        if header["name"] in header_names:
            headers[header["name"]] = header["value"]
    return headers


def _generate_gmail_web_url(item_id: str, account_index: int = 0):
    """
    Generate Gmail web interface URL for a message or thread ID.
    Uses #all to access messages from any Gmail folder/label (not just inbox).

    Args:
        item_id: Gmail message ID or thread ID
        account_index: Google account index (default 0 for primary account)

    Returns:
        Gmail web interface URL that opens the message/thread in Gmail web interface
    """
    return f"https://mail.google.com/mail/u/{account_index}/#all/{item_id}"


def _format_gmail_search_response(messages: list, query: str) -> SearchGmailMessagesResponse:
    """Format Gmail search results as a structured response."""
    message_refs = []
    for msg in messages:
        message_url = _generate_gmail_web_url(msg["id"])
        thread_url = _generate_gmail_web_url(msg["threadId"])

        message_refs.append(GmailMessageRef(
            message_id=msg["id"],
            thread_id=msg["threadId"],
            message_url=message_url,
            thread_url=thread_url
        ))

    return SearchGmailMessagesResponse(
        query=query,
        total_found=len(messages),
        messages=message_refs
    )


@server.tool
@require_google_service("gmail", "gmail_read")
@handle_http_errors("search_gmail_messages")
async def search_gmail_messages(
    service, ctx: Context, query: str, user_google_email: Optional[str] = None, page_size: int = 10
) -> SearchGmailMessagesResponse:
    """
    <description>Searches Gmail messages using standard Gmail search operators and returns message/thread IDs with web links. Returns up to 10 messages by default for efficient processing.</description>

    <use_case>Finding specific emails for follow-up, locating messages with attachments for processing, or identifying email threads for conversation analysis using Gmail's powerful search syntax.</use_case>

    <limitation>Returns only metadata (IDs, links) - use get_gmail_message_content for actual message content. Limited to 500 messages per request. Cannot search deleted or permanently removed messages.</limitation>

    <failure_cases>Fails with malformed Gmail search syntax, when user lacks Gmail access permissions, or during temporary Gmail API outages. Complex queries may timeout.</failure_cases>

    Args:
        query (str): The search query. Supports standard Gmail search operators.
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected from the refresh token.
        page_size (int): The maximum number of messages to return. Defaults to 10.

    Returns:
        SearchGmailMessagesResponse: Structured response with message IDs, thread IDs, and web URLs.
    """

    logger.info(f"[search_gmail_messages] Email: '{user_google_email}', Query: '{query}'")

    response = await asyncio.to_thread(
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=page_size)
        .execute
    )
    messages = response.get("messages", [])
    result = _format_gmail_search_response(messages, query)

    logger.info(f"[search_gmail_messages] Found {len(messages)} messages")
    return result


@server.tool
@require_google_service("gmail", "gmail_read")
@handle_http_errors("get_gmail_message_content")
async def get_gmail_message_content(
    service, ctx: Context,  message_id: str, user_google_email: Optional[str] = None
) -> GmailMessageContent:
    """
    <description>Retrieves complete Gmail message content including subject, sender, and plain text body. Extracts readable text from multipart messages and handles various email formats automatically.</description>

    <use_case>Reading individual email content for analysis, extracting message details for processing, or getting full context of specific messages found through search.</use_case>

    <limitation>Returns only plain text content - HTML formatting and attachments are not included. Cannot retrieve messages from restricted or deleted conversations.</limitation>

    <failure_cases>Fails with invalid message IDs, messages the user cannot access due to permissions, or messages that have been permanently deleted from Gmail.</failure_cases>

    Args:
        message_id (str): The unique ID of the Gmail message to retrieve.
        user_google_email (Optional[str]): The user's Google email address. Optional.

    Returns:
        GmailMessageContent: Structured message content with subject, sender, body, and web URL.
    """
    logger.info(
        f"[get_gmail_message_content] Invoked. Message ID: '{message_id}', Email: '{user_google_email}'"
    )

    logger.info(f"[get_gmail_message_content] Using service for: {user_google_email}")

    # Fetch message metadata first to get headers
    message_metadata = await asyncio.to_thread(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["Subject", "From"],
        )
        .execute
    )

    headers = {
        h["name"]: h["value"]
        for h in message_metadata.get("payload", {}).get("headers", [])
    }
    subject = headers.get("Subject", "(no subject)")
    sender = headers.get("From", "(unknown sender)")

    # Now fetch the full message to get the body parts
    message_full = await asyncio.to_thread(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full",  # Request full payload for body
        )
        .execute
    )

    # Extract the plain text body using helper function
    payload = message_full.get("payload", {})
    body_data = _extract_message_body(payload)

    return GmailMessageContent(
        message_id=message_id,
        subject=subject,
        sender=sender,
        body=body_data or None,
        web_url=_generate_gmail_web_url(message_id)
    )


@server.tool
@require_google_service("gmail", "gmail_read")
@handle_http_errors("get_gmail_messages_content_batch")
async def get_gmail_messages_content_batch(
    service,
    ctx: Context,
    message_ids: List[str],
    user_google_email: Optional[str] = None,
    format: Literal["full", "metadata"] = "full",
) -> BatchGmailMessagesResponse:
    """
    <description>Efficiently retrieves multiple Gmail messages (up to 100) in a single batch API request. Supports both full content extraction and metadata-only mode for performance optimization.</description>

    <use_case>Processing large email datasets for analysis, bulk email content extraction for reporting, or efficient retrieval of multiple related messages from search results.</use_case>

    <limitation>Limited to 100 messages per batch request. Fallback to sequential processing if batch API fails. Full format significantly slower than metadata-only for large batches.</limitation>

    <failure_cases>Fails if any message IDs are invalid, when batch API is temporarily unavailable, or if user lacks access to any messages in the batch.</failure_cases>

    Args:
        message_ids (List[str]): List of Gmail message IDs to retrieve (max 100).
        user_google_email (Optional[str]): The user's Google email address. Optional.
        format (Literal["full", "metadata"]): Message format. "full" includes body, "metadata" only headers.

    Returns:
        BatchGmailMessagesResponse: Structured response with retrieved messages and any errors.
    """
    logger.info(
        f"[get_gmail_messages_content_batch] Invoked. Message count: {len(message_ids)}, Email: '{user_google_email}'"
    )

    if not message_ids:
        raise Exception("No message IDs provided")

    retrieved_messages = []
    errors = []

    # Process in chunks of 100 (Gmail batch limit)
    for chunk_start in range(0, len(message_ids), 100):
        chunk_ids = message_ids[chunk_start:chunk_start + 100]
        results: Dict[str, Dict] = {}

        def _batch_callback(request_id, response, exception):
            """Callback for batch requests"""
            results[request_id] = {"data": response, "error": exception}

        # Try to use batch API
        try:
            batch = service.new_batch_http_request(callback=_batch_callback)

            for mid in chunk_ids:
                if format == "metadata":
                    req = service.users().messages().get(
                        userId="me",
                        id=mid,
                        format="metadata",
                        metadataHeaders=["Subject", "From"]
                    )
                else:
                    req = service.users().messages().get(
                        userId="me",
                        id=mid,
                        format="full"
                    )
                batch.add(req, request_id=mid)

            # Execute batch request
            await asyncio.to_thread(batch.execute)

        except Exception as batch_error:
            # Fallback to asyncio.gather if batch API fails
            logger.warning(
                f"[get_gmail_messages_content_batch] Batch API failed, falling back to asyncio.gather: {batch_error}"
            )

            async def fetch_message(mid: str):
                try:
                    if format == "metadata":
                        msg = await asyncio.to_thread(
                            service.users().messages().get(
                                userId="me",
                                id=mid,
                                format="metadata",
                                metadataHeaders=["Subject", "From"]
                            ).execute
                        )
                    else:
                        msg = await asyncio.to_thread(
                            service.users().messages().get(
                                userId="me",
                                id=mid,
                                format="full"
                            ).execute
                        )
                    return mid, msg, None
                except Exception as e:
                    return mid, None, e

            # Fetch all messages in parallel
            fetch_results = await asyncio.gather(
                *[fetch_message(mid) for mid in chunk_ids],
                return_exceptions=False
            )

            # Convert to results format
            for mid, msg, error in fetch_results:
                results[mid] = {"data": msg, "error": error}

        # Process results for this chunk
        for mid in chunk_ids:
            entry = results.get(mid, {"data": None, "error": "No result"})

            if entry["error"]:
                errors.append({
                    "message_id": mid,
                    "error": str(entry["error"])
                })
            else:
                message = entry["data"]
                if not message:
                    errors.append({
                        "message_id": mid,
                        "error": "No data returned"
                    })
                    continue

                # Extract content based on format
                payload = message.get("payload", {})
                headers = _extract_headers(payload, ["Subject", "From"])
                subject = headers.get("Subject", "(no subject)")
                sender = headers.get("From", "(unknown sender)")

                if format == "full":
                    body = _extract_message_body(payload)
                    retrieved_messages.append(GmailMessageContent(
                        message_id=mid,
                        subject=subject,
                        sender=sender,
                        body=body or None,
                        web_url=_generate_gmail_web_url(mid)
                    ))
                else:
                    # For metadata format, body is not retrieved
                    retrieved_messages.append(GmailMessageContent(
                        message_id=mid,
                        subject=subject,
                        sender=sender,
                        body=None,
                        web_url=_generate_gmail_web_url(mid)
                    ))

    return BatchGmailMessagesResponse(
        total_requested=len(message_ids),
        total_retrieved=len(retrieved_messages),
        messages=retrieved_messages,
        errors=errors
    )



@server.tool
@require_google_service("gmail", GMAIL_SEND_SCOPE)
@handle_http_errors("send_gmail_message")
async def send_gmail_message(
    service,
    ctx: Context,
    to: str = Body(..., description="Recipient email address."),
    subject: str = Body(..., description="Email subject."),
    body: str = Body(..., description="Email body (plain text)."),
) -> SendGmailMessageResponse:
    """
    <description>Sends a plain text email immediately from the user's Gmail account to a specified recipient. Email is delivered instantly and appears in the user's Sent folder.</description>

    <use_case>Sending automated notifications, quick responses to customer inquiries, or delivering processing results via email with immediate delivery requirements.</use_case>

    <limitation>Supports only plain text emails - no HTML formatting, attachments, or multiple recipients. Cannot schedule emails for later delivery or recall sent messages.</limitation>

    <failure_cases>Fails with invalid email addresses, when user lacks Gmail send permissions, if daily sending limits are exceeded, or if recipient domain blocks the sender.</failure_cases>

    Args:
        to (str): Recipient email address.
        subject (str): Email subject.
        body (str): Email body (plain text).

    Returns:
        SendGmailMessageResponse: Structured response with success status and message ID.
    """
    # Prepare the email
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    send_body = {"raw": raw_message}

    # Send the email
    sent_message = await asyncio.to_thread(
        service.users().messages().send(userId="me", body=send_body).execute
    )
    message_id = sent_message.get("id")

    return SendGmailMessageResponse(
        success=True,
        message_id=message_id,
        message=f"Email sent successfully to {to}"
    )


@server.tool
@require_google_service("gmail", GMAIL_COMPOSE_SCOPE)
@handle_http_errors("draft_gmail_message")
async def draft_gmail_message(
    service,
    ctx: Context,
    subject: str = Body(..., description="Email subject."),
    body: str = Body(..., description="Email body (plain text)."),
    user_google_email: Optional[str] = None,
    to: Optional[str] = Body(None, description="Optional recipient email address."),
) -> DraftGmailMessageResponse:
    """
    <description>Creates a draft email in Gmail's Drafts folder without sending. Draft can be completed and sent later through Gmail interface or API, supporting iterative email composition.</description>

    <use_case>Preparing emails for review before sending, creating template emails for later use, or composing complex messages that require additional formatting in Gmail interface.</use_case>

    <limitation>Creates plain text drafts only - no HTML formatting or attachments. Recipient can be omitted but must be added before sending the draft.</limitation>

    <failure_cases>Fails when user lacks Gmail compose permissions, if draft storage quota is exceeded, or during temporary Gmail API service interruptions.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. Optional.
        subject (str): Email subject.
        body (str): Email body (plain text).
        to (Optional[str]): Optional recipient email address. Can be left empty for drafts.

    Returns:
        DraftGmailMessageResponse: Structured response with success status and draft ID.
    """
    logger.info(
        f"[draft_gmail_message] Invoked. Email: '{user_google_email}', Subject: '{subject}'"
    )

    # Prepare the email
    message = MIMEText(body)
    message["subject"] = subject

    # Add recipient if provided
    if to:
        message["to"] = to

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

    # Create a draft instead of sending
    draft_body = {"message": {"raw": raw_message}}

    # Create the draft
    created_draft = await asyncio.to_thread(
        service.users().drafts().create(userId="me", body=draft_body).execute
    )
    draft_id = created_draft.get("id")

    return DraftGmailMessageResponse(
        success=True,
        draft_id=draft_id,
        message=f"Draft created successfully with subject: {subject}"
    )


@server.tool
@require_google_service("gmail", "gmail_read")
@handle_http_errors("get_gmail_thread_content")
async def get_gmail_thread_content(
    service, ctx: Context, thread_id: str, user_google_email: Optional[str] = None
) -> GmailThreadContent:
    """
    <description>Retrieves all messages within a Gmail conversation thread in chronological order, showing the complete email exchange history with sender details and timestamps.</description>

    <use_case>Analyzing complete email conversations for customer support, understanding full context of email exchanges, or extracting conversation history for documentation.</use_case>

    <limitation>Returns plain text content only - no HTML formatting or attachments. Cannot retrieve threads that have been permanently deleted or are restricted by permissions.</limitation>

    <failure_cases>Fails with invalid thread IDs, threads the user cannot access due to permissions, or threads that have been completely removed from Gmail.</failure_cases>

    Args:
        thread_id (str): The unique ID of the Gmail thread to retrieve.
        user_google_email (Optional[str]): The user's Google email address. Optional.

    Returns:
        GmailThreadContent: Structured thread content with all messages in chronological order.
    """
    logger.info(
        f"[get_gmail_thread_content] Invoked. Thread ID: '{thread_id}', Email: '{user_google_email}'"
    )

    # Fetch the complete thread with all messages
    thread_response = await asyncio.to_thread(
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute
    )

    messages = thread_response.get("messages", [])
    if not messages:
        raise Exception(f"No messages found in thread '{thread_id}'.")

    # Extract thread subject from the first message
    first_message = messages[0]
    first_headers = {
        h["name"]: h["value"]
        for h in first_message.get("payload", {}).get("headers", [])
    }
    thread_subject = first_headers.get("Subject", "(no subject)")

    # Build the thread messages list
    thread_messages = []

    # Process each message in the thread
    for i, message in enumerate(messages, 1):
        # Extract headers
        headers = {
            h["name"]: h["value"]
            for h in message.get("payload", {}).get("headers", [])
        }

        sender = headers.get("From", "(unknown sender)")
        date = headers.get("Date", "(unknown date)")
        subject = headers.get("Subject", "(no subject)")

        # Extract message body
        payload = message.get("payload", {})
        body_data = _extract_message_body(payload)

        # Only include subject if it's different from thread subject
        msg_subject = subject if subject != thread_subject else None

        thread_messages.append(GmailThreadMessage(
            message_number=i,
            sender=sender,
            date=date,
            subject=msg_subject,
            body=body_data or None
        ))

    return GmailThreadContent(
        thread_id=thread_id,
        subject=thread_subject,
        message_count=len(messages),
        messages=thread_messages
    )


@server.tool
@require_google_service("gmail", "gmail_read")
@handle_http_errors("list_gmail_labels")
async def list_gmail_labels(service, ctx: Context, user_google_email: Optional[str] = None) -> ListGmailLabelsResponse:
    """
    <description>Lists all Gmail labels including system labels (Inbox, Sent, Drafts) and user-created custom labels, showing label IDs and names for organization and filtering.</description>

    <use_case>Understanding Gmail organization structure, getting label IDs for message filtering operations, or auditing custom label usage across the Gmail account.</use_case>

    <limitation>Returns label metadata only - not message counts or label colors. Cannot retrieve labels from other users' accounts or deleted labels.</limitation>

    <failure_cases>Fails when user lacks Gmail access permissions, during temporary Gmail API outages, or if the Gmail account has been suspended or restricted.</failure_cases>

    Args:
        user_google_email (str): The user's Google email address. Optional.

    Returns:
        ListGmailLabelsResponse: Structured response with system and user labels separated.
    """
    logger.info(f"[list_gmail_labels] Invoked. Email: '{user_google_email}'")

    response = await asyncio.to_thread(
        service.users().labels().list(userId="me").execute
    )
    labels = response.get("labels", [])

    system_labels = []
    user_labels = []

    for label in labels:
        label_type = label.get("type", "user")
        gmail_label = GmailLabel(
            label_id=label["id"],
            name=label["name"],
            label_type=label_type
        )

        if label_type == "system":
            system_labels.append(gmail_label)
        else:
            user_labels.append(gmail_label)

    return ListGmailLabelsResponse(
        total_labels=len(labels),
        system_labels=system_labels,
        user_labels=user_labels
    )


@server.tool
@require_google_service("gmail", GMAIL_LABELS_SCOPE)
@handle_http_errors("manage_gmail_label")
async def manage_gmail_label(
    service,
    ctx: Context,
    action: Literal["create", "update", "delete"],
    name: Optional[str] = None,
    label_id: Optional[str] = None,
    user_google_email: Optional[str] = None,
    label_list_visibility: Literal["labelShow", "labelHide"] = "labelShow",
    message_list_visibility: Literal["show", "hide"] = "show",
) -> ManageGmailLabelResponse:
    """
    Manages Gmail labels: create, update, or delete labels.

    Args:
        user_google_email (Optional[str]): The user's Google email address. Optional.
        action (Literal["create", "update", "delete"]): Action to perform on the label.
        name (Optional[str]): Label name. Required for create, optional for update.
        label_id (Optional[str]): Label ID. Required for update and delete operations.
        label_list_visibility (Literal["labelShow", "labelHide"]): Whether the label is shown in the label list.
        message_list_visibility (Literal["show", "hide"]): Whether the label is shown in the message list.

    Returns:
        ManageGmailLabelResponse: Structured response with operation result.
    """
    logger.info(f"[manage_gmail_label] Invoked. Email: '{user_google_email}', Action: '{action}'")

    if action == "create" and not name:
        raise Exception("Label name is required for create action.")

    if action in ["update", "delete"] and not label_id:
        raise Exception("Label ID is required for update and delete actions.")

    if action == "create":
        label_object = {
            "name": name,
            "labelListVisibility": label_list_visibility,
            "messageListVisibility": message_list_visibility,
        }
        created_label = await asyncio.to_thread(
            service.users().labels().create(userId="me", body=label_object).execute
        )
        return ManageGmailLabelResponse(
            success=True,
            action="create",
            label_id=created_label['id'],
            label_name=created_label['name'],
            message=f"Label '{created_label['name']}' created successfully"
        )

    elif action == "update":
        current_label = await asyncio.to_thread(
            service.users().labels().get(userId="me", id=label_id).execute
        )

        label_object = {
            "id": label_id,
            "name": name if name is not None else current_label["name"],
            "labelListVisibility": label_list_visibility,
            "messageListVisibility": message_list_visibility,
        }

        updated_label = await asyncio.to_thread(
            service.users().labels().update(userId="me", id=label_id, body=label_object).execute
        )
        return ManageGmailLabelResponse(
            success=True,
            action="update",
            label_id=updated_label['id'],
            label_name=updated_label['name'],
            message=f"Label '{updated_label['name']}' updated successfully"
        )

    elif action == "delete":
        label = await asyncio.to_thread(
            service.users().labels().get(userId="me", id=label_id).execute
        )
        label_name = label["name"]

        await asyncio.to_thread(
            service.users().labels().delete(userId="me", id=label_id).execute
        )
        return ManageGmailLabelResponse(
            success=True,
            action="delete",
            label_id=label_id,
            label_name=label_name,
            message=f"Label '{label_name}' deleted successfully"
        )


@server.tool
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
@handle_http_errors("modify_gmail_message_labels")
async def modify_gmail_message_labels(
    service,
    ctx: Context,
    message_id: str,
    add_label_ids: Optional[List[str]] = None,
    remove_label_ids: Optional[List[str]] = None,
    user_google_email: Optional[str] = None,
) -> ModifyGmailMessageLabelsResponse:
    """
    Adds or removes labels from a Gmail message.

    Args:
        user_google_email (Optional[str]): The user's Google email address. Optional.
        message_id (str): The ID of the message to modify.
        add_label_ids (Optional[List[str]]): List of label IDs to add to the message.
        remove_label_ids (Optional[List[str]]): List of label IDs to remove from the message.

    Returns:
        ModifyGmailMessageLabelsResponse: Structured response with label modification details.
    """
    logger.info(f"[modify_gmail_message_labels] Invoked. Email: '{user_google_email}', Message ID: '{message_id}'")

    if not add_label_ids and not remove_label_ids:
        raise Exception("At least one of add_label_ids or remove_label_ids must be provided.")

    body = {}
    if add_label_ids:
        body["addLabelIds"] = add_label_ids
    if remove_label_ids:
        body["removeLabelIds"] = remove_label_ids

    await asyncio.to_thread(
        service.users().messages().modify(userId="me", id=message_id, body=body).execute
    )

    actions = []
    if add_label_ids:
        actions.append(f"added {len(add_label_ids)} label(s)")
    if remove_label_ids:
        actions.append(f"removed {len(remove_label_ids)} label(s)")

    return ModifyGmailMessageLabelsResponse(
        success=True,
        message_id=message_id,
        added_labels=add_label_ids or [],
        removed_labels=remove_label_ids or [],
        message=f"Message labels updated: {', '.join(actions)}"
    )
