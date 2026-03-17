#!/usr/bin/env python3
"""
LinkedIn posting tool for Ní
Usage:
python3 linkedin_tools.py --auth                                   # Authenticate (first time)
python3 linkedin_tools.py --post "Nội dung bài đăng"               # Post text only
python3 linkedin_tools.py --post "Nội dung" --image /path.jpg      # Post with image
python3 linkedin_tools.py --verify text                            # Verify text-only setup
python3 linkedin_tools.py --verify image --image /path.jpg         # Verify image setup (register only)
python3 linkedin_tools.py --verify-full image --image /path.jpg    # Full verify (register + upload)
python3 linkedin_tools.py --debug --post "Test"                    # Debug mode with verbose output

Note: Need to request 'Share on LinkedIn' and 'Sign In with LinkedIn' permissions first
"""

import os
import sys
import json
import time
import requests
from argparse import ArgumentParser
from urllib.parse import urlencode

# Fix Windows console encoding for Unicode output
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Import shared utilities (with fallback for standalone use)
try:
    from tools._shared.config_utils import (
        resolve_workspace_root as _resolve_workspace,
        load_json_config,
        format_cli_error,
        set_debug_mode as _set_shared_debug,
        ErrorCode,
        classify_http_error,
        is_retryable_status,
    )
    _HAS_SHARED_UTILS = True
except ImportError:
    _HAS_SHARED_UTILS = False

# Constants
SUPPORTED_IMAGE_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif'}
MAX_IMAGE_SIZE = 8 * 1024 * 1024  # 8MB LinkedIn limit
HTTP_TIMEOUT = 20  # seconds

# Retry config
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
BACKOFF_FACTOR = 2

# Global debug flag
DEBUG_MODE = False


# =============================================================================
# Path resolution (robust for any file location)
# =============================================================================

def get_workspace_root():
    """Resolve workspace root directory.
    Priority:
    1. OPENCLAW_WORKSPACE env var (explicit override)
    2. Walk up from script location to find credentials/ with linkedin_credentials.json
    3. Fallback to /root/.openclaw/workspace if exists (standard deployment)
    4. Final fallback to script's parent directory
    """
    if _HAS_SHARED_UTILS:
        return _resolve_workspace(
            env_var='OPENCLAW_WORKSPACE',
            marker_file='linkedin_credentials.json',
            marker_subdir='credentials',
            script_path=__file__
        )

    # Fallback implementation (standalone mode)
    # Priority 1: Explicit env override
    env_root = os.environ.get('OPENCLAW_WORKSPACE')
    if env_root and os.path.isdir(env_root):
        return os.path.normpath(env_root)

    # Priority 2: Walk up from script location to find credentials/ with actual credential file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_dir = script_dir

    for _ in range(5):  # Max 5 levels up
        creds_file = os.path.join(search_dir, 'credentials', 'linkedin_credentials.json')
        if os.path.isfile(creds_file):
            return search_dir
        parent = os.path.dirname(search_dir)
        if parent == search_dir:  # Reached root
            break
        search_dir = parent

    # Priority 3: Standard deployment path /root/.openclaw/workspace
    standard_workspace = '/root/.openclaw/workspace'
    if os.path.isdir(standard_workspace):
        return standard_workspace

    # Priority 4: Final fallback to script's parent (assumes tools/linkedin_tools.py layout)
    return os.path.dirname(script_dir)


def get_credentials_path():
    """Get resolved path to linkedin_credentials.json."""
    return os.path.join(get_workspace_root(), 'credentials', 'linkedin_credentials.json')


def get_token_path():
    """Get resolved path to linkedin_token.json."""
    return os.path.join(get_workspace_root(), 'credentials', 'linkedin_token.json')


# =============================================================================
# Debug logging
# =============================================================================

def debug_log(message):
    """Print debug message only if DEBUG_MODE is enabled."""
    if DEBUG_MODE:
        print(f"[DEBUG] {message}")


