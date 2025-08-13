# auth/google_auth.py

import asyncio
import json
import jwt
import logging
import os
import time

from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from auth.scopes import OAUTH_STATE_TO_SESSION_ID_MAP, SCOPES

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Constants
def get_default_credentials_dir():
    """Get the default credentials directory path, preferring user-specific locations."""
    # Check for explicit environment variable override
    if os.getenv("GOOGLE_MCP_CREDENTIALS_DIR"):
        return os.getenv("GOOGLE_MCP_CREDENTIALS_DIR")

    # Use user home directory for credentials storage
    home_dir = os.path.expanduser("~")
    if home_dir and home_dir != "~":  # Valid home directory found
        return os.path.join(home_dir, ".google_workspace_mcp", "credentials")

    # Fallback to current working directory if home directory is not accessible
    return os.path.join(os.getcwd(), ".credentials")


DEFAULT_CREDENTIALS_DIR = get_default_credentials_dir()

# In-memory cache for session credentials, maps session_id to Credentials object
# This is brittle and bad, but our options are limited with Claude in present state.
# This should be more robust in a production system once OAuth2.1 is implemented in client.
_SESSION_CREDENTIALS_CACHE: Dict[str, Credentials] = {}
# Centralized Client Secrets Path Logic
_client_secrets_env = os.getenv("GOOGLE_CLIENT_SECRET_PATH") or os.getenv(
    "GOOGLE_CLIENT_SECRETS"
)
if _client_secrets_env:
    CONFIG_CLIENT_SECRETS_PATH = _client_secrets_env
else:
    # Assumes this file is in auth/ and client_secret.json is in the root
    CONFIG_CLIENT_SECRETS_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "client_secret.json",
    )

# --- Helper Functions ---


def _is_token_expiring_soon(credentials: Credentials, minutes_threshold: int = 5) -> bool:
    """
    Check if credentials will expire within the specified threshold.
    
    Args:
        credentials: The credentials object to check
        minutes_threshold: How many minutes before expiry to consider "expiring soon"
    
    Returns:
        True if token will expire within the threshold, False otherwise
    """
    if not credentials.expiry:
        return False
    
    expiry_threshold = datetime.utcnow() + timedelta(minutes=minutes_threshold)
    return credentials.expiry <= expiry_threshold


def _refresh_credentials_if_needed(
    credentials: Credentials,
    session_id: Optional[str],
    user_google_email: Optional[str] = None,
    credentials_base_dir: str = DEFAULT_CREDENTIALS_DIR,
    force_refresh: bool = False,
    retry_count: int = 1
) -> Optional[Credentials]:
    """
    Refresh credentials if they're expired or expiring soon.
    
    Args:
        credentials: The credentials to potentially refresh
        user_google_email: User's Google email for saving refreshed credentials
        session_id: Session ID for caching refreshed credentials
        credentials_base_dir: Base directory for credential files
        force_refresh: Force refresh even if not expired
        retry_count: Number of retry attempts for transient errors
    
    Returns:
        Refreshed credentials or None if refresh failed
    """
    if not credentials.refresh_token:
        logger.warning(
            f"[_refresh_credentials_if_needed] No refresh token available. User: '{credentials.refresh_token}', Session: '{session_id}'"
        )
        return None
    
    needs_refresh = (
        force_refresh or
        credentials.expired or
        _is_token_expiring_soon(credentials)
    )
    
    if not needs_refresh:
        return credentials
    
    for attempt in range(retry_count):
        try:
            if credentials.expired:
                logger.info(
                    f"[_refresh_credentials_if_needed] Credentials expired. Refreshing (attempt {attempt + 1}/{retry_count}). User: '{credentials.refresh_token}', Session: '{session_id}'"
                )
            else:
                logger.info(
                    f"[_refresh_credentials_if_needed] Credentials expiring soon. Proactively refreshing (attempt {attempt + 1}/{retry_count}). User: '{credentials.refresh_token}', Session: '{session_id}'"
                )
            
            credentials.refresh(Request())
            
            logger.info(
                f"[_refresh_credentials_if_needed] Credentials refreshed successfully. User: '{credentials.refresh_token}', Session: '{session_id}'"
            )
            
            # # Save refreshed credentials
            # if user_google_email:
            #     save_credentials_to_file(
            #         user_google_email, credentials, credentials_base_dir
            #     )
            # if session_id:
            #     save_credentials_to_session(session_id, credentials)
            
            return credentials
        
        except RefreshError as e:
            logger.warning(
                f"[_refresh_credentials_if_needed] RefreshError - token expired/revoked: {e}. User: '{credentials.refresh_token}', Session: '{session_id}'. Re-authentication required."
            )
            return None  # Don't retry RefreshError - indicates token is revoked
        
        except Exception as e:
            if attempt < retry_count - 1:
                logger.warning(
                    f"[_refresh_credentials_if_needed] Transient error refreshing credentials (attempt {attempt + 1}/{retry_count}): {e}. User: '{credentials.refresh_token}', Session: '{session_id}'. Retrying..."
                )
                # Brief backoff before retry
                time.sleep(1)
                continue
            else:
                logger.error(
                    f"[_refresh_credentials_if_needed] Failed to refresh credentials after {retry_count} attempts: {e}. User: '{credentials.refresh_token}', Session: '{session_id}'",
                    exc_info=True,
                )
                return None
    
    return None


