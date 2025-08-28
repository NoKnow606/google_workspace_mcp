"""
Google Sheets MCP Tools

This module provides MCP tools for interacting with Google Sheets API.
"""

import logging
import asyncio
from typing import List, Optional

from mcp import types
from googleapiclient.errors import HttpError

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors
from core.comments import create_comment_tools
from fastmcp import Context

# Configure module logger
logger = logging.getLogger(__name__)


@server.tool
@require_google_service("drive", "drive_read")
@handle_http_errors("list_spreadsheets")
async def list_spreadsheets(
    service,
    ctx: Context,
    user_google_email: Optional[str] = None,
    max_results: int = 25,
):
    """
    <description>Lists Google Sheets spreadsheets from Drive ordered by modification time, showing file names, IDs, and last modified timestamps. Returns up to 25 spreadsheets by default.</description>
    
    <use_case>Finding existing spreadsheets for data analysis, locating recently modified sheets for collaborative work, or getting spreadsheet IDs for further operations.</use_case>
    
    <limitation>Limited to Google Sheets format only - excludes Excel files or other formats. Shows only files user has access to, not all organizational spreadsheets.</limitation>
    
    <failure_cases>Fails if user lacks Google Drive access permissions, if Drive API quotas are exceeded, or during temporary Google Drive service outages.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        max_results (int): Maximum number of spreadsheets to return. Defaults to 25.

    Returns:
        str: A formatted list of spreadsheet files (name, ID, modified time).
    """
    logger.info(f"[list_spreadsheets] Invoked. Email: '{user_google_email}'")

    files_response = await asyncio.to_thread(
        service.files()
        .list(
            q="mimeType='application/vnd.google-apps.spreadsheet'",
            pageSize=max_results,
            fields="files(id,name,modifiedTime,webViewLink)",
            orderBy="modifiedTime desc",
        )
        .execute
    )

    files = files_response.get("files", [])
    if not files:
        return f"No spreadsheets found for {user_google_email}."

    spreadsheets_list = [
        f"- \"{file['name']}\" (ID: {file['id']}) | Modified: {file.get('modifiedTime', 'Unknown')} | Link: {file.get('webViewLink', 'No link')}"
        for file in files
    ]

    text_output = (
        f"Successfully listed {len(files)} spreadsheets for {user_google_email}:\n"
        + "\n".join(spreadsheets_list)
    )

    logger.info(f"Successfully listed {len(files)} spreadsheets for {user_google_email}.")
    return text_output


@server.tool
@require_google_service("sheets", "sheets_read")
@handle_http_errors("get_spreadsheet_info")
async def get_spreadsheet_info(
    service,
    ctx: Context,
    spreadsheet_id: str,
    user_google_email: Optional[str] = None,
):
    """
    <description>Retrieves spreadsheet metadata including title, sheet names, sheet IDs, and grid dimensions (rows/columns) for each sheet. Shows spreadsheet structure without cell data.</description>
    
    <use_case>Understanding spreadsheet organization before data operations, getting sheet names for range specifications, or analyzing spreadsheet structure for automation.</use_case>
    
    <limitation>Returns structure metadata only - no cell values or formulas. Cannot retrieve information for spreadsheets without proper access permissions.</limitation>
    
    <failure_cases>Fails with invalid spreadsheet IDs, spreadsheets the user cannot access due to sharing restrictions, or spreadsheets that have been deleted.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        spreadsheet_id (str): The ID of the spreadsheet to get info for. Required.

    Returns:
        str: Formatted spreadsheet information including title and sheets list.
    """
    logger.info(f"[get_spreadsheet_info] Invoked. Email: '{user_google_email}', Spreadsheet ID: {spreadsheet_id}")

    spreadsheet = await asyncio.to_thread(
        service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute
    )

    title = spreadsheet.get("properties", {}).get("title", "Unknown")
    sheets = spreadsheet.get("sheets", [])

    sheets_info = []
    for sheet in sheets:
        sheet_props = sheet.get("properties", {})
        sheet_name = sheet_props.get("title", "Unknown")
        sheet_id = sheet_props.get("sheetId", "Unknown")
        grid_props = sheet_props.get("gridProperties", {})
        rows = grid_props.get("rowCount", "Unknown")
        cols = grid_props.get("columnCount", "Unknown")

        sheets_info.append(
            f"  - \"{sheet_name}\" (ID: {sheet_id}) | Size: {rows}x{cols}"
        )

    text_output = (
        f"Spreadsheet: \"{title}\" (ID: {spreadsheet_id})\n"
        f"Sheets ({len(sheets)}):\n"
        + "\n".join(sheets_info) if sheets_info else "  No sheets found"
    )

    logger.info(f"Successfully retrieved info for spreadsheet {spreadsheet_id} for {user_google_email}.")
    return text_output