def _print_error(error_code, message: str, context=None):
    """Print error using shared format_cli_error if available.
    error_code: ErrorCode enum value (when _HAS_SHARED_UTILS) or ignored (fallback)
    """
    if _HAS_SHARED_UTILS:
        print(format_cli_error(error_code, message, context=context))
    else:
        print(f"❌ {message}")


def sanitize_headers(headers):
    """Remove sensitive data from headers for debug output."""
    if not headers:
        return {}
    sanitized = {}
    for k, v in headers.items():
        if k.lower() == 'authorization':
            sanitized[k] = 'Bearer ***REDACTED***'
        else:
            sanitized[k] = v
    return sanitized


def truncate_body(body, max_len=200):
    """Truncate response body for debug output."""
    if not body:
        return "(empty)"
    if len(body) > max_len:
        return body[:max_len] + "..."
    return body


# =============================================================================
# HTTP Wrapper with retry and unified error handling
# =============================================================================

def is_retryable(response=None, exception=None):
    """Determine if request should be retried."""
    if exception:
        return isinstance(exception, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))
    if response is not None:
        return response.status_code in [429, 500, 502, 503, 504]
    return False


def linkedin_request(method, url, headers=None, json_data=None, data=None):
    """Unified HTTP wrapper with timeout, retry, and exception handling.
    Returns (response, error_message). On success error_message is None.
    Retries on: timeout, connection error, 429, 5xx.
    Does NOT retry on: 401, 403, other 4xx.
    """
    last_error = None
    last_response = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            debug_log(f"Request {method} {url} (attempt {attempt + 1}/{MAX_RETRIES + 1})")
            debug_log(f"Headers: {sanitize_headers(headers)}")

            response = requests.request(
                method, url, headers=headers, json=json_data, data=data, timeout=HTTP_TIMEOUT
            )

            debug_log(f"Response: {response.status_code}")
            debug_log(f"Body: {truncate_body(response.text)}")

            # Check if retryable
            if is_retryable(response=response) and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (BACKOFF_FACTOR ** attempt)
                reason = f"HTTP {response.status_code}"
                debug_log(f"Retry {attempt + 1}/{MAX_RETRIES}: {reason}, waiting {delay:.1f}s")
                time.sleep(delay)
                last_response = response
                continue

            return response, None

        except requests.exceptions.Timeout as e:
            last_error = "Request timeout"
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (BACKOFF_FACTOR ** attempt)
                debug_log(f"Retry {attempt + 1}/{MAX_RETRIES}: timeout, waiting {delay:.1f}s - {e}")
                time.sleep(delay)
                continue
            return None, f"{last_error} (server không phản hồi sau {MAX_RETRIES + 1} lần thử)"

        except requests.exceptions.ConnectionError as e:
            last_error = "Lỗi kết nối mạng"
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (BACKOFF_FACTOR ** attempt)
                debug_log(f"Retry {attempt + 1}/{MAX_RETRIES}: connection error, waiting {delay:.1f}s - {e}")
                time.sleep(delay)
                continue
            return None, f"{last_error} (sau {MAX_RETRIES + 1} lần thử)"

        except requests.exceptions.RequestException as e:
            debug_log(f"Request exception (no retry): {e}")
            return None, f"Lỗi HTTP: {str(e)}"

    # Exhausted retries with retryable response
    if last_response is not None:
        return last_response, None

    return None, last_error or "Unknown error"


def format_api_error(status_code, response_text):
    """Format API error with clear diagnostics."""
    if status_code == 401:
        return "Token không hợp lệ hoặc đã hết hạn. Chạy: python3 linkedin_tools.py --auth"
    elif status_code == 403:
        return "Không có quyền truy cập. Kiểm tra lại scopes trong LinkedIn Developer Portal"
    elif status_code == 429:
        return "Rate limit exceeded. Vui lòng thử lại sau"
    elif status_code >= 500:
        return f"LinkedIn server error ({status_code}). Vui lòng thử lại sau"
    else:
        msg = truncate_body(response_text, 200)
        return f"API error {status_code}: {msg}"


# =============================================================================
# Validation helpers
# =============================================================================

