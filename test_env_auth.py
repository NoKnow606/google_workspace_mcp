#!/usr/bin/env python3
"""
Test script for environment variable authentication support.
"""

import os
import sys
import tempfile

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth.google_auth import (
    load_credentials_from_env,
    validate_environment_credentials,
    check_client_secrets,
    get_credentials_status
)

def test_environment_credentials():
    """Test environment variable credential loading."""
    print("Testing environment variable credential loading...")
    
    # Save original environment
    original_env = {}
    test_vars = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET", 
        "GOOGLE_OAUTH_REFRESH_TOKEN",
        "GOOGLE_OAUTH_ACCESS_TOKEN",
        "GOOGLE_OAUTH_TOKEN_URI",
        "GOOGLE_OAUTH_SCOPES"
    ]
    
    for var in test_vars:
        original_env[var] = os.environ.get(var)
        if var in os.environ:
            del os.environ[var]
    
    try:
        # Test 1: No environment variables
        print("\n1. Testing with no environment variables...")
        creds = load_credentials_from_env()
        print(f"   Result: {creds}")
        assert creds is None, "Should return None when no env vars set"
        
        # Test 2: Missing required variables
        print("\n2. Testing with missing required variables...")
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "test_client_id"
        creds = load_credentials_from_env()
        print(f"   Result: {creds}")
        assert creds is None, "Should return None when missing required vars"
        
        # Test 3: Complete credentials
        print("\n3. Testing with complete credentials...")
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "test_client_id"
        os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = "test_client_secret"
        os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"] = "test_refresh_token"
        os.environ["GOOGLE_OAUTH_ACCESS_TOKEN"] = "test_access_token"
        os.environ["GOOGLE_OAUTH_SCOPES"] = "https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/calendar"
        
        creds = load_credentials_from_env()
        print(f"   Result: {creds}")
        assert creds is not None, "Should return credentials when all required vars set"
        
        if creds:
            assert creds.client_id == "test_client_id"
            assert creds.client_secret == "test_client_secret"
            assert creds.refresh_token == "test_refresh_token"
            assert creds.token == "test_access_token"
            assert len(creds.scopes) == 2
            print("   ✓ All credential fields correctly set")
        
        # Test 4: Validation
        print("\n4. Testing credential validation...")
        is_valid, errors = validate_environment_credentials()
        print(f"   Valid: {is_valid}, Errors: {errors}")
        assert is_valid, f"Should be valid, but got errors: {errors}"
        
        # Test 5: Invalid token URI
        print("\n5. Testing invalid token URI...")
        os.environ["GOOGLE_OAUTH_TOKEN_URI"] = "http://invalid.uri"
        is_valid, errors = validate_environment_credentials()
        print(f"   Valid: {is_valid}, Errors: {errors}")
        assert not is_valid, "Should be invalid due to non-HTTPS URI"
        
        # Test 6: Empty scopes
        print("\n6. Testing empty scopes...")
        os.environ["GOOGLE_OAUTH_TOKEN_URI"] = "https://oauth2.googleapis.com/token"
        os.environ["GOOGLE_OAUTH_SCOPES"] = ",,"
        is_valid, errors = validate_environment_credentials()
        print(f"   Valid: {is_valid}, Errors: {errors}")
        assert not is_valid, "Should be invalid due to empty scopes"
        
        print("\n✓ All environment credential tests passed!")
        
    finally:
        # Restore original environment
        for var in test_vars:
            if var in os.environ:
                del os.environ[var]
            if original_env[var] is not None:
                os.environ[var] = original_env[var]

def test_check_client_secrets():
    """Test the check_client_secrets function."""
    print("\nTesting check_client_secrets function...")
    
    # Save original environment
    original_env = {}
    test_vars = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET", 
        "GOOGLE_OAUTH_REFRESH_TOKEN"
    ]
    
    for var in test_vars:
        original_env[var] = os.environ.get(var)
        if var in os.environ:
            del os.environ[var]
    
    try:
        # Test 1: No credentials anywhere
        print("\n1. Testing with no credentials...")
        result = check_client_secrets()
        print(f"   Result: {result is not None}")
        assert result is not None, "Should return error message when no credentials"
        
        # Test 2: With environment credentials
        print("\n2. Testing with environment credentials...")
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "test_client_id"
        os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = "test_client_secret"
        os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"] = "test_refresh_token"
        
        result = check_client_secrets()
        print(f"   Result: {result is None}")
        assert result is None, "Should return None when valid credentials exist"
        
        # Test 3: With invalid environment credentials
        print("\n3. Testing with invalid environment credentials...")
        os.environ["GOOGLE_OAUTH_TOKEN_URI"] = "http://invalid.uri"
        
        result = check_client_secrets()
        print(f"   Result: {result is not None}")
        assert result is not None, "Should return error message when credentials are invalid"
        
        print("\n✓ All check_client_secrets tests passed!")
        
    finally:
        # Restore original environment
        for var in test_vars:
            if var in os.environ:
                del os.environ[var]
            if original_env[var] is not None:
                os.environ[var] = original_env[var]

if __name__ == "__main__":
    print("🧪 Testing environment variable authentication support...")
    
    test_environment_credentials()
    test_check_client_secrets()
    
    print("\n🎉 All tests passed! Environment variable authentication support is working correctly.")