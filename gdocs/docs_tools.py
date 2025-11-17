"""
Google Docs MCP Tools

This module provides MCP tools for interacting with Google Docs API and managing Google Docs via Drive.
"""
import logging
import asyncio
import io
import json
from typing import List, Optional, Dict, Any, Union, Literal
from uuid import uuid4
from pydantic import BaseModel, Field

from mcp import types
from fastmcp import Context
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload, HttpRequest

# Auth & server utilities
from auth.service_decorator import require_google_service, require_multiple_services
from core.utils import extract_office_xml_text, handle_http_errors
from core.server import server
from core.comments import create_comment_tools

logger = logging.getLogger(__name__)

# ==================== Pydantic Models for Structured Output ====================

class TextElement(BaseModel):
    """Represents a text run in a paragraph"""
    type: Literal["text"] = "text"
    content: str = Field(description="Text content")

class ImageElement(BaseModel):
    """Represents an image in a document"""
    type: Literal["image"] = "image"
    imageId: str = Field(description="Inline object ID")
    title: str = Field(description="Image title")
    description: Optional[str] = Field(default="", description="Image description")
    contentUri: str = Field(description="Image URL")
    width: Optional[float] = Field(default=None, description="Image width")
    height: Optional[float] = Field(default=None, description="Image height")
    widthUnit: Optional[str] = Field(default=None, description="Width unit (e.g., PT)")
    heightUnit: Optional[str] = Field(default=None, description="Height unit (e.g., PT)")

class ParagraphBlock(BaseModel):
    """Represents a paragraph with mixed text and images"""
    type: Literal["paragraph"] = "paragraph"
    elements: List[Union[TextElement, ImageElement]] = Field(default_factory=list, description="Paragraph elements")

class TableCell(BaseModel):
    """Represents a table cell"""
    content: List[Union[ParagraphBlock, 'TableBlock', 'StructuralBlock']] = Field(default_factory=list, description="Cell content blocks")

class TableRow(BaseModel):
    """Represents a table row"""
    cells: List[TableCell] = Field(default_factory=list, description="Table cells")

class TableBlock(BaseModel):
    """Represents a table"""
    type: Literal["table"] = "table"
    rows: List[TableRow] = Field(default_factory=list, description="Table rows")

class StructuralBlock(BaseModel):
    """Represents structural elements like breaks and rules"""
    type: Literal["section_break", "page_break", "horizontal_rule", "table_of_contents"] = Field(description="Block type")

class HeaderFooterBlock(BaseModel):
    """Represents header or footer content"""
    type: Literal["header", "footer"] = Field(description="Block type")
    content: List[Union[ParagraphBlock, TableBlock, StructuralBlock]] = Field(default_factory=list, description="Header/footer content")

# Union type for all content blocks
ContentBlock = Union[ParagraphBlock, TableBlock, StructuralBlock, HeaderFooterBlock]

class TabContent(BaseModel):
    """Represents a document tab"""
    tabId: str = Field(description="Tab ID")
    title: str = Field(description="Tab title")
    level: int = Field(default=0, description="Nesting level")
    index: int = Field(description="Tab index")
    content: List[ContentBlock] = Field(default_factory=list, description="Tab content blocks")
    childTabs: List['TabContent'] = Field(default_factory=list, description="Child tabs")

class ImageMetadata(BaseModel):
    """Metadata for an inline image object"""
    id: str = Field(description="Image object ID")
    title: str = Field(description="Image title")
    description: Optional[str] = Field(default="", description="Image description")
    contentUri: str = Field(description="Image URL")
    width: Optional[float] = Field(default=None, description="Image width")
    height: Optional[float] = Field(default=None, description="Image height")
    widthUnit: Optional[str] = Field(default=None, description="Width unit")
    heightUnit: Optional[str] = Field(default=None, description="Height unit")

class DocumentMetadata(BaseModel):
    """Document metadata"""
    id: str = Field(description="Document ID")
    title: str = Field(description="Document title")
    mimeType: str = Field(description="Document MIME type")
    link: str = Field(description="Document web view link")

class StructuredDocumentContent(BaseModel):
    """Complete structured document content"""
    document: DocumentMetadata = Field(description="Document metadata")
    content: List[ContentBlock] = Field(default_factory=list, description="Document content (for non-tabbed documents)")
    tabs: List[TabContent] = Field(default_factory=list, description="Document tabs (for tabbed documents)")
    images: Dict[str, ImageMetadata] = Field(default_factory=dict, description="Image metadata dictionary")

# Update forward references for recursive models
TabContent.model_rebuild()
TableCell.model_rebuild()

# ==================== Response Models ====================

class DocReference(BaseModel):
    """Reference to a Google Docs document"""
    id: str = Field(description="Document ID")
    name: str = Field(description="Document name")
    modified_time: Optional[str] = Field(default=None, description="Last modified time")
    web_view_link: Optional[str] = Field(default=None, description="Web view link")

class SearchDocsResponse(BaseModel):
    """Response for document search"""
    query: str = Field(description="Search query used")
    total_found: int = Field(description="Number of documents found")
    documents: List[DocReference] = Field(default_factory=list, description="List of document references")

class ListDocsResponse(BaseModel):
    """Response for listing documents in a folder"""
    folder_id: str = Field(description="Folder ID")
    total_found: int = Field(description="Number of documents found")
    documents: List[DocReference] = Field(default_factory=list, description="List of document references")

class CreateDocResponse(BaseModel):
    """Response for document creation"""
    success: bool = Field(description="Whether the document was created successfully")
    document_id: str = Field(description="Created document ID")
    title: str = Field(description="Document title")
    web_view_link: str = Field(description="Link to view the document")
    message: str = Field(description="Human-readable confirmation message")

