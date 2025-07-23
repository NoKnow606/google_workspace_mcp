"""
Google Docs MCP Tools

This module provides MCP tools for interacting with Google Docs API and managing Google Docs via Drive.
"""
import logging
import asyncio
import io
from typing import List, Optional
from uuid import uuid4

from mcp import types
from googleapiclient.http import MediaIoBaseDownload

# Auth & server utilities
from auth.service_decorator import require_google_service, require_multiple_services
from core.utils import extract_office_xml_text, handle_http_errors
from core.server import server
from core.comments import create_comment_tools

logger = logging.getLogger(__name__)

def process_tabs_recursively(tabs: List, level: int = 0) -> List[str]:
    """
    Recursively process tabs and their child tabs.
    
    Args:
        tabs: List of tab objects from Google Docs API
        level: Current nesting level for indentation
        
    Returns:
        List[str]: List of processed text lines from all tabs
    """
    processed_lines: List[str] = []
    indent = "  " * level  # Indentation based on nesting level
    
    for i, tab in enumerate(tabs):
        tab_properties = tab.get('tabProperties', {})
        tab_title = tab_properties.get('title', f'Tab {i+1}')
        tab_id = tab_properties.get('tabId', 'unknown')
        
        logger.info(f"[process_tabs_recursively] Processing tab at level {level}: '{tab_title}' (ID: {tab_id})")
        processed_lines.append(f"\n{indent}=== TAB {i+1}: {tab_title} (ID: {tab_id}) ===\n")
        
        # Process document content for this tab
        document_tab = tab.get('documentTab', {})
        if document_tab:
            tab_body = document_tab.get('body', {})
            if tab_body:
                tab_content = tab_body.get('content', [])
                logger.info(f"[process_tabs_recursively] Tab {i} has {len(tab_content)} content elements")
                
                if tab_content:
                    tab_processed_content = process_structural_elements(tab_content)
                    # Add indentation to content lines
                    for line in tab_processed_content:
                        processed_lines.append(f"{indent}{line}")
                else:
                    processed_lines.append(f"{indent}[EMPTY TAB CONTENT]\n")
            else:
                processed_lines.append(f"{indent}[NO BODY CONTENT]\n")
        else:
            processed_lines.append(f"{indent}[NO DOCUMENT TAB CONTENT]\n")
        
        # Process child tabs recursively
        child_tabs = tab.get('childTabs', [])
        if child_tabs:
            logger.info(f"[process_tabs_recursively] Tab '{tab_title}' has {len(child_tabs)} child tabs")
            processed_lines.append(f"{indent}--- CHILD TABS ---\n")
            processed_lines.extend(process_tabs_recursively(child_tabs, level + 1))
            processed_lines.append(f"{indent}--- END CHILD TABS ---\n")
        
        # Also check for nested tabs in different structure
        nested_tabs = tab.get('tabs', [])
        if nested_tabs:
            logger.info(f"[process_tabs_recursively] Tab '{tab_title}' has {len(nested_tabs)} nested tabs")
            processed_lines.append(f"{indent}--- NESTED TABS ---\n")
            processed_lines.extend(process_tabs_recursively(nested_tabs, level + 1))
            processed_lines.append(f"{indent}--- END NESTED TABS ---\n")
    
    return processed_lines

def process_structural_elements(elements: List) -> List[str]:
    """
    Process various types of structural elements in a Google Doc.
    
    Args:
        elements: List of structural elements from Google Docs API
        
    Returns:
        List[str]: List of processed text lines
    """
    processed_lines: List[str] = []
    
    for element in elements:
        if 'paragraph' in element:
            # Handle paragraph elements
            paragraph = element.get('paragraph', {})
            para_elements = paragraph.get('elements', [])
            current_line_text = ""
            
            for pe in para_elements:
                text_run = pe.get('textRun', {})
                if text_run and 'content' in text_run:
                    current_line_text += text_run['content']
            
            if current_line_text.strip():
                processed_lines.append(current_line_text)
                
        elif 'table' in element:
            # Handle table elements
            table = element.get('table', {})
            processed_lines.append("\n--- TABLE ---\n")
            
            table_rows = table.get('tableRows', [])
            for row in table_rows:
                row_cells = row.get('tableCells', [])
                cell_texts = []
                
                for cell in row_cells:
                    cell_content = cell.get('content', [])
                    cell_text = "".join(process_structural_elements(cell_content))
                    cell_texts.append(cell_text.strip())
                
                if any(cell_texts):  # Only add row if it has content
                    processed_lines.append(" | ".join(cell_texts) + "\n")
            
            processed_lines.append("--- END TABLE ---\n")
            
        elif 'sectionBreak' in element:
            # Handle section breaks
            processed_lines.append("\n--- SECTION BREAK ---\n")
            
        elif 'tableOfContents' in element:
            # Handle table of contents
            processed_lines.append("\n--- TABLE OF CONTENTS ---\n")
            
        elif 'pageBreak' in element:
            # Handle page breaks
            processed_lines.append("\n--- PAGE BREAK ---\n")
            
        elif 'horizontalRule' in element:
            # Handle horizontal rules
            processed_lines.append("\n--- HORIZONTAL RULE ---\n")
            
        elif 'footerContent' in element:
            # Handle footer content
            processed_lines.append("\n--- FOOTER ---\n")
            footer_content = element.get('footerContent', {}).get('content', [])
            processed_lines.extend(process_structural_elements(footer_content))
            processed_lines.append("--- END FOOTER ---\n")
            
        elif 'headerContent' in element:
            # Handle header content
            processed_lines.append("\n--- HEADER ---\n")
            header_content = element.get('headerContent', {}).get('content', [])
            processed_lines.extend(process_structural_elements(header_content))
            processed_lines.append("--- END HEADER ---\n")
            
        # Add more element types as needed
        # 'pageBreak', 'horizontalRule', etc.
    
    return processed_lines

