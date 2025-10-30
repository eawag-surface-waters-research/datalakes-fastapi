import os
from typing import Dict, Optional
import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2AuthorizationCodeBearer

from app.database import SessionDep

from dotenv import load_dotenv
load_dotenv()

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI")
GITHUB_ORG = os.getenv("GITHUB_ORG")
GITHUB_TEAM_SLUG = os.getenv("GITHUB_TEAM_SLUG")
GITHUB_SCOPES = {
        "read:org": "Read org membership",
        "read:user": "Read user name"
    }
GITHUB_AUTHORIZATION_URL = "https://github.com/login/oauth/authorize"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_ORG_MEMBERSHIP_URL = f"https://api.github.com/orgs/{GITHUB_ORG}/members/{{username}}"
GITHUB_TEAM_MEMBERSHIP_URL = f"https://api.github.com/orgs/{GITHUB_ORG}/teams/{GITHUB_TEAM_SLUG}/memberships/{{username}}"

oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=GITHUB_AUTHORIZATION_URL,
    tokenUrl="/api/auth/token",
    scopes=GITHUB_SCOPES
)

client = httpx.AsyncClient()

async def get_access_token(code: str) -> str:
    """
    Exchanges a GitHub authorization code for an access token.
    """
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub OAuth credentials are not configured."
        )

    data = {
        "client_id": GITHUB_CLIENT_ID,
        "client_secret": GITHUB_CLIENT_SECRET,
        "code": code,
        "redirect_uri": GITHUB_REDIRECT_URI,
    }
    headers = {"Accept": "application/json"}

    try:
        response = await client.post(GITHUB_ACCESS_TOKEN_URL, json=data, headers=headers)
        response.raise_for_status()
        token_data = response.json()
        if "access_token" not in token_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve access token from GitHub."
            )
        return token_data["access_token"]
    except httpx.HTTPStatusError as e:
        print(f"HTTP error during token exchange: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail="Failed to exchange authorization code for access token."
        )
    except Exception as e:
        print(f"Error during token exchange: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during authentication."
        )

async def get_github_user(token: str) -> Dict:
    """
    Fetches the authenticated user's profile from the GitHub API.
    """
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = await client.get(GITHUB_USER_URL, headers=headers)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token."
        )
    except Exception as e:
        print(f"Error fetching user data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not fetch user data from GitHub."
        )

async def check_dataset_permissions(datasets_id: int, session: SessionDep, token: str = Depends(oauth2_scheme)) -> dict:
    """
    Checks users permission for endpoints.
    """
    user_data = await get_github_user(token)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    try:
        team_url = GITHUB_TEAM_MEMBERSHIP_URL.format(username=user_data["login"])
        team_response = await client.get(team_url, headers=headers)
        team_role = team_response.json().get('role', 'member')
        if team_role == "maintainer":
            return user_data
        elif team_role == "member":
            # Request username for dataset to verify ownership
            print(datasets_id)
            return user_data
    except:
        pass
    raise HTTPException(status_code=403, detail="Permission denied, user doesn't have sufficient permissions")

async def check_member(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Checks users permission for endpoints.
    """
    user_data = await get_github_user(token)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    try:
        team_url = GITHUB_TEAM_MEMBERSHIP_URL.format(username=user_data["login"])
        team_response = await client.get(team_url, headers=headers)
        team_role = team_response.json().get('role', 'member')
        if team_role in ["maintainer", "member"]:
            return user_data
    except:
        pass
    raise HTTPException(status_code=403, detail="Permission denied, user doesn't have sufficient permissions")

async def check_maintainer(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Checks users permission for endpoints.
    """
    user_data = await get_github_user(token)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    try:
        team_url = GITHUB_TEAM_MEMBERSHIP_URL.format(username=user_data["login"])
        team_response = await client.get(team_url, headers=headers)
        team_role = team_response.json().get('role', 'member')
        if team_role in ["maintainer"]:
            return user_data
    except:
        pass
    raise HTTPException(status_code=403, detail="Permission denied, user doesn't have sufficient permissions")