def validate_and_refresh_credentials(
    credentials: Credentials,
    user_google_email: Optional[str] = None,
    session_id: Optional[str] = None,
    credentials_base_dir: str = DEFAULT_CREDENTIALS_DIR
) -> bool:
    """
    Validate credentials and refresh if needed before performing operations.
    
    This function should be called before important API operations to ensure
    credentials are valid and won't fail due to expiration.
    
    Args:
        credentials: The credentials to validate
        user_google_email: User's Google email for saving refreshed credentials
        session_id: Session ID for caching refreshed credentials
        credentials_base_dir: Base directory for credential files
    
    Returns:
        True if credentials are valid (possibly after refresh), False otherwise
    """
    if not credentials:
        logger.warning("[validate_and_refresh_credentials] No credentials provided")
        return False
    
    if not credentials.refresh_token:
        logger.warning(
            f"[validate_and_refresh_credentials] No refresh token available. User: '{user_google_email}', Session: '{session_id}'"
        )
        return credentials.valid
    
    # Check if credentials are valid or can be refreshed
    if credentials.valid and not _is_token_expiring_soon(credentials):
        logger.debug(
            f"[validate_and_refresh_credentials] Credentials are valid and not expiring soon. User: '{user_google_email}', Session: '{session_id}'"
        )
        return True
    
    # Attempt to refresh credentials
    refreshed_credentials = _refresh_credentials_if_needed(
        credentials=credentials,
        user_google_email=user_google_email,
        session_id=session_id,
        credentials_base_dir=credentials_base_dir,
        retry_count=2  # Allow more retries for explicit validation
    )
    
    if refreshed_credentials and refreshed_credentials.valid:
        logger.debug(
            f"[validate_and_refresh_credentials] Credentials successfully refreshed. User: '{user_google_email}', Session: '{session_id}'"
        )
        # Update the original credentials object with the refreshed values
        credentials.token = refreshed_credentials.token
        credentials.expiry = refreshed_credentials.expiry
        return True
    
    logger.warning(
        f"[validate_and_refresh_credentials] Failed to validate/refresh credentials. User: '{user_google_email}', Session: '{session_id}'"
    )
    return False


def get_credentials_status(credentials: Credentials) -> Dict[str, Any]:
    """
    Get detailed status information about credentials.
    
    Args:
        credentials: The credentials to check
    
    Returns:
        Dictionary with credential status information
    """
    if not credentials:
        return {
            "valid": False,
            "expired": True,
            "has_refresh_token": False,
            "expiry": None,
            "expires_in_minutes": None,
            "expiring_soon": False,
            "status": "no_credentials"
        }
    
    expires_in_minutes = None
    expiring_soon = False
    
    if credentials.expiry:
        time_until_expiry = credentials.expiry - datetime.utcnow()
        expires_in_minutes = time_until_expiry.total_seconds() / 60
        expiring_soon = _is_token_expiring_soon(credentials)
    
    status = "valid"
    if credentials.expired:
        status = "expired"
    elif expiring_soon:
        status = "expiring_soon"
    elif not credentials.valid:
        status = "invalid"
    
    return {
        "valid": credentials.valid,
        "expired": credentials.expired,
        "has_refresh_token": credentials.refresh_token is not None,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        "expires_in_minutes": expires_in_minutes,
        "expiring_soon": expiring_soon,
        "status": status
    }


# --- Helper Functions ---


def _find_any_credentials(
    base_dir: str = DEFAULT_CREDENTIALS_DIR,
) -> Optional[Credentials]:
    """
    Find and load any valid credentials from the credentials directory.
    Used in single-user mode to bypass session-to-OAuth mapping.

    Returns:
        First valid Credentials object found, or None if none exist.
    """
    if not os.path.exists(base_dir):
        logger.info(f"[single-user] Credentials directory not found: {base_dir}")
        return None

    # Scan for any .json credential files
    for filename in os.listdir(base_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(base_dir, filename)
            try:
                with open(filepath, "r") as f:
                    creds_data = json.load(f)
                credentials = Credentials(
                    token=creds_data.get("token"),
                    refresh_token=creds_data.get("refresh_token"),
                    token_uri=creds_data.get("token_uri"),
                    client_id=creds_data.get("client_id"),
                    client_secret=creds_data.get("client_secret"),
                    scopes=creds_data.get("scopes"),
                )
                logger.info(f"[single-user] Found credentials in {filepath}")
                return credentials
            except (IOError, json.JSONDecodeError, KeyError) as e:
                logger.warning(
                    f"[single-user] Error loading credentials from {filepath}: {e}"
                )
                continue

    logger.info(f"[single-user] No valid credentials found in {base_dir}")
    return None


def _get_user_credential_path(
    user_google_email: str, base_dir: str = DEFAULT_CREDENTIALS_DIR
):
    """Constructs the path to a user's credential file."""
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
        logger.info(f"Created credentials directory: {base_dir}")
    return os.path.join(base_dir, f"{user_google_email}.json")


def save_credentials_to_file(
    user_google_email: str,
    credentials: Credentials,
    base_dir: str = DEFAULT_CREDENTIALS_DIR,
):
    """Saves user credentials to a file."""
    creds_path = _get_user_credential_path(user_google_email, base_dir)
    creds_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }
    try:
        with open(creds_path, "w") as f:
            json.dump(creds_data, f)
        logger.info(f"Credentials saved for user {user_google_email} to {creds_path}")
    except IOError as e:
        logger.error(
            f"Error saving credentials for user {user_google_email} to {creds_path}: {e}"
        )
        raise


def save_credentials_to_session(session_id: str, credentials: Credentials):
    """Saves user credentials to the in-memory session cache."""
    _SESSION_CREDENTIALS_CACHE[session_id] = credentials
    logger.debug(f"Credentials saved to session cache for session_id: {session_id}")