class InsertMarkdownResponse(BaseModel):
    """Response for inserting Markdown content"""
    success: bool = Field(description="Whether the operation was successful")
    document_id: str = Field(description="Document ID")
    elements_inserted: int = Field(description="Number of elements inserted (paragraphs, images, etc.)")
    images_inserted: int = Field(description="Number of images inserted")
    start_index: int = Field(description="Starting index where content was inserted")
    end_index: int = Field(description="Ending index after insertion")
    web_view_link: str = Field(description="Link to view the document")
    message: str = Field(description="Human-readable confirmation message")

# ==================== Helper Functions ====================

def process_tabs_recursively(tabs: List, level: int = 0, target_tab_id: Optional[str] = None, inline_objects: dict = None) -> List[TabContent]:
    """
    Recursively process tabs and their child tabs.

    Args:
        tabs: List of tab objects from Google Docs API
        level: Current nesting level for indentation
        target_tab_id: If specified, only process this specific tab ID
        inline_objects: Dictionary of inline objects (images) from document

    Returns:
        List[TabContent]: List of processed tab objects (Pydantic models)
    """
    processed_tabs: List[TabContent] = []
    inline_objects = inline_objects or {}
    
    for i, tab in enumerate(tabs):
        tab_properties = tab.get('tabProperties', {})
        tab_title = tab_properties.get('title', f'Tab {i+1}')
        tab_id = tab_properties.get('tabId', 'unknown')

        # If target_tab_id is specified, skip tabs that don't match
        if target_tab_id and tab_id != target_tab_id:
            # Still check child tabs recursively
            child_tabs = tab.get('childTabs', [])
            if child_tabs:
                child_tab_results = process_tabs_recursively(child_tabs, level + 1, target_tab_id, inline_objects)
                processed_tabs.extend(child_tab_results)

            nested_tabs = tab.get('tabs', [])
            if nested_tabs:
                nested_tab_results = process_tabs_recursively(nested_tabs, level + 1, target_tab_id, inline_objects)
                processed_tabs.extend(nested_tab_results)
            continue

        logger.info(f"[process_tabs_recursively] Processing tab at level {level}: '{tab_title}' (ID: {tab_id})")

        # Process document content for this tab
        content_blocks: List[ContentBlock] = []
        document_tab = tab.get('documentTab', {})
        if document_tab:
            tab_body = document_tab.get('body', {})
            if tab_body:
                tab_content = tab_body.get('content', [])
                logger.info(f"[process_tabs_recursively] Tab {i} has {len(tab_content)} content elements")

                if tab_content:
                    content_blocks = process_structural_elements(tab_content, inline_objects)

        # Process child tabs recursively
        child_tab_list: List[TabContent] = []
        child_tabs = tab.get('childTabs', [])
        if child_tabs:
            logger.info(f"[process_tabs_recursively] Tab '{tab_title}' has {len(child_tabs)} child tabs")
            child_tab_list = process_tabs_recursively(child_tabs, level + 1, target_tab_id, inline_objects)

        # Also check for nested tabs in different structure
        nested_tabs = tab.get('tabs', [])
        if nested_tabs:
            logger.info(f"[process_tabs_recursively] Tab '{tab_title}' has {len(nested_tabs)} nested tabs")
            nested_tab_results = process_tabs_recursively(nested_tabs, level + 1, target_tab_id, inline_objects)
            child_tab_list.extend(nested_tab_results)

        # Create TabContent object
        tab_obj = TabContent(
            tabId=tab_id,
            title=tab_title,
            level=level,
            index=i + 1,
            content=content_blocks,
            childTabs=child_tab_list
        )

        processed_tabs.append(tab_obj)

    return processed_tabs

def process_structural_elements(elements: List, inline_objects: dict = None) -> List[ContentBlock]:
    """
    Process various types of structural elements in a Google Doc.

    Args:
        elements: List of structural elements from Google Docs API
        inline_objects: Dictionary of inline objects (images) from document

    Returns:
        List[ContentBlock]: List of processed content blocks (Pydantic models)
    """
    processed_content: List[ContentBlock] = []
    inline_objects = inline_objects or {}

    for element in elements:
        if 'paragraph' in element:
            # Handle paragraph elements
            paragraph = element.get('paragraph', {})
            para_elements = paragraph.get('elements', [])

            para_elem_list: List[Union[TextElement, ImageElement]] = []

            for pe in para_elements:
                # Handle text runs
                text_run = pe.get('textRun', {})
                if text_run and 'content' in text_run:
                    para_elem_list.append(TextElement(
                        type='text',
                        content=text_run['content']
                    ))

                # Handle inline objects (images)
                inline_object_element = pe.get('inlineObjectElement', {})
                if inline_object_element:
                    inline_object_id = inline_object_element.get('inlineObjectId')
                    if inline_object_id and inline_object_id in inline_objects:
                        inline_obj = inline_objects[inline_object_id]
                        inline_obj_props = inline_obj.get('inlineObjectProperties', {})
                        embedded_obj = inline_obj_props.get('embeddedObject', {})

                        # Get image size if available
                        size = embedded_obj.get('size', {})
                        width = size.get('width', {})
                        height = size.get('height', {})

                        para_elem_list.append(ImageElement(
                            type='image',
                            imageId=inline_object_id,
                            title=embedded_obj.get('title', 'Untitled Image'),
                            description=embedded_obj.get('description', ''),
                            contentUri=embedded_obj.get('imageProperties', {}).get('contentUri', ''),
                            width=width.get('magnitude') if width else None,
                            height=height.get('magnitude') if height else None,
                            widthUnit=width.get('unit') if width else None,
                            heightUnit=height.get('unit') if height else None
                        ))

            if para_elem_list:
                processed_content.append(ParagraphBlock(
                    type='paragraph',
                    elements=para_elem_list
                ))
                
        elif 'table' in element:
            # Handle table elements
            table = element.get('table', {})
            table_rows = table.get('tableRows', [])

            rows: List[TableRow] = []

            for row in table_rows:
                row_cells = row.get('tableCells', [])
                cells: List[TableCell] = []

                for cell in row_cells:
                    cell_content = cell.get('content', [])
                    cell_blocks = process_structural_elements(cell_content, inline_objects)
                    cells.append(TableCell(content=cell_blocks))

                if cells:
                    rows.append(TableRow(cells=cells))

            if rows:
                processed_content.append(TableBlock(type='table', rows=rows))

        elif 'sectionBreak' in element:
            processed_content.append(StructuralBlock(type='section_break'))

        elif 'tableOfContents' in element:
            processed_content.append(StructuralBlock(type='table_of_contents'))

        elif 'pageBreak' in element:
            processed_content.append(StructuralBlock(type='page_break'))

        elif 'horizontalRule' in element:
            processed_content.append(StructuralBlock(type='horizontal_rule'))

        elif 'footerContent' in element:
            footer_content = element.get('footerContent', {}).get('content', [])
            footer_blocks = process_structural_elements(footer_content, inline_objects)
            processed_content.append(HeaderFooterBlock(
                type='footer',
                content=footer_blocks
            ))

        elif 'headerContent' in element:
            header_content = element.get('headerContent', {}).get('content', [])
            header_blocks = process_structural_elements(header_content, inline_objects)
            processed_content.append(HeaderFooterBlock(
                type='header',
                content=header_blocks
            ))

    return processed_content