@server.tool
@require_google_service("sheets", "sheets_read")
@handle_http_errors("read_sheet_values")
async def read_sheet_values(
    service,
    ctx: Context,
    spreadsheet_id: str,
    user_google_email: Optional[str] = None,
    range_name: str = "A1:Z1000",
):
    """
    <description>Extracts cell values from a specified range in Google Sheets, returning formatted data as text. Handles up to 1000 rows efficiently with support for formulas, numbers, and text values.</description>
    
    <use_case>Reading spreadsheet data for analysis, extracting specific data ranges for processing, or getting current values from collaborative sheets for reporting.</use_case>
    
    <limitation>Limited to 1000 rows per request for performance. Returns calculated formula results, not formulas themselves. Cannot read protected ranges without proper permissions.</limitation>
    
    <failure_cases>Fails with invalid spreadsheet IDs or range specifications, ranges in protected sheets without access, or when spreadsheet contains unsupported data types.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        range_name (str): The range to read (e.g., "Sheet1!A1:D10", "A1:D10"). Defaults to "A1:Z100". Maximum 100 rows can be retrieved.
    Returns:
        str: The formatted values from the specified range.
    """
    logger.info(f"[read_sheet_values] Invoked. Email: '{user_google_email}', Spreadsheet: {spreadsheet_id}, Range: {range_name}")

    result = await asyncio.to_thread(
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute
    )

    values = result.get("values", [])
    if not values:
        return f"No data found in range '{range_name}' for {user_google_email}."

    # Format the output as a readable table
    formatted_rows = []
    for i, row in enumerate(values, 1):
        # Pad row with empty strings to show structure
        padded_row = row + [""] * max(0, len(values[0]) - len(row)) if values else row
        formatted_rows.append(f"Row {i:2d}: {padded_row}")

    text_output = (
        f"Successfully read {len(values)} rows from range '{range_name}' in spreadsheet {spreadsheet_id} for {user_google_email}:\n"
        + "\n".join(formatted_rows)
    )

    logger.info(f"Successfully read {len(values)} rows for {user_google_email}.")
    return text_output


@server.tool
@require_google_service("sheets", "sheets_write")
@handle_http_errors("modify_sheet_values")
async def modify_sheet_values(
    service,
    ctx: Context,
    spreadsheet_id: str,
    range_name: str,
    user_google_email: Optional[str] = None,
    values: Optional[List[List[str]]] = None,
    value_input_option: str = "USER_ENTERED",
    clear_values: bool = False,
):
    """
    <description>Updates or clears cell values in a specified range of Google Sheets. Supports writing 2D arrays of data, formulas, and numbers with automatic type detection when using USER_ENTERED mode.</description>
    
    <use_case>Updating spreadsheet data from external sources, clearing outdated information, or writing calculated results back to sheets for collaborative workflows.</use_case>
    
    <limitation>Cannot modify protected ranges without edit permissions. Limited by sheet size constraints (~10 million cells). Overwrites existing data within the specified range.</limitation>
    
    <failure_cases>Fails with invalid range specifications, insufficient edit permissions on protected sheets, or when trying to write arrays larger than the target range.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        range_name (str): The range to modify (e.g., "Sheet1!A1:D10", "A1:D10"). Required.
        values (Optional[List[List[str]]]): 2D array of values to write/update. Required unless clear_values=True.
        value_input_option (str): How to interpret input values ("RAW" or "USER_ENTERED"). Defaults to "USER_ENTERED".
        clear_values (bool): If True, clears the range instead of writing values. Defaults to False.

    Returns:
        str: Confirmation message of the successful modification operation.
    """
    operation = "clear" if clear_values else "write"
    logger.info(f"[modify_sheet_values] Invoked. Operation: {operation}, Email: '{user_google_email}', Spreadsheet: {spreadsheet_id}, Range: {range_name}")

    if not clear_values and not values:
        raise Exception("Either 'values' must be provided or 'clear_values' must be True.")

    if clear_values:
        result = await asyncio.to_thread(
            service.spreadsheets()
            .values()
            .clear(spreadsheetId=spreadsheet_id, range=range_name)
            .execute
        )

        cleared_range = result.get("clearedRange", range_name)
        text_output = f"Successfully cleared range '{cleared_range}' in spreadsheet {spreadsheet_id} for {user_google_email}."
        logger.info(f"Successfully cleared range '{cleared_range}' for {user_google_email}.")
    else:
        body = {"values": values}

        result = await asyncio.to_thread(
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                body=body,
            )
            .execute
        )

        updated_cells = result.get("updatedCells", 0)
        updated_rows = result.get("updatedRows", 0)
        updated_columns = result.get("updatedColumns", 0)

        text_output = (
            f"Successfully updated range '{range_name}' in spreadsheet {spreadsheet_id} for {user_google_email}. "
            f"Updated: {updated_cells} cells, {updated_rows} rows, {updated_columns} columns."
        )
        logger.info(f"Successfully updated {updated_cells} cells for {user_google_email}.")

    return text_output