def validate_image(image_path):
    """Validate image file exists, type and size.
    Returns (is_valid, message). message is mime_type on success, error on failure.
    """
    if not os.path.exists(image_path):
        return False, f"File không tồn tại: {image_path}"

    ext = os.path.splitext(image_path)[1].lower()
    if ext not in SUPPORTED_IMAGE_TYPES:
        return False, f"Định dạng không hỗ trợ: {ext}. Chỉ hỗ trợ: {', '.join(SUPPORTED_IMAGE_TYPES.keys())}"

    try:
        file_size = os.path.getsize(image_path)
    except OSError as e:
        return False, f"Không thể đọc file: {e}"

    if file_size > MAX_IMAGE_SIZE:
        return False, f"File quá lớn: {file_size / 1024 / 1024:.1f}MB. Tối đa 8MB"

    return True, SUPPORTED_IMAGE_TYPES[ext]


# =============================================================================
# Credentials & Token management
# =============================================================================

def load_credentials():
    """Load LinkedIn credentials from file.
    Returns (credentials_dict, error_message). On success error is None.
    """
    creds_path = get_credentials_path()
    debug_log(f"Loading credentials from: {creds_path}")

    if _HAS_SHARED_UTILS:
        result = load_json_config(
            creds_path,
            required_keys=['client_id', 'client_secret', 'redirect_uri', 'scopes']
        )
        if result.success:
            return result.data, None

        # Keep CLI/test backward-compatible messages and normalized path style
        display_path = (result.file_path or creds_path).replace('\\', '/')
        if result.error_code == ErrorCode.CONFIG_MISSING:
            return None, f"File không tồn tại: {display_path}"
        if result.error_code == ErrorCode.CONFIG_INVALID_JSON:
            return None, f"File credentials không phải JSON hợp lệ: {display_path}"
        if result.error_code == ErrorCode.CONFIG_MISSING_KEYS:
            details = result.error_message.split(':', 1)[-1].strip() if ':' in result.error_message else result.error_message
            return None, f"Thiếu keys trong credentials: {details}"
        return None, result.error_message

    # Fallback implementation
    try:
        with open(creds_path, 'r') as f:
            creds = json.load(f)
    except FileNotFoundError:
        return None, f"File không tồn tại: {creds_path}"
    except json.JSONDecodeError:
        return None, f"File credentials không phải JSON hợp lệ: {creds_path}"
    except IOError as e:
        return None, f"Không thể đọc file credentials: {e}"

    # Validate required keys
    required_keys = ['client_id', 'client_secret', 'redirect_uri', 'scopes']
    missing = [k for k in required_keys if k not in creds or not creds[k]]
    if missing:
        return None, f"Thiếu keys trong credentials: {', '.join(missing)}"

    return creds, None


def load_token():
    """Load access token if exists.
    Returns (token_dict, error_message). On success error is None.
    """
    token_path = get_token_path()
    debug_log(f"Loading token from: {token_path}")
    if not os.path.exists(token_path):
        return None, f"File token chưa tồn tại: {token_path}"
    try:
        with open(token_path, 'r') as f:
            token = json.load(f)
    except json.JSONDecodeError:
        return None, f"File token không phải JSON hợp lệ: {token_path}"
    except IOError as e:
        return None, f"Không thể đọc file token: {e}"

    if not token.get('access_token'):
        return None, "Token file thiếu access_token"

    return token, None


def save_token(token_data):
    """Save access token.
    Returns (success, error_message).
    """
    token_path = get_token_path()
    debug_log(f"Saving token to: {token_path}")
    try:
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, 'w') as f:
            json.dump(token_data, f)
        return True, None
    except (IOError, OSError) as e:
        return False, f"Không thể lưu token: {e}"


# =============================================================================
# LinkedIn API functions
# =============================================================================

def get_auth_url():
    """Generate LinkedIn OAuth URL."""
    creds, error = load_credentials()
    if error:
        _print_error(ErrorCode.CONFIG_MISSING if _HAS_SHARED_UTILS else None, error)
        return None
    params = {
        'response_type': 'code',
        'client_id': creds['client_id'],
        'redirect_uri': creds['redirect_uri'],
        'scope': ' '.join(creds['scopes']),
        'state': 'linkedin_auth_openclaw'
    }
    return f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(params)}"