@server.tool
@require_google_service("drive", "drive_read")
@handle_http_errors("search_docs")
async def search_docs(
    service,
    ctx: Context,
    query: str,
    page_size: int = 10,
    user_google_email: Optional[str] = None,
) -> SearchDocsResponse:
    """
    <description>Searches for Google Docs by document name using Drive API with Google Docs MIME type filtering. Returns document metadata including IDs, names, modification times, and web view links for up to 10 documents.</description>

    <use_case>Finding specific Google Docs for content processing, locating documents by partial name matches, or discovering recently modified docs for collaborative editing workflows.</use_case>

    <limitation>Searches only document titles, not content. Limited to Google Docs format only - excludes Word files or other document formats. Cannot search within document text or comments.</limitation>

    <failure_cases>Fails with malformed search queries containing special characters, when user lacks Drive access permissions, or if Google Drive API quotas are exceeded.</failure_cases>

    Returns:
        SearchDocsResponse: Structured search results with document references.
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

    documents = [
        DocReference(
            id=f['id'],
            name=f['name'],
            modified_time=f.get('modifiedTime'),
            web_view_link=f.get('webViewLink')
        )
        for f in files
    ]

    return SearchDocsResponse(
        query=query,
        total_found=len(files),
        documents=documents
    )

@server.tool
@require_multiple_services([
    {"service_type": "drive", "scopes": "drive_read", "param_name": "drive_service"},
    {"service_type": "docs", "scopes": "docs_read", "param_name": "docs_service"}
])
@handle_http_errors("get_doc_content")
async def get_doc_content(
    ctx: Context,
    drive_service: str,
    docs_service: str,
    document_id: str,
    user_google_email: Optional[str] = None,
    tab_id: Optional[str] = None,
) -> StructuredDocumentContent:
    """
    <description>Extracts structured content from Google Docs (including tabbed documents) and Office files (.docx) stored in Drive. Returns structured document data with complete document structure, image metadata, tables, and all content elements preserved.</description>

    <use_case>Extracting document content for programmatic processing, analyzing multi-tab Google Docs structure, parsing tables and images, or building automated document workflows with full access to document metadata.</use_case>

    <limitation>Returns structured data which may be verbose for large documents. Limited to documents under 50MB. Cannot process password-protected or heavily corrupted files. Images must be accessible via provided URLs.</limitation>

    <failure_cases>Fails with invalid document IDs, documents the user cannot access due to permissions, corrupted Office files, or when specifying invalid tab IDs for tabbed documents.</failure_cases>

    Args:
        document_id: The ID of the document to retrieve
        user_google_email: Optional user email for context
        tab_id: Optional tab ID to retrieve content from a specific tab only

    Returns:
        StructuredDocumentContent: Structured document content including metadata, content blocks, tabs, and images.
    """
    logger.info(f"[get_doc_content] Invoked. Document/File ID: '{document_id}' for user '{user_google_email}', tab_id: '{tab_id}'")

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

        # Extract inline objects (images) from the document
        inline_objects = doc_data.get('inlineObjects', {})
        logger.info(f"[get_doc_content] Found {len(inline_objects)} inline objects (images)")

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
        
        # Build image metadata dictionary using Pydantic models
        images_metadata: Dict[str, ImageMetadata] = {}
        for img_id, img_obj in inline_objects.items():
            img_props = img_obj.get('inlineObjectProperties', {})
            embedded = img_props.get('embeddedObject', {})
            img_size = embedded.get('size', {})

            images_metadata[img_id] = ImageMetadata(
                id=img_id,
                title=embedded.get('title', 'Untitled Image'),
                description=embedded.get('description', ''),
                contentUri=embedded.get('imageProperties', {}).get('contentUri', ''),
                width=img_size.get('width', {}).get('magnitude'),
                height=img_size.get('height', {}).get('magnitude'),
                widthUnit=img_size.get('width', {}).get('unit'),
                heightUnit=img_size.get('height', {}).get('unit')
            )

        # Build document metadata
        doc_metadata = DocumentMetadata(
            id=document_id,
            title=file_name,
            mimeType=mime_type,
            link=web_view_link
        )

        # Process document content
        content_list: List[ContentBlock] = []
        tabs_list: List[TabContent] = []

        if tabs:
            # Process tabs
            if tab_id:
                logger.info(f"[get_doc_content] Processing specific tab_id '{tab_id}' from {len(tabs)} tabs")
                processed_tabs = process_tabs_recursively(tabs, 0, tab_id, inline_objects)
                if not processed_tabs:
                    logger.warning(f"[get_doc_content] No content found for tab_id '{tab_id}'")
                    raise ValueError(f'No tab found with tab_id "{tab_id}"')
                tabs_list = processed_tabs
            else:
                logger.info(f"[get_doc_content] Processing all {len(tabs)} tabs recursively")
                tabs_list = process_tabs_recursively(tabs, 0, None, inline_objects)
        else:
            # Process body content directly
            if tab_id:
                logger.warning(f"[get_doc_content] tab_id '{tab_id}' specified but document has no tabs")
                raise ValueError(f'Specified tab_id "{tab_id}" but document has no tabs')
            body_elements = doc_data.get('body', {}).get('content', [])
            content_list = process_structural_elements(body_elements, inline_objects)

        # Build StructuredDocumentContent with Pydantic model
        structured_doc = StructuredDocumentContent(
            document=doc_metadata,
            content=content_list,
            tabs=tabs_list,
            images=images_metadata
        )

        # Return the Pydantic model directly
        return structured_doc
    else:
        logger.info(f"[get_doc_content] Processing as Drive file (e.g., .docx, other). MimeType: {mime_type}")

        # tab_id is not supported for non-Google Docs files
        if tab_id:
            logger.warning(f"[get_doc_content] tab_id '{tab_id}' specified but not supported for non-Google Docs files")
            raise ValueError(f'tab_id is not supported for non-Google Docs files (File: "{file_name}", Type: {mime_type})')

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

    # Return structured model for non-Google Docs files using Pydantic models
    doc_metadata = DocumentMetadata(
        id=document_id,
        title=file_name,
        mimeType=mime_type,
        link=web_view_link
    )

    paragraph = ParagraphBlock(
        type='paragraph',
        elements=[TextElement(type='text', content=body_text)]
    )

    structured_doc = StructuredDocumentContent(
        document=doc_metadata,
        content=[paragraph],
        tabs=[],
        images={}
    )

    return structured_doc

@server.tool
@require_google_service("drive", "drive_read")
@handle_http_errors("list_docs_in_folder")
async def list_docs_in_folder(
    service,
    ctx: Context,
    user_google_email: Optional[str] = None,
    folder_id: str = 'root',
    page_size: int = 100
) -> ListDocsResponse:
    """
    <description>Lists all Google Docs within a specific Drive folder showing document names, IDs, modification times, and web view links. Returns up to 100 documents per page with pagination support for large folders.</description>

    <use_case>Organizing document workflows by folder, bulk processing documents in specific directories, or discovering documents within project folders for content analysis.</use_case>

    <limitation>Limited to Google Docs format only - excludes Word files or other document types. Shows only immediate folder contents, not recursive subfolder documents. Requires valid folder access permissions.</limitation>

    <failure_cases>Fails with invalid folder IDs, folders the user cannot access due to sharing restrictions, or when trying to list contents of files instead of folders.</failure_cases>

    Returns:
        ListDocsResponse: Structured list of documents in the folder.
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

    documents = [
        DocReference(
            id=f['id'],
            name=f['name'],
            modified_time=f.get('modifiedTime'),
            web_view_link=f.get('webViewLink')
        )
        for f in items
    ]

    return ListDocsResponse(
        folder_id=folder_id,
        total_found=len(items),
        documents=documents
    )


