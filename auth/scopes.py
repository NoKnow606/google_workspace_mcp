import os
"""
Google Workspace OAuth Scopes

This module centralizes OAuth scope definitions for Google Workspace integration.
Separated from service_decorator.py to avoid circular imports.
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)

# Temporary map to associate OAuth state with MCP session ID
# This should ideally be a more robust cache in a production system (e.g., Redis)
OAUTH_STATE_TO_SESSION_ID_MAP: Dict[str, str] = {}

# Individual OAuth Scope Constants
USERINFO_EMAIL_SCOPE = 'https://www.googleapis.com/auth/userinfo.email'
OPENID_SCOPE = 'openid'
CALENDAR_READONLY_SCOPE = 'https://www.googleapis.com/auth/calendar.readonly'
CALENDAR_EVENTS_SCOPE = 'https://www.googleapis.com/auth/calendar.events'

# Google Drive scopes
DRIVE_READONLY_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
DRIVE_FILE_SCOPE = 'https://www.googleapis.com/auth/drive.file'

# Google Docs scopes
DOCS_READONLY_SCOPE = 'https://www.googleapis.com/auth/documents.readonly'
DOCS_WRITE_SCOPE = 'https://www.googleapis.com/auth/documents'

# Gmail API scopes
GMAIL_READONLY_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
GMAIL_SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'
GMAIL_COMPOSE_SCOPE = 'https://www.googleapis.com/auth/gmail.compose'
GMAIL_MODIFY_SCOPE = 'https://www.googleapis.com/auth/gmail.modify'
GMAIL_LABELS_SCOPE = 'https://www.googleapis.com/auth/gmail.labels'

# Google Chat API scopes
CHAT_READONLY_SCOPE = 'https://www.googleapis.com/auth/chat.messages.readonly'
CHAT_WRITE_SCOPE = 'https://www.googleapis.com/auth/chat.messages'
CHAT_SPACES_SCOPE = 'https://www.googleapis.com/auth/chat.spaces'

# Google Sheets API scopes
SHEETS_READONLY_SCOPE = 'https://www.googleapis.com/auth/spreadsheets.readonly'
SHEETS_WRITE_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'

# Google Forms API scopes
FORMS_BODY_SCOPE = 'https://www.googleapis.com/auth/forms.body'
FORMS_BODY_READONLY_SCOPE = 'https://www.googleapis.com/auth/forms.body.readonly'
FORMS_RESPONSES_READONLY_SCOPE = 'https://www.googleapis.com/auth/forms.responses.readonly'

# Google Slides API scopes
SLIDES_SCOPE = 'https://www.googleapis.com/auth/presentations'
SLIDES_READONLY_SCOPE = 'https://www.googleapis.com/auth/presentations.readonly'

# Google Tasks API scopes
TASKS_SCOPE = 'https://www.googleapis.com/auth/tasks'
TASKS_READONLY_SCOPE = 'https://www.googleapis.com/auth/tasks.readonly'

# Base OAuth scopes required for user identification
BASE_SCOPES = [
    USERINFO_EMAIL_SCOPE,
    OPENID_SCOPE
]

# Service-specific scope groups
DOCS_SCOPES = [
    DOCS_READONLY_SCOPE,
    DOCS_WRITE_SCOPE
]

CALENDAR_SCOPES = [
    CALENDAR_READONLY_SCOPE,
    CALENDAR_EVENTS_SCOPE
]

DRIVE_SCOPES = [
    DRIVE_READONLY_SCOPE,
    DRIVE_FILE_SCOPE
]

GMAIL_SCOPES = [
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_LABELS_SCOPE
]

CHAT_SCOPES = [
    CHAT_READONLY_SCOPE,
    CHAT_WRITE_SCOPE,
    CHAT_SPACES_SCOPE
]

SHEETS_SCOPES = [
    SHEETS_READONLY_SCOPE,
    SHEETS_WRITE_SCOPE
]

FORMS_SCOPES = [
    FORMS_BODY_SCOPE,
    FORMS_BODY_READONLY_SCOPE,
    FORMS_RESPONSES_READONLY_SCOPE
]

SLIDES_SCOPES = [
    SLIDES_SCOPE,
    SLIDES_READONLY_SCOPE
]

TASKS_SCOPES = [
    TASKS_SCOPE,
    TASKS_READONLY_SCOPE
]

# 通过环境变量选择 scopes
o_server_name = os.getenv("SERVER_NAME")

# 根据服务名称选择对应的 scopes
def get_scopes_for_service(service_name: str) -> list:
    """根据服务名称返回对应的 scopes"""
    base_scopes = BASE_SCOPES.copy()
    
    service_scopes_map = {
        'gmail': GMAIL_SCOPES,
        'drive': DRIVE_SCOPES,
        'calendar': CALENDAR_SCOPES,
        'docs': DOCS_SCOPES,
        'sheets': SHEETS_SCOPES,
        'chat': CHAT_SCOPES,
        'forms': FORMS_SCOPES,
        'slides': SLIDES_SCOPES,
        'tasks': TASKS_SCOPES
    }
    
    if service_name in service_scopes_map:
        return base_scopes + service_scopes_map[service_name]
    else:
        # 如果服务名称不在预定义列表中，返回所有 scopes
        logger.warning(f"Unknown service name: {service_name}, using all scopes")
        return list(set(BASE_SCOPES + CALENDAR_SCOPES + DRIVE_SCOPES + GMAIL_SCOPES + 
                       DOCS_SCOPES + CHAT_SCOPES + SHEETS_SCOPES + FORMS_SCOPES + 
                       SLIDES_SCOPES + TASKS_SCOPES))

# 保留原来的完整 scopes 定义作为备用
ALL_SCOPES = list(set(BASE_SCOPES + CALENDAR_SCOPES + DRIVE_SCOPES + GMAIL_SCOPES +
                     DOCS_SCOPES + CHAT_SCOPES + SHEETS_SCOPES + FORMS_SCOPES +
                     SLIDES_SCOPES + TASKS_SCOPES))

# 根据环境变量动态选择 scopes
SCOPES = get_scopes_for_service(o_server_name.split('_')[1]) if o_server_name is not None else ALL_SCOPES