def load_credentials_from_file(
    user_google_email: str, base_dir: str = DEFAULT_CREDENTIALS_DIR
) -> Optional[Credentials]:
    """Loads user credentials from a file."""
    creds_path = _get_user_credential_path(user_google_email, base_dir)
    if not os.path.exists(creds_path):
        logger.info(
            f"No credentials file found for user {user_google_email} at {creds_path}"
        )
        return None

    try:
        with open(creds_path, "r") as f:
            creds_data = json.load(f)

        # Parse expiry if present
        expiry = None
        if creds_data.get("expiry"):
            try:
                expiry = datetime.fromisoformat(creds_data["expiry"])
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Could not parse expiry time for {user_google_email}: {e}"
                )

        credentials = Credentials(
            token=creds_data.get("token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri=creds_data.get("token_uri"),
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
            scopes=creds_data.get("scopes"),
            expiry=expiry,
        )
        logger.debug(
            f"Credentials loaded for user {user_google_email} from {creds_path}"
        )
        return credentials
    except (IOError, json.JSONDecodeError, KeyError) as e:
        logger.error(
            f"Error loading or parsing credentials for user {user_google_email} from {creds_path}: {e}"
        )
        return None


def load_credentials_from_session(session_id: str) -> Optional[Credentials]:
    """Loads user credentials from the in-memory session cache."""
    credentials = _SESSION_CREDENTIALS_CACHE.get(session_id)
    if credentials:
        logger.debug(
            f"Credentials loaded from session cache for session_id: {session_id}"
        )
    else:
        logger.debug(
            f"No credentials found in session cache for session_id: {session_id}"
        )
    return credentials


def load_credentials_from_env() -> Optional[Credentials]:
    """
    Load credentials directly from environment variables.
    
    Environment variables used:
        - GOOGLE_OAUTH_CLIENT_ID: OAuth 2.0 client ID
        - GOOGLE_OAUTH_CLIENT_SECRET: OAuth 2.0 client secret
        - GOOGLE_OAUTH_REFRESH_TOKEN: OAuth 2.0 refresh token
        - GOOGLE_OAUTH_ACCESS_TOKEN: (optional) OAuth 2.0 access token
        - GOOGLE_OAUTH_TOKEN_URI: (optional) OAuth 2.0 token URI
        - GOOGLE_OAUTH_SCOPES: (optional) Comma-separated list of scopes
    
    Returns:
        Credentials object created from environment variables, or None if required variables are missing
    """
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") 
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")
    access_token = os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN")
    token_uri = os.getenv("GOOGLE_OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token")
    scopes_str = os.getenv("GOOGLE_OAUTH_SCOPES")
    
    # Check required environment variables
    if not client_id or not client_secret or not refresh_token:
        missing_vars = []
        if not client_id:
            missing_vars.append("GOOGLE_OAUTH_CLIENT_ID")
        if not client_secret:
            missing_vars.append("GOOGLE_OAUTH_CLIENT_SECRET")
        if not refresh_token:
            missing_vars.append("GOOGLE_OAUTH_REFRESH_TOKEN")
        
        logger.debug(f"Missing required environment variables for credentials: {missing_vars}")
        return None
    
    # Parse scopes if provided, otherwise use default MCP scopes
    scopes = None
    if scopes_str:
        scopes = [scope.strip() for scope in scopes_str.split(",") if scope.strip()]
    else:
        # Use default MCP required scopes if not specified in environment
        from auth.scopes import SCOPES
        scopes = SCOPES
    
    try:
        credentials = Credentials(
            token=access_token,  # May be None, will be refreshed if needed
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes
        )
        
        logger.info("Successfully loaded credentials from environment variables")
        return credentials
        
    except Exception as e:
        logger.error(f"Error creating credentials from environment variables: {e}")
        return None


def get_user_email_from_credentials(credentials: Credentials) -> Optional[str]:
    """
    Get user email from credentials by calling Google's userinfo API.
    
    Args:
        credentials: Valid Google credentials
    
    Returns:
        User email address or None if not available
    """
    if not credentials or not credentials.valid:
        return None
    
    try:
        from googleapiclient.discovery import build
        
        # Build the userinfo service
        service = build('oauth2', 'v2', credentials=credentials)
        
        # Get user info
        user_info = service.userinfo().get().execute()
        
        return user_info.get('email')
        
    except Exception as e:
        logger.warning(f"Failed to get user email from credentials: {e}")
        return None


def get_default_user_email_from_env() -> Optional[str]:
    """
    Get the default user email by creating credentials from environment variables
    and querying Google's userinfo API, with fallback to cached email.
    
    Returns:
        User email address or None if not available
    """
    try:
        # First, try to get from any existing credential file
        credentials_dir = os.path.expanduser("~/.google_workspace_mcp/credentials")
        if os.path.exists(credentials_dir):
            for filename in os.listdir(credentials_dir):
                if filename.endswith('.json') and '@' in filename:
                    # Extract email from filename (e.g., "user@example.com.json" -> "user@example.com")
                    email = filename.replace('.json', '')
                    logger.info(f"Found cached email from credential file: {email}")
                    return email
        
        # If no cached email, try to get from API (with shorter timeout)
        credentials = load_credentials_from_env()
        if not credentials:
            return None
        
        # Force refresh to ensure we have a valid token
        refreshed_credentials = _refresh_credentials_if_needed(
            credentials=credentials,
            user_google_email=None,
            session_id=None,
            force_refresh=True,
            retry_count=1
        )
        
        if not refreshed_credentials or not refreshed_credentials.valid:
            return None
        
        # Try to get user email with timeout protection
        try:
            # Set a shorter timeout for this operation
            import socket
            original_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(5)  # 5 second timeout
            
            email = get_user_email_from_credentials(refreshed_credentials)
            
            socket.setdefaulttimeout(original_timeout)
            
            if email:
                logger.info(f"Successfully retrieved email from API: {email}")
                return email
                
        except Exception as e:
            logger.warning(f"Failed to get user email from API (timeout/error): {e}")
            # Restore original timeout
            socket.setdefaulttimeout(original_timeout)
        
        # Fallback: try a common pattern or return None
        logger.warning("Could not determine user email from environment credentials")
        return None
        
    except Exception as e:
        logger.warning(f"Failed to get default user email from environment: {e}")
        return None


def load_client_secrets_from_env() -> Optional[Dict[str, Any]]:
    """
    Loads the client secrets from environment variables.

    Environment variables used:
        - GOOGLE_OAUTH_CLIENT_ID: OAuth 2.0 client ID
        - GOOGLE_OAUTH_CLIENT_SECRET: OAuth 2.0 client secret
        - GOOGLE_OAUTH_REDIRECT_URI: (optional) OAuth redirect URI
        - GOOGLE_OAUTH_REFRESH_TOKEN: (optional) OAuth 2.0 refresh token

    Returns:
        Client secrets configuration dict compatible with Google OAuth library,
        or None if required environment variables are not set.
    """
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")

    if client_id and client_secret:
        # Create config structure that matches Google client secrets format
        web_config = {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        }

        # Add redirect_uri if provided via environment variable
        if redirect_uri:
            web_config["redirect_uris"] = [redirect_uri]

        # Add refresh_token if provided via environment variable
        if refresh_token:
            web_config["refresh_token"] = refresh_token

        # Return the full config structure expected by Google OAuth library
        config = {"web": web_config}

        logger.info("Loaded OAuth client credentials from environment variables")
        return config

    logger.debug("OAuth client credentials not found in environment variables")
    return None


def load_client_secrets(client_secrets_path: str) -> Dict[str, Any]:
    """
    Loads the client secrets from environment variables (preferred) or from the client secrets file.

    Priority order:
    1. Environment variables (GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET)
    2. File-based credentials at the specified path

    Args:
        client_secrets_path: Path to the client secrets JSON file (used as fallback)

    Returns:
        Client secrets configuration dict

    Raises:
        ValueError: If client secrets file has invalid format
        IOError: If file cannot be read and no environment variables are set
    """
    # First, try to load from environment variables
    env_config = load_client_secrets_from_env()
    if env_config:
        # Extract the "web" config from the environment structure
        return env_config["web"]

    # Fall back to loading from file
    try:
        with open(client_secrets_path, "r") as f:
            client_config = json.load(f)
            # The file usually contains a top-level key like "web" or "installed"
            if "web" in client_config:
                logger.info(
                    f"Loaded OAuth client credentials from file: {client_secrets_path}"
                )
                return client_config["web"]
            elif "installed" in client_config:
                logger.info(
                    f"Loaded OAuth client credentials from file: {client_secrets_path}"
                )
                return client_config["installed"]
            else:
                logger.error(
                    f"Client secrets file {client_secrets_path} has unexpected format."
                )
                raise ValueError("Invalid client secrets file format")
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"Error loading client secrets file {client_secrets_path}: {e}")
        raise