def markdown_to_html(markdown_text: str) -> str:
    """
    Convert Markdown text to HTML using the markdown library.

    Args:
        markdown_text: Markdown formatted text

    Returns:
        HTML string with styling
    """
    try:
        import markdown
    except ImportError:
        raise ImportError(
            "The 'markdown' library is required for HTML conversion. "
            "Please install it with: pip install markdown"
        )

    # Convert markdown to HTML with extensions
    html_content = markdown.markdown(
        markdown_text,
        extensions=[
            'extra',  # Tables, fenced code, etc.
            'nl2br',  # Newline to <br>
            'sane_lists',  # Better list handling
        ]
    )

    # Wrap in HTML document with styling
    html_with_styles = f'''<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    /* Google Docs compatible styles */
    body {{
      font-family: Arial, sans-serif;
      font-size: 11pt;
      line-height: 1.5;
    }}

    h1 {{
      font-size: 20pt;
      font-weight: bold;
      margin-top: 20pt;
      margin-bottom: 10pt;
    }}

    h2 {{
      font-size: 16pt;
      font-weight: bold;
      margin-top: 18pt;
      margin-bottom: 8pt;
    }}

    h3 {{
      font-size: 14pt;
      font-weight: bold;
      margin-top: 16pt;
      margin-bottom: 6pt;
    }}

    p, ul, ol, table {{
      margin-bottom: 10pt;
    }}

    ul, ol {{
      padding-left: 30pt;
    }}

    li {{
      margin-bottom: 5pt;
    }}

    a {{
      color: #1155cc;
      text-decoration: underline;
    }}

    code {{
      font-family: 'Courier New', monospace;
      background-color: #f5f5f5;
      padding: 2pt 4pt;
      border-radius: 3pt;
    }}

    pre {{
      background-color: #f5f5f5;
      padding: 10pt;
      border-radius: 5pt;
      overflow-x: auto;
    }}

    blockquote {{
      border-left: 3pt solid #ccc;
      margin-left: 0;
      padding-left: 15pt;
      color: #666;
    }}

    table {{
      border-collapse: collapse;
      width: 100%;
    }}

    th, td {{
      border: 1pt solid #ddd;
      padding: 8pt;
      text-align: left;
    }}

    th {{
      background-color: #f5f5f5;
      font-weight: bold;
    }}

    hr {{
      border: none;
      border-top: 1pt solid #ccc;
      margin: 20pt 0;
    }}

    img {{
      max-width: 100%;
      height: auto;
    }}
  </style>
</head>
<body>
{html_content}
</body>
</html>'''

    return html_with_styles