@server.tool
@require_google_service("sheets", "sheets_write")
@handle_http_errors("create_spreadsheet")
async def create_spreadsheet(
    service,
    ctx: Context,
    title: str,
    user_google_email: Optional[str] = None,
    sheet_names: Optional[List[str]] = None,
):
    """
    <description>Creates a new Google Sheets spreadsheet with specified title and optional custom sheet names. Generates an empty spreadsheet ready for data entry with standard 1000x26 grid dimensions.</description>
    
    <use_case>Creating new data collection spreadsheets, setting up project tracking sheets, or initializing data analysis workbooks with predefined sheet structure.</use_case>
    
    <limitation>Creates empty sheets only - no data, formatting, or formulas. Limited to 200 sheets per spreadsheet. Cannot apply templates or import data during creation.</limitation>
    
    <failure_cases>Fails if user lacks Google Sheets creation permissions, if title exceeds character limits, or if Google Drive storage quota is exceeded.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        title (str): The title of the new spreadsheet. Required.
        sheet_names (Optional[List[str]]): List of sheet names to create. If not provided, creates one sheet with default name.

    Returns:
        str: Information about the newly created spreadsheet including ID and URL.
    """
    logger.info(f"[create_spreadsheet] Invoked. Email: '{user_google_email}', Title: {title}")

    spreadsheet_body = {
        "properties": {
            "title": title
        }
    }

    if sheet_names:
        spreadsheet_body["sheets"] = [
            {"properties": {"title": sheet_name}} for sheet_name in sheet_names
        ]

    spreadsheet = await asyncio.to_thread(
        service.spreadsheets().create(body=spreadsheet_body).execute
    )

    spreadsheet_id = spreadsheet.get("spreadsheetId")
    spreadsheet_url = spreadsheet.get("spreadsheetUrl")

    text_output = (
        f"Successfully created spreadsheet '{title}' for {user_google_email}. "
        f"ID: {spreadsheet_id} | URL: {spreadsheet_url}"
    )

    logger.info(f"Successfully created spreadsheet for {user_google_email}. ID: {spreadsheet_id}")
    return text_output


@server.tool
@require_google_service("sheets", "sheets_write")
@handle_http_errors("create_sheet")
async def create_sheet(
    service,
    ctx: Context,
    spreadsheet_id: str,
    sheet_name: str,
    user_google_email: Optional[str] = None,
):
    """
    <description>Adds a new empty sheet tab to an existing Google Sheets spreadsheet with specified name. Creates a standard 1000x26 grid ready for data entry within the existing workbook.</description>
    
    <use_case>Organizing data into separate sheets within a workbook, creating monthly/quarterly data tabs, or setting up different data categories in the same spreadsheet.</use_case>
    
    <limitation>Cannot create sheets with duplicate names within the same spreadsheet. Limited to 200 sheets per spreadsheet. Creates empty sheet only - no data or formatting.</limitation>
    
    <failure_cases>Fails with invalid spreadsheet IDs, duplicate sheet names within the spreadsheet, insufficient edit permissions, or when spreadsheet already has maximum sheet count.</failure_cases>

    Args:
        user_google_email (Optional[str]): The user's Google email address. If not provided, will be automatically detected.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        sheet_name (str): The name of the new sheet. Required.

    Returns:
        str: Confirmation message of the successful sheet creation.
    """
    logger.info(f"[create_sheet] Invoked. Email: '{user_google_email}', Spreadsheet: {spreadsheet_id}, Sheet: {sheet_name}")

    request_body = {
        "requests": [
            {
                "addSheet": {
                    "properties": {
                        "title": sheet_name
                    }
                }
            }
        ]
    }

    response = await asyncio.to_thread(
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request_body)
        .execute
    )

    sheet_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]

    text_output = (
        f"Successfully created sheet '{sheet_name}' (ID: {sheet_id}) in spreadsheet {spreadsheet_id} for {user_google_email}."
    )

    logger.info(f"Successfully created sheet for {user_google_email}. Sheet ID: {sheet_id}")
    return text_output


# Create comment management tools for sheets
_comment_tools = create_comment_tools("spreadsheet", "spreadsheet_id")

# Extract and register the functions
read_sheet_comments = _comment_tools['read_comments']
create_sheet_comment = _comment_tools['create_comment'] 
reply_to_sheet_comment = _comment_tools['reply_to_comment']
resolve_sheet_comment = _comment_tools['resolve_comment']