def check_client_secrets() -> Optional[str]:
    """
    Checks for the presence of OAuth client secrets, either as environment
    variables or as a file.

    Returns:
        An error message string if secrets are not found, otherwise None.
    """
    env_config = load_client_secrets_from_env()
    env_credentials = load_credentials_from_env()
    
    # Check if we have complete credentials from environment
    if env_credentials:
        # Validate environment credentials
        is_valid, errors = validate_environment_credentials()
        if not is_valid:
            error_msg = f"Invalid environment credentials: {'; '.join(errors)}"
            logger.error(error_msg)
            return error_msg
        
        logger.info("Found complete OAuth credentials in environment variables")
        return None
    
    # Check if we have client secrets from environment
    if env_config:
        logger.info("Found OAuth client secrets in environment variables")
        return None
    
    # Check if we have client secrets file
    if os.path.exists(CONFIG_CLIENT_SECRETS_PATH):
        logger.info(f"Found OAuth client secrets file at {CONFIG_CLIENT_SECRETS_PATH}")
        return None
    
    # No credentials found anywhere
    logger.error("OAuth client credentials not found in any location")
    return f"""OAuth client credentials not found. Please provide credentials using one of these methods:

1. Environment variables (recommended):
   - For complete credentials: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN
   - For client secrets only: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET

2. Client secrets file:
   - Provide a client secrets file at {CONFIG_CLIENT_SECRETS_PATH}

3. Environment variable path:
   - Set GOOGLE_CLIENT_SECRET_PATH to point to your client secrets file"""