async def create_doc_from_html(
    drive_service,
    title: str,
    html_content: str,
    folder_id: Optional[str] = None
) -> Dict[str, str]:
    """
    Create a Google Doc from HTML content using Drive API multipart upload.

    Args:
        drive_service: Authenticated Google Drive service
        title: Document title
        html_content: HTML content (should be complete HTML document)
        folder_id: Optional parent folder ID

    Returns:
        Dict with 'id' and 'webViewLink'
    """
    # Prepare metadata
    metadata = {
        'name': title,
        'mimeType': 'application/vnd.google-apps.document'
    }

    if folder_id:
        metadata['parents'] = [folder_id]

    # Prepare multipart body
    boundary = 'boundary_marker'

    # Build multipart request body
    body_parts = []

    # Part 1: Metadata (JSON)
    body_parts.append(f'--{boundary}')
    body_parts.append('Content-Type: application/json; charset=UTF-8')
    body_parts.append('')
    body_parts.append(json.dumps(metadata))

    # Part 2: HTML content
    body_parts.append(f'--{boundary}')
    body_parts.append('Content-Type: text/html; charset=UTF-8')
    body_parts.append('')
    body_parts.append(html_content)

    # Final boundary
    body_parts.append(f'--{boundary}--')

    # Join with CRLF
    body = '\r\n'.join(body_parts)

    # Make the request
    url = 'https://www.googleapis.com/upload/drive/v3/files'
    params = {
        'uploadType': 'multipart',
        'supportsAllDrives': 'true',
        'fields': 'id,webViewLink'
    }

    # Create request
    http = drive_service._http
    headers = {
        'Content-Type': f'multipart/related; boundary={boundary}'
    }

    # Execute request in thread
    def _execute_upload():
        import urllib.parse
        import urllib.request

        # Build full URL
        full_url = url + '?' + urllib.parse.urlencode(params)

        # Create request
        req = urllib.request.Request(
            full_url,
            data=body.encode('utf-8'),
            headers=headers,
            method='POST'
        )

        # Add authorization header
        credentials = drive_service._http.credentials
        credentials.apply(headers)
        req.headers.update(headers)

        # Execute
        response = urllib.request.urlopen(req)
        return json.loads(response.read().decode('utf-8'))

    result = await asyncio.to_thread(_execute_upload)

    logger.info(f"[create_doc_from_html] Created document: {result.get('id')}")

    return result


@server.tool
@require_google_service("drive", "drive_file")
@handle_http_errors("create_doc")
async def create_doc(
    service,
    ctx: Context,
    title: str,
    content: str,
    user_google_email: Optional[str] = None,
    folder_id: Optional[str] = None,
) -> CreateDocResponse:
    """
    <description>Creates a new Google Docs document with Markdown content automatically converted to formatted HTML. Uses Drive API multipart upload for efficient one-shot document creation with full formatting support. Document is immediately accessible via web interface.</description>

    <use_case>Creating formatted documents from Markdown (LLM outputs, reports, documentation). Supports headings, lists, tables, bold, italic, links, code blocks, images, and more. Perfect for automated document generation with rich formatting.</use_case>

    <limitation>Requires 'markdown' Python library (install with: pip install markdown). Images must be publicly accessible URLs or base64 encoded. Very large documents (>10MB) may fail.</limitation>

    <failure_cases>Fails when user lacks Google Drive write permissions, if title exceeds character limits, if storage quota is exceeded, or if 'markdown' library is not installed.</failure_cases>

    Args:
        title: The title of the new document
        content: Markdown formatted text (supports full Markdown syntax including tables, code blocks, etc.)
        user_google_email: Optional user email for context
        folder_id: Optional parent folder ID to create document in

    Returns:
        CreateDocResponse: Structured response with document creation details.
    """
    logger.info(f"[create_doc] Invoked. Email: '{user_google_email}', Title='{title}', Content length: {len(content)}")

    if not content:
        # Create empty document using Docs API for backward compatibility
        # Fall back to basic creation
        logger.info(f"[create_doc] No content provided, creating empty document")

        # We need to import docs service for empty doc creation
        from auth.google_auth import get_service_with_user_creds
        docs_service = await get_service_with_user_creds("docs", ["https://www.googleapis.com/auth/documents"], user_google_email)

        doc = await asyncio.to_thread(docs_service.documents().create(body={'title': title}).execute)
        doc_id = doc.get('documentId')
        link = f"https://docs.google.com/document/d/{doc_id}/edit"
    else:
        # Convert Markdown to HTML
        logger.info(f"[create_doc] Converting Markdown to HTML...")
        html_content = markdown_to_html(content)
        logger.info(f"[create_doc] HTML generated ({len(html_content)} characters)")

        # Create document from HTML using Drive API multipart upload
        logger.info(f"[create_doc] Uploading HTML to create formatted document...")
        result = await create_doc_from_html(
            drive_service=service,
            title=title,
            html_content=html_content,
            folder_id=folder_id
        )

        doc_id = result.get('id')
        link = result.get('webViewLink', f"https://docs.google.com/document/d/{doc_id}/edit")

    msg = f"Created Google Doc '{title}' (ID: {doc_id}) for {user_google_email}. Link: {link}"
    logger.info(f"[create_doc] {msg}")

    return CreateDocResponse(
        success=True,
        document_id=doc_id,
        title=title,
        web_view_link=link,
        message=msg
    )