def exchange_code_for_token(auth_code):
    """Exchange authorization code for access token."""
    creds, error = load_credentials()
    if error:
        _print_error(ErrorCode.CONFIG_MISSING if _HAS_SHARED_UTILS else None, error)
        return None

    data = {
        'grant_type': 'authorization_code',
        'code': auth_code,
        'client_id': creds['client_id'],
        'client_secret': creds['client_secret'],
        'redirect_uri': creds['redirect_uri']
    }

    response, error = linkedin_request('POST', 'https://www.linkedin.com/oauth/v2/accessToken', data=data)
    if error:
        _print_error(ErrorCode.NETWORK_TIMEOUT if _HAS_SHARED_UTILS else None, error)
        return None

    if response.status_code == 200:
        token_data = response.json()
        success, save_error = save_token(token_data)
        if not success:
            _print_error(ErrorCode.CONFIG_WRITE_ERROR if _HAS_SHARED_UTILS else None, save_error)
            return None
        print("✅ Xác thực LinkedIn thành công!")
        return token_data
    else:
        _print_error(ErrorCode.AUTH_ERROR if _HAS_SHARED_UTILS else None, format_api_error(response.status_code, response.text))
        return None


def get_profile(access_token):
    """Get user's LinkedIn profile. Returns (profile_dict, error_message)."""
    headers = {
        'Authorization': f'Bearer {access_token}',
        'X-Restli-Protocol-Version': '2.0.0'
    }

    response, error = linkedin_request('GET', 'https://api.linkedin.com/v2/userinfo', headers=headers)
    if error:
        return None, error

    if response.status_code == 200:
        return response.json(), None
    else:
        return None, format_api_error(response.status_code, response.text)


def register_image_upload(access_token, user_urn):
    """Register image upload with LinkedIn API.
    Returns (upload_url, asset_urn, error_message).
    """
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0'
    }

    register_body = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": user_urn,
            "serviceRelationships": [{
                "relationshipType": "OWNER",
                "identifier": "urn:li:userGeneratedContent"
            }]
        }
    }

    response, error = linkedin_request(
        'POST', 'https://api.linkedin.com/v2/assets?action=registerUpload',
        headers=headers, json_data=register_body
    )

    if error:
        return None, None, error

    if response.status_code != 200:
        return None, None, format_api_error(response.status_code, response.text)

    # Safe JSON parsing
    try:
        data = response.json()
        upload_url = data['value']['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
        asset = data['value']['asset']
        return upload_url, asset, None
    except (KeyError, TypeError) as e:
        return None, None, f"Response không đúng format: {e}"
    except json.JSONDecodeError:
        return None, None, "Response không phải JSON hợp lệ"


def upload_image_binary(upload_url, image_path, access_token):
    """Upload image binary to LinkedIn.
    Returns (success, error_message).
    """
    try:
        with open(image_path, 'rb') as f:
            image_data = f.read()
    except IOError as e:
        return False, f"Không thể đọc file ảnh: {e}"

    headers = {'Authorization': f'Bearer {access_token}'}

    response, error = linkedin_request('PUT', upload_url, headers=headers, data=image_data)
    if error:
        return False, error

    if response.status_code in [200, 201]:
        return True, None
    else:
        return False, format_api_error(response.status_code, response.text)


def create_post(access_token, user_urn, text, asset_urn=None):
    """Create post on LinkedIn.
    Returns (success, error_message).
    """
    if asset_urn:
        post_body = {
            "author": user_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "IMAGE",
                    "media": [{"status": "READY", "media": asset_urn}]
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
        }
    else:
        post_body = {
            "author": user_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE"
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
        }

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0'
    }

    response, error = linkedin_request(
        'POST', 'https://api.linkedin.com/v2/ugcPosts',
        headers=headers, json_data=post_body
    )

    if error:
        return False, error

    if response.status_code == 201:
        return True, None
    else:
        return False, format_api_error(response.status_code, response.text)