@server.tool()
@require_google_service("drive", "drive_read")
@handle_http_errors("search_docs")
async def search_docs(
    service,
    query: str,
    page_size: int = 10,
    user_google_email: Optional[str] = None,
) -> str:
    """
    Searches for Google Docs by name using Drive API (mimeType filter).

    Returns:
        str: A formatted list of Google Docs matching the search query.
    """
    logger.info(f"[search_docs] Email={user_google_email}, Query='{query}'")

    escaped_query = query.replace("'", "\\'")

    response = await asyncio.to_thread(
        service.files().list(
            q=f"name contains '{escaped_query}' and mimeType='application/vnd.google-apps.document' and trashed=false",
            pageSize=page_size,
            fields="files(id, name, createdTime, modifiedTime, webViewLink)"
        ).execute
    )
    files = response.get('files', [])
    if not files:
        return f"No Google Docs found matching '{query}'."

    output = [f"Found {len(files)} Google Docs matching '{query}':"]
    for f in files:
        output.append(
            f"- {f['name']} (ID: {f['id']}) Modified: {f.get('modifiedTime')} Link: {f.get('webViewLink')}"
        )
    return "\n".join(output)

# @server.tool()
@require_multiple_services([
    {"service_type": "drive", "scopes": "drive_read", "param_name": "drive_service"},
    {"service_type": "docs", "scopes": "docs_read", "param_name": "docs_service"}
])
# @handle_http_errors("get_doc_content")
async def get_doc_content(
    drive_service,
    docs_service,
    document_id: str,
    user_google_email: Optional[str] = None,
) -> str:
    """
    Retrieves content of a Google Doc or a Drive file (like .docx) identified by document_id.
    - Native Google Docs: Fetches content via Docs API.
    - Office files (.docx, etc.) stored in Drive: Downloads via Drive API and extracts text.

    Returns:
        str: The document content with metadata header.
    """
    logger.info(f"[get_doc_content] Invoked. Document/File ID: '{document_id}' for user '{user_google_email}'")

    # Step 2: Get file metadata from Drive
    file_metadata = await asyncio.to_thread(
        drive_service.files().get(
            fileId=document_id, fields="id, name, mimeType, webViewLink"
        ).execute
    )
    mime_type = file_metadata.get("mimeType", "")
    file_name = file_metadata.get("name", "Unknown File")
    web_view_link = file_metadata.get("webViewLink", "#")

    logger.info(f"[get_doc_content] File '{file_name}' (ID: {document_id}) has mimeType: '{mime_type}'")

    body_text = "" # Initialize body_text

    # Step 3: Process based on mimeType
    if mime_type == "application/vnd.google-apps.document":
        logger.info(f"[get_doc_content] Processing as native Google Doc.")
        doc_data = await asyncio.to_thread(
            docs_service.documents().get(
                documentId=document_id,
                includeTabsContent=True
            ).execute
        )
        logger.info(f"[get_doc_content] Processing as native Google Doc.")
        
        # Process tabs if they exist
        tabs = doc_data.get('tabs', [])
        logger.info(f"[get_doc_content] Found {len(tabs)} tabs")
        
        # Debug: Print full document structure
        logger.info(f"[get_doc_content] Document keys: {list(doc_data.keys())}")
        if tabs:
            for i, tab in enumerate(tabs):
                logger.info(f"[get_doc_content] Tab {i} keys: {list(tab.keys())}")
                logger.info(f"[get_doc_content] Tab {i} properties: {tab.get('tabProperties', {})}")
                
                # Check for child tabs in all possible locations
                child_tabs = tab.get('childTabs', [])
                nested_tabs = tab.get('tabs', [])
                document_tab = tab.get('documentTab', {})
                
                logger.info(f"[get_doc_content] Tab {i} has {len(child_tabs)} childTabs")
                logger.info(f"[get_doc_content] Tab {i} has {len(nested_tabs)} nested tabs")
                logger.info(f"[get_doc_content] Tab {i} documentTab keys: {list(document_tab.keys()) if document_tab else 'None'}")
                
                if document_tab:
                    tab_body = document_tab.get('body', {})
                    if tab_body:
                        tab_content = tab_body.get('content', [])
                        logger.info(f"[get_doc_content] Tab {i} body content elements: {len(tab_content)}")
                        # Log first few elements to understand structure
                        for j, element in enumerate(tab_content[:3]):
                            logger.info(f"[get_doc_content] Tab {i} element {j}: {list(element.keys())}")
                    else:
                        logger.info(f"[get_doc_content] Tab {i} has no body")
        
        processed_text_lines: List[str] = []
        
        if tabs:
            # Document has tabs - process all tabs recursively
            logger.info(f"[get_doc_content] Processing {len(tabs)} tabs recursively")
            processed_text_lines.extend(process_tabs_recursively(tabs, 0))
        else:
            # Document without tabs - process body content directly
            body_elements = doc_data.get('body', {}).get('content', [])
            processed_text_lines.extend(process_structural_elements(body_elements))
            
        body_text = "".join(processed_text_lines)
    else:
        logger.info(f"[get_doc_content] Processing as Drive file (e.g., .docx, other). MimeType: {mime_type}")

        export_mime_type_map = {
                # Example: "application/vnd.google-apps.spreadsheet"z: "text/csv",
                # Native GSuite types that are not Docs would go here if this function
                # was intended to export them. For .docx, direct download is used.
        }
        effective_export_mime = export_mime_type_map.get(mime_type)

        request_obj = (
            drive_service.files().export_media(fileId=document_id, mimeType=effective_export_mime)
            if effective_export_mime
            else drive_service.files().get_media(fileId=document_id)
        )

        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request_obj)
        loop = asyncio.get_event_loop()
        done = False
        while not done:
            status, done = await loop.run_in_executor(None, downloader.next_chunk)

        file_content_bytes = fh.getvalue()

        office_text = extract_office_xml_text(file_content_bytes, mime_type)
        if office_text:
            body_text = office_text
        else:
            try:
                body_text = file_content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                body_text = (
                    f"[Binary or unsupported text encoding for mimeType '{mime_type}' - "
                    f"{len(file_content_bytes)} bytes]"
                )

    header = (
        f'File: "{file_name}" (ID: {document_id}, Type: {mime_type})\n'
        f'Link: {web_view_link}\n\n--- CONTENT ---\n'
    )
    print(body_text)
    return header + body_text