def parse_inline_markdown(text: str) -> List[Dict]:
    """
    Parse inline Markdown formatting (bold, italic, links).

    Returns a list of text segments with their formatting.
    Each segment has: {'text': str, 'bold': bool, 'italic': bool, 'link': str|None}
    """
    import re

    segments = []
    remaining = text

    # Pattern to match bold, italic, and links
    # Order matters: try to match longer patterns first
    pattern = r'(\*\*\*(.+?)\*\*\*|___(.+?)___|__(.+?)__|_(.+?)_|\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^\)]+)\))'

    last_end = 0
    for match in re.finditer(pattern, remaining):
        # Add plain text before this match
        if match.start() > last_end:
            plain = remaining[last_end:match.start()]
            if plain:
                segments.append({'text': plain, 'bold': False, 'italic': False, 'link': None})

        # Determine the type of formatting
        full_match = match.group(0)

        # Bold + Italic (*** or ___)
        if match.group(2):  # ***text***
            segments.append({'text': match.group(2), 'bold': True, 'italic': True, 'link': None})
        elif match.group(3):  # ___text___
            segments.append({'text': match.group(3), 'bold': True, 'italic': True, 'link': None})
        # Bold (** or __)
        elif match.group(4):  # __text__
            segments.append({'text': match.group(4), 'bold': True, 'italic': False, 'link': None})
        elif match.group(6):  # **text**
            segments.append({'text': match.group(6), 'bold': True, 'italic': False, 'link': None})
        # Italic (* or _)
        elif match.group(5):  # _text_
            segments.append({'text': match.group(5), 'bold': False, 'italic': True, 'link': None})
        elif match.group(7):  # *text*
            segments.append({'text': match.group(7), 'bold': False, 'italic': True, 'link': None})
        # Link [text](url)
        elif match.group(8) and match.group(9):
            segments.append({'text': match.group(8), 'bold': False, 'italic': False, 'link': match.group(9)})

        last_end = match.end()

    # Add remaining plain text
    if last_end < len(remaining):
        plain = remaining[last_end:]
        if plain:
            segments.append({'text': plain, 'bold': False, 'italic': False, 'link': None})

    return segments if segments else [{'text': text, 'bold': False, 'italic': False, 'link': None}]