# =============================================================================
# Main operations
# =============================================================================

def post_to_linkedin(text, image_path=None):
    """Post to LinkedIn with optional image."""
    # Validate image first (fail fast before API calls)
    if image_path:
        is_valid, result = validate_image(image_path)
        if not is_valid:
            _print_error(ErrorCode.API_ERROR if _HAS_SHARED_UTILS else None, result)
            return False

    token_data, error = load_token()
    if error:
        _print_error(ErrorCode.CONFIG_MISSING if _HAS_SHARED_UTILS else None, f"{error}. Chạy: python3 linkedin_tools.py --auth")
        return False

    access_token = token_data.get('access_token')

    # Get user profile
    profile, error = get_profile(access_token)
    if error:
        _print_error(ErrorCode.API_ERROR if _HAS_SHARED_UTILS else None, error)
        return False

    # Validate sub before building URN
    sub = profile.get('sub')
    if not sub:
        _print_error(ErrorCode.API_ERROR if _HAS_SHARED_UTILS else None, "Profile không có 'sub' field - không thể tạo user URN")
        return False

    user_urn = f"urn:li:person:{sub}"

    # Handle image upload if provided
    asset_urn = None
    if image_path:
        print("📤 Đang upload ảnh...")

        upload_url, asset_urn, error = register_image_upload(access_token, user_urn)
        if error:
            _print_error(ErrorCode.API_ERROR if _HAS_SHARED_UTILS else None, f"Register upload failed: {error}")
            return False

        success, error = upload_image_binary(upload_url, image_path, access_token)
        if not success:
            _print_error(ErrorCode.API_ERROR if _HAS_SHARED_UTILS else None, f"Upload failed: {error}")
            return False

        print("✅ Upload ảnh thành công")

    # Create post
    success, error = create_post(access_token, user_urn, text, asset_urn)
    if not success:
        _print_error(ErrorCode.API_ERROR if _HAS_SHARED_UTILS else None, f"Đăng bài thất bại: {error}")
        return False

    post_type = "kèm ảnh" if asset_urn else "text"
    print(f"✅ Đã đăng bài ({post_type}) lên LinkedIn thành công!")
    return True


