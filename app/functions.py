from typing import List, Dict, Optional
import re


def validate_ssh_url(v: str, allowed_domains: List[str]) -> str:
    """
    Validate SSH URL format: git@host:user/repo.git (HARDENED VERSION)

    Security checks:
    - Domain whitelist enforcement
    - ASCII-only characters (prevents homograph attacks)
    - Length limits (prevents ReDoS)
    - No command injection characters
    - No path traversal attempts
    - No excessive repetition (prevents ReDoS)
    - Proper SCP-style format validation

    Args:
        v: SSH URL string to validate
        allowed_domains: List of allowed domain names

    Returns:
        Validated SSH URL string

    Raises:
        ValueError: If validation fails with descriptive error message
    """
    if not v or not v.strip():
        raise ValueError("SSH URL cannot be empty")

    v = v.strip()

    # Security: Check total length BEFORE regex (prevents ReDoS)
    if len(v) > 500:
        raise ValueError("SSH URL is too long (max 500 characters)")

    # Security: Check for excessive repetition (prevents ReDoS)
    for char in '.-_':
        if char * 50 in v:
            raise ValueError(f"SSH URL contains excessive repetition of '{char}'")

    # Security: Prevent command injection
    dangerous_chars = [';', '|', '&', '$', '`', '\n', '\r', '(', ')', '<', '>', ' ', '\t']
    for char in dangerous_chars:
        if char in v:
            raise ValueError(
                f"SSH URL contains dangerous character: '{char}'. "
                "This could lead to command injection."
            )

    # Security: Prevent path traversal
    if '../' in v or '/..' in v:
        raise ValueError("SSH URL contains path traversal attempt")

    # Security: Prevent null bytes
    if '\x00' in v:
        raise ValueError("SSH URL contains null byte")

    # Security: Use ASCII-only pattern (prevents Unicode homograph attacks)
    # NEW: Only ASCII alphanumeric, dots, hyphens, slashes
    pattern = r'^git@([a-zA-Z0-9\.-]+):([a-zA-Z0-9\.\-/]+)\.git$'
    match = re.match(pattern, v)

    if not match:
        raise ValueError(
            f"Invalid SSH URL format: '{v}'. "
            "Expected format: 'git@host:user/repo.git' "
            "(only ASCII alphanumeric, dots, hyphens allowed)"
        )

    hostname = match.group(1)
    repo_path = match.group(2)

    # Validate hostname length (DNS limit)
    if len(hostname) > 253:
        raise ValueError("Hostname is too long (max 253 characters)")

    # Security: Check for lookalike domain tricks
    # e.g., github.com.evil.com or github-com.evil.com
    is_subdomain_trick = False
    for allowed in allowed_domains:
        # Check if hostname ends with allowed domain but has extra parts
        if hostname != allowed:
            # Check for domain lookalikes
            if (hostname.endswith('.' + allowed) or
                    hostname.endswith('-' + allowed) or
                    hostname.endswith(allowed + '.') or
                    hostname.startswith(allowed + '-')):
                is_subdomain_trick = True
                break

    if is_subdomain_trick:
        raise ValueError(
            f"Suspicious domain '{hostname}' appears to mimic an allowed domain. "
            f"Allowed domains: {', '.join(allowed_domains)}"
        )

    # Check domain whitelist with exact matching
    if hostname not in allowed_domains:
        raise ValueError(
            f"Domain '{hostname}' is not allowed. "
            f"Allowed domains: {', '.join(allowed_domains)}"
        )

    # Additional validation on repo path
    # Ensure no double slashes or leading/trailing slashes
    if repo_path.startswith('/') or repo_path.endswith('/'):
        raise ValueError("Repository path cannot start or end with /")

    if '//' in repo_path:
        raise ValueError("Repository path cannot contain consecutive slashes")

    # Ensure path is not empty
    if not repo_path:
        raise ValueError("Repository path cannot be empty")

    # Security: Check for excessive path depth (could indicate suspicious activity)
    path_depth = repo_path.count('/') + 1
    if path_depth > 10:
        raise ValueError(
            f"Repository path is too deep ({path_depth} levels). "
            "Maximum 10 levels allowed."
        )

    return v


def extract_ssh_parts(ssh_url: str) -> Dict[str, Optional[str]]:
    """
    Extract components from SSH URL.

    Args:
        ssh_url: SSH URL in format git@host:path/to/repo.git

    Returns:
        Dict with keys: host, path, name
    """
    pattern = r'^git@([a-zA-Z0-9\.-]+):([a-zA-Z0-9\.\-/]+)\.git$'
    match = re.match(pattern, ssh_url)

    if not match:
        raise ValueError(
            f"Invalid SSH URL format: '{ssh_url}'. "
            "Expected format: 'git@host:user/repo.git' "
            "(only ASCII alphanumeric, dots, hyphens allowed)"
        )

    host, path = match.groups()
    name = path.split('/')[-1] if '/' in path else path

    return {
        'host': host,
        'path': path,
        'name': name,
        'ssh': ssh_url
    }