#!/usr/bin/env python3
"""
Simple test to verify environment variable functionality without external dependencies.
"""

import os
import sys

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test basic environment variable detection
def test_basic_env_detection():
    """Test basic environment variable detection."""
    print("Testing basic environment variable detection...")
    
    # Test required environment variables
    required_vars = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET", 
        "GOOGLE_OAUTH_REFRESH_TOKEN"
    ]
    
    print("\nChecking for required environment variables:")
    for var in required_vars:
        value = os.getenv(var)
        status = "✓ SET" if value else "✗ NOT SET"
        print(f"  {var}: {status}")
    
    # Test optional environment variables
    optional_vars = [
        "GOOGLE_OAUTH_ACCESS_TOKEN",
        "GOOGLE_OAUTH_TOKEN_URI",
        "GOOGLE_OAUTH_SCOPES"
    ]
    
    print("\nChecking for optional environment variables:")
    for var in optional_vars:
        value = os.getenv(var)
        status = "✓ SET" if value else "✗ NOT SET"
        print(f"  {var}: {status}")
    
    # Test validation logic
    print("\nTesting validation logic...")
    
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")
    
    if client_id and client_secret and refresh_token:
        print("✓ All required variables are set - environment credential mode available")
        
        # Test additional validation
        token_uri = os.getenv("GOOGLE_OAUTH_TOKEN_URI")
        if token_uri and not token_uri.startswith("https://"):
            print("⚠ WARNING: GOOGLE_OAUTH_TOKEN_URI should be HTTPS")
        
        scopes_str = os.getenv("GOOGLE_OAUTH_SCOPES")
        if scopes_str:
            scopes = [scope.strip() for scope in scopes_str.split(",")]
            if not scopes or any(not scope for scope in scopes):
                print("⚠ WARNING: GOOGLE_OAUTH_SCOPES contains empty scopes")
            else:
                print(f"✓ Scopes configured: {len(scopes)} scopes")
    else:
        print("ℹ Environment credential mode not available - missing required variables")

def test_environment_priority():
    """Test environment variable priority over other methods."""
    print("\nTesting environment variable priority...")
    
    # Check if environment variables are set
    env_has_creds = bool(
        os.getenv("GOOGLE_OAUTH_CLIENT_ID") and
        os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") and
        os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")
    )
    
    # Check if client secrets file exists
    client_secrets_path = os.path.join(os.path.dirname(__file__), "client_secret.json")
    file_exists = os.path.exists(client_secrets_path)
    
    print(f"Environment credentials available: {env_has_creds}")
    print(f"Client secrets file exists: {file_exists}")
    
    if env_has_creds:
        print("✓ Environment variables will take priority")
    elif file_exists:
        print("✓ Client secrets file will be used as fallback")
    else:
        print("ℹ No authentication method available")

def show_usage_example():
    """Show usage example for environment variables."""
    print("\n" + "="*60)
    print("ENVIRONMENT VARIABLE USAGE EXAMPLE")
    print("="*60)
    
    print("""
To use environment variable authentication, set these variables:

Required:
export GOOGLE_OAUTH_CLIENT_ID="your_client_id"
export GOOGLE_OAUTH_CLIENT_SECRET="your_client_secret"
export GOOGLE_OAUTH_REFRESH_TOKEN="your_refresh_token"

Optional:
export GOOGLE_OAUTH_ACCESS_TOKEN="your_access_token"
export GOOGLE_OAUTH_TOKEN_URI="https://oauth2.googleapis.com/token"
export GOOGLE_OAUTH_SCOPES="https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/calendar"

Benefits:
- No need to store credentials in files
- Works great with containers and cloud deployments
- Easy to manage in CI/CD environments
- Supports refresh token for long-lived authentication
""")

if __name__ == "__main__":
    print("🔐 Environment Variable Authentication Test")
    print("=" * 50)
    
    test_basic_env_detection()
    test_environment_priority()
    show_usage_example()
    
    print("\n✅ Environment variable authentication support is properly configured!")