def validate_environment_credentials() -> Tuple[bool, List[str]]:
    """
    Validate environment credentials and return validation results.
    
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")
    access_token = os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN")
    token_uri = os.getenv("GOOGLE_OAUTH_TOKEN_URI")
    scopes_str = os.getenv("GOOGLE_OAUTH_SCOPES")
    
    # Check required variables
    if not client_id:
        errors.append("GOOGLE_OAUTH_CLIENT_ID is required")
    elif not client_id.strip():
        errors.append("GOOGLE_OAUTH_CLIENT_ID cannot be empty")
    
    if not client_secret:
        errors.append("GOOGLE_OAUTH_CLIENT_SECRET is required")
    elif not client_secret.strip():
        errors.append("GOOGLE_OAUTH_CLIENT_SECRET cannot be empty")
    
    if not refresh_token:
        errors.append("GOOGLE_OAUTH_REFRESH_TOKEN is required")
    elif not refresh_token.strip():
        errors.append("GOOGLE_OAUTH_REFRESH_TOKEN cannot be empty")
    
    # Validate optional variables
    if token_uri and not token_uri.startswith("https://"):
        errors.append("GOOGLE_OAUTH_TOKEN_URI must be a valid HTTPS URL")
    
    if scopes_str:
        scopes = [scope.strip() for scope in scopes_str.split(",")]
        if not scopes or any(not scope for scope in scopes):
            errors.append("GOOGLE_OAUTH_SCOPES must be a comma-separated list of non-empty scopes")
    
    # Validate access token format if provided
    if access_token and not access_token.strip():
        errors.append("GOOGLE_OAUTH_ACCESS_TOKEN cannot be empty if provided")
    
    return len(errors) == 0, errors


def check_client_secrets() -> Optional[str]:
    """
    Checks for the presence of OAuth client secrets, either as environment
    variables or as a file.

    Returns:
        An error message string if secrets are not found, otherwise None.
    """
    env_config = load_client_secrets_from_env()
    env_credentials = load_credentials_from_env()
    
    # Check if we have complete credentials from environment
    if env_credentials:
        # Validate environment credentials
        is_valid, errors = validate_environment_credentials()
        if not is_valid:
            error_msg = f"Invalid environment credentials: {'; '.join(errors)}"
            logger.error(error_msg)
            return error_msg
        
        logger.info("Found complete OAuth credentials in environment variables")
        return None
    
    # Check if we have client secrets from environment
    if env_config:
        logger.info("Found OAuth client secrets in environment variables")
        return None
    
    # Check if we have client secrets file
    if os.path.exists(CONFIG_CLIENT_SECRETS_PATH):
        logger.info(f"Found OAuth client secrets file at {CONFIG_CLIENT_SECRETS_PATH}")
        return None
    
    # No credentials found anywhere
    logger.error("OAuth client credentials not found in any location")
    return f"""OAuth client credentials not found. Please provide credentials using one of these methods:

1. Environment variables (recommended):
   - For complete credentials: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN
   - For client secrets only: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET

2. Client secrets file:
   - Provide a client secrets file at {CONFIG_CLIENT_SECRETS_PATH}

3. Environment variable path:
   - Set GOOGLE_CLIENT_SECRET_PATH to point to your client secrets file"""


def create_oauth_flow(
    scopes: List[str], redirect_uri: str, state: Optional[str] = None
) -> Flow:
    """Creates an OAuth flow using environment variables or client secrets file."""
    # Try environment variables first - check for client secrets OR complete credentials
    env_config = load_client_secrets_from_env()
    env_credentials = load_credentials_from_env()
    
    if env_config:
        # Use client config directly
        flow = Flow.from_client_config(
            env_config, scopes=scopes, redirect_uri=redirect_uri, state=state
        )
        logger.debug("Created OAuth flow from environment variables")
        return flow
    
    if env_credentials:
        # If we have complete credentials, create a minimal config for OAuth flow
        # This allows the flow to work even if only complete credentials are provided
        client_config = {
            "web": {
                "client_id": env_credentials.client_id,
                "client_secret": env_credentials.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": env_credentials.token_uri or "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            }
        }
        
        flow = Flow.from_client_config(
            client_config, scopes=scopes, redirect_uri=redirect_uri, state=state
        )
        logger.debug("Created OAuth flow from environment credentials")
        return flow

    # Fall back to file-based config
    if not os.path.exists(CONFIG_CLIENT_SECRETS_PATH):
        # Provide more helpful error message that includes environment variable options
        error_msg = f"""OAuth client credentials not found. Please provide credentials using one of these methods:

1. Environment variables for complete credentials:
   - GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN

2. Environment variables for client secrets only:
   - GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET

3. Client secrets file:
   - Provide a client secrets file at {CONFIG_CLIENT_SECRETS_PATH}