def verify_setup(mode, image_path=None, full_test=False):
    """Verify LinkedIn setup for text-only or text+image posting.
    If full_test=True, also tests binary upload (not just register).
    Returns True if all checks pass.
    """
    print(f"🔍 Verify mode: {mode}" + (" (full test)" if full_test else ""))
    print("=" * 50)
    all_passed = True

    # Check 1: Credentials file with key validation
    print("[1/6] Kiểm tra credentials...", end=" ")
    creds, error = load_credentials()
    if creds:
        print("✅ OK")
    else:
        print(f"❌ FAIL - {error}")
        all_passed = False

    # Check 2: Token file
    print("[2/6] Kiểm tra token...", end=" ")
    token_data, error = load_token()
    if token_data:
        print("✅ OK")
    else:
        print(f"❌ FAIL - {error}")
        all_passed = False
        print("=" * 50)
        print("❌ VERIFY FAILED - Thiếu token")
        return False

    access_token = token_data.get('access_token')

    # Check 3: Profile/Token validity + sub validation
    print("[3/6] Kiểm tra token validity (get profile)...", end=" ")
    profile, error = get_profile(access_token)
    if not profile:
        print(f"❌ FAIL - {error}")
        all_passed = False
        print("=" * 50)
        print("❌ VERIFY FAILED - Token không hợp lệ")
        return False

    sub = profile.get('sub')
    if not sub:
        print("❌ FAIL - Profile thiếu 'sub' field")
        all_passed = False
        print("=" * 50)
        print("❌ VERIFY FAILED - Profile không hợp lệ")
        return False

    print(f"✅ OK - User: {profile.get('name', 'N/A')}")
    user_urn = f"urn:li:person:{sub}"

    # Check 4, 5, 6: Image-specific checks
    if mode == 'image':
        if not image_path:
            print("[4/6] Kiểm tra image path...", end=" ")
            print("❌ FAIL - Thiếu --image parameter")
            all_passed = False
            print("[5/6] Test register upload...", end=" ")
            print("⏭️ SKIP")
            print("[6/6] Test binary upload...", end=" ")
            print("⏭️ SKIP")
        else:
            print(f"[4/6] Kiểm tra image file: {image_path}...", end=" ")
            is_valid, result = validate_image(image_path)
            if is_valid:
                print(f"✅ OK ({result})")
            else:
                print(f"❌ FAIL - {result}")
                all_passed = False

            # Check 5: Test register upload
            if all_passed:
                print("[5/6] Test register upload API...", end=" ")
                upload_url, asset_urn, error = register_image_upload(access_token, user_urn)
                if upload_url and asset_urn:
                    print("✅ OK")
                else:
                    print(f"❌ FAIL - {error}")
                    all_passed = False

                # Check 6: Test binary upload (only if full_test)
                if all_passed and full_test:
                    print("[6/6] Test binary upload...", end=" ")
                    success, error = upload_image_binary(upload_url, image_path, access_token)
                    if success:
                        print("✅ OK")
                    else:
                        print(f"❌ FAIL - {error}")
                        all_passed = False
                elif all_passed:
                    print("[6/6] Test binary upload...", end=" ")
                    print("⏭️ SKIP (use --verify-full to test)")
            else:
                print("[5/6] Test register upload...", end=" ")
                print("⏭️ SKIP")
                print("[6/6] Test binary upload...", end=" ")
                print("⏭️ SKIP")
    else:
        print("[4/6] Kiểm tra image...", end=" ")
        print("⏭️ SKIP (text-only mode)")
        print("[5/6] Test register upload...", end=" ")
        print("⏭️ SKIP (text-only mode)")
        print("[6/6] Test binary upload...", end=" ")
        print("⏭️ SKIP (text-only mode)")

    # Summary
    print("=" * 50)
    if all_passed:
        print(f"✅ VERIFY PASSED - Sẵn sàng đăng bài {mode}")
    else:
        print(f"❌ VERIFY FAILED - Có lỗi cần fix")

    return all_passed


# =============================================================================
# CLI Entry point
# =============================================================================

if __name__ == "__main__":
    parser = ArgumentParser(description="LinkedIn tools for Ní")
    parser.add_argument("--auth", action="store_true", help="Get authentication URL")
    parser.add_argument("--code", help="Exchange auth code for token")
    parser.add_argument("--post", help="Post text to LinkedIn")
    parser.add_argument("--image", help="Path to image file (optional)")
    parser.add_argument("--verify", choices=['text', 'image'], help="Verify setup for text or image posting")
    parser.add_argument("--verify-full", choices=['text', 'image'], help="Full verify including binary upload test")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode with verbose output")

    args = parser.parse_args()

    # Set global debug mode
    if args.debug:
        DEBUG_MODE = True
        if _HAS_SHARED_UTILS:
            _set_shared_debug(True)
        print("[DEBUG] Debug mode enabled")

    if args.auth:
        auth_url = get_auth_url()
        if auth_url:
            print("🔗 Mở link này trong trình duyệt để xác thực:")
            print(auth_url)
            print("\n👉 Sau khi xác thực, copy cái 'code' từ URL trả về")
            print("   (dạng: http://localhost:8080/callback?code=AQ...&state=...)")
            print("   Rồi chạy: python3 linkedin_tools.py --code '<code>'")
        else:
            sys.exit(1)

    elif args.code:
        result = exchange_code_for_token(args.code)
        if not result:
            sys.exit(1)

    elif args.verify:
        success = verify_setup(args.verify, args.image, full_test=False)
        if not success:
            sys.exit(1)

    elif args.verify_full:
        success = verify_setup(args.verify_full, args.image, full_test=True)
        if not success:
            sys.exit(1)

    elif args.post:
        success = post_to_linkedin(args.post, args.image)
        if not success:
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)