@server.tool()
@require_google_service("drive", "drive_read")
@handle_http_errors("list_docs_in_folder")
async def list_docs_in_folder(
    service,
    user_google_email: Optional[str] = None,
    folder_id: str = 'root',
    page_size: int = 100
) -> str:
    """
    Lists Google Docs within a specific Drive folder.

    Returns:
        str: A formatted list of Google Docs in the specified folder.
    """
    logger.info(f"[list_docs_in_folder] Invoked. Email: '{user_google_email}', Folder ID: '{folder_id}'")

    rsp = await asyncio.to_thread(
        service.files().list(
            q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document' and trashed=false",
            pageSize=page_size,
            fields="files(id, name, modifiedTime, webViewLink)"
        ).execute
    )
    items = rsp.get('files', [])
    if not items:
        return f"No Google Docs found in folder '{folder_id}'."
    out = [f"Found {len(items)} Docs in folder '{folder_id}':"]
    for f in items:
        out.append(f"- {f['name']} (ID: {f['id']}) Modified: {f.get('modifiedTime')} Link: {f.get('webViewLink')}")
    return "\n".join(out)

@server.tool()
@require_google_service("docs", "docs_write")
@handle_http_errors("create_doc")
async def create_doc(
    service,
    title: str,
    user_google_email: Optional[str] = None,
    content: str = '',
) -> str:
    """
    Creates a new Google Doc and optionally inserts initial content.

    Returns:
        str: Confirmation message with document ID and link.
    """
    logger.info(f"[create_doc] Invoked. Email: '{user_google_email}', Title='{title}'")

    doc = await asyncio.to_thread(service.documents().create(body={'title': title}).execute)
    doc_id = doc.get('documentId')
    if content:
        requests = [{'insertText': {'location': {'index': 1}, 'text': content}}]
        await asyncio.to_thread(service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute)
    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    msg = f"Created Google Doc '{title}' (ID: {doc_id}) for {user_google_email}. Link: {link}"
    logger.info(f"Successfully created Google Doc '{title}' (ID: {doc_id}) for {user_google_email}. Link: {link}")
    return msg


# Create comment management tools for documents
_comment_tools = create_comment_tools("document", "document_id")

# Extract and register the functions
read_doc_comments = _comment_tools['read_comments']
create_doc_comment = _comment_tools['create_comment']
reply_to_comment = _comment_tools['reply_to_comment']
resolve_comment = _comment_tools['resolve_comment']

if __name__ == '__main__':
    asyncio.run(get_doc_content(drive_service="drive", docs_service="docs", document_id="18-52JXU073R9wQ6ip-MrKMjHVq2QgR1VIEFXgMGyuI8"))