def parse_markdown_to_elements(markdown_text: str) -> List[Dict]:
    """
    Parse Markdown text into document elements.

    Supports:
    - Headings (# H1, ## H2, ### H3)
    - Paragraphs with inline formatting
    - Lists (- or * for unordered, 1. for ordered)
    - Images (![alt](url))
    - Bold (**text** or __text__)
    - Italic (*text* or _text_)
    - Links ([text](url))

    Args:
        markdown_text: Markdown formatted text

    Returns:
        List of elements with type and content
    """
    import re

    elements = []
    lines = markdown_text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            i += 1
            continue

        # Heading (# H1, ## H2, ### H3)
        heading_match = re.match(r'^(#{1,3})\s+(.+)$', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            # Parse inline formatting in heading text
            segments = parse_inline_markdown(text)
            elements.append({
                'type': 'heading',
                'level': level,
                'segments': segments
            })
            i += 1
            continue

        # Image (![alt](url) or ![alt](url "title"))
        image_match = re.match(r'^!\[([^\]]*)\]\(([^\s\)]+)(?:\s+"([^"]+)")?\)$', stripped)
        if image_match:
            alt_text = image_match.group(1)
            url = image_match.group(2)
            title = image_match.group(3) or alt_text
            elements.append({
                'type': 'image',
                'url': url,
                'alt': alt_text,
                'title': title
            })
            i += 1
            continue

        # Unordered list (- or *)
        if re.match(r'^[\-\*]\s+', stripped):
            list_items = []
            while i < len(lines) and re.match(r'^[\-\*]\s+', lines[i].strip()):
                item_text = re.sub(r'^[\-\*]\s+', '', lines[i].strip())
                # Parse inline formatting in list item
                segments = parse_inline_markdown(item_text)
                list_items.append(segments)
                i += 1
            elements.append({
                'type': 'list',
                'ordered': False,
                'items': list_items
            })
            continue

        # Ordered list (1. 2. 3.)
        if re.match(r'^\d+\.\s+', stripped):
            list_items = []
            while i < len(lines) and re.match(r'^\d+\.\s+', lines[i].strip()):
                item_text = re.sub(r'^\d+\.\s+', '', lines[i].strip())
                # Parse inline formatting in list item
                segments = parse_inline_markdown(item_text)
                list_items.append(segments)
                i += 1
            elements.append({
                'type': 'list',
                'ordered': True,
                'items': list_items
            })
            continue

        # Horizontal rule (---, ***, ___)
        if re.match(r'^(\-{3,}|\*{3,}|_{3,})$', stripped):
            elements.append({
                'type': 'divider'
            })
            i += 1
            continue

        # Regular paragraph - collect consecutive lines
        paragraph_lines = []
        while i < len(lines):
            current = lines[i].strip()

            # Stop at empty line, heading, list, image, or divider
            if (not current or
                re.match(r'^#{1,3}\s+', current) or
                re.match(r'^[\-\*]\s+', current) or
                re.match(r'^\d+\.\s+', current) or
                re.match(r'^!\[', current) or
                re.match(r'^(\-{3,}|\*{3,}|_{3,})$', current)):
                break

            paragraph_lines.append(current)
            i += 1

        if paragraph_lines:
            paragraph_text = ' '.join(paragraph_lines)
            # Parse inline formatting in paragraph
            segments = parse_inline_markdown(paragraph_text)
            elements.append({
                'type': 'paragraph',
                'segments': segments
            })

    return elements


def build_requests_from_elements(elements: List[Dict], start_index: int = 1) -> tuple:
    """
    Build Google Docs API requests from parsed Markdown elements.

    Returns:
        tuple: (requests, current_index, images_count)
    """
    requests = []
    current_index = start_index
    images_count = 0

    for elem in elements:
        elem_type = elem.get('type')

        if elem_type == 'heading':
            level = elem['level']
            segments = elem['segments']

            # Insert text
            text_content = ''.join(seg['text'] for seg in segments) + '\n'
            text_start = current_index

            requests.append({
                'insertText': {
                    'location': {'index': current_index},
                    'text': text_content
                }
            })
            current_index += len(text_content)

            # Apply heading style to the paragraph
            heading_style = f'HEADING_{level}'
            requests.append({
                'updateParagraphStyle': {
                    'range': {
                        'startIndex': text_start,
                        'endIndex': current_index - 1
                    },
                    'paragraphStyle': {
                        'namedStyleType': heading_style
                    },
                    'fields': 'namedStyleType'
                }
            })

            # Apply inline formatting (bold, italic, links)
            segment_index = text_start
            for seg in segments:
                seg_len = len(seg['text'])
                if seg_len > 0 and (seg['bold'] or seg['italic'] or seg['link']):
                    text_style = {}
                    fields = []

                    if seg['bold']:
                        text_style['bold'] = True
                        fields.append('bold')
                    if seg['italic']:
                        text_style['italic'] = True
                        fields.append('italic')
                    if seg['link']:
                        text_style['link'] = {'url': seg['link']}
                        fields.append('link')

                    if text_style:
                        requests.append({
                            'updateTextStyle': {
                                'range': {
                                    'startIndex': segment_index,
                                    'endIndex': segment_index + seg_len
                                },
                                'textStyle': text_style,
                                'fields': ','.join(fields)
                            }
                        })
                segment_index += seg_len

        elif elem_type == 'paragraph':
            segments = elem['segments']

            # Insert text
            text_content = ''.join(seg['text'] for seg in segments) + '\n'
            text_start = current_index

            requests.append({
                'insertText': {
                    'location': {'index': current_index},
                    'text': text_content
                }
            })
            current_index += len(text_content)

            # Apply inline formatting (bold, italic, links)
            segment_index = text_start
            for seg in segments:
                seg_len = len(seg['text'])
                if seg_len > 0 and (seg['bold'] or seg['italic'] or seg['link']):
                    text_style = {}
                    fields = []

                    if seg['bold']:
                        text_style['bold'] = True
                        fields.append('bold')
                    if seg['italic']:
                        text_style['italic'] = True
                        fields.append('italic')
                    if seg['link']:
                        text_style['link'] = {'url': seg['link']}
                        fields.append('link')

                    if text_style:
                        requests.append({
                            'updateTextStyle': {
                                'range': {
                                    'startIndex': segment_index,
                                    'endIndex': segment_index + seg_len
                                },
                                'textStyle': text_style,
                                'fields': ','.join(fields)
                            }
                        })
                segment_index += seg_len

        elif elem_type == 'image':
            url = elem['url']
            title = elem.get('title', elem.get('alt', 'Image'))

            # Insert image inline without caption
            requests.append({
                'insertInlineImage': {
                    'location': {'index': current_index},
                    'uri': url,
                    'objectSize': {
                        'width': {
                            'magnitude': 400,
                            'unit': 'PT'
                        }
                    }
                }
            })
            current_index += 1
            images_count += 1

            # Add newline after image
            requests.append({
                'insertText': {
                    'location': {'index': current_index},
                    'text': '\n'
                }
            })
            current_index += 1

        elif elem_type == 'list':
            items = elem['items']
            ordered = elem['ordered']

            list_start = current_index
            list_ranges = []

            # Insert all list items
            for item_segments in items:
                item_start = current_index
                item_text = ''.join(seg['text'] for seg in item_segments) + '\n'

                requests.append({
                    'insertText': {
                        'location': {'index': current_index},
                        'text': item_text
                    }
                })
                current_index += len(item_text)

                # Track the range for this list item
                list_ranges.append({
                    'startIndex': item_start,
                    'endIndex': current_index - 1,
                    'segments': item_segments,
                    'text_start': item_start
                })

            # Apply bullet/numbering to list items
            for list_range in list_ranges:
                requests.append({
                    'createParagraphBullets': {
                        'range': {
                            'startIndex': list_range['startIndex'],
                            'endIndex': list_range['endIndex']
                        },
                        'bulletPreset': 'NUMBERED_DECIMAL_ALPHA_ROMAN' if ordered else 'BULLET_DISC_CIRCLE_SQUARE'
                    }
                })

                # Apply inline formatting to list items
                segment_index = list_range['text_start']
                for seg in list_range['segments']:
                    seg_len = len(seg['text'])
                    if seg_len > 0 and (seg['bold'] or seg['italic'] or seg['link']):
                        text_style = {}
                        fields = []

                        if seg['bold']:
                            text_style['bold'] = True
                            fields.append('bold')
                        if seg['italic']:
                            text_style['italic'] = True
                            fields.append('italic')
                        if seg['link']:
                            text_style['link'] = {'url': seg['link']}
                            fields.append('link')

                        if text_style:
                            requests.append({
                                'updateTextStyle': {
                                    'range': {
                                        'startIndex': segment_index,
                                        'endIndex': segment_index + seg_len
                                    },
                                    'textStyle': text_style,
                                    'fields': ','.join(fields)
                                }
                            })
                    segment_index += seg_len

        elif elem_type == 'divider':
            # Insert horizontal rule as text
            divider_text = '\n' + '─' * 60 + '\n'
            requests.append({
                'insertText': {
                    'location': {'index': current_index},
                    'text': divider_text
                }
            })
            current_index += len(divider_text)

    return requests, current_index, images_count


async def append_html_to_doc(
    drive_service,
    document_id: str,
    html_content: str
) -> Dict[str, Any]:
    """
    Append HTML content to an existing Google Doc by exporting, appending, and re-uploading.

    Args:
        drive_service: Authenticated Google Drive service
        document_id: ID of the document to append to
        html_content: HTML content to append (just the body content, not full HTML doc)

    Returns:
        Dict with update results
    """
    # Step 1: Export existing document as HTML
    logger.info(f"[append_html_to_doc] Exporting document {document_id} as HTML")

    def _export_html():
        request = drive_service.files().export_media(
            fileId=document_id,
            mimeType='text/html'
        )
        return request.execute().decode('utf-8')

    existing_html = await asyncio.to_thread(_export_html)

    # Step 2: Parse and append new content
    # Find the closing body tag and insert before it
    import re

    # Extract just the body content from html_content
    body_match = re.search(r'<body>(.*?)</body>', html_content, re.DOTALL)
    if body_match:
        new_content = body_match.group(1)
    else:
        new_content = html_content

    # Insert new content before closing </body>
    if '</body>' in existing_html:
        updated_html = existing_html.replace('</body>', f'{new_content}</body>')
    else:
        # If no body tag, just append
        updated_html = existing_html + new_content

    # Step 3: Re-upload the updated HTML
    logger.info(f"[append_html_to_doc] Re-uploading updated document")

    boundary = 'boundary_marker'
    metadata = json.dumps({
        'mimeType': 'application/vnd.google-apps.document'
    })

    # Build multipart body
    body_parts = [
        f'--{boundary}',
        'Content-Type: application/json; charset=UTF-8',
        '',
        metadata,
        f'--{boundary}',
        'Content-Type: text/html; charset=UTF-8',
        '',
        updated_html,
        f'--{boundary}--'
    ]

    body = '\r\n'.join(body_parts)

    # Upload update
    def _update_doc():
        import urllib.parse
        import urllib.request

        url = f'https://www.googleapis.com/upload/drive/v3/files/{document_id}'
        params = {'uploadType': 'multipart'}
        full_url = url + '?' + urllib.parse.urlencode(params)

        headers = {
            'Content-Type': f'multipart/related; boundary={boundary}'
        }

        req = urllib.request.Request(
            full_url,
            data=body.encode('utf-8'),
            headers=headers,
            method='PATCH'
        )

        # Add authorization
        credentials = drive_service._http.credentials
        credentials.apply(headers)
        req.headers.update(headers)

        response = urllib.request.urlopen(req)
        return json.loads(response.read().decode('utf-8'))

    result = await asyncio.to_thread(_update_doc)

    logger.info(f"[append_html_to_doc] Document updated successfully")

    return result


@server.tool
@require_google_service("drive", "drive_file")
@handle_http_errors("insert_markdown_content")
async def append_content(
    service,
    ctx: Context,
    document_id: str,
    content: str,
    index: Optional[int] = None,
    user_google_email: Optional[str] = None,
) -> InsertMarkdownResponse:
    """
    <description>Appends Markdown formatted content to a Google Docs document. Converts Markdown to HTML and merges with existing document. Supports full Markdown syntax including tables, code blocks, formatting, and images.</description>

    <use_case>Appending reports to existing documents, adding formatted content from LLM outputs, extending documentation, or automated content updates with rich formatting.</use_case>

    <limitation>Re-uploads entire document (may affect version history). Images must be publicly accessible URLs. Very large documents (>10MB) may be slow. The 'index' parameter is deprecated in this version.</limitation>

    <failure_cases>Fails with invalid document IDs, documents the user cannot edit, inaccessible image URLs, or if 'markdown' library is not installed.</failure_cases>

    Args:
        document_id: The ID of the document to append content to
        content: Markdown formatted text (supports headings, lists, tables, images, code blocks, etc.)
        index: Deprecated - content is always appended to the end
        user_google_email: Optional user email for context

    Returns:
        InsertMarkdownResponse: Structured response with insertion details.
    """
    logger.info(f"[append_content] Invoked. Document ID: '{document_id}', Content length: {len(content)}")

    if index is not None:
        logger.warning(f"[append_content] 'index' parameter is deprecated and will be ignored. Content will be appended to the end.")

    try:
        # Convert Markdown to HTML
        logger.info(f"[append_content] Converting Markdown to HTML...")
        html_content = markdown_to_html(content)

        # Count elements for response (approximate)
        import re
        elements_count = len(re.findall(r'<(h[1-6]|p|ul|ol|table|blockquote|pre|hr)', html_content))
        images_count = len(re.findall(r'<img', html_content))

        logger.info(f"[append_content] HTML generated with ~{elements_count} elements, {images_count} images")

        # Append HTML to document
        logger.info(f"[append_content] Appending content to document...")
        result = await append_html_to_doc(
            drive_service=service,
            document_id=document_id,
            html_content=html_content
        )

        link = f"https://docs.google.com/document/d/{document_id}/edit"
        msg = f"Successfully appended Markdown content to document. ~{elements_count} elements added ({images_count} images)."

        logger.info(f"[append_content] {msg}")

        return InsertMarkdownResponse(
            success=True,
            document_id=document_id,
            elements_inserted=elements_count,
            images_inserted=images_count,
            start_index=0,  # Not applicable in this implementation
            end_index=0,    # Not applicable in this implementation
            web_view_link=link,
            message=msg
        )

    except Exception as e:
        error_msg = f"Failed to append Markdown content: {str(e)}"
        logger.error(f"[append_content] {error_msg}", exc_info=True)
        raise Exception(error_msg)


# Create comment management tools for documents
_comment_tools = create_comment_tools("document", "document_id")

# Extract and register the functions
read_doc_comments = _comment_tools['read_comments']
create_doc_comment = _comment_tools['create_comment']
reply_to_comment = _comment_tools['reply_to_comment']
resolve_comment = _comment_tools['resolve_comment']

if __name__ == '__main__':
    asyncio.run(get_doc_content(drive_service="drive", docs_service="docs", document_id="18-52JXU073R9wQ6ip-MrKMjHVq2QgR1VIEFXgMGyuI8"))