4. Environment variable path:
   - Set GOOGLE_CLIENT_SECRET_PATH to point to your client secrets file"""
   
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    flow = Flow.from_client_secrets_file(
        CONFIG_CLIENT_SECRETS_PATH,
        scopes=scopes,
        redirect_uri=redirect_uri,
        state=state,
    )
    logger.debug(
        f"Created OAuth flow from client secrets file: {CONFIG_CLIENT_SECRETS_PATH}"
    )
    return flow


# --- Core OAuth Logic ---


async def start_auth_flow(
    mcp_session_id: Optional[str],
    user_google_email: Optional[str],
    service_name: str,  # e.g., "Google Calendar", "Gmail" for user messages
    redirect_uri: str,  # Added redirect_uri as a required parameter
):
    """
    Initiates the Google OAuth flow and returns an actionable message for the user.

    Args:
        mcp_session_id: The active MCP session ID.
        user_google_email: The user's specified Google email, if provided.
        service_name: The name of the Google service requiring auth (for user messages).
        redirect_uri: The URI Google will redirect to after authorization.

    Returns:
        A formatted string containing guidance for the LLM/user.

    Raises:
        Exception: If the OAuth flow cannot be initiated.
    """
    initial_email_provided = bool(
        user_google_email
        and user_google_email.strip()
        and user_google_email.lower() != "default"
    )
    user_display_name = (
        f"{service_name} for '{user_google_email}'"
        if initial_email_provided
        else service_name
    )

    logger.info(
        f"[start_auth_flow] Initiating auth for {user_display_name} (session: {mcp_session_id}) with global SCOPES."
    )

    try:
        if "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ and (
            "localhost" in redirect_uri or "127.0.0.1" in redirect_uri
        ):  # Use passed redirect_uri
            logger.warning(
                "OAUTHLIB_INSECURE_TRANSPORT not set. Setting it for localhost/local development."
            )
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        oauth_state = os.urandom(16).hex()
        if mcp_session_id:
            OAUTH_STATE_TO_SESSION_ID_MAP[oauth_state] = mcp_session_id
            logger.info(
                f"[start_auth_flow] Stored mcp_session_id '{mcp_session_id}' for oauth_state '{oauth_state}'."
            )

        flow = create_oauth_flow(
            scopes=SCOPES,  # Use global SCOPES
            redirect_uri=redirect_uri,  # Use passed redirect_uri
            state=oauth_state,
        )

        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        logger.info(
            f"Auth flow started for {user_display_name}. State: {oauth_state}. Advise user to visit: {auth_url}"
        )

        message_lines = [
            f"**ACTION REQUIRED: Google Authentication Needed for {user_display_name}**\n",
            f"To proceed, the user must authorize this application for {service_name} access using all required permissions.",
            "**LLM, please present this exact authorization URL to the user as a clickable hyperlink:**",
            f"Authorization URL: {auth_url}",
            f"Markdown for hyperlink: [Click here to authorize {service_name} access]({auth_url})\n",
            "**LLM, after presenting the link, instruct the user as follows:**",
            "1. Click the link and complete the authorization in their browser.",
        ]
        session_info_for_llm = (
            f" (this will link to your current session {mcp_session_id})"
            if mcp_session_id
            else ""
        )

        if not initial_email_provided:
            message_lines.extend(
                [
                    f"2. After successful authorization{session_info_for_llm}, the browser page will display the authenticated email address.",
                    "   **LLM: Instruct the user to provide you with this email address.**",
                    "3. Once you have the email, **retry their original command, ensuring you include this `user_google_email`.**",
                ]
            )
        else:
            message_lines.append(
                f"2. After successful authorization{session_info_for_llm}, **retry their original command**."
            )

        message_lines.append(
            f"\nThe application will use the new credentials. If '{user_google_email}' was provided, it must match the authenticated account."
        )
        return "\n".join(message_lines)

    except FileNotFoundError as e:
        error_text = f"OAuth client credentials not found: {e}"
        logger.error(error_text, exc_info=True)
        raise Exception(error_text)
    except Exception as e:
        error_text = f"Could not initiate authentication for {user_display_name} due to an unexpected error: {str(e)}"
        logger.error(
            f"Failed to start the OAuth flow for {user_display_name}: {e}",
            exc_info=True,
        )
        raise Exception(error_text)


def handle_auth_callback(
    scopes: List[str],
    authorization_response: str,
    redirect_uri: str,
    credentials_base_dir: str = DEFAULT_CREDENTIALS_DIR,
    session_id: Optional[str] = None,
    client_secrets_path: Optional[
        str
    ] = None,  # Deprecated: kept for backward compatibility
) -> Tuple[str, Credentials]:
    """
    Handles the callback from Google, exchanges the code for credentials,
    fetches user info, determines user_google_email, saves credentials (file & session),
    and returns them.

    Args:
        scopes: List of OAuth scopes requested.
        authorization_response: The full callback URL from Google.
        redirect_uri: The redirect URI.
        credentials_base_dir: Base directory for credential files.
        session_id: Optional MCP session ID to associate with the credentials.
        client_secrets_path: (Deprecated) Path to client secrets file. Ignored if environment variables are set.

    Returns:
        A tuple containing the user_google_email and the obtained Credentials object.

    Raises:
        ValueError: If the state is missing or doesn't match.
        FlowExchangeError: If the code exchange fails.
        HttpError: If fetching user info fails.
    """
    try:
        # Log deprecation warning if old parameter is used
        if client_secrets_path:
            logger.warning(
                "The 'client_secrets_path' parameter is deprecated. Use GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET environment variables instead."
            )

        # Allow HTTP for localhost in development
        if "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ:
            logger.warning(
                "OAUTHLIB_INSECURE_TRANSPORT not set. Setting it for localhost development."
            )
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        flow = create_oauth_flow(scopes=scopes, redirect_uri=redirect_uri)

        # Exchange the authorization code for credentials
        # Note: fetch_token will use the redirect_uri configured in the flow
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        logger.info("Successfully exchanged authorization code for tokens.")

        # Get user info to determine user_id (using email here)
        user_info = get_user_info(credentials)
        if not user_info or "email" not in user_info:
            logger.error("Could not retrieve user email from Google.")
            raise ValueError("Failed to get user email for identification.")

        user_google_email = user_info["email"]
        logger.info(f"Identified user_google_email: {user_google_email}")

        # Save the credentials to file
        save_credentials_to_file(user_google_email, credentials, credentials_base_dir)

        # If session_id is provided, also save to session cache
        if session_id:
            save_credentials_to_session(session_id, credentials)

        return user_google_email, credentials

    except Exception as e:  # Catch specific exceptions like FlowExchangeError if needed
        logger.error(f"Error handling auth callback: {e}")
        raise  # Re-raise for the caller


def get_credentials(
    required_scopes: List[str],
    user_google_email: Optional[str]=None,  # Can be None if relying on session_id
    credentials_base_dir: str = DEFAULT_CREDENTIALS_DIR,
    session_id: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None, 
    refresh_token: Optional[str] = None,
) -> Optional[Credentials]:
    """
    Retrieves stored credentials, prioritizing environment variables, then session, then file. 
    Refreshes if necessary. If credentials are loaded from file and a session_id is present, 
    they are cached in the session. In single-user mode, bypasses session mapping and uses 
    any available credentials.

    Priority order:
    1. Environment variables (GOOGLE_OAUTH_REFRESH_TOKEN, etc.)
    2. Session cache
    3. File storage
    4. Single-user mode fallback

    Args:
        user_google_email: Optional user's Google email.
        required_scopes: List of scopes the credentials must have.
        credentials_base_dir: Base directory for credential files.
        session_id: Optional MCP session ID.
        client_id: OAuth client ID from headers.
        client_secret: OAuth client secret from headers.
        refresh_token: OAuth refresh token from headers.

    Returns:
        Valid Credentials object or None.
    """
    # Priority 1: Try to create credentials from header parameters first
    if client_id and client_secret and refresh_token:
        
        try:
            header_credentials = Credentials(
                token=None,  # Will be refreshed if needed
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=required_scopes  # Use required scopes directly
            )
            
            # Force refresh to ensure we have a valid access token
            refreshed_credentials = _refresh_credentials_if_needed(
                credentials=header_credentials,
                session_id=session_id,
                credentials_base_dir=credentials_base_dir,
                force_refresh=True,  # Force refresh to get access token
                retry_count=2
            )
            
            if refreshed_credentials and refreshed_credentials.valid:
                logger.info(
                    f"[get_credentials] Successfully using header credentials. User: '{user_google_email}', Session: '{session_id}'"
                )
                return refreshed_credentials
            else:
                logger.warning(
                    f"[get_credentials] Header credentials failed validation/refresh. User: '{user_google_email}', Session: '{session_id}'"
                )
                # Fall through to other methods
        except Exception as e:
            logger.warning(
                f"[get_credentials] Error creating credentials from headers: {e}. User: '{user_google_email}', Session: '{session_id}'"
            )
            # Fall through to other methods
    
    # Priority 2: Try to load from environment variables
    env_credentials = load_credentials_from_env()
    if env_credentials:
        logger.info(
            f"[get_credentials] Using credentials from environment variables. User: '{user_google_email}', Session: '{session_id}'"
        )
        
        # Validate scopes if provided in environment
        if env_credentials.scopes and not all(scope in env_credentials.scopes for scope in required_scopes):
            logger.warning(
                f"[get_credentials] Environment credentials lack required scopes. Need: {required_scopes}, Have: {env_credentials.scopes}. User: '{user_google_email}', Session: '{session_id}'"
            )
            # Try to refresh credentials first to see if they're valid
            refreshed_credentials = _refresh_credentials_if_needed(
                credentials=env_credentials,
                user_google_email=user_google_email,
                session_id=session_id,
                credentials_base_dir=credentials_base_dir
            )
            
            if refreshed_credentials and refreshed_credentials.valid:
                logger.info(
                    f"[get_credentials] Environment credentials are valid but lack required scopes. Will need re-authentication. User: '{user_google_email}', Session: '{session_id}'"
                )
                # Fall through to other methods - OAuth flow will use the env credentials for client config
            else:
                logger.warning(
                    f"[get_credentials] Environment credentials are invalid and lack required scopes. User: '{user_google_email}', Session: '{session_id}'"
                )
                # Fall through to other methods
        else:
            # Use proactive refresh logic for environment credentials
            # Force refresh for environment credentials to ensure they work
            refreshed_credentials = _refresh_credentials_if_needed(
                credentials=env_credentials,
                user_google_email=user_google_email,
                session_id=session_id,
                credentials_base_dir=credentials_base_dir,
                force_refresh=True,  # Force refresh to ensure token is valid
                retry_count=2
            )
            
            if refreshed_credentials and refreshed_credentials.valid:
                logger.info(
                    f"[get_credentials] Successfully using environment credentials. User: '{user_google_email}', Session: '{session_id}'"
                )
                return refreshed_credentials
            else:
                logger.warning(
                    f"[get_credentials] Environment credentials failed validation/refresh. User: '{user_google_email}', Session: '{session_id}'"
                )
                # Fall through to other methods

    # Check for single-user mode
    if os.getenv("MCP_SINGLE_USER_MODE") == "1":
        logger.info(
            f"[get_credentials] Single-user mode: bypassing session mapping, finding any credentials"
        )
        credentials = _find_any_credentials(credentials_base_dir)
        if not credentials:
            logger.info(
                f"[get_credentials] Single-user mode: No credentials found in {credentials_base_dir}"
            )
            return None

        # In single-user mode, if user_google_email wasn't provided, try to get it from user info
        # This is needed for proper credential saving after refresh
        if not user_google_email and credentials.valid:
            try:
                user_info = get_user_info(credentials)
                if user_info and "email" in user_info:
                    user_google_email = user_info["email"]
                    logger.debug(
                        f"[get_credentials] Single-user mode: extracted user email {user_google_email} from credentials"
                    )
            except Exception as e:
                logger.debug(
                    f"[get_credentials] Single-user mode: could not extract user email: {e}"
                )
    else:
        credentials: Optional[Credentials] = None

        # Session ID should be provided by the caller
        if not session_id:
            logger.debug("[get_credentials] No session_id provided")

        logger.debug(
            f"[get_credentials] Called for user_google_email: '{user_google_email}', session_id: '{session_id}', required_scopes: {required_scopes}"
        )

        if session_id:
            credentials = load_credentials_from_session(session_id)
            if credentials:
                logger.debug(
                    f"[get_credentials] Loaded credentials from session for session_id '{session_id}'."
                )

        if not credentials and user_google_email:
            logger.debug(
                f"[get_credentials] No session credentials, trying file for user_google_email '{user_google_email}'."
            )
            credentials = load_credentials_from_file(
                user_google_email, credentials_base_dir
            )
            if credentials and session_id:
                logger.debug(
                    f"[get_credentials] Loaded from file for user '{user_google_email}', caching to session '{session_id}'."
                )
                save_credentials_to_session(
                    session_id, credentials
                )  # Cache for current session

        if not credentials:
            logger.info(
                f"[get_credentials] No credentials found for user '{user_google_email}' or session '{session_id}'."
            )
            return None

    logger.debug(
        f"[get_credentials] Credentials found. Scopes: {credentials.scopes}, Valid: {credentials.valid}, Expired: {credentials.expired}"
    )

    # Check if credentials have required scopes (handle None case)
    if not credentials.scopes or not all(scope in credentials.scopes for scope in required_scopes):
        logger.warning(
            f"[get_credentials] Credentials lack required scopes. Need: {required_scopes}, Have: {credentials.scopes}. User: '{user_google_email}', Session: '{session_id}'"
        )
        return None  # Re-authentication needed for scopes

    logger.debug(
        f"[get_credentials] Credentials have sufficient scopes. User: '{user_google_email}', Session: '{session_id}'"
    )

    # Use proactive refresh logic
    refreshed_credentials = _refresh_credentials_if_needed(
        credentials=credentials,
        user_google_email=user_google_email,
        session_id=session_id,
        credentials_base_dir=credentials_base_dir
    )
    
    if refreshed_credentials is None:
        logger.warning(
            f"[get_credentials] Failed to refresh credentials. User: '{user_google_email}', Session: '{session_id}'"
        )
        return None
    
    if refreshed_credentials.valid:
        logger.debug(
            f"[get_credentials] Credentials are valid (after refresh check). User: '{user_google_email}', Session: '{session_id}'"
        )
        return refreshed_credentials
    else:
        logger.warning(
            f"[get_credentials] Credentials invalid after refresh attempt. User: '{user_google_email}', Session: '{session_id}'"
        )
        return None


def get_user_info(credentials: Credentials) -> Optional[Dict[str, Any]]:
    """Fetches basic user profile information (requires userinfo.email scope)."""
    if not credentials or not credentials.valid:
        logger.error("Cannot get user info: Invalid or missing credentials.")
        return None
    try:
        # Using googleapiclient discovery to get user info
        # Requires 'google-api-python-client' library
        service = build("oauth2", "v2", credentials=credentials)
        user_info = service.userinfo().get().execute()
        logger.info(f"Successfully fetched user info: {user_info.get('email')}")
        return user_info
    except HttpError as e:
        logger.error(f"HttpError fetching user info: {e.status_code} {e.reason}")
        # Handle specific errors, e.g., 401 Unauthorized might mean token issue
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching user info: {e}")
        return None


# --- Centralized Google Service Authentication ---


class GoogleAuthenticationError(Exception):
    """Exception raised when Google authentication is required or fails."""

    def __init__(self, message: str, auth_url: Optional[str] = None):
        super().__init__(message)
        self.auth_url = auth_url


async def get_authenticated_google_service(
    service_name: str,  # "gmail", "calendar", "drive", "docs"
    version: str,  # "v1", "v3"
    tool_name: str,  # For logging/debugging
    required_scopes: List[str],
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    refresh_token: Optional[str] = None,
) -> tuple[Any, str]:
    """
    Centralized Google service authentication for all MCP tools.
    Returns (service, user_email) on success or raises GoogleAuthenticationError.

    Args:
        service_name: The Google service name ("gmail", "calendar", "drive", "docs")
        version: The API version ("v1", "v3", etc.)
        tool_name: The name of the calling tool (for logging/debugging)
        user_google_email: The user's Google email address (required)
        required_scopes: List of required OAuth scopes
        client_id: OAuth client ID from headers (optional)
        client_secret: OAuth client secret from headers (optional)
        refresh_token: OAuth refresh token from headers (optional)

    Returns:
        tuple[service, user_email] on success

    Raises:
        GoogleAuthenticationError: When authentication is required or fails
    """
    logger.info(
        f"[{tool_name}] Attempting to get authenticated {service_name} service."
    )

    # # Validate email format
    # if not user_google_email or "@" not in user_google_email:
    #     error_msg = f"Authentication required for {tool_name}. No valid 'user_google_email' provided. Please provide a valid Google email address."
    #     logger.info(f"[{tool_name}] {error_msg}")
    #     raise GoogleAuthenticationError(error_msg)

    credentials = await asyncio.to_thread(
        get_credentials,
        required_scopes=required_scopes,
        session_id=None,  # Session ID not available in service layer
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )

    if not credentials or not credentials.valid:
        logger.warning(
            f"[{tool_name}] No valid credentials. Email: ."
        )
        logger.info(
            f"[{tool_name}] Valid email  provided, initiating auth flow."
        )

        # Import here to avoid circular import
        from core.server import get_oauth_redirect_uri_for_current_mode

        # Ensure OAuth callback is available
        redirect_uri = get_oauth_redirect_uri_for_current_mode()
        # Note: We don't know the transport mode here, but the server should have set it

        # Generate auth URL and raise exception with it
        auth_response = await start_auth_flow(
            mcp_session_id=None,  # Session ID not available in service layer
            user_google_email=None,
            service_name=f"Google {service_name.title()}",
            redirect_uri=redirect_uri,
        )

        # Extract the auth URL from the response and raise with it
        raise GoogleAuthenticationError(auth_response)

    try:
        service = build(service_name, version, credentials=credentials)
        log_user_email = None

        # Try to get email from credentials if needed for validation
        if credentials and credentials.id_token:
            try:
                # Decode without verification (just to get email for logging)
                decoded_token = jwt.decode(
                    credentials.id_token, options={"verify_signature": False}
                )
                token_email = decoded_token.get("email")
                if token_email:
                    log_user_email = token_email
                    logger.info(f"[{tool_name}] Token email: {token_email}")
            except Exception as e:
                logger.debug(f"[{tool_name}] Could not decode id_token: {e}")

        logger.info(
            f"[{tool_name}] Successfully authenticated {service_name} service for user: {log_user_email}"
        )
        return service, log_user_email

    except Exception as e:
        error_msg = f"[{tool_name}] Failed to build {service_name} service: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise GoogleAuthenticationError(error_msg)
